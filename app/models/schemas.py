"""Pydantic schemas for request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Auth ──────────────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenPayload(BaseModel):
    sub: str           # user_id / username
    exp: int           # expiry unix timestamp
    iat: int           # issued-at unix timestamp
    jti: str           # JWT ID (unique per token)


# ── Session ───────────────────────────────────────────────────────────────────

class SessionInfo(BaseModel):
    session_id: str
    user_id: str
    created_at: datetime
    state: Literal["connecting", "active", "closing", "closed"]
    stt_seconds: float = 0.0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    tts_characters: int = 0
    estimated_cost_usd: float = 0.0


class UsageSnapshot(BaseModel):
    session_id: str
    user_id: str
    turn_number: int
    stt_seconds_turn: float
    llm_input_tokens_turn: int
    llm_output_tokens_turn: int
    tts_characters_turn: int
    turn_cost_usd: float
    session_cost_usd: float
    timestamp: datetime


# ── WebSocket Messages ────────────────────────────────────────────────────────

class WSTextMessage(BaseModel):
    """JSON text frame sent to/from the client."""
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WSErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


class WSSessionStarted(BaseModel):
    type: Literal["session.started"] = "session.started"
    session_id: str
    user_id: str


class WSUsageUpdate(BaseModel):
    type: Literal["usage.update"] = "usage.update"
    data: UsageSnapshot


# ── Tool Calling ──────────────────────────────────────────────────────────────

class PlayAudioToolInput(BaseModel):
    audio_url: str = Field(..., description="URL of audio file to stream to client")
    description: str = Field("", description="Human-readable description of the audio")


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unhealthy"]
    redis: Literal["ok", "error"]
    active_sessions: int
    timestamp: datetime
