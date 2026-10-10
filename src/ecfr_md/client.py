"""Client for the eCFR API (https://www.ecfr.gov/developers/documentation/api/v1).

The three API services map onto ``ECFR.admin``, ``ECFR.search`` and
``ECFR.versioner``; each method calls one endpoint.
"""

import datetime as dt
import functools
import logging
import os
import re
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self, Unpack
from xml.sax.saxutils import escape

import httpx2
from pydantic import BaseModel, ValidationError, validate_call
from pydantic_core import ErrorDetails
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ecfr_md import models
from ecfr_md.cache import Store, default_directory
from ecfr_md.citation import Citation
from ecfr_md.config import RETRY_STATUS_CODES, SPLIT_ABOVE, Settings
from ecfr_md.document import Node, Source, load, save_events
from ecfr_md.errors import (
    APIError,
    DateOutOfRangeError,
    ECFRConnectionError,
    ECFRError,
    ECFRTimeoutError,
    ECFRWarning,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    ResponseFormatError,
    ServerError,
    TitleNotFoundError,
    api_error,
)
from ecfr_md.markdown import file_events
from ecfr_md.network import Throttle
from ecfr_md.params import (
    DateLike,
    Hierarchy,
    Page,
    PaginateBy,
    PerPage,
    SearchFilters,
    SearchOrder,
    as_hierarchy,
    to_date,
)
from ecfr_md.progress import Progress
from ecfr_md.sync import SyncResult, sync

ADMIN = "/api/admin/v1"
SEARCH = "/api/search/v1"
VERSIONER = "/api/versioner/v1"

MAX_REPORTED_ERRORS = 3

logger = logging.getLogger("ecfr_md")

TITLES_TTL = 300  # Seconds to reuse the title list within a session.
GROUP_BELOW = 2_000_000  # get_many fetches a whole part if it's smaller than this.

# Structure node types above the part level, as XML division tags.
DIVISIONS = {
    "title": ("DIV1", "TITLE"),
    "subtitle": ("DIV2", "SUBTITLE"),
    "chapter": ("DIV3", "CHAPTER"),
    "subchapter": ("DIV4", "SUBCHAP"),
}
XML_DECLARATION = re.compile(rb"^\s*<\?xml[^>]*\?>")
TAG = re.compile(r"<[^>]+>")  # Some structure labels contain raw markup.


def _reserved(node: models.Node) -> bool:
    """Reserved, including placeholders like "Parts 55-58 [Reserved]" (id "S 55")."""
    return node.reserved or "[Reserved]" in node.label


def _open_tag(tag: str, identifier: str | None, kind: str, label: str) -> bytes:
    n = escape(identifier or "", {'"': "&quot;"})
    return f'<{tag} N="{n}" TYPE="{kind}"><HEAD>{label}</HEAD>'.encode()


def _day(value: DateLike | None) -> dt.date | None:
    """A validated date argument as a date (validation has already converted it)."""
    return to_date(value) if value is not None else None


def _pages(result: models.Versions) -> int:
    """How many pages of versions the API has, from its response metadata."""
    return int(str(result.meta.get("total_pages") or 1))


def _above_part(cited: Citation) -> bool:
    return not {"part", "subpart", "section", "appendix"} & cited.hierarchy.keys()


def _locate(structure: models.Node, cited: Citation) -> models.Node:
    """The structure node a citation points to, at any level."""
    node = structure
    levels = ("subtitle", "chapter", "subchapter", "part", "subpart", "section")
    for level in (*levels, "appendix"):
        if (wanted := cited.hierarchy.get(level)) is not None:
            found = next(
                (
                    c
                    for c in _descendants(node)
                    if c.type == level and c.identifier == wanted
                ),
                None,
            )
            if found is None:
                raise NodeNotFoundError(f"No {level} {wanted} in title {cited.title}")
            node = found
    return node


def _find_part(outline: models.Node, part: str) -> models.Node | None:
    return next(
        (n for n in _descendants(outline) if n.type == "part" and n.identifier == part),
        None,
    )


def _descendants(node: models.Node) -> Iterator[models.Node]:
    for child in node.children:
        yield child
        yield from _descendants(child)


@dataclass(frozen=True)
class Download:
    """A response body on disk, and where and when it came from."""

    path: Path
    url: str
    retrieved: dt.datetime


def _describe(error: ErrorDetails) -> str:
    location = ".".join(map(str, error["loc"]))
    return f"{location}: {error['msg']} (got {error['input']!r:.80})"


def validated[F: Callable](func: F) -> F:
    """Validate arguments against type hints, raising InvalidParameterError."""
    checked = validate_call(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return checked(*args, **kwargs)
        except ValidationError as exc:
            details = "; ".join(map(_describe, exc.errors(include_url=False)))
            raise InvalidParameterError(
                f"Invalid {func.__name__} argument: {details}"
            ) from exc

    return wrapper  # type: ignore[return-value]


class ECFR:
    """eCFR API client.

    Use as a context manager so connections are released::

        with ECFR() as ecfr:
            part = ecfr.get("45 CFR Part 164")
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
        progress: Progress | None = None,
    ) -> None:
        self.settings = settings = settings or Settings()
        self.progress = progress or Progress()
        self._http = httpx2.Client(
            base_url=str(settings.base_url),
            headers={"User-Agent": settings.user_agent, **settings.headers},
            timeout=settings.timeout,
            follow_redirects=True,
            http2=True,  # One connection carries concurrent requests.
            limits=httpx2.Limits(max_connections=settings.concurrency),
            transport=transport,
        )
        self._throttle = Throttle(settings.rate_limit)
        directory = settings.cache_dir or default_directory()
        self.store = Store(
            directory if settings.cache else None, settings.cache_max_age
        )
        # Kept for the session: the title list (briefly), outlines, change lists.
        self._titles: tuple[float, models.Titles] | None = None
        self._outlines: dict[tuple[int, dt.date], models.Node] = {}
        self._changes: dict[tuple[int, dt.date], list[models.ContentVersion]] = {}
        self._memo_lock = threading.Lock()
        self._retrying = Retrying(
            stop=stop_after_attempt(settings.max_attempts),
            wait=self._wait,
            retry=retry_if_exception(_is_transient),
            before_sleep=self._log_retry,
            reraise=True,
        )
        self.admin = Admin(self)
        self.search = Search(self)
        self.versioner = Versioner(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @validated
    def get(
        self,
        citation: str | int,
        /,
        *,
        date: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> Node:
        """Fetch regulation text as a navigable :class:`~ecfr_md.document.Node`.

        One request returns the whole node; navigating it makes no more.

        Parameters
        ----------
        citation: str | int
            A title number or citation: ``45``, ``"45 CFR 164"``,
            ``"45 CFR Part 164 Subpart E"``, ``"45 CFR 164.502(a)(1)(i)(A)"``.
            Hierarchy keywords (``part="164"``, ...) refine it.
        date: DateLike | None
            Point-in-time date: a ``date``, ``datetime`` or text such as
            ``"2020-01-01"``, ``"Jan 1 2020"`` or ``"last year"``. Defaults to
            the most recent available.
        """
        cited = Citation.parse(citation).with_hierarchy(hierarchy)
        date = _day(date) or self.versioner.latest_date(cited.title)
        with self._full_text(cited, date) as download:
            node = load(
                download.path.read_bytes(),
                title=cited.title,
                date=date,
                hierarchy=cited.hierarchy,
                url=download.url,
                retrieved=download.retrieved,
            )
        if not cited.paragraph:
            return node
        paragraph = node.get(str(cited))
        if paragraph is None:
            raise NodeNotFoundError(f"{cited} does not exist on {date}")
        return paragraph

    @validated
    def outline(
        self,
        citation: str | int,
        /,
        *,
        date: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> models.Node:
        """The table of contents of a title or any part of it, without its text.

        Uses the structure endpoint, which is much smaller than the text and is
        kept in the cache.

        Parameters
        ----------
        citation: str | int
            A title number or citation, as for :meth:`get`.
        date: DateLike | None
            Point-in-time date. Defaults to the most recent available.
        """
        cited = Citation.parse(citation).with_hierarchy(hierarchy)
        date = _day(date) or self.versioner.latest_date(cited.title)
        return _locate(self._outline(cited.title, date), cited)

    @validated
    def changes(
        self,
        citation: str | int,
        /,
        *,
        since: DateLike | None = None,
        until: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> list[models.ContentVersion]:
        """Changes the eCFR has issued to a title or any part of it, newest first.

        Each change is a version of a section or appendix: when it took effect
        (``date``), when the eCFR issued it (``issue_date``), and whether it
        was ``substantive`` or ``removed`` it. Uses the versions endpoint; no
        text is downloaded.

        Parameters
        ----------
        citation: str | int
            A title number or citation, as for :meth:`get`.
        since: DateLike | None
            Only changes issued on or after this date.
        until: DateLike | None
            Only changes issued on or before this date.
        """
        cited = Citation.parse(citation).with_hierarchy(hierarchy)
        versions: list[models.ContentVersion] = []
        page, pages = 1, 1
        while page <= pages:
            result = self.versioner.versions(
                cited.title,
                issue_date_gte=since,
                issue_date_lte=until,
                page=page,
                **cited.hierarchy,
            )
            versions += result.content_versions
            pages = _pages(result)
            page += 1
        by_section = sorted(versions, key=lambda v: v.identifier)
        return sorted(by_section, key=lambda v: (v.issue_date, v.date), reverse=True)

    def sync(self, path: str | Path) -> SyncResult:
        """Update Markdown saved by ecfr-md to the latest eCFR text.

        ``path`` is a saved file, a saved tree, or a directory of them. Only
        files whose text changed are rewritten; files for sections that no
        longer exist are deleted. See :mod:`ecfr_md.sync`.
        """
        return sync(self, Path(path))

    @validated
    def get_many(
        self, citations: list[str | int], *, date: DateLike | None = None
    ) -> list[Node]:
        """Fetch several citations, sharing requests where possible.

        Sections in the same small part (up to ``GROUP_BELOW`` bytes) are
        fetched with one request for the part. Results are in input order.

        Parameters
        ----------
        citations: list[str | int]
            Citations, as for :meth:`get`.
        date: DateLike | None
            Point-in-time date for all of them. Defaults to the most recent.
        """
        cited = [Citation.parse(c) for c in citations]
        results: dict[int, Node] = {}
        groups: dict[tuple[int, str], list[int]] = {}
        for i, c in enumerate(cited):
            section = c.hierarchy.get("section")
            if section and set(c.hierarchy) <= {"part", "section"}:
                part = c.hierarchy.get("part") or section.split(".")[0]
                groups.setdefault((c.title, part), []).append(i)
        for (title, part), members in groups.items():
            when = _day(date) or self.versioner.latest_date(title)
            outline = _find_part(self._outline(title, when), part)
            if len(members) < 2 or outline is None or (outline.size or 0) > GROUP_BELOW:
                continue
            node = self.get(title, date=when, part=part)
            for i in members:
                if (found := node.get(str(cited[i]))) is not None:
                    results[i] = found
        return [
            results[i] if i in results else self.get(citations[i], date=date)
            for i in range(len(cited))
        ]

    @validated
    def save(
        self,
        citation: str | int,
        /,
        path: str | Path,
        *,
        tree: bool = False,
        front_matter: bool = True,
        date: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> list[Path]:
        """Save regulation text as Markdown and return the files written.

        Same output as ``get(...).save(...)``, but the XML is processed while
        it is read from disk instead of being loaded whole.

        Parameters
        ----------
        citation: str | int
            A title number or citation, as for :meth:`get`.
        path: str | Path
            Output file, or directory when ``tree`` is true.
        tree: bool
            Write nested directories, one file per section or appendix.
        front_matter: bool
            Start each file with YAML metadata: citation, date, source URL,
            retrieval time and a note that the eCFR is not an official edition.
        date: DateLike | None
            Point-in-time date. Defaults to the most recent available.
        """
        cited = Citation.parse(citation).with_hierarchy(hierarchy)
        if cited.paragraph:
            node = self.get(citation, date=date, **hierarchy)
            return node.save(path, tree=tree, front_matter=front_matter)
        date = _day(date) or self.versioner.latest_date(cited.title)
        with self._full_text(cited, date) as download, download.path.open("rb") as f:
            source = Source(
                cited.title, date, cited.hierarchy, download.url, download.retrieved
            )
            return save_events(
                file_events(f),
                source,
                Path(path),
                tree=tree,
                front_matter=front_matter,
                free=True,
                on_file=self.progress.file_written,
            )

    @contextmanager
    def download(
        self, path: str, params: Mapping[str, object] | None = None
    ) -> Iterator[Download]:
        """Stream a response body to a temporary file, with throttling and retries.

        The file is deleted on exit.
        """
        query = _query(params or {})
        url = str(self._http.build_request("GET", path, params=query).url)
        fd, name = tempfile.mkstemp(prefix="ecfr-md-", suffix=".part")
        os.close(fd)
        partial = Path(name)
        try:
            retrieved = self._retrying(self._stream, path, query, partial)
            yield Download(partial, url, retrieved)
        finally:
            partial.unlink(missing_ok=True)

    @contextmanager
    def _text(
        self, title: int, hierarchy: Mapping[str, object], date: dt.date
    ) -> Iterator[Download]:
        """Full-text XML for a date, from the store when still current."""
        path = f"{VERSIONER}/full/{date}/title-{title}.xml"
        with self._stored("full", title, hierarchy, date, path) as download:
            yield download

    @contextmanager
    def _stored(
        self,
        kind: str,
        title: int,
        hierarchy: Mapping[str, object],
        date: dt.date,
        path: str,
    ) -> Iterator[Download]:
        """A dated response, from the store when the eCFR hasn't changed it since.

        A stored copy is used for the same date, or, if it was the latest text
        when downloaded, for any later date; either way only if no version of
        it was issued since. That needs at most one request per title (the
        versions issued since the copy), and none if the title is unchanged.
        """
        if self.store.directory is None:
            with self.download(path, hierarchy) as download:
                yield download
            return
        state = self._title(title)
        token = state.latest_issue_date
        for entry in self.store.find(kind, title, as_hierarchy(hierarchy), date):
            reusable = entry.date == date or entry.token <= entry.date <= date
            if not reusable or token is None or entry.token > token:
                continue  # A newer token than the eCFR's means it rolled back.
            if entry.token != token:
                if self._changed(title, entry.token, hierarchy):
                    continue
                entry = self.store.refresh(entry, token)
            self.progress.finished(entry.url, entry.path.stat().st_size, cached=True)
            yield Download(entry.path, entry.url, entry.retrieved)
            return
        with self.download(path, hierarchy) as download:
            if token is not None and not state.processing_in_progress:
                self.store.put(
                    kind,
                    title,
                    as_hierarchy(hierarchy),
                    download.path,
                    date=date,
                    token=token,
                    latest=date == state.up_to_date_as_of,
                    url=download.url,
                    retrieved=download.retrieved,
                )
            yield download

    def _title(self, title: int) -> models.Title:
        """A title's dates, from a title list kept for a few minutes."""
        with self._memo_lock:
            memo = self._titles
            if memo is None or time.monotonic() - memo[0] >= TITLES_TTL:
                memo = self._titles = (time.monotonic(), self.versioner.titles())
            titles = memo[1].titles
        for entry in titles:
            if entry.number == title:
                return entry
        raise TitleNotFoundError(f"Title {title} does not exist")

    def _outline(self, title: int, date: dt.date) -> models.Node:
        """A title's structure, kept for the session."""
        key = (title, date)
        if key not in self._outlines:
            path = f"{VERSIONER}/structure/{date}/title-{title}.json"
            with self._stored("structure", title, {}, date, path) as download:
                body = download.path.read_bytes()
            try:
                self._outlines[key] = models.Node.model_validate_json(body)
            except ValidationError as exc:
                raise ResponseFormatError(
                    f"Unexpected outline of title {title}"
                ) from exc
        return self._outlines[key]

    def _changed(
        self, title: int, since: dt.date, hierarchy: Mapping[str, object]
    ) -> bool:
        """Whether any version touching ``hierarchy`` was issued after ``since``."""
        key = (title, since)
        with self._memo_lock:
            if key not in self._changes:
                after = since + dt.timedelta(days=1)
                versions, page, pages = [], 1, 1
                while page <= pages:
                    result = self.versioner.versions(
                        title, issue_date_gte=after, page=page
                    )
                    versions += result.content_versions
                    pages = _pages(result)
                    page += 1
                self._changes[key] = versions
            versions = self._changes[key]
        if section := hierarchy.get("section"):
            return any(v.identifier == section for v in versions)
        if part := hierarchy.get("part"):
            return any(v.part == part for v in versions)
        return bool(versions)

    @contextmanager
    def _full_text(self, cited: Citation, date: dt.date) -> Iterator[Download]:
        """Download full text, explaining dates the eCFR has no text for.

        The API can't serve the largest titles in one response (it times out),
        so text above the part level is fetched part by part when it's large
        or when the single request fails, then joined into one XML file.
        """
        self.progress.fetching(str(cited), date)
        with ExitStack() as stack:
            try:
                if _above_part(cited) and self._text_size(cited, date) > SPLIT_ABOVE:
                    download = self._by_part(cited, date, stack)
                else:
                    try:
                        download = stack.enter_context(
                            self._text(cited.title, dict(cited.hierarchy), date)
                        )
                    except ServerError as exc:
                        if not _above_part(cited) or exc.status_code != 504:
                            raise
                        logger.info("%s timed out; fetching it part by part", cited)
                        download = self._by_part(cited, date, stack)
            except NotFoundError as exc:
                if reason := self._explain_missing(cited, date):
                    raise DateOutOfRangeError(reason) from exc
                raise
            yield download

    def _text_size(self, cited: Citation, date: dt.date) -> int:
        return _locate(self._outline(cited.title, date), cited).size or 0

    def _by_part(self, cited: Citation, date: dt.date, stack: ExitStack) -> Download:
        """Join the parts under a title, chapter, etc. into one XML file.

        Parts are fetched ``settings.concurrency`` at a time and written in
        document order; at most twice that many are held in memory.
        """
        root = _locate(self._outline(cited.title, date), cited)
        path = f"{VERSIONER}/full/{date}/title-{cited.title}.xml"
        pieces: list[bytes | dict[str, str]] = []  # XML, or a part to fetch

        def plan(node: models.Node, above: dict[str, str]) -> None:
            label = escape(TAG.sub("", node.label).strip())
            if node.type in DIVISIONS:
                tag, kind = DIVISIONS[node.type]
                pieces.append(_open_tag(tag, node.identifier, kind, label))
                inner = dict(above)
                if node.type != "title":  # The title is in the path, not a parameter.
                    inner[node.type] = node.identifier or ""
                for child in node.children:
                    plan(child, inner)
                pieces.append(f"</{tag}>".encode())
            elif node.type in ("part", "appendix") and not _reserved(node):
                params = {node.type: node.identifier or ""}
                pieces.append(params | above if node.type == "appendix" else params)
            elif node.type == "part":
                pieces.append(_open_tag("DIV5", node.identifier, "PART", label))
                pieces.append(b"</DIV5>")
            else:  # Heading-only nodes.
                pieces.append(f"<HD1>{label}</HD1>".encode())

        plan(root, {})
        jobs = [i for i, piece in enumerate(pieces) if isinstance(piece, dict)]
        self.progress.parts_started(len(jobs))

        def fetch(params: dict[str, str]) -> bytes:
            with self._text(cited.title, params, date) as part:
                data = part.path.read_bytes()
            self.progress.part_finished(params.get("part") or params["appendix"])
            return XML_DECLARATION.sub(b"", data)

        fd, name = tempfile.mkstemp(prefix="ecfr-md-", suffix=".part")
        target = Path(name)
        stack.callback(target.unlink, missing_ok=True)
        retrieved = dt.datetime.now(dt.UTC)
        ahead = 2 * self.settings.concurrency
        pool = ThreadPoolExecutor(self.settings.concurrency)
        futures: dict[int, Future[bytes]] = {}
        try:
            with os.fdopen(fd, "wb") as out:
                for done, i in enumerate([*jobs, None]):
                    for j in jobs[done : done + ahead]:
                        if j not in futures and isinstance(job := pieces[j], dict):
                            futures[j] = pool.submit(fetch, job)
                    start = jobs[done - 1] + 1 if done else 0
                    for piece in pieces[start : len(pieces) if i is None else i]:
                        if isinstance(piece, bytes):
                            out.write(piece)
                    if i is not None:
                        out.write(futures.pop(i).result())
        finally:
            pool.shutdown(cancel_futures=True)
        joined = f"joined from {len(jobs)} part requests"
        url = f"{self._url(path, cited.hierarchy)} ({joined})"
        return Download(target, url, retrieved)

    def _url(self, path: str, params: Mapping[str, object]) -> str:
        return str(self._http.build_request("GET", path, params=_query(params)).url)

    def _explain_missing(self, cited: Citation, date: dt.date) -> str | None:
        """Why a 404 happened, if it's because of the date."""
        try:
            latest = self.versioner.latest_date(cited.title)
            if date > latest:
                return f"{cited} is available up to {latest}, not {date}"
            if not cited.hierarchy:
                return None
            dates, page, pages = [], 1, 1
            while page <= pages:  # The earliest may be on any page.
                result = self.versioner.versions(
                    cited.title, page=page, **cited.hierarchy
                )
                dates += [v.date for v in result.content_versions]
                pages = _pages(result)
                page += 1
        except ECFRError:
            return None
        if dates and date < min(dates):
            return f"The earliest version of {cited} is from {min(dates)}, after {date}"
        return None

    def request(self, path: str, params: Mapping[str, object] | None = None) -> bytes:
        """GET an API path, with throttling and retries; return the body."""
        query = _query(params or {})
        return self._retrying(self._send, path, query).content

    def request_model[M: BaseModel](
        self, model: type[M], path: str, params: Mapping[str, object] | None = None
    ) -> M:
        """GET an API path and parse the JSON body into ``model``."""
        body = self.request(path, params)
        try:
            return model.model_validate_json(body)
        except ValidationError as exc:
            errors = exc.errors(include_url=False)
            shown = "; ".join(_describe(e) for e in errors[:MAX_REPORTED_ERRORS])
            more = len(errors) - MAX_REPORTED_ERRORS
            raise ResponseFormatError(
                f"Unexpected response from {path}: {shown}"
                + (f" (and {more} more)" if more > 0 else "")
            ) from exc

    def _send(self, path: str, params: dict[str, str | list[str]]) -> httpx2.Response:
        request = self._http.build_request("GET", path, params=params)
        url = str(request.url)
        self._throttle.wait()
        logger.debug("GET %s", url)
        self.progress.started(url)
        with self._translate_errors(request):
            response = self._http.send(request)
        self._check(response)
        self.progress.finished(url, response.num_bytes_downloaded, cached=False)
        return response

    def _stream(
        self, path: str, params: dict[str, str | list[str]], target: Path
    ) -> dt.datetime:
        """Download a body into ``target``; return when it was retrieved."""
        request = self._http.build_request("GET", path, params=params)
        url = str(request.url)
        self._throttle.wait()
        logger.debug("GET %s (streaming)", url)
        self.progress.started(url)
        retrieved = dt.datetime.now(dt.UTC)
        with self._translate_errors(request):
            response = self._http.send(request, stream=True)
            try:
                if response.is_error:
                    response.read()
                self._check(response)
                length = response.headers.get("Content-Length")
                total = int(length) if length and length.isdigit() else None
                with target.open("wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        done = response.num_bytes_downloaded
                        self.progress.received(url, done, total)
            finally:
                response.close()
        self.progress.finished(url, response.num_bytes_downloaded, cached=False)
        return retrieved

    def _check(self, response: httpx2.Response) -> None:
        """Raise for error statuses, and adjust the request rate."""
        if response.status_code == 429:
            error = api_error(response)
            # Every thread pauses, so cap the pause like a single retry's wait.
            pause = error.retry_after
            if pause is not None:
                pause = min(pause, self.settings.backoff_max)
            self.progress.rate_changed(self._throttle.rejected(pause))
            raise error
        if response.is_error:
            raise api_error(response)
        if (rate := self._throttle.succeeded()) is not None:
            self.progress.rate_changed(rate)

    @contextmanager
    def _translate_errors(self, request: httpx2.Request) -> Iterator[None]:
        """Turn transport failures into this package's exceptions."""
        try:
            yield
        except httpx2.TimeoutException as exc:
            raise ECFRTimeoutError(
                f"No response from {request.url} within {self.settings.timeout}s"
            ) from exc
        except httpx2.DecodingError as exc:
            raise ResponseFormatError(
                f"Could not decode response from {request.url}: {exc}"
            ) from exc
        except httpx2.RequestError as exc:
            raise ECFRConnectionError(
                f"Could not reach {request.url}: {exc or type(exc).__name__}"
            ) from exc

    def _wait(self, state: RetryCallState) -> float:
        """Exponential backoff with jitter, honouring any Retry-After header."""
        initial, cap = self.settings.backoff_initial, self.settings.backoff_max
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, APIError) and exc.retry_after is not None:
            return min(exc.retry_after, cap)
        return wait_exponential_jitter(multiplier=initial, max=cap, jitter=initial)(
            state
        )

    def _log_retry(self, state: RetryCallState) -> None:
        exc = state.outcome.exception() if state.outcome else None
        delay = state.next_action.sleep if state.next_action else 0.0
        if isinstance(exc, APIError):
            url = exc.url
        else:  # No response: rebuild the URL from the request's path and query.
            url = self._url(state.args[0], state.args[1])
        if isinstance(exc, Exception):
            self.progress.retrying(
                url, exc, delay, state.attempt_number + 1, self.settings.max_attempts
            )
        logger.warning(
            "%s; retrying in %.1fs (attempt %d of %d)",
            exc,
            delay,
            state.attempt_number + 1,
            self.settings.max_attempts,
        )


class Admin:
    """Admin service: agencies and corrections."""

    def __init__(self, client: ECFR) -> None:
        self._client = client

    @validated
    def agencies(self) -> models.Agencies:
        """All agencies and the CFR chapters they administer."""
        return self._client.request_model(models.Agencies, f"{ADMIN}/agencies.json")

    @validated
    def corrections(
        self,
        *,
        title: int | None = None,
        date: DateLike | None = None,
        error_corrected_date: DateLike | None = None,
    ) -> models.Corrections:
        """eCFR corrections, optionally filtered.

        Parameters
        ----------
        title: int | None
            Only corrections to this title.
        date: DateLike | None
            Only corrections that occurred on or before, and were corrected on or
            after, this date.
        error_corrected_date: DateLike | None
            Only corrections corrected on this date.
        """
        params = {
            "title": title,
            "date": date,
            "error_corrected_date": error_corrected_date,
        }
        return self._client.request_model(
            models.Corrections, f"{ADMIN}/corrections.json", params
        )

    @validated
    def title_corrections(self, title: int) -> models.Corrections:
        """All corrections to one title.

        Parameters
        ----------
        title: int
            CFR title number.
        """
        return self._client.request_model(
            models.Corrections, f"{ADMIN}/corrections/title/{title}.json"
        )


class Search:
    """Search service: historical full-text search of the eCFR."""

    def __init__(self, client: ECFR) -> None:
        self._client = client

    def _get[M: BaseModel](
        self, model: type[M], endpoint: str, params: Mapping[str, object]
    ) -> M:
        params = dict(params)
        params["agency_slugs[]"] = params.pop("agency_slugs", None)
        return self._client.request_model(model, f"{SEARCH}/{endpoint}", params)

    @validated
    def results(
        self,
        query: str | None = None,
        *,
        per_page: PerPage | None = None,
        page: Page | None = None,
        order: SearchOrder | None = None,
        paginate_by: PaginateBy | None = None,
        **filters: Unpack[SearchFilters],
    ) -> models.SearchResults:
        """Sections and appendices matching a search.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        per_page: PerPage | None
            Results per page, at most 1,000.
        page: Page | None
            Page number. The API cannot paginate beyond 10,000 results.
        order: SearchOrder | None
            Result ordering.
        paginate_by: PaginateBy | None
            ``date`` keeps all results from one date on the same page.
        """
        params = {
            "query": query,
            "per_page": per_page,
            "page": page,
            "order": order,
            "paginate_by": paginate_by,
            **filters,
        }
        return self._get(models.SearchResults, "results", params)

    @validated
    def count(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.SearchCount:
        """Number of results matching a search.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        """
        return self._get(models.SearchCount, "count", {"query": query, **filters})

    @validated
    def summary(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.SearchSummary:
        """Summary description of a search.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        """
        return self._get(models.SearchSummary, "summary", {"query": query, **filters})

    @validated
    def daily_counts(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.DailyCounts:
        """Result counts by date.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        """
        params = {"query": query, **filters}
        return self._get(models.DailyCounts, "counts/daily", params)

    @validated
    def title_counts(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.TitleCounts:
        """Result counts by title.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        """
        params = {"query": query, **filters}
        return self._get(models.TitleCounts, "counts/titles", params)

    @validated
    def hierarchy_counts(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.HierarchyCounts:
        """Result counts by position in the CFR hierarchy.

        Parameters
        ----------
        query: str | None
            Search term; matches headings and full text.
        """
        params = {"query": query, **filters}
        return self._get(models.HierarchyCounts, "counts/hierarchy", params)

    @validated
    def suggestions(
        self, query: str | None = None, **filters: Unpack[SearchFilters]
    ) -> models.Suggestions:
        """Search term suggestions.

        Parameters
        ----------
        query: str | None
            Partial search term.
        """
        params = {"query": query, **filters}
        return self._get(models.Suggestions, "suggestions", params)


class Versioner:
    """Versioner service: CFR content and structure at any point in time."""

    def __init__(self, client: ECFR) -> None:
        self._client = client

    @validated
    def titles(self) -> models.Titles:
        """Summary information about every title, including its latest date."""
        return self._client.request_model(models.Titles, f"{VERSIONER}/titles.json")

    @validated
    def latest_date(self, title: int) -> dt.date:
        """Most recent date for which a title's content is available.

        Parameters
        ----------
        title: int
            CFR title number.
        """
        entry = self._client._title(title)
        if entry.reserved or entry.up_to_date_as_of is None:
            raise TitleNotFoundError(f"Title {title} is reserved (no content)")
        if entry.processing_in_progress:
            warnings.warn(
                f"The eCFR is processing updates to title {title}; its "
                f"text as of {entry.up_to_date_as_of} may still change.",
                ECFRWarning,
                stacklevel=4,
            )
        return entry.up_to_date_as_of

    @validated
    def ancestry(
        self,
        title: int,
        *,
        date: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> models.Ancestry:
        """Ancestors of a hierarchy node, from the title down to the node itself.

        Parameters
        ----------
        title: int
            CFR title number.
        date: DateLike | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = _day(date) or self.latest_date(title)
        path = f"{VERSIONER}/ancestry/{date}/title-{title}.json"
        return self._client.request_model(models.Ancestry, path, hierarchy)

    @validated
    def structure(self, title: int, *, date: DateLike | None = None) -> models.Node:
        """Table of contents for a whole title, without the regulation text.

        Parameters
        ----------
        title: int
            CFR title number.
        date: DateLike | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = _day(date) or self.latest_date(title)
        path = f"{VERSIONER}/structure/{date}/title-{title}.json"
        return self._client.request_model(models.Node, path)

    @validated
    def versions(
        self,
        title: int,
        *,
        issue_date_on: DateLike | None = None,
        issue_date_lte: DateLike | None = None,
        issue_date_gte: DateLike | None = None,
        page: Page | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> models.Versions:
        """Every version of the sections and appendices within a title.

        Parameters
        ----------
        title: int
            CFR title number.
        issue_date_on: DateLike | None
            Only content added on this issue date.
        issue_date_lte: DateLike | None
            Only content added on or before this issue date.
        issue_date_gte: DateLike | None
            Only content added on or after this issue date.
        page: Page | None
            Page number; up to 1,000 results per page.
        """
        params = {
            "issue_date[on]": issue_date_on,
            "issue_date[lte]": issue_date_lte,
            "issue_date[gte]": issue_date_gte,
            "page": page,
            **hierarchy,
        }
        path = f"{VERSIONER}/versions/title-{title}.json"
        return self._client.request_model(models.Versions, path, params)

    @validated
    def full(
        self,
        title: int,
        *,
        date: DateLike | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> bytes:
        """Source XML for a title or any part of it.

        Parameters
        ----------
        title: int
            CFR title number.
        date: DateLike | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = _day(date) or self.latest_date(title)
        path = f"{VERSIONER}/full/{date}/title-{title}.xml"
        return self._client.request(path, hierarchy)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, APIError):
        return exc.status_code in RETRY_STATUS_CODES
    return isinstance(exc, ECFRConnectionError)


def _query(params: Mapping[str, object]) -> dict[str, str | list[str]]:
    """Drop unset parameters and serialise the rest the way the API expects."""
    query: dict[str, str | list[str]] = {}
    for key, value in params.items():
        match value:
            case None:
                continue
            case list():
                query[key] = [str(item) for item in value]
            case dt.date():
                query[key] = value.isoformat()
            case _:
                query[key] = str(value)
    return query
