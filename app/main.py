"""Voice AI Gateway — FastAPI entry point.

Routes:
  POST /token     — JWT issuance
  WS   /ws/talk   — Authenticated voice WebSocket (Pipecat pipeline)
  GET  /metrics   — Prometheus metrics
  GET  /health    — Health check
  GET  /sessions  — Active sessions (requires auth)
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user_http, get_current_user_ws
from app.auth.jwt_handler import AuthError, authenticate_user, create_access_token
from app.config import get_settings
from app.metrics import get_metrics_output
from app.observability.logging import configure_logging, get_logger
from app.redis.rate_limiter import rate_limiter
from app.websocket.handler import handle_voice_websocket
from app.websocket.session_manager import session_manager

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app.starting", env=settings.app_env)
    yield
    logger.info("app.shutting_down")
    await session_manager.shutdown()
    await rate_limiter.close()
    logger.info("app.stopped")


app = FastAPI(
    title="Voice AI Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── POST /token ───────────────────────────────────────────────────────────────

@app.post("/token")
async def issue_token(body: dict) -> dict:
    """Issue a JWT. Body: {"username": "alice", "password": "password123"}

    Demo users: alice/password123 · bob/password456 · charlie/password789
    """
    username = body.get("username", "")
    password = body.get("password", "")
    try:
        user_id = authenticate_user(username, password)
    except AuthError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})
    token, expires_in = create_access_token(user_id)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


# ── WS /ws/talk ───────────────────────────────────────────────────────────────

@app.websocket("/ws/talk")
async def voice_ws(
    websocket: WebSocket,
    user_id: str = Depends(get_current_user_ws),
) -> None:
    """Authenticated real-time voice WebSocket.

    Connect: ws://host/ws/talk?token=<jwt>
    Send   : binary PCM audio frames (16-bit, 16 kHz, mono)
    Receive: binary TTS audio + JSON text events
    Limit  : max 2 concurrent sessions per user (close 4001 on breach)
    """
    await handle_voice_websocket(websocket=websocket, user_id=user_id)


# ── GET /metrics ──────────────────────────────────────────────────────────────

@app.get("/metrics")
async def prometheus_metrics() -> Response:
    output, content_type = get_metrics_output()
    return Response(content=output, media_type=content_type)


# ── GET /health ───────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    redis_ok = await rate_limiter.ping()
    return {
        "status": "ok" if redis_ok else "degraded",
        "redis": "ok" if redis_ok else "error",
        "active_sessions": session_manager.count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── GET /sessions ─────────────────────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions(_: str = Depends(get_current_user_http)) -> dict:
    """List active sessions. Requires Bearer token."""
    return {
        "active_sessions": session_manager.count,
        "sessions": session_manager.snapshot(),
    }


# ── GET / ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root() -> dict:
    return {"service": "Voice AI Gateway", "version": "1.0.0",
            "docs": "/docs", "health": "/health", "metrics": "/metrics"}
