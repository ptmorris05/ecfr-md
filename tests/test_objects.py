"""The object model: text, serialization, display, levels and docs."""

import dataclasses
import datetime as dt
import inspect
import json

import pytest

import ecfr_md
from ecfr_md import (
    ECFR,
    Change,
    InvalidParameterError,
    Level,
    Match,
    Outline,
    Paragraph,
    load,
    models,
)
from ecfr_md.document import as_level

DATE = dt.date(2026, 10, 7)
ON = "https://www.ecfr.gov/on/2026-10-07/title-45"


@pytest.fixture
def part(part_xml):
    return load(part_xml, title=45, date=DATE, hierarchy={"part": "164"})


@pytest.fixture
def outline():
    structure = {
        "identifier": "45", "label": "Title 45—Public Welfare", "type": "title",
        "children": [{
            "identifier": "164", "label": "Part 164—Security", "type": "part",
            "size": 5000,
            "children": [
                {"label": "General <i>heading</i>", "type": "hed1"},
                {"identifier": "E", "label": "Subpart E—Privacy", "type": "subpart",
                 "children": [
                    {"identifier": "ECFR1a", "label": "Uses", "type": "subject_group",
                     "children": [{"identifier": "164.502", "type": "section",
                                   "label": "§ 164.502 Uses and disclosures."}]},
                    {"identifier": "164.512", "type": "section",
                     "label": "§ 164.512 Uses *without* [authorization]."},
                 ]},
                {"identifier": "Appendix A to Part 164", "type": "appendix",
                 "label": "Appendix A to Part 164—Matrix", "reserved": True},
            ],
        }],
    }  # fmt: skip
    node = models.Node.model_validate(structure)
    return Outline.build(node, 45, date=DATE)


# .text is the full text everywhere


def test_paragraph_text_is_the_whole_subtree(part):
    paragraph = part["164.104(a)"]
    assert isinstance(paragraph, Paragraph)
    assert paragraph.text == paragraph.to_markdown() == str(paragraph)
    assert "Italic one." in paragraph.text
    assert paragraph.own_text == "**(a)** *Standard: Run in*"
    leaf = part["164.104(h)"]
    assert leaf.text == leaf.own_text + "\n"


# to_markdown(max_chars=) and Jupyter display


def test_to_markdown_truncates_at_a_block_boundary(part):
    section = part["164.104"]
    full = section.to_markdown()
    assert section.to_markdown(max_chars=len(full)) == full
    assert section.to_markdown(max_chars=None) == full
    cut = section.to_markdown(max_chars=200)
    assert len(cut) <= 200
    kept, note = cut.rsplit("\n\n", 1)
    assert full.startswith(kept + "\n\n")  # Whole blocks only.
    omitted = len(full) - len(kept)
    assert note == f"… (truncated; {omitted:,} more characters)\n"


def test_to_markdown_cuts_long_blocks_at_lines_or_words():
    lines = "\n".join(f"| row {n} | value |" for n in range(50))
    xml = f'<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD><P>{"word " * 40}</P></DIV8>'
    section = load(xml, title=1)
    assert section.to_markdown(max_chars=60).startswith("# S\n\n… (truncated;")
    from ecfr_md.document import truncate

    table = truncate(lines, 100)
    assert table.startswith("| row 0 | value |\n") and "row 49" not in table
    words = truncate("word " * 40, 70)
    assert words.startswith("word word") and len(words) <= 70
    assert truncate("x" * 100, 10) == "… (truncated; 100 more characters)\n"
    with pytest.raises(InvalidParameterError, match="max_chars must be at least 1"):
        section.to_markdown(max_chars=0)


def test_jupyter_shows_markdown_capped(part, monkeypatch):
    section = part["164.104"]
    assert section._repr_markdown_() == section.to_markdown()
    monkeypatch.setattr("ecfr_md.document.REPR_MAX_CHARS", 150)
    shown = section._repr_markdown_()
    assert len(shown) <= 150
    assert shown.endswith("more characters — use .to_markdown())\n")


# toc


def test_toc_lists_citations_and_headings_without_text(part):
    toc = part.toc()
    lines = toc.splitlines()
    assert lines[:3] == [
        "45 CFR Part 164 — SECURITY AND PRIVACY",
        "  45 CFR Part 164 Subpart A — General Provisions",
        "    45 CFR 164.102 — Statutory basis.",
    ]
    assert "      45 CFR 164.104(a) — Standard: Run in" in lines
    assert "              45 CFR 164.104(a)(2)(ii)(A)(1)" in lines  # Paragraphs.
    assert lines[-1] == "  45 CFR Appendix A to Subpart C of Part 164 — Matrix"
    assert "Introductory text" not in toc
    assert part.toc(depth=1).splitlines() == [lines[0], lines[1], lines[-1]]
    assert part["164.104"].toc(depth=0) == "45 CFR 164.104 — Paragraph nesting."
    with pytest.raises(InvalidParameterError, match="depth must be 0 or more"):
        part.toc(depth=-1)


def test_toc_headings_drop_only_what_the_citation_says():
    xml = (
        '<DIV5 N="108" TYPE="PART"><HEAD>Part 108</HEAD>'
        '<DIV7 N="ECFR1a2b" TYPE="SUBJGRP"><HEAD>Organizing</HEAD>'
        '<DIV8 N="108.150" TYPE="SECTION"><HEAD>Rules for 108.150</HEAD></DIV8>'
        "</DIV7></DIV5>"
    )
    assert load(xml, title=13).toc().splitlines() == [
        "13 CFR Part 108",
        "  13 CFR Part 108 - Organizing",
        "    13 CFR 108.150 — Rules for 108.150",
    ]


# to_dict


def test_node_to_dict_is_json_ready(part):
    section = part["164.104"]
    data = section.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert {k: data[k] for k in ("citation", "level", "identifier", "date")} == {
        "citation": "45 CFR 164.104",
        "level": "section",
        "identifier": "164.104",
        "date": "2026-10-07",
    }
    assert data["heading"] == "§ 164.104 Paragraph nesting."
    assert data["url"] == section.url == f"{ON}/section-164.104"
    assert data["text"] == section.text
    assert "raw" not in data
    first = data["children"][0]
    assert first["citation"] == "45 CFR 164.104(a)"
    assert first["children"][0]["url"] == f"{ON}/section-164.104#p-164.104(a)(1)"


def test_node_to_dict_depth_text_and_raw(part):
    assert "children" not in part.to_dict(depth=0)
    shallow = part.to_dict(depth=1, text=False)
    assert "text" not in shallow
    assert [c["identifier"] for c in shallow["children"]] == [
        "A",
        "Appendix A to Subpart C of Part 164",
    ]
    assert all("children" not in c for c in shallow["children"])
    raw = part["164.104"].to_dict(depth=1, include_raw=True)
    assert raw["raw"].startswith('<DIV8 N="164.104"')
    assert raw["children"][0]["raw"] is None  # Paragraphs have no element.
    assert load("<DIV5 N='1' TYPE='PART'/>", title=1).to_dict()["date"] is None
    with pytest.raises(InvalidParameterError):
        part.to_dict(depth=-1)


def test_outline_to_dict_and_urls(outline):
    data = outline.to_dict()
    assert json.loads(json.dumps(data)) == data
    part = data["children"][0]
    assert part == part | {
        "citation": "45 CFR Part 164",
        "level": "part",
        "identifier": "164",
        "heading": "Part 164—Security",
        "date": "2026-10-07",
        "url": f"{ON}/part-164",
        "reserved": False,
        "size": 5000,
    }
    heading, subpart, appendix = part["children"]
    assert heading["level"] == "hed1" and heading["identifier"] is None
    assert subpart["children"][0]["url"] == f"{ON}/part-164/subject-group-ECFR1a"
    assert appendix["reserved"] is True
    assert appendix["url"] == f"{ON}/part-164/appendix-Appendix%20A%20to%20Part%20164"
    assert "children" not in outline.to_dict(depth=0)
    shallow = outline.to_dict(depth=1, include_raw=True)
    assert shallow["raw"]["identifier"] == "45"
    assert "children" not in shallow["children"][0]


def test_outline_without_a_date_links_to_current_text():
    node = models.Node.model_validate(
        {"identifier": "1", "label": "T", "type": "title"}
    )
    entry = Outline.build(node, 1)
    assert entry.date is None and entry.to_dict()["date"] is None
    assert entry.url == "https://www.ecfr.gov/current/title-1"


def test_outline_works_with_dataclasses_asdict(outline):
    section = outline["164.512"]
    assert section.parent is outline["E"]
    plain = dataclasses.asdict(section)  # parent isn't a field, so no loop.
    assert plain["citation"] == "45 CFR 164.512"


def version(identifier, kind="section"):
    return models.ContentVersion.model_validate(
        {
            "date": "2024-01-02",
            "amendment_date": "2024-01-02",
            "issue_date": "2024-01-05",
            "identifier": identifier,
            "name": f"§ {identifier} Heading.",
            "part": "164",
            "substantive": True,
            "removed": False,
            "title": "45",
            "type": kind,
        }
    )


def test_change_to_dict():
    change = Change.from_version(version("164.502"))
    data = change.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data == {
        "citation": "45 CFR 164.502",
        "level": "section",
        "identifier": "164.502",
        "heading": "Heading.",
        "date": "2024-01-02",
        "url": "https://www.ecfr.gov/on/2024-01-02/title-45/section-164.502",
        "issued": "2024-01-05",
        "effective": "2024-01-02",
        "kind": "substantive",
    }
    assert change.level is Level.SECTION
    assert change.to_dict(include_raw=True)["raw"]["issue_date"] == "2024-01-05"
    appendix = Change.from_version(version("Appendix A to Part 164", "appendix"))
    assert appendix.url.endswith("/part-164/appendix-Appendix%20A%20to%20Part%20164")
    assert Change.from_version(version("164.1", "odd")).level == "odd"


def result(**hierarchy):
    return models.SearchResult.model_validate(
        {
            "starts_on": "2017-01-01",
            "ends_on": None,
            "type": "Section",
            "hierarchy": {"title": "45", **hierarchy},
            "hierarchy_headings": {},
            "headings": {"section": "Heading"},
            "full_text_excerpt": "the rule.",
            "score": 1.0,
            "structure_index": 1,
            "reserved": False,
            "removed": False,
        }
    )


def test_match_to_dict():
    match = Match.from_result(result(part="164", section="164.514"))
    data = match.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data == {
        "citation": "45 CFR 164.514",
        "level": "section",
        "identifier": "164.514",
        "heading": "Heading",
        "date": "2017-01-01",
        "url": "https://www.ecfr.gov/on/2017-01-01/title-45/section-164.514",
        "excerpt": "the rule.",
        "starts_on": "2017-01-01",
        "ends_on": None,
    }
    assert "raw" in match.to_dict(include_raw=True)
    appendix = Match.from_result(result(appendix="Appendix A to Part 1"))
    assert appendix.level is Level.APPENDIX
    assert appendix.url.endswith("/title-45/appendix-Appendix%20A%20to%20Part%201")
    ended = dataclasses.replace(match, ends_on=dt.date(2020, 1, 1))
    assert ended.to_dict()["ends_on"] == "2020-01-01"


# Levels


def test_levels_are_validated_and_case_insensitive(part):
    assert as_level("Section") is as_level(Level.SECTION) is Level.SECTION
    assert as_level("Subject group") is Level.SUBJECT_GROUP
    assert Level.SECTION == "section"  # StrEnum: string comparisons still work.
    assert next(s.identifier for s in part.iter("SECTION")) == "164.102"
    assert part.find(level="Appendix") is part[1]
    with pytest.raises(InvalidParameterError, match="Did you mean 'section'") as info:
        part.iter("sections")  # Raised at the call, before iterating.
    assert "title, subtitle, chapter" in str(info.value)
    assert isinstance(info.value, ValueError)
    with pytest.raises(InvalidParameterError, match="'sectoin' is not a level"):
        part.find(level="sectoin")  # type: ignore[arg-type]
    with pytest.raises(InvalidParameterError, match=r"'zzz' is not a level\. Use"):
        as_level("zzz")


def test_outline_levels_find_and_ancestors(outline):
    part = outline["164"]
    assert part.level is Level.PART
    assert part[0].level == "hed1" and not isinstance(part[0].level, Level)
    assert [e.identifier for e in outline.iter("Section")] == ["164.502", "164.512"]
    assert [e.level for e in outline.iter("subject_group")] == [Level.SUBJECT_GROUP]
    with pytest.raises(InvalidParameterError, match="Did you mean 'section'"):
        outline.iter("sections")  # type: ignore[arg-type]
    with pytest.raises(InvalidParameterError, match="Outlines stop at sections"):
        outline.iter("paragraph")
    assert outline.find("e") is part["E"]  # Case-insensitive if nothing else matches.
    assert outline.find(level="section") is outline["164.512"]  # Nearest first.
    assert outline.find("164.512", level="section") is outline["164.512"]
    assert outline.find("nope") is None
    assert part.find(level="chapter") is None
    section = outline["164.502"]
    assert [a.citation for a in section.ancestors] == [
        "45 CFR Part 164 Subpart E",  # The subject group, cited as its subpart.
        "45 CFR Part 164 Subpart E",
        "45 CFR Part 164",
        "45 CFR",
    ]
    assert outline.ancestors == () and outline.parent is None


def test_outline_jupyter_display(outline, monkeypatch):
    shown = outline._repr_markdown_()
    assert shown.splitlines()[:3] == [
        "- Title 45—Public Welfare",
        "  - Part 164—Security",
        "    - General heading",
    ]
    assert r"Uses \*without\* \[authorization\]." in shown
    monkeypatch.setattr("ecfr_md.results.REPR_MAX_CHARS", 90)
    capped = outline._repr_markdown_()
    assert len(capped) <= 90
    assert capped.endswith("more characters — use str(outline))\n")


# Docs


@pytest.mark.parametrize(
    "name", ["get", "get_many", "save", "outline", "changes", "search", "sync"]
)
def test_shortcuts_have_their_methods_docs(name):
    function, method = getattr(ecfr_md, name), getattr(ECFR, name)
    assert function.__doc__ == method.__doc__
    assert function.__doc__


def test_signatures_show_the_date_alias():
    assert "date: DateLike | None = None" in str(inspect.signature(ecfr_md.get))


@pytest.mark.parametrize(
    "cls",
    [ecfr_md.Section, ecfr_md.Paragraph, ecfr_md.Level, ecfr_md.Title, ecfr_md.Part],
)
def test_classes_have_their_own_docstrings(cls):
    assert cls.__doc__ and cls.__doc__ != ecfr_md.Division.__doc__
