"""Per-session usage and cost tracker.

Records STT seconds, LLM tokens, and TTS characters per turn and
cumulatively for the whole session. Emits Prometheus counters and
structured logs per turn.
"""
from __future__ import annotations
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings
from app.metrics import COST_USD, LLM_INPUT_TOKENS, LLM_OUTPUT_TOKENS, STT_SECONDS, TTS_CHARS
from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TurnUsage:
    stt_seconds: float = 0.0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    tts_characters: int = 0


@dataclass
class SessionUsage:
    session_id: str
    user_id: str
    started_at: float = field(default_factory=time.time)
    turn_number: int = 0

    # Cumulative totals
    total_stt_seconds: float = 0.0
    total_llm_input_tokens: int = 0
    total_llm_output_tokens: int = 0
    total_tts_characters: int = 0
    total_cost_usd: float = 0.0

    _current: TurnUsage = field(default_factory=TurnUsage)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _turn_cost(self, t: TurnUsage) -> float:
        s = get_settings()
        return (
            t.stt_seconds * s.deepgram_cost_per_second
            + (t.llm_input_tokens / 1000) * s.groq_input_cost_per_1k_tokens
            + (t.llm_output_tokens / 1000) * s.groq_output_cost_per_1k_tokens
            + (t.tts_characters / 1000) * s.cartesia_cost_per_1k_chars
        )

    async def add_stt(self, seconds: float) -> None:
        async with self._lock:
            self._current.stt_seconds += seconds
        STT_SECONDS.labels(user_id=self.user_id).inc(seconds)

    async def add_llm_tokens(self, input_tokens: int, output_tokens: int) -> None:
        async with self._lock:
            self._current.llm_input_tokens += input_tokens
            self._current.llm_output_tokens += output_tokens
        LLM_INPUT_TOKENS.labels(user_id=self.user_id).inc(input_tokens)
        LLM_OUTPUT_TOKENS.labels(user_id=self.user_id).inc(output_tokens)

    async def add_tts_chars(self, chars: int) -> None:
        async with self._lock:
            self._current.tts_characters += chars
        TTS_CHARS.labels(user_id=self.user_id).inc(chars)

    async def finalize_turn(self) -> dict:
        """Commit current turn, compute cost, reset turn state. Returns snapshot dict."""
        async with self._lock:
            t = self._current
            cost = self._turn_cost(t)
            self.turn_number += 1
            self.total_stt_seconds += t.stt_seconds
            self.total_llm_input_tokens += t.llm_input_tokens
            self.total_llm_output_tokens += t.llm_output_tokens
            self.total_tts_characters += t.tts_characters
            self.total_cost_usd += cost

            s = get_settings()
            COST_USD.labels(user_id=self.user_id, provider="deepgram").inc(
                t.stt_seconds * s.deepgram_cost_per_second)
            COST_USD.labels(user_id=self.user_id, provider="groq").inc(
                (t.llm_input_tokens / 1000) * s.groq_input_cost_per_1k_tokens
                + (t.llm_output_tokens / 1000) * s.groq_output_cost_per_1k_tokens)
            COST_USD.labels(user_id=self.user_id, provider="cartesia").inc(
                (t.tts_characters / 1000) * s.cartesia_cost_per_1k_chars)

            snapshot = {
                "session_id": self.session_id,
                "user_id": self.user_id,
                "turn": self.turn_number,
                "stt_seconds": round(t.stt_seconds, 3),
                "llm_input_tokens": t.llm_input_tokens,
                "llm_output_tokens": t.llm_output_tokens,
                "tts_characters": t.tts_characters,
                "turn_cost_usd": round(cost, 6),
                "session_cost_usd": round(self.total_cost_usd, 6),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._current = TurnUsage()

        logger.info("usage.turn_complete", **snapshot)
        return snapshot
