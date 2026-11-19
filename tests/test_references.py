"""Cross-references: finding them, resolving them and linking them."""

import pytest
from conftest import PART_XML

from ecfr_md import Address, load
from ecfr_md.markdown import tree_key
from ecfr_md.output import read_front_matter
from ecfr_md.references import find_references, link_markdown

CONTEXT = Address(45, {"part": "164", "subpart": "E", "section": "164.502"})


def _found(text: str, context=CONTEXT) -> list[tuple[str, str | None]]:
    return [
        (r.text, str(r.target) if r.target else r.url)
        for r in find_references(text, context)
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("as defined in § 164.501.", [("§ 164.501", "45/164.501")]),
        (
            "§§ 164.502 and 164.504",
            [("§§ 164.502", "45/164.502"), ("164.504", "45/164.504")],
        ),
        (
            "§§ 164.500 through 164.534",
            [("§§ 164.500", "45/164.500"), ("164.534", "45/164.534")],
        ),
        (
            "§ 164.502(a)(1) through (3)",
            [("§ 164.502(a)(1)", "45/164.502/a/1"), ("(3)", "45/164.502/a/3")],
        ),
        (
            "§§ 164.502(b), 164.514(d), and 164.530(c)",
            [
                ("§§ 164.502(b)", "45/164.502/b"),
                ("164.514(d)", "45/164.514/d"),
                ("164.530(c)", "45/164.530/c"),
            ],
        ),
        ("26 CFR § 1.401(k)-1(a)", [("26 CFR § 1.401(k)-1(a)", "26/1.401.k-1/a")]),
        ("see 45 CFR 160.103.", [("45 CFR 160.103", "45/160.103")]),
        ("under 42 CFR 2", [("42 CFR 2", "42/part-2")]),
        (
            "45 CFR parts 160 and 164",
            [("45 CFR parts 160", "45/part-160"), ("164", "45/part-164")],
        ),
        (
            "part 160 of this subchapter",
            [("part 160 of this subchapter", "45/part-160")],
        ),
        ("part 2 of title 42", [("part 2 of title 42", "42/part-2")]),
        ("in part 160.", [("part 160", "45/part-160")]),
        (
            "subpart E of this part",
            [("subpart E of this part", "45/part-164/subpart-E")],
        ),
        ("subpart C of part 160", [("subpart C of part 160", "45/part-160/subpart-C")]),
        (
            "subparts A and E of this part",
            [
                ("subparts A", "45/part-164/subpart-A"),
                ("E of this part", "45/part-164/subpart-E"),
            ],
        ),
        (
            "paragraph (a)(1) of this section",
            [("paragraph (a)(1) of this section", "45/164.502/a/1")],
        ),
        (
            "paragraphs (a)(1) and (2) of this section",
            [
                ("paragraphs (a)(1)", "45/164.502/a/1"),
                ("(2) of this section", "45/164.502/a/2"),
            ],
        ),
        (
            "paragraphs (b) and (c)(1)",
            [("paragraphs (b)", "45/164.502/b"), ("(c)(1)", "45/164.502/c/1")],
        ),
        (
            "paragraph (c) of § 164.508",
            [("paragraph (c) of § 164.508", "45/164.508/c")],
        ),
        ("paragraph (1) of the definition of X", []),
        ("paragraphs (a) and 164.502", []),
        (
            "appendix A to this part",
            [("appendix A to this part", "45/part-164/appendix-A")],
        ),
        (
            "Appendix A to Subpart C of Part 164",
            [
                (
                    "Appendix A to Subpart C of Part 164",
                    "45/part-164/subpart-C/appendix-A",
                )
            ],
        ),
        ("Supplement I", [("Supplement I", "45/part-164/supplement-I")]),
        ("this part", [("this part", "45/part-164")]),
        ("this subpart", [("this subpart", "45/part-164/subpart-E")]),
        ("this section", [("this section", "45/164.502")]),
        ("§ followed by nothing", []),
        ("§ (a)", []),
        ("subpart Eb", []),
        (
            "45 CFR 164.502(a) and (b)",
            [("45 CFR 164.502(a)", "45/164.502/a"), ("(b)", "45/164.502/b")],
        ),
    ],
)
def test_cfr_references(text, expected):
    assert _found(text) == expected


def test_external_references():
    found = find_references("42 U.S.C. 1320d-2(a); 78 FR 5,692; Pub. L. 104-191")
    assert [(r.kind, r.text) for r in found] == [
        ("usc", "42 U.S.C. 1320d-2(a)"),
        ("fr", "78 FR 5,692"),
        ("public_law", "Pub. L. 104-191"),
    ]
    assert found[0].url.endswith("title42-section1320d-2&num=0&edition=prelim")
    assert found[1].url == "https://www.federalregister.gov/citation/78-FR-5692"
    assert found[2].url.endswith("/104th-congress/public-law/191")
    assert found[2].citation == "Pub. L. 104-191"
    assert found[0].to_dict()["address"] is None


@pytest.mark.parametrize(
    ("congress", "suffix"), [(101, "st"), (102, "nd"), (103, "rd"), (111, "th")]
)
def test_public_law_ordinals(congress, suffix):
    (law,) = find_references(f"Public Law {congress}-5")
    assert f"/{congress}{suffix}-congress/" in law.url


def test_link_templates_can_be_changed():
    (usc,) = find_references(
        "5 U.S.C. 552", urls={"usc": "https://x/{title}/{section}"}
    )
    assert usc.url == "https://x/5/552"


def test_relative_references_need_context():
    assert _found("§ 164.501, this part, paragraph (a), appendix A", None) == []
    assert _found("this subpart", Address(45, {"section": "164.502"})) == []
    assert _found("this part", Address(45)) == []
    assert _found("§ 164.501", "45 CFR 164.502") == [("§ 164.501", "45/164.501")]


def test_references_carry_the_context_date():
    dated = Address(
        45, {"section": "164.502"}, date=__import__("datetime").date(2020, 1, 1)
    )
    (ref,) = find_references("§ 164.501", dated)
    assert ref.url == "https://www.ecfr.gov/on/2020-01-01/title-45/section-164.501"
    assert ref.citation == "45 CFR 164.501"
    assert ref.to_dict() == {
        "start": 0,
        "end": 9,
        "text": "§ 164.501",
        "kind": "cfr",
        "citation": "45 CFR 164.501",
        "address": "45/164.501",
        "url": ref.url,
    }


def test_link_markdown_leaves_headings_links_and_code_alone():
    text = (
        "# § 164.502 Uses\n\nSee § 164.501, [§ 164.504](x.md), `§ 164.506` and "
        "<a title='§ 164.508'>x</a>."
    )
    linked = link_markdown(text, CONTEXT)
    assert linked.startswith("# § 164.502 Uses\n\nSee [§ 164.501](https://www.ecfr")
    assert "[§ 164.504](x.md)" in linked
    assert "`§ 164.506`" in linked
    assert "<a title='§ 164.508'>" in linked


def test_link_markdown_puts_spaces_in_angle_brackets():
    linked = link_markdown("appendix A to this part", CONTEXT, lambda r: "a b.md")
    assert linked == "[appendix A to this part](<a b.md>)"


def test_node_references_and_linked_markdown():
    part = load(PART_XML, title=45)
    section = part["164.106"]
    assert [r.citation for r in section.references] == ["45 CFR 164.106(a)"]
    assert (
        "[paragraph (a)](https://www.ecfr.gov/current/title-45/section-164.106#p-"
        in (section.to_markdown(links=True))
    )
    paragraph = part["164.102(a)(1)(i)"]
    assert "[78 FR 5692]" not in paragraph.to_markdown(links=True)
    assert part["164.102"].to_markdown(links=True).count("federalregister.gov") == 1


TREE = """<DIV5 N="160" TYPE="PART"><HEAD>PART 160—GENERAL</HEAD>
<AUTH><HED>Authority:</HED><PSPACE>42 U.S.C. 1320d.</PSPACE></AUTH>
<DIV6 N="A" TYPE="SUBPART"><HEAD>Subpart A—General</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Purpose.</HEAD>
<P>(a) See § 160.103, subpart B of this part, appendix A to this part,
§ 164.502 and paragraph (a) of this section.</P></DIV8></DIV6>
<DIV6 N="B" TYPE="SUBPART"><HEAD>Subpart B—Definitions</HEAD>
<DIV8 N="160.103" TYPE="SECTION"><HEAD>§ 160.103 Definitions.</HEAD>
<P>As in § 160.101(a) and this part.</P></DIV8></DIV6>
<DIV9 N="Appendix A to Part 160" TYPE="APPENDIX"><HEAD>Appendix A to Part 160</HEAD>
<P>Text.</P></DIV9></DIV5>"""


def test_saved_trees_link_between_their_files(tmp_path):
    files = load(TREE, title=45).save(tmp_path, tree=True)
    purpose = (tmp_path / "part-160/subpart-A/160.101.md").read_text()
    assert "[§ 160.103](../subpart-B/160.103.md)" in purpose
    assert "[subpart B of this part](../subpart-B/index.md)" in purpose
    assert "[appendix A to this part](../appendix-A.md)" in purpose
    assert (
        "[§ 164.502](https://www.ecfr.gov/current/title-45/section-164.502)" in purpose
    )
    assert "[paragraph (a) of this section](160.101.md)" in purpose
    index = (tmp_path / "part-160/index.md").read_text()
    assert "[42 U.S.C. 1320d](https://uscode.house.gov/" in index
    assert (
        "[this part](../index.md)"
        in (tmp_path / "part-160/subpart-B/160.103.md").read_text()
    )
    assert all(read_front_matter(f)["links"] is True for f in files)


def test_trees_can_be_saved_without_links(tmp_path):
    files = load(TREE, title=45).save(tmp_path, tree=True, links=False)
    assert "](" not in (tmp_path / "part-160/subpart-A/160.101.md").read_text()
    assert all(read_front_matter(f)["links"] is False for f in files)


def test_single_files_link_on_request(tmp_path):
    part = load(TREE, title=45)
    plain = read_front_matter(part.save(tmp_path / "plain.md"))
    assert "links" not in plain
    linked = part.save(tmp_path / "linked.md", links=True)
    assert read_front_matter(linked)["links"] is True
    assert "[§ 160.103](https://www.ecfr.gov/" in linked.read_text()
    paragraph = part["160.101(a)"].save(tmp_path / "p.md", tree=True)
    assert "[§ 160.103](https://" in paragraph[0].read_text()


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("45/164.502/a", "section:164.502"),
        ("45/part-164", "part:164"),
        ("45/part-164/subpart-E", "subpart:164:E"),
        ("45/part-164/subpart-C/appendix-A", "appendix:164:C:appendix-A"),
        ("45/chapter-A", None),
    ],
)
def test_tree_keys(address, key):
    assert tree_key(Address.parse(address)) == key
