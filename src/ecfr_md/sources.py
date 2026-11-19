"""Where regulation text comes from, and how the client chooses.

Two official sources serve the same text:

- :class:`ECFRApi`, the eCFR API's versioner (``base_url``): any date, any
  piece of a title, one request each; large titles take hundreds of requests.
- :class:`GovInfoBulk`, GovInfo's bulk XML (``bulk_url``): the current text
  only, one file per title (up to 160 MB), normalized by :mod:`ecfr_md.bulk`.

With ``source="auto"``, :func:`choose` picks one per request:

1. A past date always goes to the API; bulk is current text only.
2. A current bulk copy of the title in the cache is used, with no request.
3. Otherwise the costs are estimated: API requests * latency / requests in
   flight, against the title's bulk download / bandwidth. Ties go to the API.
   ``bulk_min_bytes`` replaces the estimate with a size threshold.
4. Bulk is used only if its ``Last-Modified`` is on or after the title's
   latest issue date, so it's no older than the API's text.

Latency and bandwidth start from the settings and, with ``learn_costs``, are
refined from each download as moving averages kept in the cache.
"""

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from ecfr_md.config import Settings

# Bulk files are served gzipped at about this share of the XML's size.
BULK_COMPRESSION = 0.3
LEARNING_RATE = 0.2  # Weight of each new observation in the moving averages.
COSTS_FILE = "costs.json"


class Source(Protocol):
    """A host that serves regulation text."""

    name: str
    url: str

    @property
    def host(self) -> str: ...


@dataclass(frozen=True)
class ECFRApi:
    """The eCFR API's versioner service."""

    url: str
    name: str = "api"

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc


@dataclass(frozen=True)
class GovInfoBulk:
    """GovInfo's bulk eCFR XML: one file per title, current text only."""

    url: str
    name: str = "bulk"

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    def title_url(self, title: int) -> str:
        return f"{self.url.rstrip('/')}/title-{title}/ECFR-title{title}.xml"


@dataclass(frozen=True)
class Choice:
    """The source for a request, and why."""

    source: str  # "api" or "bulk"
    reason: str


class Costs:
    """Estimated API latency and bulk bandwidth, per host, learned over time."""

    def __init__(self, settings: Settings, directory: Path | None) -> None:
        self.api_host = urlsplit(settings.base_url).netloc
        self.bulk_host = urlsplit(settings.bulk_url).netloc
        self.learn = settings.learn_costs
        self.path = directory / COSTS_FILE if directory and self.learn else None
        self.api_latency = settings.api_latency
        self.bulk_bandwidth = settings.bulk_bandwidth
        self._lock = threading.Lock()
        self._learned = False  # Since loading or saving.
        saved = self._read()
        self.api_latency = saved.get("api", {}).get(self.api_host, self.api_latency)
        self.bulk_bandwidth = saved.get("bulk", {}).get(
            self.bulk_host, self.bulk_bandwidth
        )

    def api_request(self, seconds: float) -> None:
        """Learn from an API request that took ``seconds``."""
        if self.learn:
            with self._lock:
                self.api_latency += LEARNING_RATE * (seconds - self.api_latency)
                self._learned = True

    def bulk_download(self, size: int, seconds: float) -> None:
        """Learn from a bulk download of ``size`` bytes over the network."""
        if self.learn and size > 0 and seconds > 0:
            with self._lock:
                rate = size / seconds
                self.bulk_bandwidth += LEARNING_RATE * (rate - self.bulk_bandwidth)
                self._learned = True

    def save(self) -> None:
        """Keep what was learned in the cache for later sessions."""
        if self.path is None or not self._learned:
            return
        with self._lock:
            self._learned = False
            saved = self._read()
            saved.setdefault("api", {})[self.api_host] = round(self.api_latency, 3)
            saved.setdefault("bulk", {})[self.bulk_host] = round(self.bulk_bandwidth)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                partial = self.path.with_name(f".{self.path.name}.{os.getpid()}.part")
                partial.write_text(json.dumps(saved))
                os.replace(partial, self.path)
            except OSError:
                pass  # Learning is an optimization; never fail a download for it.

    def _read(self) -> dict[str, dict[str, float]]:
        if self.path is None:
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}


def choose(
    settings: Settings,
    costs: Costs,
    *,
    past: bool,
    cached: bool,
    single: bool,
    estimate: Callable[[], tuple[int, int, int]],
    current: Callable[[], bool],
) -> Choice:
    """Pick the source for some text; see the module docstring.

    ``past`` is whether it's for a date before the latest, ``cached``
    whether a current bulk copy of the title is in the cache, and ``single``
    whether the API serves it in one request (a part or less). ``estimate``
    gives the API requests it needs (0 if they're all cached), its size and
    the whole title's, in bytes of XML. ``current`` checks whether the bulk
    copy is up to date. Both are called only when needed.
    """
    if settings.source == "api":
        return Choice("api", "source is set to api")
    if past:
        return Choice("api", "bulk has only the current text")
    if cached:
        return Choice("bulk", "a current bulk copy of the title is in the cache")
    if settings.source == "auto":
        if single and settings.bulk_min_bytes is None:
            return Choice("api", "one API request")
        requests, size, title_size = estimate()
        if requests == 0:
            return Choice("api", "the text is in the cache")
        better = _api_reason(settings, costs, requests, size, title_size)
        if better is not None:
            return Choice("api", better)
        reason = _bulk_reason(settings, costs, requests, title_size)
    else:
        reason = "source is set to bulk"
    if not current():
        return Choice("api", "the bulk copy is older than the eCFR's latest changes")
    return Choice("bulk", reason)


def _api_reason(
    settings: Settings, costs: Costs, requests: int, size: int, title_size: int
) -> str | None:
    """Why the API is the better choice, or None if bulk is."""
    if settings.bulk_min_bytes is not None:
        if size < settings.bulk_min_bytes:
            return f"{size:,} bytes is under bulk_min_bytes"
        return None
    api, bulk = _seconds(settings, costs, requests, title_size)
    if api <= bulk:
        verb = "beats" if requests == 1 else "beat"
        return (
            f"{_requests(requests)} (~{api:.0f} s) {verb} downloading the "
            f"title (~{bulk:.0f} s)"
        )
    return None


def _bulk_reason(
    settings: Settings, costs: Costs, requests: int, title_size: int
) -> str:
    if settings.bulk_min_bytes is not None:
        return "the text is over bulk_min_bytes"
    api, bulk = _seconds(settings, costs, requests, title_size)
    return (
        f"downloading the title (~{bulk:.0f} s) beats {_requests(requests)} "
        f"(~{api:.0f} s)"
    )


def _requests(n: int) -> str:
    return f"{n} API request{'' if n == 1 else 's'}"


def _seconds(
    settings: Settings, costs: Costs, requests: int, title_size: int
) -> tuple[float, float]:
    """Estimated seconds by API and by bulk."""
    parallel = max(1, min(settings.concurrency, requests))
    api = requests * costs.api_latency / parallel
    bulk = title_size * BULK_COMPRESSION / costs.bulk_bandwidth
    return api, bulk


def explain(settings: Settings, costs: Costs) -> str:
    """How the source of text is chosen with these settings, in a sentence."""
    if settings.source == "api":
        return "All text comes from the eCFR API."
    if settings.source == "bulk":
        return (
            "Current text comes from GovInfo's bulk files while they're as "
            "current as the eCFR; past dates come from the eCFR API."
        )
    rule = (
        f"text of {settings.bulk_min_bytes:,} bytes or more comes from bulk"
        if settings.bulk_min_bytes is not None
        else (
            "larger text comes from whichever is estimated faster, at "
            f"{costs.api_latency:.1f} s per API request or "
            f"{costs.bulk_bandwidth / 1e6:.1f} MB/s from bulk"
        )
    )
    return (
        "Past dates, and parts or less, come from the eCFR API, unless a "
        f"current bulk copy of the title is in the cache; {rule}, if GovInfo's "
        "copy is as current as the eCFR."
    )
