"""Week-1 test fixtures: tmp_db, test_settings, capture_logs, trace_context, migration_runner, fs_sandbox."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.observability import JsonlFormatter
from stackowl.infra.trace import TraceContext


@pytest.fixture(autouse=True)
def _restore_test_mode_guard() -> Generator[None, None, None]:
    """Prevent the process-global TestModeGuard latch from leaking across tests.

    ``Settings._post_init()`` calls ``TestModeGuard.activate()`` whenever a
    loaded config has ``test_mode=True`` — a class-level flag with no symmetric
    deactivation. Without this restore, any test that loads such a config (e.g.
    tests/journeys/commands/) leaves the latch set for every later test in the
    same process, breaking unrelated suites (tests/pipeline/ durable + drift)
    that expect live-I/O guards inactive. Snapshot on setup, restore on teardown
    so each test's mutation is invisible to the next.
    """
    from stackowl.config.test_mode import TestModeGuard

    saved = TestModeGuard.is_active()
    try:
        yield
    finally:
        TestModeGuard._active = saved  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_hydrated_tools() -> Generator[None]:
    """Prevent the process-global HydratedToolStore (FX-07) from leaking a
    session's hydrated tool names across tests — same rationale as
    ``_restore_test_mode_guard``: many tests share ``session_id="test-session"``
    via the ``trace_context`` fixture, and this store is keyed on that string.
    """
    from stackowl.infra import hydrated_tools

    hydrated_tools._by_session.clear()
    try:
        yield
    finally:
        hydrated_tools._by_session.clear()


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
