"""Addresses round-trip over real CFR identifiers, and every accepted form parses.

tests/data/cfr_nodes.jsonl samples the eCFR's identifiers: a few of every
shape at every level (reserved ranges, "1.401(k)-1", "IX [RESERVED]",
"Appendixes A-I", subject groups, subparts "Cc" beside "CC", ...), plus
paragraphs from sections whose markers repeat.
"""

import datetime as dt
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import pytest

from ecfr_md import Address, AmbiguousNodeError, InvalidCitationError, load
from ecfr_md import markdown as md
from ecfr_md.address import (
    appendix_key,
    appendix_name,
    decode_section,
    edition_date,
    encode_id,
    encode_section,
    same_identifier,
)
from ecfr_md.document import Node

NODES = [
    json.loads(line)
    for line in (Path(__file__).parent / "data" / "cfr_nodes.jsonl").open(
        encoding="utf-8"
    )
]
DIVISIONS = {
    "title": ("DIV1", "TITLE"),
    "subtitle": ("DIV2", "SUBTITLE"),
    "chapter": ("DIV3", "CHAPTER"),
    "subchapter": ("DIV4", "SUBCHAP"),
    "part": ("DIV5", "PART"),
    "subpart": ("DIV6", "SUBPART"),
    "subject_group": ("DIV7", "SUBJGRP"),
    "section": ("DIV8", "SECTION"),
    "appendix": ("DIV9", "APPENDIX"),
}


def _titles() -> dict[int, tuple[Node, list[tuple[dict, Node]]]]:
    """Each sampled title as a document holding the sampled nodes, in place."""
    roots: dict[int, ET.Element] = {}
    made: dict[tuple, ET.Element] = {}
    wanted: dict[int, list[tuple[dict, tuple]]] = defaultdict(list)
    for record in NODES:
        if record["level"] == "paragraph":
            continue
        title = record["title"]
        parent = roots.setdefault(title, ET.Element("DIV1", N=str(title), TYPE="TITLE"))
        key: tuple = (title,)
        for level, identifier in [
            *record["chain"],
            [record["level"], record["identifier"]],
        ]:
            key = (*key, level, identifier)
            if key not in made:
                tag, kind = DIVISIONS[level]
                made[key] = ET.SubElement(parent, tag, N=identifier, TYPE=kind)
                ET.SubElement(made[key], "HEAD").text = f"{level} {identifier}"
            parent = made[key]
        wanted[title].append((record, key))
    found = {}
    for title, root in roots.items():
        node = load(ET.tostring(root), title=title, date=dt.date(2024, 1, 1))
        by_key = {_key(title, n): n for n in node.iter() if hasattr(n, "element")}
        found[title] = (node, [(record, by_key[key]) for record, key in wanted[title]])
    return found


def _key(title: int, node: Node) -> tuple:
    chain = [*reversed(node.ancestors[:-1]), node]  # Without the title.
    return (title, *(x for n in chain for x in (n.level.value, n.identifier)))


TITLES = _titles()
# The eCFR has a few nodes that differ only in a note ("Appendix I to
# Subparts D-E of Part 205" and the same "[Note]"): one address names both,
# and looking it up says so (AmbiguousNodeError).
SHARED = {
    (title, address)
    for title, (root, _) in TITLES.items()
    for address, count in Counter(n.address for n in root.iter()).items()
    if count > 1
}
SAMPLED = [
    (record, node)
    for _, pairs in TITLES.values()
    for record, node in pairs
    if (record["title"], node.address) not in SHARED
]


@pytest.mark.parametrize(
    ("record", "node"),
    SAMPLED,
    ids=[f"{r['title']}:{r['level']}:{r['identifier']}" for r, _ in SAMPLED],
)
def test_every_node_is_found_by_its_address_uri_and_file_name(record, node):
    root = TITLES[record["title"]][0]
    assert root[node.address] is node
    assert root[Address.parse(node.uri)] is node
    assert Address.parse(node.uri).date == dt.date(2024, 1, 1)
    name = Address.parse(node.address).file_name()
    assert root[Address.parse(name)] is node
    assert set(node.address) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-/@,~"
    )


@pytest.mark.parametrize("title", sorted(TITLES))
def test_every_tree_path_parses_back_to_its_node(title):
    root, _ = TITLES[title]
    by_element = {
        id(n.element): n for n in (root, *root.iter()) if hasattr(n, "element")
    }
    paths: list[tuple[PurePosixPath, Node]] = []

    def emit(path, _body, chain, _heading):
        paths.append((path, by_element[id(chain[-1])]))

    events = md.element_events(root.element)
    names = md.tree_names(md.element_events(root.element))
    md.write_tree(events, emit, names=names)
    assert paths
    for path, node in paths:
        assert str(path).startswith(f"title-{title}/")
        if (title, node.address) in SHARED:
            with pytest.raises(AmbiguousNodeError):
                root[Address.parse(f"cfr/{path}")]
            continue
        assert root[Address.parse(f"cfr/{path}")] is node, path
        # Names are slugs, but for "~2" telling apart names that differ in case.
        assert node.slug in {p.removesuffix(".md").split("~")[0] for p in path.parts}


def test_case_colliding_siblings_are_told_apart_in_code_point_order():
    root, _ = TITLES[40]
    files = root.to_markdown_tree()
    folders = {p.parent.name for p in files if p.parent.parent.name == "part-60"}
    assert {"subpart-CC", "subpart-Cc~2", "subpart-AAA", "subpart-AAa~2"} <= folders


@pytest.mark.parametrize(
    "record", [r for r in NODES if r["level"] == "paragraph"], ids=lambda r: str(r)
)
def test_paragraph_addresses_round_trip(record):
    address = Address(
        record["title"], {"section": record["section"]}, tuple(record["para"])
    )
    parsed = Address.parse(str(address))
    assert parsed == address
    assert parsed.markers == tuple(record["para"])
    assert Address.parse(address.citation()).markers == tuple(record["para"])


@pytest.mark.parametrize(
    "record",
    [r for r in NODES if r["level"] == "section"],
    ids=lambda r: r["identifier"],
)
def test_section_numbers_survive_encoding(record):
    section = record["identifier"]
    segment = encode_section(section)
    assert set(segment) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-"
    )
    # Most decode exactly; the rest still match as addresses write them.
    assert same_identifier("section", section, decode_section(segment), fold=False)


@pytest.mark.parametrize(
    ("record", "node"),
    SAMPLED,
    ids=[f"{r['title']}:{r['level']}:{r['identifier']}" for r, _ in SAMPLED],
)
def test_every_node_is_found_by_its_ecfr_link(record, node):
    root = TITLES[record["title"]][0]
    assert root[Address.parse(node.url)] is node


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Addresses, with and without a title prefix, date or list.
        ("45", "45"),
        ("title-45", "45"),
        ("45/164", "45/part-164"),
        ("45/164.502/a/1", "45/164.502/a/1"),
        ("title-45/section-164.502", "45/164.502"),
        ("45/164.502/a/1@2020-01-01", "45/164.502/a/1@2020-01-01"),
        ("45/164.502@2020", "45/164.502@2020-01-01"),
        ("45/164.502@2020-03", "45/164.502@2020-03-01"),
        ("45/164.502@current", "45/164.502"),
        ("45/164.502@", "45/164.502"),
        ("45/part-164/subpart-E", "45/part-164/subpart-E"),
        ("45/part-164/subpart-e", "45/part-164/subpart-e"),
        ("45/subtitle-A", "45/subtitle-A"),
        ("45/subtitle-A/subchapter-C", "45/subtitle-A/subchapter-C"),
        ("45/subtitle-A/chapter-I/subchapter-C", "45/chapter-I/subchapter-C"),
        ("45/subtitle-A/chapter-I", "45/chapter-I"),
        ("45/subchapter-C", "45/subchapter-C"),
        (
            "41/chapter-101/subchapter-E/appendix",
            "41/chapter-101/subchapter-E/appendix",
        ),
        ("48/chapter-2/appendix-A", "48/chapter-2/appendix-A"),
        ("41/subtitle-B/appendix-A", "41/subtitle-B/appendix-A"),
        ("41/subchapter-E/appendix", "41/subchapter-E/appendix"),
        ("12/part-1026/supplement-I", "12/part-1026/supplement-I"),
        ("12/part-1026/app-J", "12/part-1026/appendix-J"),
        ("40/part-60/subpart-VVVV/table-1", "40/part-60/subpart-VVVV/table-1"),
        ("12/part-9/group-ECFR129c8723f2e5dc7", "12/part-9/group-ECFR129c8723f2e5dc7"),
        ("12/part-9/subject-group-ECFR1a", "12/part-9/group-ECFR1a"),
        ("26/1.401.k-1", "26/1.401.k-1"),
        ("17/210.3-07..210.3-08", "17/210.3-07..210.3-08"),
        ("45/164.501/1~2", "45/164.501/1~2"),
        ("cfr:45/164.502/a@2024-01-01", "45/164.502/a@2024-01-01"),
        ("cfr://45/164.502", "45/164.502"),
        ("45/part-164/subpart-Cc~2", "45/part-164/subpart-Cc"),
        # File names and paths, new and old.
        ("45_164.502_a_1.md", "45/164.502/a/1"),
        ("45_part-164_subpart-E.md", "45/part-164/subpart-E"),
        ("45_164.502_a_1@2020-01-01.md", "45/164.502/a/1@2020-01-01"),
        ("/home/me/cfr/title-45/part-164/subpart-E/164.502.md", "45/164.502"),
        ("C:\\cfr\\title-45\\part-164\\index.md", "45/part-164"),
        ("./title-45/part-164/supplement-I.md", "45/part-164/supplement-I"),
        ("~/title-45/part-164/subpart-a/section-164.104.md", "45/164.104"),
        (
            "/x/title-45/part-164/appendix-appendix-a-to-subpart-c-of-part-164.md",
            "45/part-164/subpart-c/appendix-a",
        ),
        (
            "/x/title-13/part-108/subject-group-ecfr1a/index.md",
            "13/part-108/group-ecfr1a",
        ),
        ("45-cfr-164.104-a.md", "45/164.104/a"),
        ("45-cfr-part-164-subpart-e.md", "45/part-164/subpart-e"),
        ("45-cfr-part-164@2018-01-01.md", "45/part-164@2018-01-01"),
        ("45-cfr.md", "45"),
        ("45-cfr-appendix-a-to-part-164.md", "45/part-164/appendix-a"),
        # Links.
        (
            "https://www.ecfr.gov/current/title-45/section-164.502#p-164.502(a)(1)",
            "45/164.502/a/1",
        ),
        (
            "https://www.ecfr.gov/on/2024-01-01/title-45/part-164",
            "45/part-164@2024-01-01",
        ),
        ("ecfr.gov/current/title-45/part-164/subpart-E", "45/part-164/subpart-E"),
        ("www.ecfr.gov/compare/title-45/part-164", "45/part-164"),
        ("/current/title-45/section-164.502", "45/164.502"),
        (
            "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-160/"
            "subpart-A/section-160.103",
            "45/160.103",
        ),
        (
            "https://www.ecfr.gov/current/title-1/chapter-I/subchapter-E/part-21/"
            "subpart-A/subject-group-ECFRe8a0e8af6bd5c46/section-21.11",
            "1/21.11",
        ),
        (
            "https://www.ecfr.gov/on/2024-01-01/title-12/appendix-Supplement%20I%20to%20"
            "Part%201026#p-Supplement-I-to-Part-1026(b)(1)(i)(E)",
            "12/part-1026/supplement-I@2024-01-01",
        ),
        (
            "https://www.ecfr.gov/current/title-48/appendix-Appendix%20A%20to%20Chapter%202",
            "48/chapter-2/appendix-A",
        ),
        (
            "https://www.ecfr.gov/api/versioner/v1/full/2024-01-01/title-45.xml"
            "?part=164&section=164.502",
            "45/164.502@2024-01-01",
        ),
        (
            "https://www.ecfr.gov/api/renderer/v1/content/enhanced/current/title-45"
            "?part=164&subpart=E",
            "45/part-164/subpart-E",
        ),
        (
            "https://www.law.cornell.edu/cfr/text/45/164.504#e_2_i_A",
            "45/164.504/e/2/i/A",
        ),
        (
            "https://www.law.cornell.edu/cfr/text/45/part-164/subpart-E",
            "45/part-164/subpart-E",
        ),
        ("https://www.law.cornell.edu/cfr/text/12/chapter-X", "12/chapter-X"),
        (
            "https://www.law.cornell.edu/cfr/text/45/chapter-A/subchapter-C",
            "45/subtitle-A/subchapter-C",
        ),
        (
            "https://www.law.cornell.edu/cfr/text/12/appendix-Supplement_I_to_part_1026",
            "12/part-1026/supplement-I",
        ),
        (
            "https://www.law.cornell.edu/cfr/text/12/appendix-A_to_part_1026",
            "12/part-1026/appendix-A",
        ),
        (
            "https://www.govinfo.gov/link/cfr/45/164?sectionnum=502&year=2023&link-type=pdf",
            "45/164.502@2023-10-01",
        ),
        ("https://www.govinfo.gov/link/cfr/45/164?year=mostrecent", "45/part-164"),
        (
            "https://www.govinfo.gov/content/pkg/CFR-2023-title12-vol9/pdf/"
            "CFR-2023-title12-vol9-part1026-appI-id90.pdf",
            "12/part-1026/appendix-I@2023-01-01",
        ),
        ("CFR-2023-title45-vol2-sec164-502", "45/164.502@2023-10-01"),
        (
            "CFR-2023-title12-vol9-part1026-subpartA",
            "12/part-1026/subpart-A@2023-01-01",
        ),
        ("CFR-2023-title45-vol2", "45@2023-10-01"),
        ("/us/cfr/t45/s164.502/a/1@2020-01-01", "45/164.502/a/1@2020-01-01"),
        ("/us/cfr/t45/s164.502@", "45/164.502"),
        ("/us/cfr/t45/pt164/sptE", "45/part-164/subpart-E"),
        ("/us/cfr/t45/stA/chI/schC", "45/chapter-I/subchapter-C"),
        ("/us/cfr/t12/p9/sgECFR1a", "12/part-9/group-ECFR1a"),
        ("/us/cfr/t12/pt1026/appI", "12/part-1026/appendix-I"),
        # Citations, as Citation.parse reads them, and more.
        ("45 CFR 164.502(a)(1)", "45/164.502/a/1"),
        ("45 C.F.R. § 164.502(a)(1) (2018)", "45/164.502/a/1@2018-10-01"),
        ("12 C.F.R. pt. 1026, supp. I (2020)", "12/part-1026/supplement-I@2020-01-01"),
        ("21 CFR 11.10 (2019)", "21/11.10@2019-04-01"),
        ("29 CFR 1910.1200 (2019)", "29/1910.1200@2019-07-01"),
        ("45 CFR 164.502 a 1 i", "45/164.502/a/1/i"),
        ("45 CFR 164.502.a.1", "45/164.502/a/1"),
        ("26 CFR 1.401.k-1", "26/1.401.k-1"),
        ("45 CFR 164.502.a.1(i)", "45/164.502/a/1/i"),
        ("45cfr164.502", "45/164.502"),
        ("26 CFR 1.401(k)-1", "26/1.401.k-1"),
        ("45 CFR 164.500 et seq.", "45/164.500"),
        (
            "45 CFR Appendix A to Subpart C of Part 164",
            "45/part-164/subpart-C/appendix-A",
        ),
        ("45 CFR Subtitle A Chapter I Subchapter C", "45/chapter-I/subchapter-C"),
        ("Title 45", "45"),
        ("\u201c45 CFR 164.502.\u201d", "45/164.502"),
    ],
)
def test_parse(text, expected):
    assert str(Address.parse(text)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45/164.502,164.504", ["45/164.502", "45/164.504"]),
        ("45/164.502 45/164.504", ["45/164.502", "45/164.504"]),
        (
            "45/164.502/a/1,2,ii",
            ["45/164.502/a/1", "45/164.502/a/2", "45/164.502/a/ii"],
        ),
        ("45/164.502,part-160,46/1.1", ["45/164.502", "45/part-160", "46/1.1"]),
        (
            "45/164.502@2020-01-01,164.504",
            ["45/164.502@2020-01-01", "45/164.504@2020-01-01"],
        ),
        ("45/164.502/b..d", ["45/164.502/b", "45/164.502/c", "45/164.502/d"]),
        (
            "45/164.502/a/ii..iv",
            ["45/164.502/a/ii", "45/164.502/a/iii", "45/164.502/a/iv"],
        ),
        ("45 CFR §§ 164.502, 164.506", ["45/164.502", "45/164.506"]),
        ("cfr:45/164.502,164.504", ["45/164.502", "45/164.504"]),
    ],
)
def test_parse_many(text, expected):
    assert [str(a) for a in Address.parse_many(text)] == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "Give a citation or address"),
        ("cfr:/", "Give a citation or address"),
        ("45/foo", "'foo' isn't part of an address"),
        ("45/164.502/(a)", "'\\(a\\)' isn't a paragraph marker"),
        ("section-164.502", "needs a title number first, e.g. 45/section-164.502"),
        ("164.502", "needs a title number first, e.g. 45 CFR 164.502"),
        ("hipaa.md", "isn't named like a file ecfr-md saves"),
        ("45-cfr-whatever-1.md", "isn't a file name ecfr-md wrote"),
        ("45/164.502/d..b", "isn't a range of paragraphs"),
        ("45/164.502@2020-13-01", "'2020-13-01' is not a date"),
        ("https://example.com/title-45", "isn't an eCFR, LII or GovInfo link"),
        ("https://www.ecfr.gov/current/agency-45", "isn't a page of CFR text"),
        ("https://www.ecfr.gov/current/title-45/table-1", "isn't a page of CFR text"),
        ("https://www.ecfr.gov/api/admin/v1/agencies.json", "doesn't name a title"),
        ("https://www.law.cornell.edu/uscode/text/5", "isn't a page of CFR text"),
        ("https://www.govinfo.gov/app/collection/cfr", "isn't a link to CFR text"),
        ("/us/cfr/x45", "isn't a USLM identifier"),
        ("/us/cfr/t45/zz1", "isn't a USLM level"),
        ("45/164.502,164.504", "names 2 places"),
        ("45/164.502/a/i..v", "names 5 places \\(.*, \\.\\.\\.\\)"),
    ],
)
def test_parse_mistakes_are_explained(text, message):
    with pytest.raises(InvalidCitationError, match=message):
        Address.parse(text)


def test_titles_default_for_relative_addresses():
    assert str(Address.parse("164.502/a", title=45)) == "45/164.502/a"
    assert str(Address.parse("Part 164", title=45)) == "45/part-164"
    assert str(Address.parse("subpart-E", title=45)) == "45/subpart-E"
    assert str(Address.parse("164.502", title=45)) == "45/164.502"
    assert str(Address.parse(45)) == "45"
    tree = "out/part-164/subpart-A/164.104.md"
    assert str(Address.parse(tree, title=45)) == "45/164.104"
    same = Address.parse("45/164.502")
    assert Address.parse(same) == same and Address.parse(same) is not same


def test_formats():
    paragraph = Address.parse("45/164.502/a/1@2020-01-01")
    assert paragraph.level == "paragraph"
    assert paragraph.citation() == "45 CFR 164.502(a)(1)"
    assert paragraph.citation("bluebook") == "45 C.F.R. § 164.502(a)(1) (2020)"
    assert paragraph.citation("bluebook", year=2023).endswith("(2023)")
    assert paragraph.uri() == "cfr:45/164.502/a/1@2020-01-01"
    assert paragraph.file_name() == "45_164.502_a_1@2020-01-01.md"
    assert paragraph.undated().file_name() == "45_164.502_a_1.md"
    assert paragraph.url() == (
        "https://www.ecfr.gov/on/2020-01-01/title-45/section-164.502#p-164.502(a)(1)"
    )
    supplement = Address.parse("12/part-1026/supplement-I")
    assert supplement.level == "appendix"
    assert supplement.citation() == "12 CFR Supplement I to Part 1026"
    assert supplement.citation("bluebook") == "12 C.F.R. pt. 1026, supp. I"
    assert supplement.url() == (
        "https://www.ecfr.gov/current/title-12/part-1026/"
        "appendix-Supplement%20I%20to%20Part%201026"
    )
    table = Address.parse("40/part-60/subpart-VVVV/table-1")
    assert table.citation() == "40 CFR Table 1 to Subpart VVVV of Part 60"
    assert table.citation("bluebook") == (
        "40 C.F.R. pt. 60, subpt. VVVV, Table 1 to Subpart VVVV of Part 60"
    )
    group = Address.parse("12/part-9/subpart-B/group-ECFR1a")
    assert group.level == "subject_group"
    assert group.citation() == "12 CFR Part 9 Subpart B"
    assert (
        group.url()
        == "https://www.ecfr.gov/current/title-12/part-9/subject-group-ECFR1a"
    )
    assert Address.parse("45/chapter-I/subchapter-C").citation("bluebook") == (
        "45 C.F.R. ch. I, subch. C"
    )
    assert Address.parse("45").citation("bluebook") == "45 C.F.R."
    assert Address.parse("45").url() == "https://www.ecfr.gov/current/title-45"
    assert (
        Address.parse("48/chapter-2/appendix-A")
        .url()
        .endswith("/title-48/appendix-Appendix%20A")
    )
    with pytest.raises(InvalidCitationError, match="style must be 'ofr' or 'bluebook'"):
        paragraph.citation("apa")  # type: ignore[arg-type]


def test_identifiers():
    assert encode_id("IX [RESERVED]") == "IX"
    assert encode_id("50-1 - 50-200") == "50-1..50-200"
    assert encode_id("E through G") == "E..G"
    assert encode_id("[Reserved]") == "-"
    assert encode_section("1222. 27") == "1222.27"
    assert encode_section("88.2 through 88.3") == "88.2..88.3"
    assert decode_section("105.60.001") == "105.60.001"
    assert decode_section("48.4061.a") == "48.4061(a)"
    assert decode_section("1.142.a.5-1") == "1.142(a)(5)-1"
    assert appendix_key("Appendixes A through C to Part 52") == "appendixes-A..C"
    assert appendix_key("Appendix: A to Part 1") == "appendix-A"
    assert appendix_key("Non-mandatory Appendix A to Part 1") == (
        "appendix-Non-mandatory-Appendix-A-to-Part-1"
    )
    assert appendix_key("Tables to Part 132") == "tables"
    assert appendix_key("supplement-I") == "supplement-I"
    assert (
        appendix_name("table-1", "60", "VVVV") == "Table 1 to Subpart VVVV of Part 60"
    )
    assert appendix_name("tables", None, None) == "Tables"
    assert edition_date(1, 2020) == dt.date(2020, 1, 1)
    assert edition_date(50, 2020) == dt.date(2020, 10, 1)
