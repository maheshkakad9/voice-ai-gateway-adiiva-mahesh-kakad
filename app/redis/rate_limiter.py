"""Redis-backed concurrency cap using atomic Lua scripts.

Enforces MAX_CONCURRENT_CALLS_PER_USER active sessions per user.
Returns False (deny) when at cap → caller sends WS close code 4001.
"""
from __future__ import annotations
import redis.asyncio as aioredis
from app.config import get_settings
from app.metrics import RATE_LIMIT_VIOLATIONS
from app.observability.logging import get_logger

logger = get_logger(__name__)

# Atomic check-and-increment: returns 1 if slot granted, 0 if at cap
_ACQUIRE = """
local key = KEYS[1]
local cap = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local cur = redis.call('GET', key)
if cur and tonumber(cur) >= cap then return 0 end
local v = redis.call('INCR', key)
if v == 1 then redis.call('EXPIRE', key, ttl) end
return 1
"""

# Atomic safe-decrement: never goes below zero
_RELEASE = """
local key = KEYS[1]
local cur = redis.call('GET', key)
if cur and tonumber(cur) > 0 then redis.call('DECR', key) end
return 1
"""

_SESSION_TTL = 3600  # 1 hour safety TTL on the counter key


class RateLimiter:
    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._acquire_sha: str | None = None
        self._release_sha: str | None = None

    async def _get_client(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(
                get_settings().redis_url,
                max_connections=20,
                decode_responses=True,
                socket_timeout=2.0,
            )
        return self._client

    async def _load_scripts(self, client: aioredis.Redis) -> None:
        if self._acquire_sha is None:
            self._acquire_sha = await client.script_load(_ACQUIRE)
        if self._release_sha is None:
            self._release_sha = await client.script_load(_RELEASE)

    def _key(self, user_id: str) -> str:
        return f"vgw:concurrency:{user_id}"

    async def acquire(self, user_id: str) -> bool:
        """Try to claim a slot. Returns True if granted, False if at cap."""
        s = get_settings()
        try:
            client = await self._get_client()
            await self._load_scripts(client)
            result = await client.evalsha(
                self._acquire_sha, 1,
                self._key(user_id),
                str(s.max_concurrent_calls_per_user),
                str(_SESSION_TTL),
            )
            granted = bool(result)
            if not granted:
                RATE_LIMIT_VIOLATIONS.labels(user_id=user_id).inc()
                logger.warning("rate_limit.denied", user_id=user_id,
                               cap=s.max_concurrent_calls_per_user)
            else:
                logger.info("rate_limit.slot_acquired", user_id=user_id)
            return granted
        except Exception as exc:
            logger.error("rate_limit.redis_error", error=str(exc))
            return True  # fail-open: don't block users on Redis outage

    async def release(self, user_id: str) -> None:
        """Release a slot on disconnect."""
        try:
            client = await self._get_client()
            await self._load_scripts(client)
            await client.evalsha(self._release_sha, 1, self._key(user_id))
            logger.info("rate_limit.slot_released", user_id=user_id)
        except Exception as exc:
            logger.error("rate_limit.release_error", error=str(exc))

    async def ping(self) -> bool:
        try:
            client = await self._get_client()
            return await client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


rate_limiter = RateLimiter()
