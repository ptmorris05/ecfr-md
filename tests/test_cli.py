import json

import pytest


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


def test_get_flexible_date(run, api):
    api.text("/api/versioner/v1/full/2020-03-01/title-45.xml", "<DIV5 N='1'/>")
    assert run("get", "45", "--date", "March 2020") == 0


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("get",), "Say what to get"),
        (("get", "45", "--tree"), "--tree requires --output"),
        (("get", "45", "CFR", "164", "--part", "160"), "Conflicting part"),
    ],
)
def test_get_usage_errors(run, capsys, tokens, message):
    assert run(*tokens) == 2
    assert message in capsys.readouterr().err


def test_endpoint_commands_print_json(run, capsys):
    assert run("versioner", "titles") == 0
    titles = json.loads(capsys.readouterr().out)["titles"]
    assert titles[0]["up_to_date_as_of"] == "2026-10-07"


def test_global_options_configure_client(run, api):
    assert (
        run("--user-agent", "me/2", "--headers.From", "a@b.c", "versioner", "titles")
        == 0
    )
    assert api.requests[0].headers["User-Agent"] == "me/2"
    assert api.requests[0].headers["From"] == "a@b.c"


def test_environment_configures_client(run, api, monkeypatch):
    monkeypatch.setenv("ECFR_MD_USER_AGENT", "env/1")
    assert run("versioner", "titles") == 0
    assert api.requests[0].headers["User-Agent"] == "env/1"


def test_api_errors_exit_nonzero_with_message(run, api, capsys):
    api.text("/api/admin/v1/agencies.json", '{"error":"boom"}', status=400)
    assert run("admin", "agencies") == 1
    assert "BadRequestError" in capsys.readouterr().err


def test_invalid_arguments_exit_with_usage_code(run, capsys):
    with pytest.raises(SystemExit) as info:
        run("search", "results", "--per-page", "5000")
    assert info.value.code == 2
    assert "less than or equal to 1000" in capsys.readouterr().err


def test_ambiguous_paragraph_exits_with_candidates(run, capsys):
    assert run("get", "45 CFR 164.108(1)") == 1
    err = capsys.readouterr().err
    assert "AmbiguousNodeError" in err and "45 CFR 164.108(1)" in err


def test_out_of_range_date_is_a_usage_error(run, capsys):
    assert run("get", "45", "--date", "2030-01-01") == 2
    assert "available up to 2026-10-07" in capsys.readouterr().err


def test_quiet_prints_nothing_but_the_result(run, capsys, tmp_path):
    assert run("-q", "get", "45", "CFR", "164", "-o", str(tmp_path / "a.md")) == 0
    assert capsys.readouterr().err == ""
    assert run("--quiet", "versioner", "titles") == 0
    captured = capsys.readouterr()
    assert captured.err == "" and '"titles"' in captured.out


def test_quiet_still_shows_errors(run, capsys):
    assert run("-q", "get", "45 CFR 164.108(1)") == 1
    assert "AmbiguousNodeError" in capsys.readouterr().err


def test_get_from_a_list_of_citations(run, capsys, tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("# HIPAA\n45 CFR 164.104(a)\n\n45 CFR 164.106\n")
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
