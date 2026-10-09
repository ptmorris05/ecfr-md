"""Convert eCFR XML into Markdown.

Headings follow nesting depth relative to the requested node, so a whole
title starts with ``# Title``, while a single section starts with ``# §``.
Markdown has six heading levels; deeper nodes render as bold lines.
"""

import re
import xml.etree.ElementTree as ET
from enum import StrEnum
from pathlib import Path, PurePosixPath

from cfr_md.config import BASE_URL
from cfr_md.errors import ResponseFormatError


class Layout(StrEnum):
    FILE = "file"  # One Markdown document; hierarchy encoded as headings.
    TREE = "tree"  # Nested directories; one file per section or appendix.


MAX_HEADING_LEVEL = 6
INDEX_FILE = "index.md"
LEAF_TYPES = frozenset({"SECTION", "APPENDIX"})
DIR_NAMES = {"SUBCHAP": "subchapter", "SUBJGRP": "subject-group"}

PARAGRAPH_TAGS = frozenset({"P", "FP"})
SUBHEADING_TAGS = frozenset({"HD", "HD1", "HD2", "HD3", "HD4", "CHAPNO"})
NOTE_TAGS = frozenset(
    {"AUTH", "SOURCE", "CITA", "APPRO", "SECAUTH", "PARAUTH", "EDNOTE", "NOTE"}
    | {"EFFDNOT", "CROSSREF", "FTNT", "XREF", "EXTRACT"}
)
SKIP_TAGS = frozenset({"AMDDATE", "VOLUME", "PRTPAGE", "CAPTION"})
INLINE_TAGS = frozenset(
    {"I", "B", "E", "SU", "FR", "FTREF", "HED", "PSPACE"}
    | {"sup", "sub", "br", "img", "strong", "em", "span"}
)

# GPO emphasis codes: <E T="01"> bold, "03" bold italic, "51"/"52" sup/sub.
EMPHASIS = {"I": "*", "B": "**", "strong": "**", "em": "*", "HED": "**"}
E_CODES = {"01": "**", "02": "*", "03": "***", "04": "*"}
WRAPPERS = {
    "SU": ("<sup>", "</sup>"),
    "sup": ("<sup>", "</sup>"),
    "sub": ("<sub>", "</sub>"),
}
E_WRAPPERS = {"51": WRAPPERS["sup"], "52": WRAPPERS["sub"]}

SPACED_TAGS = frozenset({"HED", "PSPACE"})  # Label and text run together.
PARAGRAPH_MARKER = re.compile(r"^((?:\([A-Za-z0-9]{1,6}\))+)")
DIV_TAG = re.compile(r"DIV\d+$")
MARKDOWN_SPECIAL = "\\`*_"


def to_markdown(xml: bytes | str) -> str:
    """Render eCFR XML as one Markdown document."""
    return _join(_blocks(_parse(xml), level=1))


def to_markdown_tree(xml: bytes | str) -> dict[PurePosixPath, str]:
    """Render eCFR XML as Markdown files keyed by relative path.

    Each hierarchy level becomes a directory with an ``index.md`` holding its
    heading, notes and links to its children; sections and appendices become
    individual files.
    """
    files: dict[PurePosixPath, str] = {}
    taken: set[str] = set()
    for div in _outer_divs(_parse(xml)):
        _tree(div, PurePosixPath(_dedupe(_slug(div), taken)), files)
    return files


def write_markdown(xml: bytes | str, path: Path, layout: Layout) -> list[Path]:
    """Write rendered Markdown to ``path`` and return the files written."""
    if Layout(layout) is Layout.FILE:
        documents = {PurePosixPath(): to_markdown(xml)}
    else:
        documents = to_markdown_tree(xml)
    written = []
    for relative, text in documents.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def _parse(xml: bytes | str) -> ET.Element:
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ResponseFormatError(f"Invalid eCFR XML: {exc}") from exc


def _join(blocks: list[str]) -> str:
    return "\n\n".join(blocks) + "\n"


def _is_div(el: ET.Element) -> bool:
    return bool(DIV_TAG.match(el.tag))


def _heading_text(el: ET.Element) -> str:
    head = el.find("HEAD")
    return _clean(_inline(head)) if head is not None else el.get("N", "")


def _heading(text: str, level: int) -> str:
    if level > MAX_HEADING_LEVEL:
        return f"**{text}**"
    return f"{'#' * level} {text}"


def _blocks(el: ET.Element, level: int) -> list[str]:
    """Markdown blocks for an element; ``level`` is the next heading level."""
    tag = el.tag
    if _is_div(el):
        children = [c for c in el if c.tag != "HEAD"]
        out = [_heading(_heading_text(el), level)]
        return out + [b for c in children for b in _blocks(c, level + 1)]
    if tag in SKIP_TAGS:
        return []
    if tag in PARAGRAPH_TAGS:
        return [_paragraph(el)]
    if tag in SUBHEADING_TAGS:
        return [f"**{_clean(_inline(el))}**"]
    if tag in NOTE_TAGS:
        return [_quote(el, level)]
    if tag == "TABLE":
        return [_table(el)]
    return _content(el, level)


def _content(el: ET.Element, level: int) -> list[str]:
    """Blocks for a container, or one inline paragraph if it holds only text."""
    if not any(d.tag not in INLINE_TAGS for d in el.iter() if d is not el):
        text = _clean(_styled(el))
        return [text] if text else []
    text = _clean(_escape(el.text or ""))
    return ([text] if text else []) + [b for c in el for b in _blocks(c, level)]


def _paragraph(el: ET.Element) -> str:
    """Bold the leading paragraph designation, e.g. (a)(1) or (ii)."""
    return PARAGRAPH_MARKER.sub(r"**\1**", _clean(_inline(el)))


def _quote(el: ET.Element, level: int) -> str:
    body = "\n\n".join(_content(el, level))
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())


def _table(el: ET.Element) -> str:
    rows = [_row(tr) for tr in el.iter("TR")]
    width = max((len(r) for r in rows), default=1)
    head_rows = sum(1 for _ in el.iterfind("THEAD/TR"))
    header = rows[0] if head_rows else [""] * width
    body = rows[1:] if head_rows else rows
    lines = [_table_line(header, width), "|" + " --- |" * width]
    lines += [_table_line(r, width) for r in body]
    caption = el.find("CAPTION")
    if caption is not None:
        lines.insert(0, f"**{_clean(_inline(caption))}**\n")
    return "\n".join(lines)


def _row(tr: ET.Element) -> list[str]:
    cells = []
    for cell in tr:
        text = _clean(_inline(cell)).replace("|", "\\|")
        cells += [text] + [""] * (int(cell.get("colspan", "1")) - 1)
    return cells


def _table_line(cells: list[str], width: int) -> str:
    return "| " + " | ".join((cells + [""] * width)[:width]) + " |"


def _image(el: ET.Element) -> str:
    src = el.get("src", "")
    return f"![]({BASE_URL}{src})" if src.startswith("/") else f"![]({src})"


def _inline(el: ET.Element) -> str:
    """Element text with emphasis, superscripts and line breaks as Markdown."""
    parts = [_escape(el.text or "")]
    for child in el:
        parts.append(_styled(child))
        if child.tag in SPACED_TAGS:
            parts.append(" ")
        parts.append(_escape(child.tail or ""))
    return "".join(parts)


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
    # Keep surrounding spaces outside the markers: "*a* b", not "*a *b".
    lead = " " if inner[0].isspace() else ""
    trail = " " if inner[-1].isspace() else ""
    return f"{lead}{marker}{core}{marker}{trail}"


def _escape(text: str) -> str:
    """Escape characters that Markdown would otherwise treat as formatting."""
    for char in MARKDOWN_SPECIAL:  # Backslash first, so escapes aren't doubled.
        text = text.replace(char, f"\\{char}")
    return text


def _clean(text: str) -> str:
    """Collapse whitespace (including newlines) to single spaces."""
    return " ".join(text.split())


def _outer_divs(el: ET.Element) -> list[ET.Element]:
    return [el] if _is_div(el) else [d for c in el for d in _outer_divs(c)]


def _tree(
    el: ET.Element, path: PurePosixPath, files: dict[PurePosixPath, str]
) -> PurePosixPath:
    """Add the files for hierarchy node ``el`` at ``path``; return its main file."""
    if el.get("TYPE") in LEAF_TYPES:
        leaf = path.with_name(f"{path.name}.md")
        files[leaf] = _join(_blocks(el, level=1))
        return leaf
    index = path / INDEX_FILE
    files[index] = ""  # Reserve the slot so the index precedes its children.
    blocks, contents, taken = [_heading(_heading_text(el), 1)], [], set()
    for child in el:
        if _is_div(child):
            main = _tree(child, path / _dedupe(_slug(child), taken), files)
            link = main.relative_to(path).as_posix()
            contents.append(f"- [{_heading_text(child)}]({link})")
        elif child.tag != "HEAD":
            blocks += _blocks(child, level=2)
    files[index] = _join(blocks + (["\n".join(contents)] if contents else []))
    return index


def _slug(el: ET.Element) -> str:
    kind = el.get("TYPE", el.tag).upper()
    kind = DIR_NAMES.get(kind, kind.lower())
    ident = re.sub(r"[^a-z0-9.]+", "-", el.get("N", "").lower()).strip("-.")
    return f"{kind}-{ident}" if ident else kind


def _dedupe(name: str, taken: set[str]) -> str:
    """Suffix ``name`` if a sibling already uses it, and reserve the result."""
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}-{n}", n + 1
    taken.add(candidate)
    return candidate
