"""Saving Markdown with provenance.

Saved files can start with YAML front matter: the citation and address, the
point-in-time date (and ``pinned`` when that date was asked for and is older
than the latest), the eCFR page and API request the text came from, when it
was retrieved, the ecfr-md version, the ``layout`` of file names (2; files
saved by ecfr-md 0.x have none) and a ``digest`` of the text, so ``sync`` can
tell when a file was edited. Files are written to a temporary name and
renamed, so an interrupted save doesn't leave a partial file.
"""

import datetime as dt
import hashlib
import html
import json
import os
import posixpath
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from ecfr_md import markdown
from ecfr_md.address import Address
from ecfr_md.citation import Citation
from ecfr_md.config import DISCLAIMER, SITE_URL, __version__
from ecfr_md.params import Hierarchy, as_hierarchy
from ecfr_md.references import Reference, link_markdown

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
# Front matter ends with a line "---" and a blank line; the text follows.
END_OF_FRONT_MATTER = b"\n---\n\n"
# Written in place of a streamed file's digest until its text is complete.
BLANK_DIGEST = "sha256:" + "0" * 64


@dataclass(frozen=True)
class Source:
    """Where a document came from; shared by every node in it."""

    title: int
    date: dt.date | None = None
    hierarchy: Hierarchy = field(default_factory=Hierarchy)
    url: str | None = None  # The API request.
    retrieved: dt.datetime | None = None
    # The date was asked for and is older than the latest: a snapshot that
    # ``sync`` leaves alone.
    pinned: bool = False
    site: str = SITE_URL  # The eCFR web site that page links go to.
    # Link templates for other codes ("usc", "fr", "public_law"), if not the
    # defaults; see find_references.
    links: tuple[tuple[str, str], ...] = ()


def web_url(
    title: int,
    date: dt.date | None,
    hierarchy: Hierarchy,
    paragraph: tuple[str, ...] = (),
    site: str = SITE_URL,
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
    # Segments are quoted whole: an appendix name can contain "/".
    segments = [quote(p, safe="") for p in path]
    url = "/".join([site.rstrip("/"), when, f"title-{title}", *segments])
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
    page: str,
    date: dt.date | None,
    hierarchy: Hierarchy,
    paragraph: tuple[str, ...],
    site: str = SITE_URL,
) -> str:
    when = f"/on/{date}" if date else "/current"
    url = site.rstrip("/") + quote(
        page.replace("/on/_SUBSTITUTE_DATE_", when), safe="/"
    )
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
    address: str | None = None,
    layout: int = markdown.LAYOUT,
    links: bool | None = None,
) -> dict[str, Any]:
    """Metadata describing which text a document holds.

    ``page`` is the eCFR page path from the XML, when the API provided one;
    otherwise the page URL is built from the hierarchy. ``address`` defaults
    to the hierarchy's. With ``layout=1`` (ecfr-md 0.x's), there is no
    ``address`` or ``layout``. ``links`` records whether cross-references
    were made links, so ``sync`` keeps that; None leaves it out.
    """
    if page:
        url = page_url(page, source.date, hierarchy, paragraph, source.site)
    else:
        url = web_url(source.title, source.date, hierarchy, paragraph, source.site)
    if address is None:
        address = str(Address(source.title, hierarchy, paragraph))
    new = layout >= markdown.LAYOUT
    fields: dict[str, Any] = {
        "citation": str(Citation(source.title, hierarchy, paragraph)),
        "address": address if new else "",
        "heading": heading,
        "date": source.date.isoformat() if source.date else "",
        "pinned": source.date.isoformat() if source.date and source.pinned else "",
        "source": url,
        "api": source.url or "",
        "retrieved": (
            source.retrieved.astimezone(dt.UTC).isoformat(timespec="seconds")
            if source.retrieved
            else ""
        ),
        "generator": f"ecfr-md {__version__}",
        "layout": layout if new else "",
        "disclaimer": DISCLAIMER,
    }
    kept = {key: value for key, value in fields.items() if value}
    return kept if links is None else {**kept, "links": links}


def context(source: Source, base: Hierarchy, chain: list[ET.Element]) -> Address:
    """Where text inside a chain of divisions is, at a snapshot's date."""
    levels = {
        TYPE_LEVELS[e.get("TYPE", "")]: e.get("N", "")
        for e in chain
        if e.get("TYPE") in TYPE_LEVELS
    }
    date = source.date if source.pinned else None
    return Address(source.title, as_hierarchy({**base, **levels}), date=date)


def linker(
    source: Source, base: Hierarchy, href: Callable[[Reference], str | None] | None
) -> markdown.Linker:
    """Link the cross-references in each block of a document (see
    :func:`markdown.write_document`): to ``href``'s choice, else the web."""

    def link(block: str, chain: list[ET.Element]) -> str:
        return link_markdown(
            block,
            context(source, base, chain),
            href,
            urls=dict(source.links),
            site=source.site,
        )

    return link


def tree_link(
    source: Source, index: dict[str, str], file: PurePosixPath
) -> Callable[[Reference], str | None]:
    """Where a file of a saved tree links a reference: the file in the tree
    that holds it, relative to ``file``, or None if it's outside the tree."""

    def href(reference: Reference) -> str | None:
        target = reference.target
        if target is None or target.title != source.title:
            return None
        path = index.get(markdown.tree_key(target) or "")
        return posixpath.relpath(path, file.parent.as_posix()) if path else None

    return href


def read_front_matter(path: Path) -> dict[str, Any] | None:
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


def body_digest(body: bytes) -> str:
    """The ``digest`` recorded for the text after a file's front matter."""
    return "sha256:" + hashlib.sha256(body).hexdigest()


def with_front_matter(fields: dict[str, Any], body: str) -> str:
    """A whole saved file: front matter, including the body's digest, and body."""
    digest = body_digest(body.encode("utf-8"))
    return render_front_matter({**fields, "digest": digest}) + body


def is_ours(path: Path) -> bool:
    """Whether a file starts with front matter written by ecfr-md."""
    meta = read_front_matter(path)
    return bool(meta) and str(meta.get("generator", "")).startswith("ecfr-md")


def edited(path: Path) -> bool:
    """Whether a saved file's text no longer matches the digest saved with it.

    Files saved before ecfr-md recorded digests have none and count as unedited.
    """
    digest = (read_front_matter(path) or {}).get("digest")
    if not digest:
        return False
    parts = path.read_bytes().split(END_OF_FRONT_MATTER, 1)
    return len(parts) < 2 or body_digest(parts[1]) != digest


def stale_files(root: Path, keep: set[Path]) -> list[Path]:
    """Files under ``root`` that ecfr-md wrote, other than ``keep``."""
    return [p for p in sorted(root.rglob("*.md")) if p not in keep and is_ours(p)]


def remove_files(files: Iterable[Path], root: Path) -> None:
    """Delete files, then the folders under ``root`` that this left empty."""
    for file in files:
        file.unlink()
        folder = file.parent
        while folder != root and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent


def subject_group_citation(parent: str, heading: str) -> str:
    """The eCFR's citation for a subject group, e.g. "13 CFR Part 108 Subpart C - X"."""
    return f"{parent} - {heading}" if heading else parent


def subject_group_url(source: Source, hierarchy: Hierarchy, identifier: str) -> str:
    """The eCFR addresses subject groups under their part, not their subpart."""
    above = {k: v for k, v in hierarchy.items() if k in URL_LEVELS and k != "subpart"}
    page = web_url(source.title, source.date, as_hierarchy(above), site=source.site)
    return f"{page}/subject-group-{quote(identifier)}"


def render_front_matter(fields: dict[str, Any]) -> str:
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
    names: list[str] | None = None,
    layout: int = markdown.LAYOUT,
    links: bool = False,
    index: dict[str, str] | None = None,
) -> list[Path]:
    """Write Markdown for a stream of XML events and return the files written.

    ``base`` locates the first element (its ancestors' hierarchy); it defaults
    to the hierarchy of the request. A tree's ``names`` come from
    :func:`markdown.tree_names` (a first pass over the same XML), as does
    its ``index`` of files, needed with ``links``; without them, the events
    are read into memory first. ``layout=1`` writes a tree as ecfr-md 0.x
    did, to sync trees it saved. With ``links``, cross-references become
    links: in a tree, to the file that holds them, else to the eCFR.
    """
    base = source.hierarchy if base is None else base
    if tree:
        written: list[Path] = []
        if names is None or (links and index is None):
            events = list(events)
            index = {}
            names = markdown.tree_names(events, layout=layout, index=index)

        def emit_file(
            relative: PurePosixPath, body: str, chain: list[ET.Element], heading: str
        ) -> None:
            address = context(source, base, chain)
            hierarchy = address.hierarchy
            if links and index is not None:
                href = tree_link(source, index, relative)
                body = link_markdown(
                    body, address, href, urls=dict(source.links), site=source.site
                )
            page = page_path(chain[-1])
            fields = provenance(
                source,
                hierarchy,
                heading=heading,
                page=page,
                layout=layout,
                links=links,
            )
            if chain[-1].get("TYPE") == "SUBJGRP":  # Only in layout 1.
                fields["citation"] = subject_group_citation(fields["citation"], heading)
                if page is None:
                    fields["source"] = subject_group_url(
                        source, hierarchy, chain[-1].get("N", "")
                    )
            text = with_front_matter(fields, body) if front_matter else body
            written.append(_notify(on_file, write_text(path / relative, text)))

        markdown.write_tree(events, emit_file, names=names, free=free)
        _remove_stale(path, written)
        return written

    target = (
        path / file_name(Address(source.title, base), source)
        if _is_directory(path)
        else path
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    try:
        with partial.open("wb") as out:
            blocks = 0
            # The text streams out after the front matter, so the digest is
            # written blank and filled in once the text is complete.
            digest = hashlib.sha256()
            blank_at: int | None = None

            def write(text: str) -> None:
                data = text.encode("utf-8")
                out.write(data)
                if blank_at is not None:
                    digest.update(data)

            def emit(block: str) -> None:
                nonlocal blocks
                write(block if not blocks else "\n\n" + block)
                blocks += 1

            def on_heading(heading: str, division: ET.Element) -> None:
                nonlocal blank_at
                if front_matter:
                    page = page_path(division)
                    fields = provenance(
                        source,
                        base,
                        heading=heading,
                        page=page,
                        layout=layout,
                        links=links or None,
                    )
                    header = render_front_matter({**fields, "digest": BLANK_DIGEST})
                    out.write(header.encode("utf-8"))
                    # The digest is the last field: its hex digits end just
                    # before its closing quote and the end of the front matter.
                    blank_at = out.tell() - len(END_OF_FRONT_MATTER) - 1 - 64

            link = linker(source, base, None) if links else None
            markdown.write_document(
                events, emit, on_heading=on_heading, free=free, link=link
            )
            write("\n")
            if blank_at is not None:
                out.seek(blank_at)
                out.write(digest.hexdigest().encode("ascii"))
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


def _remove_stale(path: Path, written: list[Path]) -> None:
    """Remove what an earlier save of the same tree left and this one didn't
    write, so a tree never mixes dates. Files ecfr-md didn't write, or that
    were edited since, are kept."""
    keep = set(written)
    for root in {path / p.relative_to(path).parts[0] for p in written}:
        if root.is_dir():  # A section saved as a tree is a single file.
            remove_files([p for p in stale_files(root, keep) if not edited(p)], root)


def file_name(address: Address, source: Source | None = None) -> str:
    """The file a single node is saved to: its address with "/" as "_",
    e.g. "45_164.502_a_1.md".

    Names include the title, so files from different titles saved into one
    directory don't overwrite each other, and a dated snapshot's date, e.g.
    "45_part-164@2018-01-01.md", so snapshots don't either.
    """
    date = source.date if source is not None and source.pinned else None
    return replace(address, date=date).file_name()


def legacy_file_name(citation: str, pinned: dt.date | None = None) -> str:
    """The name ecfr-md 0.x gave a saved file, e.g. "45-cfr-164.502-a.md"."""
    name = re.sub(r"[^a-z0-9.]+", "-", citation.lower()).strip("-.")
    return (f"{name}@{pinned.isoformat()}" if pinned else name) + ".md"


def _is_directory(path: Path) -> bool:
    return path.is_dir() or str(path).endswith(("/", "\\"))
