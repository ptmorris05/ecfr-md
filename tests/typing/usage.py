"""Typical user code, type-checked in CI (not run): ``uv run pyright``.

If a change to ecfr-md makes ordinary use fail type checking, this file fails.
"""

import datetime as dt
from pathlib import Path

import ecfr_md
from ecfr_md import (
    ECFR,
    AmbiguousNodeError,
    Change,
    Citation,
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
        nodes: list[Node] = ecfr.get_many(["45 CFR 164.502", "45 CFR 164.506"])
        changes: list[Change] = ecfr.changes("45 CFR Part 164", since="last year")
        outline: Outline = ecfr.outline("45 CFR Part 164")["Subpart E"]
        result = ecfr.sync("out")
        matches: Matches = ecfr.search("minimum necessary", limit=5)
        found = ecfr.api.search.results("minimum necessary", per_page=10)
    quick: Node = ecfr_md.get("45 CFR 164.502")
    saved: Path = ecfr_md.save("45 CFR 164.502", "out.md")
    tree: list[Path] = ecfr_md.save("45 CFR Part 164", "out", tree=True)
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
