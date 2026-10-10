"""On-disk store of regulation text, reused until the eCFR changes it.

Every entry records the date its text is for and the title's
``latest_issue_date`` when it was downloaded (its "token"). Any change to the
eCFR, including corrections applied to past dates, is issued after that token,
so one request for "versions issued since the token" shows which entries are
still current. Entries are written to a temporary file and renamed, so an
interrupted download leaves no partial entry.
"""

import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from ecfr_md.params import Hierarchy


def default_directory() -> Path:
    """The platform's user cache directory for ecfr-md."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ecfr-md"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "ecfr-md" / "Cache"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ecfr-md"


@dataclass(frozen=True)
class Entry:
    path: Path
    date: dt.date  # The date the text is for.
    token: dt.date  # The title's latest_issue_date when it was downloaded.
    url: str
    retrieved: dt.datetime


class Store:
    def __init__(self, directory: Path | None, max_age: float | None = None) -> None:
        self.directory = directory.expanduser() if directory else None
        self.max_age = max_age

    def find(
        self, kind: str, title: int, hierarchy: Hierarchy, date: dt.date
    ) -> list[Entry]:
        """Entries that may hold this text: one for the exact date, and the latest."""
        if self.directory is None:
            return []
        keys = [_key(kind, title, hierarchy, date), _key(kind, title, hierarchy, None)]
        return [entry for key in keys if (entry := self._read(key)) is not None]

    def put(
        self,
        kind: str,
        title: int,
        hierarchy: Hierarchy,
        source: Path,
        *,
        date: dt.date,
        token: dt.date,
        latest: bool,
        url: str,
        retrieved: dt.datetime,
    ) -> Entry | None:
        """Copy a downloaded file into the store; ``latest`` also files it as such."""
        if self.directory is None:
            return None
        keys = [_key(kind, title, hierarchy, date)]
        if latest:
            keys.append(_key(kind, title, hierarchy, None))
        entry = None
        for key in keys:
            path = self.directory / key
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
            try:
                try:  # The second key shares the first's file where possible.
                    os.link(entry.path if entry else source, partial)
                except OSError:
                    shutil.copyfile(source, partial)
                os.replace(partial, path)
            finally:
                partial.unlink(missing_ok=True)
            entry = Entry(path, date, token, url, retrieved)
            _write_meta(entry)
        return entry

    def refresh(self, entry: Entry, token: dt.date) -> Entry:
        """Record that an entry is still current as of a newer token."""
        refreshed = Entry(entry.path, entry.date, token, entry.url, entry.retrieved)
        _write_meta(refreshed)
        return refreshed

    def info(self) -> tuple[int, int]:
        """Number of entries and their total size in bytes."""
        if self.directory is None or not self.directory.exists():
            return 0, 0
        files = [p for p in self.directory.rglob("*") if p.is_file() and not p.suffix]
        inodes = {(p.stat().st_dev, p.stat().st_ino): p.stat().st_size for p in files}
        return len(files), sum(inodes.values())  # Linked files counted once.

    def clear(self) -> None:
        if self.directory is not None and self.directory.exists():
            shutil.rmtree(self.directory)

    def _read(self, key: str) -> Entry | None:
        assert self.directory is not None
        path = self.directory / key
        try:
            meta = json.loads(path.with_suffix(".json").read_text())
            age = dt.datetime.now().timestamp() - path.stat().st_mtime
        except (FileNotFoundError, ValueError):
            return None
        if self.max_age is not None and age > self.max_age:
            return None
        return Entry(
            path,
            dt.date.fromisoformat(meta["date"]),
            dt.date.fromisoformat(meta["token"]),
            meta["url"],
            dt.datetime.fromisoformat(meta["retrieved"]),
        )


def _key(kind: str, title: int, hierarchy: Hierarchy, date: dt.date | None) -> str:
    """A file name for a piece of text: title-45/<hash of hierarchy and date>."""
    where = json.dumps(sorted(hierarchy.items()))
    when = date.isoformat() if date else "latest"
    digest = hashlib.sha256(f"{kind}|{where}|{when}".encode()).hexdigest()[:32]
    return f"title-{title}/{digest}"


def _write_meta(entry: Entry) -> None:
    meta = {
        "date": entry.date.isoformat(),
        "token": entry.token.isoformat(),
        "url": entry.url,
        "retrieved": entry.retrieved.isoformat(),
    }
    path = entry.path.with_suffix(".json")
    partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    partial.write_text(json.dumps(meta))
    os.replace(partial, path)
