"""An MCP server that lets AI agents navigate the CFR as an expert would.

``ecfr-md mcp`` runs it (stdio, or streamable HTTP with ``--http``); needs
the ``mcp`` extra. :func:`server` builds it around an :class:`~ecfr_md.ECFR`
client, so another app can serve it too (its ``http_app()`` is ASGI).

The tools map before they read: ``outline`` gives token estimates, ``read``
returns the slice of text that fits a budget with a cursor to continue, and
every result carries canonical citations, addresses, dates and eCFR links.
``verify_quote`` and ``define`` ground what an agent writes. Everything is
read-only except ``export``, which writes only below the directory given at
startup, and ``configure``, which exists only with
``allow_config_changes``.

Resources: ``cfr:{address}`` is the text of any place in the CFR as
Markdown (``cfr:45/164.502``, ``cfr:45/part-164@2020-01-01``), and
``cfr:titles`` the titles. Clients can subscribe to a ``cfr:`` resource; the
server checks the eCFR every ``watch_interval`` seconds and notifies them
when the text changes.
"""

import asyncio
import contextlib
import contextvars
import datetime as dt
import re
import threading
from collections import Counter, OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

try:
    import anyio
    import mcp_types
    from fastmcp import Context, FastMCP
    from fastmcp.exceptions import ResourceError, ToolError
    from fastmcp.server.lifespan import lifespan
    from fastmcp.tools import ToolResult
    from mcp.server.session import ServerSession
    from mcp.server.subscriptions import (
        InMemorySubscriptionBus,
        ListenHandler,
        ResourceUpdated,
    )
    from mcp.shared.exceptions import MCPError
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS
except ImportError as exc:  # pragma: no cover - depends on the environment
    from ecfr_md.errors import MissingDependencyError

    raise MissingDependencyError(
        "The MCP server needs the mcp extra: "
        'uv tool install "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md"'
    ) from exc
from pydantic import BaseModel, Field, ValidationError

from ecfr_md.address import Address
from ecfr_md.client import ECFR
from ecfr_md.compare import Difference
from ecfr_md.compare import compare as compare_texts
from ecfr_md.config import Settings, __version__
from ecfr_md.document import Level, Node
from ecfr_md.errors import (
    ECFRConnectionError,
    ECFRError,
    InvalidCitationError,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    TitleNotFoundError,
)
from ecfr_md.passages import (
    TextIndex,
    outline_tokens,
    parse_cursor,
    passage,
)
from ecfr_md.progress import Progress
from ecfr_md.references import Reference, find_references
from ecfr_md.render.formats import Format
from ecfr_md.results import LEAVES, Change, Matches, Outline

INSTRUCTIONS = """\
The Code of Federal Regulations (CFR), from the eCFR: the current text and \
any past date.

Hierarchy: title > (subtitle) > chapter > (subchapter) > part > (subpart) > \
section or appendix > paragraphs (a) > (1) > (i) > (A) > (1) > (i). Sections \
are numbered part.section: 45 CFR 164.502 is in part 164 of title 45.

Citations: any common form works, e.g. "45 CFR 164.502(a)(1)", \
"45 C.F.R. § 164.502", "45 CFR Part 164 Subpart E", eCFR or LII URLs, or \
addresses: 45/164.502/a/1, 45/part-164/subpart-E, with @2020-01-01 for a date. \
Results give each place's citation (to quote), address (to pass back) and \
eCFR URL.

Work map first, then read:
1. Find the place: outline(title or part) shows contents and token sizes; \
search(words, within=...) finds text; resolve_citation checks a citation.
2. read(citation, budget_tokens) returns the text that fits, ending at a \
paragraph; if more remains it gives a cursor: read(cursor=...) continues. \
Paragraph labels in read show full pinpoints: (a)(1)(ii) is cited as \
45 CFR 164.502(a)(1)(ii).
3. Ground what you write: define(term, scope) gives the governing \
definition; verify_quote(citation, text) confirms a quote is at a pinpoint.
4. Over time: history and changes list versions; compare shows a redline \
between dates. references and cited_by follow cross-references.

The eCFR is an editorial compilation, not an official legal edition."""

DEFAULT_BUDGET = 3000
MAX_BUDGET = 60_000
OUTLINE_LINES = 120  # Lines an outline aims for when no depth is given.
OUTLINE_MAX_LINES = 600
MAX_RESOURCE_CHARS = 400_000
KEPT_NODES = 8  # Texts kept for cursors and repeated reads.
SHOWN_CITES = 40
REDLINE_CONTEXT = 80  # Unchanged characters kept around a change.
TRIMMED_BLOCK = 300  # Characters of an added or removed block shown.
WATCH_INTERVAL = 3600.0
CITATION_ARGUMENTS = frozenset({"address", "citation", "first", "second", "scope"})
LABEL = re.compile(
    r"^(?:§§?\s*[\w.()-]+|(?:Title|Subtitle|Chapter|Subchapter|Part|Subpart)"
    r"\s+[\w.-]+)\s*(?:[—-]\s*)?"
)
COMPLETION = re.compile(
    r"^\s*(?P<title>\d+)(?:(?P<cfr>\s*C\.?\s*F\.?\s*R\.?\s*(?:§+\s*|[Pp]art\s+)?)"
    r"|(?P<slash>/))(?P<rest>.*)$"
)

READ_ONLY = mcp_types.ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True
)


# Structured results: what each tool's output schema describes.


class Place(BaseModel):
    """A place in the CFR to cite or pass back to a tool."""

    citation: str = Field(description='To quote, e.g. "45 CFR 164.502(a)(1)".')
    address: str = Field(description="To pass back, e.g. 45/164.502/a/1.")
    heading: str | None = None
    tokens: int | None = Field(default=None, description="Estimated tokens of text.")


class TitleRow(BaseModel):
    number: int
    name: str
    citation: str
    latest: dt.date | None = Field(description="Latest date with text.")
    amended: dt.date | None = Field(description="When last amended.")
    reserved: bool
    processing: bool = Field(description="The eCFR is applying updates.")


class TitlesResult(BaseModel):
    titles: list[TitleRow]


class Entry(BaseModel):
    citation: str
    address: str
    level: str
    heading: str
    depth: int = Field(description="Levels below the outline's top entry.")
    tokens: int | None = Field(description="Estimated tokens of its text.")
    reserved: bool


class OutlineResult(BaseModel):
    citation: str
    address: str
    heading: str
    date: dt.date | None
    url: str
    tokens: int | None
    depth: int = Field(description="Levels shown below the top entry.")
    deeper: bool = Field(description="Entries below those shown exist.")
    entries: list[Entry] = Field(description="Top entry first, document order.")
    previous: Place | None = None
    next: Place | None = None


class Cited(BaseModel):
    """A cross-reference."""

    text: str = Field(description="As written, e.g. 'paragraph (a) of this section'.")
    citation: str
    address: str | None = Field(description="None outside the CFR.")
    url: str
    kind: str = Field(description="cfr, usc, fr or public_law.")
    cited_in: list[str] = Field(
        default_factory=list, description="Pinpoints that cite it."
    )


class ReadResult(BaseModel):
    citation: str
    address: str
    uri: str
    url: str
    date: dt.date | None
    level: str
    heading: str
    text: str = Field(description="Markdown; paragraph labels are full pinpoints.")
    tokens: int
    total_tokens: int
    complete: bool = Field(description="The text is all of it.")
    cursor: str | None = Field(description="Pass to read to continue; None at end.")
    paragraphs: list[str] = Field(description="Pinpoint citations in this text.")
    anchors: list[str] = Field(description="eCFR ids of those paragraphs.")
    parent: Place | None
    previous: Place | None
    next: Place | None
    children: list[Place]
    references: list[Cited]


class Hit(BaseModel):
    citation: str
    address: str
    heading: str
    excerpt: str
    url: str
    date: dt.date


class SearchResult(BaseModel):
    query: str
    total: int | None = Field(description="Matching versions the eCFR counted.")
    complete: bool
    results: list[Hit]


class ChangeRow(BaseModel):
    citation: str
    address: str
    heading: str
    issued: dt.date
    effective: dt.date
    kind: str = Field(description="substantive, editorial or removed.")


class ChangesResult(BaseModel):
    citation: str
    total: int
    changes: list[ChangeRow]


class Version(BaseModel):
    effective: dt.date
    issued: dt.date
    kinds: list[str]
    changed: list[str] = Field(description="Sections and appendices changed.")


class HistoryResult(BaseModel):
    citation: str
    versions: list[Version]
    total: int


class Redline(BaseModel):
    kind: str = Field(description="added, removed or changed.")
    at: str | None = Field(description="Pinpoint of the block.")
    before: str
    after: str
    redline: str = Field(description="[-deleted-]{+inserted+}, trimmed.")


class CompareResult(BaseModel):
    citation: str
    from_date: dt.date
    to_date: dt.date
    unchanged: int = Field(description="Blocks the same on both dates.")
    differences: list[Redline]
    amendments: list[ChangeRow]


class ReferencesResult(BaseModel):
    citation: str
    date: dt.date | None
    references: list[Cited]


class Resolved(BaseModel):
    citation: str
    bluebook: str
    address: str
    uri: str
    url: str
    level: str
    date: dt.date | None
    exists: bool | None = Field(description="None if it couldn't be checked.")
    heading: str | None = None
    problem: str | None = None


class ResolveResult(BaseModel):
    text: str
    places: list[Resolved]


class QuoteResult(BaseModel):
    verified: bool = Field(description="Word for word at the citation given.")
    found: bool = Field(description="Word for word somewhere in what was read.")
    at_cited: bool
    similarity: float
    citation: str = Field(description="Where it is, or the nearest passage.")
    address: str | None
    url: str | None
    date: str | None
    matched: str
    differences: list[dict[str, str]] = Field(default_factory=list)


class DefinitionRow(BaseModel):
    term: str
    terms: list[str]
    citation: str
    cite: str = Field(description="How to cite it.")
    address: str
    url: str
    date: str | None
    section: str
    applies_to: str | None
    text: str


class DefineResult(BaseModel):
    term: str
    scope: str
    definitions: list[DefinitionRow]
    searched: list[str]
    suggestions: list[str]
    mentions: list[Hit]


class ExportResult(BaseModel):
    format: str
    files: list[str] = Field(description="Paths written, under the export root.")
    bytes: int


class SettingsResult(BaseModel):
    settings: dict[str, Any]
    origins: dict[str, str]
    """Where each setting came from: SDK, CLI, env ..., file ... or default."""
    config_changes_allowed: bool
    export_root: str | None


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


# Progress, logging and cancellation for the calls running in threads.


class Cancelled(Exception):
    """The client cancelled the call; raised in its thread to stop the work."""


class _Call:
    """One tool call in progress: reports to its client from any thread."""

    def __init__(self, ctx: Context, loop: asyncio.AbstractEventLoop) -> None:
        self.ctx = ctx
        self.loop = loop
        # MCP 2026-07-28 deprecates log messages; progress messages remain.
        request = ctx.request_context
        self.logs = (
            request is not None
            and request.protocol_version not in MODERN_PROTOCOL_VERSIONS
        )
        self.cancelled = threading.Event()
        self.parts = 0
        self.parts_done = 0
        self.reported = -1.0

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled("Cancelled")

    def progress(self, done: float, total: float | None, message: str) -> None:
        self.check()
        asyncio.run_coroutine_threadsafe(
            self.ctx.report_progress(done, total, message), self.loop
        )

    def log(self, level: Literal["info", "warning"], message: str) -> None:
        self.check()
        if self.logs:
            asyncio.run_coroutine_threadsafe(self.ctx.log(message, level), self.loop)


_current: contextvars.ContextVar[_Call | None] = contextvars.ContextVar(
    "ecfr_md_mcp_call", default=None
)


class Relay(Progress):
    """Sends a client's progress events to the MCP call that caused them,
    and stops the work if that call was cancelled; ``fallback`` gets
    every event too."""

    def __init__(self, fallback: Progress | None = None) -> None:
        self.fallback = fallback or Progress()

    def _call(self) -> _Call | None:
        call = _current.get()
        if call is not None:
            call.check()
        return call

    def fetching(self, citation: str, date: dt.date) -> None:
        self.fallback.fetching(citation, date)
        if call := self._call():
            call.log("info", f"Fetching {citation} as of {date}")

    def started(self, url: str) -> None:
        self.fallback.started(url)
        self._call()

    def received(self, url: str, done: int, total: int | None) -> None:
        self.fallback.received(url, done, total)
        call = self._call()
        if call is None or call.parts:  # Parts report as they finish.
            return
        if done - call.reported >= 256_000 or (total and done >= total):
            call.reported = done
            call.progress(done, total, "Downloading")

    def finished(self, url: str, size: int, *, cached: bool) -> None:
        self.fallback.finished(url, size, cached=cached)
        self._call()

    def retrying(
        self, url: str, error: Exception, delay: float, attempt: int, attempts: int
    ) -> None:
        self.fallback.retrying(url, error, delay, attempt, attempts)
        if call := self._call():
            call.log("warning", f"Retrying in {delay:.0f}s ({attempt}/{attempts})")

    def parts_started(self, total: int) -> None:
        self.fallback.parts_started(total)
        if call := self._call():
            call.parts, call.parts_done = total, 0
            call.progress(0, total, f"Fetching {total} parts")

    def part_finished(self, identifier: str) -> None:
        self.fallback.part_finished(identifier)
        if call := self._call():
            call.parts_done += 1
            call.progress(call.parts_done, call.parts, f"Part {identifier}")


# Formatting.


def _approx(count: int | None) -> str:
    if count is None:
        return "size unknown"
    if count >= 1_000_000:
        return f"≈{count / 1_000_000:.1f}M"
    if count >= 10_000:
        return f"≈{count // 1000}k"
    if count >= 1000:
        return f"≈{count / 1000:.1f}k"
    return f"≈{count}"


def _description(heading: str) -> str:
    """A heading without its label: "Part 164—Security" -> "Security"."""
    rest = LABEL.sub("", heading, count=1).strip()
    return rest or heading


def _place(item: Outline | Node | None) -> Place | None:
    if item is None:
        return None
    if isinstance(item, Outline):
        return Place(
            citation=item.citation,
            address=item.address,
            heading=item.heading,
            tokens=outline_tokens(item),
        )
    return Place(citation=item.citation, address=item.address, heading=item.heading)


def _short(citation: str, title: int) -> str:
    """A citation without its title, inside the same title."""
    return citation.removeprefix(f"{title} CFR ")


def _pinpoint(node: Node) -> str:
    """ "(a)(1)" for a paragraph; the citation for anything else."""
    path = getattr(node, "path", None)
    return "".join(f"({m})" for m in path) if path else node.citation


def _hit(match: Any) -> Hit:
    return Hit(
        citation=match.citation,
        address=match.address,
        heading=match.heading,
        excerpt=match.excerpt,
        url=match.url,
        date=match.starts_on,
    )


def _change_row(change: Change) -> ChangeRow:
    return ChangeRow(
        citation=change.citation,
        address=change.address,
        heading=change.heading,
        issued=change.issued,
        effective=change.effective,
        kind=change.kind,
    )


def _redline(difference: Difference) -> str:
    """The change as [-deleted-]{+inserted+}, long unchanged runs trimmed."""
    parts = []
    for op, text in difference.words:
        if op == "-":
            parts.append(f"[-{text}-]")
        elif op == "+":
            parts.append(f"{{+{text}+}}")
        elif len(text) > 2 * REDLINE_CONTEXT + 5:
            first = not parts
            head = "" if first else text[:REDLINE_CONTEXT]
            parts.append(f"{head} … {text[-REDLINE_CONTEXT:]}")
        else:
            parts.append(text)
    return "".join(parts)


def _teach(exc: Exception) -> str:
    """The error, with what to do about it."""
    message = (str(exc) or type(exc).__name__).rstrip(".")
    end = "" if message.endswith("?") else "."
    hint = ""
    if isinstance(exc, InvalidCitationError):
        hint = (
            " Citations look like 45 CFR 164.502(a)(1), 45 CFR Part 164 "
            "Subpart E, or addresses such as 45/164.502/a/1."
        )
    elif isinstance(exc, TitleNotFoundError):
        hint = " list_titles() lists the titles."
    elif isinstance(exc, NodeNotFoundError | NotFoundError) and not any(
        said in message for said in ("did you mean", "It has", " has (")
    ):
        hint = " outline() of the part or title shows what exists."
    elif isinstance(exc, ECFRConnectionError):
        hint = " The eCFR couldn't be reached; try again shortly."
    return f"{message}{end}{hint}"


def _cited(references: Iterable[tuple[Reference, Node | None]]) -> list[Cited]:
    """Distinct references, each with the paragraphs that cite it."""
    found: dict[str, Cited] = {}
    for ref, where in references:
        key = str(ref.target.undated()) if ref.target else ref.url
        if key not in found:
            found[key] = Cited(
                text=ref.text,
                citation=ref.citation,
                address=str(ref.target.undated()) if ref.target else None,
                url=ref.url,
                kind=ref.kind,
            )
        if where is not None:
            pin = _pinpoint(where)
            if pin not in found[key].cited_in:
                found[key].cited_in.append(pin)
    return list(found.values())


def _references(index: TextIndex, start: int, end: int) -> list[Cited]:
    """The references in a stretch of a node's text, each resolved where it
    is (so "this section" is the section it's in)."""
    text = index.markdown
    sections = [
        (offset, node)
        for offset, node in index.starts
        if node.level in (Level.SECTION, Level.APPENDIX) or offset == 0
    ]
    found: list[tuple[Reference, Node | None]] = []
    for i, (offset, node) in enumerate(sections):
        stop = sections[i + 1][0] if i + 1 < len(sections) else len(text)
        lo, hi = max(offset, start), min(stop, end)
        if lo >= hi:
            continue
        context = node.section or node
        for ref in find_references(text[lo:hi], context._context(), markdown=True):
            where = index.at(lo + ref.start)
            found.append((ref, where if where.level is Level.PARAGRAPH else None))
    return _cited(found)


class _Kept:
    """The last few texts read, with their indexes, for cursors."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[Node, TextIndex]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, ecfr: ECFR, where: str, date: str | None) -> tuple[Node, TextIndex]:
        key = where if date is None else f"{where}@{date}"
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        node = ecfr.get(where, date=date)
        item = (node, TextIndex(node))
        with self._lock:
            self._items[key] = self._items[node.uri] = item
            while len(self._items) > KEPT_NODES:
                self._items.popitem(last=False)
        return item


# The server.


class Navigator:
    """The tools, resources and prompts, around one client."""

    def __init__(
        self,
        ecfr: ECFR,
        *,
        allow_config_changes: bool = False,
        export_root: Path | None = None,
    ) -> None:
        self.ecfr = ecfr
        self.relay = Relay(ecfr.progress)
        ecfr.progress = self.relay
        self.allow_config_changes = allow_config_changes
        self.export_root = export_root.resolve() if export_root else None
        self.kept = _Kept()

    async def run[T](self, ctx: Context | None, work: Callable[[], T]) -> T:
        """Run blocking work in a thread, relaying its progress to ``ctx``;
        ECFR errors become tool errors that say what to do."""
        call = _Call(ctx, asyncio.get_running_loop()) if ctx is not None else None

        def job() -> T:
            token = _current.set(call)
            try:
                return work()
            finally:
                _current.reset(token)

        try:
            return await anyio.to_thread.run_sync(job, abandon_on_cancel=True)
        except (ECFRError, Cancelled) as exc:
            raise ToolError(_teach(exc)) from exc
        finally:
            if call is not None:
                call.cancelled.set()  # Stops the thread if it's still going.

    # Tools.

    async def list_titles(self, ctx: Context) -> ToolResult:
        """The 50 titles of the CFR, with the latest date of each one's text.
        Start here when you don't know the title; then outline(title)."""
        found = await self.run(ctx, self.ecfr.titles)
        rows = [
            TitleRow(
                number=t.number,
                name=t.name,
                citation=t.citation,
                latest=t.latest,
                amended=t.amended,
                reserved=t.reserved,
                processing=t.processing,
            )
            for t in found
        ]
        lines = ["CFR titles: number, name, latest text date (* = being updated)"]
        for t in rows:
            when = f" · {t.latest}" if t.latest else ""
            lines.append(f"{t.number} {t.name}{when}{' *' if t.processing else ''}")
        lines.append('Next: outline("45") shows a title\'s contents.')
        return ToolResult(
            "\n".join(lines), TitlesResult(titles=rows).model_dump(mode="json")
        )

    async def _whole(self, ctx: Context) -> ToolResult:
        """The outline of the whole CFR: its titles."""
        titles = await self.list_titles(ctx)
        assert titles.structured_content is not None
        top = Entry(
            citation="CFR",
            address="",
            level="cfr",
            heading="Code of Federal Regulations",
            depth=0,
            tokens=None,
            reserved=False,
        )
        rows = [
            Entry(
                citation=t["citation"],
                address=str(t["number"]),
                level="title",
                heading=f"Title {t['number']}—{t['name']}",
                depth=1,
                tokens=None,
                reserved=t["reserved"],
            )
            for t in titles.structured_content["titles"]
        ]
        result = OutlineResult(
            citation="CFR",
            address="",
            heading=top.heading,
            date=None,
            url=f"{self.ecfr.settings.base_url}/current",
            tokens=None,
            depth=1,
            deeper=True,
            entries=[top, *rows],
        )
        return ToolResult(titles.content, result.model_dump(mode="json"))

    async def outline(
        self,
        ctx: Context,
        citation: Annotated[
            str,
            Field(
                description="A title, part, subpart, section, ... e.g. 45, "
                "'45 CFR Part 164', 45/part-164/subpart-E. Empty: the titles."
            ),
        ] = "",
        depth: Annotated[
            int | None,
            Field(
                ge=0, description="Levels below it to show (default: fit ~120 lines)."
            ),
        ] = None,
        date: Annotated[
            str | None, Field(description="As of this date, e.g. 2020-01-01.")
        ] = None,
    ) -> ToolResult:
        """The table of contents of any part of the CFR, without its text,
        with estimated tokens of text per entry so you can plan what to read.
        Shows as many levels as fit unless depth is given."""
        if not citation.strip() or citation.strip().casefold() in {"cfr", "all"}:
            return await self._whole(ctx)
        entry = await self.run(ctx, lambda: self.ecfr.outline(citation, date=date))
        leaf = entry.level in LEAVES
        around = await self.run(
            ctx, lambda: (entry.previous, entry.next) if leaf else (None, None)
        )
        rows, shown, deeper = _outline_rows(entry, depth)
        result = OutlineResult(
            citation=entry.citation,
            address=entry.address,
            heading=entry.heading,
            date=entry.date,
            url=entry.url,
            tokens=outline_tokens(entry),
            depth=shown,
            deeper=deeper,
            entries=rows,
            previous=_place(around[0]),
            next=_place(around[1]),
        )
        return ToolResult(_outline_markdown(result), result.model_dump(mode="json"))

    async def read(
        self,
        ctx: Context,
        citation: Annotated[
            str,
            Field(
                description="What to read: '45 CFR 164.502', '45 CFR 164.502(a)(1)', "
                "45/part-164/subpart-E, ... (not needed with a cursor)."
            ),
        ] = "",
        date: Annotated[
            str | None, Field(description="As of this date, e.g. 2020-01-01.")
        ] = None,
        budget_tokens: Annotated[
            int,
            Field(ge=100, le=MAX_BUDGET, description="Most tokens of text to return."),
        ] = DEFAULT_BUDGET,
        cursor: Annotated[
            str | None,
            Field(description="Continue a previous read: the cursor it returned."),
        ] = None,
    ) -> ToolResult:
        """The text of any part of the CFR as Markdown: as much as fits the
        budget, ending at a paragraph, with a cursor to continue (it never
        cuts text off silently). Also gives the canonical citation, date,
        eCFR link, the paragraphs included, what's around it (parent,
        previous, next, children) and the cross-references in the text."""
        if cursor:
            where, offset = parse_cursor(cursor)
            when = None
        elif citation.strip():
            where, offset, when = citation, 0, date
        else:
            raise ToolError("Say what to read, e.g. read('45 CFR 164.502')")

        def work() -> ReadResult:
            node, index = self.kept.get(self.ecfr, where, when)
            found = passage(node, budget_tokens, offset, index=index)
            parent, previous, following, children = _around(node)
            return ReadResult(
                citation=node.citation,
                address=node.address,
                uri=node.uri,
                url=node.url,
                date=node.date,
                level=node.level.value,
                heading=node.heading,
                text=found.text,
                tokens=found.tokens,
                total_tokens=found.total_tokens,
                complete=found.complete,
                cursor=found.cursor,
                paragraphs=[p.citation for p in found.paragraphs],
                anchors=[a for p in found.paragraphs if (a := p.anchor)],
                parent=parent,
                previous=previous,
                next=following,
                children=children,
                references=_references(index, found.start, found.end),
            )

        result = await self.run(ctx, work)
        return ToolResult(_read_markdown(result), result.model_dump(mode="json"))

    async def search(
        self,
        ctx: Context,
        query: Annotated[
            str, Field(description="Words to find, e.g. minimum necessary.")
        ],
        within: Annotated[
            str | None,
            Field(description="Only in this title, part, ... e.g. '45 CFR Part 164'."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
        date: Annotated[
            str | None, Field(description="Text in effect on this date.")
        ] = None,
        phrase: Annotated[
            bool, Field(description="Words in order as a phrase (else anywhere).")
        ] = True,
    ) -> ToolResult:
        """Full-text search of the CFR: sections and appendices that contain
        the words, best first, with an excerpt."""
        filters: dict[str, Any] = {}
        if within:
            filters["within"] = within
        if date:
            filters["date"] = date
        found: Matches = await self.run(
            ctx,
            lambda: self.ecfr.search(query, limit=limit, phrase=phrase, **filters),
        )
        result = SearchResult(
            query=query,
            total=found.total,
            complete=found.complete,
            results=[_hit(m) for m in found],
        )
        return ToolResult(
            _hits_markdown(result.results, f"Search {query!r}", result.complete),
            result.model_dump(mode="json"),
        )

    async def changes(
        self,
        ctx: Context,
        citation: Annotated[str, Field(description="A title, part, section, ...")],
        since: Annotated[str | None, Field(description="Issued on or after.")] = None,
        until: Annotated[str | None, Field(description="Issued on or before.")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> ToolResult:
        """Each change the eCFR issued to sections or appendices in a part of
        the CFR, newest first: when issued, when effective, and whether
        substantive, editorial or a removal. No text is downloaded."""
        found = await self.run(
            ctx, lambda: self.ecfr.changes(citation, since=since, until=until)
        )
        result = ChangesResult(
            citation=citation,
            total=len(found),
            changes=[_change_row(c) for c in found[:limit]],
        )
        lines = [
            f"{len(found)} changes to {citation}, newest first "
            "(issued · effective · kind):"
        ]
        lines += [
            f"{c.issued} · {c.effective} · {c.kind} · {c.citation} {c.heading}"
            for c in result.changes
        ]
        if len(found) > limit:
            lines.append(f"… {len(found) - limit} older; raise limit or set until.")
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    async def history(
        self,
        ctx: Context,
        citation: Annotated[str, Field(description="A section, part, ...")],
        limit: Annotated[int, Field(ge=1, le=500)] = 30,
    ) -> ToolResult:
        """The versions of a part of the CFR over time: each date its text
        changed, newest first, and what changed. To see a change,
        compare(citation, from_date=<the day before>, to_date=<the date>)."""
        found = await self.run(ctx, lambda: self.ecfr.changes(citation))
        by_date: dict[dt.date, list[Change]] = {}
        for change in found:
            by_date.setdefault(change.effective, []).append(change)
        versions = [
            Version(
                effective=effective,
                issued=min(c.issued for c in changes),
                kinds=sorted({c.kind for c in changes}),
                changed=sorted({c.citation for c in changes}),
            )
            for effective, changes in sorted(by_date.items(), reverse=True)
        ]
        result = HistoryResult(
            citation=citation, versions=versions[:limit], total=len(versions)
        )
        lines = [f"{len(versions)} versions of {citation}, newest first:"]
        for v in result.versions:
            changed = ", ".join(v.changed[:4]) + (
                f" and {len(v.changed) - 4} more" if len(v.changed) > 4 else ""
            )
            lines.append(
                f"{v.effective} ({'/'.join(v.kinds)}; issued {v.issued}) {changed}"
            )
        if len(versions) > limit:
            lines.append(f"… {len(versions) - limit} older versions; raise limit.")
        if versions:
            day = versions[0].effective - dt.timedelta(days=1)
            lines.append(
                f'See a change: compare("{citation}", from_date="{day}", '
                f'to_date="{versions[0].effective}")'
            )
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    async def compare(
        self,
        ctx: Context,
        citation: Annotated[str, Field(description="A section, part, ...")],
        from_date: Annotated[str, Field(description="The earlier date.")],
        to_date: Annotated[
            str | None, Field(description="The later date (default: latest).")
        ] = None,
    ) -> ToolResult:
        """What changed in the text between two dates, word by word, with the
        amendments issued between them and their effective dates."""

        def work() -> CompareResult:
            before = self.ecfr.get(citation, date=from_date)
            after = self.ecfr.get(citation, date=to_date)
            assert before.date is not None and after.date is not None
            if before.date > after.date:
                raise InvalidParameterError(
                    f"from_date ({before.date}) is after to_date ({after.date})"
                )
            index = {"before": TextIndex(before), "after": TextIndex(after)}
            found = compare_texts(
                after.citation,
                index["before"].markdown,
                index["after"].markdown,
                before.date,
                after.date,
            )
            amendments = self.ecfr.changes(
                citation,
                since=before.date + dt.timedelta(days=1),
                until=after.date,
            )
            return CompareResult(
                citation=after.citation,
                from_date=before.date,
                to_date=after.date,
                unchanged=found.unchanged,
                differences=[
                    Redline(
                        kind=d.kind,
                        at=_located(
                            index["after" if d.after else "before"], d.after or d.before
                        ),
                        before=d.before,
                        after=d.after,
                        redline=_redline(d),
                    )
                    for d in found.differences
                ],
                amendments=[_change_row(c) for c in amendments],
            )

        result = await self.run(ctx, work)
        return ToolResult(_compare_markdown(result), result.model_dump(mode="json"))

    async def references(
        self,
        ctx: Context,
        citation: Annotated[str, Field(description="A section, paragraph or part.")],
        date: Annotated[str | None, Field(description="As of this date.")] = None,
    ) -> ToolResult:
        """What a part of the CFR cites: each place it refers to ("§ 164.501",
        "paragraph (a) of this section", "42 U.S.C. 1320d", ...), resolved,
        with the paragraphs that cite it."""

        def work() -> ReferencesResult:
            node, index = self.kept.get(self.ecfr, citation, date)
            return ReferencesResult(
                citation=node.citation,
                date=node.date,
                references=_references(index, 0, len(index.markdown)),
            )

        result = await self.run(ctx, work)
        title = Address.parse(result.citation).title
        lines = [f"{result.citation} cites {len(result.references)} places:"]
        for ref in result.references:
            where = f" (in {', '.join(ref.cited_in[:6])})" if ref.cited_in else ""
            target = ref.address or ref.url
            lines.append(f"{_short(ref.citation, title)} · {target}{where}")
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    async def cited_by(
        self,
        ctx: Context,
        citation: Annotated[str, Field(description="A section or part.")],
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        date: Annotated[
            str | None, Field(description="Text in effect on this date.")
        ] = None,
    ) -> ToolResult:
        """Sections that may cite a section or part. Approximate: found by
        searching for its number, so "this section" and the like are missed."""
        found = await self.run(
            ctx, lambda: self.ecfr.cited_by(citation, limit=limit, date=date)
        )
        result = SearchResult(
            query=citation,
            total=found.total,
            complete=found.complete,
            results=[_hit(m) for m in found],
        )
        return ToolResult(
            _hits_markdown(result.results, f"May cite {citation}", result.complete),
            result.model_dump(mode="json"),
        )

    async def resolve_citation(
        self,
        ctx: Context,
        text: Annotated[
            str,
            Field(
                description="A citation in any form: '45 C.F.R. § 164.502(a)(1)', "
                "an eCFR or LII URL, 45/164.502, a list '§§ 164.502, 164.504', ..."
            ),
        ],
    ) -> ToolResult:
        """Turn any citation into canonical form (citation, Bluebook, address,
        eCFR URL) and check that it exists; says what does if it doesn't."""

        def work() -> ResolveResult:
            places = Address.parse_many(text)
            return ResolveResult(
                text=text, places=[self._resolve(p) for p in places[:20]]
            )

        try:
            result = await self.run(ctx, work)
        except ToolError:
            raise
        lines = []
        for p in result.places:
            if p.exists:
                lines.append(f"{p.citation} · {p.address} · {p.heading} · {p.url}")
            else:
                lines.append(f"{p.citation} · {p.address} · NOT FOUND: {p.problem}")
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    def _resolve(self, place: Address) -> Resolved:
        resolved = Resolved(
            citation=place.citation(),
            bluebook=place.citation("bluebook"),
            address=str(place),
            uri=place.uri(),
            url=place.url(),
            level=place.level,
            date=place.date,
            exists=None,
        )
        try:
            if place.paragraph:
                node = self.ecfr.get(str(place))
                section = node.section
                heading = node.heading or (section.heading if section else "")
            else:
                heading = self.ecfr.outline(str(place)).heading
        except (NodeNotFoundError, NotFoundError, TitleNotFoundError) as exc:
            return resolved.model_copy(update={"exists": False, "problem": _teach(exc)})
        return resolved.model_copy(update={"exists": True, "heading": heading})

    async def verify_quote(
        self,
        ctx: Context,
        citation: Annotated[
            str, Field(description="The pinpoint quoted, e.g. 45 CFR 164.502(a)(1).")
        ],
        text: Annotated[str, Field(description="The quoted words.")],
        date: Annotated[str | None, Field(description="As of this date.")] = None,
    ) -> ToolResult:
        """Check a quotation before you use it: is it word for word at that
        citation (ignoring formatting, quote marks and dashes)? If not, where
        is it, or what's the nearest text and how does it differ?"""
        check = await self.run(
            ctx, lambda: self.ecfr.verify_quote(citation, text, date=date)
        )
        data = check.to_dict()
        result = QuoteResult(**data)
        if check.verified:
            lines = [
                f"VERIFIED: the quote is in {check.citation} (as of {check.date})."
            ]
        elif check.found:
            lines = [
                f"WRONG PINPOINT: the quote is not in {citation}; it is word for "
                f"word in {check.citation} (as of {check.date})."
            ]
        elif check.matched:
            diff = _redline(Difference("changed", check.quote, check.matched))
            lines = [
                f"NOT FOUND word for word in {citation} (as of {check.date}). "
                f"Nearest: {check.citation}, {check.similarity:.0%} alike. "
                "[-quote only-]{+text only+}:",
                diff,
            ]
        else:
            lines = [f"NOT FOUND in {citation} (as of {check.date}); nothing similar."]
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    async def define(
        self,
        ctx: Context,
        term: Annotated[str, Field(description="The term, e.g. 'covered entity'.")],
        scope: Annotated[
            str,
            Field(
                description="Where it's used: a section or part, e.g. 45 CFR 164.502."
            ),
        ],
        date: Annotated[str | None, Field(description="As of this date.")] = None,
    ) -> ToolResult:
        """The definition of a term that governs a place in the CFR, from the
        definitions sections that apply there (e.g. 45 CFR 160.103 for
        HIPAA's Part 164), with its pinpoint citation."""
        found = await self.run(ctx, lambda: self.ecfr.define(term, scope, date=date))
        data = found.to_dict()
        result = DefineResult(
            term=data["term"],
            scope=data["scope"],
            definitions=[DefinitionRow(**d) for d in data["definitions"]],
            searched=data["searched"],
            suggestions=data["suggestions"],
            mentions=[_hit(m) for m in found.mentions],
        )
        return ToolResult(_define_markdown(result), result.model_dump(mode="json"))

    async def export(
        self,
        ctx: Context,
        citation: Annotated[
            str, Field(description="What to save, e.g. 45 CFR Part 164.")
        ],
        format: Annotated[
            Format, Field(description="markdown, text, html, json, epub, docx or pdf.")
        ] = "markdown",
        path: Annotated[
            str,
            Field(description="File (or with tree, directory) under the export root."),
        ] = "",
        date: Annotated[str | None, Field(description="As of this date.")] = None,
        tree: Annotated[
            bool,
            Field(description="A file per section (per part for json/epub/docx/pdf)."),
        ] = False,
        overwrite: Annotated[
            bool, Field(description="Replace existing files.")
        ] = False,
    ) -> ToolResult:
        """Save regulation text to a local file in any format, under the
        export directory the server was started with."""
        assert self.export_root is not None  # Registered only with a root.
        root = self.export_root
        target = (root / path).resolve() if path else root
        if not target.is_relative_to(root):
            raise ToolError(
                f"{path} is outside the export directory {root}; give a path inside it"
            )
        if not tree and (not path or target.is_dir()):
            target = target / Address.parse(citation).file_name().replace(
                ".md", _suffix(format)
            )
        if not overwrite and not tree and target.exists():
            raise ToolError(
                f"{target.relative_to(root)} exists; pass overwrite=true to replace it"
            )

        def work() -> list[Path]:
            if tree:
                return self.ecfr.save(
                    citation, target, tree=True, date=date, format=format
                )
            return [self.ecfr.save(citation, target, date=date, format=format)]

        files = await self.run(ctx, work)
        result = ExportResult(
            format=format,
            files=[str(f.resolve().relative_to(root)) for f in files],
            bytes=sum(f.stat().st_size for f in files),
        )
        shown = ", ".join(result.files[:5]) + (
            f" and {len(files) - 5} more" if len(files) > 5 else ""
        )
        text = f"Saved {citation} as {format}: {shown} ({result.bytes:,} bytes)"
        return ToolResult(text, result.model_dump(mode="json"))

    def settings_result(self) -> SettingsResult:
        settings = self.ecfr.settings
        return SettingsResult(
            settings=settings.model_dump(mode="json"),
            origins={k: origin for k, (_, origin) in settings.explain().items()},
            config_changes_allowed=self.allow_config_changes,
            export_root=str(self.export_root) if self.export_root else None,
        )

    async def settings(self) -> ToolResult:
        """The server's settings: the eCFR host, cache, rate limits, ... and
        whether they can be changed."""
        result = self.settings_result()
        lines = [
            f"{k} = {v}  ({result.origins[k]})" for k, v in result.settings.items()
        ]
        lines.append(
            "Changes allowed: configure(changes)"
            if result.config_changes_allowed
            else "Read-only (the server was started without --allow-config-changes)"
        )
        return ToolResult("\n".join(lines), result.model_dump(mode="json"))

    async def configure(
        self,
        changes: Annotated[
            dict[str, Any],
            Field(description='Settings to change, e.g. {"rate_limit": 0.5}.'),
        ],
    ) -> ToolResult:
        """Change the server's settings (hosts, cache, rate limits, ...). Takes
        effect for the next call."""
        current = self.ecfr.settings.model_dump()
        unknown = set(changes) - set(current)
        if unknown:
            raise ToolError(
                f"No setting {', '.join(sorted(unknown))}; settings() lists them"
            )
        try:
            settings = Settings.model_validate(current | changes)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in exc.errors(include_url=False)
            )
            raise ToolError(f"Invalid settings: {problems}") from exc
        self.ecfr = ECFR(settings, progress=self.relay)
        return await self.settings()

    # Resources.

    def text_resource(self, address: str) -> str:
        """The text at an address as Markdown, e.g. cfr:45/164.502."""
        try:
            node = self.ecfr.get(address)
        except ECFRError as exc:
            raise ResourceError(_teach(exc)) from exc
        text = node.to_markdown()
        if len(text) > MAX_RESOURCE_CHARS:
            cut = passage(node, MAX_RESOURCE_CHARS // 4, pinpoints=False)
            return (
                cut.text
                + f"\n[Shown {cut.end:,} of {cut.total:,} characters. The read "
                f'tool continues: read(cursor="{cut.cursor}")]\n'
            )
        return text

    def titles_resource(self) -> str:
        try:
            found = self.ecfr.titles()
        except ECFRError as exc:
            raise ResourceError(_teach(exc)) from exc
        return "\n".join(
            f"{t.number} {t.name}" + (f" · {t.latest}" if t.latest else "")
            for t in found
        )

    # Completions.

    def complete(self, value: str) -> list[str]:
        """Citations that start with ``value``: titles, then the parts and
        sections of the title it names."""
        found = COMPLETION.match(value)
        if found is None:
            numbers = [str(t.number) for t in self.ecfr.titles() if not t.reserved]
            prefix = value.strip()
            return (
                [n for n in numbers if n.startswith(prefix)]
                if prefix.isdigit() or not prefix
                else []
            )
        title = int(found["title"])
        rest = found["rest"].strip().removeprefix("part-").removeprefix("§").strip()
        outline = self.ecfr.outline(title)
        # Parts until a section number's dot is typed: "164" then "164.5".
        level = Level.SECTION if "." in rest else Level.PART
        if found["slash"]:
            form = f"{title}/part-{{}}" if level == Level.PART else f"{title}/{{}}"
        else:
            form = (
                f"{title} CFR Part {{}}" if level == Level.PART else f"{title} CFR {{}}"
            )
        return [
            form.format(entry.identifier)
            for entry in outline.iter(level)
            if not entry.reserved
            and entry.identifier
            and entry.identifier.startswith(rest)
        ]


def _suffix(format: str) -> str:
    from ecfr_md.render.formats import SUFFIXES

    return SUFFIXES[format]  # type: ignore[index]


def _outline_rows(entry: Outline, depth: int | None) -> tuple[list[Entry], int, bool]:
    """The entries to show, how many levels deep, and whether there's more."""
    levels: list[list[tuple[Outline, int]]] = [[(entry, 0)]]
    while levels[-1]:
        levels.append([(c, d + 1) for e, d in levels[-1] for c in e.children])
    levels.pop()
    most = len(levels) - 1
    if depth is None:
        shown, count = 0, 1
        for d in range(1, most + 1):
            count += len(levels[d])
            if count > OUTLINE_LINES and shown >= 1:
                break
            shown = d
    else:
        shown = min(depth, most)
    rows: list[Entry] = []

    def walk(e: Outline, d: int) -> None:
        if len(rows) >= OUTLINE_MAX_LINES:
            return
        rows.append(
            Entry(
                citation=e.citation,
                address=e.address,
                level=str(e.level),
                heading=e.heading,
                depth=d,
                tokens=outline_tokens(e),
                reserved=e.reserved,
            )
        )
        if d < shown:
            for child in e.children:
                walk(child, d + 1)

    walk(entry, 0)
    total = sum(len(level) for level in levels[: shown + 1])
    return rows, shown, shown < most or len(rows) < total


def _outline_markdown(result: OutlineResult) -> str:
    top = result.entries[0]
    lines = [
        f"{result.citation}: {_description(result.heading)} · as of {result.date} "
        f"· {_approx(result.tokens)} tokens"
    ]
    for row in result.entries[1:]:
        indent = "  " * (row.depth - 1)
        where = "" if row.level == "section" else f" · {row.address}"
        lines.append(f"{indent}{row.heading}{where} · {_approx(row.tokens)}")
    if result.previous or result.next:
        lines.append(
            f"Previous: {result.previous.citation if result.previous else 'none'} · "
            f"Next: {result.next.citation if result.next else 'none'}"
        )
    if result.deeper:
        lines.append(
            f"(Shown {result.depth} levels; outline() of an entry above, or depth=, "
            "shows more.)"
        )
    if top.level in ("section", "appendix"):
        lines.append(f'Read it: read("{top.address}")')
    return "\n".join(lines)


def _around(node: Node) -> tuple[Place | None, Place | None, Place | None, list[Place]]:
    """Parent, previous, next and children of a node, to move from it."""
    if node.level is Level.PARAGRAPH:
        parent = node.parent
        assert parent is not None  # Paragraphs are in sections.
        siblings = parent.children
        i = next(i for i, s in enumerate(siblings) if s is node)
        previous = siblings[i - 1] if i else None
        following = siblings[i + 1] if i + 1 < len(siblings) else None
        children = [_place(c) for c in node.children]
        return (
            _place(parent),
            _place(previous),
            _place(following),
            [c for c in children if c],
        )
    try:
        entry = node.outline
    except ECFRError:
        entry = None
    if entry is None:
        return _place(node.parent), None, None, []
    if node.level in (Level.SECTION, Level.APPENDIX):
        previous, following = entry.previous, entry.next
        children = [_place(c) for c in node.children]
    else:
        siblings = entry.parent.children if entry.parent else ()
        i = next((i for i, s in enumerate(siblings) if s is entry), 0)
        previous = siblings[i - 1] if i else None
        following = siblings[i + 1] if i + 1 < len(siblings) else None
        children = [_place(c) for c in entry.children]
    return (
        _place(entry.up),
        _place(previous),
        _place(following),
        [c for c in children if c],
    )


def _read_markdown(result: ReadResult) -> str:
    title = Address.parse(result.address).title
    head = [
        f"{result.citation} · {result.address} · as of {result.date} · {result.url}"
    ]
    moves = []
    if result.parent:
        moves.append(f"in {result.parent.citation}")
    if result.previous:
        moves.append(f"previous {_short(result.previous.citation, title)}")
    if result.next:
        moves.append(f"next {_short(result.next.citation, title)}")
    if moves:
        head.append(" · ".join(moves))
    if result.complete:
        head.append(f"Complete: {_approx(result.tokens)} tokens.")
    else:
        last = result.paragraphs[-1] if result.paragraphs else None
        through = f" through {_short(last, title)}" if last else ""
        rest = (
            f'More: read(cursor="{result.cursor}")'
            if result.cursor
            else "This is the end."
        )
        head.append(
            f"Part of the text: {_approx(result.tokens)} of "
            f"{_approx(result.total_tokens)} tokens{through}. {rest}"
        )
    lines = ["\n".join(head), "", result.text.rstrip()]
    # "This section", "this part" and the like are where the reader is.
    cfr = [
        r
        for r in result.references
        if r.address and not r.text.casefold().startswith("this ")
    ]
    if cfr:
        shown = ", ".join(_short(r.citation, title) for r in cfr[:SHOWN_CITES])
        more = f" and {len(cfr) - SHOWN_CITES} more" if len(cfr) > SHOWN_CITES else ""
        lines += ["", f"Cites: {shown}{more}"]
    other = [r.citation for r in result.references if not r.address]
    if other:
        lines.append(f"Also cites: {', '.join(other[:10])}")
    if result.cursor and not result.complete:
        lines += ["", f'[Continues: read(cursor="{result.cursor}")]']
    return "\n".join(lines)


def _hits_markdown(hits: Sequence[Hit], what: str, complete: bool) -> str:
    if not hits:
        return f"{what}: nothing found."
    lines = [f"{what}: {len(hits)} sections{'' if complete else ' (more exist)'}"]
    for h in hits:
        excerpt = f" — {h.excerpt}" if h.excerpt else ""
        lines.append(f"{h.citation} {h.heading}{excerpt}")
    return "\n".join(lines)


def _located(index: TextIndex, block: str) -> str | None:
    at = index.markdown.find(block)
    if at < 0:
        return None
    return index.at(at).citation


def _compare_markdown(result: CompareResult) -> str:
    counts = Counter(d.kind for d in result.differences)
    summary = ", ".join(f"{n} {k}" for k, n in counts.items()) or "no changes"
    lines = [
        f"{result.citation}: {result.from_date} → {result.to_date}: {summary}; "
        f"{result.unchanged} blocks unchanged."
    ]
    if result.amendments:
        lines.append("Amendments (issued · effective · kind):")
        lines += [
            f"  {a.issued} · {a.effective} · {a.kind} · {a.citation}"
            for a in result.amendments[:20]
        ]
    for i, d in enumerate(result.differences, 1):
        shown = d.redline
        if d.kind != "changed" and len(shown) > TRIMMED_BLOCK:
            shown = shown[:TRIMMED_BLOCK].rstrip() + "…"
        lines.append(f"{i}. {d.kind} at {d.at or result.citation}: {shown}")
    return "\n".join(lines)


def _define_markdown(result: DefineResult) -> str:
    if not result.definitions:
        lines = [f'No definition of "{result.term}" found for {result.scope}.']
        if result.suggestions:
            lines.append(f"Defined terms like it: {', '.join(result.suggestions)}")
        if result.searched:
            lines.append(f"Read: {', '.join(result.searched)}")
        if result.mentions:
            lines.append("May be defined in:")
            lines += [f"  {m.citation} — {m.excerpt}" for m in result.mentions]
        return "\n".join(lines)
    lines = []
    for d in result.definitions:
        applies = f"; applies to {d.applies_to}" if d.applies_to else ""
        lines += [
            f'"{d.term}" for {result.scope}: {d.cite}{applies} (as of {d.date})',
            d.text.rstrip(),
            "",
        ]
    return "\n".join(lines).rstrip()


# Prompts.


def summarize_obligations(
    citation: Annotated[str, Field(description="A section, subpart or part.")],
) -> str:
    """Summarize who must do what in a part of the CFR, with pinpoint citations."""
    return f"""Summarize the obligations in {citation}.

1. outline("{citation}") to see what it holds and how large each piece is.
2. read each section that isn't reserved, following cursors until complete.
3. List each obligation: who must (or must not) do what, when, and the \
conditions and exceptions ("must", "shall", "may not", "is required to").
4. define(term, "{citation}") for defined terms the obligations turn on.
5. verify_quote(pinpoint, words) for anything you quote.

Answer with a table: Obligation | Who | Pinpoint (e.g. 45 CFR 164.502(a)(1)) \
| Conditions and exceptions. Say the date of the text."""


def what_changed(
    citation: Annotated[str, Field(description="A section, subpart or part.")],
    since: Annotated[str, Field(description="A date, e.g. 2024-01-01.")],
) -> str:
    """Explain what changed in a part of the CFR since a date."""
    return f"""Explain what changed in {citation} since {since}.

1. history("{citation}") for the versions; keep those after {since}.
2. compare("{citation}", from_date="{since}") for the redline, with the \
amendments and their effective dates.
3. For each change, read the paragraph around it if the redline isn't enough.

Answer per amendment: effective date, what changed (pinpoint), and its \
effect in plain words. Note editorial changes briefly."""


def compare_provisions(
    first: Annotated[str, Field(description="A citation, e.g. 45 CFR 164.506.")],
    second: Annotated[str, Field(description="Another citation.")],
) -> str:
    """Compare two provisions of the CFR side by side."""
    return f"""Compare {first} with {second}.

1. read("{first}") and read("{second}"), following cursors until complete.
2. define the terms that differ or decide the outcome, in each one's scope.
3. Line them up: scope and who they apply to, what each requires or allows, \
conditions, exceptions, and how they cross-reference each other \
(references shows what each cites).

Answer with a table of the points of comparison, each with pinpoint \
citations to both; verify_quote anything you quote."""


def find_term(
    term: Annotated[str, Field(description="A word or phrase.")],
    scope: Annotated[
        str, Field(description="Where to look, e.g. 45 CFR Part 164 (optional).")
    ] = "",
) -> str:
    """Find where the CFR defines a term and where it uses it."""
    where = f" in {scope}" if scope else ""
    define = (
        f'1. define("{term}", "{scope}") for the governing definition.'
        if scope
        else f'1. search("{term} means") to find where it is defined, then '
        f'define("{term}", <that section>).'
    )
    within = f', within="{scope}"' if scope else ""
    return f"""Find where the CFR defines and uses "{term}"{where}.

{define}
2. search("{term}"{within}) for the sections that use it.
3. read the passages that matter, citing paragraphs exactly.

Answer with the definition (quoted, with its citation and what it applies \
to), then the main places it's used, each with a pinpoint and a line on how."""


# Change subscriptions.


class Watch:
    """Watches subscribed ``cfr:`` resources and notifies subscribers when
    the eCFR issues a change to them.

    Serves both kinds of subscription: ``subscriptions/listen`` streams
    (MCP 2026-07-28 and later) and ``resources/subscribe`` (earlier
    versions).
    """

    def __init__(self, navigator: Navigator, interval: float = WATCH_INTERVAL) -> None:
        self.navigator = navigator
        self.interval = interval
        self.bus = InMemorySubscriptionBus()
        self.listener = ListenHandler(self.bus)
        self.watching: Counter[str] = Counter()
        self.sessions: dict[str, list[ServerSession]] = {}
        self.seen: dict[str, dt.date] = {}  # The issue date checked up to.

    def _issued(self, uri: str) -> dt.date | None:
        """The latest issue date of the title of a ``cfr:`` URI; None for
        dated URIs, whose text doesn't change."""
        place = Address.parse(uri)
        if place.date is not None:
            return None
        title = next(
            (t for t in self.navigator.ecfr.titles() if t.number == place.title), None
        )
        return title.raw.latest_issue_date if title else None

    async def _watch(self, uri: str) -> None:
        Address.parse(uri)  # Raises if it names no place.
        self.watching[uri] += 1
        if uri not in self.seen:
            issued = await anyio.to_thread.run_sync(self._issued, uri)
            if issued is not None:
                self.seen[uri] = issued

    def _unwatch(self, uri: str) -> None:
        self.watching[uri] -= 1
        if self.watching[uri] <= 0:
            del self.watching[uri]
            self.seen.pop(uri, None)

    async def subscribe(
        self, ctx: Any, params: mcp_types.SubscribeRequestParams
    ) -> mcp_types.EmptyResult:
        uri = str(params.uri)
        try:
            await self._watch(uri)
        except ECFRError as exc:
            raise MCPError(mcp_types.INVALID_PARAMS, _teach(exc)) from exc
        self.sessions.setdefault(uri, []).append(ctx.session)
        return mcp_types.EmptyResult()

    async def unsubscribe(
        self, ctx: Any, params: mcp_types.UnsubscribeRequestParams
    ) -> mcp_types.EmptyResult:
        uri = str(params.uri)
        sessions = self.sessions.get(uri, [])
        if ctx.session in sessions:
            sessions.remove(ctx.session)
            self._unwatch(uri)
        return mcp_types.EmptyResult()

    async def listen(
        self, ctx: Any, params: mcp_types.SubscriptionsListenRequestParams
    ) -> mcp_types.SubscriptionsListenResult:
        uris = [
            u
            for u in params.notifications.resource_subscriptions or ()
            if u.startswith("cfr:")
        ]
        watched = []
        try:
            for uri in uris:
                try:
                    await self._watch(uri)
                    watched.append(uri)
                except ECFRError:
                    continue  # Never fires, as the spec has it for unknown URIs.
            return await self.listener(ctx, params)
        finally:
            for uri in watched:
                self._unwatch(uri)

    def _changed(self) -> list[str]:
        changed = []
        for uri in list(self.watching):
            since = self.seen.get(uri)
            issued = self._issued(uri)
            if since is None or issued is None or issued <= since:
                continue
            place = Address.parse(uri)
            found = self.navigator.ecfr.changes(
                str(place), since=since + dt.timedelta(days=1)
            )
            self.seen[uri] = issued
            if found:
                changed.append(uri)
        return changed

    async def check(self) -> list[str]:
        """Look for changes now; notify and return the URIs that changed."""
        changed = await anyio.to_thread.run_sync(self._changed)
        for uri in changed:
            await self.bus.publish(ResourceUpdated(uri))
            for session in self.sessions.get(uri, []):
                await session.send_resource_updated(uri)
        return changed

    async def run(self) -> None:
        """Check every ``interval`` seconds, for as long as the server runs."""
        while True:
            await anyio.sleep(self.interval)
            if self.watching:
                try:
                    await self.check()
                except ECFRError:
                    continue  # Offline for now; try again next time.


def server(
    ecfr: ECFR | None = None,
    *,
    allow_config_changes: bool = False,
    export_root: str | Path | None = None,
    watch_interval: float = WATCH_INTERVAL,
) -> FastMCP:
    """The MCP server, around ``ecfr`` (a new client if None).

    ``allow_config_changes`` adds a ``configure`` tool that changes the
    settings. ``export_root`` adds an ``export`` tool that saves files
    below that directory, and nowhere else. ``watch_interval`` is how often,
    in seconds, subscribed resources are checked for changes.

    Run it with ``.run()`` (stdio) or ``.run("http", port=...)``, or serve
    ``.http_app()`` from another ASGI app.
    """
    navigator = Navigator(
        ecfr or ECFR(),
        allow_config_changes=allow_config_changes,
        export_root=Path(export_root) if export_root is not None else None,
    )
    return build(navigator, Watch(navigator, watch_interval))


def build(navigator: Navigator, watch: Watch) -> FastMCP:
    """The server for a navigator and the watch on its subscriptions."""

    @lifespan
    async def watching(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        task = asyncio.create_task(watch.run())
        try:
            yield {"navigator": navigator, "watch": watch}
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    mcp = FastMCP(
        "ecfr",
        instructions=INSTRUCTIONS,
        version=__version__,
        website_url="https://github.com/ptmorris05/ecfr-md",
        lifespan=watching,
    )
    tools: list[tuple[Callable[..., Any], str, type[BaseModel], bool]] = [
        (navigator.list_titles, "List CFR titles", TitlesResult, True),
        (navigator.outline, "Outline (table of contents)", OutlineResult, True),
        (navigator.read, "Read CFR text", ReadResult, True),
        (navigator.search, "Search the CFR", SearchResult, True),
        (navigator.changes, "List changes", ChangesResult, True),
        (navigator.history, "Version history", HistoryResult, True),
        (navigator.compare, "Compare dates (redline)", CompareResult, True),
        (navigator.references, "Cross-references", ReferencesResult, True),
        (navigator.cited_by, "What cites it", SearchResult, True),
        (navigator.resolve_citation, "Resolve a citation", ResolveResult, True),
        (navigator.verify_quote, "Verify a quotation", QuoteResult, True),
        (navigator.define, "Define a term", DefineResult, True),
        (navigator.settings, "Server settings", SettingsResult, False),
    ]
    for function, title, model, world in tools:
        mcp.tool(
            function,
            title=title,
            output_schema=_schema(model),
            annotations=READ_ONLY.model_copy(
                update={"title": title, "open_world_hint": world}
            ),
        )
    if navigator.export_root is not None:
        mcp.tool(
            navigator.export,
            title="Export to a file",
            output_schema=_schema(ExportResult),
            annotations=mcp_types.ToolAnnotations(
                title="Export to a file",
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=True,
            ),
        )
    if navigator.allow_config_changes:
        mcp.tool(
            navigator.configure,
            title="Change settings",
            output_schema=_schema(SettingsResult),
            annotations=mcp_types.ToolAnnotations(
                title="Change settings",
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

    @mcp.resource(
        "cfr:{address*}",
        name="cfr-text",
        title="CFR text",
        description="The text of any place in the CFR as Markdown, by address: "
        "cfr:45/164.502, cfr:45/part-164/subpart-E, cfr:45/164.502@2020-01-01.",
        mime_type="text/markdown",
    )
    async def text(address: str) -> str:
        return await anyio.to_thread.run_sync(navigator.text_resource, address)

    @mcp.resource(
        "cfr:titles",
        name="cfr-titles",
        title="CFR titles",
        description="The titles of the CFR and the latest date of each one's text.",
        mime_type="text/plain",
    )
    async def titles() -> str:
        return await anyio.to_thread.run_sync(navigator.titles_resource)

    @mcp.resource(
        "ecfr-md:settings",
        name="settings",
        title="Server settings",
        description="The server's settings, as JSON.",
        mime_type="application/json",
    )
    def settings() -> str:
        return navigator.settings_result().model_dump_json(indent=2)

    for prompt in (summarize_obligations, what_changed, compare_provisions, find_term):
        mcp.prompt(prompt)

    @mcp.completion
    async def complete(
        ref: Any, argument: mcp_types.CompletionArgument, context: Any
    ) -> list[str]:
        if argument.name not in CITATION_ARGUMENTS:
            return []
        try:
            return await anyio.to_thread.run_sync(navigator.complete, argument.value)
        except ECFRError:
            return []

    low = mcp._mcp_server
    low.add_request_handler(
        "resources/subscribe", mcp_types.SubscribeRequestParams, watch.subscribe
    )
    low.add_request_handler(
        "resources/unsubscribe", mcp_types.UnsubscribeRequestParams, watch.unsubscribe
    )
    low.add_request_handler(
        "subscriptions/listen",
        mcp_types.SubscriptionsListenRequestParams,
        watch.listen,
    )
    return mcp
