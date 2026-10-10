"""Request pacing shared by all threads using a client."""

import threading
import time

# After a 429 the rate halves, down to this many requests per second.
MIN_RATE = 0.2
# Each successful request raises the rate by this much, up to the setting.
RECOVERY_STEP = 0.1
# Rate to start from when a 429 arrives while no limit is set.
UNLIMITED_START = 8.0


class Throttle:
    """Paces requests, and slows down when the API answers 429.

    A 429 halves the rate and pauses every thread for ``Retry-After`` (or one
    interval), not only the one that got it. Successful requests raise the
    rate again in small steps, up to ``ceiling``.
    """

    def __init__(self, ceiling: float | None) -> None:
        self.ceiling = ceiling
        self.rate = ceiling
        self._next = 0.0
        self._paused_until = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until the next request may be sent."""
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next, self._paused_until)
            self._next = start + (1 / self.rate if self.rate else 0.0)
        if start > now:
            time.sleep(start - now)

    def succeeded(self) -> float | None:
        """Record a success; return the new rate if it changed."""
        with self._lock:
            if self.rate is None or self.rate == self.ceiling:
                return None
            raised = self.rate + RECOVERY_STEP
            self.rate = raised if self.ceiling is None else min(self.ceiling, raised)
            return self.rate

    def rejected(self, retry_after: float | None) -> float:
        """Record a 429; return the new, lower rate."""
        with self._lock:
            self.rate = max(MIN_RATE, (self.rate or UNLIMITED_START) / 2)
            pause = retry_after if retry_after is not None else 1 / self.rate
            self._paused_until = max(self._paused_until, time.monotonic() + pause)
            return self.rate
