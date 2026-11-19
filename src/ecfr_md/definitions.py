"""Defined terms: what a word means where it is used.

Definitions sections ("§ 160.103 Definitions.") define terms for a scope they
name in their opening words: "As used in this subpart", "the following
definitions apply to this subchapter". :func:`definitions` finds the
sections that govern a place in the CFR and reads their terms; the nearest
scope wins, so a subpart's definition of a term overrides its part's.

>>> ecfr.define("covered entity", "45 CFR 164.502")
Definition(term='Covered entity', citation='45 CFR 160.103', scope='subchapter', ...)

Terms are read by pattern: an italic term opening a paragraph
("*Covered entity* means ...", "*Business associate:* (1) ...") or a quoted
one ("The term “applicant” means ..."). The text of a definition runs to
the next term. Definitions sections are those headed "Definitions",
"Definition of terms" and the like, in the place's part and in the first
part of its subchapter (or chapter), where definitions for the whole
subchapter usually are. Definitions elsewhere ("as defined in § 1.2") are
not followed.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ecfr_md.document import Level, Node
from ecfr_md.render.inline import plain

if TYPE_CHECKING:
    from ecfr_md.results import Outline

Scope = Literal["title", "chapter", "subchapter", "part", "subpart", "section"]

# Narrowest first: a nearer definition wins.
SCOPES: tuple[Scope, ...] = (
    "section",
    "subpart",
    "part",
    "subchapter",
    "chapter",
    "title",
)
HEADING = re.compile(
    r"\b(?:definitions?|meaning of terms|terms defined|defined terms)\b", re.I
)
SCOPE = re.compile(
    r"\b(?:this|these)\s+(title|chapter|subchapter|part|subpart|section)s?\b", re.I
)
# Leading paragraph markers, bold as ecfr-md writes them: "**(a)(1)** ".
MARKERS = re.compile(r"^(?:\*\*)?(?:\(\*?\w{1,6}\*?\)\s*)+(?:\*\*)?\s*")
# An italic term opening a paragraph: "*Covered entity* means", "*Act.* The".
ITALIC_TERM = re.compile(r"^\*(?!\*)(?P<term>[^*]{1,100}?)[.:—]?\*(?!\*)[.:—]?\s*")
QUOTED_TERM = re.compile(
    r"^(?:(?:The|the)\s+terms?\s+)?[“\"](?P<term>[^”\"]{1,100}?)[,.]?[”\"]"
    r"(?=[\s,]+(?:means|includes|refers|has|is|shall|as used|when used|in))"
)
# Italic paragraph headings in definitions sections that aren't terms.
NOT_TERMS = frozenset(
    (
        "general",
        "scope",
        "applicability",
        "purpose",
        "rules of construction",
        "other terms",
        "in general",
        "example",
        "examples",
    )
)
MAX_SECTIONS = 8  # Definitions sections read for one place.


@dataclass(frozen=True)
class Definition:
    """A defined term, its definition and where the definition is."""

    term: str
    """The term as defined, e.g. "Covered entity"."""
    text: str
    """The definition as Markdown, from the term to the next one."""
    citation: str
    """The definitions section, e.g. "45 CFR 160.103"."""
    address: str
    """The definitions section's address, e.g. "45/160.103"."""
    scope: Scope
    """Where the definition applies, from the section's words ("this subchapter")."""
    url: str
    """The definitions section's eCFR page."""
    date: dt.date | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: ``term``, ``text``, ``citation``, ``address``, ``scope``,
        ``url`` and ``date`` (ISO format, or None)."""
        return {
            "term": self.term,
            "text": self.text,
            "citation": self.citation,
            "address": self.address,
            "scope": self.scope,
            "url": self.url,
            "date": self.date.isoformat() if self.date else None,
        }


def key(term: str) -> str:
    """How terms are compared: case and spacing ignored."""
    return " ".join(term.casefold().split())


def singulars(term: str) -> list[str]:
    """A term and the singulars it may be the plural of: "entities" ->
    "entity", "businesses" -> "business", "plans" -> "plan"."""
    forms = [term]
    if term.endswith("ies"):
        forms.append(term[:-3] + "y")
    if term.endswith("es"):
        forms.append(term[:-2])
    if term.endswith("s"):
        forms.append(term[:-1])
    return forms


def read(section: Node) -> list[Definition]:
    """The terms a definitions section defines, in order, each with the
    section's scope."""
    blocks = [
        b.strip()
        for b in section.to_markdown().split("\n\n")
        if b.strip() and not b.lstrip().startswith(("#", ">"))
    ]
    scope = scope_of(blocks)
    found: list[Definition] = []
    term: str | None = None
    text: list[str] = []

    def finish() -> Iterator[Definition]:
        if term is not None:
            yield Definition(
                term=term,
                text="\n\n".join(text),
                citation=section.citation,
                address=section.address,
                scope=scope,
                url=section.url,
                date=section.date,
            )

    for block in blocks:
        if (named := term_in(block)) is not None:
            found += finish()
            term, text = named, []
        if term is not None:
            text.append(block)
    found += finish()
    return found


def term_in(block: str) -> str | None:
    """The term a paragraph of Markdown defines, if it starts with one."""
    body = MARKERS.sub("", block, count=1)
    match = ITALIC_TERM.match(body) or QUOTED_TERM.match(body)
    if match is None:
        return None
    term = " ".join(plain(match["term"]).split()).strip(" .,:;—")
    if not term or not any(c.isalpha() for c in term) or len(term.split()) > 12:
        return None
    if key(term) in NOT_TERMS or HEADING.fullmatch(term):
        return None  # A paragraph heading: "(a) *Definitions.* For ..."
    return term


def scope_of(blocks: list[str]) -> Scope:
    """The scope a definitions section names before its first term
    ("this subpart"); a part if it names none."""
    for block in blocks:
        if term_in(block) is not None:
            break
        if found := SCOPE.search(plain(block)):
            return found[1].lower()  # type: ignore[return-value]
    return "part"


def sections(entry: Outline) -> list[Outline]:
    """The definitions sections that may govern an outline entry, nearest
    first: in its part (its subpart's first), then in the first part of
    its subchapter or chapter."""
    chain = [entry, *entry.ancestors]
    part = next((e for e in chain if e.level == Level.PART), None)
    group = next(
        (e for e in chain if e.level in (Level.SUBCHAPTER, Level.CHAPTER)), None
    )
    subpart = next((e for e in chain if e.level == Level.SUBPART), None)
    found: list[Outline] = []
    for scope in (subpart, part):
        if scope is not None:
            found += [s for s in _headed(scope) if s not in found]
    if group is not None:
        first = next(
            (p for p in group.iter(Level.PART) if not p.reserved and p is not part),
            None,
        )
        if first is not None and (part is None or _before(first, part)):
            found += _headed(first)
    return found[:MAX_SECTIONS]


def _headed(scope: Outline) -> list[Outline]:
    return [
        s
        for s in scope.iter(Level.SECTION)
        if not s.reserved and HEADING.search(s.heading)
    ]


def _before(first: Outline, part: Outline) -> bool:
    """Whether a part comes before another in its title."""
    top = part.ancestors[-1] if part.ancestors else part
    order = list(top.iter(Level.PART))
    return order.index(first) < order.index(part)


def governs(section: Outline, scope: Scope, entry: Outline) -> bool:
    """Whether a definitions section, with the scope it names, covers an
    outline entry. A scope the section isn't in ("this subchapter" in a
    chapter without subchapters) is the next broader division it is in."""
    levels = SCOPES[SCOPES.index(scope) :]
    chain = (section, *section.ancestors) if scope == "section" else section.ancestors
    container = next((a for a in chain if a.level in levels), None)
    return container is not None and any(
        e is container for e in (entry, *entry.ancestors)
    )
