"""Defined terms: where the CFR defines a word, and what the definition says.

Regulations define their terms in definitions sections ("§ 160.103
Definitions") that say what they apply to ("the following definitions apply
to this subchapter"). :func:`define` finds the definitions that govern a
place in the CFR: the place's own text first, then the definitions sections
of each division it's in, nearest first, keeping those that apply to it.
:func:`find_definitions` reads the definitions in any text.

Definitions are recognised by their form, an italic term followed by
"means", "includes", "has the meaning" and the like, so unusual wording can
be missed.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ecfr_md.address import Address
from ecfr_md.document import Level, Node
from ecfr_md.errors import InvalidParameterError
from ecfr_md.passages import TextIndex
from ecfr_md.results import LEAVES, Matches, Outline

if TYPE_CHECKING:
    from ecfr_md.client import ECFR
    from ecfr_md.params import DateLike

DEFINITIONS_HEADING = re.compile(
    r"\b(?:definitions?|meaning of terms|terms defined|defined terms|glossary)\b",
    re.IGNORECASE,
)
MARKERS = r"(?:\*\*(?:\(\*?[A-Za-z0-9]{1,6}\*?\))+\*\*\s*)"
TERM = r"\*[^*\n]{1,150}?\*"
DEFINITION = re.compile(
    rf"(?P<markers>{MARKERS}*)"
    rf"(?P<terms>{TERM}(?:\s*(?:,\s*(?:or\s+|and\s+)?|or\s+|and\s+){TERM})*)"
    r"\s*(?P<verb>means\b|mean\b|has the\b|have the\b|includes?\b|is\b|are\b|exists\b"
    r"|refers? to\b|shall mean\b|stands for\b|:|—)?"
)
ONE_TERM = re.compile(TERM)
MARKER = re.compile(r"\(\*?([A-Za-z0-9]{1,6})\*?\)")
APPLIES = re.compile(
    r"\b(?:in|to|of|under|for)\s+this\s+"
    r"(subchapter|subpart|part|chapter|subtitle|title|section)\b",
    re.IGNORECASE,
)
ROMAN = frozenset("ivxl")
# Definitions sections read in divisions above a part, at most, nearest first.
MAX_ABOVE_PART = 6


@dataclass(frozen=True)
class Definition:
    """A term's definition: its text, pinpoint citation and what it applies to."""

    term: str
    """The term as the text writes it, e.g. "Covered entity"."""
    terms: tuple[str, ...]
    """Every term the definition defines ("Billing cycle", "cycle")."""
    text: str
    """The definition, as Markdown."""
    citation: str
    """Where it is: its paragraph, e.g. "12 CFR 1026.2(a)(4)", or for
    definitions without paragraph labels, the section ("45 CFR 160.103")."""
    address: str
    url: str
    date: dt.date | None
    section: str
    """The definitions section, e.g. "45 CFR 160.103"."""
    applies_to: str | None
    """The citation of what the definitions section says it applies to,
    e.g. "45 CFR Chapter A Subchapter C"; None if it doesn't say."""

    def cite(self) -> str:
        """E.g. '45 CFR 160.103 (definition of "covered entity")'."""
        if self.citation != self.section:
            return self.citation
        return f'{self.citation} (definition of "{self.term}")'

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "terms": list(self.terms),
            "citation": self.citation,
            "cite": self.cite(),
            "address": self.address,
            "url": self.url,
            "date": self.date.isoformat() if self.date else None,
            "section": self.section,
            "applies_to": self.applies_to,
            "text": self.text,
        }


class Definitions(list[Definition]):
    """The definitions of a term that govern a place, nearest first.

    ``searched`` lists the sections read; ``suggestions`` are terms they
    define that are like the one asked for; ``mentions`` are sections the
    eCFR finds with the term and "means" when no definition was found.
    """

    def __init__(
        self,
        definitions: Iterable[Definition] = (),
        *,
        term: str = "",
        scope: str = "",
        searched: list[str] | None = None,
        suggestions: list[str] | None = None,
        mentions: Matches | None = None,
    ) -> None:
        super().__init__(definitions)
        self.term = term
        self.scope = scope
        self.searched = searched or []
        self.suggestions = suggestions or []
        self.mentions = mentions if mentions is not None else Matches()

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "scope": self.scope,
            "definitions": [d.to_dict() for d in self],
            "searched": self.searched,
            "suggestions": self.suggestions,
            "mentions": [m.to_dict() for m in self.mentions],
        }


@dataclass
class _Found:
    terms: tuple[str, ...]
    start: int
    end: int
    labelled: bool = field(default=False)


def find_definitions(node: Node, applies_to: str | None = None) -> list[Definition]:
    """Every definition in a node's text, in order."""
    index = TextIndex(node)
    text = index.markdown
    found = list(_definitions(text))
    result = []
    for item in found:
        body = text[item.start : item.end].strip()
        where = node
        if item.labelled:
            starting = index.within(item.start, item.start + 4)
            where = starting[-1] if starting else index.at(item.start + 4)
        section = where.section or node
        result.append(
            Definition(
                term=item.terms[0],
                terms=item.terms,
                text=body + "\n",
                citation=where.citation,
                address=where.address,
                url=where.url,
                date=where.date,
                section=section.citation,
                applies_to=applies_to,
            )
        )
    return result


def _blocks(text: str) -> Iterator[tuple[int, int]]:
    start = 0
    for found in re.finditer(r"\n\n+", text):
        if found.start() > start:
            yield start, found.start()
        start = found.end()
    if start < len(text):
        yield start, len(text)


def _definitions(text: str) -> Iterator[_Found]:
    current: _Found | None = None
    level = 0
    for start, end in _blocks(text):
        block = text[start:end]
        found = _start(block)
        if current is not None:
            same = found is not None and _same(found.terms, current.terms)
            ends = (
                block.startswith(("#", ">"))
                or (found is not None and not same)
                or (level and _level(block, level) not in (None, *range(level + 1, 9)))
            )
            if not ends:
                current.end = end
                continue
            yield current
            current = None
        if found is not None:
            current = _Found(found.terms, start, end, found.labelled)
            level = _level(block, 0) or 0
    if current is not None:
        yield current


def _start(block: str) -> _Found | None:
    """The terms a block defines, if it starts a definition."""
    found = DEFINITION.match(block)
    if found is None:
        return None
    terms = [t.strip("*").strip() for t in ONE_TERM.findall(found["terms"])]
    if not found["verb"] and not terms[-1].endswith(":"):
        return None
    cleaned = tuple(t.rstrip(":.,; ").strip() for t in terms)
    if not all(cleaned):
        return None
    return _Found(cleaned, 0, 0, bool(found["markers"]))


def _level(block: str, within: int) -> int | None:
    """How deep a block's first paragraph label is: (a) 1, (1) 2, (i) 3,
    (A) 4, italic 5; None without one. ``within`` is the depth of the
    definition being read, which decides whether "(i)" is a letter."""
    found = re.match(r"\*\*(\(\*?[A-Za-z0-9]{1,6}\*?\))", block) or re.match(
        r"(\(\*[A-Za-z0-9]{1,6}\*\))", block
    )
    if found is None:
        return None
    label = found[1]
    marker = MARKER.match(label)
    assert marker is not None
    value = marker[1]
    if "*" in label:
        return 5
    if value.isdigit():
        return 2
    if value.isupper():
        return 4
    if set(value) <= ROMAN and (len(value) > 1 or within >= 2):
        return 3
    return 1


def _same(terms: tuple[str, ...], other: tuple[str, ...]) -> bool:
    keys = {_key(t) for t in other}
    return any(_key(t) in keys for t in terms)


def _key(term: str) -> str:
    return " ".join(term.casefold().replace("\u2019", "'").strip("\"' ").split())


def _forms(term: str) -> set[str]:
    """The term and its likely singulars: "covered entities" -> "covered entity"."""
    key = _key(term)
    forms = {key}
    if key.endswith("ies"):
        forms.add(key[:-3] + "y")
    if key.endswith("es"):
        forms.add(key[:-2])
    if key.endswith("s"):
        forms.add(key[:-1])
    return forms


def matches(term: str, definition: Definition) -> bool:
    """Whether a definition defines ``term`` (ignoring case and plurals)."""
    wanted = _forms(term)
    return any(_key(t) in wanted for t in definition.terms)


def define(
    ecfr: ECFR, term: str, citation: str | int, date: DateLike | None = None
) -> Definitions:
    """See :meth:`ECFR.define <ecfr_md.client.ECFR.define>`."""
    if not term.strip(" \"'"):
        raise InvalidParameterError('Give a term to define, e.g. "covered entity"')
    term = term.strip(" \"'")
    placed, when = _placed(ecfr, citation, date)
    chain = [placed, *placed.ancestors]
    searched: list[str] = []
    seen: set[str] = set()
    every: list[Definition] = []
    found: list[Definition] = []
    for scope in chain:
        if scope.level in (Level.TITLE, Level.SUBTITLE, "cfr"):
            break
        for candidate in _candidates(scope, placed, seen):
            seen.add(candidate.address)
            node = candidate.read(when)
            searched.append(node.citation)
            applies = _applies_to(node, candidate)
            definitions = find_definitions(node, applies.citation if applies else None)
            every += definitions
            for definition in definitions:
                if matches(term, definition) and (
                    applies is None
                    or _within(placed, applies)
                    or _within(candidate, placed)
                ):
                    found.append(definition)
        # Above a part, only the nearest subchapter or chapter: wider
        # divisions hold too many definitions sections to read.
        if found or scope.level in (Level.SUBCHAPTER, Level.CHAPTER):
            break
    suggestions: list[str] = []
    mentions = Matches()
    if not found:
        known = {_key(t): t for d in every for t in d.terms}
        close = difflib.get_close_matches(_key(term), known, n=5, cutoff=0.75)
        suggestions = [known[k] for k in close]
        top = chain[-1] if chain[-1].level != "cfr" else chain[0]
        within = next((s for s in chain if s.level == Level.TITLE), top)
        mentions = ecfr.search(f'"{term}" means', limit=5, within=within.citation)
    return Definitions(
        found,
        term=term,
        scope=placed.citation,
        searched=searched,
        suggestions=suggestions,
        mentions=mentions,
    )


def definitions(
    ecfr: ECFR, citation: str | int, date: DateLike | None = None
) -> list[Definition]:
    """See :meth:`ECFR.definitions <ecfr_md.client.ECFR.definitions>`."""
    placed, when = _placed(ecfr, citation, date)
    seen: set[str] = set()
    nearest: dict[str, Definition] = {}  # A nearer definition hides farther ones.
    for scope in [placed, *placed.ancestors]:
        if scope.level in (Level.TITLE, Level.SUBTITLE, "cfr"):
            break
        for candidate in _candidates(scope, placed, seen):
            seen.add(candidate.address)
            if not DEFINITIONS_HEADING.search(candidate.heading):
                continue  # The place itself: its italic run-in headings aren't terms.
            node = candidate.read(when)
            applies = _applies_to(node, candidate)
            if applies is not None and not (
                _within(placed, applies) or _within(candidate, placed)
            ):
                continue
            for found in find_definitions(node, applies.citation if applies else None):
                for term in found.terms:
                    nearest.setdefault(_key(term), found)
        if scope.level in (Level.SUBCHAPTER, Level.CHAPTER):
            break
    return list(dict.fromkeys(nearest.values()))


def _placed(
    ecfr: ECFR, citation: str | int, date: DateLike | None
) -> tuple[Outline, dt.date]:
    """A place's entry in the whole CFR's outline, and the date of its text."""
    place = Address.parse(citation)
    when = date if date is not None else place.date
    entry = ecfr.outline(str(place.undated()), date=when)
    assert entry.date is not None  # Outlines from a client are dated.
    return ecfr._place(entry.address, entry.date), entry.date


def _candidates(scope: Outline, place: Outline, seen: set[str]) -> list[Outline]:
    """The definitions sections in a division to read, nearest first."""
    if scope is place and scope.level in LEAVES:
        return [] if scope.address in seen else [scope]
    sections = [
        e
        for e in scope.iter()
        if e.level in LEAVES
        and not e.reserved
        and DEFINITIONS_HEADING.search(e.heading)
        and e.address not in seen
    ]
    if scope.level in (Level.SUBCHAPTER, Level.CHAPTER):
        return sections[:MAX_ABOVE_PART]  # General definitions come first.
    return sections


def _applies_to(node: Node, entry: Outline) -> Outline | None:
    """What a definitions section says it applies to: "the following
    definitions apply to this subchapter"."""
    intro = node.text[:600]
    found = APPLIES.search(intro)
    if found is None:  # Saying nothing, a section's definitions cover its part.
        return next((a for a in entry.ancestors if a.level == Level.PART), None)
    level = found[1].lower()
    if level == "section":
        return entry
    return next((a for a in entry.ancestors if a.level == level), None)


def _within(entry: Outline, scope: Outline) -> bool:
    return entry is scope or any(a is scope for a in entry.ancestors)
