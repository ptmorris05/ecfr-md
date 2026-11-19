"""What ``outline``, ``changes`` and ``search`` return: plain objects with
citations and clean headings. The API's response is in ``.raw``.

``to_dict()`` turns each into a dict for ``json.dumps``, with the same keys as
:meth:`Node.to_dict <ecfr_md.document.Node.to_dict>`. Use it rather than
``dataclasses.asdict``, which keeps dates and API models as objects.
"""

from __future__ import annotations

import datetime as dt
import functools
import html
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

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
from ecfr_md.errors import (
    AmbiguousNodeError,
    ECFRError,
    InvalidParameterError,
    NodeNotFoundError,
)
from ecfr_md.output import Source, subject_group_url, web_url
from ecfr_md.params import DateLike, Hierarchy, as_hierarchy

if TYPE_CHECKING:
    from ecfr_md.client import ECFR
    from ecfr_md.document import Node

TAG = re.compile(r"<[^>]+>")
CITED = re.compile(r"\d+\s*C\.?\s*F\.?\s*R\b", re.IGNORECASE)
# Levels a citation names, deepest first.
CITED_LEVELS = ("appendix", "section", "subpart", "part", "subchapter", "chapter")
SENTENCE_END = ".;:!?)\"”'"
MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")
# What next and previous step through: the levels that hold text.
LEAVES = frozenset({Level.SECTION, Level.APPENDIX})
CFR_LEVEL = "cfr"  # The level of the outline of the whole CFR.


@dataclass(eq=False)
class Outline:
    """An entry in a table of contents, with its children.

    Iterate over it for its children; index it like a node, by position or
    identifier at any depth: ``outline["Subpart E"]``, ``outline["164.512"]``.
    ``str(outline)`` is the indented table of contents.

    Outlines from a client can look beyond themselves: ``next`` and
    ``previous`` are the neighbouring sections or appendices, across parts,
    chapters and titles; ``up`` and ``siblings`` work at the top of the
    outline too; ``read()`` fetches the text.
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
    # None until a title of the whole-CFR outline is first opened.
    _children: tuple[Outline, ...] | None = field(default=(), repr=False)
    date: dt.date | None = None
    """The point-in-time date of the outline, if known."""
    url: str = field(default="", repr=False)
    """The eCFR web page for this entry."""
    address: str = field(default="", repr=False)
    """The entry's address, e.g. "45/part-164/subpart-E"."""

    def __post_init__(self) -> None:
        # Not a dataclass field, so dataclasses.asdict() doesn't loop forever.
        self._parent: Outline | None = None
        self._client: ECFR | None = None  # Set on the top entry by the client.
        self._load: Callable[[], tuple[Outline, ...]] | None = None

    @property
    def children(self) -> tuple[Outline, ...]:
        """The entries inside this one, in document order."""
        if self._children is None:
            assert self._load is not None  # Set along with _children = None.
            self._children = self._load()
        return self._children

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
        entry._children = tuple(
            cls.build(child, title, levels, entry, date=date, site=site)
            for child in node.children
        )
        return entry

    @classmethod
    def whole(
        cls,
        titles: Iterable[TitleInfo],
        date: dt.date | None,
        load: Callable[[int, dt.date], models.Structure],
        site: str = SITE_URL,
    ) -> Outline:
        """The outline of the whole CFR: one entry per title, each opened
        (its structure loaded with ``load``) the first time it's used.

        ``date`` is the point in time, or None for each title's latest text.
        """
        root = cls(
            level=CFR_LEVEL,
            identifier=None,
            heading="Code of Federal Regulations",
            citation="CFR",
            reserved=False,
            size=None,
            _structure=_lean(
                models.Node(type=CFR_LEVEL, label="Code of Federal Regulations")
            ),
            date=date,
            url=f"{site.rstrip('/')}/{f'on/{date}' if date else 'current'}",
        )
        entries = []
        for info in titles:
            when = info.latest if date is None or info.latest is None else date
            if when and info.latest:
                when = min(when, info.latest)  # No text after the latest date.
            raw = models.Node(
                type="title",
                identifier=str(info.number),
                label=f"Title {info.number}—{info.name}",
                reserved=info.reserved,
            )
            entry = cls(
                level=Level.TITLE,
                identifier=str(info.number),
                heading=raw.label,
                citation=info.citation,
                reserved=info.reserved,
                size=None,
                _structure=_lean(raw),
                date=when,
                url=web_url(info.number, when, as_hierarchy({}), site=site),
                address=info.address,
            )
            entry._parent = root
            if not info.reserved and when is not None:
                entry._children = None
                entry._load = functools.partial(_open_title, entry, load, site)
            entries.append(entry)
        root._children = tuple(entries)
        return root

    @property
    def up(self) -> Outline | None:
        """The entry this one is in, even at the top of this outline (a
        part's chapter, a title's CFR); None for the whole CFR."""
        return self._placed().parent

    @property
    def siblings(self) -> tuple[Outline, ...]:
        """The other entries in the same parent, in document order."""
        me = self._placed()
        return tuple(c for c in me.parent.children if c is not me) if me.parent else ()

    @property
    def next(self) -> Outline | None:
        """The next section or appendix after this entry, in document order,
        crossing into the next part, chapter or title; None at the end.
        Reserved sections are skipped."""
        return self._step(forward=True)

    @property
    def previous(self) -> Outline | None:
        """The section or appendix before this entry, in document order."""
        return self._step(forward=False)

    def read(self, date: DateLike | None = None) -> Node:
        """The text of this entry, as of ``date`` (default: the outline's date)."""
        if self.level == CFR_LEVEL:
            raise InvalidParameterError(
                "The whole CFR is too large to read at once; read a title, "
                'e.g. outline["45"].read()'
            )
        return self._navigator().get(self.address, date=date or self.date)

    def _top(self) -> Outline:
        return self.ancestors[-1] if self.ancestors else self

    def _navigator(self) -> ECFR:
        client = self._top()._client
        if client is None:
            raise ECFRError(
                f"The outline of {self.citation} wasn't loaded by a client, so it "
                "can't fetch text or look beyond itself; use ecfr_md.outline() "
                "or ECFR().outline()"
            )
        return client

    def _placed(self) -> Outline:
        """This entry in the client's outline of the whole CFR, which knows
        what's around it; the entry itself if it's there already or has no
        client."""
        top = self._top()
        if self.parent is not None or top.level == CFR_LEVEL or top._client is None:
            return self
        return top._client._place(self.address, self.date)

    def _step(self, forward: bool) -> Outline | None:
        entry = self
        while True:
            if entry.parent is None:
                placed = entry._placed()
                if placed is entry:
                    return None
                entry = placed
                continue
            siblings = entry.parent.children
            index = next(i for i, s in enumerate(siblings) if s is entry)
            later = siblings[index + 1 :] if forward else siblings[:index][::-1]
            for sibling in later:
                if (leaf := sibling._leaf(forward)) is not None:
                    return leaf
            entry = entry.parent

    def _leaf(self, forward: bool) -> Outline | None:
        """The first (or last) section or appendix in this entry."""
        if self.level in LEAVES:
            return None if self.reserved else self
        for child in self.children if forward else self.children[::-1]:
            if (leaf := child._leaf(forward)) is not None:
                return leaf
        return None

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
        if self.level == CFR_LEVEL:
            return self._in_title(Address.parse(key))
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

    def _in_title(self, address: Address) -> Outline:
        """In the whole CFR, the entry an address names."""
        wanted = str(address.title)
        title = next((t for t in self.children if t.address == wanted), None)
        if title is None:
            raise NodeNotFoundError(f"Title {address.title} does not exist")
        if address.level == "title":
            return title
        return walk(title, address.undated(), {})

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
    """Headings to print; titles of the whole CFR not yet opened stay shut."""
    yield depth, entry.heading
    for child in entry._children or ():
        yield from _entries(child, depth + 1)


def _open_title(
    entry: Outline, load: Callable[[int, dt.date], models.Structure], site: str
) -> tuple[Outline, ...]:
    """The entries of a title in the whole-CFR outline, from its structure."""
    assert entry.date is not None  # Titles with text have a date.
    title = int(entry.address)
    structure = load(title, entry.date)
    return tuple(
        Outline.build(child, title, {}, entry, date=entry.date, site=site)
        for child in structure.children
    )


def _lean(node: models.Node) -> models.Structure:
    return models.Structure.of(node)


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
