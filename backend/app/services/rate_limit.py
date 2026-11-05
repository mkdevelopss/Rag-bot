import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    """In-memory sliding-window limiter (per process). Use Redis if you run many workers."""

    def __init__(self, limit_per_minute_provider):
        self.limit_provider = limit_per_minute_provider
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        limit = int(self.limit_provider())
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._hits) > 10000:
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return True
