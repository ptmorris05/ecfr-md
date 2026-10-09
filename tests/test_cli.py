import functools
import json

import httpx2
import pytest

from cfr_md import ECFR, cli


@pytest.fixture
def run(api, monkeypatch):
    """Run the CLI against the fake API and return its exit code."""
    transport = httpx2.MockTransport(api)
    monkeypatch.setattr(cli, "ECFR", functools.partial(ECFR, transport=transport))

    def invoke(*tokens: str) -> int:
        return cli.app.meta(list(tokens), result_action="return_value")

    return invoke


def test_markdown_to_stdout(run, capsys, part_xml):
    assert run("markdown", "45", "--part", "164") == 0
    assert capsys.readouterr().out.startswith("# PART 164")


def test_markdown_to_file_and_tree(run, capsys, tmp_path):
    assert run("markdown", "45", "--part", "164", "-o", str(tmp_path / "a.md")) == 0
    assert (tmp_path / "a.md").read_text(encoding="utf-8").startswith("# PART 164")
    tree = str(tmp_path / "tree")
    assert run("markdown", "45", "--layout", "tree", "--output", tree) == 0
    assert (tmp_path / "tree" / "part-164" / "index.md").is_file()
    assert "Wrote 4 file(s)" in capsys.readouterr().err


def test_tree_layout_requires_output(run, capsys):
    assert run("markdown", "45", "--layout", "tree") == 2
    assert "--layout tree requires --output" in capsys.readouterr().err


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
    monkeypatch.setenv("CFR_MD_USER_AGENT", "env/1")
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
