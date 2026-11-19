"""The MCP server, driven by FastMCP's in-memory client: every tool,
resource, prompt and completion, the errors, progress and subscriptions."""

import asyncio
import importlib
import json
import sys
import threading
from types import SimpleNamespace

import anyio
import httpx2
import mcp_types
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from hipaa import LATEST, TITLES, VERSIONER, VERSIONS, install, version
from mcp.client.subscriptions import listen
from mcp.shared.exceptions import MCPError

import ecfr_md.client
from ecfr_md import MissingDependencyError, NodeNotFoundError
from ecfr_md.errors import (
    ECFRConnectionError,
    InvalidCitationError,
    TitleNotFoundError,
)
from ecfr_md.mcp import (
    INSTRUCTIONS,
    Cancelled,
    Navigator,
    Relay,
    Watch,
    _approx,
    _Call,
    _current,
    _description,
    _teach,
    build,
    server,
)
from ecfr_md.passages import TextIndex
from ecfr_md.progress import Progress

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def navigator(api, ecfr, tmp_path):
    install(api)
    return Navigator(ecfr, export_root=tmp_path / "out")


@pytest.fixture
def watch(navigator):
    return Watch(navigator, interval=3600)


@pytest.fixture
async def client(navigator, watch):
    async with Client(build(navigator, watch)) as connected:
        yield connected


async def call(client: Client, name: str, **arguments):
    """The tool's Markdown and structured result."""
    result = await client.call_tool(name, arguments)
    (content,) = result.content
    assert isinstance(content, mcp_types.TextContent)
    return content.text, result.structured_content


async def fails(client: Client, name: str, **arguments) -> str:
    with pytest.raises(ToolError) as caught:
        await client.call_tool(name, arguments)
    return str(caught.value)


async def test_what_the_server_offers(client):
    assert "map first, then read" in (client.instructions or "")
    assert client.instructions == INSTRUCTIONS
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {
        "list_titles",
        "outline",
        "read",
        "search",
        "changes",
        "history",
        "compare",
        "references",
        "cited_by",
        "resolve_citation",
        "verify_quote",
        "define",
        "settings",
        "export",
    }
    for name, tool in tools.items():
        assert tool.output_schema and tool.output_schema["type"] == "object"
        assert tool.annotations is not None and tool.annotations.title
        assert tool.annotations.read_only_hint is (name != "export")
    assert tools["read"].annotations.open_world_hint
    assert not tools["settings"].annotations.open_world_hint
    templates = await client.list_resource_templates()
    assert [t.uri_template for t in templates] == ["cfr:{address*}"]
    resources = {str(r.uri) for r in await client.list_resources()}
    assert resources == {"cfr:titles", "ecfr-md:settings"}
    prompts = {p.name for p in await client.list_prompts()}
    assert prompts == {
        "summarize_obligations",
        "what_changed",
        "compare_provisions",
        "find_term",
    }
    capabilities = client.server_capabilities
    assert capabilities.completions is not None
    assert capabilities.resources.subscribe  # Through subscriptions/listen.


async def test_server_without_export_or_with_config_changes(api, ecfr):
    install(api)
    async with Client(server(ecfr)) as plain:
        names = {t.name for t in await plain.list_tools()}
        assert "export" not in names and "configure" not in names
    async with Client(server(ecfr, allow_config_changes=True)) as open_:
        tools = {t.name: t for t in await open_.list_tools()}
        assert not tools["configure"].annotations.read_only_hint
        text, data = await call(open_, "configure", changes={"rate_limit": 0.5})
        assert "rate_limit = 0.5" in text and data["config_changes_allowed"]
        message = await fails(open_, "configure", changes={"colour": "red"})
        assert "No setting colour" in message
        message = await fails(open_, "configure", changes={"concurrency": 99})
        assert "Invalid settings: concurrency" in message
        text, _ = await call(open_, "settings")
        assert "Changes allowed" in text


async def test_list_titles(client):
    text, data = await call(client, "list_titles")
    assert "45 Public Welfare · 2026-10-07" in text
    assert data["titles"][0]["citation"] == "45 CFR"
    assert data["titles"][1]["reserved"]


async def test_outline_fits_the_page_and_estimates_tokens(client):
    text, data = await call(client, "outline", citation="45 CFR Part 164")
    assert text.startswith("45 CFR Part 164: Security and Privacy · as of 2026-10-07")
    assert (
        "Subpart E—Privacy of Health Information · 45/part-164/subpart-E · ≈665" in text
    )
    assert "  § 164.502 Uses and disclosures. · ≈475" in text
    assert data["depth"] == 2 and not data["deeper"]
    assert data["entries"][0]["tokens"] == 2850
    shallow, data = await call(client, "outline", citation="45", depth=1)
    assert data["deeper"] and "(Shown 1 levels" in shallow
    assert "≈7.1k tokens" in shallow
    titles, _ = await call(client, "outline")
    assert titles.startswith("CFR titles")


async def test_outline_of_a_section_says_what_is_around_it(client):
    text, data = await call(client, "outline", citation="45/164.502")
    assert "Previous: 45 CFR 164.501 · Next: 45 CFR Appendix A to Part 164" in text
    assert 'Read it: read("45/164.502")' in text
    assert data["next"]["address"] == "45/part-164/appendix-A"
    last, data = await call(client, "outline", citation="45 CFR Part 164 Appendix A")
    assert "Next: none" in last and data["next"] is None


async def test_outline_auto_depth_stops_at_a_size(client, monkeypatch):
    monkeypatch.setattr("ecfr_md.mcp.OUTLINE_LINES", 3)
    monkeypatch.setattr("ecfr_md.mcp.OUTLINE_MAX_LINES", 2)
    _, data = await call(client, "outline", citation="45 CFR Part 164")
    assert data["depth"] == 1 and data["deeper"] and len(data["entries"]) == 2


async def test_read_a_section_in_slices(client):
    text, data = await call(
        client, "read", citation="45 CFR 164.502", budget_tokens=100
    )
    assert text.startswith(
        "45 CFR 164.502 · 45/164.502 · as of 2026-10-07 · https://www.ecfr.gov/"
    )
    assert (
        "in 45 CFR Part 164 Subpart E · previous 164.501 · next Appendix A to Part 164"
        in text
    )
    assert (
        "Part of the text: ≈" in text and 'More: read(cursor="cfr:45/164.502@' in text
    )
    assert not data["complete"] and data["cursor"]
    assert data["paragraphs"][0] == "45 CFR 164.502(a)"
    rest, more = await call(client, "read", cursor=data["cursor"], budget_tokens=5000)
    assert more["cursor"] is None and "This is the end." in rest
    assert "**(a)(2)** *Required disclosures.*" in rest
    assert (
        "Cites: 164.506, 164.502(a)(1), 160.103" in rest
        and "Also cites: 42 U.S.C. 1320d" in rest
    )
    assert more["anchors"][-1] == "p-164.502(b)"
    cited = [r["citation"] for r in more["references"]]
    assert "42 U.S.C. 1320d" in cited and "45 CFR 164.502(a)(1)" in cited


async def test_read_anything(client):
    text, data = await call(client, "read", citation="45/164.502/a/1")
    assert "Complete: ≈" in text and data["complete"]
    assert data["parent"]["citation"] == "45 CFR 164.502(a)"
    assert data["next"]["citation"] == "45 CFR 164.502(a)(2)"
    assert data["previous"] is None
    assert [c["citation"] for c in data["children"]] == [
        "45 CFR 164.502(a)(1)(i)",
        "45 CFR 164.502(a)(1)(ii)",
    ]
    _, subpart = await call(client, "read", citation="45 CFR Part 164 Subpart D")
    assert subpart["previous"]["citation"] == "45 CFR Part 164 Subpart A"
    assert subpart["next"]["citation"] == "45 CFR Part 164 Subpart E"
    assert [c["citation"] for c in subpart["children"]] == [
        "45 CFR 164.402",
        "45 CFR 164.404",
    ]
    _, part = await call(client, "read", citation="45 CFR Part 160")
    assert part["previous"] is None and part["next"]["citation"] == "45 CFR Part 164"
    _, old = await call(client, "read", citation="45 CFR 164.502", date="2020-01-01")
    assert old["date"] == "2020-01-01" and "Old rule" in old["text"]


async def test_read_errors_teach(client):
    assert "Say what to read" in await fails(client, "read")
    message = await fails(client, "read", citation="45 CFR 164.5021")
    assert "did you mean 164.502" in message
    assert "isn't a cursor" in await fails(client, "read", cursor="nonsense")


async def test_search_and_cited_by(client, api):
    text, data = await call(
        client,
        "search",
        query="minimum necessary",
        within="45 CFR Part 164",
        date="2024-01-01",
    )
    assert text.startswith("Search 'minimum necessary': 2 sections\n45 CFR 164.502")
    assert api.requests[-1].url.params["date"] == "2024-01-01"
    assert data["results"][0]["address"] == "45/164.502"
    text, _ = await call(client, "cited_by", citation="45 CFR 164.502", limit=1)
    assert text.startswith("May cite 45 CFR 164.502: 1 sections\n45 CFR 164.404")
    api.json("/api/search/v1/results", {"results": [], "meta": {"total_pages": 1}})
    text, _ = await call(client, "search", query="nothing")
    assert text.startswith("Search 'nothing': nothing found. Search syntax:")


async def test_changes_and_history(client):
    text, data = await call(client, "changes", citation="45 CFR Part 164", limit=2)
    assert text.splitlines()[1].startswith("2024-06-25 · 2024-06-25 · substantive")
    assert "… 1 older" in text and data["total"] == 3
    text, data = await call(client, "history", citation="45 CFR Part 164", limit=1)
    assert text.startswith("2 versions of 45 CFR Part 164")
    assert "45 CFR 164.501, 45 CFR 164.502" in text
    assert "… 1 older versions" in text
    assert (
        'compare("45 CFR Part 164", from_date="2024-06-24", to_date="2024-06-25")'
        in text
    )
    assert data["versions"][0]["kinds"] == ["substantive"]


async def test_history_lists_many_sections_briefly(client, api):
    versions = [version(f"164.5{n:02}", "2024-06-25") for n in range(6)]
    api.json(VERSIONS, {"content_versions": versions})
    text, _ = await call(client, "history", citation="45 CFR Part 164")
    assert "and 2 more" in text
    api.json(VERSIONS, {"content_versions": []})
    text, _ = await call(client, "history", citation="45 CFR Part 164")
    assert text == "0 versions of 45 CFR Part 164, newest first:"


async def test_compare_redlines_with_amendments(client):
    text, data = await call(
        client, "compare", citation="45 CFR 164.502", from_date="2020-01-01"
    )
    assert text.startswith("45 CFR 164.502: 2020-01-01 → 2026-10-07:")
    assert "2024-06-25 · 2024-06-25 · substantive · 45 CFR 164.502" in text
    assert "[-treatment-]{+treatment, payment,+} or [-payment,-]" in text
    at = [(d["kind"], d["at"]) for d in data["differences"]]
    assert ("added", "45 CFR 164.502(a)(2)") in at
    assert ("removed", "45 CFR 164.502(c)") in at
    message = await fails(
        client,
        "compare",
        citation="45 CFR 164.502",
        from_date=LATEST,
        to_date="2020-01-01",
    )
    assert "is after to_date" in message


async def test_compare_trims_long_blocks(client, monkeypatch):
    monkeypatch.setattr("ecfr_md.mcp.TRIMMED_BLOCK", 20)
    monkeypatch.setattr("ecfr_md.mcp.REDLINE_CONTEXT", 5)
    text, _ = await call(
        client, "compare", citation="45 CFR 164.502", from_date="2020-01-01"
    )
    assert "removed at 45 CFR 164.502(c): [-**(c)** *Old rule.…" in text
    assert " … " in text


async def test_references(client):
    text, data = await call(client, "references", citation="45 CFR 164.502")
    assert text.startswith("45 CFR 164.502 cites")
    assert "Part 160 Subpart C · 45/part-160/subpart-C (in (a))" in text
    assert "42 U.S.C. 1320d · https://uscode.house.gov" in text
    assert data["references"][0]["cited_in"] == ["(a)"]


async def test_resolve_citation(client):
    text, data = await call(
        client, "resolve_citation", text="45 C.F.R. § 164.502(a)(1) (2020)"
    )
    (place,) = data["places"]
    assert place["exists"] and place["date"] == "2020-10-01"
    assert place["bluebook"] == "45 C.F.R. § 164.502(a)(1) (2020)"
    assert "Permitted uses and disclosures" in text
    text, data = await call(
        client, "resolve_citation", text="45 CFR §§ 164.502, 164.5021"
    )
    assert [p["exists"] for p in data["places"]] == [True, False]
    assert "NOT FOUND: No section 164.5021" in text
    text, data = await call(client, "resolve_citation", text="45 CFR 164.504(a)")
    assert "NOT FOUND" in text  # A reserved section has no paragraphs.
    _, data = await call(client, "resolve_citation", text="45 CFR 164.502(a)(1)(i)")
    assert data["places"][0]["heading"] == "§ 164.502 Uses and disclosures."
    assert "Citations look like" in await fails(
        client, "resolve_citation", text="banana"
    )


async def test_verify_quote(client):
    text, data = await call(
        client,
        "verify_quote",
        citation="45 CFR 164.502(a)(1)(i)",
        text="To the individual;",
    )
    assert (
        text == "VERIFIED: the quote is in 45 CFR 164.502(a)(1)(i) (as of 2026-10-07)."
    )
    assert data["verified"]
    text, _ = await call(
        client, "verify_quote", citation="45 CFR 164.502(b)", text="To the individual"
    )
    assert text.startswith("WRONG PINPOINT: the quote is not in 45 CFR 164.502(b)")
    text, data = await call(
        client,
        "verify_quote",
        citation="45 CFR 164.502",
        text="A covered entity is permitted to share protected health information",
    )
    assert text.startswith("NOT FOUND word for word in 45 CFR 164.502")
    assert "[-share-]{+use or disclose+}" in text
    assert data["differences"]
    text, _ = await call(
        client,
        "verify_quote",
        citation="45 CFR 164.502",
        text="zebra quantum harmonica",
    )
    assert "nothing similar" in text


async def test_define(client):
    text, data = await call(
        client, "define", term="covered entity", scope="45 CFR 164.502"
    )
    assert text.startswith(
        '"Covered entity" for 45 CFR 164.502: 45 CFR 160.103 (definition of '
        '"Covered entity"); applies to 45 CFR Subtitle A Subchapter C'
    )
    assert "**(1)** A health plan." in text
    assert data["definitions"][0]["section"] == "45 CFR 160.103"
    text, data = await call(
        client, "define", term="covered entty", scope="45 CFR 164.502"
    )
    assert text.startswith('No definition of "covered entty" found for 45 CFR 164.502.')
    assert "Defined terms like it: Covered entity" in text
    assert "May be defined in:" in text and data["mentions"]


async def test_define_without_suggestions(client, api):
    api.json("/api/search/v1/results", {"results": [], "meta": {"total_pages": 1}})
    text, _ = await call(client, "define", term="zebra", scope="45 CFR 164.502")
    assert "Defined terms like it" not in text and "May be defined" not in text


async def test_export(client, navigator):
    root = navigator.export_root
    text, data = await call(client, "export", citation="45 CFR 164.502")
    assert data["files"] == ["45_164.502.md"] and (root / "45_164.502.md").exists()
    assert text.startswith("Saved 45 CFR 164.502 as markdown: 45_164.502.md (")
    assert "exists; pass overwrite=true" in await fails(
        client, "export", citation="45 CFR 164.502"
    )
    _, data = await call(
        client, "export", citation="45 CFR 164.502", format="html", path="html/s.html"
    )
    assert data["files"] == ["html/s.html"]
    _, data = await call(client, "export", citation="45 CFR 164.502", overwrite=True)
    text, data = await call(
        client, "export", citation="45 CFR Part 164", path="tree", tree=True
    )
    assert len(data["files"]) > 5 and "and " in text
    (root / "dir").mkdir()
    _, data = await call(
        client, "export", citation="45/164.502", path="dir", format="text"
    )
    assert data["files"] == ["dir/45_164.502.txt"]
    message = await fails(client, "export", citation="45 CFR 164.502", path="../x.md")
    assert "outside the export directory" in message


async def test_settings(client):
    text, data = await call(client, "settings")
    assert "base_url = https://www.ecfr.gov" in text and "Read-only" in text
    assert data["export_root"].endswith("out")
    (contents,) = await client.read_resource("ecfr-md:settings")
    assert (
        json.loads(contents.text)["settings"]["concurrency"]
        == data["settings"]["concurrency"]
    )


async def test_resources(client, monkeypatch):
    (text,) = await client.read_resource("cfr:45/164.502")
    assert text.mime_type == "text/markdown"
    assert text.text.startswith("# § 164.502 Uses and disclosures.")
    (titles,) = await client.read_resource("cfr:titles")
    assert titles.text.startswith("45 Public Welfare · 2026-10-07")
    monkeypatch.setattr("ecfr_md.mcp.MAX_RESOURCE_CHARS", 400)
    (cut,) = await client.read_resource("cfr:45/164.502")
    assert "The read tool continues: read(cursor=" in cut.text
    with pytest.raises(MCPError, match="did you mean"):
        await client.read_resource("cfr:45/164.5021")


async def test_titles_resource_offline(client, api, ecfr):
    api.text(f"{VERSIONER}/titles.json", "down", status=500)
    ecfr._titles = None
    with pytest.raises(MCPError, match="500"):
        await client.read_resource("cfr:titles")


async def test_prompts(client):
    for name, arguments, expected in [
        (
            "summarize_obligations",
            {"citation": "45 CFR 164.502"},
            'outline("45 CFR 164.502")',
        ),
        (
            "what_changed",
            {"citation": "45 CFR 164.502", "since": "2024-01-01"},
            "compare(",
        ),
        (
            "compare_provisions",
            {"first": "45 CFR 164.502", "second": "45 CFR 164.504"},
            "read(",
        ),
        (
            "find_term",
            {"term": "breach", "scope": "45 CFR Part 164"},
            'define("breach", "45 CFR Part 164")',
        ),
        ("find_term", {"term": "breach"}, 'search("breach means")'),
    ]:
        result = await client.get_prompt(name, arguments)
        (message,) = result.messages
        assert expected in message.content.text


async def test_completions(client):
    template = mcp_types.ResourceTemplateReference(
        type="ref/resource", uri="cfr:{address*}"
    )
    prompt = mcp_types.PromptReference(type="ref/prompt", name="summarize_obligations")
    found = await client.complete(template, {"name": "address", "value": "4"})
    assert found.values == ["45"]
    found = await client.complete(template, {"name": "address", "value": ""})
    assert found.values == ["45"]
    found = await client.complete(template, {"name": "address", "value": "45/16"})
    assert found.values == ["45/part-160", "45/part-164"]
    found = await client.complete(
        prompt, {"name": "citation", "value": "45 CFR 164.50"}
    )
    assert found.values == [
        "45 CFR 164.500",
        "45 CFR 164.501",
        "45 CFR 164.502",
    ]
    found = await client.complete(
        prompt, {"name": "citation", "value": "45 CFR Part 1"}
    )
    assert found.values == ["45 CFR Part 160", "45 CFR Part 164"]
    found = await client.complete(prompt, {"name": "citation", "value": "hipaa"})
    assert found.values == []
    found = await client.complete(prompt, {"name": "since", "value": "2"})
    assert found.values == []
    found = await client.complete(prompt, {"name": "citation", "value": "99/"})
    assert found.values == []  # No title 99: an error, so nothing.


async def test_progress_from_a_large_fetch(client, monkeypatch):
    monkeypatch.setattr(ecfr_md.client, "SPLIT_ABOVE", 1000)  # Part by part.
    events = []

    async def progress(done, total, message):
        events.append((done, total, message))

    await client.call_tool(
        "read", {"citation": "45", "budget_tokens": 200}, progress_handler=progress
    )
    assert events[0] == (0, 2, "Fetching 2 parts")
    assert (2, 2, "Part 164") in events or (2, 2, "Part 160") in events


async def test_relay_reports_and_cancels(navigator):
    sent = []

    class Context:
        request_context = SimpleNamespace(protocol_version="2025-06-18")

        async def report_progress(self, done, total, message):
            sent.append(("progress", done, total, message))

        async def log(self, message, level):
            sent.append(("log", level, message))

    loop = asyncio.get_running_loop()
    call = _Call(Context(), loop)  # type: ignore[arg-type]
    assert call.logs
    relay = navigator.relay
    assert isinstance(relay, Relay) and isinstance(relay.fallback, Progress)

    def events() -> None:
        relay.fetching("45 CFR 164", LATEST)  # type: ignore[arg-type]
        relay.started("u")
        relay.received("u", 300_000, None)
        relay.received("u", 310_000, None)  # Too soon to report.
        relay.received("u", 400_000, 400_000)
        relay.finished("u", 400_000, cached=False)
        relay.retrying("u", ValueError("x"), 2.0, 1, 5)
        relay.parts_started(2)
        relay.received("u", 1, 2)  # Parts report as they finish.
        relay.part_finished("160")

    token = _current.set(call)
    try:
        await anyio.to_thread.run_sync(events)
    finally:
        _current.reset(token)
    events()  # No call: nothing sent.
    await asyncio.sleep(0.05)
    assert ("log", "info", "Fetching 45 CFR 164 as of 2026-10-07") in sent
    assert ("progress", 300_000, None, "Downloading") in sent
    assert ("progress", 400_000, 400_000, "Downloading") in sent
    assert ("log", "warning", "Retrying in 2s (1/5)") in sent
    assert ("progress", 1, 2, "Part 160") in sent
    assert len([s for s in sent if s[0] == "progress"]) == 4
    call.cancelled.set()
    token = _current.set(call)
    try:
        with pytest.raises(Cancelled):
            relay.started("u")
    finally:
        _current.reset(token)


async def test_cancelled_calls_stop_their_work(navigator):
    started, release = threading.Event(), threading.Event()
    seen = []

    def work():
        started.set()
        release.wait(5)
        try:
            navigator.relay.started("u")
        except Cancelled:
            seen.append("stopped")

    class Context:
        request_context = None

    with anyio.CancelScope() as scope:
        async with anyio.create_task_group() as group:
            group.start_soon(navigator.run, Context(), work)
            await anyio.to_thread.run_sync(started.wait)
            scope.cancel()
    release.set()
    await anyio.to_thread.run_sync(lambda: [threading.Event().wait(0.1)])
    assert seen == ["stopped"]
    with pytest.raises(ToolError, match="Cancelled"):
        await navigator.run(None, lambda: (_ for _ in ()).throw(Cancelled()))


async def test_listen_notifies_on_change(client, watch, api, ecfr):
    uri = "cfr:45/164.502"
    watched = [uri, "cfr:99/1.1", "cfr:banana", "x:y"]
    async with listen(client.session, resource_subscriptions=watched) as sub:
        assert watch.watching[uri] == 1  # And cfr:99/1.1, which never fires.
        assert await watch.check() == []  # Nothing new yet.
        titles = json.loads(json.dumps(TITLES))
        titles["titles"][0]["latest_issue_date"] = "2026-11-01"
        api.json(f"{VERSIONER}/titles.json", titles)
        ecfr._titles = None
        assert await watch.check() == [uri]
        event = await anext(aiter(sub))
        assert event.uri == uri
        assert await watch.check() == []  # Already told.
    for _ in range(100):  # The server ends the stream soon after.
        if uri not in watch.watching:
            break
        await asyncio.sleep(0.01)
    assert uri not in watch.watching and uri not in watch.seen


async def test_unchanged_titles_and_dated_uris_never_fire(watch, api, ecfr):
    await watch._watch("cfr:45/164.502@2020-01-01")
    await watch._watch("cfr:45/164.404")
    titles = json.loads(json.dumps(TITLES))
    titles["titles"][0]["latest_issue_date"] = "2026-11-01"
    api.json(f"{VERSIONER}/titles.json", titles)
    api.json(VERSIONS, {"content_versions": []})  # Nothing in 164.404.
    ecfr._titles = None
    assert await watch.check() == []
    assert str(watch.seen["cfr:45/164.404"]) == "2026-11-01"


async def test_resources_subscribe_before_2026(watch, api, ecfr):
    """Clients of earlier MCP versions use resources/subscribe."""
    sent = []

    class Session:
        async def send_resource_updated(self, uri):
            sent.append(uri)

    ctx = SimpleNamespace(session=Session())
    uri = "cfr:45/164.502"
    await watch.subscribe(ctx, mcp_types.SubscribeRequestParams(uri=uri))
    titles = json.loads(json.dumps(TITLES))
    titles["titles"][0]["latest_issue_date"] = "2026-11-01"
    api.json(f"{VERSIONER}/titles.json", titles)
    ecfr._titles = None
    assert await watch.check() == [uri] and sent == [uri]
    await watch.unsubscribe(ctx, mcp_types.UnsubscribeRequestParams(uri=uri))
    await watch.unsubscribe(ctx, mcp_types.UnsubscribeRequestParams(uri=uri))
    assert not watch.watching
    with pytest.raises(MCPError, match="Citations look like"):
        await watch.subscribe(ctx, mcp_types.SubscribeRequestParams(uri="cfr:banana"))


async def test_watch_checks_while_watching(watch, monkeypatch):
    checks = []

    async def check():
        checks.append(1)
        if len(checks) == 1:
            raise ECFRConnectionError("offline")  # Tried again next time.
        raise asyncio.CancelledError

    monkeypatch.setattr(watch, "check", check)
    watch.interval = 0
    watch.watching["cfr:45/164.502"] = 1
    with pytest.raises(asyncio.CancelledError):
        await watch.run()
    assert checks == [1, 1]


def test_errors_teach():
    assert _teach(TitleNotFoundError("Title 99 does not exist")).endswith(
        "list_titles() lists the titles."
    )
    assert _teach(NodeNotFoundError("No 'x' in 45 CFR")).endswith(
        "outline() of the part or title shows what exists."
    )
    assert _teach(NodeNotFoundError("No y; did you mean z?")) == "No y; did you mean z?"
    assert _teach(InvalidCitationError("Bad.")).startswith("Bad. Citations look like")
    assert _teach(ECFRConnectionError("Down")).endswith("try again shortly.")
    assert _teach(Cancelled()) == "Cancelled."


def test_texts_are_kept_for_cursors(navigator, ecfr, api, monkeypatch):
    monkeypatch.setattr("ecfr_md.mcp.KEPT_NODES", 2)
    node, _ = navigator.kept.get(ecfr, "45 CFR 164.502", None)
    requests = len(api.requests)
    again, _ = navigator.kept.get(ecfr, node.uri, None)
    assert again is node and len(api.requests) == requests
    navigator.kept.get(ecfr, "45 CFR 164.404", None)
    assert len(navigator.kept._items) == 2  # The oldest went.


def test_around_without_a_client(part_xml):
    from ecfr_md import load
    from ecfr_md.mcp import _around, _located

    part = load(part_xml, title=45)
    parent, previous, following, children = _around(part["164.104"])
    assert parent.citation == "45 CFR Part 164 Subpart A"
    assert previous is following is None and children == []
    assert _located(TextIndex(part), "not in the text") is None


def test_formatting():
    assert [_approx(n) for n in (None, 5, 1500, 15_000, 2_500_000)] == [
        "size unknown",
        "≈5",
        "≈1.5k",
        "≈15k",
        "≈2.5M",
    ]
    assert _description("Part 164—Security") == "Security"
    assert _description("§ 164.502 Uses.") == "Uses."
    assert _description("Part 164") == "Part 164"


def test_without_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "fastmcp", None)
    monkeypatch.delitem(sys.modules, "ecfr_md.mcp")
    with pytest.raises(MissingDependencyError, match=r"ecfr-md\[mcp\]"):
        importlib.import_module("ecfr_md.mcp")


def test_server_makes_its_own_client():
    made = server()
    assert made.name == "ecfr"


async def test_http_app_serves_mcp(api, ecfr):
    install(api)
    app = server(ecfr).http_app()
    async with (
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://localhost"
        ) as http,
        app.router.lifespan_context(app),
    ):
        response = await http.post("/mcp", json={})
    assert response.status_code in (400, 406)
