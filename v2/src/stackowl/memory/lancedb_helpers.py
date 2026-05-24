"""Sync helpers for :class:`LanceDBAdapter` — schema, row mapping, executor bodies.

:class:`SearchResult` lives in this module (not the adapter) so the helpers
package has no circular dependency on ``lancedb_adapter`` at runtime.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pyarrow as pa
from pydantic import BaseModel, ConfigDict

from stackowl.infra.observability import log

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    from lancedb import DBConnection  # type: ignore[import-untyped]
    from lancedb.table import Table  # type: ignore[import-untyped]


class SearchResult(BaseModel):
    """A single ANN hit returned by :meth:`LanceDBAdapter.search`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact_id: str
    score: float
    metadata: dict[str, Any]


TABLE_NAME = "committed_facts"


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
        ]
    )


def make_row(
    fact_id: str, embedding: list[float], metadata: dict[str, Any]
) -> dict[str, Any]:
    """Encode one fact as a row dict suitable for ``Table.add`` / ``merge_insert``."""
    return {
        "fact_id": fact_id,
        "embedding": list(embedding),
        "metadata": json.dumps(metadata, default=str),
    }


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


def sync_upsert(
    conn: DBConnection,
    fact_id: str,
    embedding: list[float],
    metadata: dict[str, Any],
) -> None:
    """Insert-or-update one row keyed by ``fact_id``."""
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
) -> None:
    """Batch-upsert all ``records`` in one merge_insert call."""
    first_dim = len(records[0][1])
    table = get_or_create_table(conn, first_dim)
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
