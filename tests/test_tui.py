"""The terminal browser, driven with Textual's Pilot against the fake API."""

import asyncio
import dataclasses
import datetime as dt
import functools
import logging
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx2
import pytest
from conftest import PART_XML, TITLES
from textual.widgets import Checkbox, Input, OptionList, Select, Static, Tree

from ecfr_md import load, tui
from ecfr_md.errors import MissingDependencyError
from ecfr_md.render.blocks import Heading, Image, Item, Note, Paragraph, Table
from ecfr_md.results import Outline
from ecfr_md.tui import text
from ecfr_md.tui.app import Browser, PieceView, Reader
from ecfr_md.tui.screens import (
    DateDialog,
    Export,
    GoTo,
    Help,
    History,
    Redline,
    Search,
)

FULL = "/api/versioner/v1/full/{}/title-45.xml"
STRUCTURE = "/api/versioner/v1/structure/{}/title-45.json"
VERSIONS = "/api/versioner/v1/versions/title-45.json"
SEARCH = "/api/search/v1/results"
LATEST = "2026-10-07"

REFERENCES = """<DIV8 N="164.110" TYPE="SECTION">
<HEAD>§ 164.110 References.</HEAD>
<P>(a) See § 164.102 and paragraph (b) of this section, under 42 U.S.C. 1320d.</P>
<P>(b) Only this section and <I>Term</I><SU>2</SU> in § 164.104(a).</P>
<P>(c) None here.</P>
<DIV><img src="/graphics/chart.gif"/></DIV>
</DIV8>
</DIV6>"""
XML = PART_XML.replace("</DIV6>", REFERENCES, 1)


def _section(number: str) -> dict:
    return {"identifier": number, "label": f"§ {number} Heading.", "type": "section"}


TREE = {
    "identifier": "45",
    "label": "Title 45—Public Welfare",
    "type": "title",
    "size": 9000,
    "children": [
        {
            "identifier": "A",
            "label": "Subtitle A—Department of Health and Human Services",
            "type": "subtitle",
            "children": [
                {
                    "identifier": "164",
                    "label": "Part 164—Security and Privacy",
                    "type": "part",
                    "size": 5000,
                    "children": [
                        {
                            "identifier": "A",
                            "label": "Subpart A—General Provisions",
                            "type": "subpart",
                            "children": [
                                _section(n)
                                for n in (
                                    "164.102",
                                    "164.104",
                                    "164.106",
                                    "164.108",
                                    "164.110",
                                )
                            ],
                        },
                        {
                            "identifier": "Appendix A to Subpart C of Part 164",
                            "label": "Appendix A to Subpart C of Part 164—Matrix",
                            "type": "appendix",
                        },
                    ],
                },
                {
                    "identifier": "165",
                    "label": "Part 165 [Reserved]",
                    "type": "part",
                    "reserved": True,
                },
                {
                    "identifier": "170",
                    "label": "Part 170—Huge",
                    "type": "part",
                    "size": 3_000_000,
                    "children": [_section("170.1")],
                },
            ],
        }
    ],
}


HUGE = '<DIV8 N="170.1" TYPE="SECTION"><HEAD>§ 170.1 Huge.</HEAD><P>Big.</P></DIV8>'


def full_text(xml: str):
    """Like the real endpoint: the requested section, or the whole part."""

    def respond(request: httpx2.Request) -> httpx2.Response:
        section = request.url.params.get("section")
        if section is None:
            return httpx2.Response(200, text=xml)
        if section == "170.1":
            return httpx2.Response(200, text=HUGE)
        root = ET.fromstring(xml)
        found = next((d for d in root.iter() if d.get("N") == section), None)
        if found is None:
            return httpx2.Response(404, json={"error": "No matching content found."})
        return httpx2.Response(200, content=ET.tostring(found, encoding="utf-8"))

    return respond


def version(identifier: str, issued: str, effective: str, **kind: bool) -> dict:
    return {
        "date": effective, "amendment_date": effective, "issue_date": issued,
        "identifier": identifier, "name": f"§ {identifier} Heading.",
        "part": "164", "substantive": kind.get("substantive", True),
        "removed": kind.get("removed", False), "title": "45", "type": "section",
    }  # fmt: skip


def result(section: str, heading: str, excerpt: str) -> dict:
    h = {"title": "45", "part": section.split(".")[0], "section": section}
    return {
        "starts_on": "2017-01-01", "ends_on": None, "type": "Section",
        "hierarchy": h, "hierarchy_headings": h,
        "headings": {"section": f"§ {section} {heading}"},
        "full_text_excerpt": excerpt, "score": 1.0, "structure_index": 1,
        "reserved": False, "removed": False,
    }  # fmt: skip


@pytest.fixture
def site(api):
    """The fake API with a title outline down to sections, and their text."""
    api.json(STRUCTURE.format(LATEST), TREE)
    api.routes[FULL.format(LATEST)] = full_text(XML)
    older = XML.replace('A covered entity <E T="03">must</E> comply.', "Old rule.")
    api.json(STRUCTURE.format("2024-01-01"), TREE)
    api.routes[FULL.format("2024-01-01")] = full_text(older)
    api.json(STRUCTURE.format("2024-01-02"), TREE)
    api.routes[FULL.format("2024-01-02")] = full_text(XML)
    return api


@pytest.fixture
def browser(ecfr, site, tmp_path):
    """Make a browser over the fake site that saves into a temporary directory."""
    return functools.partial(Browser, ecfr, directory=tmp_path)


def in_app(test):
    """Run an async test."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        asyncio.run(test(*args, **kwargs))

    return run


async def settle(app, pilot) -> None:
    """Wait for the workers, and what they start, to finish."""
    for _ in range(100):
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        if not app._busy:
            return
    raise AssertionError("still busy")  # pragma: no cover


async def keys(app, pilot, *pressed: str) -> None:
    for key in pressed:
        await pilot.press(key)
        await settle(app, pilot)


async def typed(app, pilot, words: str) -> None:
    await pilot.press(*[{" ": "space", ".": "full_stop"}.get(c, c) for c in words])
    await settle(app, pilot)


def notes(app) -> list[str]:
    found = [f"{n.title}: {n.message}" for n in app._notifications]
    app.clear_notifications()
    return found


def shown(app) -> str:
    """The breadcrumb, with ">" between its levels."""
    return str(app.query_one("#crumbs", Static).render()).replace("\u203a", ">")


def status(app) -> str:
    return str(app.query_one("#status", Static).render())


def reader(app) -> Reader:
    return app.query_one(Reader)


# Reading ---------------------------------------------------------------------


@in_app
async def test_opens_a_section_beside_its_outline(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert shown(app) == "Title 45 > Subtitle A > Part 164 > Subpart A > § 164.104"
        assert status(app) == f"as of {LATEST}"
        kinds = [p.kind for p in reader(app).pieces]
        assert kinds[0] == "heading" and "paragraph" in kinds
        tree = app.query_one("#outline", Tree)
        assert tree.cursor_node is not None and tree.cursor_node.data is not None
        assert tree.cursor_node.data.address == "45/164.104"
        assert app.query_one("#outline").display  # Wide enough for both.


@in_app
async def test_starts_at_the_titles_and_opens_them(browser):
    app = browser()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        pieces = reader(app).pieces
        assert shown(app) == "CFR" and status(app) == ""
        assert [p.kind for p in pieces] == ["heading", "entry", "entry"]
        assert pieces[2].dim  # Title 35 is reserved.
        await keys(app, pilot, "j", "enter")
        assert shown(app) == "Title 45" and status(app) == f"as of {LATEST}"
        assert app.view.node is None  # A title: its contents, not its text.
        await keys(app, pilot, "u")
        assert shown(app) == "CFR"
        await keys(app, pilot, "u")  # Nothing above the CFR.
        assert shown(app) == "CFR"


@in_app
async def test_a_paragraph_opens_its_section_there(browser):
    app = browser("45/164.104/a/2")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert shown(app).endswith("§ 164.104")
        assert "p-164.104(a)(2)" in reader(app).current.anchors
        await keys(app, pilot, "y")
        assert app.clipboard == "45 CFR 164.104(a)(2)"
        assert notes(app) == [": Copied 45 CFR 164.104(a)(2)"]


@in_app
async def test_parts_show_their_text_and_huge_ones_their_contents(browser):
    app = browser("45 CFR Part 164")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        kinds = {p.kind for p in reader(app).pieces}
        assert {"heading", "paragraph", "note", "table", "item"} <= kinds
        app.go("45 CFR Part 170")
        await settle(app, pilot)
        assert [p.kind for p in reader(app).pieces] == ["heading", "entry"]
        await keys(app, pilot, "j", "enter")  # A section of it: its text.
        assert shown(app).endswith("§ 170.1")
        assert [p.kind for p in reader(app).pieces] == ["heading", "paragraph"]


@in_app
async def test_stepping_through_paragraphs(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 12)) as pilot:
        await settle(app, pilot)
        view = reader(app)
        await keys(app, pilot, "k")  # Already at the top.
        assert view.cursor == 0
        await keys(app, pilot, "j", "j")
        assert view.cursor == 2
        assert view.current.citation == "45 CFR 164.104(a)"
        for _ in range(30):
            await pilot.press("j")
        await settle(app, pilot)
        assert view.cursor == len(view.pieces) - 1  # Stops at the end.
        view.scroll_home(animate=False)  # Scrolled away from the cursor...
        await settle(app, pilot)
        await keys(app, pilot, "j")
        assert view.cursor == 0  # ... stepping starts from what's in view.


@in_app
async def test_references_are_followed_and_history_kept(browser):
    app = browser("45 CFR 164.110")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        view = reader(app)
        await keys(app, pilot, "enter")  # The heading has no references.
        await keys(app, pilot, "tab")
        assert (view.cursor, view.link) == (1, 0)  # § 164.102 in (a).
        await keys(app, pilot, "enter")
        assert shown(app).endswith("§ 164.102")
        await keys(app, pilot, "b")
        assert shown(app).endswith("§ 164.110")
        assert view.cursor == 1  # Where it was.
        await keys(app, pilot, "f")
        assert shown(app).endswith("§ 164.102")
        await keys(app, pilot, "f")  # Nothing further.
        await keys(app, pilot, "b", "b")  # Nothing before the first.
        assert shown(app).endswith("§ 164.110")


@in_app
async def test_enter_chooses_among_several_references(browser, monkeypatch):
    app = browser("45 CFR 164.110")
    opened: list[str] = []
    monkeypatch.setattr(app, "open_url", lambda url, **_: opened.append(url))
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        view = reader(app)
        await keys(app, pilot, "j", "enter")  # (a) has three: the first is chosen.
        assert view.link == 0 and shown(app).endswith("§ 164.110")
        await keys(app, pilot, "tab", "enter")  # Paragraph (b), right here.
        assert shown(app).endswith("§ 164.110")
        assert "p-164.110(b)" in view.current.anchors
        await keys(app, pilot, "shift+tab", "enter")  # 42 U.S.C. 1320d.
        assert opened and "uscode.house.gov" in opened[0]
        assert any("Opened" in n for n in notes(app))
        await keys(app, pilot, "b")
        assert view.cursor == 1


@in_app
async def test_tab_wraps_around_the_references(browser):
    app = browser("45 CFR 164.110")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        view = reader(app)
        await keys(app, pilot, "shift+tab")  # Back from the top: the last one.
        last = (view.cursor, view.link)
        assert view.pieces[last[0]].kind == "image"
        await keys(app, pilot, "tab")
        assert (view.cursor, view.link) == (1, 0)


@in_app
async def test_text_without_references(browser):
    app = browser("45 CFR 164.108")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "tab", "shift+tab")
        assert reader(app).link is None


@in_app
async def test_clicks_follow_references_and_move_the_cursor(browser):
    app = browser("45 CFR 164.110")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        views = list(app.query(PieceView))
        await pilot.click(views[3])  # Paragraph (c): the cursor moves there.
        await settle(app, pilot)
        assert reader(app).cursor == 3
        await pilot.click(views[1], offset=(12, 0))  # On "§ 164.102".
        await settle(app, pilot)
        assert shown(app).endswith("§ 164.102")
        app.action_follow(99, 0)  # Gone since: nothing happens.
        reader(app).mark(99)
        await keys(app, pilot, "u")
        assert shown(app).endswith("Subpart A")
        await keys(app, pilot, "u")
        assert shown(app).endswith("Part 164")
        app.go("45")
        await settle(app, pilot)
        entries = [v for v in app.query(PieceView) if v.piece.kind == "entry"]
        await pilot.click(entries[0])
        await settle(app, pilot)
        assert shown(app) == "Title 45 > Subtitle A"


@in_app
async def test_previous_and_next_sections(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "right_square_bracket")
        assert shown(app).endswith("§ 164.106")
        await keys(app, pilot, "left_square_bracket", "left_square_bracket")
        assert shown(app).endswith("§ 164.102")
        await keys(app, pilot, "left_square_bracket")
        assert notes(app) == [": No previous section"]
        app.go("45 CFR Part 164")
        await settle(app, pilot)
        await keys(app, pilot, "right_square_bracket")  # Into the part.
        assert shown(app).endswith("§ 164.102")
        app.go("45 CFR Part 165")  # Reserved: nothing in it.
        await settle(app, pilot)
        await keys(app, pilot, "right_square_bracket")
        assert shown(app).endswith("§ 170.1")
        app.load(None)
        await settle(app, pilot)
        await keys(app, pilot, "right_square_bracket")
        assert notes(app) == [": Open a title first"]


@in_app
async def test_errors_are_notifications(browser):
    app = browser("45 CFR 164.999")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert any(n.startswith("Not found:") for n in notes(app))
        app.go("no citation here")
        await settle(app, pilot)
        assert any(n.startswith("Not a citation:") for n in notes(app))
        assert status(app) == ""  # Nothing open.


@in_app
async def test_retries_and_warnings_are_notifications(browser, site):
    calls = []

    def titles(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(503, text="busy")
        return httpx2.Response(200, json=TITLES)

    site.routes["/api/versioner/v1/titles.json"] = titles
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert any("retry 2/3" in n for n in notes(app))
        record = ("ecfr_md.client", logging.WARNING, "", 0, "retrying", None, None)
        logging.getLogger().handle(logging.LogRecord(*record))  # Reported already.
        logging.getLogger("tests.tui").warning("served from the cache")
        await settle(app, pilot)
        assert notes(app) == [": served from the cache"]
    assert not isinstance(app.ecfr.progress, type(app.status))  # Put back.


@in_app
async def test_later_is_safe_from_any_thread(browser):
    app = browser("45 CFR 164.104")
    calls: list[str] = []
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        app.later(calls.append, "ui")
        worker = threading.Thread(target=app.later, args=(calls.append, "thread"))
        worker.start()
        await settle(app, pilot)
        worker.join()
    app._loop, app._thread_id = None, 0  # As app.run() leaves it.
    app.later(calls.append, "stopped")  # The app has gone: ignored.
    assert calls == ["ui", "thread"]


# Dialogs -----------------------------------------------------------------------


@in_app
async def test_go_to_completes_from_the_outline(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "g")
        assert isinstance(app.screen, GoTo)
        await keys(app, pilot, "j")  # Typed, not a step through the text.
        assert reader(app).cursor == 0
        await keys(app, pilot, "backspace")
        await typed(app, pilot, "164.10")
        matches = app.screen.query_one("#matches", OptionList)
        assert matches.option_count == 4  # Not 164.110.
        await keys(app, pilot, "down", "down", "enter")
        assert shown(app).endswith("§ 164.104")
        await keys(app, pilot, "g")
        await keys(app, pilot, "down")  # No matches yet: stays in the box.
        app.screen.query_one(Input).value = "45 CFR 164.106(a)"
        await settle(app, pilot)
        await keys(app, pilot, "enter")
        assert shown(app).endswith("§ 164.106")
        assert "p-164.106(a)" in reader(app).current.anchors


@in_app
async def test_go_to_explains_what_it_cannot_read(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "g", "enter")  # Nothing typed.
        assert isinstance(app.screen, GoTo)
        await typed(app, pilot, "zz")
        await keys(app, pilot, "enter")
        problem = str(app.screen.query_one("#problem", Static).render())
        assert "isn't" in problem or "citation" in problem
        await keys(app, pilot, "backspace", "backspace")
        await typed(app, pilot, "heading")  # Not a citation, but in headings.
        await keys(app, pilot, "enter")
        assert not isinstance(app.screen, GoTo)
        assert shown(app).endswith("§ 164.102")  # The first.
        await keys(app, pilot, "g", "escape")
        assert not isinstance(app.screen, GoTo)


def test_completion_reads_any_start_of_a_citation(ecfr, site, tmp_path):
    app = Browser(ecfr, directory=tmp_path)

    def found(words: str) -> list[str]:
        return [e.citation for e in app.complete(words)]

    assert found("") == []
    assert found("public") == ["45 CFR"]  # At the top: titles by name.
    assert found("45")[:2] == ["45 CFR", "45 CFR Subtitle A"]
    assert found("99") == [] and found("99 CFR 1") == [] and found("35 CFR 1") == []
    assert found("45 CFR part 16") == ["45 CFR Part 164", "45 CFR Part 165"]
    assert found("45 CFR Part 164") == ["45 CFR Part 164"]
    assert found("45/subpart") == ["45 CFR Part 164 Subpart A"]
    assert found("45 CFR § 164.104(a)") == ["45 CFR 164.104"]
    assert "Appendix A" in found("45 CFR appendix")[0]
    assert found("45 CFR huge") == ["45 CFR Part 170"]
    app.view = type("View", (), {"entry": app.ecfr.outline("45/164.104")})()  # type: ignore[assignment]
    assert found("164.11") == ["45 CFR 164.110"]  # In the open title.


@in_app
async def test_search_and_open_a_match(browser, site):
    body = {
        "results": [
            result("164.104", "Paragraph nesting.", "a run of <strong>text</strong>"),
            result("164.106", "Examples.", ""),
        ],
        "meta": {"total_count": 9, "total_pages": 1},
    }
    site.json(SEARCH, body)
    app = browser("45 CFR 164.102")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "slash")
        assert isinstance(app.screen, Search)
        await keys(app, pilot, "enter")  # Nothing to search for yet.
        app.screen.query_one("#within", Checkbox).value = True
        app.screen.query_one(Input).focus()
        await typed(app, pilot, "nesting")
        await keys(app, pilot, "enter")
        assert site.requests[-1].url.params["hierarchy[part]"] == "164"
        text = str(app.screen.query_one("#status", Static).render())
        assert text == "2 matches of 9 versions"
        await keys(app, pilot, "down", "enter")
        assert shown(app).endswith("§ 164.106")


@in_app
async def test_search_without_matches_or_without_the_eCFR(browser, site):
    site.json(SEARCH, {"results": [], "meta": {"total_count": 0, "total_pages": 1}})
    app = browser()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "slash")
        assert not app.screen.query(Checkbox)  # Nothing to search within.
        await typed(app, pilot, "nothing")
        await keys(app, pilot, "enter")
        assert str(app.screen.query_one("#status", Static).render()) == "No matches"
        site.json(SEARCH, {"error": "down"}, status=400)
        await keys(app, pilot, "enter")
        assert str(app.screen.query_one("#status", Static).render()) == ""
        assert any("eCFR API error" in n for n in notes(app))
        await keys(app, pilot, "escape")


@in_app
async def test_one_match(browser, site):
    body = {"results": [result("164.104", "Nesting.", "")], "meta": {"total_count": 1}}
    site.json(SEARCH, body)
    app = browser("45 CFR 164.102")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "slash")
        await typed(app, pilot, "x")
        await keys(app, pilot, "enter")
        text = str(app.screen.query_one("#status", Static).render())
        assert text == "1 match"


@in_app
async def test_text_as_of_a_date(browser, site):
    app = browser("45 CFR 164.102")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "j", "d")
        assert isinstance(app.screen, DateDialog)
        await typed(app, pilot, "not a date")
        await keys(app, pilot, "enter")
        assert app.screen.query_one("#problem", Static).render()
        app.screen.query_one(Input).value = "2024-01-01"
        await keys(app, pilot, "enter")
        assert app.date == dt.date(2024, 1, 1)
        assert status(app) == "as of 2024-01-01"
        assert reader(app).cursor == 1  # Where it was.
        assert "Old rule." in reader(app).pieces[1].parts[0]
        assert ": Text as of 2024-01-01" in notes(app)
        await keys(app, pilot, "d", "enter")  # The same date: nothing to do.
        await keys(app, pilot, "d", "escape")
        assert app.date == dt.date(2024, 1, 1)
        await keys(app, pilot, "d")
        app.screen.query_one(Input).value = ""
        await keys(app, pilot, "enter")  # Empty: the latest.
        assert app.date is None and status(app) == f"as of {LATEST}"


@in_app
async def test_a_date_without_text_is_explained(browser, site):
    site.json(VERSIONS, {"content_versions": [version("164.102", "2020-01-01", "2020-01-01")]})  # fmt: skip  # noqa: E501
    site.json(STRUCTURE.format("2019-01-01"), None, status=404)
    app = browser("45 CFR 164.102")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "d")
        app.screen.query_one(Input).value = "2019-01-01"
        await keys(app, pilot, "enter")
        assert any(n.startswith("No text for that date") for n in notes(app))
        assert app.date is None


@in_app
async def test_an_address_with_a_date_sets_the_date(browser):
    app = browser("45/164.102@2024-01-01")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        assert app.date == dt.date(2024, 1, 1)
        assert status(app) == "as of 2024-01-01"
        await keys(app, pilot, "d")
        app.screen.query_one(Input).value = ""
        await keys(app, pilot, "enter")
        app.load(None)
        await settle(app, pilot)
        await keys(app, pilot, "d")
        app.screen.query_one(Input).value = "2024-01-01"
        await keys(app, pilot, "enter")  # The titles, again.
        assert app.date == dt.date(2024, 1, 1)


@in_app
async def test_history_and_redline(browser, site):
    site.json(
        VERSIONS,
        {"content_versions": [
            version("164.102", "2024-01-03", "2024-01-02"),
            version("164.102", "2023-01-01", "2023-01-01", substantive=False),
        ]},
    )  # fmt: skip
    for day in ("2022-12-31", "2023-01-01"):
        site.json(STRUCTURE.format(day), TREE)
        site.routes[FULL.format(day)] = full_text(XML)
    app = browser("45 CFR 164.102")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "h")
        assert isinstance(app.screen, History)
        changes = app.screen.query_one("#changes", OptionList)
        assert changes.option_count == 2
        await keys(app, pilot, "enter")
        assert isinstance(app.screen, Redline)
        body = str(app.screen.query(Static)[1].render())
        assert body.startswith("1 changed") and "substantive change" in body
        await keys(app, pilot, "escape", "down", "enter")  # The editorial one.
        assert isinstance(app.screen, Redline)
        assert "No changes to the text" in str(app.screen.query(Static)[1].render())
        await keys(app, pilot, "q", "q")
        assert not isinstance(app.screen, History | Redline)


@in_app
async def test_history_of_a_part_and_its_problems(browser, site):
    site.json(
        VERSIONS,
        {"content_versions": [
            version("164.102", "2024-01-03", "2024-01-02"),
            version("164.104", "2024-01-03", "2024-01-02", removed=True),
        ]},
    )  # fmt: skip
    app = browser("45 CFR Part 164")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "h")
        assert app.screen.query_one("#changes", OptionList).option_count == 2
        site.routes.pop(FULL.format("2024-01-01"))
        await keys(app, pilot, "enter")  # No text on the day before: an error.
        assert isinstance(app.screen, History) and notes(app)
        await keys(app, pilot, "escape")
        site.json(VERSIONS, {"error": "down"}, status=400)
        await keys(app, pilot, "h")
        assert notes(app) and isinstance(app.screen, History)
        await keys(app, pilot, "escape")
        site.json(VERSIONS, {"content_versions": []})
        await keys(app, pilot, "h")
        status_line = str(app.screen.query_one("#status", Static).render())
        assert status_line == "0 changes"
        await keys(app, pilot, "escape")
        app.load(None)
        await settle(app, pilot)
        await keys(app, pilot, "h")
        assert notes(app) == [": Open a title first"]


@in_app
async def test_export_in_any_format(browser, tmp_path):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "e")
        assert isinstance(app.screen, Export)
        path = app.screen.query_one("#path", Input)
        assert path.value == str(tmp_path / "45_164.104.md")
        app.screen.query_one("#format", Select).value = "html"
        await settle(app, pilot)
        assert path.value == str(tmp_path / "45_164.104.html")
        path.focus()
        await keys(app, pilot, "enter")
        saved = tmp_path / "45_164.104.html"
        assert saved.exists() and "164.104" in saved.read_text()
        assert any(n.startswith("Saved 45 CFR 164.104") for n in notes(app))
        await keys(app, pilot, "e")
        path = app.screen.query_one("#path", Input)
        path.value = str(tmp_path / "notes.custom")
        app.screen.query_one("#format", Select).value = "text"
        await settle(app, pilot)
        assert path.value.endswith("notes.custom")  # The user's name is kept.
        path.value = ""
        path.focus()
        await keys(app, pilot, "enter")
        assert "Say where" in str(app.screen.query_one("#problem", Static).render())
        await keys(app, pilot, "escape")


@in_app
async def test_export_contents_and_dated_text(browser, tmp_path):
    app = browser("45 CFR Part 170")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "e")
        path = app.screen.query_one("#path", Input)
        path.focus()
        await keys(app, pilot, "enter")  # Saved from the eCFR, not the screen.
        assert (tmp_path / "45_part-170.md").exists()
        assert any(n.startswith("Saved 45 CFR Part 170") for n in notes(app))
        app.go("45/164.104@2024-01-01")
        await settle(app, pilot)
        await keys(app, pilot, "e")
        path = app.screen.query_one("#path", Input)
        assert path.value.endswith("45_164.104@2024-01-01.md")
        await keys(app, pilot, "escape")
        app.load(None)
        await settle(app, pilot)
        await keys(app, pilot, "e")
        assert notes(app) == [": Open a title or part to export it"]


@in_app
async def test_export_failures_are_notifications(browser, tmp_path):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "e")
        path = app.screen.query_one("#path", Input)
        path.value = str(tmp_path / "missing" / "dir" / "x.md")
        (tmp_path / "missing").write_text("a file, not a directory")
        path.focus()
        await keys(app, pilot, "enter")
        assert any(n.startswith("File error") for n in notes(app))


@in_app
async def test_help_and_keys_held_while_a_dialog_is_open(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        await keys(app, pilot, "question_mark")
        assert isinstance(app.screen, Help)
        await keys(app, pilot, "j", "right_square_bracket")
        assert reader(app).cursor == 0 and shown(app).endswith("§ 164.104")
        await keys(app, pilot, "escape")
        assert not isinstance(app.screen, Help)


# Layout and the outline ------------------------------------------------------


@in_app
async def test_the_outline_can_be_hidden(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        outline = app.query_one("#outline")
        await keys(app, pilot, "o")
        assert not outline.display
        await keys(app, pilot, "escape")  # Only closes it in a narrow terminal.
        await keys(app, pilot, "o")
        assert outline.display and app.focused is outline


@in_app
async def test_a_narrow_terminal_shows_the_outline_on_request(browser):
    app = browser("45 CFR 164.104")
    async with app.run_test(size=(70, 24)) as pilot:
        await settle(app, pilot)
        outline = app.query_one("#outline")
        assert not outline.display and reader(app).display
        assert shown(app) == "Title 45 > … > Subpart A > § 164.104"
        await keys(app, pilot, "o")
        assert outline.display and not reader(app).display
        await keys(app, pilot, "escape")
        assert not outline.display
        await keys(app, pilot, "o", "up", "enter")  # Opens § 164.102.
        assert shown(app).endswith("§ 164.102")
        assert not outline.display and app.focused is reader(app)


@in_app
async def test_the_outline_loads_as_it_opens(browser, site):
    app = browser()
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        tree = app.query_one("#outline", Tree)
        title = tree.root.children[0]
        assert not title.children
        title.expand()
        await settle(app, pilot)
        assert [c.label.plain for c in title.children] == [
            "Subtitle A—Department of Health and Human Services"
        ]
        title.collapse()
        title.expand()  # Filled already.
        await settle(app, pilot)
        assert len(title.children) == 1
        subtitle = title.children[0]
        subtitle.expand()  # Filled while it loads, to show a place: once.
        app._reveal(
            [tree.root.data, title.data, subtitle.data, subtitle.data.children[0]]
        )
        await settle(app, pilot)
        assert len(subtitle.children) == 3
        tree.root.children[1].expand()  # Reserved: nothing to load.
        await settle(app, pilot)
        app._reveal([tree.root.data])  # The top: no entry to point at.
        missing = dataclasses.replace(tree.root.data, address="99")
        app._reveal([tree.root.data, missing])  # Not in the tree: nothing.


@in_app
async def test_nothing_to_do_before_anything_is_open(ecfr, site, tmp_path, monkeypatch):
    app = Browser(ecfr, directory=tmp_path)
    monkeypatch.setattr(app, "_planted", lambda root: None)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(app, pilot)
        for key in ("j", "right_square_bracket", "u", "y", "e", "h", "enter"):
            await keys(app, pilot, key)
        assert app.view is None and status(app) == ""
        app._export((None, Path("x")))  # type: ignore[arg-type]


# Pieces ------------------------------------------------------------------------


def test_pieces_of_text_carry_their_references():
    node = load(XML.encode(), title=45)
    pieces = text.text_pieces(node["164.110"])
    heading, first = pieces[0], pieces[1]
    assert heading.links == [] and heading.citation == "45 CFR 164.110"
    assert [link.text for link in first.links] == [
        "§ 164.102",
        "paragraph (b) of this section",
        "42 U.S.C. 1320d",
    ]
    assert first.links[0].address == "45/164.102"
    assert first.links[2].address is None and "uscode" in first.links[2].url
    # "This section" leads nowhere new.
    assert [link.text for link in pieces[2].links] == ["§ 164.104(a)"]
    assert str(pieces[2].render(2)).count("²") == 1
    image = pieces[-1]
    assert image.kind == "image" and image.links[0].url.endswith("chart.gif")


def test_pieces_render_for_the_screen():
    context = text.Address.parse("45/164.102")
    note = Note(
        (
            Heading(2, "Editorial note"),
            Paragraph("See 78 FR 5692."),
            Note((Item("inner"),)),
            Table((("a", "b"),)),
            Image("x.gif", "chart"),
            Image("y.gif"),
        )
    )
    piece = text._piece(note, context)
    assert piece.kind == "note" and len(piece.parts) == 6
    assert "78 FR 5692" in str(piece.render(0, selected=0))
    item = text._piece(Item("A *thing*"), context)
    assert str(item.render(0)) == "• A thing"
    table = text._piece(Table((("H1", "H2"), ("§ 164.102", "x", "extra")), 1), context)
    assert [link.text for link in table.links] == ["§ 164.102"]
    assert table.render(0).columns[0].header == "H1"
    ragged = text._piece(Table((("a",), ("b", "c"))), context)
    assert len(ragged.render(0).columns) == 2
    image = text._piece(Image("x.gif"), context)
    assert str(image.render(0)) == "[Image]"
    paragraph = text._piece(Paragraph("line<br>break"), context)
    assert str(paragraph.render(0)) == "line\nbreak"


def test_contents_and_crumbs(ecfr, site):
    part = ecfr.outline()["45/part-164"]
    pieces = text.contents_pieces(ecfr.outline("45/subtitle-A"))
    assert [p.detail for p in pieces[1:]] == ["5.0 KB", "", "3.0 MB"]
    assert pieces[2].dim  # Reserved.
    assert (
        str(pieces[1].render(1, selected=0)) == "Part 164—Security and Privacy  5.0 KB"
    )
    names = [text.crumb(e) for e in (*reversed(part.ancestors), part)]
    assert names == ["CFR", "Title 45", "Subtitle A", "Part 164"]
    appendix = part["Appendix A to Subpart C of Part 164"]
    assert text.crumb(appendix) == "Appendix A"
    assert text.crumb(ecfr.outline()) == "CFR"
    heading = Outline(
        "hed1",
        None,
        "Undesignated—Things",
        "45 CFR",
        False,
        None,
        None,  # type: ignore[arg-type]
    )
    assert text.crumb(heading) == "Undesignated"
    assert text._escape("a*b_[c]") == "a\\*b\\_\\[c\\]"


# The command -------------------------------------------------------------------


def test_browse_command(run, monkeypatch):
    opened: list[tuple] = []
    monkeypatch.setattr(tui, "browse", lambda ecfr, place, date: opened.append((place, date)))  # fmt: skip  # noqa: E501
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert run("browse", "45", "CFR", "164.502(a)", "--date", "2024-01-01") == 0
    assert run("browse") == 0
    assert opened[0][0] == "45/164.502/a" and opened[1] == (None, None)
    assert opened[0][1] == dt.date(2024, 1, 1)
    assert run("browse", "not a citation") == 2


def test_browse_needs_a_terminal(run, capsys):
    assert run("browse", "45") == 2
    assert "needs a terminal" in capsys.readouterr().err


def test_browse_without_the_extra(run, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "textual", None)
    with pytest.raises(MissingDependencyError, match=r"ecfr-md\[tui\] @ git\+"):
        tui.require()
    assert run("browse") == 1
    assert "Not installed" in capsys.readouterr().err


def test_browse_opens_the_app(ecfr, monkeypatch):
    ran: list[Browser] = []
    monkeypatch.setattr(Browser, "run", lambda self: ran.append(self))
    tui.browse(ecfr, "45/164.502", date="2024-01-01")
    assert ran[0].date == dt.date(2024, 1, 1) and ran[0].ecfr is ecfr
