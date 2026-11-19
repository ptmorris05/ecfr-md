"""Module-level functions for scripts: ``ecfr_md.get("45 CFR 164.502")``.

They share one :class:`~ecfr_md.client.ECFR` with default settings, created on
first use and closed at exit. For other settings, use ``ECFR(Settings(...))``.

Each function has its method's docstring, copied at import so the two can't
drift; signatures are written out so type checkers see them.
"""

import atexit
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Unpack, overload

from ecfr_md.client import ECFR
from ecfr_md.document import Node
from ecfr_md.params import DateLike, Hierarchy, SearchFilters
from ecfr_md.results import Change, Matches, Outline
from ecfr_md.syncing import SyncResult

_shared: ECFR | None = None
_lock = threading.Lock()


def _documented_by[F: Callable[..., object]](
    method: Callable[..., object],
) -> Callable[[F], F]:
    """Give a function ``method``'s docstring, parameter docs included."""

    def apply(func: F) -> F:
        func.__doc__ = method.__doc__
        return func

    return apply


def client() -> ECFR:
    """The client the module-level functions use."""
    global _shared
    with _lock:
        if _shared is None:
            _shared = ECFR()
            atexit.register(_shared.close)
        return _shared


@_documented_by(ECFR.get)
def get(
    citation: str | int,
    /,
    *,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Node:
    return client().get(citation, date=date, **hierarchy)


@_documented_by(ECFR.get_many)
def get_many(citations: list[str | int], *, date: DateLike | None = None) -> list[Node]:
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


@_documented_by(ECFR.save)
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
    shared = client()
    if tree:
        return shared.save(
            citation, path, tree=True, front_matter=front_matter, date=date, **hierarchy
        )
    return shared.save(
        citation, path, front_matter=front_matter, date=date, **hierarchy
    )


@_documented_by(ECFR.outline)
def outline(
    citation: str | int,
    /,
    *,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Outline:
    return client().outline(citation, date=date, **hierarchy)


@_documented_by(ECFR.changes)
def changes(
    citation: str | int,
    /,
    *,
    since: DateLike | None = None,
    until: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> list[Change]:
    return client().changes(citation, since=since, until=until, **hierarchy)


@_documented_by(ECFR.search)
def search(
    query: str,
    /,
    *,
    limit: int = 20,
    phrase: bool = True,
    all_versions: bool = False,
    **filters: Unpack[SearchFilters],
) -> Matches:
    return client().search(
        query, limit=limit, phrase=phrase, all_versions=all_versions, **filters
    )


@_documented_by(ECFR.sync)
def sync(path: str | Path) -> SyncResult:
    return client().sync(path)
