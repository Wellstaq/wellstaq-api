"""
app/core/redis.py

Shared Redis client, reused by OTP storage and (later) general caching.
Separate from ARQ's own Redis pool (used for job queueing) — this is
for direct key-value access from request-handling code, not job dispatch.
"""

from __future__ import annotations

import redis.asyncio as redis

from app.core.config import settings

_redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client