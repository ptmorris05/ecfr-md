"""Smoke tests against the real eCFR API. Run with: uv run pytest -m network"""

import re

import pytest

from ecfr_md import ECFR, NotFoundError, Settings

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def ecfr(tmp_path_factory):
    settings = Settings(cache_dir=tmp_path_factory.mktemp("live-cache"))
    with ECFR(settings) as client:
        yield client


def test_get_section_and_paragraph(ecfr):
    section = ecfr.get("45 CFR 164.502")
    assert section.to_markdown().startswith("# § 164.502 Uses and disclosures")
    assert [p.identifier for p in section][:3] == ["a", "b", "c"]
    paragraph = ecfr.get("45 CFR 164.512(k)(1)(i)")
    assert paragraph.heading == "Armed Forces personnel"


def test_get_part_navigation_and_history(ecfr):
    part = ecfr.get(45, part="164", date="2020-06-01")
    assert part.date.isoformat() == "2020-06-01"
    assert part["164.502(a)(1)(i)"].text == "**(i)** To the individual;"
    assert sum(1 for _ in part.iter("section")) > 30


def test_every_json_endpoint_parses(ecfr):
    assert ecfr.admin.agencies().agencies
    assert ecfr.admin.corrections(title=45).ecfr_corrections
    assert ecfr.admin.title_corrections(45).ecfr_corrections
    assert ecfr.search.results("minimum necessary", per_page=2).results
    assert ecfr.search.count("minimum necessary").meta.total_count
    assert ecfr.search.summary("minimum necessary").meta.description
    assert ecfr.search.daily_counts("minimum necessary").dates
    assert ecfr.search.title_counts("minimum necessary").titles
    assert ecfr.search.hierarchy_counts("minimum necessary").count.value
    ecfr.search.suggestions("minimum")
    assert ecfr.versioner.titles().titles
    assert ecfr.versioner.ancestry(45, part="164").ancestors
    assert ecfr.versioner.structure(45).children
    assert ecfr.versioner.versions(45, part="164").content_versions


def test_missing_content_is_not_found(ecfr):
    with pytest.raises(NotFoundError, match="No matching content found"):
        ecfr.versioner.full(45, part="999")


def test_models_parse_every_title(ecfr):
    """Contract test: the models accept the structure and corrections of all titles."""
    for title in ecfr.versioner.titles().titles:
        if not title.reserved:
            ecfr.versioner.structure(title.number, date=title.up_to_date_as_of)
            ecfr.admin.title_corrections(title.number)


# Large parts with very different markup: privacy rules, food labels, vehicle
# safety standards, tobacco grades and consumer credit (thousands of lines that
# look like Markdown lists).
CORPUS = [(45, "164"), (21, "101"), (49, "571"), (7, "29"), (12, "1026")]
NOISE = re.compile(r"<su[bp]>|</su[bp]>|<br>|!\[\]\([^)]*\)|[\s*|#>\\`_\-]|&amp;")


def in_order(needle: str, haystack: str) -> bool:
    letters = iter(haystack)
    return all(c in letters for c in needle)


@pytest.mark.parametrize(("title", "part"), CORPUS)
def test_rendering_is_lossless(ecfr, title, part):
    """Every character of every section's source text appears, in order."""
    document = ecfr.get(title, part=part)
    for section in document.iter("section"):
        source = NOISE.sub("", "".join(section.element.itertext()).replace("&", ""))
        rendered = NOISE.sub("", section.to_markdown().replace("&", ""))
        assert in_order(source, rendered), section.citation


@pytest.mark.parametrize(("title", "part"), CORPUS)
def test_every_node_is_found_by_its_own_citation(ecfr, title, part):
    document = ecfr.get(title, part=part)
    for node in document.iter():
        if node.level == "paragraph" and node.section.paragraph_issues:
            continue  # Irregular sections may legitimately be ambiguous.
        if node.level == "subject_group":
            continue  # Unnumbered groupings share their parent's citation.
        assert document[node.citation] is node, node.citation


def test_changes_are_never_issued_before_they_take_effect(ecfr):
    """The cache relies on this: every version is issued on or after its date."""
    versions = ecfr.versioner.versions(45, part="164").content_versions
    assert versions
    early = [v for v in versions if v.issue_date < v.date]
    assert early == [], early[:3]
