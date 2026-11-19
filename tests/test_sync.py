"""sync keeps what it shouldn't touch; dated saves don't collide or mix."""

import datetime as dt

import pytest

from ecfr_md.output import read_front_matter, snapshot_name

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
FULL_2018 = "/api/versioner/v1/full/2018-01-01/title-45.xml"
SECTION = "part-164/subpart-a/section-164.104.md"


def snapshot(name):
    """The default file name for text saved as of 2018-01-01 (see snapshot_name)."""
    return f"{name}@2018-01-01.md"


def test_snapshot_names():
    assert snapshot_name("45-cfr-part-164", dt.date(2018, 1, 1)) + ".md" == snapshot(
        "45-cfr-part-164"
    )


@pytest.fixture
def dated(api):
    """The fake API also serves text as of 2018-01-01."""
    api.routes[FULL_2018] = api.routes[FULL]
    return api


def amend(api, part_xml):
    api.text(FULL, part_xml.replace("Roman two.", "Roman two, amended."))


def read(path):
    return path.read_text(encoding="utf-8")


def edit(path, old, new):
    path.write_text(read(path).replace(old, new), encoding="utf-8")


# Finding what to sync


def test_sync_a_tree_from_its_top_folder(ecfr, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    index = tmp_path / "part-164" / "index.md"
    before = read(index)
    result = ecfr.sync(tmp_path / "part-164")
    assert read(index) == before  # Not replaced by the whole part.
    assert len(result.unchanged) == 7 and result.changed == []


def test_sync_finds_saves_in_nested_folders(ecfr, api, part_xml, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path / "snap" / "tree2018", tree=True)
    ecfr.save("45 CFR Part 164", tmp_path / "a" / "b" / "one.md")
    amend(api, part_xml)
    result = ecfr.sync(tmp_path)
    assert result.changed == [
        tmp_path / "a/b/one.md",
        tmp_path / "snap/tree2018" / SECTION,
    ]
    assert len(result.unchanged) == 6


# Dated snapshots


def test_a_dated_save_is_named_and_marked_with_its_date(ecfr, dated, tmp_path):
    saved = ecfr.save("45 CFR Part 164", tmp_path, date="2018-01-01")
    assert saved == tmp_path / snapshot("45-cfr-part-164")
    assert (read_front_matter(saved) or {})["pinned"] == "2018-01-01"
    latest = ecfr.save("45 CFR Part 164", tmp_path, date="2026-10-07")
    assert latest == tmp_path / "45-cfr-part-164.md"
    assert "pinned" not in (read_front_matter(latest) or {})
    # A name the user chose is kept as is.
    named = ecfr.save("45 CFR Part 164", tmp_path / "x.md", date="2018-01-01")
    assert named == tmp_path / "x.md"


def test_a_dated_node_is_named_and_marked_with_its_date(ecfr, dated, tmp_path):
    section = ecfr.get("45 CFR 164.102", date="2018-01-01")
    saved = section.save(tmp_path)
    assert saved.name == snapshot("45-cfr-164.102")
    assert section.provenance["pinned"] == "2018-01-01"
    assert ecfr.get("45 CFR 164.102").save(tmp_path).name == "45-cfr-164.102.md"
    files = ecfr.get("45 CFR 164", date="2018-01-01").save(tmp_path, tree=True)
    assert all((read_front_matter(f) or {})["pinned"] == "2018-01-01" for f in files)


def test_sync_keeps_dated_snapshots(ecfr, dated, part_xml, tmp_path):
    single = ecfr.save("45 CFR Part 164", tmp_path, date="2018-01-01")
    ecfr.save("45 CFR Part 164", tmp_path / "old", tree=True, date="2018-01-01")
    amend(dated, part_xml)
    requests = len(dated.requests)
    before = read(single)
    result = ecfr.sync(tmp_path)
    assert result.kept == [tmp_path / "old" / "part-164", single]
    assert (result.changed, result.unchanged) == ([], [])
    assert read(single) == before
    assert len(dated.requests) == requests  # Nothing downloaded.

    result = ecfr.sync(tmp_path, include_dated=True)
    assert result.kept == []
    assert single in result.changed
    assert "amended" in read(single)
    assert "pinned" not in (read_front_matter(single) or {})


# Local edits


def test_sync_keeps_local_edits(ecfr, api, part_xml, tmp_path):
    saved = ecfr.save("45 CFR Part 164", tmp_path / "hipaa.md")
    assert (read_front_matter(saved) or {})["digest"].startswith("sha256:")
    edit(saved, "Roman one.", "Roman one, with my note.")
    amend(api, part_xml)
    result = ecfr.sync(saved)
    assert result.modified == [saved] and result.changed == []
    assert "my note" in read(saved)

    result = ecfr.sync(saved, force=True)
    assert result.changed == [saved]
    assert "my note" not in read(saved) and "amended" in read(saved)
    assert ecfr.sync(saved).unchanged == [saved]  # The new digest matches.


def test_sync_keeps_edited_files_of_removed_sections(ecfr, api, part_xml, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    edited = tmp_path / "part-164/subpart-a/section-164.108.md"
    edit(edited, "Uno.", "Uno, noted.")
    without = (
        part_xml[: part_xml.index('<DIV8 N="164.108"')]
        + part_xml[part_xml.index("</DIV6>") :]
    )
    api.text(FULL, without)
    result = ecfr.sync(tmp_path)
    assert result.modified == [edited] and result.removed == []
    assert edited.exists()
    assert ecfr.sync(tmp_path, force=True).removed == [edited]
    assert not edited.exists()


def test_files_without_a_digest_are_synced_as_before(ecfr, api, part_xml, tmp_path):
    saved = ecfr.save("45 CFR Part 164", tmp_path / "old.md")
    text = read(saved)
    start = text.index("digest: ")
    text = text[:start] + text[text.index("\n", start) + 1 :]
    saved.write_text(text.replace("Roman one.", "Mine."), encoding="utf-8")
    assert read_front_matter(saved) is not None
    amend(api, part_xml)
    assert ecfr.sync(saved).changed == [saved]
    assert "Mine." not in read(saved)


def test_a_file_without_its_blank_line_counts_as_edited(ecfr, tmp_path):
    saved = ecfr.save("45 CFR Part 164", tmp_path / "hipaa.md")
    edit(saved, "\n---\n\n", "\n---\n")
    assert ecfr.sync(saved).modified == [saved]


# Dry runs


def test_dry_run_writes_nothing(ecfr, api, part_xml, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    (tmp_path / "part-164/subpart-a/section-164.106.md").unlink()
    changed = part_xml.replace("Roman two.", "Roman two, amended.")
    changed = (
        changed[: changed.index('<DIV8 N="164.108"')]
        + changed[changed.index("</DIV6>") :]
    )
    api.text(FULL, changed)
    before = {p: read(p) for p in tmp_path.rglob("*.md")}
    result = ecfr.sync(tmp_path, dry_run=True)
    assert [p.name for p in result.added] == ["section-164.106.md"]
    assert sorted(p.name for p in result.changed) == [
        "index.md",
        "section-164.104.md",
    ]
    assert [p.name for p in result.removed] == ["section-164.108.md"]
    assert {p: read(p) for p in tmp_path.rglob("*.md")} == before
    assert not list(tmp_path.rglob("*.part"))


# Saving a tree over an older one


def test_a_tree_save_removes_stale_files(ecfr, dated, part_xml, tmp_path):
    with_extra = part_xml.replace(
        "</DIV6>",
        '<DIV8 N="164.110" TYPE="SECTION"><HEAD>§ 164.110 Old.</HEAD>'
        "<P>(a) Old text.</P></DIV8></DIV6>",
    )
    dated.text(FULL_2018, with_extra)
    ecfr.save("45 CFR Part 164", tmp_path, tree=True, date="2018-01-01")
    stale = tmp_path / "part-164/subpart-a/section-164.110.md"
    mine = tmp_path / "part-164/notes.md"
    mine.write_text("my own notes\n", encoding="utf-8")
    edited = tmp_path / "part-164/subpart-a/section-164.108.md"
    assert stale.exists()

    without_subpart = (
        part_xml[: part_xml.index("<DIV6")]
        + part_xml[part_xml.index("</DIV6>") + len("</DIV6>") :]
    )
    dated.text(FULL, without_subpart)
    edit(edited, "Uno.", "Uno, noted.")
    files = ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    assert not stale.exists()
    assert mine.exists()  # Not written by ecfr-md.
    assert edited.exists()  # Edited locally.
    remaining = {p for p in tmp_path.rglob("*.md") if p not in (mine, edited)}
    assert remaining == set(files)
    assert all("pinned" not in (read_front_matter(f) or {}) for f in files)


def test_a_section_tree_save_writes_one_file(ecfr, tmp_path):
    written = ecfr.save("45 CFR 164.102", tmp_path, tree=True)
    assert written == [tmp_path / "section-164.102.md"]
    again = ecfr.get("45 CFR 164.102").save(tmp_path, tree=True)
    assert [p.name for p in again] == ["section-164.102.md"]


# The command


def test_sync_command_options(run, api, part_xml, capsys, tmp_path):
    api.routes[FULL_2018] = api.routes[FULL]
    assert run("get", "45", "CFR", "164", "--tree", "-o", str(tmp_path / "now")) == 0
    args = ["45", "CFR", "164", "--date", "2018-01-01", "-o", str(tmp_path)]
    assert run("get", *args) == 0
    section = tmp_path / "now" / SECTION
    edit(section, "Roman one.", "Mine.")
    amend(api, part_xml)
    capsys.readouterr()

    assert run("sync", str(tmp_path), "--dry-run") == 0
    err = capsys.readouterr().err
    assert "Would sync" in err and "Modified locally" in err
    assert "Kept at saved date" in err and "Mine." in read(section)

    assert run("sync", str(tmp_path)) == 0
    err = capsys.readouterr().err
    assert f"! {section}" in err and "--force" in err and "--include-dated" in err

    assert run("sync", str(tmp_path), "--force", "--include-dated") == 0
    capsys.readouterr()
    assert "Mine." not in read(section)
    dated = tmp_path / snapshot("45-cfr-part-164")
    assert "pinned" not in (read_front_matter(dated) or {})


def test_sync_command_reports_only_kept_files(run, api, capsys, tmp_path):
    api.routes[FULL_2018] = api.routes[FULL]
    args = ["45", "CFR", "164", "--date", "2018-01-01", "-o", str(tmp_path)]
    assert run("get", *args) == 0
    capsys.readouterr()
    assert run("sync", str(tmp_path)) == 0
    err = capsys.readouterr().err
    assert "Kept at saved date" in err and "is up to date" not in err
