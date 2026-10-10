"""Saving Markdown with provenance.

Saved files can start with YAML front matter: the citation, the point-in-time
date, the eCFR page and API request the text came from, when it was retrieved,
and the ecfr-md version. Files are written to a temporary name and renamed, so
an interrupted save doesn't leave a partial file.
"""

import datetime as dt
import html
import json
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ecfr_md import markdown
from ecfr_md.citation import Citation
from ecfr_md.config import BASE_URL, DISCLAIMER, __version__
from ecfr_md.params import Hierarchy, as_hierarchy

# XML division types that are API hierarchy levels.
TYPE_LEVELS = {
    "SUBTITLE": "subtitle",
    "CHAPTER": "chapter",
    "SUBCHAP": "subchapter",
    "PART": "part",
    "SUBPART": "subpart",
    "SECTION": "section",
    "APPENDIX": "appendix",
}
URL_LEVELS = ("subtitle", "chapter", "subchapter", "part", "subpart")


@dataclass(frozen=True)
class Source:
    """Where a document came from; shared by every node in it."""

    title: int
    date: dt.date | None = None
    hierarchy: Hierarchy = field(default_factory=Hierarchy)
    url: str | None = None  # The API request.
    retrieved: dt.datetime | None = None


def web_url(
    title: int,
    date: dt.date | None,
    hierarchy: Hierarchy,
    paragraph: tuple[str, ...] = (),
) -> str:
    """The eCFR page showing this text, e.g. .../on/2024-01-01/title-45/section-1.1."""
    h = hierarchy
    if "section" in h:
        section, part = h["section"], h.get("part")
        # The eCFR prefixes sections numbered unlike their part: 43 CFR Part
        # 5420 holds § 5422.1, at "section-5420.5422.1".
        if part and not section.startswith(f"{part}."):
            section = f"{part}.{section}"
        path = [f"section-{section}"]
    elif "appendix" in h:
        path = [f"part-{h['part']}"] if "part" in h else []
        path.append(f"appendix-{h['appendix']}")
    else:
        path = [f"{level}-{v}" for level in URL_LEVELS if (v := h.get(level))]
    when = f"on/{date}" if date else "current"
    url = "/".join([BASE_URL, when, f"title-{title}", *(quote(p) for p in path)])
    if paragraph and "section" in h:
        url += f"#p-{h['section']}" + "".join(f"({m})" for m in paragraph)
    return url


def page_path(el: ET.Element | None) -> str | None:
    """The eCFR page path the API records on a division, if it does."""
    raw = el.get("hierarchy_metadata") if el is not None else None
    if not raw:
        return None
    try:  # Sometimes HTML-escaped twice.
        return json.loads(html.unescape(html.unescape(raw))).get("path")
    except ValueError:
        return None


def page_url(
    page: str, date: dt.date | None, hierarchy: Hierarchy, paragraph: tuple[str, ...]
) -> str:
    when = f"/on/{date}" if date else "/current"
    url = BASE_URL + quote(page.replace("/on/_SUBSTITUTE_DATE_", when), safe="/")
    if paragraph and "section" in hierarchy:
        url += f"#p-{hierarchy['section']}" + "".join(f"({m})" for m in paragraph)
    return url


def provenance(
    source: Source,
    hierarchy: Hierarchy,
    *,
    heading: str = "",
    paragraph: tuple[str, ...] = (),
    page: str | None = None,
) -> dict[str, str]:
    """Metadata describing which text a document holds.

    ``page`` is the eCFR page path from the XML, when the API provided one;
    otherwise the page URL is built from the hierarchy.
    """
    if page:
        url = page_url(page, source.date, hierarchy, paragraph)
    else:
        url = web_url(source.title, source.date, hierarchy, paragraph)
    fields = {
        "citation": str(Citation(source.title, hierarchy, paragraph)),
        "heading": heading,
        "date": source.date.isoformat() if source.date else "",
        "source": url,
        "api": source.url or "",
        "retrieved": (
            source.retrieved.astimezone(dt.UTC).isoformat(timespec="seconds")
            if source.retrieved
            else ""
        ),
        "generator": f"ecfr-md {__version__}",
        "disclaimer": DISCLAIMER,
    }
    return {key: value for key, value in fields.items() if value}


def read_front_matter(path: Path) -> dict[str, str] | None:
    """The front matter of a saved file, or None if it has none."""
    try:
        with path.open(encoding="utf-8") as f:
            if f.readline() != "---\n":
                return None
            fields = {}
            for line in f:
                if line == "---\n":
                    return fields
                key, _, value = line.rstrip("\n").partition(": ")
                fields[key] = json.loads(value)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def subject_group_citation(parent: str, heading: str) -> str:
    """The eCFR's citation for a subject group, e.g. "13 CFR Part 108 Subpart C - X"."""
    return f"{parent} - {heading}" if heading else parent


def subject_group_url(source: Source, hierarchy: Hierarchy, identifier: str) -> str:
    """The eCFR addresses subject groups under their part, not their subpart."""
    above = {k: v for k, v in hierarchy.items() if k in URL_LEVELS and k != "subpart"}
    page = web_url(source.title, source.date, as_hierarchy(above))
    return f"{page}/subject-group-{quote(identifier)}"


def render_front_matter(fields: dict[str, str]) -> str:
    """YAML front matter; JSON strings are valid YAML, so values are quoted safely."""
    lines = [
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in fields.items()
    ]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def save_events(
    events: Iterable[tuple[str, ET.Element]],
    source: Source,
    path: Path,
    *,
    tree: bool = False,
    front_matter: bool = True,
    free: bool = False,
    base: Hierarchy | None = None,
    on_file: Callable[[Path], object] | None = None,
) -> list[Path]:
    """Write Markdown for a stream of XML events and return the files written.

    ``base`` locates the first element (its ancestors' hierarchy); it defaults
    to the hierarchy of the request.
    """
    base = source.hierarchy if base is None else base
    if tree:
        written: list[Path] = []

        def emit_file(
            relative: PurePosixPath, body: str, chain: list[ET.Element], heading: str
        ) -> None:
            levels = {
                TYPE_LEVELS[e.get("TYPE", "")]: e.get("N", "")
                for e in chain
                if e.get("TYPE") in TYPE_LEVELS
            }
            hierarchy = as_hierarchy({**base, **levels})
            page = page_path(chain[-1])
            fields = provenance(source, hierarchy, heading=heading, page=page)
            if chain[-1].get("TYPE") == "SUBJGRP":
                fields["citation"] = subject_group_citation(fields["citation"], heading)
                if page is None:
                    fields["source"] = subject_group_url(
                        source, hierarchy, chain[-1].get("N", "")
                    )
            written.append(
                _notify(
                    on_file,
                    write_text(
                        path / relative,
                        (render_front_matter(fields) if front_matter else "") + body,
                    ),
                )
            )

        markdown.write_tree(events, emit_file, free=free)
        return written

    target = (
        path / file_name(str(Citation(source.title, base)))
        if _is_directory(path)
        else path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    try:
        with partial.open("w", encoding="utf-8", newline="\n") as out:
            blocks = 0

            def emit(block: str) -> None:
                nonlocal blocks
                out.write(block if not blocks else "\n\n" + block)
                blocks += 1

            def on_heading(heading: str, division: ET.Element) -> None:
                if front_matter:
                    page = page_path(division)
                    fields = provenance(source, base, heading=heading, page=page)
                    out.write(render_front_matter(fields))

            markdown.write_document(events, emit, on_heading=on_heading, free=free)
            out.write("\n")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return [_notify(on_file, target)]


def _notify(on_file: Callable[[Path], object] | None, path: Path) -> Path:
    if on_file is not None:
        on_file(path)
    return path


def write_text(path: Path, text: str) -> Path:
    """Write a file via a temporary name and rename, so it is never partial."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    try:
        partial.write_text(text, encoding="utf-8", newline="\n")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def file_name(citation: str) -> str:
    """A file name for a citation, e.g. "45-cfr-164.502-a.md".

    Names include the title, so files from different titles saved into one
    directory don't overwrite each other.
    """
    return markdown.make_slug("", citation).lstrip("-") + ".md"


def _is_directory(path: Path) -> bool:
    return path.is_dir() or str(path).endswith(("/", "\\"))
