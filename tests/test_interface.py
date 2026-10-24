"""The user-facing conveniences: module-level functions, search, result
objects, error messages and help."""

import atexit
import datetime as dt
import functools
import json

import httpx2
import pytest
from rich.console import Console

import ecfr_md
from ecfr_md import ECFR, Citation, InvalidCitationError, NodeNotFoundError, shortcuts
from ecfr_md.ui import Terminal

RESULTS = "/api/search/v1/results"
VERSIONS = "/api/versioner/v1/versions/title-45.json"


def result(section, excerpt="the rule."):
    h = {"title": "45", "part": "164", "section": section}
    return {
        "starts_on": "2017-01-01", "ends_on": None, "type": "Section",
        "hierarchy": h, "hierarchy_headings": h,
        "headings": {"section": f"§ {section} Heading &amp; more"},
        "full_text_excerpt": excerpt,
        "score": 1.0, "structure_index": 1, "reserved": False, "removed": False,
    }  # fmt: skip


def version(identifier, name):
    return {
        "date": "2024-01-01", "amendment_date": "2024-01-01",
        "issue_date": "2024-01-02", "identifier": identifier, "name": name,
        "part": "164", "substantive": False, "removed": False, "title": "45",
        "type": "appendix",
    }  # fmt: skip


# Citations without a title


@pytest.mark.parametrize("text", ["164.502", "§ 164.502", "CFR 164.502"])
def test_section_without_title_suggests_one(text):
    with pytest.raises(
        InvalidCitationError, match="needs a title number first"
    ) as info:
        Citation.parse(text)
    assert "e.g. 45 CFR 164.502" in str(info.value)


def test_other_text_explains_citations():
    with pytest.raises(InvalidCitationError, match="starts with a title number"):
        Citation.parse("Part 164")


@pytest.mark.parametrize("command", ["get", "outline", "changes"])
def test_words_instead_of_a_citation_suggest_search(run, capsys, command):
    assert run(command, "minimum", "necessary") == 2
    err = capsys.readouterr().err
    assert "Not a citation" in err and "ecfr-md search 'minimum necessary'" in err


# Missing paragraphs


@pytest.mark.parametrize(
    ("citation", "message"),
    [
        ("45 CFR 164.104(z)", "45 CFR 164.104 has (a) to (j)"),
        ("45 CFR 164.104(a)(9)", "45 CFR 164.104(a) has (1), (2)"),
        ("45 CFR 164.104(h)(1)(i)", "45 CFR 164.104(h) has no paragraphs"),
    ],
)
def test_missing_paragraph_lists_what_exists(ecfr, citation, message):
    with pytest.raises(NodeNotFoundError, match="does not exist on 2026-10-07") as info:
        ecfr.get(citation)
    assert str(info.value).endswith(message)


# Search


def test_search_quotes_phrases_and_pages(ecfr, api):
    pages = [
        {"results": [result("164.102")], "meta": {"total_count": 2, "total_pages": 2}},
        {"results": [result("164.104")], "meta": {"total_count": 2, "total_pages": 2}},
    ]
    api.routes[RESULTS] = lambda request: httpx2.Response(
        200, json=pages[int(request.url.params["page"]) - 1]
    )
    matches = ecfr.search("minimum necessary", limit=5)
    assert [m.citation for m in matches] == ["45 CFR 164.102", "45 CFR 164.104"]
    assert matches.total == 2
    queries = [r.url.params["query"] for r in api.requests if r.url.path == RESULTS]
    assert queries == ['"minimum necessary"'] * 2
    assert matches[0].heading == "§ 164.102 Heading & more"


@pytest.mark.parametrize(
    ("query", "phrase", "sent"),
    [
        ("minimum necessary", False, "minimum necessary"),
        ('"minimum necessary" rule', True, '"minimum necessary" rule'),
        ("minimum", True, "minimum"),
    ],
)
def test_search_leaves_other_queries_alone(ecfr, api, query, phrase, sent):
    api.json(RESULTS, {"results": [], "meta": {}})
    assert ecfr.search(query, phrase=phrase) == []
    assert api.requests[-1].url.params["query"] == sent


def test_excerpts_end_at_a_word(ecfr, api):
    api.json(
        RESULTS,
        {
            "results": [result("164.102", "cut off mid wor"), result("164.104", "")],
            "meta": {"total_count": 2},
        },
    )
    first, second = ecfr.search("x")
    assert first.excerpt == "cut off mid…" and second.excerpt == ""


def test_search_command_json_and_no_matches(run, api, capsys):
    api.json(RESULTS, {"results": [result("164.102")], "meta": {"total_count": 1}})
    assert run("search", "rule", "--json") == 0
    (record,) = json.loads(capsys.readouterr().out)
    assert record["citation"] == "45 CFR 164.102" and "raw" not in record
    api.json(RESULTS, {"results": [], "meta": {"total_count": 0}})
    assert run("search", "nothing", "--save", "out") == 0
    assert "No matches" in capsys.readouterr().err


# Changes


def test_change_headings_drop_the_number(ecfr, api):
    name = "Appendix A to Part 164—Forms"
    api.json(
        VERSIONS,
        {
            "content_versions": [
                version("Appendix A to Part 164", name),
                version("Appendix B", "Appendix B"),
            ],
            "meta": {"total_pages": 1},
        },
    )
    first, second = ecfr.changes("45 CFR Part 164")
    assert first.heading == "Forms" and first.kind == "editorial"
    assert first.citation == "45 CFR Appendix A to Part 164"
    assert first.issued == dt.date(2024, 1, 2) and first.effective == dt.date(
        2024, 1, 1
    )
    assert second.heading == "Appendix B"


def test_changes_to_one_section_show_its_heading_once(run, api, capsys):
    name = "§ 164.502   Uses and disclosures."
    one = version("164.502", name) | {"type": "section"}
    api.json(VERSIONS, {"content_versions": [one, one], "meta": {"total_pages": 1}})
    assert run("changes", "45 CFR 164.502") == 0
    out = capsys.readouterr().out
    assert out.count("Uses and disclosures.") == 1 and "Section" not in out
    api.json(VERSIONS, {"content_versions": [], "meta": {"total_pages": 1}})
    assert run("changes", "45 CFR 164.502") == 0
    assert "0 changes" in capsys.readouterr().err


# Module-level functions


def test_module_functions_share_one_client(api, monkeypatch, tmp_path):
    transport = httpx2.MockTransport(api)
    monkeypatch.setattr(shortcuts, "ECFR", functools.partial(ECFR, transport=transport))
    monkeypatch.setattr(shortcuts, "_shared", None)
    closers = []
    monkeypatch.setattr(atexit, "register", closers.append)
    api.json(RESULTS, {"results": [result("164.104")], "meta": {}})
    api.json(VERSIONS, {"content_versions": [], "meta": {"total_pages": 1}})

    assert ecfr_md.get("45 CFR 164.104").citation == "45 CFR 164.104"
    assert shortcuts.client() is shortcuts.client()
    assert len(closers) == 1
    assert [n.citation for n in ecfr_md.get_many(["45 CFR 164.102"])] == [
        "45 CFR 164.102"
    ]
    assert ecfr_md.save("45 CFR 164.102", tmp_path) == tmp_path / "45-cfr-164.102.md"
    files = ecfr_md.save("45 CFR Part 164", tmp_path / "tree", tree=True)
    assert len(files) == 7
    assert ecfr_md.outline("45 CFR Part 164").identifier == "164"
    assert ecfr_md.changes("45 CFR Part 164") == []
    assert ecfr_md.search("rule")[0].citation == "45 CFR 164.104"
    assert ecfr_md.sync(tmp_path / "tree").changed == []


# Terminal and help


def test_done_line_counts_retries_and_respects_quiet():
    console = Console(record=True, width=200)
    terminal = Terminal(console)
    terminal.requests, terminal.cached, terminal.retries = 2, 1, 1
    terminal.done("Saved")
    assert "✔ Saved  2 requests, 1 cached, 1 retry ·" in console.export_text()
    Terminal(console, quiet=True).done("Hidden")
    assert "Hidden" not in console.export_text()


def test_connection_options_are_listed_only_at_the_top(run, capsys):
    run("--help")
    assert "Connection" in capsys.readouterr().out
    run("get", "--help")
    out = capsys.readouterr().out
    assert "Connection" not in out and "--output" in out
    run("--help")
    assert "Connection" in capsys.readouterr().out
