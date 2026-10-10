"""Render eCFR XML as Markdown.

Headings follow nesting depth relative to the rendered element, so a whole
title starts with ``# Title``, while a single section starts with ``# §``.
Markdown has six heading levels; deeper nodes render as bold lines.

Rendering is driven by ``(event, element)`` pairs, either walked from an
element in memory or read from a file with ``iterparse``, in which case
processed elements are discarded as it goes.
"""

import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import BinaryIO

from ecfr_md.config import BASE_URL
from ecfr_md.errors import ResponseFormatError

Events = Iterable[tuple[str, ET.Element]]

MAX_HEADING_LEVEL = 6
INDEX_FILE = "index.md"
LEAF_TYPES = frozenset({"SECTION", "APPENDIX"})
DIR_NAMES = {"SUBCHAP": "subchapter", "SUBJGRP": "subject-group"}

# P, P-2 and P-DASH are paragraphs; FP, FP-1, FP1-2, FP-DASH are flush lines.
PARAGRAPH_TAG = re.compile(r"F?P\d*(?:-\w+)?|FRP")
# Only these start numbered paragraphs such as "(a)"; flush lines continue them.
NUMBERED_TAG = re.compile(r"P(?:-\d+)?")
SUBHEADING_TAG = re.compile(r"HD\d*|HED\d+|CHAPNO|TCAP|BCAP")
NOTE_TAGS = frozenset(
    {"AUTH", "SOURCE", "CITA", "APPRO", "SECAUTH", "PARAUTH", "EDNOTE", "NOTE"}
    | {"EFFDNOT", "CROSSREF", "FTNT", "XREF", "EXTRACT"}
)
SKIP_TAGS = frozenset({"AMDDATE", "VOLUME", "PRTPAGE", "CAPTION", "AC"})
INLINE_TAGS = frozenset(
    {"I", "B", "E", "SU", "FR", "FTREF", "HED", "PSPACE", "AC"}
    | {"sup", "sub", "br", "img", "strong", "em", "span"}
)
OMISSION = "\\* \\* \\* \\* \\*"  # <STARS/>: text omitted from the source.

# GPO emphasis codes: <E T="01"> bold, "03" bold italic, "51"-"54" (italic)
# superscript and subscript. Unknown codes are italic.
EMPHASIS = {"I": "*", "B": "**", "strong": "**", "em": "*", "HED": "**"}
E_CODES = {"01": "**", "02": "*", "03": "***", "04": "*"}
HTML_EMPHASIS = {
    "*": ("<em>", "</em>"),
    "**": ("<strong>", "</strong>"),
    "***": ("<strong><em>", "</em></strong>"),
}
WRAPPERS = {
    "SU": ("<sup>", "</sup>"),
    "sup": ("<sup>", "</sup>"),
    "sub": ("<sub>", "</sub>"),
}
E_WRAPPERS = {
    "51": WRAPPERS["sup"],
    "52": WRAPPERS["sub"],
    "53": WRAPPERS["sup"],
    "54": WRAPPERS["sub"],
}

SPACED_TAGS = frozenset({"HED", "PSPACE"})  # Label and text run together.
PARAGRAPH_MARKER = re.compile(r"^((?:\([A-Za-z0-9]+\))+)")
DIV_TAG = re.compile(r"DIV\d+$")
MARKDOWN_SPECIAL = "\\`*_<"  # Backslash first, so escapes aren't doubled.
ENTITY = re.compile(r"&(?=#?\w+;)")
# Text that Markdown would read as a list, heading, quote or rule if it
# started a line: "1. Loans", "- Note", "# 5", "> 3", "===".
BLOCK_START = re.compile(r"^(?:(\d+)([.)])(?=\s|$)|([-+#>=]))")


def parse(xml: bytes | str) -> ET.Element:
    """Parse eCFR XML, raising ResponseFormatError if it is malformed."""
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ResponseFormatError(f"Invalid eCFR XML: {exc}") from exc


def element_events(root: ET.Element) -> Iterator[tuple[str, ET.Element]]:
    """``iterparse``-style start/end events for an element already in memory."""
    yield "start", root
    stack = [(root, iter(root))]
    while stack:
        element, children = stack[-1]
        child = next(children, None)
        if child is None:
            stack.pop()
            yield "end", element
        else:
            yield "start", child
            stack.append((child, iter(child)))


def file_events(source: BinaryIO) -> Iterator[tuple[str, ET.Element]]:
    """Start/end events streamed from an XML file."""
    try:
        yield from ET.iterparse(source, events=("start", "end"))
    except ET.ParseError as exc:
        raise ResponseFormatError(f"Invalid eCFR XML: {exc}") from exc


def render(el: ET.Element) -> str:
    """One Markdown document for an element and everything inside it."""
    blocks: list[str] = []
    write_document(element_events(el), blocks.append)
    return join(blocks)


def write_document(
    events: Events,
    emit: Callable[[str], object],
    *,
    on_heading: Callable[[str, ET.Element], object] | None = None,
    free: bool = False,
) -> None:
    """Emit the Markdown blocks of a document, in order, as events arrive.

    ``on_heading`` receives the top-level heading as plain text, and its
    division element, before the first block. With ``free``, processed
    elements are discarded as they are written.
    """
    # Open elements, each with whether its heading has been emitted. The flag
    # lives with the element: ids can't be used, since freed elements' ids are
    # reused by new ones.
    stack: list[list] = []

    def heading(entry: list, head: ET.Element | None, level: int) -> None:
        entry[1] = True
        division = entry[0]
        text = render_inline(head) if head is not None else division.get("N", "")
        if on_heading and level == 1:
            on_heading(plain_text(head) if head is not None else text, division)
        emit(_heading(text, level))

    def divisions() -> int:
        return sum(1 for e, _ in stack if is_division(e))

    for event, el in events:
        parent = stack[-1] if stack else None
        if event == "start":
            level = divisions()
            stack.append([el, False])
            # A division without a HEAD is titled by its number, up front.
            if parent and is_division(parent[0]) and el.tag != "HEAD" and not parent[1]:
                heading(parent, None, level)
            continue
        stack.pop()
        parent = stack[-1] if stack else None
        if parent is None or not is_division(parent[0]):
            continue  # Rendered by its nearest division ancestor.
        level = divisions()
        if el.tag == "HEAD":
            if not parent[1]:
                heading(parent, el, level)
        elif not is_division(el):
            for block in _blocks(el, level + 1):
                emit(block)
        if free:
            parent[0].remove(el)


@dataclass
class _Folder:
    element: ET.Element
    path: PurePosixPath
    heading: str = ""  # Markdown heading line for index.md
    title: str = ""  # Inline heading text, for links from the parent
    plain: str = ""  # Heading without formatting, for metadata
    blocks: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)
    taken: set[str] = field(default_factory=set)


def write_tree(
    events: Events,
    emit: Callable[[PurePosixPath, str, list[ET.Element], str], object],
    *,
    free: bool = False,
) -> None:
    """Emit one file per section or appendix and an ``index.md`` per level.

    ``emit(path, markdown, divisions, heading)`` receives each file's relative
    path, its Markdown, the chain of division elements down to it and its plain
    heading. Index files arrive after their children.
    """
    stack: list[ET.Element] = []
    folders: list[_Folder] = []
    taken: set[str] = set()
    leaf: ET.Element | None = None  # Leaves are rendered whole when they end.
    for event, el in events:
        if event == "start":
            stack.append(el)
            if leaf is None and is_division(el):
                parent = folders[-1] if folders else None
                name = dedupe(slug(el), parent.taken if parent else taken)
                path = (parent.path if parent else PurePosixPath()) / name
                folders.append(_Folder(el, path))
                if el.get("TYPE") in LEAF_TYPES:
                    leaf = el
            continue
        stack.pop()
        if leaf is not None and el is not leaf:
            continue
        parent_el = stack[-1] if stack else None
        if folders and el is folders[-1].element:
            folder = folders.pop()
            chain = [f.element for f in folders] + [el]
            file_leaf = leaf
            if el is leaf:
                leaf = None
                file = folder.path.with_name(f"{folder.path.name}.md")
                emit(file, render(el), chain, plain_text(el.find("HEAD")))
            else:
                file = folder.path / INDEX_FILE
                heading = folder.heading or _heading(el.get("N", ""), 1)
                contents = ["\n".join(folder.contents)] if folder.contents else []
                body = join([heading, *folder.blocks, *contents])
                emit(file, body, chain, folder.plain)
            if folders:
                link = file.relative_to(folders[-1].path).as_posix()
                # A leaf still has its HEAD; other folders' HEADs were freed.
                title = heading_text(el) if el is file_leaf else folder.title
                text = (
                    (title or el.get("N", "")).replace("[", "\\[").replace("]", "\\]")
                )
                folders[-1].contents.append(f"- [{text}]({link})")
        elif folders and parent_el is folders[-1].element:
            folder = folders[-1]
            if el.tag == "HEAD":
                if not folder.heading:
                    folder.title, folder.plain = render_inline(el), plain_text(el)
                    folder.heading = _heading(folder.title, 1)
            else:
                folder.blocks += _blocks(el, 2)
        else:
            continue
        if free and parent_el is not None:
            parent_el.remove(el)


def render_blocks(el: ET.Element, level: int = 1) -> list[str]:
    """Markdown blocks (paragraphs, tables, quotes) for an element."""
    return _blocks(el, level)


def render_inline(el: ET.Element) -> str:
    """An element's text on one line, with inline formatting."""
    return _clean(_inline(el))


def plain_text(el: ET.Element | None) -> str:
    """An element's text without formatting, e.g. for headings in metadata."""
    return _clean("".join(el.itertext())) if el is not None else ""


def escape_block_start(text: str) -> str:
    """Escape text that would start a list, heading, quote or rule."""
    match = BLOCK_START.match(text)
    if match is None:
        return text
    if match[1]:
        return f"{match[1]}\\{match[2]}{text[match.end() :]}"
    return f"\\{text}"


def join(blocks: list[str]) -> str:
    return "\n\n".join(blocks) + "\n"


def is_division(el: ET.Element) -> bool:
    return bool(DIV_TAG.match(el.tag))


def is_paragraph(el: ET.Element) -> bool:
    return bool(PARAGRAPH_TAG.fullmatch(el.tag))


def is_subheading(el: ET.Element) -> bool:
    return bool(SUBHEADING_TAG.fullmatch(el.tag))


def is_numbered(el: ET.Element) -> bool:
    """Whether a paragraph element can start a numbered paragraph."""
    return bool(NUMBERED_TAG.fullmatch(el.tag))


def heading_text(el: ET.Element) -> str:
    head = el.find("HEAD")
    return _clean(_inline(head)) if head is not None else el.get("N", "")


def _heading(text: str, level: int) -> str:
    if level > MAX_HEADING_LEVEL:
        return _strong(text)
    return f"{'#' * level} {text}"


def _blocks(el: ET.Element, level: int) -> list[str]:
    """Markdown blocks for an element; ``level`` is the next heading level."""
    tag = el.tag
    if is_division(el):
        children = [c for c in el if c.tag != "HEAD"]
        out = [_heading(heading_text(el), level)]
        return out + [b for c in children for b in _blocks(c, level + 1)]
    if tag in SKIP_TAGS:
        return []
    if tag == "STARS":
        return [OMISSION]
    if is_paragraph(el):
        text = _paragraph(el)
        return [text] if text else []
    if is_subheading(el):
        text = render_inline(el)
        return [_strong(text)] if text else []
    if tag in NOTE_TAGS:
        return [_quote(el, level)]
    if tag == "TABLE":
        return [_table(el)]
    if tag == "LDRWK":
        return [_leaders(el)]
    if tag == "LI":
        return [f"- {render_inline(el)}"]
    return _content(el, level)


def _content(el: ET.Element, level: int) -> list[str]:
    """Blocks for a container, or one inline paragraph if it holds only text."""
    if not any(d.tag not in INLINE_TAGS for d in el.iter() if d is not el):
        text = escape_block_start(_clean(_styled(el)))
        return [text] if text else []
    blocks = [_loose_text(el.text)]
    for child in el:
        blocks += [*_blocks(child, level), _loose_text(child.tail)]
    return [b for b in blocks if b]


def _loose_text(text: str | None) -> str:
    """Text between block elements, as a paragraph of its own."""
    return escape_block_start(_clean(_escape(text or "")))


def _paragraph(el: ET.Element) -> str:
    """Bold the leading paragraph designation, e.g. (a)(1) or (ii)."""
    text = _clean(_inline(el))
    match = PARAGRAPH_MARKER.match(text)
    if match is None:
        return escape_block_start(text)
    rest = text[match.end() :]
    if rest and not rest[0].isspace():  # "**(i)**When" wouldn't render as bold.
        return f"<strong>{match[1]}</strong>{rest}"
    return f"**{match[1]}**{rest}"


def _quote(el: ET.Element, level: int) -> str:
    body = "\n\n".join(_content(el, level))
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())


def _table(el: ET.Element) -> str:
    rows = _grid(el)
    width = max((len(r) for r in rows), default=1)
    head_rows = sum(1 for _ in el.iterfind("THEAD/TR"))
    header = rows[0] if head_rows else [""] * width
    body = rows[1:] if head_rows else rows
    lines = [_table_line(header, width), "|" + " --- |" * width]
    lines += [_table_line(r, width) for r in body]
    caption = el.find("CAPTION")
    if caption is not None:  # Captions can hold several lines (<P>s).
        parts = [render_inline(p) for p in caption] or [render_inline(caption)]
        lines.insert(0, "<br>".join(_strong(p) for p in parts if p) + "\n")
    return "\n".join(lines)


def _grid(el: ET.Element) -> list[list[str]]:
    """Table cells by row and column, with row and column spans laid out.

    A cell spanning several rows leaves its column empty in the rows below,
    so later cells stay in their own columns.
    """
    rows: list[list[str]] = []
    spanned: dict[int, int] = {}  # Column -> rows still covered from above.
    for tr in el.iter("TR"):
        row: list[str] = []
        for cell in tr:
            _fill_spanned(row, spanned)
            text = _clean(_inline(cell)).replace("|", "\\|")
            down = _span(cell.get("rowspan"))
            for k in range(_span(cell.get("colspan"))):
                if down > 1:
                    spanned[len(row)] = down - 1
                row.append(text if k == 0 else "")
        last = max((c for c, n in spanned.items() if n), default=-1)
        while len(row) <= last:  # Spans continuing past this row's last cell.
            _fill_spanned(row, spanned)
            if len(row) <= last and not spanned.get(len(row)):
                row.append("")
        rows.append(row)
    return rows


def _fill_spanned(row: list[str], spanned: dict[int, int]) -> None:
    """Add empty cells for columns covered by a cell from a row above."""
    while spanned.get(len(row), 0):
        spanned[len(row)] -= 1
        row.append("")


def _span(value: str | None) -> int:
    """A rowspan or colspan; malformed values count as 1."""
    return int(value) if value and value.strip().isdigit() and int(value) > 0 else 1


def _leaders(el: ET.Element) -> str:
    """Leader work ("Dividends ...... $10,000") as a two-column table."""
    rows: list[list[str]] = []
    for child in el:
        text = render_inline(child).replace("|", "\\|")
        if child.tag == "LDRFIG" and rows and len(rows[-1]) == 1:
            rows[-1].append(text)
        else:
            rows.append([text] if child.tag != "LDRFIG" else ["", text])
    return "\n".join(["|  |  |", "| --- | --- |", *(_table_line(r, 2) for r in rows)])


def _table_line(cells: list[str], width: int) -> str:
    return "| " + " | ".join((cells + [""] * width)[:width]) + " |"


def _image(el: ET.Element) -> str:
    src = el.get("src", "")
    return f"![]({BASE_URL}{src})" if src.startswith("/") else f"![]({src})"


def _inline(el: ET.Element) -> str:
    """Element text with emphasis, superscripts and line breaks as Markdown."""
    parts = [_escape(el.text or "")]
    for child in el:
        tail = _escape(child.tail or "")
        parts.append(_delimit(parts[-1], _styled(child), tail))
        if child.tag in SPACED_TAGS:
            parts.append(" ")
        if tail:
            parts.append(tail)
    return "".join(parts)


def _delimit(before: str, styled: str, after: str) -> str:
    """Use HTML for emphasis that Markdown would fail to recognise.

    CommonMark only treats ``*`` as emphasis when it is "flanked" correctly:
    ``*R-*value`` (punctuation then a letter) and ``*U**o*`` (two spans side
    by side) show literal asterisks. Those spans become ``<em>``/``<strong>``.
    """
    match = re.fullmatch(r"(\s?)(\*{1,3})(\S(?:.*\S)?)\2(\s?)", styled, re.S)
    if match is None:
        return styled
    lead, marker, core, trail = match.groups()
    previous = lead or before[-1:]
    following = trail or after[:1]
    opens = not _is_punctuation(core[0]) or _is_boundary(previous)
    closes = not _is_punctuation(core[-1]) or _is_boundary(following)
    if opens and closes and not before.endswith("*") and not after.startswith("*"):
        return styled
    start, end = HTML_EMPHASIS[marker]
    return f"{lead}{start}{core}{end}{trail}"


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char)[0] in "PS"  # As CommonMark defines it.


def _is_boundary(char: str) -> bool:
    return not char or char.isspace() or _is_punctuation(char)


def _styled(el: ET.Element) -> str:
    if el.tag == "br":
        return "<br>"
    if el.tag == "img":
        return _image(el)
    inner = _inline(el)
    code = el.get("T", "")
    if el.tag in WRAPPERS or code in E_WRAPPERS:
        start, end = WRAPPERS.get(el.tag) or E_WRAPPERS[code]
        return f"{start}{inner.strip()}{end}"
    marker = E_CODES.get(code, "*") if el.tag == "E" else EMPHASIS.get(el.tag)
    core = inner.strip()
    if not marker or not core:
        return inner
    if _has_emphasis(core):  # Nested emphasis: "**§ 110.79**c**:**" won't pair.
        start, end = HTML_EMPHASIS[marker]
        lead = " " if inner[0].isspace() else ""
        trail = " " if inner[-1].isspace() else ""
        return f"{lead}{start}{core}{end}{trail}"
    # Keep surrounding spaces outside the markers: "*a* b", not "*a *b".
    lead = " " if inner[0].isspace() else ""
    trail = " " if inner[-1].isspace() else ""
    return f"{lead}{marker}{core}{marker}{trail}"


def _strong(text: str) -> str:
    """Bold text, with HTML if it already contains emphasis."""
    return f"<strong>{text}</strong>" if _has_emphasis(text) else f"**{text}**"


def _has_emphasis(text: str) -> bool:
    return "*" in text.replace("\\*", "")


def _escape(text: str) -> str:
    """Escape characters that Markdown or HTML would otherwise interpret."""
    for char in MARKDOWN_SPECIAL:
        text = text.replace(char, f"\\{char}")
    return ENTITY.sub("&amp;", text)


def _clean(text: str) -> str:
    """Collapse whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def outer_divisions(el: ET.Element) -> list[ET.Element]:
    return [el] if is_division(el) else [d for c in el for d in outer_divisions(c)]


def slug(el: ET.Element) -> str:
    kind = el.get("TYPE", el.tag).upper()
    return make_slug(DIR_NAMES.get(kind, kind.lower()), el.get("N", ""))


def make_slug(kind: str, identifier: str) -> str:
    ident = re.sub(r"[^a-z0-9.]+", "-", identifier.lower()).strip("-.")
    return f"{kind}-{ident}" if ident else kind


def dedupe(name: str, taken: set[str]) -> str:
    """Suffix ``name`` if a sibling already uses it, and reserve the result."""
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}-{n}", n + 1
    taken.add(candidate)
    return candidate
