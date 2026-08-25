"""ESC-51 — the knowledge graph traversed an edge type nothing has ever written.

MEASURED 2026-08-25, and the numbers are what make this worth a test rather than
a tidy-up:

  * 2,267 real turns carry `graph_context_len` on "[pipeline] classify: exit".
    It is ZERO on 2,267 of 2,267. Not low — zero, every turn ever logged.
  * The graph is NOT empty. Read from a byte copy of the live graph.kuzu (copied
    so the core's single-writer lock was never touched): 8,282 Entity nodes,
    20,065 Fact nodes, 37,483 MENTIONS edges.
  * RELATED_TO edges: ZERO. And `sync_traverse` was
    "MATCH (start:Entity {id})-[:RELATED_TO*1..N]->(other:Entity)".
  * `add_relation`, the ONLY function that creates a RELATED_TO edge, has zero
    callers anywhere in src/.

So the traversal read an edge type nothing writes, returned [] for every entity
on every turn, and the 37,483 MENTIONS edges that DO exist were never read. The
subsystem has never contributed one character to one turn and structurally could
not have.

FAILURE MODE 1, INVERTED. This codebase's first shape is "a write with no
reader". This is a READ WITH NO WRITER — the same defect from the other end.

The lookup was never the problem, which is why this went unnoticed: verified
against real stored ids, sha256("ORG|Telegram")[:16] reproduces the stored
ent_9996aaad0158e29d exactly. Writer and reader agreed on identity. They
disagreed only about which edge to walk.

THE FIX walks co-mention — two entities named by the SAME fact are related, which
is precisely what MENTIONS already records — and keeps RELATED_TO in the same
pattern so that if anything ever calls `add_relation`, it works immediately.
Measured on the real graph copy: the old cypher returned 0 rows for Telegram, the
new one returns 25 real co-mentions (OpenAI, GitHub, Anthropic, Instagram, ...).

AND IT IS BATCHED, for a measured reason. classify derives ~30 candidate ids per
turn and traversed them one at a time. Against the real graph that loop cost
545.6ms and returned 11 rows (a per-id LIMIT truncated it); one batched query
cost 59.4ms and returned 25. Nine times faster and strictly more complete — and
without batching, fixing the edge bug would have ADDED half a second to every
turn, trading a silent subsystem for a slow one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import kuzu
import pytest

from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_link_fact_to_entity,
    sync_traverse,
    sync_traverse_many,
    sync_upsert_entity,
    sync_upsert_fact,
)


@pytest.fixture()
def conn(tmp_path: Path) -> kuzu.Connection:
    """A real temp Kuzu DB with the StackOwl schema — never the live graph.

    The live graph is single-writer and owned by the running core; a test that
    opened it would race the platform for the lock. It is also the only honest
    way to test a Cypher change: a fake connection would have happily returned
    whatever the old query asked for, which is how this bug survived.
    """
    db = kuzu.Database(str(tmp_path / "graph.kuzu"))
    connection = kuzu.Connection(db)
    sync_create_schema(connection)
    return connection


def _two_entities_in_one_fact(connection: kuzu.Connection) -> None:
    """The shape the live graph is made of: one fact naming two entities.

    37,483 MENTIONS edges exist in exactly this shape and none of them were
    reachable.
    """
    sync_upsert_fact(connection, "f1", "Telegram delivery uses the OpenAI key", 0.9)
    sync_upsert_entity(connection, "e_tg", "Telegram", "ORG", "f1")
    sync_upsert_entity(connection, "e_oa", "OpenAI", "ORG", "f1")
    sync_link_fact_to_entity(connection, "f1", "e_tg", "subject")
    sync_link_fact_to_entity(connection, "f1", "e_oa", "object")


def _names(rows: list[dict[str, Any]]) -> set[str]:
    return {r.get("name") for r in rows}


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_co_mentioned_entities_are_reachable(conn: kuzu.Connection) -> None:
    """THE bug. Before the fix this returned [] — for every entity, every turn."""
    _two_entities_in_one_fact(conn)

    rows = sync_traverse(conn, "e_tg", 1)

    assert _names(rows) == {"OpenAI"}, (
        "two entities named by the same fact are related, and MENTIONS already "
        "records it — traversing RELATED_TO instead read an edge type nothing "
        "has ever written"
    )


def test_the_entity_itself_is_excluded(conn: kuzu.Connection) -> None:
    """Self would otherwise always come back via the shared fact, and the prompt
    block would tell the model that Telegram is related to Telegram."""
    _two_entities_in_one_fact(conn)

    assert "Telegram" not in _names(sync_traverse(conn, "e_tg", 1))


def test_an_isolated_entity_returns_nothing(conn: kuzu.Connection) -> None:
    """The empty answer must stay empty — this fix must not invent relations."""
    sync_upsert_fact(conn, "f9", "a fact naming one thing", 0.5)
    sync_upsert_entity(conn, "e_lonely", "Solo", "ORG", "f9")
    sync_link_fact_to_entity(conn, "f9", "e_lonely", "subject")

    assert sync_traverse(conn, "e_lonely", 1) == []


def test_an_unknown_id_returns_nothing(conn: kuzu.Connection) -> None:
    _two_entities_in_one_fact(conn)
    assert sync_traverse(conn, "ent_does_not_exist", 1) == []


# ---------------------------------------------------------------------------
# RELATED_TO must still work if anything ever writes one
# ---------------------------------------------------------------------------

def test_a_RELATED_TO_edge_is_still_traversed(conn: kuzu.Connection) -> None:
    """`add_relation` has no callers today, but the edge type is part of the
    schema and the fix must not quietly drop it — otherwise the first caller to
    appear would hit this same silence from the other direction."""
    sync_upsert_fact(conn, "f2", "unrelated fact", 0.5)
    sync_upsert_entity(conn, "e_a", "Alpha", "ORG", "f2")
    sync_upsert_entity(conn, "e_b", "Beta", "ORG", "f2")
    conn.execute(
        "MATCH (a:Entity {id: 'e_a'}), (b:Entity {id: 'e_b'}) "
        "CREATE (a)-[:RELATED_TO {relation: 'x', strength: 1.0}]->(b)"
    )

    assert "Beta" in _names(sync_traverse(conn, "e_a", 1))


# ---------------------------------------------------------------------------
# Batching — the latency half
# ---------------------------------------------------------------------------

def test_traverse_many_covers_every_id_in_one_call(conn: kuzu.Connection) -> None:
    """classify derives ~30 candidate ids per turn. Measured on the real graph:
    the per-id loop cost 545.6ms for 11 rows, one batched query cost 59.4ms for
    25. Fixing the edge bug WITHOUT this would have added half a second to every
    turn — a silent subsystem traded for a slow one."""
    _two_entities_in_one_fact(conn)
    sync_upsert_fact(conn, "f3", "GitHub hosts the repo", 0.9)
    sync_upsert_entity(conn, "e_gh", "GitHub", "ORG", "f3")
    sync_upsert_entity(conn, "e_repo", "repo", "CONCEPT", "f3")
    sync_link_fact_to_entity(conn, "f3", "e_gh", "subject")
    sync_link_fact_to_entity(conn, "f3", "e_repo", "object")

    rows = sync_traverse_many(conn, ["e_tg", "e_gh"], 1, limit=25)

    assert {"OpenAI", "repo"} <= _names(rows), (
        "one call must cover every candidate id, not just the first"
    )


def test_traverse_many_with_no_ids_does_not_query(conn: kuzu.Connection) -> None:
    assert sync_traverse_many(conn, [], 1, limit=25) == []


def test_traverse_many_is_bounded(conn: kuzu.Connection) -> None:
    """A hub entity must not dump its whole neighbourhood into the prompt."""
    sync_upsert_fact(conn, "hub", "a fact naming many things", 0.9)
    sync_upsert_entity(conn, "e_hub", "Hub", "ORG", "hub")
    sync_link_fact_to_entity(conn, "hub", "e_hub", "subject")
    for i in range(30):
        sync_upsert_entity(conn, f"e_{i}", f"Thing{i}", "CONCEPT", "hub")
        sync_link_fact_to_entity(conn, "hub", f"e_{i}", "object")

    assert len(sync_traverse_many(conn, ["e_hub"], 1, limit=10)) <= 10
