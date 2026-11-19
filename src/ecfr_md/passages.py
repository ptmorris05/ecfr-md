"""Text sized for a reader with a budget, and quotes checked against it.

Built for AI agents (the MCP server uses it) but plain SDK code:

- :func:`passage` cuts a node's Markdown into the slice that fits a token
  budget, ending at a block boundary, with a cursor to continue from. It
  never cuts silently: the passage says what it covers and what is left.
- :class:`TextIndex` knows which paragraph every character of a node's text
  is in, so a slice or a quote gets its pinpoint citation.
- :func:`check_quote` says whether a quote is in the text, where, and if
  not, the nearest passage and how alike they are.

Token counts are estimates: four characters to a token, which is close for
English regulation text with the usual tokenizers.
"""

from __future__ import annotations

import bisect
import difflib
import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from ecfr_md.compare import Difference
from ecfr_md.document import Level, Node, Paragraph
from ecfr_md.errors import InvalidParameterError

if TYPE_CHECKING:
    from ecfr_md.results import Outline

CHARS_PER_TOKEN = 4
# Outline sizes are of the eCFR's XML, of which the Markdown is about 95%.
MARKDOWN_PER_XML_BYTE = 0.95
MARKUP = frozenset("*#>\\_`")
SAME = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)
# Paragraph labels where a paragraph starts: **(a)**, **(e)(1)**, (*1*).
LABELS = re.compile(r"(?:\*\*)?(?:\(\*?[A-Za-z0-9]{1,6}\*?\))*(?:\*\*)?")
QUOTED_MARKERS = re.compile(r"^\s*(?:\([A-Za-z0-9]{1,6}\)\s*)+")
WORD = re.compile(r"\S+")
# A slice may end early, before a heading, to keep a section whole.
KEEP_WHOLE = 0.6
NEAREST_CANDIDATES = 24
FOUND_SIMILARITY = 0.5  # Below this, nothing near enough is reported.


def tokens(text: str) -> int:
    """Estimated tokens in ``text``."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def outline_tokens(entry: Outline) -> int | None:
    """Estimated tokens in an outline entry's text, from the eCFR's sizes;
    None if the eCFR gives no size for it or anything in it."""
    size = outline_size(entry)
    return None if size is None else math.ceil(size * MARKDOWN_PER_XML_BYTE / 4)


def outline_size(entry: Outline) -> int | None:
    """Bytes of XML in an entry: its own size, else its children's."""
    if entry.size is not None:
        return entry.size
    if entry._children is None:  # A title of the whole CFR not yet opened.
        return None
    sizes = [s for c in entry.children if (s := outline_size(c)) is not None]
    return sum(sizes) if sizes else None


def normalize(text: str) -> tuple[str, list[int]]:
    """Text without Markdown markup, with curly quotes and dashes made plain
    and whitespace collapsed; and for each character kept, its position in
    ``text``."""
    kept: list[str] = []
    positions: list[int] = []
    space = True  # Drops leading whitespace.
    for i, ch in enumerate(text.translate(SAME)):
        if ch in MARKUP:
            continue
        if ch.isspace():
            if space:
                continue
            ch, space = " ", True
        else:
            space = False
        kept.append(ch)
        positions.append(i)
    if kept and kept[-1] == " ":
        kept.pop()
        positions.pop()
    return "".join(kept), positions


def plain(text: str) -> str:
    """``text`` as :func:`normalize` makes it."""
    return normalize(text)[0]


class TextIndex:
    """A node's Markdown, and where each node inside it starts.

    ``starts`` lists ``(offset, node)`` in document order: each section,
    appendix and paragraph at the offset in ``markdown`` where its text
    begins. :meth:`at` gives the node an offset is in.
    """

    def __init__(self, node: Node, markdown: str | None = None) -> None:
        self.node = node
        self.markdown = node.to_markdown() if markdown is None else markdown
        self.plain, self.positions = normalize(self.markdown)
        self.starts: list[tuple[int, Node]] = [(0, node)]
        at = 0  # In the plain text; nodes start in document order.
        for inner in node.iter():
            if isinstance(inner, Paragraph):
                key = plain(inner.blocks[0])[:80] if inner.blocks else ""
            else:
                key = plain(inner.heading)[:80]
            if not key:
                continue
            found = self.plain.find(key, at)
            if found < 0:
                continue  # Text the Markdown writes differently; rare.
            at = found
            raw = self.positions[found]
            if self.markdown.startswith("**", raw - 2):  # A bold label: **(a)**.
                raw -= 2
            self.starts.append((raw, inner))
        self._offsets = [offset for offset, _ in self.starts]

    def at(self, offset: int) -> Node:
        """The deepest node whose text includes ``offset`` in the Markdown."""
        i = bisect.bisect_right(self._offsets, offset) - 1
        return self.starts[max(i, 0)][1]

    def within(self, start: int, end: int) -> list[Node]:
        """The paragraphs that start between two offsets in the Markdown."""
        return [
            node
            for offset, node in self.starts
            if start <= offset < end and node.level is Level.PARAGRAPH
        ]

    def raw(self, start: int, end: int) -> tuple[int, int]:
        """Offsets in the Markdown for offsets in the plain text."""
        last = len(self.positions) - 1
        return self.positions[min(start, last)], self.positions[min(end, last)] + 1


@dataclass(frozen=True)
class Passage:
    """A slice of a node's text that fits a budget.

    ``start`` and ``end`` are offsets in the node's Markdown; ``total`` is
    its length. ``cursor`` continues where this passage ends, None at the
    end of the text.
    """

    node: Node
    text: str
    start: int
    end: int
    total: int
    tokens: int
    total_tokens: int
    paragraphs: list[Node] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """The passage is all of the text."""
        return self.start == 0 and self.end >= self.total

    @property
    def more(self) -> bool:
        """Text follows this passage."""
        return self.end < self.total

    @property
    def cursor(self) -> str | None:
        """Where to continue: the node's URI with the offset, e.g.
        ``cfr:45/164.502@2024-01-01#5120``; None at the end."""
        return f"{self.node.uri}#{self.end}" if self.more else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "total": self.total,
            "tokens": self.tokens,
            "total_tokens": self.total_tokens,
            "complete": self.complete,
            "cursor": self.cursor,
            "paragraphs": [
                {"citation": p.citation, "address": p.address, "anchor": p.anchor}
                for p in self.paragraphs
            ],
        }


def parse_cursor(cursor: str) -> tuple[str, int]:
    """The address and offset in a :attr:`Passage.cursor`."""
    where, _, offset = cursor.strip().rpartition("#")
    if not where or not offset.isdigit():
        raise InvalidParameterError(
            f"{cursor!r} isn't a cursor; pass back the cursor a passage gave, "
            "e.g. cfr:45/164.502@2024-01-01#5120"
        )
    return where, int(offset)


def passage(
    node: Node,
    budget: int,
    offset: int = 0,
    *,
    pinpoints: bool = True,
    index: TextIndex | None = None,
) -> Passage:
    """The text of ``node`` from ``offset`` that fits ``budget`` tokens.

    It ends at a block (paragraph, table, note) boundary; before a heading,
    if that keeps most of the budget, so sections stay whole. A block larger
    than the whole budget is cut at a sentence, else a word. With
    ``pinpoints``, paragraph labels below the top level show their full
    path, "(a)(1)(ii)" rather than "(ii)", so each can be cited as it is.
    """
    if budget < 1:
        raise InvalidParameterError(f"The budget must be at least 1; got {budget}")
    index = index or TextIndex(node)
    text = index.markdown
    if not 0 <= offset <= len(text):
        raise InvalidParameterError(
            f"Offset {offset} is outside the text, which is {len(text):,} "
            "characters; continue from a cursor a passage gave"
        )
    end = _slice_end(text, offset, budget * CHARS_PER_TOKEN)
    body = text[offset:end]
    if pinpoints:
        body = _pinpointed(body, offset, index)
    return Passage(
        node=node,
        text=body.strip("\n") + "\n",
        start=offset,
        end=end,
        total=len(text),
        tokens=tokens(body),
        total_tokens=tokens(text),
        paragraphs=index.within(offset, end),
    )


def _slice_end(text: str, start: int, chars: int) -> int:
    limit = start + chars
    if limit >= len(text):
        return len(text)
    breaks = [m.end() for m in re.finditer(r"\n\n+", text[start:limit])]
    if breaks:
        end = start + breaks[-1]
        headings = [
            start + b
            for b in breaks
            if text.startswith("#", start + b) and b >= chars * KEEP_WHOLE
        ]
        return headings[-1] if headings else end
    # One block larger than the budget: end at a sentence, else a word.
    window = text[start:limit]
    for pattern in (r"[.;:](?=\s)", r"\s"):
        found = [m.end() for m in re.finditer(pattern, window)]
        if found and found[-1] > chars // 4:
            return start + found[-1]
    return limit


def _pinpointed(body: str, offset: int, index: TextIndex) -> str:
    """Paragraph labels as full paths: "**(ii)**" becomes "**(a)(1)(ii)**"."""
    starts = [
        (start - offset, node)
        for start, node in index.starts
        if offset <= start < offset + len(body) and isinstance(node, Paragraph)
    ]
    pieces: list[str] = []
    done = i = 0
    while i < len(starts):
        local = starts[i][0]
        labels = LABELS.match(body, local)
        assert labels is not None  # It matches nothing, at worst.
        # Paragraphs that start in one run of labels, **(e)(1)**, are named
        # by the deepest of them.
        inside = [n for at, n in starts[i:] if at < max(labels.end(), local + 1)]
        i += len(inside)
        path = inside[-1].path
        if len(path) > 1:  # A top-level label is its own pinpoint already.
            pieces += [body[done:local], "**" + "".join(f"({m})" for m in path) + "**"]
            done = labels.end()
    pieces.append(body[done:])
    return "".join(pieces)


@dataclass(frozen=True)
class QuoteCheck:
    """Whether a quote is in a citation's text.

    ``found`` is true when the quote is there word for word (ignoring
    formatting, whitespace, quote marks, dashes and paragraph labels);
    ``cited`` when it's also within the citation given. ``citation`` is the
    pinpoint of the match, or of the nearest passage when not found, whose
    ``similarity`` (0 to 1) says how alike it is; ``matched`` is that text.
    """

    quote: str
    citation: str
    address: str | None
    found: bool
    cited: bool
    similarity: float
    matched: str
    url: str | None = None
    date: str | None = None

    @property
    def verified(self) -> bool:
        """Found word for word, at the citation given."""
        return self.found and self.cited

    def differences(self) -> list[tuple[Literal["=", "-", "+"], str]]:
        """Word by word, from the quote to the text: kept ("="), only in the
        quote ("-") and only in the text ("+")."""
        return Difference("changed", self.quote, self.matched).words

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "verified": self.verified,
            "found": self.found,
            "at_cited": self.cited,
            "similarity": round(self.similarity, 3),
            "citation": self.citation,
            "address": self.address,
            "url": self.url,
            "date": self.date,
            "matched": self.matched,
        }
        if not self.found and self.matched:
            data["differences"] = [
                {"op": op, "text": text} for op, text in self.differences()
            ]
        return data


def check_quote(scope: Node, quote: str, cited: Node | None = None) -> QuoteCheck:
    """Look for ``quote`` in the text of ``scope``.

    ``cited`` is the node the quote is attributed to (default ``scope``); a
    quote found elsewhere in ``scope`` is ``found`` but not ``cited``.
    """
    wanted = plain(QUOTED_MARKERS.sub("", plain(quote))).strip(" \"'")
    if not wanted:
        raise InvalidParameterError("Give the words to check")
    index = TextIndex(scope)
    cited = cited or scope
    at = index.plain.find(wanted)
    if at >= 0:
        start, end = at, at + len(wanted)
        similarity = 1.0
    else:
        start, end, similarity = _nearest(index.plain, wanted)
    if similarity < FOUND_SIMILARITY:
        return QuoteCheck(
            quote=wanted,
            citation=scope.citation,
            address=scope.address,
            found=False,
            cited=False,
            similarity=similarity,
            matched="",
            url=scope.url,
            date=scope.date.isoformat() if scope.date else None,
        )
    raw_start, raw_end = index.raw(start, end - 1)
    first, last = index.at(raw_start), index.at(raw_end - 1)
    where = _common(first, last)
    return QuoteCheck(
        quote=wanted,
        citation=_span(first, last, where),
        address=where.address,
        found=similarity == 1.0,
        cited=_inside(where, cited),
        similarity=similarity,
        matched=index.plain[start:end],
        url=where.url,
        date=where.date.isoformat() if where.date else None,
    )


def _nearest(text: str, wanted: str) -> tuple[int, int, float]:
    """The span of ``text`` most like ``wanted``, and how alike (0 to 1)."""
    words = [(m.start(), m.end()) for m in WORD.finditer(text)]
    want = [_stem(w) for w in WORD.findall(wanted)]
    if not words or not want:
        return 0, 0, 0.0
    n = len(want)
    target = Counter(want)
    lowered = [_stem(text[s:e]) for s, e in words]
    window = Counter(lowered[:n])
    scores: list[tuple[int, int]] = []
    for i in range(max(len(words) - n + 1, 1)):
        if i:
            window[lowered[i - 1]] -= 1
            if i + n - 1 < len(lowered):
                window[lowered[i + n - 1]] += 1
        scores.append((sum((window & target).values()), i))
    best = (0, 0, 0.0)
    tried: set[tuple[int, int]] = set()
    for _, first in sorted(scores, key=lambda s: (-s[0], s[1]))[:NEAREST_CANDIDATES]:
        for i, size in itertools.product(
            range(max(first - 2, 0), first + 3), range(n - 2, n + 3)
        ):
            j = min(i + max(size, 1), len(words)) - 1
            if i >= len(words) or (i, j) in tried:
                continue
            tried.add((i, j))
            start, end = words[i][0], words[j][1]
            ratio = difflib.SequenceMatcher(
                None, wanted.casefold(), text[start:end].casefold(), autojunk=False
            ).ratio()
            if ratio > best[2]:
                best = (start, end, ratio)
    # Not word for word, so never 1.
    return best[0], best[1], min(best[2], 0.999)


def _common(first: Node, last: Node) -> Node:
    """The deepest node that holds both."""
    chain = [first, *first.ancestors]
    above = {id(n) for n in (last, *last.ancestors)}
    return next(n for n in chain if id(n) in above)


def _span(first: Node, last: Node, where: Node) -> str:
    """A pinpoint citation from one paragraph to another: "45 CFR
    164.502(a)(1) through (a)(2)"; a single paragraph's own citation."""
    if first is last or first is where:
        return first.citation
    if isinstance(first, Paragraph) and isinstance(last, Paragraph):
        return f"{first.citation} through {''.join(f'({m})' for m in last.path)}"
    return f"{first.citation} through {last.citation}"


def _inside(node: Node, scope: Node) -> bool:
    return node is scope or any(a is scope for a in node.ancestors)


def _stem(word: str) -> str:
    """A word reduced for matching: lower case, no punctuation, no plural s."""
    bare = "".join(ch for ch in word.casefold() if ch.isalnum())
    return bare[:-1] if len(bare) > 3 and bare.endswith("s") else bare
