"""The terminal browser: an outline of the CFR beside its text.

Everything it shows comes from the same client as the SDK and the command
line (``ECFR.outline``, ``get``, ``search``, ``changes``, ``compare`` and
``save``), with the same cache and settings. Network calls run in worker
threads, so the screen never waits on them.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import logging
import re
import threading
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import get_current_worker

from ecfr_md.address import Address
from ecfr_md.client import ECFR
from ecfr_md.document import Node
from ecfr_md.errors import ECFRError, InvalidCitationError
from ecfr_md.params import DateLike, to_date
from ecfr_md.progress import Progress
from ecfr_md.render.formats import Format
from ecfr_md.results import CFR_LEVEL, LEAVES, Outline
from ecfr_md.tui.screens import (
    DateDialog,
    Export,
    GoTo,
    Help,
    History,
    Search,
)
from ecfr_md.tui.text import (
    CONTENTS_LEVELS,
    INDENT,
    MUTED,
    Link,
    Piece,
    contents_pieces,
    crumb,
    text_pieces,
)
from ecfr_md.ui import ERROR_TITLES, describe, size

SEPARATOR = " \u203a "
STATUS_WIDTH = 24  # Columns kept for the date (or what's being fetched).
NARROW = 100  # Terminal columns below which the outline is hidden until wanted.
TEXT_BELOW = 2_000_000  # Parts (bytes) larger than this show contents, not text.
MATCHES = 40  # Most matches offered while typing a citation.
TITLE_ONLY = re.compile(r"\s*(\d+)\s*$")
TITLED = re.compile(r"\s*(\d+)\s*(?:C\.?\s?F\.?\s?R\.?|/)\s*(.*)$", re.IGNORECASE)
LEVEL_WORDS = re.compile(
    r"^(?P<level>§§?|sections?|subparts?|parts?|appendix|appendices)\s*", re.IGNORECASE
)
LEVEL_NAMES = {"§": "section", "s": "section", "p": "part", "a": "appendix"}
READER_ACTIONS = frozenset(
    {"next_piece", "previous_piece", "next_section", "previous_section", "up"}
    | {"go_back", "go_forward", "copy", "go", "search", "date", "history", "export"}
    | {"help", "outline", "back_out", "switch_pane", "leave_outline"}
)
PARAGRAPH = Binding.Group("Paragraph", compact=True)
REFERENCE = Binding.Group("Reference", compact=True)
MOVE = Binding.Group("Move", compact=True)
FOLD = Binding.Group("Fold", compact=True)


@dataclass
class Place:
    """Somewhere the browser has been: an address (None for the whole CFR),
    and the paragraph the cursor was on."""

    address: str | None
    cursor: int = 0
    anchor: str | None = None
    within: str | None = None
    """An address below this one, to put the cursor on (coming up from it)."""


@dataclass
class View:
    """What the reader shows: an outline entry, and its text if it has some."""

    entry: Outline
    node: Node | None
    pieces: list[Piece]
    path: list[Outline]
    """The entries from the top of the CFR down to ``entry``."""


class Status(Progress):
    """Requests in flight, shown in the header; retries as notifications."""

    def __init__(self, app: Browser) -> None:
        self._app = app
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}

    def started(self, url: str) -> None:
        with self._lock:
            self._active[url] = describe(url)
        self._app.later(self._app.update_status)

    def finished(self, url: str, size: int, *, cached: bool) -> None:
        with self._lock:
            self._active.pop(url, None)
        self._app.later(self._app.update_status)

    def retrying(
        self, url: str, error: Exception, delay: float, attempt: int, attempts: int
    ) -> None:
        with self._lock:
            self._active.pop(url, None)
        message = f"{describe(url)}: retry {attempt}/{attempts} in {delay:.0f}s"
        self._app.later(self._app.notify, message, severity="warning")

    def clear(self) -> None:
        with self._lock:
            self._active.clear()

    @property
    def activity(self) -> str:
        with self._lock:
            names = list(self._active.values())
        return names[-1] if names else ""


class Notifier(logging.Handler):
    """Warnings (such as text served offline from the cache) as notifications."""

    def __init__(self, app: Browser) -> None:
        super().__init__(logging.WARNING)
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("ecfr_md"):
            return  # Retries and the like: the header and Status report them.
        self._app.later(
            self._app.notify, record.getMessage(), severity="warning", timeout=8
        )


class PieceView(Static):
    """One paragraph, heading, table or note."""

    def __init__(self, piece: Piece, index: int) -> None:
        super().__init__(piece.render(index), classes=f"piece {piece.kind}")
        self.piece = piece
        self.index = index
        indent = INDENT * max(piece.depth - 1, 0)
        if piece.kind == "note":
            indent = INDENT
        self.styles.padding = (0, 1, 0, indent + 1)

    def select(self, link: int | None) -> None:
        self.update(self.piece.render(self.index, link))

    def on_click(self, event: Click) -> None:
        """A click on an entry opens it; elsewhere, it moves the cursor
        here (a click on a reference follows it, through its action)."""
        if "@click" in event.style.meta:
            return
        browser = self.app
        assert isinstance(browser, Browser)
        if self.piece.kind == "entry":
            browser.action_follow(self.index, 0)
        else:
            browser.query_one(Reader).mark(self.index, None)


class Reader(VerticalScroll):
    """The text, a piece at a time, with a cursor on one of them.

    Up and down move the cursor a paragraph at a time, left and right
    between references, enter follows one (or opens an entry of contents),
    escape goes back; page up and down, home and end scroll.
    """

    can_focus = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "app.previous_piece", "Previous paragraph", group=PARAGRAPH),
        Binding("down", "app.next_piece", "Next paragraph", group=PARAGRAPH),
        Binding("left", "previous_link", "Previous reference", group=REFERENCE),
        Binding("right", "next_link", "Next reference", group=REFERENCE),
        Binding("enter", "follow_cursor", "Open"),
        Binding("escape", "app.back_out", "Back"),
        Binding("tab,shift+tab", "app.switch_pane", "Outline"),
    ]

    def __init__(self) -> None:
        super().__init__(id="reader")
        self.pieces: list[Piece] = []
        self.cursor = 0
        self.link: int | None = None

    def show(self, pieces: list[Piece], cursor: int = 0) -> None:
        self.pieces = pieces
        self.cursor = min(cursor, max(len(pieces) - 1, 0))
        self.link = None
        self.remove_children()
        self.mount_all(PieceView(p, i) for i, p in enumerate(pieces))
        self.call_after_refresh(self._after_show)

    def _after_show(self) -> None:
        self.mark(self.cursor)
        if self.cursor:
            self.scroll_cursor(top=True)
        else:
            self.scroll_home(animate=False)

    @property
    def views(self) -> list[PieceView]:
        return list(self.query_children(PieceView))

    def mark(self, index: int, link: int | None = None) -> None:
        views = self.views
        if index >= len(views):
            return
        old = views[self.cursor] if self.cursor < len(views) else None
        if old is not None:
            old.remove_class("-cursor")
            old.select(None)
        self.cursor = index
        self.link = link
        views[index].add_class("-cursor")
        views[index].select(self.link)

    def scroll_cursor(self, top: bool = False) -> None:
        views = self.views
        if views:
            self.scroll_to_widget(views[self.cursor], animate=False, top=top)

    def _start(self) -> int:
        """Where stepping starts: the cursor, or the first piece in view
        if the text was scrolled away from it."""
        views = self.views
        top = self.scroll_y
        bottom = top + self.scrollable_content_region.height

        def seen(view: PieceView) -> bool:
            region = view.virtual_region
            return region.bottom > top and region.y < bottom

        if not views or seen(views[self.cursor]):
            return self.cursor
        return next((i for i, v in enumerate(views) if seen(v)), self.cursor)

    def step(self, delta: int) -> None:
        """To the next (or previous) piece; through a piece taller than
        the screen, half a screen at a time first."""
        if not self.pieces:
            return
        start = self._start()
        moved = start != self.cursor
        if not moved and self._beyond(delta):
            height = self.scrollable_content_region.height
            self.scroll_relative(y=delta * max(1, height // 2), animate=False)
            return
        index = start if moved else start + delta
        index = max(0, min(index, len(self.pieces) - 1))
        self.mark(index)
        self.scroll_cursor()

    def _beyond(self, delta: int) -> bool:
        """Whether the cursor's piece goes on past the screen, that way."""
        region = self.views[self.cursor].virtual_region
        if delta > 0:
            return region.bottom > self.scroll_y + self.scrollable_content_region.height
        return region.y < self.scroll_y

    def move_link(self, delta: int) -> None:
        """To the next (or previous) reference, across pieces, from the
        cursor (or what's in view, if the text was scrolled away from it)."""
        links = [
            (i, n)
            for i, piece in enumerate(self.pieces)
            for n in range(len(piece.links))
        ]
        if not links:
            return
        start = self._start()
        if start != self.cursor:
            self.mark(start)
        here = (self.cursor, self.link if self.link is not None else -1)
        later = [pos for pos in links if (pos > here if delta > 0 else pos < here)]
        if not later:
            later = links  # Wrap around.
        index, number = later[0] if delta > 0 else later[-1]
        self.mark(index, number)
        self.scroll_cursor()

    @property
    def current(self) -> Piece | None:
        return self.pieces[self.cursor] if self.pieces else None

    def action_follow_cursor(self) -> None:
        """Follow the reference chosen with left and right; in a paragraph
        with one reference (or an entry of contents), that one; with
        several, choose the first. If the text was scrolled away from the
        cursor, the cursor comes to what's in view first."""
        piece = self.current
        if piece is None:
            return
        start = self._start()
        if start != self.cursor:
            self.mark(start)
            return
        if not piece.links:
            return
        if self.link is None and len(piece.links) > 1:
            self.mark(self.cursor, 0)
            return
        browser = self.app
        assert isinstance(browser, Browser)
        browser.open_link(piece.links[self.link or 0])

    def action_next_link(self) -> None:
        self.move_link(1)

    def action_previous_link(self) -> None:
        self.move_link(-1)


class OutlineTree(Tree[Outline]):
    """The outline: up and down move, right opens a branch (then goes into
    it), left closes it (or goes to the one above), enter reads the entry."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", group=MOVE),
        Binding("down", "cursor_down", "Down", group=MOVE),
        Binding("left", "fold", "Close", group=FOLD),
        Binding("right", "unfold", "Open", group=FOLD),
        Binding("enter", "select_cursor", "Read"),
        Binding("escape", "app.leave_outline", "Text"),
        Binding("tab,shift+tab", "app.switch_pane", "Text", show=False),
    ]

    def action_unfold(self) -> None:
        node = self.cursor_node
        if node is None or not node.allow_expand:
            return
        if not node.is_expanded:
            node.expand()
        elif node.children:
            self.move_cursor(node.children[0], animate=False)

    def action_fold(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent, animate=False)


class Browser(App[None]):
    """Read and navigate the CFR in the terminal."""

    TITLE = "ecfr-md"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen {
        layout: vertical;
    }
    #header {
        height: 1;
        padding: 0 1;
        background: $panel;
    }
    #crumbs {
        width: 1fr;
        height: 1;
        color: $text-muted;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    #status {
        width: auto;
        height: 1;
        color: $text-muted;
    }
    #body {
        height: 1fr;
    }
    #outline {
        width: 38;
        max-width: 40%;
        height: 1fr;
        padding: 0 1 0 0;
        border-right: solid $panel-lighten-1;
        background: $background;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
    }
    #outline > .tree--cursor {
        background: $boost;
        text-style: bold;
    }
    #outline:focus > .tree--cursor {
        background: $accent 40%;
    }
    #reader {
        width: 1fr;
        height: 1fr;
        align-horizontal: center;
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }
    .piece {
        width: 100%;
        max-width: 92;
        margin-bottom: 1;
        border-left: outer $background;
    }
    .piece.entry {
        margin-bottom: 0;
    }
    .piece.entry:hover {
        background: $boost;
    }
    .piece.heading {
        margin-top: 1;
    }
    .piece.-cursor {
        border-left: outer $panel-lighten-2;
        background: $boost;
    }
    #reader:focus .piece.-cursor {
        border-left: outer $accent;
    }
    Screen.-narrow #outline {
        display: none;
    }
    Screen.-narrow.-outline #outline {
        display: block;
        width: 100%;
        max-width: 100%;
        border-right: none;
    }
    Screen.-narrow.-outline #reader {
        display: none;
    }
    Screen.-hide-outline #outline {
        display: none;
    }
    Footer {
        background: $background;
    }
    Footer FooterLabel {
        margin-right: 1;
    }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("g", "go", "Go to"),
        Binding("slash", "search", "Search", key_display="/"),
        Binding("left_square_bracket", "previous_section", "Prev", key_display="["),
        Binding("right_square_bracket", "next_section", "Next", key_display="]"),
        Binding("j", "next_piece", "Next paragraph", show=False),
        Binding("k", "previous_piece", "Previous paragraph", show=False),
        Binding("u", "up", "Up", show=False),
        Binding("b", "go_back", "Back", show=False),
        Binding("f", "go_forward", "Forward", show=False),
        Binding("d", "date", "Date"),
        Binding("h", "history", "History"),
        Binding("e", "export", "Export"),
        Binding("y", "copy", "Copy citation", show=False),
        Binding("o", "outline", "Outline", show=False),
        Binding("question_mark", "help", "Keys", key_display="?"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        ecfr: ECFR,
        citation: str | None = None,
        *,
        date: DateLike | None = None,
        directory: Path | None = None,
    ) -> None:
        super().__init__()
        self.ecfr = ecfr
        self.date = to_date(date) if date is not None else None
        self.directory = directory or Path.cwd()
        # What to open once the outline is planted: (address or citation,
        # whether it's typed, the cursor), or None.
        self._pending: tuple[str | None, bool, int] | None = (citation, True, 0)
        self.status = Status(self)
        self._restore: tuple[Progress, list[logging.Handler], Any] | None = None
        self.view: View | None = None
        self.history: list[Place] = []
        self.index = -1
        self._busy = 0
        self._outline_open: bool | None = None  # None: as the width suits.

    # Layout ----------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static("ecfr-md", id="crumbs")
            yield Static("", id="status")
        with Horizontal(id="body"):
            tree = OutlineTree("Code of Federal Regulations", id="outline")
            tree.show_root = False
            tree.auto_expand = False
            tree.guide_depth = 2
            yield tree
            yield Reader()
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self) -> None:
        # Progress and warnings show here, not printed over the screen.
        root = logging.getLogger()
        self._restore = (self.ecfr.progress, root.handlers[:], warnings.showwarning)
        self.ecfr.progress = self.status
        root.handlers = [Notifier(self)]
        logging.captureWarnings(True)
        self._layout(self.size.width)
        self.query_one(Reader).focus()
        self.background(self._root, self._planted)

    def on_unmount(self) -> None:
        if self._restore is not None:
            progress, handlers, showwarning = self._restore
            self.ecfr.progress = progress
            logging.getLogger().handlers = handlers
            warnings.showwarning = showwarning

    def on_resize(self, event: Any) -> None:
        self._layout(event.size.width)

    def _layout(self, width: int) -> None:
        narrow = width < NARROW
        self.screen.set_class(narrow, "-narrow")
        opened = self._outline_open
        self.screen.set_class(narrow and bool(opened), "-outline")
        self.screen.set_class(not narrow and opened is False, "-hide-outline")

    def _outline_shown(self) -> bool:
        if self.screen.has_class("-narrow"):
            return self.screen.has_class("-outline")
        return not self.screen.has_class("-hide-outline")

    def action_leave_outline(self) -> None:
        """Back to the text; in a narrow terminal, the outline closes."""
        if self.screen.has_class("-outline"):
            self.action_outline()
        else:
            self.query_one(Reader).focus()

    def action_switch_pane(self) -> None:
        """Between the outline and the text, showing the outline if hidden."""
        if self.focused is not self.query_one(Reader):
            self.action_leave_outline()
        elif self._outline_shown():
            self.query_one(Tree).focus()
        else:
            self.action_outline()

    def action_outline(self) -> None:
        self._outline_open = not self._outline_shown()
        self._layout(self.size.width)
        if self._outline_open:
            self.query_one(Tree).focus()
        else:
            self.query_one(Reader).focus()

    # Running work in the background ------------------------------------------

    def later(self, callback: Callable[..., object], *args: Any, **kwargs: Any) -> None:
        """Run ``callback`` on the UI thread, from any thread."""
        if threading.get_ident() == self._thread_id:
            callback(*args, **kwargs)
        else:
            with contextlib.suppress(RuntimeError):  # The app has stopped.
                self.call_from_thread(callback, *args, **kwargs)

    def background[T](
        self,
        job: Callable[[], T],
        done: Callable[[T], object],
        *,
        group: str = "load",
        exclusive: bool = True,
        failed: Callable[[], object] | None = None,
    ) -> None:
        """Run ``job`` in a thread, then ``done`` with its result on the UI
        thread; an error is a notification. Unless ``exclusive`` is False,
        a newer job in the same group replaces an older one."""

        def work() -> None:
            try:
                result = job()
            except (ECFRError, OSError) as exc:
                self.later(self._failed, exc, failed)
            else:
                if not get_current_worker().is_cancelled:
                    self.later(done, result)
            finally:
                self.later(self._idle)

        self._busy += 1
        self.update_status()
        self.run_worker(work, thread=True, exclusive=exclusive, group=group)

    def _idle(self) -> None:
        self._busy = max(0, self._busy - 1)
        if not self._busy:
            self.status.clear()  # A request that failed never finished.
        self.update_status()

    def _failed(self, exc: BaseException, failed: Callable[[], object] | None) -> None:
        if failed is not None:
            failed()
        self.report(exc)

    def report(self, exc: BaseException) -> None:
        """An error as a notification, titled as the command line titles it."""
        title = next((t for cls, t in ERROR_TITLES if isinstance(exc, cls)), "Error")
        self.notify(str(exc), title=title, severity="error", timeout=10)

    def update_status(self) -> None:
        """The right of the header: what's being fetched, else the date of
        the text (highlighted if it's a past date the user chose)."""
        activity = self.status.activity
        status = self.query_one("#status", Static)
        if activity:
            status.update(f"fetching {activity}…")
        elif self._busy:
            status.update("loading…")
        elif self.view is None:
            status.update("")
        else:
            view = self.view
            when = view.node.date if view.node is not None else view.entry.date
            style = "$warning" if self.date else MUTED
            status.update(Content.styled(f"as of {when}" if when else "", style))

    # Loading places --------------------------------------------------------

    def _root(self) -> Outline:
        return self.ecfr.outline(date=self.date)

    def _planted(self, root: Outline) -> None:
        """The outline of the whole CFR is in the tree; open the first place."""
        tree = self.query_one("#outline", Tree)
        tree.clear()
        tree.root.data = root
        self._fill(tree.root, root.children)
        tree.root.expand()
        pending, self._pending = self._pending, None
        if pending is None:
            return
        text, typed, cursor = pending
        if text is not None and typed:
            self.go(text)
        else:
            self.load(text, record=typed, cursor=cursor)

    def resolve(self, text: str) -> str:
        """An address for anything typed: a citation, address or link, read
        in the open title if it doesn't name one. Raises InvalidCitationError."""
        return str(Address.parse(text, title=self._title()))

    def go(self, text: str) -> None:
        """Open a citation, address or link typed by the user."""
        try:
            address = self.resolve(text)
        except InvalidCitationError as exc:
            self.report(exc)
            return
        self.load(address)

    def load(
        self,
        address: str | None,
        *,
        record: bool = True,
        cursor: int = 0,
        within: str | None = None,
        first: bool = False,
    ) -> None:
        """Show an address (None: the whole CFR), in the background, with
        the cursor on what's ``within`` it if given. Unless ``record`` is
        False, it's added to the history; ``first`` puts it before the first
        place instead."""
        anchor = None
        if address is not None:
            place = Address.parse(address)
            if place.date is not None:
                self._set_date(place.date)
            if place.paragraph and "section" in place.hierarchy:
                section = place.hierarchy["section"]
                anchor = f"p-{section}" + "".join(f"({m})" for m in place.markers)
            address = str(dataclasses.replace(place, paragraph=(), date=None))
        view = self.view
        place = Place(address, cursor, anchor, within)
        if view is not None and anchor is not None and view.entry.address == address:
            self._show(view, place, record)  # Here already.
            return
        self.background(
            lambda: self._fetch(address),
            lambda view: self._show(view, place, record, first=first),
        )

    def _set_date(self, day: dt.date) -> None:
        if day != self.date:
            self.date = day
            self.background(self._root, self._planted, group="tree")

    def _fetch(self, address: str | None) -> View:
        if address is not None:  # Says why, if it isn't there at this date.
            self.ecfr.outline(address, date=self.date)
        root = self.ecfr.outline(date=self.date)
        entry = root if address is None else root[address]
        path = [*reversed(entry.ancestors), entry]
        for above in path:  # Load each title's outline here, not in the UI.
            above.children  # noqa: B018
        node = None
        if self._readable(entry):
            node = self.ecfr.get(entry.address, date=entry.date)
            pieces = text_pieces(node)
        else:
            pieces = contents_pieces(entry)
        return View(entry, node, pieces, path)

    @staticmethod
    def _readable(entry: Outline) -> bool:
        """Whether to show text (not contents): sections and appendices,
        and parts and below unless huge."""
        if entry.level in LEAVES:
            return True
        if entry.level in CONTENTS_LEVELS:
            return False
        part = next((e for e in (entry, *entry.ancestors) if e.level == "part"), None)
        return part is None or part.size is None or part.size <= TEXT_BELOW

    def _show(
        self, view: View, place: Place, record: bool, *, first: bool = False
    ) -> None:
        reader = self.query_one(Reader)
        if self.index >= 0 and self.view is not None:
            self.history[self.index].cursor = reader.cursor  # Come back to it.
        cursor = self._landing(view, place)
        place = dataclasses.replace(place, cursor=cursor, anchor=None, within=None)
        if record:
            del self.history[self.index + 1 :]
            self.history.append(place)
            self.index = len(self.history) - 1
        elif first:
            self.history.insert(0, place)
            self.index = 0
        if view is self.view:
            reader.mark(cursor, None)
            reader.call_after_refresh(reader.scroll_cursor, True)
            return
        self.view = view
        reader.show(view.pieces, cursor)
        self._crumbs(view)
        self._reveal(view.path)
        if self.screen.has_class("-narrow") and self._outline_open:
            self._outline_open = False
            self._layout(self.size.width)
            reader.focus()

    @staticmethod
    def _landing(view: View, place: Place) -> int:
        """Where the cursor goes: on the paragraph asked for; on the entry
        or heading of what's ``within``; else where it was, or on the first
        entry of contents."""
        pieces = view.pieces
        if place.anchor is not None:
            return next(
                (i for i, p in enumerate(pieces) if place.anchor in p.anchors),
                place.cursor,
            )
        children = view.entry.children if place.within is not None else ()
        below = next((c for c in children if c.address == place.within), None)
        if below is not None:
            for i, piece in enumerate(pieces):
                if piece.kind == "entry" and piece.links[0].address == below.address:
                    return i
                if piece.kind == "heading" and piece.citation == below.citation:
                    return i
        if not place.cursor and len(pieces) > 1 and pieces[1].kind == "entry":
            return 1
        return place.cursor

    def _crumbs(self, view: View) -> None:
        names = [crumb(e) for e in view.path if e.level != CFR_LEVEL] or ["CFR"]
        room = self.size.width - STATUS_WIDTH
        while len(SEPARATOR.join(names)) > room and len(names) > 3:
            names[1:3] = ["…"]  # The levels after the title give way first.
        self.query_one("#crumbs", Static).update(SEPARATOR.join(names))
        self.update_status()

    # The outline tree ------------------------------------------------------

    def _fill(self, branch: TreeNode[Outline], children: Sequence[Outline]) -> None:
        for child in children:
            label = Text(child.heading, style="dim" if child.reserved else "")
            if child.level in LEAVES or child.reserved:
                branch.add_leaf(label, data=child)
            else:
                branch.add(label, data=child)

    @on(Tree.NodeExpanded, "#outline")
    def _expanded(self, event: Tree.NodeExpanded[Outline]) -> None:
        branch = event.node
        entry = branch.data
        if branch.children or entry is None:
            return

        def fill(children: Sequence[Outline]) -> None:
            if not branch.children:  # Not filled meanwhile, to show a place.
                self._fill(branch, children)

        self.background(lambda: entry.children, fill, group="expand", exclusive=False)

    @on(Tree.NodeSelected, "#outline")
    def _selected(self, event: Tree.NodeSelected[Outline]) -> None:
        entry = event.node.data
        if entry is not None:
            self.load(entry.address or None)

    def _reveal(self, path: list[Outline]) -> None:
        """Open the tree down to the entry shown, and put its cursor there."""
        tree = self.query_one("#outline", Tree)
        branch: TreeNode[Outline] = tree.root
        for entry in path[1:]:
            if not branch.children and branch.data is not None:
                self._fill(branch, branch.data.children)
            found = next(
                (
                    c
                    for c in branch.children
                    if c.data and c.data.address == entry.address
                ),
                None,
            )
            if found is None:
                return
            branch.expand()
            branch = found
        if branch is not tree.root:
            tree.call_after_refresh(self._point, tree, branch)

    @staticmethod
    def _point(tree: Tree[Outline], branch: TreeNode[Outline]) -> None:
        """Put the tree's cursor on a node, once its lines are laid out."""
        tree.move_cursor(branch)
        tree.scroll_to_node(branch, animate=False)

    # Actions -----------------------------------------------------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keys for the text do nothing while a dialog is open."""
        return not (action in READER_ACTIONS and isinstance(self.screen, ModalScreen))

    def action_next_piece(self) -> None:
        self.query_one(Reader).step(1)

    def action_previous_piece(self) -> None:
        self.query_one(Reader).step(-1)

    def action_follow(self, index: int, number: int) -> None:
        """A reference was clicked."""
        reader = self.query_one(Reader)
        if index < len(reader.pieces) and number < len(reader.pieces[index].links):
            reader.mark(index, number)
            self.open_link(reader.pieces[index].links[number])

    def open_link(self, link: Link) -> None:
        if link.address is not None:
            self.load(link.address)
        elif link.url:
            self.open_url(link.url)
            self.notify(f"Opened {link.url}", timeout=4)

    def _neighbour(self, forward: bool) -> None:
        view = self.view
        if view is None:
            return
        entry = view.entry
        if entry.level == CFR_LEVEL:
            self.notify("Open a title first", timeout=4)
            return

        def find() -> Outline | None:
            if forward and entry.level not in LEAVES:
                first = next(
                    (e for e in entry.iter() if e.level in LEAVES and not e.reserved),
                    None,
                )
                if first is not None:
                    return first
            return entry.next if forward else entry.previous

        def done(found: Outline | None) -> None:
            if found is None:
                self.notify(
                    f"No {'next' if forward else 'previous'} section", timeout=4
                )
            else:
                self.load(found.address)

        self.background(find, done, group="step")

    def action_next_section(self) -> None:
        self._neighbour(forward=True)

    def action_previous_section(self) -> None:
        self._neighbour(forward=False)

    def action_up(self, first: bool = False) -> None:
        """Up a level, the cursor on where it came from."""
        view = self.view
        if view is None or view.entry.level == CFR_LEVEL:
            return
        above = view.path[-2]
        self.load(
            above.address or None,
            record=not first,
            within=view.entry.address,
            first=first,
        )

    def action_back_out(self) -> None:
        """Escape in the text: let go of the chosen reference; else back;
        at the first place, up a level (which becomes the first place)."""
        reader = self.query_one(Reader)
        if reader.link is not None:
            reader.mark(reader.cursor, None)
        elif self.index > 0:
            self.action_go_back()
        else:
            self.action_up(first=True)

    def action_go_back(self) -> None:
        if self.index > 0:
            self._travel(self.index - 1)

    def action_go_forward(self) -> None:
        if self.index < len(self.history) - 1:
            self._travel(self.index + 1)

    def _travel(self, index: int) -> None:
        if self.view is not None:
            self.history[self.index].cursor = self.query_one(Reader).cursor
        self.index = index
        place = self.history[index]
        self.load(place.address, record=False, cursor=place.cursor)

    def action_copy(self) -> None:
        piece = self.query_one(Reader).current
        if piece is None:
            return
        self.copy_to_clipboard(piece.citation)
        self.notify(f"Copied {piece.citation}", timeout=3)

    def action_go(self) -> None:
        self.push_screen(GoTo(self.complete), self._chosen)

    def action_search(self) -> None:
        self.push_screen(Search(within=self._scope()), self._chosen)

    def _chosen(self, address: str | None) -> None:
        if address:
            self.load(address)

    def action_date(self) -> None:
        self.push_screen(DateDialog(self.date), self._dated)

    def _dated(self, text: str | None) -> None:
        if text is None:
            return
        day = to_date(text) if text else None
        if day == self.date:
            return
        view = self.view
        address = view.entry.address or None if view is not None else None

        def check() -> Outline:
            """The outline at the new date, if what's open has text then."""
            if address is not None:
                self.ecfr.outline(address, date=day)
            return self.ecfr.outline(date=day)

        def redated(root: Outline) -> None:
            self.date = day
            if view is not None:  # Open it again at the new date.
                cursor = self.query_one(Reader).cursor
                self._pending = (address, False, cursor)
            self._planted(root)
            self.notify(f"Text as of {day}" if day else "Latest text", timeout=3)

        self.background(check, redated, group="tree")

    def action_history(self) -> None:
        view = self.view
        if view is None or view.entry.level == CFR_LEVEL:
            self.notify("Open a title first", timeout=4)
            return
        self.push_screen(History(view.entry.citation))

    def action_export(self) -> None:
        view = self.view
        if view is None or view.entry.level == CFR_LEVEL:
            self.notify("Open a title or part to export it", timeout=4)
            return
        place = Address.parse(view.entry.address)
        if self.date is not None:
            place = dataclasses.replace(place, date=view.entry.date)
        stem = str(place).replace("/", "_")
        self.push_screen(
            Export(view.entry.citation, stem, self.directory), self._export
        )

    def _export(self, chosen: tuple[Format, Path] | None) -> None:
        view = self.view
        if chosen is None or view is None:
            return
        format, path = chosen
        node, entry, ecfr = view.node, view.entry, self.ecfr

        def save() -> Path:
            if node is not None:
                return node.export(path, format)
            saved = ecfr.save(entry.address, path, date=entry.date, format=format)
            assert isinstance(saved, Path)
            return saved

        def saved(file: Path) -> None:
            self.notify(
                f"{file} · {size(file.stat().st_size)}",
                title=f"Saved {entry.citation}",
                timeout=6,
            )

        self.background(save, saved, group="export")

    def action_help(self) -> None:
        self.push_screen(Help())

    # Helpers ---------------------------------------------------------------

    def _title(self) -> int | None:
        view = self.view
        if view is None or view.entry.level == CFR_LEVEL:
            return None
        return int(view.entry.address.split("/")[0])

    def _scope(self) -> Outline | None:
        """What a search can be limited to: the open part, else the title."""
        view = self.view
        if view is None or view.entry.level == CFR_LEVEL:
            return None
        path = view.path
        return next((e for e in reversed(path) if e.level == "part"), path[1])

    def complete(self, text: str) -> list[Outline]:
        """Outline entries that match what's typed so far, for "go to".

        Runs in a worker: it may load a title's outline.
        """
        root = self.ecfr.outline(date=self.date)
        text = text.strip()
        if not text:
            return []
        if only := TITLE_ONLY.fullmatch(text):
            number = only[1]
            title = next((t for t in root.children if t.identifier == number), None)
            return [title, *title.children][:MATCHES] if title is not None else []
        if titled := TITLED.match(text):
            number, rest = titled[1], titled[2]
        elif (current := self._title()) is not None:
            number, rest = str(current), text
        else:
            words = text.lower()
            return [t for t in root.children if words in t.heading.lower()][:MATCHES]
        title = next((t for t in root.children if t.identifier == number), None)
        if title is None or title.reserved:
            return []
        level = None
        if found := LEVEL_WORDS.match(rest):
            word = found["level"].lower()
            level = "subpart" if word.startswith("sub") else LEVEL_NAMES[word[0]]
            rest = rest[found.end() :]
        wanted = rest.strip().split("(")[0].lower()
        matches = []
        for entry in title.iter():
            if level is not None and entry.level != level:
                continue
            identifier = (entry.identifier or "").lower()
            named = len(wanted) >= 3 and not wanted[0].isdigit()
            if (
                (wanted and identifier.startswith(wanted))
                or (not wanted and level is not None)
                or (named and wanted in entry.heading.lower())
            ):
                matches.append(entry)
        exact = [m for m in matches if (m.identifier or "").lower() == wanted]
        others = [m for m in matches if m not in exact]
        return (exact + others)[:MATCHES]
