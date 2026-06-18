"""Week-1 test fixtures: tmp_db, test_settings, capture_logs, trace_context, migration_runner, fs_sandbox."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import yaml
import pytest

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.observability import JsonlFormatter
from stackowl.infra.trace import TraceContext


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """In-process DbPool backed by a temp file with all 8 migrations applied."""
    db_path = tmp_path / "test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
def test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Settings with TEST_MODE enabled and one stub openai provider."""
    config_file = tmp_path / "test_stackowl.yaml"
    config_file.write_text(
        yaml.dump({
            "test_mode": True,
            "providers": [{
                "name": "stub",
                "protocol": "openai",
                "base_url": "http://localhost:9999",
                "api_key": None,
                "default_model": "gpt-stub",
                "tier": "fast",
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(config_file))
    from stackowl.config.test_mode import TestModeGuard

    TestModeGuard._active = False  # type: ignore[attr-defined]
    return Settings()


@pytest.fixture()
def capture_logs() -> Generator[list[dict[str, Any]], None, None]:
    """Capture log records as parsed JSONL dicts for assertion in tests."""
    records: list[dict[str, Any]] = []
    formatter = JsonlFormatter()

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raw = formatter.format(record)
            records.append(json.loads(raw))

    handler = _Capture()
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger("stackowl")
    root.addHandler(handler)
    try:
        yield records
    finally:
        root.removeHandler(handler)


@pytest.fixture()
def trace_context() -> Generator[None, None, None]:
    """Start a fresh TraceContext for the test and reset it on teardown."""
    token = TraceContext.start(session_id="test-session")
    try:
        yield
    finally:
        TraceContext.reset(token)


@pytest.fixture()
def migration_runner(tmp_path: Path) -> MigrationRunner:
    """MigrationRunner bound to a temp-path database (migrations not yet run)."""
    return MigrationRunner(db_path=tmp_path / "migration_test.db")


@pytest.fixture()
def fs_sandbox(tmp_path: Path) -> Generator[dict[str, Path], None, None]:
    """Temporary directory tree mimicking the platform data layout."""
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    data.mkdir()
    logs.mkdir()
    os.environ["STACKOWL_DATA_DIR"] = str(data)
    os.environ["STACKOWL_LOG_DIR"] = str(logs)
    try:
        yield {"root": tmp_path, "data": data, "logs": logs}
    finally:
        os.environ.pop("STACKOWL_DATA_DIR", None)
        os.environ.pop("STACKOWL_LOG_DIR", None)
