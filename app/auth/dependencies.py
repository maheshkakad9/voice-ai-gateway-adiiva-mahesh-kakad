from __future__ import annotations
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.auth.jwt_handler import AuthError, extract_user_id
from app.metrics import JWT_REJECTIONS

_bearer = HTTPBearer(auto_error=False)


async def get_current_user_http(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if creds is None:
        JWT_REJECTIONS.labels(reason="missing").inc()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return extract_user_id(creds.credentials)
    except AuthError as e:
        JWT_REJECTIONS.labels(reason=e.code).inc()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=str(e),
                            headers={"WWW-Authenticate": "Bearer"})


async def get_current_user_ws(token: str = Query(..., alias="token")) -> str:
    """JWT passed as ?token=<jwt> on WebSocket upgrade URL."""
    try:
        return extract_user_id(token)
    except AuthError as e:
        JWT_REJECTIONS.labels(reason=e.code).inc()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
