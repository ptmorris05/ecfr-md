"""Comparing a citation's text on two dates."""

import json

import pytest

from ecfr_md import Comparison, Difference, InvalidParameterError
from ecfr_md.compare import compare

OLD = "/api/versioner/v1/full/2024-01-01/title-45.xml"
NEW = "/api/versioner/v1/full/2026-10-07/title-45.xml"


def section(*paragraphs: str) -> str:
    body = "".join(f"<P>{p}</P>" for p in paragraphs)
    return f'<DIV8 N="164.502" TYPE="SECTION"><HEAD>§ 164.502 Uses.</HEAD>{body}</DIV8>'


@pytest.fixture
def versions(api):
    api.text(OLD, section("(a) Keep.", "(b) Old rule applies.", "(c) Gone."))
    api.text(NEW, section("(a) Keep.", "(b) New rule applies.", "(c) Inserted.", "(d) Gone."))  # fmt: skip  # noqa: E501
    return api


def test_compare_lines_up_paragraphs_and_words(ecfr, versions):
    found = ecfr.compare("45/164.502", "2024-01-01")  # Any form of citation.
    assert found.citation == "45 CFR 164.502"
    assert [d.kind for d in found.differences] == ["changed", "added", "changed"]
    rule = found.differences[0]
    assert ("-", "Old") in rule.words and ("+", "New") in rule.words
    assert "<del>Old</del><ins>New</ins>" in found.to_markdown()
    assert found.unchanged == 2  # The heading and (a).
    assert json.loads(json.dumps(found.to_dict()))["differences"][1]["kind"] == "added"


def test_unlike_blocks_are_removed_and_added_not_changed():
    found = compare("x", "Alpha beta gamma.\n\nKeep.", "Keep.\n\nZzz qqq.", *[None] * 2)  # type: ignore[arg-type]
    assert [d.kind for d in found.differences] == ["removed", "added"]
    assert found.differences[0].to_markdown() == "<del>Alpha beta gamma.</del>"


def test_no_changes(ecfr, versions):
    versions.text(OLD, section("(a) Keep."))
    versions.text(NEW, section("(a) Keep."))
    found = ecfr.compare("45 CFR 164.502", "2024-01-01")
    assert not found and found.to_markdown().endswith("No changes.\n")


def test_dates_in_the_wrong_order(ecfr, versions):
    with pytest.raises(InvalidParameterError, match="is after the second"):
        ecfr.compare("45 CFR 164.502", "2026-10-07", "2024-01-01")


def test_compare_command(run, versions, capsys):
    assert run("compare", "45", "CFR", "164.502", "--since", "2024-01-01") == 0
    captured = capsys.readouterr()
    assert "<del>Old</del><ins>New</ins>" in captured.out  # Piped: Markdown.
    assert "2 changed, 1 added" in captured.err
    assert run("compare", "45 CFR 164.502", "--since", "2024-01-01", "--json") == 0
    assert json.loads(capsys.readouterr().out)["since"] == "2024-01-01"


def test_redline_in_a_terminal(ecfr, versions):
    import io

    from rich.console import Console

    from ecfr_md.ui import redline

    screen = Console(file=io.StringIO(), force_terminal=True, no_color=True)
    screen.print(redline(ecfr.compare("45 CFR 164.502", "2024-01-01")))
    assert "Changed" in screen.file.getvalue() and "Added" in screen.file.getvalue()


def test_redline_shows_words_not_markdown():
    from ecfr_md.ui import redline

    found = compare("x", "*Old.* \\* \\* \\*", "**New.** \\* \\* \\*", *[None] * 2)  # type: ignore[arg-type]
    words = "".join(t.plain for t in redline(found).renderables[1::3])
    assert words == "Old.New. * * *"


def test_module_level_compare_and_types():
    assert Comparison.__doc__ and Difference.__doc__


def test_changes_points_to_compare_and_checks_dates(run, api, capsys):
    version = {
        "date": "2024-04-26", "amendment_date": "2024-04-26",
        "issue_date": "2024-04-26", "identifier": "164.502", "name": "§ 164.502 Uses.",
        "part": "164", "substantive": True, "removed": False,
        "title": "45", "type": "section",
    }  # fmt: skip
    api.json(
        "/api/versioner/v1/versions/title-45.json", {"content_versions": [version]}
    )
    assert run("changes", "45", "CFR", "164.502") == 0
    assert (
        "ecfr-md compare 45 CFR 164.502 --since 2024-04-25" in capsys.readouterr().err
    )
    assert run("changes", "45", "--since", "2025-01-01", "--until", "2024-01-01") == 2


def test_compare_in_a_terminal_and_from_python(run, versions, monkeypatch, capsys):
    import ecfr_md
    from ecfr_md import cli

    monkeypatch.setattr(type(cli.console), "is_terminal", property(lambda self: True))
    assert run("compare", "45 CFR 164.502", "--since", "2024-01-01") == 0
    assert "Changed" in capsys.readouterr().out
    monkeypatch.setattr(ecfr_md.shortcuts, "client", lambda: _client(versions))
    assert ecfr_md.compare("45 CFR 164.502", "2024-01-01")


def _client(api):
    import httpx2

    from ecfr_md import ECFR, Settings

    return ECFR(Settings(cache=False), transport=httpx2.MockTransport(api))
