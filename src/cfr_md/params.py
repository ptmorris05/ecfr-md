"""Typed keyword arguments shared by several eCFR endpoints."""

from datetime import date
from enum import StrEnum
from typing import Annotated, TypedDict

from pydantic import Field


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
        Appendix, e.g. 'A'. Also give the subtitle, chapter or part it belongs to.
    """

    subtitle: str
    chapter: str
    subchapter: str
    part: str
    subpart: str
    section: str
    appendix: str


class SearchFilters(TypedDict, total=False):
    """Filters accepted by every search endpoint.

    Attributes
    ----------
    agency_slugs: list[str]
        Limit to content associated with these agencies (see `admin agencies`).
    date: date
        Limit to content present on this date.
    last_modified_after: date
        Limit to content last modified after this date.
    last_modified_on_or_after: date
        Limit to content last modified on or after this date.
    last_modified_before: date
        Limit to content last modified before this date.
    last_modified_on_or_before: date
        Limit to content last modified on or before this date.
    """

    agency_slugs: list[str]
    date: date
    last_modified_after: date
    last_modified_on_or_after: date
    last_modified_before: date
    last_modified_on_or_before: date


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
