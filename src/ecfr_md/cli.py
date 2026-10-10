"""Download Code of Federal Regulations text as Markdown.

  ecfr-md get 45 CFR 164.502                  print a section
  ecfr-md get "45 CFR 164.502(a)(1)"          print a paragraph (quote parentheses)
  ecfr-md get 45 CFR Part 164 -o hipaa.md     save one file
  ecfr-md get 45 --part 164 --tree -o hipaa/  save a directory tree

Every eCFR API endpoint is also available under admin, search and versioner.
"""

import datetime as dt
import functools
import inspect
import io
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Unpack

from cyclopts import App, Parameter, config
from pydantic import BaseModel
from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown

from ecfr_md.client import ECFR, Admin, Search, Versioner
from ecfr_md.config import Settings, __version__
from ecfr_md.errors import ECFRError, InvalidParameterError
from ecfr_md.params import DateLike, Hierarchy, SearchFilters
from ecfr_md.ui import (
    Terminal,
    changes_table,
    outline_tree,
    result_citation,
    search_table,
    size,
)

ENV_PREFIX = "ECFR_MD_"
EXIT_ERROR = 1
EXIT_USAGE = 2
FORMAT_BELOW = 2_000_000  # Larger text is printed as plain Markdown.

DEFAULT_SETTINGS = Settings()

Client = Annotated[ECFR, Parameter(parse=False)]

console = Console()
err_console = Console(stderr=True)

app = App(name="ecfr-md", version=__version__, help=__doc__)
app.meta.config = config.Env(ENV_PREFIX, command=False)


@app.command
def get(
    *citation: str,
    output: Annotated[Path | None, Parameter(alias="-o")] = None,
    tree: bool = False,
    front_matter: bool = True,
    date: DateLike | None = None,
    from_file: Annotated[Path | None, Parameter(name="--from")] = None,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """Download regulation text as Markdown.

    Parameters
    ----------
    citation: str
        What to get: a title number or citation such as 45, 45 CFR 164,
        45 CFR Part 164 Subpart E or 45 CFR 164.502(a)(1). The hierarchy
        options below refine it.
    output: Path | None
        File (or directory, with --tree) to write. Prints to stdout if omitted.
    tree: bool
        Write nested directories with one file per section or appendix,
        instead of a single file.
    front_matter: bool
        Start saved files with YAML metadata: citation, date, source URL,
        retrieval time and a note that the eCFR is not an official edition.
    date: DateLike | None
        Point-in-time date, e.g. 2020-01-01, "Jan 1 2020" or "last year".
        Defaults to the most recent available.
    from_file: Path | None
        File with one citation per line ("-" for stdin); each is saved to the
        --output directory. Sections in the same part share a request.
    """
    if from_file is not None:
        _get_many(ecfr, from_file, output, front_matter, date)
        return
    if not citation:
        raise InvalidParameterError("Say what to get, e.g. ecfr-md get 45 CFR 164")
    if tree and output is None:
        raise InvalidParameterError("--tree requires --output DIRECTORY")
    cited = " ".join(citation)
    terminal = _terminal(ecfr)
    if output is None:
        with terminal.live():
            text = ecfr.get(cited, date=date, **hierarchy).to_markdown()
        if console.is_terminal and len(text) <= FORMAT_BELOW:
            console.print(Markdown(text))  # Formatted for reading.
        else:
            _print_markdown(text)
            if console.is_terminal:
                terminal.note("Long text is printed as Markdown; use -o to save it.")
        return
    with terminal.live():
        files = ecfr.save(
            cited,
            output,
            tree=tree,
            front_matter=front_matter,
            date=date,
            **hierarchy,
        )
    terminal.summary(
        "Saved",
        {
            "Citation": terminal.target,
            "Text as of": str(terminal.date),
            "Output": str(output),
            "Files": f"{len(files):,}",
            "Size": size(sum(f.stat().st_size for f in files)),
        },
    )


def _get_many(
    ecfr: ECFR,
    source: Path,
    output: Path | None,
    front_matter: bool,
    date: DateLike | None,
) -> None:
    if output is None:
        raise InvalidParameterError("--from requires --output DIRECTORY")
    # utf-8-sig also accepts the byte-order mark Windows editors add.
    data = sys.stdin.buffer.read() if str(source) == "-" else source.read_bytes()
    text = data.decode("utf-8-sig")
    citations = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not citations:
        raise InvalidParameterError(f"No citations in {source}")
    terminal = _terminal(ecfr)
    output.mkdir(parents=True, exist_ok=True)
    with terminal.live():
        nodes = ecfr.get_many(citations, date=date)
        files = [
            f for node in nodes for f in node.save(output, front_matter=front_matter)
        ]
    terminal.summary(
        "Saved",
        {
            "Citations": f"{len(citations):,}",
            "Output": str(output),
            "Files": f"{len(files):,}",
            "Size": size(sum(f.stat().st_size for f in files)),
        },
    )


cache_app = App(name="cache", help="The local cache of downloaded text.")
app.command(cache_app)


@cache_app.command
def info(*, ecfr: Client) -> None:
    """Show where the cache is and how much it holds."""
    entries, used = ecfr.store.info()
    directory = ecfr.store.directory
    _terminal(ecfr).summary(
        "Cache",
        {
            "Directory": str(directory) if directory else "off",
            "Entries": f"{entries:,}",
            "Size": size(used),
        },
        network=False,
    )


@cache_app.command
def clear(*, ecfr: Client) -> None:
    """Delete everything in the cache."""
    entries, used = ecfr.store.info()
    ecfr.store.clear()
    _terminal(ecfr).summary(
        "Cache cleared",
        {"Removed": f"{entries:,} entries, {size(used)}"},
        network=False,
    )


def _print_markdown(text: str) -> None:
    """Write Markdown to stdout as UTF-8, whatever the console's encoding."""
    sys.stdout.flush()
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


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
            result = getattr(getattr(ecfr, service), name)(*args, **kwargs)
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


services: dict[str, App] = {}
for service, cls in (("admin", Admin), ("search", Search), ("versioner", Versioner)):
    sub_app = services[service] = App(name=service, help=cls.__doc__)
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if not name.startswith("_"):
            sub_app.command(
                _endpoint(service, name, method), name=name.replace("_", "-")
            )
    app.command(sub_app)


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

    Parameters
    ----------
    citation: str
        A title number or citation, e.g. 45 or 45 CFR Part 164.
    depth: int | None
        Levels to show below the citation (default: all).
    date: DateLike | None
        Point-in-time date. Defaults to the most recent available.
    as_json: bool
        Print the API's JSON instead of a tree.
    """
    with _terminal(ecfr).live():
        node = ecfr.outline(_citation(citation), date=date, **hierarchy)
    if as_json:
        console.print_json(node.model_dump_json(exclude_unset=True))
    else:
        console.print(outline_tree(node, depth))


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
    terminal = _terminal(ecfr)
    with terminal.live():
        versions = ecfr.changes(
            _citation(citation), since=since, until=until, **hierarchy
        )
    if as_json:
        console.print_json(json.dumps([v.model_dump(mode="json") for v in versions]))
        return
    console.print(changes_table(versions))
    terminal.note(f"{len(versions):,} changes")


@app.command(name="sync")
def sync_command(path: Path, *, ecfr: Client) -> None:
    """Update Markdown saved by ecfr-md to the latest text.

    PATH is a file or directory written by ``ecfr-md get -o``. Only files
    whose text changed are rewritten; files for sections that no longer exist
    are deleted.

    Parameters
    ----------
    path: Path
        A saved file, a saved tree, or a directory containing them.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        result = ecfr.sync(path)
    for kind, files in (
        ("+", result.added),
        ("~", result.changed),
        ("-", result.removed),
    ):
        for file in files[:20]:
            terminal.note(f"{kind} {file}")
    terminal.summary(
        "Synced",
        {
            "Path": str(path),
            "Changed": f"{len(result.changed):,}",
            "Added": f"{len(result.added):,}",
            "Removed": f"{len(result.removed):,}",
            "Unchanged": f"{len(result.unchanged):,}",
        },
    )


@services["search"].default
def search_text(
    query: str,
    *,
    limit: int = 20,
    save: Path | None = None,
    all_versions: bool = False,
    ecfr: Client,
    **filters: Unpack[SearchFilters],
) -> None:
    """Search the eCFR; with --save, write the current text of each match.

    Parameters
    ----------
    query: str
        Words to search for in headings and text.
    limit: int
        Maximum number of sections to show.
    save: Path | None
        Directory to save each matching section to, as Markdown.
    all_versions: bool
        Include past versions of sections, not only current text.
    """
    terminal = _terminal(ecfr)
    with terminal.live():
        found = ecfr.search.results(query, per_page=min(1000, limit * 3), **filters)
        results, seen = [], set()
        for result in found.results:
            citation = result_citation(result)
            current = result.ends_on is None or all_versions
            if current and citation not in seen and not result.removed:
                seen.add(citation)
                results.append(result)
        results = results[:limit]
    console.print(search_table(results))
    total = found.meta.total_count
    terminal.note(f"{len(results)} shown" + (f" of {total:,} matches" if total else ""))
    if save is None or not results:
        return
    with terminal.live():
        nodes = ecfr.get_many([result_citation(r) for r in results])
        files = [f for node in nodes for f in node.save(save)]
    terminal.summary(
        "Saved",
        {
            "Output": str(save),
            "Files": f"{len(files):,}",
            "Size": size(sum(f.stat().st_size for f in files)),
        },
    )


def _citation(words: tuple[str, ...]) -> str:
    if not words:
        raise InvalidParameterError("Say which title or citation, e.g. 45 CFR 164")
    return " ".join(words)


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    settings: Annotated[
        Settings, Parameter(name="*", group="Connection")
    ] = DEFAULT_SETTINGS,
    quiet: Annotated[bool, Parameter(alias="-q", group="Output")] = False,
    verbose: Annotated[bool, Parameter(alias="-v", group="Output")] = False,
) -> int:
    """Configure the client, run the command and report errors.

    Parameters
    ----------
    quiet: bool
        No progress or summary; only warnings and errors.
    verbose: bool
        Log every request.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_path=False)],
    )
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    if not verbose:  # The terminal display reports retries itself.
        logging.getLogger("ecfr_md").setLevel(logging.ERROR)
    logging.captureWarnings(True)  # Show ECFRWarning through the same handler.
    command, bound, _ = app.parse_args(tokens)
    terminal = Terminal(err_console, quiet=quiet)
    try:
        with ECFR(settings, progress=terminal) as ecfr:
            command(*bound.args, **bound.kwargs, ecfr=ecfr)
    except InvalidParameterError as exc:
        terminal.error(exc)
        return EXIT_USAGE
    except (ECFRError, OSError) as exc:
        terminal.error(exc)
        return EXIT_ERROR
    return 0


def _terminal(ecfr: ECFR) -> Terminal:
    progress = ecfr.progress
    return (
        progress
        if isinstance(progress, Terminal)
        else Terminal(err_console, quiet=True)
    )


def main() -> None:
    # Windows consoles and pipes default to a legacy code page that can't
    # encode "§", "—" or the box-drawing characters in tables.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(app.meta(sys.argv[1:]))
