"""Shared fixtures and stubs for Story 6.4 tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.state import PipelineState


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for all Story 6.4 tests."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool]:
    """Per-test fresh DbPool with all migrations applied."""
    db_path = tmp_path / "story64.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


# StubEmbeddingProvider / StubEmbeddingRegistry / FakeLanceDB stood here. They
# existed to drive SqliteMemoryBridge's SEMANTIC recall path, which went with
# LanceDB in D08.2 — the vectors it ranked hydrated from committed_facts, empty
# since migration 0112. What remains below serves the tests that outlived them:
# the MemoryCommand suite in test_story_6_4b.py, which is live.


async def insert_committed(
    pool: DbPool, fact_id: str, content: str, committed_at: str | None = None
) -> None:
    """Insert a single committed_fact + matching FTS row."""
    iso = committed_at or datetime.now(UTC).isoformat()
    await pool.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, committed_at,
                source_type, source_ref, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (fact_id, content, b"\x00" * 16, "stub", iso, "conversation", "sess", "[]"),
    )
    rows = await pool.fetch_all(
        "SELECT rowid AS rid FROM committed_facts WHERE fact_id = ?", (fact_id,)
    )
    await pool.execute(
        "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)",
        (rows[0]["rid"], content),
    )


def make_state() -> PipelineState:
    """Minimal :class:`PipelineState` for slash-command tests."""
    return PipelineState(
        trace_id="t",
        session_key="s",
        input_text="",
        channel="cli",
        owl_name="secretary",
        pipeline_step="",
    )


async def seed_committed_facts(
    db: DbPool, n: int, *, content_size: int, confidence: float = 0.1
) -> None:
    """Seed n paired (staged, committed) facts of a given size for budget tests."""
    now = datetime.now(UTC).isoformat()
    for i in range(n):
        fid = f"budget-{i}"
        content = "x" * content_size
        await db.execute(
            """INSERT INTO staged_facts (
                   fact_id, content, source_type, source_ref, confidence,
                   staged_at, reinforcement_count, status, embedding, embedding_model
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                content,
                "conversation",
                "sess",
                confidence,
                now,
                0,
                "committed",
                None,
                None,
            ),
        )
        await db.execute(
            """INSERT INTO committed_facts
                   (fact_id, content, embedding, embedding_model, committed_at,
                    source_type, source_ref, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fid,
                content,
                b"\x00" * 4,
                "stub",
                now,
                "conversation",
                "sess",
                "[]",
            ),
        )
