"""Owl/Skill/Trait graph sync helper tests — mirrors test_kuzu_upsert_edges.py's
direct-Connection style (no TestModeGuard on these low-level sync_* functions)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import kuzu
import pytest

from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_delete_skill,
    sync_delete_trait,
    sync_link_owl_has_trait,
    sync_link_owl_owns_skill,
    sync_list_skill_ids,
    sync_list_trait_ids,
    sync_upsert_owl,
    sync_upsert_skill,
    sync_upsert_trait,
)


@pytest.fixture()
def conn(tmp_path: Path) -> kuzu.Connection:
    db = kuzu.Database(str(tmp_path / "graph.kuzu"))
    connection = kuzu.Connection(db)
    sync_create_schema(connection)
    return connection


def _scalar(connection: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None) -> Any:
    result = connection.execute(cypher, params or {})
    assert result.has_next()
    return result.get_next()[0]


def test_upsert_owl_creates_node(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    count = _scalar(conn, "MATCH (o:Owl {name: 'Brain'}) RETURN count(*)")
    assert count == 1


def test_upsert_owl_is_idempotent(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_owl(conn, "Brain")
    count = _scalar(conn, "MATCH (o:Owl {name: 'Brain'}) RETURN count(*)")
    assert count == 1


def test_upsert_skill_creates_node_with_props(conn: kuzu.Connection) -> None:
    sync_upsert_skill(conn, "principal-default::web_search", "principal-default", "web_search")
    name = _scalar(
        conn, "MATCH (s:Skill {id: 'principal-default::web_search'}) RETURN s.name"
    )
    assert name == "web_search"
    owner = _scalar(
        conn, "MATCH (s:Skill {id: 'principal-default::web_search'}) RETURN s.owner_id"
    )
    assert owner == "principal-default"


def test_upsert_trait_creates_node_with_value(conn: kuzu.Connection) -> None:
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.7)
    value = _scalar(
        conn, "MATCH (t:Trait {id: 'Brain::challenge_level'}) RETURN t.value"
    )
    assert value == pytest.approx(0.7)


def test_upsert_trait_overwrites_value_on_resync(conn: kuzu.Connection) -> None:
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.7)
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.9)
    value = _scalar(
        conn, "MATCH (t:Trait {id: 'Brain::challenge_level'}) RETURN t.value"
    )
    assert value == pytest.approx(0.9)


def test_link_owl_owns_skill_creates_edge(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_skill(conn, "principal-default::web_search", "principal-default", "web_search")
    sync_link_owl_owns_skill(conn, "Brain", "principal-default::web_search")
    count = _scalar(
        conn,
        "MATCH (o:Owl {name: 'Brain'})-[:OWNS]->(s:Skill {id: 'principal-default::web_search'}) "
        "RETURN count(*)",
    )
    assert count == 1


def test_link_owl_owns_skill_is_idempotent(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_skill(conn, "principal-default::web_search", "principal-default", "web_search")
    sync_link_owl_owns_skill(conn, "Brain", "principal-default::web_search")
    sync_link_owl_owns_skill(conn, "Brain", "principal-default::web_search")
    count = _scalar(
        conn,
        "MATCH (o:Owl {name: 'Brain'})-[:OWNS]->(s:Skill {id: 'principal-default::web_search'}) "
        "RETURN count(*)",
    )
    assert count == 1


def test_link_owl_has_trait_creates_edge(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.7)
    sync_link_owl_has_trait(conn, "Brain", "Brain::challenge_level")
    count = _scalar(
        conn,
        "MATCH (o:Owl {name: 'Brain'})-[:HAS_TRAIT]->(t:Trait {id: 'Brain::challenge_level'}) "
        "RETURN count(*)",
    )
    assert count == 1


def test_delete_skill_removes_node_and_edges(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_skill(conn, "principal-default::web_search", "principal-default", "web_search")
    sync_link_owl_owns_skill(conn, "Brain", "principal-default::web_search")

    sync_delete_skill(conn, "principal-default::web_search")

    count = _scalar(conn, "MATCH (s:Skill) RETURN count(*)")
    assert count == 0
    edge_count = _scalar(conn, "MATCH (:Owl)-[:OWNS]->(:Skill) RETURN count(*)")
    assert edge_count == 0


def test_delete_trait_removes_node_and_edges(conn: kuzu.Connection) -> None:
    sync_upsert_owl(conn, "Brain")
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.7)
    sync_link_owl_has_trait(conn, "Brain", "Brain::challenge_level")

    sync_delete_trait(conn, "Brain::challenge_level")

    count = _scalar(conn, "MATCH (t:Trait) RETURN count(*)")
    assert count == 0
    edge_count = _scalar(conn, "MATCH (:Owl)-[:HAS_TRAIT]->(:Trait) RETURN count(*)")
    assert edge_count == 0


def test_list_skill_ids_returns_all_ids(conn: kuzu.Connection) -> None:
    sync_upsert_skill(conn, "a::s1", "a", "s1")
    sync_upsert_skill(conn, "a::s2", "a", "s2")
    ids = sync_list_skill_ids(conn)
    assert set(ids) == {"a::s1", "a::s2"}


def test_list_trait_ids_returns_all_ids(conn: kuzu.Connection) -> None:
    sync_upsert_trait(conn, "Brain::challenge_level", "Brain", "challenge_level", 0.5)
    sync_upsert_trait(conn, "Brain::verbosity", "Brain", "verbosity", 0.5)
    ids = sync_list_trait_ids(conn)
    assert set(ids) == {"Brain::challenge_level", "Brain::verbosity"}
