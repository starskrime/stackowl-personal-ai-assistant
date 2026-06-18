"""Smoke tests verifying all 6 Week-1 fixtures are importable and functional."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from stackowl.infra.trace import TraceContext


def test_migration_runner_fixture(migration_runner: Any) -> None:
    # Expected count is derived dynamically from the actual .sql files on disk
    # (no more manual bumps on every new migration).
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    results = migration_runner.run()
    assert len(results) == expected
    assert all(r.action == "applied" for r in results)


async def test_tmp_db_fixture(tmp_db: Any) -> None:
    rows = await tmp_db.fetch_all("SELECT key FROM stackowl_meta WHERE key = 'schema_version'")
    assert rows == [{"key": "schema_version"}]


def test_test_settings_fixture(test_settings: Any) -> None:
    assert test_settings.test_mode is True
    assert len(test_settings.providers) == 1
    assert test_settings.providers[0].name == "stub"


def test_capture_logs_fixture(capture_logs: list[dict[str, Any]]) -> None:
    log = logging.getLogger("stackowl.test")
    log.setLevel(logging.DEBUG)
    log.warning("fixture smoke test")
    assert any(r["msg"] == "fixture smoke test" for r in capture_logs)
    assert all({"ts", "level", "module", "msg", "trace_id", "fields"}.issubset(r) for r in capture_logs)


def test_trace_context_fixture(trace_context: None) -> None:
    ctx = TraceContext.get()
    assert ctx["trace_id"] is not None
    assert ctx["session_id"] == "test-session"
    assert ctx["span_id"] is not None


def test_fs_sandbox_fixture(fs_sandbox: dict[str, Path]) -> None:
    assert fs_sandbox["data"].exists()
    assert fs_sandbox["logs"].exists()
    import os

    assert os.environ.get("STACKOWL_DATA_DIR") == str(fs_sandbox["data"])


def test_observability_jsonl_format(capture_logs: list[dict[str, Any]], trace_context: None) -> None:
    log = logging.getLogger("stackowl.db")
    log.setLevel(logging.DEBUG)
    log.info("test structured log")
    assert capture_logs
    record = capture_logs[-1]
    required = {"ts", "level", "module", "msg", "trace_id", "span_id", "parent_span_id", "session_id", "duration_ms", "fields"}
    assert required.issubset(record.keys()), f"missing fields: {required - record.keys()}"
    assert record["trace_id"] is not None
    parsed = json.dumps(record)
    assert "test structured log" in parsed


def test_sensitive_field_redaction(capture_logs: list[dict[str, Any]]) -> None:
    log = logging.getLogger("stackowl.config")
    log.setLevel(logging.DEBUG)
    record = logging.LogRecord(
        name="stackowl.config",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="testing redaction",
        args=(),
        exc_info=None,
    )
    record._fields = {"api_key": "sk-secret-123", "user": "boss"}  # type: ignore[attr-defined]
    from stackowl.infra.observability import JsonlFormatter, SensitiveFieldFilter

    SensitiveFieldFilter().filter(record)
    fmt = JsonlFormatter()
    output = json.loads(fmt.format(record))
    assert output["fields"]["api_key"] == "***"
    assert output["fields"]["user"] == "boss"
