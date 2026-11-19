"""Hosts and sources through the client: GovInfo bulk files, the automatic
choice, configured sites, and large titles read part by part."""

import datetime as dt
import json
import logging
import xml.etree.ElementTree as ET

import httpx2
import pytest

from ecfr_md import ECFR, Settings, client, document, markdown
from ecfr_md.client import Memo
from ecfr_md.errors import ECFRError, NodeNotFoundError, ResponseFormatError
from ecfr_md.models import Structure
from ecfr_md.output import read_front_matter
from ecfr_md.sources import COSTS_FILE

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"
FULL_2018 = "/api/versioner/v1/full/2018-01-01/title-45.xml"
BULK = "/bulkdata/ECFR/title-45/ECFR-title45.xml"
BULK_URL = f"https://www.govinfo.gov{BULK}"
STRUCTURE = "/api/versioner/v1/structure/2026-10-07/title-45.json"
VERSIONS = "/api/versioner/v1/versions/title-45.json"
CURRENT = "Wed, 07 Oct 2026 12:00:00 GMT"  # The title's latest issue date.


def bulk_file(part_xml: str) -> str:
    """Part 164 as GovInfo publishes it: in a print volume, with "§" in N."""
    part = part_xml.split("?>", 1)[1].replace('N="164.', 'N="§ 164.')
    return (
        '<?xml version="1.0"?><DLPSTEXTCLASS><HEADER/><TEXT><BODY><ECFRBRWS>'
        '<DIV1 N="1" TYPE="TITLE"><HEAD>Title 45—Public Welfare--Volume 1</HEAD>'
        "<CFRTOC><PTHD>Part</PTHD></CFRTOC>"
        '<DIV3 N="A" TYPE="CHAPTER"><HEAD>SUBTITLE A—Department of Health</HEAD>'
        f"{part}</DIV3></DIV1></ECFRBRWS></BODY></TEXT></DLPSTEXTCLASS>"
    )


def entry(kind: str, n: str, *children: dict) -> dict:
    return {"identifier": n, "label": f"{n}", "type": kind, "children": list(children)}


TITLE_45 = {
    "identifier": "45", "label": "Title 45—Public Welfare", "type": "title",
    "size": 1000,
    "children": [entry(
        "subtitle", "A",
        entry(
            "part", "164",
            entry(
                "subpart", "A",
                *(entry("section", f"164.{n}") for n in (102, 104, 106, 108)),
            ),
            entry("appendix", "Appendix A to Subpart C of Part 164"),
        ) | {"size": 5000},
        entry("part", "165") | {"reserved": True},
    )],
}  # fmt: skip


@pytest.fixture
def govinfo(api, part_xml):
    """The fake API also serves GovInfo's bulk file of title 45."""
    api.json(STRUCTURE, TITLE_45)
    state = {"modified": CURRENT, "body": bulk_file(part_xml), "status": 200}

    def route(request: httpx2.Request) -> httpx2.Response:
        headers = {"Last-Modified": state["modified"]} if state["modified"] else {}
        if request.method == "HEAD":
            return httpx2.Response(state["status"], headers=headers)
        return httpx2.Response(state["status"], text=state["body"], headers=headers)

    api.routes[BULK] = route
    api.bulk = state  # type: ignore[attr-defined]
    return api


def connect(api, **settings) -> ECFR:
    defaults = {"backoff_initial": 0, "backoff_max": 0, "max_attempts": 2}
    cache = {"cache": "cache_dir" in settings}
    return ECFR(
        Settings(**(defaults | cache | settings)),
        transport=httpx2.MockTransport(api),
    )


def test_text_from_bulk(govinfo):
    with connect(govinfo, source="bulk") as ecfr:
        title = ecfr.get(45)
        section = ecfr.get("45 CFR 164.104")
    assert title.provenance["api"] == BULK_URL
    assert [p.identifier for p in title.iter("part")] == ["164", "165"]
    assert title.heading == "Title 45—Public Welfare"
    assert section.provenance["api"] == BULK_URL
    assert section.children[0].identifier == "a"
    assert FULL not in govinfo.paths()


def test_bulk_text_is_the_apis(govinfo):
    with connect(govinfo, source="bulk") as ecfr:
        from_bulk = ecfr.get("45 CFR Part 164").to_markdown()
    with connect(govinfo, source="api") as ecfr:
        from_api = ecfr.get("45 CFR Part 164").to_markdown()
    assert from_bulk == from_api


def test_a_bulk_copy_in_the_cache_serves_the_title(govinfo, tmp_path, caplog):
    caplog.set_level(logging.INFO, "ecfr_md")
    with connect(govinfo, source="bulk", cache_dir=tmp_path) as ecfr:
        ecfr.get(45)
    downloads = govinfo.paths().count(BULK)
    with connect(govinfo, cache_dir=tmp_path) as ecfr:  # Automatic.
        ecfr.get("45 CFR 164.102")
        ecfr.get("45 CFR Part 164")
    assert govinfo.paths().count(BULK) == downloads  # No more requests.
    assert FULL not in govinfo.paths()
    assert "a current bulk copy of the title is in the cache" in caplog.text
    learned = json.loads((tmp_path / COSTS_FILE).read_text())
    assert set(learned) == {"api", "bulk"}


def test_stale_or_unknown_bulk_copies_are_not_used(govinfo, caplog):
    caplog.set_level(logging.INFO, "ecfr_md")
    govinfo.bulk["modified"] = "Tue, 06 Oct 2026 12:00:00 GMT"
    with connect(govinfo, source="bulk") as ecfr:
        ecfr.get(45)
        ecfr.get(45)  # Last-Modified is kept a while.
    assert govinfo.paths().count(BULK) == 1  # Only the HEAD request.
    assert "older than the eCFR's latest changes" in caplog.text
    govinfo.bulk["modified"] = None
    with connect(govinfo, source="bulk") as ecfr:
        ecfr.get(45)
    assert "Can't tell how current" in caplog.text
    govinfo.bulk["modified"] = "not a date"
    with connect(govinfo, source="bulk") as ecfr:
        ecfr.get(45)
    assert govinfo.paths().count(FULL) == 4


def test_bulk_failures_fall_back_to_the_api(govinfo, caplog):
    caplog.set_level(logging.WARNING, "ecfr_md")
    govinfo.bulk["body"] = '<DIV1 N="1" TYPE="TITLE"><DIV5 N="9" TYPE="PART"/></DIV1>'
    with connect(govinfo, source="bulk") as ecfr:
        node = ecfr.get("45 CFR Part 164")
    assert node.provenance["api"].startswith("https://www.ecfr.gov/")
    assert "Couldn't use GovInfo's bulk file of title 45" in caplog.text
    assert "Part 9 isn't in the structure" in caplog.text


def test_bulk_errors_fall_back_to_the_api(govinfo, caplog):
    caplog.set_level(logging.WARNING, "ecfr_md")
    govinfo.bulk["status"] = 404
    govinfo.routes["/bulkdata/ECFR/title-45/ECFR-title45.xml"] = lambda request: (
        httpx2.Response(200, headers={"Last-Modified": CURRENT})
        if request.method == "HEAD"
        else httpx2.Response(404, text="gone")
    )
    with connect(govinfo, source="bulk") as ecfr:
        assert ecfr.get(45).identifier == "164"
    assert "Couldn't use GovInfo's bulk file" in caplog.text


def test_past_dates_come_from_the_api(govinfo):
    govinfo.routes[FULL_2018] = govinfo.routes[FULL]
    with connect(govinfo, source="bulk") as ecfr:
        node = ecfr.get(45, date="2018-01-01")
    assert node.provenance["api"].startswith("https://www.ecfr.gov/")
    assert BULK not in govinfo.paths()


def test_a_title_too_large_for_one_request_comes_from_bulk(
    govinfo, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, "ecfr_md")
    monkeypatch.setattr(client, "SPLIT_ABOVE", 500)
    with connect(govinfo, api_latency=100) as ecfr:
        title = ecfr.get(45)
    assert title.provenance["api"] == BULK_URL
    assert "from www.govinfo.gov: downloading the title (~0 s) beats" in caplog.text


def test_a_title_in_the_cache_comes_from_the_api(govinfo, monkeypatch, tmp_path):
    with connect(govinfo, source="api", cache_dir=tmp_path) as ecfr:
        ecfr.get(45)  # Cached.
    with connect(govinfo, cache_dir=tmp_path, api_latency=100) as ecfr:
        assert ecfr._choose(client.Citation(45), dt.date(2026, 10, 7)).reason == (
            "the text is in the cache"
        )
    monkeypatch.setattr(client, "SPLIT_ABOVE", 500)
    with connect(govinfo, cache_dir=tmp_path, api_latency=100) as ecfr:
        choice = ecfr._choose(client.Citation(45), dt.date(2026, 10, 7))
    assert choice.source == "bulk"  # Its parts aren't cached.


def test_learned_api_latency(govinfo):
    with connect(govinfo, api_latency=50) as ecfr:
        ecfr.get("45 CFR Part 164")
        assert ecfr._costs.api_latency < 50


# Configured hosts


def test_links_and_images_go_to_the_configured_hosts(api, part_xml, tmp_path):
    api.text(FULL, part_xml.replace("<P>(c)", '<P><img src="/graphics/x.gif"/>(c)'))
    settings = {
        "site_url": "https://mirror.example",
        "image_url": "https://img.example",
    }
    with connect(api, **settings) as ecfr:
        part = ecfr.get("45 CFR Part 164")
        saved = ecfr.save("45 CFR Part 164", tmp_path / "part.md")
        outline = ecfr.outline(45)
        title = ecfr.titles()[0]
    assert part.url == "https://mirror.example/on/2026-10-07/title-45/part-164"
    assert "![](https://img.example/graphics/x.gif)" in part.to_markdown()
    text = saved.read_text(encoding="utf-8")
    assert "![](https://img.example/graphics/x.gif)" in text
    assert read_front_matter(saved)["source"].startswith("https://mirror.example/")
    assert outline.url.startswith("https://mirror.example/")
    assert outline["164"].url.startswith("https://mirror.example/")
    assert title.url.startswith("https://mirror.example/")


def test_references_link_to_the_configured_sites(api, part_xml, tmp_path):
    api.text(FULL, part_xml.replace("[78 FR 5692", "See § 164.104 and [78 FR 5692"))
    settings = {
        "site_url": "https://mirror.example",
        "fr_url": "https://fr.example/{volume}/{page}",
    }
    with connect(api, **settings) as ecfr:
        part = ecfr.get("45 CFR Part 164")
        ecfr.save("45 CFR Part 164", tmp_path / "tree", tree=True)
        ecfr.save("45 CFR Part 164", tmp_path / "one.md", links=True)
    section = part["164.102"]
    urls = [r.url for r in section.references]
    assert urls == [
        "https://mirror.example/current/title-45/section-164.104",
        "https://fr.example/78/5692",
    ]
    linked = section.to_markdown(links=True)
    assert "(https://fr.example/78/5692)" in linked
    saved = (tmp_path / "tree/part-164/subpart-A/164.102.md").read_text("utf-8")
    assert "(https://fr.example/78/5692)" in saved and "(164.104.md)" in saved
    one = (tmp_path / "one.md").read_text("utf-8")
    assert "(https://fr.example/78/5692)" in one


def test_changes_and_matches_link_to_the_site(api):
    version = {
        "date": "2024-01-05", "amendment_date": "2024-01-05",
        "issue_date": "2024-01-05", "identifier": "164.502", "name": "§ 164.502 x",
        "part": "164", "substantive": True, "removed": False,
        "title": "45", "type": "section",
    }  # fmt: skip
    api.json(VERSIONS, {"content_versions": [version], "meta": {}})
    api.json(
        "/api/search/v1/results",
        {
            "results": [{
                "starts_on": "2024-01-01", "ends_on": None, "type": "Section",
                "hierarchy": {"title": "45", "part": "164", "section": "164.502"},
                "hierarchy_headings": {}, "headings": {"section": "§ 164.502 Uses"},
                "full_text_excerpt": "minimum necessary.", "removed": False,
                "score": 1.0, "structure_index": 1, "reserved": False,
            }],
            "meta": {"total_count": 1, "total_pages": 1},
        },
    )  # fmt: skip
    with connect(api, site_url="https://mirror.example/") as ecfr:
        change = ecfr.changes(45)[0]
        match = ecfr.search("minimum necessary")[0]
    assert change.url.startswith("https://mirror.example/on/2024-01-05/")
    assert match.url.startswith("https://mirror.example/on/2024-01-01/")


# Large divisions, read part by part


def by_part(request: httpx2.Request) -> httpx2.Response:
    part = request.url.params["part"]
    if part == "404":
        return httpx2.Response(404, json={"error": "No matching content found."})
    return httpx2.Response(
        200,
        text=(
            f'<?xml version="1.0"?><DIV5 N="{part}" TYPE="PART"><HEAD>PART {part}'
            f'</HEAD><DIV8 N="{part}.1" TYPE="SECTION"><HEAD>§ {part}.1 One.</HEAD>'
            f'<P>(a) Text of {part}. <img src="/graphics/{part}.gif"/></P>'
            "</DIV8></DIV5>"
        ),
    )


def parts(*sizes: tuple[str, int]) -> dict:
    return {
        "identifier": "45", "label": "Title 45", "type": "title", "size": 10**9,
        "children": [{
            "identifier": "A", "label": "Subtitle A", "type": "subtitle",
            "children": [
                {"identifier": p, "label": f"Part {p}", "type": "part", "size": s}
                for p, s in sizes
            ] + [{"identifier": "200", "label": "Part 200 [Reserved]", "type": "part",
                  "reserved": True}],
        }],
    }  # fmt: skip


@pytest.fixture
def large(api):
    api.json(STRUCTURE, parts(("1", 10), ("2", 30), ("3", 20)))
    api.routes[FULL] = by_part
    return api


def test_large_titles_are_fetched_largest_part_first(large):
    with connect(large, concurrency=1) as ecfr:
        title = ecfr.get(45)
    fetched = [r.url.params["part"] for r in large.requests if r.url.path == FULL]
    assert fetched == ["2", "3", "1"]
    assert [p.identifier for p in title.iter("part")] == ["1", "2", "3", "200"]


def test_large_divisions_read_parts_when_needed(large, monkeypatch, tmp_path):
    monkeypatch.setattr(document, "PARTS_KEPT", 1)  # One part at a time.
    with connect(large, image_url="https://img.example") as ecfr:
        title = ecfr.get(45)
    assert title._parts is not None
    stub = title.children[0].children[0]
    assert stub.heading == "PART 1" and stub.slug == "part-1"
    assert title["2.1"].heading == "§ 2.1 One."
    assert "Text of 3." in title["Part 3"]["3.1/a"].text
    assert len(title._parts._kept) == 1
    assert stub.children[0] is not stub.children[0]  # Not kept with the part.
    text = title.to_markdown()
    assert (
        text.index("Text of 1.") < text.index("Text of 2.") < text.index("Text of 3.")
    )
    assert "![](https://img.example/graphics/2.gif)" in text
    assert "PART 200 [RESERVED]" not in text and "Part 200 [Reserved]" in text
    tree = title.to_markdown_tree()
    assert any(str(path).endswith("2.1.md") for path in tree)
    files = title.save(tmp_path / "t", tree=True)
    assert len(files) == len(tree)
    assert 'ecfr-md-part="0.xml"' in ET.tostring(title.element, encoding="unicode")
    assert "<DIV8" in title.children[0].children[1]._raw


def test_part_files_are_removed_with_the_division(large):
    with connect(large) as ecfr:
        title = ecfr.get(45)
    directory = title._parts.directory
    assert directory.exists()
    del title
    import gc

    gc.collect()
    assert not directory.exists()


def test_a_failed_part_leaves_nothing_behind(api, tmp_path, monkeypatch):
    api.json(STRUCTURE, parts(("404", 10), ("2", 30), ("3", 20)))
    api.routes[FULL] = by_part
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    with connect(api, concurrency=1) as ecfr, pytest.raises(ECFRError):
        ecfr.get(45)
    assert list(tmp_path.iterdir()) == []


def test_lazy_loading_rejects_bad_xml(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<DIV1><DIV5 TYPE='PART'></DIV1>")
    with pytest.raises(ResponseFormatError, match="Invalid eCFR XML"):
        document.load_file(bad, title=45, lazy=True)
    bad.write_text("<DIV1><DIV5 TYPE='PART'><HEAD>x</HEAD></DIV5></DIV1>")
    (tmp_path / "parts").mkdir()
    title = document.load_file(bad, title=45, lazy=True)
    (title._parts.directory / "0.xml").write_text("<oops")
    with pytest.raises(ResponseFormatError):
        title.children[0].children  # noqa: B018
    with pytest.raises(ResponseFormatError, match="no CFR content"):
        bad.write_text("<ECFR/>")
        document.load_file(bad, title=45)


def test_parts_without_a_heading_or_text(tmp_path):
    file = tmp_path / "t.xml"
    file.write_text(
        '<DIV1 N="45" TYPE="TITLE"><HEAD>T</HEAD><DIV5 N="1" TYPE="PART"/>'
        '<DIV5 N="2" TYPE="PART"><HEAD/></DIV5>'
        '<DIV5 N="3" TYPE="PART" >\n<P>x</P></DIV5 ></DIV1>'
    )
    title = document.load_file(file, title=45, lazy=True)
    assert [p.heading for p in title.children] == ["", "", ""]
    assert markdown.plain_text(title.children[2].element.find("P")) == "x"


def test_structures_need_labels_and_types():
    with pytest.raises(ValueError, match="needs a label and a type"):
        Structure.parse(b'{"label": 1, "type": "title"}')
    with pytest.raises(ValueError, match="Unexpected structure"):
        Structure.parse(b'{"type": "title"}')
    title = Structure.parse(json.dumps(TITLE_45).encode())
    section = title.children[0].children[0].children[0].children[1]
    assert section.raw().identifier == "164.104"


def test_memos_forget_the_least_recently_used():
    memo: Memo[str, int] = Memo(2)
    memo.put("a", 1)
    memo.put("b", 2)
    assert memo.get("a") == 1
    memo.put("c", 3)
    assert (memo.get("a"), memo.get("b"), memo.get("c")) == (1, None, 3)


# get_many in parallel


def test_get_many_fetches_the_rest_in_parallel(ecfr):
    found = ecfr.get_many(
        ["45 CFR 164.102", "45 CFR 164.999", "45 CFR 164.104"], errors="return"
    )
    assert found[0].citation == "45 CFR 164.102"
    assert isinstance(found[1], NodeNotFoundError)
    assert found[2].citation == "45 CFR 164.104"
    with pytest.raises(NodeNotFoundError):
        ecfr.get_many(["45 CFR 164.999", "45 CFR 164.102"])
