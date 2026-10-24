import functools
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
import pytest

from ecfr_md import ECFR, Settings, cli

TITLES = {
    "titles": [
        {
            "number": 45,
            "name": "Public Welfare",
            "latest_amended_on": "2026-01-01",
            "latest_issue_date": "2026-01-01",
            "up_to_date_as_of": "2026-10-07",
            "reserved": False,
        },
        {"number": 35, "name": "[Reserved]", "reserved": True},
    ],
    "meta": {"date": "2026-10-08", "import_in_progress": False},
}

# A small title outline: below the size at which text is fetched part by part.
STRUCTURE = {
    "identifier": "45",
    "label": "Title 45—Public Welfare",
    "type": "title",
    "size": 1000,
    "children": [
        {
            "identifier": "A",
            "label": "Subtitle A—Department of Health and Human Services",
            "type": "subtitle",
            "children": [
                {
                    "identifier": "164",
                    "label": "Part 164",
                    "type": "part",
                    "size": 5000,
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

PART_XML = """<?xml version="1.0"?>
<DIV5 N="164" TYPE="PART">
<HEAD>PART 164—SECURITY AND PRIVACY</HEAD>
<AUTH><HED>Authority:</HED><PSPACE>42 U.S.C. 1320d.</PSPACE></AUTH>
<DIV6 N="A" TYPE="SUBPART">
<HEAD>Subpart A—General Provisions</HEAD>
<DIV8 N="164.102" TYPE="SECTION">
<HEAD>§ 164.102 Statutory basis.</HEAD>
<P>(a)(1) <I>Standard.</I> A covered entity <E T="03">must</E> comply.</P>
<P>(i) See the <E T="04">Federal Register</E><SU>1</SU> * * *</P>
<EXTRACT><P>“THIS NOTICE DESCRIBES HOW MEDICAL INFORMATION MAY BE USED.”</P></EXTRACT>
<CITA TYPE="N">[78 FR 5692, Jan. 25, 2013]</CITA>
</DIV8>
<DIV8 N="164.104" TYPE="SECTION">
<HEAD>§ 164.104 Paragraph nesting.</HEAD>
<P>Introductory text belongs to no paragraph.</P>
<P>(a) <I>Standard: Run in</I>—(1) <I>First.</I> One.</P>
<P>(2) Two.</P>
<P>(i) Roman one.</P>
<P>(ii) Roman two.</P>
<P>(A) Upper.</P>
<P>(<I>1</I>) Italic one.</P>
<P>(b)-(g) [Reserved]</P>
<P>(h) Letter h.</P>
<P>(i) Letter i, not roman one.</P>
<P>(j) <I>Heading.</I> (1) Run in after a heading.</P>
<CITA TYPE="N">[80 FR 1, Jan. 1, 2015]</CITA>
</DIV8>
<DIV8 N="164.106" TYPE="SECTION">
<HEAD>§ 164.106 Examples and rare markup.</HEAD>
<P>(a) <I>Rule.</I> The rule.</P>
<P>(b) <I>Examples.</I> The following examples illustrate paragraph (a):</P>
<P><I>Example 1.</I> (i) Facts. A plan.</P>
<P>(ii) Conclusion. It complies.</P>
<P><I>Example 2.</I> (i) Facts. Another plan.</P>
<P>(c) <I>Other markup.</I> Applies from 2020.</P>
<STARS/>
<LDRWK><FL-2>Dividends</FL-2><LDRFIG>$10,000</LDRFIG></LDRWK>
<SCOL2><LI>Asbestos.</LI><LI>Bauxite.</LI></SCOL2>
<FP-2>1. Loans for equipment</FP-2>
<FP-2>- not a list</FP-2>
<P>Values below 5 &lt;Q&gt; and AT&amp;T&amp;copy;</P>
</DIV8>
<DIV8 N="164.108" TYPE="SECTION">
<HEAD>§ 164.108 Definitions.</HEAD>
<P><I>Term A</I> means:</P>
<P>(1) One.</P>
<P>(2) Two.</P>
<P><I>Term B</I> means:</P>
<P>(1) Uno.</P>
</DIV8>
</DIV6>
<DIV9 N="Appendix A to Subpart C of Part 164" TYPE="APPENDIX">
<HEAD>Appendix A to Subpart C of Part 164—Matrix</HEAD>
<DIV><TABLE>
<THEAD><TR><TH>Standards</TH><TH>Sections</TH></TR></THEAD>
<TBODY>
<TR><TD colspan="2"><strong>Administrative Safeguards</strong></TD></TR>
<TR><TD>Access | Control</TD><TD>164.312(a)(1)</TD></TR>
</TBODY>
</TABLE></DIV>
</DIV9>
</DIV5>
"""


@dataclass
class FakeAPI:
    """Routes request paths to canned responses and records every request."""

    routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = field(
        default_factory=dict
    )
    requests: list[httpx2.Request] = field(default_factory=list)

    def json(self, path: str, body: object, status: int = 200) -> None:
        self.routes[path] = lambda _: httpx2.Response(status, json=body)

    def text(self, path: str, body: str, status: int = 200, **headers: str) -> None:
        self.routes[path] = lambda _: httpx2.Response(
            status, text=body, headers=headers
        )

    def sequence(self, path: str, *responses: httpx2.Response) -> None:
        pending = list(responses)
        self.routes[path] = lambda _: pending.pop(0)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None and "/structure/" in request.url.path:
            return httpx2.Response(200, json=STRUCTURE)
        if route is None:
            return httpx2.Response(404, text="null")
        return route(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def full_text(request: httpx2.Request) -> httpx2.Response:
    """Like the real endpoint: the requested section, or the whole part."""
    section = request.url.params.get("section")
    if section is None:
        return httpx2.Response(200, text=PART_XML)
    root = ET.fromstring(PART_XML)
    found = next((d for d in root.iter() if d.get("N") == section), None)
    if found is None:
        return httpx2.Response(404, json={"error": "No matching content found."})
    return httpx2.Response(200, content=ET.tostring(found, encoding="utf-8"))


@pytest.fixture
def api() -> FakeAPI:
    fake = FakeAPI()
    fake.json("/api/versioner/v1/titles.json", TITLES)
    fake.routes["/api/versioner/v1/full/2026-10-07/title-45.xml"] = full_text
    return fake


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Never touch the user's cache: anything defaulting to it uses a temp dir."""
    directory = tmp_path_factory.mktemp("cache")
    monkeypatch.setattr("ecfr_md.client.default_directory", lambda: directory)
    return directory


@pytest.fixture
def settings() -> Settings:
    return Settings(backoff_initial=0, backoff_max=0, max_attempts=3, cache=False)


@pytest.fixture
def ecfr(api: FakeAPI, settings: Settings):
    with ECFR(settings, transport=httpx2.MockTransport(api)) as client:
        yield client


@pytest.fixture
def part_xml() -> str:
    return PART_XML


@pytest.fixture
def run(api, monkeypatch):
    """Run the CLI against the fake API and return its exit code."""
    transport = httpx2.MockTransport(api)
    monkeypatch.setattr(cli, "ECFR", functools.partial(ECFR, transport=transport))

    def invoke(*tokens: str) -> int:
        return cli.run(tokens, result_action="return_value")

    return invoke
