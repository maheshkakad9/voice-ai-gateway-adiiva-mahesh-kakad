"""Async Redis client management."""
from __future__ import annotations

import redis.asyncio as aioredis

from app.config import get_settings
from app.observability.logging import get_logger

logger = get_logger(__name__)

_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, initialising on first call."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            max_connections=settings.redis_max_connections,
            decode_responses=True,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
        logger.info("redis.client_initialized", url=settings.redis_url)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("redis.client_closed")


async def ping_redis() -> bool:
    """Health-check ping; returns True if Redis is reachable."""
    try:
        client = await get_redis()
        return await client.ping()
    except Exception as exc:
        logger.error("redis.ping_failed", error=str(exc))
        return False
