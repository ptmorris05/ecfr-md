"""Bring saved Markdown up to date with the eCFR.

``sync`` reads the citation from a saved file's front matter (or from the top
``index.md`` of a saved tree), saves it again into a temporary directory and
compares. Text that hasn't changed comes from the cache, so an unchanged
title costs one small request. Only files whose text changed are rewritten;
files ecfr-md wrote for sections that no longer exist are deleted. Other
files are never touched.

Two kinds of saved file are kept as they are unless asked otherwise:
snapshots saved with a past date (front matter ``pinned``), and files edited
since they were saved (their text no longer matches the front matter
``digest``).

Files saved by ecfr-md 0.x have no ``layout`` in their front matter and
lowercase names ("subpart-e/section-164.502.md", "45-cfr-164.502.md"). They
are updated in place, under those names, unless ``migrate`` is given: then
they are renamed to today's layout ("subpart-E/164.502.md", "45_164.502.md").
Files the user named are never renamed.

Trees are saved with cross-references as links between their files (front
matter ``links``). A tree saved before that has no ``links`` field, so its
next sync rewrites every file once, adding the links; one saved with
``links=False`` stays without.
"""

from __future__ import annotations

import datetime as dt
import filecmp
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ecfr_md.address import Address
from ecfr_md.errors import InvalidParameterError
from ecfr_md.markdown import LAYOUT
from ecfr_md.output import (
    edited,
    is_ours,
    legacy_file_name,
    read_front_matter,
    remove_files,
    stale_files,
)

if TYPE_CHECKING:
    from ecfr_md.client import ECFR

# Front matter that changes on every download, even when the text doesn't.
VOLATILE = frozenset({"date", "retrieved", "api", "source"})


@dataclass
class SyncResult:
    """What ``sync`` did, or with ``dry_run`` would do, to each file.

    ``kept`` holds snapshots saved with a past date (files, or a tree's top
    folder), left at that date. ``modified`` holds files edited since they
    were saved, left as they are. ``moved`` holds (old, new) paths of files
    renamed to today's layout by ``migrate``.
    """

    added: list[Path] = field(default_factory=list)
    changed: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    moved: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass(frozen=True)
class _Options:
    dry_run: bool = False
    force: bool = False
    migrate: bool = False


def sync(
    ecfr: ECFR,
    path: Path,
    *,
    dry_run: bool = False,
    include_dated: bool = False,
    force: bool = False,
    migrate: bool = False,
) -> SyncResult:
    """Update a saved file or directory to the latest eCFR text."""
    result = SyncResult()
    options = _Options(dry_run, force, migrate)
    for root, tree in _roots(path):
        meta = read_front_matter(root / "index.md" if tree else root) or {}
        # Files saved since addresses have one; older ones have a citation.
        citation = meta.get("address") or meta.get("citation")
        if not citation or not str(meta.get("generator", "")).startswith("ecfr-md"):
            raise InvalidParameterError(f"{root} has no ecfr-md front matter to sync")
        old = int(meta.get("layout") or 1) < LAYOUT
        pinned = meta.get("pinned")
        renamed = None
        if migrate and not tree and old:
            renamed = _new_name(root, str(meta.get("citation")), pinned)
        if pinned and not include_dated:
            if renamed:  # Kept at its date, under its new name.
                _move(root, renamed, result, options)
            else:
                result.kept.append(root)
            continue
        layout = LAYOUT if migrate or not old else 1
        links = meta.get("links")  # Not recorded before: the default.
        with tempfile.TemporaryDirectory(prefix="ecfr-md-sync-") as tmp:
            target = Path(tmp) / root.name
            if tree:
                ecfr._save(citation, tmp, tree=True, layout=layout, links=links)
            else:
                ecfr._save(citation, target, tree=False, layout=layout, links=links)
            # The fresh copy's top folder may be named differently from a
            # folder the user renamed; compare by content, not by name.
            fresh = next(Path(tmp).iterdir()) if tree else target
            if tree and migrate and old:
                _migrate(fresh, root, result, options)
            elif renamed and not (edited(root) and not force):
                _merge(fresh, root, result, options)
                _move(root, renamed, result, options)
            else:
                _merge(fresh, root, result, options)
    return result


def _new_name(path: Path, citation: str, pinned: str | None) -> Path | None:
    """Today's name for a file ecfr-md 0.x named, or None if the user named it."""
    date = dt.date.fromisoformat(pinned) if pinned else None
    if path.name != legacy_file_name(citation, date):
        return None
    return path.with_name(replace(Address.parse(citation), date=date).file_name())


def _move(old: Path, new: Path, result: SyncResult, options: _Options) -> None:
    if not options.dry_run:
        os.replace(old, new)
    for found in (result.changed, result.unchanged):
        if old in found:
            found[found.index(old)] = new
    result.moved.append((old, new))


def _migrate(new: Path, old: Path, result: SyncResult, options: _Options) -> None:
    """Move a tree saved by ecfr-md 0.x to today's layout, updating its text.

    Files are matched by citation. Folders keep a name the user gave them;
    ones ecfr-md named are renamed. Old files go before new ones arrive, so
    on a case-insensitive file system "subpart-e" really becomes "subpart-E".
    """
    root = old.with_name(new.name) if _same_name(old.name, new.name) else old
    ours: dict[str, Path] = {}
    for path in sorted(old.rglob("*.md")):
        meta = read_front_matter(path) or {}
        if str(meta.get("generator", "")).startswith("ecfr-md") and meta.get(
            "citation"
        ):
            ours.setdefault(str(meta["citation"]), path)
    removed: list[Path] = []
    arriving: list[tuple[Path, Path]] = []
    for file in sorted(new.rglob("*.md")):
        destination = root / file.relative_to(new)
        citation = str((read_front_matter(file) or {}).get("citation"))
        prior = ours.pop(citation, None)
        if prior is not None and edited(prior) and not options.force:
            result.modified.append(prior)
            continue
        arriving.append((file, destination))
        if prior is None:
            result.added.append(destination)
        else:
            removed.append(prior)
            result.moved.append((prior, destination))
    for stale in ours.values():
        if edited(stale) and not options.force:
            result.modified.append(stale)
        else:
            removed.append(stale)
            result.removed.append(stale)
    if options.dry_run:
        return
    remove_files(removed, old)
    if root.name != old.name:  # Windows paths compare without case.
        old.rename(root)
    for file, destination in arriving:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, destination)


def _same_name(a: str, b: str) -> bool:
    """Whether two folder names differ only as old and new layouts name them."""
    return re.sub(r"[^a-z0-9]+", "-", a.casefold()) == re.sub(
        r"[^a-z0-9]+", "-", b.casefold()
    )


def _roots(path: Path) -> list[tuple[Path, bool]]:
    """Saved documents under ``path``: (location, is_tree)."""
    if path.is_file():
        return [(path, False)]
    if not path.is_dir():
        raise InvalidParameterError(f"{path} doesn't exist")
    roots = _find(path)
    if not roots:
        raise InvalidParameterError(f"No Markdown saved by ecfr-md in {path}")
    return roots


def _find(folder: Path) -> list[tuple[Path, bool]]:
    """Saved files and trees in ``folder`` or below it.

    A folder whose ``index.md`` ecfr-md wrote is a tree, synced as a whole;
    its subfolders are part of it, not trees of their own.
    """
    if is_ours(folder / "index.md"):
        return [(folder, True)]
    roots: list[tuple[Path, bool]] = []
    files: list[tuple[Path, bool]] = []
    for child in sorted(folder.iterdir()):
        if child.is_dir() and not child.is_symlink():
            roots += _find(child)
        elif child.suffix == ".md" and is_ours(child):
            files.append((child, False))
    return roots + files


def _merge(new: Path, old: Path, result: SyncResult, options: _Options) -> None:
    """Make ``old`` match ``new``, rewriting only files whose text differs."""
    if new.is_file():
        _update(new, old, result, options)
        return
    fresh = {old / p.relative_to(new) for p in new.rglob("*.md")}
    for file in sorted(fresh):
        _update(new / file.relative_to(old), file, result, options)
    removed = []
    for existing in stale_files(old, fresh):
        if edited(existing) and not options.force:
            result.modified.append(existing)
        else:
            removed.append(existing)
    result.removed += removed
    if not options.dry_run:
        remove_files(removed, old)


def _update(new: Path, old: Path, result: SyncResult, options: _Options) -> None:
    if not old.exists():
        if not options.dry_run:
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(new, old)
        result.added.append(old)
    elif filecmp.cmp(new, old, shallow=False) or _same_text(new, old):
        result.unchanged.append(old)
    elif edited(old) and not options.force:
        result.modified.append(old)
    else:
        if not options.dry_run:
            partial = old.with_name(f".{old.name}.part")
            shutil.copyfile(new, partial)
            os.replace(partial, old)
        result.changed.append(old)


def _same_text(a: Path, b: Path) -> bool:
    """Equal apart from front matter that changes on every download."""

    def stable(path: Path) -> tuple[dict, str]:
        text = path.read_text(encoding="utf-8")
        meta = read_front_matter(path) or {}
        body = text.split("\n---\n\n", 1)[-1] if meta else text
        return {k: v for k, v in meta.items() if k not in VOLATILE}, body

    try:
        return stable(a) == stable(b)
    except UnicodeDecodeError:  # Re-saved in another encoding: not ours as is.
        return False
