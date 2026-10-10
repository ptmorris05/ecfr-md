import io
import re
from pathlib import PurePosixPath

import pytest

from ecfr_md import ResponseFormatError, load
from ecfr_md import markdown as md

SECTIONS = ["164.102", "164.104", "164.106", "164.108"]


@pytest.fixture
def part(part_xml):
    return load(part_xml, title=45, hierarchy={"part": "164"})


def test_headings_follow_relative_depth(part):
    text = part.to_markdown()
    assert text.startswith("# PART 164—SECURITY AND PRIVACY\n")
    assert "\n## Subpart A—General Provisions\n" in text
    assert "\n### § 164.102 Statutory basis.\n" in text
    assert "\n## Appendix A to Subpart C of Part 164—Matrix\n" in text


def test_single_section_starts_at_top_level():
    xml = '<DIV8 N="1.1" TYPE="SECTION"><HEAD>§ 1.1 Scope.</HEAD><P>Text.</P></DIV8>'
    assert load(xml, title=1).to_markdown() == "# § 1.1 Scope.\n\nText.\n"


def test_division_without_head_is_titled_by_number_first():
    xml = '<DIV5 N="9" TYPE="PART"><P>Intro.</P><DIV8 N="9.1"><P>A.</P></DIV8></DIV5>'
    assert load(xml, title=1).to_markdown() == "# 9\n\nIntro.\n\n## 9.1\n\nA.\n"


def test_headings_deeper_than_six_levels_become_bold():
    xml = "<DIV1><HEAD>1</HEAD>" * 7 + "</DIV1>" * 7
    assert load(xml, title=1).to_markdown().splitlines()[-1] == "**1**"


def test_paragraphs_and_inline_formatting(part):
    text = part.to_markdown()
    assert "**(a)(1)** *Standard.* A covered entity ***must*** comply." in text
    assert r"**(i)** See the *Federal Register*<sup>1</sup> \* \* \*" in text


def test_notes_render_as_blockquotes(part):
    text = part.to_markdown()
    assert "> **Authority:** 42 U.S.C. 1320d." in text
    assert "> “THIS NOTICE DESCRIBES HOW MEDICAL INFORMATION MAY BE USED.”" in text
    assert "> [78 FR 5692, Jan. 25, 2013]" in text


def test_tables(part):
    assert (
        "| Standards | Sections |\n"
        "| --- | --- |\n"
        "| **Administrative Safeguards** |  |\n"
        "| Access \\| Control | 164.312(a)(1) |"
    ) in part.to_markdown()


def test_rare_markup(part):
    text = part["164.106"].to_markdown()
    assert "\n\\* \\* \\* \\* \\*\n" in text  # <STARS/>: omitted text
    assert "| Dividends | $10,000 |" in text  # leader work
    assert "- Asbestos.\n\n- Bauxite." in text  # list columns


def test_text_that_looks_like_markdown_is_escaped(part):
    text = part["164.106"].to_markdown()
    assert "\n1\\. Loans for equipment\n" in text  # not an ordered list
    assert "\n\\- not a list\n" in text
    assert r"Values below 5 \<Q> and AT&T&amp;copy;" in text  # not HTML


def test_invalid_xml_raises():
    with pytest.raises(ResponseFormatError, match="Invalid eCFR XML"):
        load("<DIV5>", title=1)
    with pytest.raises(ResponseFormatError, match="Invalid eCFR XML"):
        list(md.file_events(io.BytesIO(b"<DIV5>")))


def test_xml_without_cfr_content_raises():
    with pytest.raises(ResponseFormatError, match="no CFR content"):
        load("<ECFR><AMDDATE>Jan 1</AMDDATE></ECFR>", title=1)


def test_streaming_matches_in_memory_rendering(part, part_xml):
    blocks: list[str] = []
    events = md.file_events(io.BytesIO(part_xml.encode()))
    md.write_document(events, blocks.append, free=True)
    assert md.join(blocks) == part.to_markdown()

    files: dict[PurePosixPath, str] = {}
    events = md.file_events(io.BytesIO(part_xml.encode()))
    md.write_tree(events, lambda p, body, *_: files.__setitem__(p, body), free=True)
    assert files == part.to_markdown_tree()


def test_tree(part):
    files = part.to_markdown_tree()
    sections = [f"part-164/subpart-a/section-{s}.md" for s in SECTIONS]
    assert sorted(str(p) for p in files) == sorted(
        [
            "part-164/index.md",
            "part-164/subpart-a/index.md",
            *sections,
            "part-164/appendix-appendix-a-to-subpart-c-of-part-164.md",
        ]
    )
    index = files[PurePosixPath("part-164/index.md")]
    assert index.startswith("# PART 164—SECURITY AND PRIVACY\n\n> **Authority:**")
    assert "- [Subpart A—General Provisions](subpart-a/index.md)" in index
    subpart = files[PurePosixPath("part-164/subpart-a/index.md")]
    links = re.findall(r"\]\(([^)]+)\)", subpart)
    assert links == [f"section-{s}.md" for s in SECTIONS]  # Document order.


def test_tree_deduplicates_sibling_names():
    xml = (
        '<DIV5 N="1" TYPE="PART"><HEAD>Part 1</HEAD>'
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>A</HEAD></DIV8>'
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>B</HEAD></DIV8></DIV5>'
    )
    names = sorted(p.name for p in load(xml, title=1).to_markdown_tree())
    assert names == ["index.md", "section-1.1-2.md", "section-1.1.md"]


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("1. Loans", "1\\. Loans"),
        ("12) Item", "12\\) Item"),
        ("1.5 percent", "1.5 percent"),
        ("- dash", "\\- dash"),
        ("+ plus", "\\+ plus"),
        ("# hash", "\\# hash"),
        ("> quote", "\\> quote"),
        ("=== rule", "\\=== rule"),
        ("Plain text", "Plain text"),
    ],
)
def test_escape_block_start(text, escaped):
    assert md.escape_block_start(text) == escaped


def test_streaming_many_sections_keeps_every_heading():
    """Freed elements' ids get reused; streaming must not mistake new for old."""
    sections = "".join(
        f'<DIV8 N="1.{i}" TYPE="SECTION"><HEAD>§ 1.{i} S.</HEAD><P>T{i}.</P></DIV8>'
        for i in range(3000)
    )
    xml = f'<DIV5 N="1" TYPE="PART"><HEAD>Part 1</HEAD>{sections}</DIV5>'.encode()
    blocks: list[str] = []
    md.write_document(md.file_events(io.BytesIO(xml)), blocks.append, free=True)
    assert md.join(blocks) == load(xml, title=1).to_markdown()
    assert sum(b.startswith("## ") for b in blocks) == 3000


@pytest.mark.parametrize(
    ("inline", "expected"),
    [
        ("<I>R-</I>value", "<em>R-</em>value"),  # punctuation then a letter
        ("bank <I>i'</I>s", "bank <em>i'</em>s"),
        ("<I>U</I><E T='54'>o</E>", "*U*<sub>o</sub>"),  # italic subscript
        ("<I>U</I><E T='8145'>o</E>", "*U*<em>o</em>"),  # adjacent spans
        ("<B>b</B><I>i</I>", "**b**<em>i</em>"),
        ("<I>Standard.</I> Text", "*Standard.* Text"),
        ("(<I>a</I>)", "(*a*)"),
        (
            "<B>§ 110.79<E T='01'>c</E>:</B>",
            "<strong>§ 110.79**c**:</strong>",
        ),  # nested
    ],
)
def test_emphasis_renders_in_commonmark(inline, expected):
    xml = f'<DIV8 N="1" TYPE="SECTION"><HEAD>S</HEAD><P>{inline}</P></DIV8>'
    assert load(xml, title=1).to_markdown() == f"# S\n\n{expected}\n"


def test_marker_without_a_space_still_renders_bold():
    xml = '<DIV8 N="1" TYPE="SECTION"><HEAD>S</HEAD><P>(i)When</P></DIV8>'
    assert "<strong>(i)</strong>When" in load(xml, title=1).to_markdown()
