"""Typical user code, type-checked in CI (not run): ``uv run pyright``.

If a change to ecfr-md makes ordinary use fail type checking, this file fails.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import ecfr_md
from ecfr_md import (
    ECFR,
    Address,
    AmbiguousNodeError,
    Change,
    Citation,
    Level,
    LevelName,
    Matches,
    Node,
    Outline,
    Paragraph,
    Settings,
)


def usage() -> None:
    settings = Settings(user_agent="my-app/1.0", rate_limit=2, cache=False)
    with ECFR(settings) as ecfr:
        part = ecfr.get("45 CFR Part 164", date="2024-01-01")
        part = ecfr.get(45, part="164", date=dt.date(2024, 1, 1))
        section: Node = part["164.502"]
        text: str = section["(a)(1)"].text
        parent: Node | None = section["(a)(1)"].section
        file: Path = ecfr.save("45 CFR 164.502", "out.md")
        file = part.save("out.md")
        files: list[Path] = ecfr.save("45 CFR Part 164", "out", tree=True)
        files = part.save(Path("out"), tree=True, front_matter=False)
        pdf: Path = part.export("hipaa.pdf")
        pages: list[Path] = part.export("site", "html", tree=True)
        file = ecfr.save("45 CFR 164.502", "out", format="docx")
        print(pdf, pages, file)
        nodes: list[Node] = ecfr.get_many(["45 CFR 164.502", "45 CFR 164.506"])
        changes: list[Change] = ecfr.changes("45 CFR Part 164", since="last year")
        outline: Outline = ecfr.outline("45 CFR Part 164")["Subpart E"]
        result = ecfr.sync("out", migrate=True)
        moved: list[tuple[Path, Path]] = result.moved
        address: Address = Address.parse("45 CFR 164.502(a)(1) (2018)")
        cited: str = address.citation("bluebook")
        same: Node = part[address] if address.title == 45 else part["164.502/a/1"]
        print(
            moved, cited, same.address, same.uri, same.cite("bluebook"), address.url()
        )
        matches: Matches = ecfr.search("minimum necessary", limit=5)
        found = ecfr.api.search.results("minimum necessary", per_page=10)
    quick: Node = ecfr_md.get("45 CFR 164.502")
    saved: Path = ecfr_md.save("45 CFR 164.502", "out.md")
    tree: list[Path] = ecfr_md.save("45 CFR Part 164", "out", tree=True)
    book: Path = ecfr_md.save("45 CFR Part 164", "out.epub", format="epub")
    print(book)
    print(quick, saved, tree, [m.citation for m in matches], [c.kind for c in changes])
    print(ecfr_md.outline(45), ecfr_md.changes(45), ecfr_md.search("x"))
    for paragraph in section.iter("paragraph"):
        if isinstance(paragraph, Paragraph):
            print(paragraph.citation, paragraph.depth, paragraph.heading)
    try:
        part["(1)"]
    except AmbiguousNodeError as error:
        print([n.citation for n in error.candidates])
    print(text, parent, file, files, nodes, outline, result, found)
    print(Citation.parse("45 CFR 164.502(a)").paragraph)


def objects(
    part: Node, outline: Outline, changes: list[Change], matches: Matches
) -> None:
    paragraph = part["164.512(k)"]
    whole: str = paragraph.text
    if isinstance(paragraph, Paragraph):
        lead: str = paragraph.own_text
        print(lead)
    short: str = part.to_markdown(max_chars=4000)
    toc: str = part.toc(depth=2)
    data: dict[str, Any] = part.to_dict(depth=1, text=False, include_raw=True)
    rows = [c.to_dict() for c in changes] + [m.to_dict() for m in matches]
    print(json.dumps([data, outline.to_dict(depth=0), *rows]), whole, short, toc)
    level: LevelName = "section"
    sections: list[Node] = list(part.iter(level))
    found: Node | None = part.find(level=Level.APPENDIX)
    entry: Outline | None = outline.find("164.512", level="section")
    above: tuple[Outline, ...] = outline["164.512"].ancestors
    kind: Level | str = outline.level
    url: str = part.url + outline.url + changes[0].url + matches[0].url
    print(sections, found, entry, above, kind, url, part.cfr_title)
