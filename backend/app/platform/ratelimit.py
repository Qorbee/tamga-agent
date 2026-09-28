"""A sliding-window rate limit per key, held in memory.

One `api` process serves Tamga (docs/architecture/01), so memory is the store. A second process
would give each its own window, and the limit would need Postgres behind it.
"""

import time
from collections import deque
from itertools import islice

# Beyond this many keys, idle ones are swept, and if every key is recent the oldest are dropped: a
# flood of addresses costs a few of them their history, never unbounded memory.
_MAX_KEYS = 10_000


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if len(self._hits) >= _MAX_KEYS:
            self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _sweep(self, now: float) -> None:
        idle = [
            key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self.window
        ]
        for key in idle:
            del self._hits[key]
        # Down to nine tenths, so a flood pays for one scan per thousand new keys, not one each.
        excess = len(self._hits) - _MAX_KEYS * 9 // 10
        if excess > 0:
            # Keys in first-seen order: the longest-known go first.
            for key in list(islice(self._hits, excess)):
                del self._hits[key]
