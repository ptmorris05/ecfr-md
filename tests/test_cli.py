import datetime as dt
import io
import json
import logging

import pytest

from ecfr_md import cli
from ecfr_md.errors import ECFRWarning


def test_get_prints_markdown(run, capsys):
    assert run("get", "45", "CFR", "164") == 0
    assert capsys.readouterr().out.startswith("# PART 164")


def test_get_paragraph_citation(run, capsys):
    assert run("get", "45 CFR 164.104(a)(1)") == 0
    assert capsys.readouterr().out == "**(1)** *First.* One.\n"


def test_get_file_and_tree(run, capsys, tmp_path):
    assert run("get", "45", "--part", "164", "-o", str(tmp_path / "a.md")) == 0
    saved = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert saved.startswith('---\ncitation: "45 CFR Part 164"\n')
    assert (
        run(
            "get", "45", "CFR", "164", "-o", str(tmp_path / "b.md"), "--no-front-matter"
        )
        == 0
    )
    assert (tmp_path / "b.md").read_text(encoding="utf-8").startswith("# PART 164")
    assert run("get", "45", "CFR", "164", "--tree", "--output", str(tmp_path)) == 0
    assert (tmp_path / "part-164" / "index.md").is_file()
    err = capsys.readouterr().err
    assert "Saved" in err and "Files" in err and "7" in err


def test_get_all_titles(run, api, capsys, tmp_path):
    assert run("get", "--all", "-o", str(tmp_path / "flat")) == 0
    assert [p.name for p in (tmp_path / "flat").iterdir()] == ["45-cfr.md"]
    assert run("get", "--all", "--tree", "-o", str(tmp_path / "cfr")) == 0
    assert (tmp_path / "cfr" / "part-164" / "index.md").is_file()
    assert not any("title-35" in str(r.url) for r in api.requests)  # Reserved.
    assert "Titles" in capsys.readouterr().err
    assert run("sync", str(tmp_path / "cfr")) == 0


def test_get_flexible_date(run, api):
    api.text("/api/versioner/v1/full/2020-03-01/title-45.xml", "<DIV5 N='1'/>")
    assert run("get", "45", "--date", "March 2020") == 0


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("get",), "Say what to get"),
        (("get", "--all"), "--all requires --output"),
        (("get", "45", "--all", "-o", "out"), "leave out the citation"),
        (("get", "45", "--tree"), "--tree requires --output"),
        (("get", "45", "CFR", "164", "--part", "160"), "Conflicting part"),
    ],
)
def test_get_usage_errors(run, capsys, tokens, message):
    assert run(*tokens) == 2
    assert message in capsys.readouterr().err


def test_endpoint_commands_print_json(run, capsys):
    assert run("api", "versioner", "titles") == 0
    titles = json.loads(capsys.readouterr().out)["titles"]
    assert titles[0]["up_to_date_as_of"] == "2026-10-07"


def test_global_options_configure_client(run, api):
    assert (
        run(
            "--user-agent",
            "me/2",
            "--headers.From",
            "a@b.c",
            "api",
            "versioner",
            "titles",
        )
        == 0
    )
    assert api.requests[0].headers["User-Agent"] == "me/2"
    assert api.requests[0].headers["From"] == "a@b.c"


def test_environment_configures_client(run, api, monkeypatch):
    monkeypatch.setenv("ECFR_MD_USER_AGENT", "env/1")
    assert run("api", "versioner", "titles") == 0
    assert api.requests[0].headers["User-Agent"] == "env/1"


def test_api_errors_exit_nonzero_with_message(run, api, capsys):
    api.text("/api/admin/v1/agencies.json", '{"error":"boom"}', status=400)
    assert run("api", "admin", "agencies") == 1
    assert "eCFR API error" in capsys.readouterr().err


def test_invalid_arguments_exit_with_usage_code(run, capsys):
    with pytest.raises(SystemExit) as info:
        run("api", "search", "results", "--per-page", "5000")
    assert info.value.code == 2
    assert "less than or equal to 1000" in capsys.readouterr().err


def test_ambiguous_paragraph_exits_with_candidates(run, capsys):
    assert run("get", "45 CFR 164.108(1)") == 1
    err = capsys.readouterr().err
    assert "More than one match" in err and "45 CFR 164.108(1)" in err


def test_future_date_gives_the_latest_text_with_a_warning(run, capsys, tmp_path):
    with pytest.warns(ECFRWarning, match="no text after 2026-10-07"):
        assert (
            run("get", "45", "--date", "2030-01-01", "-o", str(tmp_path / "a.md")) == 0
        )
    assert 'date: "2026-10-07"' in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_quiet_prints_nothing_but_the_result(run, capsys, tmp_path):
    assert run("-q", "get", "45", "CFR", "164", "-o", str(tmp_path / "a.md")) == 0
    assert capsys.readouterr().err == ""
    assert run("--quiet", "api", "versioner", "titles") == 0
    captured = capsys.readouterr()
    assert captured.err == "" and '"titles"' in captured.out


def test_quiet_still_shows_errors(run, capsys):
    assert run("-q", "get", "45 CFR 164.108(1)") == 1
    assert "More than one match" in capsys.readouterr().err


def test_get_from_a_list_of_citations(run, capsys, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text(
        "# HIPAA\n45 CFR 164.104(a)\n\n45 CFR 164.106\n", encoding="utf-8"
    )
    assert run("get", "--from", str(listing), "-o", str(tmp_path / "out")) == 0
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["45-cfr-164.104-a.md", "45-cfr-164.106.md"]
    assert "Saved" in capsys.readouterr().err


def test_cache_info_and_clear(run, capsys, isolated_cache):
    assert run("get", "45", "CFR", "164") == 0  # Fills the cache.
    capsys.readouterr()
    assert run("cache", "info") == 0
    err = capsys.readouterr().err
    assert str(isolated_cache) in err.replace("\n", "") or "Entries" in err
    assert run("cache", "clear") == 0
    assert not isolated_cache.exists() or not any(isolated_cache.rglob("*"))


@pytest.mark.parametrize("tokens", [(), ("api",), ("cache",), ("api", "admin")])
def test_bare_command_groups_print_help(run, capsys, tokens):
    assert run(*tokens) == 0
    assert "Usage: ecfr-md" in capsys.readouterr().out


def test_a_closed_pipe_is_not_an_error(run, monkeypatch):
    def closed(text: str) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(cli, "_print_markdown", closed)
    monkeypatch.setattr(cli.os, "dup2", lambda *_: None)
    assert run("get", "45", "CFR", "164") == 0


def test_verbose_logs_only_our_requests(run):
    assert run("-v", "api", "versioner", "titles") == 0
    assert logging.getLogger("ecfr_md").level == logging.DEBUG
    # HTTP/2 libraries would log every header; they keep the root's WARNING.
    assert logging.getLogger("hpack").level == logging.NOTSET
    assert run("api", "versioner", "titles") == 0
    assert logging.getLogger("ecfr_md").level == logging.ERROR


def test_from_lists_comments_repeats_and_stdin(run, tmp_path, monkeypatch):
    text = (
        '"45 CFR 164.104"  # quoted, with a comment\n'
        "45 CFR 164.104\n"  # A repeat.
        "45 CFR 164.106, 164.108\n"  # A list.
    )
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(text.encode())))
    assert run("get", "--from", "-", "-o", str(tmp_path / "out")) == 0
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["45-cfr-164.104.md", "45-cfr-164.106.md", "45-cfr-164.108.md"]


def test_from_reports_every_bad_line(run, capsys, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("45 CFR 164.104\nbogus\n" + "nope\n" * 11, encoding="utf-8")
    assert run("get", "--from", str(listing), "-o", str(tmp_path / "out")) == 2
    err = capsys.readouterr().err
    assert "list.txt:2:" in err and "and 2 more" in err
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_from_saves_what_it_can(run, capsys, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("45 CFR 164.104\n45 CFR 164.999\n", encoding="utf-8")
    assert run("get", "--from", str(listing), "-o", str(tmp_path / "out")) == 1
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["45-cfr-164.104.md"]
    assert "1 of 2 citations could not be saved" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("get", "45", "--from", "x.txt", "-o", "d"), "leave out the citation"),
        (("get", "--all", "--from", "x.txt", "-o", "d"), "leave out --all"),
        (("get", "--from", "x.txt", "-o", "-"), "--from requires --output"),
        (("get", "45", "-o", "s.docx"), "use a name ending in .md"),
    ],
)
def test_output_usage_errors(run, capsys, tokens, message):
    assert run(*tokens) == 2
    assert message in capsys.readouterr().err


def test_tree_onto_a_file_is_explained(run, capsys, tmp_path):
    existing = tmp_path / "fut.md"
    existing.write_text("x", encoding="utf-8")
    assert run("get", "45", "--tree", "-o", str(existing)) == 2
    assert "pick a directory such as" in capsys.readouterr().err


def test_output_dash_and_new_directories(run, capsys, tmp_path):
    assert run("get", "45", "CFR", "164.104", "-o", "-") == 0
    assert capsys.readouterr().out.startswith("# §")
    assert run("get", "45", "CFR", "164.104", "-o", f"{tmp_path}/new/") == 0
    assert [p.name for p in (tmp_path / "new").iterdir()] == ["45-cfr-164.104.md"]


def test_terminals_get_panels_and_pipes_get_lines():
    from rich.console import Console

    from ecfr_md.errors import NodeNotFoundError
    from ecfr_md.ui import Terminal

    screen = Console(file=io.StringIO(), force_terminal=True, width=60, no_color=True)
    Terminal(screen).error(NodeNotFoundError("No part 9 in title 45"))
    Terminal(screen).summary("Saved", {"Files": "3"}, network=False)
    assert "✘ Not found" in screen.file.getvalue() and "╭" in screen.file.getvalue()
    pipe = Console(file=io.StringIO(), width=60)
    Terminal(pipe).error(NodeNotFoundError("No part 9 in title 45"))
    assert pipe.file.getvalue() == "✘ Not found: No part 9 in title 45\n"


def test_titles_and_bare_outline_list_the_titles(run, capsys):
    assert run("titles") == 0
    out = capsys.readouterr().out
    assert "Public Welfare" in out and "[Reserved]" in out
    assert run("outline", "--json") == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["citation"] == "45 CFR" and listed[1]["reserved"] is True


def test_outline_json_has_citations_and_honours_depth(run, capsys):
    assert run("outline", "45", "--json", "--depth", "1") == 0
    tree = json.loads(capsys.readouterr().out)
    assert tree["citation"] == "45 CFR"
    assert [c["citation"] for c in tree["children"]] == ["45 CFR Subtitle A"]
    assert "children" not in tree["children"][0]
    assert run("outline", "45", "--depth", "-1") == 2


def test_titles_from_python(ecfr):
    (welfare, reserved) = ecfr.titles()
    assert welfare.citation == "45 CFR" and welfare.latest == dt.date(2026, 10, 7)
    assert welfare.to_dict()["url"].endswith("/on/2026-10-07/title-45")
    assert reserved.reserved and reserved.to_dict(include_raw=True)["raw"]
    assert reserved.url.endswith("/current/title-35")


def test_reading_in_a_terminal(run, monkeypatch, capsys):
    monkeypatch.setattr(type(cli.console), "is_terminal", property(lambda self: True))
    screen = {"height": 40}
    monkeypatch.setattr(
        type(cli.console), "height", property(lambda self: screen["height"])
    )
    paged = []

    class Pager:
        def __init__(self, **kwargs):
            paged.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cli.console, "pager", Pager)
    assert run("get", "45 CFR 164.104(a)(2)(i)") == 0  # Short: no pager.
    captured = capsys.readouterr()
    assert "§ 164.104 Paragraph nesting." in captured.out  # The context above.
    assert (
        "45 CFR 164.104(a)(2)(i) · eCFR text as of 2026-10-07 · https://"
        in captured.err
    )
    assert paged == []
    screen["height"] = 5
    assert run("get", "45", "CFR", "164") == 0
    assert len(paged) == 1
    assert run("get", "45", "CFR", "164", "--no-pager") == 0
    assert len(paged) == 1


def test_footnote_marks_become_superscripts():
    from ecfr_md.ui import _superscripts

    assert _superscripts("APF<sup>3</sup> and n<sup>a</sup>") == "APF³ and nᵃ"
