"""SessionManager: maps WebSocket connections to running PipelineTask asyncio.Tasks.

One asyncio.Task is created per session; it runs run_bot() which owns the
PipelineTask + PipelineRunner. The manager tracks all live tasks so they can
be cancelled on disconnect or server shutdown.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import WebSocket

from app.metrics import ACTIVE_SESSIONS
from app.observability.logging import get_logger
from app.services.usage_tracker import SessionUsage

logger = get_logger(__name__)


@dataclass
class ActiveSession:
    session_id: str
    user_id: str
    websocket: WebSocket
    usage: SessionUsage
    task: asyncio.Task
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SessionManager:
    """Async-safe registry of active voice sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, websocket: WebSocket, user_id: str) -> ActiveSession:
        from app.pipeline.bot import run_bot

        session_id = str(uuid.uuid4())
        usage = SessionUsage(session_id=session_id, user_id=user_id)

        # Each session's pipeline runs as its own asyncio.Task — fully non-blocking
        task = asyncio.create_task(
            run_bot(websocket=websocket, user_id=user_id,
                    session_id=session_id, usage=usage),
            name=f"bot-{session_id[:8]}",
        )

        session = ActiveSession(
            session_id=session_id,
            user_id=user_id,
            websocket=websocket,
            usage=usage,
            task=task,
        )

        async with self._lock:
            self._sessions[session_id] = session

        ACTIVE_SESSIONS.set(len(self._sessions))
        logger.info("session.created", session_id=session_id, user_id=user_id,
                    total=len(self._sessions))
        return session

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session is None:
            return

        # Cancel the pipeline task if still running
        if not session.task.done():
            session.task.cancel()
            try:
                await asyncio.wait_for(session.task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        ACTIVE_SESSIONS.set(len(self._sessions))
        logger.info(
            "session.removed",
            session_id=session_id,
            user_id=session.user_id,
            turns=session.usage.turn_number,
            total_cost_usd=round(session.usage.total_cost_usd, 6),
            total=len(self._sessions),
        )

    @property
    def count(self) -> int:
        return len(self._sessions)

    def snapshot(self) -> list[dict]:
        return [
            {
                "session_id": s.session_id,
                "user_id": s.user_id,
                "created_at": s.created_at.isoformat(),
                "turns": s.usage.turn_number,
                "total_cost_usd": round(s.usage.total_cost_usd, 6),
            }
            for s in self._sessions.values()
        ]

    async def shutdown(self) -> None:
        async with self._lock:
            ids = list(self._sessions.keys())
        await asyncio.gather(*[self.remove(sid) for sid in ids], return_exceptions=True)


session_manager = SessionManager()
