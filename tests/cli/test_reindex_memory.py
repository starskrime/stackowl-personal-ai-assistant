"""`stackowl db reindex-memory` — re-embed the lessons corpus with the active model.

ESC-5. The command used to rebuild a LanceDB vector table from ``committed_facts``,
and both ends of that were dead: the table has held 0 rows since migration 0112, and
LanceDB itself was removed in D08.2. Bakir chose to REPOINT the command rather than
delete it, because the need it serves is real and now lives in the lessons corpus:
after an embedding-model change, vectors written by the old model cannot be compared
with queries embedded by the new one, and the store degrades honestly rather than
curing itself.

These tests verify the WIRING — that the command reaches the store, forwards the
active model, and reports what happened — with the heavy dependencies (a real
embedding model, the DB pool) mocked so nothing touches the real ~/.stackowl home.
The re-embedding behaviour itself is covered against a real database in
tests/learning/test_lessons_sqlite_store.py::TestReembed.
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


class _FakeStore:
    """Records what the command asked for. Constructed by the command itself."""

    last: dict[str, Any] = {}
    written = 0

    def __init__(self, db: Any, *, embedding_model: str = "") -> None:
        _FakeStore.last["embedding_model_at_construction"] = embedding_model

    async def reembed_all(self, embed: Any, *, model: str, **kw: Any) -> int:
        _FakeStore.last["model"] = model
        # Prove the command handed over a WORKING embed callable rather than
        # something that merely satisfies the signature — a reindex that cannot
        # embed would report success and rewrite nothing.
        _FakeStore.last["embedded"] = await embed(["a lesson"])
        return _FakeStore.written


def _patches():  # type: ignore[no-untyped-def]
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
        patch("stackowl.learning.lessons_store.SqliteLessonsStore", _FakeStore),
    ]


def _run() -> Any:
    ctx = _patches()
    for p in ctx:
        p.start()
    try:
        return runner.invoke(app, ["db", "reindex-memory"])
    finally:
        for p in reversed(ctx):
            p.stop()


def test_it_reembeds_with_the_active_model_and_reports_the_count() -> None:
    _FakeStore.last = {}
    _FakeStore.written = 3

    result = _run()

    assert result.exit_code == 0, result.output
    assert _FakeStore.last["model"] == "all-MiniLM-L6-v2", (
        "the ACTIVE model must be stamped — reindexing with the old one would "
        "leave the corpus exactly as incomparable as it was"
    )
    assert _FakeStore.last["embedded"] == [[0.1] * 384]
    assert "Re-embedded 3 lesson" in result.output
    assert "all-MiniLM-L6-v2" in result.output


def test_an_empty_corpus_is_reported_not_treated_as_success() -> None:
    """0 written is a fact about the corpus, not a failure — but it must SAY so
    rather than print a tick, or an operator cannot tell a no-op from a repair."""
    _FakeStore.last = {}
    _FakeStore.written = 0

    result = _run()

    assert result.exit_code == 0, result.output
    assert "No lessons to reindex" in result.output


def test_a_failure_exits_nonzero_rather_than_claiming_success() -> None:
    """A reindex that silently failed would leave the corpus unreachable while
    telling the operator it was fixed."""

    async def _boom(self: Any, embed: Any, *, model: str, **kw: Any) -> int:
        raise RuntimeError("embedder unavailable")

    ctx = _patches()
    for p in ctx:
        p.start()
    try:
        with patch.object(_FakeStore, "reembed_all", _boom):
            result = runner.invoke(app, ["db", "reindex-memory"])
    finally:
        for p in reversed(ctx):
            p.stop()

    assert result.exit_code == 1, result.output
    assert "failed" in result.output.lower()
