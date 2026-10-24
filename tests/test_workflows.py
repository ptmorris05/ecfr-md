"""outline, changes, sync and search, against the fake API."""

import json

import pytest

from ecfr_md import AmbiguousNodeError, InvalidParameterError, NodeNotFoundError

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
VERSIONS = "/api/versioner/v1/versions/title-45.json"
RESULTS = "/api/search/v1/results"


def version(identifier, issued, effective, **extra):
    return {
        "date": effective, "amendment_date": effective, "issue_date": issued,
        "identifier": identifier, "name": f"§ {identifier}   Heading {identifier}.",
        "part": identifier.split(".")[0], "substantive": True, "removed": False,
        "title": "45", "type": "section",
    } | extra  # fmt: skip


# outline


def test_outline_finds_any_level(ecfr):
    title = ecfr.outline(45)
    assert title.heading == "Title 45—Public Welfare"
    assert title.citation == "45 CFR" and title.raw.identifier == "45"
    part = ecfr.outline("45 CFR Part 164")
    assert (part.level, part.identifier, part.citation) == (
        "part",
        "164",
        "45 CFR Part 164",
    )
    assert part.size == 5000 and part.parent is None
    assert ecfr.outline(45, subtitle="A").level == "subtitle"


def test_outline_navigates_like_a_node(ecfr):
    title = ecfr.outline(45)
    subtitle = title[0]
    assert title["A"] is subtitle and title["Subtitle A"] is subtitle
    assert title["subtitle a"] is subtitle  # Case is ignored if nothing else matches.
    assert title["164"].citation == "45 CFR Part 164"
    assert title["45 CFR Part 165"].reserved
    assert title["164"].parent is subtitle and len(subtitle) == 2
    assert [e.identifier for e in title] == ["A"]
    assert [e.identifier for e in title.iter("part")] == ["164", "165"]
    assert len(list(title.iter())) == 3
    assert title.get("999") is None and title.get(5) is None
    with pytest.raises(NodeNotFoundError, match="No '999' in 45 CFR"):
        title["999"]
    assert bool(title["164"]) and len(title["164"]) == 0
    assert str(title) == (
        "Title 45—Public Welfare\n"
        "  Subtitle A—Department of Health and Human Services\n"
        "    Part 164\n"
        "    Part 165 [Reserved]"
    )
    assert repr(subtitle).startswith("<Outline 45 CFR Subtitle A 'Subtitle A—")


def test_outline_ambiguous_identifier(ecfr, api):
    structure = {
        "identifier": "45", "label": "Title 45", "type": "title",
        "children": [
            {"identifier": "I", "label": "Chapter I", "type": "chapter"},
            {"identifier": "I", "label": "Subtitle <b>I</b>", "type": "subtitle"},
        ],
    }  # fmt: skip
    api.json("/api/versioner/v1/structure/2026-10-07/title-45.json", structure)
    title = ecfr.outline(45)
    assert title["Chapter I"].heading == "Chapter I"
    assert title[1].heading == "Subtitle I"  # Markup is removed.
    with pytest.raises(AmbiguousNodeError) as info:
        title["I"]
    assert len(info.value.candidates) == 2


def test_outline_command(run, capsys):
    assert run("outline", "45") == 0
    out = capsys.readouterr().out
    assert "Title 45—Public Welfare" in out and "Part 165 [Reserved]" in out
    assert run("outline", "45", "--depth", "1") == 0
    out = capsys.readouterr().out
    assert "Subtitle A" in out and "Part 164" not in out and "2 parts" in out
    assert run("outline", "45", "--depth", "0") == 0
    assert "1 subtitle" in capsys.readouterr().out
    assert run("outline", "45", "--json") == 0
    assert json.loads(capsys.readouterr().out)["identifier"] == "45"


# changes


@pytest.fixture
def versions(api):
    page = {
        "content_versions": [
            version("164.502", "2024-04-26", "2024-06-25"),
            version("164.512", "2024-04-26", "2024-06-25", substantive=False),
            version("164.530", "2025-01-01", "2025-01-01", removed=True),
        ],
        "meta": {"total_pages": "1"},
    }
    api.json(VERSIONS, page)
    return page


def test_changes_newest_first(ecfr, api, versions):
    found = ecfr.changes("45 CFR Part 164", since="2024-01-01")
    assert [v.identifier for v in found] == ["164.530", "164.502", "164.512"]
    params = api.requests[-1].url.params
    assert params["part"] == "164" and params["issue_date[gte]"] == "2024-01-01"


def test_changes_command(run, capsys, versions):
    assert run("changes", "45", "CFR", "Part", "164") == 0
    captured = capsys.readouterr()
    assert "removed" in captured.out and "editorial" in captured.out
    assert "164.502" in captured.out and "3 changes" in captured.err
    assert run("changes", "45 CFR Part 164", "--json") == 0
    assert len(json.loads(capsys.readouterr().out)) == 3


# sync


def test_sync_rewrites_only_what_changed(ecfr, api, part_xml, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    mine = tmp_path / "part-164" / "notes.md"
    mine.write_text("my own notes\n", encoding="utf-8")

    unchanged = ecfr.sync(tmp_path)
    assert (unchanged.changed, unchanged.added, unchanged.removed) == ([], [], [])
    assert len(unchanged.unchanged) == 7

    edited = part_xml.replace("Roman two.", "Roman two, amended.")
    edited = (
        edited[: edited.index('<DIV8 N="164.108"')] + edited[edited.index("</DIV6>") :]
    )
    api.text(FULL, edited)
    result = ecfr.sync(tmp_path)
    assert sorted(p.name for p in result.changed) == ["index.md", "section-164.104.md"]
    assert [p.name for p in result.removed] == ["section-164.108.md"]
    assert "amended" in (tmp_path / "part-164/subpart-a/section-164.104.md").read_text(
        encoding="utf-8"
    )
    assert mine.exists()  # Files ecfr-md didn't write are left alone.


def test_sync_single_file(ecfr, api, part_xml, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path / "hipaa.md")
    assert ecfr.sync(tmp_path / "hipaa.md").unchanged == [tmp_path / "hipaa.md"]
    api.text(FULL, part_xml.replace("Roman two.", "Roman two, amended."))
    assert ecfr.sync(tmp_path / "hipaa.md").changed == [tmp_path / "hipaa.md"]


def test_sync_needs_front_matter(ecfr, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path / "plain.md", front_matter=False)
    with pytest.raises(InvalidParameterError, match="front matter"):
        ecfr.sync(tmp_path / "plain.md")


def test_sync_command(run, capsys, tmp_path):
    assert run("get", "45", "CFR", "164", "--tree", "-o", str(tmp_path)) == 0
    capsys.readouterr()
    assert run("sync", str(tmp_path)) == 0
    assert "is up to date" in capsys.readouterr().err


# search


@pytest.fixture
def results(api):
    def result(section, ends_on=None):
        h = {"title": "45", "part": "164", "section": section}
        return {
            "starts_on": "2017-01-01", "ends_on": ends_on, "type": "Section",
            "hierarchy": h, "hierarchy_headings": h,
            "headings": {"section": f"<strong>§ {section}</strong> Heading"},
            "full_text_excerpt": "the <strong>minimum</strong> necessary rule.",
            "score": 1.0, "structure_index": 1, "reserved": False, "removed": False,
        }  # fmt: skip

    page = {
        "results": [
            result("164.104"),
            result("164.104", ends_on="2020-01-01"),  # A past version.
            result("164.106"),
        ],
        "meta": {"total_count": 3},
    }
    api.json(RESULTS, page)


def test_search_command_shows_current_matches(run, capsys, results):
    assert run("search", "minimum necessary") == 0
    captured = capsys.readouterr()
    assert captured.out.count("45 CFR 164.104") == 1
    assert "45 CFR 164.106" in captured.out and "minimum necessary" in captured.out
    assert "2 shown of 3 matches" in captured.err


def test_search_save(run, api, capsys, results, tmp_path):
    assert run("search", "minimum", "--save", str(tmp_path)) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "45-cfr-164.104.md",
        "45-cfr-164.106.md",
    ]
    full = [r for r in api.requests if r.url.path == FULL]
    assert len(full) == 1  # Both sections came from one request for part 164.


def test_search_api_subcommands_still_work(run, capsys, results):
    assert run("api", "search", "results", "minimum") == 0
    assert json.loads(capsys.readouterr().out)["meta"]["total_count"] == 3
