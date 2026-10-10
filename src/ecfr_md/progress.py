"""Progress events.

Pass a :class:`Progress` subclass to ``ECFR(progress=...)`` to follow
downloads and file writes. The base class ignores every event. Methods can be
called from several threads at once.
"""

import datetime as dt
from pathlib import Path


class Progress:
    def fetching(self, citation: str, date: dt.date) -> None:
        """Regulation text for ``citation`` as of ``date`` is being fetched."""

    def started(self, url: str) -> None:
        """A request was sent."""

    def received(self, url: str, done: int, total: int | None) -> None:
        """``done`` bytes of a response have arrived (``total`` if known)."""

    def finished(self, url: str, size: int, *, cached: bool) -> None:
        """A response is complete, or was read from the cache."""

    def retrying(
        self, url: str, error: Exception, delay: float, attempt: int, attempts: int
    ) -> None:
        """A request failed and will be retried after ``delay`` seconds."""

    def rate_changed(self, rate: float | None) -> None:
        """The request rate limit changed (requests per second; None = none)."""

    def parts_started(self, total: int) -> None:
        """Text is being fetched one part at a time, ``total`` parts in all."""

    def part_finished(self, identifier: str) -> None:
        """One part has been fetched."""

    def file_written(self, path: Path) -> None:
        """A Markdown file was written."""
