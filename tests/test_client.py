import datetime as dt
import time

import httpx2
import pytest

from cfr_md import (
    ECFR,
    BadRequestError,
    ECFRConnectionError,
    ECFRTimeoutError,
    InvalidParameterError,
    Layout,
    NotFoundError,
    RateLimitError,
    ResponseFormatError,
    SearchOrder,
    ServerError,
    Settings,
    TitleNotFoundError,
    to_markdown,
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


def test_markdown_resolves_latest_date(ecfr, api, part_xml):
    assert ecfr.markdown(45, part="164") == to_markdown(part_xml)
    assert api.paths() == ["/api/versioner/v1/titles.json", FULL]
    assert api.requests[1].url.params["part"] == "164"


def test_explicit_date_skips_lookup(ecfr, api):
    api.text("/api/versioner/v1/full/2020-01-01/title-45.xml", "<DIV5/>")
    ecfr.versioner.full(45, date=dt.date(2020, 1, 1))
    assert api.paths() == ["/api/versioner/v1/full/2020-01-01/title-45.xml"]


def test_save_layouts(ecfr, tmp_path):
    assert ecfr.save(45, tmp_path / "p.md", part="164") == [tmp_path / "p.md"]
    tree = ecfr.save(45, str(tmp_path / "tree"), layout="tree", part="164")
    assert len(tree) == 4
    assert ecfr.markdown_tree(45, part="164").keys() == {
        p.relative_to(tmp_path / "tree") for p in tree
    }
    assert Layout.TREE == "tree"


@pytest.mark.parametrize(
    ("title", "message"), [(35, "reserved"), (99, "does not exist")]
)
def test_unknown_titles(ecfr, title, message):
    with pytest.raises(TitleNotFoundError, match=message):
        ecfr.markdown(title)


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
        (lambda e: e.save(45, "x", layout="zip"), "layout"),
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
