"""Constants and client settings."""

from importlib.metadata import version

from pydantic import BaseModel, ConfigDict, Field

__version__ = version("cfr-md")

BASE_URL = "https://www.ecfr.gov"
USER_AGENT = f"cfr-md/{__version__}"
RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class Settings(BaseModel):
    """Connection settings for the eCFR API client."""

    model_config = ConfigDict(frozen=True)

    base_url: str = Field(BASE_URL, description="eCFR API base URL.")
    user_agent: str = Field(
        USER_AGENT,
        description="User-Agent header. Consider including contact details.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra request headers, e.g. --headers.From me@example.com.",
    )
    timeout: float = Field(60.0, gt=0, description="Per-request timeout in seconds.")
    max_attempts: int = Field(
        5, ge=1, description="Attempts per request, including the first."
    )
    backoff_initial: float = Field(
        1.0, ge=0, description="First retry delay in seconds; doubles each retry."
    )
    backoff_max: float = Field(
        60.0, ge=0, description="Upper bound on any retry delay, in seconds."
    )
    rate_limit: float | None = Field(
        None, gt=0, description="Maximum requests per second (unlimited if unset)."
    )
