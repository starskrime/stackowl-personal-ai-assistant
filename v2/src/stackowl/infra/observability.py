"""Observability — JSONL logging, SensitiveFieldFilter, and named logger instances."""

from __future__ import annotations

import fnmatch
import json
import logging
import logging.handlers
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import platformdirs

from stackowl.infra.trace import TraceContext

_SENSITIVE_PATTERNS = (
    "api_key",
    "token",
    "password",
    "secret",
    "*_key",
    "key_*",
    "*token",
    "*secret",
)


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(fnmatch.fnmatch(k, p) for p in _SENSITIVE_PATTERNS)


def _clean_value(key: str, value: Any) -> Any:
    if _is_sensitive(key):
        return "***"
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        p = urlparse(value)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    return value


class SensitiveFieldFilter(logging.Filter):
    """Redacts sensitive keys and strips URL query strings from structured fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        fields: dict[str, Any] = getattr(record, "_fields", {})
        if fields:
            record._fields = {k: _clean_value(k, v) for k, v in fields.items()}
        return True


class JsonlFormatter(logging.Formatter):
    """Formats log records as single-line JSONL with fixed top-level schema."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = TraceContext.get()
        fields: dict[str, Any] = dict(getattr(record, "_fields", {}))
        if record.exc_info:
            fields["exc"] = self.formatException(record.exc_info)
        duration_ms: float | None = fields.pop("duration_ms", None)
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
            "trace_id": ctx["trace_id"],
            "span_id": ctx["span_id"],
            "parent_span_id": ctx["parent_span_id"],
            "session_id": ctx["session_id"],
            "duration_ms": duration_ms,
            "fields": fields,
        }
        return json.dumps(entry, default=str, ensure_ascii=False)


def _log_dir() -> Path:
    raw = os.environ.get("STACKOWL_LOG_DIR")
    return Path(raw) if raw else Path(platformdirs.user_log_dir("stackowl"))


def setup_logging() -> logging.Handler:
    """Configure JSONL file logging for the stackowl logger hierarchy.

    Call once at process startup; returns the configured handler.
    """
    level_name = os.environ.get("STACKOWL_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    retain_days = int(os.environ.get("STACKOWL_LOG_RETAIN_DAYS", "30"))

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "stackowl.jsonl"

    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        utc=True,
        backupCount=retain_days,
        encoding="utf-8",
    )

    def _namer(default_name: str) -> str:
        stem, _, date = default_name.rpartition(".")
        return str(Path(stem).parent / f"stackowl-{date}.jsonl")

    handler.namer = _namer
    handler.setFormatter(JsonlFormatter())
    handler.addFilter(SensitiveFieldFilter())

    root_logger = logging.getLogger("stackowl")
    root_logger.setLevel(level)
    root_logger.addHandler(handler)
    root_logger.propagate = False
    return handler


class _Loggers:
    startup = logging.getLogger("stackowl.startup")
    config = logging.getLogger("stackowl.config")
    db = logging.getLogger("stackowl.db")
    health = logging.getLogger("stackowl.health")
    tool = logging.getLogger("stackowl.tool")
    gateway = logging.getLogger("stackowl.gateway")
    engine = logging.getLogger("stackowl.engine")
    parliament = logging.getLogger("stackowl.parliament")
    memory = logging.getLogger("stackowl.memory")
    heartbeat = logging.getLogger("stackowl.heartbeat")
    scheduler = logging.getLogger("stackowl.scheduler")
    notifications = logging.getLogger("stackowl.notifications")
    webhook = logging.getLogger("stackowl.webhook")
    cli = logging.getLogger("stackowl.cli")
    tui = logging.getLogger("stackowl.tui")
    discord = logging.getLogger("stackowl.discord")
    slack = logging.getLogger("stackowl.slack")
    telegram = logging.getLogger("stackowl.telegram")
    whatsapp = logging.getLogger("stackowl.whatsapp")
    mcp = logging.getLogger("stackowl.mcp")
    plugins = logging.getLogger("stackowl.plugins")
    integrations = logging.getLogger("stackowl.integrations")
    infra = logging.getLogger("stackowl.infra")
    setup = logging.getLogger("stackowl.setup")
    security = logging.getLogger("stackowl.security")


log = _Loggers()
