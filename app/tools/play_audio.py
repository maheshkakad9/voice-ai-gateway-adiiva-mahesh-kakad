"""Play Audio tool: Gemini triggers this to stream audio frames to the client.

When Gemini issues a `play_audio` function call, this tool:
1. Fetches the audio from the provided URL (or uses a default tone).
2. Streams binary WebSocket frames back to the connected client.
3. Records TTS characters for cost tracking.

The tool runs inside asyncio.to_thread() where needed, keeping
the event loop non-blocking.
"""
from __future__ import annotations

import asyncio
import io
import struct
import wave
from typing import TYPE_CHECKING

import httpx

from app.observability.logging import get_logger

if TYPE_CHECKING:
    from fastapi import WebSocket
    from app.services.usage_tracker import SessionUsage

logger = get_logger(__name__)

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "play_audio",
        "description": (
            "Stream a local demo song as binary WebSocket frames to the client. "
            "Use this whenever you want the user to hear music or a song."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Human-readable description of what this audio represents.",
                },
            },
            "required": ["description"],
        },
    }
}

# Chunk size for streaming: 4 KB per frame
_CHUNK_SIZE = 4096


def _generate_sine_wave_wav(frequency: float = 440.0, duration: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a simple sine-wave WAV fallback (used if audio_url is unavailable)."""
    import math

    num_samples = int(sample_rate * duration)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        for i in range(num_samples):
            sample = int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
            wf.writeframes(struct.pack("<h", sample))
    return buf.getvalue()


async def execute_play_audio(
    description: str,
    websocket: "WebSocket",
    usage: "SessionUsage",
    session_id: str,
) -> dict:
    """Execute the play_audio tool call.

    Streams audio bytes as binary WebSocket frames. Returns a result dict
    for inclusion in the Gemini tool_result message.
    """
    logger.info(
        "tool.play_audio.start",
        session_id=session_id,
        description=description,
    )

    try:
        with open("app/assets/demo.wav", "rb") as f:
            audio_bytes = f.read()
    except Exception as exc:
        logger.warning(
            "tool.play_audio.file_missing",
            session_id=session_id,
            error=str(exc),
        )
        # Fall back to a generated sine wave if file is missing
        audio_bytes = await asyncio.to_thread(_generate_sine_wave_wav, 440.0, 1.0)

    if not audio_bytes:
        return {"status": "error", "message": "Failed to obtain audio"}

    # Record TTS-equivalent characters (use byte length as a proxy cost)
    char_equivalent = len(description) or 50
    await usage.add_tts_chars(char_equivalent)

    # Stream binary frames over WebSocket
    total_bytes = len(audio_bytes)
    sent = 0
    try:
        while sent < total_bytes:
            chunk = audio_bytes[sent : sent + _CHUNK_SIZE]
            await websocket.send_bytes(chunk)
            sent += len(chunk)
            # Yield control to event loop between chunks
            await asyncio.sleep(0)
    except Exception as exc:
        logger.error(
            "tool.play_audio.stream_error",
            session_id=session_id,
            error=str(exc),
        )
        return {"status": "error", "message": str(exc)}

    logger.info(
        "tool.play_audio.complete",
        session_id=session_id,
        bytes_sent=sent,
    )
    return {
        "status": "ok",
        "bytes_sent": sent,
        "description": description,
    }
