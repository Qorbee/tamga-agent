"""The application's Redis client: cache, rate limits, locks, pub/sub.

One client per process, opened in the lifespan. Every key starts with a namespace owned by one
module (``<module>:<purpose>:<id>``) and carries a TTL unless it is meant to live forever —
an unbounded key family is a memory leak with a delay.
"""

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis

_redis: Redis | None = None


def init_redis(url: str) -> None:
    global _redis
    _redis = Redis.from_url(url, decode_responses=True)


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("init_redis() has not run; the app lifespan opens Redis")
    return _redis


RedisDep = Annotated[Redis, Depends(get_redis)]
