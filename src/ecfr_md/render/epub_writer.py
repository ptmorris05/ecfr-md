"""EPUB 3: the HTML pages as an e-book, one XHTML file per division.

Built with the standard library: a zip whose first entry is the uncompressed
``mimetype``, then ``META-INF/container.xml``, the package document, a
navigation document listing every heading, and the pages.
"""

import datetime as dt
import re
import zipfile
from collections.abc import Iterable
from html import escape
from typing import IO

from ecfr_md.config import DISCLAIMER, __version__
from ecfr_md.render.blocks import Block, Heading
from ecfr_md.render.html_writer import CSS, Ids, body
from ecfr_md.render.inline import plain

MIMETYPE = "application/epub+zip"
CONTAINER = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
XHTML = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" \
lang="en" xml:lang="en">
<head>
<meta charset="utf-8" />
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css" />
</head>
<body>
{body}</body>
</html>
"""
NOT_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def xml_id(anchor: str) -> str:
    """An anchor as an XML name: "p-164.502(a)(1)" -> "p-164.502-a-1"."""
    name = NOT_NAME.sub("-", anchor).strip("-")
    return name if name[:1].isalpha() else f"id-{name}"


def write(blocks: Iterable[Block], out: IO[bytes]) -> None:
    """Write an EPUB of the blocks to a binary file."""
    chapters: list[list[Block]] = []
    for block in blocks:
        if isinstance(block, Heading) or not chapters:
            chapters.append([])
        chapters[-1].append(block)
    first = chapters[0][0] if chapters else None
    meta = (
        first.target.provenance
        if isinstance(first, Heading) and first.target and first.target.provenance
        else {}
    )
    title = plain(first.text) if isinstance(first, Heading) else "CFR"
    ids = Ids(xml_id)
    pages: list[tuple[str, str]] = []  # File name and XHTML.
    toc: list[tuple[int, str, str]] = []  # Level, link and text of each heading.
    for n, chapter in enumerate(chapters, 1):
        name = f"page-{n:05d}.xhtml"
        html = "".join(body(chapter, ids))
        head = chapter[0]
        if isinstance(head, Heading):
            found = re.search(r'<section id="([^"]+)"', html)
            link = f"{name}#{found[1]}" if found else name
            toc.append((head.level, link, plain(head.text)))
        pages.append((name, XHTML.format(title=escape(title), body=html)))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as epub:
        epub.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", CONTAINER)
        epub.writestr("OEBPS/content.opf", _package(title, meta, [p for p, _ in pages]))
        epub.writestr("OEBPS/nav.xhtml", _nav(title, toc))
        epub.writestr("OEBPS/style.css", CSS)
        for name, page in pages:
            epub.writestr(f"OEBPS/{name}", page)


def _package(title: str, meta: dict, pages: list[str]) -> str:
    identifier = escape(
        f"urn:cfr:{meta.get('address', 'cfr')}@{meta.get('date', 'current')}"
    )
    modified = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = "".join(
        f'    <item id="p{n}" href="{name}" media-type="application/xhtml+xml"/>\n'
        for n, name in enumerate(pages, 1)
    )
    spine = "".join(f'    <itemref idref="p{n}"/>\n' for n in range(1, len(pages) + 1))
    source = meta.get("source", "")
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" xml:lang="en"
  unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">{identifier}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>en</dc:language>
    <dc:publisher>Office of the Federal Register (eCFR)</dc:publisher>
    <dc:source>{escape(source)}</dc:source>
    <dc:description>{escape(DISCLAIMER)}</dc:description>
    <dc:date>{escape(meta.get("date", ""))}</dc:date>
    <meta property="dcterms:modified">{modified}</meta>
    <meta name="generator" content="ecfr-md {escape(__version__)}"/>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
      properties="nav"/>
    <item id="css" href="style.css" media-type="text/css"/>
{items}  </manifest>
  <spine>
{spine}  </spine>
</package>
"""


def _nav(title: str, toc: list[tuple[int, str, str]]) -> str:
    """The navigation document: headings as nested lists."""
    out: list[str] = []
    levels: list[int] = []
    for level, link, text in toc:
        while len(levels) > 1 and level < levels[-1]:
            out.append("</li>\n</ol>\n")
            levels.pop()
        if levels and level > levels[-1]:
            out.append("\n<ol>\n")
            levels.append(level)
        elif levels:
            out.append("</li>\n")
        else:
            levels.append(level)
        out.append(f'<li><a href="{escape(link)}">{escape(text) or escape(link)}</a>')
    if levels:
        out.append("</li>\n" + "</ol>\n</li>\n" * (len(levels) - 1))
    items = "".join(out)
    nav = f'<nav epub:type="toc" id="toc">\n<h1>Contents</h1>\n<ol>\n{items}'
    nav += "</ol>\n</nav>\n"
    return XHTML.format(title=escape(title), body=nav)
