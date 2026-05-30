"""Story 6.5 (part B) — KuzuSyncJobHandler, migration, pipeline classify."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.entity_extractor import EntityExtractor
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.memory.kuzu_sync_handler import KuzuSyncJobHandler

from tests._story_6_5_helpers import (  # noqa: F401 — re-exports
    StubProvider,
    StubRegistry,
    adapter,
    db,
    insert_committed,
    make_job,
    no_test_mode_guard,
)


# ---------------------------------------------------------------------------
# KuzuSyncJobHandler
# ---------------------------------------------------------------------------


async def test_kuzu_sync_handler_syncs_facts(
    adapter: KuzuAdapter, db: DbPool
) -> None:
    """T12 — handler syncs facts and writes kuzu_sync_log entries."""
    await insert_committed(db, "f-sync-1", "Hello from Berlin")
    response = (
        '[{"name": "Berlin", "entity_type": "LOCATION", "mentions": ["Berlin"]}]'
    )
    extractor = EntityExtractor(
        provider_registry=StubRegistry(StubProvider(response)),  # type: ignore[arg-type]
    )
    handler = KuzuSyncJobHandler(adapter, extractor, db, batch_size=10)
    result = await handler.execute(make_job())
    assert result.success is True
    assert "synced_count=1" in (result.output or "")
    log_rows = await db.fetch_all("SELECT fact_id, entity_count FROM kuzu_sync_log")
    assert len(log_rows) == 1
    assert log_rows[0]["fact_id"] == "f-sync-1"
    assert log_rows[0]["entity_count"] == 1


async def test_kuzu_sync_handler_empty_fact_set(
    adapter: KuzuAdapter, db: DbPool
) -> None:
    """T13 — handler returns success with synced_count=0 when no facts pending."""
    extractor = EntityExtractor(
        provider_registry=StubRegistry(StubProvider("[]")),  # type: ignore[arg-type]
    )
    handler = KuzuSyncJobHandler(adapter, extractor, db)
    result = await handler.execute(make_job())
    assert result.success is True
    assert "synced_count=0" in (result.output or "")


# ---------------------------------------------------------------------------
# Migration 0016
# ---------------------------------------------------------------------------


def test_migration_0016_file_exists() -> None:
    """T14 — migration file exists."""
    path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
        / "0016_kuzu_sync.sql"
    )
    assert path.exists(), f"missing migration: {path}"


def test_migration_count_is_16(tmp_path: Path) -> None:
    """T15 — MigrationRunner discovers exactly 19 migration files.

    Name kept historical for log searchability; updated when Story 6.6
    (Migration 0017 dreamworker_runs) raised the count to 17, again
    when Story 7.1 (Migration 0018 jobs_v2) raised it to 18, again
    when Story 7.4 (Migration 0019 notification_overrides) raised it to 19,
    and again when Story 7.5 (Migration 0020 webhook_events_log) raised it to 20.
    """
    runner = MigrationRunner(db_path=tmp_path / "count.db")
    results = runner.run()
    assert len(results) == 36  # +0036 E4 staged_facts agent_self


# ---------------------------------------------------------------------------
# Pipeline classify integration
# ---------------------------------------------------------------------------


async def test_pipeline_classify_calls_kuzu_traverse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T18 — classify step calls KuzuAdapter.traverse when adapter wired."""
    from stackowl.pipeline.services import StepServices, reset_services, set_services
    from stackowl.pipeline.state import PipelineState
    from stackowl.pipeline.steps import classify

    traversals: list[str] = []

    class _SpyAdapter:
        async def traverse(
            self, entity_id: str, max_hops: int = 2
        ) -> list[dict[str, Any]]:
            traversals.append(entity_id)
            return [{"name": "Spy", "entity_type": "TOPIC"}]

    class _NullBridge:
        async def retrieve(self, query: str, session_id: str) -> str:
            return ""

    services = StepServices(
        memory_bridge=_NullBridge(),  # type: ignore[arg-type]
        kuzu_adapter=_SpyAdapter(),  # type: ignore[arg-type]
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id="t",
            session_id="s",
            input_text="tell me about Berlin and Alice",
            channel="cli",
            owl_name="secretary",
            pipeline_step="",
        )
        new_state = await classify.run(state)
    finally:
        reset_services(token)
    assert traversals, "kuzu.traverse was not invoked"
    assert new_state.memory_context is not None
    assert "Related entities" in new_state.memory_context
