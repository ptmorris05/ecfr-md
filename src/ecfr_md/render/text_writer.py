"""Plain text: headings, then one line per paragraph, indented by its depth.

Lines aren't wrapped, so each paragraph stays on one line for searching.
Tables are laid out in columns; notes are indented.
"""

from collections.abc import Iterable, Iterator

from ecfr_md.output import render_front_matter
from ecfr_md.render.blocks import Block, Heading, Image, Item, Note, Paragraph, Table
from ecfr_md.render.inline import plain

INDENT = "    "
UNDERLINES = {1: "=", 2: "-"}


def document(blocks: Iterable[Block], *, front_matter: bool = True) -> Iterator[str]:
    """Plain text, in pieces as the blocks arrive; with ``front_matter``, it
    starts with the same metadata as a Markdown file."""
    first = True
    for block in blocks:
        heading = block.target if isinstance(block, Heading) else None
        if first and front_matter and heading and heading.provenance:
            yield render_front_matter(heading.provenance)
        if not first:
            yield "\n"
        first = False
        yield "\n".join(lines(block)) + "\n"


def lines(block: Block, indent: str = "") -> list[str]:
    """A block's lines of text."""
    match block:
        case Heading(level, text):
            heading = plain(text)
            mark = UNDERLINES.get(level)
            return [heading, mark * len(heading)] if mark and heading else [heading]
        case Paragraph(text, depth):
            return [indent + INDENT * max(depth - 1, 0) + plain(text)]
        case Item(text):
            return [f"{indent}  - {plain(text)}"]
        case Note(inner):
            out: list[str] = []
            for i, b in enumerate(inner):
                out += ([""] if i else []) + lines(b, indent + INDENT)
            return out
        case Table():
            return [indent + line for line in _table(block)]
        case Image(src, alt):
            return [f"{indent}[Image{': ' + alt if alt else ''}: {src}]"]


def _table(table: Table) -> list[str]:
    rows = [[plain(c) for c in row] for row in table.rows]
    width = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (width - len(r)) for r in rows]
    sizes = [max(len(r[i]) for r in rows) for i in range(width)]
    out = [
        " | ".join(c.ljust(s) for c, s in zip(r, sizes, strict=True)).rstrip()
        for r in rows
    ]
    if table.header:
        out.insert(table.header, "-+-".join("-" * s for s in sizes))
    return out
