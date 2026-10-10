"""Code of Federal Regulations text from the eCFR API, as navigable Markdown.

The public API is what this module exports, plus the response models in
:mod:`ecfr_md.models`. Other modules are internal and may change.

>>> from ecfr_md import ECFR
>>> with ECFR() as ecfr:
...     part = ecfr.get("45 CFR Part 164")
...     part.save("hipaa.md")
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
from ecfr_md.sync import SyncResult

__all__ = [
    "ECFR",
    "APIError",
    "AmbiguousNodeError",
    "Appendix",
    "BadRequestError",
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
    "Node",
    "NodeNotFoundError",
    "NotFoundError",
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
    "load",
]
