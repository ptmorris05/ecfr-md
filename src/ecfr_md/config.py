"""Constants and client settings.

Settings come from, highest precedence first: arguments in Python
(``Settings(...)``, ``Settings.load(...)``, ``ecfr_md.configure(...)``),
command-line options, ``ECFR_MD_*`` environment variables, the config file
(``config.toml`` in the user config directory, or ``--config PATH`` /
``ECFR_MD_CONFIG``), and the defaults: the official eCFR and GovInfo sites.
:meth:`Settings.explain` says where each value came from.
"""

import json
import os
import sys
import tomllib
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

from ecfr_md.errors import InvalidParameterError

__version__ = version("ecfr-md")

BASE_URL = "https://www.ecfr.gov"
SITE_URL = BASE_URL
BULK_URL = "https://www.govinfo.gov/bulkdata/ECFR"
USC_URL = (
    "https://uscode.house.gov/view.xhtml"
    "?req=granuleid:USC-prelim-title{title}-section{section}&num=0&edition=prelim"
)
FR_URL = "https://www.federalregister.gov/citation/{volume}-FR-{page}"
PUBLIC_LAW_URL = "https://www.congress.gov/public-law/{congress}{suffix}-congress/public-law/{number}"
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
ENV_PREFIX = "ECFR_MD_"
CONFIG_ENV = "ECFR_MD_CONFIG"

Origin = str  # "SDK", "CLI", "env ECFR_MD_X", "file /path/config.toml", "default"


class Settings(BaseModel):
    """Settings for the eCFR client: where text comes from and how it's fetched.

    ``Settings()`` is the defaults plus what you pass. ``Settings.load()``
    also reads ``ECFR_MD_*`` environment variables and the config file, as
    the command line does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(default=BASE_URL, description="eCFR API base URL.")
    site_url: str = Field(
        default=SITE_URL,
        description="eCFR web site for page links in saved files and output.",
    )
    image_url: str | None = Field(
        default=None,
        description="Host for images in the text (default: site_url).",
    )
    source: Literal["auto", "api", "bulk"] = Field(
        default="auto",
        description=(
            "Where text comes from: the eCFR API, GovInfo's bulk title files, "
            "or auto: whichever is faster for each request."
        ),
    )
    bulk_url: str = Field(
        default=BULK_URL, description="GovInfo bulk eCFR XML (one file per title)."
    )
    bulk_min_bytes: int | None = Field(
        default=None,
        gt=0,
        description=(
            "With source auto, use bulk for text at least this large (bytes), "
            "instead of estimating which is faster."
        ),
    )
    api_latency: float = Field(
        default=4.0,
        gt=0,
        description=(
            "Starting estimate of seconds per eCFR API request for a part, "
            "with 2 in flight (measured November 2026)."
        ),
    )
    bulk_bandwidth: float = Field(
        default=2_500_000,
        gt=0,
        description="Starting estimate of bytes per second from the bulk host.",
    )
    learn_costs: bool = Field(
        default=True,
        description="Refine those estimates from each download, kept in the cache.",
    )
    usc_url: str = Field(
        default=USC_URL,
        description="Link for a U.S. Code citation; {title} and {section}.",
    )
    fr_url: str = Field(
        default=FR_URL,
        description="Link for a Federal Register citation; {volume} and {page}.",
    )
    public_law_url: str = Field(
        default=PUBLIC_LAW_URL,
        description=(
            "Link for a public law; {congress}, its {suffix} (st, nd, rd, th) "
            "and {number}."
        ),
    )
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
        default=2,
        ge=1,
        le=16,
        description=(
            "Most requests in flight at once, across all threads. The eCFR "
            "answers 429 (slow down) to 3 or more, so more isn't faster."
        ),
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

    _origins: dict[str, Origin] = PrivateAttr(default_factory=dict)
    _config: Path | None = PrivateAttr(default=None)

    @property
    def images(self) -> str:
        """Where images in the text are: ``image_url``, else ``site_url``."""
        return self.image_url or self.site_url

    @property
    def links(self) -> dict[str, str]:
        """Link templates for other codes, as ``find_references`` takes them."""
        return {
            "usc": self.usc_url,
            "fr": self.fr_url,
            "public_law": self.public_law_url,
        }

    @property
    def config_file(self) -> Path:
        """The config file these settings were loaded from, or would be."""
        return self._config or Path(os.environ.get(CONFIG_ENV) or config_path())

    @classmethod
    def load(
        cls,
        config: str | Path | None = None,
        *,
        env: Mapping[str, str] | None = None,
        origin: Origin = "SDK",
        read: bool = True,
        **overrides: Any,
    ) -> Self:
        """Settings from the config file and environment, then ``overrides``.

        ``config`` is the config file; by default ``ECFR_MD_CONFIG`` or
        :func:`config_path`. A missing file is no error. ``env`` defaults to
        ``os.environ``; ``origin`` names where ``overrides`` came from in
        :meth:`explain`. ``read=False`` skips the file and the environment,
        but still records which file is the config file.
        """
        env = os.environ if env is None else env
        path = Path(config) if config else Path(env.get(CONFIG_ENV) or config_path())
        values: dict[str, Any] = {}
        origins: dict[str, Origin] = {}
        given = [(read_config(path), f"file {path}"), (_from_env(env), "env")]
        for found, where in [*(given if read else []), (overrides, origin)]:
            for key, value in found.items():
                values[key] = value
                origins[key] = (
                    f"env {ENV_PREFIX}{key.upper()}" if where == "env" else where
                )
        try:
            settings = cls(**values)
        except ValidationError as exc:
            sources = sorted({origins.get(str(e["loc"][0]), "") for e in exc.errors()})
            raise InvalidParameterError(
                f"Invalid settings (from {', '.join(sources)}): "
                + "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                    for e in exc.errors(include_url=False)
                )
            ) from exc
        settings._origins = origins
        settings._config = path
        return settings

    def explain(self) -> dict[str, tuple[Any, Origin]]:
        """Each setting's value and where it came from: "SDK", "CLI",
        "env ECFR_MD_...", "file <path>" or "default"."""
        given = self._origins or dict.fromkeys(self.model_fields_set, "SDK")
        return {
            name: (getattr(self, name), given.get(name, "default"))
            for name in type(self).model_fields
        }


def config_path() -> Path:
    """The default config file: ``config.toml`` in the user config directory."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ecfr-md" / "config.toml"


def read_config(path: Path) -> dict[str, Any]:
    """The settings in a config file; none if it doesn't exist."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InvalidParameterError(
            f"Can't read the config file {path}: {exc}"
        ) from exc
    unknown = sorted(set(data) - set(Settings.model_fields))
    if unknown:
        raise InvalidParameterError(
            f"Unknown setting{'s' if len(unknown) > 1 else ''} in {path}: "
            f"{', '.join(unknown)}"
        )
    return data


def write_config(path: Path, values: Mapping[str, Any]) -> None:
    """Write settings as TOML: plain values, then the headers table."""
    lines = [
        f"{key} = {_toml(value)}"
        for key, value in values.items()
        if key != "headers" and value is not None
    ]
    headers = values.get("headers") or {}
    if headers:
        lines += ["", "[headers]"]
        lines += [f"{json.dumps(k)} = {_toml(v)}" for k, v in headers.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.part")
    partial.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return json.dumps(str(value))  # A TOML basic string, escaped like JSON.


def _from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Settings given as ECFR_MD_<NAME> variables; headers as a JSON object."""
    found: dict[str, Any] = {}
    for name in Settings.model_fields:
        value = env.get(f"{ENV_PREFIX}{name.upper()}")
        if value is None:
            continue
        if name == "headers":
            try:
                found[name] = json.loads(value)
            except ValueError as exc:
                raise InvalidParameterError(
                    f'{ENV_PREFIX}HEADERS must be a JSON object, e.g. {{"From": '
                    f'"me@example.com"}}; got {value!r}'
                ) from exc
        else:
            found[name] = value
    return found
