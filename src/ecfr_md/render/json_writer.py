"""JSON: the node tree, each node with its own text.

The document is its top node: ``level``, ``identifier``, ``heading``,
``citation``, ``address``, ``url``, ``text`` and ``children``, plus
``provenance``. ``text`` is a node's own Markdown, without its children's:
a part's authority and source notes, a section's whole text. Sections and
appendices are the leaves.
"""

import json
from collections.abc import Iterable
from typing import Any

from ecfr_md.render.blocks import Block, Heading, to_markdown


def tree(blocks: Iterable[Block]) -> dict[str, Any]:
    """The node tree of a document's blocks."""
    root: dict[str, Any] | None = None
    stack: list[tuple[int, dict[str, Any], list[str]]] = []
    for block in blocks:
        if isinstance(block, Heading) and block.target is not None:
            target = block.target
            node: dict[str, Any] = {
                "level": target.level,
                "identifier": target.identifier,
                "heading": target.heading,
                "citation": target.citation,
                "address": target.address,
                "url": target.url,
                "text": [],
                "children": [],
            }
            if target.provenance:
                node["provenance"] = target.provenance
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            if stack:
                stack[-1][1]["children"].append(node)
            else:
                root = root or node
            stack.append((block.level, node, node["text"]))
        elif stack:
            stack[-1][2].append(to_markdown(block))
    if root is None:
        return {}
    _join(root)
    return root


def _join(node: dict[str, Any]) -> None:
    node["text"] = "\n\n".join(node["text"])
    for child in node["children"]:
        _join(child)


def dumps(blocks: Iterable[Block]) -> str:
    """The tree as indented JSON."""
    return json.dumps(tree(blocks), ensure_ascii=False, indent=2) + "\n"
