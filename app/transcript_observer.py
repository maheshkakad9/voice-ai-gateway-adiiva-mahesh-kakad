# transcript_observer.py
# Drop this file into your app/ directory and wire it into bot.py
# to emit transcript JSON events over the WebSocket so the frontend
# can display the live conversation.
#
# Usage in bot.py — add after the pipeline is built:
#
#   from app.transcript_observer import attach_transcript_observer
#   attach_transcript_observer(task, websocket)
#
# This uses Pipecat's frame observer API to intercept:
#   - TranscriptionFrame  → user speech
#   - LLMFullResponseEndFrame + accumulated text → assistant response
#   - BotStartedSpeakingFrame / BotStoppedSpeakingFrame → speaking state

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    MetricsFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTSUsageMetricsData
from pipecat.pipeline.task import PipelineTask
from pipecat.observers.base_observer import BaseObserver
from app.services.usage_tracker import SessionUsage

class TranscriptObserver(BaseObserver):
    """
    Sits at the end of the pipeline and sends JSON transcript events
    back to the browser over the same WebSocket used for audio.

    JSON events emitted:
      {"type": "transcription",       "text": "...", "role": "user"}
      {"type": "bot-tts-text",        "text": "..."}   ← streamed chunks
      {"type": "bot-started-speaking"}
      {"type": "bot-stopped-speaking"}
    """

    def __init__(self, websocket: "WebSocket", usage: SessionUsage | None = None):
        super().__init__()
        self._ws = websocket
        self._usage = usage
        self._assistant_buf: list[str] = []

    async def _send_json(self, payload: dict) -> None:
        try:
            await self._ws.send_text(json.dumps(payload))
        except Exception:
            pass  # WebSocket may already be closed

    async def on_push_frame(self, data) -> None:
        frame = data.frame

        if isinstance(frame, TranscriptionFrame):
            # User's speech recognised by Deepgram. Estimate STT cost based on length
            if self._usage:
                await self._usage.add_stt(max(1.0, len(frame.text) / 10.0))

            await self._send_json(
                {"type": "transcription", "text": frame.text, "role": "user"}
            )

        elif isinstance(frame, MetricsFrame):
            if self._usage:
                for m in frame.data:
                    if isinstance(m, LLMUsageMetricsData):
                        await self._usage.add_llm_tokens(
                            input_tokens=m.value.prompt_tokens,
                            output_tokens=m.value.completion_tokens,
                        )
                    elif isinstance(m, TTSUsageMetricsData):
                        await self._usage.add_tts_chars(m.value)

        elif isinstance(frame, LLMTextFrame):
            # Assistant text chunk (streamed from LLM)
            self._assistant_buf.append(frame.text)
            await self._send_json({"type": "bot-tts-text", "text": frame.text})

        elif isinstance(frame, LLMFullResponseEndFrame):
            # Full assistant turn finished — clear buffer
            self._assistant_buf.clear()
            await self._send_json({"type": "bot-llm-stopped"})

        elif isinstance(frame, BotStartedSpeakingFrame):
            await self._send_json({"type": "bot-started-speaking"})

        elif isinstance(frame, BotStoppedSpeakingFrame):
            await self._send_json({"type": "bot-stopped-speaking"})
            if self._usage:
                await self._usage.finalize_turn()


def attach_transcript_observer(task: PipelineTask, websocket: "WebSocket", usage: SessionUsage | None = None) -> TranscriptObserver:
    """
    Convenience function: creates a TranscriptObserver and registers it
    as a pipeline observer on the given task.

    Call this in bot.py AFTER building the pipeline and task, e.g.:

        from app.transcript_observer import attach_transcript_observer
        observer = attach_transcript_observer(task, websocket, usage)
    """
    observer = TranscriptObserver(websocket, usage)
    # Pipecat ≥ 0.0.79: register as a frame observer on the pipeline task
    task.add_observer(observer)
    return observer