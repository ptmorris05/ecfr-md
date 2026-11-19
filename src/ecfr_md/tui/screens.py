"""The browser's dialogs: go to, search, date, history and redline, export, help.

Each is a modal screen over the text. They do their own network calls in
worker threads, through the browser's client, and report errors the same
way the browser does.
"""

from __future__ import annotations

import datetime as dt
import textwrap
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from rich.table import Table as RichTable
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from ecfr_md import query as queries
from ecfr_md.compare import Comparison
from ecfr_md.errors import InvalidParameterError
from ecfr_md.params import SearchFilters, to_date
from ecfr_md.render.formats import FORMATS, SUFFIXES, Format
from ecfr_md.results import Change, Match, Outline
from ecfr_md.tui.text import MUTED
from ecfr_md.ui import redline

if TYPE_CHECKING:
    from ecfr_md.tui.app import Browser

EXCERPT = 160  # Characters of a search excerpt shown.
FORMAT_NAMES: dict[Format, str] = {
    "markdown": "Markdown",
    "text": "Plain text",
    "html": "HTML",
    "json": "JSON",
    "epub": "EPUB",
    "docx": "Word",
    "pdf": "PDF",
}
KIND_STYLES = {"removed": "$error", "substantive": "$warning", "editorial": MUTED}


class Dialog[T](ModalScreen[T]):
    """A panel over the text, closed with escape (or q, outside a text box)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close", show=False),
    ]
    DEFAULT_CSS = """
    Dialog {
        align: center top;
        background: $background 60%;
    }
    Dialog > Vertical {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        margin-top: 2;
        padding: 1 2;
        background: $surface;
        border: round $primary 50%;
    }
    Dialog .title {
        text-style: bold;
        margin-bottom: 1;
    }
    Dialog .hint {
        color: $text-muted;
    }
    Dialog .footnote {
        margin-top: 1;
    }
    Dialog .error {
        color: $error;
    }
    Dialog Input, Dialog Select, Dialog Checkbox {
        margin-bottom: 1;
    }
    Dialog Input {
        background: $boost;
    }
    Dialog OptionList {
        height: auto;
        max-height: 20;
        border: none;
        padding: 0;
        background: $surface;
    }
    """

    @property
    def browser(self) -> Browser:
        return cast("Browser", self.app)

    def action_close(self) -> None:
        self.dismiss()


class GoTo(Dialog[str | None]):
    """A citation in any form, with matching entries of the outline below."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("down", "into_list", show=False)]

    def __init__(self, complete: Callable[[str], Sequence[Outline]]) -> None:
        super().__init__()
        self._complete = complete
        self._found: list[Outline] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Go to", classes="title")
            yield Input(
                compact=True,
                placeholder="45 CFR 164.502(a)(1), 45/164.502, part 160, …",
            )
            yield Static("", id="problem", classes="error")
            yield OptionList(id="matches")
            yield Static(
                "Any citation, address or eCFR link · ↓ for matches",
                classes="hint footnote",
            )

    @on(Input.Changed)
    def _typed(self, event: Input.Changed) -> None:
        self.query_one("#problem", Static).update("")
        text = event.value
        self.browser.background(
            lambda: self._complete(text), self._show_matches, group="complete"
        )

    def _show_matches(self, found: Sequence[Outline]) -> None:
        self._found = list(found)
        matches = self.query_one("#matches", OptionList)
        matches.clear_options()
        matches.add_options(
            Option(Content.assemble((e.citation, "bold"), "  ", (_heading(e), MUTED)))
            for e in self._found
        )

    def action_into_list(self) -> None:
        matches = self.query_one("#matches", OptionList)
        if matches.option_count:
            matches.focus()
            matches.highlighted = 0

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        try:
            address = self.browser.resolve(text)
        except InvalidParameterError as exc:
            if self._found:  # Not a citation: the best match, then.
                self.dismiss(self._found[0].address)
                return
            self.query_one("#problem", Static).update(str(exc))
            return
        self.dismiss(address)

    @on(OptionList.OptionSelected, "#matches")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._found[event.option_index].address)


def _heading(entry: Outline) -> str:
    """The heading without the number the citation already gives."""
    number = f"§ {entry.identifier}"
    if entry.heading.startswith(number):
        return entry.heading.removeprefix(number).strip()
    return entry.heading


class Search(Dialog[str | None]):
    """Full-text search of the eCFR; choosing a match opens it."""

    def __init__(self, query: str = "", within: Outline | None = None) -> None:
        super().__init__()
        self._query = query
        self._within = within
        self._matches: list[Match] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Search", classes="title")
            yield Input(
                self._query,
                placeholder='Words, "a phrase", -not, a | b, prefix*',
                compact=True,
            )
            if self._within is not None:
                yield Checkbox(
                    f"Only in {self._within.citation}", id="within", compact=True
                )
            yield Static("", id="status", classes="hint")
            yield OptionList(id="results")

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        filters: SearchFilters = {}
        if self._within is not None and self.query_one("#within", Checkbox).value:
            filters["within"] = self._within.citation
        self.query_one("#status", Static).update("Searching…")
        ecfr = self.browser.ecfr
        self.browser.background(
            lambda: ecfr.search(query, limit=50, **filters),
            self._show,
            group="search",
            failed=lambda: self.query_one("#status", Static).update(""),
        )

    def _show(self, matches: list[Match]) -> None:
        self._matches = list(matches)
        total = getattr(matches, "total", len(matches))
        status = self.query_one("#status", Static)
        note = getattr(matches, "note", "")
        if not matches:
            status.update(f"No matches. Search syntax: {queries.SYNTAX}")
        else:
            shown = len(matches)
            status.update(
                f"{shown} {'match' if shown == 1 else 'matches'}"
                + (f" of {total:,} versions" if total and total > shown else "")
                + (f". {note}" if note else "")
            )
        results = self.query_one("#results", OptionList)
        results.clear_options()
        results.add_options(spaced(Option(_match(m)) for m in self._matches))
        if matches:
            results.focus()
            results.highlighted = 0

    @on(OptionList.OptionSelected, "#results")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self._matches[event.option_index].address)


def _match(match: Match) -> Content:
    line = Content.assemble((match.citation, "bold"), "  ", match.heading)
    if match.excerpt:
        excerpt = textwrap.shorten(match.excerpt, EXCERPT, placeholder="…")
        line += Content.styled(f"\n{excerpt}", MUTED)
    return line


def spaced(options: Iterable[Option]) -> Iterator[Option | None]:
    """Options with a separator after each."""
    for option in options:
        yield option
        yield None


class DateDialog(Dialog[str | None]):
    """The date to read the text as of; empty for the latest. Returns the
    date as ISO text, "" for the latest, or None if cancelled."""

    def __init__(self, current: dt.date | None) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Text as of", classes="title")
            yield Input(
                self._current.isoformat() if self._current else "",
                placeholder="2020-01-01, Jan 1 2020, last year · empty for latest",
                compact=True,
            )
            yield Static("", id="problem", classes="error")

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.dismiss("")
            return
        try:
            day = to_date(text)
        except ValueError as exc:
            self.query_one("#problem", Static).update(str(exc))
            return
        self.dismiss(day.isoformat())


class History(Dialog[None]):
    """Changes the eCFR issued to what's open; choosing one shows its redline."""

    def __init__(self, citation: str) -> None:
        super().__init__()
        self._citation = citation
        self._changes: list[Change] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"History of {self._citation}", classes="title")
            yield Static("Loading…", id="status", classes="hint")
            yield OptionList(id="changes")

    def on_mount(self) -> None:
        ecfr, citation = self.browser.ecfr, self._citation
        self.browser.background(
            lambda: ecfr.changes(citation),
            self._show,
            group="history",
            failed=lambda: self.query_one("#status", Static).update(""),
        )

    def _show(self, changes: list[Change]) -> None:
        self._changes = changes
        count = len(changes)
        hint = "Enter shows what changed" if changes else ""
        self.query_one("#status", Static).update(
            f"{count} {'change' if count == 1 else 'changes'}"
            + (f" · {hint}" if hint else "")
        )
        single = len({c.identifier for c in changes}) == 1
        changes_list = self.query_one("#changes", OptionList)
        changes_list.add_options(spaced(Option(_change(c, single)) for c in changes))
        if changes:
            changes_list.focus()
            changes_list.highlighted = 0

    @on(OptionList.OptionSelected, "#changes")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        change = self._changes[event.option_index]
        since = change.effective - dt.timedelta(days=1)
        ecfr = self.browser.ecfr
        self.query_one("#status", Static).update(f"Comparing {change.citation}…")
        self.browser.background(
            lambda: ecfr.compare(change.address, since, change.effective),
            lambda found: self._redline(found, change),
            group="history",
            failed=lambda: self.query_one("#status", Static).update(""),
        )

    def _redline(self, comparison: Comparison, change: Change) -> None:
        self.query_one("#status", Static).update("Enter shows what changed")
        self.app.push_screen(Redline(comparison, change))


def _change(change: Change, single: bool) -> Content:
    line = Content.assemble(
        (str(change.issued), "bold"),
        ("  effective " + str(change.effective), MUTED),
        "  ",
        (change.kind, KIND_STYLES[change.kind]),
    )
    if not single:
        line += Content.assemble("  ", (change.citation, "bold"))
    if change.heading:
        line += Content.styled(f"\n{change.heading}", MUTED)
    return line


class Redline(Dialog[None]):
    """What one change did to the text, word by word."""

    DEFAULT_CSS = """
    Redline > Vertical {
        height: 90%;
    }
    Redline VerticalScroll {
        height: 1fr;
    }
    """

    def __init__(self, comparison: Comparison, change: Change) -> None:
        super().__init__()
        self._comparison = comparison
        self._change = change

    def compose(self) -> ComposeResult:
        found = self._comparison
        with Vertical():
            yield Static(
                f"{found.citation}: {found.since} to {found.until}", classes="title"
            )
            kinds = [d.kind for d in found.differences]
            counts = ", ".join(
                f"{kinds.count(k)} {k}"
                for k in ("changed", "added", "removed")
                if k in kinds
            )
            yield Static(
                f"{counts or 'No changes to the text'} · "
                f"{found.unchanged:,} unchanged · {self._change.kind} change, "
                f"issued {self._change.issued}",
                classes="hint",
            )
            with VerticalScroll():
                yield Static(redline(found) if found else "")


class Export(Dialog[tuple[Format, Path] | None]):
    """Save what's open in any format, to a file the user chooses."""

    def __init__(self, citation: str, stem: str, directory: Path) -> None:
        super().__init__()
        self._citation = citation
        self._stem = stem
        self._directory = directory

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Save {self._citation}", classes="title")
            yield Select(
                [(f"{FORMAT_NAMES[f]} ({SUFFIXES[f]})", f) for f in FORMATS],
                value="markdown",
                allow_blank=False,
                id="format",
                compact=True,
            )
            yield Input(
                str(self._directory / f"{self._stem}.md"), id="path", compact=True
            )
            yield Static("", id="problem", classes="error")
            yield Static("Enter saves · Word and PDF need extras", classes="hint")

    @on(Select.Changed, "#format")
    def _format(self, event: Select.Changed) -> None:
        format = cast("Format", event.value)
        path = self.query_one("#path", Input)
        current = Path(path.value)
        if current.suffix.lower() in {*SUFFIXES.values(), ""}:
            path.value = str(current.with_suffix(SUFFIXES[format]))

    @on(Input.Submitted, "#path")
    def _submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.query_one("#problem", Static).update("Say where to save it")
            return
        format = cast("Format", self.query_one("#format", Select).value)
        self.dismiss((format, Path(text).expanduser()))


KEYS: tuple[tuple[str, str], ...] = (
    ("g", "Go to a citation, address or eCFR link"),
    ("/", "Search the eCFR"),
    ("[  ]", "Previous and next section"),
    ("j  k", "Next and previous paragraph"),
    ("tab", "Next reference; shift+tab, previous"),
    ("enter", "Follow the reference (or click it)"),
    ("u", "Up a level"),
    ("b  f", "Back and forward"),
    ("d", "Text as of a date"),
    ("h", "History of changes, with redlines"),
    ("e", "Export: Markdown, text, HTML, JSON, EPUB, Word, PDF"),
    ("y", "Copy the citation of the paragraph"),
    ("o", "Show or hide the outline"),
    ("q", "Quit"),
)


class Help(Dialog[None]):
    """The keys."""

    def compose(self) -> ComposeResult:
        table = RichTable.grid(padding=(0, 3))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for key, does in KEYS:
            table.add_row(key, does)
        with Vertical():
            yield Static("Keys", classes="title")
            yield Static(table)
            yield Static(
                "\nThe eCFR is an editorial compilation, not an official legal "
                "edition of the CFR.",
                classes="hint",
            )
