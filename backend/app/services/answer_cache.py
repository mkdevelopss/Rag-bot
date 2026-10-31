import threading
import time
from collections import OrderedDict


class AnswerCache:
    def __init__(self, max_entries: int = 512, ttl_seconds: int = 600):
        if max_entries < 1 or ttl_seconds < 1:
            raise ValueError("Answer cache limits must be positive.")
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, answer = entry
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return answer

    def set(self, key: str, answer: str) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, answer)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
