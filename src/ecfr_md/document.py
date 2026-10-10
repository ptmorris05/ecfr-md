"""Navigable CFR documents.

``ECFR.get`` returns a :class:`Node`: a :class:`Title`, :class:`Part`,
:class:`Section`, :class:`Paragraph` and so on. Nodes wrap the XML of a single
API response; navigating them makes no further requests. Children are built
on first access.

>>> part = ecfr.get("45 CFR Part 164")
>>> part["E"]                      # a subpart
>>> part["164.502(a)(1)"]          # any descendant, down to paragraphs
>>> [s.citation for s in part.iter("section")]
>>> part.save("hipaa.md")

An identifier that matches several nodes raises
:class:`~ecfr_md.errors.AmbiguousNodeError`, which lists them.
"""

from __future__ import annotations

import datetime as dt
import re
import string
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from enum import StrEnum
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import ClassVar

from ecfr_md import markdown
from ecfr_md.citation import KEYWORDS, PARAGRAPH, Citation
from ecfr_md.errors import (
    AmbiguousNodeError,
    InvalidCitationError,
    NodeNotFoundError,
    ResponseFormatError,
)
from ecfr_md.output import (
    Source,
    file_name,
    page_path,
    provenance,
    render_front_matter,
    save_events,
    subject_group_citation,
    subject_group_url,
    write_text,
)
from ecfr_md.params import Hierarchy, as_hierarchy


class Level(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CHAPTER = "chapter"
    SUBCHAPTER = "subchapter"
    PART = "part"
    SUBPART = "subpart"
    SUBJECT_GROUP = "subject_group"
    SECTION = "section"
    APPENDIX = "appendix"
    PARAGRAPH = "paragraph"


# Levels that are also API hierarchy parameters, broadest first.
HIERARCHY_ORDER = ("subtitle", "chapter", "subchapter", "part", "subpart", "section")
HIERARCHY_LEVELS = frozenset(Hierarchy.__annotations__)
# Section metadata that belongs to no paragraph.
SECTION_NOTES = frozenset({"HEAD", "CITA", "SECAUTH", "APPRO", "PARAUTH", "EDNOTE"})
LEADING_MARKERS = re.compile(r"((?:\(\*?\w+\*?\)\s*)+)(.*)", re.S)
MARKER = re.compile(r"\((\*?)(\w+)\*?\)")  # "(a)" or italic "(*1*)".
# OFR style runs a first child into its parent, after a dash or an italic
# heading: "(k) *Standard*—(1) *Military*—(i) ..." or "(a) *Standard.* (1) An ..."
RUN_IN_CHILD = re.compile(r"(?:—|(?<=\*) )(?=\(\*?(?:1|i|A)\*?\) )")
# "(b)-(d) [Reserved]" stands for (b), (c) and (d).
MARKER_RANGE = re.compile(
    r"((?:\(\w+\))+)\s*(?:-|\u2013|through)\s*((?:\(\w+\))+)\s*(.*)", re.S
)
ITALIC_HEADING = re.compile(r"\*(?!\*)(.+?)\*(?!\*)")
# Worked examples restart paragraph numbering ("Example 1. (i) Facts ...").
# A label ("Example 1.", "*Example.*", "Examples:"), not a sentence such as
# "Examples of records include:", which introduces real paragraphs.
EXAMPLE_START = re.compile(r"[*\s]*(?:Example|Illustration)s?(?:\s+\d+)?\s*[.:*\u2014]")


ROMAN_VALUES = {"m": 1000, "d": 500, "c": 100, "l": 50, "x": 10, "v": 5, "i": 1}
ROMAN_SYMBOLS = (
    ("m", 1000), ("cm", 900), ("d", 500), ("cd", 400), ("c", 100), ("xc", 90),
    ("l", 50), ("xl", 40), ("x", 10), ("ix", 9), ("v", 5), ("iv", 4), ("i", 1),
)  # fmt: skip
ITALIC_LEVELS = (5, 6)
LOOKAHEAD = 200  # Markers read ahead to tell (i) the letter from (i) the numeral.


def _letters(marker: str, alphabet: str) -> int | None:
    """Position in a, ..., z, aa, ..., zz, aaa, ... (no upper limit)."""
    if marker and set(marker) <= set(alphabet) and len(set(marker)) == 1:
        return (len(marker) - 1) * len(alphabet) + alphabet.index(marker[0]) + 1
    return None


def _number(marker: str) -> int | None:
    return int(marker) if marker.isdigit() and int(marker) > 0 else None


def _roman(marker: str) -> int | None:
    """Value of any canonical lowercase roman numeral, e.g. 'xliv' -> 44."""
    if not marker or not set(marker) <= ROMAN_VALUES.keys():
        return None
    values = [ROMAN_VALUES[c] for c in marker]
    total = sum(
        -v if v < nxt else v for v, nxt in zip(values, [*values[1:], 0], strict=True)
    )
    canonical, rest = "", total
    for symbol, value in ROMAN_SYMBOLS:
        count, rest = divmod(rest, value)
        canonical += symbol * count
    return total if canonical == marker else None  # Rejects e.g. "iiii", "vx".


# Position of a marker within each OFR paragraph level: (a), (1), (i), (A),
# then italic (1) and (i). None means the marker can't be at that level.
POSITIONS: dict[int, Callable[[str], int | None]] = {
    1: lambda m: _letters(m, string.ascii_lowercase),
    2: _number,
    3: _roman,
    4: lambda m: _letters(m, string.ascii_uppercase),
    5: _number,
    6: _roman,
}


class Node(ABC):
    """A piece of the CFR: iterable over its children, indexable by identifier."""

    level: ClassVar[Level]

    def __init__(self, identifier: str, parent: Node | None, source: Source) -> None:
        self.identifier = identifier
        self.parent = parent
        self.source = source

    @property
    @abstractmethod
    def heading(self) -> str:
        """The node's heading as plain text, e.g. "§ 164.502 Uses and ..."."""

    @property
    @abstractmethod
    def children(self) -> tuple[Node, ...]:
        """Child nodes in document order."""

    @abstractmethod
    def to_markdown(self) -> str:
        """This node and everything inside it as one Markdown document."""

    def to_markdown_tree(self) -> dict[PurePosixPath, str]:
        """Markdown files keyed by relative path, one per section or appendix."""
        return {PurePosixPath(f"{self.slug}.md"): self.to_markdown()}

    def save(
        self, path: str | Path, *, tree: bool = False, front_matter: bool = True
    ) -> list[Path]:
        """Write Markdown to ``path`` and return the files written.

        By default this writes one file; an existing directory receives a file
        named after the citation, e.g. ``45-cfr-164.502.md``. With
        ``tree=True``, ``path`` is a directory that gets one folder per
        hierarchy level and one file per section or appendix. Each
        file starts with YAML front matter (see :attr:`provenance`) unless
        ``front_matter`` is false. Files are replaced atomically.
        """
        target = Path(path)
        if target.is_dir() or str(path).endswith(("/", "\\")):
            target = target / file_name(self.citation)
        header = render_front_matter(self.provenance) if front_matter else ""
        return [write_text(target, header + self.to_markdown())]

    @property
    def text(self) -> str:
        """This node's Markdown: a paragraph's own text without its
        subparagraphs; for other nodes, everything inside them."""
        return self.to_markdown()

    @property
    def section(self) -> Node | None:
        """The section this node is in, or itself if it is one."""
        chain = (self, *self.ancestors)
        return next((n for n in chain if n.level is Level.SECTION), None)

    @property
    def provenance(self) -> dict[str, str]:
        """Citation, date, eCFR page, API request, retrieval time and generator."""
        return provenance(
            self.source, self.hierarchy, heading=self.heading, page=self._page
        )

    @property
    @abstractmethod
    def _page(self) -> str | None:
        """The eCFR page path the API gave for this node, if any."""

    @property
    @abstractmethod
    def slug(self) -> str:
        """A file or folder name for the node, e.g. "section-164.502"."""

    @property
    def date(self) -> dt.date | None:
        """Point-in-time date of the text."""
        return self.source.date

    @property
    def cfr_title(self) -> int:
        return self.source.title

    @property
    def ancestors(self) -> tuple[Node, ...]:
        """Parent, grandparent, ... up to the root of the response."""
        node, found = self.parent, []
        while node is not None:
            found.append(node)
            node = node.parent
        return tuple(found)

    @property
    def hierarchy(self) -> Hierarchy:
        """API hierarchy parameters that locate this node."""
        levels = dict(self.source.hierarchy)
        for node in (*reversed(self.ancestors), self):
            if node.level in HIERARCHY_LEVELS:
                levels[node.level.value] = node.identifier
        return as_hierarchy(levels)

    @property
    def citation(self) -> str:
        """Standard citation, e.g. ``45 CFR 164.502(a)(1)``."""
        return str(Citation(self.cfr_title, self.hierarchy))

    def __iter__(self) -> Iterator[Node]:
        return iter(self.children)

    def __len__(self) -> int:
        return len(self.children)

    def __bool__(self) -> bool:
        return True  # A node without children is still truthy.

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str | int):
            return False
        try:
            self[key]
        except (NodeNotFoundError, IndexError):
            return False
        except AmbiguousNodeError:
            pass
        return True

    def __getitem__(self, key: str | int) -> Node:
        """A child by index, or the nearest descendant by identifier.

        Keys can be identifiers ("E", "164.502"), name their level
        ("Subpart E", "§ 164.502"), be full citations ("45 CFR Part 164
        Subpart E", "164.502(a)(1)") or paragraph markers (``section["(a)(1)"]``).
        Raises NodeNotFoundError if nothing matches and AmbiguousNodeError if
        several nodes match at the nearest depth.
        """
        if isinstance(key, int):
            return self.children[key]
        if (cited := self._as_citation(key)) is not None:
            try:
                return self._walk(cited)
            except NodeNotFoundError:
                # "1" reads as "Part 1" but may be a paragraph or other identifier.
                if not _split_markers(key)[0] or self._nearest(key.strip(), None) == []:
                    raise
        base, markers = _split_markers(key)
        node = self._resolve(base) if base else self
        for marker in markers:
            matches = [
                c
                for c in node.children
                if c.level is Level.PARAGRAPH and c.identifier == marker
            ]
            node = _single(matches, f"({marker})", node)
        return node

    def get(self, key: str | int, default: Node | None = None) -> Node | None:
        """Like ``node[key]``, but return ``default`` when nothing matches.

        Ambiguous keys still raise AmbiguousNodeError.
        """
        try:
            return self[key]
        except (NodeNotFoundError, IndexError):
            return default

    def iter(self, level: Level | str | None = None) -> Iterator[Node]:
        """Every descendant in document order, optionally only one level.

        ``part.iter("section")`` yields all sections at any depth.
        """
        wanted = Level(level) if level else None
        for child in self.children:
            if wanted is None or child.level is wanted:
                yield child
            if _descend(child, wanted):
                yield from child.iter(wanted)

    def find(
        self, identifier: str | None = None, *, level: Level | str | None = None
    ) -> Node | None:
        """The first descendant (nearest, then in document order) that matches.

        Unlike ``node[...]``, this doesn't check that the match is unique.
        """
        matches = self._nearest(identifier, Level(level) if level else None)
        return matches[0] if matches else None

    def _nearest(self, identifier: str | None, wanted: Level | None) -> list[Node]:
        """All matches at the shallowest depth that has any.

        Identifiers match exactly; failing that, ignoring case ("subpart e").
        """
        for same in (str.__eq__, lambda a, b: a.casefold() == b.casefold()):
            layer = list(self.children)
            while layer:
                matches = [
                    n
                    for n in layer
                    if (identifier is None or same(n.identifier, identifier))
                    and (wanted is None or n.level is wanted)
                ]
                if matches:
                    return matches
                layer = [c for n in layer if _descend(n, wanted) for c in n.children]
            if identifier is None:
                break
        return []

    def _walk(self, cited: Citation) -> Node:
        """Step down a citation's levels from this node."""
        node: Node = self
        for level in (*HIERARCHY_ORDER, "appendix"):
            value = cited.hierarchy.get(level)
            if value is not None and not node._within(level, value):
                node = node._resolve_level(level, value)
        return node[_markers(cited.paragraph)] if cited.paragraph else node

    def _as_citation(self, key: str) -> Citation | None:
        """Read a multi-word key as a citation in this node's title, if it is one."""
        text = key.strip()
        # "45 CFR ..." and "Title 45 ..." name a title; a bare "164" is a part.
        if not re.match(r"\d+\s*C\.?\s*F\.?\s*R\b|title\b", text, re.I):
            text = f"{self.cfr_title} CFR {text}"
        try:
            cited = Citation.parse(text)
        except InvalidCitationError:
            return None
        if cited.title != self.cfr_title:
            raise NodeNotFoundError(f"{key!r} is not in title {self.cfr_title}")
        return cited

    def _within(self, level: str, value: str) -> bool:
        """Whether this node is already inside the given level."""
        if self.source.hierarchy.get(level) == value:
            return True
        return any(
            n.level == level and n.identifier == value for n in (self, *self.ancestors)
        )

    def _resolve_level(self, level: str, value: str) -> Node:
        return _single(self._nearest(value, Level(level)), f"{level} {value}", self)

    def _resolve(self, key: str) -> Node:
        words = key.replace("§", " ").split()
        level = KEYWORDS.get(words[0].lower().rstrip(".")) if len(words) > 1 else None
        if level and level != "appendix":  # Appendix names start with "Appendix".
            words = words[1:]
        identifier = " ".join(words)
        wanted = Level(level) if level else None
        return _single(self._nearest(identifier, wanted), identifier, self)

    def __str__(self) -> str:
        return self.to_markdown()

    def __repr__(self) -> str:
        heading = f" {self.heading!r}" if self.heading else ""
        return f"<{type(self).__name__} {self.citation}{heading}>"


class Division(Node):
    """A structural level backed by an XML ``DIV`` element."""

    def __init__(
        self, element: ET.Element, parent: Node | None, source: Source
    ) -> None:
        super().__init__(element.get("N", ""), parent, source)
        self.element = element

    @property
    def heading(self) -> str:
        return markdown.plain_text(self.element.find("HEAD"))

    @property
    def children(self) -> tuple[Node, ...]:
        return self._divisions

    @cached_property
    def _divisions(self) -> tuple[Node, ...]:
        return tuple(
            _division(child, self, self.source)
            for child in self.element
            if markdown.is_division(child)
        )

    @property
    def slug(self) -> str:
        return markdown.slug(self.element)

    @property
    def _page(self) -> str | None:
        return page_path(self.element)

    def to_markdown(self) -> str:
        return markdown.render(self.element)

    def to_markdown_tree(self) -> dict[PurePosixPath, str]:
        files: dict[PurePosixPath, str] = {}
        markdown.write_tree(
            markdown.element_events(self.element),
            lambda path, body, *_: files.__setitem__(path, body),
        )
        return files

    def save(
        self, path: str | Path, *, tree: bool = False, front_matter: bool = True
    ) -> list[Path]:
        if not tree:
            return super().save(path, front_matter=front_matter)
        base = self.parent.hierarchy if self.parent else self.source.hierarchy
        return save_events(
            markdown.element_events(self.element),
            self.source,
            Path(path),
            tree=True,
            front_matter=front_matter,
            base=base,
        )


class Title(Division):
    level = Level.TITLE


class Subtitle(Division):
    level = Level.SUBTITLE


class Chapter(Division):
    level = Level.CHAPTER


class Subchapter(Division):
    level = Level.SUBCHAPTER


class Part(Division):
    level = Level.PART


class Subpart(Division):
    level = Level.SUBPART


class SubjectGroup(Division):
    """An unnumbered group of sections, cited like the eCFR: "<subpart> - <heading>"."""

    level = Level.SUBJECT_GROUP

    @property
    def citation(self) -> str:
        return subject_group_citation(super().citation, self.heading)

    @property
    def provenance(self) -> dict[str, str]:
        fields = super().provenance
        fields["citation"] = self.citation
        if self._page is None:
            fields["source"] = subject_group_url(
                self.source, self.hierarchy, self.identifier
            )
        return fields


class Appendix(Division):
    level = Level.APPENDIX


class Section(Division):
    level = Level.SECTION

    @cached_property
    def _parsed(self) -> tuple[tuple[Paragraph, ...], tuple[str, ...]]:
        roots = _paragraphs(self)
        return tuple(roots), tuple(_issues(self, roots))

    @property
    def children(self) -> tuple[Node, ...]:
        """Top-level paragraphs: (a), (b), ..."""
        return self._parsed[0]

    @property
    def paragraph_issues(self) -> tuple[str, ...]:
        """Irregularities found while rebuilding the paragraph tree.

        Empty when every paragraph sequence runs (a), (b), (c)... without gaps
        or repeats. Otherwise lookups in this section deserve a second look;
        :meth:`to_markdown` is faithful either way.
        """
        return self._parsed[1]


class Paragraph(Node):
    """A numbered paragraph: (a), (1), (i), (A), then italic (1) and (i)."""

    level = Level.PARAGRAPH

    def __init__(self, marker: str, depth: int, parent: Node) -> None:
        super().__init__(marker, parent, parent.source)
        self.depth = depth
        self.blocks: list[str] = []
        self._children: list[Paragraph] = []

    @property
    def children(self) -> tuple[Node, ...]:
        return tuple(self._children)

    @property
    def label(self) -> str:
        return f"({self.identifier})"

    @property
    def path(self) -> tuple[str, ...]:
        """Markers from the section down, e.g. ``('a', '1', 'i')``."""
        chain = (*reversed(self.ancestors), self)
        return tuple(n.identifier for n in chain if n.level is Level.PARAGRAPH)

    @property
    def heading(self) -> str:
        """The italic run-in heading, e.g. "Standard: Minimum necessary"."""
        if not self.blocks:
            return ""
        _, _, rest = self.blocks[0].partition("** ")
        match = ITALIC_HEADING.match(rest)
        return match[1].rstrip(" .") if match else ""

    @property
    def text(self) -> str:
        """This paragraph's own Markdown, without its subparagraphs."""
        return "\n\n".join(self.blocks)

    @property
    def citation(self) -> str:
        return str(Citation(self.cfr_title, self.hierarchy, self.path))

    @property
    def provenance(self) -> dict[str, str]:
        return provenance(
            self.source,
            self.hierarchy,
            heading=self.heading,
            paragraph=self.path,
            page=self._page,
        )

    @property
    def _page(self) -> str | None:
        return self.section._page if self.section else None

    @property
    def slug(self) -> str:
        section = self.section.identifier if self.section else ""
        return markdown.make_slug("paragraph", "-".join([section, *self.path]))

    def to_markdown(self) -> str:
        parts = [*self.blocks, *(c.to_markdown().rstrip("\n") for c in self.children)]
        return "\n\n".join(p for p in parts if p) + "\n"


XML_TYPES: dict[str, type[Division]] = {
    "TITLE": Title,
    "SUBTITLE": Subtitle,
    "CHAPTER": Chapter,
    "SUBCHAP": Subchapter,
    "PART": Part,
    "SUBPART": Subpart,
    "SUBJGRP": SubjectGroup,
    "SECTION": Section,
    "APPENDIX": Appendix,
}


def load(
    xml: bytes | str,
    *,
    title: int,
    date: dt.date | None = None,
    hierarchy: Hierarchy | None = None,
    url: str | None = None,
    retrieved: dt.datetime | None = None,
) -> Division:
    """Build a navigable document from eCFR XML (e.g. ``versioner.full``).

    ``url`` and ``retrieved`` record where and when the XML was fetched; they
    appear in saved files' front matter.
    """
    divisions = markdown.outer_divisions(markdown.parse(xml))
    if not divisions:
        raise ResponseFormatError("The eCFR XML contains no CFR content")
    source = Source(title, date, hierarchy or as_hierarchy({}), url, retrieved)
    return _division(divisions[0], None, source)


def _division(element: ET.Element, parent: Node | None, source: Source) -> Division:
    # Unknown division types are treated as groupings of their children.
    cls = XML_TYPES.get(element.get("TYPE", ""), SubjectGroup)
    return cls(element, parent, source)


def _descend(node: Node, wanted: Level | None) -> bool:
    """Whether a search for ``wanted`` should look inside ``node``.

    Paragraphs are parsed on demand, so only search them when asked to.
    """
    return node.level is not Level.SECTION or wanted in (None, Level.PARAGRAPH)


def _single(matches: list[Node], key: str, scope: Node) -> Node:
    if not matches:
        raise NodeNotFoundError(f"No {key!r} in {scope.citation}")
    if len(matches) > 1:
        found = ", ".join(m.citation for m in matches[:5])
        more = f" and {len(matches) - 5} more" if len(matches) > 5 else ""
        raise AmbiguousNodeError(
            f"{len(matches)} nodes match {key!r} in {scope.citation}: {found}{more}",
            matches,
        )
    return matches[0]


def _markers(paragraph: tuple[str, ...]) -> str:
    return "".join(f"({m})" for m in paragraph)


def _split_markers(key: str) -> tuple[str, tuple[str, ...]]:
    """'164.502(a)(1)' -> ('164.502', ('a', '1')); keeps Title 26's '1.401(k)-1'."""
    match = re.fullmatch(r"(.*?)((?:\(\w+\))*)", key.strip())
    assert match is not None  # The pattern matches any string.
    return match[1].strip(), tuple(PARAGRAPH.findall(match[2]))


def _paragraphs(section: Section) -> list[Paragraph]:
    """Rebuild paragraph nesting from the flat paragraph elements of a section.

    Worked examples ("Example 1. (i) Facts...") restart their own numbering,
    so their text is attached to the paragraph they illustrate instead of
    being parsed into paragraphs of the section.
    """
    roots: list[Paragraph] = []
    open_: list[Paragraph] = []  # Path from a root to the latest paragraph.
    example: list[Paragraph] | None = None  # Owner of the current example, if any.

    def attach(blocks: list[str]) -> None:
        """Add text to the open paragraph, or to the example's owner."""
        if example is not None:
            target = example[0] if example else None
        else:
            target = open_[-1] if open_ else None
        if target is not None:
            target.blocks += [b for b in blocks if b]

    # Each element with its text and markers, so markers can be read ahead.
    entries = []
    for element in section.element:
        if element.tag in SECTION_NOTES:
            continue
        inline = ""  # Only text-like elements can start examples or paragraphs.
        if markdown.is_paragraph(element) or markdown.is_subheading(element):
            inline = markdown.render_inline(element)
        markers = []
        if markdown.is_numbered(element):
            for segment in _expand_ranges(inline):
                markers += [m for _, m in _leading(segment)]
        entries.append((element, inline, markers))

    for index, (element, inline, _) in enumerate(entries):
        numbered = markdown.is_numbered(element)
        later = [m for _, _, markers in entries[index + 1 :] for m in markers]
        if EXAMPLE_START.match(inline):
            example = [open_[-1]] if open_ else []
        if example is not None and numbered:
            match = LEADING_MARKERS.fullmatch(inline)
            markers = MARKER.findall(match[1]) if match else []
            if markers and _leaves_example(markers[0][1], open_, example):
                example = None
        if example is not None or not numbered:
            attach(markdown.render_blocks(element, level=2))
            continue
        segments = _expand_ranges(inline)
        for n, segment in enumerate(segments):
            match = LEADING_MARKERS.fullmatch(segment)
            markers = _leading(segment)
            if not markers:
                attach([markdown.escape_block_start(segment)])
                continue
            rest = [
                m
                for later_segment in segments[n + 1 :]
                for _, m in _leading(later_segment)
            ]
            if reentered := _reentry(markers, open_):
                # "(g)(1)(iii)" continues the open (g)(1); keep its last child
                # open so (iii) follows (ii).
                del open_[reentered + 1 :]
            for k, (italic, marker) in enumerate(markers):
                if k < reentered:
                    continue
                upcoming = [m for _, m in markers[k + 1 :]] + rest + later
                depth = _depth(marker, bool(italic), open_, upcoming[:LOOKAHEAD])
                while open_ and open_[-1].depth >= depth:
                    open_.pop()
                parent = open_[-1] if open_ else section
                paragraph = Paragraph(marker, depth, parent)
                (open_[-1]._children if open_ else roots).append(paragraph)
                open_.append(paragraph)
            assert match is not None  # _leading found markers, so it matched.
            label = match[1].strip().replace("*", "")
            open_[-1].blocks.append(f"**{label}** {match[2]}".rstrip())
    return roots


def _leaves_example(
    marker: str, open_: list[Paragraph], example: list[Paragraph]
) -> bool:
    """Whether a marker resumes the section's numbering after an example.

    It does if it continues the numbering of the paragraph the example belongs
    to, or of one of that paragraph's ancestors: after examples under (c)(2),
    "(3)" or "(d)" resume the section, while "(ii)" belongs to the example.
    """
    if not example:  # Examples before any paragraph end at "(a)".
        return POSITIONS[1](marker) == 1
    for paragraph in open_:  # The owner is the last open paragraph.
        position = POSITIONS[paragraph.depth]
        current = position(paragraph.identifier)
        if current and position(marker) == current + 1:
            return True
    return False


def _issues(section: Section, roots: list[Paragraph]) -> list[str]:
    """Describe sibling paragraphs that repeat or skip in their sequence."""
    issues = []
    for parent, children in [
        (section, roots),
        *((p, p._children) for p in _walk(roots)),
    ]:
        where = parent.citation
        seen: set[str] = set()
        expected = 1
        for child in children:
            if child.identifier in seen:
                issues.append(f"{where}: ({child.identifier}) appears more than once")
            seen.add(child.identifier)
            position = POSITIONS[child.depth](child.identifier)
            if position != expected:
                issues.append(
                    f"{where}: ({child.identifier}) is out of sequence "
                    f"(expected position {expected})"
                )
            expected = (position or expected) + 1
    return issues


def _walk(paragraphs: list[Paragraph]) -> Iterator[Paragraph]:
    for paragraph in paragraphs:
        yield paragraph
        yield from _walk(paragraph._children)


def _expand_ranges(text: str) -> list[str]:
    """Split run-in children apart and expand reserved ranges.

    "(b)-(d) [Reserved]" stands for (b), (c) and (d). Ranges can cross
    levels: "(a)-(g)(1)(ii)" is (a) to (f), then (g), (g)(1), (g)(1)(i) and
    (g)(1)(ii); "(g)(2)-(o)" is (g)(2), then (h) to (o).
    """
    segments = []
    for segment in RUN_IN_CHILD.split(text):
        match = MARKER_RANGE.fullmatch(segment)
        start = PARAGRAPH.findall(match[1]) if match else []
        end = PARAGRAPH.findall(match[2]) if match else []
        paths = _range_paths(start, end) if match else None
        if match is None or paths is None:
            segments.append(segment)
            continue
        rest = match[3]
        segments += ["".join(f"({m})" for m in path) + f" {rest}" for path in paths]
    return segments


def _range_paths(start: list[str], end: list[str]) -> list[list[str]] | None:
    """Marker paths covering a range.

    The first markers' kind sets the level: "(b)-(d)" letters, "(3)-(5)"
    numbers, "(ii)-(iv)" roman numerals, "(A)-(C)" capitals. Ranges such as
    "(i)-(v)" that fit both letters and numerals are read as numerals.
    Returns None if the markers don't fit the levels.
    """
    fits = [
        d
        for d in (3, 1, 2, 4)  # Numerals first: "(i)-(v)" is rarely i to v.
        if (lo := POSITIONS[d](start[0]))
        and (hi := POSITIONS[d](end[0]))
        and (lo <= hi or len(start) > 1)
        # "(c)-(d)" are letters, not the numerals 100 to 500.
        and (d != 3 or set(start[0] + end[0]) <= set("ivx"))
    ]
    if not fits:
        return None
    shared = 0
    while shared < min(len(start), len(end)) - 1 and start[shared] == end[shared]:
        shared += 1
    level = fits[0] + shared
    if level not in POSITIONS:
        return None
    first, last = POSITIONS[level](start[shared]), POSITIONS[level](end[shared])
    if first is None or last is None or last < first:
        return None
    paths = [start]  # Starts with any levels it shares with open paragraphs.
    paths += [[_marker_at(level, n)] for n in range(first + 1, last + 1)]
    for depth, marker in enumerate(end[shared + 1 :], start=level + 1):
        final = POSITIONS[depth](marker) if depth in POSITIONS else None
        if final is None:
            return None
        paths += [[_marker_at(depth, n)] for n in range(1, final + 1)]
    return paths


def _marker_at(depth: int, position: int) -> str:
    """The marker at a position of a level: (1, 3) -> "c", (3, 4) -> "iv"."""
    if depth in (2, 5):
        return str(position)
    if depth in (3, 6):
        numeral, rest = "", position
        for symbol, value in ROMAN_SYMBOLS:
            count, rest = divmod(rest, value)
            numeral += symbol * count
        return numeral
    alphabet = string.ascii_lowercase if depth == 1 else string.ascii_uppercase
    repeat, index = divmod(position - 1, len(alphabet))
    return alphabet[index] * (repeat + 1)


def _reentry(markers: list[tuple[str, str]], open_: list[Paragraph]) -> int:
    """How many leading markers name paragraphs already open, from the top.

    A line can restate its ancestors: after (g)(1)(ii), "(g)(1)(iii)" means
    (iii) in the same (g)(1), not a second (g). The last marker is always new.
    """
    count = 0
    for (_, marker), paragraph in zip(markers[:-1], open_, strict=False):
        if paragraph.identifier != marker or paragraph.depth != count + 1:
            break
        count += 1
    return count


def _leading(segment: str) -> list[tuple[str, str]]:
    """Leading paragraph markers of a segment as (italic, marker), if valid."""
    match = LEADING_MARKERS.fullmatch(segment)
    markers = MARKER.findall(match[1]) if match else []
    return markers if all(_is_marker(m) for _, m in markers) else []


def _is_marker(text: str) -> bool:
    """Whether text is a paragraph marker at some level, e.g. not "Reserved"."""
    return any(position(text) for position in POSITIONS.values())


def _depth(
    marker: str,
    italic: bool,
    open_: list[Paragraph],
    upcoming: Sequence[str] = (),
) -> int:
    """Nesting depth of a marker, from the paragraphs currently open.

    Markers are ambiguous: (i) is a letter after (h) but a roman numeral under
    (1), and sources sometimes drop the italics of levels 5 and 6. In order,
    prefer a level the marker continues (h -> i) or starts directly below the
    current paragraph, then a deeper level it starts, then one it skips ahead in.
    When several levels fit, the next markers decide: (i) followed by (ii) is a
    roman numeral, followed by (j) a letter.
    """
    positions = {d: p for d, find in POSITIONS.items() if (p := find(marker))}
    deepest = open_[-1].depth if open_ else 0
    candidates = list(positions)
    if italic:
        candidates = [d for d in candidates if d in ITALIC_LEVELS] or candidates
    elif deepest < ITALIC_LEVELS[0] - 1:
        # Plain markers only reach the italic levels from directly under (A).
        candidates = [d for d in candidates if d not in ITALIC_LEVELS] or candidates
    previous = {p.depth: POSITIONS[p.depth](p.identifier) or 0 for p in open_}

    def continues(d: int) -> bool:
        return d in previous and positions[d] == previous[d] + 1

    def starts(d: int) -> bool:
        return positions[d] == 1 and d > deepest

    if fits := [
        d for d in candidates if continues(d) or (starts(d) and d == deepest + 1)
    ]:
        for later in upcoming if len(fits) > 1 else ():
            for d in fits:
                if POSITIONS[d](later) == positions[d] + 1:
                    return d
        return max(fits)
    if deeper := [d for d in candidates if starts(d)]:
        return min(deeper)
    if gaps := [d for d in candidates if d in previous and positions[d] > previous[d]]:
        return max(gaps)
    return candidates[0]
