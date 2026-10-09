"""Exceptions raised by the eCFR client.

Everything derives from :class:`ECFRError`, so ``except ECFRError`` catches all
failures originating in this package.
"""

import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx2


class ECFRError(Exception):
    """Base class for all cfr-md errors."""


class InvalidParameterError(ECFRError, ValueError):
    """A request parameter failed validation before being sent."""


class TitleNotFoundError(ECFRError, LookupError):
    """The requested CFR title does not exist or has no content."""


class ECFRConnectionError(ECFRError):
    """The API could not be reached (DNS, TLS, connection reset, ...)."""


class ECFRTimeoutError(ECFRConnectionError):
    """The API did not respond within the configured timeout."""


class ResponseFormatError(ECFRError):
    """The API returned a body that could not be parsed as expected."""


class APIError(ECFRError):
    """The API answered with an HTTP error status."""

    def __init__(self, response: httpx2.Response) -> None:
        self.response = response
        self.status_code = response.status_code
        self.url = str(response.request.url)
        self.detail = _error_detail(response)
        self.retry_after = _retry_after(response)
        message = f"{self.status_code} {response.reason_phrase} for {self.url}"
        super().__init__(f"{message}: {self.detail}" if self.detail else message)


class BadRequestError(APIError):
    """HTTP 400: the API rejected the request parameters."""


class NotFoundError(APIError):
    """HTTP 404: no content matches the request."""


class RateLimitError(APIError):
    """HTTP 429: too many requests."""


class ServerError(APIError):
    """HTTP 5xx: the API failed or is temporarily unavailable."""


def api_error(response: httpx2.Response) -> APIError:
    """Build the most specific :class:`APIError` for an error response."""
    match response.status_code:
        case 400:
            cls = BadRequestError
        case 404:
            cls = NotFoundError
        case 429:
            cls = RateLimitError
        case code if code >= 500:
            cls = ServerError
        case _:
            cls = APIError
    return cls(response)


def _error_detail(response: httpx2.Response) -> str:
    """Extract a human-readable message from the API's assorted error bodies.

    Seen in the wild: ``{"error": "..."}``, ``{"message": "..."}``,
    ``{"errors": {"field": ["..."]}}``, ``null``, ``{}`` and XML
    ``<hash><error>...</error></hash>``.
    """
    text = response.text.strip()
    try:
        body = json.loads(text)
    except ValueError:
        match = re.search(r"<error>(.*?)</error>", text, re.S)
        return match.group(1).strip() if match else text[:500]
    return "; ".join(_messages(body))


def _messages(body: object) -> list[str]:
    match body:
        case str():
            return [body]
        case list():
            return [m for item in body for m in _messages(item)]
        case dict():
            known = [body[key] for key in ("error", "errors", "message") if key in body]
            return [m for value in known or body.values() for m in _messages(value)]
        case _:
            return []


def _retry_after(response: httpx2.Response) -> float | None:
    """Seconds to wait according to a ``Retry-After`` header, if present."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
