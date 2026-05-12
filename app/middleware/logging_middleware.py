"""HTTP request/response structured logging middleware."""
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.observability.logging import get_logger

logger = get_logger(__name__)


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with timing and structured fields."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()

        # Bind request context for this request's log chain
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "http.request_error",
                status_code=500,
                duration_ms=round((time.perf_counter() - start) * 1000, 1),
                error=str(exc),
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        # Only log non-WebSocket, non-metrics paths to reduce noise
        if not request.url.path.startswith("/metrics"):
            logger.info(
                "http.request",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response
