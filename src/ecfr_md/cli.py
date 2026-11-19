"""Download U.S. federal regulations (CFR) from the eCFR as Markdown.

Common commands:

    ecfr-md get 45 CFR 164.502                 print a section
    ecfr-md get 45/164.502/a/1                 a paragraph, by its address
    ecfr-md get 45 CFR Part 164 -o hipaa.md    save to a file
    ecfr-md get 45 CFR 164 --tree -o hipaa/    one file per section
    ecfr-md get --all --tree -o cfr/           every title
    ecfr-md search "minimum necessary"         find sections
    ecfr-md outline 45 CFR Part 164            table of contents
    ecfr-md references 45 CFR 164.502          what a section cites
    ecfr-md cited-by 45 CFR 164.502            what cites it
    ecfr-md compare 45 CFR 164.502 --since 2024-01-01   what changed
    ecfr-md titles                             what titles exist
    ecfr-md config show                        settings, and where each is from
    ecfr-md browse 45 CFR 164.502              read and navigate full screen
    ecfr-md define "covered entity" 45 CFR 164.502   a defined term
    ecfr-md verify 45 CFR 164.502(a) --quote "..."      check a quotation
    ecfr-md mcp                                serve AI agents (MCP)
    ecfr-md serve --open                       read in a web browser

Run ecfr-md COMMAND --help for a command's options. The options below go
before the command. Every eCFR API endpoint is under ecfr-md api.
"""

import datetime as dt
import difflib
import functools
import inspect
import io
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import warnings
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Unpack

from cyclopts import App, CycloptsError, Group, Parameter
from cyclopts.panel import CycloptsPanel
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rich.console import Console, RenderableType
from rich.logging import RichHandler
from rich.text import Text

from ecfr_md import query as queries
from ecfr_md import sources, tui
from ecfr_md.address import Address
from ecfr_md.client import ECFR, Admin, Search, Versioner
from ecfr_md.config import (
    ENV_PREFIX,
    PROJECT_URL,
    Settings,
    __version__,
    read_config,
    write_config,
)
from ecfr_md.errors import (
    ECFRError,
    ECFRWarning,
    InvalidCitationError,
    InvalidParameterError,
    MissingDependencyError,
)
from ecfr_md.params import DateLike, Hierarchy, SearchFilters, to_date
from ecfr_md.progress import Progress
from ecfr_md.render.export import render
from ecfr_md.render.formats import BINARY, SUFFIXES, Format, format_for
from ecfr_md.results import LEAVES, Matches
from ecfr_md.ui import (
    Terminal,
    changes_table,
    outline_tree,
    reading,
    redline,
    references_table,
    search_table,
    size,
    titles_table,
)

EXIT_ERROR = 1
EXIT_USAGE = 2
FORMAT_BELOW = 2_000_000  # Larger text is printed as plain Markdown.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
HELP_FLAGS = frozenset({"--help", "-h"})
KINDS = ("changed", "added", "removed")
PARTIAL = re.compile(r"\.(.+?)(?:\.[0-9a-f]{32})?\.part")  # write_text's temp name.
CITED = re.compile(r"\d|§|title\b", re.IGNORECASE)  # How a citation starts.
COMMENT = re.compile(r"\s#")  # A comment after a citation on a --from line.

DEFAULT_SETTINGS = Settings()

Client = Annotated[ECFR, Parameter(parse=False)]

console = Console()
err_console = Console(stderr=True)


class TopLevelGroup(Group):
    """A help panel shown by ``ecfr-md --help`` but not repeated per command."""

    shown = True

    @property
    def show(self) -> bool:
        return TopLevelGroup.shown


CONNECTION = TopLevelGroup("Connection")

app = App(name="ecfr-md", version=__version__, help=__doc__)


@app.command
def get(
    *citation: str,
    output: Annotated[
        str | None, Parameter(alias="-o", allow_leading_hyphen=True)
    ] = None,
    tree: bool = False,
    front_matter: bool = True,
    date: DateLike | None = None,
    from_file: Annotated[
        str | None, Parameter(name="--from", allow_leading_hyphen=True)
    ] = None,
    all_titles: Annotated[bool, Parameter(name="--all")] = False,
    format: Format | None = None,
    pager: bool = True,
    links: bool | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """Download regulation text as Markdown, text, HTML, JSON, EPUB, Word or PDF.

    Parameters
    ----------
    citation: str
        What to get: a title number, citation or address such as 45,
        45 CFR 164, 45 CFR Part 164 Subpart E, 45/164.502/a/1 or an eCFR
        link. Several (45/164.502 45/164.504) are saved to an --output
        directory. Asked for if left out in a terminal. The hierarchy
        options below refine it.
    output: str | None
        File to write; "-" for stdout. Its suffix picks the format: .md,
        .txt, .html, .json, .epub, .docx or .pdf. A name ending in "/" or an
        existing directory gets a file named after the citation. With
        --tree, --from or --all, the directory to write to. Prints to stdout
        if omitted.
    tree: bool
        Write nested directories with one file per section or appendix
        (Markdown, text, HTML) or per part (JSON, EPUB, Word, PDF), instead
        of a single file.
    front_matter: bool
        Start saved files with YAML metadata: citation, date, source URL,
        retrieval time and a note that the eCFR is not an official edition.
    date: DateLike | None
        Point-in-time date, e.g. 2020-01-01, "Jan 1 2020" or "last year".
        Defaults to the most recent available.
    from_file: str | None
        File with one citation per line ("-" for stdin), each saved to the
        --output directory. Blank lines and "# comments" are skipped, and
        lists such as "45 CFR 164.502, 164.506" give one file each.
    all_titles: bool
        Every title of the CFR, each saved to the --output directory. Large
        titles are fetched part by part, so this takes a while.
    format: Format | None
        Save or print in this format instead of the one the --output suffix
        names (Markdown if none). Word and PDF need the docx and pdf extras.
    pager: bool
        In a terminal, page through text longer than the screen ($PAGER,
        or less).
    links: bool | None
        Make cross-references Markdown links: in a --tree, between its
        files, else to the eCFR. On for trees, off otherwise.
    as_json: bool
        JSON: the same as --format json.
    """
    if as_json:
        if format not in (None, "json"):
            raise InvalidParameterError(f"--json is --format json; not with {format}")
        format = "json"
    if from_file is not None:
        if citation or all_titles:
            raise InvalidParameterError(
                "--from reads the citations from a file; leave out "
                + ("--all" if all_titles else "the citation")
            )
        directory = _directory(output, "--from")
        _get_many(ecfr, from_file, directory, front_matter, date, format, links)
        return
    if all_titles:
        if citation:
            raise InvalidParameterError(
                "--all gets every title; leave out the citation"
            )
        _get_all(
            ecfr, _directory(output, "--all"), tree, front_matter, date, format, links
        )
        return
    if not citation and sys.stdin.isatty() and err_console.is_terminal:
        citation = (err_console.input("Citation or address: "),)
    if not any(word.strip() for word in citation):
        raise InvalidParameterError(
            "Say what to get, e.g. ecfr-md get 45 CFR 164, or --all for every title"
        )
    places = _places(citation)
    if len(places) > 1:
        _get_listed(ecfr, places, output, tree, front_matter, date, format, links)
        return
    target = _directory(output, "--tree") if tree else _file(output, format)
    cited = places[0]
    terminal = _terminal(ecfr)
    if target is None:
        if format in BINARY:
            raise InvalidParameterError(
                f"Save {format.upper()} to a file: -o FILE{SUFFIXES[format]}"
            )
        with terminal.live():
            node = ecfr.get(cited, date=date, **hierarchy)
        if format not in (None, "markdown"):
            _print_markdown(render(node, format, front_matter=False))
            return
        text = node.to_markdown(links=bool(links))
        if console.is_terminal and len(text) <= FORMAT_BELOW and not links:
            _show(reading(node), pager)  # Formatted for reading.
        else:
            _print_markdown(text)
            if console.is_terminal:
                terminal.note("Long text is printed as Markdown; use -o to save it.")
        terminal.note(f"{node.citation} · eCFR text as of {node.date} · {node.url}")
        return
    with terminal.live():
        if tree:
            files = ecfr.save(
                cited,
                target,
                tree=True,
                front_matter=front_matter,
                date=date,
                format=format,
                links=links,
                **hierarchy,
            )
        else:
            file = ecfr.save(
                cited,
                target,
                front_matter=front_matter,
                date=date,
                format=format,
                links=links,
                **hierarchy,
            )
            files = [file]
    if not tree:
        terminal.done(
            f"Saved {terminal.target} to {files[0]}",
            f"as of {terminal.date}",
            size(files[0].stat().st_size),
        )
        return
    terminal.summary(
        "Saved",
        {
            "Citation": terminal.target,
            "Text as of": str(terminal.date),
            "Output": str(target),
            "Files": f"{len(files):,}",
            "Size": _total_size(files),
        },
    )


def _show(renderable: RenderableType, pager: bool) -> None:
    """Print to the terminal, through a pager if it's taller than the screen."""
    lines = console.render_lines(renderable, pad=False)
    if not pager or len(lines) <= console.height:
        console.print(renderable)
        return
    # Keep colours (-R), quit at once if it fits after all (-F), and leave the
    # text on screen after quitting (-X), unless the user has set LESS.
    os.environ.setdefault("LESS", "-RFX")
    with console.pager(styles=sys.platform != "win32"):
        console.print(renderable)


def _directory(output: str | None, option: str) -> Path:
    """The directory an option writes to, created if it doesn't exist."""
    if output is None or output == "-":
        raise InvalidParameterError(f"{option} requires --output DIRECTORY")
    path = Path(output)
    if path.exists() and not path.is_dir():
        raise InvalidParameterError(
            f"{option} writes a directory, but {output} is a file; "
            f"pick a directory such as {path.with_suffix('')}-files/"
        )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file(output: str | None, format: Format | None = None) -> Path | None:
    """Where to save one file; None for stdout."""
    if output is None or output == "-":
        return None
    path = Path(output)
    if output.endswith(("/", "\\")) or path.is_dir():
        path.mkdir(parents=True, exist_ok=True)  # Named after the citation.
        return path
    format_for(path, format)  # A suffix that names no format is a mistake.
    return path


def _places(words: Sequence[str]) -> list[str]:
    """The places named on the command line: one citation in several words
    ("45 CFR 164.502"), or several ("45/164.502 45/164.504"), as addresses."""
    text = _citation(tuple(words))
    try:
        found = Address.parse_many(text)
    except InvalidCitationError:
        if len(words) < 2:
            raise
        try:  # Each word a citation of its own: "45/164.502" "12 CFR 1026.1".
            found = [a for word in words for a in Address.parse_many(word)]
        except InvalidCitationError:
            raise InvalidCitationError(
                f"{text!r} isn't a citation or address; e.g. 45 CFR 164.502, "
                "45/164.502/a/1, or several: 45/164.502 45/164.504"
            ) from None
    return [str(a) for a in found]


def _get_listed(
    ecfr: ECFR,
    places: list[str],
    output: str | None,
    tree: bool,
    front_matter: bool,
    date: DateLike | None,
    format: Format | None = None,
    links: bool | None = None,
) -> None:
    """Several citations: printed one after another, or each saved."""
    if output is None or output == "-":
        if format in BINARY or format == "html":
            raise InvalidParameterError(
                f"Save several citations as {format.upper()} with -o DIRECTORY/"
            )
        with _terminal(ecfr).live():
            nodes = ecfr.get_many(places, date=date)
        if format in (None, "markdown"):
            texts = [node.to_markdown(links=bool(links)) for node in nodes]
        else:
            texts = [render(n, format, front_matter=False) for n in nodes]
        _print_markdown("\n".join(texts))
        return
    if not tree:
        directory = _directory(output, "Several citations")
        _save_many(ecfr, places, directory, front_matter, date, format, links)
        return
    directory = _directory(output, "--tree")
    terminal = _terminal(ecfr)
    files: list[Path] = []
    with terminal.live():
        for place in places:
            files += ecfr.save(
                place,
                directory,
                tree=True,
                front_matter=front_matter,
                date=date,
                format=format,
                links=links,
            )
    terminal.summary(
        "Saved",
        {
            "Citations": f"{len(places):,}",
            "Output": str(directory),
            "Files": f"{len(files):,}",
            "Size": _total_size(files),
        },
    )


def _read_citations(text: str, name: str) -> list[str]:
    """Citations in a --from file, each once, or every line that isn't one."""
    citations: dict[str, None] = {}  # Ordered, without repeats.
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        line = COMMENT.split(line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        try:
            for cited in Address.parse_many(line):
                citations[str(cited)] = None
        except InvalidCitationError as exc:
            problems.append(f"{name}:{number}: {exc}")
    if problems:
        more = f"\n... and {len(problems) - 10} more" if len(problems) > 10 else ""
        raise InvalidCitationError("\n".join(problems[:10]) + more)
    if not citations:
        raise InvalidParameterError(f"No citations in {name}")
    return list(citations)


def _get_many(
    ecfr: ECFR,
    source: str,
    output: Path,
    front_matter: bool,
    date: DateLike | None,
    format: Format | None = None,
    links: bool | None = None,
) -> None:
    # utf-8-sig also accepts the byte-order mark Windows editors add.
    data = sys.stdin.buffer.read() if source == "-" else Path(source).read_bytes()
    name = "stdin" if source == "-" else source
    citations = _read_citations(data.decode("utf-8-sig"), name)
    _save_many(ecfr, citations, output, front_matter, date, format, links)


def _save_many(
    ecfr: ECFR,
    citations: list[str],
    output: Path,
    front_matter: bool,
    date: DateLike | None,
    format: Format | None = None,
    links: bool | None = None,
) -> None:
    terminal = _terminal(ecfr)
    with terminal.live():
        results = ecfr.get_many(citations, date=date, errors="return")
        files, failed = [], []
        for cited, result in zip(citations, results, strict=True):
            if isinstance(result, ECFRError):
                failed.append(f"{cited}: {result}")
            else:
                saved = result.save(
                    output, front_matter=front_matter, format=format, links=links
                )
                files.append(saved)
    terminal.summary(
        "Saved",
        {
            "Citations": f"{len(citations):,}",
            "Output": str(output),
            "Files": f"{len(files):,}",
            "Size": _total_size(files),
        }
        | ({"Failed": f"{len(failed):,}"} if failed else {}),
    )
    if failed:
        raise ECFRError(
            f"{len(failed)} of {len(citations)} citations could not be saved:\n"
            + "\n".join(failed)
        )


def _get_all(
    ecfr: ECFR,
    output: Path,
    tree: bool,
    front_matter: bool,
    date: DateLike | None,
    format: Format | None = None,
    links: bool | None = None,
) -> None:
    titles = [t.number for t in ecfr.api.versioner.titles().titles if not t.reserved]
    terminal = _terminal(ecfr)
    files: list[Path] = []
    with terminal.live():
        for title in titles:
            saved = ecfr.save(
                title,
                output,
                tree=tree,
                front_matter=front_matter,
                date=date,
                format=format,
                links=links,
            )
            files += saved if isinstance(saved, list) else [saved]
    terminal.summary(
        "Saved",
        {
            "Titles": f"{len(titles):,}",
            "Text as of": str(terminal.date) if date else "latest for each title",
            "Output": str(output),
            "Files": f"{len(files):,}",
            "Size": _total_size(files),
        },
    )


@app.command
def search(
    *query: str,
    limit: int = 20,
    save: Path | None = None,
    phrase: bool | None = None,
    all_versions: bool = False,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **filters: Unpack[SearchFilters],
) -> None:
    """Find sections and appendices that mention words; with --save, save them.

    Plain words find the exact phrase first, then sections with all the
    words in any form. The eCFR's operators work too (quote the query for
    the shell): '"exact phrase"', -exclude, 'a | b', '( )' to group,
    'prefix*', '"phrase"~3' for words near each other, and AND, OR, NOT.

    Parameters
    ----------
    query: str
        Words or a query to search headings and text for.
    limit: int
        Maximum number of sections to show.
    save: Path | None
        Directory to save each matching section to, as Markdown.
    phrase: bool | None
        --phrase finds only the exact phrase; --no-phrase only sections with
        all the words. By default, the phrase first, then the words.
    all_versions: bool
        Include past versions of sections, not only current text.
    as_json: bool
        Print JSON instead of a table.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        matches = ecfr.search(
            " ".join(query),
            limit=limit,
            phrase=phrase,
            all_versions=all_versions,
            **filters,
        )
    if as_json:
        console.print_json(json.dumps(matches.to_dict()))
    elif not matches:
        terminal.note(f"No matches. Search syntax: {queries.SYNTAX}")
    else:
        console.print(search_table(matches, width=console.width))
        if matches.note:
            terminal.note(matches.note)
        terminal.note(_search_summary(matches, limit))
    if save is None or not matches:
        return
    directory = _directory(str(save), "--save")
    with terminal.live():
        nodes = ecfr.get_many([m.citation for m in matches])
        files = [node.save(directory) for node in nodes]
    terminal.done(
        f"Saved {len(files):,} {'file' if len(files) == 1 else 'files'} to {save}",
        _total_size(files),
    )


def _search_summary(matches: Matches, limit: int) -> str:
    sections = f"{len(matches)} {'section' if len(matches) == 1 else 'sections'}"
    if matches.complete:
        return sections
    return f"{sections} shown; there are more (raise --limit to see them)"


@app.command
def outline(
    *citation: str,
    depth: int | None = None,
    date: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """Show the table of contents of a title or any part of it, without its text.

    With no citation, lists the titles. A section or appendix is followed by
    the ones before and after it.

    Parameters
    ----------
    citation: str
        A title number or citation, e.g. 45 or 45 CFR Part 164.
    depth: int | None
        Levels to show below the citation (default: all).
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    as_json: bool
        Print JSON (citation, level, heading, url, children, ...) instead
        of a tree.
    """
    if depth is not None and depth < 0:
        raise InvalidParameterError("--depth must be 0 or more")
    if not citation and not hierarchy:
        titles(as_json=as_json, ecfr=ecfr)
        return
    with _terminal(ecfr).live():
        entry = ecfr.outline(_citation(citation), date=date, **hierarchy)
    if as_json:
        console.print_json(json.dumps(entry.to_dict(depth=depth)))
        return
    console.print(outline_tree(entry, depth))
    if entry.level in LEAVES:
        terminal = _terminal(ecfr)
        with terminal.live():
            around = [entry.previous, entry.next]
        before, after = (e.citation if e else "none" for e in around)
        terminal.note(f"Previous: {before} · Next: {after}")


@app.command
def references(
    *citation: str,
    date: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """List the cross-references in a section or any other text, and what
    each refers to.

    Parameters
    ----------
    citation: str
        A citation, e.g. 45 CFR 164.502.
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    as_json: bool
        Print JSON (text, citation, address, url, ...) instead of a table.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        node = ecfr.get(_citation(citation), date=date, **hierarchy)
    found = node.references
    if as_json:
        console.print_json(json.dumps([r.to_dict() for r in found]))
        return
    if found:
        console.print(references_table(found))
    count = len(found)
    terminal.note(f"{count:,} {'reference' if count == 1 else 'references'}")


@app.command(name="cited-by")
def cited_by(
    *citation: str,
    limit: int = 20,
    date: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
) -> None:
    """Find sections that may cite a section or part. Approximate: it
    searches the eCFR for the section number or "part N".

    Parameters
    ----------
    citation: str
        A section or part, e.g. 45 CFR 164.502.
    limit: int
        Maximum number of sections to show.
    date: DateLike | None
        Only text in effect on this date. Defaults to the current text.
    as_json: bool
        Print JSON instead of a table.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        matches = ecfr.cited_by(_citation(citation), limit=limit, date=date)
    if as_json:
        console.print_json(json.dumps(matches.to_dict()))
    elif not matches:
        terminal.note("Nothing found that cites it")
    else:
        console.print(search_table(matches, width=console.width))
        terminal.note(_search_summary(matches, limit))


@app.command
def browse(
    *citation: str,
    date: DateLike | None = None,
    ecfr: Client,
) -> None:
    """Read and navigate the CFR full screen: an outline beside the text,
    cross-references to follow, search, history and export.

    Needs the tui extra. Press ? in the browser for its keys.

    Parameters
    ----------
    citation: str
        Where to open, e.g. 45 CFR 164.502 or 45/164.502/a/1. Opens the
        list of titles if left out.
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    """
    tui.require()
    place = str(Address.parse(" ".join(citation))) if citation else None
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise InvalidParameterError(
            "ecfr-md browse needs a terminal; to print text, use ecfr-md get"
        )
    tui.browse(ecfr, place, date=date)


@app.command
def titles(
    *,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
) -> None:
    """List the titles of the CFR and the latest date of each one's text.

    Parameters
    ----------
    as_json: bool
        Print JSON instead of a table.
    """
    with _terminal(ecfr).live():
        found = ecfr.titles()
    if as_json:
        console.print_json(json.dumps([t.to_dict() for t in found]))
    else:
        console.print(titles_table(found))


@app.command
def changes(
    *citation: str,
    since: DateLike | None = None,
    until: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """List changes the eCFR has issued to a title or any part of it.

    Shows when each change was issued and took effect, and whether it was
    substantive, editorial or a removal. Downloads no text.

    Parameters
    ----------
    citation: str
        A title number or citation, e.g. 45 CFR Part 164.
    since: DateLike | None
        Only changes issued on or after this date, e.g. 2024-01-01 or "last year".
    until: DateLike | None
        Only changes issued on or before this date.
    as_json: bool
        Print JSON instead of a table.
    """
    if since and until and to_date(since) > to_date(until):
        raise InvalidParameterError(f"--since {since} is after --until {until}")
    terminal = _terminal(ecfr)
    with terminal.live():
        found = ecfr.changes(_citation(citation), since=since, until=until, **hierarchy)
    if as_json:
        console.print_json(json.dumps([change.to_dict() for change in found]))
        return
    if found:
        console.print(changes_table(found, width=console.width))
    terminal.note(f"{len(found):,} {'change' if len(found) == 1 else 'changes'}")
    if found:  # The natural next question: what did the latest one change?
        latest = found[0]
        before = latest.effective - dt.timedelta(days=1)
        terminal.note(
            f"To see what changed: ecfr-md compare {latest.citation} --since {before}"
        )


@app.command
def compare(
    *citation: str,
    since: DateLike,
    until: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """Show what changed in the text between two dates, as a redline.

    Parameters
    ----------
    citation: str
        A citation, e.g. 45 CFR 164.502.
    since: DateLike
        The earlier date, e.g. the day before a change took effect.
    until: DateLike | None
        The later date. Defaults to the most recent available.
    as_json: bool
        Print JSON (each difference with its words) instead of a redline.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        found = ecfr.compare(_citation(citation), since, until, **hierarchy)
    if as_json:
        console.print_json(json.dumps(found.to_dict()))
        return
    if not console.is_terminal:
        _print_markdown(found.to_markdown())
    elif found:
        console.print(redline(found))
    kinds = [d.kind for d in found.differences]
    counts = ", ".join(f"{kinds.count(k)} {k}" for k in KINDS if k in kinds)
    terminal.note(
        f"{found.since} to {found.until}: {counts or 'no changes'}; "
        f"{found.unchanged:,} paragraphs, tables and notes unchanged"
    )


@app.command
def define(
    term: str,
    *citation: str,
    date: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
) -> None:
    """Show the definition of a term that governs a place in the CFR.

    Looks in the place's text, then in the definitions sections of each
    division it's in, nearest first: ecfr-md define "covered entity"
    45 CFR 164.502 finds 45 CFR 160.103.

    Parameters
    ----------
    term: str
        The term, quoted if it's several words. Case and plurals don't
        matter.
    citation: str
        Where the term is used: a section, part or other division.
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    as_json: bool
        Print JSON (each definition with its citation and text).
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        found = ecfr.define(term, _citation(citation), date=date)
    if as_json:
        console.print_json(json.dumps(found.to_dict()))
        return
    for definition in found:
        applies = definition.applies_to
        terminal.note(definition.cite() + (f"; for {applies}" if applies else ""))
        _print_markdown(definition.text + "\n")
    if found:
        return
    terminal.note(f"No definition of {found.term!r} found for {found.scope}")
    if found.suggestions:
        terminal.note(f"Defined terms like it: {', '.join(found.suggestions)}")
    for match in found.mentions:
        terminal.note(f"May be defined in {match.citation}: {match.excerpt}")


@app.command
def verify(
    *citation: str,
    quote: str,
    date: DateLike | None = None,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
) -> None:
    """Check that a quotation is word for word at a citation.

    Formatting, whitespace, quote marks and dashes don't count. If it isn't
    there, shows where it is, or the nearest text and how alike it is.
    Exits with 1 unless verified.

    Parameters
    ----------
    citation: str
        The pinpoint quoted, e.g. 45 CFR 164.502(a)(1).
    quote: str
        The quoted words.
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    as_json: bool
        Print JSON (verified, found, citation, similarity, ...).
    """
    cited = _citation(citation)
    terminal = _terminal(ecfr)
    with terminal.live():
        check = ecfr.verify_quote(cited, quote, date=date)
    if as_json:
        console.print_json(json.dumps(check.to_dict()))
    elif check.verified:
        terminal.done(f"Verified: in {check.citation}", f"as of {check.date}")
    elif check.found:
        terminal.note(f"Not in {cited}, but word for word in {check.citation}")
    elif check.matched:
        terminal.note(
            f"Not found in {cited}; nearest is {check.citation} "
            f"({check.similarity:.0%} alike): {check.matched}"
        )
    else:
        terminal.note(f"Not found in {cited}, nor anything like it")
    if not check.verified:
        raise NotVerified


class NotVerified(Exception):
    """``verify`` found no exact match at the citation (exit status 1)."""


@app.command
def mcp(
    *,
    http: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
    allow_config_changes: bool = False,
    export_dir: Path | None = None,
    export: bool = True,
    watch_interval: float = 3600.0,
    ecfr: Client,
) -> None:
    """Serve the CFR to AI agents over MCP: stdio, or HTTP with --http.

    Agents get tools to outline, read (sized to a token budget), search,
    compare, define terms and verify quotes, plus cfr: resources. The
    options before mcp (or ECFR_MD_* variables) configure it. Needs the mcp
    extra.

    Parameters
    ----------
    http: bool
        Serve streamable HTTP at http://HOST:PORT/mcp instead of stdio.
    host: str
        Address to listen on with --http. Anything but 127.0.0.1 or
        localhost lets other machines connect.
    port: int
        Port to listen on with --http.
    allow_config_changes: bool
        Let agents change the settings (hosts, cache, ...) with a configure
        tool. Off: they can only read them.
    export_dir: Path | None
        Where the export tool may save files (default: the current
        directory). It refuses paths outside it.
    export: bool
        Offer the export tool. With --no-export, agents can't write files.
    watch_interval: float
        Seconds between checks of the eCFR for changes to subscribed text.
    """
    from ecfr_md.mcp import server

    ecfr.progress = Progress()  # Standard output is the protocol: stay quiet.
    root = (export_dir or Path.cwd()) if export else None
    agents = server(
        ecfr,
        allow_config_changes=allow_config_changes,
        export_root=root,
        watch_interval=watch_interval,
    )
    if not http:
        agents.run(show_banner=False)
        return
    if host not in LOCAL_HOSTS:
        warnings.warn(
            f"Serving on {host}: other machines can connect", ECFRWarning, stacklevel=1
        )
    agents.run("http", show_banner=False, host=host, port=port)


@app.command(name="sync")
def sync_command(
    path: Path,
    *,
    dry_run: bool = False,
    include_dated: bool = False,
    force: bool = False,
    migrate: bool = False,
    as_json: Annotated[bool, Parameter(name="--json")] = False,
    ecfr: Client,
) -> None:
    """Update Markdown saved by ecfr-md to the latest text.

    PATH is a file or directory written by ecfr-md get -o; folders are
    searched to any depth. Only files whose text changed are rewritten; files
    for sections that no longer exist are deleted. Files saved with a past
    --date are kept at that date, and files edited since they were saved are
    left as they are.

    Parameters
    ----------
    path: Path
        A saved file, a saved tree, or a directory containing them.
    dry_run: bool
        Show what would change without writing anything.
    include_dated: bool
        Also update files saved with a past --date to the latest text.
    force: bool
        Also overwrite or delete files edited since they were saved.
    migrate: bool
        Rename files and folders saved by ecfr-md 0.x (45-cfr-164.502.md,
        subpart-e/section-164.502.md) to today's names (45_164.502.md,
        subpart-E/164.502.md). Without it they keep their names.
    as_json: bool
        Print JSON (the files in each group) instead of a summary.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        result = ecfr.sync(
            path,
            dry_run=dry_run,
            include_dated=include_dated,
            force=force,
            migrate=migrate,
        )
    if as_json:
        groups = {
            name: [str(f) for f in getattr(result, name)]
            for name in ("added", "changed", "removed", "unchanged", "kept", "modified")
        }
        moved = [[str(old), str(new)] for old, new in result.moved]
        console.print_json(json.dumps({"dry_run": dry_run, **groups, "moved": moved}))
        return
    if not (
        result.added
        or result.changed
        or result.removed
        or result.kept
        or result.modified
        or result.moved
    ):
        checked = len(result.unchanged)
        terminal.done(
            f"{path} is up to date",
            f"{checked:,} {'file' if checked == 1 else 'files'} checked",
        )
        return
    for kind, files in (
        ("+", result.added),
        ("~", result.changed),
        ("-", result.removed),
        ("=", result.kept),
        ("!", result.modified),
    ):
        for file in files[:20]:
            terminal.note(f"{kind} {file}")
    for old, new in result.moved[:20]:
        terminal.note(f"> {old} -> {new}")
    if result.kept:
        terminal.note("= kept at saved date; use --include-dated to update them")
    if result.modified:
        terminal.note("! modified locally, left as is; use --force to overwrite them")
    terminal.summary(
        "Would sync" if dry_run else "Synced",
        {
            "Path": str(path),
            "Changed": f"{len(result.changed):,}",
            "Added": f"{len(result.added):,}",
            "Removed": f"{len(result.removed):,}",
            "Unchanged": f"{len(result.unchanged):,}",
            "Kept at saved date": f"{len(result.kept):,}",
            "Modified locally": f"{len(result.modified):,}",
        }
        | ({"Renamed": f"{len(result.moved):,}"} if migrate else {}),
    )


@app.command
def serve(
    *,
    port: Annotated[int, Parameter(alias="-p")] = 8000,
    host: str = "127.0.0.1",
    open_browser: Annotated[bool, Parameter(name="--open")] = False,
    save_dir: Path | None = None,
    ecfr: Client,
) -> None:
    """Read the CFR in a web browser: a navigator served on this computer.

    Pages are fetched from the eCFR when first opened and kept in the cache
    (--cache-dir), so what has been read is there offline. Every page is also
    Markdown and JSON (add .md or .json), with /llms.txt for AI agents.
    Needs the web extra. Stop with Ctrl+C.

    Parameters
    ----------
    port: int
        Port to listen on.
    host: str
        Address to listen on. The default answers only this computer;
        0.0.0.0 answers others on the network too.
    open_browser: bool
        Open the navigator in the default browser.
    save_dir: Path | None
        Folder the export menu saves to on this computer. Without it,
        exports are downloaded through the browser.
    """
    try:
        import uvicorn

        from ecfr_md.web import create_app
    except ImportError:
        package = f'"ecfr-md[web] @ git+{PROJECT_URL}"'
        raise MissingDependencyError(
            "ecfr-md serve needs the optional 'web' extra. Install it with: "
            f"uv tool install {package}, or pip install {package}"
        ) from None
    local = host in ("127.0.0.1", "localhost", "::1")
    terminal = _terminal(ecfr)
    if not local:
        err_console.print(
            Text(
                f"! Listening on {host}: other computers that can reach this one "
                "can read through it and change its settings. Use the default "
                "(127.0.0.1) to keep it to this computer.",
                style="yellow",
            ),
            soft_wrap=True,
        )
    ecfr.progress = Progress()  # Pages show their own progress.
    navigator = create_app(ecfr, save_dir=save_dir, hosts=None if local else ["*"])
    shown = "localhost" if local else host
    url = f"http://{'[' + shown + ']' if ':' in shown else shown}:{port}/"
    terminal.note(f"Serving the CFR at {url} (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(navigator, host=host, port=port, log_level="warning")


cache_app = App(name="cache", help="The local cache of downloaded text.")
app.command(cache_app)


@cache_app.command
def info(
    *, as_json: Annotated[bool, Parameter(name="--json")] = False, ecfr: Client
) -> None:
    """Show where the cache is and how much it holds.

    Parameters
    ----------
    as_json: bool
        Print JSON instead of lines.
    """
    store = ecfr._store
    entries, used = store.info()
    facts = {
        "directory": str(store.directory) if store.directory else None,
        "entries": entries,
        "bytes": used,
    }
    if as_json:
        console.print_json(json.dumps(facts))
        return
    console.print(
        f"Directory  {facts['directory'] or 'off'}\n"
        f"Entries    {entries:,}\n"
        f"Size       {size(used)}",
        soft_wrap=True,
        markup=False,
        highlight=False,
    )


@cache_app.command
def clear(
    *, as_json: Annotated[bool, Parameter(name="--json")] = False, ecfr: Client
) -> None:
    """Delete everything in the cache.

    Parameters
    ----------
    as_json: bool
        Print JSON (entries and bytes deleted) instead of a line.
    """
    entries, used = ecfr._store.info()
    ecfr._store.clear()
    if as_json:
        console.print_json(json.dumps({"entries": entries, "bytes": used}))
        return
    _terminal(ecfr).done(
        "Cache cleared", f"{entries:,} entries, {size(used)}", network=False
    )


config_app = App(
    name="config",
    help=(
        "Settings: hosts, sources, cache and connection. Values come from "
        "options before the command, ECFR_MD_* variables, the config file and "
        "the defaults, in that order."
    ),
)
app.command(config_app)


@config_app.command
def show(
    *, as_json: Annotated[bool, Parameter(name="--json")] = False, ecfr: Client
) -> None:
    """Show each setting, where its value came from, and how text sources are
    chosen.

    Parameters
    ----------
    as_json: bool
        Print JSON instead of lines.
    """
    settings = ecfr.settings
    explained = settings.explain()
    choice = sources.explain(settings, ecfr._costs)
    if as_json:
        data = {
            "settings": {
                name: {"value": _plain(value), "from": origin}
                for name, (value, origin) in explained.items()
            },
            "config_file": str(settings.config_file),
            "sources": choice,
        }
        console.print_json(json.dumps(data))
        return
    width = max(len(name) for name in explained)
    lines = [
        f"{name.ljust(width)}  {_shown(value)}  ({origin})"
        for name, (value, origin) in explained.items()
    ]
    console.print(
        "\n".join([*lines, "", f"Config file: {settings.config_file}", choice]),
        soft_wrap=True,
        markup=False,
        highlight=False,
    )


@config_app.command
def path(*, ecfr: Client) -> None:
    """Print where the config file is (it may not exist yet)."""
    console.print(str(ecfr.settings.config_file), soft_wrap=True, highlight=False)


@config_app.command(name="set")
def set_setting(key: str, value: str, /, *, ecfr: Client) -> None:
    """Save a setting in the config file, e.g. ecfr-md config set source bulk.

    Parameters
    ----------
    key: str
        A setting's name, as ecfr-md config show lists them; headers.NAME
        for a request header.
    value: str
        Its new value.
    """
    file = ecfr.settings.config_file
    values = read_config(file)
    name, _, header = key.replace("-", "_").partition(".")
    if name == "headers" and header:
        values["headers"] = {**values.get("headers", {}), header: value}
    elif name in Settings.model_fields and name != "headers":
        values[name] = value
    else:
        raise InvalidParameterError(_unknown_setting(key))
    _write_settings(file, values)
    _terminal(ecfr).done("Saved", f"{key} in {file}", network=False)


@config_app.command
def unset(key: str, /, *, ecfr: Client) -> None:
    """Remove a setting from the config file, so its default applies.

    Parameters
    ----------
    key: str
        A setting's name; headers.NAME for a request header.
    """
    file = ecfr.settings.config_file
    values = read_config(file)
    name, _, header = key.replace("-", "_").partition(".")
    if name == "headers" and header:
        found = header in values.get("headers", {})
        values.get("headers", {}).pop(header, None)
    else:
        found = values.pop(name, None) is not None
    if not found:
        raise InvalidParameterError(f"{key} isn't set in {file}")
    _write_settings(file, values)
    _terminal(ecfr).done("Removed", f"{key} from {file}", network=False)


@config_app.command
def edit(*, ecfr: Client) -> None:
    """Open the config file in $VISUAL or $EDITOR, then check it."""
    file = ecfr.settings.config_file
    if not file.exists():
        write_config(file, {})
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    editor = editor or ("notepad" if sys.platform == "win32" else "vi")
    subprocess.run([*shlex.split(editor), str(file)], check=False)
    Settings.load(file, env={})  # Says what's wrong, if anything.
    _terminal(ecfr).done("Checked", str(file), network=False)


def _write_settings(file: Path, values: dict) -> None:
    """Check settings, then write them with their proper types."""
    checked = Settings.load(file, env={}, origin=f"file {file}", **values)
    write_config(file, {name: getattr(checked, name) for name in values})


def _unknown_setting(key: str) -> str:
    close = difflib.get_close_matches(key, Settings.model_fields, n=1)
    hint = f"; did you mean {close[0]}?" if close else ""
    return f"No setting {key!r}{hint} (ecfr-md config show lists them)"


def _plain(value: object) -> object:
    """A setting's value as JSON can hold it."""
    return str(value) if isinstance(value, Path) else value


def _shown(value: object) -> str:
    return "(none)" if value is None else str(value)


def _print_markdown(text: str) -> None:
    """Write Markdown to stdout as UTF-8, whatever the console's encoding."""
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _total_size(files: Sequence[Path]) -> str:
    return size(sum(f.stat().st_size for f in files))


def _render(result: object) -> None:
    match result:
        case BaseModel():
            console.print_json(result.model_dump_json(exclude_unset=True))
        case bytes():
            sys.stdout.buffer.write(result)
        case dt.date():
            console.print(result.isoformat())


def _endpoint(service: str, name: str, method: Callable):
    """Expose an SDK method as a command with the same parameters and help."""

    @functools.wraps(method)
    def command(*args, ecfr: Client, **kwargs) -> None:
        with _terminal(ecfr).live():
            result = getattr(getattr(ecfr.api, service), name)(*args, **kwargs)
        _render(result)

    params = list(inspect.signature(method).parameters.values())[1:]  # Drop self.
    client = inspect.Parameter(
        "ecfr", inspect.Parameter.KEYWORD_ONLY, annotation=Client
    )
    position = next(
        (i for i, p in enumerate(params) if p.kind is p.VAR_KEYWORD), len(params)
    )
    params.insert(position, client)
    del command.__wrapped__  # Make cyclopts use the signature below.
    signature = inspect.Signature(params, return_annotation=None)
    setattr(command, "__signature__", signature)  # noqa: B010 (not a plain attribute for typing)
    return command


api_app = App(
    name="api",
    help="One command per eCFR API endpoint, printing its JSON.",
)
app.command(api_app)
for service, cls in (("admin", Admin), ("search", Search), ("versioner", Versioner)):
    sub_app = App(name=service, help=cls.__doc__)
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if not name.startswith("_"):
            sub_app.command(
                _endpoint(service, name, method), name=name.replace("_", "-")
            )
    api_app.command(sub_app)


def _citation(words: tuple[str, ...]) -> str:
    if not words:
        raise InvalidParameterError("Say which title or citation, e.g. 45 CFR 164")
    text = " ".join(words)
    if not any(c.isdigit() for c in text):
        raise InvalidCitationError(
            f"{text!r} isn't a citation like 45 CFR 164.502. To find sections "
            f"that mention it: ecfr-md search {shlex.quote(text)}"
        )
    return text


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    settings: Annotated[Settings, Parameter(name="*", group=CONNECTION)] = (
        DEFAULT_SETTINGS
    ),
    config_file: Annotated[
        Path | None, Parameter(name="--config", group=CONNECTION)
    ] = None,
    quiet: Annotated[bool, Parameter(alias="-q", group="Output")] = False,
    verbose: Annotated[bool, Parameter(alias="-v", group="Output")] = False,
) -> int:
    """Configure the client, run the command and report errors.

    Parameters
    ----------
    config_file: Path | None
        Settings file (TOML); default: $ECFR_MD_CONFIG, else ecfr-md config path.
    quiet: bool
        No progress or summary; only warnings and errors.
    verbose: bool
        Log every request, and which host served the text and why.
    """
    quiet = quiet or _env_flag("QUIET")
    verbose = verbose or _env_flag("VERBOSE")
    # Only ecfr-md's own records: HTTP/2 libraries log every header at DEBUG.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_path=False, show_time=False)],
    )
    # The terminal display reports retries itself unless asked for every request.
    logging.getLogger("ecfr_md").setLevel(logging.DEBUG if verbose else logging.ERROR)
    logging.captureWarnings(True)  # Show ECFRWarning through the same handler.
    warnings.formatwarning = lambda message, *_args, **_kwargs: str(message)
    command, bound, _ = app.parse_args(tokens)
    if "ecfr" not in inspect.signature(command).parameters:
        command(*bound.args, **bound.kwargs)  # Help for a bare command group.
        return 0
    terminal = Terminal(err_console, quiet=quiet)
    try:
        # Options given here win over ECFR_MD_* variables and the config file.
        given = {name: getattr(settings, name) for name in settings.model_fields_set}
        try:
            settings = Settings.load(config_file, origin="CLI", **given)
        except InvalidParameterError:
            if command not in (edit, path, set_setting, unset):
                raise
            # Bad settings mustn't stop the commands that fix them.
            settings = Settings.load(config_file, origin="CLI", read=False, **given)
        with ECFR(settings, progress=terminal) as ecfr:
            command(*bound.args, **bound.kwargs, ecfr=ecfr)
    except NotVerified:
        return EXIT_ERROR
    except BrokenPipeError:
        # The reader stopped early, as `| head` does; that isn't an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except InvalidParameterError as exc:
        terminal.error(exc)
        return EXIT_USAGE
    except OSError as exc:
        terminal.error(_file_error(exc))
        return EXIT_ERROR
    except ECFRError as exc:
        terminal.error(exc)
        return EXIT_ERROR
    return 0


def _env_flag(name: str) -> bool:
    """Whether an ECFR_MD_<NAME> variable is set to something true."""
    value = os.environ.get(f"{ENV_PREFIX}{name}", "")
    return value.strip().lower() in ("1", "true", "yes", "on")


def _file_error(exc: OSError) -> OSError:
    """Name the file the user asked for, not the temporary one written first."""
    if exc.filename is None or exc.strerror is None:
        return exc
    path = Path(exc.filename)
    if match := PARTIAL.fullmatch(path.name):  # ".x.md.part" becomes x.md.
        path = path.with_name(match[1])
    return OSError(f"Can't write {path}: {exc.strerror.lower()}")


def _terminal(ecfr: ECFR) -> Terminal:
    progress = ecfr.progress
    return (
        progress
        if isinstance(progress, Terminal)
        else Terminal(err_console, quiet=True)
    )


def _usage_error(exc: CycloptsError) -> RenderableType:
    """Cyclopts' message, except for settings, whose pydantic report is jargon."""
    cause = exc.__cause__
    if not isinstance(cause, PydanticValidationError):
        return CycloptsPanel(exc)
    problems = [
        f"--{'.'.join(map(str, e['loc'])).replace('_', '-')}: "
        f"{e['msg'][0].lower()}{e['msg'][1:]} (got {e['input']})"
        for e in cause.errors(include_url=False)
    ]
    return Text(f"✘ Invalid usage: {'; '.join(problems)}")


def run(tokens: Sequence[str], **kwargs) -> int:
    """Run the command line; connection options are listed only by ecfr-md --help."""
    tokens = list(tokens)
    if tokens and CITED.match(tokens[0]) and tokens[0] not in app:
        # "ecfr-md 45 CFR 164.502": the command was left out.
        hint = f"Say what to do first, e.g. ecfr-md get {shlex.join(tokens)}"
        Terminal(err_console).error(InvalidParameterError(hint))
        return EXIT_USAGE
    asking = next((i for i, t in enumerate(tokens) if t in HELP_FLAGS), None)
    before = tokens if asking is None else tokens[:asking]
    TopLevelGroup.shown = not any(t in app for t in before if not t.startswith("-"))
    try:
        return app.meta(tokens, error_formatter=_usage_error, **kwargs)
    finally:
        TopLevelGroup.shown = True


def main() -> None:
    # Windows consoles and pipes default to a legacy code page that can't
    # encode "§", "—" or the box-drawing characters in tables.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(run(sys.argv[1:]))
