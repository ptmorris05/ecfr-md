"""A small CFR for the web navigator and definitions tests: title 45 with a
subchapter, a definitions section for it, a part of text with its own
definitions, a part too large to show whole, and two dates of text."""

import copy
import xml.etree.ElementTree as ET

import httpx2
from conftest import PART_XML, TITLES, FakeAPI

LATEST = "2026-10-07"
EARLIER = "2020-01-01"
FULL = f"/api/versioner/v1/full/{LATEST}/title-45.xml"
FULL_EARLIER = f"/api/versioner/v1/full/{EARLIER}/title-45.xml"
VERSIONS = "/api/versioner/v1/versions/title-45.json"
RESULTS = "/api/search/v1/results"
APPENDIX = "Appendix A to Subpart C of Part 164"


def _section(number: str, heading: str = "Heading.", reserved: bool = False) -> dict:
    return {
        "identifier": number,
        "label": f"§ {number} {'[Reserved]' if reserved else heading}",
        "type": "section",
        "reserved": reserved,
    }


STRUCTURE = {
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
                    "identifier": "C",
                    "label": "Subchapter C—Administrative Data Standards",
                    "type": "subchapter",
                    "children": [
                        {
                            "identifier": "160",
                            "label": "Part 160—General",
                            "type": "part",
                            "size": 900,
                            "children": [
                                _section("160.101", "Purpose."),
                                _section("160.103", "Definitions."),
                            ],
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
                                        _section("164.102"),
                                        _section("164.104"),
                                        _section("164.106"),
                                        _section("164.108", "Definitions."),
                                    ],
                                },
                                {
                                    "identifier": APPENDIX,
                                    "label": f"{APPENDIX}—Matrix",
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
                        {"label": "Other parts", "type": "hed1"},
                        {
                            "identifier": "170",
                            "label": "Part 170—Large",
                            "type": "part",
                            "size": 3_000_000,
                            "children": [_section("170.1", "Scope.")],
                        },
                    ],
                },
            ],
        }
    ],
}

PART_160 = """<DIV5 N="160" TYPE="PART"><HEAD>PART 160—GENERAL</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Purpose.</HEAD>
<P>A <I>covered entity</I> must follow § 164.102 and 42 U.S.C. 1320d.</P></DIV8>
<DIV8 N="160.103" TYPE="SECTION"><HEAD>§ 160.103 Definitions.</HEAD>
<P>The following definitions apply to this subchapter:</P>
<P><I>Covered entity</I> means a health plan.</P>
<P><I>Standard</I> means a rule.</P>
<P>(1) Its first part.</P>
</DIV8></DIV5>"""

PART_170 = """<DIV5 N="170" TYPE="PART"><HEAD>PART 170—LARGE</HEAD>
<DIV8 N="170.1" TYPE="SECTION"><HEAD>§ 170.1 Scope.</HEAD><P>Text.</P></DIV8></DIV5>"""

# Text of part 164 as it was: one paragraph said otherwise; one was added since.
PART_EARLIER = PART_XML.replace("comply.", "obey.").replace("<P>(h) Letter h.</P>", "")


def _pick(xml: str, request: httpx2.Request) -> httpx2.Response:
    params = request.url.params
    root = ET.fromstring(xml.split("?>", 1)[-1])
    wanted = params.get("section") or params.get("appendix") or params.get("subpart")
    if wanted is None:
        return httpx2.Response(200, text=xml)
    found = next((d for d in root.iter() if d.get("N") == wanted), None)
    if found is None:
        return httpx2.Response(404, json={"error": "No matching content found."})
    return httpx2.Response(200, content=ET.tostring(found, encoding="utf-8"))


def _full(part_164: str):
    def respond(request: httpx2.Request) -> httpx2.Response:
        part = request.url.params.get("part")
        section = request.url.params.get("section", "")
        if part == "160" or section.startswith("160."):
            return _pick(PART_160, request)
        if part == "170" or section.startswith("170."):
            return _pick(PART_170, request)
        if part == "164" or section.startswith("164.") or request.url.params:
            return _pick(part_164, request)
        body = part_164.split("?>", 1)[1]
        title = (
            '<DIV1 N="45" TYPE="TITLE"><DIV2 N="A" TYPE="SUBTITLE">'
            f'<DIV4 N="C" TYPE="SUBCHAP">{PART_160}{body}{PART_170}</DIV4>'
            "</DIV2></DIV1>"
        )
        return httpx2.Response(200, text=title)

    return respond


def version(identifier: str, date: str, removed: bool = False) -> dict:
    return {
        "date": date,
        "amendment_date": date,
        "issue_date": date,
        "identifier": identifier,
        "name": f"§ {identifier} Statutory basis.",
        "part": identifier.split(".")[0],
        "substantive": True,
        "removed": removed,
        "title": "45",
        "type": "section",
    }


def result(section: str, excerpt: str = "the rule must comply.") -> dict:
    h = {"title": "45", "part": "164", "section": section}
    return {
        "starts_on": "2017-01-01",
        "ends_on": None,
        "type": "Section",
        "hierarchy": h,
        "hierarchy_headings": h,
        "headings": {"section": f"§ {section} Statutory basis."},
        "full_text_excerpt": excerpt,
        "score": 1.0,
        "structure_index": 1,
        "reserved": False,
        "removed": False,
    }


def install(api: FakeAPI) -> FakeAPI:
    """Routes for the small CFR on a fake API."""
    titles = copy.deepcopy(TITLES)
    api.json("/api/versioner/v1/titles.json", titles)
    for date in (LATEST, EARLIER):
        api.json(f"/api/versioner/v1/structure/{date}/title-45.json", STRUCTURE)
    api.text("/api/versioner/v1/structure/2015-01-01/title-45.json", "null", 404)
    api.routes[FULL] = _full(PART_XML)
    api.routes[FULL_EARLIER] = _full(PART_EARLIER)
    api.json(
        VERSIONS,
        {
            "content_versions": [
                version("164.102", "2026-01-01"),
                version("164.102", EARLIER),
                version("164.106", "2018-01-01", removed=True),
            ],
            "meta": {},
        },
    )
    api.json(
        RESULTS,
        {"results": [result("164.102")], "meta": {"total_count": 1, "total_pages": 1}},
    )
    return api
