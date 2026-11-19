"""What ``outline``, ``changes`` and ``search`` return: plain objects with
citations and clean headings. The API's response is in ``.raw``.

``to_dict()`` turns each into a dict for ``json.dumps``, with the same keys as
:meth:`Node.to_dict <ecfr_md.document.Node.to_dict>`. Use it rather than
``dataclasses.asdict``, which keeps dates and API models as objects.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ecfr_md import models
from ecfr_md.address import Address, relative, walk
from ecfr_md.citation import KEYWORDS, Citation
from ecfr_md.config import SITE_URL
from ecfr_md.document import (
    REPR_MAX_CHARS,
    Level,
    LevelName,
    as_level,
    check_depth,
    truncate,
)
from ecfr_md.errors import AmbiguousNodeError, InvalidParameterError, NodeNotFoundError
from ecfr_md.output import Source, subject_group_url, web_url
from ecfr_md.params import Hierarchy, as_hierarchy

TAG = re.compile(r"<[^>]+>")
CITED = re.compile(r"\d+\s*C\.?\s*F\.?\s*R\b", re.IGNORECASE)
# Levels a citation names, deepest first.
CITED_LEVELS = ("appendix", "section", "subpart", "part", "subchapter", "chapter")
SENTENCE_END = ".;:!?)\"”'"
MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")


@dataclass(eq=False)
class Outline:
    """An entry in a table of contents, with its children.

    Iterate over it for its children; index it like a node, by position or
    identifier at any depth: ``outline["Subpart E"]``, ``outline["164.512"]``.
    ``str(outline)`` is the indented table of contents.
    """

    level: Level | str
    """A :class:`Level` such as ``Level.PART``; a plain string for entries the
    eCFR gives other types, such as unnumbered headings (``"hed1"``)."""
    identifier: str | None
    heading: str
    """The label without markup, e.g. "Part 164—Security and Privacy"."""
    citation: str
    reserved: bool
    size: int | None
    """Size of the full text in bytes, where the eCFR gives it (parts)."""
    _structure: models.Structure = field(repr=False)
    children: tuple[Outline, ...] = field(default=(), repr=False)
    date: dt.date | None = None
    """The point-in-time date of the outline, if known."""
    url: str = field(default="", repr=False)
    """The eCFR web page for this entry."""
    address: str = field(default="", repr=False)
    """The entry's address, e.g. "45/part-164/subpart-E"."""

    def __post_init__(self) -> None:
        # Not a dataclass field, so dataclasses.asdict() doesn't loop forever.
        self._parent: Outline | None = None

    @property
    def parent(self) -> Outline | None:
        """The entry this one is in, or None at the top of the outline."""
        return self._parent

    @property
    def raw(self) -> models.Node:
        """The API's structure node for this entry, with everything below it."""
        return self._structure.raw()

    @classmethod
    def build(
        cls,
        node: models.Structure | models.Node,
        title: int,
        hierarchy: Mapping[str, object] | None = None,
        parent: Outline | None = None,
        *,
        date: dt.date | None = None,
        site: str = SITE_URL,
    ) -> Outline:
        """An outline from the API's structure, with citations and links."""
        if isinstance(node, models.Node):
            node = models.Structure.of(node)
        levels: dict[str, object] = dict(hierarchy or {})
        if node.type in KEYWORDS.values() and node.identifier:
            levels[node.type] = node.identifier
        located = as_hierarchy(levels)
        group = node.identifier if node.type == "subject_group" else None
        if group:
            url = subject_group_url(Source(title, date, site=site), located, group)
        else:
            url = web_url(title, date, located, site=site)
        entry = cls(
            level=Level(node.type) if node.type in Level else node.type,
            identifier=node.identifier,
            heading=" ".join(TAG.sub("", node.label).split()),
            citation=str(Citation(title, located)),
            reserved=node.reserved,
            size=node.size,
            _structure=node,
            date=date,
            url=url,
            address=str(Address(title, located, group=group)),
        )
        entry._parent = parent
        entry.children = tuple(
            cls.build(child, title, levels, entry, date=date, site=site)
            for child in node.children
        )
        return entry

    @property
    def ancestors(self) -> tuple[Outline, ...]:
        """Parent, grandparent, ... up to the top of the outline."""
        entry, found = self.parent, []
        while entry is not None:
            found.append(entry)
            entry = entry.parent
        return tuple(found)

    def __iter__(self) -> Iterator[Outline]:
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def __bool__(self) -> bool:
        return True

    @property
    def uri(self) -> str:
        """The address at the outline's date, e.g. "cfr:45/part-164@2024-01-01"."""
        return f"cfr:{self.address}@{self.date}" if self.date else f"cfr:{self.address}"

    def __getitem__(self, key: str | int | Address) -> Outline:
        """A child by position, or the nearest entry by identifier.

        Keys can name their level ("Subpart E", "§ 164.512"), be citations
        ("45 CFR 164.512") or addresses, relative or not ("subpart-E",
        "45/164.512"). Raises NodeNotFoundError if nothing matches and
        AmbiguousNodeError if several entries match at the nearest depth.
        """
        if isinstance(key, int):
            return self.children[key]
        title = int(self.address.split("/")[0])
        if isinstance(key, str):
            key = relative(key, title, in_section=False) or key
        if isinstance(key, Address):
            if key.title != title:
                raise NodeNotFoundError(f"{key} is not in title {title}")
            return walk(self, key, {})
        level, identifier = _lookup(key)
        matches = self._nearest(identifier, level)
        if len(matches) == 1:
            return matches[0]
        if matches:
            found = ", ".join(m.citation for m in matches[:5])
            raise AmbiguousNodeError(
                f"{len(matches)} entries match {key!r} in {self.citation}: {found}",
                matches,
            )
        raise NodeNotFoundError(f"No {key!r} in {self.citation}")

    def get(
        self, key: str | int | Address, default: Outline | None = None
    ) -> Outline | None:
        """Like ``outline[key]``, but return ``default`` when nothing matches."""
        try:
            return self[key]
        except (NodeNotFoundError, IndexError):
            return default

    def iter(self, level: Level | LevelName | None = None) -> Iterator[Outline]:
        """Every entry below this one in document order, optionally one level.

        Level names are case-insensitive; an unknown one raises
        InvalidParameterError.
        """
        # Checked now, not when iteration starts.
        wanted = _outline_level(level) if level is not None else None
        return self._iter(wanted)

    def _iter(self, wanted: Level | None) -> Iterator[Outline]:
        for child in self.children:
            if wanted is None or child.level == wanted:
                yield child
            yield from child._iter(wanted)

    def find(
        self, identifier: str | None = None, *, level: Level | LevelName | None = None
    ) -> Outline | None:
        """The first entry below this one (nearest, then in document order)
        that matches, or None. Unlike ``outline[...]``, it needn't be unique."""
        wanted = _outline_level(level) if level is not None else None
        matches = self._nearest(identifier, wanted)
        return matches[0] if matches else None

    def _nearest(self, identifier: str | None, level: str | None) -> list[Outline]:
        """All matches at the shallowest depth that has any.

        Identifiers match exactly; failing that, ignoring case ("subpart e").
        """
        for same in (str.__eq__, lambda a, b: a.casefold() == b.casefold()):
            layer = list(self.children)
            while layer:
                matches = [
                    e
                    for e in layer
                    if (
                        identifier is None
                        or (e.identifier is not None and same(e.identifier, identifier))
                    )
                    and level in (None, e.level)
                ]
                if matches:
                    return matches
                layer = [c for e in layer for c in e.children]
            if identifier is None:
                break
        return []

    def to_dict(
        self, *, depth: int | None = None, include_raw: bool = False
    ) -> dict[str, Any]:
        """This entry as a dict ready for ``json.dumps``.

        Keys: ``citation``, ``address``, ``uri``, ``level``, ``identifier``,
        ``heading``, ``date`` (ISO format, or None), ``url``, ``reserved``,
        ``size`` and ``children``.

        Parameters
        ----------
        depth: int | None
            Levels of children to include; ``0`` leaves out ``children``.
            Defaults to all of them.
        include_raw: bool
            Include ``raw``: the API's structure node, as JSON-ready data.
        """
        check_depth(depth)
        data: dict[str, Any] = {
            "citation": self.citation,
            "address": self.address,
            "uri": self.uri,
            "level": str(self.level),
            "identifier": self.identifier,
            "heading": self.heading,
            "date": self.date.isoformat() if self.date else None,
            "url": self.url,
            "reserved": self.reserved,
            "size": self.size,
        }
        if include_raw:
            data["raw"] = self.raw.model_dump(mode="json")
        if depth is None or depth > 0:
            below = None if depth is None else depth - 1
            data["children"] = [
                c.to_dict(depth=below, include_raw=include_raw) for c in self.children
            ]
        return data

    def _repr_markdown_(self) -> str:
        """Jupyter shows the table of contents as a nested list, cut short if long."""
        lines = (
            "  " * depth + "- " + MARKDOWN_SPECIAL.sub(r"\\\1", heading)
            for depth, heading in _entries(self, 0)
        )
        return truncate("\n".join(lines) + "\n", REPR_MAX_CHARS, " — use str(outline)")

    def __str__(self) -> str:
        return "\n".join("  " * depth + heading for depth, heading in _entries(self, 0))

    def __repr__(self) -> str:
        return f"<Outline {self.citation} {self.heading!r}>"


def _outline_level(level: Level | str) -> Level:
    wanted = as_level(level)
    if wanted is Level.PARAGRAPH:
        raise InvalidParameterError(
            "Outlines stop at sections and appendices; for paragraphs, get the "
            "text: ecfr_md.get(citation).iter('paragraph')"
        )
    return wanted


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


def _entries(entry: Outline, depth: int) -> Iterator[tuple[int, str]]:
    yield depth, entry.heading
    for child in entry.children:
        yield from _entries(child, depth + 1)


def _level_or_type(kind: str) -> Level | str:
    """A Level for the API's type names ("Section", "appendix"), else the name."""
    name = kind.casefold()
    return Level(name) if name in Level else kind


def _page(title: str | None, date: dt.date, located: Hierarchy, site: str) -> str:
    return web_url(int(title or 0), date, located, site=site)


def _address(title: str | None, located: Mapping[str, str | None]) -> str:
    levels = as_hierarchy({k: v for k, v in located.items() if v})
    return str(Address(int(title or 0), levels))


def _uri(address: str, date: dt.date | None) -> str:
    return f"cfr:{address}@{date.isoformat()}" if date else f"cfr:{address}"


@dataclass(frozen=True)
class TitleInfo:
    """A CFR title, and how current the eCFR's text of it is."""

    number: int
    name: str
    """E.g. "Public Welfare"."""
    latest: dt.date | None
    """The latest date the eCFR has text for; None for reserved titles."""
    amended: dt.date | None
    """When the latest amendment took effect."""
    reserved: bool
    processing: bool
    """The eCFR is applying updates, so the latest text may still change."""
    raw: models.Title = field(repr=False)
    site: str = field(default=SITE_URL, repr=False)
    """The eCFR web site ``url`` links to."""

    @classmethod
    def from_model(cls, title: models.Title, site: str = SITE_URL) -> TitleInfo:
        return cls(
            number=title.number,
            name=title.name,
            latest=title.up_to_date_as_of,
            amended=title.latest_amended_on,
            reserved=title.reserved,
            processing=title.processing_in_progress,
            raw=title,
            site=site,
        )

    @property
    def citation(self) -> str:
        """E.g. "45 CFR"."""
        return f"{self.number} CFR"

    @property
    def address(self) -> str:
        """E.g. "45"."""
        return str(self.number)

    @property
    def uri(self) -> str:
        """The title at its latest date, e.g. "cfr:45@2024-01-01"."""
        return _uri(self.address, self.latest)

    @property
    def url(self) -> str:
        """The title's eCFR page."""
        return web_url(self.number, self.latest, as_hierarchy({}), site=self.site)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """This title as a dict ready for ``json.dumps``.

        Keys: ``citation``, ``address``, ``uri``, ``level`` ("title"),
        ``identifier``, ``heading`` (the name), ``date`` (latest, ISO format),
        ``url``, ``amended``, ``reserved`` and ``processing``; with
        ``include_raw``, also ``raw``.
        """
        data: dict[str, Any] = {
            "citation": self.citation,
            "address": self.address,
            "uri": self.uri,
            "level": "title",
            "identifier": str(self.number),
            "heading": self.name,
            "date": self.latest.isoformat() if self.latest else None,
            "url": self.url,
            "amended": self.amended.isoformat() if self.amended else None,
            "reserved": self.reserved,
            "processing": self.processing,
        }
        if include_raw:
            data["raw"] = self.raw.model_dump(mode="json")
        return data


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
    site: str = field(default=SITE_URL, repr=False)
    """The eCFR web site ``url`` links to."""

    @classmethod
    def from_version(
        cls, version: models.ContentVersion, site: str = SITE_URL
    ) -> Change:
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
            site=site,
        )

    @property
    def level(self) -> Level | str:
        """``Level.SECTION`` or ``Level.APPENDIX``."""
        return _level_or_type(self.raw.type)

    @property
    def address(self) -> str:
        """The section or appendix changed, e.g. "45/164.502"."""
        key = "appendix" if self.level == Level.APPENDIX else "section"
        located = {"part": self.raw.part, key: self.identifier}
        return _address(self.raw.title, located)

    @property
    def uri(self) -> str:
        """The address as of this change, e.g. "cfr:45/164.502@2024-01-01"."""
        return _uri(self.address, self.effective)

    @property
    def url(self) -> str:
        """The eCFR page showing the text as of this change."""
        key = "appendix" if self.level == Level.APPENDIX else "section"
        located = as_hierarchy({"part": self.raw.part, key: self.identifier})
        return _page(self.raw.title, self.effective, located, self.site)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """This change as a dict ready for ``json.dumps``.

        Keys: ``citation``, ``address``, ``uri``, ``level``, ``identifier``,
        ``heading``, ``date`` (when it took effect, ISO format), ``url``,
        ``issued``, ``effective`` and ``kind``; with ``include_raw``, also
        ``raw``, the API's version.
        """
        data: dict[str, Any] = {
            "citation": self.citation,
            "address": self.address,
            "uri": self.uri,
            "level": str(self.level),
            "identifier": self.identifier,
            "heading": self.heading,
            "date": self.effective.isoformat(),
            "url": self.url,
            "issued": self.issued.isoformat(),
            "effective": self.effective.isoformat(),
            "kind": self.kind,
        }
        if include_raw:
            data["raw"] = self.raw.model_dump(mode="json")
        return data


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
    site: str = field(default=SITE_URL, repr=False)
    """The eCFR web site ``url`` links to."""

    @classmethod
    def from_result(cls, result: models.SearchResult, site: str = SITE_URL) -> Match:
        h = result.hierarchy
        return cls(
            citation=f"{h.title} CFR {h.section or h.appendix}",
            heading=plain(result.headings.section or result.headings.appendix or ""),
            excerpt=_whole_words(plain(result.full_text_excerpt or "")),
            starts_on=result.starts_on,
            ends_on=result.ends_on,
            raw=result,
            site=site,
        )

    @property
    def identifier(self) -> str:
        """The section number or appendix name, e.g. "164.514"."""
        h = self.raw.hierarchy
        return h.section or h.appendix or ""

    @property
    def level(self) -> Level:
        """``Level.SECTION`` or ``Level.APPENDIX``."""
        return Level.SECTION if self.raw.hierarchy.section else Level.APPENDIX

    @property
    def address(self) -> str:
        """The section or appendix, e.g. "45/164.514"."""
        h = self.raw.hierarchy
        return _address(h.title, {"part": h.part, self.level.value: self.identifier})

    @property
    def uri(self) -> str:
        """The address as of ``starts_on``, e.g. "cfr:45/164.514@2024-01-01"."""
        return _uri(self.address, self.starts_on)

    @property
    def url(self) -> str:
        """The eCFR page showing the matching text, as of ``starts_on``."""
        h = self.raw.hierarchy
        located = {"part": h.part, self.level.value: self.identifier}
        hierarchy = as_hierarchy({k: v for k, v in located.items() if v})
        return _page(h.title, self.starts_on, hierarchy, self.site)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """This match as a dict ready for ``json.dumps``.

        Keys: ``citation``, ``address``, ``uri``, ``level``, ``identifier``,
        ``heading``, ``date`` (``starts_on``, ISO format), ``url``,
        ``excerpt``, ``starts_on`` and ``ends_on`` (None if current); with
        ``include_raw``, also ``raw``, the API's search result.
        """
        data: dict[str, Any] = {
            "citation": self.citation,
            "address": self.address,
            "uri": self.uri,
            "level": self.level.value,
            "identifier": self.identifier,
            "heading": self.heading,
            "date": self.starts_on.isoformat(),
            "url": self.url,
            "excerpt": self.excerpt,
            "starts_on": self.starts_on.isoformat(),
            "ends_on": self.ends_on.isoformat() if self.ends_on else None,
        }
        if include_raw:
            data["raw"] = self.raw.model_dump(mode="json")
        return data


class Matches(list[Match]):
    """Search matches, best first, one per section.

    ``total`` is how many matching versions the eCFR counted: a section that
    matches in several versions counts several times. ``complete`` is true
    when every match is in the list.
    """

    def __init__(
        self,
        matches: Iterable[Match] = (),
        total: int | None = None,
        *,
        complete: bool = False,
    ):
        super().__init__(matches)
        self.total = total
        self.complete = complete

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: the matches, and whether more exist."""
        return {
            "complete": self.complete,
            "total_versions": self.total,
            "results": [m.to_dict() for m in self],
        }


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
