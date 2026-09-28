from app.platform import ratelimit
from app.platform.ratelimit import RateLimiter


def test_a_key_is_refused_past_its_limit() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60)

    assert [limiter.allow("10.0.0.1") for _ in range(3)] == [True, True, False]
    assert limiter.allow("10.0.0.2") is True


def test_a_flood_of_new_keys_keeps_memory_bounded() -> None:
    limiter = RateLimiter(limit=5, window_seconds=3600)

    for n in range(ratelimit._MAX_KEYS * 2):
        limiter.allow(f"client-{n}")

    assert len(limiter._hits) <= ratelimit._MAX_KEYS
