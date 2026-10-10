import datetime as dt
import json
from pathlib import Path

import pytest

from ecfr_md import (
    AmbiguousNodeError,
    Appendix,
    Level,
    NodeNotFoundError,
    Paragraph,
    Part,
    Section,
    Subpart,
    load,
)
from ecfr_md.output import web_url

RETRIEVED = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def part(part_xml):
    return load(
        part_xml,
        title=45,
        date=dt.date(2026, 10, 7),
        hierarchy={"part": "164"},
        url="https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/title-45.xml?part=164",
        retrieved=RETRIEVED,
    )


def ids(node):
    return [child.identifier for child in node]


def front_matter(text: str) -> dict[str, str]:
    head, _, _ = text.removeprefix("---\n").partition("\n---\n")
    return {
        k: json.loads(v) for k, v in (line.split(": ", 1) for line in head.splitlines())
    }


def test_node_types_and_citations(part):
    assert isinstance(part, Part)
    assert part.citation == "45 CFR Part 164"
    assert isinstance(part["A"], Subpart)
    assert part["A"].citation == "45 CFR Part 164 Subpart A"
    assert isinstance(part["164.104"], Section)
    assert part["164.104"].citation == "45 CFR 164.104"
    assert isinstance(part[1], Appendix)
    assert part[1].citation == "45 CFR Appendix A to Subpart C of Part 164"
    assert repr(part["A"]) == (
        "<Subpart 45 CFR Part 164 Subpart A 'Subpart A—General Provisions'>"
    )


def test_children_iteration_and_lookup(part):
    assert ids(part) == ["A", "Appendix A to Subpart C of Part 164"]
    assert len(part) == 2
    assert part["Subpart A"] is part["A"]
    assert part["subpart a"] is part["A"]  # Case-insensitive if no exact match.
    assert part["45 CFR 164.104"] is part["164.104"] is part["Title 45 164.104"]
    assert part["§ 164.104"] is part["A"]["164.104"]
    assert "164.104" in part
    assert "164.999" not in part
    assert part.get("164.999") is None
    with pytest.raises(NodeNotFoundError, match=r"164\.999"):
        part["164.999"]
    with pytest.raises(KeyError):  # NodeNotFoundError is a KeyError.
        part["164.999"]


def test_ambiguous_lookups_raise_instead_of_guessing(part):
    section = part["164.108"]
    with pytest.raises(AmbiguousNodeError, match=r"2 nodes match '\(1\)'") as info:
        section["(1)"]
    assert [p.text for p in info.value.candidates] == ["**(1)** One.", "**(1)** Uno."]
    with pytest.raises(AmbiguousNodeError):
        part["164.108(1)"]
    with pytest.raises(AmbiguousNodeError):
        section.get("(1)")  # Ambiguity is not absence.
    assert "(1)" in section
    assert section.find("1") is section[0]  # find() returns the first match.


def test_iter_and_find_by_level(part):
    assert [s.identifier for s in part.iter("section")] == [
        "164.102",
        "164.104",
        "164.106",
        "164.108",
    ]
    assert [s.identifier for s in part.iter(Level.SUBPART)] == ["A"]
    assert part.find(level="appendix") is part[1]
    assert part.find("164.104", level="section") is part["164.104"]
    assert part.find("nope") is None


def test_parent_ancestors_and_hierarchy(part):
    paragraph = part["164.104(a)(2)(ii)(A)"]
    assert paragraph.parent is part["164.104(a)(2)(ii)"]
    assert [a.identifier for a in paragraph.ancestors] == [
        "ii",
        "2",
        "a",
        "164.104",
        "A",
        "164",
    ]
    assert paragraph.section is part["164.104"]
    assert paragraph.hierarchy == {"part": "164", "subpart": "A", "section": "164.104"}


def test_paragraph_nesting(part):
    section = part["164.104"]
    assert ids(section) == ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    assert ids(section["a"]) == ["1", "2"]
    assert ids(section["(a)(2)"]) == ["i", "ii"]
    assert ids(section["(a)(2)(ii)"]) == ["A"]
    assert ids(section["(a)(2)(ii)(A)"]) == ["1"]  # Italic level 5.
    assert section["(a)(2)(ii)(A)(1)"].depth == 5
    assert ids(section["j"]) == ["1"]  # Run in after an italic heading.
    assert section.paragraph_issues == ()


def test_worked_examples_are_not_paragraphs(part):
    section = part["164.106"]
    assert ids(section) == ["a", "b", "c"]
    assert ids(section["b"]) == []
    examples = section["b"].to_markdown()
    assert "*Example 1.* (i) Facts. A plan." in examples
    assert "**(ii)** Conclusion. It complies." in examples
    assert "*Example 2.* (i) Facts. Another plan." in examples
    assert section.paragraph_issues == ()


def test_paragraph_issues_flag_irregular_sections(part):
    assert part["164.108"].paragraph_issues == (
        "45 CFR 164.108: (1) appears more than once",
        "45 CFR 164.108: (1) is out of sequence (expected position 3)",
    )


def test_paragraph_text_and_headings(part):
    first = part["164.104(a)(1)"]
    assert isinstance(first, Paragraph)
    assert first.citation == "45 CFR 164.104(a)(1)"
    assert first.heading == "First"
    assert first.text == "**(1)** *First.* One."
    assert part["164.104(a)"].heading == "Standard: Run in"
    assert part["164.104(c)"].text == "**(c)** [Reserved]"
    assert part["164.104(i)"].text == "**(i)** Letter i, not roman one."
    assert part["164.104(a)(2)"].to_markdown() == (
        "**(2)** Two.\n\n**(i)** Roman one.\n\n**(ii)** Roman two.\n\n"
        "**(A)** Upper.\n\n**(1)** Italic one.\n"
    )
    section_text = part["164.104"].to_markdown()
    assert "Introductory text belongs to no paragraph." in section_text
    assert "[80 FR 1" not in part["164.104(j)"].to_markdown()


def test_nodes_are_truthy_even_without_children(part):
    leaf = part["164.104(h)"]
    assert len(leaf) == 0
    assert leaf


def test_provenance(part):
    assert part["164.104(a)(1)"].provenance == {
        "citation": "45 CFR 164.104(a)(1)",
        "heading": "First",
        "date": "2026-10-07",
        "source": "https://www.ecfr.gov/on/2026-10-07/title-45/section-164.104#p-164.104(a)(1)",
        "api": "https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/title-45.xml?part=164",
        "retrieved": "2026-10-09T12:00:00+00:00",
        "generator": part.provenance["generator"],
        "disclaimer": part.provenance["disclaimer"],
    }
    assert part.provenance["generator"].startswith("ecfr-md ")
    assert "not an official legal edition" in part.provenance["disclaimer"]


@pytest.mark.parametrize(
    ("hierarchy", "paragraph", "url"),
    [
        ({}, (), "https://www.ecfr.gov/on/2026-10-07/title-45"),
        ({"part": "164", "subpart": "E"}, (), ".../title-45/part-164/subpart-E"),
        (
            {"subtitle": "A", "subchapter": "C"},
            (),
            ".../title-45/subtitle-A/subchapter-C",
        ),
        (
            {"part": "164", "section": "164.502"},
            ("a",),
            ".../title-45/section-164.502#p-164.502(a)",
        ),
        (
            {"part": "164", "appendix": "Appendix A to Part 164"},
            (),
            ".../title-45/part-164/appendix-Appendix%20A%20to%20Part%20164",
        ),
    ],
)
def test_web_url(hierarchy, paragraph, url):
    expected = url.replace(".../", "https://www.ecfr.gov/on/2026-10-07/")
    assert web_url(45, dt.date(2026, 10, 7), hierarchy, paragraph) == expected


def test_save_file_directory_and_tree(part, tmp_path: Path):
    assert part.save(tmp_path / "p.md") == [tmp_path / "p.md"]
    saved = (tmp_path / "p.md").read_text(encoding="utf-8")
    assert front_matter(saved)["citation"] == "45 CFR Part 164"
    assert saved.endswith(part.to_markdown())

    assert part.save(tmp_path) == [tmp_path / "45-cfr-part-164.md"]
    plain = part["164.104"].save(tmp_path / "s.md", front_matter=False)[0]
    assert plain.read_text(encoding="utf-8") == str(part["164.104"])

    written = part.save(tmp_path / "tree", tree=True)
    assert len(written) == 7
    section = tmp_path / "tree/part-164/subpart-a/section-164.104.md"
    meta = front_matter(section.read_text(encoding="utf-8"))
    assert meta["citation"] == "45 CFR 164.104"
    assert meta["heading"] == "§ 164.104 Paragraph nesting."
    index = front_matter(
        (tmp_path / "tree/part-164/subpart-a/index.md").read_text(encoding="utf-8")
    )
    assert index["citation"] == "45 CFR Part 164 Subpart A"

    paragraph = part["164.104(a)"]
    assert paragraph.save(tmp_path) == [tmp_path / "45-cfr-164.104-a.md"]
    assert not list(tmp_path.rglob("*.part"))  # Atomic writes leave no debris.


def test_subtree_save_keeps_ancestors_in_metadata(part, tmp_path: Path):
    part["A"].save(tmp_path, tree=True)
    meta = front_matter(
        (tmp_path / "subpart-a/section-164.102.md").read_text(encoding="utf-8")
    )
    assert meta["citation"] == "45 CFR 164.102"
    index = front_matter((tmp_path / "subpart-a/index.md").read_text(encoding="utf-8"))
    assert index["citation"] == "45 CFR Part 164 Subpart A"


def test_subject_groups_are_cited_like_the_ecfr():
    xml = (
        '<DIV5 N="108" TYPE="PART"><HEAD>Part 108</HEAD>'
        '<DIV6 N="C" TYPE="SUBPART"><HEAD>Subpart C</HEAD>'
        '<DIV7 N="ECFR1a2b" TYPE="SUBJGRP"><HEAD>Organizing a Company</HEAD>'
        '<DIV8 N="108.150" TYPE="SECTION"><HEAD>§ 108.150 X.</HEAD></DIV8>'
        "</DIV7></DIV6></DIV5>"
    )
    part = load(xml, title=13, date=dt.date(2026, 9, 15))
    group = part.find(level="subject_group")
    assert group.citation == "13 CFR Part 108 Subpart C - Organizing a Company"
    assert group.provenance["source"] == (
        "https://www.ecfr.gov/on/2026-09-15/title-13/part-108/subject-group-ECFR1a2b"
    )
    assert part["108.150"].citation == "13 CFR 108.150"


def test_page_links_come_from_the_api_metadata_when_present():
    meta = '{"path":"/on/_SUBSTITUTE_DATE_/title-43/section-5420.5422.1"}'
    xml = (
        f'<DIV8 N="5422.1" TYPE="SECTION" hierarchy_metadata=\'{meta}\'>'
        "<HEAD>§ 5422.1 X.</HEAD><P>(a) Text.</P></DIV8>"
    )
    section = load(xml, title=43, date=dt.date(2026, 9, 15))
    page = "https://www.ecfr.gov/on/2026-09-15/title-43/section-5420.5422.1"
    assert section.provenance["source"] == page
    assert section["(a)"].provenance["source"] == f"{page}#p-5422.1(a)"


def test_built_links_prefix_sections_numbered_unlike_their_part():
    url = web_url(43, dt.date(2026, 9, 15), {"part": "5420", "section": "5422.1"})
    assert url.endswith("/title-43/section-5420.5422.1")


@pytest.mark.parametrize(
    ("markers", "expected"),
    [
        # (i) after (h)(2) is the letter when (j) follows...
        (["h", "1", "2", "i", "j"], {"h": ["1", "2"], "i": [], "j": []}),
        # ...and a roman numeral under (2) when (ii) follows.
        (["h", "1", "2", "i", "ii", "3"], {"h": ["1", "2", "3"]}),
    ],
)
def test_lookahead_decides_letter_or_roman_i(markers, expected):
    paragraphs = "".join(
        f"<P>({m}) Text {m}.</P>" for m in ["a", "b", "c", "d", "e", "f", "g", *markers]
    )
    section = load(
        f'<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD>{paragraphs}</DIV8>', title=1
    )
    for parent, children in expected.items():
        assert ids(section[f"({parent})"]) == children
    assert section.paragraph_issues == ()
    if "ii" in markers:
        assert ids(section["(h)(2)"]) == ["i", "ii"]
