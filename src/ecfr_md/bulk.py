"""GovInfo's bulk eCFR XML, rewritten in the eCFR API's dialect.

GovInfo publishes each title as one file
(``govinfo.gov/bulkdata/ECFR/title-N/ECFR-titleN.xml``), laid out for print:
one ``DIV1`` per volume, with chapters and subchapters split across volumes,
print tables of contents and a TEI header. The text is the same as the API's,
in nearly the same XML. :func:`normalize` streams a bulk file into the layout
the API's ``full`` endpoint returns for a whole title, so the same parser and
renderer apply:

- the volumes are merged, and the divisions above the part are rebuilt from
  the title's structure, which names them as the API does (bulk calls
  Subtitle A of title 45 a chapter, and Subtitle B "Subtitle B")
- print tables of contents (``CFRTOC``, ``PTHD``, ``CHAPTI``) and the
  ``HEADER`` are dropped
- "§ " is taken off section numbers
- appendices get the names the structure gives them
- tables get a caption, header and footer rows as the API marks them up

Anything that can't be matched to the structure raises
:class:`BulkFormatError`, so the caller can fall back to the API.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO

from ecfr_md.errors import ResponseFormatError
from ecfr_md.models import Structure

# Structure node types above the part, and their XML division tags.
CONTAINERS = {
    "title": ("DIV1", "TITLE"),
    "subtitle": ("DIV2", "SUBTITLE"),
    "chapter": ("DIV3", "CHAPTER"),
    "subchapter": ("DIV4", "SUBCHAP"),
}
CONTAINER_TAGS = frozenset({"DIV1", "DIV2", "DIV3", "DIV4"})
KINDS = {xml: kind for kind, (_, xml) in CONTAINERS.items()}
# Print matter with no place in the API's XML.
DROPPED = frozenset({"CFRTOC", "PTHD", "CHAPTI", "HEADER", "AMDDATE"})
UNIT_TYPES = {"PART": "part", "APPENDIX": "appendix", "SECTION": "section"}
VOLUME = re.compile(r"\s*-+\s*Volume\s+\d+\s*$", re.IGNORECASE)
SECTION_SIGNS = re.compile(r"^(?:[§\s]+|sec(?:tion)?\.?\s+)", re.IGNORECASE)
# How the API marks up emphasis codes in tables: (tag, class, italic inside).
TABLE_EMPHASIS = {
    "51": ("sup", None, False),
    "0731": ("sup", None, False),
    "52": ("sub", None, False),
    "0732": ("sub", None, False),
    "53": ("sup", None, True),
    "54": ("sub", None, True),
    "02": ("strong", "minor-caps", False),
    "04": ("strong", None, False),
}
RANGE_DASHES = re.compile(r"(\d)--(\d)")  # "§§ 808.57--808.101"
LOOKAHEAD = 5  # Structure entries to look through for a section's name.
PLAIN = frozenset({"0112"})  # Codes the API drops in tables.
FRACTION = re.compile(r"(\d+)/(\d+)")


class BulkFormatError(ResponseFormatError):
    """A bulk file that doesn't match the title's structure."""


@dataclass
class _Container:
    """A division above the part as the bulk file has it (in one volume)."""

    kind: str  # As the structure names it: "chapter", ...
    head: ET.Element | None = None
    content: list[ET.Element] = field(default_factory=list)
    units: int = 0  # Parts (or other units) seen in it so far.


@dataclass
class _Unit:
    """A part, or other division directly under a container, in the structure."""

    node: Structure
    chain: tuple[Structure, ...]  # Containers from the title down.


def normalize(source: BinaryIO, target: BinaryIO, structure: Structure) -> None:
    """Write a bulk title file as the API's XML for the whole title."""
    _Writer(structure, target).run(source)


def _units(node: Structure, chain: tuple[Structure, ...]) -> Iterator[_Unit]:
    """Parts and other divisions under containers, in document order; empty
    containers are units of their own."""
    if node.type in CONTAINERS and (node.children or node.type == "title"):
        for child in node.children:
            yield from _units(child, (*chain, node))
    else:
        yield _Unit(node, chain)


class _Writer:
    def __init__(self, structure: Structure, out: BinaryIO) -> None:
        self.structure = structure
        self.out = out
        self.units = list(_units(structure, ()))
        self.next = 0  # Index of the next structure unit to write.
        self.open: list[Structure] = []  # Containers open in the output.
        self.orphans: list[_Container] = []  # Ended bulk containers with no units.
        self.title_head: str | None = None
        self.closing = b""  # The end tag of the part last written, until the next.
        self.names = _Names(structure)

    def run(self, source: BinaryIO) -> None:
        self.out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<ECFR>\n')
        stack: list[ET.Element] = []
        containers: list[tuple[ET.Element, _Container]] = []
        try:
            for event, el in ET.iterparse(source, events=("start", "end")):
                if event == "start":
                    stack.append(el)
                    if el.tag in CONTAINER_TAGS:
                        kind = KINDS.get(el.get("TYPE", ""), "")
                        containers.append((el, _Container(kind)))
                    continue
                stack.pop()
                parent = stack[-1] if stack else None
                if containers and el is containers[-1][0]:
                    _, ended = containers.pop()
                    if not ended.units:
                        self.orphans.append(ended)
                elif containers and parent is containers[-1][0]:
                    self._child(el, containers)
                    continue  # Kept or written; removed below.
                if parent is not None and el.tag in DROPPED | CONTAINER_TAGS:
                    parent.remove(el)
        except ET.ParseError as exc:
            raise BulkFormatError(f"Invalid bulk XML: {exc}") from exc
        self._finish()
        self.out.write(b"</ECFR>\n")

    def _child(
        self, el: ET.Element, containers: list[tuple[ET.Element, _Container]]
    ) -> None:
        """A direct child of a container: its heading, content or a unit."""
        parent, container = containers[-1]
        if el.tag == "HEAD":
            container.head = el
            text = "".join(el.itertext()).strip()
            if text.upper().startswith("SUBTITLE"):
                container.kind = "subtitle"  # Bulk can call a subtitle a chapter.
            if self.title_head is None and parent.tag == "DIV1":
                self.title_head = VOLUME.sub("", text)
        elif el.tag in DROPPED:
            pass
        elif el.get("TYPE") in UNIT_TYPES and el.tag.startswith("DIV"):
            for _, open_container in containers:
                open_container.units += 1
            self._unit(el, [c for _, c in containers[1:]])
        elif container.units:
            self._close_unit()
            self._write(el)  # After its container's parts: written in place.
        else:
            container.content.append(el)
            return  # Kept until its container is written.
        parent.remove(el)

    def _unit(self, el: ET.Element, bulk: list[_Container]) -> None:
        """Write a part (or other unit), and the containers it is in."""
        kind = UNIT_TYPES[el.get("TYPE", "")]
        number = _number(el.get("N", ""))
        last = self.units[self.next - 1] if self.next else None
        if kind == "part" and self.closing and last and last.node.identifier == number:
            # A part continued in the next print volume: one part, as the API has.
            for child in list(el)[1:]:
                self.names.clean(child)
                self._write(child)
            return
        for i in range(self.next, len(self.units)):
            unit = self.units[i]
            if unit.node.type == kind and (
                kind != "part" or unit.node.identifier == number
            ):
                break
        else:
            raise BulkFormatError(f"{kind.title()} {number} isn't in the structure")
        for skipped in self.units[self.next : i]:
            self._placeholder(skipped, bulk)
        self.next = i + 1
        self._enter(unit.chain[1:], bulk)
        if unit.node.identifier is not None and kind != "part":
            el.set("N", unit.node.identifier)
        self.names = _Names(unit.node)
        self.names.clean(el)
        _place_appendices(el, unit.node)
        el.tail = None
        text = ET.tostring(el, encoding="utf-8", xml_declaration=False)
        end = f"</{el.tag}>".encode()
        if text.endswith(end):  # Left open, in case the next volume continues it.
            text, self.closing = text[: -len(end)], end + b"\n"
        self.out.write(text)

    def _placeholder(self, unit: _Unit, bulk: list[_Container]) -> None:
        """A unit the bulk file doesn't have: a reserved part or empty container."""
        self._enter(unit.chain[1:], bulk)
        node = unit.node
        label = node.label.strip()
        if node.type in CONTAINERS:
            tag, kind = CONTAINERS[node.type]
            self._open(tag, kind, node.identifier, self._orphan(node.type), label)
            self.out.write(f"</{tag}>\n".encode())
        elif node.type == "part":
            self._open("DIV5", "PART", node.identifier, None, label)
            self.out.write(b"</DIV5>\n")
        # Other kinds the bulk file left out (rare) are left out here too.

    def _close_unit(self) -> None:
        self.out.write(self.closing)
        self.closing = b""

    def _enter(self, chain: tuple[Structure, ...], bulk: list[_Container]) -> None:
        """Close and open containers so the output is inside ``chain``."""
        self._close_unit()
        self._start_title()
        inner = list(chain)
        common = 0
        while (
            common < min(len(self.open) - 1, len(inner))
            and self.open[common + 1] is inner[common]
        ):
            common += 1
        while len(self.open) - 1 > common:
            node = self.open.pop()
            self.out.write(f"</{CONTAINERS[node.type][0]}>\n".encode())
        for node in inner[common:]:
            tag, kind = CONTAINERS[node.type]
            match = next((c for c in bulk if c.kind == node.type), None)
            self._open(tag, kind, node.identifier, match, node.label.strip())
            self.open.append(node)

    def _orphan(self, kind: str) -> _Container | None:
        """The next ended bulk container of a kind that had no parts in it."""
        for i, orphan in enumerate(self.orphans):
            if orphan.kind == kind:
                del self.orphans[: i + 1]
                return orphan
        return None

    def _start_title(self) -> None:
        if self.open:
            return
        node = self.structure
        head = self.title_head or node.label.strip()
        self.out.write(
            f'<DIV1 N="{_attr(node.identifier)}" TYPE="TITLE">'
            f"<HEAD>{_text(head)}</HEAD>\n".encode()
        )
        self.open.append(node)

    def _open(
        self,
        tag: str,
        kind: str,
        identifier: str | None,
        bulk: _Container | None,
        label: str,
    ) -> None:
        self.out.write(f'<{tag} N="{_attr(identifier)}" TYPE="{kind}">'.encode())
        if bulk is not None and bulk.head is not None:
            self._write(bulk.head)
        else:
            self.out.write(f"<HEAD>{_text(label)}</HEAD>\n".encode())
        if bulk is not None:
            for el in bulk.content:
                self._write(el)
            bulk.content.clear()

    def _finish(self) -> None:
        self._close_unit()
        for unit in self.units[self.next :]:
            self._placeholder(unit, [])
        self._start_title()
        while self.open:
            node = self.open.pop()
            self.out.write(f"</{CONTAINERS[node.type][0]}>\n".encode())

    def _write(self, el: ET.Element) -> None:
        self.out.write(ET.tostring(el, encoding="utf-8", xml_declaration=False))


def _number(n: str) -> str:
    """A division's number without "§", "§§", "Sec." or "Section"."""
    return SECTION_SIGNS.sub("", n).strip().replace("\u2014", "-")


def _attr(value: str | None) -> str:
    return _text(value or "").replace('"', "&quot;")


def _text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Names:
    """Gives a part's sections and appendices the structure's names, in order.

    The structure names them as the API does: "164.502", "Appendix A to
    Part 5b". Bulk has "§ 164.502", "Section 01", "§§ 808.57—808.101" or
    "Appendix A", and sometimes no name at all.
    """

    def __init__(self, node: Structure) -> None:
        self.pending = [
            c for c in _descendants(node) if c.type in ("section", "appendix")
        ]

    def clean(self, root: ET.Element) -> None:
        """Rewrite part of a part in place, as the API writes it."""
        for el in root.iter():
            el.attrib.pop("NODE", None)
            kind = el.get("TYPE")
            if kind in ("SECTION", "APPENDIX") and el.get("N") is not None:
                el.set("N", self._name(kind.lower(), el))
            if (
                kind == "SECTION"
                and (head := el.find("HEAD")) is not None
                and head.text
            ):
                head.text = RANGE_DASHES.sub(r"\1-\2", head.text)
        for empty in [
            (parent, el)
            for parent in root.iter()
            for el in parent
            if el.get("TYPE") == "SUBPART" and _empty(el)
        ]:
            empty[0].remove(empty[1])
        _reattach(root)
        for wrapper in [d for d in root.iter("DIV") if d.find("DIV/TABLE") is not None]:
            _table(wrapper)
        for parent in root.iter():
            _drop_repeated_links(parent)

    def _name(self, kind: str, el: ET.Element) -> str:
        given = el.get("N", "")
        if kind == "section":  # Skipping those bulk doesn't have.
            for i, node in enumerate(self.pending[:LOOKAHEAD]):
                if node.type == kind and _same(given, node.identifier or ""):
                    del self.pending[: i + 1]
                    return node.identifier or ""
            return _number(given)
        # Appendices can be in another place, e.g. after a subpart, not in it.
        heading = _loose(_appendix_name(el))
        tests = (
            lambda name: name == _loose(given),
            lambda name: name == heading,
            lambda name: (
                bool(heading) and (heading.startswith(name) or name.startswith(heading))
            ),
            lambda name: bool(given.strip()) and name.startswith(_loose(given)),
        )
        for test in tests:
            for node in self.pending:
                if node.type == kind and test(_loose(node.identifier or "")):
                    self.pending.remove(node)
                    return node.identifier or ""
        return _appendix_name(el)


def _same(given: str, name: str) -> bool:
    """Whether bulk's number for a section is the structure's."""
    return _loose(_number(given)) == _loose(name)


def _loose(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", name.casefold())


def _drop_repeated_links(parent: ET.Element) -> None:
    """Bulk links an amendment once per instruction; the API once per document."""
    previous: str | None = None
    for child in list(parent):
        text = "".join(child.itertext()) if child.tag == "XREF" else None
        if text is not None and text == previous:
            parent.remove(child)
        previous = text


def _reattach(part: ET.Element) -> None:
    """Put back text that bulk lets run out of an appendix at the end of a
    part, after the appendix (and its subpart) has ended."""
    home: ET.Element | None = None
    for child in list(part):
        kind = child.get("TYPE") if child.tag.startswith("DIV") else None
        if kind == "APPENDIX":
            home = child
        elif kind == "SUBPART" and len(child) and child[-1].get("TYPE") == "APPENDIX":
            home = child[-1]
        elif kind is not None or child.tag == "HEAD":
            home = None
        elif home is not None:
            part.remove(child)
            home.append(child)


def _empty(division: ET.Element) -> bool:
    """A division with no heading and nothing in it, which bulk has and the
    API doesn't, e.g. ``<DIV6 N="0" TYPE="SUBPART"><HEAD> </HEAD></DIV6>``."""
    return all(c.tag == "HEAD" and not "".join(c.itertext()).strip() for c in division)


def _place_appendices(part: ET.Element, node: Structure) -> None:
    """Move appendices the structure puts in the part, not in a subpart,
    out of the subpart bulk has them at the end of."""
    in_part = {c.identifier for c in node.children if c.type == "appendix"}
    for subpart in [c for c in part if c.get("TYPE") == "SUBPART"]:
        moved = [
            c for c in subpart if c.get("TYPE") == "APPENDIX" and c.get("N") in in_part
        ]
        at = list(part).index(subpart) + 1
        for appendix in moved:
            subpart.remove(appendix)
            part.insert(at, appendix)
            at += 1


def _descendants(node: Structure) -> Iterator[Structure]:
    for child in node.children:
        yield child
        yield from _descendants(child)


def _appendix_name(el: ET.Element) -> str:
    """An appendix's name from its heading, up to the dash: the API's usual name."""
    head = el.find("HEAD")
    text = " ".join("".join(head.itertext()).split()) if head is not None else ""
    return text.split("—")[0].strip() or el.get("N", "")


def _table(wrapper: ET.Element) -> None:
    """Bulk tables have a title and notes beside them, and no header rows.

    The API puts the title in a ``CAPTION``, header rows (those of ``TH``
    cells) in ``THEAD``, the rest in ``TBODY``, and notes in ``TFOOT``.
    """
    holder = next(d for d in wrapper if d.find("TABLE") is not None)
    table = holder.find("TABLE")
    assert table is not None
    caption: list[ET.Element] = []
    notes: list[ET.Element] = []
    for div in list(wrapper):
        role = div.get("class", "")
        if role == "table_head":
            caption += list(div)
            wrapper.remove(div)
        elif role == "table_foot":
            notes += list(div)
            wrapper.remove(div)
    for el in [*table.iter(), *(n for p in notes + caption for n in p.iter())]:
        _inline(el)
    rows = [row for row in table if row.tag == "TR"]
    for row in rows:
        table.remove(row)
        for cell in row:
            _trim(cell)
    if caption:
        box = ET.SubElement(table, "CAPTION")
        for p in caption:
            _trim(p)
            p.set("class", "title")
            box.append(p)
        table.remove(box)
        table.insert(0, box)
    width = max((_width(row) for row in rows), default=1)
    heads = 0
    while heads < len(rows) and all(c.tag == "TH" for c in rows[heads]):
        heads += 1
    if heads:
        ET.SubElement(table, "THEAD").extend(rows[:heads])
    ET.SubElement(table, "TBODY").extend(rows[heads:])
    if notes:
        foot = ET.SubElement(table, "TFOOT")
        for p in notes:
            cell = ET.SubElement(ET.SubElement(foot, "TR"), "TD", colspan=str(width))
            cell.text = (p.text or "").lstrip("\n")
            cell.extend(list(p))
            _trim(cell)
    for el in list(table.iter()):
        _space(el)
    for el in list(table.iter()):
        for child in [c for c in el if c.tag == "E" and c.get("T") in PLAIN]:
            _unwrap(el, child)


def _inline(parent: ET.Element) -> None:
    """Emphasis codes and fractions in a table, as the API writes them."""
    for child in list(parent):
        if child.tag == "E" and (style := TABLE_EMPHASIS.get(child.get("T", ""))):
            _restyle(child, *style)
        elif child.tag == "fr" and not len(child):
            match = FRACTION.fullmatch(child.text or "")
            if match:
                below = ET.Element("sub")
                below.text, below.tail = match[2], child.tail
                child.tag, child.text, child.tail = "sup", match[1], "\u2044"
                parent.insert(list(parent).index(child) + 1, below)


def _space(parent: ET.Element) -> None:
    """Space inline elements in a table as the API does: subscripts and
    superscripts from what follows ("UO<sub>2</sub> F<sub>2</sub>"), italic
    ones from what precedes, and accents (``AC``) from the letter before."""
    children = list(parent)
    for i, child in enumerate(children):
        before = children[i - 1].tail if i else parent.text
        if child.tag in ("sub", "sup"):
            after = children[i + 1] if i + 1 < len(children) else None
            tail = child.tail or ""
            fraction = i > 0 and children[i - 1].tail == "\u2044"
            if not fraction and (
                tail[:1].isalnum()
                or tail[:1] == "("
                or (not tail and after is not None and after.tag != "br")
            ):
                child.tail = " " + tail
            italic = len(child) == 1 and child[0].tag == "em" and not child.text
            if italic and (i or before) and not (before or "")[-1:].isspace():
                _after(parent, children, i, " ")
        elif child.tag == "AC" and before and not before[-1].isspace():
            _after(parent, children, i, "\n")


def _after(parent: ET.Element, children: list[ET.Element], i: int, text: str) -> None:
    """Add text just before child ``i``."""
    if i:
        children[i - 1].tail = (children[i - 1].tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _unwrap(parent: ET.Element, el: ET.Element) -> None:
    """Replace an element with its contents."""
    at = list(parent).index(el)
    text = (el.text or "") + ""
    kids = list(el)
    tail = el.tail or ""
    parent.remove(el)
    for i, kid in enumerate(kids):
        parent.insert(at + i, kid)
    if kids:
        kids[-1].tail = (kids[-1].tail or "") + tail
    else:
        text += tail
    if at:
        prev = parent[at - 1]
        prev.tail = (prev.tail or "") + text
    else:
        parent.text = (parent.text or "") + text


def _restyle(el: ET.Element, tag: str, css: str | None, italic: bool) -> None:
    el.tag = tag
    el.attrib.clear()
    if css:
        el.set("class", css)
    if italic:
        inner = ET.Element("em")
        inner.text, el.text = el.text, None
        inner.extend(list(el))
        el[:] = [inner]


def _width(row: ET.Element) -> int:
    return sum(int(c.get("colspan", "1") or 1) for c in row)


def _trim(el: ET.Element) -> None:
    """Drop the line break bulk ends each run of text in a table with."""
    for node in el.iter():
        if node.text and node.text.endswith("\n"):
            node.text = node.text[:-1]
        if node is not el and node.tail and node.tail.endswith("\n"):
            node.tail = node.tail[:-1]
