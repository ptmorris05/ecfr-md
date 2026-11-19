"""Saving nodes and downloaded text in every format.

One file holds a whole node. In a tree, text, HTML (and Markdown) get a file
per section or appendix plus an index page per level, laid out as Markdown
trees are; JSON, EPUB, DOCX and PDF get a file per part. Both are written
while the XML is read, so a tree of a whole title never needs it all in
memory at once.
"""

import os
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import IO

from ecfr_md import markdown
from ecfr_md.address import Address
from ecfr_md.document import Division, Node, _division
from ecfr_md.output import TYPE_LEVELS, Source, file_name
from ecfr_md.params import Hierarchy, as_hierarchy
from ecfr_md.render import json_writer, text_writer
from ecfr_md.render.blocks import (
    Block,
    Heading,
    event_blocks,
    from_markdown,
    node_blocks,
    target,
)
from ecfr_md.render.formats import PER_SECTION, SUFFIXES, Format, require
from ecfr_md.render.html_writer import page

Events = Iterable[tuple[str, ET.Element]]
OnFile = Callable[[Path], object] | None
ABOVE_PART = frozenset({"TITLE", "SUBTITLE", "CHAPTER", "SUBCHAP", "SUBJGRP"})


def export(
    node: Node,
    path: str | Path,
    format: Format,
    *,
    tree: bool = False,
    front_matter: bool = True,
) -> Path | list[Path]:
    """Save a node in a format other than Markdown; see :meth:`Node.save`."""
    require(format)
    if tree and isinstance(node, Division):
        base = node.parent.hierarchy if node.parent else node.source.hierarchy
        return save_events(
            markdown.element_events(node.element),
            node.source,
            Path(path),
            format,
            tree=True,
            front_matter=front_matter,
            base=base,
        )
    file = _target(path, node._address(), node.source, format)
    write(format, node_blocks(node), file, front_matter=front_matter)
    return [file] if tree else file


def render(node: Node, format: Format, *, front_matter: bool = True) -> str:
    """A node as text, HTML or JSON, or Markdown."""
    if format == "markdown":
        return node.to_markdown()
    blocks = node_blocks(node)
    if format == "text":
        return "".join(text_writer.document(blocks, front_matter=front_matter))
    if format == "html":
        return "".join(page(blocks))
    return json_writer.dumps(blocks)


def write(
    format: Format,
    blocks: Iterable[Block],
    path: Path,
    *,
    front_matter: bool = True,
    up: str | None = None,
    links: str | None = None,
) -> Path:
    """Write blocks to a file in a format other than Markdown, atomically."""
    require(format)
    with _atomic(path) as out:
        if format == "text":
            for chunk in text_writer.document(blocks, front_matter=front_matter):
                out.write(chunk.encode("utf-8"))
        elif format == "html":
            for chunk in page(blocks, up=up, links=links):
                out.write(chunk.encode("utf-8"))
        elif format == "json":
            out.write(json_writer.dumps(blocks).encode("utf-8"))
        elif format == "epub":
            from ecfr_md.render import epub_writer

            epub_writer.write(blocks, out)
        elif format == "docx":
            from ecfr_md.render import docx_writer

            docx_writer.write(blocks, out)
        else:
            from ecfr_md.render import pdf_writer

            pdf_writer.write(blocks, out)
    return path


def save_events(
    events: Events,
    source: Source,
    path: Path,
    format: Format,
    *,
    tree: bool = False,
    front_matter: bool = True,
    free: bool = False,
    base: Hierarchy | None = None,
    on_file: OnFile = None,
    names: list[str] | None = None,
) -> list[Path]:
    """Write a stream of XML events in a format other than Markdown, as
    :func:`ecfr_md.output.save_events` writes Markdown, and return the files.

    A tree of text or HTML takes the same ``names`` (from
    :func:`markdown.tree_names`); without them, the events are read into
    memory first.
    """
    require(format)
    base = source.hierarchy if base is None else base
    if not tree:
        file = _target(path, Address(source.title, base), source, format)
        blocks = event_blocks(events, source, base, free=free)
        return [
            _notify(on_file, write(format, blocks, file, front_matter=front_matter))
        ]
    if format not in PER_SECTION:
        return list(
            _parts(events, source, path, format, front_matter, free, base, on_file)
        )
    if names is None:
        events = list(events)
        names = markdown.tree_names(events)
    written: list[Path] = []
    suffix = SUFFIXES[format]

    def emit(
        relative: PurePosixPath, body: str, chain: list[ET.Element], _heading: str
    ) -> None:
        above = _levels(base, chain[:-1])
        up = None
        if relative.name == markdown.INDEX_FILE:
            file = path / relative.with_name(f"index{suffix}")
            blocks = _index(body, chain[-1], replace(source, hierarchy=above))
            if len(chain) > 1:
                up = f"../index{suffix}"
        else:
            file = path / relative.with_suffix(suffix)
            blocks = event_blocks(markdown.element_events(chain[-1]), source, above)
            if len(chain) > 1:
                up = f"index{suffix}"
        done = write(
            format, blocks, file, front_matter=front_matter, up=up, links=suffix
        )
        written.append(_notify(on_file, done))

    markdown.write_tree(events, emit, names=names, free=free)
    return written


def _index(body: str, element: ET.Element, source: Source) -> list[Block]:
    """An index page: the division's heading, its notes and its contents."""
    blocks = from_markdown(body)
    node = _division(element, None, source)
    first = blocks[0]
    assert isinstance(first, Heading)  # write_tree starts indexes with one.
    blocks[0] = replace(first, level=1, target=target(node, document=True))
    return blocks


def _parts(
    events: Events,
    source: Source,
    path: Path,
    format: Format,
    front_matter: bool,
    free: bool,
    base: Hierarchy,
    on_file: OnFile,
) -> Iterator[Path]:
    """One file per part, in folders named as in a Markdown tree.

    Divisions outside parts (a subpart or section saved on its own, an
    appendix to a chapter) get a file of their own.
    """
    stack: list[ET.Element] = []
    starts: list[int] = []  # Files written before each open division began.
    written = 0
    for event, el in events:
        division = markdown.is_division(el)
        if event == "start":
            stack.append(el)
            if division:
                starts.append(written)
            continue
        stack.pop()
        if not division:
            continue
        empty = starts.pop() == written  # No file for anything inside it.
        ancestors = [e for e in stack if markdown.is_division(e)]
        kind = el.get("TYPE")
        above = all(e.get("TYPE") in ABOVE_PART for e in ancestors)
        alone = not ancestors or (above and kind in markdown.LEAF_TYPES)
        if kind == "PART" or (empty and alone):
            folders = [
                markdown.segment(e) for e in ancestors if e.get("TYPE") != "SUBJGRP"
            ]
            file = path.joinpath(*folders, markdown.segment(el) + SUFFIXES[format])
            blocks = event_blocks(
                markdown.element_events(el), source, _levels(base, ancestors)
            )
            written += 1
            yield _notify(
                on_file, write(format, blocks, file, front_matter=front_matter)
            )
            if free and stack:
                stack[-1].remove(el)


def _levels(base: Hierarchy, divisions: list[ET.Element]) -> Hierarchy:
    """The hierarchy of a chain of divisions, below ``base``."""
    levels = dict(base)
    for el in divisions:
        if (kind := TYPE_LEVELS.get(el.get("TYPE", ""))) is not None:
            levels[kind] = el.get("N", "")
    return as_hierarchy(levels)


def _target(path: str | Path, address: Address, source: Source, format: Format) -> Path:
    """The file to write: ``path``, or in a directory a file named after the address."""
    if Path(path).is_dir() or str(path).endswith(("/", "\\")):
        name = file_name(address, source).removesuffix(".md") + SUFFIXES[format]
        return Path(path) / name
    return Path(path)


def _notify(on_file: OnFile, path: Path) -> Path:
    if on_file is not None:
        on_file(path)
    return path


@contextmanager
def _atomic(path: Path) -> Iterator[IO[bytes]]:
    """A binary file written under a temporary name and renamed when complete."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    try:
        with partial.open("wb") as out:
            yield out
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
