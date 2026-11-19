"""What cites a place, and saving and syncing linked Markdown with a client."""

import json

import pytest

from ecfr_md import InvalidParameterError
from ecfr_md.output import read_front_matter


def _result(title: str, section: str, excerpt: str) -> dict:
    h = {"title": title, "part": section.split(".")[0], "section": section}
    return {
        "starts_on": "2017-01-01", "ends_on": None, "type": "Section",
        "hierarchy": h, "hierarchy_headings": h, "headings": {"section": "§ x"},
        "full_text_excerpt": excerpt, "score": 1.0, "structure_index": 1,
        "reserved": False, "removed": False,
    }  # fmt: skip


def test_cited_by_searches_for_the_number(ecfr, api):
    results = [
        _result("45", "164.502", "§ 164.502 itself"),
        _result("45", "164.504", "required by § 164.502(e)"),
        _result("48", "324.7001", "by 45 CFR 164.502(e)(1)"),
        _result("21", "1.1", "a different 164.502"),
    ]
    body = {"results": results, "meta": {"total_count": 4, "total_pages": 1}}
    api.json("/api/search/v1/results", body)
    found = ecfr.cited_by("45 CFR 164.502(a)", date="2024-01-01")
    assert [m.citation for m in found] == ["45 CFR 164.504", "48 CFR 324.7001"]
    assert api.requests[-1].url.params["query"] == '"164.502"'
    assert api.requests[-1].url.params["date"] == "2024-01-01"
    ecfr.cited_by("45/part-164", limit=1)
    assert api.requests[-1].url.params["query"] == '"part 164"'
    with pytest.raises(InvalidParameterError, match="neither"):
        ecfr.cited_by(45)


def test_client_saves_link_trees_by_default(ecfr, tmp_path):
    files = ecfr.save("45 CFR Part 164", tmp_path, tree=True)
    assert all(read_front_matter(f)["links"] is True for f in files)
    index = (tmp_path / "part-164/index.md").read_text()
    assert "[42 U.S.C. 1320d](https://uscode.house.gov/" in index
    single = ecfr.save("45 CFR Part 164", tmp_path / "one.md", links=True)
    assert "[78 FR 5692](https://www.federalregister.gov/" in single.read_text()
    paragraph = ecfr.save("45 CFR 164.104(a)", tmp_path / "p.md", links=True)
    assert read_front_matter(paragraph)["links"] is True


def test_sync_adds_links_once_to_trees_saved_before_them(ecfr, tmp_path):
    ecfr.save("45 CFR Part 164", tmp_path, tree=True, links=False)
    assert not ecfr.sync(tmp_path).changed  # Saved without links: kept so.
    for file in tmp_path.rglob("*.md"):  # As saved before links existed.
        file.write_text(file.read_text().replace("links: false\n", ""))
    result = ecfr.sync(tmp_path)
    index = tmp_path / "part-164/index.md"
    assert index in result.changed
    assert "[42 U.S.C. 1320d](" in index.read_text()
    assert not ecfr.sync(tmp_path).changed


def test_cli_references(run, capsys):
    assert run("references", "45 CFR 164.106") == 0
    out, err = capsys.readouterr()
    assert "paragraph (a)" in out and "45 CFR 164.106(a)" in out
    assert "1 reference" in err
    assert run("references", "45 CFR 164.102", "--json") == 0
    found = json.loads(capsys.readouterr().out)
    assert [r["kind"] for r in found] == ["fr"]
    assert run("references", "45 CFR 164.108") == 0
    out, err = capsys.readouterr()
    assert "0 references" in err


def test_cli_cited_by(run, api, capsys):
    body = {
        "results": [_result("45", "164.504", "by § 164.502(e)")],
        "meta": {"total_count": 1, "total_pages": 1},
    }
    api.json("/api/search/v1/results", body)
    assert run("cited-by", "45 CFR 164.502") == 0
    assert "45 CFR 164.504" in capsys.readouterr().out
    assert run("cited-by", "45 CFR 164.502", "--json") == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["address"]
    assert run("cited-by", "45 CFR 164.504") == 0
    assert "Nothing found" in capsys.readouterr().err


def test_cli_get_links(run, capsys, tmp_path):
    assert run("get", "45 CFR 164.102", "--links") == 0
    assert "[78 FR 5692](https://www.federalregister.gov/" in capsys.readouterr().out
    assert run("get", "45 CFR 164.102", "45 CFR 164.106", "--links") == 0
    assert "[78 FR 5692](" in capsys.readouterr().out
    assert (
        run("get", "45 CFR Part 164", "--tree", "--no-links", "-o", str(tmp_path)) == 0
    )
    assert "](" not in (tmp_path / "part-164/index.md").read_text().split("\n- [")[0]
