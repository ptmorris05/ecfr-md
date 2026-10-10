"""Typical user code, type-checked in CI (not run): ``uv run pyright``.

If a change to ecfr-md makes ordinary use fail type checking, this file fails.
"""

import datetime as dt
from pathlib import Path

from ecfr_md import ECFR, AmbiguousNodeError, Citation, Node, Paragraph, Settings


def usage() -> None:
    settings = Settings(user_agent="my-app/1.0", rate_limit=2, cache=False)
    with ECFR(settings) as ecfr:
        part = ecfr.get("45 CFR Part 164", date="2024-01-01")
        part = ecfr.get(45, part="164", date=dt.date(2024, 1, 1))
        section: Node = part["164.502"]
        text: str = section["(a)(1)"].text
        parent: Node | None = section["(a)(1)"].section
        files: list[Path] = ecfr.save("45 CFR 164.502", "out.md")
        files = part.save(Path("out"), tree=True, front_matter=False)
        nodes: list[Node] = ecfr.get_many(["45 CFR 164.502", "45 CFR 164.506"])
        changes = ecfr.changes("45 CFR Part 164", since="last year")
        outline = ecfr.outline("45 CFR Part 164")
        result = ecfr.sync("out")
        found = ecfr.search.results("minimum necessary", per_page=10)
    for paragraph in section.iter("paragraph"):
        if isinstance(paragraph, Paragraph):
            print(paragraph.citation, paragraph.depth, paragraph.heading)
    try:
        part["(1)"]
    except AmbiguousNodeError as error:
        print([n.citation for n in error.candidates])
    print(text, parent, files, nodes, changes, outline, result, found)
    print(Citation.parse("45 CFR 164.502(a)").paragraph)
