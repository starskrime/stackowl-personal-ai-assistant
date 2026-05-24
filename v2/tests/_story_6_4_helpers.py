"""Shared fixtures and stubs for Story 6.4 tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.bridge import HealthReport
from stackowl.memory.lancedb_adapter import SearchResult
from stackowl.pipeline.state import PipelineState


@pytest.fixture(autouse=True)
def no_test_mode_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable :class:`TestModeGuard` for all Story 6.4 tests."""
    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        lambda *a, **kw: None,
    )


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncGenerator[DbPool, None]:
    """Per-test fresh DbPool with all migrations applied."""
    db_path = tmp_path / "story64.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


class StubEmbeddingProvider:
    """Deterministic embedder for tests."""

    def __init__(self, dim: int = 4, name: str = "stub-embed") -> None:
        self._dim = dim
        self._name = name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(i + 1) / self._dim for i in range(self._dim)] for _ in texts]

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def is_local(self) -> bool:
        return True


class StubEmbeddingRegistry:
    """Registry stub exposing ``.get()`` like the real one."""

    def __init__(self, provider: StubEmbeddingProvider) -> None:
        self._provider = provider

    def get(self) -> StubEmbeddingProvider:
        return self._provider


class FakeLanceDB:
    """In-memory stand-in for :class:`LanceDBAdapter`."""

    def __init__(
        self,
        search_results: list[SearchResult] | None = None,
        raise_on_search: Exception | None = None,
    ) -> None:
        self.upserts: list[tuple[str, list[float], dict[str, Any]]] = []
        self.deletes: list[str] = []
        self.searches: list[tuple[list[float], int]] = []
        self._search_results = search_results or []
        self._raise_on_search = raise_on_search

    async def upsert(
        self, fact_id: str, embedding: list[float], metadata: dict[str, Any]
    ) -> None:
        self.upserts.append((fact_id, embedding, metadata))

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filter_expr: str | None = None,
    ) -> list[SearchResult]:
        self.searches.append((list(query_embedding), limit))
        if self._raise_on_search is not None:
            raise self._raise_on_search
        return self._search_results

    async def delete(self, fact_id: str) -> None:
        self.deletes.append(fact_id)

    async def health(self) -> HealthReport:
        return HealthReport(
            name="memory.lancedb", status="ok", details={}, latency_ms=0.0
        )

    async def reindex(
        self, records: list[tuple[str, list[float], dict[str, Any]]]
    ) -> int:
        for fid, emb, md in records:
            self.upserts.append((fid, emb, md))
        return len(records)


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
        session_id="s",
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
