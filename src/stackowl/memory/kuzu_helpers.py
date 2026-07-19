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
    """CREATE NODE TABLE IF NOT EXISTS Owl (
        name STRING,
        PRIMARY KEY (name)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Skill (
        id STRING,
        owner_id STRING,
        name STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE IF NOT EXISTS Trait (
        id STRING,
        owl_name STRING,
        trait_name STRING,
        value FLOAT,
        PRIMARY KEY (id)
    )""",
    """CREATE REL TABLE IF NOT EXISTS OWNS (
        FROM Owl TO Skill
    )""",
    """CREATE REL TABLE IF NOT EXISTS HAS_TRAIT (
        FROM Owl TO Trait
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
    """Upsert an Entity by id via MERGE + SET.

    ``MERGE`` matches the node if it already exists (keyed on the ``id`` PK) or
    creates it otherwise; ``SET`` then updates its properties in place. Unlike
    the former delete-then-insert, this preserves any connected ``MENTIONS``
    edges — Kuzu refuses to ``DELETE`` a node that still has edges, and
    entities are shared across facts. Kuzu 0.11.3 supports ``MERGE``.
    """
    conn.execute(
        """MERGE (e:Entity {id: $id})
           SET e.name = $name,
               e.entity_type = $entity_type,
               e.source_fact_id = $source_fact_id""",
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
    """Upsert a Fact node by id via MERGE + SET.

    Matches on the ``id`` PK if present, else creates the node, then updates
    its properties in place. This preserves any outgoing ``MENTIONS`` edges; a
    plain ``DELETE`` would fail on a Fact that already has edges. Kuzu 0.11.3
    supports ``MERGE``.
    """
    conn.execute(
        """MERGE (f:Fact {id: $id})
           SET f.content = $content,
               f.confidence = $confidence""",
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


def sync_upsert_owl(conn: kuzu.Connection, name: str) -> None:
    """Upsert an Owl node by name via MERGE (no other properties to set)."""
    conn.execute("MERGE (o:Owl {name: $name})", {"name": name})


def sync_upsert_skill(
    conn: kuzu.Connection,
    skill_id: str,
    owner_id: str,
    name: str,
) -> None:
    """Upsert a Skill node by id via MERGE + SET.

    ``skill_id`` is the caller-composed ``f"{owner_id}::{name}"`` — matches
    ``skill_ownership``'s own ``(owner_id, owl_name, skill_name)`` PK shape
    directly, so syncing an ownership row never needs a join through
    ``skills.skill_id`` (that table's own surrogate PK, unrelated to this key).
    """
    conn.execute(
        """MERGE (s:Skill {id: $id})
           SET s.owner_id = $owner_id,
               s.name = $name""",
        {"id": skill_id, "owner_id": owner_id, "name": name},
    )


def sync_upsert_trait(
    conn: kuzu.Connection,
    trait_id: str,
    owl_name: str,
    trait_name: str,
    value: float,
) -> None:
    """Upsert a Trait node by id via MERGE + SET.

    ``trait_id`` is the caller-composed ``f"{owl_name}::{trait_name}"``.
    ``value`` overwrites on every sync (current state, not history — SQLite's
    ``dna_checkpoints`` table owns mutation history).
    """
    conn.execute(
        """MERGE (t:Trait {id: $id})
           SET t.owl_name = $owl_name,
               t.trait_name = $trait_name,
               t.value = $value""",
        {
            "id": trait_id,
            "owl_name": owl_name,
            "trait_name": trait_name,
            "value": float(value),
        },
    )


def sync_link_owl_owns_skill(conn: kuzu.Connection, owl_name: str, skill_id: str) -> None:
    """Add an OWNS edge from Owl -> Skill (idempotent — MERGE, unlike the
    existing MENTIONS/RELATED_TO edges which use CREATE; verified safe for
    this Kuzu version, see Task 1's test suite)."""
    conn.execute(
        """MATCH (o:Owl {name: $owl_name}), (s:Skill {id: $skill_id})
           MERGE (o)-[:OWNS]->(s)""",
        {"owl_name": owl_name, "skill_id": skill_id},
    )


def sync_link_owl_has_trait(conn: kuzu.Connection, owl_name: str, trait_id: str) -> None:
    """Add a HAS_TRAIT edge from Owl -> Trait (idempotent — MERGE)."""
    conn.execute(
        """MATCH (o:Owl {name: $owl_name}), (t:Trait {id: $trait_id})
           MERGE (o)-[:HAS_TRAIT]->(t)""",
        {"owl_name": owl_name, "trait_id": trait_id},
    )


def sync_delete_skill(conn: kuzu.Connection, skill_id: str) -> None:
    """Remove a Skill node and any edges touching it (reconciliation prune)."""
    conn.execute("MATCH (s:Skill {id: $id}) DETACH DELETE s", {"id": skill_id})


def sync_delete_trait(conn: kuzu.Connection, trait_id: str) -> None:
    """Remove a Trait node and any edges touching it (reconciliation prune)."""
    conn.execute("MATCH (t:Trait {id: $id}) DETACH DELETE t", {"id": trait_id})


def sync_list_skill_ids(conn: kuzu.Connection) -> list[str]:
    """All Skill node ids currently in the graph (for reconciliation diffing)."""
    result = conn.execute("MATCH (s:Skill) RETURN s.id AS id")
    return [str(row["id"]) for row in _rows_from_result(result)]


def sync_list_trait_ids(conn: kuzu.Connection) -> list[str]:
    """All Trait node ids currently in the graph (for reconciliation diffing)."""
    result = conn.execute("MATCH (t:Trait) RETURN t.id AS id")
    return [str(row["id"]) for row in _rows_from_result(result)]
