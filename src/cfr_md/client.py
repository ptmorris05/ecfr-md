"""Client for the eCFR API (https://www.ecfr.gov/developers/documentation/api/v1).

The three API services map onto ``ECFR.admin``, ``ECFR.search`` and
``ECFR.versioner``; each method corresponds to exactly one endpoint.
"""

import datetime as dt
import functools
import logging
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Self, Unpack

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

from cfr_md import models
from cfr_md.config import RETRY_STATUS_CODES, Settings
from cfr_md.errors import (
    APIError,
    ECFRConnectionError,
    ECFRTimeoutError,
    InvalidParameterError,
    ResponseFormatError,
    TitleNotFoundError,
    api_error,
)
from cfr_md.markdown import Layout, to_markdown, to_markdown_tree, write_markdown
from cfr_md.params import (
    Hierarchy,
    Page,
    PaginateBy,
    PerPage,
    SearchFilters,
    SearchOrder,
)

ADMIN = "/api/admin/v1"
SEARCH = "/api/search/v1"
VERSIONER = "/api/versioner/v1"

MAX_REPORTED_ERRORS = 3

logger = logging.getLogger("cfr_md")


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
            text = ecfr.markdown(45, part="164")
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        self.settings = settings = settings or Settings()
        self._http = httpx2.Client(
            base_url=str(settings.base_url),
            headers={"User-Agent": settings.user_agent, **settings.headers},
            timeout=settings.timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._throttle = _Throttle(settings.rate_limit)
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
    def markdown(
        self, title: int, *, date: dt.date | None = None, **hierarchy: Unpack[Hierarchy]
    ) -> str:
        """Regulation text as a single Markdown document.

        Parameters
        ----------
        title: int
            CFR title number, e.g. 45.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        return to_markdown(self.versioner.full(title, date=date, **hierarchy))

    @validated
    def markdown_tree(
        self, title: int, *, date: dt.date | None = None, **hierarchy: Unpack[Hierarchy]
    ) -> dict[PurePosixPath, str]:
        """Regulation text as Markdown files keyed by relative path.

        Parameters
        ----------
        title: int
            CFR title number, e.g. 45.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        return to_markdown_tree(self.versioner.full(title, date=date, **hierarchy))

    @validated
    def save(
        self,
        title: int,
        path: Path,
        *,
        layout: Layout = Layout.FILE,
        date: dt.date | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> list[Path]:
        """Save regulation text as Markdown and return the files written.

        Parameters
        ----------
        title: int
            CFR title number, e.g. 45.
        path: Path
            Output file (``file`` layout) or directory (``tree`` layout).
        layout: Layout
            ``file``: one Markdown file, hierarchy encoded as headings.
            ``tree``: nested directories, one file per section or appendix.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        xml = self.versioner.full(title, date=date, **hierarchy)
        return write_markdown(xml, path, layout)

    def get(self, path: str, params: Mapping[str, object] | None = None) -> bytes:
        """GET an API path, with throttling and retries, returning the raw body."""
        query = _query(params or {})
        return self._retrying(self._send, path, query).content

    def get_model[M: BaseModel](
        self, model: type[M], path: str, params: Mapping[str, object] | None = None
    ) -> M:
        """GET an API path and parse the JSON body into ``model``."""
        body = self.get(path, params)
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
        self._throttle.wait()
        logger.debug("GET %s", request.url)
        try:
            response = self._http.send(request)
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
        if response.is_error:
            raise api_error(response)
        return response

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
        return self._client.get_model(models.Agencies, f"{ADMIN}/agencies.json")

    @validated
    def corrections(
        self,
        *,
        title: int | None = None,
        date: dt.date | None = None,
        error_corrected_date: dt.date | None = None,
    ) -> models.Corrections:
        """eCFR corrections, optionally filtered.

        Parameters
        ----------
        title: int | None
            Only corrections to this title.
        date: dt.date | None
            Only corrections that occurred on or before, and were corrected on or
            after, this date.
        error_corrected_date: dt.date | None
            Only corrections corrected on this date.
        """
        params = {
            "title": title,
            "date": date,
            "error_corrected_date": error_corrected_date,
        }
        return self._client.get_model(
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
        return self._client.get_model(
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
        return self._client.get_model(model, f"{SEARCH}/{endpoint}", params)

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
        return self._client.get_model(models.Titles, f"{VERSIONER}/titles.json")

    @validated
    def latest_date(self, title: int) -> dt.date:
        """Most recent date for which a title's content is available.

        Parameters
        ----------
        title: int
            CFR title number.
        """
        for entry in self.titles().titles:
            if entry.number == title:
                if entry.reserved or entry.up_to_date_as_of is None:
                    raise TitleNotFoundError(f"Title {title} is reserved (no content)")
                return entry.up_to_date_as_of
        raise TitleNotFoundError(f"Title {title} does not exist")

    @validated
    def ancestry(
        self, title: int, *, date: dt.date | None = None, **hierarchy: Unpack[Hierarchy]
    ) -> models.Ancestry:
        """Ancestors of a hierarchy node, from the title down to the node itself.

        Parameters
        ----------
        title: int
            CFR title number.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = date or self.latest_date(title)
        path = f"{VERSIONER}/ancestry/{date}/title-{title}.json"
        return self._client.get_model(models.Ancestry, path, hierarchy)

    @validated
    def structure(self, title: int, *, date: dt.date | None = None) -> models.Node:
        """Table of contents for a whole title, without the regulation text.

        Parameters
        ----------
        title: int
            CFR title number.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = date or self.latest_date(title)
        path = f"{VERSIONER}/structure/{date}/title-{title}.json"
        return self._client.get_model(models.Node, path)

    @validated
    def versions(
        self,
        title: int,
        *,
        issue_date_on: dt.date | None = None,
        issue_date_lte: dt.date | None = None,
        issue_date_gte: dt.date | None = None,
        page: Page | None = None,
        **hierarchy: Unpack[Hierarchy],
    ) -> models.Versions:
        """Every version of the sections and appendices within a title.

        Parameters
        ----------
        title: int
            CFR title number.
        issue_date_on: dt.date | None
            Only content added on this issue date.
        issue_date_lte: dt.date | None
            Only content added on or before this issue date.
        issue_date_gte: dt.date | None
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
        return self._client.get_model(models.Versions, path, params)

    @validated
    def full(
        self, title: int, *, date: dt.date | None = None, **hierarchy: Unpack[Hierarchy]
    ) -> bytes:
        """Source XML for a title or any part of it.

        Parameters
        ----------
        title: int
            CFR title number.
        date: dt.date | None
            Point-in-time date. Defaults to the most recent available.
        """
        date = date or self.latest_date(title)
        path = f"{VERSIONER}/full/{date}/title-{title}.xml"
        return self._client.get(path, hierarchy)


class _Throttle:
    """Space requests at least ``1 / per_second`` seconds apart (thread-safe)."""

    def __init__(self, per_second: float | None) -> None:
        self._interval = 1 / per_second if per_second else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


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
