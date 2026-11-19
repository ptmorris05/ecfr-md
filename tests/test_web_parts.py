"""The navigator's pieces on their own: settings forms, reading HTML, pages."""

import datetime as dt
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, Field
from starlette.testclient import TestClient
from web_data import APPENDIX, install

from ecfr_md import Settings
from ecfr_md.compare import Difference
from ecfr_md.config import read_config
from ecfr_md.errors import ECFRError, InvalidParameterError
from ecfr_md.render.blocks import Heading, Paragraph
from ecfr_md.web import create_app, pages, prefs, reader


@pytest.fixture
def api(api):
    return install(api)


# Settings ---------------------------------------------------------------------


class Choices(BaseModel):
    mode: Literal["auto", "api"] = Field(default="auto", description="How.")
    limits: list[int] = Field(default_factory=list, description="Limits.")
    where: Path | None = None
    either: int | str = 1


def test_fields_of_any_model():
    views = {v.name: v for v in prefs.fields(Choices(limits=[1, 2]))}
    assert views["mode"].kind == "choice" and views["mode"].choices == ("auto", "api")
    assert views["limits"].kind == "json" and views["limits"].value == "[1, 2]"
    assert views["limits"].origin == "set"
    assert views["where"].kind == "path" and views["where"].optional
    assert views["where"].value == ""
    assert views["either"].kind == "json"
    assert views["mode"].to_dict()["choices"] == ["auto", "api"]


def test_origins_from_explain(monkeypatch, tmp_path):
    config = tmp_path / "c.toml"
    config.write_text("timeout = 5.0\n", encoding="utf-8")
    monkeypatch.setenv("ECFR_MD_BASE_URL", "https://mirror.example")
    settings = Settings.load(config, origin="CLI", cache=False)
    views = {v.name: v.origin for v in prefs.fields(settings)}
    assert views["base_url"] == "env"
    assert views["timeout"] == "file"
    assert views["cache"] == "cli"
    assert views["concurrency"] == "default"
    changed, _ = prefs.parse({"timeout": "9"}, settings)
    assert {v.name: v.origin for v in prefs.fields(changed)}["timeout"] == "set here"
    assert changed.config_file == config


def test_parsing_forms():
    settings, changed = prefs.parse(
        {"rate_limit": "", "cache_dir": "~/c", "headers": "From: a\nbad line\n: x"},
        Settings(rate_limit=2.0),
    )
    assert settings.rate_limit is None and settings.cache is False
    assert settings.headers == {"From": "a", "bad line": ""}
    assert changed == {"rate_limit", "cache", "cache_dir", "headers", "learn_costs"}
    with pytest.raises(prefs.Invalid) as info:
        prefs.parse({"concurrency": "99", "cache": "on"}, Settings())
    assert list(info.value.problems) == ["concurrency"]


def test_parsing_json_fields(monkeypatch):
    # Settings fields that aren't text, numbers or switches are JSON.
    found, _ = prefs.parse({"limits": "[1, 2]"}, Choices())  # type: ignore[arg-type]
    assert found.limits == [1, 2]  # type: ignore[attr-defined]
    with pytest.raises(prefs.Invalid):
        prefs.parse({"limits": "nope"}, Choices())  # type: ignore[arg-type]


def test_writing_config_keeps_the_rest(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timeout = 5.0\n\n[headers]\nFrom = "me@example.com"\n')
    current = Settings.load(path, read=False, origin="CLI", rate_limit=1.0)
    settings, _ = prefs.parse(
        {"rate_limit": "0.5", "cache": "on", "learn_costs": "on"}, current
    )
    prefs.write_config(settings, path)
    # Only what the page set; the CLI's value and the file's others stay.
    assert Settings.load(path).explain()["rate_limit"][0] == 0.5
    assert read_config(path) == {
        "timeout": 5.0,
        "headers": {"From": "me@example.com"},
        "rate_limit": 0.5,
    }
    back, _ = prefs.parse(
        {"rate_limit": "", "cache": "on", "learn_costs": "on"}, settings
    )
    prefs.write_config(back, path)
    assert "rate_limit" not in path.read_text()
    assert prefs.dumps({"t": {"n": 1}}) == "\n[t]\nn = 1\n"
    assert prefs.dumps({"list": [1, "a"], "odd key": True}) == (
        'list = [1, "a"]\n"odd key" = true\n'
    )


# Reading HTML --------------------------------------------------------------------


def test_reader_pieces():
    html = "".join(
        reader.render(
            [
                Heading(1, "§ 1.1 *Scope*", None),
                Paragraph("A [link](#here) and **(a)** inside.", 1, ("p-1.1(a)",)),
                Paragraph(
                    "**(b)(1)(i)** Three markers, two anchors.",
                    1,
                    ("p-1.1(b)", "p-1.1(b)(1)"),
                ),
            ]
        )
    )
    assert '<span class="words"><em>Scope</em></span>' in html
    assert '<a href="#here">link</a>' in html
    assert (
        '<a class="mk" href="#p-1.1(b)">(b)</a>'
        '<a class="mk" href="#p-1.1(b)(1)">(1)</a>(i)' in html
    )
    assert reader._rest("**§ 1.1** Words", "§ 1.1") == "Words"


def test_redline_pieces():
    blocks = [
        Difference("removed", "Old words.", ""),
        Difference("added", "", "**(a)** New words."),
    ]
    html = "".join(
        reader.redline(
            blocks, paragraphs=[Paragraph("**(a)** New words.", 2, ("p-1.1(a)",))]
        )
    )
    assert '<div class="change removed"><del><p>Old words.</p>\n</del></div>' in html
    assert '<p id="p-1.1(a)" class="p d2">' in html


# Pages --------------------------------------------------------------------------------


def test_sizes():
    assert [pages.size(n) for n in (999, 1500, 25_000, 3_400_000, 5_000_000_000)] == [
        "999 B",
        "1.5 KB",
        "25 KB",
        "3.4 MB",
        "5.0 GB",
    ]
    assert pages.split_variant("a/b.md") == ("a/b", ".md")


def test_places(ecfr):
    place = pages.place(
        ecfr, pages.parse_page("/current/title-45/part-164/appendix-" + APPENDIX), None
    )
    assert place.crumbs()[-1].label == "Appendix A"
    assert place.title == f"45 CFR {APPENDIX} — {APPENDIX}—Matrix"
    assert place.counts(place.entry) == place.counts(place.entry) == (1, 0)
    subject = pages.place(ecfr, pages.parse_page("/current/title-45/subtitle-A"), None)
    assert pages.label(subject.entry.children[0].children[-1]) == "Part 170"
    heading = subject.entry.children[0].children[3]
    assert pages.label(heading) == "Other parts"
    assert subject.rest == "title-45/subtitle-A"
    dated = pages.place(
        ecfr, pages.parse_page("/current/title-45/subtitle-A"), dt.date(2020, 1, 1)
    )
    assert dated.rest == "title-45/subtitle-A"


def test_errors_by_kind(ecfr, monkeypatch):
    def raises(error):
        def place(*args, **kwargs):
            raise error

        return place

    with TestClient(create_app(ecfr), raise_server_exceptions=False) as client:
        monkeypatch.setattr(pages, "place", raises(InvalidParameterError("bad")))
        assert client.get("/current/title-45/part-164").status_code == 400
        monkeypatch.setattr(pages, "place", raises(ECFRError("odd")))
        response = client.get(
            "/current/title-45/part-164", headers={"Accept": "application/json"}
        )
        assert response.status_code == 502 and response.json()["error"] == "odd"
