"""Parse and format CFR citations such as ``45 CFR 164.502(a)(1)(i)(A)``."""

import re
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
# Part numbers: "164", "26a", or Title 41's "109-46".
NUMBER = re.compile(r"\d+[A-Za-z]?(?:-\d+[A-Za-z]?)*")
# Trailing "(a)(1)" groups are paragraphs; earlier ones belong to the section
# number itself, as in Title 26's "1.401(k)-1".
SECTION = re.compile(r"(?P<section>\d[\w-]*\..+?)(?P<paragraph>(?:\(\w+\))*)")
RANGE_WORDS = frozenset({"-", "\u2013", "\u2014", "through"})
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
        """
        source = str(text)
        cfr = CFR.search(source)
        if cfr and not source[: cfr.start()].strip():
            raise InvalidCitationError(_no_title(source[cfr.end() :]))
        normalised = CFR.sub(" ", source).replace("§", " ")
        normalised = normalised.strip().rstrip(".;:,")  # A citation ending a sentence.
        # Section ranges like "210.3-07—210.3-08" are identified as "X - Y".
        normalised = re.sub(r"(?<=\d)\s*[\u2013\u2014]\s*(?=\d)", " - ", normalised)
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
            if level == "appendix" and tokens and not APPENDIX_NAME.match(tokens[0]):
                name = " ".join(["Appendix", *tokens]).strip(" ,")
                citation = citation.with_appendix(name)
                tokens = []
            elif level == "section" and tokens:
                citation = citation.with_section(take())
            elif level and tokens:
                citation = citation.with_hierarchy({level: take()})
            elif level:
                raise InvalidCitationError(f"{word!r} needs a value in {source!r}")
            elif SECTION.fullmatch(word):
                ranged = len(tokens) > 1 and tokens[0] in RANGE_WORDS
                if ranged and SECTION.fullmatch(tokens[1].strip(",")):  # A range.
                    tokens.pop(0)
                    word = f"{word} - {take()}"
                citation = citation.with_section(word)
            elif NUMBER.fullmatch(word):
                citation = citation.with_hierarchy({"part": word})
            elif APPENDIX_NAME.match(rest := " ".join([raw, *tokens]).strip(" ,")):
                citation = citation.with_appendix(rest)
                tokens = []
            else:
                raise InvalidCitationError(
                    f"Unexpected {word!r} in citation {source!r}"
                )
        return citation

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


def _no_title(source: str) -> str:
    """Explain that a citation needs its title, using the rest of it if possible."""
    rest = " ".join(source.replace("§", " ").split())
    if rest and rest[0].isdigit() and "." in rest.split()[0]:
        return f"{rest!r} needs a title number first, e.g. 45 CFR {rest}"
    return f"A citation starts with a title number, e.g. 45 CFR 164.502; got {source!r}"
