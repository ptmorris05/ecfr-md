"""The terminal browser: ``ecfr-md browse``, a full-screen reader of the CFR.

It needs the optional ``tui`` extra (Textual). :func:`browse` opens it.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from ecfr_md.config import PROJECT_URL
from ecfr_md.errors import MissingDependencyError

if TYPE_CHECKING:
    from ecfr_md.client import ECFR
    from ecfr_md.params import DateLike


def require() -> None:
    """Check Textual is installed, or explain how to install it."""
    try:
        importlib.import_module("textual")
    except ImportError:
        package = f'"ecfr-md[tui] @ git+{PROJECT_URL}"'
        raise MissingDependencyError(
            "The terminal browser needs the optional 'tui' extra. Install it "
            f"with: uv tool install {package} (for the ecfr-md command), or "
            f"uv add {package} / pip install {package} (for Python)"
        ) from None


def browse(
    ecfr: ECFR, citation: str | None = None, *, date: DateLike | None = None
) -> None:
    """Open the terminal browser at ``citation`` (default: the list of
    titles), as of ``date`` (default: the latest text), until the user quits."""
    require()
    from ecfr_md.tui.app import Browser

    Browser(ecfr, citation, date=date).run()
