# ruff: noqa: E501 - XML reads best a paragraph to a line.
"""A small HIPAA-like title 45 for agent tests: definitions sections that
apply to a subchapter, a part and subparts; a section with paragraphs and
cross-references; an older version of it; changes and search results."""

import xml.etree.ElementTree as ET

import httpx2

LATEST = "2026-10-07"
PAST = "2020-01-01"
VERSIONER = "/api/versioner/v1"
VERSIONS = f"{VERSIONER}/versions/title-45.json"
SEARCH = "/api/search/v1/results"


def _section(number: str, heading: str, size: int = 400, reserved: bool = False):
    return {
        "identifier": number,
        "label": f"§ {number} {heading}",
        "type": "section",
        "size": size,
        "reserved": reserved,
    }


def _subpart(identifier: str, heading: str, *sections: dict) -> dict:
    return {
        "identifier": identifier,
        "label": f"Subpart {identifier}—{heading}",
        "type": "subpart",
        "children": list(sections),
    }


STRUCTURE = {
    "identifier": "45",
    "label": "Title 45—Public Welfare",
    "type": "title",
    "size": 30000,
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
                            "label": "Part 160—General Administrative Requirements",
                            "type": "part",
                            "size": 3000,
                            "children": [
                                _subpart(
                                    "A",
                                    "General Provisions",
                                    _section("160.101", "Statutory basis."),
                                    _section("160.103", "Definitions.", 1200),
                                )
                            ],
                        },
                        {
                            "identifier": "164",
                            "label": "Part 164—Security and Privacy",
                            "type": "part",
                            "size": 12000,
                            "children": [
                                _subpart(
                                    "A",
                                    "General Provisions",
                                    _section("164.102", "Statutory basis."),
                                    _section("164.103", "Definitions."),
                                ),
                                _subpart(
                                    "D",
                                    "Notification in the Case of Breach",
                                    _section("164.402", "Definitions."),
                                    _section("164.404", "Notification to individuals."),
                                ),
                                _subpart(
                                    "E",
                                    "Privacy of Health Information",
                                    _section("164.500", "Applicability."),
                                    _section("164.501", "Definitions."),
                                    _section("164.502", "Uses and disclosures.", 2000),
                                    _section("164.504", "[Reserved]", 0, True),
                                ),
                                {
                                    "identifier": "Appendix A to Part 164",
                                    "label": "Appendix A to Part 164—Matrix",
                                    "type": "appendix",
                                    "size": 300,
                                },
                            ],
                        },
                    ],
                }
            ],
        }
    ],
}

PART_160 = """<DIV5 N="160" TYPE="PART">
<HEAD>PART 160—GENERAL ADMINISTRATIVE REQUIREMENTS</HEAD>
<DIV6 N="A" TYPE="SUBPART"><HEAD>Subpart A—General Provisions</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Statutory basis.</HEAD>
<P>The requirements of this subchapter implement the Act.</P></DIV8>
<DIV8 N="160.103" TYPE="SECTION"><HEAD>§ 160.103 Definitions.</HEAD>
<P>Except as otherwise provided, the following definitions apply to this subchapter:</P>
<P><I>Business associate:</I> (1) Except as provided in paragraph (2) of this definition, a person who acts for a covered entity.</P>
<P>(2) A covered entity may be a business associate of another covered entity.</P>
<P><I>Covered entity</I> means:</P>
<P>(1) A health plan.</P>
<P>(2) A health care clearinghouse.</P>
<P><I>Health plan</I> or <I>plan</I> means an individual or group plan.</P>
</DIV8></DIV6></DIV5>"""

PART_164 = """<DIV5 N="164" TYPE="PART">
<HEAD>PART 164—SECURITY AND PRIVACY</HEAD>
<DIV6 N="A" TYPE="SUBPART"><HEAD>Subpart A—General Provisions</HEAD>
<DIV8 N="164.102" TYPE="SECTION"><HEAD>§ 164.102 Statutory basis.</HEAD>
<P>The provisions of this part are adopted under the Act.</P></DIV8>
<DIV8 N="164.103" TYPE="SECTION"><HEAD>§ 164.103 Definitions.</HEAD>
<P>As used in this part, the following terms have the following meanings:</P>
<P><I>Common control</I> exists if an entity has the power to direct another.</P>
</DIV8></DIV6>
<DIV6 N="D" TYPE="SUBPART"><HEAD>Subpart D—Notification in the Case of Breach</HEAD>
<DIV8 N="164.402" TYPE="SECTION"><HEAD>§ 164.402 Definitions.</HEAD>
<P>As used in this subpart, the following terms have the following meanings:</P>
<P><I>Breach</I> means the acquisition of information in a manner not permitted under subpart E of this part.</P>
<P><I>Disclosure</I> means a disclosure in breach.</P>
</DIV8>
<DIV8 N="164.404" TYPE="SECTION"><HEAD>§ 164.404 Notification to individuals.</HEAD>
<P>(a) <I>Standard.</I> A covered entity shall notify each individual.</P>
<P>(b) <I>Timing.</I> Without unreasonable delay.</P>
</DIV8></DIV6>
<DIV6 N="E" TYPE="SUBPART"><HEAD>Subpart E—Privacy of Health Information</HEAD>
<DIV8 N="164.500" TYPE="SECTION"><HEAD>§ 164.500 Applicability.</HEAD>
<P>(a) Except as otherwise provided, the standards apply to covered entities.</P>
</DIV8>
<DIV8 N="164.501" TYPE="SECTION"><HEAD>§ 164.501 Definitions.</HEAD>
<P>As used in this subpart, the following terms have the following meanings:</P>
<P><I>Disclosure</I> means the release of information outside the entity holding it.</P>
<P><I>Use</I> means the sharing of information within an entity.</P>
</DIV8>
{section_502}
<DIV8 N="164.504" TYPE="SECTION"><HEAD>§ 164.504 [Reserved]</HEAD></DIV8>
</DIV6>
<DIV9 N="Appendix A to Part 164" TYPE="APPENDIX">
<HEAD>Appendix A to Part 164—Matrix</HEAD><P>A table of standards.</P></DIV9>
</DIV5>"""

SECTION_502 = """<DIV8 N="164.502" TYPE="SECTION">
<HEAD>§ 164.502 Uses and disclosures.</HEAD>
<P>(a) <I>Standard.</I> A covered entity may not use or disclose protected health information, except as permitted by this subpart or by subpart C of part 160 of this subchapter.</P>
<P>(1) <I>Permitted uses and disclosures.</I> A covered entity is permitted to use or disclose protected health information as follows:</P>
<P>(i) To the individual;</P>
<P>(ii) For treatment, payment, or health care operations, as permitted by § 164.506; and</P>
<P>(2) <I>Required disclosures.</I> A covered entity is required to disclose protected health information to the Secretary under 42 U.S.C. 1320d.</P>
<P>(b) <I>Standard: Minimum necessary.</I> A covered entity must limit protected health information to the minimum necessary, as described in paragraph (a)(1) of this section and § 160.103.</P>
<CITA TYPE="N">[65 FR 82802, Dec. 28, 2000]</CITA>
</DIV8>"""

SECTION_502_PAST = """<DIV8 N="164.502" TYPE="SECTION">
<HEAD>§ 164.502 Uses and disclosures.</HEAD>
<P>(a) <I>Standard.</I> A covered entity may not use or disclose protected health information, except as permitted by this subpart or by subpart C of part 160 of this subchapter.</P>
<P>(1) <I>Permitted uses and disclosures.</I> A covered entity is permitted to use or disclose protected health information as follows:</P>
<P>(i) To the individual;</P>
<P>(ii) For treatment or payment, as permitted by § 164.506; and</P>
<P>(b) <I>Standard: Minimum necessary.</I> A covered entity must limit protected health information to the minimum necessary, as described in paragraph (a)(1) of this section and § 160.103.</P>
<P>(c) <I>Old rule.</I> A rule since removed, with enough words in it to stand apart from every other paragraph of the section.</P>
</DIV8>"""


def _title(section_502: str) -> ET.Element:
    xml = (
        '<DIV1 N="45" TYPE="TITLE"><HEAD>Title 45—Public Welfare</HEAD>'
        '<DIV2 N="A" TYPE="SUBTITLE"><HEAD>Subtitle A</HEAD>'
        '<DIV4 N="C" TYPE="SUBCHAP"><HEAD>Subchapter C</HEAD>'
        f"{PART_160}{PART_164.replace('{section_502}', section_502)}"
        "</DIV4></DIV2></DIV1>"
    )
    return ET.fromstring(xml)


def full_text(section_502: str):
    """A full-text endpoint: the requested part, section or appendix."""
    root = _title(section_502)

    def respond(request: httpx2.Request) -> httpx2.Response:
        params = request.url.params
        scope = root
        if "part" in params:
            scope = next(d for d in root.iter() if d.get("N") == params["part"])
        for key in ("section", "appendix", "subpart"):
            if key in params:
                wanted = params[key]
                found = next((d for d in scope.iter() if d.get("N") == wanted), None)
                if found is None:
                    return httpx2.Response(404, json={"error": "No content."})
                return httpx2.Response(200, content=ET.tostring(found))
        return httpx2.Response(200, content=ET.tostring(scope))

    return respond


TITLES = {
    "titles": [
        {
            "number": 45,
            "name": "Public Welfare",
            "latest_amended_on": "2026-01-01",
            "latest_issue_date": "2026-01-01",
            "up_to_date_as_of": LATEST,
            "reserved": False,
        },
        {"number": 35, "name": "[Reserved]", "reserved": True},
    ],
    "meta": {"date": LATEST, "import_in_progress": False},
}


def version(section: str, date: str, substantive: bool = True, **extra) -> dict:
    return {
        "date": date,
        "amendment_date": date,
        "issue_date": date,
        "identifier": section,
        "name": f"§ {section} Uses and disclosures.",
        "part": section.split(".")[0],
        "substantive": substantive,
        "removed": False,
        "subpart": "E",
        "title": "45",
        "type": "section",
    } | extra


VERSION_LIST = {
    "content_versions": [
        version("164.502", "2024-06-25"),
        version("164.502", "2016-12-30", substantive=False),
        version("164.501", "2024-06-25"),
    ],
    "meta": {"total_count": 3},
}


def hit(section: str, excerpt: str, title: str = "45") -> dict:
    h = {"title": title, "part": section.split(".")[0], "section": section}
    return {
        "starts_on": "2017-01-01",
        "ends_on": None,
        "type": "Section",
        "hierarchy": h,
        "hierarchy_headings": h,
        "headings": {"section": f"§ {section} Heading."},
        "full_text_excerpt": excerpt,
        "score": 1.0,
        "structure_index": 1,
        "reserved": False,
        "removed": False,
    }


SEARCH_RESULTS = {
    "results": [
        hit("164.502", "must limit to the <strong>minimum necessary</strong>"),
        hit("164.404", "covered entity shall notify"),
    ],
    "meta": {"total_count": 2, "total_pages": 1},
}


def install(api) -> None:
    """Point a FakeAPI at this title."""
    api.json(f"{VERSIONER}/titles.json", TITLES)
    for date in (LATEST, PAST, "2020-10-01", "2024-06-24", "2024-06-25"):
        api.json(f"{VERSIONER}/structure/{date}/title-45.json", STRUCTURE)
    api.routes[f"{VERSIONER}/full/{LATEST}/title-45.xml"] = full_text(SECTION_502)
    for date in (PAST, "2020-10-01", "2024-06-24"):
        api.routes[f"{VERSIONER}/full/{date}/title-45.xml"] = full_text(
            SECTION_502_PAST
        )
    api.routes[f"{VERSIONER}/full/2024-06-25/title-45.xml"] = full_text(SECTION_502)
    api.json(VERSIONS, VERSION_LIST)
    api.json(SEARCH, SEARCH_RESULTS)
