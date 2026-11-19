"""Moving around the CFR: outlines and text that know what's next to them."""

import copy
import xml.etree.ElementTree as ET

import httpx2
import pytest
from conftest import PART_XML, TITLES

from ecfr_md import ECFRError, InvalidParameterError, Outline, load
from ecfr_md.results import CFR_LEVEL

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
FULL_46 = "/api/versioner/v1/full/2026-09-01/title-46.xml"


def _section(number: str, reserved: bool = False) -> dict:
    label = f"§ {number} {'[Reserved]' if reserved else 'Heading.'}"
    return {
        "identifier": number,
        "label": label,
        "type": "section",
        "reserved": reserved,
    }


STRUCTURE_45 = {
    "identifier": "45",
    "label": "Title 45—Public Welfare",
    "type": "title",
    "size": 9000,
    "children": [
        {
            "identifier": "A",
            "label": "Subtitle A—Department of Health and Human Services",
            "type": "subtitle",
            "children": [
                {
                    "identifier": "160",
                    "label": "Part 160—General",
                    "type": "part",
                    "size": 900,
                    "children": [_section("160.101"), _section("160.102", True)],
                },
                {
                    "identifier": "164",
                    "label": "Part 164—Security and Privacy",
                    "type": "part",
                    "size": 5000,
                    "children": [
                        {
                            "identifier": "A",
                            "label": "Subpart A—General Provisions",
                            "type": "subpart",
                            "children": [
                                _section(n)
                                for n in ("164.102", "164.104", "164.106", "164.108")
                            ],
                        },
                        {
                            "identifier": "Appendix A to Subpart C of Part 164",
                            "label": "Appendix A to Subpart C of Part 164—Matrix",
                            "type": "appendix",
                        },
                    ],
                },
                {
                    "identifier": "165",
                    "label": "Part 165 [Reserved]",
                    "type": "part",
                    "reserved": True,
                },
            ],
        }
    ],
}
STRUCTURE_46 = {
    "identifier": "46",
    "label": "Title 46—Shipping",
    "type": "title",
    "size": 500,
    "children": [
        {
            "identifier": "1",
            "label": "Part 1—Organization",
            "type": "part",
            "size": 500,
            "children": [_section("1.01")],
        }
    ],
}
PART_160 = """<DIV5 N="160" TYPE="PART"><HEAD>PART 160—GENERAL</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Purpose.</HEAD>
<P>See § 164.102 and paragraph (b) of this section.</P></DIV8></DIV5>"""
PART_1 = """<DIV5 N="1" TYPE="PART"><HEAD>PART 1—ORGANIZATION</HEAD>
<DIV8 N="1.01" TYPE="SECTION"><HEAD>§ 1.01 Purpose.</HEAD><P>Text.</P></DIV8></DIV5>"""


def _titles() -> dict:
    titles = copy.deepcopy(TITLES)
    titles["titles"].insert(
        1,
        {
            "number": 46,
            "name": "Shipping",
            "latest_amended_on": "2026-01-01",
            "latest_issue_date": "2026-01-01",
            "up_to_date_as_of": "2026-09-01",
            "reserved": False,
        },
    )
    return titles


def _full_45(request: httpx2.Request) -> httpx2.Response:
    params = request.url.params
    if params.get("part") == "160":
        return httpx2.Response(200, text=PART_160)
    if "section" in params:
        root = ET.fromstring(PART_XML)
        found = next(d for d in root.iter() if d.get("N") == params["section"])
        return httpx2.Response(200, content=ET.tostring(found, encoding="utf-8"))
    if params.get("part") == "164":
        return httpx2.Response(200, text=PART_XML)
    body = PART_XML.split("?>", 1)[1]
    title = (
        f'<DIV1 N="45" TYPE="TITLE"><DIV2 N="A" TYPE="SUBTITLE">{body}</DIV2></DIV1>'
    )
    return httpx2.Response(200, text=title)


@pytest.fixture
def api(api):
    api.json("/api/versioner/v1/titles.json", _titles())
    api.json("/api/versioner/v1/structure/2026-10-07/title-45.json", STRUCTURE_45)
    api.json("/api/versioner/v1/structure/2026-09-01/title-46.json", STRUCTURE_46)
    api.routes[FULL] = _full_45
    api.text(FULL_46, PART_1)
    return api


def test_whole_cfr_outline_opens_titles_when_used(ecfr, api):
    cfr = ecfr.outline()
    assert cfr.level == CFR_LEVEL
    assert [t.citation for t in cfr] == ["45 CFR", "46 CFR", "35 CFR"]
    assert not any("/structure/" in p for p in api.paths())
    assert str(cfr).splitlines()[1] == "  Title 45—Public Welfare"
    assert cfr["45"]["Part 164"].citation == "45 CFR Part 164"
    assert cfr["45 CFR 164.104"].address == "45/164.104"
    assert cfr["35"].children == ()
    assert ecfr.outline() is cfr  # Kept for the session.
    with pytest.raises(ECFRError, match="Title 7 does not exist"):
        cfr["7"]
    with pytest.raises(InvalidParameterError, match="too large"):
        cfr.read()
    with pytest.raises(InvalidParameterError, match="Give a title"):
        ecfr.outline(part="164")


def test_next_and_previous_cross_parts_and_titles(ecfr):
    entry = ecfr.outline("45 CFR 160.101")
    assert entry.previous is None
    assert entry.next.citation == "45 CFR 164.102"  # Skips reserved 160.102.
    last = ecfr.outline("45 CFR 164.108")
    assert last.next.citation == "45 CFR Appendix A to Subpart C of Part 164"
    appendix = last.next
    assert appendix.next.citation == "46 CFR 1.01"  # Past reserved part 165.
    assert appendix.next.next is None
    assert appendix.next.previous is appendix
    part = ecfr.outline("45 CFR Part 164")
    assert part.next.citation == "46 CFR 1.01"
    assert part.previous.citation == "45 CFR 160.101"
    inside = part["164.104"]
    assert inside.next is part["164.106"]  # Within the outline, the same entries.


def test_up_and_siblings(ecfr):
    part = ecfr.outline("45 CFR Part 164")
    assert part.parent is None
    assert part.up.citation == "45 CFR Subtitle A"
    assert [s.citation for s in part.siblings] == [
        "45 CFR Part 160",
        "45 CFR Part 165",
    ]
    section = part["164.104"]
    assert section.up is section.parent
    assert len(section.siblings) == 3
    assert ecfr.outline().up is None
    assert ecfr.outline().siblings == ()


def test_reading_from_the_outline(ecfr, api):
    section = ecfr.outline("45 CFR 164.104").read()
    assert section.citation == "45 CFR 164.104"
    assert section.parent.citation == "45 CFR Part 164 Subpart A"  # Whole part.
    assert dict(api.requests[-1].url.params) == {"part": "164"}


def test_detached_outlines_say_why_they_cannot_move(ecfr):
    entry = Outline.build(ecfr._outline(45, ecfr._when(45, None)), 45)
    assert entry.next is None
    assert entry.up is None
    with pytest.raises(ECFRError, match="wasn't loaded by a client"):
        entry.read()


def test_text_moves_without_requests_inside_its_part(ecfr, api):
    section = ecfr.get("45 CFR 164.104")
    fetched = api.paths().count(FULL)
    following = section.next
    assert following.citation == "45 CFR 164.106"
    assert following.parent is section.parent
    assert section.previous.citation == "45 CFR 164.102"
    assert section.up.citation == "45 CFR Part 164 Subpart A"
    assert api.paths().count(FULL) == fetched


def test_text_moves_across_parts_and_titles(ecfr, api):
    section = ecfr.get("45 CFR 164.102")
    assert section.previous.citation == "45 CFR 160.101"
    appendix = ecfr.get("45 CFR 164.108").next
    assert appendix.citation == "45 CFR Appendix A to Subpart C of Part 164"
    assert appendix.next.citation == "46 CFR 1.01"
    part = ecfr.get("45 CFR 164.108").ancestors[-1]
    assert part.up.citation == "45 CFR Subtitle A"
    paragraph = ecfr.get("45 CFR 164.104(a)(1)")
    assert paragraph.outline.citation == "45 CFR 164.104"
    assert paragraph.next.citation == "45 CFR 164.106"


def test_last_text_has_no_next(ecfr):
    assert ecfr.get("46 CFR 1.01").next is None


def test_loaded_text_needs_a_client_to_move():
    node = load(PART_XML, title=45)
    with pytest.raises(ECFRError, match="loaded without a client"):
        _ = node["164.104"].next


def test_anchors_match_the_ecfr(ecfr):
    part = load(PART_XML, title=45)
    assert part.anchor == "part-164"
    assert part["A"].anchor == "subpart-A"
    assert part["164.104"].anchor == "164.104"
    assert part["164.104(a)(2)(i)"].anchor == "p-164.104(a)(2)(i)"
    appendix = part["Appendix A to Subpart C of Part 164"]
    assert appendix.anchor == "Appendix-A-to-Subpart-C-of-Part-164"
    assert load("<DIV1 N='45' TYPE='TITLE'/>", title=45).anchor is None
