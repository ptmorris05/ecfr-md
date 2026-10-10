"""Behaviour that coverage showed had no test."""

import datetime as dt
import email.utils
from pathlib import Path

import httpx2
import pytest

from ecfr_md import InvalidParameterError, load
from ecfr_md import markdown as md
from ecfr_md.cache import default_directory
from ecfr_md.errors import api_error
from ecfr_md.output import page_path, read_front_matter

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"


def test_sync_adds_files_for_new_sections_and_removes_empty_folders(
    ecfr, api, part_xml, tmp_path
):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    added = part_xml.replace(
        "</DIV6>",
        '<DIV8 N="164.110" TYPE="SECTION"><HEAD>§ 164.110 New.</HEAD>'
        "<P>(a) New text.</P></DIV8></DIV6>",
    )
    api.text(FULL, added)
    result = ecfr.sync(tmp_path)
    assert [p.name for p in result.added] == ["section-164.110.md"]

    without_subpart = (
        part_xml[: part_xml.index("<DIV6")]
        + part_xml[part_xml.index("</DIV6>") + len("</DIV6>") :]
    )
    api.text(FULL, without_subpart)
    result = ecfr.sync(tmp_path)
    assert len(result.removed) == 6
    assert not (tmp_path / "part-164" / "subpart-a").exists()


def test_sync_errors(ecfr, tmp_path):
    with pytest.raises(InvalidParameterError, match="doesn't exist"):
        ecfr.sync(tmp_path / "missing")
    (tmp_path / "notes.md").write_text("# Mine\n", encoding="utf-8")
    with pytest.raises(InvalidParameterError, match="No Markdown saved by ecfr-md"):
        ecfr.sync(tmp_path)


def test_images_link_to_the_ecfr():
    xml = (
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD>'
        '<P>See <img src="/graphics/er01.gif"/> and <img src="x.png"/>.</P>'
        '<MATH><img src="/graphics/eq.gif"/></MATH></DIV8>'
    )
    text = load(xml, title=1).to_markdown()
    assert "![](https://www.ecfr.gov/graphics/er01.gif)" in text
    assert "![](x.png)" in text
    assert "![](https://www.ecfr.gov/graphics/eq.gif)" in text


def test_row_spans_past_the_last_cell_of_a_row():
    xml = (
        '<TABLE><TR><TD>a</TD><TD rowspan="3">tall</TD></TR>'
        "<TR><TD>b</TD></TR><TR></TR></TABLE>"
    )
    assert md._grid(md.parse(xml)) == [["a", "tall"], ["b", ""], ["", ""]]


def test_division_inside_a_non_division_element():
    xml = (
        '<DIV5 N="1" TYPE="PART"><HEAD>Part 1</HEAD><EXTRACT>'
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>§ 1.1</HEAD><P>Quoted.</P></DIV8>'
        "</EXTRACT></DIV5>"
    )
    text = load(xml, title=1).to_markdown()
    assert "> ## § 1.1" in text and "> Quoted." in text


def test_empty_subheadings_and_paragraphs_are_dropped():
    xml = '<DIV8 N="1.1" TYPE="SECTION"><HEAD>S</HEAD><HD1> </HD1><P> </P></DIV8>'
    assert load(xml, title=1).to_markdown() == "# S\n"


def test_retry_after_as_an_http_date():
    later = email.utils.format_datetime(
        dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30), usegmt=True
    )
    for value, low, high in [(later, 20, 31), ("garbage", None, None)]:
        response = httpx2.Response(
            429,
            headers={"Retry-After": value},
            request=httpx2.Request("GET", "https://x/y"),
        )
        wait = api_error(response).retry_after
        if low is None:
            assert wait is None
        else:
            assert low <= wait <= high


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Caches/ecfr-md"),
        ("linux", {}, ".cache/ecfr-md"),
        ("linux", {"XDG_CACHE_HOME": "/xdg"}, "/xdg/ecfr-md"),
        ("win32", {"LOCALAPPDATA": "C:/AppData"}, "C:/AppData/ecfr-md/Cache"),
    ],
)
def test_default_cache_directory(monkeypatch, platform, env, expected):
    monkeypatch.setattr("sys.platform", platform)
    for key in ("XDG_CACHE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert default_directory().as_posix().endswith(expected)


def test_subject_group_files_in_a_tree(tmp_path: Path):
    xml = (
        '<DIV5 N="108" TYPE="PART"><HEAD>Part 108</HEAD>'
        '<DIV6 N="C" TYPE="SUBPART"><HEAD>Subpart C</HEAD>'
        '<DIV7 N="ECFR1a" TYPE="SUBJGRP"><HEAD>Organizing</HEAD>'
        '<DIV8 N="108.150" TYPE="SECTION"><HEAD>§ 108.150 X.</HEAD></DIV8>'
        "</DIV7></DIV6></DIV5>"
    )
    load(xml, title=13, date=dt.date(2026, 9, 15)).save(tmp_path, tree=True)
    index = tmp_path / "part-108/subpart-c/subject-group-ecfr1a/index.md"
    meta = read_front_matter(index)
    assert meta["citation"] == "13 CFR Part 108 Subpart C - Organizing"
    assert meta["source"].endswith("/title-13/part-108/subject-group-ECFR1a")


def test_malformed_metadata_is_ignored(tmp_path: Path):
    element = md.parse('<DIV8 N="1" hierarchy_metadata="{not json"/>')
    assert page_path(element) is None
    broken = tmp_path / "broken.md"
    broken.write_text('---\ncitation: "unterminated\n---\n', encoding="utf-8")
    assert read_front_matter(broken) is None
    assert read_front_matter(tmp_path / "missing.md") is None


def test_terminal_display_renders_progress_and_quiet_prints_nothing():
    import io

    from rich.console import Console

    from ecfr_md.ui import Terminal

    url = "https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/title-40.xml?part=60"
    out = io.StringIO()
    console = Console(file=out, width=100, force_terminal=True, color_system=None)
    terminal = Terminal(console)
    with terminal.live():
        terminal.fetching("40 CFR", dt.date(2026, 10, 7))
        terminal.parts_started(2)
        terminal.started(url)
        terminal.received(url, 500, 1000)
        terminal.retrying(url, ConnectionError("reset"), 1.5, 2, 5)
        terminal.rate_changed(2.0)
        terminal.part_finished("60")
        terminal.finished(url, 1000, cached=False)
        terminal.finished("https://x/cached", 10, cached=True)
        terminal.file_written(Path("a.md"))
        console.print(terminal)  # One frame of the live display.
    terminal.summary("Saved", {"Files": "1"})
    text = out.getvalue()
    for expected in (
        "40 CFR",
        "as of 2026-10-07",
        "Fetching 2 parts separately",
        "part 60: reset",
        "retry 2/5 in 1.5s",
        "Rate limited by the server",
        "1/2",
        "1 cached",
        "1 file written",
        "limit 2.0/s",
        "Saved",
        "1 retry",
    ):
        assert expected in text, expected

    quiet_out = io.StringIO()
    quiet = Terminal(Console(file=quiet_out, force_terminal=True), quiet=True)
    with quiet.live():
        quiet.parts_started(1)
        quiet.note("hidden")
    quiet.summary("Saved", {})
    assert quiet_out.getvalue() == ""
