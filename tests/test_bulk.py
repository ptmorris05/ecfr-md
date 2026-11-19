"""GovInfo bulk XML rewritten in the eCFR API's dialect."""

import io
import json
import xml.etree.ElementTree as ET

import pytest

from ecfr_md import markdown
from ecfr_md.bulk import BulkFormatError, normalize
from ecfr_md.models import Structure


def node(kind: str, n: str | None, label: str, *children: dict, **extra) -> dict:
    return {"identifier": n, "label": label, "type": kind, "children": list(children)} | extra  # fmt: skip  # noqa: E501


STRUCTURE = node(
    "title", "99", "Title 99—Examples",
    node(
        "subtitle", "A", "Subtitle A—Alpha",
        node(
            "subchapter", "A", "Subchapter A—General",
            node("part", "1", "Part 1 [Reserved]", reserved=True),
            node(
                "part", "2", "Part 2—Rules",
                node("section", "2.1", "§ 2.1 First."),
                node("section", "2.2", "§ 2.2 Second."),
                node(
                    "subpart", "B", "Subpart B",
                    node("section", "2.10", "§ 2.10 Tenth."),
                ),
                node("appendix", "Appendix A to Part 2", "Appendix A to Part 2—A"),
            ),
        ),
        node("subchapter", "B", "Subchapter B [Reserved]", reserved=True),
        node(
            "subchapter", "C", "Subchapter C—Continued",
            node(
                "part", "3", "Part 3—Long",
                node("section", "3.1", "§ 3.1 One."),
                node("section", "3.2", "§ 3.2 Two."),
            ),
        ),
    ),
    node(
        "subtitle", "B", "Subtitle B—Beta",
        node(
            "chapter", "II", "Chapter II—Agency",
            node("appendix", "Appendix to Chapter II", "Appendix to Chapter II"),
            node(
                "part", "4", "Part 4—Tables",
                node("section", "4.1", "§ 4.1 Table."),
                node("section", "4.2-4.3", "§§ 4.2-4.3 [Reserved]"),
                node("appendix", "Appendix A to Part 4", "Appendix A to Part 4—Notes"),
            ),
        ),
    ),
)  # fmt: skip

BULK = """<?xml version="1.0" encoding="UTF-8" ?>
<DLPSTEXTCLASS>
<HEADER><FILEDESC><TITLESTMT><TITLE>Title 99</TITLE></TITLESTMT></FILEDESC>
</HEADER>
<TEXT><BODY><ECFRBRWS>
<AMDDATE>Nov. 3, 2026</AMDDATE>
<DIV1 N="1" NODE="99:1" TYPE="TITLE">
<HEAD>Title 99—Examples--Volume 1</HEAD>
<CFRTOC><PTHD>Part</PTHD>
<CHAPTI><SUBJECT>SUBTITLE A</SUBJECT><PG>1</PG></CHAPTI></CFRTOC>
<DIV3 N="A" NODE="99:1.0.1" TYPE="CHAPTER">
<HEAD> SUBTITLE A—Alpha</HEAD>
<DIV4 N="A" NODE="99:1.0.1.1" TYPE="SUBCHAP">
<HEAD>SUBCHAPTER A—GENERAL</HEAD>
<P>Subchapter note.</P>
<DIV5 N="2" NODE="99:1.0.1.1.2" TYPE="PART">
<HEAD>PART 2—RULES</HEAD>
<DIV8 N="§ 2.1" NODE="99:1.0.1.1.2.0.1.1" TYPE="SECTION">
<HEAD>§ 2.1   First.</HEAD>
<XREF ID="1" REFID="1">Link to an amendment published at 91 FR 1.</XREF>
<XREF ID="1" REFID="2">Link to an amendment published at 91 FR 1.</XREF>
<P>(a) One.</P>
</DIV8>
<DIV8 N="Sec. 2.2" NODE="99:x" TYPE="SECTION">
<HEAD>§ 2.2   Second.</HEAD>
<P>Two.</P>
</DIV8>
<DIV6 N="0" NODE="99:y" TYPE="SUBPART">
<HEAD> </HEAD>
</DIV6>
<DIV6 N="B" NODE="99:z" TYPE="SUBPART">
<HEAD>Subpart B</HEAD>
<DIV8 N="§ 2.10" NODE="99:w" TYPE="SECTION">
<HEAD>§ 2.10   Tenth.</HEAD>
<P>Ten.</P>
</DIV8>
<DIV9 N="Appendix A" NODE="99:v" TYPE="APPENDIX">
<HEAD>Appendix A to Part 2—Forms</HEAD>
<P>A form.</P>
</DIV9>
</DIV6>
<P>Run-out text of the appendix.</P>
</DIV5>
<P>After the parts.</P>
</DIV4>
<DIV4 N="B [RESERVED]  " NODE="99:1.0.1.2" TYPE="SUBCHAP">
<HEAD>SUBCHAPTER B [RESERVED]</HEAD>
</DIV4>
<DIV4 N="C" NODE="99:1.0.1.3" TYPE="SUBCHAP">
<HEAD>SUBCHAPTER C—CONTINUED</HEAD>
<DIV5 N="3" NODE="99:1.0.1.3.3" TYPE="PART">
<HEAD>PART 3—LONG</HEAD>
<DIV8 N="§ 3.1" NODE="99:u" TYPE="SECTION">
<HEAD>§ 3.1   One.</HEAD>
<P>One.</P>
</DIV8>
</DIV5>
</DIV4>
</DIV3>
</DIV1>
<DIV1 N="2" NODE="99:2" TYPE="TITLE">
<HEAD>Title 99—Examples--Volume 2</HEAD>
<CFRTOC><PTHD>Part</PTHD></CFRTOC>
<DIV3 N="A" NODE="99:2.0.1" TYPE="CHAPTER">
<HEAD> SUBTITLE A—Alpha (Continued)</HEAD>
<DIV4 N="C" NODE="99:2.0.1.3" TYPE="SUBCHAP">
<HEAD>SUBCHAPTER C—CONTINUED (Continued)</HEAD>
<DIV5 N="3" NODE="99:2.0.1.3.3" TYPE="PART">
<HEAD>PART 3—LONG (CONTINUED)</HEAD>
<DIV8 N="§ 3.2" NODE="99:t" TYPE="SECTION">
<HEAD>§ 3.2   Two.</HEAD>
<P>Two.</P>
</DIV8>
</DIV5>
</DIV4>
</DIV3>
<DIV2 N="Subtitle B" NODE="99:2.1" TYPE="SUBTITLE">
<HEAD>Subtitle B—Beta</HEAD>
<DIV3 N="II" NODE="99:2.1.2" TYPE="CHAPTER">
<HEAD>CHAPTER II—AGENCY</HEAD>
<DIV9 N="" NODE="99:s" TYPE="APPENDIX">
<HEAD>Appendix to Chapter II</HEAD>
<P>Chapter appendix.</P>
</DIV9>
<DIV5 N="4" NODE="99:2.1.2.4" TYPE="PART">
<HEAD>PART 4—TABLES</HEAD>
<DIV8 N="§ 4.1" NODE="99:r" TYPE="SECTION">
<HEAD>§ 4.1   Table.</HEAD>
<DIV width="100%"><DIV class="table_head"><P class="gpotbl_title">Table 1 to § 4.1
</P></DIV><DIV class="gpotbl_div"><TABLE class="gpotbl_table"><TR><TH>Item
<br />name
</TH><TH>Value
</TH></TR>
<TR><TD colspan="2"><E T="02">Group</E> one</TD></TR>
<TR><TD>UO<E T="52">2</E>F</TD><TD><fr>1/2</fr> cup<E T="0112">%</E></TD></TR>
<TR><TD>0.75 <E T="03">F</E><E T="54">TOW</E></TD><TD>X<AC T="8" />=</TD></TR>
<TR><TD><E T="04">Bold</E> <fr>x/y</fr></TD><TD>C<E T="0732">1</E><E T="0731">2</E></TD>
</TR><TR><TD><E T="0112">%<I>i</I></E> after</TD><TD>z</TD></TR></TABLE></DIV>
<DIV class="table_foot"><P class="gpotbl_note">
<sup>1</sup> A note.</P></DIV></DIV>
</DIV8>
<DIV8 N="§§ 4.2—4.3" NODE="99:q" TYPE="SECTION">
<HEAD>§§ 4.2--4.3   [Reserved]</HEAD>
</DIV8>
<DIV8 N="§ 4.99" NODE="99:p" TYPE="SECTION">
<HEAD>§ 4.99   Not in the structure.</HEAD>
</DIV8>
<DIV9 N="Appendix A" NODE="99:o" TYPE="APPENDIX">
<HEAD>Appendix A to Part 4—Notes</HEAD>
<DIV width="100%"><DIV class="gpotbl_div"><TABLE><TR><TD>a</TD></TR></TABLE></DIV></DIV>
</DIV9>
<DIV9 N="Appendix Z" NODE="99:n" TYPE="APPENDIX">
<HEAD>Appendix Z to Part 4—Unlisted</HEAD>
</DIV9>
<HD1>A heading after the appendices</HD1>
</DIV5>
</DIV3>
</DIV2>
</DIV1>
</ECFRBRWS></BODY></TEXT>
</DLPSTEXTCLASS>
"""


def normalized(bulk: str = BULK, structure: dict = STRUCTURE) -> ET.Element:
    out = io.BytesIO()
    normalize(
        io.BytesIO(bulk.encode()), out, Structure.parse(json.dumps(structure).encode())
    )
    return ET.fromstring(out.getvalue())


def outline(root: ET.Element) -> list[tuple[str, str | None, str | None]]:
    return [
        (e.tag, e.get("TYPE"), e.get("N"))
        for e in root.iter()
        if markdown.is_division(e) and e.get("TYPE") != "SECTION"
    ]


def find(root: ET.Element, kind: str, n: str) -> ET.Element:
    return next(e for e in root.iter() if e.get("TYPE") == kind and e.get("N") == n)


def test_volumes_merge_into_the_structure():
    root = normalized()
    assert root.tag == "ECFR"
    assert outline(root) == [
        ("DIV1", "TITLE", "99"),
        ("DIV2", "SUBTITLE", "A"),
        ("DIV4", "SUBCHAP", "A"),
        ("DIV5", "PART", "1"),
        ("DIV5", "PART", "2"),
        ("DIV6", "SUBPART", "B"),
        ("DIV9", "APPENDIX", "Appendix A to Part 2"),
        ("DIV4", "SUBCHAP", "B"),
        ("DIV4", "SUBCHAP", "C"),
        ("DIV5", "PART", "3"),
        ("DIV2", "SUBTITLE", "B"),
        ("DIV3", "CHAPTER", "II"),
        ("DIV9", "APPENDIX", "Appendix to Chapter II"),
        ("DIV5", "PART", "4"),
        ("DIV9", "APPENDIX", "Appendix A to Part 4"),
        ("DIV9", "APPENDIX", "Appendix Z to Part 4"),
    ]
    title = root[0]
    assert markdown.plain_text(title.find("HEAD")) == "Title 99—Examples"
    assert not [e for e in root.iter() if e.tag in ("CFRTOC", "HEADER", "AMDDATE")]
    assert not [e for e in root.iter() if "NODE" in e.attrib]


def test_heads_come_from_the_first_volume_and_the_structure():
    root = normalized()
    heads = {
        (e.get("TYPE"), e.get("N")): markdown.plain_text(e.find("HEAD"))
        for e in root.iter()
        if markdown.is_division(e)
    }
    assert heads["SUBTITLE", "A"] == "SUBTITLE A—Alpha"
    assert heads["SUBCHAP", "B"] == "SUBCHAPTER B [RESERVED]"  # Bulk's, though empty.
    assert heads["PART", "1"] == "Part 1 [Reserved]"  # Only in the structure.
    assert heads["PART", "3"] == "PART 3—LONG"


def test_container_text_stays_in_its_container():
    subchapter = find(normalized(), "SUBCHAP", "A")
    assert [e.tag for e in subchapter][:3] == ["HEAD", "P", "DIV5"]
    assert markdown.plain_text(subchapter[-1]) == "After the parts."


def test_sections_and_appendices_get_the_structures_names():
    root = normalized()
    sections = [e.get("N") for e in root.iter() if e.get("TYPE") == "SECTION"]
    assert sections == ["2.1", "2.2", "2.10", "3.1", "3.2", "4.1", "4.2-4.3", "4.99"]
    head = find(root, "SECTION", "4.2-4.3").find("HEAD")
    assert head is not None and head.text == "§§ 4.2-4.3   [Reserved]"


def test_a_part_continued_in_the_next_volume_is_one_part():
    part = find(normalized(), "PART", "3")
    assert [e.get("N") for e in part if e.tag == "DIV8"] == ["3.1", "3.2"]
    assert len(part.findall("HEAD")) == 1


def test_print_quirks_are_undone():
    part = find(normalized(), "PART", "2")
    assert [(e.tag, e.get("N")) for e in part if e.tag != "HEAD"] == [
        ("DIV8", "2.1"),
        ("DIV8", "2.2"),
        ("DIV6", "B"),  # The empty subpart "0" is gone,
        ("DIV9", "Appendix A to Part 2"),  # the appendix is out of subpart B,
    ]
    appendix = part[-1]
    assert markdown.plain_text(appendix[-1]) == "Run-out text of the appendix."
    section = find(part, "SECTION", "2.1")
    assert len(section.findall("XREF")) == 1


def test_tables_are_marked_up_as_the_api_does():
    section = find(normalized(), "SECTION", "4.1")
    table = section.find("DIV/DIV/TABLE")
    assert table is not None
    assert [e.tag for e in table] == ["CAPTION", "THEAD", "TBODY", "TFOOT"]
    assert section.find("DIV/DIV[@class='table_head']") is None
    text = markdown.render(section)
    assert "**Table 1 to § 4.1**" in text
    assert "| Item<br>name | Value |" in text
    assert "| **Group** one |  |" in text
    assert "| UO<sub>2</sub> F | <sup>1</sup>\u2044<sub>2</sub> cup% |" in text
    assert "| 0.75 ***F*** <sub>*TOW*</sub> | X =" in text
    assert "| **Bold** x/y | C<sub>1</sub> <sup>2</sup> |" in text
    assert "| %*i* after | z |" in text
    assert "| <sup>1</sup> A note. |  |" in text
    plain = find(normalized(), "APPENDIX", "Appendix A to Part 4")
    assert [e.tag for e in plain.find("DIV/DIV/TABLE")] == ["TBODY"]


def test_rendered_text_matches_the_api():
    root = normalized()
    expected = "# § 2.10 Tenth.\n\nTen.\n"
    assert markdown.render(find(root, "SECTION", "2.10")) == expected


def test_names_without_a_match_come_from_the_bulk_file():
    structure = node(
        "title", "99", "Title 99",
        node("part", "5", "Part 5", node("appendix", "Exhibit B", "Exhibit B")),
    )  # fmt: skip
    bulk = """<DIV1 N="1" TYPE="TITLE"><HEAD>Title 99</HEAD>
    <DIV5 N="5" TYPE="PART"><HEAD>PART 5</HEAD>
    <DIV9 N="" TYPE="APPENDIX"><HEAD>Exhibit A to Part 5—Forms</HEAD></DIV9>
    <DIV9 N="" TYPE="APPENDIX"><HEAD>Exhibit B to Part 5</HEAD></DIV9>
    <DIV9 N="" TYPE="APPENDIX"></DIV9>
    </DIV5></DIV1>"""
    names = [
        e.get("N")
        for e in normalized(bulk, structure).iter()
        if e.get("TYPE") == "APPENDIX"
    ]
    assert names == ["Exhibit A to Part 5", "Exhibit B", ""]


def test_titles_without_volumes_or_parts():
    structure = node("title", "98", "Title 98—Empty")
    root = normalized("<ECFRBRWS/>", structure)
    assert outline(root) == [("DIV1", "TITLE", "98")]
    assert markdown.plain_text(root[0].find("HEAD")) == "Title 98—Empty"


def test_what_bulk_lacks_comes_from_the_structure():
    structure = node(
        "title", "98", "Title 98",
        node("chapter", "I", "Chapter I", node("part", "1", "Part 1")),
        node("chapter", "II", "Chapter II [Reserved]", reserved=True),
        node("part", "9", "Part 9 [Reserved]", reserved=True),
    )  # fmt: skip
    bulk = """<DIV1 N="1" TYPE="TITLE"><HEAD>Title 98</HEAD>
    <DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I</HEAD>
    <DIV5 N="1" TYPE="PART"><HEAD>PART 1</HEAD></DIV5></DIV3></DIV1>"""
    root = normalized(bulk, structure)
    assert outline(root)[1:] == [
        ("DIV3", "CHAPTER", "I"),
        ("DIV5", "PART", "1"),
        ("DIV3", "CHAPTER", "II"),
        ("DIV5", "PART", "9"),
    ]
    assert (
        markdown.plain_text(find(root, "CHAPTER", "II")[0]) == "Chapter II [Reserved]"
    )


def test_a_part_the_structure_lacks_is_an_error():
    bulk = '<DIV1 N="1" TYPE="TITLE"><HEAD>T</HEAD><DIV5 N="7" TYPE="PART"/></DIV1>'
    with pytest.raises(BulkFormatError, match="Part 7 isn't in the structure"):
        normalized(bulk)


def test_malformed_bulk_xml_is_an_error():
    with pytest.raises(BulkFormatError, match="Invalid bulk XML"):
        normalized("<DIV1><HEAD></DIV1>")
