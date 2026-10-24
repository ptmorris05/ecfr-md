"""What ``outline``, ``changes`` and ``search`` return: plain objects with
citations and clean headings. The API's response is kept in ``.raw``.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Literal

from ecfr_md import models
from ecfr_md.citation import KEYWORDS, Citation
from ecfr_md.errors import AmbiguousNodeError, NodeNotFoundError
from ecfr_md.params import as_hierarchy

TAG = re.compile(r"<[^>]+>")
CITED = re.compile(r"\d+\s*C\.?\s*F\.?\s*R\b", re.IGNORECASE)
# Levels a citation names, deepest first.
CITED_LEVELS = ("appendix", "section", "subpart", "part", "subchapter", "chapter")
SENTENCE_END = ".;:!?)\"”'"


@dataclass(eq=False)
class Outline:
    """An entry in a table of contents, with its children.

    Iterate over it for its children; index it like a node, by position or
    identifier at any depth: ``outline["Subpart E"]``, ``outline["164.512"]``.
    ``str(outline)`` is the indented table of contents.
    """

    level: str
    """``"title"``, ``"part"``, ``"subpart"``, ``"section"``, ``"appendix"``, ..."""
    identifier: str | None
    heading: str
    """The label without markup, e.g. "Part 164—Security and Privacy"."""
    citation: str
    reserved: bool
    size: int | None
    """Size of the full text in bytes, where the eCFR gives it (parts)."""
    raw: models.Node = field(repr=False)
    parent: Outline | None = field(default=None, repr=False)
    children: tuple[Outline, ...] = field(default=(), repr=False)

    @classmethod
    def build(
        cls,
        node: models.Node,
        title: int,
        hierarchy: Mapping[str, object] | None = None,
        parent: Outline | None = None,
    ) -> Outline:
        levels: dict[str, object] = dict(hierarchy or {})
        if node.type in KEYWORDS.values() and node.identifier:
            levels[node.type] = node.identifier
        entry = cls(
            level=node.type,
            identifier=node.identifier,
            heading=" ".join(TAG.sub("", node.label).split()),
            citation=str(Citation(title, as_hierarchy(levels))),
            reserved=node.reserved,
            size=node.size,
            raw=node,
            parent=parent,
        )
        entry.children = tuple(
            cls.build(child, title, levels, entry) for child in node.children
        )
        return entry

    def __iter__(self) -> Iterator[Outline]:
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def __bool__(self) -> bool:
        return True

    def __getitem__(self, key: str | int) -> Outline:
        """A child by position, or the nearest entry by identifier.

        Keys can name their level ("Subpart E", "§ 164.512") or be citations
        ("45 CFR 164.512"). Raises NodeNotFoundError if nothing matches and
        AmbiguousNodeError if several entries match at the nearest depth.
        """
        if isinstance(key, int):
            return self.children[key]
        level, identifier = _lookup(key)
        for same in (str.__eq__, lambda a, b: a.casefold() == b.casefold()):
            layer = list(self.children)
            while layer:
                matches = [
                    e
                    for e in layer
                    if e.identifier is not None
                    and same(e.identifier, identifier)
                    and level in (None, e.level)
                ]
                if len(matches) == 1:
                    return matches[0]
                if matches:
                    found = ", ".join(m.citation for m in matches[:5])
                    raise AmbiguousNodeError(
                        f"{len(matches)} entries match {key!r} in {self.citation}: "
                        f"{found}",
                        matches,
                    )
                layer = [c for e in layer for c in e.children]
        raise NodeNotFoundError(f"No {key!r} in {self.citation}")

    def get(self, key: str | int, default: Outline | None = None) -> Outline | None:
        """Like ``outline[key]``, but return ``default`` when nothing matches."""
        try:
            return self[key]
        except (NodeNotFoundError, IndexError):
            return default

    def iter(self, level: str | None = None) -> Iterator[Outline]:
        """Every entry below this one in document order, optionally one level."""
        for child in self.children:
            if level is None or child.level == level:
                yield child
            yield from child.iter(level)

    def __str__(self) -> str:
        return "\n".join(_lines(self, 0))

    def __repr__(self) -> str:
        return f"<Outline {self.citation} {self.heading!r}>"


def _lookup(key: str) -> tuple[str | None, str]:
    """The level (if named) and identifier a key asks for."""
    text = key.strip()
    if CITED.match(text):
        cited = Citation.parse(text)
        for level in CITED_LEVELS:
            if value := cited.hierarchy.get(level):
                return level, value
    words = text.replace("§", " ").split()
    level = KEYWORDS.get(words[0].lower().rstrip(".")) if len(words) > 1 else None
    if level and level != "appendix":  # Appendix names start with "Appendix".
        words = words[1:]
    return level, " ".join(words)


def _lines(entry: Outline, depth: int) -> Iterator[str]:
    yield "  " * depth + entry.heading
    for child in entry.children:
        yield from _lines(child, depth + 1)


@dataclass(frozen=True)
class Change:
    """A version of a section or appendix the eCFR issued."""

    citation: str
    """E.g. "45 CFR 164.502"."""
    identifier: str
    heading: str
    """The heading without its number, e.g. "Uses and disclosures of ..."."""
    issued: dt.date
    """When the eCFR published the change."""
    effective: dt.date
    """When the change took effect."""
    kind: Literal["substantive", "editorial", "removed"]
    raw: models.ContentVersion = field(repr=False)

    @classmethod
    def from_version(cls, version: models.ContentVersion) -> Change:
        name = " ".join(version.name.split())
        if version.removed:
            kind = "removed"
        elif version.substantive:
            kind = "substantive"
        else:
            kind = "editorial"
        return cls(
            citation=f"{version.title} CFR {version.identifier}",
            identifier=version.identifier,
            heading=_without_number(name, version.identifier),
            issued=version.issue_date,
            effective=version.date,
            kind=kind,
            raw=version,
        )


@dataclass(frozen=True)
class Match:
    """A section or appendix that matches a search."""

    citation: str
    """E.g. "45 CFR 164.514"; pass it to ``get``."""
    heading: str
    excerpt: str
    """Text around the matching words, without markup."""
    starts_on: dt.date
    ends_on: dt.date | None
    """When this text was replaced, or None if it is current."""
    raw: models.SearchResult = field(repr=False)

    @classmethod
    def from_result(cls, result: models.SearchResult) -> Match:
        h = result.hierarchy
        return cls(
            citation=f"{h.title} CFR {h.section or h.appendix}",
            heading=plain(result.headings.section or result.headings.appendix or ""),
            excerpt=_whole_words(plain(result.full_text_excerpt or "")),
            starts_on=result.starts_on,
            ends_on=result.ends_on,
            raw=result,
        )


class Matches(list[Match]):
    """Search matches, best first. ``total`` is how many the eCFR found."""

    def __init__(self, matches: Iterable[Match] = (), total: int | None = None):
        super().__init__(matches)
        self.total = total


def _without_number(name: str, identifier: str) -> str:
    """ "§ 164.502 Uses ..." -> "Uses ...", "Appendix A to Part 1—Forms" -> "Forms"."""
    for prefix in (f"§ {identifier}", identifier):
        if name.startswith(prefix) and name != prefix:
            return name.removeprefix(prefix).lstrip(" —-")
    return name


def plain(text: str) -> str:
    """Text without HTML tags or entities, on one line."""
    return " ".join(html.unescape(TAG.sub("", text)).split())


def _whole_words(text: str) -> str:
    """Excerpts are cut off mid-word; end them at a word instead."""
    if not text or text.endswith(tuple(SENTENCE_END)):
        return text
    return text.rsplit(" ", 1)[0] + "…"
