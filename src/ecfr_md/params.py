"""Typed parameters shared by several eCFR endpoints."""

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, TypedDict, cast

from cyclopts import Parameter, Token
from pydantic import BeforeValidator, Field

YEAR = re.compile(r"(19|20)\d\d")
ISO_DATE = re.compile(r"\d{4}-?\d{2}-?\d{2}$")
# Unambiguous for regulations: "March 2020" is 2020-03-01, "last month" is past.
DATEPARSER_SETTINGS = {
    "PREFER_DATES_FROM": "past",
    "PREFER_DAY_OF_MONTH": "first",
    "RETURN_AS_TIMEZONE_AWARE": False,
}


def to_date(value: object) -> dt.date:
    """Coerce anything date-like to a ``date``.

    Accepts ``date``, ``datetime`` (and subclasses such as pandas ``Timestamp``),
    objects with a ``.date()`` method (arrow, pendulum) and text in any format
    ``dateparser`` understands: ``"2024-01-05"``, ``"Jan 5, 2024"``,
    ``"5 January 2024"``, ``"01/05/2024"``, ``"yesterday"``, ``"3 weeks ago"``.
    """
    match value:
        case dt.datetime():
            return value.date()
        case dt.date():
            return value
        case str():
            return _parse_date(value)
    convert = getattr(value, "date", None)
    result = convert() if callable(convert) else None
    if isinstance(result, dt.date):
        return to_date(result)
    raise ValueError(f"expected a date, datetime or date string, got {value!r}")


def _parse_date(text: str) -> dt.date:
    text = text.strip()
    if YEAR.fullmatch(text):  # "2024": the start of the year, not this month in it.
        return dt.date(int(text), 1, 1)
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        # An invalid ISO date like 2025-16-01 is a typo, not a puzzle to solve.
        if ISO_DATE.match(text.strip()):
            raise ValueError(f"{text!r} is not a valid YYYY-MM-DD date") from None
        if text.isdigit():
            raise ValueError(f"{text!r} is not a date; use e.g. 2024-01-05") from None
    import dateparser  # Deferred: costs ~0.4s to import, and ISO dates skip it.

    parsed = dateparser.parse(text, settings=cast(Any, DATEPARSER_SETTINGS))
    if parsed is None:
        raise ValueError(f"could not understand the date {text!r}")
    return parsed.date()


def _convert_date(type_: type, tokens: Sequence[Token]) -> dt.date:
    return to_date(tokens[0].value)


# Statically a date or a string; at runtime also anything with a .date() method.
DateLike = Annotated[
    dt.date | str, BeforeValidator(to_date), Parameter(converter=_convert_date)
]


class Hierarchy(TypedDict, total=False):
    """Location within a CFR title. Omit everything to select the whole title.

    Attributes
    ----------
    subtitle: str
        Subtitle, an uppercase letter, e.g. 'A'.
    chapter: str
        Chapter, roman numerals or digits, e.g. 'I'.
    subchapter: str
        Subchapter, e.g. 'C'. Full text ignores this unless chapter is also given.
    part: str
        Part, e.g. '164'.
    subpart: str
        Subpart, e.g. 'E'.
    section: str
        Section, e.g. '164.502'.
    appendix: str
        Full appendix name, e.g. 'Appendix A to Subpart C of Part 164'.
    """

    subtitle: str
    chapter: str
    subchapter: str
    part: str
    subpart: str
    section: str
    appendix: str


def as_hierarchy(levels: Mapping[str, object]) -> Hierarchy:
    """A Hierarchy from a mapping of level names to identifiers."""
    return cast(Hierarchy, {level: str(value) for level, value in levels.items()})


class SearchFilters(TypedDict, total=False):
    """Filters accepted by every search endpoint.

    Attributes
    ----------
    agency_slugs: list[str]
        Limit to content associated with these agencies (see `admin agencies`).
    date: DateLike
        Limit to content present on this date.
    last_modified_after: DateLike
        Limit to content last modified after this date.
    last_modified_on_or_after: DateLike
        Limit to content last modified on or after this date.
    last_modified_before: DateLike
        Limit to content last modified before this date.
    last_modified_on_or_before: DateLike
        Limit to content last modified on or before this date.
    """

    agency_slugs: list[str]
    date: DateLike
    last_modified_after: DateLike
    last_modified_on_or_after: DateLike
    last_modified_before: DateLike
    last_modified_on_or_before: DateLike


class SearchOrder(StrEnum):
    CITATIONS = "citations"
    RELEVANCE = "relevance"
    HIERARCHY = "hierarchy"
    NEWEST_FIRST = "newest_first"
    OLDEST_FIRST = "oldest_first"
    SUGGESTIONS = "suggestions"


class PaginateBy(StrEnum):
    DATE = "date"
    RESULTS = "results"


PerPage = Annotated[int, Field(ge=1, le=1000)]
Page = Annotated[int, Field(ge=1)]
