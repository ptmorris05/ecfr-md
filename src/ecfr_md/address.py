"""CFR addresses: one short, shell-safe name for every place in the CFR.

An address is ``title ["/" path] ["@" date]``:

- ``45/164.502/a/1`` is paragraph (a)(1) of 45 CFR 164.502. Sections need no
  part: a section number is unique in its title. Parentheses inside a section
  number become dots, so 26 CFR 1.401(k)-1 is ``26/1.401.k-1``.
- ``45/part-164/subpart-E``, ``45/subtitle-A/subchapter-C`` and
  ``45/chapter-I`` are divisions above sections.
- ``12/part-1026/supplement-I`` and ``40/part-60/subpart-VVVV/table-1`` are
  appendices (and supplements, tables, figures, ...); ``12/part-9/group-ECFR…``
  is an unnumbered group of sections, by its eCFR id.
- ``@2020-01-01`` (or ``@2020``, ``@2020-01``, ``@current``) is the date.
- ``45/164.501/1~2`` is the second of the paragraphs (1) that repeat side by
  side, as in definitions; the first is ``1~1``, and ``1`` alone is ambiguous.
- ``45/164.502,164.504`` and ``45/164.502/a/1,2`` list places; ``../b..d``
  is a range of paragraphs.

Addresses use only letters, digits and ``. - / @ , ~``, so they work in a
shell, a URL and a file name alike. :meth:`Address.parse` reads them and every
other common way of naming a place in the CFR: citations, eCFR, LII and
GovInfo URLs, USLM identifiers, ``cfr:`` URIs and the files ecfr-md saves.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
import string
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from urllib.parse import SplitResult, parse_qs, quote, unquote, urlsplit

from ecfr_md.citation import UPPER_LEVELS, Citation
from ecfr_md.config import BASE_URL, SITE_URL
from ecfr_md.errors import AmbiguousNodeError, InvalidCitationError, NodeNotFoundError
from ecfr_md.params import Hierarchy, as_hierarchy

STRUCTURE = ("subtitle", "chapter", "subchapter", "part", "subpart")
SEGMENT_LEVELS = {
    "subtitle": "subtitle",
    "chapter": "chapter",
    "subchapter": "subchapter",
    "part": "part",
    "subpart": "subpart",
    "group": "subject_group",
    "subject-group": "subject_group",
    "section": "section",
}
# The first word of an appendix-level name, and the word its address uses.
APPENDIX_KINDS = {
    "appendix": "appendix",
    "app": "appendix",
    "appendixes": "appendixes",
    "appendices": "appendixes",
    "supplement": "supplement",
    "supp": "supplement",
    "table": "table",
    "tables": "tables",
    "figure": "figure",
    "fig": "figure",
    "figures": "figures",
    "schedule": "schedule",
    "exhibit": "exhibit",
    "list": "list",
    "subject": "subject",
    "annex": "annex",
    "attachment": "attachment",
}
APPENDIX_NAME = re.compile(
    r"(?P<kind>[A-Za-z]+):?"
    r"(?:\s+(?P<des>(?!to\b|of\b)[^\s,]+(?:\s+(?:and|through)\s+[^\s,]+)?))?"
    r"(?:,?\s+(?:to|of)\s+(?P<target>.*))?",
    re.IGNORECASE,
)
APPENDIX_KEY = re.compile(rf"(?:{'|'.join(set(APPENDIX_KINDS.values()))})(?:-\S+)?")
TO_SUBPART = re.compile(r"\bto\s+Subparts?\s+(\S+)(?:\s+(?:of|to)\s+Part\b|$)", re.I)
TO_PART = re.compile(r"\bto\s+Part\b", re.I)
DATE = re.compile(r"@(\d{4}(?:-\d{2}(?:-\d{2})?)?|current)?$", re.IGNORECASE)
TITLE_SEGMENT = re.compile(r"(?:title-)?(\d+)", re.IGNORECASE)
SECTION_SEGMENT = re.compile(r"\d[\w-]*\.[\w.-]*")
PART_SEGMENT = re.compile(r"\d+[A-Za-z]?(?:-\d+[A-Za-z]?)*")
MARKER_SEGMENT = re.compile(r"[A-Za-z0-9]+(?:~\d+)?(?:\.\.[A-Za-z0-9]+)?")
TREE_SUFFIX = re.compile(r"~\d+$")
KEYWORD_SEGMENT = re.compile(
    r"(?P<kw>subject-group|subchapter|subtitle|chapter|subpart|section|group|part)"
    r"-(?P<id>.+)",
    re.IGNORECASE,
)
# What starts an address rather than a citation: "45/", "45@", "45," or "45".
ADDRESS_START = re.compile(r"(?:title-)?\d+(?:/|@|,|$)|title-\d+\b", re.IGNORECASE)
CFR = re.compile(r"(?<![A-Za-z])C\.?\s*F\.?\s*R(?![A-Za-z])", re.IGNORECASE)
# What starts an address without its title: "subpart-E", "164.502/a".
RELATIVE_START = re.compile(
    rf"(?:{'|'.join(SEGMENT_LEVELS)}|{'|'.join(APPENDIX_KINDS)})-|[^\s/]+/",
    re.IGNORECASE,
)
URL_START = re.compile(
    r"(?:https?://|www\.|(?:www\.)?ecfr\.gov|(?:www\.)?law\.cornell\.edu|"
    r"(?:www\.|api\.)?govinfo\.gov|/current/|/on/\d)",
    re.IGNORECASE,
)
GRANULE = re.compile(
    r"CFR-(?P<year>\d{4})-title(?P<title>\d+)-vol\d+"
    r"(?:-(?P<kind>sec|part)(?P<id>[\w-]+?)(?:-subpart(?P<subpart>\w+))?"
    r"(?:-app(?P<app>[A-Za-z0-9]+)(?:-id\d+)?)?)?(?:\.\w+)?$",
    re.IGNORECASE,
)
LEGACY_FILE = re.compile(r"(\d+)-cfr(?:-(.+))?", re.IGNORECASE)
QUOTES = "\"'\u201c\u201d\u2018\u2019"
# A Bluebook citation's year is that title's annual edition.
EDITION_YEAR = re.compile(r"\s*\(\s*((?:19|20)\d\d)\s*\)$")
SPACED_MARKER = re.compile(r"[a-z]{1,3}|[A-Z]{1,3}|\d{1,3}|[ivxlc]{1,6}")
NOT_MARKERS = frozenset({"and", "or", "to", "of", "et", "seq", "thru", "as", "in"})
CITED_SECTION = re.compile(r"\d[\w-]*\.[\w.()-]*")
DOTTED = re.compile(r"(?P<section>\d[\w-]*\.\w+)(?P<markers>(?:\.\w+)+)(?P<rest>.*)")
USLM_PREFIXES = {
    "st": "subtitle",
    "ch": "chapter",
    "sch": "subchapter",
    "pt": "part",
    "p": "part",
    "spt": "subpart",
    "sg": "subject_group",
    "s": "section",
}
LII_LEVELS = frozenset({"chapter", "subchapter", "part", "subpart"})
SUBTITLE = re.compile(r"[A-HJKN-UWYZ]")  # Single capitals that aren't numerals.
# Annual editions: titles 1-16 are revised as of January 1, 17-27 April 1,
# 28-41 July 1 and 42-50 October 1.
EDITIONS = ((16, 1), (27, 4), (41, 7))
BLUEBOOK = {
    "subtitle": "subtit.",
    "chapter": "ch.",
    "subchapter": "subch.",
    "part": "pt.",
    "subpart": "subpt.",
}
EXAMPLES = "e.g. 45 CFR 164.502, 45/164.502/a/1 or 45/part-164/subpart-E"

ROMAN_VALUES = {"m": 1000, "d": 500, "c": 100, "l": 50, "x": 10, "v": 5, "i": 1}
ROMAN_SYMBOLS = (
    ("m", 1000), ("cm", 900), ("d", 500), ("cd", 400), ("c", 100), ("xc", 90),
    ("l", 50), ("xl", 40), ("x", 10), ("ix", 9), ("v", 5), ("iv", 4), ("i", 1),
)  # fmt: skip


def letters(marker: str, alphabet: str) -> int | None:
    """Position in a, ..., z, aa, ..., zz, aaa, ... (no upper limit)."""
    if marker and set(marker) <= set(alphabet) and len(set(marker)) == 1:
        return (len(marker) - 1) * len(alphabet) + alphabet.index(marker[0]) + 1
    return None


def number(marker: str) -> int | None:
    return int(marker) if marker.isdigit() and int(marker) > 0 else None


def roman(marker: str) -> int | None:
    """Value of any canonical lowercase roman numeral, e.g. 'xliv' -> 44."""
    if not marker or not set(marker) <= ROMAN_VALUES.keys():
        return None
    values = [ROMAN_VALUES[c] for c in marker]
    total = sum(
        -v if v < nxt else v for v, nxt in zip(values, [*values[1:], 0], strict=True)
    )
    return total if roman_numeral(total) == marker else None  # Rejects e.g. "iiii".


def roman_numeral(value: int) -> str:
    numeral, rest = "", value
    for symbol, size in ROMAN_SYMBOLS:
        count, rest = divmod(rest, size)
        numeral += symbol * count
    return numeral


# Position of a marker within each OFR paragraph level: (a), (1), (i), (A),
# then italic (1) and (i). None means the marker can't be at that level.
POSITIONS: dict[int, Callable[[str], int | None]] = {
    1: lambda m: letters(m, string.ascii_lowercase),
    2: number,
    3: roman,
    4: lambda m: letters(m, string.ascii_uppercase),
    5: number,
    6: roman,
}


def marker_at(depth: int, position: int) -> str:
    """The marker at a position of a level: (1, 3) -> "c", (3, 4) -> "iv"."""
    if depth in (2, 5):
        return str(position)
    if depth in (3, 6):
        return roman_numeral(position)
    alphabet = string.ascii_lowercase if depth == 1 else string.ascii_uppercase
    repeat, index = divmod(position - 1, len(alphabet))
    return alphabet[index] * (repeat + 1)


def encode_section(section: str) -> str:
    """A section number as an address segment: "1.401(k)-1" -> "1.401.k-1".

    The eCFR names a few reserved sections by a range, "210.3-07 - 210.3-08";
    that becomes "210.3-07..210.3-08".
    """
    section = re.sub(r"(?<=\.)\s+", "", " ".join(section.split()))  # "1222. 27"
    ends = re.split(r"\s+(?:-|\u2013|\u2014|through)\s+|\s*[\u2013\u2014]\s*", section)
    if len(ends) > 1:
        return "..".join(encode_section(end) for end in ends)
    section = re.sub(r"\((\w+)\)", r".\1", section)
    return re.sub(r"[^A-Za-z0-9.-]+", "-", section).strip("-")


def decode_section(segment: str) -> str:
    """The section number an address segment names: "1.401.k-1" -> "1.401(k)-1".

    The first dot separates part and section. Later dots before a hyphen were
    parentheses ("1.142.a.5-1"), as were later dots before a letter
    ("48.4061.a"); otherwise they are the number's own (41 CFR "105.60.001").
    Lookups compare numbers as addresses write them, so either reading finds
    the section.
    """
    if ".." in segment:
        return " - ".join(decode_section(end) for end in segment.split(".."))
    head, dot, rest = segment.partition(".")
    body, hyphen, tail = rest.partition("-")
    num, *markers = body.split(".")
    if not dot or not markers or not (hyphen or not markers[0].isdigit()):
        return segment
    return f"{head}.{num}" + "".join(f"({m})" for m in markers) + hyphen + tail


def encode_id(identifier: str) -> str:
    """A division's identifier as used in addresses and folder names.

    Bracketed notes are dropped ("IX [RESERVED]" is "IX"), ranges use ".."
    ("50-1 - 50-200" is "50-1..50-200", "E through G" is "E..G") and anything
    else outside letters, digits, "." and "-" becomes "-".
    """
    text = re.sub(r"\s*\[[^\]]*\]", "", identifier)
    text = re.sub(r"\s+(?:-|\u2013|\u2014|through|thru)\s+", "..", text.strip())
    return re.sub(r"[^A-Za-z0-9.-]+", "-", text).strip("-") or "-"


def appendix_key(name: str) -> str:
    """The address of an appendix-level name within its part or subpart.

    "Appendix A to Part 1026" is "appendix-A", "Supplement I to Part 1026"
    "supplement-I", "Table 1 to Subpart A of Part 63" "table-1". Names that
    don't follow the pattern are kept whole: "appendix-<name>". Keys are
    returned unchanged.
    """
    name = " ".join(name.split())
    if APPENDIX_KEY.fullmatch(name):
        return name
    match = APPENDIX_NAME.fullmatch(name)
    if match and (kind := APPENDIX_KINDS.get(match["kind"].lower())):
        return kind if match["des"] is None else f"{kind}-{_designator(match['des'])}"
    if name.lower().startswith("appendix:"):
        return appendix_key(name[len("appendix:") :])
    return f"appendix-{_designator(name)}"


def _designator(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\s+(?:through|-|\u2013)\s+", "..", text)
    return re.sub(r"[^A-Za-z0-9.]+", "-", text.replace("(", "").replace(")", "")).strip(
        "-."
    )


def appendix_name(key: str, part: str | None, subpart: str | None) -> str:
    """The full name an appendix key most likely stands for.

    "appendix-A" in part 1026 is "Appendix A to Part 1026": the eCFR's own
    name for most appendices. Lookups compare keys, so this is only for
    display and for the eCFR's web addresses.
    """
    kind, _, designator = key.partition("-")
    words = [kind.capitalize(), designator] if designator else [kind.capitalize()]
    if part:
        words += [
            "to",
            f"Subpart {subpart} of Part {part}" if subpart else f"Part {part}",
        ]
    return " ".join(words)


def appendix_subpart(name: str, subpart: str | None = None) -> str | None:
    """The subpart an appendix belongs to: the one its name gives, else the
    one it sits in (``subpart``), unless its name says it is "to Part" N."""
    if found := TO_SUBPART.search(name):
        return found[1]
    if TO_PART.search(name):
        return None
    return subpart


def same_identifier(level: str, actual: str | None, wanted: str, fold: bool) -> bool:
    """Whether an identifier, as the eCFR gives it, is the one an address names."""
    if actual is None:
        return False
    if actual == wanted:
        return True
    if level == "appendix":
        a, b = appendix_key(actual), appendix_key(wanted)
    elif level == "section":
        a, b = encode_section(actual), encode_section(wanted)
    else:
        a, b = encode_id(actual), encode_id(wanted)
    return a.casefold() == b.casefold() if fold else a == b


def edition_date(title: int, year: int) -> dt.date:
    """The date of a title's annual edition: titles 1-16 are revised as of
    January 1, 17-27 April 1, 28-41 July 1 and 42-50 October 1."""
    month = next((month for last, month in EDITIONS if title <= last), 10)
    return dt.date(year, month, 1)


@dataclass(frozen=True)
class Address:
    """A place in the CFR, down to a paragraph, optionally at a date.

    >>> str(Address.parse("45 CFR 164.502(a)(1)"))
    '45/164.502/a/1'
    >>> Address.parse("45/part-164/subpart-E").citation()
    '45 CFR Part 164 Subpart E'

    ``hierarchy`` holds the levels, as for the API; an appendix may be its
    key ("appendix-A") or full name. ``paragraph`` holds the markers, where a
    marker repeated among its siblings has its occurrence ("1~2").
    ``group`` is a subject group's eCFR id; ``date`` is None for the latest.
    """

    title: int
    hierarchy: Hierarchy = field(default_factory=Hierarchy)
    paragraph: tuple[str, ...] = ()
    group: str | None = None
    date: dt.date | None = None

    @classmethod
    def parse(cls, text: str | int | Address, *, title: int | None = None) -> Self:
        """Read one place in the CFR, written any common way.

        Accepts addresses (``45/164.502/a/1@2020-01-01``, ``title-45/...``,
        ``section-164.502``), file names and paths ecfr-md saved, old or new
        (``45_164.502_a_1.md``, ``.../part-164/subpart-E/164.502.md``),
        ``cfr:`` URIs, eCFR, LII and GovInfo URLs, USLM identifiers
        (``/us/cfr/t45/s164.502/a/1``), GovInfo granule ids and citations as
        :meth:`Citation.parse <ecfr_md.citation.Citation.parse>` reads them,
        plus Bluebook forms (``45 C.F.R. § 164.502(a)(1) (2018)``, where the
        year means that title's annual edition), spaced markers
        (``164.502 a 1 i``) and dotted ones (``164.502.a.1``).

        ``title`` is the title for addresses that leave it out, such as
        ``164.502/a``. Raises :class:`InvalidCitationError`, saying what to
        do, for anything else, and for lists and ranges, which
        :meth:`parse_many` reads.
        """
        if isinstance(text, Address):
            return cls(
                text.title, text.hierarchy, text.paragraph, text.group, text.date
            )
        found = cls.parse_many(text, title=title)
        if len(found) > 1:
            shown = ", ".join(str(a) for a in found[:3])
            more = ", ..." if len(found) > 3 else ""
            raise InvalidCitationError(
                f"{str(text)!r} names {len(found)} places ({shown}{more}), but one "
                "was expected. Give them one at a time, or use get_many (on the "
                "command line, -o DIRECTORY or --from FILE)"
            )
        return found[0]

    @classmethod
    def parse_many(cls, text: str | int, *, title: int | None = None) -> list[Self]:
        """Read one or several places: a list such as ``45/164.502,164.504``
        or ``45 CFR §§ 164.502, 164.506``, or a range of paragraphs such as
        ``45/164.502/b..d``. Accepts everything :meth:`parse` does."""
        found = _parse(str(text), title)
        return [cls(a.title, a.hierarchy, a.paragraph, a.group, a.date) for a in found]

    @property
    def level(self) -> str:
        """The level named: "title", "part", "section", "paragraph", ..."""
        h = self.hierarchy
        if self.paragraph:
            return "paragraph"
        for level in ("section", "appendix"):
            if level in h:
                return level
        if self.group is not None:
            return "subject_group"
        return next((lv for lv in reversed(STRUCTURE) if lv in h), "title")

    @property
    def markers(self) -> tuple[str, ...]:
        """Paragraph markers without occurrences: ("a", "1")."""
        return tuple(m.partition("~")[0] for m in self.paragraph)

    def __str__(self) -> str:
        path = "/".join([str(self.title), *self._segments()])
        return f"{path}@{self.date.isoformat()}" if self.date else path

    def _segments(self) -> list[str]:
        h = self.hierarchy
        level = self.level

        def seg(name: str) -> str:
            return f"{name}-{encode_id(h[name])}"  # type: ignore[literal-required]

        if "section" in h:
            return [encode_section(h["section"]), *self.paragraph]
        segments = []
        if level in UPPER_LEVELS:
            # Chapters are unique in a title; subchapters only in a chapter.
            if "subtitle" in h and (level == "subtitle" or "chapter" not in h):
                segments.append(seg("subtitle"))
            if "chapter" in h and level != "subtitle":
                segments.append(seg("chapter"))
            if level == "subchapter":
                segments.append(seg("subchapter"))
            return segments
        for above in (
            ("part",),
            ("chapter", "subchapter"),
            ("subtitle", "subchapter"),
            ("subchapter",),
        ):
            if above[0] in h:
                segments += [seg(name) for name in above if name in h]
                break
        subpart = h.get("subpart")
        if "appendix" in h:
            subpart = appendix_subpart(h["appendix"], subpart)
        if subpart and level != "part":
            segments.append(f"subpart-{encode_id(subpart)}")
        if level == "subject_group":
            segments.append(f"group-{self.group}")
        if "appendix" in h:
            segments.append(appendix_key(h["appendix"]))
        return segments

    def citation(
        self, style: Literal["ofr", "bluebook"] = "ofr", year: int | None = None
    ) -> str:
        """A citation: ``"ofr"`` as the Federal Register writes them
        (``45 CFR 164.502(a)(1)``), or ``"bluebook"``
        (``45 C.F.R. § 164.502(a)(1) (2024)``), with ``year`` or else the
        year of the address's date, if any."""
        cited = self.to_citation()
        h = cited.hierarchy
        if "appendix" in h and APPENDIX_KEY.fullmatch(h["appendix"]):
            subpart = h.get("subpart")
            h = as_hierarchy(
                {**h, "appendix": appendix_name(h["appendix"], h.get("part"), subpart)}
            )
            cited = Citation(cited.title, h, cited.paragraph)
        if style == "ofr":
            return str(cited)
        if style != "bluebook":
            raise InvalidCitationError(
                f"style must be 'ofr' or 'bluebook', not {style!r}"
            )
        when = year or (self.date.year if self.date else None)
        cite = " ".join(f"{self.title} C.F.R. {_bluebook(cited)}".split())
        return cite + (f" ({when})" if when else "")

    def to_citation(self) -> Citation:
        """The citation's levels and markers, without occurrences or date."""
        return Citation(self.title, as_hierarchy(self.hierarchy), self.markers)

    def url(self, site: str = SITE_URL) -> str:
        """The eCFR web page (on ``site``), as the eCFR links it: sections by number
        (``/current/title-45/section-164.502#p-164.502(a)(1)``), everything
        else under its part (``.../part-1026/appendix-Supplement%20I%20to...``)."""
        h = self.hierarchy
        when = f"on/{self.date.isoformat()}" if self.date else "current"
        if "section" in h:
            path = [f"section-{h['section']}"]
        elif "appendix" in h:
            name = h["appendix"]
            if APPENDIX_KEY.fullmatch(name):
                name = appendix_name(name, h.get("part"), h.get("subpart"))
            path = [f"part-{h['part']}"] if "part" in h else []
            path.append(f"appendix-{name}")
        else:
            levels = [lv for lv in STRUCTURE if lv in h]
            if self.group is not None:
                levels = [lv for lv in levels if lv != "subpart"]
            path = [f"{lv}-{h[lv]}" for lv in levels]  # type: ignore[literal-required]
            if self.group is not None:
                path.append(f"subject-group-{self.group}")
        url = "/".join(
            [
                site.rstrip("/"),
                when,
                f"title-{self.title}",
                *(quote(p, safe="") for p in path),
            ]
        )
        if self.paragraph and "section" in h:
            url += f"#p-{h['section']}" + "".join(f"({m})" for m in self.markers)
        return url

    def uri(self) -> str:
        """The address as a URI, e.g. ``cfr:45/164.502/a/1@2024-01-01``."""
        return f"cfr:{self}"

    def file_name(self) -> str:
        """A file name for the text at this address, e.g. "45_164.502_a_1.md",
        or with a date "45_164.502_a_1@2020-01-01.md"."""
        return str(self).replace("/", "_") + ".md"

    def undated(self) -> Address:
        """The same place without a date."""
        return dataclasses.replace(self, date=None)


def _bluebook(cited: Citation) -> str:
    h = cited.hierarchy
    if "section" in h:
        return f"§ {h['section']}" + "".join(f"({m})" for m in cited.paragraph)
    shown = [k for k in STRUCTURE if k in h and not ("part" in h and k in UPPER_LEVELS)]
    pieces = [f"{BLUEBOOK[k]} {h[k]}" for k in shown]  # type: ignore[literal-required]
    if "appendix" in h:
        name = h["appendix"]
        kind = APPENDIX_NAME.fullmatch(name)
        short = {"appendix": "app.", "supplement": "supp."}
        if kind and kind["des"] and (word := short.get(kind["kind"].lower())):
            pieces = [p for p in pieces if not p.startswith("subpt.")]
            pieces.append(f"{word} {kind['des']}")
        else:
            pieces.append(name)
    return ", ".join(pieces)


class _Draft:
    """An address being parsed."""

    def __init__(self, title: int, date: dt.date | None = None) -> None:
        self.title = title
        self.hierarchy: dict[str, str] = {}
        self.paragraph: list[str] = []
        self.group: str | None = None
        self.date = date

    def done(self) -> Address:
        # A section or appendix is found without the group it is in.
        inside = self.hierarchy.keys() & {"section", "appendix"}
        return Address(
            self.title,
            as_hierarchy(self.hierarchy),
            tuple(self.paragraph),
            None if inside else self.group,
            self.date,
        )


def _parse(text: str, title: int | None) -> list[Address]:
    raw = _clean(text)
    if not raw:
        raise InvalidCitationError(f"Give a citation or address, {EXAMPLES}")
    if URL_START.match(raw):
        return [_url(raw)]
    if raw.lower().startswith("cfr:"):
        return _addresses(raw[4:].lstrip("/"), title)
    if raw.lower().startswith("/us/cfr/"):
        return [_uslm(raw)]
    if granule := GRANULE.search(raw):
        return [_granule(granule)]
    if raw.lower().endswith(".md") or "\\" in raw or raw.startswith(("/", "./", "~")):
        return [_file(raw, title)]
    if ADDRESS_START.match(raw) or (RELATIVE_START.match(raw) and not CFR.search(raw)):
        return _addresses(raw, title)
    return _citations(raw, title)


def _clean(text: str) -> str:
    """Pasted text without quotes, invisible characters or a final full stop."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(text.split()).strip(QUOTES).strip().rstrip(".;:,").strip()


def _date(text: str | None) -> dt.date | None:
    """An address's date: YYYY-MM-DD, YYYY-MM or YYYY (the first day)."""
    if text is None or text.lower() == "current":
        return None
    try:
        return dt.date.fromisoformat((text + "-01-01")[:10])
    except ValueError:
        raise InvalidCitationError(
            f"{text!r} is not a date; dates in addresses are YYYY-MM-DD, e.g. "
            "45/164.502@2024-01-01"
        ) from None


def _addresses(text: str, title: int | None) -> list[Address]:
    """Canonical addresses, possibly listed with "," and ranges of paragraphs."""
    found: list[Address] = []
    date: dt.date | None = None
    for item in re.split(r"[\s,;]+", text):
        if not item:
            continue
        stamp = DATE.search(item)
        if stamp and stamp.group(0):
            item, date = item[: stamp.start()], _date(stamp[1])
        previous = found[-1] if found else None
        if previous and previous.paragraph and MARKER_SEGMENT.fullmatch(item):
            # "45/164.502/a/1,2": a sibling of the paragraph before.
            item = "/".join([str(previous.undated()).rsplit("/", 1)[0], item])
        elif previous and not TITLE_SEGMENT.fullmatch(item.split("/")[0]):
            item = f"{previous.title}/{item}"  # Later items inherit the title.
        address = _segments(item.split("/"), title, date)
        found += _expand(address)
    if not found:
        raise InvalidCitationError(f"Give a citation or address, {EXAMPLES}")
    return found


def _expand(address: Address) -> list[Address]:
    """A range of paragraphs, "b..d", as one address per paragraph."""
    if not address.paragraph or ".." not in address.paragraph[-1]:
        return [address]
    first, last = address.paragraph[-1].split("..")
    # The level the markers' depth gives, else any both ends fit, numerals
    # first: "(i)-(v)" is rarely i to v.
    depth = len(address.paragraph)
    for level in (depth, 3, 1, 2, 4):
        position = POSITIONS.get(level)
        start, end = (position(first), position(last)) if position else (None, None)
        if start is not None and end is not None and start <= end:
            base = address.paragraph[:-1]
            return [
                dataclasses.replace(address, paragraph=(*base, marker_at(level, n)))
                for n in range(start, end + 1)
            ]
    raise InvalidCitationError(
        f"({first}) to ({last}) isn't a range of paragraphs at the same level"
    )


def _segments(
    segments: Sequence[str], title: int | None, date: dt.date | None
) -> Address:
    """An address from its "/"-separated segments (title first, if given)."""
    segs = [s for s in segments if s and s.lower() not in ("index", "index.md")]
    if segs and (head := TITLE_SEGMENT.fullmatch(segs[0])):
        title = int(head[1])
        segs.pop(0)
    elif title is None:
        rest = "/".join(segs)
        raise InvalidCitationError(
            f"{rest!r} needs a title number first, e.g. 45/{rest}"
        )
    draft = _Draft(title, date)
    while segs:
        # A saved tree tells apart siblings whose names differ only in case
        # with "~2" ("subpart-Cc~2" beside "subpart-CC"); the name finds it.
        seg = TREE_SUFFIX.sub("", segs.pop(0))
        keyword = KEYWORD_SEGMENT.fullmatch(seg)
        level = SEGMENT_LEVELS[keyword["kw"].lower()] if keyword else None
        if level == "section" or (level is None and SECTION_SEGMENT.fullmatch(seg)):
            token = keyword["id"] if keyword else seg
            draft.hierarchy["section"] = decode_section(token)
            draft.paragraph = segs
            for marker in draft.paragraph:
                if not MARKER_SEGMENT.fullmatch(marker):
                    raise InvalidCitationError(
                        f"{marker!r} isn't a paragraph marker such as a, 1, i or "
                        f"A, in {'/'.join(segments)!r}"
                    )
            break
        if keyword and level == "subject_group":
            draft.group = keyword["id"]
        elif keyword and level:
            draft.hierarchy[level] = keyword["id"]
        elif PART_SEGMENT.fullmatch(seg) and "part" not in draft.hierarchy:
            draft.hierarchy["part"] = seg  # "45/164" is part 164.
        elif (kind := seg.split("-")[0].lower()) in APPENDIX_KINDS:
            _appendix(draft, seg, kind)
        else:
            raise InvalidCitationError(
                f"{seg!r} isn't part of an address, in {'/'.join(segments)!r}; "
                f"addresses look like 45/164.502/a/1 or 45/part-164/subpart-E"
            )
    return draft.done()


def _appendix(draft: _Draft, segment: str, kind: str) -> None:
    rest = segment[len(kind) + 1 :]
    words = rest.replace("-", " ")
    if rest == rest.lower() and re.search(r"\b(?:to|of)\b", words):
        # An old file name: "appendix-appendix-a-to-subpart-c-of-part-164".
        name = (
            words if words.split()[0].lower() in APPENDIX_KINDS else f"{kind} {words}"
        )
        draft.hierarchy["appendix"] = name
        if subpart := appendix_subpart(name):
            draft.hierarchy["subpart"] = subpart
        if part := re.search(r"\b(?:to|of) part (\S+)$", name):
            draft.hierarchy.setdefault("part", part[1])
        return
    designator = f"-{rest}" if rest else ""
    draft.hierarchy["appendix"] = APPENDIX_KINDS[kind] + designator


def _file(path: str, title: int | None) -> Address:
    """A file or folder ecfr-md saved: a single file's name, or a path in a tree.

    Any folders above the tree's first level ("title-45", "part-164", ...)
    are ignored.
    """
    path = re.sub(r"\.md$", "", path, flags=re.IGNORECASE)
    segments = [s for s in re.split(r"[/\\]+", path) if s]
    name = segments[-1] if segments else ""
    stamp = DATE.search(name)
    date = _date(stamp[1]) if stamp and stamp.group(0) else None
    if stamp and stamp.group(0):
        segments[-1] = name = name[: stamp.start()]
    if legacy := LEGACY_FILE.fullmatch(name):
        return _legacy(int(legacy[1]), legacy[2] or "", date)
    if "_" in name:  # A single file: the address with "/" as "_".
        return _segments(name.split("_"), title, date)
    for i in range(len(segments) - 1, -1, -1):
        if re.fullmatch(r"title-\d+", segments[i], re.IGNORECASE):
            return _segments(segments[i:], title, date)
    start = next((i for i, s in enumerate(segments) if _is_tree_segment(s)), None)
    if start is None:
        raise InvalidCitationError(
            f"{path!r} isn't named like a file ecfr-md saves (e.g. 45_164.502.md); "
            f"give a citation or address, {EXAMPLES}"
        )
    return _segments(segments[start:], title, date)


def _is_tree_segment(segment: str) -> bool:
    return bool(
        KEYWORD_SEGMENT.fullmatch(segment)
        or SECTION_SEGMENT.fullmatch(segment)
        or segment.split("-")[0].lower() in APPENDIX_KINDS
    )


def _legacy(title: int, rest: str, date: dt.date | None) -> Address:
    """An old file name: "45-cfr-164.502-a", "45-cfr-part-164-subpart-e".

    Old names lost parentheses and case, so a section number with a hyphen
    reads as a paragraph: "12-cfr-745.9-1" is 745.9(1).
    """
    draft = _Draft(title, date)
    words = rest.split("-") if rest else []
    while words:
        word = words.pop(0).lower()
        if SECTION_SEGMENT.fullmatch(word):
            draft.hierarchy["section"] = word
            draft.paragraph = words
            break
        if word in APPENDIX_KINDS:
            _appendix(draft, "-".join([word, *words]), word)
            break
        if word in STRUCTURE and words:
            draft.hierarchy[word] = words.pop(0)
        else:
            raise InvalidCitationError(
                f"{title}-cfr-{rest!r} isn't a file name ecfr-md wrote; name a "
                f"place like {EXAMPLES}"
            )
    return draft.done()


def _citations(text: str, title: int | None) -> list[Address]:
    """Citations: what Citation.parse_many reads, plus a Bluebook edition year,
    spaced markers ("164.502 a 1") and dotted ones ("164.502.a.1")."""
    year = EDITION_YEAR.search(text)
    if year:
        text = text[: year.start()]
    if title is not None and not re.match(r"(?:title\s+)?\d+(?![\d.])", text, re.I):
        text = f"{title} CFR {text}"
    words: list[str] = []
    for word in text.split(" "):
        if dotted := DOTTED.fullmatch(word):
            markers = dotted["markers"].split(".")[1:]
            if "-" in dotted["rest"]:  # "1.401.k-1": markers in the number.
                word = decode_section(word)
            else:
                word = (
                    dotted["section"]
                    + "".join(f"({m})" for m in markers)
                    + dotted["rest"]
                )
        elif (
            words
            and SPACED_MARKER.fullmatch(word)
            and word.lower() not in NOT_MARKERS
            and CITED_SECTION.fullmatch(words[-1])
        ):
            words[-1] += f"({word})"
            continue
        words.append(word)
    found = Citation.parse_many(" ".join(words))
    return [
        Address(
            c.title,
            c.hierarchy,
            c.paragraph,
            date=edition_date(c.title, int(year[1])) if year else None,
        )
        for c in found
    ]


def _url(text: str) -> Address:
    if text.startswith("/"):
        text = BASE_URL + text
    elif not re.match(r"https?://", text, re.IGNORECASE):
        text = "https://" + text
    url = urlsplit(text)
    host = url.netloc.lower()
    if host.endswith("ecfr.gov"):
        return _ecfr_api(url) if "/api/" in url.path else _ecfr_page(url)
    if host.endswith("law.cornell.edu"):
        return _lii(url)
    if host.endswith("govinfo.gov"):
        return _govinfo(url)
    raise InvalidCitationError(
        f"{text} isn't an eCFR, LII or GovInfo link; give a citation or address, "
        f"{EXAMPLES}"
    )


def _ecfr_page(url: SplitResult) -> Address:
    """An eCFR web page: /current/title-45/section-164.502#p-164.502(a)(1)."""
    segments = [unquote(s) for s in url.path.split("/") if s]
    date = None
    if segments[:1] == ["on"] and len(segments) > 1:
        date = _date(segments[1])
        segments = segments[2:]
    elif segments[:1] in (["current"], ["compare"]):
        segments = segments[1:]
    if not segments or not TITLE_SEGMENT.fullmatch(segments[0]):
        raise InvalidCitationError(f"{url.geturl()} isn't a page of CFR text")
    draft = _Draft(int(segments[0].removeprefix("title-")), date)
    for segment in segments[1:]:
        kind, _, identifier = segment.partition("-")
        if segment.startswith("subject-group-"):
            draft.group = segment.removeprefix("subject-group-")
        elif kind == "appendix":
            draft.hierarchy["appendix"] = identifier
            for level in ("part", "chapter"):  # "Appendix A to Chapter 2"
                if found := re.search(rf"\bto {level} (\S+)", identifier, re.I):
                    draft.hierarchy.setdefault(level, found[1])
        elif kind in (*STRUCTURE, "section") and identifier:
            draft.hierarchy[kind] = identifier
        else:
            raise InvalidCitationError(f"{url.geturl()} isn't a page of CFR text")
    anchor = unquote(url.fragment)
    section = draft.hierarchy.get("section")
    if section and anchor.startswith(f"p-{section}("):
        draft.paragraph = re.findall(r"\((\w+)\)", anchor[len(section) + 2 :])
    return draft.done()


def _ecfr_api(url: SplitResult) -> Address:
    """An eCFR API request: /api/versioner/v1/full/2024-01-01/title-45.xml?part=164."""
    title = re.search(r"title-(\d+)", url.path)
    if title is None:
        raise InvalidCitationError(f"{url.geturl()} doesn't name a title")
    when = re.search(r"/(\d{4}-\d{2}-\d{2})/", url.path)
    draft = _Draft(int(title[1]), _date(when[1]) if when else None)
    for key, values in parse_qs(url.query).items():
        if key in (*STRUCTURE, "section", "appendix"):
            draft.hierarchy[key] = values[0]
    return draft.done()


def _lii(url: SplitResult) -> Address:
    """A Cornell LII page: /cfr/text/45/164.502#a_1_i, /cfr/text/45/part-164."""
    segments = [s for s in unquote(url.path).split("/") if s]
    if (
        segments[:2] != ["cfr", "text"]
        or not segments[2:3]
        or not segments[2].isdigit()
    ):
        raise InvalidCitationError(f"{url.geturl()} isn't a page of CFR text")
    draft = _Draft(int(segments[2]))
    for segment in segments[3:]:
        kind, _, identifier = segment.partition("-")
        if kind == "chapter" and SUBTITLE.fullmatch(identifier):
            draft.hierarchy["subtitle"] = identifier  # LII calls subtitles chapters.
        elif kind in LII_LEVELS and identifier:
            draft.hierarchy[kind] = identifier
        elif kind == "appendix":
            name = identifier.replace("_", " ")
            first = name.split()[0].lower()
            draft.hierarchy["appendix"] = (
                name if first in APPENDIX_KINDS else f"Appendix {name}"
            )
            if part := re.search(r"\bto part (\S+)", name, re.I):
                draft.hierarchy["part"] = part[1]
        else:
            draft.hierarchy["section"] = segment
    if url.fragment and "section" in draft.hierarchy:
        draft.paragraph = url.fragment.split("_")
    return draft.done()


def _govinfo(url: SplitResult) -> Address:
    """GovInfo's link service (/link/cfr/45/164?sectionnum=502) or a granule."""
    if granule := GRANULE.search(unquote(url.path)):
        return _granule(granule)
    segments = [s for s in url.path.split("/") if s]
    if segments[:2] != ["link", "cfr"] or len(segments) < 4:
        raise InvalidCitationError(f"{url.geturl()} isn't a link to CFR text")
    title, part = int(segments[2]), segments[3]
    query = parse_qs(url.query)
    year = query.get("year", ["mostrecent"])[0]
    draft = _Draft(title, edition_date(title, int(year)) if year.isdigit() else None)
    if number := query.get("sectionnum"):
        draft.hierarchy["section"] = f"{part}.{number[0]}"
    else:
        draft.hierarchy["part"] = part
    return draft.done()


def _granule(found: re.Match[str]) -> Address:
    """A GovInfo granule id: CFR-2023-title45-vol2-sec164-502, ...-part164-appA.

    Granule ids drop parentheses, so 1.401(k)-1 comes back as 1.401k-1.
    """
    title = int(found["title"])
    draft = _Draft(title, edition_date(title, int(found["year"])))
    identifier = found["id"]
    if found["kind"] and found["kind"].lower() == "sec":
        head, _, rest = identifier.partition("-")
        draft.hierarchy["section"] = f"{head}.{rest}"
    elif found["kind"]:
        draft.hierarchy["part"] = identifier
        if found["subpart"]:
            draft.hierarchy["subpart"] = found["subpart"]
        if found["app"]:
            draft.hierarchy["appendix"] = f"appendix-{found['app']}"
    return draft.done()


def _uslm(text: str) -> Address:
    """A USLM identifier: /us/cfr/t45/s164.502/a/1@2024-01-01, /us/cfr/t45/pt164."""
    core, _, when = text.partition("@")
    segments = [s for s in core.split("/") if s][2:]
    if not segments or not re.fullmatch(r"t\d+", segments[0], re.IGNORECASE):
        raise InvalidCitationError(f"{text!r} isn't a USLM identifier of CFR text")
    draft = _Draft(int(segments.pop(0)[1:]), _date(when or None))
    while segments:
        segment = segments.pop(0)
        if segment.lower().startswith("app"):
            draft.hierarchy["appendix"] = appendix_key(f"appendix {segment[3:]}")
            continue
        found = re.fullmatch(r"(sch|spt|st|ch|pt|sg|s|p)(.+)", segment, re.IGNORECASE)
        if found is None:
            raise InvalidCitationError(f"{segment!r} in {text!r} isn't a USLM level")
        level = USLM_PREFIXES[found[1].lower()]
        if level == "section":
            draft.hierarchy["section"] = found[2]
            draft.paragraph = segments
            break
        if level == "subject_group":
            draft.group = found[2]
        else:
            draft.hierarchy[level] = found[2]
    return draft.done()


def walk[N](scope: N, address: Address, above: Mapping[str, object]) -> N:
    """The node an address names, at or below ``scope``.

    ``above`` holds the levels that contain ``scope`` but aren't nodes
    (the hierarchy of the request). Levels the scope is already in are
    skipped; others match the nearest descendant, comparing identifiers
    exactly, then as addresses write them, then ignoring case. Nodes and
    outline entries both work: anything with ``level``, ``identifier``,
    ``citation``, ``children`` and ``ancestors``.
    """
    node: Any = scope
    h = address.hierarchy
    for level in STRUCTURE:
        value = h.get(level)
        if value is None or _within(node, level, value, above):
            continue
        if level == "subpart" and "appendix" in h:
            continue  # An appendix "to Subpart C" sits beside it; see below.
        node = _one(_nearest(node, level, value), f"{level} {value}", node)
    if address.group is not None and not _within(
        node, "subject_group", address.group, above
    ):
        node = _one(_nearest(node, "subject_group", address.group), address.group, node)
    if "section" in h and not _within(node, "section", h["section"], above):
        node = _one(_nearest(node, "section", h["section"]), h["section"], node)
    if "appendix" in h and not _within(node, "appendix", h["appendix"], above):
        node = _appendix_in(node, h["appendix"], h.get("subpart"), above)
    for marker in address.paragraph:
        node = _paragraph(node, marker)
    return node


def _within(node: Any, level: str, value: str, above: Mapping[str, object]) -> bool:
    if any(
        same_identifier(level, _text(above.get(level)), value, fold)
        for fold in (False, True)
    ):
        return True
    return any(
        n.level == level and same_identifier(level, n.identifier, value, fold)
        for n in (node, *node.ancestors)
        for fold in (False, True)
    )


def _nearest(node: Any, level: str, value: str, *, deep: bool = False) -> list[Any]:
    """Matches at the shallowest depth (or with ``deep``, at any depth),
    exact, then as addresses write them, then ignoring case."""
    for test in (
        lambda n: n.identifier == value,
        lambda n: same_identifier(level, n.identifier, value, False),
        lambda n: same_identifier(level, n.identifier, value, True),
        lambda n: level == "section" and _prefixed(n, value),
    ):
        layer, found = list(node.children), []
        while layer and not (found and not deep):
            found += [n for n in layer if n.level == level and test(n)]
            layer = [c for n in layer if n.level != "section" for c in n.children]
        if found:
            return found
    return []


def _prefixed(node: Any, value: str) -> bool:
    """Whether a section is named as the eCFR's links name sections numbered
    unlike their part: 43 CFR Part 5420 holds § 5422.1, "section-5420.5422.1"."""
    part = next((a.identifier for a in node.ancestors if a.level == "part"), None)
    return part is not None and same_identifier(
        "section", f"{part}.{node.identifier}", value, False
    )


def _appendix_in(
    node: Any, name: str, subpart: str | None, above: Mapping[str, object]
) -> Any:
    """An appendix, telling "Appendix A to Part 164" from "Appendix A to
    Subpart C of Part 164": an appendix to a subpart can sit beside it."""
    found = _nearest(node, "appendix", name, deep=True)
    if len(found) > 1 or subpart is not None:

        def belongs(n: Any) -> str | None:
            inside = next(
                (a.identifier for a in n.ancestors if a.level == "subpart"),
                _text(above.get("subpart")),
            )
            return appendix_subpart(n.identifier or "", inside)

        if subpart is None:
            found = [n for n in found if belongs(n) is None] or found
        else:
            found = [
                n
                for n in found
                if (b := belongs(n)) is not None and b.casefold() == subpart.casefold()
            ]
    where = f"{name} to subpart {subpart}" if subpart else name
    return _one(found, where, node)


def _paragraph(node: Any, marker: str) -> Any:
    """A child paragraph; "1~2" is the second (1) among its siblings.

    A marker that repeats needs its occurrence: "1" alone is ambiguous.
    """
    plain, _, nth = marker.partition("~")
    found = [
        c for c in node.children if c.level == "paragraph" and c.identifier == plain
    ]
    if not nth:
        return _one(found, f"({plain})", node)
    if not 0 < int(nth) <= len(found):
        raise NodeNotFoundError(
            f"No ({plain}) number {nth} in {node.citation}; it has {len(found)}"
        )
    return found[int(nth) - 1]


def _one(matches: list[Any], key: str, scope: Any) -> Any:
    if not matches:
        raise NodeNotFoundError(f"No {key!r} in {scope.citation}")
    if len(matches) > 1:
        # Repeated paragraphs share a citation, but not an address.
        named = [m.citation for m in matches]
        if len(set(named)) < len(named):
            named = [m.address for m in matches]
        found = ", ".join(named[:5])
        more = f" and {len(matches) - 5} more" if len(matches) > 5 else ""
        raise AmbiguousNodeError(
            f"{len(matches)} nodes match {key!r} in {scope.citation}: {found}{more}",
            matches,
        )
    return matches[0]


def relative(key: str, title: int, in_section: bool) -> Address | None:
    """A lookup key read as an address relative to a node, if it is one.

    "subpart-E", "164.512/k/1", "group-ECFR…" and "45/164.502" are addresses;
    in a section, so are markers such as "a/1". Other keys ("Subpart E",
    "164.502(a)") are left to the citation reader.
    """
    text = key.strip()
    markers = text.strip("/").split("/")
    if (
        in_section
        and ("/" in text or "~" in text)
        and all(MARKER_SEGMENT.fullmatch(m) and ".." not in m for m in markers)
    ):
        return Address(title, paragraph=tuple(markers))
    looks = (
        "/" in text
        or "~" in text
        or KEYWORD_SEGMENT.fullmatch(text)
        or APPENDIX_KEY.fullmatch(text)
        or DOTTED.fullmatch(text)
    )
    if not looks or " " in text:
        return None
    try:
        return Address.parse(text, title=title)
    except InvalidCitationError:
        return None  # Perhaps an identifier: looked up as one.


def _text(value: object) -> str | None:
    return None if value is None else str(value)
