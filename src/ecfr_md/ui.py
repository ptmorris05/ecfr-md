"""Terminal output for the ``ecfr-md`` command, drawn with rich on stderr."""

import datetime as dt
import html
import re
import threading
import time
from collections.abc import Iterator
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

from ecfr_md import models
from ecfr_md.errors import AmbiguousNodeError, ECFRError
from ecfr_md.progress import Progress

ACCENT = "#5fafff"
GOOD = "#5fd787"
WARN = "#ffaf5f"
BAD = "#ff5f5f"
DIM = "grey58"

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
        name = type(exc).__name__ if isinstance(exc, ECFRError) else "Error"
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


def outline_tree(node: models.Node, depth: int | None = None) -> Tree:
    """A table of contents as a tree; reserved entries are dimmed."""

    def label(n: models.Node) -> Text:
        text = Text(" ".join(_TAGS.sub("", n.label).split()))
        if n.reserved:
            text.stylize(DIM)
        elif n.type in ("title", "chapter", "subtitle", "subchapter", "part"):
            text.stylize("bold")
        if n.type == "part" and n.size:
            text.append(f"  {size(n.size)}", style=DIM)
        return text

    def add(branch: Tree, n: models.Node, level: int) -> None:
        if depth is not None and level >= depth:
            if n.children:
                branch.add(Text(f"{len(n.children)} more", style=DIM))
            return
        for child in n.children:
            add(branch.add(label(child)), child, level + 1)

    tree = Tree(label(node), guide_style=DIM)
    add(tree, node, 0)
    return tree


def changes_table(versions: list[models.ContentVersion]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}", pad_edge=False)
    table.add_column("Issued", no_wrap=True)
    table.add_column("Effective", no_wrap=True, style=DIM)
    table.add_column("Section", no_wrap=True)
    table.add_column("Heading")
    table.add_column("Kind", no_wrap=True)
    for v in versions:
        heading = " ".join(v.name.split()).removeprefix(f"§ {v.identifier}").strip()
        if v.removed:
            kind = Text("removed", style=BAD)
        elif v.substantive:
            kind = Text("substantive", style=WARN)
        else:
            kind = Text("editorial", style=DIM)
        table.add_row(str(v.issue_date), str(v.date), v.identifier, heading, kind)
    return table


def search_table(results: list[models.SearchResult]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style=f"bold {ACCENT}", pad_edge=False)
    table.add_column("Citation", no_wrap=True, style="bold")
    table.add_column("Heading / excerpt")
    for r in results:
        heading = _plain(r.headings.section or r.headings.appendix or "")
        excerpt = _plain(r.full_text_excerpt or "")
        cell = Text(heading)
        if excerpt:
            cell.append(f"\n{excerpt[:200]}", style=DIM)
        table.add_row(result_citation(r), cell)
    return table


def result_citation(result: models.SearchResult) -> str:
    h = result.hierarchy
    return f"{h.title} CFR {h.section or h.appendix}"


_TAGS = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", text)).split())
