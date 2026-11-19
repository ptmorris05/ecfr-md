"""Export formats: their names, file suffixes and optional dependencies."""

import importlib
from pathlib import Path
from types import ModuleType
from typing import Literal, get_args

from ecfr_md.config import PROJECT_URL
from ecfr_md.errors import InvalidParameterError, MissingDependencyError

Format = Literal["markdown", "text", "html", "json", "epub", "docx", "pdf"]
"""An export format's name."""

FORMATS: tuple[Format, ...] = get_args(Format)
SUFFIXES: dict[Format, str] = {
    "markdown": ".md",
    "text": ".txt",
    "html": ".html",
    "json": ".json",
    "epub": ".epub",
    "docx": ".docx",
    "pdf": ".pdf",
}
BY_SUFFIX: dict[str, Format] = {
    **{suffix: name for name, suffix in SUFFIXES.items()},
    ".markdown": "markdown",
    ".htm": "html",
    ".xhtml": "html",
}
# Formats saved one file per section in a tree, like Markdown; the others
# make one file per part.
PER_SECTION: frozenset[Format] = frozenset({"markdown", "text", "html"})
BINARY: frozenset[Format] = frozenset({"epub", "docx", "pdf"})
# Formats that need an optional extra: the module to import and the extra.
EXTRAS: dict[Format, tuple[str, str]] = {
    "docx": ("docx", "docx"),
    "pdf": ("typst", "pdf"),
}


def as_format(value: str) -> Format:
    """A format from its name, raising InvalidParameterError for anything else."""
    name = value.strip().lower()
    if name in FORMATS:
        return name  # type: ignore[return-value]
    raise InvalidParameterError(
        f"{value!r} is not an export format; use one of: {', '.join(FORMATS)}"
    )


def format_for(
    path: str | Path, format: str | None = None, *, tree: bool = False
) -> Format:
    """The format to save ``path`` in: ``format`` if given, else the file's
    suffix (``.pdf``, ``.html``, ...). A directory, a tree or a name without a
    suffix is Markdown."""
    if format is not None:
        return as_format(format)
    text = str(path)
    if tree or text.endswith(("/", "\\")) or Path(text).is_dir():
        return "markdown"
    suffix = Path(text).suffix.lower()
    if not suffix:
        return "markdown"
    if suffix in BY_SUFFIX:
        return BY_SUFFIX[suffix]
    known = ", ".join(sorted(BY_SUFFIX))
    raise InvalidParameterError(
        f"Can't tell what format {Path(text).name} should be from {suffix!r}; "
        f"name it {known}, or choose a format (format= or --format)"
    )


def require(format: Format) -> ModuleType | None:
    """Import the optional package a format needs, or explain how to install it."""
    if format not in EXTRAS:
        return None
    module, extra = EXTRAS[format]
    try:
        return importlib.import_module(module)
    except ImportError:
        package = f'"ecfr-md[{extra}] @ git+{PROJECT_URL}"'
        raise MissingDependencyError(
            f"Saving {format.upper()} needs the optional {extra!r} extra. Install it "
            f"with: uv tool install {package} (for the ecfr-md command), or "
            f"uv add {package} / pip install {package} (for Python)"
        ) from None
