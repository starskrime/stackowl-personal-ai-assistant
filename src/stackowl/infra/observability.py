"""Observability — JSONL logging, SensitiveFieldFilter, and named logger instances."""

from __future__ import annotations

import fnmatch
import json
import logging
import logging.handlers
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

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


# FX-04 — the key-only check above misses a secret that's part of a VALUE under
# an innocuous key: a rendered shell command logged as {"command": "curl -H
# 'Authorization: Bearer sk-...'"} matches no sensitive key name at all. These
# patterns catch the shape of the secret in the string content itself,
# independent of what key it's filed under.
_ENV_ASSIGN_RE = re.compile(
    r"((?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+", re.IGNORECASE,
)
_SECRET_SHAPE_PATTERNS = (
    re.compile(r"bearer\s+[a-z0-9._-]+", re.IGNORECASE),   # Authorization: Bearer <token>
    re.compile(r"sk-[a-z0-9]{20,}", re.IGNORECASE),         # OpenAI/Anthropic-style secret keys
    re.compile(r"akia[0-9a-z]{16}", re.IGNORECASE),         # AWS access key id
    re.compile(r"gh[a-z]_[a-z0-9]{20,}", re.IGNORECASE),    # GitHub PAT (ghp_/gho_/ghu_/ghs_/ghr_)
)
#: Skip scanning short strings — no secret shape above is under this long, and
#: it keeps the per-log-line cost negligible.
_MIN_SCAN_LEN = 12


def _redact_string(value: str) -> str:
    if len(value) < _MIN_SCAN_LEN:
        return value
    redacted = _ENV_ASSIGN_RE.sub(r"\1***", value)
    for pattern in _SECRET_SHAPE_PATTERNS:
        redacted = pattern.sub("***", redacted)
    return redacted


def _clean_value(key: str, value: Any) -> Any:
    if _is_sensitive(key):
        return "***"
    return _scan_value(value)


def _scan_value(value: Any) -> Any:
    """Recurse into nested dicts/lists and scan string content for secret-shaped
    substrings (FX-04) — closes the gap where ``_clean_value``'s key-only check
    can't see a secret hiding inside a value under an unrelated key.
    """
    if isinstance(value, dict):
        return {k: _clean_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scan_value(v) for v in value]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            p = urlparse(value)
            return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
        return _redact_string(value)
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


@asynccontextmanager
async def traced_span(
    logger: logging.Logger, name: str, **fields: Any
) -> AsyncIterator[None]:
    """Open a TraceContext child span AND log entry/exit with duration_ms.

    Latency-map instrumentation: every call site that wraps its work in this
    (instead of a bare ``TraceContext.span``) gets a span_id/parent_span_id
    edge in the JSONL trace tree AND a `duration_ms` on the exit line, so a
    trace_id's full request path can be reconstructed as a waterfall — see
    ``stackowl trace <trace_id>``. Exceptions are logged (with duration_ms)
    then re-raised — never swallowed.
    """
    async with TraceContext.span(name):
        t0 = time.monotonic()
        logger.debug(f"{name}: entry", extra={"_fields": dict(fields)})
        try:
            yield
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error(
                f"{name}: failed",
                exc_info=exc,
                extra={"_fields": {**fields, "duration_ms": duration_ms}},
            )
            raise
        else:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.debug(
                f"{name}: exit",
                extra={"_fields": {**fields, "duration_ms": duration_ms}},
            )


def _log_dir() -> Path:
    from stackowl.paths import StackowlHome
    return StackowlHome.logs_dir()


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
    skills = logging.getLogger("stackowl.skills")
    integrations = logging.getLogger("stackowl.integrations")
    infra = logging.getLogger("stackowl.infra")
    setup = logging.getLogger("stackowl.setup")
    security = logging.getLogger("stackowl.security")
    tenancy = logging.getLogger("stackowl.tenancy")
    tasks = logging.getLogger("stackowl.tasks")
    owls = logging.getLogger("stackowl.owls")


log = _Loggers()
