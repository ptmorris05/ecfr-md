"""Bugs found by looking where earlier checks couldn't see."""

import httpx2
import pytest

from ecfr_md import ECFR, Settings, load
from ecfr_md import markdown as md
from ecfr_md.errors import DateOutOfRangeError

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"


def test_rowspan_keeps_cells_in_their_columns():
    xml = (
        "<TABLE><THEAD>"
        '<TR><TH rowspan="2">Area</TH><TH rowspan="2">Percent</TH>'
        '<TH colspan="2">Angles</TH></TR>'
        "<TR><TH>Left</TH><TH>Right</TH></TR></THEAD>"
        "<TBODY><TR><TD>A</TD><TD>80</TD><TD>16</TD><TD>49</TD></TR>"
        '<TR><TD rowspan="2">B</TD><TD>1</TD><TD>2</TD><TD>3</TD></TR>'
        "<TR><TD>4</TD><TD>5</TD><TD>6</TD></TR></TBODY></TABLE>"
    )
    lines = md._table(md.parse(xml)).splitlines()
    assert lines == [
        "| Area | Percent | Angles |  |",
        "| --- | --- | --- | --- |",
        "|  |  | Left | Right |",
        "| A | 80 | 16 | 49 |",
        "| B | 1 | 2 | 3 |",
        "|  | 4 | 5 | 6 |",
    ]


def test_malformed_spans_do_not_crash():
    xml = '<TABLE><TR><TD colspan="x">a</TD><TD rowspan="">b</TD></TR></TABLE>'
    assert "| a | b |" in md._table(md.parse(xml))


def test_paragraph_number_key_is_not_read_as_a_part():
    xml = (
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD>'
        "<P>(1) One.</P><P>(2) Two.</P></DIV8>"
    )
    section = load(xml, title=45)
    assert section["1"].text == "**(1)** One."


def test_sync_survives_a_renamed_folder(ecfr, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    (tmp_path / "part-164").rename(tmp_path / "hipaa")
    result = ecfr.sync(tmp_path)
    assert result.removed == []
    assert (tmp_path / "hipaa" / "subpart-a" / "section-164.104.md").exists()


def test_sync_skips_other_index_files(ecfr, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.md").write_text("# My docs\n", encoding="utf-8")
    assert len(ecfr.sync(tmp_path).unchanged) == 7
    assert (tmp_path / "docs" / "index.md").read_text(encoding="utf-8") == "# My docs\n"


def test_a_long_retry_after_does_not_stall_every_request(api):
    settings = Settings(backoff_initial=0, backoff_max=0.2, cache=False)
    api.sequence(
        FULL,
        httpx2.Response(429, text="", headers={"Retry-After": "3600"}),
        httpx2.Response(200, text="<DIV5 N='164'/>"),
    )
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.api.versioner.full(45, date="2026-10-07")
        assert ecfr._throttle._paused_until - __import__("time").monotonic() < 1


def test_earliest_version_reads_every_page(ecfr, api):
    def page(request):
        number = int(request.url.params.get("page", "1"))
        date = "2019-01-01" if number == 2 else "2021-01-01"
        version = {
            "date": date, "amendment_date": date, "issue_date": date,
            "identifier": "164.104", "name": "x", "part": "164",
            "substantive": True, "removed": False, "title": "45", "type": "section",
        }  # fmt: skip
        return httpx2.Response(
            200, json={"content_versions": [version], "meta": {"total_pages": "2"}}
        )

    api.routes["/api/versioner/v1/versions/title-45.json"] = page
    with pytest.raises(DateOutOfRangeError, match="2019-01-01"):
        ecfr.get("45 CFR 164.104", date="2018-01-01")


def test_appendices_fetched_by_part_get_no_title_parameter(ecfr, api, monkeypatch):
    monkeypatch.setattr("ecfr_md.client.SPLIT_ABOVE", 0)
    structure = {
        "identifier": "45", "label": "Title 45", "type": "title", "size": 9,
        "children": [{
            "identifier": "I", "label": "Chapter I", "type": "chapter",
            "children": [{"identifier": "Appendix A to Chapter I",
                          "label": "Appendix A", "type": "appendix"}],
        }],
    }  # fmt: skip
    api.json("/api/versioner/v1/structure/2026-10-07/title-45.json", structure)
    api.text(FULL, '<DIV9 N="Appendix A to Chapter I" TYPE="APPENDIX"/>')
    ecfr.get(45)
    params = dict(api.requests[-1].url.params)
    assert params == {"appendix": "Appendix A to Chapter I", "chapter": "I"}


def test_markdown_output_is_utf8_whatever_the_console_encoding(monkeypatch):
    import io

    from ecfr_md import cli

    raw = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli._print_markdown("§ 164.502 — ━ text\n")
    assert raw.getvalue().decode("utf-8") == "§ 164.502 — ━ text\n"


def test_files_from_different_titles_do_not_overwrite_each_other(tmp_path):
    xml = '<DIV8 N="425.706" TYPE="SECTION"><HEAD>§ 425.706</HEAD><P>{}</P></DIV8>'
    load(xml.format("Title 42"), title=42).save(tmp_path)
    load(xml.format("Title 45"), title=45).save(tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["42-cfr-425.706.md", "45-cfr-425.706.md"]


def test_retried_connection_errors_leave_no_stale_progress(api, settings):
    from rich.console import Console

    from ecfr_md.ui import Terminal

    terminal = Terminal(Console(file=__import__("io").StringIO()))
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx2.ConnectError("reset")
        return api(request)

    with ECFR(settings, transport=httpx2.MockTransport(flaky), progress=terminal) as e:
        e.api.versioner.full(45, date="2026-10-07", part="164")
    assert terminal._tasks == {}
    assert terminal._bars.tasks == []  # No bar left behind on screen.


@pytest.mark.parametrize(
    ("intro", "is_example"),
    [
        ("<I>Example 1.</I> Facts.", True),
        ("<I>Example.</I> Facts.", True),
        ("Examples: Facts.", True),
        ("Examples of records exempt from disclosure include:", False),
    ],
)
def test_only_example_labels_start_examples(intro, is_example):
    xml = (
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD><P>(a) Rule.</P>'
        f"<P>{intro}</P><P>(1) One.</P><P>(2) Two.</P></DIV8>"
    )
    section = load(xml, title=1)
    assert [p.identifier for p in section["a"]] == ([] if is_example else ["1", "2"])


def test_cached_text_is_not_reused_after_the_ecfr_rolls_back(api, part_xml, tmp_path):
    def titles(issued):
        title = {"number": 45, "name": "Public Welfare", "reserved": False}
        return {"titles": [title | {"latest_issue_date": issued,
                                    "up_to_date_as_of": "2026-10-07"}]}  # fmt: skip

    settings = Settings(cache_dir=tmp_path, backoff_initial=0)

    def fetch():
        with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
            ecfr.get("45 CFR Part 164")

    api.json("/api/versioner/v1/titles.json", titles("2026-10-05"))
    fetch()
    api.json("/api/versioner/v1/titles.json", titles("2026-10-01"))  # Rolled back.
    api.json("/api/versioner/v1/versions/title-45.json", {"content_versions": []})
    fetch()
    assert api.paths().count(FULL) == 2


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("(b)-(d) [Reserved]", ["b", "c", "d"]),
        ("(c)-(d) [Reserved]", ["c", "d"]),
        ("(3)-(5) [Reserved]", ["3", "4", "5"]),
        ("(A)-(C) [Reserved]", ["A", "B", "C"]),
        ("(ii)-(iv) [Reserved]", ["ii", "iii", "iv"]),
        ("(i)-(v) [Reserved]", ["i", "ii", "iii", "iv", "v"]),
    ],
)
def test_reserved_ranges_expand_at_their_own_level(line, expected):
    from ecfr_md.document import MARKER, _expand_ranges

    markers = [MARKER.findall(s)[-1][1] for s in _expand_ranges(line)]
    assert markers == expected


def test_cross_level_reserved_ranges_and_restated_branches():
    lines = [
        "(a)-(g)(1)(ii) [Reserved].",
        "(g)(1)(iii) Real text.",
        "(g)(2)-(i) [Reserved].",
        "(j) Next.",
    ]
    xml = (
        '<DIV8 N="1.25-3" TYPE="SECTION"><HEAD>S</HEAD>'
        + "".join(f"<P>{line}</P>" for line in lines)
        + "</DIV8>"
    )
    section = load(xml, title=26)
    assert [p.identifier for p in section] == list("abcdefghij")
    assert section["(g)(1)(iii)"].text == "**(g)(1)(iii)** Real text."
    assert [p.identifier for p in section["(g)(1)"]] == ["i", "ii", "iii"]
    assert [p.identifier for p in section["g"]] == ["1", "2"]
    assert section.paragraph_issues == ()
