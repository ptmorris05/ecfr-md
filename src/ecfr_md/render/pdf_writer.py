"""PDF, typeset by Typst (the ``pdf`` extra), which needs no system libraries.

Every heading is a PDF bookmark, nested by level. Pages have a running head
(the document's citation and the current heading) and page numbers. Fonts
are the ones built into Typst, so output is the same on every system.
"""

import string
from collections.abc import Iterable
from typing import IO, Any

import typst

from ecfr_md.config import DISCLAIMER
from ecfr_md.render.blocks import Block, Heading, Image, Item, Note, Paragraph, Table
from ecfr_md.render.inline import Span, plain, spans

# Every ASCII punctuation character is escaped, so text is never markup.
ESCAPES = str.maketrans({c: "\\" + c for c in string.punctuation})
INDENT_EM = 1.5

PREAMBLE = """\
#set document(title: {title}, author: "ecfr-md", keywords: ("CFR", "eCFR"))
#set text(font: "Libertinus Serif", size: 10.5pt, lang: "en", hyphenate: true)
#set par(justify: true, leading: 0.62em, spacing: 0.95em)
#set page(
  paper: "us-letter",
  margin: (x: 1in, top: 1in, bottom: 0.9in),
  numbering: "1",
  header: context {{
    let page = here().page()
    let here-on = query(heading).filter(h => h.location().page() == page)
    let before = query(selector(heading).before(here()))
    let current = if here-on.len() > 0 {{ here-on.first() }} else if before.len() > 0 {{
      before.last()
    }}
    set text(size: 8pt, fill: luma(90))
    grid(columns: (auto, 1fr), column-gutter: 1.5em, align: (left, right),
      [{citation}], if current != none {{ current.body }})
    v(-0.35em)
    line(length: 100%, stroke: 0.3pt + luma(170))
  }},
)
#set heading(numbering: none)
#show heading: set block(above: 1.5em, below: 0.75em, sticky: true)
#show heading: set par(justify: false)
#show heading.where(level: 1): set text(size: 16pt)
#show heading.where(level: 2): set text(size: 13.5pt)
#show heading.where(level: 3): set text(size: 12pt)
#show heading: set text(size: 11pt)
#show table: set text(size: 8.5pt)
#show table: set par(justify: false)
#show link: underline
"""


def source(blocks: Iterable[Block]) -> str:
    """The Typst markup for a document."""
    out: list[str] = []
    meta: dict[str, Any] | None = None
    for block in blocks:
        if meta is None and isinstance(block, Heading):
            meta = (block.target.provenance if block.target else None) or {}
            title = plain(block.text)
            citation = meta.get("citation", "")
            out.append(PREAMBLE.format(title=_string(title), citation=escape(citation)))
            out.append(_block(block))
            out.append(_about(meta))
            continue
        out.append(_block(block))
    if meta is None:
        out.insert(0, PREAMBLE.format(title='"CFR"', citation=""))
    return "\n".join(out) + "\n"


def write(blocks: Iterable[Block], out: IO[bytes]) -> None:
    """Typeset the blocks and write the PDF to a binary file."""
    pdf = typst.compile(
        source(blocks).encode("utf-8"), format="pdf", ignore_system_fonts=True
    )
    assert isinstance(pdf, bytes)
    out.write(pdf)


def escape(text: str) -> str:
    """Plain text as Typst markup."""
    return text.translate(ESCAPES)


def _string(text: str) -> str:
    """Text as a Typst string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _about(meta: dict[str, Any]) -> str:
    """Where the text is from, under the first heading."""
    parts = []
    if date := meta.get("date"):
        parts.append(f"eCFR text as of {escape(date)}.")
    if url := meta.get("source"):
        parts.append(f"#link({_string(url)})[{escape(url)}]")
    parts.append(escape(meta.get("disclaimer", DISCLAIMER)))
    style = "#set text(size: 8.5pt, fill: luma(80))"
    return f"#block(below: 1.5em)[{style}\n{' '.join(parts)}]\n"


def _block(block: Block) -> str:
    match block:
        case Heading(level, text):
            return f"{'=' * level} {inline(text)}\n"
        case Paragraph(text, depth):
            body = inline(text)
            if depth > 1:
                return f"#pad(left: {INDENT_EM * (depth - 1)}em)[{body}]\n"
            return f"{body}\n"
        case Item(text):
            return f"- {inline(text)}\n"
        case Note(inner):
            body = "\n".join(_block(b) for b in inner)
            return (
                "#block(inset: (left: 0.9em), stroke: (left: 0.6pt + luma(170)))"
                f"[#set text(size: 9pt, fill: luma(70))\n{body}]\n"
            )
        case Table():
            return _table(block)
        case Image(src, alt):
            label = escape(f"Image{': ' + alt if alt else ''}")
            return f"#link({_string(src)})[\\[{label}\\]]\n"


def _table(table: Table) -> str:
    width = max((len(r) for r in table.rows), default=0)
    if not width:
        return ""

    def row(cells: tuple[str, ...], style: str = "{}") -> str:
        padded = [*cells, *[""] * (width - len(cells))]
        return ", ".join("[" + style.format(inline(c)) + "]" for c in padded)

    rows = [
        row(r, "#strong[{}]" if i < table.header else "{}")
        for i, r in enumerate(table.rows)
    ]
    head = rows[: table.header]
    lines = [f"  table.header({', '.join(head)})," if head else ""]
    lines += [f"  {r}," for r in rows[table.header :]]
    return (
        f"#table(columns: (1fr,) * {width}, inset: 4pt, stroke: 0.4pt + luma(160),\n"
        + "\n".join(line for line in lines if line)
        + "\n)\n"
    )


def inline(markdown: str) -> str:
    """Inline Markdown as Typst markup."""
    return "".join(_span(s) for s in spans(markdown))


def _span(span: Span) -> str:
    if span.kind == "break":
        return "#linebreak()"
    if span.kind == "image":
        return f"#link({_string(span.href or '')})[\\[Image\\]]"
    markup = escape(span.text)
    for on, func in (
        (span.sub, "sub"),
        (span.sup, "super"),
        (span.italic, "emph"),
        (span.bold, "strong"),
    ):
        if on:
            markup = f"#{func}[{markup}]"
    if span.href is not None:
        markup = f"#link({_string(span.href)})[{markup}]"
    return markup
