"""What the terminal browser shows: a node's text, or an outline's contents,
as a list of :class:`Piece`, one per paragraph, heading, table or note.

The pieces come from the same block model every export format writes
(:mod:`ecfr_md.render.blocks`), with cross-references found by
:func:`~ecfr_md.references.find_references` and kept as :class:`Link`, so
the reader can step through paragraphs and follow what they cite.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from rich import box
from rich.table import Table as RichTable
from rich.text import Text
from textual.content import Content
from textual.style import Style

from ecfr_md.address import Address
from ecfr_md.document import Node
from ecfr_md.references import Reference, find_references
from ecfr_md.render.blocks import (
    Block,
    Heading,
    Image,
    Item,
    Note,
    Paragraph,
    Table,
    node_blocks,
)
from ecfr_md.render.inline import plain, spans
from ecfr_md.results import Outline
from ecfr_md.ui import SUPERSCRIPT, size

INDENT = 2  # Columns per paragraph depth.
MUTED = "$foreground 60%"  # Notes, details, excerpts: there, but quiet.
LINK = "ref:"  # How a reference's link is marked while its text is parsed.
# References that lead where the reader already is.
SELF = re.compile(r"[Tt]his\s+(?:section|subpart|part)")
CONTENTS_LEVELS = frozenset({"cfr", "title", "subtitle", "chapter", "subchapter"})
NAMES = {
    "title": "Title",
    "subtitle": "Subtitle",
    "chapter": "Chapter",
    "subchapter": "Subchapter",
    "part": "Part",
    "subpart": "Subpart",
}


@dataclass(frozen=True)
class Link:
    """Where a reference, or an entry in contents, leads: a place in the
    CFR (``address``), or a page elsewhere (``url``)."""

    text: str
    address: str | None
    url: str


@dataclass
class Piece:
    """One thing the reader steps to: a paragraph, heading, table or note."""

    kind: str
    """"heading", "paragraph", "item", "table", "note", "image" or "entry"."""
    parts: list[str] = field(default_factory=list)
    """Inline Markdown, one string per run of text (a note has several)."""
    depth: int = 0
    links: list[Link] = field(default_factory=list)
    citation: str = ""
    """What copying the citation here copies, e.g. "45 CFR 164.502(a)(1)"."""
    anchors: tuple[str, ...] = ()
    """The eCFR ids of the paragraphs this piece starts, e.g. "p-164.502(a)"."""
    table: Table | None = None
    level: int = 0
    """A heading's level, 1 for the text's own."""
    detail: str = ""
    """A contents entry's dimmed detail, e.g. its size."""
    dim: bool = False

    def render(self, index: int, selected: int | None = None) -> Content | RichTable:
        """The piece for display. Its links follow on click (``app.follow``);
        link ``selected`` is highlighted."""
        if self.table is not None:
            return _table(self.table)
        if self.kind == "entry":
            return _entry(self, selected)
        runs = [self._run(index, i, selected) for i in range(len(self.parts))]
        body = Content("\n\n").join(runs)
        match self.kind:
            case "heading":
                return body.stylize_before("bold")
            case "note":
                return body.stylize_before(MUTED)
            case "item":
                return Content("• ") + body
            case "image":
                return body.stylize_before(f"{MUTED} italic")
        return body

    def _run(self, index: int, part: int, selected: int | None) -> Content:
        """One run of inline Markdown, its references made links."""
        first = sum(_count_links(p) for p in self.parts[:part])
        pieces: list[str | tuple[str, str | Style]] = []
        for span in spans(self.parts[part]):
            if span.kind == "break":
                pieces.append("\n")
                continue
            text = span.text.translate(SUPERSCRIPT) if span.sup else span.text
            style = Style(bold=span.bold or None, italic=span.italic or None)
            if span.href and span.href.startswith(LINK):
                number = first + int(span.href.removeprefix(LINK))
                action = f"app.follow({index}, {number})"
                style += Style.from_meta({"@click": action})
                if number == selected:
                    style += Style(reverse=True)
            pieces.append((text, style))
        return Content.assemble(*pieces)


def _count_links(text: str) -> int:
    return text.count(f"]({LINK}")


def _entry(piece: Piece, selected: int | None) -> Content:
    """An entry in contents: clicking anywhere on it opens it, so it isn't
    drawn as a link."""
    label = Content.styled(plain(piece.parts[0]), "bold" if selected == 0 else "")
    if piece.dim:
        label = label.stylize_before(MUTED)
    if piece.detail:
        label += Content.styled(f"  {piece.detail}", MUTED)
    return label


def _table(table: Table) -> RichTable:
    grid = RichTable(
        box=box.SIMPLE_HEAD,
        show_header=bool(table.header),
        header_style="bold",
        pad_edge=False,
        expand=False,
    )
    width = max((len(r) for r in table.rows), default=0)
    head = table.rows[0] if table.header else ()
    for i in range(width):
        grid.add_column(plain(head[i]) if i < len(head) else "")
    for row in table.rows[1 if table.header else 0 :]:
        cells = [Text(plain(c).translate(SUPERSCRIPT)) for c in row]
        grid.add_row(*cells, *([""] * (width - len(cells))))
    return grid


# Building pieces ---------------------------------------------------------------


def text_pieces(node: Node) -> list[Piece]:
    """A node's text as pieces, each with its references and citation."""
    context = node._context()
    pinned = context.date
    pieces: list[Piece] = []
    for block in node_blocks(node):
        if isinstance(block, Heading) and block.target is not None:
            place = Address.parse(block.target.address)
            context = dataclasses.replace(place, date=pinned)
        pieces.append(_piece(block, context))
    return pieces


def _piece(block: Block, context: Address) -> Piece:
    citation = context.citation()
    match block:
        case Heading(level, text):  # Its citation is itself: not a reference.
            return Piece("heading", [text], level=level, citation=citation)
        case Paragraph(text, depth, anchors):
            if anchors:
                citation = f"{context.title} CFR {anchors[0].removeprefix('p-')}"
            piece = Piece("paragraph", depth=depth, anchors=anchors, citation=citation)
            _add_text(piece, text, context)
            return piece
        case Item(text):
            piece = Piece("item", citation=citation)
            _add_text(piece, text, context)
            return piece
        case Table(rows):
            piece = Piece("table", table=block, citation=citation)
            for row in rows:
                for cell in row:
                    piece.links += _links(cell, context)
            return piece
        case Note(inner):
            piece = Piece("note", citation=citation, depth=1)
            for text in _note_texts(inner):
                _add_text(piece, text, context)
            return piece
        case Image(src, alt):
            piece = Piece("image", citation=citation)
            piece.parts.append(f"[Image{': ' + alt if alt else ''}]")
            piece.links.append(Link(alt or "image", None, src))
            return piece


def _note_texts(blocks: Iterable[Block]) -> Iterator[str]:
    for block in blocks:
        match block:
            case Heading(text=text) | Paragraph(text=text) | Item(text=text):
                yield text
            case Note(inner):
                yield from _note_texts(inner)
            case Table(rows):
                yield " · ".join(" | ".join(row) for row in rows)
            case Image(alt=alt):
                yield f"[Image{': ' + alt if alt else ''}]"


def _add_text(piece: Piece, text: str, context: Address) -> None:
    """Add a run of text to a piece, its references marked as links."""
    found = _references(text, context)
    marked, last = [], 0
    for i, ref in enumerate(found):
        marked += [text[last : ref.start], f"[{ref.text}]({LINK}{i})"]
        last = ref.end
        target = str(ref.target.undated()) if ref.target else None
        piece.links.append(Link(ref.text, target, ref.url))
    piece.parts.append("".join([*marked, text[last:]]))


def _links(text: str, context: Address) -> list[Link]:
    return [
        Link(r.text, str(r.target.undated()) if r.target else None, r.url)
        for r in _references(text, context)
    ]


def _references(text: str, context: Address) -> list[Reference]:
    """References worth following: not "this section" and the like, which
    lead where the reader already is."""
    return [
        r
        for r in find_references(text, context, markdown=True)
        if not SELF.fullmatch(r.text)
    ]


def contents_pieces(entry: Outline) -> list[Piece]:
    """An outline entry's contents: its heading, then one entry per child."""
    heading = Piece("heading", [_escape(entry.heading)], level=1)
    heading.citation = entry.citation
    pieces = [heading]
    for child in entry.children:
        piece = Piece("entry", citation=child.citation, dim=child.reserved)
        piece.parts.append(_escape(child.heading))
        piece.links.append(Link(child.heading, child.address or None, child.url))
        if child.level == "part" and child.size:
            piece.detail = size(child.size)
        pieces.append(piece)
    return pieces


def _escape(text: str) -> str:
    """Plain text as inline Markdown that reads back as itself."""
    return "".join(f"\\{c}" if c in "\\`*_[]<>&#!" else c for c in text)


def crumb(entry: Outline) -> str:
    """A short name for an outline entry in the breadcrumb: "Part 164", "§ 164.502"."""
    if entry.level == "cfr":
        return "CFR"
    if entry.level == "section":
        return f"§ {entry.identifier}"
    if entry.level == "appendix" and entry.identifier:
        return entry.identifier.split(" to ")[0]
    name = NAMES.get(str(entry.level))
    if name and entry.identifier:
        return f"{name} {entry.identifier}"
    return entry.heading.split("—")[0]
