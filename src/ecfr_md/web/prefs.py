"""The Settings page: every field of :class:`~ecfr_md.config.Settings`, as a form.

Fields are read from the model, not listed here, so settings added to the
model appear on the page by themselves. Values are validated by the model;
the page applies them to a new client and can write them to the config
file, keeping whatever else the file holds.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tomllib
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from ecfr_md.config import Settings

Kind = Literal["bool", "int", "float", "text", "path", "mapping", "choice", "json"]
CONFIG_ENV = "ECFR_MD_CONFIG"


@dataclass(frozen=True)
class FieldView:
    """A setting as the form shows it."""

    name: str
    kind: Kind
    value: str
    """The current value as the form writes it."""
    default: str
    description: str
    origin: str
    """Where the value comes from: "default", "set here", or what
    ``Settings.explain()`` says ("env", "file", "cli", ...)."""
    optional: bool
    choices: tuple[str, ...] = ()
    checked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "value": self.value,
            "default": self.default,
            "description": self.description,
            "origin": self.origin,
            "optional": self.optional,
            "choices": list(self.choices),
        }


def fields(
    settings: BaseModel, changed: frozenset[str] = frozenset()
) -> list[FieldView]:
    """The settings' fields, in the model's order."""
    origins = _origins(settings)
    model = type(settings)
    views = []
    for name, info in model.model_fields.items():
        kind, optional, choices = _kind(info.annotation)
        value = getattr(settings, name)
        default = info.get_default(call_default_factory=True)
        origin = origins.get(name) or (
            "set here" if name in changed else "default" if value == default else "set"
        )
        views.append(
            FieldView(
                name=name,
                kind=kind,
                value=_shown(value, kind),
                default=_shown(default, kind),
                description=info.description or "",
                origin=origin,
                optional=optional,
                choices=choices,
                checked=kind == "bool" and bool(value),
            )
        )
    return views


def _kind(annotation: Any) -> tuple[Kind, bool, tuple[str, ...]]:
    """How a field is edited, whether it can be left empty, and its choices."""
    args = typing.get_args(annotation)
    optional = False
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        optional = len(inner) < len(args)
        annotation = inner[0] if len(inner) == 1 else annotation
        args = typing.get_args(annotation)
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return "choice", optional, tuple(str(a) for a in args)
    if origin in (dict, Mapping) or annotation is dict:
        return "mapping", optional, ()
    simple: dict[Any, Kind] = {
        bool: "bool",
        int: "int",
        float: "float",
        str: "text",
        Path: "path",
    }
    return simple.get(annotation, "json"), optional, ()


def _shown(value: Any, kind: Kind) -> str:
    if value is None:
        return ""
    if kind == "bool":
        return "true" if value else "false"
    if kind == "mapping":
        return "\n".join(f"{k}: {v}" for k, v in value.items())
    if kind == "json":
        return json.dumps(value, default=str)
    return str(value)


def _origins(settings: BaseModel) -> dict[str, str]:
    """Where each value came from, if the model can say (``explain()``)."""
    explain = getattr(settings, "explain", None)
    if not callable(explain):
        return {}
    try:
        found = explain()
    except Exception:  # A newer explain() with other needs: show no badges.
        return {}
    if not isinstance(found, Mapping):
        return {}
    origins = {}
    for name, item in found.items():
        origin = getattr(item, "origin", None)
        if origin is None and isinstance(item, Mapping):
            origin = item.get("origin")
        if origin is None and isinstance(item, tuple | list) and len(item) == 2:
            origin = item[1]
        if origin is not None:
            origins[str(name)] = str(getattr(origin, "value", origin))
    return origins


def parse(
    form: Mapping[str, str], current: Settings
) -> tuple[Settings, frozenset[str]]:
    """New settings from a submitted form, and the names that changed.

    Raises :class:`Invalid` listing each field's problem.
    """
    model = type(current)
    values = current.model_dump()
    for name, info in model.model_fields.items():
        kind, optional, _ = _kind(info.annotation)
        if kind == "bool":
            values[name] = name in form
            continue
        if name not in form:
            continue
        text = form[name].strip()
        if not text and optional:
            values[name] = None
        elif kind == "mapping":
            values[name] = _mapping(text)
        elif kind == "json":
            try:
                values[name] = json.loads(text)
            except ValueError:
                values[name] = text
        else:
            values[name] = text
    try:
        settings = model.model_validate(values)
    except ValidationError as exc:
        raise Invalid(
            {
                ".".join(str(p) for p in e["loc"]): e["msg"]
                for e in exc.errors(include_url=False)
            }
        ) from None
    changed = frozenset(
        name
        for name in model.model_fields
        if getattr(settings, name) != getattr(current, name)
    )
    return settings, changed


def _mapping(text: str) -> dict[str, str]:
    found = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            found[key.strip()] = value.strip()
    return found


class Invalid(ValueError):
    """Settings that don't validate: field name to message."""

    def __init__(self, problems: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in problems.items()))
        self.problems = problems


def config_path() -> Path:
    """The config file the page writes: ``ecfr_md.config.config_path()`` where
    it exists, else ``$ECFR_MD_CONFIG``, else ``ecfr-md/config.toml`` in the
    user's config directory."""
    found = getattr(importlib.import_module("ecfr_md.config"), "config_path", None)
    if callable(found):
        return Path(str(found()))
    if env := os.environ.get(CONFIG_ENV):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "ecfr-md" / "config.toml"


def write_config(settings: Settings, path: Path) -> Path:
    """Write the settings that differ from the defaults to a TOML file.

    Keys the file holds that aren't settings, and tables, are kept;
    settings back at their defaults are removed.
    """
    try:
        existing = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = {}
    model = type(settings)
    for name, info in model.model_fields.items():
        value = getattr(settings, name)
        if value == info.get_default(call_default_factory=True) or value is None:
            existing.pop(name, None)
        else:
            existing[name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(existing), encoding="utf-8")
    return path


def dumps(data: Mapping[str, Any]) -> str:
    """A small TOML writer: values, then tables."""
    lines = [
        f"{_key(k)} = {_value(v)}"
        for k, v in data.items()
        if not isinstance(v, dict) or _inline(v)
    ]
    for name, table in data.items():
        if isinstance(table, dict) and not _inline(table):
            lines += ["", f"[{_key(name)}]", dumps(table).rstrip("\n")]
    return "\n".join(lines) + "\n"


def _inline(table: Mapping[str, Any]) -> bool:
    """Settings that are mappings of text (headers) are written inline."""
    return all(isinstance(v, str) for v in table.values())


def _key(key: str) -> str:
    return key if key.replace("_", "").replace("-", "").isalnum() else json.dumps(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items())
            + " }"
        )
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    return json.dumps(str(getattr(value, "value", value)), ensure_ascii=False)
