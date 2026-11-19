"""The navigator for programs: Markdown and JSON pages, ``/llms.txt`` and an
OpenAPI description of the JSON, plus small helpers the pages share."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ecfr_md.config import DISCLAIMER, __version__
from ecfr_md.output import with_front_matter
from ecfr_md.params import as_hierarchy
from ecfr_md.results import TitleInfo

if TYPE_CHECKING:
    from ecfr_md.client import ECFR
    from ecfr_md.document import Node
    from ecfr_md.web.pages import Place

SIZE = re.compile(rb'"size"\s*:\s*(\d+)')
HEAD_BYTES = 4096
WORD = re.compile(r"[\w\u2019'-]+")


def cached_size(ecfr: ECFR, title: TitleInfo) -> int | None:
    """A title's size from its outline, if that is downloaded already;
    None rather than a request for it."""
    if title.latest is None:
        return None
    memo = ecfr._outlines.get((title.number, title.latest))
    if memo is not None:
        return memo.size
    for entry in ecfr._store.find(
        "structure", title.number, as_hierarchy({}), title.latest
    ):
        with entry.path.open("rb") as f:
            head = f.read(HEAD_BYTES)
        top = head.split(b'"children"', 1)[0]
        if found := SIZE.search(top):
            return int(found[1])
    return None


def cached_titles(ecfr: ECFR) -> list[int]:
    """Titles whose outlines are downloaded, so indexing them costs nothing."""
    found = []
    for title in ecfr.titles():
        if title.latest is None:
            continue
        memo = ecfr._outlines.get((title.number, title.latest)) is not None
        stored = ecfr._store.find(
            "structure", title.number, as_hierarchy({}), title.latest
        )
        if memo or stored:
            found.append(title.number)
    return found


def text_fragment(query: str) -> str:
    """A link fragment that highlights the words searched for (a text
    fragment, ``#:~:text=``), in browsers that support them."""
    words = query.strip().strip('"')
    if not words or len(words) > 80:
        return ""
    return "#:~:text=" + quote(words, safe="")


def highlight(text: str, query: str) -> str:
    """An excerpt as HTML with the words of the query marked."""
    words = {w.casefold() for w in WORD.findall(query) if len(w) > 2}
    out = []
    for piece in re.split(r"([\w\u2019'-]+)", text):
        escaped = html.escape(piece)
        out.append(f"<mark>{escaped}</mark>" if piece.casefold() in words else escaped)
    return "".join(out)


def _md(text: str) -> str:
    """Text safe inside a Markdown link label."""
    return re.sub(r"([\[\]\\*_`])", r"\\\1", text)


def home_markdown(titles: list[dict[str, Any]]) -> str:
    lines = ["# Code of Federal Regulations", ""]
    for t in titles:
        if t["reserved"]:
            lines.append(f"- Title {t['identifier']} [Reserved]")
            continue
        lines.append(
            f"- [{_md(t['citation'])} — {_md(t['heading'])}]({t['href']}.md)"
            f" (text as of {t['date']})"
        )
    lines += ["", f"*{DISCLAIMER}*", ""]
    return "\n".join(lines)


def contents_markdown(fields: dict[str, Any], place: Place) -> str:
    lines = [f"# {place.title}", ""]
    for row in place.contents():
        if not row.href:
            lines.append(f"- {_md(row.heading)}")
            continue
        count = f" ({row.sections} sections)" if row.sections > 1 else ""
        lines.append(f"- [{_md(row.heading)}]({row.href}.md){count}")
    return with_front_matter(_fields(fields), "\n".join(lines) + "\n")


def text_markdown(fields: dict[str, Any], node: Node, place: Place) -> str:
    return with_front_matter(_fields(fields), node.to_markdown())


def _fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v}


def search_markdown(
    query: str,
    within: str,
    results: list[dict[str, Any]],
    total: int | None,
    error: str,
) -> str:
    head = f"# Search: {query}" + (f" in {within}" if within else "")
    lines = [head, ""]
    if error:
        return "\n".join([*lines, error, ""])
    if not query:
        return "\n".join([*lines, "Search with ?q=words", ""])
    if total is not None:
        lines += [f"{total:,} matching versions; {len(results)} sections shown.", ""]
    for r in results:
        page = r["href"].split("#")[0]
        lines += [
            f"## [{_md(r['citation'])}]({page}.md) {_md(r['heading'])}",
            "",
            r["excerpt"],
            "",
        ]
    return "\n".join(lines)


def history_markdown(place: Place, changes: list[dict[str, Any]]) -> str:
    lines = [f"# Changes to {place.citation}", ""]
    if not changes:
        lines.append("No changes recorded.")
    for c in changes:
        lines.append(
            f"- {c['effective']} ({c['kind']}, issued {c['issued']}): "
            f"[{_md(c['citation'])}]({c['href']}.md) {_md(c['heading'])} — "
            f"[what changed]({c['compare']}.md)"
        )
    return "\n".join(lines) + "\n"


def llms_txt(base: str) -> str:
    """``/llms.txt``: what this server is and how a program reads it."""
    from ecfr_md.web.app import TEMPLATES

    return TEMPLATES.get_template("llms.txt").render(base=base)


def _get(
    summary: str, parameters: list[dict[str, Any]], description: str = ""
) -> dict[str, Any]:
    op: dict[str, Any] = {
        "summary": summary,
        "parameters": parameters,
        "responses": {
            "200": {
                "description": "JSON",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            "404": {
                "description": "Not in the CFR",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/Error"}
                    }
                },
            },
        },
    }
    if description:
        op["description"] = description
    return {"get": op}


def _param(
    name: str, where: str, description: str, required: bool = False, example: str = ""
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "name": name,
        "in": where,
        "required": required or where == "path",
        "description": description,
        "schema": {"type": "string"},
    }
    if example:
        p["example"] = example
    return p


PATH = _param(
    "path",
    "path",
    "A place, as in eCFR page paths: title-45, title-45/part-164/subpart-E, "
    "title-45/section-164.502",
    example="title-45/section-164.502",
)


def openapi(base: str) -> dict[str, Any]:
    """An OpenAPI 3.1 description of the JSON pages."""
    date = _param("date", "path", "A date, YYYY-MM-DD", example="2020-01-01")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "eCFR navigator",
            "version": __version__,
            "description": (
                "Read, search and compare the Code of Federal Regulations. Every "
                "HTML page is also JSON at the same path with .json. " + DISCLAIMER
            ),
        },
        "servers": [{"url": base}],
        "paths": {
            "/index.json": _get("Every title, with the date of its latest text", []),
            "/current/{path}.json": _get(
                "A place's current text or contents",
                [PATH, _param("full", "query", "1 for the text of a large part")],
                "Contents for titles and divisions above parts; text from parts down. "
                "Includes breadcrumb, previous, next and up.",
            ),
            "/on/{date}/{path}.json": _get("A place's text on a date", [date, PATH]),
            "/history/{path}.json": _get(
                "The changes issued to a place, newest first", [PATH]
            ),
            "/compare/{later}/to/{earlier}/{path}.json": _get(
                "What changed between two dates, word by word",
                [
                    _param("later", "path", "The later date", example="2024-12-31"),
                    _param("earlier", "path", "The earlier date", example="2013-01-01"),
                    PATH,
                ],
            ),
            "/search.json": _get(
                "Full-text search",
                [
                    _param(
                        "q",
                        "query",
                        "Words to find; quoted for a phrase",
                        True,
                        "minimum necessary",
                    ),
                    _param(
                        "within",
                        "query",
                        "A citation to search within",
                        example="45 CFR Part 164",
                    ),
                    _param("limit", "query", "Most results, up to 200"),
                ],
            ),
            "/go.json": _get(
                "Resolve any citation, address or link",
                [
                    _param(
                        "cite",
                        "query",
                        "e.g. 45 CFR 164.502(a)(1), 45/164.502, an eCFR or LII link",
                        True,
                        "45 CFR 164.502(a)(1)",
                    ),
                    _param("date", "query", "A date for the page"),
                ],
            ),
            "/api/terms": _get(
                "Defined terms that apply to a place",
                [
                    _param("address", "query", "A place", True, "45/164.502"),
                    _param("date", "query", "A date"),
                ],
            ),
            "/api/preview": _get(
                "The heading and first lines of a page",
                [
                    _param(
                        "href",
                        "query",
                        "A page path",
                        True,
                        "/current/title-45/section-164.501",
                    )
                ],
            ),
            "/api/index/{title}": _get(
                "Every citation and heading in a title",
                [_param("title", "path", "A title number", example="45")],
            ),
            "/settings.json": _get(
                "The server's settings and where each came from", []
            ),
        },
        "components": {
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "status": {"type": "integer"},
                    },
                }
            }
        },
    }
