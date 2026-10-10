"""Edge cases and error paths, one test per behaviour."""

import datetime as dt
import io
import runpy
import sys
from pathlib import PurePosixPath

import httpx2
import pytest
from rich.console import Console

from ecfr_md import (
    ECFR,
    Citation,
    InvalidCitationError,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    ResponseFormatError,
    ServerError,
    Settings,
    cli,
    load,
)
from ecfr_md import markdown as md
from ecfr_md.cache import Store
from ecfr_md.document import _range_paths
from ecfr_md.errors import api_error
from ecfr_md.output import read_front_matter
from ecfr_md.ui import describe, size

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
STRUCTURE = "/api/versioner/v1/structure/2026-10-07/title-45.json"


def section(*lines: str, n: str = "1.1"):
    body = "".join(f"<P>{line}</P>" for line in lines)
    return load(f'<DIV8 N="{n}" TYPE="SECTION"><HEAD>S</HEAD>{body}</DIV8>', title=1)


def ids(node):
    return [child.identifier for child in node]


# Citations


def test_section_keyword_and_invalid_section_numbers():
    assert Citation.parse("45 CFR section 164.502").hierarchy == {"section": "164.502"}
    with pytest.raises(InvalidCitationError, match="not a section number"):
        Citation(45).with_section("abc")


# Nodes


def test_node_lookups_at_the_edges(part_xml):
    part = load(part_xml, title=45, hierarchy={"part": "164"})
    assert 1.5 not in part  # Only strings and indexes are keys.
    assert part.find(level="title") is None
    with pytest.raises(NodeNotFoundError, match="not in title 45"):
        part["40 CFR 60.1"]
    with pytest.raises(NodeNotFoundError):
        part["Subpart Z9 extra"]  # A level word, then no match.
    assert part.slug == "part-164"
    assert part["A"].text == part["A"].to_markdown()


def test_paragraph_label_heading_and_tree():
    s = section("(a)(1) Text.")
    assert s["a"].label == "(a)"
    assert s["a"].heading == ""  # (a) has no text of its own.
    paragraph = s["(a)(1)"]
    assert paragraph.to_markdown_tree() == {
        PurePosixPath(f"{paragraph.slug}.md"): paragraph.to_markdown()
    }


def test_restated_branch_that_differs_starts_a_new_one():
    s = section("(a)(1) One.", "(b)(1) Two.")
    assert ids(s) == ["a", "b"] and ids(s["b"]) == ["1"]


def test_gaps_in_numbering_stay_at_their_level():
    s = section("(a) A.", "(1) One.", "(3) Three.")
    assert ids(s["a"]) == ["1", "3"]


def test_examples_before_any_paragraph_end_at_a():
    s = section("<I>Example.</I> Facts.", "(i) Detail.", "(a) Rule.")
    assert ids(s) == ["a"]


def test_examples_under_deep_paragraphs_resume_at_their_level():
    s = section(
        "(a) A.", "(1) One.", "(i) Deep.", "<I>Example 1.</I> Facts.", "(2) Two."
    )
    assert ids(s["a"]) == ["1", "2"]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (["c"], ["a"]),  # Backwards.
        (["a", "x"], ["a", "y"]),  # Not numbers at the second level.
        (["a"], ["b", "z"]),  # Not a number under (b).
        (["a", "1", "i", "A", "1", "i", "x"], ["a", "1", "i", "A", "1", "i", "y"]),
    ],
)
def test_ranges_that_do_not_fit_are_left_alone(start, end):
    assert _range_paths(start, end) is None


def test_ranges_sharing_a_parent():
    assert _range_paths(["g", "2"], ["g", "4"]) == [["g", "2"], ["3"], ["4"]]


# Markdown


def test_skipped_tags_line_breaks_and_captions():
    xml = (
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD><PRTPAGE P="5"/>'
        "<TABLE><CAPTION><P>Table 1</P><P>Rates</P></CAPTION>"
        "<TR><TD>a<br/>b</TD></TR></TABLE></DIV8>"
    )
    text = load(xml, title=1).to_markdown()
    assert "**Table 1**<br>**Rates**" in text
    assert "| a<br>b |" in text
    caption = md.parse("<TABLE><CAPTION>Plain caption</CAPTION></TABLE>")
    assert md._table(caption).startswith("**Plain caption**")


def test_front_matter_without_an_end_is_not_front_matter(tmp_path):
    path = tmp_path / "x.md"
    path.write_text('---\ncitation: "45 CFR 1.1"\n')
    assert read_front_matter(path) is None


def test_retry_after_dates_without_a_time_zone():
    response = httpx2.Response(
        429,
        headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 -0000"},
        request=httpx2.Request("GET", "https://x/y"),
    )
    assert api_error(response).retry_after > 0


# Cache


def test_store_without_a_directory_does_nothing(tmp_path):
    store = Store(None)
    assert store.find("full", 45, {}, dt.date(2026, 1, 1)) == []
    assert store.info() == (0, 0)
    entry = store.put(
        "full",
        45,
        {},
        tmp_path,
        date=dt.date(2026, 1, 1),
        token=dt.date(2026, 1, 1),
        latest=True,
        url="u",
        retrieved=dt.datetime.now(dt.UTC),
    )
    assert entry is None


def test_store_copies_when_links_are_not_supported(tmp_path, monkeypatch):
    def no_links(*_):
        raise OSError("links not supported")

    monkeypatch.setattr("os.link", no_links)
    source = tmp_path / "source.xml"
    source.write_text("<DIV5/>")
    store = Store(tmp_path / "cache")
    store.put(
        "full",
        45,
        {"part": "164"},
        source,
        date=dt.date(2026, 1, 1),
        token=dt.date(2026, 1, 1),
        latest=True,
        url="u",
        retrieved=dt.datetime.now(dt.UTC),
    )
    assert store.info()[0] == 2  # Exact and latest entries, copied.


def test_store_ignores_entries_for_other_dates(api, part_xml, tmp_path):
    settings = Settings(cache_dir=tmp_path, backoff_initial=0)
    api.text("/api/versioner/v1/full/2026-01-01/title-45.xml", part_xml)
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.get("45 CFR Part 164")  # Stored as the latest (2026-10-07).
        ecfr.get("45 CFR Part 164", date="2026-01-01")  # Earlier: not reusable.
    assert api.paths().count("/api/versioner/v1/full/2026-01-01/title-45.xml") == 1


@pytest.mark.parametrize(
    ("citation", "changed_identifier", "downloads"),
    [
        ("45 CFR 164.104", "164.999", 1),  # Another section changed: reuse.
        ("45 CFR 164.104", "164.104", 2),  # This section changed: download.
        (45, "164.999", 2),  # Anything in the title changed: download.
    ],
)
def test_store_change_checks_by_level(
    api, part_xml, tmp_path, citation, changed_identifier, downloads
):
    settings = Settings(cache_dir=tmp_path, backoff_initial=0)
    title = {"number": 45, "name": "x", "reserved": False}
    api.text("/api/versioner/v1/full/2026-10-08/title-45.xml", part_xml)

    def session(issued: str, up_to_date: str):
        state = title | {"latest_issue_date": issued, "up_to_date_as_of": up_to_date}
        api.json("/api/versioner/v1/titles.json", {"titles": [state]})
        with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
            ecfr.get(citation)

    session("2026-01-01", "2026-10-07")
    version = {
        "date": "2026-10-08", "amendment_date": "2026-10-08",
        "issue_date": "2026-10-08", "identifier": changed_identifier, "name": "x",
        "part": "164", "substantive": True, "removed": False,
        "title": "45", "type": "section",
    }  # fmt: skip
    api.json(
        "/api/versioner/v1/versions/title-45.json",
        {"content_versions": [version], "meta": {}},
    )
    session("2026-10-08", "2026-10-08")
    full = [p for p in api.paths() if "/full/" in p]
    assert len(full) == downloads


# Client


def test_outline_of_a_missing_chapter(ecfr):
    with pytest.raises(NodeNotFoundError, match="No chapter IX"):
        ecfr.outline(45, chapter="IX")


def test_invalid_outline_json(ecfr, api):
    api.json(STRUCTURE, {"unexpected": True})
    with pytest.raises(ResponseFormatError, match="outline of title 45"):
        ecfr.outline(45)


def test_part_requests_do_not_fall_back_on_504(ecfr, api):
    api.text(FULL, "Gateway Time-out", status=504)
    with pytest.raises(ServerError):
        ecfr.get("45 CFR Part 164")


def test_heading_only_outline_entries_become_subheadings(ecfr, api, monkeypatch):
    monkeypatch.setattr("ecfr_md.client.SPLIT_ABOVE", 0)
    structure = {
        "identifier": "45", "label": "Title 45", "type": "title", "size": 9,
        "children": [{"label": "Hearing Process", "type": "hed1"}],
    }  # fmt: skip
    api.json(STRUCTURE, structure)
    assert "**Hearing Process**" in ecfr.get(45).to_markdown()


def test_missing_text_within_the_date_range_is_not_a_date_problem(ecfr, api):
    version = {
        "date": "2017-01-03", "amendment_date": "2017-01-03",
        "issue_date": "2017-01-03", "identifier": "164.104", "name": "x",
        "part": "164", "substantive": True, "removed": False,
        "title": "45", "type": "section",
    }  # fmt: skip
    api.json(
        "/api/versioner/v1/versions/title-45.json", {"content_versions": [version]}
    )
    api.text(FULL, '{"error":"No matching content found."}', status=404)
    with pytest.raises(NotFoundError):
        ecfr.get("45 CFR 164.104")


def test_undecodable_responses(api, settings):
    def broken(request):
        raise httpx2.DecodingError("bad gzip", request=request)

    with (
        ECFR(settings, transport=httpx2.MockTransport(broken)) as ecfr,
        pytest.raises(ResponseFormatError, match="Could not decode"),
    ):
        ecfr.versioner.titles()


def test_every_endpoint_wrapper(ecfr, api):
    api.json("/api/admin/v1/corrections.json", {"ecfr_corrections": []})
    api.json("/api/search/v1/count", {"meta": {"total_count": 3}})
    api.json("/api/search/v1/summary", {"meta": {"description": "d"}})
    api.json("/api/search/v1/counts/daily", {"dates": {"2024-01-01": 2}})
    api.json("/api/search/v1/counts/titles", {"titles": {"45": 2}})
    api.json(
        "/api/search/v1/counts/hierarchy",
        {"count": {"value": 2, "relation": "eq"}, "children": []},
    )
    api.json("/api/search/v1/suggestions", {"suggestions": ["minimum"]})
    api.json("/api/versioner/v1/ancestry/2026-10-07/title-45.json", {"ancestors": []})
    assert ecfr.admin.corrections(title=45, date="2024-01-01").ecfr_corrections == []
    assert ecfr.search.count("x").meta.total_count == 3
    assert ecfr.search.summary("x").meta.description == "d"
    assert ecfr.search.daily_counts("x").dates == {dt.date(2024, 1, 1): 2}
    assert ecfr.search.title_counts("x").titles == {45: 2}
    assert ecfr.search.hierarchy_counts("x").count.value == 2
    assert ecfr.search.suggestions("min").suggestions == ["minimum"]
    assert ecfr.versioner.ancestry(45, part="164").ancestors == []
    assert ecfr.versioner.structure(45).identifier == "45"


# Command line


def test_get_formats_short_text_in_a_terminal(run, monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, force_terminal=True))
    assert run("get", "45 CFR 164.104(a)(1)") == 0
    assert "(1)" in out.getvalue() and "**" not in out.getvalue()


def test_get_prints_long_text_as_markdown_in_a_terminal(run, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "console", Console(file=io.StringIO(), force_terminal=True)
    )
    monkeypatch.setattr(cli, "FORMAT_BELOW", 10)
    assert run("get", "45", "CFR", "164") == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# PART 164")
    assert "use -o to save it" in captured.err


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("get", "--from", "list.txt"), "--from requires --output"),
        (("outline",), "Say which title or citation"),
    ],
)
def test_more_usage_errors(run, capsys, tokens, message):
    assert run(*tokens) == 2
    assert message in capsys.readouterr().err


def test_from_an_empty_list(run, capsys, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing here\n")
    assert run("get", "--from", str(empty), "-o", str(tmp_path / "out")) == 2
    assert "No citations" in capsys.readouterr().err


def test_versioner_full_prints_raw_xml(run, capsys):
    assert run("versioner", "full", "45", "--part", "164") == 0
    assert capsys.readouterr().out.startswith("<?xml")


def test_versioner_latest_date(run, capsys):
    assert run("versioner", "latest-date", "45") == 0
    assert capsys.readouterr().out.strip() == "2026-10-07"


def test_sync_lists_what_changed(run, capsys, tmp_path):
    assert run("get", "45", "CFR", "164", "--tree", "-o", str(tmp_path)) == 0
    edited = next(p for p in tmp_path.rglob("*.md") if p.name != "index.md")
    edited.write_text(
        edited.read_text(encoding="utf-8").replace("\n\n", "\n\nEdited.\n\n", 1)
    )
    capsys.readouterr()
    assert run("sync", str(tmp_path)) == 0
    assert "~ " in capsys.readouterr().err


def test_main_entry_point(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ecfr-md", "--version"])
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 0
    with pytest.raises(SystemExit):
        runpy.run_module("ecfr_md", run_name="__main__")


# Terminal display


def test_request_names_and_sizes():
    assert describe("https://www.ecfr.gov/api/other/thing") == "thing"
    assert [size(n) for n in (999, 1500, 2.5e6, 2.5e9, 3e12)] == [
        "999 B",
        "1.5 KB",
        "2.5 MB",
        "2.5 GB",
        "3000.0 GB",
    ]


def test_restarted_requests_replace_their_bar():
    from ecfr_md.ui import Terminal

    terminal = Terminal(Console(file=io.StringIO()))
    terminal.started("https://x/a")
    terminal.started("https://x/a")
    assert len(terminal._bars.tasks) == 1


def test_invalid_arguments_are_rejected_before_requests(ecfr, api):
    with pytest.raises(InvalidParameterError):
        ecfr.get_many(["45 CFR 164.104"], date="not a date")
    assert api.requests == []
