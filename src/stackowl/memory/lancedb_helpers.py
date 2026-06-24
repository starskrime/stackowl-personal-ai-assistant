"""Sync helpers for :class:`LanceDBAdapter` — schema, row mapping, executor bodies.

:class:`SearchResult` lives in this module (not the adapter) so the helpers
package has no circular dependency on ``lancedb_adapter`` at runtime.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pyarrow as pa
from pydantic import BaseModel, ConfigDict

from stackowl.exceptions import StackOwlError
from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from lancedb import DBConnection  # type: ignore[import-untyped]
    from lancedb.table import Table  # type: ignore[import-untyped]


class EmbeddingDimensionMismatch(StackOwlError):
    """Raised when an upsert's vector dim/model disagrees with the corpus identity.

    A SIGNAL (caught loudly above the promoter's generic B5 swallow), NOT the
    doomed Arrow length error that a mixed-dim ``merge_insert`` would otherwise
    raise and the promoter would silently absorb. Carries the stored vs active
    ``(model, dim)`` for operator-visible logging.
    """


class SearchResult(BaseModel):
    """A single ANN hit returned by :meth:`LanceDBAdapter.search`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    score: float
    metadata: dict[str, Any]


TABLE_NAME = "committed_facts"
# One-row sidecar recording the corpus identity (embedding model + dim) the
# vectors in TABLE_NAME were written under. Absent ⇒ legacy/untagged corpus ⇒
# treated as a MISMATCH by readers (never default-to-active).
META_TABLE_NAME = "committed_facts_meta"


def _table_names(conn: DBConnection) -> list[str]:
    """Return the list of table names in ``conn``.

    ``DBConnection.list_tables()`` returns a ``ListTablesResponse`` object,
    not a plain list — naive ``in`` checks silently report ``False``. This
    helper normalises to a string list.
    """
    response = conn.list_tables()
    names = getattr(response, "tables", None)
    if names is None:
        # Fallback: try iterating directly (older versions)
        try:
            return list(response)
        except TypeError as exc:
            log.memory.warning(
                "[memory] lancedb_helpers._table_names: response not iterable",
                exc_info=exc,
            )
            return []
    return list(names)


def make_schema(dim: int) -> pa.Schema:
    """Build the PyArrow schema for a given embedding dimension."""
    return pa.schema(
        [
            pa.field("fact_id", pa.string()),
            pa.field("embedding", pa.list_(pa.float32(), dim)),
            pa.field("metadata", pa.string()),  # JSON-encoded
            # F062/F063: queryable corpus model per row (defense-in-depth filter +
            # ANN model-scoping). Sourced from the metadata dict, never inferred.
            pa.field("embedding_model", pa.string()),
        ]
    )


def _schema_matches(table: Table, target: pa.Schema) -> bool:
    """Return ``True`` when ``table``'s schema matches ``target`` field-for-field.

    Used by :func:`sync_reindex` (a full rebuild from the SQLite SoT) to decide
    whether the existing on-disk table must be dropped + recreated. A mismatch
    means either a schema-evolved corpus (e.g. a legacy/untagged table created
    before the F062 ``embedding_model`` column existed — the keystone live break)
    or a dim swap (the pinned ``embedding`` fixed-size-list dim differs). In both
    cases appending would raise an Arrow ``_cast_to_target_schema`` error.
    """
    existing_names = sorted(f.name for f in table.schema)
    target_names = sorted(f.name for f in target)
    if existing_names != target_names:
        return False
    # The embedding dim is pinned at create time; a dim swap must also recreate.
    return bool(table.schema.field("embedding").type == target.field("embedding").type)


def make_row(
    fact_id: str, embedding: list[float], metadata: dict[str, Any]
) -> dict[str, Any]:
    """Encode one fact as a row dict suitable for ``Table.add`` / ``merge_insert``."""
    return {
        "fact_id": fact_id,
        "embedding": list(embedding),
        "metadata": json.dumps(metadata, default=str),
        # Empty string (never None) so the column stays non-null + filterable.
        "embedding_model": str(metadata.get("embedding_model") or ""),
    }


def read_corpus_identity(conn: DBConnection) -> tuple[str | None, int | None]:
    """Return the stored corpus ``(model, dim)``, or ``(None, None)`` when absent.

    Absent sidecar = a legacy/untagged corpus (or a brand-new dir). Readers MUST
    treat ``None`` as a MISMATCH and degrade (FTS + reindex) — never as a match.
    """
    if META_TABLE_NAME not in _table_names(conn):
        return (None, None)
    try:
        rows = conn.open_table(META_TABLE_NAME).search().limit(1).to_list()
    except Exception as exc:  # B5 — a corrupt sidecar must not crash recall
        log.memory.warning(
            "[memory] lancedb_helpers.read_corpus_identity: read failed",
            exc_info=exc,
        )
        return (None, None)
    if not rows:
        return (None, None)
    row = rows[0]
    model = row.get("model")
    dim = row.get("dim")
    return (
        str(model) if model is not None else None,
        int(dim) if dim is not None else None,
    )


def write_corpus_identity(conn: DBConnection, model: str, dim: int) -> None:
    """Persist the corpus ``(model, dim)`` as the single sidecar row.

    Idempotent: replaces any prior row so the sidecar always reflects exactly
    one identity (the corpus the current vectors were written under).
    """
    log.memory.info(
        "[memory] lancedb_helpers.write_corpus_identity: writing",
        extra={"_fields": {"model": model, "dim": dim}},
    )
    schema = pa.schema([pa.field("model", pa.string()), pa.field("dim", pa.int64())])
    row = {"model": str(model), "dim": int(dim)}
    if META_TABLE_NAME in _table_names(conn):
        # Single-row table: clear then insert (replace) so identity never duplicates.
        table = conn.open_table(META_TABLE_NAME)
        table.delete("true")
        table.add([row])
    else:
        conn.create_table(META_TABLE_NAME, data=[row], schema=schema, exist_ok=True)


def get_or_create_table(conn: DBConnection, dim: int) -> Table:
    """Open the committed_facts table, creating it on first call.

    Uses ``exist_ok=True`` so concurrent callers don't race on creation —
    LanceDB caches table listings per connection and a second caller may
    not see a freshly-created table yet.
    """
    if TABLE_NAME not in _table_names(conn):
        log.memory.info(
            "[memory] lancedb_helpers.get_or_create_table: creating table",
            extra={"_fields": {"table": TABLE_NAME, "dim": dim}},
        )
        conn.create_table(TABLE_NAME, schema=make_schema(dim), exist_ok=True)
    return conn.open_table(TABLE_NAME)


def sync_recreate_table(conn: DBConnection, dim: int) -> Table:
    """Drop and recreate the committed_facts table at ``dim`` (F066 reindex).

    The Arrow vector dim is pinned at create time and cannot be altered in
    place, so a model/dim swap requires dropping the table. Callers MUST build
    the new table from the SQLite source of truth BEFORE relying on it (the
    reindex re-adds every committed vector). Returns the fresh empty table.
    """
    log.memory.info(
        "[memory] lancedb_helpers.sync_recreate_table: recreating at new dim",
        extra={"_fields": {"table": TABLE_NAME, "dim": dim}},
    )
    if TABLE_NAME in _table_names(conn):
        conn.drop_table(TABLE_NAME)
    conn.create_table(TABLE_NAME, schema=make_schema(dim), exist_ok=True)
    return conn.open_table(TABLE_NAME)


def sync_upsert(
    conn: DBConnection,
    fact_id: str,
    embedding: list[float],
    metadata: dict[str, Any],
) -> None:
    """Insert-or-update one row keyed by ``fact_id``.

    F066 — before writing, compare the vector dim against the stored corpus
    identity. A different dim would otherwise be rejected by Arrow and the
    throw swallowed by the promoter's B5 (silent FTS-only degrade). Raise the
    typed :class:`EmbeddingDimensionMismatch` SIGNAL instead so the adapter can
    handle it loudly and defer the fact to the reindex phase.
    """
    stored_model, stored_dim = read_corpus_identity(conn)
    if stored_dim is not None and stored_dim != len(embedding):
        active_model = str(metadata.get("embedding_model") or "")
        log.memory.warning(
            "[memory] lancedb_helpers.sync_upsert: embedding dim swap detected",
            extra={
                "_fields": {
                    "fact_id": fact_id,
                    "stored_dim": stored_dim,
                    "stored_model": stored_model,
                    "active_dim": len(embedding),
                    "active_model": active_model,
                }
            },
        )
        raise EmbeddingDimensionMismatch(
            f"vector dim {len(embedding)} (model {active_model!r}) does not match "
            f"corpus dim {stored_dim} (model {stored_model!r})"
        )
    table = get_or_create_table(conn, len(embedding))
    row = make_row(fact_id, embedding, metadata)
    (
        table.merge_insert("fact_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute([row])
    )


def sync_reindex(
    conn: DBConnection,
    records: list[tuple[str, list[float], dict[str, Any]]],
    target_dim: int | None = None,
) -> None:
    """Batch-upsert all ``records`` in one merge_insert call.

    F066 — when ``target_dim`` is given and differs from the existing corpus
    dim, the table is dropped + recreated at ``target_dim`` first (a model/dim
    swap rebuild). Callers pass the SQLite-sourced re-embedded records so the
    new table is fully populated. When ``target_dim`` is None the legacy
    same-dim behaviour is preserved (first record's dim).
    """
    first_dim = len(records[0][1])
    if target_dim is not None and target_dim != first_dim:
        # Defensive: every record must match the declared target dim.
        raise EmbeddingDimensionMismatch(
            f"reindex target_dim={target_dim} but first record dim={first_dim}"
        )
    effective_dim = target_dim if target_dim is not None else first_dim
    target_schema = make_schema(effective_dim)
    if TABLE_NAME in _table_names(conn) and not _schema_matches(
        conn.open_table(TABLE_NAME), target_schema
    ):
        # The existing table predates the current schema OR is a dim swap. Both
        # callers of reindex pass the FULL committed-fact set re-built from the
        # SQLite source of truth, so dropping + recreating at the current schema
        # and refilling is lossless — and is the only way to add the missing
        # embedding_model column (Arrow cannot evolve a pinned schema in place).
        log.memory.info(
            "[memory] lancedb_helpers.sync_reindex: existing table schema stale/"
            "mismatched — recreating from SoT",
            extra={"_fields": {"effective_dim": effective_dim}},
        )
        table = sync_recreate_table(conn, effective_dim)
    else:
        table = get_or_create_table(conn, effective_dim)
    rows = [make_row(fid, emb, md) for fid, emb, md in records]
    (
        table.merge_insert("fact_id")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute(rows)
    )


def sync_delete(conn: DBConnection, fact_id: str) -> None:
    """Delete one row by ``fact_id``; no-op when the table is absent."""
    if TABLE_NAME not in _table_names(conn):
        return
    table = conn.open_table(TABLE_NAME)
    escaped = fact_id.replace("'", "''")
    table.delete(f"fact_id = '{escaped}'")


def sync_search(
    conn: DBConnection,
    query_embedding: list[float],
    limit: int,
    filter_expr: str | None,
) -> list[SearchResult]:
    """Run an ANN search; returns ``[]`` when the table does not yet exist."""
    if TABLE_NAME not in _table_names(conn):
        return []
    table = conn.open_table(TABLE_NAME)
    query = table.search(query_embedding).limit(limit)
    if filter_expr:
        query = query.where(filter_expr)
    rows = query.to_list()
    results: list[SearchResult] = []
    for raw in rows:
        try:
            metadata = json.loads(raw.get("metadata") or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except json.JSONDecodeError as exc:
            log.memory.warning(
                "[memory] lancedb_helpers.sync_search: bad metadata JSON",
                exc_info=exc,
                extra={"_fields": {"fact_id": raw.get("fact_id")}},
            )
            metadata = {}
        distance = float(raw.get("_distance", 0.0))
        score = 1.0 / (1.0 + distance)
        results.append(
            SearchResult(fact_id=raw["fact_id"], score=score, metadata=metadata)
        )
    return results


def sync_list_tables(conn: DBConnection) -> list[str]:
    """List all tables in the LanceDB connection."""
    return list(_table_names(conn))
