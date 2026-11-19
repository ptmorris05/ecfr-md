"""The commands agents and scripts use: define, verify, mcp, and --json."""

import json

import pytest
from hipaa import install

import ecfr_md.mcp
from ecfr_md import cli


@pytest.fixture
def hipaa(api):
    install(api)


def test_get_json(run, capsys):
    assert run("get", "45 CFR 164.104", "--json") == 0
    assert json.loads(capsys.readouterr().out)["citation"] == "45 CFR 164.104"
    assert run("get", "45 CFR 164.104", "--json", "--format", "html") == 2
    assert "--json is --format json" in capsys.readouterr().err


def test_sync_json(run, capsys, tmp_path):
    assert run("get", "45 CFR 164.104", "-o", str(tmp_path / "s.md")) == 0
    assert run("sync", str(tmp_path), "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["unchanged"] == [str(tmp_path / "s.md")] and not result["dry_run"]
    assert result["moved"] == []


def test_cache_clear_json(run, capsys):
    assert run("cache", "clear", "--json") == 0
    assert set(json.loads(capsys.readouterr().out)) == {"entries", "bytes"}


def test_define(run, capsys, hipaa):
    assert run("define", "covered entity", "45", "CFR", "164.502") == 0
    out, err = capsys.readouterr()
    assert out.startswith("*Covered entity* means:")
    assert '45 CFR 160.103 (definition of "Covered entity")' in err
    assert "for 45 CFR Subtitle A Subchapter C" in err
    assert run("define", "use", "45 CFR 164.502", "--json") == 0
    found = json.loads(capsys.readouterr().out)
    assert found["definitions"][0]["citation"] == "45 CFR 164.501"
    assert run("define", "covered entty", "45 CFR 164.502") == 0
    err = capsys.readouterr().err
    assert "No definition of 'covered entty'" in err
    assert "Defined terms like it: Covered entity" in err
    assert "May be defined in 45 CFR 164.502" in err


def test_define_without_suggestions(run, capsys, api, hipaa):
    api.json("/api/search/v1/results", {"results": [], "meta": {"total_pages": 1}})
    assert run("define", "zebra", "45 CFR 164.502") == 0
    assert "Defined terms" not in capsys.readouterr().err


def test_verify(run, capsys, hipaa):
    assert run("verify", "45 CFR 164.502(a)(1)(i)", "--quote", "To the individual") == 0
    assert "Verified: in 45 CFR 164.502(a)(1)(i)" in capsys.readouterr().err
    assert run("verify", "45 CFR 164.502(b)", "--quote", "To the individual") == 1
    assert "but word for word in 45 CFR 164.502(a)(1)(i)" in capsys.readouterr().err
    assert run("verify", "45 CFR 164.502", "--quote", "To the individuals") == 1
    assert "nearest is 45 CFR 164.502(a)(1)(i)" in capsys.readouterr().err
    assert run("verify", "45 CFR 164.502", "--quote", "zebra quantum harmonica") == 1
    assert "nor anything like it" in capsys.readouterr().err
    assert (
        run("verify", "45 CFR 164.502(b)", "--quote", "To the individual", "--json")
        == 1
    )
    assert json.loads(capsys.readouterr().out)["found"]


def test_mcp_command(run, monkeypatch, tmp_path, recwarn):
    runs = []

    def fake_run(self, transport=None, show_banner=None, **kwargs):
        runs.append((self, transport, kwargs))

    built = []
    real = ecfr_md.mcp.server

    def server(ecfr, **kwargs):
        built.append(kwargs)
        return real(ecfr, **kwargs)

    monkeypatch.setattr(ecfr_md.mcp.FastMCP, "run", fake_run)
    monkeypatch.setattr(ecfr_md.mcp, "server", server)
    assert run("mcp") == 0
    assert runs[-1][1] is None and built[-1]["export_root"] is not None
    assert not built[-1]["allow_config_changes"]
    assert run("mcp", "--no-export", "--allow-config-changes") == 0
    assert built[-1]["export_root"] is None and built[-1]["allow_config_changes"]
    assert run("mcp", "--http", "--port", "9000", "--export-dir", str(tmp_path)) == 0
    assert runs[-1][1] == "http" and runs[-1][2] == {"host": "127.0.0.1", "port": 9000}
    assert built[-1]["export_root"] == tmp_path
    assert not [w for w in recwarn if "other machines" in str(w.message)]
    assert run("mcp", "--http", "--host", "0.0.0.0") == 0
    assert any("other machines can connect" in str(w.message) for w in recwarn)


def test_every_command_has_json():
    """Agents can ask any command for JSON (api commands always print it)."""
    import inspect

    for name in ("get", "search", "outline", "references", "cited_by", "titles"):
        assert "as_json" in inspect.signature(getattr(cli, name)).parameters
    for name in ("changes", "compare", "define", "verify", "sync_command"):
        assert "as_json" in inspect.signature(getattr(cli, name)).parameters
    assert "as_json" in inspect.signature(cli.clear).parameters
    assert "as_json" in inspect.signature(cli.info).parameters
