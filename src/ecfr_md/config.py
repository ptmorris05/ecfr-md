"""Constants and client settings."""

from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__version__ = version("ecfr-md")

BASE_URL = "https://www.ecfr.gov"
PROJECT_URL = "https://github.com/ptmorris05/ecfr-md"
# Identifies the client to the eCFR's operators, with where to find it.
USER_AGENT = f"ecfr-md/{__version__} (+{PROJECT_URL})"
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Text above the part level larger than this (bytes, per the structure
# endpoint) is fetched part by part; the API times out on the largest titles.
SPLIT_ABOVE = 50_000_000
DISCLAIMER = (
    "The eCFR is an editorial compilation of CFR material and Federal Register "
    "amendments. It is not an official legal edition of the CFR."
)


class Settings(BaseModel):
    """Connection settings for the eCFR API client."""

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(default=BASE_URL, description="eCFR API base URL.")
    user_agent: str = Field(
        default=USER_AGENT,
        description="User-Agent header. Consider including contact details.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra request headers, e.g. --headers.From me@example.com.",
    )
    timeout: float = Field(
        default=60.0, gt=0, description="Per-request timeout in seconds."
    )
    max_attempts: int = Field(
        default=5, ge=1, description="Attempts per request, including the first."
    )
    backoff_initial: float = Field(
        default=1.0,
        ge=0,
        description="First retry delay in seconds; doubles each retry.",
    )
    backoff_max: float = Field(
        default=60.0, ge=0, description="Upper bound on any retry delay, in seconds."
    )
    rate_limit: float | None = Field(
        default=None,
        gt=0,
        description="Maximum requests per second (unlimited if unset).",
    )
    concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Requests in flight at once when fetching parts.",
    )
    cache: bool = Field(
        default=True,
        description="Keep downloaded text and reuse it until the eCFR changes it.",
    )
    cache_dir: Path | None = Field(
        default=None, description="Cache directory (default: the user cache directory)."
    )
    cache_max_age: float | None = Field(
        default=None,
        gt=0,
        description="Download again when cached text is this many seconds old.",
    )
