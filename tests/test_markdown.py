from pathlib import Path, PurePosixPath

import pytest

from cfr_md import (
    Layout,
    ResponseFormatError,
    to_markdown,
    to_markdown_tree,
    write_markdown,
)


def test_headings_follow_relative_depth(part_xml):
    text = to_markdown(part_xml)
    assert text.startswith("# PART 164—SECURITY AND PRIVACY\n")
    assert "\n## Subpart A—General Provisions\n" in text
    assert "\n### § 164.102 Statutory basis.\n" in text
    assert "\n## Appendix A to Subpart C of Part 164—Matrix\n" in text


def test_single_section_starts_at_top_level():
    xml = '<DIV8 N="1.1" TYPE="SECTION"><HEAD>§ 1.1 Scope.</HEAD><P>Text.</P></DIV8>'
    assert to_markdown(xml) == "# § 1.1 Scope.\n\nText.\n"


def test_headings_deeper_than_six_levels_become_bold():
    xml = "<DIV1><HEAD>1</HEAD>" * 7 + "</DIV1>" * 7
    assert to_markdown(xml).splitlines()[-1] == "**1**"


def test_paragraphs_and_inline_formatting(part_xml):
    text = to_markdown(part_xml)
    assert "**(a)(1)** *Standard.* A covered entity ***must*** comply." in text
    assert r"**(i)** See the *Federal Register*<sup>1</sup> \* \* \*" in text


def test_notes_render_as_blockquotes(part_xml):
    text = to_markdown(part_xml)
    assert "> **Authority:** 42 U.S.C. 1320d." in text
    assert "> “THIS NOTICE DESCRIBES HOW MEDICAL INFORMATION MAY BE USED.”" in text
    assert "> [78 FR 5692, Jan. 25, 2013]" in text


def test_tables(part_xml):
    assert (
        "| Standards | Sections |\n"
        "| --- | --- |\n"
        "| **Administrative Safeguards** |  |\n"
        "| Access \\| Control | 164.312(a)(1) |"
    ) in to_markdown(part_xml)


def test_invalid_xml_raises():
    with pytest.raises(ResponseFormatError, match="Invalid eCFR XML"):
        to_markdown("<DIV5>")


def test_tree_layout(part_xml):
    files = to_markdown_tree(part_xml)
    assert list(files) == [
        PurePosixPath("part-164/index.md"),
        PurePosixPath("part-164/subpart-a/index.md"),
        PurePosixPath("part-164/subpart-a/section-164.102.md"),
        PurePosixPath("part-164/appendix-appendix-a-to-subpart-c-of-part-164.md"),
    ]
    index = files[PurePosixPath("part-164/index.md")]
    assert index.startswith("# PART 164—SECURITY AND PRIVACY\n\n> **Authority:**")
    assert "- [Subpart A—General Provisions](subpart-a/index.md)" in index
    section = files[PurePosixPath("part-164/subpart-a/section-164.102.md")]
    assert section.startswith("# § 164.102 Statutory basis.\n")


def test_tree_deduplicates_sibling_names():
    xml = (
        '<DIV5 N="1" TYPE="PART"><HEAD>Part 1</HEAD>'
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>A</HEAD></DIV8>'
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>B</HEAD></DIV8></DIV5>'
    )
    names = [p.name for p in to_markdown_tree(xml)]
    assert names == ["index.md", "section-1.1.md", "section-1.1-2.md"]


@pytest.mark.parametrize("layout", ["file", Layout.FILE])
def test_write_file(tmp_path: Path, part_xml, layout):
    target = tmp_path / "nested" / "out.md"
    assert write_markdown(part_xml, target, layout) == [target]
    assert target.read_text(encoding="utf-8") == to_markdown(part_xml)


def test_write_tree(tmp_path: Path, part_xml):
    written = write_markdown(part_xml, tmp_path, Layout.TREE)
    assert len(written) == 4
    assert (tmp_path / "part-164" / "subpart-a" / "section-164.102.md").is_file()
