"""What changed in a piece of the CFR between two dates.

The two texts are lined up block by block (a paragraph, a table, a note),
so a change shows where it is; changed blocks are then compared word by word.
"""

from __future__ import annotations

import datetime as dt
import difflib
import html
import re
from dataclasses import dataclass, field
from typing import Any, Literal

WORDS = re.compile(r"\s+|[^\s]+")
ALIKE = 0.5  # How alike two blocks must be to count as one changed block.


@dataclass(frozen=True)
class Difference:
    """One block added, removed or changed."""

    kind: Literal["added", "removed", "changed", "unchanged"]
    """What happened to the block; "unchanged" only from :func:`aligned`."""
    before: str
    """The block's Markdown before; empty if added."""
    after: str
    """The block's Markdown after; empty if removed."""

    @property
    def words(self) -> list[tuple[Literal["=", "-", "+"], str]]:
        """The change word by word: kept ("="), deleted ("-"), inserted ("+")."""
        old, new = WORDS.findall(self.before), WORDS.findall(self.after)
        result: list[tuple[Literal["=", "-", "+"], str]] = []
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                result.append(("=", "".join(old[i1:i2])))
                continue
            if i2 > i1:
                result.append(("-", "".join(old[i1:i2])))
            if j2 > j1:
                result.append(("+", "".join(new[j1:j2])))
        return result

    def to_markdown(self) -> str:
        """The block with deletions in <del> and insertions in <ins>."""
        tags = {"-": ("<del>", "</del>"), "+": ("<ins>", "</ins>")}
        parts = []
        for op, text in self.words:
            if op == "=":
                parts.append(text)
            else:
                start, end = tags[op]
                parts.append(f"{start}{html.escape(text, quote=False)}{end}")
        return "".join(parts)


@dataclass(frozen=True)
class Comparison:
    """The differences in one citation's text between two dates."""

    citation: str
    since: dt.date
    until: dt.date
    differences: list[Difference] = field(default_factory=list)
    unchanged: int = 0
    """Blocks that are the same on both dates."""

    def __bool__(self) -> bool:
        return bool(self.differences)

    def to_markdown(self) -> str:
        """A redline: each changed block, with what was deleted and inserted."""
        head = f"# {self.citation}: {self.since} to {self.until}"
        if not self.differences:
            return f"{head}\n\nNo changes.\n"
        blocks = [head, *(d.to_markdown() for d in self.differences)]
        return "\n\n".join(blocks) + "\n"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready: dates, counts and every difference with its words."""
        return {
            "citation": self.citation,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "unchanged": self.unchanged,
            "differences": [
                {
                    "kind": d.kind,
                    "before": d.before,
                    "after": d.after,
                    "words": [{"op": op, "text": text} for op, text in d.words],
                }
                for d in self.differences
            ],
        }


def compare(
    citation: str, before: str, after: str, since: dt.date, until: dt.date
) -> Comparison:
    """Compare two Markdown texts of the same citation."""
    blocks = aligned(before, after)
    differences = [b for b in blocks if b.kind != "unchanged"]
    unchanged = len(blocks) - len(differences)
    return Comparison(citation, since, until, differences, unchanged)


def aligned(before: str, after: str) -> list[Difference]:
    """Every block of two Markdown texts in order, the unchanged ones too:
    the whole text as a redline, as a reader sees it."""
    old, new = _blocks(before), _blocks(after)
    blocks: list[Difference] = []
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            blocks += [Difference("unchanged", b, b) for b in old[i1:i2]]
            continue
        blocks += _pair(old[i1:i2], new[j1:j2])
    return blocks


def _pair(removed: list[str], added: list[str]) -> list[Difference]:
    """Match old blocks to the new blocks they became, in document order.

    Inserting a paragraph shifts the ones after it, so blocks are paired by
    how alike they are, not by position; a block too unlike any other was
    removed (or added) rather than changed.
    """
    result: list[Difference] = []
    start = 0  # New blocks before this one are already accounted for.
    for before in removed:
        scores = [
            (difflib.SequenceMatcher(None, before, after).ratio(), j)
            for j, after in enumerate(added[start:], start)
        ]
        best, j = max(scores, default=(0.0, -1))
        if best < ALIKE:
            result.append(Difference("removed", before, ""))
            continue
        result += [Difference("added", "", b) for b in added[start:j]]
        result.append(Difference("changed", before, added[j]))
        start = j + 1
    result += [Difference("added", "", b) for b in added[start:]]
    return result


def _blocks(markdown: str) -> list[str]:
    return [b.strip() for b in markdown.split("\n\n") if b.strip()]
