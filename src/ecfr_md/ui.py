"""Terminal output for the ``ecfr-md`` command, drawn with rich on stderr."""

import datetime as dt
import textwrap
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.progress import (
    Progress as Bars,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from ecfr_md.errors import (
    AmbiguousNodeError,
    APIError,
    DateOutOfRangeError,
    ECFRConnectionError,
    ECFRTimeoutError,
    InvalidCitationError,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    RateLimitError,
    ResponseFormatError,
    TitleNotFoundError,
)
from ecfr_md.progress import Progress
from ecfr_md.results import Change, Match, Outline

ACCENT = "#5fafff"
GOOD = "#5fd787"
WARN = "#ffaf5f"
BAD = "#ff5f5f"
DIM = "grey58"
EXCERPT_WIDTH = 200  # Characters of a search excerpt shown.

# Error panel titles, most specific first.
ERROR_TITLES: tuple[tuple[type[BaseException], str], ...] = (
    (InvalidCitationError, "Not a citation"),
    (DateOutOfRangeError, "No text for that date"),
    (InvalidParameterError, "Invalid usage"),
    (TitleNotFoundError, "No such title"),
    (AmbiguousNodeError, "More than one match"),
    (NodeNotFoundError, "Not found"),
    (ECFRTimeoutError, "No response from the eCFR"),
    (ECFRConnectionError, "Can't reach the eCFR"),
    (RateLimitError, "Rate limited by the eCFR"),
    (NotFoundError, "Not found"),
    (APIError, "eCFR API error"),
    (ResponseFormatError, "Unexpected response from the eCFR"),
    (OSError, "File error"),
)
# Names for counts of outline entries: (one, many).
COUNT_NAMES = {
    "appendix": ("appendix", "appendices"),
    "subject_group": ("group", "groups"),
}
OUTLINE_LEVELS = frozenset(
    {"title", "subtitle", "chapter", "subchapter", "part", "subpart", "section"}
)

ENDPOINT_NAMES = {
    "titles.json": "list of titles",
    "structure": "outline",
    "ancestry": "ancestry",
    "versions": "version history",
    "agencies.json": "agencies",
    "corrections": "corrections",
}


def describe(url: str) -> str:
    """A short name for a request, e.g. "part 164" or "outline of title 40"."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    segments = parts.path.strip("/").split("/")
    title = next(
        (
            s.removeprefix("title-").split(".")[0]
            for s in segments
            if s.startswith("title-")
        ),
        "",
    )
    if "full" in segments:
        for level in (
            "section",
            "appendix",
            "subpart",
            "part",
            "subchapter",
            "chapter",
            "subtitle",
        ):
            if level in params:
                return f"{level} {params[level]}"
        return f"title {title}"
    for key, name in ENDPOINT_NAMES.items():
        if key in segments or segments[-1] == key:
            return f"{name} of title {title}" if title else name
    if "search" in segments:
        return f"search {segments[-1]}"
    return segments[-1]


class Terminal(Progress):
    """Live progress, notable events and a closing summary.

    ``quiet`` turns everything off except errors.
    """

    def __init__(self, console: Console, *, quiet: bool = False) -> None:
        self.console = console
        self.quiet = quiet
        self._lock = threading.Lock()
        self._bars = Bars(
            SpinnerColumn("dots", style=ACCENT),
            TextColumn("{task.description}"),
            BarColumn(
                bar_width=None,
                style=DIM,
                complete_style=ACCENT,
                finished_style=GOOD,
                pulse_style=DIM,
            ),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeElapsedColumn(),
            console=console,
            expand=True,
        )
        self._parts_bar = Bars(
            TextColumn("{task.description}"),
            BarColumn(
                bar_width=None, style=DIM, complete_style=GOOD, finished_style=GOOD
            ),
            MofNCompleteColumn(),
            TextColumn("parts"),
            TimeRemainingColumn(compact=True, elapsed_when_finished=True),
            console=console,
            expand=True,
        )
        self._tasks: dict[str, TaskID] = {}
        self._parts_task: TaskID | None = None
        self.target = ""
        self.date: dt.date | None = None
        self.requests = 0
        self.cached = 0
        self.bytes_received = 0
        self.retries = 0
        self.files = 0
        self.rate: float | None = None
        self.started_at = time.monotonic()
        self._live: Live | None = None

    # Progress events -----------------------------------------------------

    def fetching(self, citation: str, date: dt.date) -> None:
        self.target, self.date = citation, date

    def started(self, url: str) -> None:
        with self._lock:
            self.requests += 1
            if (old := self._tasks.pop(url, None)) is not None:
                self._bars.remove_task(old)
            self._tasks[url] = self._bars.add_task(describe(url), total=None)

    def received(self, url: str, done: int, total: int | None) -> None:
        task = self._tasks.get(url)
        if task is not None:
            self._bars.update(task, completed=done, total=total)

    def finished(self, url: str, size: int, *, cached: bool) -> None:
        with self._lock:
            if cached:
                self.cached += 1
            else:
                self.bytes_received += size
            task = self._tasks.pop(url, None)
        if task is not None:
            self._bars.remove_task(task)

    def retrying(
        self, url: str, error: Exception, delay: float, attempt: int, attempts: int
    ) -> None:
        with self._lock:
            self.retries += 1
            task = self._tasks.pop(url, None)
        if task is not None:
            self._bars.remove_task(task)
        reason = getattr(error, "detail", "") or str(error) or type(error).__name__
        reason = reason.split(": ", 1)[-1] if url in reason else reason  # No URL twice.
        status = getattr(error, "status_code", None)
        what = (
            f"{status} {reason}"
            if status and not reason.startswith(str(status))
            else reason
        )
        self._event(
            "↻",
            WARN,
            f"{describe(url)}: {what}",
            f"retry {attempt}/{attempts} in {delay:.1f}s",
        )

    def rate_changed(self, rate: float | None) -> None:
        slower = rate is not None and (self.rate is None or rate < self.rate)
        self.rate = rate
        if slower:
            self._event(
                "▼", WARN, "Rate limited by the server", f"now {rate:.1f} requests/s"
            )

    def parts_started(self, total: int) -> None:
        self._event("◆", ACCENT, f"Fetching {total} parts separately")
        self._parts_task = self._parts_bar.add_task("All", total=total)

    def part_finished(self, identifier: str) -> None:
        if self._parts_task is not None:
            self._parts_bar.advance(self._parts_task)

    def file_written(self, path: Path) -> None:
        with self._lock:
            self.files += 1

    # Display -------------------------------------------------------------

    @contextmanager
    def live(self) -> Iterator[None]:
        """Show progress while the block runs."""
        if self.quiet:
            yield
            return
        with Live(
            self, console=self.console, refresh_per_second=12, transient=True
        ) as live:
            self._live = live
            try:
                yield
            finally:
                self._live = None

    def __rich__(self) -> RenderableType:
        rows: list[RenderableType] = [self._header()]
        if self._parts_task is not None:
            rows.append(self._parts_bar)
        rows.append(self._bars)
        rows.append(self._status())
        return Group(*rows)

    def _header(self) -> Text:
        text = Text()
        text.append("ecfr-md ", style=f"bold {ACCENT}")
        text.append("▸ ", style=DIM)
        text.append(self.target or "connecting", style="bold")
        if self.date:
            text.append(f"  as of {self.date}", style=DIM)
        return text

    def _status(self) -> Text:
        text = Text(style=DIM)
        text.append(_count(self.requests, "request", "requests"))
        if self.cached:
            text.append(f" · {self.cached} cached")
        text.append(f" · {size(self.bytes_received)} received")
        if self.retries:
            text.append(f" · {_count(self.retries, 'retry', 'retries')}", style=WARN)
        if self.files:
            text.append(f" · {_count(self.files, 'file', 'files')} written")
        if self.rate is not None:
            text.append(f" · limit {self.rate:.1f}/s")
        return text

    def _event(self, symbol: str, color: str, message: str, detail: str = "") -> None:
        if self.quiet:
            return
        line = Text()
        line.append(f"{symbol} ", style=f"bold {color}")
        line.append(message)
        if detail:
            line.append(f"  {detail}", style=DIM)
        self.console.print(line)

    def note(self, message: str) -> None:
        """A dim line on stderr, unless quiet."""
        if not self.quiet:
            self.console.print(Text(message, style=DIM), soft_wrap=True)

    def done(self, message: str, *details: str, network: bool = True) -> None:
        """A one-line closing message, with details dimmed."""
        if self.quiet:
            return
        line = Text()
        line.append("✔ ", style=f"bold {GOOD}")
        line.append(message)
        extra = [*details, *(self._network_brief() if network else ())]
        if extra:
            line.append("  " + " · ".join(extra), style=DIM)
        self.console.print(line)

    def _network_brief(self) -> tuple[str, str]:
        transfer = _count(self.requests, "request", "requests")
        if self.cached:
            transfer += f", {self.cached} cached"
        if self.retries:
            transfer += f", {_count(self.retries, 'retry', 'retries')}"
        return transfer, f"{time.monotonic() - self.started_at:.1f}s"

    def summary(
        self, title: str, rows: dict[str, str], *, network: bool = True
    ) -> None:
        """A closing panel of key facts."""
        if self.quiet:
            return
        table = Table.grid(padding=(0, 2))
        table.add_column(style=DIM, justify="right")
        table.add_column()
        for key, value in rows.items():
            table.add_row(key, value)
        if network:
            self._network_rows(table)
        self.console.print(
            Panel(
                table,
                title=f"[bold {GOOD}]✔[/] [bold]{title}[/]",
                title_align="left",
                border_style=GOOD,
                box=box.ROUNDED,
                padding=(0, 1),
                expand=False,
            )
        )

    def _network_rows(self, table: Table) -> None:
        elapsed = time.monotonic() - self.started_at
        transfer = f"{_count(self.requests, 'request', 'requests')}, "
        transfer += f"{size(self.bytes_received)} received"
        if self.cached:
            transfer += f", {self.cached} from cache"
        if self.retries:
            transfer += f", {_count(self.retries, 'retry', 'retries')}"
        table.add_row("Network", transfer)
        table.add_row("Time", f"{elapsed:.1f}s")

    def error(self, exc: BaseException) -> None:
        """An error panel; shown even when quiet."""
        body: RenderableType = Text(str(exc))
        if isinstance(exc, AmbiguousNodeError):
            table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
            for node in exc.candidates[:10]:
                table.add_row(f"[bold]{node.citation}[/]", Text(_first_line(node)[:70]))
            body = Group(Text(str(exc).split(":")[0] + ":"), table)
        name = next((t for cls, t in ERROR_TITLES if isinstance(exc, cls)), "Error")
        self.console.print(
            Panel(
                body,
                title=f"[bold {BAD}]✘ {name}[/]",
                title_align="left",
                border_style=BAD,
                box=box.ROUNDED,
                padding=(0, 1),
                expand=False,
            )
        )


def _first_line(node: object) -> str:
    text = getattr(node, "text", "") or getattr(node, "heading", "")
    return text.splitlines()[0].replace("**", "") if text else ""


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def size(n: float) -> str:
    if n < 1000:
        return f"{n:.0f} B"
    for unit in ("KB", "MB"):
        n /= 1000
        if n < 1000:
            return f"{n:.1f} {unit}"
    return f"{n / 1000:.1f} GB"


# Command output --------------------------------------------------------------


def outline_tree(entry: Outline, depth: int | None = None) -> Tree:
    """A table of contents as a tree; reserved entries are dimmed.

    Below ``depth``, entries show how many of each level they contain.
    """

    def label(e: Outline, counts: bool) -> Text:
        text = Text(e.heading)
        if e.reserved:
            text.stylize(DIM)
        elif e.level in ("title", "chapter", "subtitle", "subchapter", "part"):
            text.stylize("bold")
        if e.level == "part" and e.size:
            text.append(f"  {size(e.size)}", style=DIM)
        if counts and e.children:
            text.append(f"  {_contents(e.children)}", style=DIM)
        return text

    def add(branch: Tree, e: Outline, level: int) -> None:
        for child in e.children:
            last = depth is not None and level + 1 >= depth
            sub_branch = branch.add(label(child, last))
            if not last:
                add(sub_branch, child, level + 1)

    tree = Tree(label(entry, depth == 0), guide_style=DIM)
    if depth != 0:
        add(tree, entry, 0)
    return tree


def _contents(children: Sequence[Outline]) -> str:
    """E.g. "12 sections, 1 appendix"."""
    counts: dict[str, int] = {}
    for child in children:
        counts[child.level] = counts.get(child.level, 0) + 1
    words = []
    for level, n in counts.items():
        default = (level, f"{level}s") if level in OUTLINE_LEVELS else None
        one, many = COUNT_NAMES.get(level) or default or ("heading", "headings")
        words.append(_count(n, one, many))
    return ", ".join(words)


def changes_table(changes: Sequence[Change]) -> RenderableType:
    """Changes; for a single section, its heading is shown once, above."""
    single = len({c.identifier for c in changes}) == 1
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}", pad_edge=False)
    table.add_column("Issued", no_wrap=True)
    table.add_column("Effective", no_wrap=True, style=DIM)
    if not single:
        table.add_column("Section", max_width=14, overflow="fold")
        table.add_column("Heading", min_width=20)
    table.add_column("Kind", no_wrap=True, min_width=len("substantive"))
    styles = {"removed": BAD, "substantive": WARN, "editorial": DIM}
    for c in changes:
        kind = Text(c.kind, style=styles[c.kind])
        columns = [] if single else [c.identifier, c.heading]
        table.add_row(str(c.issued), str(c.effective), *columns, kind)
    if not single:
        return table
    heading = Text(changes[0].citation, style="bold")
    heading.append(f"  {changes[0].heading}")
    return Group(heading, table)


def search_table(matches: Sequence[Match]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}", pad_edge=False)
    table.add_column("Citation", no_wrap=True, style="bold")
    table.add_column("Heading / excerpt")
    for m in matches:
        cell = Text(m.heading)
        if m.excerpt:
            excerpt = textwrap.shorten(m.excerpt, EXCERPT_WIDTH, placeholder="…")
            cell.append(f"\n{excerpt}", style=DIM)
        table.add_row(m.citation, cell)
    return table
