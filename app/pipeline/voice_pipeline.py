"""Voice pipeline: STT → Gemini LLM → TTS, Pipecat-quickstart architecture.

This module implements the core voice pipeline using Pipecat's pipeline
primitives. Each pipeline runs as a dedicated asyncio Task per WebSocket
session, preserving full concurrency without blocking the event loop.

The mandatory 160ms synchronization throttle required by the assignment is
isolated inside asyncio.to_thread() so the event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator

from app.config import get_settings
from app.metrics import (
    E2E_LATENCY_SECONDS,
    LLM_LATENCY_SECONDS,
    STT_LATENCY_SECONDS,
    TTS_LATENCY_SECONDS,
)
from app.observability.logging import get_logger
from app.services.usage_tracker import SessionUsage
from app.tools.play_audio import TOOL_SCHEMA, execute_play_audio

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger(__name__)

# ─── Mandatory synchronization throttle ──────────────────────────────────────
# The assignment mandates a 160ms blocking throttle to prevent race conditions
# in the session loop. We isolate it inside asyncio.to_thread() to keep the
# FastAPI event loop non-blocking while satisfying the requirement exactly.

def _blocking_session_throttle() -> None:
    """160ms blocking throttle — MUST run in a thread, NOT in the event loop."""
    import time as _time
    _time.sleep(0.16)


async def session_throttle() -> None:
    """Async-safe wrapper: runs the mandatory 160ms blocking sleep in a thread."""
    await asyncio.to_thread(_blocking_session_throttle)


# ─── Pipecat-style frame types ────────────────────────────────────────────────

@dataclass
class AudioFrame:
    """Raw audio bytes from the WebSocket client (mic input)."""
    data: bytes
    sample_rate: int = 16000
    timestamp: float = field(default_factory=time.time)


@dataclass
class TranscriptionFrame:
    """Transcript produced by the STT service."""
    text: str
    is_final: bool = True
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class LLMResponseFrame:
    """Text chunk from the LLM streaming response."""
    text: str
    is_final: bool = False
    tool_call: dict[str, Any] | None = None


@dataclass
class TTSAudioFrame:
    """Audio bytes from TTS synthesis."""
    data: bytes
    sample_rate: int = 24000


# ─── STT Processor (Deepgram) ─────────────────────────────────────────────────

class DeepgramSTTProcessor:
    """Simulates Deepgram STT with a realistic async call pattern.

    In production this uses pipecat.services.deepgram.DeepgramSTTService.
    For portability in the demo, this performs a lightweight REST transcription.
    """

    def __init__(self, usage: SessionUsage, session_id: str) -> None:
        self.usage = usage
        self.session_id = session_id
        self._api_key = get_settings().deepgram_api_key

    async def transcribe(self, frame: AudioFrame) -> TranscriptionFrame | None:
        t0 = time.perf_counter()
        audio_seconds = len(frame.data) / (frame.sample_rate * 2)  # 16-bit PCM

        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {self._api_key}",
                        "Content-Type": "audio/wav",
                    },
                    params={
                        "model": get_settings().deepgram_model,
                        "language": "en",
                        "smart_format": "true",
                        "encoding": "linear16",
                        "sample_rate": str(frame.sample_rate),
                    },
                    content=frame.data,
                )
                response.raise_for_status()
                result = response.json()
                transcript = (
                    result.get("results", {})
                    .get("channels", [{}])[0]
                    .get("alternatives", [{}])[0]
                    .get("transcript", "")
                    .strip()
                )
        except Exception as exc:
            logger.error("stt.error", session_id=self.session_id, error=str(exc))
            # Return empty transcript on failure rather than crashing the session
            transcript = ""

        latency = time.perf_counter() - t0
        STT_LATENCY_SECONDS.observe(latency)
        await self.usage.record_stt(audio_seconds)

        logger.info(
            "stt.transcribed",
            session_id=self.session_id,
            transcript=transcript[:80],
            latency_ms=round(latency * 1000, 1),
        )

        if not transcript:
            return None

        return TranscriptionFrame(text=transcript, timestamp=time.time())


# ─── LLM Processor (OpenAI) ───────────────────────────────────────────

class OpenAILLMProcessor:
    """Streams responses from OpenAI with tool-call support."""

    _SYSTEM_PROMPT = (
        "You are a helpful, friendly voice assistant. "
        "Keep responses concise and conversational — suitable for voice. "
        "When the user asks you to play a sound or audio, use the play_audio tool."
    )

    def __init__(self, usage: SessionUsage, session_id: str) -> None:
        self.usage = usage
        self.session_id = session_id
        self._settings = get_settings()
        self._history: list[dict] = [{"role": "system", "content": self._SYSTEM_PROMPT}]
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(api_key=self._settings.openai_api_key)
        return self._client

    async def generate(
        self, transcript: str
    ) -> AsyncGenerator[LLMResponseFrame, None]:
        """Stream LLM response frames for the given transcript."""
        self._history.append({"role": "user", "content": transcript})
        t0 = time.perf_counter()
        first_token = True

        try:
            client = self._get_client()
            response = await client.chat.completions.create(
                model=self._settings.openai_model,
                messages=self._history,
                tools=[TOOL_SCHEMA],
                stream=True,
            )

            full_text = ""
            tool_call: dict | None = None
            tool_call_name = ""
            tool_call_args = ""
            tool_call_id = ""

            async for chunk in response:
                if first_token:
                    LLM_LATENCY_SECONDS.observe(time.perf_counter() - t0)
                    first_token = False

                if not chunk.choices:
                    continue
                    
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    tc = delta.tool_calls[0]
                    if tc.id:
                        tool_call_id = tc.id
                    if tc.function.name:
                        tool_call_name += tc.function.name
                    if tc.function.arguments:
                        tool_call_args += tc.function.arguments
                elif delta.content:
                    full_text += delta.content
                    yield LLMResponseFrame(text=delta.content, is_final=False)

            # Assemble tool call if it exists
            if tool_call_name:
                try:
                    tool_call = {
                        "name": tool_call_name,
                        "args": json.loads(tool_call_args) if tool_call_args else {},
                    }
                except json.JSONDecodeError:
                    logger.error("llm.tool_parse_error", session_id=self.session_id, args=tool_call_args)

            # Count usage approximation
            input_tokens = max(1, len(transcript) // 4)
            output_tokens = max(1, len(full_text) // 4)
            # Use add_llm_tokens if record_llm_tokens doesn't exist
            if hasattr(self.usage, 'add_llm_tokens'):
                await self.usage.add_llm_tokens(input_tokens, output_tokens)
            else:
                await self.usage.record_llm_tokens(input_tokens, output_tokens)

            # Update history
            if tool_call:
                self._history.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call_id or "call_123",
                        "type": "function",
                        "function": {"name": tool_call["name"], "arguments": tool_call_args}
                    }],
                })
                yield LLMResponseFrame(text="", is_final=True, tool_call=tool_call)
            else:
                self._history.append({"role": "assistant", "content": full_text})
                yield LLMResponseFrame(text="", is_final=True)

        except Exception as exc:
            logger.error("llm.error", session_id=self.session_id, error=str(exc))
            error_msg = "I'm sorry, I encountered an error. Please try again."
            yield LLMResponseFrame(text=error_msg, is_final=True)


# ─── TTS Processor (ElevenLabs) ──────────────────────────────────────────────

class ElevenLabsTTSProcessor:
    """Streams TTS audio from ElevenLabs."""

    def __init__(self, usage: SessionUsage, session_id: str) -> None:
        self.usage = usage
        self.session_id = session_id
        self._settings = get_settings()

    async def synthesize(self, text: str) -> AsyncGenerator[TTSAudioFrame, None]:
        """Stream TTS audio frames for the given text."""
        if not text.strip():
            return

        t0 = time.perf_counter()
        await self.usage.record_tts(len(text))

        try:
            import httpx
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._settings.elevenlabs_voice_id}/stream"
            headers = {
                "xi-api-key": self._settings.elevenlabs_api_key,
                "Content-Type": "application/json",
            }
            payload = {
                "text": text,
                "model_id": "eleven_turbo_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                "output_format": "pcm_16000",
            }

            first_chunk = True
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        if chunk:
                            if first_chunk:
                                TTS_LATENCY_SECONDS.observe(time.perf_counter() - t0)
                                first_chunk = False
                            yield TTSAudioFrame(data=chunk)
                            await asyncio.sleep(0)  # yield to event loop

        except Exception as exc:
            logger.error("tts.error", session_id=self.session_id, error=str(exc))


# ─── PipelineTask ─────────────────────────────────────────────────────────────

class PipelineTask:
    """Orchestrates the full STT → LLM → TTS pipeline for one WebSocket session.

    Designed to run as an asyncio.Task. The mandatory 160ms throttle is
    applied via session_throttle() (runs time.sleep(0.16) in a thread).
    """

    def __init__(
        self,
        websocket: "WebSocket",
        usage: SessionUsage,
        session_id: str,
    ) -> None:
        self.websocket = websocket
        self.usage = usage
        self.session_id = session_id

        self._stt = DeepgramSTTProcessor(usage, session_id)
        self._llm = OpenAILLMProcessor(usage, session_id)
        self._tts = ElevenLabsTTSProcessor(usage, session_id)

        self._audio_queue: asyncio.Queue[AudioFrame | None] = asyncio.Queue(maxsize=32)
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        """Launch the pipeline processing loop as a background task."""
        self._running = True
        self._task = asyncio.create_task(
            self._pipeline_loop(),
            name=f"pipeline-{self.session_id}",
        )

    async def push_audio(self, data: bytes) -> None:
        """Enqueue raw audio bytes from the WebSocket client."""
        if self._running:
            frame = AudioFrame(data=data, sample_rate=get_settings().pipeline_audio_sample_rate)
            try:
                self._audio_queue.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("pipeline.queue_full", session_id=self.session_id)

    async def stop(self) -> None:
        """Gracefully stop the pipeline."""
        self._running = False
        await self._audio_queue.put(None)  # sentinel
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

    async def _pipeline_loop(self) -> None:
        """Main pipeline loop. Processes audio frames sequentially."""
        logger.info("pipeline.loop_started", session_id=self.session_id)

        while self._running:
            # ── Mandatory 160ms throttle (blocked in thread, not event loop) ──
            await session_throttle()

            try:
                frame = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if frame is None:
                break  # sentinel received

            e2e_start = time.perf_counter()

            # ── STT ────────────────────────────────────────────────────────────
            transcript_frame = await self._stt.transcribe(frame)
            if transcript_frame is None or not transcript_frame.text:
                continue

            await self._send_json({
                "type": "transcript",
                "text": transcript_frame.text,
                "is_final": transcript_frame.is_final,
            })

            # ── LLM ────────────────────────────────────────────────────────────
            llm_text_buffer = ""
            tool_call: dict | None = None

            async for llm_frame in self._llm.generate(transcript_frame.text):
                if llm_frame.tool_call:
                    tool_call = llm_frame.tool_call
                elif llm_frame.text:
                    llm_text_buffer += llm_frame.text

            # ── Tool call handling ─────────────────────────────────────────────
            if tool_call and tool_call.get("name") == "play_audio":
                args = tool_call.get("args", {})
                await execute_play_audio(
                    audio_url=args.get("audio_url", ""),
                    description=args.get("description", ""),
                    websocket=self.websocket,
                    usage=self.usage,
                    session_id=self.session_id,
                )
                # Record E2E latency for tool path
                E2E_LATENCY_SECONDS.observe(time.perf_counter() - e2e_start)
                snapshot = await self.usage.finalize_turn()
                await self._send_json({"type": "usage.update", "data": snapshot.model_dump(mode="json")})
                continue

            # ── TTS ────────────────────────────────────────────────────────────
            if llm_text_buffer:
                first_audio = True
                async for tts_frame in self._tts.synthesize(llm_text_buffer):
                    if first_audio:
                        E2E_LATENCY_SECONDS.observe(time.perf_counter() - e2e_start)
                        first_audio = False
                    await self.websocket.send_bytes(tts_frame.data)

            # ── Finalize turn ──────────────────────────────────────────────────
            snapshot = await self.usage.finalize_turn()
            await self._send_json({"type": "usage.update", "data": snapshot.model_dump(mode="json")})

        logger.info("pipeline.loop_stopped", session_id=self.session_id)

    async def _send_json(self, payload: dict) -> None:
        """Send a JSON text frame to the client, ignoring closed-socket errors."""
        try:
            await self.websocket.send_text(json.dumps(payload))
        except Exception:
            pass
