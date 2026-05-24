"""Sync helpers for :class:`KuzuAdapter` — schema DDL, query execution, BFS.

Kuzu's Python API is synchronous; these helpers run inside
``loop.run_in_executor(None, ...)`` calls. They never log directly — the
adapter wraps them with 4-point logging and ``TestModeGuard`` checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — typing-only imports
    import kuzu


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE NODE TABLE IF NOT EXISTS Entity (
        id STRING,
        name STRING,
        entity_type STRING,
        source_fact_id STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Fact (
        id STRING,
        content STRING,
        confidence FLOAT,
        PRIMARY KEY (id)
    )""",
    """CREATE REL TABLE IF NOT EXISTS MENTIONS (
        FROM Fact TO Entity,
        mention_type STRING
    )""",
    """CREATE REL TABLE IF NOT EXISTS RELATED_TO (
        FROM Entity TO Entity,
        relation STRING,
        strength FLOAT
    )""",
)


def sync_create_schema(conn: kuzu.Connection) -> None:
    """Run every DDL statement; ``IF NOT EXISTS`` keeps re-runs cheap."""
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)


def _rows_from_result(result: Any) -> list[dict[str, Any]]:
    """Drain a Kuzu QueryResult into a list of dicts keyed by column name."""
    rows: list[dict[str, Any]] = []
    column_names = list(result.get_column_names())
    while result.has_next():
        values = result.get_next()
        rows.append(dict(zip(column_names, values, strict=False)))
    return rows


def sync_upsert_entity(
    conn: kuzu.Connection,
    entity_id: str,
    name: str,
    entity_type: str,
    source_fact_id: str,
) -> None:
    """Delete-then-insert by id (Kuzu < 1.0 has no MERGE)."""
    conn.execute(
        "MATCH (e:Entity {id: $id}) DELETE e",
        {"id": entity_id},
    )
    conn.execute(
        """CREATE (:Entity {
            id: $id,
            name: $name,
            entity_type: $entity_type,
            source_fact_id: $source_fact_id
        })""",
        {
            "id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "source_fact_id": source_fact_id,
        },
    )


def sync_upsert_fact(
    conn: kuzu.Connection,
    fact_id: str,
    content: str,
    confidence: float,
) -> None:
    """Delete-then-insert a Fact node keyed by id."""
    conn.execute(
        "MATCH (f:Fact {id: $id}) DELETE f",
        {"id": fact_id},
    )
    conn.execute(
        """CREATE (:Fact {
            id: $id,
            content: $content,
            confidence: $confidence
        })""",
        {
            "id": fact_id,
            "content": content,
            "confidence": float(confidence),
        },
    )


def sync_link_fact_to_entity(
    conn: kuzu.Connection,
    fact_id: str,
    entity_id: str,
    mention_type: str,
) -> None:
    """Add a MENTIONS edge from Fact -> Entity if both nodes exist."""
    conn.execute(
        """MATCH (f:Fact {id: $fid}), (e:Entity {id: $eid})
           CREATE (f)-[:MENTIONS {mention_type: $mt}]->(e)""",
        {"fid": fact_id, "eid": entity_id, "mt": mention_type},
    )


def sync_link_entities(
    conn: kuzu.Connection,
    from_id: str,
    to_id: str,
    relation: str,
    strength: float,
) -> None:
    """Add a RELATED_TO edge between two Entity nodes."""
    conn.execute(
        """MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id})
           CREATE (a)-[:RELATED_TO {relation: $rel, strength: $st}]->(b)""",
        {
            "from_id": from_id,
            "to_id": to_id,
            "rel": relation,
            "st": float(strength),
        },
    )


def sync_traverse(
    conn: kuzu.Connection,
    entity_id: str,
    max_hops: int,
) -> list[dict[str, Any]]:
    """Variable-length traversal from ``entity_id`` up to ``max_hops`` edges.

    Returns one dict per reachable Entity. Kuzu's variable-length syntax is
    ``*1..N`` (inclusive). Self is excluded.
    """
    if max_hops < 1:
        return []
    cypher = (
        f"MATCH (start:Entity {{id: $id}})-[:RELATED_TO*1..{max_hops}]->(other:Entity) "
        "RETURN DISTINCT other.id AS id, other.name AS name, "
        "other.entity_type AS entity_type, other.source_fact_id AS source_fact_id"
    )
    result = conn.execute(cypher, {"id": entity_id})
    return _rows_from_result(result)


def sync_probe(conn: kuzu.Connection) -> int:
    """Cheap connectivity probe — returns the number of Entity rows."""
    result = conn.execute("MATCH (e:Entity) RETURN count(e) AS c")
    rows = _rows_from_result(result)
    if not rows:
        return 0
    raw = rows[0].get("c", 0)
    return int(raw) if raw is not None else 0
