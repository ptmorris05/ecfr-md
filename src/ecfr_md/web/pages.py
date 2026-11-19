"""What the web navigator's pages show, independent of how they're served.

Each page is built from the SDK (outlines, nodes, changes, search) into
plain data that the HTML templates, the Markdown variants and the JSON
variants all read, so the three never disagree.

Paths mirror ecfr.gov: an eCFR link becomes a local one by dropping the
host, and back.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from ecfr_md.address import Address
from ecfr_md.document import Level
from ecfr_md.errors import DateOutOfRangeError, NotFoundError
from ecfr_md.results import CFR_LEVEL, Outline

if TYPE_CHECKING:
    from ecfr_md.client import ECFR

# A part up to this size (bytes of XML, per the outline) is shown whole on
# its page; a larger one shows its contents, and its text on request.
READ_PART_MAX = 1_500_000
# Levels whose page is a table of contents only: their text can be a whole title.
CONTENTS_ONLY = frozenset({"title", "subtitle", "chapter", "subchapter", CFR_LEVEL})
VARIANTS = (".md", ".json")
SECTION_HEADING = re.compile(r"^(§§?\s*\S+?)\.?\s+(.*)$", re.S)
SHORT = {
    "title": "Title",
    "subtitle": "Subtitle",
    "chapter": "Chapter",
    "subchapter": "Subchapter",
    "part": "Part",
    "subpart": "Subpart",
}


def split_variant(path: str) -> tuple[str, str]:
    """A path and the variant it asks for: ``("…/section-164.502", ".json")``,
    or ``""`` for the HTML page."""
    for suffix in VARIANTS:
        if path.endswith(suffix):
            return path.removesuffix(suffix), suffix
    return path, ""


def local(url: str) -> str:
    """The path of an eCFR page on this server: the URL without its host."""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%")
    return path + (f"#{parts.fragment}" if parts.fragment else "")


def href(address: Address | str, date: dt.date | None = None) -> str:
    """The local page of an address, at a date (None for current text)."""
    place = Address.parse(address) if isinstance(address, str) else address
    return local(replace(place, date=date).url())


def parse_page(path: str) -> Address:
    """The place a page path names: ``/current/title-45/part-164``."""
    return Address.parse(path if path.startswith("/") else f"/{path}")


@dataclass(frozen=True)
class Crumb:
    """A step of the breadcrumb: a short label and the full heading."""

    label: str
    heading: str
    href: str
    citation: str
    level: str

    def to_dict(self) -> dict[str, str]:
        return {
            "label": self.label,
            "heading": self.heading,
            "href": self.href,
            "citation": self.citation,
            "level": self.level,
        }


@dataclass(frozen=True)
class Link:
    """A neighbouring page: previous, next, up."""

    href: str
    citation: str
    heading: str

    def to_dict(self) -> dict[str, str]:
        return {"href": self.href, "citation": self.citation, "heading": self.heading}


@dataclass
class Place:
    """A page's place in the CFR and the date it shows."""

    entry: Outline
    """The entry in the outline of the whole CFR."""
    date: dt.date | None
    """The date asked for; None for the current text."""
    text_date: dt.date | None
    """The date of the text shown: the date asked for, or the latest."""
    _counts: dict[int, tuple[int, int]] = field(default_factory=dict, repr=False)

    @property
    def level(self) -> str:
        return str(self.entry.level)

    @property
    def address(self) -> Address:
        return Address.parse(self.entry.address)

    @property
    def citation(self) -> str:
        return self.entry.citation

    @property
    def heading(self) -> str:
        return self.entry.heading

    @property
    def href(self) -> str:
        return href(self.address, self.date)

    @property
    def rest(self) -> str:
        """The page path without its date: "title-45/section-164.502"."""
        path = self.href.split("#")[0]
        if path.startswith("/on/"):
            return path.split("/", 3)[3]
        return path.removeprefix("/current/")

    @property
    def official(self) -> str:
        """The same page on ecfr.gov."""
        return replace(self.address, date=self.date).url()

    @property
    def title(self) -> str:
        """The page title: the citation and heading."""
        heading = self.heading
        short = SECTION_HEADING.match(heading)
        return f"{self.citation} {short[2] if short else '— ' + heading}"

    @property
    def contents_only(self) -> bool:
        """Whether the page shows contents rather than text."""
        if self.level in CONTENTS_ONLY:
            return True
        return self.level == "part" and (self.entry.size or 0) > READ_PART_MAX

    def crumbs(self) -> list[Crumb]:
        """The breadcrumb, from the title down to this page."""
        chain = [*reversed(self.entry.ancestors), self.entry]
        return [
            Crumb(
                label(e),
                e.heading,
                href(e.address, self.date),
                e.citation,
                str(e.level),
            )
            for e in chain
            if e.level != CFR_LEVEL and e.identifier is not None
        ]

    def link(self, entry: Outline | None) -> Link | None:
        if entry is None or entry.level == CFR_LEVEL:
            return None
        return Link(href(entry.address, self.date), entry.citation, entry.heading)

    def neighbours(self) -> dict[str, Link | None]:
        """Previous, next and up: sections step through the text in order;
        divisions step to their siblings."""
        entry = self.entry
        if entry.level in (Level.SECTION, Level.APPENDIX):
            before, after = entry.previous, entry.next
        else:
            siblings = entry.parent.children if entry.parent else (entry,)
            same = [s for s in siblings if s.level == entry.level and not s.reserved]
            at = next((i for i, s in enumerate(same) if s is entry), None)
            before = same[at - 1] if at else None
            after = same[at + 1] if at is not None and at + 1 < len(same) else None
        return {
            "previous": self.link(before),
            "next": self.link(after),
            "up": self.link(entry.parent),
        }

    def counts(self, entry: Outline) -> tuple[int, int]:
        """Sections and appendices in an entry, and the bytes of their text
        (from the parts' sizes, where the outline gives them)."""
        known = self._counts.get(id(entry))
        if known is not None:
            return known
        if entry.level in (Level.SECTION, Level.APPENDIX):
            found = (0 if entry.reserved else 1, 0)
        else:
            sections = size = 0
            for child in entry.children:
                n, s = self.counts(child)
                sections += n
                size += s
            found = (sections, entry.size or size)
        self._counts[id(entry)] = found
        return found

    def contents(self, entry: Outline | None = None) -> list[Row]:
        """The entries inside an entry (default: this page's), with counts."""
        rows = []
        for child in (entry or self.entry).children:
            sections, size = self.counts(child)
            rows.append(
                Row(
                    href=href(child.address, self.date) if child.identifier else "",
                    citation=child.citation,
                    heading=child.heading,
                    level=str(child.level),
                    reserved=child.reserved,
                    sections=sections,
                    size=size,
                    leaf=child.level in (Level.SECTION, Level.APPENDIX)
                    or not child.children,
                    address=child.address,
                )
            )
        return rows

    def to_dict(self) -> dict[str, Any]:
        neighbours = self.neighbours()
        return {
            "citation": self.citation,
            "address": str(self.address),
            "level": self.level,
            "heading": self.heading,
            "date": self.text_date.isoformat() if self.text_date else None,
            "current": self.date is None,
            "href": self.href,
            "url": self.official,
            "breadcrumb": [c.to_dict() for c in self.crumbs()],
            **{k: v.to_dict() if v else None for k, v in neighbours.items()},
        }


@dataclass(frozen=True)
class Row:
    """An entry in a table of contents."""

    href: str
    citation: str
    heading: str
    level: str
    reserved: bool
    sections: int
    size: int
    leaf: bool
    address: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "address": self.address,
            "heading": self.heading,
            "level": self.level,
            "href": self.href,
            "reserved": self.reserved,
            "sections": self.sections,
            "size": self.size,
        }


def label(entry: Outline) -> str:
    """A short name for a breadcrumb: "Title 45", "Part 164", "§ 164.502"."""
    level = str(entry.level)
    identifier = entry.identifier or ""
    if level == "section":
        return f"§ {identifier}"
    if level == "appendix":
        return identifier.split(" to ")[0]
    if level in SHORT:
        return f"{SHORT[level]} {identifier}"
    return entry.heading


def place(ecfr: ECFR, address: Address, date: dt.date | None) -> Place:
    """The outline entry for a page, at a date (None for current text)."""
    try:
        entry = ecfr._place(str(address.undated()), date)
        entry.children  # noqa: B018 (a title's outline is fetched here)
    except NotFoundError as exc:  # The eCFR has no outline for that date.
        assert date is not None  # The current outline always exists.
        reason = ecfr._explain_missing(address.to_citation(), date)
        raise DateOutOfRangeError(
            reason or f"The eCFR has no text of title {address.title} on {date}"
        ) from exc
    return Place(entry, date, entry.date)


def resolve(text: str) -> Address:
    """Any citation, address or link, as :meth:`Address.parse` reads it."""
    return Address.parse(text)


def go(text: str) -> tuple[Address, str]:
    """Where ``/go?cite=`` sends a citation: its address and local page."""
    address = resolve(text)
    fragment = ""
    if address.paragraph:
        section = address.hierarchy.get("section", "")
        fragment = f"#p-{section}" + "".join(f"({m})" for m in address.markers)
    page = replace(address, paragraph=())
    return address, href(page, page.date) + fragment


def size(n: int) -> str:
    """A byte count for people: "12 KB", "3.4 MB"."""
    if n < 1000:
        return f"{n} B"
    value = n / 1000
    for unit in ("KB", "MB"):
        if value < 1000:
            return _scaled(value, unit)
        value /= 1000
    return _scaled(value, "GB")


def _scaled(value: float, unit: str) -> str:
    return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"


def split_heading(text: str) -> tuple[str, str]:
    """A section heading's number and words: ("§ 164.502", "Uses and ...")."""
    found = SECTION_HEADING.match(text.strip())
    return (found[1], found[2]) if found else ("", text)


def citations(address: Address, date: dt.date | None) -> dict[str, str]:
    """A place's citation in OFR and Bluebook style."""
    year = (date or dt.date.today()).year
    return {
        "ofr": address.citation("ofr"),
        "bluebook": address.citation("bluebook", year),
    }


def index_entries(entry: Outline) -> Iterator[dict[str, str]]:
    """Every entry below one, down to sections, for the command palette."""
    for child in entry.iter():
        if child.identifier is None or child.level == Level.SUBJECT_GROUP:
            continue
        yield {
            "c": child.citation,
            "h": child.heading,
            "u": href(child.address),
            "l": str(child.level),
        }
