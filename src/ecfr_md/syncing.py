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
"""

from __future__ import annotations

import filecmp
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ecfr_md.errors import InvalidParameterError
from ecfr_md.output import (
    edited,
    is_ours,
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
    were saved, left as they are.
    """

    added: list[Path] = field(default_factory=list)
    changed: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class _Options:
    dry_run: bool = False
    force: bool = False


def sync(
    ecfr: ECFR,
    path: Path,
    *,
    dry_run: bool = False,
    include_dated: bool = False,
    force: bool = False,
) -> SyncResult:
    """Update a saved file or directory to the latest eCFR text."""
    result = SyncResult()
    options = _Options(dry_run, force)
    for root, tree in _roots(path):
        meta = read_front_matter(root / "index.md" if tree else root) or {}
        citation = meta.get("citation")
        if not citation or not str(meta.get("generator", "")).startswith("ecfr-md"):
            raise InvalidParameterError(f"{root} has no ecfr-md front matter to sync")
        if meta.get("pinned") and not include_dated:
            result.kept.append(root)
            continue
        with tempfile.TemporaryDirectory(prefix="ecfr-md-sync-") as tmp:
            target = Path(tmp) / root.name
            if tree:
                ecfr.save(citation, tmp, tree=True)
            else:
                ecfr.save(citation, target)
            # The fresh copy's top folder may be named differently from a
            # folder the user renamed; compare by content, not by name.
            fresh = next(Path(tmp).iterdir()) if tree else target
            _merge(fresh, root, result, options)
    return result


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
