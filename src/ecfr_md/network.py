"""Request pacing shared by all threads using a client."""

import threading
import time

# After a 429 the rate halves, down to this many requests per second.
MIN_RATE = 0.2
# After a 429, the rate recovers to this share of the rate that got it, so
# it settles below the server's limit instead of climbing back into it.
RECOVER_TO = 0.8
# Successes in a row before one more request may be in flight.
PROBE_AFTER = 50
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
        self._limited: float | None = None  # The highest rate a 429 allows.
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
            caps = [c for c in (self.ceiling, self._limited) if c is not None]
            if self.rate is None or not caps or self.rate >= min(caps):
                return None
            self.rate = min(self.rate + RECOVERY_STEP, *caps)
            return self.rate

    def rejected(self, retry_after: float | None) -> float:
        """Record a 429; return the new, lower rate."""
        with self._lock:
            hit = self.rate or UNLIMITED_START
            limited = max(MIN_RATE, hit * RECOVER_TO)
            self._limited = min(limited, self._limited or limited)
            self.rate = max(MIN_RATE, hit / 2)
            pause = retry_after if retry_after is not None else 1 / self.rate
            self._paused_until = max(self._paused_until, time.monotonic() + pause)
            return self.rate


class Limiter:
    """Bounds the requests in flight at once, across all threads.

    The eCFR answers 429 when one client has three or more requests running
    (measured November 2026); two run without any. A 429 halves the bound and
    marks the level that failed; after ``PROBE_AFTER`` successes in a row it
    tries one more, but never the level that failed, nor above ``ceiling``.
    """

    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self.limit = ceiling
        self._failed_at: int | None = None
        self._active = 0
        self._streak = 0
        self._changed = threading.Condition()

    def __enter__(self) -> None:
        with self._changed:
            while self._active >= self.limit:
                self._changed.wait()
            self._active += 1

    def __exit__(self, *exc_info: object) -> None:
        with self._changed:
            self._active -= 1
            self._changed.notify_all()

    def succeeded(self) -> None:
        with self._changed:
            self._streak += 1
            below = self._failed_at is None or self.limit + 1 < self._failed_at
            if self._streak >= PROBE_AFTER and self.limit < self.ceiling and below:
                self.limit += 1
                self._streak = 0
                self._changed.notify_all()

    def rejected(self) -> int:
        """Record a 429; return the new bound."""
        with self._changed:
            running = max(self._active, 1)
            self._failed_at = min(running, self._failed_at or running)
            self.limit = max(1, min(self.limit, running) // 2)
            self._streak = 0
            return self.limit
