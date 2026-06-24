"""`stackowl db reindex-memory` — manual semantic-corpus rebuild from the SQLite
SoT (immediate recovery after the 2026-06-23 LanceDB schema break instead of
waiting for the nightly dream-worker).

The command is thin glue over the existing ``reembed_committed_facts`` machinery;
these tests verify the wiring (active model/dim forwarded, written count reported)
with the heavy deps (real embedding model, on-disk LanceDB, DB pool) mocked out so
nothing touches the real ~/.stackowl home.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from stackowl.cli.app import app

runner = CliRunner()


class _FakeDbPool:
    def __init__(self, *a: Any, **k: Any) -> None: ...
    async def open(self) -> None: ...
    async def close(self) -> None: ...


class _FakeEmbedProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


class _FakeRegistry:
    active_model = "all-MiniLM-L6-v2"
    active_dim = 384

    def get(self) -> _FakeEmbedProvider:
        return _FakeEmbedProvider()

    @classmethod
    async def create(cls, *a: Any, **k: Any) -> "_FakeRegistry":
        return cls()


class _FakeLanceDB:
    def __init__(self, *a: Any, **k: Any) -> None: ...

    async def corpus_identity(self) -> tuple[str, int]:
        return ("all-MiniLM-L6-v2", 384)


def _patches(reembed: Any):  # type: ignore[no-untyped-def]
    return [
        patch(
            "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
            lambda *a, **k: None,
        ),
        patch("stackowl.db.pool.DbPool", _FakeDbPool),
        patch(
            "stackowl.embeddings.registry.EmbeddingRegistry.create",
            _FakeRegistry.create,
        ),
        patch("stackowl.memory.lancedb_adapter.LanceDBAdapter", _FakeLanceDB),
        patch(
            "stackowl.memory.dream_worker_helpers.reembed_committed_facts", reembed
        ),
    ]


def test_reindex_memory_reports_written_count_and_identity() -> None:
    seen: dict[str, Any] = {}

    async def _fake_reembed(db: Any, lancedb: Any, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 3

    ctx: list[Any] = _patches(_fake_reembed)
    for p in ctx:
        p.start()
    try:
        result = runner.invoke(app, ["db", "reindex-memory"])
    finally:
        for p in reversed(ctx):
            p.stop()

    assert result.exit_code == 0, result.output
    # The active model/dim were forwarded into the existing reembed machinery.
    assert seen["active_model"] == "all-MiniLM-L6-v2"
    assert seen["active_dim"] == 384
    assert "Reindexed 3 fact" in result.output
    assert "all-MiniLM-L6-v2" in result.output


def test_reindex_memory_handles_empty_corpus() -> None:
    async def _fake_reembed(db: Any, lancedb: Any, **kwargs: Any) -> int:
        return 0

    ctx: list[Any] = _patches(_fake_reembed)
    for p in ctx:
        p.start()
    try:
        result = runner.invoke(app, ["db", "reindex-memory"])
    finally:
        for p in reversed(ctx):
            p.stop()

    assert result.exit_code == 0, result.output
    assert "No committed facts to reindex" in result.output
