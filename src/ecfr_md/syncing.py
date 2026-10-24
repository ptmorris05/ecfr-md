"""Bring saved Markdown up to date with the eCFR.

``sync`` reads the citation from a saved file's front matter (or from the top
``index.md`` of a saved tree), saves it again into a temporary directory and
compares. Text that hasn't changed comes from the cache, so an unchanged
title costs one small request. Only files whose text changed are rewritten;
files ecfr-md wrote for sections that no longer exist are deleted. Other
files are never touched.
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
from ecfr_md.output import read_front_matter

if TYPE_CHECKING:
    from ecfr_md.client import ECFR

# Front matter that changes on every download, even when the text doesn't.
VOLATILE = frozenset({"date", "retrieved", "api", "source"})


@dataclass
class SyncResult:
    added: list[Path] = field(default_factory=list)
    changed: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


def sync(ecfr: ECFR, path: Path) -> SyncResult:
    """Update a saved file or directory to the latest eCFR text."""
    result = SyncResult()
    for root, tree in _roots(path):
        meta = read_front_matter(root / "index.md" if tree else root) or {}
        citation = meta.get("citation")
        if not citation or not str(meta.get("generator", "")).startswith("ecfr-md"):
            raise InvalidParameterError(f"{root} has no ecfr-md front matter to sync")
        with tempfile.TemporaryDirectory(prefix="ecfr-md-sync-") as tmp:
            target = Path(tmp) / root.name
            if tree:
                ecfr.save(citation, tmp, tree=True)
            else:
                ecfr.save(citation, target)
            # The fresh copy's top folder may be named differently from a
            # folder the user renamed; compare by content, not by name.
            fresh = next(Path(tmp).iterdir()) if tree else target
            _merge(fresh, root, result)
    return result


def _roots(path: Path) -> list[tuple[Path, bool]]:
    """Saved documents under ``path``: (location, is_tree)."""
    if path.is_file():
        return [(path, False)]
    if not path.is_dir():
        raise InvalidParameterError(f"{path} doesn't exist")
    roots = [(d, True) for d in sorted(path.iterdir()) if _ours(d / "index.md")]
    roots += [(f, False) for f in sorted(path.glob("*.md")) if _ours(f)]
    if not roots:
        raise InvalidParameterError(f"No Markdown saved by ecfr-md in {path}")
    return roots


def _merge(new: Path, old: Path, result: SyncResult) -> None:
    """Make ``old`` match ``new``, rewriting only files whose text differs."""
    if new.is_file():
        _update(new, old, result)
        return
    fresh = {p.relative_to(new) for p in new.rglob("*.md")}
    for relative in sorted(fresh):
        _update(new / relative, old / relative, result)
    for existing in sorted(old.rglob("*.md")):
        if existing.relative_to(old) not in fresh and _ours(existing):
            existing.unlink()
            result.removed.append(existing)
    for directory in sorted(old.rglob("*"), reverse=True):  # Deepest first.
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def _update(new: Path, old: Path, result: SyncResult) -> None:
    if not old.exists():
        old.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(new, old)
        result.added.append(old)
    elif filecmp.cmp(new, old, shallow=False) or _same_text(new, old):
        result.unchanged.append(old)
    else:
        partial = old.with_name(f".{old.name}.part")
        shutil.copyfile(new, partial)
        os.replace(partial, old)
        result.changed.append(old)


def _same_text(a: Path, b: Path) -> bool:
    """Equal apart from front matter that changes on every download."""

    def stable(path: Path) -> tuple[dict, str]:
        text = path.read_text(encoding="utf-8")
        meta = read_front_matter(path) or {}
        body = text.split("\n---\n\n", 1)[1] if meta else text
        return {k: v for k, v in meta.items() if k not in VOLATILE}, body

    try:
        return stable(a) == stable(b)
    except UnicodeDecodeError:  # Re-saved in another encoding: not ours as is.
        return False


def _ours(path: Path) -> bool:
    meta = read_front_matter(path)
    return bool(meta) and str(meta.get("generator", "")).startswith("ecfr-md")
