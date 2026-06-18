"""Edge-preserving upsert tests for the Kuzu sync helpers.

Reproduces the production bug where ``sync_upsert_entity`` did a
delete-then-insert and Kuzu 0.11.3 refused to ``DELETE`` a node that still had
connected ``MENTIONS`` edges (entities are shared across facts), silently
dropping the entity write. The fix uses ``MERGE`` + ``SET`` which upserts in
place and preserves connected edges.

The sync helpers take a real ``kuzu.Connection`` and have no ``TestModeGuard``,
so they are exercised directly against a real temp Kuzu DB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import kuzu
import pytest

from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_link_fact_to_entity,
    sync_upsert_entity,
    sync_upsert_fact,
)


@pytest.fixture()
def conn(tmp_path: Path) -> kuzu.Connection:
    """A fresh on-disk Kuzu connection with the StackOwl schema applied."""
    db = kuzu.Database(str(tmp_path / "graph.kuzu"))
    connection = kuzu.Connection(db)
    sync_create_schema(connection)
    return connection


def _scalar(connection: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None) -> Any:
    """Return the single scalar value of a one-row, one-column query."""
    result = connection.execute(cypher, params or {})
    assert result.has_next()
    return result.get_next()[0]


def test_reupsert_entity_with_existing_mentions_edge_does_not_raise(
    conn: kuzu.Connection,
) -> None:
    """The bug repro: re-upserting an entity that already has a MENTIONS edge."""
    sync_upsert_fact(conn, "f1", "content one", 0.9)
    sync_upsert_entity(conn, "e1", "Acme", "ORG", "f1")
    sync_link_fact_to_entity(conn, "f1", "e1", "subject")  # now e1 has an edge

    # A SECOND fact mentions the same entity → re-upsert must not raise.
    sync_upsert_entity(conn, "e1", "Acme Corp", "ORG", "f2")


def test_reupsert_entity_preserves_mentions_edge(conn: kuzu.Connection) -> None:
    """After re-upsert: the f1->e1 edge survives and props are updated."""
    sync_upsert_fact(conn, "f1", "content one", 0.9)
    sync_upsert_entity(conn, "e1", "Acme", "ORG", "f1")
    sync_link_fact_to_entity(conn, "f1", "e1", "subject")

    sync_upsert_entity(conn, "e1", "Acme Corp", "ORG", "f2")

    edge_count = _scalar(
        conn,
        "MATCH (f:Fact {id: 'f1'})-[:MENTIONS]->(e:Entity {id: 'e1'}) RETURN count(*)",
    )
    assert edge_count == 1

    name = _scalar(conn, "MATCH (e:Entity {id: 'e1'}) RETURN e.name")
    assert name == "Acme Corp"
    src = _scalar(conn, "MATCH (e:Entity {id: 'e1'}) RETURN e.source_fact_id")
    assert src == "f2"


def test_reupsert_fact_with_existing_mentions_edge_does_not_raise_and_preserves_edge(
    conn: kuzu.Connection,
) -> None:
    """sync_upsert_fact has the identical latent bug: a Fact with an outgoing
    MENTIONS edge must re-upsert without raising and keep the edge."""
    sync_upsert_fact(conn, "f1", "content one", 0.9)
    sync_upsert_entity(conn, "e1", "Acme", "ORG", "f1")
    sync_link_fact_to_entity(conn, "f1", "e1", "subject")  # f1 now has an edge

    # Re-upsert the fact (e.g. content changed) — must not raise.
    sync_upsert_fact(conn, "f1", "content one updated", 0.5)

    edge_count = _scalar(
        conn,
        "MATCH (f:Fact {id: 'f1'})-[:MENTIONS]->(e:Entity {id: 'e1'}) RETURN count(*)",
    )
    assert edge_count == 1

    content = _scalar(conn, "MATCH (f:Fact {id: 'f1'}) RETURN f.content")
    assert content == "content one updated"
    confidence = _scalar(conn, "MATCH (f:Fact {id: 'f1'}) RETURN f.confidence")
    assert confidence == pytest.approx(0.5)


def test_upsert_entity_creates_when_absent(conn: kuzu.Connection) -> None:
    """MERGE on a non-existent id CREATES the node with the right props."""
    sync_upsert_entity(conn, "e9", "Globex", "ORG", "f9")

    count = _scalar(conn, "MATCH (e:Entity {id: 'e9'}) RETURN count(*)")
    assert count == 1
    name = _scalar(conn, "MATCH (e:Entity {id: 'e9'}) RETURN e.name")
    assert name == "Globex"
    etype = _scalar(conn, "MATCH (e:Entity {id: 'e9'}) RETURN e.entity_type")
    assert etype == "ORG"
    src = _scalar(conn, "MATCH (e:Entity {id: 'e9'}) RETURN e.source_fact_id")
    assert src == "f9"


def test_upsert_is_idempotent(conn: kuzu.Connection) -> None:
    """Twice with same id+props → exactly one node, edge count unchanged."""
    sync_upsert_fact(conn, "f1", "content", 0.9)
    sync_upsert_entity(conn, "e1", "Acme", "ORG", "f1")
    sync_link_fact_to_entity(conn, "f1", "e1", "subject")

    sync_upsert_entity(conn, "e1", "Acme", "ORG", "f1")

    node_count = _scalar(conn, "MATCH (e:Entity {id: 'e1'}) RETURN count(*)")
    assert node_count == 1
    edge_count = _scalar(
        conn,
        "MATCH (f:Fact {id: 'f1'})-[:MENTIONS]->(e:Entity {id: 'e1'}) RETURN count(*)",
    )
    assert edge_count == 1
