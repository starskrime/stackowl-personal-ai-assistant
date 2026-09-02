"""Story 6.5 (part B) — KuzuSyncJobHandler, migration, pipeline classify."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
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
    """T15 — MigrationRunner discovers and runs EVERY migration .sql file.

    Name kept historical for log searchability. The expected count is now
    derived dynamically from the actual ``.sql`` files on disk (no more
    manual bumps on every new migration); the invariant under test is that
    the runner discovers all migration files with none silently skipped.
    """
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "stackowl"
        / "db"
        / "migrations"
    )
    expected = len(sorted(migrations_dir.glob("*.sql")))
    runner = MigrationRunner(db_path=tmp_path / "count.db")
    results = runner.run()
    assert len(results) == expected


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
        # ESC-51 — classify now calls traverse_many ONCE for every candidate id
        # rather than traverse per id. This double had to move with it: a stub
        # that keeps the old method name goes on passing while the real adapter
        # is called differently, which is this codebase's second failure mode
        # (test doubles that stopped resembling the real thing).
        async def traverse_many(
            self, entity_ids: list[str], max_hops: int = 2, limit: int = 25
        ) -> list[dict[str, Any]]:
            traversals.extend(entity_ids)
            return [{"name": "Spy", "entity_type": "TOPIC"}]

    class _NullBridge:
        async def retrieve(self, query: str, session_key: str) -> str:
            return ""

    services = StepServices(
        memory_bridge=_NullBridge(),  # type: ignore[arg-type]
        kuzu_adapter=_SpyAdapter(),  # type: ignore[arg-type]
    )
    token = set_services(services)
    try:
        state = PipelineState(
            trace_id="t",
            session_key="s",
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
