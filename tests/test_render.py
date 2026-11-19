"""The block model and every export writer."""

import datetime as dt
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import docx
import pytest
from pypdf import PdfReader

from ecfr_md import InvalidParameterError, MissingDependencyError, load
from ecfr_md.markdown import element_events, file_events, parse
from ecfr_md.output import Source
from ecfr_md.params import as_hierarchy
from ecfr_md.render import blocks as model
from ecfr_md.render import (
    docx_writer,
    epub_writer,
    html_writer,
    json_writer,
    pdf_writer,
    text_writer,
)
from ecfr_md.render.blocks import (
    Heading,
    Image,
    Item,
    Note,
    Paragraph,
    Table,
    event_blocks,
    from_markdown,
    node_blocks,
    to_markdown,
)
from ecfr_md.render.formats import as_format, format_for, require
from ecfr_md.render.inline import Span, plain, spans

DATE = dt.date(2026, 10, 7)
TITLE_XML = """<DIV1 N="45" TYPE="TITLE"><HEAD>Title 45—Public Welfare</HEAD>
<DIV2 N="A" TYPE="SUBTITLE"><HEAD>SUBTITLE A</HEAD>
<DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I</HEAD>
<DIV5 N="160" TYPE="PART"><HEAD>PART 160—GENERAL</HEAD>
<DIV7 N="ECFR1" TYPE="SUBJGRP"><HEAD>Definitions</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Basis.</HEAD><P>(a) Text.</P></DIV8>
</DIV7>
</DIV5>
<DIV9 N="Appendix A to Chapter I" TYPE="APPENDIX">
<HEAD>Appendix A to Chapter I—Forms</HEAD><P>Form.</P></DIV9>
</DIV3>
</DIV2>
</DIV1>"""


@pytest.fixture
def part(part_xml):
    return load(part_xml, title=45, date=DATE, hierarchy={"part": "164"})


@pytest.fixture
def title():
    return load(TITLE_XML, title=45, date=DATE)


# Formats ---------------------------------------------------------------------


def test_formats_by_name_and_suffix(tmp_path):
    assert as_format(" PDF ") == "pdf"
    with pytest.raises(InvalidParameterError, match="not an export format"):
        as_format("rtf")
    assert format_for("x.md", "html") == "html"
    assert format_for("x.pdf") == "pdf"
    assert format_for("x.HTM") == "html"
    assert format_for("x.txt") == "text"
    assert format_for("notes") == "markdown"
    assert format_for("x.pdf", tree=True) == "markdown"
    assert format_for("dir/") == "markdown"
    assert format_for(tmp_path) == "markdown"
    with pytest.raises(InvalidParameterError, match=r"from '\.rtf'"):
        format_for("x.rtf")


def test_missing_extra_says_how_to_install(monkeypatch):
    assert require("html") is None
    assert require("pdf") is not None
    monkeypatch.setitem(sys.modules, "typst", None)
    with pytest.raises(MissingDependencyError) as raised:
        require("pdf")
    message = str(raised.value)
    assert (
        'uv tool install "ecfr-md[pdf] @ git+https://github.com/ptmorris05/' in message
    )
    assert isinstance(raised.value, ImportError)


# Inline text -----------------------------------------------------------------


def test_inline_spans():
    found = spans(
        "**(a)** *Standard.* x<sup>1</sup><sub>2</sub><br>"
        "[link](http://a.b) ![fig](http://img) <em>e</em></em><Q> &amp;"
    )
    assert found[0] == Span("(a)", bold=True)
    assert Span("Standard.", italic=True) in found
    assert Span("1", sup=True) in found and Span("2", sub=True) in found
    assert Span("", kind="break") in found
    assert Span("link", href="http://a.b") in found
    assert Span("fig", href="http://img", kind="image") in found
    assert Span("e", italic=True) in found
    assert found[-1].text.endswith("&") and not found[-1].italic
    assert plain("a<br>b ![c](d)") == "a b c"
    assert plain("a\nb") == "a b"
    assert spans("") == []


# Blocks ----------------------------------------------------------------------


def test_blocks_of_a_part(part):
    found = list(node_blocks(part))
    headings = [b for b in found if isinstance(b, Heading)]
    assert [(h.level, h.target and h.target.anchor) for h in headings] == [
        (1, "part-164"),
        (2, "subpart-A"),
        (3, "164.102"),
        (3, "164.104"),
        (3, "164.106"),
        (3, "164.108"),
        (2, "appendix-A"),
    ]
    first = headings[0].target
    assert (
        first and first.provenance and first.provenance["citation"] == "45 CFR Part 164"
    )
    assert all(h.target and h.target.provenance is None for h in headings[1:])
    numbered = {
        b.anchors[0]: b for b in found if isinstance(b, Paragraph) and b.anchors
    }
    assert numbered["p-164.102(a)"].anchors == ("p-164.102(a)", "p-164.102(a)(1)")
    assert numbered["p-164.104(a)(2)(ii)(A)(1)"].depth == 5
    reserved = numbered["p-164.104(b)"]
    assert reserved.anchors[-1] == "p-164.104(g)" and reserved.depth == 1
    unnumbered = [b for b in found if isinstance(b, Paragraph) and not b.anchors]
    assert all(b.depth == 0 for b in unnumbered)
    assert any(isinstance(b, Note) for b in found)
    assert [b.text for b in found if isinstance(b, Item)] == ["Asbestos.", "Bauxite."]
    table = found[-1]
    assert isinstance(table, Table) and table.header == 1
    assert table.rows[2] == ("Access | Control", "164.312(a)(1)")


def test_blocks_of_a_paragraph(part):
    found = list(node_blocks(part["164.104(a)"]))
    assert found[0] == Heading(1, "45 CFR 164.104(a)", found[0].target)
    target = found[0].target
    assert target and target.anchor == "p-164.104(a)" and target.provenance
    paragraphs = [b for b in found if isinstance(b, Paragraph)]
    assert [(p.depth, p.anchors) for p in paragraphs[:2]] == [
        (1, ("p-164.104(a)",)),
        (2, ("p-164.104(a)(1)",)),
    ]


def test_blocks_of_a_title_and_streamed_xml(title, tmp_path):
    found = list(node_blocks(title))
    heading = found[0]
    assert isinstance(heading, Heading) and heading.target
    assert heading.target.anchor == "title-45"
    group = next(b for b in found if isinstance(b, Heading) and b.text == "Definitions")
    assert group.target and group.target.level == "subject_group"
    file = tmp_path / "t.xml"
    file.write_text(TITLE_XML, encoding="utf-8")
    with file.open("rb") as f:
        streamed = list(
            event_blocks(file_events(f), title.source, as_hierarchy({}), free=True)
        )
    assert streamed == found


def test_divisions_without_headings_and_wrappers():
    source = Source(45, DATE)
    xml = (
        "<ECFR><DIV5 N='1' TYPE='PART'><DIV6 N='A' TYPE='SUBPART'/>"
        "<DIV8 N='1.1' TYPE='SECTION'><P>(a) A.</P>"
        "<DIV9 N='Table 1' TYPE='APPENDIX'><HEAD>Table 1</HEAD><P>T.</P></DIV9>"
        "</DIV8></DIV5></ECFR>"
    )
    found = list(event_blocks(element_events(parse(xml)), source, as_hierarchy({})))
    assert [b.text for b in found if isinstance(b, Heading)] == [
        "1",
        "A",
        "1.1",
        "Table 1",
    ]
    assert [b.level for b in found if isinstance(b, Heading)] == [1, 2, 2, 3]


def test_from_markdown_reads_every_block():
    found = from_markdown(
        "## Group\n\n1. one\n2. two\n\n<div>raw</div>\n\n    code\n\n---\n\n"
        "![alt](http://x/y.png)\n\n> a\n>\n> > b\n\n|  |  |\n| --- | --- |\n| c | d |"
    )
    assert found[0] == Heading(2, "Group")
    assert found[1:3] == [Item("one"), Item("two")]
    assert found[3:5] == [Paragraph("<div>raw</div>"), Paragraph("code")]
    assert found[5] == Image("http://x/y.png", "alt")
    note = found[6]
    assert isinstance(note, Note) and isinstance(note.blocks[1], Note)
    table = found[7]
    assert isinstance(table, Table) and table.rows == (("c", "d"),) and not table.header
    assert [to_markdown(b) for b in found[:3]] == ["## Group", "- one", "- two"]
    assert to_markdown(found[5]) == "![alt](http://x/y.png)"
    assert to_markdown(note) == "> a\n>\n> > b"
    assert to_markdown(Heading(9, "Deep")) == "###### Deep"


# Writers ---------------------------------------------------------------------


def test_html_page(part):
    html = "".join(html_writer.page(node_blocks(part), up="index.html", links=".html"))
    assert html.startswith("<!DOCTYPE html>")
    assert "<title>45 CFR Part 164 — PART 164—SECURITY AND PRIVACY</title>" in html
    assert '<link rel="canonical" href="https://www.ecfr.gov/on/2026-10-07/' in html
    assert '<section id="part-164" class="part">\n<h1>' in html
    assert '<p id="p-164.104(a)(2)" class="d2"><strong>(2)</strong> Two.</p>' in html
    assert '<span id="p-164.102(a)(1)"></span>' in html
    assert 'id="p-164.108(1)~2"' in html  # Repeated markers stay unique.
    assert '<aside class="note">' in html and "prefers-color-scheme: dark" in html
    assert "<thead>\n<tr><th>Standards</th>" in html
    assert '<a class="up" href="index.html">Contents</a>' in html
    assert "eCFR text as of 2026-10-07" in html
    assert "Values below 5 &lt;Q&gt; and AT&amp;T&amp;copy;" in html
    root = ET.fromstring(html.split("\n", 1)[1])  # Well-formed, for EPUB.
    assert root.tag == "html"
    assert html.count("<section") == html.count("</section>")


def test_html_details():
    blocks = [
        Heading(8, "Deep [x](a/164.502.md#p-1) [y](http://e.gov/z.md)"),
        Paragraph("text<br>more", depth=9),
        Item("one"),
        Table((("h",), ("a", "b")), header=1),
        Table((("a",),)),
        Image("http://x/y.png", "fig"),
        Paragraph("![i](http://x/i.png)x"),
        Item("last"),
    ]
    html = "".join(html_writer.page(blocks, links=".html"))
    assert "<title>Deep x y</title>" in html
    assert '<p class="heading" role="heading" aria-level="8">' in html
    assert 'href="a/164.502.html#p-1"' in html and 'href="http://e.gov/z.md"' in html
    assert '<p class="d6">text<br />more</p>' in html
    assert "<th>h</th><th></th>" in html and "<tbody>\n<tr><td>a</td>" in html
    assert (
        '<figure><img src="http://x/y.png" alt="fig" loading="lazy" /></figure>' in html
    )
    assert html.endswith(
        "</ul>\n</section>\n</main>\n<footer><p>The eCFR is an "
        "editorial compilation of CFR material and Federal Register "
        "amendments. It is not an official legal edition of the CFR."
        "</p></footer>\n</body>\n</html>\n"
    )
    bare = "".join(html_writer.page([Paragraph("x")]))
    assert "<title>CFR</title>" in bare and 'class="source"' not in bare
    assert "<title>CFR</title>" in "".join(html_writer.page([]))
    titled = Heading(
        1,
        "§ 1.1 X",
        model.Target("section", "1.1", "§ 1.1 X", "1 CFR 1.1", "1/1.1", "u", "1.1"),
    )
    assert "<title>1 CFR 1.1 — § 1.1 X</title>" in "".join(html_writer.page([titled]))
    group = Heading(
        1, "A - B", model.Target("subject_group", "G", "B", "A - B", "", "", "")
    )
    assert "<title>A - B</title>" in "".join(html_writer.page([group]))


def test_text(part):
    text = "".join(text_writer.document(node_blocks(part)))
    assert text.startswith('---\ncitation: "45 CFR Part 164"\n')
    assert "\nPART 164—SECURITY AND PRIVACY\n=============================\n" in text
    assert "\nSubpart A—General Provisions\n----------------------------\n" in text
    assert "\n    (2) Two.\n" in text
    assert "\n                (1) Italic one.\n" in text
    assert "\n    Authority: 42 U.S.C. 1320d.\n" in text
    assert "Standards                 | Sections\n--------------------------+" in text
    assert "\n  - Asbestos.\n" in text
    bare = "".join(text_writer.document(node_blocks(part), front_matter=False))
    assert bare.startswith("PART 164")
    extra = [
        Heading(3, "Deep"),
        Note((Paragraph("a"), Image("http://x", "fig"))),
        Image("http://y"),
        Heading(1, ""),
    ]
    assert "".join(text_writer.document(extra)) == (
        "Deep\n\n    a\n\n    [Image: fig: http://x]\n\n[Image: http://y]\n\n\n"
    )
    assert text_writer.lines(Table(())) == []


def test_json_tree_round_trips(part):
    data = json.loads(json_writer.dumps(node_blocks(part)))
    expected = part.to_dict(text=False)

    def addresses(node, leaves):
        if node["level"] in leaves:
            return [node["address"]]
        return [
            node["address"],
            *(a for c in node["children"] for a in addresses(c, leaves)),
        ]

    assert addresses(data, ()) == addresses(expected, ("section", "appendix"))
    assert data["provenance"]["citation"] == "45 CFR Part 164"
    assert data["text"] == "> **Authority:** 42 U.S.C. 1320d."
    section = data["children"][0]["children"][1]
    assert section["citation"] == "45 CFR 164.104" and section["children"] == []
    assert section["text"].startswith("Introductory text belongs to no paragraph.\n\n")
    assert json_writer.tree([]) == {}
    loose = json_writer.tree([Paragraph("x"), *node_blocks(part), Heading(2, "Group")])
    assert loose["children"][-1]["text"].endswith("\n\n## Group")


def test_epub_structure(part):
    out = io.BytesIO()
    epub_writer.write(node_blocks(part), out)
    book = zipfile.ZipFile(out)
    first = book.infolist()[0]
    assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
    assert book.read("mimetype") == b"application/epub+zip"
    container = ET.fromstring(book.read("META-INF/container.xml"))
    rootfile = container.find(".//{*}rootfile")
    assert rootfile is not None and rootfile.get("full-path") == "OEBPS/content.opf"
    opf = ET.fromstring(book.read("OEBPS/content.opf"))
    assert opf.get("version") == "3.0"
    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    assert (
        opf.findtext(".//dc:identifier", namespaces=ns)
        == "urn:cfr:45/part-164@2026-10-07"
    )
    assert opf.find(".//opf:meta[@property='dcterms:modified']", ns) is not None
    items = {i.get("href"): i for i in opf.iterfind(".//opf:item", ns)}
    assert items["nav.xhtml"].get("properties") == "nav"
    spine = [r.get("idref") for r in opf.iterfind(".//opf:itemref", ns)]
    assert len(spine) == 7
    ids = {}
    for name in book.namelist():
        if name.endswith(".xhtml"):
            page = ET.fromstring(book.read(name))  # Well-formed XHTML.
            found = [e.get("id") for e in page.iter() if e.get("id")]
            assert all(re.fullmatch(r"[A-Za-z][\w.~-]*", i) for i in found)
            ids[name.removeprefix("OEBPS/")] = set(found)
    assert "p-164.102-a-1" in ids["page-00003.xhtml"]
    nav = ET.fromstring(book.read("OEBPS/nav.xhtml"))
    links = [a.get("href") or "" for a in nav.iter("{http://www.w3.org/1999/xhtml}a")]
    assert len(links) == 7
    for link in links:
        page, _, anchor = link.partition("#")
        assert anchor in ids[page]
    nested = nav.findall(".//{*}ol/{*}li/{*}ol/{*}li/{*}ol/{*}li")
    assert len(nested) == 4  # Sections in a subpart in the part.


def test_epub_navigation_with_uneven_levels():
    toc = [(2, "a", "A"), (4, "b", "B"), (3, "c", "C"), (1, "d", "D"), (2, "e", "")]
    nav = ET.fromstring(epub_writer._nav("T", toc))
    assert [a.text for a in nav.iter("{http://www.w3.org/1999/xhtml}a")] == [
        "A",
        "B",
        "C",
        "D",
        "e",
    ]
    out = io.BytesIO()
    epub_writer.write([Paragraph("loose"), Heading(1, "H")], out)
    assert "OEBPS/page-00002.xhtml" in zipfile.ZipFile(out).namelist()
    empty = io.BytesIO()
    epub_writer.write([], empty)
    assert zipfile.ZipFile(empty).read("OEBPS/content.opf")


def test_docx_reopens_with_headings(part, tmp_path):
    file = tmp_path / "part.docx"
    extra = [
        Heading(11, "Very deep"),
        Item("x"),
        Note((Image("http://x/y.png", "fig"),)),
        Table(()),
        Paragraph("![i](http://x/i.png) [l](http://e.gov)<br>x<sub>2</sub>"),
    ]
    with file.open("wb") as out:
        docx_writer.write([*node_blocks(part), *extra], out)
    doc = docx.Document(str(file))
    styles = [(p.style.name if p.style else "", p.text) for p in doc.paragraphs]
    assert styles[0] == ("Heading 1", "PART 164—SECURITY AND PRIVACY")
    assert ("Heading 2", "Subpart A—General Provisions") in styles
    assert ("Heading 3", "§ 164.102 Statutory basis.") in styles
    assert ("Heading 9", "Very deep") in styles
    assert ("CFR Note", "Authority: 42 U.S.C. 1320d.") in styles
    assert ("List Bullet", "Asbestos.") in styles
    assert ("CFR Note", "[Image: fig: http://x/y.png]") in styles
    assert doc.core_properties.subject == "45 CFR Part 164"
    assert doc.core_properties.identifier == "45/part-164"
    assert "45 CFR Part 164" in doc.sections[0].header.paragraphs[0].text
    two = next(p for p in doc.paragraphs if p.text == "(2) Two.")
    assert two.paragraph_format.left_indent == docx_writer.INDENT
    assert any(r.font.superscript for p in doc.paragraphs for r in p.runs)
    assert doc.paragraphs[-1].runs[-1].font.subscript
    assert len(doc.tables) == 2
    header = doc.tables[1].rows[0]
    assert (
        header.cells[0].text == "Standards"
        and header.cells[0].paragraphs[0].runs[0].bold
    )
    assert 'w:tblHeader w:val="true"' in header._tr.xml
    last = doc.paragraphs[-1]
    assert last.text.startswith("[Image: http://x/i.png] l")


def test_pdf_has_bookmarks_and_pages(part, tmp_path):
    file = tmp_path / "part.pdf"
    with file.open("wb") as out:
        pdf_writer.write(node_blocks(part), out)
    reader = PdfReader(file)
    assert len(reader.pages) >= 2
    assert reader.metadata and reader.metadata.title == "PART 164—SECURITY AND PRIVACY"
    outline = reader.outline
    assert [getattr(o, "title", None) for o in outline] == [
        "PART 164—SECURITY AND PRIVACY",
        None,
    ]
    assert [getattr(o, "title", None) for o in outline[1]] == [
        "Subpart A—General Provisions",
        None,
        "Appendix A to Subpart C of Part 164—Matrix",
    ]
    assert len(outline[1][1]) == 4  # The sections in subpart A.
    text = reader.pages[0].extract_text()
    assert "45 CFR Part 164" in text and "Statutory basis." in text


def test_pdf_source_details():
    blocks = [
        Paragraph("loose"),
        Item("*it*"),
        Image("http://x/y.png", "fig"),
        Image("http://x/z.png"),
        Table((("a", "b"), ("c",)), header=1),
        Table(()),
        Paragraph("[l](http://e.gov) ![i](http://x/i.png)<br>x"),
    ]
    source = pdf_writer.source(blocks)
    assert source.startswith('#set document(title: "CFR"')
    assert "- #emph[it]" in source
    assert '#link("http://x/y.png")[\\[Image\\: fig\\]]' in source
    assert (
        "table.header([#strong[a]], [#strong[b]])," in source and "[c], []," in source
    )
    assert (
        '#link("http://e.gov")[l] #link("http://x/i.png")[\\[Image\\]]#linebreak()x'
        in source
    )
    assert pdf_writer.escape('#a*b "c"') == '\\#a\\*b \\"c\\"'
    out = io.BytesIO()
    pdf_writer.write(blocks, out)
    assert out.getvalue().startswith(b"%PDF")


def test_render_package_exports():
    from ecfr_md import render

    assert render.FORMATS[0] == "markdown" and render.format_for("a.json") == "json"
    assert Path(model.__file__).name == "blocks.py"
