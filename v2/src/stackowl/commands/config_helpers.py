"""Pure helpers used by :mod:`stackowl.commands.config_command`.

Kept separate so the command class itself stays under the 300-line budget
and helpers can be unit-tested in isolation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML

from stackowl.infra.observability import log


def config_path() -> Path:
    """Resolve the YAML config path honouring ``STACKOWL_CONFIG_FILE``."""
    return Path(os.environ.get("STACKOWL_CONFIG_FILE", "stackowl.yaml"))


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    return y


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML preserving comments. Returns empty dict on missing/invalid."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            loaded = _yaml().load(fh)
    except Exception as exc:
        log.config.warning(
            "[commands] config.load_yaml: parse failed",
            extra={"_fields": {"path": str(path), "error": str(exc)}},
        )
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        _yaml().dump(data, fh)


def resolve_field(settings_cls: type[BaseModel], key: str) -> tuple[type[BaseModel] | None, str, Any, dict[str, Any]]:
    """Walk a dotted ``key`` through ``settings_cls`` returning leaf info.

    Returns ``(owner_model, leaf_name, field_default, json_schema_extra)``.
    ``owner_model`` is ``None`` when the path does not exist.
    """
    parts = key.split(".")
    model: type[BaseModel] = settings_cls
    for part in parts[:-1]:
        field = model.model_fields.get(part)
        if field is None or not isinstance(field.annotation, type) or not issubclass(field.annotation, BaseModel):
            return None, parts[-1], None, {}
        model = field.annotation
    leaf = parts[-1]
    info = model.model_fields.get(leaf)
    if info is None:
        return None, leaf, None, {}
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    return model, leaf, info.default, extra


def set_nested(data: dict[str, Any], parts: list[str], value: Any) -> None:
    """Navigate/create nested dicts then set the leaf key."""
    cursor: dict[str, Any] = data
    for key in parts[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[key] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def delete_nested(data: dict[str, Any], parts: list[str]) -> bool:
    cursor: dict[str, Any] = data
    for key in parts[:-1]:
        nxt = cursor.get(key)
        if not isinstance(nxt, dict):
            return False
        cursor = nxt
    return cursor.pop(parts[-1], None) is not None


def flatten(
    prefix: str,
    value: Any,
    sensitive_keys: set[str],
    out: list[tuple[str, str]],
) -> None:
    """Flatten nested dicts into dot-notation ``(key, repr)`` pairs."""
    if isinstance(value, dict):
        for k, v in value.items():
            sub = f"{prefix}.{k}" if prefix else str(k)
            flatten(sub, v, sensitive_keys, out)
        return
    rendered = "***" if prefix in sensitive_keys else stringify(value)
    out.append((prefix, rendered))


def stringify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def coerce_scalar(raw: str) -> Any:
    """Best-effort scalar coercion: bool, int, float, null, else str.

    Each conversion failure is downgraded to a warning so the cascade remains
    auditable without flooding logs at info/error level (B5 compliant).
    """
    lower = raw.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"null", "none", "~"}:
        return None
    int_value = _try_int(raw)
    if int_value is not None:
        return int_value
    float_value = _try_float(raw)
    if float_value is not None:
        return float_value
    return raw


def _try_int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        # Expected fall-through; coerce_scalar will try float next.
        log.config.debug(
            "[commands] config.coerce_scalar: not int — trying float",
            extra={"_fields": {"raw": raw[:40]}},
        )
        return None


def _try_float(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        # Expected fall-through; coerce_scalar will keep raw string.
        log.config.debug(
            "[commands] config.coerce_scalar: not float — keeping str",
            extra={"_fields": {"raw": raw[:40]}},
        )
        return None


def collect_sensitive(model: type[BaseModel], prefix: str, out: set[str]) -> None:
    """Walk ``model`` recursively and record dotted keys with ``sensitive=True``."""
    for name, field in model.model_fields.items():
        dotted = f"{prefix}.{name}" if prefix else name
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if extra.get("sensitive"):
            out.add(dotted)
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel):
            collect_sensitive(field.annotation, dotted, out)
