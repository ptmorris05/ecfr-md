"""Addresses in use: node and outline lookups, the client, the CLI and sync."""

import datetime as dt
import json
import xml.etree.ElementTree as ET

import pytest

from ecfr_md import (
    Address,
    AmbiguousNodeError,
    InvalidParameterError,
    NodeNotFoundError,
    load,
)
from ecfr_md import cli as cli_module
from ecfr_md.output import legacy_file_name, read_front_matter

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
FULL_2018 = "/api/versioner/v1/full/2018-10-01/title-45.xml"
STRUCTURE = "/api/versioner/v1/structure/2026-10-07/title-45.json"
GROUP = "ECFR1a"


def group_xml(part_xml: str) -> str:
    """Part 164 with sections 164.106 and 164.108 in a subject group."""
    start = part_xml.index('<DIV8 N="164.106"')
    end = part_xml.index("</DIV6>")
    return (
        part_xml[:start]
        + f'<DIV7 N="{GROUP}" TYPE="SUBJGRP"><HEAD>Other rules</HEAD>'
        + "<P>Read with § 164.102.</P>"
        + part_xml[start:end]
        + "</DIV7>"
        + part_xml[end:]
    )


def entry(kind: str, n: str, *children: dict) -> dict:
    return {"identifier": n, "label": f"{kind} {n}", "type": kind, "children": list(children)}  # fmt: skip  # noqa: E501


STRUCTURE_WITH_GROUP = entry(
    "title",
    "45",
    entry(
        "part",
        "164",
        entry(
            "subpart",
            "A",
            entry("section", "164.102"),
            entry("section", "164.104"),
            entry("subject_group", GROUP, entry("section", "164.106")),
        ),
        entry("appendix", "Appendix A to Subpart C of Part 164"),
        entry("appendix", "Appendix A to Part 164"),
    ),
)


@pytest.fixture
def grouped(api, part_xml):
    api.text(FULL, group_xml(part_xml))
    api.json(STRUCTURE, STRUCTURE_WITH_GROUP)
    return api


@pytest.fixture
def part(part_xml):
    return load(
        group_xml(part_xml),
        title=45,
        date=dt.date(2026, 10, 7),
        hierarchy={"part": "164"},
    )


def test_nodes_have_addresses(part):
    paragraph = part["164.104(a)(1)"]
    assert paragraph.address == "45/164.104/a/1"
    assert paragraph.uri == "cfr:45/164.104/a/1@2026-10-07"
    assert paragraph.markers == paragraph.path == ("a", "1")
    assert paragraph.slug == "1" and paragraph.occurrence is None
    assert paragraph.cite() == paragraph.citation == "45 CFR 164.104(a)(1)"
    assert paragraph.cite("bluebook") == "45 C.F.R. § 164.104(a)(1) (2026)"
    assert paragraph.cite("bluebook", year=2025).endswith("(2025)")
    assert part.address == "45/part-164" and part.slug == "part-164"
    assert part["A"].address == "45/part-164/subpart-A"
    assert part["164.104"].slug == "164.104"
    group = part[f"group-{GROUP}"]
    assert group.address == f"45/part-164/subpart-A/group-{GROUP}"
    assert group.slug == f"group-{GROUP}"
    appendix = part["Appendix A to Subpart C of Part 164"]
    assert appendix.address == "45/part-164/subpart-C/appendix-A"
    assert appendix.slug == "appendix-A"
    data = paragraph.to_dict(depth=0, text=False)
    assert list(data)[:3] == ["citation", "address", "uri"]


def test_repeated_markers_have_occurrences(part):
    section = part["164.108"]
    first, _, second = section.children
    assert (first.slug, second.slug) == ("1~1", "1~2")
    assert first.occurrence == 1 and second.occurrence == 2
    assert second.address == "45/164.108/1~2"
    assert part["164.108/1~2"] is second
    assert section["1~2"] is second
    assert section["1~1/"] is first
    with pytest.raises(AmbiguousNodeError, match=r"45/164\.108/1~1, 45/164\.108/1~2"):
        part["164.108/1"]
    with pytest.raises(NodeNotFoundError, match=r"No \(1\) number 3 .* it has 2"):
        section["1~3"]


def test_nodes_are_indexed_by_relative_addresses(part):
    assert part["subpart-A"] is part["A"]
    assert part["subpart-a"] is part["A"]  # Old file names were lowercase.
    assert part["164.104/a/1"] is part["164.104(a)(1)"]
    assert part["164.104"]["a/1"] is part["164.104(a)(1)"]
    assert part["164.104(a)"]["1/"] is part["164.104(a)(1)"]
    assert part["45/164.104"] is part["164.104"]
    assert part[Address.parse("45 CFR 164.104(a)")] is part["164.104(a)"]
    assert part["appendix-A"] is part["Appendix A to Subpart C of Part 164"]
    assert Address.parse("45/164.104") in part
    assert part.get("164.999/a") is None
    assert "45/foo" not in part  # Not an address, nor an identifier here.
    with pytest.raises(NodeNotFoundError, match="is not in title 45"):
        part["40/60.1"]
    with pytest.raises(NodeNotFoundError, match="No 'appendix-B to subpart C'"):
        part["subpart-C/appendix-B"]
    section = load(
        '<DIV8 N="1.1" TYPE="SECTION"><HEAD>§ 1.1</HEAD><P>(a) A.</P></DIV8>',
        title=1,
        hierarchy={"part": "1", "section": "1.1"},
    )
    assert section["1/1.1/a"] is section["a"]  # Already in part 1 and § 1.1.


def test_appendices_beside_their_subpart():
    xml = (
        '<DIV5 N="9" TYPE="PART"><HEAD>Part 9</HEAD>'
        '<DIV6 N="C" TYPE="SUBPART"><HEAD>Subpart C</HEAD>'
        '<DIV9 N="Appendix A to Subpart C of Part 9" TYPE="APPENDIX">'
        "<HEAD>A</HEAD></DIV9>"
        "</DIV6>"
        '<DIV9 N="Appendix A to Part 9" TYPE="APPENDIX"><HEAD>A</HEAD></DIV9>'
        "</DIV5>"
    )
    part = load(xml, title=1)
    to_part, to_subpart = part["Appendix A to Part 9"], part["C"].children[0]
    assert part["appendix-A"] is to_part
    assert part["subpart-C/appendix-A"] is to_subpart
    assert to_subpart.address == "1/part-9/subpart-C/appendix-A"
    files = part.to_markdown_tree()
    assert sorted(str(p) for p in files) == [
        "part-9/appendix-A.md",
        "part-9/index.md",
        "part-9/subpart-C/appendix-A.md",
        "part-9/subpart-C/index.md",
    ]


def test_subject_groups_are_flattened_in_trees(part):
    files = part.to_markdown_tree()
    assert "part-164/subpart-A/164.106.md" in {str(p) for p in files}
    index = next(body for p, body in files.items() if str(p).endswith("A/index.md"))
    assert "## Other rules\n\nRead with § 164.102.\n\n- [§ 164.106" in index


def test_outlines_have_addresses(ecfr, grouped):
    outline = ecfr.outline("45/part-164")
    assert outline.address == "45/part-164"
    assert outline.uri == "cfr:45/part-164@2026-10-07"
    assert outline["subpart-A"].address == "45/part-164/subpart-A"
    assert outline["164.104"].address == "45/164.104"
    assert outline[Address.parse("45/164.106")].identifier == "164.106"
    assert outline.to_dict(depth=0)["address"] == "45/part-164"
    assert outline["subpart-C/appendix-A"].identifier == (
        "Appendix A to Subpart C of Part 164"
    )
    assert outline["appendix-A"].identifier == "Appendix A to Part 164"
    with pytest.raises(NodeNotFoundError, match="is not in title 45"):
        outline["12/part-1"]
    group = ecfr.outline(f"45/part-164/subpart-A/group-{GROUP}")
    assert group.identifier == GROUP
    unknown = load("<DIV5 N='1' TYPE='PART'/>", title=1)
    assert unknown.address == "1/part-1"


def test_other_results_have_addresses(ecfr, api):
    title = ecfr.titles()[0]
    assert (title.address, title.uri) == ("45", "cfr:45@2026-10-07")
    assert title.to_dict()["address"] == "45"
    reserved = ecfr.titles()[1]
    assert reserved.uri == "cfr:35"


def test_get_by_address_link_or_file_name(ecfr, api):
    for text in (
        "45/164.104/a/1",
        "https://www.ecfr.gov/current/title-45/section-164.104#p-164.104(a)(1)",
        "45_164.104_a_1.md",
        "45 CFR 164.104 a 1",
        "/us/cfr/t45/s164.104/a/1",
    ):
        assert ecfr.get(text).citation == "45 CFR 164.104(a)(1)"
    assert ecfr.get("45/164.108/1~2").blocks == ["**(1)** Uno."]
    with pytest.raises(AmbiguousNodeError, match=r"45/164\.108/1~1"):
        ecfr.get("45/164.108/1")
    with pytest.raises(NodeNotFoundError, match="has \\(a\\), \\(b\\)"):
        ecfr.get("45/164.106/z")


def test_dates_come_from_the_address(ecfr, api, part_xml):
    api.text(FULL_2018, part_xml)
    node = ecfr.get("45 C.F.R. § 164.104 (2018)")  # The 2018 edition.
    assert node.date == dt.date(2018, 10, 1)
    assert node.provenance["pinned"] == "2018-10-01"
    assert ecfr.get("45/164.104@2018-10-01", date="2018-10-01").date == node.date
    with pytest.raises(InvalidParameterError, match="as of 2018-10-01, but date"):
        ecfr.get("45/164.104@2018-10-01", date="2020-01-01")


def test_groups_and_appendices_by_address(ecfr, grouped, tmp_path):
    group = ecfr.get(f"45/part-164/subpart-A/group-{GROUP}")
    assert group.heading == "Other rules"
    with pytest.raises(NodeNotFoundError, match="No 'ECFRzz'"):
        ecfr.get("45/part-164/subpart-A/group-ECFRzz")
    ecfr.get("45/part-164/subpart-C/appendix-A")
    assert dict(grouped.requests[-1].url.params) == {
        "part": "164",
        "appendix": "Appendix A to Subpart C of Part 164",
    }
    ecfr.get("45/part-164/appendix-A")
    assert grouped.requests[-1].url.params["appendix"] == "Appendix A to Part 164"
    tree = ecfr.save("45/part-164", tmp_path / "tree", tree=True)
    assert tmp_path / "tree/part-164/subpart-A/164.106.md" in tree
    saved = ecfr.save(f"45/part-164/subpart-A/group-{GROUP}", tmp_path)
    assert saved.name == f"45_part-164_subpart-A_group-{GROUP}.md"


def test_sections_named_as_addresses_are_looked_up(ecfr, api, part_xml):
    xml = part_xml.replace('N="164.108"', 'N="164.108(T)"')
    api.text(FULL, xml)
    structure = entry(
        "title", "45", entry("part", "164", entry("section", "164.108(T)"))
    )
    api.json(STRUCTURE, structure)
    ecfr.get("45/164.108.T")
    assert api.requests[-1].url.params["section"] == "164.108(T)"


def test_changes_and_search_accept_addresses(ecfr, grouped):
    versions = "/api/versioner/v1/versions/title-45.json"
    grouped.json(versions, {"content_versions": [], "meta": {}})
    assert ecfr.changes("45/part-164/subpart-C/appendix-A") == []
    params = grouped.requests[-1].url.params
    assert params["appendix"] == "Appendix A to Subpart C of Part 164"
    assert ecfr.changes("45/part-164") == []
    grouped.json("/api/search/v1/results", {"results": [], "meta": {"total_count": 0}})
    ecfr.search("x", within="45/part-164/subpart-A")
    params = grouped.requests[-1].url.params
    assert (params["hierarchy[part]"], params["hierarchy[subpart]"]) == ("164", "A")


def test_get_many_with_addresses(ecfr, api, part_xml):
    api.text(FULL_2018, part_xml)
    found = ecfr.get_many(["45/164.102", "45/164.104/a", "45/164.104@2018-10-01"])
    assert [n.address for n in found] == ["45/164.102", "45/164.104/a", "45/164.104"]
    assert found[2].date == dt.date(2018, 10, 1)


def test_new_files_record_their_address_and_layout(ecfr, tmp_path):
    file = ecfr.save("45 CFR 164.104", tmp_path)
    assert file.name == "45_164.104.md"
    assert ecfr.save("45/164.104/a", tmp_path).name == "45_164.104_a.md"
    meta = read_front_matter(file) or {}
    assert (meta["address"], meta["layout"]) == ("45/164.104", 2)
    files = ecfr.save("45/part-164", tmp_path / "tree", tree=True)
    names = sorted(str(f.relative_to(tmp_path / "tree")) for f in files)
    assert "part-164/subpart-A/164.104.md" in names
    assert "part-164/subpart-C/appendix-A.md" in names


# The command line


def test_get_several(run, capsys, tmp_path):
    assert run("get", "45/164.102", "45/164.104") == 0
    out = capsys.readouterr().out
    assert "§ 164.102" in out and "§ 164.104" in out
    assert run("get", "45/164.102,164.104", "-o", str(tmp_path / "flat")) == 0
    assert sorted(p.name for p in (tmp_path / "flat").iterdir()) == [
        "45_164.102.md",
        "45_164.104.md",
    ]
    assert (
        run("get", "45/164.102", "45/164.104", "--tree", "-o", str(tmp_path / "t")) == 0
    )
    assert sorted(p.name for p in (tmp_path / "t").iterdir()) == [
        "164.102.md",
        "164.104.md",
    ]
    assert (
        run("get", "45 CFR 164.102", "45 CFR 164.104", "-o", str(tmp_path / "w")) == 0
    )
    assert len(list((tmp_path / "w").iterdir())) == 2


def test_get_explains_words_that_arent_citations(run, capsys):
    assert run("get", "45/164.102", "45 CFR nonsense") == 2
    assert "isn't a citation or address" in capsys.readouterr().err
    assert run("get", "45/nonsense") == 2
    assert "'nonsense' isn't part of an address" in capsys.readouterr().err


def test_get_asks_for_a_citation_in_a_terminal(run, capsys, monkeypatch):
    monkeypatch.setattr(cli_module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(type(cli_module.err_console), "is_terminal", True)
    monkeypatch.setattr(cli_module.err_console, "input", lambda prompt: "45/164.102")
    assert run("get") == 0
    assert "§ 164.102" in capsys.readouterr().out
    monkeypatch.setattr(cli_module.err_console, "input", lambda prompt: " ")
    assert run("get") == 2


# Files saved by ecfr-md 0.x


def save_old(ecfr, citation, path, *, tree=False, date=None):
    """Save as ecfr-md 0.x did: lowercase names, no address or layout."""
    return ecfr._save(citation, path, tree=tree, date=date, layout=1)


def test_old_trees_sync_in_place(ecfr, api, part_xml, tmp_path):
    save_old(ecfr, "45 CFR Part 164", tmp_path, tree=True)
    old = tmp_path / "part-164/subpart-a/section-164.104.md"
    assert old.exists()
    assert "layout" not in (read_front_matter(tmp_path / "part-164/index.md") or {})
    api.text(FULL, part_xml.replace("Roman two.", "Roman two, amended."))
    result = ecfr.sync(tmp_path)
    assert result.changed == [old] and not result.added and not result.removed
    assert "address" not in (read_front_matter(old) or {})


def test_migrate_renames_old_trees(ecfr, api, part_xml, tmp_path):
    save_old(ecfr, "45 CFR Part 164", tmp_path, tree=True)
    save_old(ecfr, "45 CFR Part 164", tmp_path / "mine", tree=True)
    (tmp_path / "mine/part-164").rename(tmp_path / "mine/hipaa2")
    old = tmp_path / "part-164/subpart-a"
    (old / "section-164.106.md").write_text(
        (old / "section-164.106.md").read_text(encoding="utf-8") + "my note\n",
        encoding="utf-8",
    )
    notes = old / "notes.md"
    notes.write_text("mine\n", encoding="utf-8")
    api.text(FULL, part_xml.replace('<DIV8 N="164.108"', '<DIV8 N="164.110"'))
    preview = ecfr.sync(tmp_path / "part-164", migrate=True, dry_run=True)
    assert (old / "section-164.104.md").exists()  # A dry run moves nothing.
    result = ecfr.sync(tmp_path / "part-164", migrate=True)
    assert result.moved == preview.moved
    new = tmp_path / "part-164/subpart-A"
    assert (old / "section-164.104.md", new / "164.104.md") in result.moved
    assert (new / "164.104.md").exists() and (new / "index.md").exists()
    assert result.modified == [old / "section-164.106.md"]  # Edited: kept.
    assert result.removed == [old / "section-164.108.md"]
    assert result.added == [new / "164.110.md"]
    assert notes.exists()  # Not ecfr-md's: kept.
    meta = read_front_matter(new / "164.104.md") or {}
    assert meta["layout"] == 2
    # Migrated, the tree syncs as today's.
    assert not ecfr.sync(tmp_path / "part-164").moved
    # A folder the user renamed keeps its name.
    renamed = ecfr.sync(tmp_path / "mine", migrate=True)
    assert (tmp_path / "mine/hipaa2/subpart-A/164.104.md").exists()
    assert renamed.moved
    # A tree's top folder is renamed too, if ecfr-md named it.
    api.json(STRUCTURE, STRUCTURE_WITH_GROUP)
    subpart = ET.tostring(ET.fromstring(part_xml).find("DIV6"), encoding="unicode")
    api.text(FULL, subpart)
    save_old(ecfr, "45 CFR Part 164 Subpart A", tmp_path / "sub", tree=True)
    stale = tmp_path / "sub/subpart-a/section-164.108.md"
    stale.write_text(stale.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    api.text(FULL, subpart.replace('<DIV8 N="164.108"', '<DIV8 N="164.110"'))
    moved = ecfr.sync(tmp_path / "sub/subpart-a", migrate=True)
    assert moved.modified == [stale]  # Edited, and gone from the eCFR: kept.
    assert sorted(p.name for p in (tmp_path / "sub").iterdir()) == ["subpart-A"]
    assert (tmp_path / "sub/subpart-A/164.104.md").exists()


def test_migrate_renames_old_files(ecfr, api, part_xml, tmp_path):
    api.text("/api/versioner/v1/full/2018-01-01/title-45.xml", part_xml)
    plain = tmp_path / "45-cfr-164.102.md"
    save_old(ecfr, "45 CFR 164.102", plain)
    dated = tmp_path / legacy_file_name("45 CFR 164.104", dt.date(2018, 1, 1))
    save_old(ecfr, "45 CFR 164.104", dated, date="2018-01-01")
    edited = tmp_path / "45-cfr-164.106.md"
    save_old(ecfr, "45 CFR 164.106", edited)
    edited.write_text(edited.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    chosen = tmp_path / "chosen.md"
    save_old(ecfr, "45 CFR 164.108", chosen)
    kept = ecfr.sync(tmp_path)
    assert {p.name for p in kept.unchanged} >= {plain.name, chosen.name}
    result = ecfr.sync(tmp_path, migrate=True)
    assert sorted((a.name, b.name) for a, b in result.moved) == [
        ("45-cfr-164.102.md", "45_164.102.md"),
        ("45-cfr-164.104@2018-01-01.md", "45_164.104@2018-01-01.md"),
    ]
    assert tmp_path / "45_164.102.md" in result.changed  # Front matter is new.
    assert result.modified == [edited]
    assert chosen.exists()
    assert not plain.exists() and (tmp_path / "45_164.102.md").exists()


def test_sync_migrate_on_the_command_line(run, ecfr, capsys, tmp_path):
    save_old(ecfr, "45 CFR 164.102", tmp_path / "45-cfr-164.102.md")
    assert run("sync", str(tmp_path), "--migrate") == 0
    err = capsys.readouterr().err
    assert "45-cfr-164.102.md -> " in err and "Renamed" in err
    assert (tmp_path / "45_164.102.md").exists()


def test_front_matter_is_json_safe(ecfr, tmp_path):
    file = ecfr.save("45 CFR 164.104", tmp_path)
    head = file.read_text(encoding="utf-8").split("\n---\n", 1)[0]
    assert 'address: "45/164.104"' in head and "layout: 2" in head
    assert json.loads(head.split("layout: ", 1)[1].split("\n")[0]) == 2
