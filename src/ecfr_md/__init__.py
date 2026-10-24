"""Code of Federal Regulations text from the eCFR API, as navigable Markdown.

The public API is what this module exports, plus the response models in
:mod:`ecfr_md.models`. Other modules are internal and may change.

>>> import ecfr_md
>>> section = ecfr_md.get("45 CFR 164.502")
>>> ecfr_md.save("45 CFR Part 164", "hipaa.md")

The module-level functions share one client with default settings. For other
settings, or to close connections when done, use :class:`ECFR`:

>>> from ecfr_md import ECFR, Settings
>>> with ECFR(Settings(user_agent="my-app/1.0")) as ecfr:
...     part = ecfr.get("45 CFR Part 164")
"""

from ecfr_md.citation import Citation
from ecfr_md.client import ECFR
from ecfr_md.config import Settings, __version__
from ecfr_md.document import (
    Appendix,
    Chapter,
    Division,
    Level,
    Node,
    Paragraph,
    Part,
    Section,
    Subchapter,
    SubjectGroup,
    Subpart,
    Subtitle,
    Title,
    load,
)
from ecfr_md.errors import (
    AmbiguousNodeError,
    APIError,
    BadRequestError,
    DateOutOfRangeError,
    ECFRConnectionError,
    ECFRError,
    ECFRTimeoutError,
    ECFRWarning,
    InvalidCitationError,
    InvalidParameterError,
    NodeNotFoundError,
    NotFoundError,
    RateLimitError,
    ResponseFormatError,
    ServerError,
    TitleNotFoundError,
)
from ecfr_md.params import Hierarchy, PaginateBy, SearchFilters, SearchOrder
from ecfr_md.progress import Progress
from ecfr_md.results import Change, Match, Matches, Outline
from ecfr_md.shortcuts import (
    changes,
    get,
    get_many,
    outline,
    save,
    search,
    sync,
)
from ecfr_md.syncing import SyncResult

__all__ = [
    "ECFR",
    "APIError",
    "AmbiguousNodeError",
    "Appendix",
    "BadRequestError",
    "Change",
    "Chapter",
    "Citation",
    "DateOutOfRangeError",
    "Division",
    "ECFRConnectionError",
    "ECFRError",
    "ECFRTimeoutError",
    "ECFRWarning",
    "Hierarchy",
    "InvalidCitationError",
    "InvalidParameterError",
    "Level",
    "Match",
    "Matches",
    "Node",
    "NodeNotFoundError",
    "NotFoundError",
    "Outline",
    "PaginateBy",
    "Paragraph",
    "Part",
    "Progress",
    "RateLimitError",
    "ResponseFormatError",
    "SearchFilters",
    "SearchOrder",
    "Section",
    "ServerError",
    "Settings",
    "Subchapter",
    "SubjectGroup",
    "Subpart",
    "Subtitle",
    "SyncResult",
    "Title",
    "TitleNotFoundError",
    "__version__",
    "changes",
    "get",
    "get_many",
    "load",
    "outline",
    "save",
    "search",
    "sync",
]
