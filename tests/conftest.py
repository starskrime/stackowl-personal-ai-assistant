"""Week-1 test fixtures: tmp_db, test_settings, capture_logs, trace_context, migration_runner, fs_sandbox."""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._source_helpers import install as _install_source_guard
from stackowl.config.settings import Settings
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.observability import JsonlFormatter
from stackowl.infra.trace import TraceContext

# 139 call sites across 82 files assert on source text read back with
# `inspect.getsource`, which seeks to a line number frozen at IMPORT time and
# reads the file as it is NOW. Edit the tree under a running suite and those
# reads land at the wrong offset — silently, in both directions. Installed here
# rather than at each call site so no test has to remember. See
# tests/_source_helpers.py for the 2026-09-04 run this comes from.
_install_source_guard()


@pytest.fixture(autouse=True)
def _drop_the_presented_tools_memo() -> Generator[None]:
    """Prevent the process-global presented-tools memo from leaking across tests.

    execute() memoizes the presented tool array on
    ``(session_key, owl, protocol, window, hydrated)``. The pins are deliberately
    NOT in that key: Law 1 wants the array byte-stable for the life of a
    conversation, or the cached prefix is void. In production that is right,
    because one session's owl does not change its skill set between turns. Across
    TESTS it is not: two tests that reuse a session key and owl name while
    building different worlds share one entry, and the second silently receives
    the first's array.

    MEASURED, not hypothesised. tests/journeys/test_skill_injection_journey.py
    passed file-by-file and failed as a suite: journey_a's array (no coupled
    tool) was served to journey_b, so the coupling assertion failed on a memo hit
    while the coupling itself worked — the trace showed total_pins=1. The
    dangerous direction is the mirror: a test that asserts a tool is ABSENT would
    PASS on a stale array that never contained it.

    Only the boundaries are cleared, so a test that populates the memo and then
    asserts a hit still works.
    """
    from stackowl.infra import presented_tools

    presented_tools.clear()
    yield
    presented_tools.clear()


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


@pytest.fixture(scope="session")
def _migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the migrated schema ONCE per session; tests copy the file (DEBT-38).

    MEASURED 2026-08-30 on this box: ``MigrationRunner().run()`` takes 9.3s,
    because there are now 128 migrations and each commits in its own exclusive
    transaction — 128 fsyncs. That per-migration durability is CORRECT for a real
    boot (a crash mid-migration must leave a consistent ledger) and worthless for
    a temp file that is deleted at the end of the test, so this is fixed on the
    TEST side and production semantics are untouched.

    ``tmp_db``'s docstring said "all 8 migrations". It was 8 once. Nobody
    re-read it as the number grew to 128, which is how a fixture quietly became
    the most expensive thing in the suite.

    A COPY IS NOT AN APPROXIMATION: verified before adopting — 226 schema objects
    and all 128 ledger rows are identical between a copied database and a freshly
    migrated one. Per-test cost falls from 9.3s to 2.7ms, ~3,600x.

    Tests that exercise the MIGRATIONS THEMSELVES (``tests/db/``) construct
    MigrationRunner directly and are deliberately untouched — they must run the
    real thing.
    """
    template = tmp_path_factory.mktemp("schema") / "template.db"
    MigrationRunner(db_path=template).run()
    return template


def seed_migrated_db(dest: Path, template: Path) -> Path:
    """Copy the session's migrated schema to ``dest``. See ``_migrated_template``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, dest)
    return dest


@pytest.fixture()
async def tmp_db(tmp_path: Path, _migrated_template: Path) -> AsyncGenerator[DbPool]:
    """In-process DbPool backed by a temp file carrying the full migrated schema.

    Copies the session template rather than re-running 128 migrations — see
    ``_migrated_template`` for the measurement and why a copy is exact.
    """
    db_path = seed_migrated_db(tmp_path / "test.db", _migrated_template)
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

    THE LEVEL MATTERS AS MUCH AS ``propagate``, and restoring only the latter
    left half the bug in place until 2026-09-01. ``setup_logging`` also calls
    ``setLevel(INFO)`` on that same ``stackowl`` logger, and a DEBUG record is
    discarded AT THE LOGGER before propagation is ever consulted —
    ``isEnabledFor(DEBUG)`` goes False. ``caplog.at_level(DEBUG)`` raises the
    level on the ROOT logger and on its own handler, neither of which can undo a
    named logger's own gate.

    So the propagate-only fix rescued the ERROR/WARNING assertions this
    docstring was written for (the kuzu ones) and left every DEBUG assertion
    still blind. MEASURED: three tests — mcp's register_on_recycled_noop, owls'
    it_is_still_observable_at_debug and pipeline's
    derive_skips_observably_when_tier_unset — passed alone and failed in the
    full run, reproducible with `pytest tests/cli <the test>`.

    NOTSET rather than DEBUG: it makes the logger defer to the root level, which
    is exactly the knob ``caplog.at_level`` controls, so a test asking for
    WARNING still gets WARNING rather than a flood.
    """
    logger = logging.getLogger("stackowl")
    previous_propagate = logger.propagate
    previous_level = logger.level
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
    try:
        yield
    finally:
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)
