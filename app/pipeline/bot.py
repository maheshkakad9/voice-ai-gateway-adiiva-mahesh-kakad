"""Voice pipeline — real Pipecat Quickstart Architecture.

Stack:
  Transport : FastAPIWebsocketTransport
  STT       : DeepgramSTTService
  LLM       : GoogleLLMService (Gemini 2.0 Flash)
  TTS       : CartesiaTTSService
  Tool      : play_audio  (FunctionSchema + register_direct_function)

Pipeline (exactly matching Pipecat Quickstart pattern):
  transport.input()
  → stt
  → context_aggregator.user()
  → llm
  → tts
  → transport.output()
  → context_aggregator.assistant()

Managed by PipelineTask + PipelineRunner, one per WebSocket session.

Mandatory 160 ms throttle:
  time.sleep(0.16) is isolated inside asyncio.to_thread() so the
  FastAPI event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMMessagesUpdateFrame, OutputAudioRawFrame, InputAudioRawFrame, Frame
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.cartesia.tts import CartesiaTTSService, CartesiaTTSSettings
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMContext, OpenAILLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from app.transcript_observer import attach_transcript_observer
from app.config import get_settings
from app.observability.logging import get_logger
from app.services.usage_tracker import SessionUsage


if TYPE_CHECKING:
    from fastapi import WebSocket

logger = get_logger(__name__)


# ── Mandatory 160 ms synchronisation throttle ─────────────────────────────────
# time.sleep(0.16) MUST be used, isolated in asyncio.to_thread() so the
# event loop is never blocked.

def _blocking_throttle() -> None:
    time.sleep(0.16)


async def _async_throttle() -> None:
    await asyncio.to_thread(_blocking_throttle)


# ── Play Audio tool ───────────────────────────────────────────────────────────

play_audio_schema = {
    "type": "function",
    "function": {
        "name": "play_audio",
        "description": (
            "Stream the local demo song to the user over the WebSocket connection. "
            "Use this when the user asks to play a sound, music, or a song."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short human-readable label for the audio (e.g. 'demo song').",
                },
            },
            "required": ["label"],
        },
    }
}


def _make_play_audio_handler(websocket: "WebSocket", usage: SessionUsage, session_id: str):
    """Return a direct-function handler bound to this session's websocket."""
    from app.tools.play_audio import execute_play_audio

    async def play_audio(params: FunctionCallParams, **kwargs) -> None:
        label: str = params.arguments.get("label", "demo song")

        # Mandatory 160 ms throttle before executing the tool
        await _async_throttle()

        result = await execute_play_audio(
            description=label,
            websocket=websocket,
            usage=usage,
            session_id=session_id,
        )
        await params.result_callback(result)

    play_audio.__name__ = "play_audio"
    return play_audio


# ── Pipeline builder ──────────────────────────────────────────────────────────

class RawAudioFrameSerializer(FrameSerializer):
    """Custom serializer that passes raw audio bytes through to the WebSocket.
    Required in Pipecat 0.0.105+ since default transports drop frames if no serializer is set."""
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(audio=data, num_channels=1, sample_rate=16000)
        return None

async def run_bot(
    websocket: "WebSocket", user_id: str, session_id: str, usage: SessionUsage
) -> None:
    """Build and run a full Pipecat voice pipeline for one WebSocket session."""
    s = get_settings()

    # ── Transport ─────────────────────────────────────────────────────────────
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=True,
            # FIX: vad_enabled / vad_audio_passthrough are removed in 0.0.105.
            # Pass vad_analyzer here; passthrough is now always on.
            vad_analyzer=SileroVADAnalyzer(params=VADParams(min_volume=0.01)),
            serializer=RawAudioFrameSerializer(),
        ),
    )

    # ── STT ───────────────────────────────────────────────────────────────────
    stt = DeepgramSTTService(api_key=s.deepgram_api_key)

    # ── LLM ───────────────────────────────────────────────────────────────────
    tools = [play_audio_schema]

    llm = OpenAILLMService(
        api_key=s.groq_api_key,
        model=s.groq_model,
        base_url="https://api.groq.com/openai/v1",
    )

    play_audio_fn = _make_play_audio_handler(websocket, usage, session_id)
    llm.register_direct_function(play_audio_fn)

    # ── TTS ───────────────────────────────────────────────────────────────────
    # FIX: voice_id kwarg is deprecated; use settings=CartesiaTTSSettings(voice=...)
    # The field is named `voice` (not `voice_id`) in TTSSettings.
    tts = CartesiaTTSService(
        api_key=s.cartesia_api_key,
        settings=CartesiaTTSSettings(voice=s.cartesia_voice_id),
    )

    # ── Context ───────────────────────────────────────────────────────────────
    context = OpenAILLMContext(
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a concise, friendly voice assistant. "
                    "Keep responses short and conversational. "
                    "CRITICAL INSTRUCTION: When the user asks you to play music, a song, or a sound, YOU MUST natively call the `play_audio` function tool. "
                    "DO NOT write Python code. DO NOT output JSON. DO NOT write `play_audio(...)` as text. "
                    "Just call the tool directly using the function calling API."
                )
            }
        ],
        tools=tools
    )
    ctx_aggregators = llm.create_context_aggregator(context)

    # ── Pipeline (Pipecat Quickstart structure) ───────────────────────────────
    pipeline = Pipeline([
        transport.input(),           # Receives InputAudioRawFrame from client
        stt,                         # Audio → TranscriptionFrame
        ctx_aggregators.user(),      # Accumulates user turns into context
        llm,                         # Context → LLMTextFrame (+ tool calls)
        tts,                         # LLMTextFrame → TTSAudioRawFrame
        transport.output(),          # Sends audio back to client
        ctx_aggregators.assistant(), # Captures assistant turns into context
    ])

    # ── PipelineTask ──────────────────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            allow_interruptions=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info("pipeline.client_connected", session_id=session_id, user_id=user_id)

        # Mandatory 160 ms throttle before first LLM call
        await _async_throttle()

        await task.queue_frames([
            LLMMessagesUpdateFrame(
                messages=[{"role": "user", "content": "Say a very brief greeting (one sentence)."}],
                run_llm=True,
            )
        ])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info("pipeline.client_disconnected", session_id=session_id)
        await task.cancel()

    # ── PipelineRunner ────────────────────────────────────────────────────────
    runner = PipelineRunner(handle_sigint=False)
    logger.info(
        "pipeline.starting", session_id=session_id, user_id=user_id, model=s.groq_model
    )
    attach_transcript_observer(task, websocket, usage)
    await runner.run(task)
    logger.info("pipeline.stopped", session_id=session_id)