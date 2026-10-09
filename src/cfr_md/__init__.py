"""Download Code of Federal Regulations text from the eCFR API as Markdown.

>>> from cfr_md import ECFR
>>> with ECFR() as ecfr:
...     text = ecfr.markdown(45, part="164")
"""

from cfr_md.client import ECFR
from cfr_md.config import Settings, __version__
from cfr_md.errors import (
    APIError,
    BadRequestError,
    ECFRConnectionError,
    ECFRError,
    ECFRTimeoutError,
    InvalidParameterError,
    NotFoundError,
    RateLimitError,
    ResponseFormatError,
    ServerError,
    TitleNotFoundError,
)
from cfr_md.markdown import Layout, to_markdown, to_markdown_tree, write_markdown
from cfr_md.params import Hierarchy, PaginateBy, SearchFilters, SearchOrder

__all__ = [
    "ECFR",
    "APIError",
    "BadRequestError",
    "ECFRConnectionError",
    "ECFRError",
    "ECFRTimeoutError",
    "Hierarchy",
    "InvalidParameterError",
    "Layout",
    "NotFoundError",
    "PaginateBy",
    "RateLimitError",
    "ResponseFormatError",
    "SearchFilters",
    "SearchOrder",
    "ServerError",
    "Settings",
    "TitleNotFoundError",
    "__version__",
    "to_markdown",
    "to_markdown_tree",
    "write_markdown",
]
