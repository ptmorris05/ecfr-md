"""Search queries: what the eCFR's search understands, and plain words.

The eCFR searches with these operators (measured November 2026):

- ``words`` — sections with every word; words are stemmed, so "slur" also
  finds "slurs".
- ``"exact phrase"`` — the words together, as written (not stemmed).
- ``-word`` — without the word (or ``-"phrase"``).
- ``a | b`` — either one; ``( )`` groups, e.g. ``ethnic (slur | epithet)``.
- ``prefix*`` — words that start with it: ``harass*``.
- ``"phrase"~3`` — the phrase's words within 3 words of each other.

``AND``, ``OR`` and ``NOT`` in capitals are read as ``+``, ``|`` and ``-``,
since the eCFR would search for them as words.
"""

from __future__ import annotations

import re

from ecfr_md.errors import InvalidParameterError

SYNTAX = (
    'words (all of them, any form: slur finds slurs), "exact phrase", '
    "-exclude, a | b (either), ( ) to group, prefix*, "
    '"phrase"~3 (words near each other); AND, OR, NOT also work'
)
OPERATOR = re.compile(r'["|()*~+]|(?:^|\s)-\S')
WORD_OPERATORS = {"AND": "+", "OR": "|", "NOT": "-"}
QUOTED = re.compile(r'"([^"]*)"(?:~\d+)?')
QUOTED_MINUS = re.compile(r'-"[^"]*"')


def is_advanced(query: str) -> bool:
    """Whether a query uses search operators, not only words."""
    return bool(OPERATOR.search(query)) or any(
        w in WORD_OPERATORS for w in query.split()
    )


def normalize(query: str) -> str:
    """The query as the eCFR reads it: word operators made symbols, after
    checking that quotes and parentheses are balanced."""
    query = " ".join(query.split())
    if not query:
        raise InvalidParameterError(
            'Give words to search for, e.g. "minimum necessary"'
        )
    if query.count('"') % 2:
        raise InvalidParameterError(
            f"Unbalanced quotes in {query!r}: put each phrase in a pair of quotes. "
            f"Search syntax: {SYNTAX}"
        )
    depth = 0
    for char in QUOTED.sub("", query):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth < 0:
            break
    if depth:
        raise InvalidParameterError(
            f"Unbalanced parentheses in {query!r}. Search syntax: {SYNTAX}"
        )
    words = []
    for word in _tokens(query):
        operator = WORD_OPERATORS.get(word)
        if operator == "-":
            words.append("-")  # Joined to the next word below.
        elif operator is not None:
            words.append(operator)
        elif words and words[-1] == "-":
            words[-1] = f"-{word}"
        else:
            words.append(word)
    return " ".join(words)


def phrase(query: str) -> str:
    """Plain words as one exact phrase."""
    return f'"{query}"'


def relaxed(query: str) -> str:
    """A query's phrases as plain words, which the eCFR stems."""
    return " ".join(QUOTED.sub(r"\1", query).split())


def terms(query: str) -> list[str]:
    """The stems of the words a query looks for, lower case, to mark in
    results: "slurs" and "slur" are "slur"; excluded words are left out."""
    found: list[str] = []
    for token in _tokens(QUOTED_MINUS.sub("", query)):
        if token.startswith("-") or token in WORD_OPERATORS:
            continue
        for word in re.findall(r"\w[\w'-]*", token):
            stem = _stem(word.casefold())
            if len(stem) > 2 and stem not in found:
                found.append(stem)
    return found


def marks(word: str, stems: list[str]) -> bool:
    """Whether a word in a result is one of the query's (in any form)."""
    word = word.casefold()
    return any(word.startswith(stem) for stem in stems)


def fragment(query: str) -> str:
    """Text to find on the result's page: the phrase, for plain words or a
    query's first quoted phrase; nothing for other queries."""
    if not is_advanced(query):
        return query
    first = QUOTED.search(query)
    return first[1] if first else ""


def _stem(word: str) -> str:
    """A rough stem, enough to mark "slurs" and "harassing" for "slur" and
    "harass"; a trailing ``*`` is already a prefix."""
    if word.endswith("*"):
        return word.rstrip("*")
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if word.endswith("ss") and suffix in ("es", "s"):
            continue  # "harass", "access": not plurals.
        if word.endswith(suffix) and len(word) - len(suffix) > 3:
            return word[: -len(suffix)]
    return word


def _tokens(query: str) -> list[str]:
    """Words, keeping each quoted phrase (and its ~N) as one."""
    return re.findall(r'-?"[^"]*"(?:~\d+)?|\S+', query)
