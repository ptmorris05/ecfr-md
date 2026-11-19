"""Word documents, with python-docx (the ``docx`` extra).

Divisions get Word's built-in heading styles, so the navigation pane and a
table of contents show the outline. Numbered paragraphs are indented by
depth; notes are small and grey; the header names the document and the
footer numbers the pages.
"""

from collections.abc import Iterable
from typing import IO, Any

from docx import Document
from docx.document import Document as WordDocument
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.table import _Cell
from docx.text.paragraph import Paragraph as WordParagraph

from ecfr_md.config import DISCLAIMER
from ecfr_md.render.blocks import Block, Heading, Image, Item, Note, Paragraph, Table
from ecfr_md.render.inline import plain, spans

MAX_HEADING = 9  # Word's built-in heading styles.
INDENT = Pt(18)
TEXT = RGBColor(0x1F, 0x23, 0x28)
MUTED = RGBColor(0x5D, 0x64, 0x70)
NOTE_STYLE = "CFR Note"


def write(blocks: Iterable[Block], out: IO[bytes]) -> None:
    """Write a Word document of the blocks to a binary file."""
    doc = Document()
    _styles(doc)
    meta: dict[str, Any] = {}
    for block in blocks:
        if not meta and isinstance(block, Heading) and block.target:
            meta = block.target.provenance or {}
            _properties(doc, block, meta)
        _add(doc, block)
    doc.save(out)


def _styles(doc: WordDocument) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Georgia"  # type: ignore[attr-defined]
    normal.font.size = Pt(10.5)  # type: ignore[attr-defined]
    for level in range(1, MAX_HEADING + 1):
        doc.styles[f"Heading {level}"].font.color.rgb = TEXT  # type: ignore[attr-defined]
    note = doc.styles.add_style(NOTE_STYLE, WD_STYLE_TYPE.PARAGRAPH)
    note.base_style = normal  # type: ignore[attr-defined]
    note.font.size = Pt(9)  # type: ignore[attr-defined]
    note.font.color.rgb = MUTED  # type: ignore[attr-defined]
    note.paragraph_format.left_indent = INDENT  # type: ignore[attr-defined]


def _properties(doc: WordDocument, heading: Heading, meta: dict[str, Any]) -> None:
    """Document properties, and a header and footer on every page."""
    props = doc.core_properties
    props.title = plain(heading.text)[:255]
    props.subject = meta.get("citation", "")
    props.identifier = meta.get("address", "")
    props.keywords = "CFR; eCFR"
    props.comments = f"{meta.get('source', '')} {DISCLAIMER}".strip()
    props.author = "ecfr-md"
    section = doc.sections[0]
    date = f" · eCFR text as of {meta['date']}" if meta.get("date") else ""
    header = section.header.paragraphs[0]
    header.text = f"{meta.get('citation', '')}{date}"
    header.runs[0].font.size = Pt(8)
    header.runs[0].font.color.rgb = MUTED
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)


def _add(doc: WordDocument, block: Block, style: str | None = None) -> None:
    match block:
        case Heading(level, text):
            _runs(doc.add_heading("", min(level, MAX_HEADING)), text)
        case Paragraph(text, depth):
            paragraph = doc.add_paragraph(style=style)
            if depth > 1:
                paragraph.paragraph_format.left_indent = INDENT * (depth - 1)
            _runs(paragraph, text)
        case Item(text):
            _runs(doc.add_paragraph(style="List Bullet"), text)
        case Note(inner):
            for b in inner:
                _add(doc, b, NOTE_STYLE)
        case Table():
            _table(doc, block)
        case Image(src, alt):
            _runs(
                doc.add_paragraph(style=style),
                f"[Image{': ' + alt if alt else ''}: {src}]",
            )


def _runs(paragraph: WordParagraph, markdown: str) -> None:
    """Add inline Markdown to a paragraph as formatted runs."""
    for span in spans(markdown):
        if span.kind == "break":
            paragraph.add_run().add_break()
            continue
        text = f"[Image: {span.href}]" if span.kind == "image" else span.text
        run = paragraph.add_run(text)
        run.bold = span.bold or None
        run.italic = span.italic or None
        if span.sup:  # One property: setting either clears the other.
            run.font.superscript = True
        elif span.sub:
            run.font.subscript = True
        if span.href is not None and span.kind == "text":
            run.underline = True


def _table(doc: WordDocument, table: Table) -> None:
    width = max((len(r) for r in table.rows), default=0)
    if not width:
        return
    grid = doc.add_table(rows=len(table.rows), cols=width)
    grid.style = "Table Grid"
    for i, (row, cells) in enumerate(zip(grid.rows, table.rows, strict=True)):
        if i < table.header:
            row_props = row._tr.get_or_add_trPr()
            repeat = OxmlElement("w:tblHeader")
            repeat.set(qn("w:val"), "true")
            row_props.append(repeat)  # Repeated on each page.
        for cell, text in zip(row.cells, cells, strict=False):
            _cell(cell, text, bold=i < table.header)


def _cell(cell: _Cell, text: str, *, bold: bool) -> None:
    paragraph = cell.paragraphs[0]
    _runs(paragraph, text)
    for run in paragraph.runs:
        run.font.size = Pt(9)
        if bold:
            run.bold = True
