from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2
import pytest

from cfr_md import ECFR, Settings

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
        if route is None:
            return httpx2.Response(404, text="null")
        return route(request)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


@pytest.fixture
def api() -> FakeAPI:
    fake = FakeAPI()
    fake.json("/api/versioner/v1/titles.json", TITLES)
    fake.text("/api/versioner/v1/full/2026-10-07/title-45.xml", PART_XML)
    return fake


@pytest.fixture
def settings() -> Settings:
    return Settings(backoff_initial=0, backoff_max=0, max_attempts=3)


@pytest.fixture
def ecfr(api: FakeAPI, settings: Settings):
    with ECFR(settings, transport=httpx2.MockTransport(api)) as client:
        yield client


@pytest.fixture
def part_xml() -> str:
    return PART_XML
