import datetime as dt
import time

import httpx2
import pytest

from ecfr_md import (
    ECFR,
    APIError,
    BadRequestError,
    DateOutOfRangeError,
    ECFRConnectionError,
    ECFRTimeoutError,
    ECFRWarning,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    RateLimitError,
    ResponseFormatError,
    SearchOrder,
    Section,
    ServerError,
    Settings,
    TitleNotFoundError,
)

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"


def test_sends_configured_headers(api):
    settings = Settings(user_agent="me/1.0", headers={"From": "me@example.com"})
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.versioner.titles()
    headers = api.requests[0].headers
    assert headers["User-Agent"] == "me/1.0"
    assert headers["From"] == "me@example.com"
    assert "gzip" in headers["Accept-Encoding"]  # Required by the full-text endpoint.


def test_get_resolves_latest_date(ecfr, api):
    part = ecfr.get(45, part="164")
    assert part.citation == "45 CFR Part 164"
    assert part.date == dt.date(2026, 10, 7)
    assert api.paths() == ["/api/versioner/v1/titles.json", FULL]
    assert api.requests[1].url.params["part"] == "164"


def test_get_accepts_citations(ecfr, api):
    section = ecfr.get("45 CFR 164.104")
    assert isinstance(section, Section)
    assert dict(api.requests[1].url.params) == {"section": "164.104"}


def test_get_paragraph_navigates_the_response(ecfr, api):
    paragraph = ecfr.get("45 C.F.R. § 164.104(a)(2)(ii)")
    assert paragraph.citation == "45 CFR 164.104(a)(2)(ii)"
    assert api.paths().count(FULL) == 1  # Navigation makes no more requests.
    with pytest.raises(NodeNotFoundError, match=r"164\.104\(z\) does not exist"):
        ecfr.get("45 CFR 164.104(z)")


def test_get_accepts_flexible_dates(ecfr, api):
    api.text("/api/versioner/v1/full/2020-03-01/title-45.xml", "<DIV5 N='1'/>")
    for date in ("March 2020", "2020-03-01", dt.datetime(2020, 3, 1, 9, 30)):
        assert ecfr.get(45, date=date).date == dt.date(2020, 3, 1)
    assert "/api/versioner/v1/titles.json" not in api.paths()  # Date given.


def body(path) -> str:
    """A saved file without its front matter."""
    return path.read_text(encoding="utf-8").split("\n---\n\n", 1)[1]


def test_save_streams_the_same_markdown_as_get(ecfr, tmp_path):
    assert ecfr.save("45 CFR 164", tmp_path / "p.md") == [tmp_path / "p.md"]
    node = ecfr.get("45 CFR 164")
    node.save(tmp_path / "q.md")
    assert body(tmp_path / "p.md") == body(tmp_path / "q.md") == node.to_markdown()
    meta = (tmp_path / "p.md").read_text(encoding="utf-8").split("\n---\n")[0]
    assert 'citation: "45 CFR Part 164"' in meta
    assert 'api: "https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/' in meta

    tree = ecfr.save(45, str(tmp_path / "tree"), tree=True, part="164")
    assert len(tree) == 7
    assert node.to_markdown_tree().keys() == {
        p.relative_to(tmp_path / "tree") for p in tree
    }
    assert not list(tmp_path.rglob("*.part"))


def test_save_paragraph_citation(ecfr, tmp_path):
    files = ecfr.save("45 CFR 164.104(a)(1)", tmp_path, front_matter=False)
    assert files == [tmp_path / "45-cfr-164.104-a-1.md"]
    assert files[0].read_text(encoding="utf-8") == "**(1)** *First.* One.\n"


OLD = "/api/versioner/v1/full/2020-01-01/title-45.xml"


PART = "/api/versioner/v1/full/2026-10-08/title-45.xml"
VERSIONS = "/api/versioner/v1/versions/title-45.json"


def titles(latest_issue: str, up_to_date: str, **extra) -> dict:
    title = {"number": 45, "name": "Public Welfare", "reserved": False}
    title |= {"latest_issue_date": latest_issue, "up_to_date_as_of": up_to_date}
    return {"titles": [title | extra]}


def version(part: str) -> dict:
    return {
        "date": "2026-10-08", "amendment_date": "2026-10-08",
        "issue_date": "2026-10-08", "identifier": f"{part}.1", "name": "x",
        "part": part, "substantive": True, "removed": False,
        "title": "45", "type": "section",
    }  # fmt: skip


def test_store_reuses_text_until_the_eCFR_changes_it(api, part_xml, tmp_path):
    api.text(PART, part_xml)
    settings = Settings(cache_dir=tmp_path, backoff_initial=0)

    def session():
        with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
            return ecfr.get("45 CFR Part 164")

    first = session()
    assert session().provenance["retrieved"] == first.provenance["retrieved"]
    assert api.paths().count(FULL) == 1  # Reused across sessions.

    # A new day; the title changed, but not part 164: still reused.
    api.json("/api/versioner/v1/titles.json", titles("2026-10-08", "2026-10-08"))
    api.json(VERSIONS, {"content_versions": [version("160")], "meta": {}})
    assert session().date == dt.date(2026, 10, 8)
    assert api.paths().count(PART) == 0
    assert api.requests[-1].url.params["issue_date[gte]"] == "2026-01-02"

    # Part 164 changed: downloaded again.
    api.json("/api/versioner/v1/titles.json", titles("2026-10-09", "2026-10-09"))
    api.text("/api/versioner/v1/full/2026-10-09/title-45.xml", part_xml)
    api.json(VERSIONS, {"content_versions": [version("164")], "meta": {}})
    session()
    assert api.paths().count("/api/versioner/v1/full/2026-10-09/title-45.xml") == 1
    assert not list(tmp_path.rglob("*.part"))


def test_store_skips_titles_being_processed(api, tmp_path):
    processing = titles("2026-01-01", "2026-10-07", processing_in_progress=True)
    api.json("/api/versioner/v1/titles.json", processing)
    settings = Settings(cache_dir=tmp_path, backoff_initial=0)
    with (
        ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr,
        pytest.warns(ECFRWarning),
    ):
        ecfr.get("45 CFR Part 164")
        ecfr.get("45 CFR Part 164")
    assert api.paths().count(FULL) == 2


def test_without_cache_no_extra_requests(ecfr, api):
    ecfr.get("45 CFR 164.104", date="2026-10-07")
    assert api.paths() == [FULL]


def test_cache_max_age(api, tmp_path):
    api.text(OLD, "<DIV5 N='1'/>")
    settings = Settings(cache_dir=tmp_path, cache_max_age=0.001, backoff_initial=0)
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.get(45, date="2020-01-01")
        time.sleep(0.01)
        ecfr.get(45, date="2020-01-01")
    assert api.paths().count(OLD) == 2


def test_temporary_downloads_are_removed(ecfr, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    ecfr.get(45)
    with pytest.raises(NotFoundError):
        ecfr.get(45, date="2026-10-01")
    assert list(tmp_path.iterdir()) == []


class DropsMidway(httpx2.SyncByteStream):
    def __iter__(self):
        yield b"<DIV5 N='164'"
        raise httpx2.ReadError("connection reset")


def test_download_restarts_after_a_dropped_connection(ecfr, api):
    api.sequence(
        FULL,
        httpx2.Response(200, stream=DropsMidway()),
        httpx2.Response(200, text="<DIV5 N='164' TYPE='PART'/>"),
    )
    assert ecfr.get(45).identifier == "164"
    assert api.paths().count(FULL) == 2


def test_dates_after_the_latest_are_explained(ecfr):
    with pytest.raises(DateOutOfRangeError, match="available up to 2026-10-07"):
        ecfr.get("45 CFR 164.104", date="2030-01-01")


def test_dates_before_the_first_version_are_explained(ecfr, api):
    api.json(
        "/api/versioner/v1/versions/title-45.json",
        {"content_versions": [{
            "date": "2017-01-03", "amendment_date": "2017-01-03",
            "issue_date": "2017-01-03", "identifier": "164.104", "name": "x",
            "part": "164", "substantive": True, "removed": False,
            "title": "45", "type": "section",
        }]},
    )  # fmt: skip
    with pytest.raises(DateOutOfRangeError, match=r"earliest version .* 2017-01-03"):
        ecfr.get("45 CFR 164.104", date="2016-06-01")


def test_missing_content_is_still_not_found(ecfr):
    with pytest.raises(NotFoundError):
        ecfr.get("45 CFR 164.999")


def test_warns_while_a_title_is_being_processed(ecfr, api):
    title = {"number": 45, "name": "Public Welfare", "reserved": False}
    title |= {"up_to_date_as_of": "2026-10-07", "processing_in_progress": True}
    titles = {"titles": [title]}
    api.json("/api/versioner/v1/titles.json", titles)
    with pytest.warns(ECFRWarning, match="processing updates to title 45"):
        ecfr.get(45)


@pytest.mark.parametrize(
    ("title", "message"), [(35, "reserved"), (99, "does not exist")]
)
def test_unknown_titles(ecfr, title, message):
    with pytest.raises(TitleNotFoundError, match=message):
        ecfr.get(title)


def test_search_query_serialisation(ecfr, api):
    api.json("/api/search/v1/results", {"results": [], "meta": {"total_count": 0}})
    ecfr.search.results(
        "minimum necessary",
        agency_slugs=["hhs", "cms"],
        last_modified_after=dt.date(2024, 1, 2),
        order=SearchOrder.NEWEST_FIRST,
        per_page=10,
    )
    params = api.requests[0].url.params
    assert params["query"] == "minimum necessary"
    assert params.get_list("agency_slugs[]") == ["hhs", "cms"]
    assert params["last_modified_after"] == "2024-01-02"
    assert params["order"] == "newest_first"
    assert params["per_page"] == "10"
    assert "page" not in params


def test_versions_query_serialisation(ecfr, api):
    api.json("/api/versioner/v1/versions/title-45.json", {"content_versions": []})
    ecfr.versioner.versions(45, issue_date_gte="2020-01-01", part="164")
    params = api.requests[0].url.params
    assert dict(params) == {"issue_date[gte]": "2020-01-01", "part": "164"}


def test_parses_response_models(ecfr, api):
    api.json(
        "/api/admin/v1/corrections/title/45.json",
        {
            "ecfr_corrections": [
                {
                    "id": 46,
                    "cfr_references": [
                        {
                            "cfr_reference": "45 CFR 46.103",
                            "hierarchy": {"title": "45", "section": "46.103"},
                        }
                    ],
                    "corrective_action": "(a) amended",
                    "error_corrected": "2005-06-27",
                    "error_occurred": "2005-06-23",
                    "fr_citation": "70 FR 36328",
                    "position": 1,
                    "display_in_toc": False,
                    "title": 45,
                    "year": 2005,
                    "last_modified": "2021-12-22",
                    "new_field": "kept",
                }
            ]
        },
    )
    (correction,) = ecfr.admin.title_corrections(45).ecfr_corrections
    assert correction.error_corrected == dt.date(2005, 6, 27)
    assert correction.cfr_references[0].hierarchy.section == "46.103"
    assert correction.model_extra == {"new_field": "kept"}


def test_unexpected_json_raises_response_format_error(ecfr, api):
    api.json("/api/admin/v1/agencies.json", {"unexpected": True})
    with pytest.raises(ResponseFormatError, match=r"agencies\.json"):
        ecfr.admin.agencies()


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda e: e.search.results(per_page=5000), "per_page"),
        (lambda e: e.search.results(page=0), "page"),
        (lambda e: e.versioner.full(45, date="2025-16-01"), "date"),
        (lambda e: e.versioner.full(45, section=164), "section"),
        (lambda e: e.get("45 CFR banana"), "banana"),
        (lambda e: e.get("45 CFR 164", part="160"), "Conflicting part"),
        (lambda e: e.get(45, date="not a date"), "date"),
    ],
)
def test_invalid_arguments_fail_before_any_request(ecfr, api, call, message):
    with pytest.raises(InvalidParameterError, match=message):
        call(ecfr)
    assert api.requests == []


@pytest.mark.parametrize(
    ("status", "body", "error", "detail"),
    [
        (404, '{"error":"No matching content found."}', NotFoundError, "No matching"),
        (404, "null", NotFoundError, ""),
        (
            400,
            '{"errors":{"error":["can only paginate"]}}',
            BadRequestError,
            "can only",
        ),
        (400, '{"errors":{"per_page":["too big"]}}', BadRequestError, "too big"),
        (404, "<hash><error>Not Found</error></hash>", NotFoundError, "Not Found"),
        (418, '{"message":"teapot"}', Exception, "teapot"),
        (  # HTML error page from the proxy in front of the API.
            403,
            "<html><head><title>403 Forbidden</title></head><body/></html>",
            APIError,
            "403 Forbidden",
        ),
    ],
)
def test_api_errors_carry_server_detail(ecfr, api, status, body, error, detail):
    api.text(FULL, body, status=status)
    with pytest.raises(error) as info:
        ecfr.versioner.full(45)
    assert info.value.status_code == status
    assert detail in info.value.detail
    assert FULL in str(info.value)
    assert api.paths().count(FULL) == 1  # Client errors are not retried.


def test_retries_transient_errors_then_succeeds(ecfr, api):
    api.sequence(
        FULL,
        httpx2.Response(503, json={"message": "Title 45 currently processing"}),
        httpx2.Response(429, text="slow down"),
        httpx2.Response(200, text="<DIV5/>"),
    )
    assert ecfr.versioner.full(45) == b"<DIV5/>"
    assert api.paths().count(FULL) == 3


def test_gives_up_after_max_attempts(ecfr, api):
    api.text(FULL, '{"message":"down"}', status=502)
    with pytest.raises(ServerError, match="down"):
        ecfr.versioner.full(45)
    assert api.paths().count(FULL) == 3


def test_honours_retry_after(api):
    settings = Settings(max_attempts=2, backoff_initial=30, backoff_max=60)
    api.sequence(
        FULL,
        httpx2.Response(429, text="", headers={"Retry-After": "0"}),
        httpx2.Response(200, text="<DIV5/>"),
    )
    start = time.monotonic()
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.versioner.full(45, date="2026-10-07")
    assert time.monotonic() - start < 5  # Retry-After: 0 overrode the 30s backoff.


def test_rate_limit_error_exposes_retry_after(ecfr, api):
    api.text(FULL, "", status=429, **{"Retry-After": "0"})
    with pytest.raises(RateLimitError) as info:
        ecfr.versioner.full(45)
    assert info.value.retry_after == 0


@pytest.mark.parametrize(
    ("exception", "error", "message"),
    [
        (httpx2.ConnectError("refused"), ECFRConnectionError, "Could not reach"),
        (httpx2.ReadTimeout("slow"), ECFRTimeoutError, "within 60.0s"),
    ],
)
def test_transport_failures_are_retried_and_wrapped(
    api, settings, exception, error, message
):
    def fail(request):
        api.requests.append(request)
        raise exception

    with (
        ECFR(settings, transport=httpx2.MockTransport(fail)) as ecfr,
        pytest.raises(error, match=message),
    ):
        ecfr.versioner.titles()
    assert len(api.requests) == settings.max_attempts


def test_rate_limit_spaces_requests(api):
    settings = Settings(rate_limit=20)
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        start = time.monotonic()
        for _ in range(3):
            ecfr.versioner.titles()
    assert time.monotonic() - start >= 0.1  # Two gaps of 1/20s.


def test_large_titles_are_fetched_part_by_part(ecfr, api, monkeypatch):
    monkeypatch.setattr("ecfr_md.client.SPLIT_ABOVE", 0)
    title = ecfr.get(45)
    assert title.level == "title" and title.heading == "Title 45—Public Welfare"
    subtitle = title["A"]
    assert [p.identifier for p in subtitle] == ["164", "165"]
    assert title["164.104(a)(1)"].text == "**(1)** *First.* One."
    assert title["Part 165"].heading == "Part 165 [Reserved]"
    assert api.paths().count(FULL) == 1  # Only the unreserved part.
    assert dict(api.requests[-1].url.params) == {"part": "164"}
    assert "joined from 1 part requests" in title.provenance["api"]


def test_falls_back_to_parts_when_the_whole_title_times_out(ecfr, api, part_xml):
    def full(request):
        if "part" not in request.url.params:
            return httpx2.Response(504, text="Gateway Time-out")
        return httpx2.Response(200, text=part_xml)

    api.routes[FULL] = full
    title = ecfr.get(45)
    assert title["A"]["164"].citation == "45 CFR Part 164"
    assert title["164"] is title["A"]["164"]


def test_get_many_shares_one_request_per_small_part(ecfr, api):
    nodes = ecfr.get_many(["45 CFR 164.104(a)", "45 CFR 164.106", "45 CFR 164.104(c)"])
    assert [n.citation for n in nodes] == [
        "45 CFR 164.104(a)",
        "45 CFR 164.106",
        "45 CFR 164.104(c)",
    ]
    full = [r for r in api.requests if r.url.path == FULL]
    assert [dict(r.url.params) for r in full] == [{"part": "164"}]


def test_get_many_fetches_separately_when_the_part_is_large(ecfr, api, monkeypatch):
    monkeypatch.setattr("ecfr_md.client.GROUP_BELOW", 0)
    ecfr.get_many(["45 CFR 164.104", "45 CFR 164.106"])
    full = [dict(r.url.params) for r in api.requests if r.url.path == FULL]
    assert full == [{"section": "164.104"}, {"section": "164.106"}]
