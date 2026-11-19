"""Parse and format CFR citations such as ``45 CFR 164.502(a)(1)(i)(A)``."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

from ecfr_md.errors import InvalidCitationError
from ecfr_md.params import Hierarchy, as_hierarchy

LEVELS = ("subtitle", "chapter", "subchapter", "part", "subpart", "section")
UPPER_LEVELS = ("subtitle", "chapter", "subchapter")  # Only cited above a part.
KEYWORDS = {
    "subtitle": "subtitle",
    "chapter": "chapter",
    "chap": "chapter",
    "ch": "chapter",
    "subchapter": "subchapter",
    "subchap": "subchapter",
    "subch": "subchapter",
    "part": "part",
    "pt": "part",
    "subpart": "subpart",
    "subpt": "subpart",
    "section": "section",
    "sec": "section",
    "appendix": "appendix",
    "app": "appendix",
}
CFR = re.compile(r"(?<![A-Za-z])C\.?\s*F\.?\s*R(?![A-Za-z])\.?", re.IGNORECASE)
# "CRF" and "CFT" after the title number are typos for "CFR".
CFR_TYPO = re.compile(
    r"\s*\d+\s*(?P<typo>C\.?\s*(?:R\.?\s*F|F\.?\s*T)(?![A-Za-z])\.?)", re.IGNORECASE
)
QUOTES = "\"'\u201c\u201d\u2018\u2019"
# A trailing "(2023)" is the year of an edition, not a paragraph.
YEAR = re.compile(r"\s*\(\s*((?:19|20)\d\d)\s*\)$")
ET_SEQ = re.compile(r"\s+et\.?\s+seq\b\.?$", re.IGNORECASE)
SUPP = re.compile(r"\bsupp\b\.?", re.IGNORECASE)
OF_TITLE = re.compile(r"(?P<rest>.+?),?\s+of\s+title\s+(?P<title>\d+)", re.IGNORECASE)
# Part numbers: "164", "26a", or Title 41's "109-46".
NUMBER = re.compile(r"\d+[A-Za-z]?(?:-\d+[A-Za-z]?)*")
# Trailing "(a)(1)" groups are paragraphs; earlier ones belong to the section
# number itself, as in Title 26's "1.401(k)-1".
SECTION = re.compile(r"(?P<section>\d[\w-]*\..+?)(?P<paragraph>(?:\(\w+\))*)")
# The eCFR names a few sections by a range, "210.3-07 - 210.3-08" (an em dash
# in its text). Other ranges, "164.500-164.534" or "through", span sections.
RANGE_WORDS = frozenset({"\u2013", "through", "thru", "to"})
TIGHT_RANGE = re.compile(r"\d\w*\.[\w.()]+-\d[\w-]*\.[\w.()-]+")
LIST_WORDS = frozenset({"and", "or", "&"})
LIST_ITEM = r"\d[\w-]*\.[^\s,;&]+"
LIST_SEPARATOR = re.compile(r"\s*[,;]\s*(?:and\s+|or\s+)?|\s+(?:and|or|&)\s+", re.I)
LIST = re.compile(
    rf"(?P<head>.*?)(?P<items>{LIST_ITEM}(?:(?:{LIST_SEPARATOR.pattern}){LIST_ITEM})+)",
    re.IGNORECASE,
)
PARAGRAPH = re.compile(r"\((\w+)\)")
# "164.502 (a)" -> "164.502(a)", but not "Table 1a (North)": only before a
# marker-like group (number, one or two letters, or a lowercase roman numeral).
SPACED_MARKER = re.compile(r"\s+(?=\((?:\d+|[a-zA-Z]{1,2}|[ivxlc]+)\))")
APPENDIX_PART = re.compile(r"\b(?:to|of)\s+part\s+([\w-]+)", re.IGNORECASE)
# Appendix-level names in the CFR, besides "Appendix A to Part 1": every leading
# word found across all titles, plus anything attached "to Part N".
APPENDIX_NAME = re.compile(
    r"(?:non-mandatory\s+)?(?:appendix:?|appendixes|appendices|tables?|figures?|"
    r"supplement|exhibits?|annex|attachment|schedule|form|enclosure|special|"
    r"alphabetical|list|policy|subject)\b"
    r"|.*\bto\s+(?:subpart\s+\S+\s+of\s+)?part\s+\S+$",
    re.IGNORECASE,
)
# "Appendix J" or "Supplement I" alone, completed from the cited part.
SHORT_APPENDIX = re.compile(r"(appendix|supplement)\s+(\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class Citation:
    """A location in the CFR, down to a paragraph.

    >>> Citation.parse("45 CFR 164.502(a)(1)")
    Citation(title=45, hierarchy={'section': '164.502'}, paragraph=('a', '1'))
    """

    title: int
    hierarchy: Hierarchy = field(default_factory=Hierarchy)
    paragraph: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str | int) -> Self:
        """Parse a citation.

        Understands, case-insensitively and with or without ``§``, commas and
        dots in "C.F.R.": ``45``, ``Title 45``, ``45 CFR 164``,
        ``45 CFR Part 164 Subpart E``, ``45 CFR 164.502(a)(1)(i)(A)``,
        ``45 CFR Subtitle A Chapter I Subchapter C``,
        ``45 CFR Appendix A to Subpart C of Part 164`` and other appendix-level
        names such as ``12 CFR Supplement I to Part 1026``.

        Citations can be pasted as found: surrounding quotes and invisible
        characters are dropped, ``Supp.`` and ``app.`` are completed from the
        part (``12 CFR pt. 1026, app. J`` is ``Appendix J to Part 1026``), a
        trailing ``et seq.`` is ignored (the citation is its first section),
        and ``Subpart E of Part 164`` and ``Part 164 of Title 45`` work.

        Raises :class:`InvalidCitationError`, saying what to do instead, for a
        range or list of sections (:meth:`parse_many` expands lists), a
        trailing year such as ``(2023)`` (:class:`~ecfr_md.address.Address`
        reads it as that year's annual edition), ``CRF`` for ``CFR`` and
        unbalanced parentheses.
        """
        source = _clean(str(text))
        if typo := CFR_TYPO.match(source):
            raise InvalidCitationError(
                f'"{typo["typo"].strip()}" isn\'t recognised; did you mean CFR?'
            )
        if not _balanced(source):
            raise InvalidCitationError(
                f"unbalanced parentheses in {source!r}; each ( needs a matching )"
            )
        if of_title := OF_TITLE.fullmatch(source.rstrip(".;:,")):
            source = f"{of_title['title']} CFR {of_title['rest']}"
        cfr = CFR.search(source)
        if cfr and not source[: cfr.start()].strip():
            raise InvalidCitationError(_no_title(source[cfr.end() :]))
        normalised = CFR.sub(" ", source).replace("§", " ")
        normalised = normalised.strip().rstrip(".;:,")  # A citation ending a sentence.
        normalised = ET_SEQ.sub("", normalised).rstrip(".;:,")
        if year := YEAR.search(normalised):
            raise InvalidCitationError(
                f'"{year[0].strip()}" is an edition year, which a Citation has no '
                "place for; Address.parse reads it as the date of that year's "
                f'annual edition (or use date="{year[1]}")'
            )
        normalised = SUPP.sub("Supplement", normalised)
        # An em dash in a section the eCFR names by a range becomes "X - Y".
        normalised = re.sub(r"(?<=\d)\s*\u2014\s*(?=\d)", " - ", normalised)
        normalised = re.sub(r"(?<=\d)\s*\u2013\s*(?=\d)", " \u2013 ", normalised)
        # Commas are kept only inside appendix names ("Table 1 to Part 660, Subpart C").
        tokens = [t for t in SPACED_MARKER.sub("", normalised).split() if t != ","]
        if tokens and tokens[0].lower() == "title":
            tokens.pop(0)
        if not tokens or not tokens[0].strip(",").isdigit():
            raise InvalidCitationError(_no_title(source))
        citation = cls(int(tokens.pop(0).strip(",")))

        def take() -> str:
            return tokens.pop(0).strip(",")

        while tokens:
            raw = tokens[0]
            word = take()
            level = KEYWORDS.get(word.lower().rstrip("."))
            if "section" in citation.hierarchy and (
                word.lower() in LIST_WORDS or (not level and SECTION.fullmatch(word))
            ):
                first = citation.hierarchy["section"]
                message = _several(source, "lists", citation, first)
                raise InvalidCitationError(
                    f"{message} (in Python, Citation.parse_many splits a list)"
                )
            if level == "appendix" and tokens and not APPENDIX_NAME.match(tokens[0]):
                name = " ".join(["Appendix", *tokens]).strip(" ,")
                citation = _with_appendix(citation, name)
                tokens = []
            elif level == "section" and tokens:
                citation = citation.with_section(take())
            elif level and tokens:
                citation = citation.with_hierarchy({level: take()})
            elif level:
                raise InvalidCitationError(f"{word!r} needs a value in {source!r}")
            elif word.lower() == "of" and tokens:  # "Subpart E of Part 164".
                continue
            elif TIGHT_RANGE.fullmatch(word) or (
                SECTION.fullmatch(word)
                and len(tokens) > 1
                and tokens[0].lower() in RANGE_WORDS
                and SECTION.fullmatch(tokens[1].strip(","))
            ):
                message = _several(source, "is a range of", citation, word)
                raise InvalidCitationError(message)
            elif SECTION.fullmatch(word):
                ranged = len(tokens) > 1 and tokens[0] == "-"
                if ranged and SECTION.fullmatch(tokens[1].strip(",")):  # One section.
                    tokens.pop(0)
                    word = f"{word} - {take()}"
                citation = citation.with_section(word)
            elif NUMBER.fullmatch(word):
                citation = citation.with_hierarchy({"part": word})
            elif APPENDIX_NAME.match(rest := " ".join([raw, *tokens]).strip(" ,")):
                citation = _with_appendix(citation, rest)
                tokens = []
            else:
                raise InvalidCitationError(
                    f"Unexpected {word!r} in citation {source!r}"
                )
        return citation

    @classmethod
    def parse_many(cls, text: str | int) -> list[Self]:
        """Parse a citation that may list several sections.

        ``45 CFR §§ 164.502, 164.506`` and ``45 CFR 164.502 and 164.506(a)``
        give one citation per section, all in the same title. Ranges such as
        ``164.500-164.534`` are not expanded, since only the eCFR knows which
        sections lie between the ends: they raise :class:`InvalidCitationError`
        as in :meth:`parse`. Anything else is parsed as one citation.
        """
        source = _clean(str(text))
        listed = LIST.fullmatch(source.rstrip(".;:,"))
        if listed is None:
            return [cls.parse(source)]
        items = LIST_SEPARATOR.split(listed["items"])
        return [cls.parse(listed["head"] + item) for item in items]

    def with_hierarchy(self, hierarchy: Mapping[str, object]) -> Self:
        """Add hierarchy levels, rejecting any that contradict existing ones."""
        merged: dict[str, object] = dict(self.hierarchy)
        for level, value in hierarchy.items():
            if merged.get(level, value) != value:
                raise InvalidCitationError(
                    f"Conflicting {level}: {merged[level]!r} and {value!r} in {self}"
                )
            merged[level] = value
        return type(self)(self.title, as_hierarchy(merged), self.paragraph)

    def with_section(self, text: str) -> Self:
        """Add a section number and any trailing paragraph markers.

        The part isn't inferred from the number: in some parts sections are
        numbered differently (43 CFR Part 4100 holds § 4110.1).
        """
        match = SECTION.fullmatch(text)
        if match is None:
            raise InvalidCitationError(f"{text!r} is not a section number like 164.502")
        cited = self.with_hierarchy({"section": match["section"]})
        markers = tuple(PARAGRAPH.findall(match["paragraph"]))
        return type(self)(cited.title, cited.hierarchy, markers)

    def with_appendix(self, name: str) -> Self:
        """Add a full appendix name, plus its part if the name ends in one."""
        parts = APPENDIX_PART.findall(name)
        cited = self.with_hierarchy({"part": parts[-1]}) if parts else self
        return cited.with_hierarchy({"appendix": name})

    def __str__(self) -> str:
        h = self.hierarchy
        if "section" in h:
            return f"{self.title} CFR {h['section']}" + "".join(
                f"({marker})" for marker in self.paragraph
            )
        if "appendix" in h:
            return f"{self.title} CFR {h['appendix']}"
        shown = [
            k for k in LEVELS if k in h and not ("part" in h and k in UPPER_LEVELS)
        ]
        return " ".join([f"{self.title} CFR", *(f"{k.title()} {h[k]}" for k in shown)])


def _clean(text: str) -> str:
    """A pasted citation without surrounding quotes or invisible characters."""
    visible = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return visible.strip().strip(QUOTES).strip()


def _balanced(text: str) -> bool:
    """Whether every "(" in the text is closed, in order."""
    depth = 0
    for char in text:
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            return False
    return depth == 0


def _with_appendix[C: Citation](citation: C, name: str) -> C:
    """Add an appendix, completing "Appendix J" or "Supp. I" from the part."""
    name = re.sub(r"\bsubpt\b\.?", "Subpart", name, flags=re.IGNORECASE)
    name = re.sub(r"\bpt\b\.?", "Part", name, flags=re.IGNORECASE)
    short = SHORT_APPENDIX.fullmatch(name)
    part = citation.hierarchy.get("part")
    if short and part:
        subpart = citation.hierarchy.get("subpart")
        to = f"Subpart {subpart} of Part {part}" if subpart else f"Part {part}"
        name = f"{short[1].title()} {short[2]} to {to}"
    return citation.with_appendix(name)


def _several(source: str, kind: str, citation: Citation, section: str) -> str:
    """Explain that a range or list of sections isn't one citation."""
    part = citation.hierarchy.get("part") or section.split(".")[0]
    return (
        f"{source!r} {kind} several sections, but a citation names one. Cite the "
        f"part or subpart that contains them (e.g. {citation.title} CFR Part "
        f"{part}), or put one citation per line in a file and use --from FILE"
    )


def _no_title(source: str) -> str:
    """Explain that a citation needs its title, using the rest of it if possible."""
    rest = " ".join(source.replace("§", " ").split())
    title = "45"
    # "164.502 45 CFR": the title is there, just in the wrong place.
    if found := re.search(rf"(?<!\S)(\d+)\s*(?:{CFR.pattern})", rest, re.IGNORECASE):
        title = found[1]
        rest = " ".join((rest[: found.start()] + rest[found.end() :]).split())
    if rest and rest[0].isdigit() and "." in rest.split()[0]:
        return f"{rest!r} needs a title number first, e.g. {title} CFR {rest}"
    return f"A citation starts with a title number, e.g. 45 CFR 164.502; got {source!r}"
