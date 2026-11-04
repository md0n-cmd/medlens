"""Tiny in-memory sliding-window rate limiter (per client IP). Good enough for a single free-tier instance."""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

LIMITED_PATHS = {"/api/analyze", "/api/extract"}
MESSAGE = "Too many requests from your connection. Please wait a minute and try again."


class RateLimiter:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Return (allowed, seconds until a slot frees up)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            if len(self._hits) > 10_000:          # bound memory: drop idle clients
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MIN", "30")))
