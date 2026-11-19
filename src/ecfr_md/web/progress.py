"""Download progress for one page, from the client's shared progress events.

The client reports progress to one :class:`~ecfr_md.progress.Progress`.
:class:`Relay` passes each event on, and also to whoever is listening in
the thread that caused it, so the page being built can show how far its
own download has got.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ecfr_md.progress import Progress

Listener = Callable[[float], object]
# How far along a download of unknown size is after this many bytes: halfway.
HALFWAY = 400_000


class Relay(Progress):
    """Progress events passed on to ``inner`` and to this thread's listener,
    as a fraction from 0 to 1."""

    def __init__(self, inner: Progress | None = None) -> None:
        self.inner = inner or Progress()
        self._local = threading.local()

    @contextmanager
    def listen(self, listener: Listener) -> Iterator[None]:
        self._local.listener = listener
        try:
            yield
        finally:
            del self._local.listener

    def _tell(self, fraction: float) -> None:
        listener = getattr(self._local, "listener", None)
        if listener is not None:
            listener(min(max(fraction, 0.0), 1.0))

    def fetching(self, citation: str, date: dt.date) -> None:
        self.inner.fetching(citation, date)
        self._tell(0.08)

    def started(self, url: str) -> None:
        self.inner.started(url)
        self._tell(0.12)

    def received(self, url: str, done: int, total: int | None) -> None:
        self.inner.received(url, done, total)
        share = done / total if total else done / (done + HALFWAY)
        self._tell(0.12 + 0.8 * share)

    def finished(self, url: str, size: int, *, cached: bool) -> None:
        self.inner.finished(url, size, cached=cached)
        self._tell(0.95)

    def retrying(
        self, url: str, error: Exception, delay: float, attempt: int, attempts: int
    ) -> None:
        self.inner.retrying(url, error, delay, attempt, attempts)

    def rate_changed(self, rate: float | None) -> None:
        self.inner.rate_changed(rate)

    def parts_started(self, total: int) -> None:
        self.inner.parts_started(total)

    def part_finished(self, identifier: str) -> None:
        self.inner.part_finished(identifier)

    def file_written(self, path: Path) -> None:
        self.inner.file_written(path)
