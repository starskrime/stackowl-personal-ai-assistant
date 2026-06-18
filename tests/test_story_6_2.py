"""Epic 6 Story 6.2 — SQLite Memory Store & MemoryBridge tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from pydantic import ValidationError

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.exceptions import DuplicateFactError
from stackowl.memory.bridge import HealthReport, NullMemoryBridge
from stackowl.memory.models import MemoryRecord, StagedFact
from stackowl.memory.sqlite_bridge import SqliteMemoryBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def memory_db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """A DbPool with all migrations applied (including 0014_memory_tables)."""
    db_path = tmp_path / "memory_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
def bridge(memory_db: DbPool) -> SqliteMemoryBridge:
    return SqliteMemoryBridge(memory_db)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_staged_fact_model_frozen() -> None:
    fact = StagedFact(content="x", source_type="manual", source_ref="s", confidence=0.5)
    with pytest.raises(ValidationError):
        fact.content = "y"  # type: ignore[misc]


def test_memory_record_model_frozen() -> None:
    record = MemoryRecord(
        fact_id="f1",
        content="hi",
        embedding=[0.1],
        embedding_model="m",
        committed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        source_type="manual",
        source_ref="r",
    )
    with pytest.raises(ValidationError):
        record.content = "no"  # type: ignore[misc]


def test_health_report_dataclass_frozen() -> None:
    report = HealthReport(name="x", status="ok", details={}, latency_ms=1.0)
    with pytest.raises(Exception):  # FrozenInstanceError is a dataclasses subclass
        report.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NullMemoryBridge tests
# ---------------------------------------------------------------------------


async def test_null_bridge_retrieve_returns_empty() -> None:
    null = NullMemoryBridge()
    assert await null.retrieve("anything", "session-1") == ""


async def test_null_bridge_stage_noop() -> None:
    null = NullMemoryBridge()
    fact = StagedFact(content="c", source_type="manual", source_ref="r", confidence=0.5)
    # Must not raise
    await null.stage(fact)


async def test_null_bridge_recall_returns_empty_list() -> None:
    null = NullMemoryBridge()
    assert await null.recall("q") == []


async def test_null_bridge_list_staged_returns_empty() -> None:
    null = NullMemoryBridge()
    assert await null.list_staged() == []
    assert await null.list_staged(status="rejected") == []


# ---------------------------------------------------------------------------
# SqliteMemoryBridge tests
# ---------------------------------------------------------------------------


async def test_sqlite_bridge_stage_inserts_row(
    bridge: SqliteMemoryBridge, memory_db: DbPool
) -> None:
    fact = StagedFact(
        content="boss prefers root-cause fixes",
        source_type="conversation",
        source_ref="sess-1",
        confidence=0.7,
    )
    await bridge.stage(fact)
    rows = await memory_db.fetch_all("SELECT fact_id, content FROM staged_facts")
    assert len(rows) == 1
    assert rows[0]["fact_id"] == fact.fact_id
    assert rows[0]["content"] == "boss prefers root-cause fixes"


async def test_sqlite_bridge_stage_with_embedding(
    bridge: SqliteMemoryBridge, memory_db: DbPool
) -> None:
    embedding = [0.1, 0.25, -0.5, 1.0]
    fact = StagedFact(
        content="vector test",
        source_type="manual",
        source_ref="r",
        confidence=0.9,
        embedding=embedding,
        embedding_model="all-MiniLM-L6-v2",
    )
    await bridge.stage(fact)
    listed = await bridge.list_staged()
    assert len(listed) == 1
    round_tripped = listed[0].embedding
    assert round_tripped is not None
    # Float32 round-trip — allow tiny precision loss
    assert len(round_tripped) == len(embedding)
    for a, b in zip(round_tripped, embedding, strict=True):
        assert abs(a - b) < 1e-6
    assert listed[0].embedding_model == "all-MiniLM-L6-v2"


async def test_sqlite_bridge_duplicate_raises(bridge: SqliteMemoryBridge) -> None:
    fact = StagedFact(
        fact_id="dup-1",
        content="first",
        source_type="manual",
        source_ref="r",
        confidence=0.5,
    )
    await bridge.stage(fact)
    dup = StagedFact(
        fact_id="dup-1",
        content="second",
        source_type="manual",
        source_ref="r",
        confidence=0.5,
    )
    with pytest.raises(DuplicateFactError) as exc_info:
        await bridge.stage(dup)
    assert exc_info.value.fact_id == "dup-1"


async def test_sqlite_bridge_list_staged_by_status(bridge: SqliteMemoryBridge) -> None:
    for i in range(3):
        await bridge.stage(
            StagedFact(
                content=f"fact-{i}",
                source_type="manual",
                source_ref=f"r{i}",
                confidence=0.5,
            )
        )
    staged = await bridge.list_staged(status="staged")
    assert len(staged) == 3
    rejected = await bridge.list_staged(status="rejected")
    assert rejected == []


async def test_sqlite_bridge_delete_removes_fact(bridge: SqliteMemoryBridge) -> None:
    fact = StagedFact(
        content="to-delete",
        source_type="manual",
        source_ref="r",
        confidence=0.5,
    )
    await bridge.stage(fact)
    assert len(await bridge.list_staged()) == 1
    await bridge.delete(fact.fact_id)
    assert await bridge.list_staged() == []


async def test_sqlite_bridge_delete_is_idempotent(bridge: SqliteMemoryBridge) -> None:
    # Deleting a non-existent fact must not raise.
    await bridge.delete("never-existed")


async def test_sqlite_bridge_recall_fts5(
    bridge: SqliteMemoryBridge, memory_db: DbPool
) -> None:
    # Insert directly into committed_facts (promote flow not implemented yet)
    await memory_db.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, committed_at,
                source_type, source_ref, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "f1",
            "the sky is blue today",
            b"\x00" * 4,
            "test",
            "2024-01-01T00:00:00+00:00",
            "manual",
            "test",
            "[]",
        ),
    )
    # Use the same rowid for FTS sync
    rows = await memory_db.fetch_all("SELECT rowid AS rid FROM committed_facts WHERE fact_id = 'f1'")
    rid = rows[0]["rid"]
    await memory_db.execute(
        "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)",
        (rid, "the sky is blue today"),
    )
    results = await bridge.recall("sky blue")
    assert len(results) == 1
    assert results[0].fact_id == "f1"
    assert "sky" in results[0].content


async def test_sqlite_bridge_recall_empty_query_returns_empty(bridge: SqliteMemoryBridge) -> None:
    # No data + arbitrary query returns []
    assert await bridge.recall("nothing") == []


async def test_sqlite_bridge_retrieve_formats_context(
    bridge: SqliteMemoryBridge, memory_db: DbPool
) -> None:
    await memory_db.execute(
        """INSERT INTO committed_facts
               (fact_id, content, embedding, embedding_model, committed_at,
                source_type, source_ref, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "f2",
            "boss likes verification before completion",
            b"\x00" * 4,
            "test",
            "2024-01-02T00:00:00+00:00",
            "manual",
            "test",
            "[]",
        ),
    )
    rows = await memory_db.fetch_all("SELECT rowid AS rid FROM committed_facts WHERE fact_id = 'f2'")
    rid = rows[0]["rid"]
    await memory_db.execute(
        "INSERT INTO committed_facts_fts(rowid, content) VALUES (?, ?)",
        (rid, "boss likes verification before completion"),
    )
    out = await bridge.retrieve("verification", session_id="sess-x")
    # Task 10: trust-aware renderer. This fact carries no explicit trust column,
    # so it defaults to 'untrusted' (fail-safe) and renders in the FENCED
    # External-reference region — never as a bare "Prior context:" bullet.
    assert "## External reference data" in out
    assert '<memory_reference trust="untrusted" source="manual">' in out
    assert "boss likes verification" in out


async def test_sqlite_bridge_retrieve_empty_when_no_matches(bridge: SqliteMemoryBridge) -> None:
    out = await bridge.retrieve("absolutely nothing here", session_id="s")
    assert out == ""


async def test_sqlite_bridge_store_creates_staged_fact(
    bridge: SqliteMemoryBridge,
) -> None:
    await bridge.store("a thing the user said", session_id="sess-store")
    staged = await bridge.list_staged()
    assert len(staged) == 1
    assert staged[0].source_type == "conversation"
    assert staged[0].source_ref == "sess-store"
    assert staged[0].content == "a thing the user said"


async def test_sqlite_bridge_health_ok(bridge: SqliteMemoryBridge) -> None:
    report = await bridge.health()
    assert report.status == "ok"
    assert report.name == "memory.sqlite"


async def test_sqlite_bridge_health_has_counts(bridge: SqliteMemoryBridge) -> None:
    await bridge.stage(
        StagedFact(
            content="x",
            source_type="manual",
            source_ref="r",
            confidence=0.5,
        )
    )
    report = await bridge.health()
    assert "staged_count" in report.details
    assert "committed_count" in report.details
    assert report.details["staged_count"] == 1
    assert report.details["committed_count"] == 0
