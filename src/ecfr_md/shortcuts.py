"""Module-level functions for scripts: ``ecfr_md.get("45 CFR 164.502")``.

They share one :class:`~ecfr_md.client.ECFR`, created on first use with the
settings from the config file and ``ECFR_MD_*`` variables, and closed at exit.
:func:`configure` changes its settings; or use ``ECFR(Settings(...))``.

Each function has its method's docstring, copied at import so the two can't
drift; signatures are written out so type checkers see them.
"""

import atexit
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Unpack, overload

from ecfr_md.client import ECFR
from ecfr_md.compare import Comparison
from ecfr_md.config import Settings
from ecfr_md.definitions import Definitions
from ecfr_md.document import Node
from ecfr_md.params import DateLike, Hierarchy, SearchFilters
from ecfr_md.passages import QuoteCheck
from ecfr_md.render.formats import Format
from ecfr_md.results import Change, Matches, Outline, TitleInfo
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
            atexit.register(_close)
        return _shared


def configure(settings: Settings | None = None, /, **fields: Any) -> ECFR:
    """Change the settings of the client the module-level functions share.

    Give a :class:`~ecfr_md.config.Settings`, or settings by name, which win
    over ``ECFR_MD_*`` variables and the config file, as with
    ``Settings.load(**fields)``::

        ecfr_md.configure(site_url="https://mirror.example", source="api")

    The client in use before is closed. Returns the new one.
    """
    global _shared
    new = ECFR(settings if settings is not None else Settings.load(**fields))
    with _lock:
        old, _shared = _shared, new
    if old is None:
        atexit.register(_close)
    else:
        old.close()
    return new


def _close() -> None:
    if _shared is not None:
        _shared.close()


@_documented_by(ECFR.titles)
def titles() -> list[TitleInfo]:
    return client().titles()


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
def get_many(
    citations: Sequence[str | int], *, date: DateLike | None = None
) -> list[Node]:
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
    format: Format | None = None,
    links: bool | None = None,
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
    format: Format | None = None,
    links: bool | None = None,
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
    format: Format | None = None,
    links: bool | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Path | list[Path]:
    shared = client()
    if tree:
        return shared.save(
            citation,
            path,
            tree=True,
            front_matter=front_matter,
            date=date,
            format=format,
            links=links,
            **hierarchy,
        )
    return shared.save(
        citation,
        path,
        front_matter=front_matter,
        date=date,
        format=format,
        links=links,
        **hierarchy,
    )


@_documented_by(ECFR.outline)
def outline(
    citation: str | int | None = None,
    /,
    *,
    date: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Outline:
    return client().outline(citation, date=date, **hierarchy)


@_documented_by(ECFR.compare)
def compare(
    citation: str | int,
    /,
    since: DateLike,
    until: DateLike | None = None,
    **hierarchy: Unpack[Hierarchy],
) -> Comparison:
    return client().compare(citation, since, until, **hierarchy)


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


@_documented_by(ECFR.cited_by)
def cited_by(
    citation: str | int, /, *, limit: int = 20, date: DateLike | None = None
) -> Matches:
    return client().cited_by(citation, limit=limit, date=date)


@_documented_by(ECFR.define)
def define(
    term: str, citation: str | int, /, *, date: DateLike | None = None
) -> Definitions:
    return client().define(term, citation, date=date)


@_documented_by(ECFR.verify_quote)
def verify_quote(
    citation: str | int, /, text: str, *, date: DateLike | None = None
) -> QuoteCheck:
    return client().verify_quote(citation, text, date=date)


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
def sync(
    path: str | Path,
    *,
    dry_run: bool = False,
    include_dated: bool = False,
    force: bool = False,
    migrate: bool = False,
) -> SyncResult:
    return client().sync(
        path,
        dry_run=dry_run,
        include_dated=include_dated,
        force=force,
        migrate=migrate,
    )
