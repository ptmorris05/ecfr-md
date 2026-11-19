"""Settings from Python, options, ECFR_MD_* variables and the config file."""

import json
from pathlib import Path

import httpx2
import pytest

import ecfr_md
from ecfr_md import ECFR, Settings, shortcuts
from ecfr_md.config import config_path, read_config, write_config
from ecfr_md.errors import InvalidParameterError

TITLES = "/api/versioner/v1/titles.json"


def test_load_takes_the_file_then_the_environment_then_arguments(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(
        'site_url = "https://file.example"\ntimeout = 9\nconcurrency = 1\n'
        '[headers]\nFrom = "file@example.com"\n'
    )
    env = {"ECFR_MD_TIMEOUT": "8", "ECFR_MD_CONCURRENCY": "3"}
    settings = Settings.load(file, env=env, concurrency=4)
    assert settings.site_url == "https://file.example"
    assert (settings.timeout, settings.concurrency) == (8, 4)
    explained = settings.explain()
    assert explained["site_url"] == ("https://file.example", f"file {file}")
    assert explained["timeout"] == (8.0, "env ECFR_MD_TIMEOUT")
    assert explained["concurrency"] == (4, "SDK")
    assert explained["headers"] == ({"From": "file@example.com"}, f"file {file}")
    assert explained["source"] == ("auto", "default")
    assert settings.config_file == file


def test_settings_made_directly_explain_themselves():
    explained = Settings(source="api").explain()
    assert explained["source"] == ("api", "SDK")
    assert explained["base_url"] == ("https://www.ecfr.gov", "default")


def test_the_config_file_comes_from_the_environment(isolated_config):
    isolated_config.write_text('source = "bulk"\n')
    assert Settings.load().source == "bulk"
    assert Settings().config_file == isolated_config


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("darwin", {}, "Library/Application Support/ecfr-md/config.toml"),
        ("linux", {}, ".config/ecfr-md/config.toml"),
        ("linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/ecfr-md/config.toml"),
        ("win32", {"LOCALAPPDATA": "C:/Data"}, "C:/Data/ecfr-md/config.toml"),
    ],
)
def test_default_config_file(monkeypatch, platform, env, expected):
    monkeypatch.setattr("sys.platform", platform)
    for key in ("XDG_CONFIG_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert config_path().as_posix().endswith(expected)


def test_headers_from_the_environment_are_json():
    settings = Settings.load(env={"ECFR_MD_HEADERS": '{"From": "a@b.c"}'})
    assert settings.headers == {"From": "a@b.c"}
    with pytest.raises(InvalidParameterError, match="JSON object"):
        Settings.load(env={"ECFR_MD_HEADERS": "From=a@b.c"})


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("nonsense = 1\n", "Unknown setting in"),
        ("a = 1\nb = 2\n", "Unknown settings in"),
        ("timeout = \n", "Can't read the config file"),
        ("concurrency = 99\n", "concurrency: Input should be less than"),
    ],
)
def test_bad_config_files_are_explained(tmp_path, text, message):
    file = tmp_path / "config.toml"
    file.write_text(text)
    with pytest.raises(InvalidParameterError, match=message):
        Settings.load(file, env={})


def test_config_files_round_trip(tmp_path):
    file = tmp_path / "deep" / "config.toml"
    values = {
        "source": "bulk",
        "cache": False,
        "concurrency": 1,
        "timeout": 2.5,
        "cache_dir": Path("/tmp/x"),
        "rate_limit": None,
        "headers": {"From": 'me "quoted"'},
    }
    write_config(file, values)
    assert read_config(file) == {
        "source": "bulk",
        "cache": False,
        "concurrency": 1,
        "timeout": 2.5,
        "cache_dir": str(Path("/tmp/x")),
        "headers": {"From": 'me "quoted"'},
    }
    assert read_config(tmp_path / "missing.toml") == {}


def test_unknown_settings_are_refused():
    with pytest.raises(ValueError, match="Extra inputs"):
        Settings(nonsense=1)  # type: ignore[call-arg]


def test_images_default_to_the_site():
    assert Settings(site_url="https://m.example").images == "https://m.example"
    assert Settings(image_url="https://i.example").images == "https://i.example"


def test_clients_read_settings_like_the_command_line(isolated_config, api):
    isolated_config.write_text('user_agent = "file/1"\n')
    with ECFR(transport=httpx2.MockTransport(api)) as ecfr:
        ecfr.api.versioner.titles()
    assert api.requests[0].headers["User-Agent"] == "file/1"


def test_configure_replaces_the_shared_client(monkeypatch):
    monkeypatch.setattr(shortcuts, "_shared", None)
    first = ecfr_md.configure(site_url="https://one.example")
    assert shortcuts.client() is first
    assert first.settings.explain()["site_url"][1] == "SDK"
    second = ecfr_md.configure(Settings(source="api"))
    assert shortcuts.client() is second and second.settings.source == "api"
    assert first._http.is_closed
    shortcuts._close()
    assert second._http.is_closed
    monkeypatch.setattr(shortcuts, "_shared", None)
    shortcuts._close()  # Nothing to close.


# The config command


def test_config_show_says_where_each_value_is_from(run, capsys, monkeypatch):
    monkeypatch.setenv("ECFR_MD_SOURCE", "api")
    assert run("--timeout", "7", "config", "show") == 0
    out = capsys.readouterr().out
    assert "timeout          7.0  (CLI)" in out
    assert "source           api  (env ECFR_MD_SOURCE)" in out
    assert "image_url        (none)  (default)" in out
    assert "All text comes from the eCFR API." in out


def test_config_show_json(run, capsys, isolated_config):
    assert run("--cache-dir", "/tmp/c", "config", "show", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["settings"]["cache_dir"] == {
        "value": str(Path("/tmp/c")),
        "from": "CLI",
    }
    assert data["config_file"] == str(isolated_config)
    assert data["sources"].startswith("Past dates")


def test_config_path(run, capsys, tmp_path):
    other = tmp_path / "other.toml"
    assert run("--config", str(other), "config", "path") == 0
    assert capsys.readouterr().out.strip() == str(other)


def test_config_set_and_unset(run, capsys, isolated_config):
    assert run("config", "set", "source", "bulk") == 0
    assert run("config", "set", "headers.From", "me@example.com") == 0
    assert run("config", "set", "bulk-min-bytes", "1000") == 0
    assert read_config(isolated_config) == {
        "source": "bulk",
        "bulk_min_bytes": 1000,
        "headers": {"From": "me@example.com"},
    }
    assert run("config", "unset", "headers.From") == 0
    assert run("config", "unset", "source") == 0
    assert read_config(isolated_config) == {"bulk_min_bytes": 1000}
    assert "Removed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (("set", "sorce", "api"), "did you mean source?"),
        (("set", "zzz", "1"), "No setting 'zzz' (ecfr-md"),
        (("set", "headers", "x"), "No setting 'headers'"),
        (("set", "source", "ftp"), "source: Input should be"),
        (("unset", "timeout"), "timeout isn't set"),
        (("unset", "headers.From"), "headers.From isn't set"),
    ],
)
def test_config_errors(run, capsys, tokens, message):
    assert run("config", *tokens) == 2
    assert message in capsys.readouterr().err


def test_config_edit_opens_an_editor_then_checks(run, capsys, monkeypatch):
    opened = []

    def editor(command, check):
        opened.append(command)
        Path(command[-1]).write_text("concurrency = 0\n")

    monkeypatch.setattr("ecfr_md.cli.subprocess.run", editor)
    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.delenv("VISUAL", raising=False)
    assert run("config", "edit") == 2
    assert opened[0][:2] == ["code", "--wait"]
    assert "concurrency: Input should be greater" in capsys.readouterr().err
    monkeypatch.delenv("EDITOR")
    monkeypatch.setattr("ecfr_md.cli.sys.platform", "win32")
    monkeypatch.setattr("ecfr_md.cli.subprocess.run", lambda command, check: None)
    Path(opened[0][-1]).write_text("")
    assert run("config", "edit") == 0
    assert "Checked" in capsys.readouterr().err


def test_config_edit_uses_vi_without_an_editor(run, monkeypatch, isolated_config):
    opened = []
    monkeypatch.setattr(
        "ecfr_md.cli.subprocess.run", lambda command, check: opened.append(command)
    )
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr("ecfr_md.cli.sys.platform", "linux")
    assert run("config", "edit") == 0
    assert opened == [["vi", str(isolated_config)]]
    assert isolated_config.exists()


def test_quiet_and_verbose_from_the_environment(
    run, capsys, caplog, monkeypatch, tmp_path
):
    monkeypatch.setenv("ECFR_MD_QUIET", "yes")
    assert run("get", "45", "CFR", "164", "-o", str(tmp_path / "a.md")) == 0
    assert capsys.readouterr().err == ""
    assert "one API request" not in caplog.text
    monkeypatch.setenv("ECFR_MD_QUIET", "0")
    monkeypatch.setenv("ECFR_MD_VERBOSE", "true")
    assert run("get", "45", "CFR", "164", "-o", str(tmp_path / "b.md")) == 0
    assert "45 CFR Part 164 from www.ecfr.gov: one API request" in caplog.text


def test_bad_settings_can_be_fixed(run, capsys, isolated_config, monkeypatch):
    isolated_config.write_text("concurrency = 99\n")
    assert run("config", "show") == 2
    assert "concurrency: Input should be less than" in capsys.readouterr().err
    assert run("config", "set", "concurrency", "2") == 0
    assert read_config(isolated_config) == {"concurrency": 2}
    monkeypatch.setenv("ECFR_MD_TIMEOUT", "never")
    assert run("config", "path") == 0
    assert capsys.readouterr().out.strip() == str(isolated_config)


def test_bad_settings_in_the_environment_are_usage_errors(run, capsys, monkeypatch):
    monkeypatch.setenv("ECFR_MD_CONCURRENCY", "many")
    assert run("titles") == 2
    assert "env ECFR_MD_CONCURRENCY" in capsys.readouterr().err
