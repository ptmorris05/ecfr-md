"""Regulation text as the web navigator shows it.

The HTML writer of :mod:`ecfr_md.render.html_writer`, annotated for reading:
paragraph markers are links to their own anchors, cross-references link to
pages of this server (with hover previews), notes are set apart by kind,
and section headings carry their citation. Everything else stays as the
eCFR wrote it.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator
from dataclasses import replace
from html import escape
from urllib.parse import urlsplit

from ecfr_md.address import Address
from ecfr_md.compare import Difference
from ecfr_md.references import link_markdown
from ecfr_md.render.blocks import Block, Heading, Note, Paragraph, from_markdown
from ecfr_md.render.html_writer import Ids, Writer
from ecfr_md.render.inline import plain, spans
from ecfr_md.web.pages import local, split_heading

LEADING_MARKERS = re.compile(r"^\((\*?\w{1,8}\*?)\)")
FEDERAL_REGISTER = re.compile(r"\b\d+\s+FR\s+\d")
NOTE_KINDS = (
    ("authority", re.compile(r"^\*\*Authority", re.I)),
    ("source", re.compile(r"^\*\*Source", re.I)),
    ("editorial", re.compile(r"^\*\*(?:Editorial|Effective|Nomenclature|Note)", re.I)),
)
ECFR_HOSTS = ("ecfr.gov", "www.ecfr.gov")


class Reader(Writer):
    """HTML for reading on this server.

    ``date`` is the date of a past text, or None for the current text; it
    dates the links to other pages, so reading stays at one point in time.
    """

    def __init__(
        self,
        date: dt.date | None = None,
        ids: Ids | None = None,
        year: int | None = None,
    ) -> None:
        super().__init__(ids)
        self.date = date
        self.year = year or (date.year if date else None)
        self.places: list[Address | None] = []  # Where each open section is.

    def context(self) -> Address | None:
        place = self.places[-1] if self.places else None
        return replace(place, date=self.date) if place else None

    def section(self, heading: Heading) -> str:
        target = heading.target
        if target is None:
            self.places.append(self.places[-1] if self.places else None)
            return f"<section>\n{self.heading(heading)}\n"
        place = Address.parse(target.address)
        self.places.append(place)
        anchor = escape(self.ids(target.anchor))
        attrs = (
            f' id="{anchor}" class="div {escape(target.level)}"'
            f' data-cite="{escape(target.citation)}"'
            f' data-address="{escape(target.address)}"'
            f' data-bluebook="{escape(place.citation("bluebook", self.year))}"'
        )
        return f"<section{attrs}>\n{self.heading(heading, target.level, anchor)}\n"

    def end_section(self) -> str:
        self.places.pop()
        return "</section>\n"

    def heading(  # type: ignore[override]
        self, heading: Heading, level: str = "", anchor: str = ""
    ) -> str:
        n = min(max(heading.level, 1), 6)
        text = plain(heading.text)
        number, words = split_heading(text) if level in ("section", "") else ("", text)
        if number:
            inner = (
                f'<span class="num">{escape(number)}</span> '
                f'<span class="words">{self.inline(_rest(heading.text, number))}</span>'
            )
        else:
            inner = self.inline(heading.text) if words == text else escape(words)
        link = (
            f'<a class="self" href="#{anchor}" aria-label="Link to this">#</a>'
            if anchor
            else ""
        )
        return f"<h{n}>{inner}{link}</h{n}>"

    def paragraph(self, block: Paragraph) -> str:
        anchors = [self.ids(a) for a in block.anchors]
        text = self._linked(block.text)
        attrs = ""
        if anchors:
            attrs = (
                f' id="{escape(anchors[0])}" class="p d{min(max(block.depth, 1), 6)}"'
            )
        body = self._markers(text, anchors) if anchors else self.inline(text)
        extra = "".join(
            f'<span id="{escape(a)}" class="also"></span>' for a in anchors[1:]
        )
        return f"<p{attrs}>{extra}{body}</p>\n"

    def _markers(self, text: str, anchors: list[str]) -> str:
        """The paragraph with its leading markers ("(a)(1)") as links to
        their paragraphs."""
        found = spans(text)
        if not found or not found[0].bold or not LEADING_MARKERS.match(found[0].text):
            return self.inline(text)
        marker = found[0].text.strip()
        links = []
        rest = marker
        for anchor in anchors:
            match = LEADING_MARKERS.match(rest)
            if match is None:
                break
            links.append(
                f'<a class="mk" href="#{escape(anchor)}">{escape(match[0])}</a>'
            )
            rest = rest[match.end() :]
        tail = escape(rest)
        return "".join(links) + tail + "".join(self.span(s) for s in found[1:])

    def note(self, block: Note) -> str:
        first = next((b for b in block.blocks if isinstance(b, Paragraph)), None)
        text = first.text if first else ""
        kind = next((k for k, pattern in NOTE_KINDS if pattern.match(text)), None)
        if kind is None:
            source = text.startswith("[") and FEDERAL_REGISTER.search(text)
            kind = "source" if source else "extract"
        tag = "blockquote" if kind == "extract" else "aside"
        inner = "".join(self.body(block.blocks))
        return f'<{tag} class="note {kind}">\n{inner}</{tag}>\n'

    def _linked(self, text: str) -> str:
        """Markdown with cross-references made links, to pages of this server."""
        context = self.context()
        if context is None:
            return text
        return link_markdown(text, context)

    def link(self, href: str, html: str) -> str:
        parts = urlsplit(href)
        if parts.netloc in ECFR_HOSTS and parts.path.startswith(("/current/", "/on/")):
            path = local(href)
            return f'<a class="xref" href="{escape(path)}">{html}</a>'
        if parts.scheme in ("http", "https"):
            return f'<a class="ext" href="{escape(href)}" rel="noopener">{html}</a>'
        return super().link(href, html)


def _rest(markdown: str, number: str) -> str:
    """A heading's Markdown after its number."""
    head = markdown.lstrip()
    if head.startswith(number):
        return head[len(number) :].lstrip(" .")
    words = split_heading(plain(markdown))[1]
    return words


def render(
    blocks: Iterable[Block], date: dt.date | None = None, year: int | None = None
) -> Iterator[str]:
    """Reading HTML for blocks, in pieces as they arrive. ``date`` dates
    links (None for current text); ``year`` is for Bluebook citations."""
    yield from Reader(date, year=year).body(blocks)


def redline(
    blocks: list[Difference],
    date: dt.date | None = None,
    *,
    context: Address | None = None,
    paragraphs: Iterable[Block] = (),
) -> Iterator[str]:
    """The whole text with changes marked: deletions struck, insertions
    underlined, added and removed blocks set apart.

    ``context`` is where the text is, for its cross-references;
    ``paragraphs`` are the blocks of either text, which give paragraphs
    their depth and anchors.
    """
    known = {b.text: b for b in paragraphs if isinstance(b, Paragraph)}
    reader = Reader(date)
    reader.places.append(context)
    for block in blocks:
        markdown = block.after if block.kind != "removed" else block.before
        if block.kind == "changed":
            markdown = block.to_markdown()
        source = block.after if block.kind != "removed" else block.before
        html = "".join(reader.body(_flat(from_markdown(markdown), known.get(source))))
        if block.kind == "unchanged":
            yield f'<div class="same">{html}</div>\n'
        else:
            yield f'<div class="change {block.kind}">{_mark(block.kind, html)}</div>\n'


def _flat(blocks: list[Block], like: Paragraph | None) -> list[Block]:
    """Headings of a redline as plain headings, not divisions; a paragraph
    with the depth and anchors of the paragraph it is."""
    flat: list[Block] = []
    for b in blocks:
        if isinstance(b, Heading):
            b = replace(b, target=None)
        elif isinstance(b, Paragraph) and like is not None and len(blocks) == 1:
            b = replace(b, depth=like.depth, anchors=like.anchors)
        flat.append(b)
    return flat


def _mark(kind: str, html: str) -> str:
    if kind == "added":
        return f"<ins>{html}</ins>"
    if kind == "removed":
        return f"<del>{html}</del>"
    return html
