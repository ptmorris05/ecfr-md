"""The web navigator: every page, its Markdown and JSON, and the scripts' JSON."""

import datetime as dt
import functools
import json
import sys
import types
from pathlib import Path

import httpx2
import pytest
from starlette.testclient import TestClient
from web_data import EARLIER, LATEST, RESULTS, VERSIONS, install

from ecfr_md import ECFR, Settings, cli
from ecfr_md.errors import ECFRConnectionError
from ecfr_md.web import app as web
from ecfr_md.web import create_app

SECTION = "/current/title-45/section-164.104"


@pytest.fixture
def api(api):
    return install(api)


@pytest.fixture
def factory(api, settings):
    return functools.partial(ECFR, transport=httpx2.MockTransport(api))


@pytest.fixture
def client(ecfr, factory, tmp_path):
    app = create_app(ecfr, save_dir=tmp_path / "saved", factory=factory)
    with TestClient(app, raise_server_exceptions=False) as test:
        yield test


def test_home_lists_titles_in_every_variant(client, ecfr):
    page = client.get("/")
    assert page.status_code == 200
    assert '<a class="item" href="/current/title-45">' in page.text
    assert "Reserved" in page.text
    assert "public domain" not in page.text  # No branding, just the disclaimer.
    assert "not an official legal edition" in page.text
    data = client.get("/index.json").json()
    assert data["titles"][0]["href"] == "/current/title-45"
    assert data["titles"][0]["size"] is None  # Nothing downloaded yet.
    assert data["links"]["md"] == "/index.md"
    ecfr.outline(45)  # Now the title's outline is downloaded.
    assert client.get("/index.json").json()["titles"][0]["size"] == 9000
    markdown = client.get("/index.md")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "- [45 CFR — Public Welfare](/current/title-45.md)" in markdown.text
    assert "- Title 35 [Reserved]" in markdown.text


def test_title_and_divisions_show_contents(client):
    page = client.get("/current/title-45")
    assert page.status_code == 200
    assert 'data-src="/current/title-45/subtitle-A?toc=1"' in page.text
    assert "8 § · 3.0 MB" in page.text  # Sections and appendices, and size.
    fragment = client.get("/current/title-45/subtitle-A/subchapter-C?toc=1")
    assert fragment.text.lstrip().startswith('<ol class="toc">')
    assert 'href="/current/title-45/part-160"' in fragment.text
    data = client.get("/current/title-45/subtitle-A/subchapter-C.json").json()
    assert data["citation"] == "45 CFR Subtitle A Subchapter C"
    assert [c["sections"] for c in data["contents"]] == [2, 5, 0, 0, 1]
    assert data["breadcrumb"][-1]["label"] == "Subchapter C"
    assert data["next"] is None and data["previous"] is None
    assert data["up"]["citation"] == "45 CFR Subtitle A"
    assert "text" not in data
    markdown = client.get("/current/title-45/subtitle-A/subchapter-C.md").text
    assert markdown.startswith("---\n")
    assert "- [Part 160—General](/current/title-45/part-160.md) (2 sections)" in (
        markdown
    )
    assert "- Other parts\n" in markdown  # A heading, not a place.
    assert '<span class="group">Other parts</span>' in fragment.text


def test_a_part_reads_whole_with_its_contents(client):
    page = client.get("/current/title-45/part-164")
    assert '<details class="contents-inline">' in page.text
    assert 'class="div part"' in page.text
    assert page.text.rstrip().endswith("</html>")
    data = client.get("/current/title-45/part-164.json").json()
    assert data["text"]["level"] == "part"
    assert data["contents"][0]["level"] == "subpart"
    assert data["next"]["citation"] == "45 CFR Part 170"  # Past the reserved part.
    assert data["previous"]["citation"] == "45 CFR Part 160"


def test_a_large_part_shows_contents_and_its_text_on_request(client):
    page = client.get("/current/title-45/part-170")
    assert "read all of it" in page.text
    assert 'id="170.1"' not in page.text
    full = client.get("/current/title-45/part-170?full=1")
    assert 'id="170.1"' in full.text
    assert "text" not in client.get("/current/title-45/part-170.json").json()
    assert "§ 170.1" in client.get("/current/title-45/part-170.md").text


def test_reading_a_section(client):
    page = client.get(SECTION)
    text = page.text
    assert page.headers["content-type"].startswith("text/html")
    # Markers link to their paragraphs, run-in ones too.
    assert '<a class="mk" href="#p-164.104(a)">(a)</a>' in text
    assert '<p id="p-164.104(a)(2)" class="p d2">' in text
    assert '<span class="num">§ 164.104</span>' in text
    assert 'data-bluebook="45 C.F.R. § 164.104 (2026)"' in text
    assert '<link rel="next" href="/current/title-45/section-164.106">' in text
    assert '<link rel="prev" href="/current/title-45/section-164.102">' in text
    assert '<link rel="up" href="/current/title-45/part-164/subpart-A">' in text
    assert '"urls": ["/current/title-45/section-164.102", "/current/' in text
    assert 'href="https://www.ecfr.gov/current/title-45/section-164.104"' in text
    assert "<style>:root{--load:1}</style>" in text
    assert '<span id="end" hidden></span>' in text
    assert 'class="note source"' in text  # The source note, as marginalia.
    other = client.get("/current/title-45/section-164.102").text
    assert 'class="note authority"' not in other
    assert '<blockquote class="note extract">' in other
    assert '<a class="ext" href="https://www.federalregister.gov/' in other


def test_cross_references_link_here(client):
    text = client.get("/current/title-45/section-160.101").text
    assert '<a class="xref" href="/current/title-45/section-164.102">' in text
    assert 'class="ext" href="https://uscode.house.gov/' in text
    past = client.get(f"/on/{EARLIER}/title-45/section-160.101").text
    assert f'<a class="xref" href="/on/{EARLIER}/title-45/section-164.102">' in past


def test_section_variants(client):
    data = client.get(SECTION + ".json").json()
    assert data["citation"] == "45 CFR 164.104"
    assert data["date"] == LATEST and data["current"] is True
    assert data["citations"] == {
        "ofr": "45 CFR 164.104",
        "bluebook": "45 C.F.R. § 164.104 (2026)",
    }
    assert data["text"]["children"] == []
    assert data["links"] == {
        "html": SECTION,
        "md": SECTION + ".md",
        "json": SECTION + ".json",
    }
    markdown = client.get(SECTION + ".md").text
    assert 'next: "/current/title-45/section-164.106.md"' in markdown
    assert "\n# § 164.104 Paragraph nesting.\n" in markdown


def test_past_dates(client):
    page = client.get(f"/on/{EARLIER}/title-45/section-164.102")
    assert "obey." in page.text
    assert '<a href="/current/title-45/section-164.102">Current text</a>' in page.text
    future = client.get(
        "/on/2099-01-01/title-45/section-164.102.json?x=1", follow_redirects=False
    )
    assert future.status_code == 307
    assert future.headers["location"] == "/current/title-45/section-164.102.json?x=1"
    too_early = client.get("/on/2015-01-01/title-45/section-164.102")
    assert too_early.status_code == 404
    assert "earliest version of 45 CFR 164.102 is from 2018-01-01" in too_early.text
    title = client.get("/on/2015-01-01/title-45.json")
    assert title.json()["error"] == "The eCFR has no text of title 45 on 2015-01-01"
    assert client.get("/on/2020-13-01/title-45").status_code == 404
    assert client.get("/on/yesterday/title-45").status_code == 404


def test_missing_places(client):
    assert client.get("/current/title-45/section-999.9").status_code == 404
    assert client.get("/current/title-7").status_code == 404
    bad = client.get("/current/title-45/nonsense.json")
    assert bad.status_code == 404 and "error" in bad.json()
    assert client.get("/current/title-45/nonsense.md").text.startswith("# Error 404")
    assert client.get("/current", follow_redirects=False).status_code == 307


def test_text_errors_show_in_the_page(client, api):
    api.routes["/api/versioner/v1/full/2026-10-07/title-45.xml"] = lambda r: (
        httpx2.Response(404, json={"error": "No matching content found."})
    )
    page = client.get("/current/title-45/part-164")
    assert page.status_code == 200  # The top of the page was already sent.
    assert 'class="problem" role="alert"' in page.text


def test_offline_pages_say_so(client, api, monkeypatch):
    def offline(*args, **kwargs):
        raise ECFRConnectionError("no network")

    monkeypatch.setattr(ECFR, "get", offline)
    page = client.get(SECTION)
    assert (
        "isn&#39;t in the cache yet" in page.text or "isn't in the cache" in page.text
    )
    data = client.get(SECTION + ".json")
    assert data.status_code == 503
    assert "can't be reached" in data.json()["error"]


def test_unexpected_errors_are_logged(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(web.pages, "place", broken)
    page = client.get(SECTION)
    assert page.status_code == 500
    assert "its log has the details" in page.text


def test_go_resolves_anything(client):
    found = client.get("/go?cite=45 CFR 164.104(a)(2)", follow_redirects=False)
    assert found.status_code == 303
    assert found.headers["location"] == SECTION + "#p-164.104(a)(2)"
    dated = client.get("/go?cite=45/164.104&date=2020-01-01", follow_redirects=False)
    assert dated.headers["location"] == f"/on/{EARLIER}/title-45/section-164.104"
    dated_paragraph = client.get(
        "/go?cite=45/164.104/a&date=2020-01-01", follow_redirects=False
    )
    assert dated_paragraph.headers["location"].endswith("164.104#p-164.104(a)")
    words = client.get("/go?cite=minimum necessary", follow_redirects=False)
    assert words.headers["location"] == "/search?q=minimum+necessary"
    assert client.get("/go", follow_redirects=False).headers["location"] == "/"
    data = client.get("/go.json?cite=https://www.ecfr.gov/current/title-45/part-164")
    assert data.json() == {
        "cite": "https://www.ecfr.gov/current/title-45/part-164",
        "address": "45/part-164",
        "citation": "45 CFR Part 164",
        "href": "/current/title-45/part-164",
        "url": "https://www.ecfr.gov/current/title-45/part-164",
    }
    assert client.get("/go.json?cite=nonsense").status_code == 400
    assert client.get("/go.json?cite=45/164.104&date=someday").status_code == 400
    assert client.get("/go.md?cite=45/164.104", follow_redirects=False).status_code == (
        303
    )


def test_search(client, api):
    page = client.get("/search?q=must comply&within=45 CFR 164")
    assert "<mark>must</mark> <mark>comply</mark>" in page.text
    assert 'href="/current/title-45/section-164.102#:~:text=must%20comply"' in (
        page.text
    )
    assert api.requests[-1].url.params["query"] == '"must comply"'
    data = client.get("/search.json?q=comply&limit=nope").json()
    assert data["total"] == 1 and data["results"][0]["citation"] == "45 CFR 164.102"
    assert client.get("/search.json?q=comply&limit=5").json()["error"] is None
    markdown = client.get("/search.md?q=comply&within=45").text
    assert markdown.startswith("# Search: comply in 45")
    assert "## [45 CFR 164.102](/current/title-45/section-164.102.md)" in markdown
    assert "Search with ?q=words" in client.get("/search.md").text
    assert "Words or" in client.get("/search").text
    error = client.get("/search.json?q=x&within=nonsense").json()["error"]
    assert error
    assert error in client.get("/search.md?q=x&within=nonsense").text
    assert 'role="alert"' in client.get("/search?q=x&within=nonsense").text
    long = "x" * 90
    assert "#:~:" not in client.get(f"/search.json?q={long}").text
    api.json(RESULTS, {"results": [], "meta": {}})
    assert "Nothing found" in client.get("/search?q=zebra").text
    assert "\n\n## " not in client.get("/search.md?q=zebra").text


def test_history_and_compare(client, api):
    page = client.get("/history/title-45/part-164")
    assert "What changed" in page.text
    assert 'href="/compare/2026-01-01/to/2025-12-31/title-45/section-164.102"' in (
        page.text
    )
    data = client.get("/history/title-45/part-164.json").json()
    assert data["dates"] == ["2026-01-01", "2020-01-01", "2018-01-01"]
    assert data["changes"][0]["href"] == "/on/2026-01-01/title-45/section-164.102"
    markdown = client.get("/history/title-45/section-164.102.md").text
    assert "[what changed](/compare/2026-01-01/to/2025-12-31/" in markdown
    api.json(VERSIONS, {"content_versions": [], "meta": {}})
    assert "records no changes" in client.get("/history/title-45/part-164").text
    assert "No changes recorded." in client.get("/history/title-45/part-164.md").text
    assert client.get("/history/title-45/nonsense").status_code == 404

    path = f"/compare/{LATEST}/to/{EARLIER}/title-45/section-164.102"
    redline = client.get(path)
    assert "<del>obey.</del><ins>comply.</ins>" in redline.text
    assert "1 changed" in redline.text
    assert '<div class="same">' in redline.text
    assert 'id="p-164.102(a)(1)(i)"' in redline.text  # Depth and anchors kept.
    data = client.get(path + ".json").json()
    assert data["since"] == EARLIER and len(data["differences"]) == 1
    assert "<del>obey.</del>" in client.get(path + ".md").text
    swapped = client.get(
        f"/compare/{EARLIER}/to/{LATEST}/title-45/section-164.102",
        follow_redirects=False,
    )
    assert swapped.headers["location"] == path
    part = client.get(f"/compare/{LATEST}/to/{EARLIER}/title-45/part-164").text
    assert '<div class="change added">' in part
    same = client.get(f"/compare/{LATEST}/to/{LATEST}/title-45/section-164.102").text
    assert "No changes between these dates." in same
    assert client.get(f"/compare/{LATEST}/to/{EARLIER}/title-45").status_code == 400
    assert client.get(f"/compare/soon/to/{EARLIER}/title-45").status_code == 404


def test_export_downloads_and_saves(client, tmp_path):
    menu = client.get("/export/title-45/section-164.104")
    assert ">Download</a>" in menu.text and "Save to" in menu.text
    download = client.get("/export/title-45/section-164.104?format=html")
    assert download.headers["content-type"].startswith("text/html")
    assert "attachment" in download.headers["content-disposition"]
    assert "45_164.104.html" in download.headers["content-disposition"]
    dated = client.get(
        f"/export/title-45/section-164.104?format=markdown&date={EARLIER}"
    )
    assert f'date: "{EARLIER}"' in dated.text
    saved = client.post(
        "/export/title-45/section-164.104",
        data={"format": "text"},
        headers={"Accept": "application/json"},
    ).json()
    assert Path(saved["saved"]).parent == tmp_path / "saved"
    assert saved["format"] == "text" and saved["bytes"] > 0
    page = client.post("/export/title-45/section-164.104", data={"format": "json"})
    assert "<h1>Saved</h1>" in page.text
    assert client.get("/export/title-45/section-164.104?format=nope").status_code == 400
    assert client.get("/export/title-45/nonsense?format=html").status_code == 400
    assert (
        client.get("/export/title-45/section-164.104?date=2020-13-45").status_code
        == 400
    )


def test_export_failures_clean_up(client, monkeypatch):
    def fails(*args, **kwargs):
        raise ECFRConnectionError("down")

    monkeypatch.setattr(ECFR, "save", fails)
    response = client.get("/export/title-45/section-164.104?format=html")
    assert response.status_code == 503


def test_export_menu_lists_formats_and_extras(client, monkeypatch):
    real = web.require

    def without_pdf(name):
        if name == "pdf":
            raise web.MissingDependencyError("needs pdf")
        return real(name)

    monkeypatch.setattr(web, "require", without_pdf)
    page = client.get(f"/on/{EARLIER}/title-45/section-164.104").text
    assert 'title="Needs the pdf extra">PDF</span>' in page
    assert f"/export/title-45/section-164.104?date={EARLIER}&amp;format=epub" in page


def test_saving_needs_a_folder(ecfr):
    with TestClient(create_app(ecfr)) as client:
        response = client.post(
            "/export/title-45/section-164.104", data={"format": "text"}
        )
        assert response.status_code == 400
        assert "--save-dir" in response.text
        assert "Save to" not in client.get("/export/title-45/section-164.104").text


def test_settings_page_applies_and_writes(client, monkeypatch, tmp_path):
    config = tmp_path / "config" / "ecfr-md.toml"
    monkeypatch.setenv("ECFR_MD_CONFIG", str(config))
    page = client.get("/settings")
    assert 'name="base_url"' in page.text and 'name="cache"' in page.text
    data = client.get("/settings.json").json()
    names = [f["name"] for f in data["settings"]]
    assert names == list(Settings.model_fields)
    assert data["config_file"] == str(config)
    form = {f["name"]: f["value"] for f in data["settings"] if f["kind"] != "bool"}
    form |= {"timeout": "30", "headers": "From: me@example.com", "write": "on"}
    navigator = client.app.state.navigator
    before = navigator.ecfr
    applied = client.post("/settings", data=form)
    assert applied.status_code == 200
    assert "Applied: headers, timeout." in applied.text
    assert navigator.ecfr is not before and navigator.ecfr.settings.timeout == 30
    assert navigator.ecfr.settings.cache is False  # Its box wasn't ticked.
    assert navigator.retired == [before]
    assert config.read_text() == (
        'headers = { From = "me@example.com" }\ntimeout = 30.0\nmax_attempts = 3\n'
        "backoff_initial = 0.0\nbackoff_max = 0.0\ncache = false\n"
    )
    origins = {
        f["name"]: f["origin"] for f in client.get("/settings.json").json()["settings"]
    }
    assert origins["timeout"] == "set here" and origins["base_url"] == "default"
    bad = client.post("/settings", data={**form, "timeout": "-1"})
    assert bad.status_code == 400
    assert "greater than 0" in bad.text
    # Text still reads through the new client.
    assert client.get(SECTION).status_code == 200


def test_origin_checks_refuse_other_sites(client):
    refused = client.post(
        "/settings", data={}, headers={"Origin": "https://evil.example"}
    )
    assert refused.status_code == 403
    refused = client.post(
        "/settings", data={}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert refused.status_code == 403
    allowed = client.post(
        "/export/title-45/section-164.104",
        data={"format": "text"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert allowed.status_code == 200


def test_only_local_host_names(ecfr):
    with TestClient(create_app(ecfr), base_url="http://evil.example") as client:
        assert client.get("/").status_code == 400
    with TestClient(create_app(ecfr, hosts=["*"]), base_url="http://other") as client:
        assert client.get("/").status_code == 200


def test_preview_cards(client):
    card = client.get("/api/preview?href=/current/title-45/section-164.104").json()
    assert card["citation"] == "45 CFR 164.104"
    assert card["html"].startswith("<p>Introductory text")
    assert card["more"] is False
    paragraph = client.get(
        "/api/preview?href=/current/title-45/section-164.104%23p-164.104(a)(2)"
    ).json()
    assert paragraph["citation"] == "45 CFR 164.104(a)(2)"
    long = client.get("/api/preview?href=/current/title-45/part-164").json()
    assert long["more"] is True
    missing = client.get("/api/preview?href=/current/title-45/section-1.1")
    assert missing.status_code == 404


def test_terms(client):
    found = client.get("/api/terms?address=45/164.104").json()
    terms = {t["term"]: t for t in found["terms"]}
    assert terms["Covered entity"]["href"] == "/current/title-45/section-160.103"
    assert (
        terms["Covered entity"]["html"]
        == "<p><em>Covered entity</em> means a health plan.</p>\n"
    )
    assert client.get("/api/terms?address=45/164.104").json() == found  # Kept.
    past = client.get(f"/api/terms?address=45/164.104&date={EARLIER}").json()
    assert past["terms"][0]["href"].startswith(f"/on/{EARLIER}/")
    error = client.get("/api/terms?address=nonsense").json()
    assert error["terms"] == [] and error["error"]


def test_palette_index(client, ecfr):
    assert client.get("/api/index").json() == {"titles": []}
    entries = client.get("/api/index/45").json()["entries"]
    assert {
        "c": "45 CFR 164.104",
        "h": "§ 164.104 Heading.",
        "u": SECTION,
        "l": "section",
    } in entries
    assert client.get("/api/index").json() == {"titles": [45]}
    assert client.get("/api/index/7").status_code == 404


def test_cached_titles_on_disk(api, settings, tmp_path):
    stored = settings.model_copy(update={"cache": True, "cache_dir": tmp_path})
    transport = httpx2.MockTransport(api)
    with ECFR(stored, transport=transport) as first:
        first.outline(45)
    with (
        ECFR(stored, transport=transport) as ecfr,
        TestClient(create_app(ecfr)) as client,
    ):
        assert client.get("/api/index").json() == {"titles": [45]}
        assert client.get("/index.json").json()["titles"][0]["size"] == 9000


def test_for_agents(client):
    llms = client.get("/llms.txt").text
    assert llms.startswith("# Code of Federal Regulations")
    assert "(http://testserver/current/title-45/section-164.502.md)" in llms
    spec = client.get("/openapi.json").json()
    assert spec["openapi"] == "3.1.0"
    assert "/search.json" in spec["paths"]
    assert spec["servers"] == [{"url": "http://testserver"}]


def test_static_files_and_service_worker(client):
    worker = client.get("/sw.js")
    assert worker.headers["service-worker-allowed"] == "/"
    assert "__VERSION__" not in worker.text
    assert client.get("/static/ecfr.css").status_code == 200
    font = client.get("/static/fonts/SourceSerif4-Roman.woff2")
    assert font.status_code == 200 and len(font.content) > 100_000
    assert (
        "SIL Open Font License" in client.get("/static/fonts/OFL-SourceSerif4.md").text
    )
    assert client.get("/favicon.ico").status_code == 204
    assert json.loads(client.get("/static/aliases.json").text)[0][1] == (
        "45/part-164/subpart-E"
    )


def test_progress_reaches_the_page(ecfr):
    relay = web.Relay()
    heard = []
    relay.started("u")  # Nobody listening: nothing to hear.
    with relay.listen(heard.append):
        relay.fetching("45 CFR 1", dt.date(2024, 1, 1))
        relay.started("u")
        relay.received("u", 50, 100)
        relay.received("u", 400_000, None)
        relay.finished("u", 100, cached=False)
        relay.retrying("u", RuntimeError(), 1.0, 1, 3)
        relay.rate_changed(None)
        relay.parts_started(2)
        relay.part_finished("1")
        relay.file_written(Path("x"))
    assert heard == [0.08, 0.12, 0.52, 0.52, 0.95]


def test_streamed_progress(ecfr, factory, api):
    def slow(request):
        return httpx2.Response(
            200,
            content=b"<?xml version='1.0'?>"
            + api.routes["/api/versioner/v1/full/2026-10-07/title-45.xml"](
                request
            ).content.split(b"?>", 1)[-1],
            headers={"Content-Length": "100000"},
        )

    app = create_app(ecfr, factory=factory)
    nav = app.state.navigator
    place = web.pages.place(nav.ecfr, web.pages.parse_page(SECTION), None)
    with nav.relay.listen(lambda f: None):
        pass
    chunks = []

    class Talks(ECFR):
        def get(self, *args, **kwargs):
            nav.relay.fetching("x", dt.date(2024, 1, 1))
            nav.relay.received("u", 50, 100)
            return super().get(*args, **kwargs)

    nav.ecfr.__class__ = Talks
    chunks = list(web._text_stream(nav, place))
    assert chunks[0] == "<style>:root{--load:0.08}</style>\n"
    assert "--load:0.52" in chunks[1]


def test_mcp_is_mounted_when_installed(ecfr, monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    started = []

    class Server:
        def http_app(self, path):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def lifespan(app):
                started.append(True)
                yield

            return Starlette(
                routes=[Route(path, lambda r: PlainTextResponse("mcp"))],
                lifespan=lifespan,
            )

    module = types.ModuleType("ecfr_md.mcp")
    module.server = lambda client: Server()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ecfr_md.mcp", module)
    with TestClient(create_app(ecfr)) as client:
        assert client.get("/mcp/").text == "mcp"
    assert started == [True]


def test_serve_command(run, monkeypatch, tmp_path, capsys):
    import uvicorn

    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        cli.threading,
        "Timer",
        lambda delay, f, args: types.SimpleNamespace(start=lambda: f(*args)),
    )
    assert run("serve", "--port", "9000", "--open", "--save-dir", str(tmp_path)) == 0
    assert served["port"] == 9000 and served["host"] == "127.0.0.1"
    assert served["app"].state.navigator.save_dir == tmp_path
    assert opened == ["http://localhost:9000/"]
    assert "Serving the CFR at http://localhost:9000/" in capsys.readouterr().err
    assert run("serve", "--host", "0.0.0.0") == 0
    assert "other computers" in capsys.readouterr().err
    assert run("serve", "--host", "::") == 0
    assert "http://[::]:8000/" in capsys.readouterr().err


def test_serve_needs_the_web_extra(run, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert run("serve") == 1
    assert "'web' extra" in capsys.readouterr().err
