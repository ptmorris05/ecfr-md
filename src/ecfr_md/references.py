"""Cross-references in regulation text: "§ 164.501", "paragraph (a)(1) of
this section", "45 CFR part 160", "42 U.S.C. 1320d", "78 FR 5692", ...

:func:`find_references` finds them and resolves each CFR reference to an
:class:`~ecfr_md.address.Address`, using the place the text is in (its
``context``) for relative ones such as "subpart E of this part". Citations of
the U.S. Code, the Federal Register and public laws link to their official
sites; the link templates are the module constants ``USC_URL``, ``FR_URL``
and ``PUBLIC_LAW_URL``, and each call can override them with ``urls``. CFR
references link to the eCFR's site, or another given as ``site``.

:func:`link_markdown` turns the references in Markdown into links.

Resolution is by pattern, not by reading the sentence, so it can miss
unusual wording and can't tell whether a target exists; see the README for
measured precision and recall.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from ecfr_md.address import POSITIONS, Address
from ecfr_md.config import FR_URL, PUBLIC_LAW_URL, SITE_URL, USC_URL
from ecfr_md.params import as_hierarchy

Kind = Literal["cfr", "usc", "fr", "public_law"]

SECTION = (
    r"\d+[A-Za-z]?\.\d+[A-Za-z0-9]*(?:-\d+[A-Za-z0-9]*)*(?:\([a-z0-9]+\)-\d+[A-Za-z]?)?"
)
MARKERS = r"(?:\([A-Za-z0-9]{1,6}\))+"
PART = r"\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?(?![\d.]*\d)"
SUBPART = r"(?:[A-Z]{1,4}|\d+)(?:-\d+)?\b"
# Words between the items of a list or range.
JOIN = re.compile(
    r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|through|to)\s+|\s*[-\u2013]\s*"
)
TAIL = r"(?:\s+of\s+this\s+(?:subchapter|chapter|title)|\s+of\s+title\s+(?P<of>\d+))?"
SUBPART_JOIN = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or|through)\s+")
PART_NUMBER = re.compile(PART)
PART_TAIL = re.compile(TAIL)
SUBPART_ID = re.compile(SUBPART)

ITEM = re.compile(rf"(?P<section>{SECTION})?(?P<markers>{MARKERS})?")
SECTIONS = re.compile(r"(?<![\w§])(?:(?P<title>\d+)\s+C\.?\s?F\.?\s?R\.?\s+)?§§?\s*")
TITLED = re.compile(
    r"(?<![\w.])(?P<title>\d+)\s+C\.?\s?F\.?\s?R\.?\s+(?:(?P<parts>[Pp]arts?\s+)|(?=\d))"
)
PARAGRAPHS = re.compile(r"\b[Pp]aragraphs?\s+(?=\()")
PARAGRAPH_TAIL = re.compile(
    rf"\s+of\s+(?:(?P<this>this\s+section)|§\s*(?P<section>{SECTION})|(?P<other>\w))"
)
PARTS = re.compile(r"(?<![\w.])[Pp]arts?\s+(?=\d)")
SUBPARTS = re.compile(r"\b[Ss]ubparts?\s+(?=[A-Z0-9])")
SUBPART_TAIL = re.compile(
    rf"\s+(?:of|to)\s+(?:(?P<this>this\s+part)|[Pp]art\s+(?P<part>{PART}){TAIL})"
)
APPENDIX = re.compile(
    rf"\b(?P<kind>[Aa]ppendix|[Ss]upplement)\s+(?P<des>[IVX]+|[A-Z]{{1,3}}\d*|\d+)\b"
    rf"(?:\s+(?:to|of)\s+(?:(?:[Ss]ubpart\s+(?P<subpart>{SUBPART})\s+of\s+)?"
    rf"(?:(?P<this>this\s+part)|[Pp]art\s+(?P<part>{PART}){TAIL})))?"
)
THIS = re.compile(r"\b[Tt]his\s+(?P<level>part|section|subpart)\b")
USC = re.compile(
    r"(?<![\w.])(?P<title>\d+)\s+U\.\s?S\.\s?C\.?\s+(?:§§?\s*)?"
    r"(?P<section>\d+[A-Za-z]*(?:-\d+[A-Za-z]*)*)(?:\([A-Za-z0-9]+\))*"
)
FR = re.compile(r"(?<![\w.])(?P<volume>\d+)\s+FR\s+(?P<page>\d{1,3}(?:,\d{3})+|\d+)\b")
PUBLIC_LAW = re.compile(
    r"\bPub(?:lic|\.)?\s*L(?:aw|\.)?\s*(?:No\.\s*)?(?P<congress>\d+)[-\u2013](?P<number>\d+)"
)
# Markdown that mustn't be linked: headings, links and images, HTML, code.
PROTECTED = re.compile(
    r"^#.*$|!?\[(?:\\.|[^\]\\])*\]\((?:[^()]|\([^()]*\))*\)|<[^>\n]+>|`[^`\n]*`",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Reference:
    """A citation found in text: where it is, and what it points to.

    ``target`` is the CFR place for ``kind == "cfr"``, else None; ``url`` is
    the eCFR page, or the official site of the U.S. Code (``"usc"``), the
    Federal Register (``"fr"``) or public laws (``"public_law"``).
    """

    start: int
    end: int
    text: str
    target: Address | None
    url: str
    kind: Kind = "cfr"

    @property
    def citation(self) -> str:
        """The target's citation, e.g. "45 CFR 164.501"; else the text."""
        return self.target.citation() if self.target else self.text

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: ``start``, ``end``, ``text``, ``kind``, ``citation``,
        ``address`` (None outside the CFR) and ``url``."""
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "kind": self.kind,
            "citation": self.citation,
            "address": str(self.target.undated()) if self.target else None,
            "url": self.url,
        }


@dataclass(frozen=True)
class _Where:
    """The place text is in, for resolving "this part" and the like."""

    title: int | None
    part: str | None
    subpart: str | None
    section: str | None
    date: Any

    def at(self, title: int | None = None, **levels: str | None) -> Address | None:
        title = title or self.title
        if title is None:
            return None
        return Address(
            title, as_hierarchy({k: v for k, v in levels.items() if v}), date=self.date
        )


def find_references(
    text: str,
    context: Address | str | int | None = None,
    *,
    urls: Mapping[str, str] | None = None,
    markdown: bool = False,
    site: str = SITE_URL,
) -> list[Reference]:
    """Cross-references in ``text``, in order.

    ``context`` is where the text is: an address or citation such as
    ``45/164.502`` (its title, part, subpart and section resolve relative
    references, and its date dates the links). Without it, only references
    that name their title ("45 CFR 160.103") and other codes are found.
    ``urls`` overrides the link templates: keys ``"usc"``, ``"fr"`` and
    ``"public_law"``. With ``markdown``, headings, links, HTML tags and code
    are skipped. CFR references link to pages on ``site``.
    """
    where = _where(context)
    templates = {"usc": USC_URL, "fr": FR_URL, "public_law": PUBLIC_LAW_URL}
    templates |= dict(urls or {})
    found: list[Reference] = []
    taken = [m.span() for m in PROTECTED.finditer(text)] if markdown else []
    for finder in (_paragraphs, _sections, _titled, _appendices, _subparts, _parts):
        for start, end, target in finder(text, where):
            _add(found, taken, text, start, end, target, site)
    for start, end, target in _this(text, where):
        _add(found, taken, text, start, end, target, site)
    for kind, start, end, url in _external(text, templates):
        if not _overlaps(taken, start, end):
            taken.append((start, end))
            found.append(Reference(start, end, text[start:end], None, url, kind))
    return sorted(found, key=lambda r: r.start)


def link_markdown(
    text: str,
    context: Address | str | int | None = None,
    href: Callable[[Reference], str | None] | None = None,
    *,
    urls: Mapping[str, str] | None = None,
    site: str = SITE_URL,
) -> str:
    """Markdown with each reference made a link.

    ``href`` chooses where a reference links (for example a relative path
    to a saved file), returning None to use its ``url``. Headings, existing
    links, HTML tags and code are left alone.
    """
    pieces, last = [], 0
    for ref in find_references(text, context, urls=urls, markdown=True, site=site):
        link = (href(ref) if href else None) or ref.url
        pieces += [text[last : ref.start], f"[{ref.text}]({_destination(link)})"]
        last = ref.end
    return "".join([*pieces, text[last:]])


def _destination(link: str) -> str:
    """A link destination Markdown reads whole: spaces need angle brackets."""
    return f"<{link}>" if " " in link else link


def _where(context: Address | str | int | None) -> _Where:
    if context is None:
        return _Where(None, None, None, None, None)
    address = Address.parse(context) if not isinstance(context, Address) else context
    h = address.hierarchy
    section = h.get("section")
    part = h.get("part") or (section.split(".")[0] if section else None)
    return _Where(address.title, part, h.get("subpart"), section, address.date)


def _add(
    found: list[Reference],
    taken: list[tuple[int, int]],
    text: str,
    start: int,
    end: int,
    target: Address | None,
    site: str,
) -> None:
    if target is None or _overlaps(taken, start, end):
        return
    taken.append((start, end))
    found.append(Reference(start, end, text[start:end], target, target.url(site)))


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < b and a < end for a, b in spans)


Found = Iterator[tuple[int, int, Address | None]]


def _items(text: str, at: int) -> Iterator[tuple[int, int, str | None, tuple]]:
    """A list or range of sections and paragraphs starting at ``at``:
    (start, end, section, markers) for each item."""
    pos = at
    while True:
        item = ITEM.match(text, pos)
        if item is None or item.end() == pos:
            return
        markers = tuple(re.findall(r"\((\w+)\)", item["markers"] or ""))
        yield item.start(), item.end(), item["section"], markers
        join = JOIN.match(text, item.end())
        following = ITEM.match(text, join.end()) if join else None
        if join is None or following is None or following.end() == join.end():
            return
        pos = join.end()


def _sections(text: str, where: _Where) -> Found:
    """ "§ 164.501", "§§ 164.502 and 164.504", "§ 164.502(a)(1) through (3)"."""
    for found in SECTIONS.finditer(text):
        title = int(found["title"]) if found["title"] else None
        yield from _section_list(text, found, title, where)


def _section_list(
    text: str, found: re.Match, title: int | None, where: _Where
) -> Found:
    """Sections, and paragraphs of the one before, listed after ``found``."""
    section, markers = None, ()
    for n, (start, end, number, more) in enumerate(_items(text, found.end())):
        if number is not None:
            section, markers = number, more
        elif section is None:
            return  # "§ (a)": no section to be in.
        else:
            markers = _continue(markers, more)
        yield (
            found.start() if n == 0 else start,
            end,
            _paragraph(where, title, section, markers),
        )


def _titled(text: str, where: _Where) -> Found:
    """ "45 CFR 160.103", "45 CFR part 160", "45 CFR parts 160 and 164"."""
    for found in TITLED.finditer(text):
        title = int(found["title"])
        if found["parts"]:
            yield from _part_list(text, found, title, where)
            continue
        listed = list(_section_list(text, found, title, where))
        yield from listed
        if not listed and (part := PART_NUMBER.match(text, found.end())):
            yield found.start(), part.end(), where.at(title, part=part[0])


def _part_list(text: str, found: re.Match, title: int | None, where: _Where) -> Found:
    pos, first = found.end(), True
    while part := PART_NUMBER.match(text, pos):
        begin = found.start() if first else part.start()
        first = False
        tail = PART_TAIL.match(text, part.end())
        end = tail.end() if tail and tail.group(0) else part.end()
        named = int(tail["of"]) if tail and tail["of"] else title
        yield begin, end, where.at(named, part=part[0])
        join = JOIN.match(text, part.end())
        if join is None:
            return
        pos = join.end()


def _parts(text: str, where: _Where) -> Found:
    """ "part 160", "parts 160 and 164 of this subchapter", "part 2 of title 42"."""
    for found in PARTS.finditer(text):
        yield from _part_list(text, found, None, where)


def _subparts(text: str, where: _Where) -> Found:
    """ "subpart E of this part", "subparts A and E of part 164", "subpart C"."""
    for found in SUBPARTS.finditer(text):
        items, pos = [], found.end()
        while item := SUBPART_ID.match(text, pos):
            items.append(item)
            between = SUBPART_JOIN.match(text, item.end())
            if between is None:
                break
            pos = between.end()
        if not items:
            continue
        tail = SUBPART_TAIL.match(text, items[-1].end())
        part, title = where.part, where.title
        if tail and tail["part"]:
            part, title = tail["part"], int(tail["of"]) if tail["of"] else title
        for n, item in enumerate(items):
            begin = found.start() if n == 0 else item.start()
            last = n == len(items) - 1
            end = tail.end() if tail and last else item.end()
            yield begin, end, where.at(title, part=part, subpart=item[0])


def _paragraphs(text: str, where: _Where) -> Found:
    """ "paragraph (a)(1) of this section", "paragraphs (b) and (c)", "paragraph
    (c) of § 164.502"."""
    for found in PARAGRAPHS.finditer(text):
        items = list(_items(text, found.end()))
        if not items or any(number for _, _, number, _ in items):
            continue
        tail = PARAGRAPH_TAIL.match(text, items[-1][1])
        if tail and tail["other"]:
            continue  # "of the definition", "of this appendix": not this section.
        section = tail["section"] if tail and tail["section"] else where.section
        markers: tuple[str, ...] = ()
        for n, (start, end, _, more) in enumerate(items):
            markers = _continue(markers, more)
            begin = found.start() if n == 0 else start
            if tail and n == len(items) - 1:
                end = tail.end()
            yield begin, end, _paragraph(where, None, section, markers)


def _appendices(text: str, where: _Where) -> Found:
    """ "appendix A to this part", "appendix B to part 50", "Supplement I"."""
    for found in APPENDIX.finditer(text):
        part = found["part"] or where.part
        if part is None:
            continue
        key = f"{found['kind'].lower()}-{found['des']}"
        title = int(found["of"]) if found["of"] else None
        target = where.at(title, part=part, subpart=found["subpart"], appendix=key)
        yield found.start(), found.end(), target


def _this(text: str, where: _Where) -> Found:
    """ "this part", "this subpart", "this section"."""
    for found in THIS.finditer(text):
        level = found["level"]
        if level == "section":
            target = where.at(section=where.section) if where.section else None
        elif level == "subpart":
            target = where.at(part=where.part, subpart=where.subpart)
            target = target if where.subpart else None
        else:
            target = where.at(part=where.part) if where.part else None
        yield found.start(), found.end(), target


def _external(
    text: str, templates: Mapping[str, str]
) -> Iterator[tuple[Kind, int, int, str]]:
    for found in USC.finditer(text):
        url = templates["usc"].format(title=found["title"], section=found["section"])
        yield "usc", found.start(), found.end(), url
    for found in FR.finditer(text):
        page = found["page"].replace(",", "")
        url = templates["fr"].format(volume=found["volume"], page=page)
        yield "fr", found.start(), found.end(), url
    for found in PUBLIC_LAW.finditer(text):
        congress = int(found["congress"])
        url = templates["public_law"].format(
            congress=congress, suffix=_ordinal(congress), number=found["number"]
        )
        yield "public_law", found.start(), found.end(), url


def _ordinal(n: int) -> str:
    """The suffix of an ordinal number: 101 -> "st", 104 -> "th", 112 -> "th"."""
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _paragraph(
    where: _Where, title: int | None, section: str | None, markers: tuple[str, ...]
) -> Address | None:
    target = where.at(title, section=section) if section else None
    return replace(target, paragraph=markers) if target else None


def _continue(previous: tuple[str, ...], markers: tuple[str, ...]) -> tuple[str, ...]:
    """Markers listed after others, as "(a)(1) and (2)" lists (a)(2): they
    replace the previous ones from the level they start at, the deepest
    where each marker fits the level after the one before ("(c)(3)" after
    (c)(2)(i) starts at (c), not at the roman numeral c)."""
    levels = [
        d
        for d in range(min(len(previous), 4), 0, -1)
        if all(
            d + k in POSITIONS and POSITIONS[d + k](m) for k, m in enumerate(markers)
        )
    ]
    if not levels:
        return markers
    return (*previous[: levels[0] - 1], *markers)
