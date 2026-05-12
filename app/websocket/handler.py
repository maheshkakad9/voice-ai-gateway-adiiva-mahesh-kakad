"""WebSocket handler for /ws/talk.

Lifecycle:
  1. JWT already validated by FastAPI dependency (user_id injected)
  2. Redis concurrency check — close 4001 on breach
  3. Create session (spawns pipeline asyncio.Task)
  4. Wait for pipeline task to finish (client disconnect or error)
  5. Teardown: remove session, release Redis slot
"""
from __future__ import annotations

import time

from fastapi import WebSocket, WebSocketDisconnect

from app.metrics import ACTIVE_WS, WS_DISCONNECTIONS, WS_TOTAL
from app.observability.logging import get_logger
from app.redis.rate_limiter import rate_limiter
from app.websocket.session_manager import session_manager

logger = get_logger(__name__)

_WS_CLOSE_RATE_LIMITED = 4001


async def handle_voice_websocket(websocket: WebSocket, user_id: str) -> None:
    await websocket.accept()
    WS_TOTAL.inc()
    ACTIVE_WS.inc()
    t_connect = time.perf_counter()
    logger.info("ws.connected", user_id=user_id)

    # ── Redis concurrency cap ─────────────────────────────────────────────────
    granted = await rate_limiter.acquire(user_id)
    if not granted:
        logger.warning("ws.rate_limited", user_id=user_id, code=_WS_CLOSE_RATE_LIMITED)
        await websocket.close(
            code=_WS_CLOSE_RATE_LIMITED,
            reason="Concurrency limit exceeded",
        )
        ACTIVE_WS.dec()
        WS_DISCONNECTIONS.labels(reason="rate_limited").inc()
        return

    # ── Create session + start pipeline ──────────────────────────────────────
    session = await session_manager.create(websocket=websocket, user_id=user_id)
    session_id = session.session_id

    reason = "normal"
    try:
        # Block here until the pipeline task finishes (disconnect or error)
        await session.task
    except Exception as exc:
        reason = "error"
        logger.error("ws.pipeline_error", session_id=session_id, error=str(exc))
    finally:
        await session_manager.remove(session_id)
        await rate_limiter.release(user_id)

        duration = round(time.perf_counter() - t_connect, 2)
        ACTIVE_WS.dec()
        WS_DISCONNECTIONS.labels(reason=reason).inc()
        logger.info("ws.closed", session_id=session_id, user_id=user_id,
                    reason=reason, duration_s=duration)
