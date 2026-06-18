"""T0 — Shared corpus-identity primitive (C3 / F062+F066 foundation).

Identity = ``(model_name, dim)`` written to a one-row LanceDB sidecar table
``committed_facts_meta`` and read back. Absent sidecar (legacy corpus) ⇒
``(None, None)`` so a read-side gate treats it as a MISMATCH (never
default-to-active). The ``embedding_model`` column is added to the committed
schema (defense-in-depth + F063 ANN model-scoping). The registry exposes
``active_model``/``active_dim`` as the single identity authority.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.embeddings.registry import EmbeddingRegistry
from stackowl.memory.lancedb_helpers import (
    EmbeddingDimensionMismatch,
    make_row,
    make_schema,
    read_corpus_identity,
    write_corpus_identity,
)


def _connect(data_dir: Path):  # type: ignore[no-untyped-def]
    import lancedb

    data_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(data_dir))


def test_read_absent_identity_is_none_none(tmp_path: Path) -> None:
    """A legacy corpus with no sidecar reads as ``(None, None)`` (= mismatch)."""
    conn = _connect(tmp_path / "lance")
    assert read_corpus_identity(conn) == (None, None)


def test_write_then_read_round_trips_model_and_dim(tmp_path: Path) -> None:
    """Writing identity then reading it returns the exact ``(model, dim)``."""
    conn = _connect(tmp_path / "lance")
    write_corpus_identity(conn, "all-MiniLM-L6-v2", 384)
    assert read_corpus_identity(conn) == ("all-MiniLM-L6-v2", 384)


def test_write_is_idempotent_single_row(tmp_path: Path) -> None:
    """Re-writing replaces the single row (never accumulates stale identities)."""
    conn = _connect(tmp_path / "lance")
    write_corpus_identity(conn, "hash-v1-384d", 384)
    write_corpus_identity(conn, "all-MiniLM-L6-v2", 384)
    assert read_corpus_identity(conn) == ("all-MiniLM-L6-v2", 384)


def test_schema_has_embedding_model_column() -> None:
    """``make_schema`` carries an ``embedding_model`` string column."""
    schema = make_schema(384)
    assert "embedding_model" in schema.names


def test_make_row_sets_embedding_model_from_metadata() -> None:
    """``make_row`` reads ``embedding_model`` out of the metadata dict it's given."""
    row = make_row("f1", [0.0] * 4, {"embedding_model": "all-MiniLM-L6-v2"})
    assert row["embedding_model"] == "all-MiniLM-L6-v2"


def test_make_row_empty_embedding_model_when_absent() -> None:
    """Missing embedding_model defaults to empty string (never None in the column)."""
    row = make_row("f1", [0.0] * 4, {})
    assert row["embedding_model"] == ""


@pytest.mark.asyncio
async def test_registry_active_identity_hash_fallback() -> None:
    """A bare registry (.get() lazily yields hash) reports the hash identity."""
    registry = EmbeddingRegistry()
    assert registry.active_model == "hash-v1-384d"
    assert registry.active_dim == 384


def test_dimension_mismatch_is_stackowl_error() -> None:
    """The typed signal is a StackOwlError so the promoter B5 can target it."""
    from stackowl.exceptions import StackOwlError

    assert issubclass(EmbeddingDimensionMismatch, StackOwlError)
