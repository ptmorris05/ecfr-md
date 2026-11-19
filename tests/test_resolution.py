"""Finding what a citation names: ambiguity, completion, divisions, not found."""

import datetime as dt
import json
import xml.etree.ElementTree as ET

import httpx2
import pytest

from ecfr_md import ECFR, client
from ecfr_md.errors import (
    AmbiguousNodeError,
    ECFRError,
    InvalidParameterError,
    NodeNotFoundError,
)

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
STRUCTURE = "/api/versioner/v1/structure/2026-10-07/title-45.json"


def node(kind: str, n: str, *children: dict) -> dict:
    return {"identifier": n, "label": f"{kind} {n}", "type": kind, "children": list(children)}  # fmt: skip  # noqa: E501


def part(n: str, *children: dict) -> dict:
    return node("part", n, *children)


def subpart(n: str) -> dict:
    return node("subpart", n)


def section(n: str) -> dict:
    return node("section", n)


# Title 45 with two subtitles; Subpart E is in two parts, and Subchapter C has
# no chapter above it, like the real title.
TITLE = node(
    "title",
    "45",
    node(
        "subtitle",
        "A",
        node("subchapter", "C", part("164", subpart("E"), section("164.502"))),
        part("5", subpart("E")),
    ),
    node("subtitle", "B", part("1600")),
) | {"size": 1000}

TITLE_XML = """<?xml version="1.0"?>
<DIV1 N="45" TYPE="TITLE"><HEAD>Title 45</HEAD>
<DIV2 N="A" TYPE="SUBTITLE"><HEAD>Subtitle A</HEAD>
<DIV4 N="C" TYPE="SUBCHAP"><HEAD>SUBCHAPTER C—DATA</HEAD>
<DIV5 N="164" TYPE="PART"><HEAD>PART 164—PRIVACY</HEAD>
<DIV8 N="164.502" TYPE="SECTION"><HEAD>§ 164.502 Uses.</HEAD><P>(a) Rule.</P></DIV8>
</DIV5></DIV4>
<DIV5 N="5" TYPE="PART"><HEAD>PART 5—FOIA</HEAD></DIV5>
</DIV2>
<DIV2 N="B" TYPE="SUBTITLE"><HEAD>Subtitle B</HEAD>
<DIV5 N="1600" TYPE="PART"><HEAD>PART 1600</HEAD></DIV5>
</DIV2>
</DIV1>
"""


def title_text(request: httpx2.Request) -> httpx2.Response:
    """Like the real endpoint: a section if asked for one, else the whole title."""
    section = request.url.params.get("section")
    if section is None:
        return httpx2.Response(200, text=TITLE_XML)
    found = next(
        (d for d in ET.fromstring(TITLE_XML).iter() if d.get("N") == section), None
    )
    if found is None:
        return httpx2.Response(404, json={"error": "No matching content found."})
    return httpx2.Response(200, content=ET.tostring(found, encoding="utf-8"))


@pytest.fixture
def titled(api):
    api.json(STRUCTURE, TITLE)
    api.routes[FULL] = title_text
    return api


def test_a_subpart_in_several_parts_is_ambiguous(ecfr, titled):
    with pytest.raises(AmbiguousNodeError) as caught:
        ecfr.get("45 CFR Subpart E")
    assert [c.citation for c in caught.value.candidates] == [
        "45 CFR Part 164 Subpart E",
        "45 CFR Part 5 Subpart E",
    ]
    assert "Say which, e.g. 45 CFR Part 164 Subpart E" in str(caught.value)
    assert FULL not in titled.paths()  # Nothing fetched for a guess.


def test_long_candidate_lists_are_shortened(ecfr, api):
    many = dict(TITLE, children=[part(str(n), subpart("E")) for n in range(12)])
    api.json(STRUCTURE, many)
    with pytest.raises(AmbiguousNodeError, match=r"and 2 more$"):
        ecfr.outline("45 CFR Subpart E")


def test_a_division_comes_out_of_the_whole_title(ecfr, titled):
    node = ecfr.get("45 CFR Subtitle A Subchapter C")
    assert node.heading == "SUBCHAPTER C—DATA"
    assert [s.citation for s in node.iter("section")] == ["45 CFR 164.502"]
    # The API ignores subchapter, so the title is asked for once, unfiltered.
    assert [r.url.query for r in titled.requests if r.url.path == FULL] == [b""]


def test_a_division_missing_from_the_text(ecfr, titled):
    titled.text(FULL, TITLE_XML.replace('N="C" TYPE="SUBCHAP"', 'N="D" TYPE="SUBCHAP"'))
    with pytest.raises(NodeNotFoundError, match="not in the text of title 45"):
        ecfr.get("45 CFR Subtitle A Subchapter C")


def test_a_division_of_a_very_large_title_is_fetched_by_part(ecfr, titled, monkeypatch):
    monkeypatch.setattr(client, "SPLIT_ABOVE", 500)
    titled.routes[FULL] = lambda request: httpx2.Response(
        200,
        text=f"<DIV5 N='{request.url.params['part']}' TYPE='PART'><HEAD/></DIV5>",
    )
    node = ecfr.get("45 CFR Subtitle B")
    assert [p.identifier for p in node] == ["1600"]


def test_a_subpart_named_with_its_part_needs_no_guess(ecfr, titled):
    titled.text(FULL, "<DIV6 N='E' TYPE='SUBPART'><HEAD>Subpart E</HEAD></DIV6>")
    node = ecfr.get("45 CFR Part 164 Subpart E")
    sent = next(r for r in titled.requests if r.url.path == FULL).url.params
    assert dict(sent) == {
        "part": "164",
        "subpart": "E",
        "subtitle": "A",
        "subchapter": "C",
    }
    assert node.citation == "45 CFR Part 164 Subpart E"


@pytest.mark.parametrize(
    ("citation", "message"),
    [
        ("45 CFR Part 9", "No part 9 in title 45. It has parts 164, 5, 1600"),
        (
            "45 CFR Part 164 Subpart Z",
            "No subpart Z in 45 CFR Part 164. It has subparts E",
        ),
        ("45 CFR Part 1600 Subpart A", "^No subpart A in 45 CFR Part 1600$"),
        (
            "45 CFR 164.999",
            "No section 164.999 in 45 CFR Part 164. It has sections 164.502",
        ),
        ("45 CFR 1600.1", "^No section 1600.1 in 45 CFR Part 1600$"),
        ("45 CFR 7.1", "^No section 7.1 in title 45$"),
    ],
)
def test_missing_levels_say_what_is_there(ecfr, titled, citation, message):
    with pytest.raises(NodeNotFoundError, match=message):
        ecfr.outline(citation)


def test_a_section_cited_under_the_wrong_part(ecfr, titled):
    with pytest.raises(LookupError, match=r"section 164\.502 is in 45 CFR Part 164"):
        ecfr.outline("45 CFR 164.502", part="5")


def test_long_lists_of_what_is_there_give_a_range(ecfr, api):
    api.json(STRUCTURE, dict(TITLE, children=[part(str(n)) for n in range(1, 21)]))
    with pytest.raises(NodeNotFoundError, match="It has parts 1 to 20 \\(20 in all\\)"):
        ecfr.outline("45 CFR Part 99")


def test_a_404_for_something_the_structure_lists_is_not_rewritten(ecfr, titled):
    titled.text(FULL, '{"error":"No matching content found."}', status=404)
    with pytest.raises(ECFRError, match="404"):
        ecfr.get("45 CFR 164.502")


def test_future_dates_use_the_latest_text(ecfr, titled):
    with pytest.warns(UserWarning, match="using 2026-10-07 instead of 2030-01-01"):
        node = ecfr.get("45 CFR 164.502", date="2030-01-01")
    assert node.date == dt.date(2026, 10, 7)


def test_outline_explains_dates_before_the_first_version(ecfr, api):
    api.text("/api/versioner/v1/structure/2010-01-01/title-45.json", "null", status=404)
    api.json(
        "/api/versioner/v1/versions/title-45.json",
        {"content_versions": [{
            "date": "2017-01-03", "amendment_date": "2017-01-03",
            "issue_date": "2017-01-03", "identifier": "164.502", "name": "x",
            "part": "164", "substantive": True, "removed": False,
            "title": "45", "type": "section",
        }]},
    )  # fmt: skip
    with pytest.raises(InvalidParameterError, match="earliest version"):
        ecfr.outline("45 CFR 164.502", date="2010-01-01")


def test_outline_404_without_a_date_reason(ecfr, api):
    api.text("/api/versioner/v1/structure/2026-10-07/title-45.json", "null", status=404)
    with pytest.raises(ECFRError, match="404"):
        ecfr.outline(45)


def test_get_many_can_return_errors_in_place(ecfr, titled):
    results = ecfr.get_many(["45 CFR 164.502", "45 CFR 164.999"], errors="return")
    assert results[0].citation == "45 CFR 164.502"
    assert isinstance(results[1], NodeNotFoundError)
    with pytest.raises(NodeNotFoundError):
        ecfr.get_many(["45 CFR 164.502", "45 CFR 164.999"])


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda e: e.get(45, prt="164"), "no option 'prt'; did you mean part\\?"),
        (lambda e: e.search("x", zzz=1), "no option 'zzz'\\. Options: agency_slugs"),
        (
            lambda e: e.get(None),
            "citation should be a string or a whole number; got None",
        ),
        (lambda e: e.search("x", limit=0), "limit: input should be greater than"),
    ],
)
def test_arguments_are_checked_in_plain_words(ecfr, api, call, message):
    with pytest.raises(InvalidParameterError, match=message):
        call(ecfr)
    assert api.requests == []


def test_whole_numbers_are_accepted_for_levels(ecfr, titled):
    assert ecfr.outline(45, part=164).citation == "45 CFR Part 164"


def test_explain_handles_unnamed_locations():
    from pydantic import TypeAdapter, ValidationError

    try:
        TypeAdapter(int).validate_python("x")
    except ValidationError as exc:
        assert client._explain(exc, []) == (
            "argument: input should be a valid integer, unable to parse string "
            "as an integer; got 'x'"
        )


def test_ambiguity_is_shown_as_a_table(run, api, capsys):
    api.json(STRUCTURE, TITLE)
    assert run("outline", "45", "CFR", "Subpart", "E") == 1
    err = capsys.readouterr().err
    assert "More than one match" in err and "45 CFR Part 5 Subpart E" in err


def test_structure_round_trips_as_json():
    assert json.loads(json.dumps(TITLE))["children"][0]["identifier"] == "A"


def test_closed_client_is_an_ecfr_error(api, settings):
    ecfr = ECFR(settings, transport=httpx2.MockTransport(api))
    ecfr.close()
    with pytest.raises(ECFRError, match="closed"):
        ecfr.get(45)


def test_a_trailing_slash_means_a_new_directory(ecfr, titled, tmp_path):
    saved = ecfr.save("45 CFR 164.502", f"{tmp_path}/new/")
    assert saved.parent == tmp_path / "new" and saved.suffix == ".md"
