"""Module-level functions for scripts: ``ecfr_md.get("45 CFR 164.502")``.

They share one :class:`~ecfr_md.client.ECFR` with default settings, created on
first use and closed at exit. For other settings, use ``ECFR(Settings(...))``.
"""

import atexit
import threading
from pathlib import Path
from typing import Literal, Unpack, overload

from ecfr_md.client import ECFR
from ecfr_md.document import Node
from ecfr_md.params import DateLike, Hierarchy, SearchFilters
from ecfr_md.results import Change, Matches, Outline
from ecfr_md.syncing import SyncResult

_shared: ECFR | None = None
_lock = threading.Lock()


def client() -> ECFR:
    """The client the module-level functions use."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = ECFR()
            atexit.register(_shared.close)
        return _shared


def get(
    citation: str | int,
    /,
    *,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Node:
    """Regulation text as a navigable node. See :meth:`ECFR.get`."""
    return client().get(citation, date=date, **hierarchy)


def get_many(citations: list[str | int], *, date: DateLike | None = None) -> list[Node]:
    """Several citations, sharing requests where possible. See :meth:`ECFR.get_many`."""
    return client().get_many(citations, date=date)


@overload
def save(
    citation: str | int,
    /,
    path: str | Path,
    *,
    tree: Literal[False] = False,
    front_matter: bool = True,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Path: ...


@overload
def save(
    citation: str | int,
    /,
    path: str | Path,
    *,
    tree: Literal[True],
    front_matter: bool = True,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> list[Path]: ...


def save(
    citation: str | int,
    /,
    path: str | Path,
    *,
    tree: bool = False,
    front_matter: bool = True,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Path | list[Path]:
    """Save regulation text as Markdown. See :meth:`ECFR.save`."""
    shared = client()
    if tree:
        return shared.save(
            citation, path, tree=True, front_matter=front_matter, date=date, **hierarchy
        )
    return shared.save(
        citation, path, front_matter=front_matter, date=date, **hierarchy
    )


def outline(
    citation: str | int,
    /,
    *,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Outline:
    """The table of contents, without text. See :meth:`ECFR.outline`."""
    return client().outline(citation, date=date, **hierarchy)


def changes(
    citation: str | int,
    /,
    *,
    since: DateLike | None = None,
    until: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> list[Change]:
    """Changes the eCFR has issued, newest first. See :meth:`ECFR.changes`."""
    return client().changes(citation, since=since, until=until, **hierarchy)


def search(
    query: str,
    /,
    *,
    limit: int = 20,
    phrase: bool = True,
    all_versions: bool = False,
    **filters: Unpack[SearchFilters],
) -> Matches:
    """Sections and appendices that match a search. See :meth:`ECFR.search`."""
    return client().search(
        query, limit=limit, phrase=phrase, all_versions=all_versions, **filters
    )


def sync(path: str | Path) -> SyncResult:
    """Update Markdown saved by ecfr-md. See :meth:`ECFR.sync`."""
    return client().sync(path)
