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
def _restore_test_mode_guard() -> Generator[None]:
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
    ``_restore_test_mode_guard``: many tests share ``session_key="test-session"``
    via the ``trace_context`` fixture, and this store is keyed on that string.
    """
    from stackowl.infra import hydrated_tools

    hydrated_tools._by_session.clear()
    try:
        yield
    finally:
        hydrated_tools._by_session.clear()


@pytest.fixture()
async def tmp_db(tmp_path: Path) -> AsyncGenerator[DbPool]:
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
def capture_logs() -> Generator[list[dict[str, Any]]]:
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
def trace_context() -> Generator[None]:
    """Start a fresh TraceContext for the test and reset it on teardown."""
    token = TraceContext.start(session_key="test-session")
    try:
        yield
    finally:
        TraceContext.reset(token)


@pytest.fixture()
def migration_runner(tmp_path: Path) -> MigrationRunner:
    """MigrationRunner bound to a temp-path database (migrations not yet run)."""
    return MigrationRunner(db_path=tmp_path / "migration_test.db")


@pytest.fixture()
def fs_sandbox(tmp_path: Path) -> Generator[dict[str, Path]]:
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


@pytest.fixture(autouse=True)
def _isolate_stackowl_home(tmp_path_factory: pytest.TempPathFactory) -> Generator[None]:
    """No test may touch the operator's real ~/.stackowl. Autouse, every test.

    WHY THIS EXISTS, and it is not hypothetical — three separate instances were
    found on 2026-08-14, all from the same gap:

      * ``tests/smoke/test_e4_s2_skill_manage_consent_telegram_smoke.py`` wrote
        into the real skills tree and LEFT A REAL SKILL BEHIND on 2026-08-07
        (``learned/greet-politely``), which then failed later runs with
        "already exists".
      * ``tests/smoke/test_e4_s1_memory_telegram_smoke.py`` would have written
        its fact into the real curated profile once repaired.
      * ``tests/journeys/test_memory_fix_guards.py`` ACTUALLY DID: the entry
        ``[permanent] the deploy bastion host is bastion-prod-7`` is test data
        sitting in the operator's live USER.md, consuming his permanent budget
        and entering his system prompt on every turn.

    THE GAP was that conftest already isolated ``STACKOWL_DATA_DIR`` — which
    overrides ``workspace()`` and therefore the DATABASE — while ``home()``
    reads ``STACKOWL_HOME``, which nothing set. So every test got an isolated
    database and the real home directory, and anything writing curated memory,
    skills, or backups reached straight through.

    Tests that deliberately exercise path resolution (``tests/paths/``) set the
    variable themselves; a later ``monkeypatch.setenv`` still wins over this.
    """
    previous = os.environ.get("STACKOWL_HOME")
    os.environ["STACKOWL_HOME"] = str(tmp_path_factory.mktemp("stackowl_home"))
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("STACKOWL_HOME", None)
        else:
            os.environ["STACKOWL_HOME"] = previous


@pytest.fixture(autouse=True)
def _caplog_can_see_stackowl_logs() -> Generator[None]:
    """Keep ``caplog`` able to observe ``stackowl.*`` records.

    ``observability.configure_logging`` sets ``propagate = False`` on the
    ``stackowl`` logger so records go to the JSONL file and not to stderr. That
    is correct in production — but pytest's ``caplog`` attaches its handler to
    the TRUE root logger, so once ANY test triggers that configuration, every
    later ``caplog`` assertion about a ``stackowl.*`` logger silently sees
    nothing.

    The result is an order-dependent suite: tests that assert "we logged a LOUD
    ERROR" pass in isolation and fail once something earlier configured logging.
    Two of them (kuzu degradation) were doing exactly that, and the failure mode
    is the worst kind — it looks like the code stopped logging.

    Restored per-test rather than once, because configure_logging may run at any
    point during a test and flip it back.
    """
    logger = logging.getLogger("stackowl")
    previous = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = previous
