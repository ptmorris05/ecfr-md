"""Defined terms: found in the definitions sections that govern a place."""

import pytest
from web_data import install

import ecfr_md
from ecfr_md import Definition, NodeNotFoundError, load, shortcuts
from ecfr_md import definitions as defined


@pytest.fixture
def api(api):
    return install(api)


def test_terms_from_the_part_and_the_subchapter(ecfr):
    found = ecfr.definitions("45 CFR 164.104(a)")
    terms = {d.term: d for d in found}
    # 164.108 names no scope, so it covers its part; 160.103 its subchapter.
    assert terms["Term A"].citation == "45 CFR 164.108"
    assert terms["Term A"].scope == "part"
    assert terms["Covered entity"].citation == "45 CFR 160.103"
    assert terms["Covered entity"].scope == "subchapter"
    assert (
        terms["Standard"].text == "*Standard* means a rule.\n\n**(1)** Its first part."
    )
    assert next(iter(terms)) == "Term A"  # Nearest scope first.
    assert found[0].to_dict() == {
        "term": "Term A",
        "text": "*Term A* means:\n\n**(1)** One.\n\n**(2)** Two.",
        "citation": "45 CFR 164.108",
        "address": "45/164.108",
        "scope": "part",
        "url": "https://www.ecfr.gov/on/2026-10-07/title-45/section-164.108",
        "date": "2026-10-07",
    }


def test_define_finds_plurals_and_suggests(ecfr):
    assert ecfr.define("covered entities", "45/164.102").citation == "45 CFR 160.103"
    assert ecfr.define("STANDARDS", "45/164.102").term == "Standard"
    with pytest.raises(NodeNotFoundError, match="did you mean 'Term B' or 'Term A'"):
        ecfr.define("term a b", "45/164.102")
    with pytest.raises(NodeNotFoundError, match=r"\(4 terms do\)"):
        ecfr.define("zebra", "45/164.102")


def test_definitions_section_scope_limits_it(ecfr):
    # Part 160's own sections are governed by 160.103 but not by 164.108.
    terms = {d.term for d in ecfr.definitions("45 CFR 160.101")}
    assert terms == {"Covered entity", "Standard"}
    # Part 170 comes after part 160 in the subchapter, so 160.103 governs it.
    assert {d.term for d in ecfr.definitions("45/170.1")} == terms


def test_module_level_define(ecfr, monkeypatch):
    monkeypatch.setattr(shortcuts, "_shared", ecfr)
    assert ecfr_md.define("term a", "45 CFR 164.104").term == "Term A"
    assert Definition.__doc__


def test_reading_terms():
    assert defined.singulars("entities") == ["entities", "entity", "entiti", "entitie"]
    assert defined.term_in("**(a)** *Act* means the Act.") == "Act"
    assert defined.term_in("The term “person” means anyone.") == "person"
    assert defined.term_in("*Definitions.* For this part:") is None
    assert defined.term_in("*General.* The rule.") is None
    assert defined.term_in("*123* means a number.") is None
    assert defined.term_in("No term here.") is None
    assert defined.scope_of(
        ["As used in this subpart, terms mean:", "*X* means y."]
    ) == ("subpart")
    assert defined.scope_of(["*X* means y.", "this title"]) == "part"


def test_governs_falls_back_to_a_broader_scope(ecfr):
    cfr = ecfr.outline()
    home = cfr["45 CFR 160.103"]
    entry = cfr["45 CFR 164.102"]
    # "This chapter" in a title without chapters: the title.
    assert defined.governs(home, "chapter", entry)
    assert defined.governs(home, "section", home)
    assert not defined.governs(home, "section", entry)
    lone = ecfr_md.Outline.build(ecfr._outline(45, ecfr._when(45, None)), 45)
    assert not defined.governs(lone["160.103"], "subchapter", entry)


def test_sections_outside_a_part():
    # A section with no part above it has no definitions sections to read.
    node = load(
        b'<DIV8 N="1.1" TYPE="SECTION"><HEAD>\xc2\xa7 1.1 Definitions.</HEAD>'
        b"<P><I>Thing</I> means a thing.</P></DIV8>",
        title=1,
    )
    (thing,) = defined.read(node)
    assert thing.term == "Thing" and thing.date is None
    assert thing.to_dict()["date"] is None
