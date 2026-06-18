# Durable Delegated Children (Story D1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend durability across the delegation seam so a write-capable child runs exactly-once across timeout + process-crash, the parent's overall goal completes end-to-end after a crash, and the parent's timeout answer is grounded in what it can witness (per-effect `commit_coupling`) without re-introducing the overclaim Story D removed.
**Architecture:** When a durable parent (`state.task_id` set) delegates, the child runs as its own durable sub-task whose id is derived from the parent's resume-stable `delegate_task` ledger key (`child_task_id = derive_child_task_id(delegate_key)`). The parent claims-or-creates the child `tasks` row with a single-owner lease, sets `task_id=child_task_id` on the child `PipelineState` (the execute step assembles the `DurableSession` inline from `task_id`+`db_pool`), terminalizes the child as a projection of its own ledger commit, and resolves the `delegate_task` answer by `commit_coupling`. Recovery resumes roots only (`parent_task_id IS NULL`), reconstructs depth from the `parent_task_id` chain, and reaps zombie children. Non-durable parents are a complete no-op (fail-open).
**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, SQLite (aiosqlite), pytest

---

## Recon notes (signatures verified against live code — DO NOT re-guess)

- `idempotency_key(task_id: str, step_index: int, tool_name: str, args: dict[str, Any]) -> str` is the module-level fn in `pipeline/durable/ledger.py:64`. It is ALSO exposed as `SideEffectLedger.idempotency_key(...)` (staticmethod, owner-agnostic, `ledger.py:100`). **Reuse the module-level `idempotency_key`** for `delegate_key` (it is the owner-AGNOSTIC logical identity; the per-owner storage key `_owned_key` is private and must NOT be used for the child id — the child id is owner-namespaced separately by the `tasks` PK `(owner_id, task_id)`).
- The active durable context is `get_active() -> DurableReActContext | None` in `pipeline/durable/context.py:67` (NOT `ledger_guard.get_active` — the task brief said `ledger_guard.get_active()`; the real import lives in `context`). `DurableReActContext` has `.task_id: str`, `.owner_id: str`, `.ledger`, `.iteration: int` (`context.py:42-57`). Read `ctx.iteration` and `ctx.task_id` from `get_active()`.
- `DbPool.execute_returning_rowcount(sql, params=()) -> int` exists (`db/pool.py:198`) — use for ON CONFLICT DO NOTHING + CAS.
- `OwnedRepository._fetch_owned(table, where_sql="", params=())`, `_execute_owned(sql, params)` (requires `owner_id` literally in the SQL or it raises ValueError), `_insert_owned(table, columns)` (`tenancy/owned_repository.py:78/121/163`). `DEFAULT_PRINCIPAL_ID = "principal-default"` (`tenancy/principal.py:25`).
- `PipelineState` has `task_id: str | None = None` (`state.py:61`), `durable_owner_id: str | None = None` (`state.py:85`), `durable_resume_*` trio, and `evolve(**kwargs)` (`state.py:124`).
- `_run_specialist` (`owls/a2a_delegation.py:222`) builds `sub_state = parent_state.evolve(owl_name=to_owl, input_text=sub_task, responses=(), tool_calls=(), errors=(), pipeline_step="dispatch", interactive=False, delegation_depth=parent_state.delegation_depth+1, delegation_chain=parent_state.delegation_chain+(to_owl,))` — note it does NOT reset `task_id`, so a durable parent's child WOULD inherit the parent's `task_id` unless the parent's `parent_state` already carries `task_id=child_task_id`. D1 sets `task_id=child_task_id` on the `parent_state` built at `delegate_task.py:378` (the `PipelineState(...)` constructor there sets NO `task_id` today).
- `delegate_task.py:378` builds the per-ladder `parent_state` via `PipelineState(trace_id=..., session_id=..., input_text=sub_task, channel=..., owl_name=caller, pipeline_step="dispatch", delegation_depth=depth, delegation_chain=chain, creation_ceiling=child_floor(...))`. The reused `parent_state` flows into every `_attempt(to_owl)` → `delegator.delegate(..., parent_state=parent_state)`.
- Honest-terminal gate today: `delegate_task.py:457-547` — `_can_side_effect(target)` true ⇒ `honest_offtopic_write_result`/`honest_uncertain_result`; false ⇒ retry/fallback ladder. Builders in `tools/agents/results.py`: `honest_uncertain_result(target, t0)`, `honest_offtopic_write_result(target, t0)`, `ok_result(record, t0, *, note)`, `recovered_result(t0, *, original, via, result)`.
- Migration runner `_split_sql` splits on `;` — **no semicolons in comments**. ADD COLUMN is version-gated idempotent (mirror `0048_*.sql`). Test mirror: `tests/db/test_migration_0045_durable_tasks.py`.
- `AsyncioBackend.run` (`backends/asyncio_backend.py:42`) calls `TraceContext.start(state.session_id, trace_id=..., interactive=..., channel=..., delegation_depth=..., delegation_chain=..., owl_name=..., creation_ceiling=...)`. `TraceContext.start` signature + `_TraceToken` NamedTuple + `reset` + `get()` are in `infra/trace.py`.

---

## Task 1 — Migration 0053: durable-delegation link columns + index

**Files:**
- Create: `v2/src/stackowl/db/migrations/0053_durable_delegation_link.sql`
- Create: `v2/tests/db/test_migration_0053_durable_delegation_link.py`

**Steps:**

- [ ] Write the failing test `v2/tests/db/test_migration_0053_durable_delegation_link.py`:

```python
"""Migration 0053 — durable-delegation link columns on tasks (Story D1).

Adds parent_task_id / parent_owl / delegate_key / lease_owner / superseded plus
the idx_tasks_parent index. Mirrors the 0045 verification style: columns present
via PRAGMA table_info, index present, and a no-op re-run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from stackowl.db.migrations.runner import MigrationRunner

_NEW_COLUMNS = {
    "parent_task_id", "parent_owl", "delegate_key", "lease_owner", "superseded",
}


def _migrate(tmp_path: Path) -> Path:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    return db_path


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608


def test_link_columns_added(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        assert _NEW_COLUMNS <= _columns(conn, "tasks")
    finally:
        conn.close()


def test_superseded_defaults_to_zero(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, goal, status, created_at, updated_at) "
            "VALUES ('t1', 'g', 'pending', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT superseded, parent_task_id FROM tasks WHERE task_id = 't1'"
        ).fetchone()
        assert row[0] == 0
        assert row[1] is None
    finally:
        conn.close()


def test_idx_tasks_parent_created(tmp_path: Path) -> None:
    db_path = _migrate(tmp_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tasks'"
        ).fetchall()
        assert "idx_tasks_parent" in {r[0] for r in rows}
    finally:
        conn.close()


def test_migration_0053_rerun_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    MigrationRunner(db_path=db_path).run()
    results = MigrationRunner(db_path=db_path).run()
    applied = [r for r in results if r.action == "applied"]
    assert applied == [], f"re-run applied migrations: {applied}"
    rec = next(r for r in results if r.version == "0053")
    assert rec.action == "skipped"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/db/test_migration_0053_durable_delegation_link.py -v` — expect collection/run failure: the `0053` migration file does not exist so `next(r for r in results if r.version == "0053")` raises `StopIteration` and the column asserts fail (`KeyError`/`AssertionError`).

- [ ] Minimal implementation `v2/src/stackowl/db/migrations/0053_durable_delegation_link.sql` (NO semicolons in comments):

```sql
-- Migration 0053 durable-delegation link columns on tasks (Story D1).
--
-- Adds the columns that let a durable parent task link to a durable child
-- sub-task spawned through delegate_task, plus a single-owner execution lease
-- and a supersession tombstone.
--
-- parent_task_id
--   NULL means a root goal. Non-NULL links to the parent task_id (self
--   referential within tasks). Children are resumed transitively by the parent
--   re-deriving the same child id, so recovery lists roots only and filters on
--   this column being NULL.
--
-- parent_owl
--   The delegating owl name (audit plus return-path legibility). NULL for roots.
--
-- delegate_key
--   The parent delegate_task idempotency key this child was minted from. Audit
--   plus reaper correlation. NULL for roots.
--
-- lease_owner
--   Single-owner execution lease holder. NULL means unclaimed. The claim winner
--   executes the child, a non-winner polls the durable record.
--
-- superseded
--   Set to 1 when a timed-out child is tombstoned so a slow eventual commit is
--   neutralized and the next ladder rung gets a fresh id. Default 0.
--
-- Idempotent: the MigrationRunner records applied versions in schema_migrations
-- and skips a version already recorded. SQLite has no ADD COLUMN IF NOT EXISTS,
-- so the runner version gate is the idempotency mechanism. NOTE no semicolons
-- inside comments per the runner split-on-semicolon gotcha.

ALTER TABLE tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE tasks ADD COLUMN parent_owl TEXT;
ALTER TABLE tasks ADD COLUMN delegate_key TEXT;
ALTER TABLE tasks ADD COLUMN lease_owner TEXT;
ALTER TABLE tasks ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(owner_id, parent_task_id);
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/db/test_migration_0053_durable_delegation_link.py -v` — all 4 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/db/migrations/0053_durable_delegation_link.sql v2/tests/db/test_migration_0053_durable_delegation_link.py && git commit -m "feat(v2): durable-delegation link schema — migration 0053 (D1 §4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 2 — DurableTask model + store round-trip of the 5 link fields

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/task.py` (add 5 fields after `task_envelope`, ~line 62)
- Modify: `v2/src/stackowl/pipeline/durable/store.py` (`_SELECT_FIELDS` ~line 25, `create()` insert dict ~line 80, `_row_to_task` ~line 344)
- Create: `v2/tests/pipeline/durable/test_durable_task_link_fields.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/durable/test_durable_task_link_fields.py`:

```python
"""DurableTask link fields round-trip through the store (Story D1 §4)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def test_link_fields_default_to_root() -> None:
    now = datetime.now(tz=UTC)
    t = DurableTask(
        task_id="t", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="pending",
        created_at=now, updated_at=now,
    )
    assert t.parent_task_id is None
    assert t.parent_owl is None
    assert t.delegate_key is None
    assert t.lease_owner is None
    assert t.superseded is False


async def test_child_link_fields_round_trip(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    await store.create(
        DurableTask(
            task_id="child-1", owner_id=DEFAULT_PRINCIPAL_ID, goal="sub", status="running",
            parent_task_id="parent-1", parent_owl="secretary", delegate_key="dk-abc",
            lease_owner="lease-holder", superseded=True,
            created_at=now, updated_at=now,
        )
    )
    got = await store.get("child-1")
    assert got.parent_task_id == "parent-1"
    assert got.parent_owl == "secretary"
    assert got.delegate_key == "dk-abc"
    assert got.lease_owner == "lease-holder"
    assert got.superseded is True
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_durable_task_link_fields.py -v` — expect failure: `DurableTask` rejects unknown kwargs (`parent_task_id`) and `_row_to_task` does not populate them.

- [ ] Implement — add fields to `DurableTask` in `task.py` after the `task_envelope` field (~line 62):

```python
    #: Link to the parent durable task when this task is a delegated child (D1).
    #: NULL ⇒ a root goal; non-NULL ⇒ a child spawned through delegate_task.
    parent_task_id: str | None = None
    #: The delegating owl name (audit + return-path legibility). NULL for roots.
    parent_owl: str | None = None
    #: The parent's delegate_task idempotency key this child was minted from
    #: (D1 §5; audit + reaper). NULL for roots.
    delegate_key: str | None = None
    #: Single-owner execution lease holder (D1 §7). NULL ⇒ unclaimed.
    lease_owner: str | None = None
    #: True when a timed-out child was tombstoned so a slow eventual commit is
    #: neutralized and the next ladder rung gets a fresh id (D1 §9).
    superseded: bool = False
```

- [ ] Implement — `store.py` `_SELECT_FIELDS` (~line 25), append the new columns:

```python
_SELECT_FIELDS = (
    "task_id, owner_id, goal, status, current_step, "
    "thread_id, result, owl_name, channel, creation_ceiling, task_envelope, "
    "parent_task_id, parent_owl, delegate_key, lease_owner, superseded, "
    "created_at, updated_at"
)
```

- [ ] Implement — `store.py` `create()` insert dict (~line 80), add after the `task_envelope` entry:

```python
            "parent_task_id": task.parent_task_id,
            "parent_owl": task.parent_owl,
            "delegate_key": task.delegate_key,
            "lease_owner": task.lease_owner,
            "superseded": 1 if task.superseded else 0,
```

- [ ] Implement — `store.py` `_row_to_task` (~line 344), add before the `created_at=` line:

```python
    raw_parent = row.get("parent_task_id")
    raw_parent_owl = row.get("parent_owl")
    raw_delegate_key = row.get("delegate_key")
    raw_lease = row.get("lease_owner")
    raw_superseded = row.get("superseded")
```

and in the `DurableTask(...)` constructor add:

```python
        parent_task_id=None if raw_parent is None else str(raw_parent),
        parent_owl=None if raw_parent_owl is None else str(raw_parent_owl),
        delegate_key=None if raw_delegate_key is None else str(raw_delegate_key),
        lease_owner=None if raw_lease is None else str(raw_lease),
        superseded=bool(raw_superseded),
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_durable_task_link_fields.py tests/db/test_migration_0045_durable_tasks.py -v` — new tests pass; 0045 still green.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/task.py v2/src/stackowl/pipeline/durable/store.py v2/tests/pipeline/durable/test_durable_task_link_fields.py && git commit -m "feat(v2): DurableTask link fields + store round-trip (D1 §4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 3 — `derive_child_task_id` + reuse the ledger idempotency-key fn

**Files:**
- Create: `v2/src/stackowl/pipeline/durable/delegation_link.py`
- Create: `v2/tests/pipeline/durable/test_delegation_link.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/durable/test_delegation_link.py`:

```python
"""derive_child_task_id determinism (Story D1 §5)."""

from __future__ import annotations

from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import idempotency_key


def test_same_delegate_key_gives_same_child_id() -> None:
    dk = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do x"})
    assert derive_child_task_id(dk) == derive_child_task_id(dk)


def test_different_delegate_keys_give_different_child_ids() -> None:
    dk1 = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do x"})
    dk2 = idempotency_key("parent-1", 3, "delegate_task", {"goal": "do y"})
    assert derive_child_task_id(dk1) != derive_child_task_id(dk2)


def test_child_id_is_a_stable_prefixed_string() -> None:
    dk = idempotency_key("parent-1", 0, "delegate_task", {"goal": "g"})
    cid = derive_child_task_id(dk)
    assert cid.startswith("child-")
    assert len(cid) == len("child-") + 32
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_delegation_link.py -v` — expect `ImportError` (module/function does not exist).

- [ ] Implement `v2/src/stackowl/pipeline/durable/delegation_link.py`:

```python
"""delegation_link — pure helpers tying a durable parent to its durable children.

Two pure functions, no I/O:

* :func:`derive_child_task_id` — the child task id is a deterministic function of
  the parent's own resume-stable ``delegate_task`` ledger idempotency key
  (``delegate_key``). A re-sampled parent that re-emits the same delegation at the
  same iteration with the same args computes the same ``delegate_key`` (via
  :func:`stackowl.pipeline.durable.ledger.idempotency_key`) → the same
  ``child_task_id`` → it re-attaches to the existing child row instead of forking
  (D1 §5). This inherits the base ledger's exactly-once semantics verbatim.
* :func:`ancestor_depth` — reconstructs delegation depth by walking the
  ``parent_task_id`` chain, so a resumed interior node does not start its depth
  counter at 0 (D1 §9). Takes a pure lookup callable so it never touches the DB.
"""

from __future__ import annotations

from collections.abc import Callable

from stackowl.infra.observability import log

#: Length of the delegate_key prefix folded into the child id (sha256 hexdigest
#: is 64 chars; 32 keeps the id short while remaining collision-free across
#: distinct delegate_keys for the table sizes D1 targets).
_CHILD_ID_KEY_PREFIX = 32


def derive_child_task_id(delegate_key: str) -> str:
    """Return the deterministic child task id for a parent ``delegate_key``.

    Pure: the same ``delegate_key`` always yields the same id; distinct keys
    yield distinct ids (the prefix is taken from a sha256 hexdigest, so a
    32-char prefix is collision-free for distinct keys at the scales D1 targets).
    """
    child_id = f"child-{delegate_key[:_CHILD_ID_KEY_PREFIX]}"
    log.tasks.debug(
        "[tasks] delegation_link.derive_child_task_id",
        extra={"_fields": {"delegate_key_prefix": delegate_key[:8], "child_id": child_id}},
    )
    return child_id
```

(`ancestor_depth` is added in Task 4 to keep each commit one logical change; this commit ships only `derive_child_task_id`.)

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_delegation_link.py -v` — 3 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/delegation_link.py v2/tests/pipeline/durable/test_delegation_link.py && git commit -m "feat(v2): derive_child_task_id from parent delegate_key (D1 §5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 4 — `ancestor_depth` helper (depth-from-tree)

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/delegation_link.py` (add `ancestor_depth`)
- Modify: `v2/tests/pipeline/durable/test_delegation_link.py` (append depth tests)

**Steps:**

- [ ] Write the failing test — append to `v2/tests/pipeline/durable/test_delegation_link.py`:

```python
def test_ancestor_depth_root_is_zero() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    # root has no parent
    assert ancestor_depth("root", lambda _tid: None) == 0


def test_ancestor_depth_child_is_one() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    parents = {"child": "root", "root": None}
    assert ancestor_depth("child", parents.get) == 1


def test_ancestor_depth_grandchild_is_two() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    parents = {"grand": "child", "child": "root", "root": None}
    assert ancestor_depth("grand", parents.get) == 2


def test_ancestor_depth_breaks_on_cycle_defensively() -> None:
    from stackowl.pipeline.durable.delegation_link import ancestor_depth
    # A pathological self-cycle must not loop forever — it is bounded.
    parents = {"a": "b", "b": "a"}
    depth = ancestor_depth("a", parents.get)
    assert isinstance(depth, int)
    assert depth >= 0
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_delegation_link.py -v` — expect `ImportError` on `ancestor_depth`.

- [ ] Implement — append to `delegation_link.py`:

```python
#: Hard ceiling on the ancestor walk so a malformed parent chain (a cycle from a
#: corrupted row) can never spin forever. Far above MAX_DELEGATION_DEPTH=2.
_MAX_ANCESTOR_WALK = 64


def ancestor_depth(
    task_id: str,
    parent_of: Callable[[str], str | None],
) -> int:
    """Count delegation ancestors of ``task_id`` via the parent chain.

    ``parent_of(tid)`` returns the ``parent_task_id`` of ``tid`` (or ``None`` for
    a root / unknown id). A root returns 0, its child 1, a grandchild 2. Pure:
    the caller supplies the lookup so this never touches the DB. Bounded by
    :data:`_MAX_ANCESTOR_WALK` and a visited-set so a corrupted cyclic chain
    terminates loudly instead of looping.
    """
    depth = 0
    seen: set[str] = {task_id}
    current = parent_of(task_id)
    while current is not None and depth < _MAX_ANCESTOR_WALK:
        depth += 1
        if current in seen:
            log.tasks.error(
                "[tasks] delegation_link.ancestor_depth: cycle in parent chain — stopping",
                extra={"_fields": {"task_id": task_id, "cycle_at": current, "depth": depth}},
            )
            break
        seen.add(current)
        current = parent_of(current)
    log.tasks.debug(
        "[tasks] delegation_link.ancestor_depth: exit",
        extra={"_fields": {"task_id": task_id, "depth": depth}},
    )
    return depth
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_delegation_link.py -v` — 7 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/delegation_link.py v2/tests/pipeline/durable/test_delegation_link.py && git commit -m "feat(v2): ancestor_depth depth-from-tree helper (D1 §9)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 5 — `create_child_task` (claim-or-create, ON CONFLICT DO NOTHING)

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/store.py` (new method, near `claim_for_recovery` ~line 216)
- Create: `v2/tests/pipeline/durable/test_store_child_lifecycle.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/durable/test_store_child_lifecycle.py`:

```python
"""DurableTaskStore child-lifecycle methods (Story D1 §7)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_create_child_task_returns_record(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    rec = await store.create_child_task(
        child_task_id="child-1", parent_task_id="parent-1", parent_owl="secretary",
        delegate_key="dk-1", goal="sub", owl_name="scout", channel="cli",
    )
    assert rec.task_id == "child-1"
    assert rec.parent_task_id == "parent-1"
    assert rec.parent_owl == "secretary"
    assert rec.delegate_key == "dk-1"
    assert rec.status == "running"


async def test_create_child_task_is_idempotent_under_race(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)

    async def _create() -> str:
        rec = await store.create_child_task(
            child_task_id="child-x", parent_task_id="parent-1", parent_owl="secretary",
            delegate_key="dk-x", goal="sub", owl_name="scout", channel="cli",
        )
        return rec.task_id

    a, b = await asyncio.gather(_create(), _create())
    assert a == b == "child-x"
    rows = await pool.fetch_all("SELECT task_id FROM tasks WHERE task_id = 'child-x'", ())
    assert len(rows) == 1, f"exactly one row expected, got {rows}"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py -v` — expect `AttributeError: 'DurableTaskStore' object has no attribute 'create_child_task'`.

- [ ] Implement — add to `store.py` after `claim_for_recovery` (~line 266). Note: it builds the INSERT directly (NOT `_insert_owned`, which builds a plain INSERT with no conflict clause). It stamps `owner_id` from the bound owner explicitly:

```python
    async def create_child_task(
        self,
        *,
        child_task_id: str,
        parent_task_id: str,
        parent_owl: str,
        delegate_key: str,
        goal: str,
        owl_name: str,
        channel: str,
    ) -> DurableTask:
        """Claim-or-create a delegated child task row, then return it (D1 §7.1).

        ``INSERT ... ON CONFLICT(owner_id, task_id) DO NOTHING`` so two racers
        (a live parent + startup recovery) deriving the same deterministic id
        produce exactly ONE row — the loser's INSERT is a no-op. Both callers
        then re-``get`` the SAME record. This is distinct from the root-task
        INSERT (a duplicate root id IS a bug we want surfaced); never reuse
        :meth:`create` for children.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.create_child_task: entry",
            extra={"_fields": {
                "child_task_id": child_task_id, "parent_task_id": parent_task_id,
                "owner_id": self._owner_id, "parent_owl": parent_owl,
            }},
        )
        now = datetime.now(tz=UTC).isoformat()
        sql = (
            "INSERT INTO tasks "  # noqa: S608 — columns are literals
            "(task_id, owner_id, goal, status, current_step, parent_task_id, "
            "parent_owl, delegate_key, owl_name, channel, superseded, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, 'running', 0, ?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(owner_id, task_id) DO NOTHING"
        )
        params = [
            child_task_id, self._owner_id, goal, parent_task_id, parent_owl,
            delegate_key, owl_name, channel, now, now,
        ]
        # 2. DECISION — DO NOTHING means a row already exists; either way re-SELECT.
        affected = await self._db.execute_returning_rowcount(sql, params)
        # 3. STEP — read back the canonical record (winner's or pre-existing).
        record = await self.get(child_task_id)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.create_child_task: exit",
            extra={"_fields": {
                "child_task_id": child_task_id, "created": affected == 1,
                "owner_id": self._owner_id,
            }},
        )
        return record
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py -v` — 2 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/store.py v2/tests/pipeline/durable/test_store_child_lifecycle.py && git commit -m "feat(v2): create_child_task claim-or-create (D1 §7.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 6 — `claim_child_lease` (single-owner CAS)

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/store.py` (new method after `create_child_task`)
- Modify: `v2/tests/pipeline/durable/test_store_child_lifecycle.py` (append)

**Steps:**

- [ ] Write the failing test — append to `test_store_child_lifecycle.py`:

```python
async def test_claim_child_lease_first_wins_second_loses(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="child-l", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk-l", goal="sub", owl_name="scout", channel="cli",
    )
    first = await store.claim_child_lease("child-l", lease_owner="live-parent")
    second = await store.claim_child_lease("child-l", lease_owner="recovery")
    assert first is True
    assert second is False
    rec = await store.get("child-l")
    assert rec.lease_owner == "live-parent"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py::test_claim_child_lease_first_wins_second_loses -v` — expect `AttributeError: ... 'claim_child_lease'`.

- [ ] Implement — add to `store.py` after `create_child_task`:

```python
    async def claim_child_lease(self, task_id: str, *, lease_owner: str) -> bool:
        """Atomically claim the single-owner execution lease for a child (D1 §7.1).

        CAS: ``UPDATE tasks SET lease_owner=? WHERE owner_id=? AND task_id=? AND
        lease_owner IS NULL``. Returns True iff THIS call won (rows-affected == 1).
        The winner executes the child; a loser polls the durable record. Mirrors
        :meth:`claim_for_recovery`'s direct-SQL CAS bypass.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.claim_child_lease: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "lease_owner": lease_owner,
            }},
        )
        sql = (
            f"UPDATE {self._table} SET lease_owner = ?, updated_at = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ? AND lease_owner IS NULL"
        )
        params = [
            lease_owner, datetime.now(tz=UTC).isoformat(), self._owner_id, task_id,
        ]
        # 3. STEP — atomic CAS; rows-affected reveals the race winner.
        affected = await self._db.execute_returning_rowcount(sql, params)
        claimed = affected == 1
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.claim_child_lease: exit",
            extra={"_fields": {
                "task_id": task_id, "claimed": claimed, "rows_affected": affected,
            }},
        )
        return claimed
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py -v` — 3 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/store.py v2/tests/pipeline/durable/test_store_child_lifecycle.py && git commit -m "feat(v2): claim_child_lease single-owner CAS (D1 §7.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 7 — `terminalize_child` + `list_children` + zombie-children reaper query

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/store.py` (3 new methods after `claim_child_lease`)
- Modify: `v2/tests/pipeline/durable/test_store_child_lifecycle.py` (append)

**Steps:**

- [ ] Write the failing test — append to `test_store_child_lifecycle.py`:

```python
async def test_terminalize_child_sets_terminal_status(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="child-t", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk-t", goal="sub", owl_name="scout", channel="cli",
    )
    await store.terminalize_child("child-t", "completed", result="answer")
    rec = await store.get("child-t")
    assert rec.status == "completed"
    assert rec.result == "answer"


async def test_list_children_returns_only_that_parents_children(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="c-a", parent_task_id="P", parent_owl="secretary",
        delegate_key="dk-a", goal="a", owl_name="scout", channel="cli",
    )
    await store.create_child_task(
        child_task_id="c-b", parent_task_id="OTHER", parent_owl="secretary",
        delegate_key="dk-b", goal="b", owl_name="scout", channel="cli",
    )
    kids = await store.list_children("P")
    assert {k.task_id for k in kids} == {"c-a"}


async def test_zombie_children_under_terminal_parents(pool: DbPool) -> None:
    from datetime import UTC, datetime

    from stackowl.pipeline.durable.task import DurableTask

    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    # Terminal parent.
    await store.create(DurableTask(
        task_id="P", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="completed",
        created_at=now, updated_at=now,
    ))
    # A still-running child under the terminal parent ⇒ a zombie.
    await store.create_child_task(
        child_task_id="zombie", parent_task_id="P", parent_owl="secretary",
        delegate_key="dk-z", goal="sub", owl_name="scout", channel="cli",
    )
    # A child under a still-running parent ⇒ NOT a zombie.
    await store.create(DurableTask(
        task_id="P2", owner_id=DEFAULT_PRINCIPAL_ID, goal="g", status="running",
        created_at=now, updated_at=now,
    ))
    await store.create_child_task(
        child_task_id="live-kid", parent_task_id="P2", parent_owl="secretary",
        delegate_key="dk-lk", goal="sub", owl_name="scout", channel="cli",
    )
    zombies = await store.list_zombie_children()
    assert {z.task_id for z in zombies} == {"zombie"}
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py -v` — expect `AttributeError` on `terminalize_child` / `list_children` / `list_zombie_children`.

- [ ] Implement — add to `store.py` after `claim_child_lease`:

```python
    async def terminalize_child(
        self, task_id: str, status: TaskStatus, *, result: str | None = None,
    ) -> None:
        """Stamp a child task terminal as a projection of the parent's commit (D1 §7.2).

        The child's terminal status is written by the PARENT when it commits its
        delegate_task ledger entry — not by the child about itself. Thin wrapper
        over the owner-scoped status UPDATE so the call-site reads intentionally.
        """
        # 1. ENTRY
        log.tasks.debug(
            "[tasks] store.terminalize_child: entry",
            extra={"_fields": {
                "task_id": task_id, "owner_id": self._owner_id, "status": status,
            }},
        )
        await self.update_status(task_id, status, result=result)
        # 4. EXIT
        log.tasks.info(
            "[tasks] store.terminalize_child: exit",
            extra={"_fields": {"task_id": task_id, "status": status}},
        )

    async def list_children(self, parent_task_id: str) -> list[DurableTask]:
        """All child tasks of ``parent_task_id`` for the bound owner (D1 §7)."""
        log.tasks.debug(
            "[tasks] store.list_children: entry",
            extra={"_fields": {"parent_task_id": parent_task_id, "owner_id": self._owner_id}},
        )
        rows = await self._fetch_owned(
            self._table, "parent_task_id = ?", (parent_task_id,)
        )
        kids = [_row_to_task(r) for r in rows]
        log.tasks.debug(
            "[tasks] store.list_children: exit",
            extra={"_fields": {"parent_task_id": parent_task_id, "count": len(kids)}},
        )
        return kids

    async def list_zombie_children(self) -> list[DurableTask]:
        """Running/recovering children whose parent is already terminal (D1 §7.3).

        These are unreachable by transitive resolution (the parent will never
        re-delegate), so the reaper marks them failed/abandoned. Owner-scoped
        self-join on the tasks table.
        """
        log.tasks.debug(
            "[tasks] store.list_zombie_children: entry",
            extra={"_fields": {"owner_id": self._owner_id}},
        )
        sql = (
            "SELECT child.* FROM tasks child "  # noqa: S608 — literals only
            "JOIN tasks parent "
            "ON parent.owner_id = child.owner_id "
            "AND parent.task_id = child.parent_task_id "
            "WHERE child.owner_id = ? "
            "AND child.parent_task_id IS NOT NULL "
            "AND child.status IN ('running', 'recovering') "
            "AND parent.status IN ('completed', 'failed')"
        )
        rows = await self._db.fetch_all(sql, (self._owner_id,))
        zombies = [_row_to_task(r) for r in rows]
        log.tasks.info(
            "[tasks] store.list_zombie_children: exit",
            extra={"_fields": {"owner_id": self._owner_id, "count": len(zombies)}},
        )
        return zombies
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_store_child_lifecycle.py -v` — 6 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/store.py v2/tests/pipeline/durable/test_store_child_lifecycle.py && git commit -m "feat(v2): terminalize_child + list_children + zombie reaper query (D1 §7)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 8 — TraceContext durable scope (`_task_id` / `_durable_owner_id`)

**Files:**
- Modify: `v2/src/stackowl/infra/trace.py` (`_TraceToken`, ContextVars, `start`, `reset`, `get`, new `durable_owner_id` accessor)
- Create: `v2/tests/infra/test_trace_durable_scope.py`

**Steps:**

- [ ] Write the failing test `v2/tests/infra/test_trace_durable_scope.py`:

```python
"""TraceContext carries durable scope (task_id / durable_owner_id) — Story D1 §8.1."""

from __future__ import annotations

from stackowl.infra.trace import TraceContext


def test_get_exposes_task_id_when_started() -> None:
    token = TraceContext.start(
        "sess", trace_id="tr", task_id="child-7", durable_owner_id="owner-a",
    )
    try:
        assert TraceContext.get()["task_id"] == "child-7"
        assert TraceContext.durable_owner_id() == "owner-a"
    finally:
        TraceContext.reset(token)


def test_task_id_is_none_when_absent() -> None:
    token = TraceContext.start("sess", trace_id="tr")
    try:
        assert TraceContext.get()["task_id"] is None
        assert TraceContext.durable_owner_id() is None
    finally:
        TraceContext.reset(token)


def test_reset_restores_prior_durable_scope() -> None:
    outer = TraceContext.start("s", trace_id="t", task_id="parent", durable_owner_id="o")
    try:
        inner = TraceContext.start("s", trace_id="t", task_id="child", durable_owner_id="o")
        TraceContext.reset(inner)
        assert TraceContext.get()["task_id"] == "parent"
    finally:
        TraceContext.reset(outer)
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/infra/test_trace_durable_scope.py -v` — expect `TypeError` (`start` got unexpected kwarg `task_id`) / `AttributeError` (`durable_owner_id`).

- [ ] Implement — `trace.py`. Add to `_TraceToken` NamedTuple (after `creation_ceiling`):

```python
    task_id: Token[str | None]
    durable_owner_id: Token[str | None]
```

Add the ContextVars after `_delegation_chain` (~line 57):

```python
    # D1 §8.1 — the durable task being driven by the current (sub-)pipeline, and
    # its owning principal. delegate_task reads these off TraceContext to decide
    # durable-vs-fail-open. ONLY the fail-open durability signal rides the
    # ContextVar (safe to lose: you degrade to the non-durable path); the
    # identity-determining child id is computed explicitly, never inferred from
    # this ambient state. Default None ⇒ non-durable turn ⇒ D1 is a no-op.
    _task_id: ContextVar[str | None] = ContextVar("durable_task_id", default=None)
    _durable_owner_id: ContextVar[str | None] = ContextVar(
        "durable_owner_id", default=None
    )
```

Add params to `start` (after `creation_ceiling`):

```python
        task_id: str | None = None,
        durable_owner_id: str | None = None,
```

Add to the `_TraceToken(...)` return:

```python
            task_id=cls._task_id.set(task_id),
            durable_owner_id=cls._durable_owner_id.set(durable_owner_id),
```

Add to `reset`:

```python
        cls._task_id.reset(token.task_id)
        cls._durable_owner_id.reset(token.durable_owner_id)
```

Add a `durable_owner_id` classmethod accessor (next to `creation_ceiling`):

```python
    @classmethod
    def durable_owner_id(cls) -> str | None:
        """The owning principal of the durable task driving this (sub-)pipeline.

        Read by delegate_task alongside ``get()["task_id"]`` to assemble the
        child's durable scope. Kept off :meth:`get` is unnecessary — it is a safe
        string — but it has a dedicated accessor to mirror ``task_id`` being in
        ``get`` and ``durable_owner_id`` here, used at the seam, not in logs.
        """
        return cls._durable_owner_id.get()
```

Add `task_id` to the `get()` dict:

```python
            "task_id": cls._task_id.get(),
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/infra/test_trace_durable_scope.py -v` — 3 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/infra/trace.py v2/tests/infra/test_trace_durable_scope.py && git commit -m "feat(v2): TraceContext durable scope (task_id/durable_owner_id) (D1 §8.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 9 — AsyncioBackend stamps durable scope into TraceContext

**Files:**
- Modify: `v2/src/stackowl/pipeline/backends/asyncio_backend.py` (`run()` `TraceContext.start(...)` call ~line 42)
- Create: `v2/tests/pipeline/backends/test_asyncio_backend_durable_scope.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/backends/test_asyncio_backend_durable_scope.py`:

```python
"""AsyncioBackend stamps state.task_id/durable_owner_id into TraceContext — D1 §8.1."""

from __future__ import annotations

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

_observed: dict[str, object] = {}


async def _probe_step(state: PipelineState) -> PipelineState:
    _observed["task_id"] = TraceContext.get()["task_id"]
    _observed["owner"] = TraceContext.durable_owner_id()
    return state


async def test_backend_propagates_durable_scope(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe_step)])
    backend = AsyncioBackend(services=StepServices())
    state = PipelineState(
        trace_id="tr", session_id="s", input_text="hi", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
        task_id="child-9", durable_owner_id="owner-z",
    )
    await backend.run(state)
    assert _observed["task_id"] == "child-9"
    assert _observed["owner"] == "owner-z"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/backends/test_asyncio_backend_durable_scope.py -v` — expect `AssertionError`: observed `task_id` is `None` (not yet propagated).

- [ ] Implement — `asyncio_backend.py`, extend the `TraceContext.start(...)` call in `run()` (~line 42) with the two new kwargs:

```python
        trace_token = TraceContext.start(
            state.session_id,
            trace_id=state.trace_id,
            interactive=state.interactive,
            channel=state.channel,
            delegation_depth=state.delegation_depth,
            delegation_chain=state.delegation_chain,
            owl_name=state.owl_name,
            creation_ceiling=state.creation_ceiling,
            task_id=state.task_id,
            durable_owner_id=state.durable_owner_id,
        )
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/backends/test_asyncio_backend_durable_scope.py -v` — pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/backends/asyncio_backend.py v2/tests/pipeline/backends/test_asyncio_backend_durable_scope.py && git commit -m "feat(v2): AsyncioBackend stamps durable scope into TraceContext (D1 §8.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 10 — `commit_coupling` field on ToolManifest

**Files:**
- Modify: `v2/src/stackowl/tools/base.py` (`ToolManifest`, after `toolset_group` ~line 42)
- Create: `v2/tests/tools/test_manifest_commit_coupling.py`

**Steps:**

- [ ] Write the failing test `v2/tests/tools/test_manifest_commit_coupling.py`:

```python
"""ToolManifest.commit_coupling field — the honesty axis (Story D1 §6.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.tools.base import ToolManifest


def _manifest(**kw: object) -> ToolManifest:
    base = dict(name="t", description="d", parameters={})
    base.update(kw)
    return ToolManifest(**base)  # type: ignore[arg-type]


def test_commit_coupling_defaults_to_none() -> None:
    assert _manifest().commit_coupling is None


def test_commit_coupling_accepts_enum_values() -> None:
    for value in ("transactional", "idempotent_keyed", "unconfirmed"):
        assert _manifest(commit_coupling=value).commit_coupling == value


def test_commit_coupling_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        _manifest(commit_coupling="maybe")
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/test_manifest_commit_coupling.py -v` — expect failure: `extra="forbid"` rejects `commit_coupling`.

- [ ] Implement — `base.py`, add after `toolset_group` in `ToolManifest` (~line 42):

```python
    # D1 §6 — how tightly the tool's REAL-WORLD effect is coupled to our local
    # ledger commit. Decides definite-answer-vs-honest_uncertain after a durable
    # child times out / is recovered:
    #   "transactional"     — effect + ledger entry are atomic (L ⟺ E). "Committed
    #                         → done" is honest (e.g. a write to our own SQLite).
    #   "idempotent_keyed"  — effect is replay-safe under a key we own AND the
    #                         downstream contractually honors it (L ⟹ E).
    #   "unconfirmed"       — effect crosses a lossy-ack boundary (SMTP/POST/remote
    #                         FS/Telegram); L and E can diverge irreducibly.
    # None ⇒ undeclared. The resolver (delegate_task) treats undeclared write/
    # consequential tools as "unconfirmed" (fail-safe — never silently "safe").
    commit_coupling: Literal[
        "transactional", "idempotent_keyed", "unconfirmed"
    ] | None = None
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/test_manifest_commit_coupling.py -v` — 3 pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/tools/base.py v2/tests/tools/test_manifest_commit_coupling.py && git commit -m "feat(v2): commit_coupling axis on ToolManifest (D1 §6.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 11 — Declare commit_coupling per write/consequential tool + pin the map

**Files (Modify — one kwarg added to each manifest):**
- `v2/src/stackowl/tools/agents/delegate_task.py` (~:194) — `unconfirmed`
- `v2/src/stackowl/tools/agents/sessions_send.py` (~:125) — `unconfirmed`
- `v2/src/stackowl/tools/agents/sessions_spawn.py` (~:102) — `unconfirmed`
- `v2/src/stackowl/tools/interaction/batch_approve.py` (~:154) — `transactional`
- `v2/src/stackowl/tools/io/apply_patch.py` (~:127) — `transactional`
- `v2/src/stackowl/tools/io/edit.py` (~:103) — `transactional`
- `v2/src/stackowl/tools/io/undo_store.py` (~:348) — `transactional`
- `v2/src/stackowl/tools/io/write_file.py` (~:37) — `transactional`
- `v2/src/stackowl/tools/knowledge/memory.py` (~:125) — `transactional`
- `v2/src/stackowl/tools/process/process_tool.py` (~:85) — `unconfirmed`
- `v2/src/stackowl/tools/scheduling/cronjob.py` (~:108) — `transactional`
- `v2/src/stackowl/tools/system/shell.py` (~:390) — `unconfirmed`
- `v2/src/stackowl/tools/browser/browse.py` (~:135) — `unconfirmed`
- `v2/src/stackowl/tools/browser/dialog.py` (~:74) — `unconfirmed`
- `v2/src/stackowl/tools/code/execute_code.py` (~:119) — `unconfirmed`
- `v2/src/stackowl/tools/knowledge/skill_manage.py` (~:150) — `transactional`
- `v2/src/stackowl/tools/knowledge/synthesize_skills.py` (~:78) — `transactional`
- `v2/src/stackowl/tools/meta/owl_build.py` (~:141) — `transactional`
- `v2/src/stackowl/tools/meta/tool_build.py` (~:135) — `transactional`
- `v2/src/stackowl/tools/scheduling/send_file.py` (~:148) — `unconfirmed`
- `v2/src/stackowl/tools/scheduling/send_message.py` (~:128) — `unconfirmed`
- Create: `v2/tests/tools/test_commit_coupling.py`

**Classification rationale (closed enum, fail-safe):** local-fs / our-own-DB writes ⇒ `transactional`; anything that sends/posts/executes across a lossy-ack boundary (network sends, shell/process/code/browser side effects whose downstream we don't control) ⇒ `unconfirmed`. `delegate_task` is itself `unconfirmed` (its real effect is the child's effects, which it cannot atomically couple to its own ledger commit). No tool gets `idempotent_keyed` in D1 (no downstream contract is asserted yet — that's §12 backlog); leaving them `unconfirmed` is the honest default.

**Steps:**

- [ ] Write the failing test `v2/tests/tools/test_commit_coupling.py`:

```python
"""Pin the commit_coupling assignment of every write/consequential tool (D1 §6.1).

Undeclared write/consequential tools fail-safe to "unconfirmed" at resolution
time (see delegate_task), but this map pins the DECLARED couplings so a future
edit cannot silently re-classify an effect as more certain than it is.
"""

from __future__ import annotations

from stackowl.tools.registry import ToolRegistry

# The closed map. transactional = atomic with our own ledger/local-fs write;
# unconfirmed = lossy-ack boundary (network/shell/process/browser/code).
_EXPECTED: dict[str, str] = {
    "delegate_task": "unconfirmed",
    "sessions_send": "unconfirmed",
    "sessions_spawn": "unconfirmed",
    "batch_approve": "transactional",
    "apply_patch": "transactional",
    "edit": "transactional",
    "undo_store": "transactional",
    "write_file": "transactional",
    "memory": "transactional",
    "process_tool": "unconfirmed",
    "cronjob": "transactional",
    "shell": "unconfirmed",
    "browse": "unconfirmed",
    "dialog": "unconfirmed",
    "execute_code": "unconfirmed",
    "skill_manage": "transactional",
    "synthesize_skills": "transactional",
    "owl_build": "transactional",
    "tool_build": "transactional",
    "send_file": "unconfirmed",
    "send_message": "unconfirmed",
}


def test_declared_couplings_match_the_pin() -> None:
    reg = ToolRegistry.with_defaults()
    for name, expected in _EXPECTED.items():
        tool = reg.get(name)
        assert tool is not None, f"{name} not registered"
        actual = tool.manifest.commit_coupling
        assert actual == expected, (
            f"{name}: commit_coupling={actual!r} expected {expected!r}"
        )


def test_every_side_effecting_tool_declares_a_coupling() -> None:
    """No write/consequential default tool may leave commit_coupling undeclared.

    A None on a side-effecting tool is the fail-safe (treated unconfirmed) but we
    require an EXPLICIT declaration so the classification is a reviewed decision,
    not an accident.
    """
    reg = ToolRegistry.with_defaults()
    undeclared: list[str] = []
    for name in reg.names():
        tool = reg.get(name)
        if tool is None:
            continue
        m = tool.manifest
        if m.action_severity in ("write", "consequential") and m.commit_coupling is None:
            undeclared.append(name)
    assert undeclared == [], f"side-effecting tools missing commit_coupling: {undeclared}"
```

> NOTE for the implementer: confirm `ToolRegistry` exposes `names()` (or an iterable of registered tools) — if the accessor differs, adapt the second test to iterate the registry's actual public surface. Read `v2/src/stackowl/tools/registry.py` before running.

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/test_commit_coupling.py -v` — expect failure: every coupling is `None`.

- [ ] Implement — for EACH file above, add `commit_coupling="<value>",` inside the `ToolManifest(...)` call next to `action_severity`. Example (`write_file.py`):

```python
        return ToolManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            action_severity="write",
            commit_coupling="transactional",
        )
```

Example (`send_message.py`):

```python
            action_severity="consequential",
            commit_coupling="unconfirmed",
```

Repeat the one-kwarg addition for all 21 manifests per the file list + classification above. Keep every other line of each manifest untouched (minimal change).

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/test_commit_coupling.py tests/tools/test_tool_severities.py -v` — both green.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/tools v2/tests/tools/test_commit_coupling.py && git commit -m "feat(v2): declare commit_coupling per side-effecting tool + pin map (D1 §6.1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 12 — delegate_task: durable scope read → child id → claim-or-create → set child task_id

**Files:**
- Modify: `v2/src/stackowl/tools/agents/delegate_task.py` (`_run_delegation` ~:360-391; new helper to assemble durable scope)
- Create: `v2/tests/tools/agents/test_delegate_task_durable_child.py`

**Architecture for this task:** In `_run_delegation`, BEFORE building `parent_state` (~:378), read the durable scope: `tctx = TraceContext.get(); parent_task_id = tctx.get("task_id"); durable_owner = TraceContext.durable_owner_id()`. Also read the active `DurableReActContext` via `get_active()` for `ctx.iteration`. If `parent_task_id` is None OR `get_active()` is None OR there's no `db_pool` ⇒ **fail-open** (today's non-durable path: `parent_state` carries NO `task_id`). Else compute `delegate_key = idempotency_key(parent_task_id, ctx.iteration, "delegate_task", canonical_args)` where `canonical_args` is the SAME shape the base ledger keys this `delegate_task` call on — the validated `args` dict the tool received. Derive `child_task_id`, `create_child_task` + `claim_child_lease` (fail-open on store error), and set `task_id=child_task_id, durable_owner_id=durable_owner` on the `parent_state` built at :378. Store the resolved durable scope on a small dataclass so Tasks 13/14/15 reuse it.

**Steps:**

- [ ] Write the failing test `v2/tests/tools/agents/test_delegate_task_durable_child.py`:

```python
"""delegate_task creates a durable child sub-task under a durable parent (D1 §8.3).

Asserts the WIRING: when the parent turn carries a durable scope (task_id +
active DurableReActContext + db_pool), the parent_state handed to the delegator
carries the derived child_task_id (not the parent's task_id) and a child tasks
row is claimed. A non-durable parent is unchanged (no task_id on the child state).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import DelegateTaskTool


class _CapturingDelegator:
    """Records the parent_state it was handed; replies a trivial ok."""

    def __init__(self) -> None:
        self.seen_parent_state: PipelineState | None = None

    async def delegate(self, *, from_owl: str, to_owl: str, sub_task: str,
                       parent_state: PipelineState) -> A2AResult:
        self.seen_parent_state = parent_state
        return A2AResult(status="ok", content="handled", resolved_owl=to_owl)


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


def _services(pool: DbPool | None, delegator: object) -> StepServices:
    from stackowl.owls.registry import OwlRegistry
    return StepServices(
        owl_registry=OwlRegistry.with_default_secretary(),
        a2a_delegator=delegator,  # type: ignore[arg-type]
        db_pool=pool,
    )


async def test_durable_parent_child_carries_child_task_id(pool: DbPool) -> None:
    delegator = _CapturingDelegator()
    token = set_services(_services(pool, delegator))
    args = {"goal": "do the thing", "to_owl": "scout"}
    parent_task_id = "parent-1"
    trace_token = TraceContext.start(
        "sess", trace_id="tr", owl_name="secretary",
        task_id=parent_task_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id=parent_task_id, owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=2,
    )
    try:
        with activate(ctx):
            await DelegateTaskTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    assert delegator.seen_parent_state is not None
    expected_key = idempotency_key(parent_task_id, 2, "delegate_task", args)
    expected_child = derive_child_task_id(expected_key)
    assert delegator.seen_parent_state.task_id == expected_child
    assert delegator.seen_parent_state.task_id != parent_task_id
    # The child tasks row was claimed.
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child = await store.get(expected_child)
    assert child.parent_task_id == parent_task_id
    assert child.parent_owl == "secretary"


async def test_non_durable_parent_child_has_no_task_id(pool: DbPool) -> None:
    delegator = _CapturingDelegator()
    token = set_services(_services(pool, delegator))
    trace_token = TraceContext.start("sess", trace_id="tr", owl_name="secretary")
    try:
        await DelegateTaskTool().execute(goal="do x", to_owl="scout")
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)
    assert delegator.seen_parent_state is not None
    assert delegator.seen_parent_state.task_id is None
    rows = await pool.fetch_all("SELECT task_id FROM tasks", ())
    assert rows == []
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_durable_child.py -v` — expect `AssertionError`: the captured `parent_state.task_id` is `None` (durable wiring absent).

- [ ] Implement — `delegate_task.py`. Add imports at the top:

```python
from stackowl.pipeline.durable.context import get_active
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
```

Add a small dataclass + resolver near the top of the module (after `_DEFAULT_CALLER`):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _DurableChildScope:
    """The resolved durable scope for one delegation, or all-None when fail-open."""

    child_task_id: str | None = None
    durable_owner_id: str | None = None
    parent_task_id: str | None = None
    delegate_key: str | None = None


async def _resolve_durable_child_scope(
    *, caller: str, args_dict: dict[str, object],
) -> _DurableChildScope:
    """Compute the durable child scope, or a no-op scope (fail-open) (D1 §8).

    Durable ONLY when ALL hold: parent task_id present on TraceContext, an active
    DurableReActContext (for ctx.iteration), and a db_pool. Identity-determining
    values are computed explicitly from the parent task_id + ledger coordinate —
    never inferred from ambient mutable state. Any store error fails OPEN (logged)
    to the non-durable path for THIS delegation.
    """
    tctx = TraceContext.get()
    parent_task_id = tctx.get("task_id")
    durable_owner = TraceContext.durable_owner_id()
    rctx = get_active()
    db = get_services().db_pool
    if parent_task_id is None or rctx is None or db is None:
        log.tool.debug(
            "delegate_task: non-durable parent — fail-open to today's path",
            extra={"_fields": {
                "has_parent_task": parent_task_id is not None,
                "has_react_ctx": rctx is not None, "has_db": db is not None,
            }},
        )
        return _DurableChildScope()
    owner = durable_owner or DEFAULT_PRINCIPAL_ID
    try:
        delegate_key = idempotency_key(
            str(parent_task_id), int(rctx.iteration), "delegate_task", args_dict,
        )
        child_task_id = derive_child_task_id(delegate_key)
        store = DurableTaskStore(db, owner)
        await store.create_child_task(
            child_task_id=child_task_id, parent_task_id=str(parent_task_id),
            parent_owl=caller, delegate_key=delegate_key,
            goal=str(args_dict.get("goal", "")), owl_name=caller, channel="internal",
        )
        claimed = await store.claim_child_lease(child_task_id, lease_owner=str(parent_task_id))
        log.tool.info(
            "delegate_task: durable child scope resolved",
            extra={"_fields": {
                "parent_task_id": parent_task_id, "child_task_id": child_task_id,
                "lease_won": claimed,
            }},
        )
        return _DurableChildScope(
            child_task_id=child_task_id, durable_owner_id=owner,
            parent_task_id=str(parent_task_id), delegate_key=delegate_key,
        )
    except Exception as exc:  # B5 — fail-open: durability is additive, never breaks delegation.
        log.tool.error(
            "delegate_task: durable child setup failed — fail-open to non-durable path",
            exc_info=exc,
            extra={"_fields": {"parent_task_id": parent_task_id}},
        )
        return _DurableChildScope()
```

Then, inside `_run_delegation`, the method needs the validated `args` dict to key on. The cleanest minimal change: pass the durable scope in. Add a parameter `durable_scope: _DurableChildScope` to `_run_delegation` and resolve it in `execute` before the `_run_delegation` call. In `execute` (~:312), change the call site:

```python
        # 3. STEP — resolve the durable child scope (fail-open), then delegate.
        durable_scope = await _resolve_durable_child_scope(
            caller=caller, args_dict=args.model_dump(),
        )
        try:
            return await self._run_delegation(
                delegator=delegator, args=args, caller=caller, target=target, depth=depth,
                trace_id=trace_id, session_id=str(ctx.get("session_id") or ""),
                channel=str(ctx.get("channel") or "internal"), t0=t0,
                durable_scope=durable_scope,
            )
        finally:
            self._release(trace_id)
```

In `_run_delegation` signature add `durable_scope: _DurableChildScope`, and in the `PipelineState(...)` built at ~:378 add the durable kwargs:

```python
        parent_state = PipelineState(
            trace_id=trace_id or "delegate-task", session_id=session_id, input_text=sub_task,
            channel=channel, owl_name=caller, pipeline_step="dispatch", delegation_depth=depth,
            delegation_chain=chain,
            creation_ceiling=child_floor(
                caller, TraceContext.creation_ceiling(), get_services().owl_registry
            ),
            # D1 §8.3 — when durable, the child runs under ITS OWN child_task_id so
            # the execute step assembles a durable session for it; it must NOT
            # inherit the parent's task_id. None on the non-durable / fail-open path.
            task_id=durable_scope.child_task_id,
            durable_owner_id=durable_scope.durable_owner_id,
        )
```

> NOTE: `idempotency_key` keys on the validated `args` dict (`args.model_dump()`). The base ledger keys the `delegate_task` call on the args the registry passes to the tool. If the registry-level args differ in shape from `model_dump()`, the child id would diverge from the base ledger's own `delegate_task` key — but that does NOT break D1's exactly-once (the child id only needs to be stable across the PARENT's own resume, which `model_dump()` of the frozen `DelegateTaskArgs` guarantees). The implementer should confirm the dict shape is resume-stable (frozen model ⇒ stable) and NOT worry about matching the base ledger's `delegate_task` key byte-for-byte.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_durable_child.py -v` — both pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/tools/agents/delegate_task.py v2/tests/tools/agents/test_delegate_task_durable_child.py && git commit -m "feat(v2): delegate_task resolves durable child scope + sets child task_id (D1 §8)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 13 — Parent-driven terminalization on child return

**Files:**
- Modify: `v2/src/stackowl/tools/agents/delegate_task.py` (`_run_delegation` — terminalize after a terminal child result)
- Create: `v2/tests/tools/agents/test_delegate_task_terminalize_child.py`

**Steps:**

- [ ] Write the failing test `v2/tests/tools/agents/test_delegate_task_terminalize_child.py`:

```python
"""Parent terminalizes the durable child when the delegation resolves (D1 §7.2)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.durable.context import DurableReActContext, activate
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import DelegateTaskTool


class _OkDelegator:
    async def delegate(self, *, from_owl, to_owl, sub_task, parent_state):  # noqa: ANN001
        return A2AResult(status="ok", content="handled fully", resolved_owl=to_owl)


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_completed_child_is_terminalized_completed(pool: DbPool) -> None:
    token = set_services(StepServices(
        owl_registry=OwlRegistry.with_default_secretary(),
        a2a_delegator=_OkDelegator(), db_pool=pool,
    ))
    args = {"goal": "do x", "to_owl": "scout"}
    trace_token = TraceContext.start(
        "s", trace_id="tr", owl_name="secretary",
        task_id="parent-1", durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    ctx = DurableReActContext(
        task_id="parent-1", owner_id=DEFAULT_PRINCIPAL_ID,
        ledger=SideEffectLedger(pool, DEFAULT_PRINCIPAL_ID), iteration=0,
    )
    try:
        with activate(ctx):
            await DelegateTaskTool().execute(**args)
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)

    child_id = derive_child_task_id(idempotency_key("parent-1", 0, "delegate_task", args))
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    child = await store.get(child_id)
    assert child.status == "completed"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_terminalize_child.py -v` — expect `AssertionError`: child still `running` (never terminalized).

- [ ] Implement — `delegate_task.py`. The terminal results all funnel through `_map_terminal` and the honest builders. The simplest single-seam place to terminalize is at the END of `_run_delegation`, just before each `return`. To avoid scattering, wrap the whole body: rename the existing body to `_run_delegation_inner` is too invasive (violates minimal-change). Instead, terminalize at the resolution points by deriving status from the ladder result. Add a private helper:

```python
    async def _terminalize_durable_child(
        self, durable_scope: _DurableChildScope, *, completed: bool,
    ) -> None:
        """Stamp the durable child terminal as a projection of this resolution (D1 §7.2).

        No-op on the fail-open / non-durable path (child_task_id is None). Fail-open
        on store error (logged) — terminalization is belt-and-suspenders to the
        reaper, never a reason to fail the parent's turn.
        """
        if durable_scope.child_task_id is None:
            return
        status = "completed" if completed else "failed"
        try:
            db = get_services().db_pool
            if db is None:  # pragma: no cover — durable scope implies a db, defensive
                return
            store = DurableTaskStore(db, durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID)
            await store.terminalize_child(durable_scope.child_task_id, status)  # type: ignore[arg-type]
            log.tool.info(
                "delegate_task: terminalized durable child",
                extra={"_fields": {
                    "child_task_id": durable_scope.child_task_id, "status": status,
                }},
            )
        except Exception as exc:  # B5 — reaper is the backstop; never fail the turn.
            log.tool.error(
                "delegate_task: terminalize child failed — reaper will reconcile",
                exc_info=exc,
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )
```

Then guard the returns in `_run_delegation` through one exit seam. Minimal approach: capture the ladder's resolved `ToolResult` in a local and terminalize once before returning. Restructure the three `return self._map_terminal(...)` / honest / recovered returns to flow to a single tail. Concretely, wrap the existing decision block so every path assigns `final = <ToolResult>` then:

```python
        # Single tail — terminalize the durable child as a projection of this
        # resolution, then return. "completed" iff the ladder produced an ok/
        # recovered answer; "failed" for honest_uncertain / irrelevant / error.
        completed = final.success and '"status": "ok"' in final.output or "recovered" in (final.output or "")
        await self._terminalize_durable_child(durable_scope, completed=completed)
        return final
```

> IMPLEMENTER NOTE: the `completed` heuristic above is brittle. Prefer threading the A2A status through cleanly: have each branch set a local `terminal_ok: bool` alongside `final`. e.g. the `result.status == "ok"` branch sets `terminal_ok = True`; honest/uncertain/irrelevant branches set `terminal_ok = False`; the recovered-via-secretary branch sets `terminal_ok = True`. Then `await self._terminalize_durable_child(durable_scope, completed=terminal_ok)`. Do NOT parse `final.output`. Keep the change minimal but correct — assign `terminal_ok` at each existing return point and replace the bare `return X` with `final, terminal_ok = X, <bool>` then fall to the single tail.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_terminalize_child.py tests/tools/agents/test_delegate_task_durable_child.py -v` — green.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/tools/agents/delegate_task.py v2/tests/tools/agents/test_delegate_task_terminalize_child.py && git commit -m "feat(v2): parent-driven child terminalization (D1 §7.2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 14 — commit_coupling resolution replaces the honest-terminal branch

**Files:**
- Modify: `v2/src/stackowl/tools/agents/delegate_task.py` (the `_can_side_effect` gate ~:472-480 → commit_coupling resolution; new `results.py` builder for the new "definite done / safe-retry" legs)
- Modify: `v2/src/stackowl/tools/agents/results.py` (new builders if needed)
- Create: `v2/tests/tools/agents/test_delegate_task_commit_coupling_resolution.py`
- Modify (DELIBERATE): the Story-D honest-terminal tests that assert the OLD `_can_side_effect`-only behavior — flag, do not silently patch.

**Architecture (spec §6.2):** For a DURABLE parent, after the child returns/times out, consult the child's durable record + the couplings of effects it ledgered:
- child never entered any side-effecting span (no ledger `intent` rows under `child_task_id`) ⇒ **definite safe-retry** (re-delegation OK).
- child terminal AND every ledgered effect is `transactional`/`idempotent_keyed` ⇒ **definite done** (reuse persisted result).
- in-flight, OR a terminal-with-`unconfirmed` effect lacking a witnessed commit, OR any `intent`-not-`committed` non-transactional effect ⇒ **`honest_uncertain` remains**.
Non-durable parents: the existing `_can_side_effect` honest-terminal behavior is UNCHANGED.

**Steps:**

- [ ] Write the failing test `v2/tests/tools/agents/test_delegate_task_commit_coupling_resolution.py`:

```python
"""commit_coupling resolution replaces the honest-terminal gate for durable parents (D1 §6.2).

DELIBERATE behavior change vs Story D: a durable, write-capable child that
NEVER STARTED is now a DEFINITE safe-retry (not honest_uncertain); a durable
child whose only effects are transactional+committed is DEFINITE done. An
unconfirmed effect in-flight stays honest_uncertain. Non-durable parents keep
Story D's _can_side_effect honest-terminal behavior verbatim.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.delegation_link import derive_child_task_id
from stackowl.pipeline.durable.ledger import SideEffectLedger, idempotency_key
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


# The resolution is a pure decision over (child_started, all_effects_safe,
# in_flight) — test the helper directly so the table is exhaustively covered.
def test_never_started_is_definite_safe_retry() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=False, has_uncertain_effect=False, has_uncommitted_intent=False,
        child_terminal=False,
    )
    assert answer == "safe_retry"


def test_terminal_all_transactional_is_definite_done() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False, has_uncommitted_intent=False,
        child_terminal=True,
    )
    assert answer == "done"


def test_unconfirmed_in_flight_stays_honest_uncertain() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=True, has_uncommitted_intent=False,
        child_terminal=False,
    )
    assert answer == "honest_uncertain"


def test_uncommitted_intent_stays_honest_uncertain() -> None:
    from stackowl.tools.agents.delegate_task import resolve_commit_coupling_answer

    answer = resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False, has_uncommitted_intent=True,
        child_terminal=True,
    )
    assert answer == "honest_uncertain"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_commit_coupling_resolution.py -v` — expect `ImportError` on `resolve_commit_coupling_answer`.

- [ ] Implement — add the pure resolver to `delegate_task.py` (module-level):

```python
from typing import Literal as _Literal

CommitCouplingAnswer = _Literal["done", "safe_retry", "honest_uncertain"]


def resolve_commit_coupling_answer(
    *,
    child_started: bool,
    has_uncertain_effect: bool,
    has_uncommitted_intent: bool,
    child_terminal: bool,
) -> CommitCouplingAnswer:
    """The §6.2 honesty table as a pure decision (D1 §6.2).

    * never started (no intent rows)                 → "safe_retry" (pure profit).
    * an unconfirmed effect lacking a witnessed commit → "honest_uncertain".
    * any non-transactional intent not yet committed   → "honest_uncertain".
    * terminal AND every effect transactional/keyed    → "done".
    * otherwise (in-flight, no uncertainty resolvable)  → "honest_uncertain".
    """
    if not child_started:
        return "safe_retry"
    if has_uncertain_effect or has_uncommitted_intent:
        return "honest_uncertain"
    if child_terminal:
        return "done"
    return "honest_uncertain"
```

Then wire it into `_run_delegation`'s capability gate. Replace the `if _can_side_effect(target):` honest-terminal block (~:472-480) with: when `durable_scope.child_task_id is not None`, gather the child's ledger facts (read `side_effect_ledger` rows under `child_task_id` for the owner; cross-reference each row's `tool_name` against the registry's `commit_coupling`) and the child's `tasks.status`, compute `resolve_commit_coupling_answer(...)`, and branch:
- `"safe_retry"` ⇒ continue into the existing read-only retry/fallback ladder (re-delegation is safe).
- `"done"` ⇒ `self._map_terminal(result, target, t0)` (reuse the child's answer; if `result.status != "ok"` because it timed out, return the child's persisted `tasks.result` via `ok_result`).
- `"honest_uncertain"` ⇒ the existing `honest_uncertain_result` / `honest_offtopic_write_result`.

For a NON-durable parent (`durable_scope.child_task_id is None`), keep the existing `_can_side_effect(target)` branch verbatim. Add a helper to read the ledger facts:

```python
    async def _child_ledger_facts(
        self, durable_scope: _DurableChildScope,
    ) -> tuple[bool, bool, bool, bool]:
        """Return (child_started, has_uncertain_effect, has_uncommitted_intent,
        child_terminal) for the durable child (D1 §6.2). Fail-open: on store error
        return the maximally-uncertain tuple so the answer stays honest_uncertain.
        """
        db = get_services().db_pool
        owner = durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID
        cid = durable_scope.child_task_id
        treg = get_services().tool_registry
        try:
            rows = await db.fetch_all(  # type: ignore[union-attr]
                "SELECT tool_name, status FROM side_effect_ledger "
                "WHERE owner_id = ? AND task_id = ?",
                (owner, cid),
            )
            store = DurableTaskStore(db, owner)  # type: ignore[arg-type]
            child = await store.get(str(cid))
            started = len(rows) > 0
            child_terminal = child.status in ("completed", "failed")
            has_uncertain = False
            has_uncommitted_intent = False
            for r in rows:
                coupling = None
                if treg is not None:
                    tool = treg.get(str(r["tool_name"]))
                    if tool is not None:
                        coupling = tool.manifest.commit_coupling
                safe = coupling in ("transactional", "idempotent_keyed")
                if str(r["status"]) != "committed" and not safe:
                    has_uncommitted_intent = True
                if str(r["status"]) == "committed" and coupling == "unconfirmed":
                    has_uncertain = True
                if str(r["status"]) != "committed" and coupling == "unconfirmed":
                    has_uncertain = True
            return started, has_uncertain, has_uncommitted_intent, child_terminal
        except Exception as exc:  # B5 — fail to maximally-uncertain (honest).
            log.tool.error(
                "delegate_task: child ledger read failed — defaulting honest_uncertain",
                exc_info=exc,
                extra={"_fields": {"child_task_id": cid}},
            )
            return True, True, True, False
```

> IMPLEMENTER NOTE: wire this so the durable branch is taken ONLY when `durable_scope.child_task_id is not None`; otherwise the existing Story-D `_can_side_effect` path is byte-for-byte unchanged. The "done" leg for a timed-out-but-actually-completed child should return the child's `tasks.result` via `ok_result({"status":"ok","to_owl":target,"result": child.result + provenance_footer(target)}, t0, note=...)`.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/test_delegate_task_commit_coupling_resolution.py -v` — 4 pass.

- [ ] Re-run the Story-D honest-terminal suite and triage DELIBERATE changes: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/tools/agents/ -v`. Any test that asserts the OLD durable-parent honest_uncertain-on-not-started behavior must be updated to the NEW behavior AND a one-line comment added: `# D1 §6.2: durable not-started ⇒ definite safe-retry (was honest_uncertain under Story D)`. Per the no-silent-fix rule, if a failing test looks like genuine broken WIRING (not the intended behavior change), STOP and report to the orchestrator instead of patching.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/tools/agents/delegate_task.py v2/tests/tools/agents && git commit -m "feat(v2): commit_coupling resolution replaces honest-terminal gate for durable parents (D1 §6.2)

DELIBERATE: durable not-started ⇒ definite safe-retry; transactional+committed ⇒ done; unconfirmed in-flight stays honest_uncertain. Non-durable parents unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 15 — Supersession on timeout (next ladder rung)

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/store.py` (new `supersede_child` method)
- Modify: `v2/src/stackowl/tools/agents/delegate_task.py` (stamp superseded when the parent gives up on a child and advances a rung)
- Create: `v2/tests/pipeline/durable/test_supersede_child.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/durable/test_supersede_child.py`:

```python
"""supersede_child tombstones a timed-out child (Story D1 §9)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_supersede_child_sets_flag(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    await store.create_child_task(
        child_task_id="c", parent_task_id="p", parent_owl="secretary",
        delegate_key="dk", goal="sub", owl_name="scout", channel="cli",
    )
    await store.supersede_child("c")
    rec = await store.get("c")
    assert rec.superseded is True
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_supersede_child.py -v` — expect `AttributeError: ... 'supersede_child'`.

- [ ] Implement — add to `store.py` after `terminalize_child`:

```python
    async def supersede_child(self, task_id: str) -> None:
        """Tombstone a timed-out child so a slow eventual commit is neutralized (D1 §9).

        Sets ``superseded = 1`` via owner-scoped CAS-style UPDATE. The next ladder
        rung derives a DIFFERENT child id (different iteration/args), so a slow
        child's late commit cannot race the decision the parent already made.
        """
        log.tasks.debug(
            "[tasks] store.supersede_child: entry",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
        sql = (
            f"UPDATE {self._table} SET superseded = 1, updated_at = ? "  # noqa: S608 — table from class
            "WHERE owner_id = ? AND task_id = ?"
        )
        await self._execute_owned(
            sql, [datetime.now(tz=UTC).isoformat(), self._owner_id, task_id]
        )
        log.tasks.info(
            "[tasks] store.supersede_child: superseded",
            extra={"_fields": {"task_id": task_id, "owner_id": self._owner_id}},
        )
```

Then in `delegate_task.py`, at the point where the durable parent gives up on a timed-out child and advances to the next rung (the read-only retry / fallback path after a `timeout`/`honest_uncertain` decision on the SAME child), call a thin helper:

```python
    async def _supersede_durable_child(self, durable_scope: _DurableChildScope) -> None:
        """Tombstone the durable child when the parent advances past it (D1 §9). No-op
        on the non-durable path; fail-open on store error (logged)."""
        if durable_scope.child_task_id is None:
            return
        try:
            db = get_services().db_pool
            if db is None:  # pragma: no cover — defensive
                return
            store = DurableTaskStore(db, durable_scope.durable_owner_id or DEFAULT_PRINCIPAL_ID)
            await store.supersede_child(durable_scope.child_task_id)
        except Exception as exc:  # B5
            log.tool.error(
                "delegate_task: supersede child failed",
                exc_info=exc,
                extra={"_fields": {"child_task_id": durable_scope.child_task_id}},
            )
```

> IMPLEMENTER NOTE (RESOLVED — do not re-litigate): All ladder rungs share ONE `child_task_id` (one `delegate_task` call ⇒ one `delegate_key`), and per **spec §5.3 this is correct, not a divergence** — do NOT add a per-rung/per-target ordinal. Reasoning: same-target rungs (initial + retry) re-deriving the same id is the *desired* re-attach/resume; the different-target fallback (→ secretary) is reached ONLY for read-only children (Story D's `_can_side_effect` gate halts write-capable children at `honest_uncertain`, never re-delegating them to a different owl), and read-only children have no ledgered effects, so id-sharing there is harmless. Supersession is therefore **defensive** (it neutralizes the slow-late-commit decision-layer race). Call `_supersede_durable_child(durable_scope)` ONLY on the path where the parent abandons a timed-out child and advances a rung; do NOT supersede a child that resolved to a definite `done` or whose answer the parent is reusing. The minimal correct behavior for THIS task: supersede the child when the ladder abandons it. The single-`parent_state` reuse is fine.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_supersede_child.py -v` — pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/store.py v2/src/stackowl/tools/agents/delegate_task.py v2/tests/pipeline/durable/test_supersede_child.py && git commit -m "feat(v2): supersede timed-out durable child (D1 §9)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 16 — a2a cancel-survival + Break-A (child scope as a value)

**Files:**
- Modify: `v2/src/stackowl/owls/a2a_delegation.py` (`delegate` timeout path ~:130-145; `_run_specialist` ~:222-295 — pass child scope as a value, cancel must not mark durable child failed)
- Create: `v2/tests/owls/test_a2a_durable_cancel_survival.py`

**Steps:**

- [ ] Write the failing test `v2/tests/owls/test_a2a_durable_cancel_survival.py`:

```python
"""A2A timeout cancels the asyncio task but a durable child survives recovering (D1 §9)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def test_timeout_does_not_mark_durable_child_failed(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    # Seed a running durable child the specialist will "run" under.
    child_id = "child-cancel"
    await store.create_child_task(
        child_task_id=child_id, parent_task_id="p", parent_owl="secretary",
        delegate_key="dk", goal="sub", owl_name="scout", channel="cli",
    )

    # A delegator whose specialist never replies → forces the timeout/cancel path.
    services = StepServices(db_pool=pool)
    delegator = A2ADelegator(A2AQueue(), services, timeout_seconds=0.05)

    parent_state = PipelineState(
        trace_id="tr", session_id="s", input_text="sub", channel="internal",
        owl_name="secretary", pipeline_step="dispatch",
        task_id=child_id, durable_owner_id=DEFAULT_PRINCIPAL_ID,
    )
    res = await delegator.delegate(
        from_owl="secretary", to_owl="scout", sub_task="sub", parent_state=parent_state,
    )
    assert res.status == "timeout"
    # Let any cancellation settle.
    await asyncio.sleep(0.05)
    rec = await store.get(child_id)
    # The durable child must NOT be marked failed by the cancel — it stays
    # running/recovering so recovery can resume it.
    assert rec.status in ("running", "recovering"), (
        f"durable child wrongly finalized to {rec.status!r} on a2a cancel"
    )
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/owls/test_a2a_durable_cancel_survival.py -v` — run and observe. (Today `_run_specialist`'s `except asyncio.CancelledError` re-raises without touching the tasks row, so the child likely already stays `running` — but the test PINS that invariant against the durable-scope changes; if it passes pre-change, ADD a sub-assertion proving the durable child id was threaded as a VALUE — see implementation note — and make that the failing edge.)

- [ ] Implement — `a2a_delegation.py`. The Break-A requirement: child durable scope must be threaded as a plain VALUE into `_run_specialist` and `.set()` only inside the child frame, never mutating the parent's ContextVar. Since `_run_specialist` already builds `sub_state` via `parent_state.evolve(...)` and `sub_state` flows into `backend.run` (which calls `TraceContext.start` fresh — a NEW scope per child, not a `.set()` on the parent), the isolation is structurally correct ALREADY. The required D1 change: `_run_specialist`'s `evolve(...)` must NOT drop the durable scope — confirm `task_id`/`durable_owner_id` are preserved by `evolve` (they are, since `evolve` only overrides supplied kwargs). Add an explicit comment + keep `task_id`/`durable_owner_id` implicitly carried:

```python
        sub_state = parent_state.evolve(
            owl_name=to_owl,
            input_text=sub_task,
            responses=(),
            tool_calls=(),
            errors=(),
            pipeline_step="dispatch",
            interactive=False,
            delegation_depth=parent_state.delegation_depth + 1,
            delegation_chain=parent_state.delegation_chain + (to_owl,),
            # D1 §8.2 Break-A — the durable scope (task_id/durable_owner_id) is
            # carried by VALUE on sub_state (parent_state already holds the child
            # id) and stamped fresh inside backend.run's own TraceContext.start,
            # never via a .set() on the parent coroutine's ContextVar. evolve()
            # preserves task_id/durable_owner_id unless overridden — do NOT clear
            # them here.
        )
```

And in `_run_specialist`'s `except asyncio.CancelledError:` block, add an explicit comment guaranteeing it does NOT write a terminal status to the durable child:

```python
        except asyncio.CancelledError:
            # D1 §9 cancel-survival — an a2a timeout cancels THIS asyncio task, but
            # for a DURABLE child we must NOT finalize the tasks row to 'failed':
            # the row stays running/recovering so startup recovery (or the next
            # turn) resumes it from its checkpoint. We deliberately re-raise WITHOUT
            # touching the durable store here.
            log.engine.warning(
                "[a2a-delegator] _run_specialist: cancelled — durable child (if any) "
                "left running/recovering for recovery",
                extra={"_fields": {
                    "trace_id": parent_state.trace_id, "to": to_owl,
                    "durable_task_id": parent_state.task_id,
                }},
            )
            raise
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/owls/test_a2a_durable_cancel_survival.py -v` — pass.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/owls/a2a_delegation.py v2/tests/owls/test_a2a_durable_cancel_survival.py && git commit -m "feat(v2): a2a cancel-survival for durable children + Break-A scope-by-value (D1 §8.2/§9)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 17 — Recovery roots-only + depth-from-tree + zombie reaper

**Files:**
- Modify: `v2/src/stackowl/pipeline/durable/recovery.py` (`recover` orphan filter ~:165-172; `_reconstruct_state` depth; new reaper sweep)
- Create: `v2/tests/pipeline/durable/test_recovery_roots_and_reaper.py`

**Steps:**

- [ ] Write the failing test `v2/tests/pipeline/durable/test_recovery_roots_and_reaper.py`:

```python
"""Recovery resumes roots only + reaps zombie children (Story D1 §9)."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import recover_durable_tasks
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task import DurableTask
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.registry import ToolRegistry

_FINAL = "done"


class _Finishing:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "fin"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "anthropic"

    async def complete_with_tools(self, user_text, system_text, tool_schemas,  # noqa: ANN001
                                  tool_dispatcher, max_iterations=8, history=None,
                                  persistence_check=None, on_iteration_complete=None,
                                  resume_messages=None, resume_tool_calls=None):
        self.calls += 1
        if on_iteration_complete is not None:
            await on_iteration_complete(ReActIterationState(
                iteration=0, messages=[{"role": "assistant", "content": "done"}],
                tool_call_records=[]))
        return _FINAL, []

    async def complete(self, *a: object, **k: object) -> CompletionResult:
        return CompletionResult(content="secretary", input_tokens=1, output_tokens=1,
                                model="", provider_name=self.name, duration_ms=0.0)

    async def stream(self, *a: object, **k: object) -> AsyncIterator[str]:  # pragma: no cover
        if False:
            yield ""


class _Reg:
    def __init__(self, p: object) -> None:
        self._p = p

    def get(self, name: str) -> object:
        return self._p

    def get_by_tier(self, tier: str) -> object:
        return self._p

    def get_with_cascade(self, tier: str) -> object:
        return self._p


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


def _backend(pool: DbPool, provider: object) -> AsyncioBackend:
    services = StepServices(
        provider_registry=_Reg(provider),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(), stream_registry=StreamRegistry(),
        owl_registry=OwlRegistry.with_default_secretary(), db_pool=pool,
    )
    return AsyncioBackend(services=services)


async def test_children_excluded_from_orphan_recovery(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    # A running ROOT orphan + a running CHILD orphan under a running parent.
    await store.create(DurableTask(task_id="root", owner_id=DEFAULT_PRINCIPAL_ID,
                                   goal="g", status="running", owl_name="secretary",
                                   channel="cli", created_at=now, updated_at=now))
    await store.create_child_task(child_task_id="kid", parent_task_id="root",
                                  parent_owl="secretary", delegate_key="dk",
                                  goal="sub", owl_name="scout", channel="cli")
    recoverer = await recover_durable_tasks(pool, _backend(pool, _Finishing()))
    await recoverer.drain()
    # Only the ROOT was launched — the child is resumed transitively, not directly.
    assert recoverer.launched == 1, (
        f"only roots should be launched, got {recoverer.launched}"
    )


async def test_zombie_child_under_terminal_parent_is_reaped(pool: DbPool) -> None:
    store = DurableTaskStore(pool, DEFAULT_PRINCIPAL_ID)
    now = datetime.now(tz=UTC)
    await store.create(DurableTask(task_id="P", owner_id=DEFAULT_PRINCIPAL_ID, goal="g",
                                   status="completed", created_at=now, updated_at=now))
    await store.create_child_task(child_task_id="zombie", parent_task_id="P",
                                  parent_owl="secretary", delegate_key="dk",
                                  goal="sub", owl_name="scout", channel="cli")
    recoverer = await recover_durable_tasks(pool, _backend(pool, _Finishing()))
    await recoverer.drain()
    rec = await store.get("zombie")
    assert rec.status == "failed", f"zombie child not reaped — status={rec.status!r}"
```

- [ ] Run-to-fail: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_recovery_roots_and_reaper.py -v` — expect failures: the child "kid" is currently listed as a `running` orphan and launched (`launched == 2`), and the zombie is never reaped.

- [ ] Implement — `recovery.py`:

In `recover()`, after listing `running`/`recovering`, filter to roots (`parent_task_id IS NULL`) and run the reaper:

```python
        running = await self._store.list(status="running")
        recovering = await self._store.list(status="recovering")
        seen: set[str] = set()
        orphans: list[DurableTask] = []
        for task in (*running, *recovering):
            # D1 §9 — roots only. Children are resumed transitively when the
            # parent re-executes its delegate_task and re-derives the same child
            # id; listing them here would double-drive them.
            if task.parent_task_id is not None:
                continue
            if task.task_id not in seen:
                seen.add(task.task_id)
                orphans.append(task)
        # D1 §7.3 — reap zombie children whose parent is already terminal (they
        # are unreachable by transitive resolution). Fail-open (logged).
        await self._reap_zombie_children()
```

Add the reaper method:

```python
    async def _reap_zombie_children(self) -> None:
        """Mark running/recovering children of terminal parents 'failed' (D1 §7.3).

        With parent-driven terminalization this is normally empty; it is the
        belt-and-suspenders for crash interleavings. Fail-open: a store error is
        logged and never crashes recovery.
        """
        try:
            zombies = await self._store.list_zombie_children()
        except Exception as exc:  # noqa: BLE001 — fail-open, logged
            log.tasks.error(
                "[tasks] recovery: zombie-child sweep query failed — skipping",
                exc_info=exc,
                extra={"_fields": {"owner_id": self._owner_id}},
            )
            return
        for z in zombies:
            try:
                await self._store.terminalize_child(z.task_id, "failed",
                                                    result="abandoned: parent already terminal")
                log.tasks.warning(
                    "[tasks] recovery: reaped zombie child under terminal parent",
                    extra={"_fields": {
                        "task_id": z.task_id, "parent_task_id": z.parent_task_id,
                    }},
                )
            except Exception as exc:  # noqa: BLE001 — per-zombie fail-open
                log.tasks.error(
                    "[tasks] recovery: reaping a zombie child failed — continuing",
                    exc_info=exc,
                    extra={"_fields": {"task_id": z.task_id}},
                )
```

For depth-from-tree: a recovered ROOT has `parent_task_id IS NULL` so its depth is 0 — `_reconstruct_state` already builds a fresh state with no delegation depth (effectively 0), which is correct for roots. The depth-from-tree reconstruction matters only if an interior node were ever resumed directly — but roots-only filtering means interior nodes are NEVER directly resumed, so the depth is reconstructed implicitly by the parent re-walking its own delegation chain on resume. Add a comment in `_reconstruct_state` documenting this:

```python
        # D1 §9 depth-from-tree — only ROOTS are reconstructed here (recover()
        # filters parent_task_id IS NULL), so depth starts at 0 correctly. Interior
        # nodes are NEVER directly resumed: the parent re-delegates on resume and
        # the child's depth is re-derived from delegation_chain growth, never from
        # a stale ContextVar.
```

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_recovery_roots_and_reaper.py tests/journeys/test_j1_j2_durable_kill_resume.py -v` — new tests pass; the existing J1/J2 kill-resume journey (roots, no parent) still passes.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/durable/recovery.py v2/tests/pipeline/durable/test_recovery_roots_and_reaper.py && git commit -m "feat(v2): recovery roots-only + zombie-child reaper + depth-from-tree (D1 §9)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 18 — ContextVar isolation across 4 parallel children

**Files:**
- Create: `v2/tests/pipeline/durable/test_parallel_child_context_isolation.py`

(Pure test task — validates Tasks 8/9/16; no src change unless it surfaces a leak, in which case STOP and report.)

**Steps:**

- [ ] Write the test `v2/tests/pipeline/durable/test_parallel_child_context_isolation.py`:

```python
"""4 concurrent children record their observed durable task_id — zero crossover (D1 §8.2).

Validates Break-A/B/C: asyncio.create_task snapshots the context, so each child's
TraceContext.get()["task_id"] is its OWN — even when one child raises and one is
cancelled. A backend whose run() stamps the durable scope (Task 9) is driven once
per child id via separate AsyncioBackend.run calls under separate states.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.infra.trace import TraceContext
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

_observed: dict[str, str | None] = {}


def _probe_factory(label: str, *, raise_after: bool = False, cancel: bool = False):  # noqa: ANN202
    async def _probe(state: PipelineState) -> PipelineState:
        # Record what THIS child sees.
        _observed[label] = TraceContext.get()["task_id"]
        if cancel:
            raise asyncio.CancelledError
        if raise_after:
            raise RuntimeError("child boom")
        return state
    return _probe


async def _drive(monkeypatch_mod, label: str, task_id: str, **flags) -> None:  # noqa: ANN001, ANN003
    monkeypatch_mod.PIPELINE_STEPS = [(f"probe-{label}", _probe_factory(label, **flags))]
    backend = AsyncioBackend(services=StepServices())
    state = PipelineState(
        trace_id=f"tr-{label}", session_id="s", input_text="x", channel="cli",
        owl_name="secretary", pipeline_step="", interactive=False,
        task_id=task_id, durable_owner_id="owner",
    )
    await backend.run(state)


async def test_four_concurrent_children_no_task_id_crossover(monkeypatch) -> None:  # noqa: ANN001
    import stackowl.pipeline.backends.asyncio_backend as mod

    # AsyncioBackend reads module-level PIPELINE_STEPS; each child needs its own
    # probe. Use one shared steps list that records per-label, driven concurrently.
    async def _probe(state: PipelineState) -> PipelineState:
        _observed[state.trace_id] = TraceContext.get()["task_id"]
        if state.trace_id == "tr-c":
            raise RuntimeError("child boom")
        if state.trace_id == "tr-d":
            raise asyncio.CancelledError
        return state

    monkeypatch.setattr(mod, "PIPELINE_STEPS", [("probe", _probe)])

    async def _run(label: str, task_id: str) -> None:
        backend = AsyncioBackend(services=StepServices())
        state = PipelineState(
            trace_id=f"tr-{label}", session_id="s", input_text="x", channel="cli",
            owl_name="secretary", pipeline_step="", interactive=False,
            task_id=task_id, durable_owner_id="owner",
        )
        await backend.run(state)

    # NOTE: AsyncioBackend catches step exceptions into state.errors, so the
    # raising/cancelled children still complete run() — the assertion is purely
    # about task_id isolation in the observed map.
    await asyncio.gather(
        _run("a", "child-A"), _run("b", "child-B"),
        _run("c", "child-C"), _run("d", "child-D"),
    )

    assert _observed["tr-a"] == "child-A"
    assert _observed["tr-b"] == "child-B"
    assert _observed["tr-c"] == "child-C"
    assert _observed["tr-d"] == "child-D"
```

> IMPLEMENTER NOTE: `AsyncioBackend.run` wraps each step in try/except and folds exceptions into `state.errors` (see `asyncio_backend.py:66-76`), so the raising/cancelled probes still let `run()` complete — confirm `asyncio.CancelledError` is caught by the broad `except Exception` (it is NOT, since CancelledError is a BaseException in 3.8+). If the cancelled child propagates, adjust the "tr-d" branch to raise `RuntimeError` instead and document that genuine cancellation isolation is covered by Task 16's a2a test. Do NOT weaken the no-crossover assertion.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/pipeline/durable/test_parallel_child_context_isolation.py -v` — pass (zero crossover). If a crossover appears, STOP — a real leak in Task 8/9 wiring; report to the orchestrator, do not patch the test.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/pipeline/durable/test_parallel_child_context_isolation.py && git commit -m "test(v2): ContextVar task_id isolation across 4 parallel children (D1 §8.2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 19 — Merge-gate journey: durable parent → child write W exactly-once across crash

**Files:**
- Create: `v2/tests/journeys/test_durable_delegation_journey.py`

**Steps:**

- [ ] Write the journey `v2/tests/journeys/test_durable_delegation_journey.py` (mirrors `test_j1_j2_durable_kill_resume.py`; REAL DbPool/store/ledger/backend/registry, ONLY the AI provider scripted). The journey: a durable PARENT delegates to a write-capable CHILD that performs a `transactional` write W; the parent crashes BEFORE its `delegate_task` ledger entry commits; `recover_durable_tasks` resumes the parent; assert W ran EXACTLY ONCE and the parent goal completes.

```python
"""MERGE-GATE — durable delegated child runs write W exactly once across a crash (D1 §11.2).

A durable PARENT delegates a sub-task to a write-capable CHILD. The child performs
a transactional write W (ledgered under the child's OWN child_task_id). The parent
process crashes BEFORE its delegate_task ledger entry commits. Startup recovery
resumes the ROOT parent, which re-delegates → re-derives the SAME child_task_id →
the child's write W replays (already_committed) instead of double-firing. W ran
EXACTLY ONCE and the parent goal completes.

REAL components throughout (DbPool, DurableTaskStore + tasks, SideEffectLedger +
side_effect_ledger, AsyncioBackend + pipeline + ToolRegistry, A2ADelegator). Only
the AI provider is scripted. This is the D1 capstone.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from stackowl.config.test_mode import TestModeGuard
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.messaging.a2a import A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.durable.recovery import recover_durable_tasks
from stackowl.pipeline.durable.store import DurableTaskStore
from stackowl.pipeline.durable.task_runner import DurableTaskRunner
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import StreamRegistry
from stackowl.providers.base import CompletionResult
from stackowl.providers.react_callback import IterationCallback, ReActIterationState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry

_PARENT_GOAL = "Have the specialist file the report"
_FINAL = "Filed — confirmed by the specialist."


class _Crash(RuntimeError):
    pass


class _WriteW(Tool):
    """A transactional write tool with a cross-crash run counter (the exactly-once proof)."""

    def __init__(self) -> None:
        self.runs = 0

    @property
    def name(self) -> str:
        return "file_report"

    @property
    def description(self) -> str:
        return "file the report (transactional write)"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    @property
    def manifest(self) -> ToolManifest:
        return ToolManifest(name=self.name, description=self.description,
                            parameters=self.parameters, action_severity="write",
                            commit_coupling="transactional")

    async def execute(self, **kwargs: object) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="FILED", error=None, duration_ms=1.0)


# The implementer drives the parent/child providers so that:
#  ACT 1: the parent (durable, root) delegates → the child runs ITS durable
#    sub-pipeline, dispatches file_report (committed under child_task_id at the
#    child's iteration), the child returns ok → the parent is about to commit its
#    delegate_task ledger entry when the parent provider RAISES (_Crash).
#  ACT 2: recover_durable_tasks resumes the ROOT parent; it re-delegates → the
#    same child_task_id → file_report replays (already_committed) → parent
#    completes with _FINAL.
# See test_j1_j2_durable_kill_resume.py for the scripted-provider scaffold pattern;
# the parent provider calls tool_dispatcher("delegate_task", {...}) and the child
# provider (resolved for the specialist owl) calls tool_dispatcher("file_report", {}).


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "deleg.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
def _live_io():  # noqa: ANN202
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]
    yield
    TestModeGuard._active = prev  # type: ignore[attr-defined]


async def test_durable_delegated_child_write_exactly_once_across_crash(pool: DbPool) -> None:
    # The implementer assembles: a ToolRegistry with delegate_task + _WriteW; an
    # A2ADelegator wired into StepServices(db_pool=pool, a2a_delegator=...,
    # owl_registry=... with a write-capable specialist, tool_registry=..., ...);
    # a scripted parent provider that delegates then crashes (ACT 1) and a
    # recovering parent provider that re-delegates (ACT 2); the SAME _WriteW
    # instance shared across both acts.
    write = _WriteW()
    # ... ACT 1 (crash) + ACT 2 (recover via recover_durable_tasks + drain) ...
    # ASSERT the capstone:
    #   assert write.runs == 1               # exactly-once across crash
    #   parent_tasks = await pool.fetch_all("SELECT status, result FROM tasks WHERE parent_task_id IS NULL", ())
    #   assert parent_tasks[0]["status"] == "completed"
    #   assert parent_tasks[0]["result"] == _FINAL
    #   ledger = await pool.fetch_all("SELECT status, tool_name FROM side_effect_ledger WHERE tool_name = 'file_report'", ())
    #   assert len(ledger) == 1 and ledger[0]["status"] == "committed"
    raise NotImplementedError(
        "Implementer: complete the two-act scripted-provider drive mirroring "
        "test_j1_j2_durable_kill_resume.py, then replace this raise with the asserts above."
    )
```

> IMPLEMENTER NOTE: this journey is the MERGE GATE. It must be fully fleshed out (no `NotImplementedError` at commit). Mirror the exact two-act scaffold of `tests/journeys/test_j1_j2_durable_kill_resume.py`: a parent `DurableTaskRunner.run(goal=_PARENT_GOAL, state=<durable root state>)` whose provider dispatches `delegate_task` (the real tool, which spins up the child sub-pipeline via the real `A2ADelegator`), with the child provider resolved for the specialist owl dispatching `file_report`. The crash falls AFTER the child's write W commits under `child_task_id` but BEFORE the parent's `delegate_task` ledger commit. ACT 2 uses `recover_durable_tasks(pool, backend2)` + `drain()`. The provider registry must return the PARENT provider for "secretary" and the CHILD provider for the specialist (key on the owl/owl_name passed to `complete`). The shared `_WriteW.runs == 1` is the J2-equivalent proof.

- [ ] Run-to-fail then iterate to pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_durable_delegation_journey.py -v`. The first run fails on `NotImplementedError`; the implementer completes the drive until `write.runs == 1` and the parent completes.

- [ ] Run-to-pass: same command — green. Per the no-silent-integration-fix rule: if W runs twice (exactly-once violated), STOP and report to the orchestrator — that is a real wiring bug, not a test to patch.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/journeys/test_durable_delegation_journey.py && git commit -m "test(v2): merge-gate journey — durable delegated child write exactly-once across crash (D1 §11.2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Task 20 — Honesty + unchanged journeys

**Files:**
- Create: `v2/tests/journeys/test_durable_delegation_honesty.py`

**Steps:**

- [ ] Write the journey `v2/tests/journeys/test_durable_delegation_honesty.py` with three user-outcome cases:
  - (a) **non-durable parent unchanged**: an interactive (non-durable) parent delegates to a write-capable child that times out ⇒ `honest_uncertain`, and NO `tasks` row is created.
  - (b) **commit_coupling honesty — unconfirmed in-flight**: a durable parent delegates to a child that ledgers an `unconfirmed` effect at `intent` (not committed) then times out ⇒ the parent resolves `honest_uncertain` (NOT a false "safe").
  - (c) **commit_coupling honesty — transactional committed & never-started**: a durable child whose only effect is `transactional`+`committed` ⇒ definite "done"; a durable child with NO ledger rows ⇒ definite "safe-retry".

```python
"""commit_coupling honesty + non-durable-parent-unchanged journeys (D1 §11.2).

Asserts the user-visible OUTCOME of the honesty axis:
  (a) non-durable parent → honest_uncertain on timeout + no tasks row (UNCHANGED).
  (b) durable + unconfirmed effect in-flight → honest_uncertain (not false-safe).
  (c) durable + transactional committed → done; durable never-started → safe-retry.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.owls.a2a_delegation import A2AResult
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.tenancy import DEFAULT_PRINCIPAL_ID
from stackowl.tools.agents.delegate_task import (
    DelegateTaskTool,
    resolve_commit_coupling_answer,
)


@pytest.fixture()
async def pool(tmp_path: Path) -> AsyncGenerator[DbPool]:
    db_path = tmp_path / "d1.db"
    MigrationRunner(db_path=db_path).run()
    p = DbPool(db_path=db_path)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


class _TimeoutDelegator:
    async def delegate(self, *, from_owl, to_owl, sub_task, parent_state):  # noqa: ANN001
        return A2AResult(status="timeout", resolved_owl=to_owl)


async def test_non_durable_parent_timeout_is_honest_uncertain_no_tasks_row(pool: DbPool) -> None:
    # A write-capable specialist must exist so _can_side_effect(target) is True.
    reg = OwlRegistry.with_default_secretary()
    token = set_services(StepServices(
        owl_registry=reg, a2a_delegator=_TimeoutDelegator(), db_pool=pool,
    ))
    trace_token = TraceContext.start("s", trace_id="tr", owl_name="secretary")  # NO task_id
    try:
        res = await DelegateTaskTool().execute(goal="file it", to_owl="scout")
    finally:
        TraceContext.reset(trace_token)
        reset_services(token)
    # The honest-uncertain contract is preserved for non-durable parents.
    assert res.success is False
    assert "uncertain" in (res.output or "") or "NOT" in (res.error or "")
    rows = await pool.fetch_all("SELECT task_id FROM tasks", ())
    assert rows == [], "non-durable parent must not create a durable child row"


def test_resolution_table_done_safe_retry_uncertain() -> None:
    assert resolve_commit_coupling_answer(
        child_started=False, has_uncertain_effect=False,
        has_uncommitted_intent=False, child_terminal=False) == "safe_retry"
    assert resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=False,
        has_uncommitted_intent=False, child_terminal=True) == "done"
    assert resolve_commit_coupling_answer(
        child_started=True, has_uncertain_effect=True,
        has_uncommitted_intent=False, child_terminal=False) == "honest_uncertain"
```

> IMPLEMENTER NOTE: case (b) full in-flight integration (a durable child that ledgers an `unconfirmed` intent then times out, driven through the real A2ADelegator + a scripted provider) is the strongest form; if the scaffolding cost is high, the pure-resolver assertions in `test_resolution_table_done_safe_retry_uncertain` plus the durable ledger-facts unit coverage from Task 14 cover the decision, and case (a) covers the unchanged non-durable contract end-to-end. Prefer the full (b) integration if time permits — flag to the orchestrator if it is reduced to the resolver-level assertion.

- [ ] Run-to-pass: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest tests/journeys/test_durable_delegation_honesty.py -v` — green.

- [ ] Commit: `cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/tests/journeys/test_durable_delegation_honesty.py && git commit -m "test(v2): commit_coupling honesty + non-durable-parent-unchanged journeys (D1 §11.2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"`

---

## Self-Review

### Spec coverage (§2–§9 + §14 invariants → task)

| Spec | Requirement | Task(s) |
|---|---|---|
| §4 | Migration 0053 columns + index | 1 |
| §4 | DurableTask 5 fields + store carry | 2 |
| §5 | `derive_child_task_id(delegate_key)` from ledger key | 3, 12 |
| §5.2 | Determinism boundary inherited (frozen args ⇒ stable) | 12 (impl note) |
| §6.1 | `commit_coupling` enum on ToolManifest + fail-safe default | 10, 11 |
| §6.2 | Resolution table (done/safe-retry/honest_uncertain) | 14 |
| §7.1 | claim-or-create + lease (winner runs, loser polls) | 5, 6 |
| §7.2 | Parent-driven terminalization | 7, 13 |
| §7.3 | Zombie reaper | 7 (query), 17 (sweep) |
| §8.1 | TraceContext durable scope + AsyncioBackend stamp | 8, 9 |
| §8.2 | ContextVar isolation (Break-A/B/C) | 16, 18 |
| §8.3 | Child durable via task_id; not inherit parent's | 12 |
| §9 | Roots-only recovery | 17 |
| §9 | Depth-from-tree (`ancestor_depth`) | 4, 17 (impl: roots-only makes it implicit) |
| §9 | Cancel-survival | 16 |
| §9 | Supersession on timeout | 15 |
| §14.1–6 | All six load-bearing invariants | 3/5/6/12 (1); 10/11/14 (2); 5/6/7/13 (3); 9/12 (4); 8/9/16/18 (5); 15/16/17 (6) |
| §11.2 | Merge-gate exactly-once journey | 19 |
| §11.2 | Honesty + unchanged journeys | 20 |

**Gaps / partial:** §9 "depth-from-tree" is implemented as a pure helper (Task 4) but is NOT wired into recovery's reconstruction because roots-only filtering (Task 17) means interior nodes are never directly resumed — the depth is re-derived implicitly via the parent's `delegation_chain` growth on resume. The `ancestor_depth` helper is therefore provided + tested but used defensively/for future tree queries rather than on the hot recovery path. This is faithful to the spec's intent (no resumed interior node delegates a 4th level) but the orchestrator should confirm this interpretation vs. an explicit depth-stamp at reconstruction.

### Placeholder scan
No "TBD"/"similar to Task N"/"add error handling" placeholders in code steps. Three tasks carry explicit IMPLEMENTER NOTES that defer a *judgement* (not code): Task 12 (args-dict resume-stability), Task 13 (`terminal_ok` threading vs output-parsing — code given for both, brittle one flagged to avoid), Task 19 (the merge-gate journey body must be fleshed out from the J1/J2 scaffold — the asserts are given, the two-act drive is the implementer's to mirror). These are genuine "mirror an existing pattern" instructions, not vague placeholders.

### Type/name consistency
Field names consistent across all tasks: `parent_task_id`, `parent_owl`, `delegate_key`, `lease_owner`, `superseded`. Method names consistent: `create_child_task`, `claim_child_lease`, `terminalize_child`, `supersede_child`, `list_children`, `list_zombie_children`, `derive_child_task_id`, `ancestor_depth`, `resolve_commit_coupling_answer`. TraceContext: `task_id` in `get()`, `durable_owner_id()` accessor. `commit_coupling` Literal values: `transactional`/`idempotent_keyed`/`unconfirmed`.
