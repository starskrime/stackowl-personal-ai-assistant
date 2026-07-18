# Graph-Backed Owl/Skill/DNA Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Kuzu graph two new node types (`Owl`, `Skill`, `Trait`) connected by
`OWNS`/`HAS_TRAIT` edges, kept in sync with SQLite (which stays authoritative) via
best-effort inline writes at the two real mutation sites, backstopped by a weekly
reconciliation sweep.

**Architecture:** SQLite (`skills`/`skill_ownership`/`owl_dna`) is unchanged and remains
the source of truth. Kuzu gains a derived mirror of it. Two call sites get a best-effort
post-write sync call (never blocks, never raises). A new lightweight scheduler job diffs
SQLite against the graph weekly and backfills/prunes any drift.

**Tech Stack:** Python 3.13, Kuzu (embedded graph DB, already a dependency), SQLite via
`DbPool`, pytest + pytest-asyncio.

## Global Constraints

- SQLite stays authoritative — never write skill ownership or DNA values FROM the graph.
- Every graph read/write introduced by this plan is best-effort: try/except, `log.memory.warning` (or the calling module's own logger) on failure, never raises, never blocks the real (SQLite) operation.
- New Kuzu node/edge writes use `MERGE` (verified working and idempotent against this Kuzu version — see Task 1), not the existing `CREATE`-based pattern used for `MENTIONS`/`RELATED_TO` (those stay untouched).
- No SQLite migration needed — this plan only adds Kuzu-side schema (bootstrapped via `sync_create_schema`, same as today) and reads existing SQLite tables.
- Spec: `docs/superpowers/specs/2026-07-18-kuzu-graph-owl-skill-dna-sync-design.md`.

---

### Task 1: Graph schema — Owl/Skill/Trait node tables, OWNS/HAS_TRAIT edges, sync helpers

**Files:**
- Modify: `src/stackowl/memory/kuzu_helpers.py`
- Test: `tests/memory/test_kuzu_owl_skill_trait_sync.py` (new)

**Interfaces:**
- Produces: `sync_upsert_owl(conn, name: str) -> None`, `sync_upsert_skill(conn, skill_id: str, owner_id: str, name: str) -> None`, `sync_upsert_trait(conn, trait_id: str, owl_name: str, trait_name: str, value: float) -> None`, `sync_link_owl_owns_skill(conn, owl_name: str, skill_id: str) -> None`, `sync_link_owl_has_trait(conn, owl_name: str, trait_id: str) -> None`, `sync_delete_skill(conn, skill_id: str) -> None`, `sync_delete_trait(conn, trait_id: str) -> None`, `sync_list_skill_ids(conn) -> list[str]`, `sync_list_trait_ids(conn) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/memory/test_kuzu_owl_skill_trait_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/memory/test_kuzu_owl_skill_trait_sync.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_upsert_owl'` (the functions don't exist yet).

- [ ] **Step 3: Add the schema DDL and sync helper functions**

Edit `src/stackowl/memory/kuzu_helpers.py` — add to `SCHEMA_STATEMENTS` (insert before the
tuple's closing `)`, right after the existing `RELATED_TO` statement):

```python
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
```

Then append these functions at the end of the file (after `sync_probe`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_kuzu_owl_skill_trait_sync.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/memory/kuzu_helpers.py tests/memory/test_kuzu_owl_skill_trait_sync.py`
Run: `uv run mypy src/stackowl/memory/kuzu_helpers.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/memory/kuzu_helpers.py tests/memory/test_kuzu_owl_skill_trait_sync.py
git commit -m "feat(memory): add Owl/Skill/Trait graph schema + sync helpers"
```

---

### Task 2: KuzuAdapter public async methods

**Files:**
- Modify: `src/stackowl/memory/kuzu_adapter.py`
- Test: `tests/memory/test_kuzu_adapter_owl_skill_trait.py` (new)

**Interfaces:**
- Consumes: Task 1's `sync_upsert_owl`, `sync_upsert_skill`, `sync_upsert_trait`, `sync_link_owl_owns_skill`, `sync_link_owl_has_trait`, `sync_delete_skill`, `sync_delete_trait`, `sync_list_skill_ids`, `sync_list_trait_ids`.
- Produces: `KuzuAdapter.upsert_owl_node(name: str) -> None`, `upsert_skill_node(skill_id: str, owner_id: str, name: str) -> None`, `upsert_trait_node(trait_id: str, owl_name: str, trait_name: str, value: float) -> None`, `link_owl_owns_skill(owl_name: str, skill_id: str) -> None`, `link_owl_has_trait(owl_name: str, trait_id: str) -> None`, `delete_skill_node(skill_id: str) -> None`, `delete_trait_node(trait_id: str) -> None`, `list_skill_ids() -> list[str]`, `list_trait_ids() -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/memory/test_kuzu_adapter_owl_skill_trait.py`:

```python
"""KuzuAdapter Owl/Skill/Trait method tests — exercises the async wrapper against
a real on-disk Kuzu DB, monkey-patching TestModeGuard exactly like the existing
adapter test suite (see test_kuzu_adapter_healable.py) since these methods gate
on TestModeGuard.assert_not_test_mode like every other public adapter method."""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.memory.kuzu_adapter import KuzuAdapter


@pytest.fixture(autouse=True)
def _not_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)


@pytest.fixture()
async def adapter(tmp_path: Path):
    a = KuzuAdapter(data_dir=tmp_path)
    yield a
    await a.aclose()


async def test_upsert_owl_node(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    # round-trip via a Skill link — confirms the Owl node exists and is matchable
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")
    ids = await adapter.list_skill_ids()
    assert ids == ["p::s1"]


async def test_upsert_skill_node_and_list(adapter: KuzuAdapter) -> None:
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.upsert_skill_node("p::s2", "p", "s2")
    ids = await adapter.list_skill_ids()
    assert set(ids) == {"p::s1", "p::s2"}


async def test_upsert_trait_node_and_list(adapter: KuzuAdapter) -> None:
    await adapter.upsert_trait_node("Brain::challenge_level", "Brain", "challenge_level", 0.6)
    ids = await adapter.list_trait_ids()
    assert ids == ["Brain::challenge_level"]


async def test_link_owl_owns_skill(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")
    # no direct read API for edges on the adapter yet — verified via delete's
    # DETACH DELETE removing exactly this edge in the next test


async def test_delete_skill_node_removes_it(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_skill_node("p::s1", "p", "s1")
    await adapter.link_owl_owns_skill("Brain", "p::s1")

    await adapter.delete_skill_node("p::s1")

    ids = await adapter.list_skill_ids()
    assert ids == []


async def test_delete_trait_node_removes_it(adapter: KuzuAdapter) -> None:
    await adapter.upsert_owl_node("Brain")
    await adapter.upsert_trait_node("Brain::challenge_level", "Brain", "challenge_level", 0.6)
    await adapter.link_owl_has_trait("Brain", "Brain::challenge_level")

    await adapter.delete_trait_node("Brain::challenge_level")

    ids = await adapter.list_trait_ids()
    assert ids == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/memory/test_kuzu_adapter_owl_skill_trait.py -v`
Expected: FAIL — `AttributeError: 'KuzuAdapter' object has no attribute 'upsert_owl_node'`

- [ ] **Step 3: Add the methods**

Edit `src/stackowl/memory/kuzu_adapter.py`:

Add the new sync helper names to the existing import block (lines 41-49):

```python
from stackowl.memory.kuzu_helpers import (
    sync_create_schema,
    sync_delete_skill,
    sync_delete_trait,
    sync_link_entities,
    sync_link_fact_to_entity,
    sync_link_owl_has_trait,
    sync_link_owl_owns_skill,
    sync_list_skill_ids,
    sync_list_trait_ids,
    sync_probe,
    sync_traverse,
    sync_upsert_entity,
    sync_upsert_fact,
    sync_upsert_owl,
    sync_upsert_skill,
    sync_upsert_trait,
)
```

Add these methods after `link_entities` (after line 261, before `async def traverse`):

```python
    async def upsert_owl_node(self, name: str) -> None:
        """Upsert an Owl node keyed by ``name``."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_owl_node: entry", extra={"_fields": {"name": name}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_owl_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_upsert_owl, self._conn, name)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_owl_node: exit", extra={"_fields": {"name": name}},
        )

    async def upsert_skill_node(self, skill_id: str, owner_id: str, name: str) -> None:
        """Upsert a Skill node keyed by ``skill_id`` (``f"{owner_id}::{name}"``)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_skill_node: entry",
            extra={"_fields": {"skill_id": skill_id, "name": name}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_skill_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_upsert_skill, self._conn, skill_id, owner_id, name,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_skill_node: exit", extra={"_fields": {"skill_id": skill_id}},
        )

    async def upsert_trait_node(
        self, trait_id: str, owl_name: str, trait_name: str, value: float,
    ) -> None:
        """Upsert a Trait node keyed by ``trait_id`` (``f"{owl_name}::{trait_name}"``)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.upsert_trait_node: entry",
            extra={"_fields": {"trait_id": trait_id, "value": value}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.upsert_trait_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_upsert_trait, self._conn, trait_id, owl_name, trait_name, value,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.upsert_trait_node: exit", extra={"_fields": {"trait_id": trait_id}},
        )

    async def link_owl_owns_skill(self, owl_name: str, skill_id: str) -> None:
        """Create an Owl -> Skill OWNS edge (idempotent)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_owl_owns_skill: entry",
            extra={"_fields": {"owl_name": owl_name, "skill_id": skill_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_owl_owns_skill")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_link_owl_owns_skill, self._conn, owl_name, skill_id,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_owl_owns_skill: exit",
            extra={"_fields": {"owl_name": owl_name, "skill_id": skill_id}},
        )

    async def link_owl_has_trait(self, owl_name: str, trait_id: str) -> None:
        """Create an Owl -> Trait HAS_TRAIT edge (idempotent)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.link_owl_has_trait: entry",
            extra={"_fields": {"owl_name": owl_name, "trait_id": trait_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.link_owl_has_trait")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(
            self._executor, sync_link_owl_has_trait, self._conn, owl_name, trait_id,
        )
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.link_owl_has_trait: exit",
            extra={"_fields": {"owl_name": owl_name, "trait_id": trait_id}},
        )

    async def delete_skill_node(self, skill_id: str) -> None:
        """Remove a Skill node and its edges (reconciliation prune)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.delete_skill_node: entry", extra={"_fields": {"skill_id": skill_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.delete_skill_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_delete_skill, self._conn, skill_id)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.delete_skill_node: exit", extra={"_fields": {"skill_id": skill_id}},
        )

    async def delete_trait_node(self, trait_id: str) -> None:
        """Remove a Trait node and its edges (reconciliation prune)."""
        # 1. ENTRY
        log.memory.debug(
            "[memory] kuzu.delete_trait_node: entry", extra={"_fields": {"trait_id": trait_id}},
        )
        TestModeGuard.assert_not_test_mode("kuzu.delete_trait_node")
        loop = asyncio.get_event_loop()
        # 3. STEP
        await loop.run_in_executor(self._executor, sync_delete_trait, self._conn, trait_id)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.delete_trait_node: exit", extra={"_fields": {"trait_id": trait_id}},
        )

    async def list_skill_ids(self) -> list[str]:
        """All Skill node ids currently in the graph (reconciliation diffing)."""
        # 1. ENTRY
        log.memory.debug("[memory] kuzu.list_skill_ids: entry")
        TestModeGuard.assert_not_test_mode("kuzu.list_skill_ids")
        loop = asyncio.get_event_loop()
        # 3. STEP
        ids = await loop.run_in_executor(self._executor, sync_list_skill_ids, self._conn)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.list_skill_ids: exit", extra={"_fields": {"count": len(ids)}},
        )
        return ids

    async def list_trait_ids(self) -> list[str]:
        """All Trait node ids currently in the graph (reconciliation diffing)."""
        # 1. ENTRY
        log.memory.debug("[memory] kuzu.list_trait_ids: entry")
        TestModeGuard.assert_not_test_mode("kuzu.list_trait_ids")
        loop = asyncio.get_event_loop()
        # 3. STEP
        ids = await loop.run_in_executor(self._executor, sync_list_trait_ids, self._conn)
        # 4. EXIT
        log.memory.debug(
            "[memory] kuzu.list_trait_ids: exit", extra={"_fields": {"count": len(ids)}},
        )
        return ids
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/memory/test_kuzu_adapter_owl_skill_trait.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run Task 1's tests too (regression check) + lint + type-check**

Run: `uv run pytest tests/memory/test_kuzu_owl_skill_trait_sync.py tests/memory/test_kuzu_adapter_owl_skill_trait.py tests/memory/test_kuzu_upsert_edges.py -v`
Expected: PASS (all)
Run: `uv run ruff check src/stackowl/memory/kuzu_adapter.py tests/memory/test_kuzu_adapter_owl_skill_trait.py`
Run: `uv run mypy src/stackowl/memory/kuzu_adapter.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/memory/kuzu_adapter.py tests/memory/test_kuzu_adapter_owl_skill_trait.py
git commit -m "feat(memory): expose Owl/Skill/Trait graph ops on KuzuAdapter"
```

---

### Task 3: Best-effort skill-ownership sync on attach

**Files:**
- Modify: `src/stackowl/skills/synthesizer.py:337-373` (constructor), `src/stackowl/skills/synthesizer.py:564-609` (`_attach_to_owner`)
- Modify: `src/stackowl/skills/synthesizer_handler.py:37-115`
- Modify: `src/stackowl/scheduler/assembly.py` (the `SkillSynthesizerHandler(...)` construction call)
- Test: `tests/skills/test_synthesizer_graph_sync.py` (new)

**Interfaces:**
- Consumes: Task 2's `KuzuAdapter.upsert_owl_node`, `upsert_skill_node`, `link_owl_owns_skill`.
- Produces: `SkillSynthesizer.__init__(..., kuzu: KuzuAdapter | None = None)`; `_attach_to_owner` now also best-effort syncs the graph after a successful durable attach.

- [ ] **Step 1: Write the failing test**

Create `tests/skills/test_synthesizer_graph_sync.py`:

```python
"""SkillSynthesizer's best-effort graph sync on skill attach — a Kuzu failure
must never affect the durable (SQLite) attach outcome or raise."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.outcome_store import TaskOutcome
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.skills.synthesizer import SkillSynthesizer, ToolSequenceCluster

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "synth.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


def _cluster(owner: str) -> ToolSequenceCluster:
    outcome = TaskOutcome(
        outcome_id=1, trace_id="t1", session_id="s1", owl_name=owner,
        channel="cli", success=True, latency_ms=100.0, tool_call_count=1,
        failure_class=None, quality_score=0.9, step_durations={},
        input_text="do the thing", response_text="done",
        captured_at=0.0, scored_at=0.0, tool_sequence=("web_search",),
    )
    return ToolSequenceCluster(sequence=("web_search",), outcomes=(outcome,))


def _make_synth(db: DbPool, registry: OwlRegistry, kuzu: Any) -> SkillSynthesizer:
    return SkillSynthesizer(
        outcome_store=AsyncMock(), skill_store=AsyncMock(),
        provider=AsyncMock(), skills_root=Path("/tmp/unused"),
        owl_registry=registry, db=db, kuzu=kuzu,
    )


async def test_attach_syncs_graph_on_success(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    kuzu = AsyncMock()
    synth = _make_synth(db, registry, kuzu)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True
    kuzu.upsert_owl_node.assert_awaited_once_with("Brain")
    kuzu.upsert_skill_node.assert_awaited_once()
    kuzu.link_owl_owns_skill.assert_awaited_once()


async def test_attach_survives_graph_sync_failure(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    kuzu = AsyncMock()
    kuzu.upsert_owl_node.side_effect = RuntimeError("kuzu down")
    synth = _make_synth(db, registry, kuzu)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True  # the durable/live attach outcome is unaffected


async def test_attach_with_no_kuzu_wired_still_works(db: DbPool) -> None:
    registry = OwlRegistry()
    registry.register(OwlAgentManifest(
        name="Brain", role="assistant", system_prompt="You are Brain.",
        model_tier="fast", skills=(),
    ))
    synth = _make_synth(db, registry, kuzu=None)

    attached = await synth._attach_to_owner(_cluster("Brain"), "new_skill")

    assert attached is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/skills/test_synthesizer_graph_sync.py -v`
Expected: FAIL — `TypeError: SkillSynthesizer.__init__() got an unexpected keyword argument 'kuzu'`

- [ ] **Step 3: Wire the sync**

Edit `src/stackowl/skills/synthesizer.py` — add `kuzu` to the constructor (after
`consent_gate: ConsequentialActionGate | None = None,` in the `__init__` signature):

```python
        consent_gate: ConsequentialActionGate | None = None,
        kuzu: KuzuAdapter | None = None,
```

and store it (after `self._consent_gate = consent_gate`):

```python
        # Dynamic-injection arc, sub-project 1 — best-effort graph mirror of
        # skill ownership. None (no graph wired) degrades to exactly today's
        # behavior; a Kuzu failure at sync time never affects the durable attach.
        self._kuzu = kuzu
```

Add the import near the top of the file (with the other `TYPE_CHECKING`-gated or direct
imports, matching however `OwlRegistry`/`DbPool` are imported there):

```python
from stackowl.memory.kuzu_adapter import KuzuAdapter
```

Then edit `_attach_to_owner` (replace the body from `attached = attach_skill_to_owl(...)`
through the `return attached` line):

```python
            attached = attach_skill_to_owl(self._owl_registry, owner, skill_name)
            # Persist regardless of the live result so the durable record exists
            # for the next boot even if the owl wasn't loaded this process
            # (idempotent upsert — boot hydrate then attaches it).
            await persist_skill_ownership(self._db, owner, skill_name)
            await self._sync_ownership_to_graph(owner, skill_name)
            log.skills.info(
                "[synth] attach_to_owner: exit",
                extra={"_fields": {
                    "skill": skill_name, "owner": owner, "live_attached": attached,
                }},
            )
            return attached
```

And add this new method right after `_attach_to_owner`:

```python
    async def _sync_ownership_to_graph(self, owner: str, skill_name: str) -> None:
        """Best-effort mirror of a new skill attach into the graph.

        Never raises, never blocks the caller — a Kuzu failure here must not
        affect the already-committed durable (SQLite) attach outcome. No-op
        when no graph is wired (``self._kuzu is None``, the default)."""
        if self._kuzu is None:
            return
        try:
            skill_id = f"{DEFAULT_PRINCIPAL_ID}::{skill_name}"
            await self._kuzu.upsert_owl_node(owner)
            await self._kuzu.upsert_skill_node(skill_id, DEFAULT_PRINCIPAL_ID, skill_name)
            await self._kuzu.link_owl_owns_skill(owner, skill_id)
        except Exception as exc:  # noqa: BLE001 — a graph-sync failure must never break synthesis
            log.skills.warning(
                "[synth] _sync_ownership_to_graph: failed — graph left stale",
                exc_info=exc,
                extra={"_fields": {"owner": owner, "skill": skill_name}},
            )
```

Check whether `DEFAULT_PRINCIPAL_ID` is already imported in `synthesizer.py` (grep
`grep -n "DEFAULT_PRINCIPAL_ID" src/stackowl/skills/synthesizer.py`); if not, add
`from stackowl.tenancy.principal import DEFAULT_PRINCIPAL_ID` near the other imports.

Now edit `src/stackowl/skills/synthesizer_handler.py` — add `kuzu` to
`SkillSynthesizerHandler.__init__`'s keyword-only params (after `consent_gate:
ConsequentialActionGate | None = None,`):

```python
        consent_gate: ConsequentialActionGate | None = None,
        kuzu: KuzuAdapter | None = None,
```

store it (after `self._consent_gate = consent_gate`):

```python
        self._kuzu = kuzu
```

add the import (near the top, alongside the other stackowl imports):

```python
from stackowl.memory.kuzu_adapter import KuzuAdapter
```

and pass it through in `execute()`'s `SkillSynthesizer(...)` construction (add after
`consent_gate=self._consent_gate,`):

```python
            consent_gate=self._consent_gate,
            kuzu=self._kuzu,
```

Finally, in `src/stackowl/scheduler/assembly.py`, find the existing
`SkillSynthesizerHandler(` construction call and add `kuzu=memory_components.kuzu_adapter,`
as one more keyword argument (matching how `memory_components.embedding_registry` is
already passed there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/skills/test_synthesizer_graph_sync.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the existing synthesizer test suite too (regression check) + lint + type-check**

Run: `uv run pytest tests/skills/ -k synthesizer -v`
Expected: PASS (no regressions)
Run: `uv run ruff check src/stackowl/skills/synthesizer.py src/stackowl/skills/synthesizer_handler.py src/stackowl/scheduler/assembly.py tests/skills/test_synthesizer_graph_sync.py`
Run: `uv run mypy src/stackowl/skills/synthesizer.py src/stackowl/skills/synthesizer_handler.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/skills/synthesizer.py src/stackowl/skills/synthesizer_handler.py src/stackowl/scheduler/assembly.py tests/skills/test_synthesizer_graph_sync.py
git commit -m "feat(skills): best-effort graph sync when a skill is attached to an owl"
```

---

### Task 4: Best-effort DNA trait sync on persist

**Files:**
- Modify: `src/stackowl/owls/evolution.py:161-198` (constructor), `:850-851` (`_persist_dna`)
- Modify: `src/stackowl/scheduler/handlers/evolution.py` (`register_evolution_handler`)
- Modify: `src/stackowl/scheduler/assembly.py` (the `register_evolution_handler(...)` call)
- Test: `tests/owls/test_evolution_graph_sync.py` (new)

**Interfaces:**
- Consumes: Task 2's `KuzuAdapter.upsert_owl_node`, `upsert_trait_node`, `link_owl_has_trait`.
- Produces: `EvolutionCoordinator.__init__(..., kuzu: KuzuAdapter | None = None)`; `_persist_dna` now also best-effort syncs all 7 traits to the graph after the SQLite write.

- [ ] **Step 1: Write the failing test**

Create `tests/owls/test_evolution_graph_sync.py`:

```python
"""EvolutionCoordinator's best-effort DNA graph sync — a Kuzu failure must
never affect the durable (SQLite) persist outcome or raise."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.dna import OwlDNA
from stackowl.owls.dna_hydrator import read_all_owl_dna
from stackowl.owls.evolution import EvolutionCoordinator
from stackowl.owls.registry import OwlRegistry

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "evo.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def test_persist_dna_syncs_all_traits_to_graph(db: DbPool) -> None:
    kuzu = AsyncMock()
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=kuzu,
    )

    await coordinator._persist_dna("Brain", OwlDNA())

    kuzu.upsert_owl_node.assert_awaited_once_with("Brain")
    assert kuzu.upsert_trait_node.await_count == 7  # one per TRAIT_NAMES entry
    assert kuzu.link_owl_has_trait.await_count == 7
    # the durable write still happened regardless of the graph mock
    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted


async def test_persist_dna_survives_graph_sync_failure(db: DbPool) -> None:
    kuzu = AsyncMock()
    kuzu.upsert_owl_node.side_effect = RuntimeError("kuzu down")
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=kuzu,
    )

    await coordinator._persist_dna("Brain", OwlDNA())  # must not raise

    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted  # SQLite write unaffected


async def test_persist_dna_with_no_kuzu_wired_still_works(db: DbPool) -> None:
    coordinator = EvolutionCoordinator(
        db, provider_registry=AsyncMock(), owl_registry=OwlRegistry(), kuzu=None,
    )

    await coordinator._persist_dna("Brain", OwlDNA())  # must not raise

    persisted = await read_all_owl_dna(db)
    assert "Brain" in persisted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_evolution_graph_sync.py -v`
Expected: FAIL — `TypeError: EvolutionCoordinator.__init__() got an unexpected keyword argument 'kuzu'`

- [ ] **Step 3: Wire the sync**

Edit `src/stackowl/owls/evolution.py` — add `kuzu` to `EvolutionCoordinator.__init__`'s
signature (after `shadow_validator: ShadowValidator | None = None,`):

```python
        shadow_validator: ShadowValidator | None = None,
        kuzu: KuzuAdapter | None = None,
```

store it (after `self._skill_store = SkillIndexStore(db)`):

```python
        # Dynamic-injection arc, sub-project 1 — best-effort graph mirror of DNA
        # trait state. None (no graph wired) degrades to exactly today's
        # behavior; a Kuzu failure at sync time never affects the durable persist.
        self._kuzu = kuzu
```

Add the import near the top of the file (alongside the other `stackowl.owls`/`stackowl.memory` imports):

```python
from stackowl.memory.kuzu_adapter import KuzuAdapter
```

Replace `_persist_dna` (lines 850-851):

```python
    async def _persist_dna(self, owl_name: str, dna: OwlDNA) -> None:
        await upsert_owl_dna(self._db, owl_name, dna, table="owl_dna")
        await self._sync_dna_to_graph(owl_name, dna)

    async def _sync_dna_to_graph(self, owl_name: str, dna: OwlDNA) -> None:
        """Best-effort mirror of the just-persisted DNA values into the graph.

        Never raises, never blocks the caller — a Kuzu failure here must not
        affect the already-committed durable (SQLite) persist. No-op when no
        graph is wired (``self._kuzu is None``, the default)."""
        if self._kuzu is None:
            return
        try:
            await self._kuzu.upsert_owl_node(owl_name)
            for trait_name in _MUTABLE_TRAITS:
                trait_id = f"{owl_name}::{trait_name}"
                value = float(getattr(dna, trait_name))
                await self._kuzu.upsert_trait_node(trait_id, owl_name, trait_name, value)
                await self._kuzu.link_owl_has_trait(owl_name, trait_id)
        except Exception as exc:  # noqa: BLE001 — a graph-sync failure must never break evolution
            log.engine.warning(
                "[dna] coordinator._sync_dna_to_graph: failed — graph left stale",
                exc_info=exc,
                extra={"_fields": {"owl": owl_name}},
            )
```

Now edit `src/stackowl/scheduler/handlers/evolution.py`'s `register_evolution_handler` —
add a `kuzu` parameter (after `delegation_governor: ConcurrencyGovernor | None = None,`):

```python
    delegation_governor: ConcurrencyGovernor | None = None,
    kuzu: KuzuAdapter | None = None,
```

pass it through to the `EvolutionCoordinator(...)` construction (add after
`delegation_governor=delegation_governor,`):

```python
        delegation_governor=delegation_governor,
        kuzu=kuzu,
```

and add the import at the top of that file:

```python
from stackowl.memory.kuzu_adapter import KuzuAdapter
```

Finally, in `src/stackowl/scheduler/assembly.py`, find the existing
`register_evolution_handler(` call and add `kuzu=memory_components.kuzu_adapter,` as one
more keyword argument.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/owls/test_evolution_graph_sync.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the existing evolution test suite too (regression check) + lint + type-check**

Run: `uv run pytest tests/owls/ -k evolution -v`
Expected: PASS (no regressions)
Run: `uv run ruff check src/stackowl/owls/evolution.py src/stackowl/scheduler/handlers/evolution.py src/stackowl/scheduler/assembly.py tests/owls/test_evolution_graph_sync.py`
Run: `uv run mypy src/stackowl/owls/evolution.py src/stackowl/scheduler/handlers/evolution.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/owls/evolution.py src/stackowl/scheduler/handlers/evolution.py src/stackowl/scheduler/assembly.py tests/owls/test_evolution_graph_sync.py
git commit -m "feat(owls): best-effort graph sync when DNA is persisted"
```

---

### Task 5: GraphReconciliationHandler — weekly diff + backfill + prune

**Files:**
- Create: `src/stackowl/scheduler/handlers/graph_reconciliation.py`
- Test: `tests/scheduler/test_graph_reconciliation.py` (new)

**Interfaces:**
- Consumes: Task 2's full `KuzuAdapter` Owl/Skill/Trait API; `DbPool.fetch_all`.
- Produces: `GraphReconciliationHandler(db: DbPool, kuzu: KuzuAdapter | None)` — a `JobHandler` with `handler_name == "graph_reconciliation"`.

- [ ] **Step 1: Write the failing test**

Create `tests/scheduler/test_graph_reconciliation.py`:

```python
"""GraphReconciliationHandler — diffs SQLite against the graph, backfills what's
missing, prunes what's stale. Per-item isolated (one bad row never stops the rest)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.kuzu_adapter import KuzuAdapter
from stackowl.scheduler.handlers.graph_reconciliation import GraphReconciliationHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def db(tmp_path: Path) -> AsyncIterator[DbPool]:
    db_path = tmp_path / "recon.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture()
async def kuzu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[KuzuAdapter]:
    from stackowl.config.test_mode import TestModeGuard
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    adapter = KuzuAdapter(data_dir=tmp_path / "kuzu")
    yield adapter
    await adapter.aclose()


def _job() -> Job:
    return Job(
        job_id="graph_reconciliation-1", handler_name="graph_reconciliation",
        schedule="every 168h", idempotency_key="graph_reconciliation:every-168h",
        last_run_at=None, next_run_at="2026-01-01T00:00:00+00:00", status="running",
    )


async def test_backfills_missing_skill_ownership(db: DbPool, kuzu: KuzuAdapter) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "web_search", 0.0),
    )
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert ids == ["principal-default::web_search"]


async def test_backfills_missing_dna_traits(db: DbPool, kuzu: KuzuAdapter) -> None:
    from stackowl.owls.dna import OwlDNA
    from stackowl.owls.dna_storage import upsert_owl_dna

    await upsert_owl_dna(db, "Brain", OwlDNA(), table="owl_dna")
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_trait_ids()
    assert len(ids) == 7  # one per TRAIT_NAMES entry


async def test_prunes_stale_skill_no_longer_in_sqlite(db: DbPool, kuzu: KuzuAdapter) -> None:
    await kuzu.upsert_skill_node("principal-default::gone", "principal-default", "gone")
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert ids == []


async def test_no_kuzu_wired_is_a_clean_noop(db: DbPool) -> None:
    handler = GraphReconciliationHandler(db, None)

    result = await handler.execute(_job())

    assert result.success is True


async def test_one_bad_row_does_not_stop_the_sweep(
    db: DbPool, kuzu: KuzuAdapter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "s1", 0.0),
    )
    await db.execute(
        "INSERT INTO skill_ownership (owner_id, owl_name, skill_name, attached_at) "
        "VALUES (?, ?, ?, ?)",
        ("principal-default", "Brain", "s2", 0.0),
    )
    original = kuzu.upsert_skill_node
    calls = {"n": 0}

    async def _flaky(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(kuzu, "upsert_skill_node", _flaky)
    handler = GraphReconciliationHandler(db, kuzu)

    result = await handler.execute(_job())

    assert result.success is True
    ids = await kuzu.list_skill_ids()
    assert len(ids) == 1  # the second row still got synced despite the first raising
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_graph_reconciliation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.scheduler.handlers.graph_reconciliation'`

- [ ] **Step 3: Implement the handler**

Create `src/stackowl/scheduler/handlers/graph_reconciliation.py`:

```python
"""GraphReconciliationHandler — weekly diff-and-backfill-and-prune between
SQLite (authoritative: skill_ownership, owl_dna) and the Kuzu graph (derived
index: Owl/Skill/Trait nodes, OWNS/HAS_TRAIT edges).

Backstops the best-effort inline sync in synthesizer.py/evolution.py — an
extended Kuzu outage (or a bug) can only ever leave the graph stale until the
next weekly sweep closes the gap. Never fails the tick on one bad row (mirrors
retry_sweep/objective_driver's per-item isolation); a no-graph-wired box is a
clean, honest no-op.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from stackowl.infra.observability import log
from stackowl.owls.dna_defaults import TRAIT_NAMES
from stackowl.scheduler.base import JobHandler
from stackowl.scheduler.job import Job, JobResult

if TYPE_CHECKING:  # pragma: no cover — typing only
    from stackowl.db.pool import DbPool
    from stackowl.memory.kuzu_adapter import KuzuAdapter

_SELECT_SKILL_OWNERSHIP = "SELECT owner_id, owl_name, skill_name FROM skill_ownership"
_SELECT_OWL_DNA = "SELECT owl_name, " + ", ".join(TRAIT_NAMES) + " FROM owl_dna"


class GraphReconciliationHandler(JobHandler):
    """Diff SQLite against the graph; backfill what's missing, prune what's stale."""

    def __init__(self, db: DbPool, kuzu: KuzuAdapter | None) -> None:
        self._db = db
        self._kuzu = kuzu

    @property
    def handler_name(self) -> str:
        return "graph_reconciliation"

    async def execute(self, job: Job) -> JobResult:
        t0 = time.monotonic()
        # 1. ENTRY
        log.scheduler.debug(
            "[scheduler] graph_reconciliation.execute: entry",
            extra={"_fields": {"job_id": job.job_id, "has_kuzu": self._kuzu is not None}},
        )
        if self._kuzu is None:
            return JobResult(
                job_id=job.job_id, effect_class="state_change", success=True,
                output="graph_reconciliation: noop (no graph wired)", error=None,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        backfilled_skills = await self._reconcile_skills()
        backfilled_traits = await self._reconcile_traits()

        duration_ms = (time.monotonic() - t0) * 1000
        # 4. EXIT
        log.scheduler.info(
            "[scheduler] graph_reconciliation.execute: exit",
            extra={"_fields": {
                "backfilled_skills": backfilled_skills,
                "backfilled_traits": backfilled_traits,
                "duration_ms": duration_ms,
            }},
        )
        return JobResult(
            job_id=job.job_id, effect_class="state_change", success=True,
            output=f"skills={backfilled_skills} traits={backfilled_traits}",
            error=None, duration_ms=duration_ms,
            metadata={"backfilled_skills": backfilled_skills, "backfilled_traits": backfilled_traits},
        )

    async def _reconcile_skills(self) -> int:
        assert self._kuzu is not None
        rows = await self._db.fetch_all(_SELECT_SKILL_OWNERSHIP)
        want: dict[str, tuple[str, str, str]] = {}
        for row in rows:
            owner_id = str(row["owner_id"])
            owl_name = str(row["owl_name"])
            skill_name = str(row["skill_name"])
            skill_id = f"{owner_id}::{skill_name}"
            want[skill_id] = (owner_id, owl_name, skill_name)

        have = set(await self._kuzu.list_skill_ids())
        touched = 0
        for skill_id, (owner_id, owl_name, skill_name) in want.items():
            if skill_id in have:
                continue
            try:
                await self._kuzu.upsert_owl_node(owl_name)
                await self._kuzu.upsert_skill_node(skill_id, owner_id, skill_name)
                await self._kuzu.link_owl_owns_skill(owl_name, skill_id)
                touched += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_skills: row failed",
                    exc_info=exc,
                    extra={"_fields": {"skill_id": skill_id}},
                )

        for stale_id in have - want.keys():
            try:
                await self._kuzu.delete_skill_node(stale_id)
            except Exception as exc:  # noqa: BLE001
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_skills: prune failed",
                    exc_info=exc,
                    extra={"_fields": {"skill_id": stale_id}},
                )
        return touched

    async def _reconcile_traits(self) -> int:
        assert self._kuzu is not None
        rows = await self._db.fetch_all(_SELECT_OWL_DNA)
        want: dict[str, tuple[str, str, float]] = {}
        for row in rows:
            owl_name = str(row["owl_name"])
            for trait_name in TRAIT_NAMES:
                trait_id = f"{owl_name}::{trait_name}"
                want[trait_id] = (owl_name, trait_name, float(row[trait_name]))

        have = set(await self._kuzu.list_trait_ids())
        touched = 0
        for trait_id, (owl_name, trait_name, value) in want.items():
            if trait_id in have:
                continue
            try:
                await self._kuzu.upsert_owl_node(owl_name)
                await self._kuzu.upsert_trait_node(trait_id, owl_name, trait_name, value)
                await self._kuzu.link_owl_has_trait(owl_name, trait_id)
                touched += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not stop the sweep
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_traits: row failed",
                    exc_info=exc,
                    extra={"_fields": {"trait_id": trait_id}},
                )

        for stale_id in have - want.keys():
            try:
                await self._kuzu.delete_trait_node(stale_id)
            except Exception as exc:  # noqa: BLE001
                log.scheduler.warning(
                    "[scheduler] graph_reconciliation._reconcile_traits: prune failed",
                    exc_info=exc,
                    extra={"_fields": {"trait_id": stale_id}},
                )
        return touched
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_graph_reconciliation.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/stackowl/scheduler/handlers/graph_reconciliation.py tests/scheduler/test_graph_reconciliation.py`
Run: `uv run mypy src/stackowl/scheduler/handlers/graph_reconciliation.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/scheduler/handlers/graph_reconciliation.py tests/scheduler/test_graph_reconciliation.py
git commit -m "feat(scheduler): add GraphReconciliationHandler (weekly diff/backfill/prune)"
```

---

### Task 6: Register + seed the reconciliation job

**Files:**
- Modify: `src/stackowl/scheduler/assembly.py`

**Interfaces:**
- Consumes: Task 5's `GraphReconciliationHandler`; the existing `_seed_minutes_schedule` helper and `HandlerRegistry.instance().register(...)` pattern already used for every other maintenance sweep in this file.

- [ ] **Step 1: Register the handler**

In `src/stackowl/scheduler/assembly.py`, find where `HandlerRegistry.instance().register(...)`
is called for the other maintenance handlers (e.g. `tool_pruning_handler`) and add,
right after the `register_evolution_handler(...)` call:

```python
        from stackowl.scheduler.handlers.graph_reconciliation import (
            GraphReconciliationHandler,
        )

        graph_reconciliation_handler = GraphReconciliationHandler(
            db=db, kuzu=memory_components.kuzu_adapter,
        )
        HandlerRegistry.instance().register(graph_reconciliation_handler)
```

- [ ] **Step 2: Seed the weekly job**

Find the block seeding the other every-Nm/every-Nh maintenance jobs (e.g.
`downloads_janitor`'s `_seed_minutes_schedule(db, handler_name="downloads_janitor",
schedule="every 12h", interval_minutes=720)`) and add, in the same style:

```python
        # Dynamic-injection arc, sub-project 1 — weekly diff/backfill/prune between
        # SQLite (authoritative) and the derived graph mirror. 168h = 7 days,
        # matching this codebase's "every Nh" schedule DSL (no dedicated "every Nd").
        await _seed_minutes_schedule(
            db, handler_name="graph_reconciliation", schedule="every 168h",
            interval_minutes=10080,
        )
```

- [ ] **Step 3: Verify the wiring compiles and the new handler is reachable**

Run: `uv run python -c "import stackowl.scheduler.assembly"`
Expected: no import errors

Run: `uv run pytest tests/scheduler/test_no_dummy_schedulers.py -v`
Expected: PASS (this file audits that every registered handler either has a seeded row or
declares `on_demand`/`event` — confirms `graph_reconciliation` isn't flagged dangling)

- [ ] **Step 4: Run the full set of this plan's new tests together (final regression check)**

Run: `uv run pytest tests/memory/test_kuzu_owl_skill_trait_sync.py tests/memory/test_kuzu_adapter_owl_skill_trait.py tests/skills/test_synthesizer_graph_sync.py tests/owls/test_evolution_graph_sync.py tests/scheduler/test_graph_reconciliation.py tests/scheduler/test_no_dummy_schedulers.py -v`
Expected: PASS (all)

- [ ] **Step 5: Lint + type-check the one remaining touched file**

Run: `uv run ruff check src/stackowl/scheduler/assembly.py`
Run: `uv run mypy src/stackowl/scheduler/assembly.py`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/scheduler/assembly.py
git commit -m "feat(scheduler): register + seed graph_reconciliation weekly"
```

---

## Plan Self-Review Notes

- **Spec coverage:** every item in the sub-project 1 spec's Implementation Surface table
  (§5) maps to a task here: `kuzu_adapter.py`/`kuzu_helpers.py` → Tasks 1-2;
  `owls/skill_ownership.py`'s hook → relocated to `synthesizer.py`'s `_attach_to_owner`
  (the actual call site where both live-overlay and durable-persist happen together —
  `attach_skill_to_owl` itself is a plain sync function with no async boundary to hook)
  → Task 3; `owls/evolution.py` → Task 4; `graph_reconciliation.py` (new) +
  `scheduler/assembly.py` → Tasks 5-6.
- **Verified, not assumed:** Kuzu's relationship `MERGE` support (needed for idempotent
  `OWNS`/`HAS_TRAIT` edges, unlike the existing `CREATE`-based `MENTIONS`/`RELATED_TO`)
  and `DETACH DELETE` were both confirmed against the actual installed Kuzu version
  before writing this plan, not assumed from documentation.
- **Type consistency:** `skill_id` is `f"{owner_id}::{name}"` and `trait_id` is
  `f"{owl_name}::{trait_name}"` everywhere this plan constructs one — Tasks 1, 2, 3, 4,
  and 5 all use this exact composition, never a bare name or a different separator.
