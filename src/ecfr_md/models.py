"""Response models for the eCFR API.

Unknown fields are kept (``extra="allow"``), so fields the API adds later
don't cause errors; they are in ``model.model_extra``.
"""

from __future__ import annotations

import datetime as dt
import json
import zlib
from typing import Any

from pydantic import BaseModel, ConfigDict


class Model(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


# Admin service


class CFRReference(Model):
    title: int
    subtitle: str | None = None
    chapter: str | None = None
    subchapter: str | None = None
    part: str | None = None


class Agency(Model):
    name: str
    short_name: str | None = None
    display_name: str
    sortable_name: str
    slug: str
    children: list[Agency] = []
    cfr_references: list[CFRReference] = []


class Agencies(Model):
    agencies: list[Agency]


class CorrectionHierarchy(Model):
    title: str
    subtitle: str | None = None
    chapter: str | None = None
    subchapter: str | None = None
    part: str | None = None
    subpart: str | None = None
    section: str | None = None
    appendix: str | None = None


class CorrectionReference(Model):
    cfr_reference: str
    hierarchy: CorrectionHierarchy


class Correction(Model):
    id: int
    cfr_references: list[CorrectionReference]
    corrective_action: str
    error_corrected: dt.date
    error_occurred: dt.date | None = None
    fr_citation: str
    position: int | None = None
    display_in_toc: bool
    title: int
    year: int
    last_modified: dt.date


class Corrections(Model):
    ecfr_corrections: list[Correction]


# Search service


class SearchHierarchy(Model):
    title: str | None = None
    subtitle: str | None = None
    chapter: str | None = None
    subchapter: str | None = None
    part: str | None = None
    subpart: str | None = None
    subject_group: str | None = None
    section: str | None = None
    appendix: str | None = None


class SearchResult(Model):
    starts_on: dt.date
    ends_on: dt.date | None = None
    type: str
    hierarchy: SearchHierarchy
    hierarchy_headings: SearchHierarchy
    headings: SearchHierarchy
    full_text_excerpt: str | None = None
    score: float
    structure_index: int
    reserved: bool
    removed: bool
    change_types: list[str] = []


class SearchMeta(Model):
    description: str | None = None
    current_page: int | None = None
    total_count: int | None = None
    total_pages: int | None = None
    max_score: float | None = None
    min_date: dt.date | None = None
    max_date: dt.date | None = None


class SearchResults(Model):
    results: list[SearchResult]
    meta: SearchMeta


class SearchCount(Model):
    meta: SearchMeta


class SearchSummary(Model):
    meta: SearchMeta


class DailyCounts(Model):
    dates: dict[dt.date, int]


class TitleCounts(Model):
    titles: dict[int, int]


class TotalCount(Model):
    value: int
    relation: str


class HierarchyCount(Model):
    level: str
    hierarchy: str | None = None
    hierarchy_heading: str | None = None
    heading: str | None = None
    structure_index: int | None = None
    count: int
    max_score: float | None = None
    children: list[HierarchyCount] = []


class HierarchyCounts(Model):
    count: TotalCount
    max_score: float | None = None
    shown_count: int | None = None
    children: list[HierarchyCount] = []


class Suggestions(Model):
    suggestions: list[object]


# Versioner service


class Title(Model):
    number: int
    name: str
    latest_amended_on: dt.date | None = None
    latest_issue_date: dt.date | None = None
    up_to_date_as_of: dt.date | None = None
    reserved: bool
    processing_in_progress: bool = False


class TitlesMeta(Model):
    date: dt.date | None = None
    import_in_progress: bool = False


class Titles(Model):
    titles: list[Title]
    meta: TitlesMeta | None = None


class Node(Model):
    """A node of a title's table of contents (title, part, section, ...).

    Heading-only nodes (``type`` like ``"hed1"``) have no identifier.
    """

    identifier: str | None = None
    label: str
    label_level: str | None = None
    label_description: str | None = None
    reserved: bool = False
    type: str
    size: int | None = None
    volumes: list[str] = []
    descendant_range: str | None = None
    received_on: dt.datetime | None = None
    generated_id: bool = False
    children: list[Node] = []


class Ancestry(Model):
    ancestors: list[Node]


class ContentVersion(Model):
    date: dt.date
    amendment_date: dt.date
    issue_date: dt.date
    identifier: str
    name: str
    part: str
    substantive: bool
    removed: bool
    subpart: str | None = None
    title: str
    type: str


class Versions(Model):
    content_versions: list[ContentVersion]
    meta: dict[str, object] = {}


class Structure:
    """A lean node of a title's table of contents, for keeping in memory.

    A :class:`Node` per entry of a large title takes over 50 MB; this keeps
    only what navigation needs, and the response compressed, so ``raw()``
    can give the full :class:`Node` when asked.
    """

    __slots__ = (
        "_source",
        "children",
        "identifier",
        "label",
        "parent",
        "reserved",
        "size",
        "type",
    )

    def __init__(
        self, data: dict[str, Any], parent: Structure | None, source: bytes
    ) -> None:
        identifier, label, kind = data.get("identifier"), data["label"], data["type"]
        size = data.get("size")
        if not isinstance(label, str) or not isinstance(kind, str):
            raise ValueError("A structure node needs a label and a type")
        self.identifier = None if identifier is None else str(identifier)
        self.label = label
        self.type = kind
        self.reserved = bool(data.get("reserved", False))
        self.size = int(size) if size is not None else None
        self.parent = parent
        self._source = source
        self.children = tuple(
            Structure(child, self, source) for child in data.get("children") or ()
        )

    @classmethod
    def parse(cls, body: bytes) -> Structure:
        """Read the structure endpoint's JSON; raises ValueError if malformed."""
        try:
            return cls(json.loads(body), None, zlib.compress(body))
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Unexpected structure: {exc!r}") from exc

    @classmethod
    def of(cls, node: Node) -> Structure:
        """The lean form of a full node."""
        return cls.parse(node.model_dump_json(exclude_unset=True).encode())

    def raw(self) -> Node:
        """This entry as the API gave it, with everything below it."""
        steps: list[int] = []
        node = self
        while node.parent is not None:
            steps.append(_position(node.parent.children, node))
            node = node.parent
        data = json.loads(zlib.decompress(self._source))
        for step in reversed(steps):
            data = data["children"][step]
        return Node.model_validate(data)


def _position(siblings: tuple[Structure, ...], node: Structure) -> int:
    return next(i for i, sibling in enumerate(siblings) if sibling is node)
