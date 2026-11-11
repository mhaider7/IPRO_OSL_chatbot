"""Per-host minimum-interval rate limiter (thread-safe)."""
from __future__ import annotations

import threading
import time
from typing import Callable


class HostRateLimiter:
    def __init__(
        self,
        min_interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_ok: dict[str, float] = {}

    def wait(self, host: str) -> float:
        """Block until a request to `host` is allowed; returns seconds slept."""
        with self._lock:
            now = self._clock()
            start = max(now, self._next_ok.get(host, 0.0))
            self._next_ok[host] = start + self.min_interval
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return max(delay, 0.0)

    def back_off(self, host: str, seconds: float) -> None:
        """Honor Retry-After: push the next allowed request for `host` out."""
        with self._lock:
            self._next_ok[host] = max(self._next_ok.get(host, 0.0), self._clock() + seconds)

    def blocked_for(self, host: str) -> float:
        with self._lock:
            return max(0.0, self._next_ok.get(host, 0.0) - self._clock())
