"""Inline text: the Markdown inside a block, as styled spans.

ecfr-md's Markdown uses ``**bold**``, ``*italic*``, links and images, plus the
HTML tags ``<strong>``, ``<em>``, ``<sup>``, ``<sub>`` and ``<br>`` where
Markdown has no syntax. markdown-it-py reads it; each writer turns the spans
into its own markup.
"""

import re
from dataclasses import dataclass, replace
from functools import cache
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

HTML_TAG = re.compile(r"<(/?)([A-Za-z]+)\s*/?>")
STYLES = {"strong": "bold", "b": "bold", "em": "italic", "i": "italic"}
STYLES |= {"sup": "sup", "sub": "sub"}
MARKERS = {"strong_open": "bold", "em_open": "italic"}
CLOSERS = {"strong_close": "bold", "em_close": "italic"}


@dataclass(frozen=True)
class Span:
    """A run of text in one style; a line break; or an image (``text`` is
    its description and ``href`` its source)."""

    text: str
    bold: bool = False
    italic: bool = False
    sup: bool = False
    sub: bool = False
    href: str | None = None
    kind: Literal["text", "break", "image"] = "text"


@cache
def parser() -> MarkdownIt:
    """CommonMark with tables, as ecfr-md writes it."""
    return MarkdownIt("commonmark").enable("table")


def spans(markdown: str) -> list[Span]:
    """The styled spans of a line of inline Markdown."""
    tokens = parser().parseInline(markdown)
    return _spans(tokens[0].children or []) if tokens else []


def _spans(tokens: list[Token]) -> list[Span]:
    out: list[Span] = []
    depth = {"bold": 0, "italic": 0, "sup": 0, "sub": 0}
    links: list[str] = []

    def style() -> Span:
        on = [depth[name] > 0 for name in ("bold", "italic", "sup", "sub")]
        return Span("", *on, href=links[-1] if links else None)

    for token in tokens:
        kind = token.type
        if kind in MARKERS or kind in CLOSERS:
            depth[MARKERS.get(kind) or CLOSERS[kind]] += 1 if kind in MARKERS else -1
        elif kind == "link_open":
            links.append(str(token.attrs.get("href", "")))
        elif kind == "link_close":
            links.pop()
        elif kind in ("softbreak", "hardbreak"):
            out.append(replace(style(), kind="break"))
        elif kind == "image":
            src = str(token.attrs.get("src", ""))
            out.append(replace(style(), text=token.content, href=src, kind="image"))
        elif kind == "html_inline" and (tag := HTML_TAG.fullmatch(token.content)):
            name = tag[2].lower()
            if name == "br":
                out.append(replace(style(), kind="break"))
            elif name in STYLES:
                depth[STYLES[name]] = max(
                    0, depth[STYLES[name]] + (-1 if tag[1] else 1)
                )
        elif token.content:  # Text, code and unknown HTML, as written.
            out.append(replace(style(), text=token.content))
    return out


def plain(markdown: str) -> str:
    """Inline Markdown as plain text: breaks are spaces, images their description."""
    return "".join(" " if s.kind == "break" else s.text for s in spans(markdown))
