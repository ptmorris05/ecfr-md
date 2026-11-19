"""HTML: semantic, anchored pages that read well on screen and in print.

Each division is a ``<section>`` whose id is its eCFR anchor (``part-164``,
``subpart-E``, ``164.502``), and each numbered paragraph a ``<p>`` with the
eCFR's paragraph id (``p-164.502(a)(1)``), so links into the eCFR work here
by swapping the page. The markup is also well-formed XML, for EPUB.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from html import escape

from ecfr_md.config import DISCLAIMER, __version__
from ecfr_md.render.blocks import Block, Heading, Image, Item, Note, Paragraph, Table
from ecfr_md.render.inline import Span, plain, spans

MAX_HEADING = 6
SPACE = re.compile(r"\s+")
# Links between Markdown files in a tree point to the files of another format.
MARKDOWN_LINK = re.compile(r"^(?![a-z][a-z0-9+.-]*:)([^#?]*)\.md(?=$|[#?])", re.I)

CSS = """\
:root {
  color-scheme: light dark;
  --text: #1f2328; --muted: #5d6470; --rule: #d6d9de; --bg: #ffffff;
  --link: #0b57d0; --mark: rgba(11, 87, 208, 0.08);
}
@media (prefers-color-scheme: dark) {
  :root {
    --text: #e3e5e8; --muted: #9aa1ab; --rule: #363b42; --bg: #16181b;
    --link: #8ab4f8; --mark: rgba(138, 180, 248, 0.12);
  }
}
html { background: var(--bg); color: var(--text); }
body {
  margin: 0 auto; max-width: 42rem; padding: 2.5rem 1.25rem 4rem;
  font: 1.0625rem/1.6 Charter, "Bitstream Charter", "Iowan Old Style",
    "Source Serif Pro", Georgia, Cambria, serif;
  font-kerning: normal; hyphens: auto; overflow-wrap: break-word;
}
h1, h2, h3, h4, h5, h6, .heading {
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-weight: 600; line-height: 1.3; margin: 2.2em 0 0.7em; hyphens: manual;
}
h1 { font-size: 1.55rem; margin-top: 0.5em; }
h2 { font-size: 1.3rem; }
h3 { font-size: 1.12rem; }
h4, h5, h6, .heading { font-size: 1rem; }
p { margin: 0.65em 0; }
.d2 { margin-left: 1.5em; } .d3 { margin-left: 3em; } .d4 { margin-left: 4.5em; }
.d5 { margin-left: 6em; } .d6 { margin-left: 7.5em; }
.note {
  margin: 1em 0; padding: 0 0 0 1em; border-left: 2px solid var(--rule);
  color: var(--muted); font-size: 0.9em;
}
.table { overflow-x: auto; margin: 1em 0; }
table { border-collapse: collapse; font-size: 0.88em; line-height: 1.4; }
th, td {
  border: 1px solid var(--rule); padding: 0.3em 0.55em;
  vertical-align: top; text-align: left;
}
th { font-weight: 600; }
figure { margin: 1em 0; }
img { max-width: 100%; height: auto; }
a { color: var(--link); text-underline-offset: 0.15em; }
:target { background: var(--mark); }
.source, footer {
  font: 0.82rem/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color: var(--muted);
}
.source { margin: 0 0 1.5rem; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule); }
@media print {
  :root { --text: #000; --muted: #333; --rule: #999; --bg: #fff; --link: #000; }
  body { max-width: none; padding: 0; font-size: 11pt; }
  a { text-decoration: none; }
  h1, h2, h3, h4, h5, h6, .heading { break-after: avoid; }
  tr, figure, img { break-inside: avoid; }
  .up { display: none; }
}
"""


class Ids:
    """Unique element ids: a repeated anchor gets "~2", "~3"; ``clean``
    rewrites anchors for formats with stricter ids."""

    def __init__(self, clean: Callable[[str], str] | None = None) -> None:
        self.clean = clean or (lambda anchor: SPACE.sub("-", anchor))
        self.seen: set[str] = set()

    def __call__(self, anchor: str) -> str:
        base = self.clean(anchor)
        found, n = base, 2
        while found in self.seen:
            found, n = f"{base}~{n}", n + 1
        self.seen.add(found)
        return found


def page(
    blocks: Iterable[Block],
    *,
    up: str | None = None,
    links: str | None = None,
) -> Iterator[str]:
    """A standalone HTML page, in pieces as the blocks arrive.

    ``up`` links to a contents page; ``links`` is the suffix that links to
    Markdown files (``164.502.md``) get, as in a tree of pages.
    """
    blocks = iter(blocks)
    first = next(blocks, None)
    meta = (
        first.target.provenance if isinstance(first, Heading) and first.target else None
    )
    meta = meta or {}
    title = _title(first)
    yield '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8" />\n'
    yield '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
    yield f"<title>{escape(title)}</title>\n"
    yield f'<meta name="generator" content="ecfr-md {escape(__version__)}" />\n'
    for key, value in meta.items():
        yield f'<meta name="cfr:{escape(key)}" content="{escape(str(value))}" />\n'
    if source := meta.get("source"):
        yield f'<link rel="canonical" href="{escape(source)}" />\n'
    yield f"<style>\n{CSS}</style>\n</head>\n<body>\n"
    yield _source_line(meta, up)
    yield "<main>\n"
    rest = blocks if first is None else _chain([first], blocks)
    yield from body(rest, Ids(), links=links)
    yield "</main>\n"
    yield f"<footer><p>{escape(meta.get('disclaimer', DISCLAIMER))}</p></footer>\n"
    yield "</body>\n</html>\n"


def _chain(head: list[Block], rest: Iterator[Block]) -> Iterator[Block]:
    yield from head
    yield from rest


def _title(first: Block | None) -> str:
    if not isinstance(first, Heading):
        return "CFR"
    heading = plain(first.text)
    if first.target is None or first.target.citation in heading:
        return heading
    return f"{first.target.citation} — {heading}" if heading else first.target.citation


def _source_line(meta: dict, up: str | None) -> str:
    parts = []
    if up:
        parts.append(f'<a class="up" href="{escape(up)}">Contents</a>')
    if citation := meta.get("citation"):
        parts.append(escape(citation))
    if date := meta.get("date"):
        parts.append(f"eCFR text as of {escape(date)}")
    if source := meta.get("source"):
        parts.append(f'<a href="{escape(source)}">ecfr.gov</a>')
    if not parts:
        return ""
    return f'<p class="source">{" · ".join(parts)}</p>\n'


def body(
    blocks: Iterable[Block], ids: Ids, *, links: str | None = None
) -> Iterator[str]:
    """HTML for blocks: divisions as nested sections, lists grouped."""
    open_levels: list[int] = []
    in_list = False
    for block in blocks:
        if in_list and not isinstance(block, Item):
            yield "</ul>\n"
            in_list = False
        if isinstance(block, Heading):
            while open_levels and open_levels[-1] >= block.level:
                open_levels.pop()
                yield "</section>\n"
            open_levels.append(block.level)
            yield _section(block, ids, links)
        elif isinstance(block, Item):
            if not in_list:
                yield "<ul>\n"
                in_list = True
            yield f"<li>{inline(block.text, links)}</li>\n"
        else:
            yield _block(block, ids, links)
    if in_list:
        yield "</ul>\n"
    yield "</section>\n" * len(open_levels)


def _section(heading: Heading, ids: Ids, links: str | None) -> str:
    target = heading.target
    attrs = ""
    if target is not None:
        attrs = f' id="{escape(ids(target.anchor))}" class="{escape(target.level)}"'
    text = inline(heading.text, links)
    level = heading.level
    if level <= MAX_HEADING:
        tag = f"<h{level}>{text}</h{level}>"
    else:
        tag = f'<p class="heading" role="heading" aria-level="{level}">{text}</p>'
    return f"<section{attrs}>\n{tag}\n"


def _block(block: Paragraph | Note | Table | Image, ids: Ids, links: str | None) -> str:
    match block:
        case Paragraph(text, depth, anchors):
            attrs = f' id="{escape(ids(anchors[0]))}"' if anchors else ""
            if depth > 1:
                attrs += f' class="d{min(depth, 6)}"'
            extra = "".join(f'<span id="{escape(ids(a))}"></span>' for a in anchors[1:])
            return f"<p{attrs}>{extra}{inline(text, links)}</p>\n"
        case Note(inner):
            notes = "".join(body(inner, ids, links=links))
            return f'<aside class="note">\n{notes}</aside>\n'
        case Table():
            return _table(block, links)
        case Image(src, alt):
            img = f'<img src="{escape(src)}" alt="{escape(alt)}" loading="lazy" />'
            return f"<figure>{img}</figure>\n"


def _table(table: Table, links: str | None) -> str:
    width = max((len(r) for r in table.rows), default=0)
    out = ['<div class="table"><table>\n']
    for i, row in enumerate(table.rows):
        if i == 0 and table.header:
            out.append("<thead>\n")
        if i == table.header:
            out.append("<tbody>\n")
        tag = "th" if i < table.header else "td"
        cells = [*row, *[""] * (width - len(row))]
        out.append(
            "<tr>"
            + "".join(f"<{tag}>{inline(c, links)}</{tag}>" for c in cells)
            + "</tr>\n"
        )
        if i == table.header - 1:
            out.append("</thead>\n")
    if len(table.rows) > table.header:
        out.append("</tbody>\n")
    out.append("</table></div>\n")
    return "".join(out)


def inline(markdown: str, links: str | None = None) -> str:
    """Inline Markdown as HTML."""
    return "".join(_span(s, links) for s in spans(markdown))


def _span(span: Span, links: str | None) -> str:
    if span.kind == "break":
        return "<br />"
    if span.kind == "image":
        alt = escape(span.text)
        return f'<img src="{escape(span.href or "")}" alt="{alt}" loading="lazy" />'
    html = escape(span.text, quote=False)
    for on, tag in (
        (span.sub, "sub"),
        (span.sup, "sup"),
        (span.italic, "em"),
        (span.bold, "strong"),
    ):
        if on:
            html = f"<{tag}>{html}</{tag}>"
    if span.href is not None:
        href = MARKDOWN_LINK.sub(rf"\1{links}", span.href) if links else span.href
        html = f'<a href="{escape(href)}">{html}</a>'
    return html
