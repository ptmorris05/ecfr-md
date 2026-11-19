"""The web navigator: a Starlette app over one :class:`~ecfr_md.client.ECFR`.

Every page is HTML for people, and the same path with ``.md`` or ``.json``
for programs. Text is fetched on first view through the client's cache, so
whatever has been read is there next time, online or not.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import re
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jinja2
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from ecfr_md.address import Address
from ecfr_md.client import ECFR
from ecfr_md.compare import aligned
from ecfr_md.compare import compare as compare_texts
from ecfr_md.config import DISCLAIMER, Settings, __version__
from ecfr_md.document import Node
from ecfr_md.errors import (
    DateOutOfRangeError,
    ECFRConnectionError,
    ECFRError,
    ECFRTimeoutError,
    InvalidCitationError,
    InvalidParameterError,
    MissingDependencyError,
    NotFoundError,
    ServerError,
)
from ecfr_md.params import to_date
from ecfr_md.render import json_writer
from ecfr_md.render.blocks import node_blocks
from ecfr_md.render.formats import EXTRAS, FORMATS, SUFFIXES, Format, as_format, require
from ecfr_md.results import Change
from ecfr_md.web import agents, pages, prefs, reader
from ecfr_md.web.progress import Relay

logger = logging.getLogger("ecfr_md.web")

HERE = Path(__file__).parent
STATIC = HERE / "static"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
SAFE_FETCH_SITES = frozenset({"same-origin", "none"})
OFFLINE = (ECFRConnectionError, ECFRTimeoutError, ServerError)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# A citation with a date after it: "45 CFR 164.502 as of 2019", "... on 2020-01-01".
AS_OF = re.compile(r"(?P<cite>.*\d.*?)\s+(?:as\s+of|on)\s+(?P<date>.+)", re.I)
SEARCH_LIMIT = 25
PREVIEW_CHARS = 900
CHANGES_TTL = 600  # Seconds a place's list of changes is reused.
FORMAT_NAMES = {
    "markdown": "Markdown",
    "text": "Plain text",
    "html": "HTML",
    "json": "JSON",
    "epub": "EPUB",
    "docx": "Word",
    "pdf": "PDF",
}


class Navigator:
    """What the app serves from: the client (replaced when settings change)
    and where exports are saved."""

    def __init__(
        self,
        ecfr: ECFR,
        save_dir: Path | None = None,
        factory: Callable[[Settings], ECFR] = ECFR,
    ) -> None:
        self.relay = Relay(ecfr.progress)
        self.factory = factory
        ecfr.progress = self.relay
        self.ecfr = ecfr
        self.save_dir = save_dir
        self.retired: list[ECFR] = []  # Clients replaced by new settings.
        self._terms: dict[tuple[str, dt.date | None], list[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._changes: dict[str, tuple[float, list[Change]]] = {}
        self._owned: list[ECFR] = []  # Clients this navigator made, to close.
        self.agents: Any = None  # The MCP server's navigator, when mounted.

    def apply(self, settings: Settings) -> None:
        """Use new settings from now on; requests under way finish with
        the old client, which is closed when the server stops."""
        fresh = self.factory(settings)
        fresh.progress = self.relay
        with self._lock:
            self.retired.append(self.ecfr)
            self.ecfr = fresh
            if self.agents is not None:  # The MCP server reads through it too.
                self.agents.ecfr = fresh
                fresh.progress = self.agents.relay
            self._owned.append(fresh)
            self._terms.clear()
            self._changes.clear()

    def changes(self, address: str) -> list[Change]:
        """The changes to a place, kept for a few minutes: every page asks
        for its own, and they change at most daily."""
        now = time.monotonic()
        kept = self._changes.get(address)
        if kept is None or now - kept[0] > CHANGES_TTL:
            kept = self._changes[address] = (now, self.ecfr.changes(address))
        return kept[1]

    def terms(self, address: str, date: dt.date | None) -> list[dict[str, Any]]:
        """The defined terms that apply to a place, kept for the session."""
        key = (address, date)
        if key not in self._terms:
            found = self.ecfr.definitions(address, date=date)
            self._terms[key] = [
                {
                    **d.to_dict(),
                    "scope": d.applies_to or d.section,
                    "href": pages.href(d.address, date),
                    "html": _definition_html(d.text, date),
                }
                for d in found
            ]
        return self._terms[key]

    def close(self) -> None:
        """Close the clients made here; the one passed in is its owner's."""
        for client in self._owned:
            client.close()


def _definition_html(markdown: str, date: dt.date | None) -> str:
    from ecfr_md.render.blocks import from_markdown

    return "".join(reader.render(from_markdown(markdown), date))


# Templates -------------------------------------------------------------------


def _environment() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("ecfr_md.web", "templates"),
        autoescape=jinja2.select_autoescape(("html",)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        version=__version__,
        disclaimer=DISCLAIMER,
        formats=FORMAT_NAMES,
    )
    env.filters["size"] = pages.size
    env.filters["tojson_attr"] = lambda v: json.dumps(v, ensure_ascii=False)
    env.filters["longdate"] = _long_date
    return env


def _long_date(value: dt.date | str) -> str:
    day = dt.date.fromisoformat(value) if isinstance(value, str) else value
    return f"{day:%B} {day.day}, {day.year}"


TEMPLATES = _environment()


def _html(name: str, code: int = 200, **context: Any) -> HTMLResponse:
    return HTMLResponse(TEMPLATES.get_template(name).render(**context), code)


def _stream(name: str, **context: Any) -> StreamingResponse:
    """A page sent as it is rendered, so the top shows while text loads."""
    return StreamingResponse(
        TEMPLATES.get_template(name).generate(**context),
        media_type="text/html; charset=utf-8",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _markdown(text: str, status: int = 200) -> Response:
    return PlainTextResponse(text, status, media_type="text/markdown; charset=utf-8")


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status)


# Pages -----------------------------------------------------------------------


def _nav(request: Request) -> Navigator:
    return request.app.state.navigator


def home(request: Request) -> Response:
    nav = _nav(request)
    variant = request.path_params.get("variant", "")
    titles = nav.ecfr.titles()
    rows = [
        {
            **t.to_dict(),
            "href": f"/current/title-{t.number}",
            "size": agents.cached_size(nav.ecfr, t),
        }
        for t in titles
    ]
    if variant == ".json":
        return _json({"titles": rows, **_links("/index")})
    if variant == ".md":
        return _markdown(agents.home_markdown(rows))
    return _html("home.html", titles=rows, page=None, official="https://www.ecfr.gov/")


def _links(path: str, query: str = "") -> dict[str, Any]:
    q = f"?{query}" if query else ""
    return {
        "links": {"html": path + q, "md": f"{path}.md{q}", "json": f"{path}.json{q}"}
    }


def _page_date(request: Request) -> dt.date | None:
    text = request.path_params.get("date")
    if text is None:
        return None
    if not DATE.fullmatch(text):
        raise HTTPException(404, f"{text!r} is not a date; dates are YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise HTTPException(404, f"{text} is not a date") from None


def text_page(request: Request) -> Response:
    """A title, division, part, section or appendix: its contents or text."""
    nav = _nav(request)
    rest, variant = pages.split_variant(request.path_params["rest"])
    date = _page_date(request)
    try:
        address = pages.parse_page(f"/current/{rest}")
    except InvalidCitationError as exc:
        raise HTTPException(404, str(exc)) from None
    latest = _latest(nav.ecfr, address.title)
    if date is not None and latest is not None and date >= latest:
        target = pages.href(address) + variant
        return RedirectResponse(_with_query(target, request), 307)
    place = pages.place(nav.ecfr, address, date)
    if request.query_params.get("toc") and variant == "":
        return _html("toc_rows.html", rows=place.contents(), place=place)
    full = bool(request.query_params.get("full"))
    contents = place.contents_only and not full
    if variant == ".json":
        return _json(_page_json(nav, place, contents))
    if variant == ".md":
        return _markdown(_page_markdown(nav, place, contents))
    context = _page_context(request, place)
    if contents:
        return _html("contents.html", **context, rows=place.contents())
    return _stream(
        "read.html",
        **context,
        rows=place.contents() if place.level == "part" else [],
        text=_text_stream(nav, place),
    )


def _with_query(path: str, request: Request) -> str:
    return f"{path}?{request.url.query}" if request.url.query else path


def _latest(ecfr: ECFR, title: int) -> dt.date | None:
    info = next((t for t in ecfr.titles() if t.number == title), None)
    if info is None:
        raise HTTPException(404, f"Title {title} does not exist")
    return info.latest


def _page_context(request: Request, place: pages.Place) -> dict[str, Any]:
    neighbours = place.neighbours()
    base = place.href.split("#")[0]
    return {
        "page": place,
        "crumbs": place.crumbs(),
        "neighbours": neighbours,
        "official": place.official,
        "alternates": _links(base)["links"],
        "cite": pages.citations(place.address, place.text_date),
        "export": _export_links(place),
        "save_dir": _nav(request).save_dir,
        "embed": bool(request.query_params.get("embed")),
        "prerender": [n.href for k, n in neighbours.items() if n and k != "up"],
    }


def _export_links(place: pages.Place) -> list[dict[str, Any]]:
    path = f"/export/{place.rest}"
    query = {"date": place.date.isoformat()} if place.date else {}
    found = []
    for name in FORMATS:
        available = True
        if name in EXTRAS:
            try:
                require(name)
            except MissingDependencyError:
                available = False
        found.append(
            {
                "name": name,
                "label": FORMAT_NAMES[name],
                "suffix": SUFFIXES[name],
                "href": f"{path}?{urlencode({**query, 'format': name})}",
                "action": f"{path}?{urlencode(query)}" if query else path,
                "available": available,
                "extra": EXTRAS.get(name, ("", ""))[1],
            }
        )
    return found


def _text_stream(nav: Navigator, place: pages.Place) -> Iterator[Markup]:
    """The text of a page as HTML, after progress marks while it downloads.

    The download runs in a thread that reports progress here; each step
    moves the hairline under the breadcrumb (a ``<style>`` the browser
    applies as it arrives).
    """
    events: queue.Queue[tuple[str, Any]] = queue.Queue()
    ecfr = nav.ecfr

    def fetch() -> None:
        try:
            with nav.relay.listen(lambda f: events.put(("progress", f))):
                node = ecfr.get(str(place.address), date=place.date)
            events.put(("node", node))
        except Exception as exc:  # Shown in the page, which has started.
            events.put(("error", exc))

    threading.Thread(target=fetch, daemon=True, name="ecfr-md page").start()
    shown = 0.0
    while True:
        kind, value = events.get()
        if kind == "progress":
            if value - shown >= 0.04:
                shown = value
                yield Markup(f"<style>:root{{--load:{value:.2f}}}</style>\n")
            continue
        if kind == "error":
            yield Markup(
                TEMPLATES.get_template("text_error.html").render(
                    error=_message(value), page=place
                )
            )
            return
        break
    node: Node = value
    yield Markup("<style>:root{--load:1}</style>\n")
    year = place.text_date.year if place.text_date else None
    for chunk in reader.render(node_blocks(node), place.date, year):
        yield Markup(chunk)


def _message(error: BaseException) -> str:
    if isinstance(error, OFFLINE):
        return (
            "The eCFR can't be reached, and this text isn't in the cache yet. "
            "It will be here once it has been read online."
        )
    return str(error)


def _page_json(nav: Navigator, place: pages.Place, contents: bool) -> dict[str, Any]:
    data = place.to_dict()
    data |= _links(place.href.split("#")[0])
    data["citations"] = pages.citations(place.address, place.text_date)
    if contents or place.level == "part":
        data["contents"] = [r.to_dict() for r in place.contents()]
    if not contents:
        node = nav.ecfr.get(str(place.address), date=place.date)
        data["text"] = json_writer.tree(node_blocks(node))
    return data


def _page_markdown(nav: Navigator, place: pages.Place, contents: bool) -> str:
    fields = {
        "citation": place.citation,
        "date": place.text_date.isoformat() if place.text_date else None,
        "url": place.official,
        **{k: (f"{v.href}.md" if v else None) for k, v in place.neighbours().items()},
    }
    if contents:
        return agents.contents_markdown(fields, place)
    node = nav.ecfr.get(str(place.address), date=place.date)
    return agents.text_markdown(fields, node, place)


def go(request: Request) -> Response:
    """Any citation, address or link: redirect to its page."""
    cite = request.query_params.get("cite", "").strip()
    variant = request.path_params.get("variant", "")
    date = request.query_params.get("date", "").strip()
    dated = AS_OF.fullmatch(cite)
    if dated and not date:  # "164.502 as of 2019"
        cite, date = dated["cite"], dated["date"]
    try:
        address, target = pages.go(cite)
        when = to_date(date) if date else address.date
    except (InvalidCitationError, ValueError) as exc:
        if variant == ".json":
            return _json({"error": str(exc), "cite": cite}, 400)
        if not cite:
            return RedirectResponse("/", 303)
        query = urlencode({"q": cite})
        return RedirectResponse(f"/search?{query}", 303)
    if when is not None:
        target = pages.href(replace(address, paragraph=()), when) + (
            "#" + target.split("#", 1)[1] if "#" in target else ""
        )
    if variant == ".json":
        return _json(
            {
                "cite": cite,
                "address": str(replace(address, date=when)),
                "citation": address.citation(),
                "href": target,
                "url": replace(address, date=when).url(),
            }
        )
    return RedirectResponse(target, 303)


def search(request: Request) -> Response:
    nav = _nav(request)
    variant = request.path_params.get("variant", "")
    query = request.query_params.get("q", "").strip()
    within = request.query_params.get("within", "").strip()
    try:
        limit = min(max(int(request.query_params.get("limit", SEARCH_LIMIT)), 1), 200)
    except ValueError:
        limit = SEARCH_LIMIT
    results: list[dict[str, Any]] = []
    total = None
    error = ""
    if query:
        try:
            filters: dict[str, Any] = {"within": within} if within else {}
            matches = nav.ecfr.search(query, limit=limit, **filters)
            total = matches.total
            results = [
                {
                    **m.to_dict(),
                    "href": pages.href(m.address) + agents.text_fragment(query),
                    "html": agents.highlight(m.excerpt, query),
                }
                for m in matches
            ]
        except (InvalidParameterError, InvalidCitationError) as exc:
            error = str(exc)
    params = urlencode({k: v for k, v in (("q", query), ("within", within)) if v})
    if variant == ".json":
        return _json(
            {
                "query": query,
                "within": within or None,
                "total": total,
                "results": results,
                "error": error or None,
                **_links("/search", params),
            }
        )
    if variant == ".md":
        return _markdown(agents.search_markdown(query, within, results, total, error))
    return _html(
        "search.html",
        query=query,
        within=within,
        results=results,
        total=total,
        error=error,
        page=None,
        official="https://www.ecfr.gov/search",
        alternates=_links("/search", params)["links"],
    )


def history(request: Request) -> Response:
    """The changes the eCFR has issued to a place, newest first."""
    nav = _nav(request)
    rest, variant = pages.split_variant(request.path_params["rest"])
    try:
        address = pages.parse_page(f"/current/{rest}")
    except InvalidCitationError as exc:
        raise HTTPException(404, str(exc)) from None
    place = pages.place(nav.ecfr, address, None)
    changes = nav.changes(str(address))
    rows = []
    for change in changes:
        before = change.effective - dt.timedelta(days=1)
        rows.append(
            {
                **change.to_dict(),
                "href": pages.href(change.address, change.effective),
                "compare": _compare_href(change.address, change.effective, before),
            }
        )
    dates = sorted({c.effective for c in changes}, reverse=True)
    base = f"/history/{rest}"
    if variant == ".json":
        return _json(
            {
                **place.to_dict(),
                "dates": [d.isoformat() for d in dates],
                "changes": rows,
                **_links(base),
            }
        )
    if variant == ".md":
        return _markdown(agents.history_markdown(place, rows))
    return _html(
        "history.html",
        **_page_context(request, place),
        changes=rows,
        dates=dates,
    )


def _compare_href(address: str | Address, later: dt.date, earlier: dt.date) -> str:
    path = pages.href(address).removeprefix("/current/")
    return f"/compare/{later}/to/{earlier}/{path}"


def compare(request: Request) -> Response:
    """A redline of a place between two dates."""
    nav = _nav(request)
    rest, variant = pages.split_variant(request.path_params["rest"])
    try:
        later = to_date(request.path_params["later"])
        earlier = to_date(request.path_params["earlier"])
        address = pages.parse_page(f"/current/{rest}")
    except (ValueError, InvalidCitationError) as exc:
        raise HTTPException(404, str(exc)) from None
    if earlier > later:
        target = f"/compare/{earlier}/to/{later}/{rest}{variant}"
        return RedirectResponse(target, 307)
    place = pages.place(nav.ecfr, address, None)
    if place.contents_only:
        raise HTTPException(
            400, f"{place.citation} is too large to compare; compare a part or section"
        )
    before = nav.ecfr.get(str(address), date=earlier)
    after = nav.ecfr.get(str(address), date=later)
    assert before.date is not None and after.date is not None  # Set by get().
    blocks = aligned(before.to_markdown(), after.to_markdown())
    found = compare_texts(
        after.citation,
        before.to_markdown(),
        after.to_markdown(),
        before.date,
        after.date,
    )
    base = f"/compare/{later}/to/{earlier}/{rest}"
    if variant == ".json":
        return _json({**place.to_dict(), **found.to_dict(), **_links(base)})
    if variant == ".md":
        return _markdown(found.to_markdown())
    kinds = [d.kind for d in found.differences]
    return _html(
        "compare.html",
        **_page_context(request, place),
        since=before.date,
        until=after.date,
        counts={k: kinds.count(k) for k in ("changed", "added", "removed")},
        unchanged=found.unchanged,
        redline=Markup(
            "".join(
                reader.redline(
                    blocks,
                    None if after.date == place.text_date else after.date,
                    context=address.undated(),
                    paragraphs=[*node_blocks(before), *node_blocks(after)],
                )
            )
        ),
        later_href=pages.href(address, after.date),
        earlier_href=pages.href(address, before.date),
    )


# Exports ---------------------------------------------------------------------


def export(request: Request) -> Response:
    """The export menu of a place, or with ``?format=`` the file to download."""
    nav = _nav(request)
    address, date = _export_place(request)
    name = request.query_params.get("format", "")
    if not name:
        place = pages.place(nav.ecfr, address, date)
        return _html("export.html", **_page_context(request, place))
    format = _format(name)
    directory = Path(tempfile.mkdtemp(prefix="ecfr-md-"))
    try:
        path = nav.ecfr.save(str(address), f"{directory}/", format=format, date=date)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return FileResponse(
        path,
        filename=path.name,
        background=BackgroundTask(shutil.rmtree, directory, True),
    )


async def _form(request: Request) -> dict[str, str]:
    """A submitted form (URL-encoded, as these pages send them)."""
    body = (await request.body()).decode("utf-8", "replace")
    return {k: v[-1] for k, v in parse_qs(body, keep_blank_values=True).items()}


async def export_save(request: Request) -> Response:
    """Save a place in a format to the --save-dir."""
    form = await _form(request)
    format = _format(str(form.get("format", "")))
    return await run_in_threadpool(_save, request, format)


def _save(request: Request, format: Format) -> Response:
    nav = _nav(request)
    address, date = _export_place(request)
    if nav.save_dir is None:
        raise HTTPException(
            400, "Start the server with --save-dir DIR to save files on this computer"
        )
    nav.save_dir.mkdir(parents=True, exist_ok=True)
    path = nav.ecfr.save(str(address), f"{nav.save_dir}/", format=format, date=date)
    result = {"saved": str(path), "format": format, "bytes": path.stat().st_size}
    if "application/json" in request.headers.get("accept", ""):
        return _json(result)
    return _html("saved.html", saved=result, page=None, official="")


def _export_place(request: Request) -> tuple[Address, dt.date | None]:
    try:
        address = pages.parse_page(f"/current/{request.path_params['rest']}")
        text = request.query_params.get("date", "")
        return address, to_date(text) if text else None
    except (ValueError, InvalidCitationError) as exc:
        raise HTTPException(400, str(exc)) from None


def _format(name: str) -> Format:
    try:
        format = as_format(name)
        require(format)
    except (InvalidParameterError, MissingDependencyError) as exc:
        raise HTTPException(400, str(exc)) from None
    return format


# Settings --------------------------------------------------------------------


def settings_page(request: Request) -> Response:
    nav = _nav(request)
    variant = request.path_params.get("variant", "")
    views = prefs.fields(nav.ecfr.settings)
    if variant == ".json":
        return _json(
            {
                "settings": [v.to_dict() for v in views],
                "config_file": str(nav.ecfr.settings.config_file),
            }
        )
    return _html(
        "settings.html",
        fields=views,
        problems={},
        saved="",
        config=nav.ecfr.settings.config_file,
        page=None,
        official="",
    )


async def settings_post(request: Request) -> Response:
    form = await _form(request)
    nav = _nav(request)
    values = {k: v for k, v in form.items() if k != "write"}
    try:
        settings, changed = prefs.parse(values, nav.ecfr.settings)
    except prefs.Invalid as exc:
        views = prefs.fields(nav.ecfr.settings)
        return _html(
            "settings.html",
            code=400,
            fields=views,
            problems=exc.problems,
            saved="",
            config=nav.ecfr.settings.config_file,
            page=None,
            official="",
        )
    if changed:
        nav.apply(settings)
    saved = ""
    if "write" in form:
        saved = str(prefs.write_config(settings, settings.config_file))
    return _html(
        "settings.html",
        fields=prefs.fields(nav.ecfr.settings),
        problems={},
        saved=saved,
        applied=sorted(changed),
        config=nav.ecfr.settings.config_file,
        page=None,
        official="",
    )


# JSON for the page's scripts ----------------------------------------------------


def preview(request: Request) -> Response:
    """A cross-reference's target, for its hover card: heading and first lines."""
    nav = _nav(request)
    target = request.query_params.get("href", "")
    parts = urlsplit(target)
    try:
        address = pages.parse_page(parts.path)
        section = address.hierarchy.get("section")
        if section and parts.fragment.startswith(f"p-{section}("):
            markers = re.findall(r"\((\w+)\)", parts.fragment[len(section) + 2 :])
            address = replace(address, paragraph=tuple(markers))
        node = nav.ecfr.get(str(address.undated()), date=address.date)
    except ECFRError as exc:
        return _json({"error": str(exc)}, 404)
    blocks = _trim(list(node_blocks(node))[1:])
    html = "".join(reader.render(blocks, address.date))
    return _json(
        {
            "citation": node.citation,
            "heading": node.heading,
            "href": target,
            "url": node.url,
            "html": html,
            "more": len(node.to_markdown()) > PREVIEW_CHARS,
        }
    )


def _trim(blocks: list[Any]) -> list[Any]:
    """The first blocks of a text, up to about ``PREVIEW_CHARS`` characters."""
    kept, used = [], 0
    for block in blocks:
        kept.append(block)
        used += len(getattr(block, "text", "") or "")
        if used >= PREVIEW_CHARS:
            break
    return kept


def terms(request: Request) -> Response:
    """The defined terms that apply to a place, with their definitions."""
    nav = _nav(request)
    address = request.query_params.get("address", "")
    date_text = request.query_params.get("date", "")
    try:
        date = to_date(date_text) if date_text else None
        found = nav.terms(address, date)
    except (InvalidCitationError, ValueError, ECFRError) as exc:
        return _json({"terms": [], "error": str(exc)})
    return _json({"address": address, "terms": found})


def index(request: Request) -> Response:
    """Citations and headings for the command palette: of one title, or
    the list of titles whose outlines are already downloaded."""
    nav = _nav(request)
    title = request.path_params.get("title")
    if title is None:
        return _json({"titles": agents.cached_titles(nav.ecfr)})
    try:
        root = nav.ecfr.outline()[str(title)]
        entries = list(pages.index_entries(root))
    except ECFRError as exc:
        return _json({"error": str(exc), "entries": []}, 404)
    return _json({"title": title, "entries": entries})


# For agents ----------------------------------------------------------------------


def llms(request: Request) -> Response:
    return PlainTextResponse(agents.llms_txt(str(request.base_url).rstrip("/")))


def openapi(request: Request) -> Response:
    return _json(agents.openapi(str(request.base_url).rstrip("/")))


def service_worker(request: Request) -> Response:
    body = (
        (STATIC / "sw.js")
        .read_text(encoding="utf-8")
        .replace("__VERSION__", __version__)
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def favicon(request: Request) -> Response:
    return Response(status_code=204)


# Errors ----------------------------------------------------------------------------


async def _error(request: Request, exc: Exception) -> Response:
    status = 500
    message = str(exc)
    if isinstance(exc, HTTPException):
        status, message = exc.status_code, str(exc.detail)
    elif isinstance(exc, (LookupError, NotFoundError, DateOutOfRangeError)):
        status = 404
    elif isinstance(exc, InvalidParameterError):
        status = 400
    elif isinstance(exc, OFFLINE):
        status, message = 503, _message(exc)
    elif isinstance(exc, ECFRError):
        status = 502
    else:
        logger.error("Error serving %s", request.url.path, exc_info=exc)
        message = "Something went wrong on this server; its log has the details."
    path = request.url.path
    if (
        path.endswith(".json")
        or path.startswith("/api/")
        or "json" in request.headers.get("accept", "")[:20]
    ):
        return _json({"error": message, "status": status}, status)
    if path.endswith(".md"):
        return _markdown(f"# Error {status}\n\n{message}\n", status)
    return _html(
        "error.html", status, status=status, message=message, page=None, official=""
    )


# Safety ----------------------------------------------------------------------------


class SameOrigin:
    """Refuse requests that change something (POST) from other sites: a web
    page elsewhere mustn't change this server's settings or write files."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            headers = {
                k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]
            }
            origin = headers.get("origin")
            host = headers.get("host", "")
            site = headers.get("sec-fetch-site")
            foreign = (origin is not None and urlsplit(origin).netloc != host) or (
                site is not None and site not in SAFE_FETCH_SITES
            )
            if foreign:
                response = PlainTextResponse("Cross-site requests are refused", 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# The app ---------------------------------------------------------------------------


def create_app(
    ecfr: ECFR,
    *,
    save_dir: str | Path | None = None,
    hosts: list[str] | None = None,
    factory: Callable[[Settings], ECFR] | None = None,
) -> Starlette:
    """The web navigator for a client.

    Parameters
    ----------
    ecfr: ECFR
        The client pages are fetched with; its cache is the navigator's.
    save_dir: str | Path | None
        Where the export menu saves files on this computer; without it,
        exports are only downloaded.
    hosts: list[str] | None
        Host names the server answers to, which stops other sites reaching
        it through DNS rebinding. Defaults to the local host names; ``["*"]``
        allows any.
    factory: Callable[[Settings], ECFR] | None
        Makes the client that replaces ``ecfr`` when the Settings page
        changes a setting; ``ECFR`` by default.
    """
    navigator = Navigator(
        ecfr, Path(save_dir).expanduser() if save_dir else None, factory or ECFR
    )
    routes: list[BaseRoute] = [
        Route("/", home),
        *_variants("/index", home),
        Route("/current", lambda r: RedirectResponse("/", 307)),
        Route("/current/{rest:path}", text_page),
        Route("/on/{date}/{rest:path}", text_page),
        Route("/history/{rest:path}", history),
        Route("/compare/{later}/to/{earlier}/{rest:path}", compare),
        Route("/search", search),
        *_variants("/search", search),
        Route("/go", go),
        *_variants("/go", go),
        Route("/export/{rest:path}", export, methods=["GET"]),
        Route("/export/{rest:path}", export_save, methods=["POST"]),
        Route("/settings", settings_page, methods=["GET"]),
        Route("/settings", settings_post, methods=["POST"]),
        Route("/settings.json", _variant_of(settings_page, ".json")),
        Route("/api/preview", preview),
        Route("/api/terms", terms),
        Route("/api/index", index),
        Route("/api/index/{title:int}", index),
        Route("/llms.txt", llms),
        Route("/openapi.json", openapi),
        Route("/sw.js", service_worker),
        Route("/favicon.ico", favicon),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    # MCP: the MCP server (ecfr_md.mcp, with the mcp extra) is mounted at /mcp,
    # so agents reach this navigator's client over streamable HTTP.
    mcp_app = _mcp_app(navigator)
    if mcp_app is not None:
        routes.insert(0, Mount("/mcp", app=mcp_app))

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        inner = getattr(getattr(mcp_app, "router", None), "lifespan_context", None)
        if inner is not None:
            async with inner(mcp_app):
                yield
        else:
            yield
        navigator.close()

    allowed = (
        hosts
        if hosts is not None
        else ["127.0.0.1", "localhost", "[::1]", "::1", "testserver"]
    )
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=allowed),
            Middleware(SameOrigin),
        ],
        exception_handlers={
            Exception: _error,
            HTTPException: _error,
            ECFRError: _error,
        },
        lifespan=lifespan,
    )
    app.state.navigator = navigator
    return app


def _variants(path: str, endpoint: Callable[[Request], Any]) -> list[Route]:
    """Routes for a page's Markdown and JSON variants: ``/search.md``."""
    return [Route(path + v, _variant_of(endpoint, v)) for v in pages.VARIANTS]


def _variant_of(
    endpoint: Callable[[Request], Any], variant: str
) -> Callable[[Request], Any]:
    def run(request: Request) -> Any:
        request.scope["path_params"]["variant"] = variant
        return endpoint(request)

    return run


def _mcp_app(navigator: Navigator) -> Any:
    """The MCP server's HTTP app, when the mcp extra is installed."""
    try:
        from ecfr_md import mcp
    except ImportError:
        return None
    agents = mcp.Navigator(navigator.ecfr)
    navigator.agents = agents
    return mcp.build(agents, mcp.Watch(agents)).http_app(path="/")
