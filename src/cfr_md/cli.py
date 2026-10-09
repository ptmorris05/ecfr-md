"""Command-line interface: ``cfr-md``.

Every API endpoint is available under ``admin``, ``search`` and ``versioner``
(generated from the SDK methods, so options and help match the SDK exactly);
``markdown`` downloads regulation text as Markdown.
"""

import datetime as dt
import functools
import inspect
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Unpack

from cyclopts import App, Parameter, config
from pydantic import BaseModel
from rich.console import Console
from rich.logging import RichHandler

from cfr_md.client import ECFR, Admin, Search, Versioner
from cfr_md.config import Settings, __version__
from cfr_md.errors import ECFRError, InvalidParameterError
from cfr_md.markdown import Layout
from cfr_md.params import Hierarchy

ENV_PREFIX = "CFR_MD_"
EXIT_ERROR = 1
EXIT_USAGE = 2

DEFAULT_SETTINGS = Settings()

Client = Annotated[ECFR, Parameter(parse=False)]

console = Console()
err_console = Console(stderr=True)

app = App(name="cfr-md", version=__version__, help=__doc__)
app.meta.config = config.Env(ENV_PREFIX, command=False)


@app.command
def markdown(
    title: int,
    *,
    output: Annotated[Path | None, Parameter(alias="-o")] = None,
    layout: Layout = Layout.FILE,
    date: dt.date | None = None,
    ecfr: Client,
    **hierarchy: Unpack[Hierarchy],
) -> None:
    """Download regulation text as Markdown.

    Examples: ``cfr-md markdown 45 --part 164 -o hipaa.md`` (one file) or
    ``cfr-md markdown 45 --part 164 --layout tree -o hipaa/`` (directory tree).

    Parameters
    ----------
    title: int
        CFR title number, e.g. 45.
    output: Path | None
        Output file (file layout) or directory (tree layout). Prints to stdout
        when omitted (file layout only).
    layout: Layout
        file: one Markdown document, hierarchy encoded as headings.
        tree: nested directories with one file per section or appendix.
    date: dt.date | None
        Point-in-time date. Defaults to the most recent available.
    """
    if output is None:
        if layout == Layout.TREE:
            raise InvalidParameterError("--layout tree requires --output DIRECTORY")
        sys.stdout.write(ecfr.markdown(title, date=date, **hierarchy))
        return
    with err_console.status("Downloading…"):
        files = ecfr.save(title, output, layout=layout, date=date, **hierarchy)
    err_console.print(f"Wrote {len(files)} file(s) to {output}", soft_wrap=True)


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
        _render(getattr(getattr(ecfr, service), name)(*args, **kwargs))

    params = list(inspect.signature(method).parameters.values())[1:]  # Drop self.
    client = inspect.Parameter(
        "ecfr", inspect.Parameter.KEYWORD_ONLY, annotation=Client
    )
    position = next(
        (i for i, p in enumerate(params) if p.kind is p.VAR_KEYWORD), len(params)
    )
    params.insert(position, client)
    del command.__wrapped__  # Make cyclopts use the signature below.
    command.__signature__ = inspect.Signature(params, return_annotation=None)
    return command


for service, cls in (("admin", Admin), ("search", Search), ("versioner", Versioner)):
    sub_app = App(name=service, help=cls.__doc__)
    for name, method in inspect.getmembers(cls, inspect.isfunction):
        if not name.startswith("_"):
            sub_app.command(
                _endpoint(service, name, method), name=name.replace("_", "-")
            )
    app.command(sub_app)


@app.meta.default
def launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    settings: Annotated[
        Settings, Parameter(name="*", group="Connection")
    ] = DEFAULT_SETTINGS,
    verbose: Annotated[bool, Parameter(alias="-v", group="Connection")] = False,
) -> int:
    """Configure the client, run the command and report errors.

    Parameters
    ----------
    verbose: bool
        Log every request.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err_console, show_path=False)],
    )
    logging.getLogger("httpcore2").setLevel(logging.WARNING)
    command, bound, _ = app.parse_args(tokens)
    try:
        with ECFR(settings) as ecfr:
            command(*bound.args, **bound.kwargs, ecfr=ecfr)
    except InvalidParameterError as exc:
        err_console.print(f"[red]Error:[/red] {exc}", highlight=False, soft_wrap=True)
        return EXIT_USAGE
    except ECFRError as exc:
        err_console.print(
            f"[red]{type(exc).__name__}:[/red] {exc}", highlight=False, soft_wrap=True
        )
        return EXIT_ERROR
    except OSError as exc:
        err_console.print(
            f"[red]Could not write output:[/red] {exc}", highlight=False, soft_wrap=True
        )
        return EXIT_ERROR
    return 0


def main() -> None:
    sys.exit(app.meta())
