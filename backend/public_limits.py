"""Limits that hold no matter how many clients there are.

slowapi's limits in main.py are per client IP, so a botnet -- or simply many
users -- multiplies them. The pieces here bound the *total*: total swipes and
new sessions per hour (what reaches the never-purged tables), total cold-start
news fetches, and total identical database reads. All state is in-process,
which is exact for the single uvicorn worker this deployment runs. See
docs/RATE_LIMITS.md.
"""

import math
import threading
import time


class GlobalBudget:
    """A fixed-window counter shared by every client.

    take() returns False once `limit` events have happened in the current
    window; the caller answers 503 with retry_after(). Fixed rather than
    sliding window: it is O(1), and the worst case (2x the limit across a
    window boundary) is still a hard bound, which is all this needs to be.
    """

    def __init__(self, name: str, limit: int, window_seconds: float = 3600.0):
        if limit < 1:
            raise ValueError(f"{name}: limit must be >= 1, got {limit}")
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._window_start = time.monotonic()
        self._count = 0
        self.rejected = 0          # since process start; reported in telemetry

    def _roll(self, now: float) -> None:
        if now - self._window_start >= self.window:
            self._window_start = now
            self._count = 0

    def take(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._roll(now)
            if self._count >= self.limit:
                self.rejected += 1
                return False
            self._count += 1
            return True

    def retry_after(self) -> int:
        """Whole seconds until the window resets (at least 1)."""
        with self._lock:
            remaining = self.window - (time.monotonic() - self._window_start)
        return max(1, math.ceil(remaining))

    def snapshot(self) -> dict:
        with self._lock:
            self._roll(time.monotonic())
            return {
                "limit_per_window": self.limit,
                "window_seconds": self.window,
                "used_this_window": self._count,
                "rejected_since_start": self.rejected,
                "measured": True,
                "source": "in-process counter",
            }


class TTLCache:
    """Tiny thread-safe cache for public GET payloads.

    The deck, the topic catalogue and the summaries are the same for every
    client, so serving them from memory for a short while turns N identical
    queries into one. clear() is called whenever the underlying data changes
    (refresh pipeline, cold-start fetch, summary generation).
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._data = {}            # key -> (expires_at, value)

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            expires_at, value = hit
            if expires_at < time.monotonic():
                del self._data[key]
                return None
            return value

    def set(self, key, value) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._data) >= self.max_entries:
                # Query parameters are client-controlled, so the key space is
                # unbounded; drop everything rather than grow without limit.
                self._data.clear()
            self._data[key] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class ColdStartGate:
    """Allows at most one synchronous cold-start fetch per key per cooldown.

    GET /api/v1/feed fetches news inside the request when the requested topics
    have no articles. Without a gate, every request against an empty topic
    triggers ~12 outbound calls per topic. With it, the first request fetches
    and everyone else in the cooldown gets the (possibly empty) deck as it is.
    The lock is non-blocking: concurrent requests skip rather than queue
    behind a fetch that can take a minute.
    """

    def __init__(self, cooldown_seconds: float):
        self.cooldown = cooldown_seconds
        self._lock = threading.Lock()
        self._last = {}            # key -> monotonic time of the last fetch

    def run_once(self, key, fetch) -> bool:
        """Call fetch() if allowed for `key`. Returns True if it ran."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            now = time.monotonic()
            last = self._last.get(key)
            if last is not None and now - last < self.cooldown:
                return False
            # Recorded before fetching, so a fetch that raises still starts
            # the cooldown -- a failing upstream must not be retried per request.
            self._last[key] = now
            fetch()
            return True
        finally:
            self._lock.release()
