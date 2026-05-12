from __future__ import annotations
import time, uuid
from typing import Any
from jose import ExpiredSignatureError, JWTError, jwt
from app.config import get_settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Demo users — replace with real DB + bcrypt in production
_USERS: dict[str, str] = {
    "alice": "password123",
    "bob": "password456",
    "charlie": "password789",
}


class AuthError(Exception):
    def __init__(self, message: str, code: str = "auth_error") -> None:
        super().__init__(message)
        self.code = code


def authenticate_user(username: str, password: str) -> str:
    expected = _USERS.get(username)
    if not expected or expected != password:
        raise AuthError("Invalid credentials", code="invalid_credentials")
    return username


def create_access_token(user_id: str) -> tuple[str, int]:
    s = get_settings()
    now = int(time.time())
    expires_in = s.jwt_expiry_minutes * 60
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": now + expires_in,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)
    logger.info("jwt.issued", user_id=user_id)
    return token, expires_in


def decode_token(token: str) -> dict[str, Any]:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except ExpiredSignatureError:
        raise AuthError("Token expired", code="token_expired")
    except JWTError as e:
        raise AuthError(f"Token invalid: {e}", code="token_invalid")


def extract_user_id(token: str) -> str:
    payload = decode_token(token)
    uid: str | None = payload.get("sub")
    if not uid:
        raise AuthError("Missing subject claim", code="token_invalid")
    return uid
