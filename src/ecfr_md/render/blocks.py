"""Documents as blocks: the one model every export format writes.

A node becomes a sequence of blocks in reading order: a :class:`Heading` per
division (with the :class:`Target` it names), :class:`Paragraph` (with its
nesting depth and eCFR-style anchors, e.g. ``p-164.502(a)(1)``),
:class:`Table`, :class:`Note`, :class:`Item` and :class:`Image`. Text inside
blocks is ecfr-md's inline Markdown; :mod:`ecfr_md.render.inline` reads it.

Blocks are built from ``(event, element)`` pairs, as Markdown is, so a large
document can be written while it is read.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from markdown_it.token import Token

from ecfr_md import markdown
from ecfr_md.document import Division, Node, Section, _division
from ecfr_md.document import Paragraph as ParagraphNode
from ecfr_md.output import TYPE_LEVELS, Source
from ecfr_md.params import Hierarchy, as_hierarchy
from ecfr_md.render.inline import parser

MAX_DEPTH = 6


@dataclass(frozen=True)
class Target:
    """The division a heading starts: what it is and where it lives."""

    level: str
    identifier: str
    heading: str
    citation: str
    address: str
    url: str
    anchor: str
    # Only the first heading of a document has it: the document's metadata.
    provenance: dict[str, Any] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Heading:
    """A division's heading; ``level`` 1 is the document's own."""

    level: int
    text: str
    target: Target | None = None


@dataclass(frozen=True)
class Paragraph:
    """A paragraph of text. ``depth`` is its level in the section, 1 for
    (a), 2 for (1) and so on, or 0 outside numbered paragraphs. ``anchors``
    are the paragraphs it starts, e.g. ``p-164.502(a)``, ``p-164.502(a)(1)``."""

    text: str
    depth: int = 0
    anchors: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    """An entry in a bulleted list."""

    text: str


@dataclass(frozen=True)
class Table:
    """A table: rows of cells (inline Markdown); the first ``header`` rows head it."""

    rows: tuple[tuple[str, ...], ...]
    header: int = 0
    markdown: str = ""


@dataclass(frozen=True)
class Note:
    """Authority, source, editorial and other notes, and quoted extracts."""

    blocks: tuple[Block, ...]
    markdown: str = ""


@dataclass(frozen=True)
class Image:
    """A figure on its own line; ``src`` is its URL."""

    src: str
    alt: str = ""


Block = Heading | Paragraph | Item | Table | Note | Image


def to_markdown(block: Block) -> str:
    """A block as Markdown, as ecfr-md writes it."""
    match block:
        case Heading(level, text):
            return f"{'#' * min(level, markdown.MAX_HEADING_LEVEL)} {text}"
        case Paragraph(text):
            return text
        case Item(text):
            return f"- {text}"
        case Image(src, alt):
            return f"![{alt}]({src})"
        case Table(markdown=source) | Note(markdown=source):
            return source


# Building blocks from XML ---------------------------------------------------


def node_blocks(node: Node) -> Iterator[Block]:
    """The blocks of a node and everything in it."""
    if isinstance(node, ParagraphNode):
        yield from _paragraph_blocks(node)
        return
    assert isinstance(node, Division)
    base = node.parent.hierarchy if node.parent else node.source.hierarchy
    yield from event_blocks(markdown.element_events(node.element), node.source, base)


def event_blocks(
    events: Iterable[tuple[str, ET.Element]],
    source: Source,
    base: Hierarchy,
    *,
    free: bool = False,
    top: int = 1,
) -> Iterator[Block]:
    """Blocks for a stream of XML events, in order, as they arrive.

    ``base`` is the hierarchy above the first division. The first heading's
    target carries the document's provenance. With ``free``, elements are
    discarded once written.
    """
    stack: list[_Open] = []
    leaf: _Open | None = None  # Sections and appendices are built whole.
    for event, el in events:
        if leaf is not None:
            if event == "end" and el is leaf.element:
                stack.pop()
                yield from _leaf(leaf, source, top)
                leaf = None
                _discard(free, stack, el)
            continue
        parent = stack[-1] if stack else None
        if event == "start":
            if parent and parent.division and not parent.headed and el.tag != "HEAD":
                yield parent.heading(None, source, top)
            stack.append(_open(el, stack, base, top))
            if el.get("TYPE") in markdown.LEAF_TYPES and markdown.is_division(el):
                leaf = stack[-1]
            continue
        opened = stack.pop()
        if opened.division and not opened.headed:  # Empty, without a heading.
            yield opened.heading(None, source, top)
        parent = stack[-1] if stack else None
        if parent is None or not parent.division:
            continue
        if el.tag == "HEAD":
            if not parent.headed:
                yield parent.heading(el, source, top)
        elif not opened.division:
            yield from from_markdown(markdown.join(markdown.render_blocks(el, 2)))
        _discard(free, stack, el)


@dataclass
class _Open:
    """An element being read, with what's known about it if it's a division."""

    element: ET.Element
    division: bool
    level: int = 0
    above: Hierarchy = field(default_factory=Hierarchy)  # Its ancestors' levels.
    levels: Hierarchy = field(default_factory=Hierarchy)  # Including its own.
    headed: bool = False

    def node(self, source: Source) -> Division:
        return _division(self.element, None, replace(source, hierarchy=self.above))

    def heading(self, head: ET.Element | None, source: Source, top: int) -> Heading:
        self.headed = True
        text = markdown.render_inline(head) if head is not None else ""
        node = self.node(source)
        return Heading(
            self.level, text or node.identifier, target(node, self.level == top)
        )


def _open(el: ET.Element, stack: list[_Open], base: Hierarchy, top: int) -> _Open:
    if not markdown.is_division(el):
        return _Open(el, division=False)
    above = next((o.levels for o in reversed(stack) if o.division), base)
    levels = dict(above)
    if (kind := TYPE_LEVELS.get(el.get("TYPE", ""))) is not None:
        levels[kind] = el.get("N", "")
    level = top + sum(1 for o in stack if o.division)
    return _Open(el, True, level, above, as_hierarchy(levels))


def _discard(free: bool, stack: list[_Open], el: ET.Element) -> None:
    if free and stack:
        stack[-1].element.remove(el)


def target(node: Node, document: bool = False) -> Target:
    """What a heading names; with ``document``, with the node's provenance."""
    if isinstance(node, ParagraphNode):
        anchor = _anchor(node)
    elif node.level == "section":
        anchor = node.identifier
    elif node.level == "title":
        anchor = f"title-{node.identifier}"
    else:
        anchor = node.address.rsplit("/", 1)[-1]
    return Target(
        level=node.level.value,
        identifier=node.identifier,
        heading=node.heading,
        citation=node.citation,
        address=node.address,
        url=node.url,
        anchor=anchor,
        provenance=node.provenance if document else None,
    )


def _leaf(opened: _Open, source: Source, top: int) -> Iterator[Block]:
    """A section or appendix: its heading, then its text."""
    el = opened.element
    yield opened.heading(el.find("HEAD"), source, top)
    node = opened.node(source)
    starts = _starts(node) if isinstance(node, Section) else {}
    for child in el:
        if child.tag == "HEAD":
            continue
        if markdown.is_division(child):
            yield from event_blocks(
                markdown.element_events(child),
                source,
                opened.levels,
                top=opened.level + 1,
            )
            continue
        # An element that starts paragraphs is indented to the first one's
        # depth and anchored to all of them; other text isn't indented, as
        # on the eCFR.
        started = starts.get(id(child), [])
        for block in from_markdown(markdown.join(markdown.render_blocks(child, 2))):
            if started and isinstance(block, Paragraph):
                anchors = tuple(_anchor(p) for p in started)
                block = replace(block, depth=started[0].depth, anchors=anchors)
                started = []
            yield block


def _starts(section: Section) -> dict[int, list[ParagraphNode]]:
    """The paragraphs each element of a section starts, by the element's id."""
    starts: dict[int, list[ParagraphNode]] = {}
    pending = list(reversed(section.children))
    while pending:
        paragraph = pending.pop()
        assert isinstance(paragraph, ParagraphNode)
        if paragraph._element is not None:
            starts.setdefault(id(paragraph._element), []).append(paragraph)
        pending += reversed(paragraph.children)
    return starts


def _anchor(paragraph: ParagraphNode) -> str:
    """The eCFR's id for a paragraph: ``p-164.502(a)(1)``."""
    section = paragraph.section.identifier if paragraph.section else ""
    return f"p-{section}" + "".join(f"({m})" for m in paragraph.path)


def _paragraph_blocks(node: ParagraphNode) -> Iterator[Block]:
    """A paragraph and its subparagraphs, headed by its citation."""
    yield Heading(1, node.citation, target(node, document=True))
    pending: list[ParagraphNode] = [node]
    while pending:
        paragraph = pending.pop()
        depth = paragraph.depth - node.depth + 1
        first = True
        for text in paragraph.blocks:
            for block in from_markdown(text):
                if isinstance(block, Paragraph):
                    anchors = (_anchor(paragraph),) if first else ()
                    block = replace(block, depth=depth, anchors=anchors)
                    first = False
                yield block
        pending += [
            c for c in reversed(paragraph.children) if isinstance(c, ParagraphNode)
        ]


# Reading Markdown ------------------------------------------------------------


def from_markdown(text: str) -> list[Block]:
    """Blocks for Markdown as ecfr-md writes it."""
    lines = text.splitlines()
    return _from_tokens(parser().parse(text), lines)


def _from_tokens(tokens: list[Token], lines: list[str]) -> list[Block]:
    blocks: list[Block] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        end = _close(tokens, i)
        inner = tokens[i + 1 : end]
        match token.type:
            case "heading_open":
                blocks.append(Heading(int(token.tag[1:]), inner[0].content))
            case "paragraph_open":
                blocks.append(_paragraph(inner[0]))
            case "blockquote_open":
                blocks.append(
                    Note(tuple(_from_tokens(inner, lines)), _source(token, lines))
                )
            case "bullet_list_open" | "ordered_list_open":
                blocks += [Item(t.content) for t in inner if t.type == "inline"]
            case "table_open":
                blocks.append(_table(inner, _source(token, lines)))
            case "html_block" | "code_block" | "fence":
                if token.content.strip():
                    blocks.append(Paragraph(" ".join(token.content.split())))
        i = end + 1
    return blocks


def _close(tokens: list[Token], start: int) -> int:
    """The index of the token that closes the one at ``start`` (itself if none)."""
    token = tokens[start]
    if token.nesting != 1:
        return start
    return next(
        i
        for i in range(start + 1, len(tokens))
        if tokens[i].nesting == -1 and tokens[i].level == token.level
    )


def _source(token: Token, lines: list[str]) -> str:
    start, end = token.map or (0, len(lines))
    return "\n".join(lines[start:end])


def _paragraph(inline: Token) -> Paragraph | Image:
    children = inline.children or []
    if len(children) == 1 and children[0].type == "image":
        image = children[0]
        return Image(str(image.attrs.get("src", "")), image.content)
    return Paragraph(inline.content)


def _table(tokens: list[Token], source: str) -> Table:
    rows: list[tuple[str, ...]] = []
    header = 0
    cells: list[str] = []
    for token in tokens:
        if token.type == "tr_open":
            cells = []
        elif token.type == "inline":
            cells.append(token.content)
        elif token.type == "tr_close":
            rows.append(tuple(cells))
        elif token.type == "thead_close":
            header = len(rows)
    if header and not any(c.strip() for c in rows[0]):  # An empty header row.
        rows, header = rows[1:], 0
    return Table(tuple(rows), header, source)
