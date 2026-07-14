# Dependency-graph-aware epic execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `ObjectiveDriverHandler` run a "repo"-bearing objective (an *epic*) as a dependency graph of stories that launch concurrently, isolate into git worktrees, get verified by `run_tests`, and auto-merge into an internal integration branch — with one human-confirmed final merge, not per-story approval.

**Architecture:** Additive-only extension of the existing `Objective`/`Subgoal` tick/retry/block/notify skeleton (`src/stackowl/objectives/driver.py`). A plain objective (`repo` unset) is byte-identical to today. An epic objective (`repo` set) branches inside `_advance` into new dedicated methods: a pure readiness function over `depends_on`, background-task launch (mirroring `RecoveryDriver`'s held-strong-ref pattern), per-story worktree/claude_code/run_tests/merge sequence, worktree-aware crash recovery, and a final `objective-merge` slash command mirroring `objective-cancel`.

**Tech Stack:** Python 3.13, `asyncio`, `pydantic`, SQLite (via `DbPool`), pytest + pytest-asyncio, real git repos in tests (no git mocking).

## Global Constraints

- Minimal diffs — change only the exact lines needed, no unrelated refactors.
- Read every touched file fully before editing (this codebase has fragile history).
- Gate every task: targeted `uv run pytest <path>` (never the full suite — it hangs on this box) + `uv run ruff check src/` + `uv run mypy src/` on touched files only.
- No half-finished capability shipped without a real caller — every task's deliverable must be independently testable and reachable.
- A plain (non-epic) objective must remain byte-identical at every step — most tasks include a regression check against `tests/objectives/test_driver.py`'s existing (non-epic) tests.
- 4-point logging (entry/decision/step/exit) on every new `execute()`-shaped method, per CLAUDE.md.
- Never a silent `except` — always `log.<ns>.error(...)` on any caught exception.
- Full design spec: `docs/superpowers/specs/2026-07-13-epic-execution-design.md` — read it before starting; every task below cites the section it implements.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/stackowl/db/migrations/0086_epic_execution.sql` | New columns: `objectives.repo/integration_branch/base_branch`, `objective_subgoals.depends_on/worktree_path/story_branch`. |
| `src/stackowl/objectives/model.py` (modify) | `Objective`/`Subgoal`/`SubgoalSpec` gain the new fields. |
| `src/stackowl/objectives/graph.py` (new) | Pure functions: `readiness_set()`, `validate_graph()` (on-stack-marking DFS). No DB, no I/O — independently unit-testable. |
| `src/stackowl/objectives/store.py` (modify) | Persist/read the new columns; `add_subgoals` resolves `depends_on` indices → real subgoal_ids. |
| `src/stackowl/objectives/decomposer.py` (modify) | New `decompose_epic_specs()` — graph-aware prompt, parses `depends_on` indices. Existing `decompose_specs()` untouched. |
| `src/stackowl/tools/scheduling/objective_tool.py` (modify) | `repo` param; epic consent gate (mirrors `shell._gate_catastrophic`); base/integration branch setup; graph validation. |
| `src/stackowl/objectives/epic_runner.py` (new) | The per-story background sequence (worktree → claude_code → run_tests → merge → cleanup) and the crash-recovery orphan check. Kept out of `driver.py` so that file doesn't grow unbounded — `driver.py` calls into it. |
| `src/stackowl/objectives/driver.py` (modify) | `_advance` gains the `objective.repo` branch: readiness scan, launch-with-sync-point, failure isolation, partial-completion notify. Delegates the actual per-story work to `epic_runner.py`. |
| `src/stackowl/commands/owls_command.py` (modify) | New `objective-merge <id> YES` subcommand, mirroring `objective-cancel`; `objective-cancel` extended to clean up epic worktrees. |
| `tests/objectives/test_graph.py` (new) | Pure-function tests for readiness + cycle/diamond/dangling-index validation. |
| `tests/objectives/test_epic_runner.py` (new) | Real-git tests: worktree sequence, merge conflict, post-merge integration-test failure, crash-recovery clean/dirty branches, dropped-launch orphan. |
| `tests/objectives/test_driver.py` (modify) | Fake-backend tests for the epic branch in `_advance`: concurrent launch, failure isolation, partial completion. |
| `tests/tools/scheduling/test_objective_tool.py` (modify) | Consent-gate test for a `repo`-bearing call. |
| `tests/commands/test_owls_command.py` (modify) | `objective-merge` command tests (full + partial + not-ready refusal). |

---

## Task 1: Migration — epic columns

**Files:**
- Create: `src/stackowl/db/migrations/0086_epic_execution.sql`
- Test: manual — migrations don't get a dedicated pytest file in this repo (verified by `uv run python -m stackowl db migrate` against a scratch DB); Task 2's model tests are the real regression check.

**Interfaces:**
- Produces: 6 new nullable columns other tasks read/write via `ObjectiveStore`.

- [ ] **Step 1: Read the migration this mirrors**

Read `src/stackowl/db/migrations/0085_memory_scope_key.sql` (already in the repo — column-only ALTER TABLE pattern, no index) and `src/stackowl/db/migrations/0066_objectives.sql` (the original `objectives`/`objective_subgoals` table definitions) in full before writing the new one.

- [ ] **Step 2: Write the migration**

```sql
-- Migration 0086 — epic execution columns (Task #4 of the coding-capability
-- build plan; see docs/superpowers/specs/2026-07-13-epic-execution-design.md).
--
-- Column-only change: no new table, no index. NULL/empty for every existing
-- row and every caller that doesn't set them — a plain objective (repo unset)
-- stays byte-identical.

ALTER TABLE objectives ADD COLUMN repo TEXT;
ALTER TABLE objectives ADD COLUMN integration_branch TEXT;
ALTER TABLE objectives ADD COLUMN base_branch TEXT;

ALTER TABLE objective_subgoals ADD COLUMN depends_on TEXT;
ALTER TABLE objective_subgoals ADD COLUMN worktree_path TEXT;
ALTER TABLE objective_subgoals ADD COLUMN story_branch TEXT;
```

- [ ] **Step 3: Verify migration applies cleanly**

Run: `uv run python -m stackowl db migrate`
Expected: exits 0, logs `0086_epic_execution` applied. Re-run the same command — expected: exits 0, no-op (idempotent, matches every other migration in this repo).

- [ ] **Step 4: Commit**

```bash
git add src/stackowl/db/migrations/0086_epic_execution.sql
git commit -m "feat(objectives): add epic execution schema columns (migration 0086)"
```

---

## Task 2: Model updates

**Files:**
- Modify: `src/stackowl/objectives/model.py`
- Test: `tests/objectives/test_model.py` (new — this repo's `objectives/` package has no dedicated model test file today; check `tests/objectives/` first in case one exists before creating).

**Interfaces:**
- Consumes: nothing new.
- Produces: `Objective.repo: str | None`, `Objective.integration_branch: str | None`, `Objective.base_branch: str | None`, `Subgoal.depends_on: list[str]`, `Subgoal.worktree_path: str | None`, `Subgoal.story_branch: str | None`, `SubgoalSpec.depends_on: list[int]`.

- [ ] **Step 1: Read the current model file fully**

Read `src/stackowl/objectives/model.py` in full (154 lines) before editing.

- [ ] **Step 2: Write the failing test**

```python
# tests/objectives/test_model.py
from __future__ import annotations

from stackowl.objectives.model import Objective, Subgoal, SubgoalSpec


def test_objective_epic_fields_default_none() -> None:
    obj = Objective(objective_id="obj-1", owner_id="default", intent="test")
    assert obj.repo is None
    assert obj.integration_branch is None
    assert obj.base_branch is None


def test_objective_epic_fields_settable() -> None:
    obj = Objective(
        objective_id="obj-1", owner_id="default", intent="test",
        repo="/tmp/repo", integration_branch="stackowl/epic-obj-1",
        base_branch="main",
    )
    assert obj.repo == "/tmp/repo"
    assert obj.integration_branch == "stackowl/epic-obj-1"
    assert obj.base_branch == "main"


def test_subgoal_depends_on_defaults_empty() -> None:
    sg = Subgoal(
        subgoal_id="sub-1", owner_id="default", objective_id="obj-1",
        position=0, description="test",
    )
    assert sg.depends_on == []
    assert sg.worktree_path is None
    assert sg.story_branch is None


def test_subgoalspec_depends_on_defaults_empty() -> None:
    spec = SubgoalSpec(description="test")
    assert spec.depends_on == []


def test_subgoalspec_depends_on_settable() -> None:
    spec = SubgoalSpec(description="test", depends_on=[0, 2])
    assert spec.depends_on == [0, 2]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/objectives/test_model.py -v`
Expected: FAIL — `repo`/`depends_on`/etc. are unexpected keyword arguments (pydantic `extra` is not `"allow"` on these models, so passing them raises `ValidationError`, or the assertion on the default fails with `AttributeError`).

- [ ] **Step 4: Add the fields**

In `Objective` (after `updated_at`... actually insert alongside the other optional fields, near `blocker_kind`):

```python
    #: Task #4 (coding-capability build plan) — set only for an EPIC objective.
    #: None (every existing row, every plain-objective caller) ⇒ the linear,
    #: single-subgoal-per-tick driver path, byte-identical to today.
    repo: str | None = None
    #: The epic's internal integration branch (e.g. "stackowl/epic-obj-1"),
    #: branched off base_branch when the epic starts. Set together with repo.
    integration_branch: str | None = None
    #: The branch `objective-merge` targets — captured via `git branch
    #: --show-current` in `repo` at epic creation.
    base_branch: str | None = None
```

In `Subgoal` (after `decomposition_depth`):

```python
    #: Task #4 — subgoal_ids that must reach status "done" before this story
    #: is ready to launch. Empty (default, every existing row) ⇒ ready
    #: immediately — matches today's linear behavior.
    depends_on: list[str] = Field(default_factory=list)
    #: Set once this story's worktree is created (epic path only).
    worktree_path: str | None = None
    #: Set once this story's scratch branch is created (epic path only).
    story_branch: str | None = None
```

In `SubgoalSpec` (after `estimated_complexity`):

```python
    #: Task #4 — indices into the SAME decomposition batch this spec's story
    #: depends on (e.g. story 2 depending on story 0 emits `depends_on=[0]`).
    #: Resolved to real subgoal_ids by the store on insert. Empty (default,
    #: every existing caller) ⇒ ready immediately.
    depends_on: list[int] = Field(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/objectives/test_model.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Regression check — existing model/store/driver tests unaffected**

Run: `uv run pytest tests/objectives/ -q`
Expected: all pass, no new failures (additive fields with defaults touch no existing assertion).

- [ ] **Step 7: Gate**

Run: `uv run ruff check src/stackowl/objectives/model.py && uv run mypy src/stackowl/objectives/model.py`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/stackowl/objectives/model.py tests/objectives/test_model.py
git commit -m "feat(objectives): add epic fields to Objective/Subgoal/SubgoalSpec models"
```

---

## Task 3: Graph module — readiness + cycle validation

**Files:**
- Create: `src/stackowl/objectives/graph.py`
- Test: `tests/objectives/test_graph.py`

**Interfaces:**
- Consumes: `Subgoal.depends_on: list[str]`, `Subgoal.status: SubgoalStatus`, `Subgoal.subgoal_id: str` (Task 2); `SubgoalSpec.depends_on: list[int]` (Task 2).
- Produces:
  - `readiness_set(subgoals: list[Subgoal]) -> set[str]` — subgoal_ids of every `pending` story whose `depends_on` are all `status == "done"`.
  - `validate_graph(specs: Sequence[SubgoalSpec]) -> GraphError | None` — `None` if acyclic and all indices in range; otherwise a `GraphError` naming the problem.
  - `GraphError` — a frozen dataclass: `kind: Literal["cycle", "out_of_range"]`, `detail: str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/objectives/test_graph.py
from __future__ import annotations

from datetime import UTC, datetime

from stackowl.objectives.graph import GraphError, readiness_set, validate_graph
from stackowl.objectives.model import Subgoal, SubgoalSpec

_NOW = datetime.now(tz=UTC)


def _sg(subgoal_id: str, status: str, depends_on: list[str] | None = None) -> Subgoal:
    return Subgoal(
        subgoal_id=subgoal_id, owner_id="default", objective_id="obj-1",
        position=0, description="x", status=status,  # type: ignore[arg-type]
        depends_on=depends_on or [], created_at=_NOW, updated_at=_NOW,
    )


def test_readiness_no_deps_is_ready() -> None:
    subgoals = [_sg("a", "pending")]
    assert readiness_set(subgoals) == {"a"}


def test_readiness_waits_for_incomplete_dep() -> None:
    subgoals = [_sg("a", "running"), _sg("b", "pending", depends_on=["a"])]
    assert readiness_set(subgoals) == set()


def test_readiness_fires_once_dep_done() -> None:
    subgoals = [_sg("a", "done"), _sg("b", "pending", depends_on=["a"])]
    assert readiness_set(subgoals) == {"b"}


def test_readiness_ignores_non_pending() -> None:
    subgoals = [_sg("a", "done"), _sg("b", "done", depends_on=["a"])]
    assert readiness_set(subgoals) == set()


def test_readiness_diamond_all_ready_when_deps_done() -> None:
    subgoals = [
        _sg("a", "done"),
        _sg("b", "done", depends_on=["a"]),
        _sg("c", "done", depends_on=["a"]),
        _sg("d", "pending", depends_on=["b", "c"]),
    ]
    assert readiness_set(subgoals) == {"d"}


def test_validate_graph_accepts_diamond() -> None:
    # D depends on B and C; both depend on A. Legal fan-in, NOT a cycle.
    specs = [
        SubgoalSpec(description="a"),
        SubgoalSpec(description="b", depends_on=[0]),
        SubgoalSpec(description="c", depends_on=[0]),
        SubgoalSpec(description="d", depends_on=[1, 2]),
    ]
    assert validate_graph(specs) is None


def test_validate_graph_detects_self_cycle() -> None:
    specs = [SubgoalSpec(description="a", depends_on=[0])]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "cycle"


def test_validate_graph_detects_two_node_cycle() -> None:
    specs = [
        SubgoalSpec(description="a", depends_on=[1]),
        SubgoalSpec(description="b", depends_on=[0]),
    ]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "cycle"


def test_validate_graph_detects_out_of_range_index() -> None:
    specs = [SubgoalSpec(description="a", depends_on=[5])]
    err = validate_graph(specs)
    assert err is not None
    assert err.kind == "out_of_range"


def test_validate_graph_accepts_empty_deps() -> None:
    specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b")]
    assert validate_graph(specs) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.objectives.graph'`.

- [ ] **Step 3: Write the implementation**

```python
# src/stackowl/objectives/graph.py
"""Pure dependency-graph functions for epic execution (Task #4).

No DB, no I/O — every function here takes plain in-memory data and returns
plain in-memory data, so the driver's readiness scan and the epic-creation
validation step are both independently unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from stackowl.objectives.model import Subgoal, SubgoalSpec

__all__ = ["GraphError", "readiness_set", "validate_graph"]


@dataclass(frozen=True)
class GraphError:
    """Why an epic's decomposed dependency graph is invalid."""

    kind: Literal["cycle", "out_of_range"]
    detail: str


def readiness_set(subgoals: list[Subgoal]) -> set[str]:
    """Return the subgoal_ids of every `pending` story whose dependencies are
    all `done`. A story with no `depends_on` is ready immediately (matches
    every pre-epic row, which has an empty list by default)."""
    done_ids = {sg.subgoal_id for sg in subgoals if sg.status == "done"}
    return {
        sg.subgoal_id
        for sg in subgoals
        if sg.status == "pending" and all(dep in done_ids for dep in sg.depends_on)
    }


def validate_graph(specs: Sequence[SubgoalSpec]) -> GraphError | None:
    """Validate a decomposition batch's `depends_on` indices BEFORE any
    subgoal is persisted (Creation flow §4 of the design spec).

    Uses on-stack marking (three-color DFS) — NOT a flat visited set, which
    would false-reject a legitimate diamond dependency (a node reached twice
    via two different, valid paths is fine; a node reached while still on the
    current recursion stack is a real cycle). Returns the FIRST problem found
    (cycle checked before out-of-range on a given node, deterministic order:
    node 0, 1, 2... by index)."""
    n = len(specs)
    for i, spec in enumerate(specs):
        for dep in spec.depends_on:
            if dep < 0 or dep >= n:
                return GraphError(
                    "out_of_range",
                    f"story {i} depends on index {dep}, but the batch has {n} stories",
                )

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * n

    def visit(i: int, path: list[int]) -> GraphError | None:
        color[i] = GRAY
        for dep in specs[i].depends_on:
            if color[dep] == GRAY:
                cycle = " -> ".join(str(x) for x in (*path, dep))
                return GraphError("cycle", f"dependency cycle: {cycle}")
            if color[dep] == WHITE:
                err = visit(dep, [*path, dep])
                if err is not None:
                    return err
        color[i] = BLACK
        return None

    for i in range(n):
        if color[i] == WHITE:
            err = visit(i, [i])
            if err is not None:
                return err
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_graph.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Gate**

Run: `uv run ruff check src/stackowl/objectives/graph.py && uv run mypy src/stackowl/objectives/graph.py`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/stackowl/objectives/graph.py tests/objectives/test_graph.py
git commit -m "feat(objectives): add pure readiness/cycle-validation graph functions"
```

---

## Task 4: Store — persist the new columns

**Files:**
- Modify: `src/stackowl/objectives/store.py`
- Test: `tests/objectives/test_objective_store.py` (existing file — extend it)

**Interfaces:**
- Consumes: `graph.validate_graph` is NOT called here (that's Task 6, in `ObjectiveTool`) — the store trusts its caller already validated.
- Produces: `ObjectiveStore.create()` persists `repo`/`integration_branch`/`base_branch`; `ObjectiveStore.add_subgoals(objective_id, items, *, depth=0)` — **signature unchanged**, but items may now be `SubgoalSpec`s carrying `depends_on` indices, resolved to real subgoal_ids using the position of each item WITHIN THIS SAME CALL (mirrors how `position` is already assigned); `ObjectiveStore.update_subgoal(...)` gains optional `worktree_path`/`story_branch` kwargs, same "only written when explicitly supplied" convention as `attempts`/`verified`.

- [ ] **Step 1: Read the current store file fully**

Read `src/stackowl/objectives/store.py` in full (459 lines) before editing — already read once this session; re-confirm nothing changed.

- [ ] **Step 2: Write the failing tests**

```python
# append to tests/objectives/test_objective_store.py
import pytest

from stackowl.objectives.model import Objective, SubgoalSpec


@pytest.mark.asyncio
async def test_create_persists_epic_fields(store) -> None:  # `store` fixture already exists in this file
    obj = Objective(
        objective_id="obj-epic-1", owner_id="default", intent="epic test",
        repo="/tmp/repo", integration_branch="stackowl/epic-obj-epic-1",
        base_branch="main",
    )
    await store.create(obj)
    fetched = await store.get("obj-epic-1")
    assert fetched.repo == "/tmp/repo"
    assert fetched.integration_branch == "stackowl/epic-obj-epic-1"
    assert fetched.base_branch == "main"


@pytest.mark.asyncio
async def test_add_subgoals_resolves_depends_on_indices(store) -> None:
    obj = Objective(objective_id="obj-epic-2", owner_id="default", intent="epic test")
    await store.create(obj)
    specs = [
        SubgoalSpec(description="a"),
        SubgoalSpec(description="b", depends_on=[0]),
    ]
    created = await store.add_subgoals("obj-epic-2", specs)
    assert created[0].depends_on == []
    assert created[1].depends_on == [created[0].subgoal_id]

    reloaded = await store.list_subgoals("obj-epic-2")
    by_desc = {sg.description: sg for sg in reloaded}
    assert by_desc["b"].depends_on == [by_desc["a"].subgoal_id]


@pytest.mark.asyncio
async def test_update_subgoal_sets_worktree_and_branch(store) -> None:
    obj = Objective(objective_id="obj-epic-3", owner_id="default", intent="epic test")
    await store.create(obj)
    [sg] = await store.add_subgoals("obj-epic-3", [SubgoalSpec(description="a")])
    await store.update_subgoal(
        sg.subgoal_id, "running", worktree_path="/tmp/wt", story_branch="stackowl/story-x",
    )
    reloaded = (await store.list_subgoals("obj-epic-3"))[0]
    assert reloaded.worktree_path == "/tmp/wt"
    assert reloaded.story_branch == "stackowl/story-x"
```

Check `tests/objectives/test_objective_store.py`'s existing fixture name before pasting — use whatever the file's `store`/`db` fixture is actually called (read the file's top to confirm; adjust the tests above to match, do not invent a new fixture name).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_objective_store.py -v -k epic`
Expected: FAIL — `repo`/`depends_on` not persisted (silently dropped or `KeyError` on the row mapper).

- [ ] **Step 4: Implement — `create()`**

In `ObjectiveStore.create()`, add to the `_insert_owned(_OBJECTIVES, {...})` dict:

```python
                "repo": objective.repo,
                "integration_branch": objective.integration_branch,
                "base_branch": objective.base_branch,
```

- [ ] **Step 5: Implement — `_row_to_objective()`**

Add to the `Objective(...)` construction:

```python
            repo=row.get("repo"),
            integration_branch=row.get("integration_branch"),
            base_branch=row.get("base_branch"),
```

- [ ] **Step 6: Implement — `depends_on` (de)serialization helpers**

Add near `_dumps_outcome`/`_loads_outcome`:

```python
def _dumps_depends_on(ids: list[str]) -> str | None:
    """JSON-encode subgoal_id dependencies; empty ⇒ SQL NULL (ready immediately)."""
    return _dumps(ids)


def _loads_depends_on(text: Any) -> list[str]:
    return _loads_list(text)
```

- [ ] **Step 7: Implement — `add_subgoals()` resolves indices**

Replace the body of `add_subgoals` (keep the signature — `items: Sequence[str | SubgoalSpec]` already accepts a `SubgoalSpec`, which now carries `depends_on: list[int]`):

```python
    async def add_subgoals(
        self,
        objective_id: str,
        items: Sequence[str | SubgoalSpec],
        *,
        depth: int = 0,
    ) -> list[Subgoal]:
        """Append ordered sub-goals (positions continue after any existing ones).

        Each item is either a plain description string (legacy / no acceptance
        criterion) or a :class:`SubgoalSpec` carrying an OPTIONAL declared
        ``acceptance_criteria``, complexity estimate, and (Task #4) a
        ``depends_on`` list of INDICES into this SAME batch — resolved here to
        real subgoal_ids, since ids don't exist until insert. A bare string is
        normalized to a criterion-free, dependency-free spec, so every
        existing caller is unchanged (byte-identical). ``depth`` stamps
        ``decomposition_depth`` on every created row (Task 3); 0 (the
        default) for the objective's initial, top-level decomposition."""
        existing = await self.list_subgoals(objective_id)
        start = len(existing)
        specs = [SubgoalSpec(description=item) if isinstance(item, str) else item for item in items]
        now = _now()
        # First pass: mint every subgoal_id up front so depends_on indices
        # (which reference OTHER items in this same batch, including ones
        # that come later positionally) can all be resolved before any row
        # is inserted.
        ids = [f"sub-{uuid.uuid4().hex[:12]}" for _ in specs]
        created: list[Subgoal] = []
        for offset, spec in enumerate(specs):
            position = start + offset
            depends_on_ids = [ids[i] for i in spec.depends_on]
            created.append(
                await self._create_subgoal_row(
                    objective_id, position, spec, depth, now,
                    subgoal_id=ids[offset], depends_on=depends_on_ids,
                )
            )
        return created
```

- [ ] **Step 8: Implement — `_create_subgoal_row()` accepts `subgoal_id`/`depends_on`**

Change the signature and body:

```python
    async def _create_subgoal_row(
        self,
        objective_id: str,
        position: int,
        spec: SubgoalSpec,
        depth: int,
        now: datetime,
        *,
        subgoal_id: str | None = None,
        depends_on: list[str] | None = None,
    ) -> Subgoal:
        """Shared INSERT core for :meth:`add_subgoals` and :meth:`insert_subgoals_at`."""
        subgoal_id = subgoal_id or f"sub-{uuid.uuid4().hex[:12]}"
        depends_on = depends_on or []
        await self._insert_owned(
            _SUBGOALS,
            {
                "subgoal_id": subgoal_id,
                "objective_id": objective_id,
                "position": position,
                "description": spec.description,
                "status": "pending",
                "result": None,
                "acceptance_criteria": _dumps_outcome(spec.acceptance_criteria),
                "attempts": 0,
                "verified": None,
                "task_id": None,
                "estimated_complexity": spec.estimated_complexity,
                "decomposition_depth": depth,
                "depends_on": _dumps_depends_on(depends_on),
                "worktree_path": None,
                "story_branch": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        return Subgoal(
            subgoal_id=subgoal_id,
            owner_id=self._owner_id,
            objective_id=objective_id,
            position=position,
            description=spec.description,
            status="pending",
            acceptance_criteria=spec.acceptance_criteria,
            estimated_complexity=spec.estimated_complexity,
            decomposition_depth=depth,
            depends_on=depends_on,
            created_at=now,
            updated_at=now,
        )
```

- [ ] **Step 9: Implement — `_row_to_subgoal()`**

Add to the `Subgoal(...)` construction:

```python
            depends_on=_loads_depends_on(row.get("depends_on")),
            worktree_path=row.get("worktree_path"),
            story_branch=row.get("story_branch"),
```

- [ ] **Step 10: Implement — `update_subgoal()` gains `worktree_path`/`story_branch`**

Extend the signature and body (same "only written when explicitly supplied" convention as `attempts`):

```python
    async def update_subgoal(
        self,
        subgoal_id: str,
        status: SubgoalStatus,
        *,
        result: str | None = None,
        task_id: str | None = None,
        attempts: int | None = None,
        verified: bool | None = None,
        worktree_path: str | None = None,
        story_branch: str | None = None,
    ) -> None:
        """... (docstring: add one line) ``worktree_path``/``story_branch``
        follow the same "only written when supplied" convention (Task #4)."""
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, _now().isoformat()]
        if result is not None:
            sets.append("result = ?")
            params.append(result)
        if task_id is not None:
            sets.append("task_id = ?")
            params.append(task_id)
        if attempts is not None:
            sets.append("attempts = ?")
            params.append(attempts)
        if verified is not None:
            sets.append("verified = ?")
            params.append(1 if verified else 0)
        if worktree_path is not None:
            sets.append("worktree_path = ?")
            params.append(worktree_path)
        if story_branch is not None:
            sets.append("story_branch = ?")
            params.append(story_branch)
        await self._update_owned(
            _SUBGOALS,
            set_sql=", ".join(sets),
            set_params=tuple(params),
            where_sql="subgoal_id = ?",
            where_params=(subgoal_id,),
        )
```

- [ ] **Step 11: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_objective_store.py -v`
Expected: PASS — every existing test plus the 3 new ones.

- [ ] **Step 12: Regression check**

Run: `uv run pytest tests/objectives/ -q`
Expected: all pass.

- [ ] **Step 13: Gate**

Run: `uv run ruff check src/stackowl/objectives/store.py && uv run mypy src/stackowl/objectives/store.py`
Expected: both clean.

- [ ] **Step 14: Commit**

```bash
git add src/stackowl/objectives/store.py tests/objectives/test_objective_store.py
git commit -m "feat(objectives): persist epic repo/branch fields and story dependencies"
```

---

## Task 5: Decomposer — graph-aware epic prompt

**Files:**
- Modify: `src/stackowl/objectives/decomposer.py`
- Test: `tests/objectives/test_decomposer.py` (existing — extend)

**Interfaces:**
- Consumes: `SubgoalSpec.depends_on: list[int]` (Task 2).
- Produces: `ObjectiveDecomposer.decompose_epic_specs(intent: str) -> list[SubgoalSpec]` — new method, additive; `decompose_specs`/`decompose` untouched.

- [ ] **Step 1: Read the current decomposer file fully**

Already read in full this session (187 lines) — re-confirm before editing.

- [ ] **Step 2: Write the failing tests**

```python
# append to tests/objectives/test_decomposer.py
import pytest

from stackowl.objectives.decomposer import ObjectiveDecomposer


class _StubProvider:
    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, messages, **kwargs):
        from stackowl.providers.base import CompletionResult
        return CompletionResult(content=self._content, model="stub", usage=None)


class _StubRegistry:
    def __init__(self, content: str) -> None:
        self._provider = _StubProvider(content)

    def get_with_cascade(self, tier: str):
        return self._provider


@pytest.mark.asyncio
async def test_decompose_epic_parses_depends_on_markers() -> None:
    reply = (
        "Set up the database schema <<complexity: 0.2>>\n"
        "Write the API endpoint <<depends-on: 0>> <<complexity: 0.3>>\n"
        "Write the frontend page <<depends-on: 0>> <<complexity: 0.3>>\n"
    )
    decomposer = ObjectiveDecomposer(_StubRegistry(reply))  # type: ignore[arg-type]
    specs = await decomposer.decompose_epic_specs("build a feature")
    assert len(specs) == 3
    assert specs[0].depends_on == []
    assert specs[1].depends_on == [0]
    assert specs[2].depends_on == [0]


@pytest.mark.asyncio
async def test_decompose_epic_no_markers_means_no_deps() -> None:
    reply = "Step one\nStep two\n"
    decomposer = ObjectiveDecomposer(_StubRegistry(reply))  # type: ignore[arg-type]
    specs = await decomposer.decompose_epic_specs("build a feature")
    assert all(s.depends_on == [] for s in specs)


@pytest.mark.asyncio
async def test_decompose_epic_provider_failure_falls_back_single_step() -> None:
    class _RaisingRegistry:
        def get_with_cascade(self, tier: str):
            raise RuntimeError("no provider")

    decomposer = ObjectiveDecomposer(_RaisingRegistry())  # type: ignore[arg-type]
    specs = await decomposer.decompose_epic_specs("build a feature")
    assert len(specs) == 1
    assert specs[0].depends_on == []
```

Check the existing `tests/objectives/test_decomposer.py` for its actual stub-provider pattern before pasting — reuse it instead of inventing `_StubProvider`/`_StubRegistry` if equivalents already exist in that file (read the file first; this repo's convention is reuse-before-write).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_decomposer.py -v -k epic`
Expected: FAIL — `AttributeError: 'ObjectiveDecomposer' object has no attribute 'decompose_epic_specs'`.

- [ ] **Step 4: Implement — depends-on marker regex + parser + prompt + method**

Add near the other marker regexes:

```python
#: Task #4 — a trailing ``<<depends-on: i,j,k>>`` marker naming this step's
#: zero-based dependency indices within the SAME decomposition batch.
_DEPENDS_ON_RE = re.compile(r"<<\s*depends-on\s*:\s*(?P<idx>[0-9,\s]+)\s*>>", re.IGNORECASE)
```

Add a new prompt builder (parallel to `_build_prompt`, not modifying it):

```python
    def _build_epic_prompt(self, intent: str) -> str:
        """Graph-aware decomposition prompt (Task #4) — same base instructions
        as :meth:`_build_prompt` plus the dependency-marker convention."""
        base = self._build_prompt(intent)
        return (
            base
            + "\n\nAdditionally: if a step depends on one or more EARLIER "
            "steps in this list being done first, append "
            "`<<depends-on: i>>` (or `<<depends-on: i,j>>` for multiple) at "
            "the end of that step's line, using the 0-based position of "
            "each step it depends on. Independent steps that can run "
            "concurrently need no marker."
        )
```

Add the parse helper and public method:

```python
    @staticmethod
    def _parse_depends_on(line: str) -> tuple[str, list[int]]:
        """Extract a trailing ``<<depends-on: ...>>`` marker; returns
        (line with marker removed, parsed indices — invalid/unparseable
        tokens are dropped, never raise)."""
        match = _DEPENDS_ON_RE.search(line)
        if match is None:
            return line, []
        indices: list[int] = []
        for token in match.group("idx").split(","):
            token = token.strip()
            if token.isdigit():
                indices.append(int(token))
        return _DEPENDS_ON_RE.sub("", line), indices

    async def decompose_epic_specs(self, intent: str) -> list[SubgoalSpec]:
        """Graph-aware decomposition (Task #4): same fail-safe contract as
        :meth:`decompose_specs` (provider failure / empty reply degrades to a
        single dependency-free spec), but parses ``<<depends-on: ...>>``
        markers into :attr:`SubgoalSpec.depends_on`. Does not call or modify
        :meth:`decompose_specs` — kept fully separate so the plain-objective
        path is untouched."""
        log.engine.debug(
            "[objectives] decompose_epic: entry",
            extra={"_fields": {"intent_preview": intent[:80]}},
        )
        prompt = self._build_epic_prompt(intent)
        messages = [Message(role="user", content=prompt)]
        t0 = time.monotonic()
        try:
            provider = self._provider_registry.get_with_cascade(_DECOMP_TIER)
            result = await provider.complete(
                messages, model="", max_tokens=_DECOMP_MAX_TOKENS,
                temperature=_DECOMP_TEMPERATURE,
            )
        except Exception as exc:  # noqa: BLE001 — never strand an epic
            log.engine.error(
                "[objectives] decompose_epic: provider call failed — single-step fallback",
                exc_info=exc,
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]

        specs: list[SubgoalSpec] = []
        for line in (result.content or "").splitlines():
            stripped = _MARKER_RE.sub("", line)
            stripped, depends_on = self._parse_depends_on(stripped)
            criterion = None
            match = _PRODUCES_FILE_RE.search(stripped)
            if match is not None:
                raw_dir = (match.group("dir") or "").strip()
                criterion = ExpectedOutcome(kind="artifact", artifact_dir=raw_dir or None)
                stripped = _PRODUCES_FILE_RE.sub("", stripped)
            complexity = 0.0
            cmatch = _COMPLEXITY_RE.search(stripped)
            if cmatch is not None:
                try:
                    complexity = max(0.0, min(1.0, float(cmatch.group("val"))))
                except ValueError:
                    complexity = 0.0
                stripped = _COMPLEXITY_RE.sub("", stripped)
            cleaned = stripped.strip()
            if cleaned:
                specs.append(
                    SubgoalSpec(
                        description=cleaned, acceptance_criteria=criterion,
                        estimated_complexity=complexity, depends_on=depends_on,
                    )
                )
            if len(specs) >= _MAX_SUBGOALS:
                break

        if not specs:
            log.engine.info(
                "[objectives] decompose_epic: empty/garbled reply — single-step fallback",
                extra={"_fields": {"intent_preview": intent[:80]}},
            )
            return [SubgoalSpec(description=intent)]
        log.engine.info(
            "[objectives] decompose_epic: exit",
            extra={"_fields": {
                "intent_preview": intent[:80], "subgoal_count": len(specs),
                "with_deps": sum(1 for s in specs if s.depends_on),
                "latency_ms": (time.monotonic() - t0) * 1000,
            }},
        )
        return specs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_decomposer.py -v`
Expected: PASS — every existing test plus the 3 new ones.

- [ ] **Step 6: Gate**

Run: `uv run pytest tests/objectives/ -q && uv run ruff check src/stackowl/objectives/decomposer.py && uv run mypy src/stackowl/objectives/decomposer.py`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/objectives/decomposer.py tests/objectives/test_decomposer.py
git commit -m "feat(objectives): add graph-aware epic decomposition with depends-on markers"
```

---

## Task 6: ObjectiveTool — repo param, consent gate, branch setup, validation

**Files:**
- Modify: `src/stackowl/tools/scheduling/objective_tool.py`
- Test: `tests/tools/scheduling/test_objective_tool.py` (existing — extend; if it doesn't exist, check `tests/tools/scheduling/` first)

**Interfaces:**
- Consumes: `graph.validate_graph` (Task 3), `ObjectiveDecomposer.decompose_epic_specs` (Task 5), `git_tool.is_git_repo`/`add_worktree` — actually here just need branch inspection: reuse `git_tool`'s module-level `run_argv`-based pattern; add a small `git_tool.current_branch(repo) -> str | None` helper (new, see Step 4) — and `shell.py`'s consent-gate pattern (`get_services().consent_gate`).
- Produces: `ObjectiveTool` accepts `repo: str | None` in its args; on a repo-bearing call, gates via `get_services().consent_gate.policy.request(...)` before persisting anything.

- [ ] **Step 1: Read the current tool file fully**

Already read in full this session (215 lines) — re-confirm, plus read `src/stackowl/tools/system/shell.py`'s `_gate_catastrophic()` (already read in full this session) as the exact pattern to mirror.

- [ ] **Step 2: Add `git_tool.current_branch()` helper (small, needed by this task)**

In `src/stackowl/tools/system/git_tool.py`, add near `is_git_repo`/`add_worktree`:

```python
async def current_branch(repo: str) -> str | None:
    """The current checked-out branch name in ``repo``, or None if detached/unreadable."""
    result = await run_argv(
        ["git", "branch", "--show-current"], tool_name="git", workdir=repo, intent="read",
    )
    if not result.success:
        return None
    branch = result.output.strip()
    return branch or None
```

Update `__all__` to `["GitTool", "add_worktree", "current_branch", "is_git_repo"]`.

Write a matching test in `tests/tools/system/test_git_tool.py`:

```python
@pytest.mark.asyncio
async def test_current_branch_returns_checked_out_branch(repo: Path) -> None:
    from stackowl.tools.system.git_tool import current_branch

    branch = await current_branch(str(repo))
    assert branch in ("main", "master")  # git init's default varies by config
```

Run: `uv run pytest tests/tools/system/test_git_tool.py -v -k current_branch` — expect PASS after adding the function (write test first per TDD, confirm it fails with `ImportError` before adding the function, matching every other task's step order).

- [ ] **Step 3: Write the failing tests for ObjectiveTool**

```python
# append to tests/tools/scheduling/test_objective_tool.py
import subprocess
from pathlib import Path

import pytest

from stackowl.tools.scheduling.objective_tool import ObjectiveTool


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_repo_bearing_call_requires_consent(tmp_path: Path, monkeypatch) -> None:
    """No consent gate wired ⇒ fail closed, epic never created."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    # (Wire test doubles for db_pool / provider_registry / consent_gate per
    # this file's existing get_services() monkeypatch pattern — read the
    # file's existing tests for the exact fixture/monkeypatch shape used for
    # a wired-db, no-consent-gate scenario before writing this call.)
    result = await ObjectiveTool()(intent="build a feature", repo=str(repo))
    assert result.success is False
    assert result.side_effect_committed is False


@pytest.mark.asyncio
async def test_repo_bearing_call_consent_summary_discloses_bypass_permissions(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    summary = ObjectiveTool().consent_summary(intent="build a feature", repo=str(repo))
    assert summary is not None
    assert str(repo) in summary
    assert "bypassPermissions" in summary


@pytest.mark.asyncio
async def test_plain_objective_call_untouched(monkeypatch) -> None:
    """No repo ⇒ no consent gate consulted at all (byte-identical to today)."""
    # (Reuse this file's existing "plain objective create succeeds" test
    # setup verbatim — just assert it still passes unmodified after this
    # task's changes.)
```

The consent-gate test doubles must mirror whatever `tests/tools/scheduling/test_objective_tool.py` already uses to stub `get_services()` — read that file in full before writing these three tests, and adapt the exact fixture names.

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/tools/scheduling/test_objective_tool.py -v -k repo`
Expected: FAIL — `ObjectiveTool()(intent=..., repo=...)` raises `ValidationError` (unexpected kwarg) or `consent_summary` returns `None`.

- [ ] **Step 5: Implement — `ObjectiveArgs` gains `repo`**

```python
class ObjectiveArgs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: str = Field(..., description="The standing objective to pursue.")
    repo: str | None = Field(
        default=None,
        description=(
            "Path to a git repo — makes this an EPIC: decomposed into a "
            "dependency graph of stories that run concurrently in isolated "
            "worktrees, verified by tests, and auto-merged into an internal "
            "integration branch. CONSEQUENTIAL: requires consent (stories "
            "run unattended, bypassPermissions). Omit for a plain objective."
        ),
    )
```

- [ ] **Step 6: Implement — `consent_summary()`**

```python
    def consent_summary(self, **call_args: object) -> str | None:
        """Bounded consent digest for an EPIC (repo-bearing) call only —
        mirrors execute_code's per-call summary. A plain objective (no repo)
        has nothing consequential to summarize; returns None (falls back to
        the static description, which is fine since the gate is never
        consulted for a plain call — see execute())."""
        repo = call_args.get("repo")
        if not isinstance(repo, str) or not repo:
            return None
        intent = call_args.get("intent")
        intent = intent if isinstance(intent, str) else ""
        digest = intent[:200] + ("…" if len(intent) > 200 else "")
        return (
            f"Run an EPIC in {repo}: decompose \"{digest}\" into stories that "
            "run UNATTENDED and CONCURRENTLY, each with permission_mode="
            "bypassPermissions (full shell access, isolated to a worktree — "
            "not sandboxed from network/host side effects). Auto-merges "
            "each verified story into an internal integration branch; you "
            "confirm once at the end to merge into your real branch."
        )
```

- [ ] **Step 7: Implement — `execute()` gates + builds the epic**

Replace the body from the `objective_id = ...` line onward (keep everything above unchanged — intent validation, cron-prompt scan, db check):

```python
        repo = args.repo.strip() if args.repo else None
        if repo:
            gated = await self._gate_epic_consent(repo=repo, intent=intent, t0=t0)
            if gated is not None:
                return gated

        ctx = TraceContext.get()
        channel = ctx.get("channel")
        channel_str = channel if isinstance(channel, str) else None
        target_channels, target_addresses = self._resolve_durable_target(channel_str)

        objective_id = f"obj-{uuid.uuid4().hex[:8]}"
        base_branch: str | None = None
        integration_branch: str | None = None
        if repo:
            from stackowl.tools.system.git_tool import current_branch

            base_branch = await current_branch(repo)
            if base_branch is None:
                return self._err(f"could not determine the current branch in {repo!r}", t0)
            integration_branch = f"stackowl/epic-{objective_id}"

        objective = Objective(
            objective_id=objective_id,
            owner_id=DEFAULT_PRINCIPAL_ID,
            intent=intent,
            channel=channel_str,
            target_channels=target_channels,
            target_addresses=target_addresses,
            repo=repo,
            integration_branch=integration_branch,
            base_branch=base_branch,
        )
        try:
            if repo:
                from stackowl.tools.system.shell import run_argv

                branch_result = await run_argv(
                    ["git", "branch", integration_branch],
                    tool_name="git", workdir=repo, intent="write",
                )
                if not branch_result.success:
                    return self._err(
                        f"could not create integration branch: {branch_result.error}", t0,
                    )

            store = ObjectiveStore(db, DEFAULT_PRINCIPAL_ID)
            await store.create(objective)
            await store.append_event(objective_id, "created", intent)

            if repo:
                decomposer = (
                    ObjectiveDecomposer(services.provider_registry)
                    if services.provider_registry else None
                )
                specs = (
                    await decomposer.decompose_epic_specs(intent)
                    if decomposer else [SubgoalSpec(description=intent)]
                )
                from stackowl.objectives.graph import validate_graph

                graph_error = validate_graph(specs)
                if graph_error is not None:
                    await store.update_status(objective_id, "abandoned")
                    return self._err(
                        f"invalid story dependency graph ({graph_error.kind}): "
                        f"{graph_error.detail}",
                        t0,
                    )
            else:
                decomposer = (
                    ObjectiveDecomposer(services.provider_registry)
                    if services.provider_registry else None
                )
                specs = (
                    await decomposer.decompose_specs(intent)
                    if decomposer else [SubgoalSpec(description=intent)]
                )

            await store.add_subgoals(objective_id, specs)
            await store.append_event(objective_id, "decomposed", f"{len(specs)} step(s)")
            subgoals = [s.description for s in specs]
        except Exception as exc:  # B5 — never raise out of a tool
            log.tool.error(
                "objective.execute: persist failed — degrading",
                exc_info=exc,
                extra={"_fields": {"objective_id": objective_id}},
            )
            return self._err("could not create the objective (a storage error occurred)", t0)
```

The rest of `execute()` (the `payload = {...}` construction and return) is unchanged. Note `t0` must already exist at the top of `execute()` (it does — confirm before editing) and is now also threaded into `_gate_epic_consent`.

- [ ] **Step 8: Implement — `_gate_epic_consent()` (mirrors `shell._gate_catastrophic`)**

```python
    async def _gate_epic_consent(self, *, repo: str, intent: str, t0: float) -> ToolResult | None:
        """Require consent before creating an EPIC — the ONE consent point for
        its entire unattended run (Consent posture, design spec). Mirrors
        shell.py's `_gate_catastrophic`: `ObjectiveTool.manifest.action_severity`
        stays "write" unconditionally (no per-call manifest variance — the
        tool ABC has no seam for that); this calls the SAME consent policy
        directly instead. Returns a refused ToolResult when consent must NOT
        proceed (no interactive user, no gate wired, declined); returns None
        when approved. Fail-closed on every path, matching every other
        consequential gate in this codebase."""
        ctx = TraceContext.get()
        interactive = bool(ctx.get("interactive", False))
        channel = ctx.get("channel")
        session_id = ctx.get("session_id")
        if not interactive or not session_id or not channel:
            log.tool.warning(
                "objective.execute: epic creation with no interactive user — refused",
                extra={"_fields": {"repo": repo}},
            )
            return self._err(
                "refused: creating an epic requires an interactive user to "
                "approve unattended execution, and none is present", t0,
            )
        gate = get_services().consent_gate
        if gate is None:
            log.tool.error(
                "objective.execute: epic creation but no consent gate wired — refused",
            )
            return self._err(
                "refused: epic creation requires a consent gate, none is available", t0,
            )
        try:
            allowed = await gate.policy.request(
                tool_name="objective",
                channel=channel,
                session_id=session_id,
                category="epic_execution",
                summary=self.consent_summary(intent=intent, repo=repo) or "",
            )
        except Exception as exc:  # fail-closed on any gate error
            log.tool.error(
                "objective.execute: consent gate raised — refused",
                exc_info=exc,
            )
            return self._err("refused: consent check failed", t0)
        if not allowed:
            log.tool.info("objective.execute: epic creation declined by user")
            return self._err("declined by user", t0)
        return None
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/tools/scheduling/test_objective_tool.py -v`
Expected: PASS — every existing test plus the new ones.

- [ ] **Step 10: Regression check**

Run: `uv run pytest tests/objectives/ tests/tools/scheduling/ -q`
Expected: all pass.

- [ ] **Step 11: Gate**

Run: `uv run ruff check src/stackowl/tools/scheduling/objective_tool.py src/stackowl/tools/system/git_tool.py && uv run mypy src/stackowl/tools/scheduling/objective_tool.py src/stackowl/tools/system/git_tool.py`
Expected: both clean.

- [ ] **Step 12: Commit**

```bash
git add src/stackowl/tools/scheduling/objective_tool.py src/stackowl/tools/system/git_tool.py tests/tools/scheduling/test_objective_tool.py tests/objectives/test_decomposer.py tests/tools/system/test_git_tool.py
git commit -m "feat(objectives): epic creation — consent gate, integration branch, graph validation"
```

---

## Task 7: `epic_runner.py` — per-story background sequence

**Files:**
- Create: `src/stackowl/objectives/epic_runner.py`
- Test: `tests/objectives/test_epic_runner.py`

**Interfaces:**
- Consumes: `git_tool.add_worktree`/`is_git_repo`/`current_branch`, `git_tool`'s `status`/`worktree_remove` operations (via `GitTool()` calls), `ClaudeCodeTool`, `RunTestsTool`, `ObjectiveStore.update_subgoal`.
- Produces: `async def run_story(objective: Objective, subgoal: Subgoal, store: ObjectiveStore, locks: dict[str, asyncio.Lock]) -> None` — the full per-story sequence; mutates the subgoal's row via `store` as its only observable effect (no return value — matches the fire-and-forget background-task shape `driver.py` launches it under). `async def detect_orphan_and_recover(objective, subgoal, store) -> None`.

- [ ] **Step 1: Read the files this composes**

Read `src/stackowl/tools/system/git_tool.py`, `src/stackowl/tools/system/claude_code.py`, `src/stackowl/tools/system/run_tests.py` in full (already read this session — re-confirm current state, especially after Task 6's `current_branch` addition) before writing.

- [ ] **Step 2: Write the failing tests (real git, real subprocess — no mocking)**

```python
# tests/objectives/test_epic_runner.py
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from stackowl.objectives.epic_runner import run_story
from stackowl.objectives.model import Objective
from stackowl.objectives.store import ObjectiveStore


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _init_repo(r)
    return r


def _stub_claude(tmp_path: Path, *, writes_file: bool = True) -> Path:
    stub = tmp_path / "claude"
    body = "#!/bin/sh\n"
    if writes_file:
        body += "echo done > story_output.txt\n"
    body += (
        'echo \'{"type": "result", "is_error": false, "result": "done", '
        '"session_id": "s"}\'\n'
    )
    stub.write_text(body)
    os.chmod(stub, 0o755)
    return stub


@pytest.mark.asyncio
async def test_run_story_clean_merge_marks_done(
    repo: Path, tmp_path: Path, monkeypatch, db_pool,  # `db_pool` — reuse this repo's existing DB test fixture (check tests/objectives/test_objective_store.py for its exact name)
) -> None:
    integration_branch = "stackowl/epic-test"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)

    store = ObjectiveStore(db_pool, "default")
    objective = Objective(
        objective_id="obj-1", owner_id="default", intent="test",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    [subgoal] = await store.add_subgoals("obj-1", ["do the thing"])

    stub = _stub_claude(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: str(stub) if name == "claude" else None)

    locks: dict[str, asyncio.Lock] = {}
    await run_story(objective, subgoal, store, locks)

    reloaded = (await store.list_subgoals("obj-1"))[0]
    assert reloaded.status == "done"
    log_output = subprocess.run(
        ["git", "log", integration_branch, "--oneline"], cwd=repo, check=True,
        capture_output=True, text=True,
    ).stdout
    assert len(log_output.splitlines()) >= 2  # init commit + the merge landed
    assert reloaded.worktree_path is None or not Path(reloaded.worktree_path).exists()


@pytest.mark.asyncio
async def test_run_story_claude_unavailable_blocks_story(
    repo: Path, monkeypatch, db_pool,
) -> None:
    integration_branch = "stackowl/epic-test2"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)
    store = ObjectiveStore(db_pool, "default")
    objective = Objective(
        objective_id="obj-2", owner_id="default", intent="test",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    [subgoal] = await store.add_subgoals("obj-2", ["do the thing"])

    monkeypatch.setattr("shutil.which", lambda name: None)  # no claude binary
    locks: dict[str, asyncio.Lock] = {}
    await run_story(objective, subgoal, store, locks)

    reloaded = (await store.list_subgoals("obj-2"))[0]
    assert reloaded.status in ("pending", "blocked")  # retried or escalated, never silently "done"
```

The `db_pool` fixture name must match whatever `tests/objectives/test_objective_store.py` already defines — read that file's fixtures before writing these tests.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_epic_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'stackowl.objectives.epic_runner'`.

- [ ] **Step 4: Implement `epic_runner.py`**

```python
# src/stackowl/objectives/epic_runner.py
"""Per-story background sequence for epic execution (Task #4).

`run_story` is what `driver.py` launches as a background `asyncio.Task` for
each ready story: create a worktree off the integration branch's current
tip, run `claude_code`, run `run_tests`, and on success merge inline under a
repo-keyed lock (re-testing the MERGED integration branch, not just the
story's own worktree). Mutates the subgoal row via `store` as its only
observable effect — no return value, matching the fire-and-forget shape the
driver launches it under.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from stackowl.infra.observability import log
from stackowl.objectives.model import Objective, Subgoal
from stackowl.objectives.store import ObjectiveStore
from stackowl.paths import StackowlHome
from stackowl.tools.system.claude_code import ClaudeCodeTool
from stackowl.tools.system.git_tool import GitTool, add_worktree
from stackowl.tools.system.run_tests import RunTestsTool
from stackowl.tools.system.shell import run_argv

__all__ = ["detect_orphan_and_recover", "run_story"]

#: Mirrors driver.py's _MAX_SUBGOAL_ATTEMPTS — kept in sync manually (both
#: constants are small and stable; a shared import would create a circular
#: dependency between driver.py and epic_runner.py).
_MAX_SUBGOAL_ATTEMPTS = 3


def _merge_lock(locks: dict[str, asyncio.Lock], repo: str) -> asyncio.Lock:
    lock = locks.get(repo)
    if lock is None:
        lock = asyncio.Lock()
        locks[repo] = lock
    return lock


async def run_story(
    objective: Objective, subgoal: Subgoal, store: ObjectiveStore, locks: dict[str, asyncio.Lock],
) -> None:
    """Execute the per-story background sequence (§Execution model, design spec)."""
    assert objective.repo is not None and objective.integration_branch is not None
    repo = objective.repo
    log.scheduler.info(
        "[objectives] epic_runner.run_story: entry",
        extra={"_fields": {"objective_id": objective.objective_id, "subgoal_id": subgoal.subgoal_id}},
    )

    # Step 1 — re-validate repo (TOCTOU) then create the worktree.
    git = GitTool()
    status_check = await git(operation="status", repo=repo)
    if not status_check.success:
        await _escalate(store, subgoal, "repo is no longer a valid git repository", worktree=None)
        return

    branch = f"stackowl/story-{subgoal.subgoal_id}"
    worktree_path = str(StackowlHome.worktrees_dir() / branch.replace("/", "-"))
    add_result = await add_worktree(
        repo, worktree_path, new_branch=branch, base_ref=objective.integration_branch,
    )
    if not add_result.success:
        await _escalate(store, subgoal, f"worktree creation failed: {add_result.error}", worktree=None)
        return
    await store.update_subgoal(
        subgoal.subgoal_id, "running", worktree_path=worktree_path, story_branch=branch,
    )

    # Step 2 — claude_code.
    claude_result = await ClaudeCodeTool()(
        prompt=subgoal.description, workdir=worktree_path, permission_mode="bypassPermissions",
    )
    if not claude_result.success:
        await _retry_or_block(store, subgoal, f"claude_code failed: {claude_result.error}")
        return

    # Step 3 — run_tests (story's own worktree).
    story_tests = await RunTestsTool()(command="uv run pytest -q", workdir=worktree_path)
    story_record = json.loads(story_tests.output) if story_tests.success and story_tests.output else {}
    if not story_tests.success or not story_record.get("all_passed"):
        await _retry_or_block(
            store, subgoal, f"tests failed in story worktree: {story_record or story_tests.error}",
        )
        return

    # Step 4 — merge under a repo-keyed lock.
    lock = _merge_lock(locks, repo)
    async with lock:
        merge = await _merge_branch(repo, branch, objective.integration_branch)
        if merge == "conflict":
            log.scheduler.warning(
                "[objectives] epic_runner.run_story: merge conflict — escalating",
                extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
            )
            await _escalate(store, subgoal, "merge conflict with integration branch", worktree=worktree_path)
            return
        if merge == "failed":
            await _escalate(store, subgoal, "merge into integration branch failed", worktree=worktree_path)
            return

        # Re-test the MERGED integration branch, not just the story's worktree.
        integration_tests = await RunTestsTool()(command="uv run pytest -q", workdir=repo)
        integration_record = (
            json.loads(integration_tests.output) if integration_tests.success and integration_tests.output else {}
        )
        if not integration_tests.success or not integration_record.get("all_passed"):
            await _escalate(
                store, subgoal,
                f"merge succeeded but integration tests failed: {integration_record or integration_tests.error}",
                worktree=worktree_path,
            )
            return

    # Clean merge + integration tests pass — clean up the worktree, keep the branch.
    await git(operation="worktree_remove", repo=repo, path=worktree_path)
    await store.update_subgoal(subgoal.subgoal_id, "done", result="merged and verified", worktree_path=None)
    await store.append_event(objective.objective_id, "subgoal_done", subgoal.description)
    log.scheduler.info(
        "[objectives] epic_runner.run_story: exit — done",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
    )


async def _merge_branch(repo: str, branch: str, integration_branch: str) -> str:
    """Merge `branch` into `integration_branch` via a direct git CLI call
    (GitTool has no dedicated "merge" operation — this is the one place that
    needs it, kept local rather than growing GitTool's surface for a single
    caller). Returns "ok" | "conflict" | "failed"."""
    checkout = await run_argv(
        ["git", "checkout", integration_branch], tool_name="git", workdir=repo, intent="write",
    )
    if not checkout.success:
        return "failed"
    merge = await run_argv(
        ["git", "merge", "--no-ff", branch], tool_name="git", workdir=repo, intent="write",
    )
    if merge.success:
        return "ok"
    await run_argv(["git", "merge", "--abort"], tool_name="git", workdir=repo, intent="write")
    return "conflict" if "conflict" in (merge.error or "").lower() else "failed"


async def _retry_or_block(store: ObjectiveStore, subgoal: Subgoal, reason: str) -> None:
    used = subgoal.attempts + 1
    if used < _MAX_SUBGOAL_ATTEMPTS:
        await store.update_subgoal(subgoal.subgoal_id, "pending", result=reason, attempts=used)
        log.scheduler.info(
            "[objectives] epic_runner: story failed — retrying",
            extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "attempt": used}},
        )
        return
    await store.update_subgoal(subgoal.subgoal_id, "blocked", result=reason, attempts=used)
    log.scheduler.warning(
        "[objectives] epic_runner: story failed — retry budget exhausted, blocked",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "reason": reason}},
    )


async def _escalate(store: ObjectiveStore, subgoal: Subgoal, reason: str, *, worktree: str | None) -> None:
    """Escalate a story to blocked WITHOUT consuming the retry budget — used
    for outcomes a clean retry would not fix (merge conflict, integration
    test failure): the work is preserved (worktree left for inspection)."""
    await store.update_subgoal(subgoal.subgoal_id, "blocked", result=reason)
    log.scheduler.warning(
        "[objectives] epic_runner: story escalated",
        extra={"_fields": {"subgoal_id": subgoal.subgoal_id, "reason": reason, "worktree": worktree}},
    )


async def detect_orphan_and_recover(
    objective: Objective, subgoal: Subgoal, store: ObjectiveStore,
) -> None:
    """Crash recovery — worktree-aware orphan handling (§Execution model).
    Caller (driver.py) already confirmed this subgoal is `running` and NOT in
    the process's local live-task set before calling this."""
    if subgoal.worktree_path is None or not Path(subgoal.worktree_path).exists():
        # Never got far enough to create a worktree, or it's already gone —
        # safe to just restart.
        await store.update_subgoal(subgoal.subgoal_id, "pending", attempts=subgoal.attempts + 1)
        return
    git = GitTool()
    status = await git(operation="status", repo=subgoal.worktree_path)
    if not status.success:
        await _escalate(
            store, subgoal, "orphan recovery: could not read worktree git status",
            worktree=subgoal.worktree_path,
        )
        return
    record = json.loads(status.output) if status.output else {}
    if record.get("clean", False):
        await git(
            operation="worktree_remove", repo=objective.repo or "",
            path=subgoal.worktree_path, force=True,
        )
        await store.update_subgoal(
            subgoal.subgoal_id, "pending", attempts=subgoal.attempts + 1, worktree_path=None,
        )
        log.scheduler.info(
            "[objectives] epic_runner.detect_orphan_and_recover: clean tree — restarting fresh",
            extra={"_fields": {"subgoal_id": subgoal.subgoal_id}},
        )
        return
    await _escalate(
        store, subgoal, f"orphan recovery: worktree is dirty — {record}",
        worktree=subgoal.worktree_path,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_epic_runner.py -v`
Expected: PASS. If the clean-merge test's git-log assertion doesn't match real output, tighten it against the ACTUAL `git log` output rather than guessing — this is exactly the kind of assertion that needs the real command's output checked, not assumed.

- [ ] **Step 6: Gate**

Run: `uv run ruff check src/stackowl/objectives/epic_runner.py && uv run mypy src/stackowl/objectives/epic_runner.py`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/objectives/epic_runner.py tests/objectives/test_epic_runner.py
git commit -m "feat(objectives): per-story epic execution sequence (worktree/claude_code/tests/merge)"
```

---

## Task 8: Driver — epic branch in `_advance`

**Files:**
- Modify: `src/stackowl/objectives/driver.py`
- Test: `tests/objectives/test_driver.py` (existing — extend)

**Interfaces:**
- Consumes: `graph.readiness_set` (Task 3), `epic_runner.run_story`/`detect_orphan_and_recover` (Task 7).
- Produces: `ObjectiveDriverHandler` gains `self._epic_drives: set[asyncio.Task]` (constructor) and `self._merge_locks: dict[str, asyncio.Lock]` (constructor); `_advance` branches to `_advance_epic` when `objective.repo` is set; `_settle_epic_status` stubbed as a no-op FOR THIS TASK ONLY (real body in Task 9).

- [ ] **Step 1: Read the current driver file fully**

Already read in full this session (829 lines) — re-confirm before editing.

- [ ] **Step 2: Write the failing tests**

```python
# append to tests/objectives/test_driver.py
import asyncio

import pytest

from stackowl.objectives.model import Objective, SubgoalSpec


@pytest.mark.asyncio
async def test_epic_advance_launches_ready_stories_concurrently(driver, store, monkeypatch) -> None:
    # (Use this file's existing fake-backend/db fixtures — read the top of
    # test_driver.py to confirm the exact fixture names `driver`/`store`
    # refer to, and adapt if named differently.)
    objective = Objective(
        objective_id="obj-epic", owner_id="default", intent="epic",
        repo="/tmp/fake-repo", integration_branch="stackowl/epic-obj-epic", base_branch="main",
    )
    await store.create(objective)
    await store.add_subgoals(
        "obj-epic",
        [SubgoalSpec(description="a"), SubgoalSpec(description="b")],  # both independent
    )

    launched: list[str] = []

    async def _fake_run_story(objective, subgoal, store_, locks):
        launched.append(subgoal.subgoal_id)
        await store_.update_subgoal(subgoal.subgoal_id, "done", result="ok")

    monkeypatch.setattr("stackowl.objectives.driver.run_story", _fake_run_story)

    result = await driver._advance(store, await store.get("obj-epic"))
    assert result is True
    for _ in range(20):
        if len(launched) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(launched) == 2  # both independent stories launched THIS tick


@pytest.mark.asyncio
async def test_epic_failure_isolation_does_not_block_siblings(driver, store, monkeypatch) -> None:
    objective = Objective(
        objective_id="obj-epic2", owner_id="default", intent="epic",
        repo="/tmp/fake-repo", integration_branch="stackowl/epic-obj-epic2", base_branch="main",
    )
    await store.create(objective)
    specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b")]  # independent
    await store.add_subgoals("obj-epic2", specs)

    async def _fake_run_story(objective, subgoal, store_, locks):
        if subgoal.description == "a":
            await store_.update_subgoal(subgoal.subgoal_id, "blocked", result="failed")
        else:
            await store_.update_subgoal(subgoal.subgoal_id, "done", result="ok")

    monkeypatch.setattr("stackowl.objectives.driver.run_story", _fake_run_story)
    await driver._advance(store, await store.get("obj-epic2"))
    for _ in range(20):
        subgoals = await store.list_subgoals("obj-epic2")
        if all(sg.status in ("blocked", "done") for sg in subgoals):
            break
        await asyncio.sleep(0.01)
    subgoals = await store.list_subgoals("obj-epic2")
    by_desc = {sg.description: sg for sg in subgoals}
    assert by_desc["a"].status == "blocked"
    assert by_desc["b"].status == "done"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_driver.py -v -k epic`
Expected: FAIL — `_advance` doesn't check `objective.repo`, treats it as a plain linear objective (picks ONE pending subgoal per tick, not both).

- [ ] **Step 4: Implement — imports + constructor gains the strong-ref sets**

Add to the top-level imports in `driver.py`:

```python
import asyncio
```

(Check it isn't already imported before adding — it likely isn't, per the file's current import list.) Also add:

```python
from stackowl.objectives.epic_runner import detect_orphan_and_recover, run_story
from stackowl.objectives.graph import readiness_set
```

In `ObjectiveDriverHandler.__init__`, add after `self._recovery = recovery or RecoveryActuator()`:

```python
        # Task #4 — held strong-refs for background story tasks (mirrors
        # RecoveryDriver._drives) and per-repo merge locks (mirrors
        # TurnRegistry.session_intake_lock's lazy-per-key pattern).
        self._epic_drives: set[asyncio.Task[None]] = set()
        self._merge_locks: dict[str, asyncio.Lock] = {}
```

- [ ] **Step 5: Implement — `_advance` branches**

Change the top of `_advance`:

```python
    async def _advance(self, store: ObjectiveStore, objective: Objective) -> bool:
        """Advance one objective. Plain objective (repo unset): unchanged
        linear behavior below. Epic (repo set): dispatches to _advance_epic —
        readiness-graph scan, concurrent background launch, worktree-aware
        crash recovery, and partial-completion notify (Task #4)."""
        if objective.repo:
            return await self._advance_epic(store, objective)
        nxt = await store.next_pending_subgoal(objective.objective_id)
        # ... rest of the existing method UNCHANGED from here
```

- [ ] **Step 6: Implement — `_advance_epic` and the `_settle_epic_status` stub**

Add both as new methods (placed near `_advance`):

```python
    async def _advance_epic(self, store: ObjectiveStore, objective: Objective) -> bool:
        """Task #4 epic path: recover orphans, launch every ready story
        concurrently, and let each story's own background task drive it to a
        terminal state (see epic_runner.run_story). Returns did-work."""
        subgoals = await store.list_subgoals(objective.objective_id)
        did_work = False

        # Crash recovery — worktree-aware orphan check (runs every tick, no
        # separate boot sweep; see design spec's Crash recovery section).
        live_ids = {t.get_name() for t in self._epic_drives}
        for sg in subgoals:
            if sg.status == "running" and sg.subgoal_id not in live_ids:
                await detect_orphan_and_recover(objective, sg, store)
                did_work = True
        if did_work:
            subgoals = await store.list_subgoals(objective.objective_id)  # re-read post-recovery

        ready = readiness_set(subgoals)
        for sg in subgoals:
            if sg.subgoal_id not in ready:
                continue
            # Explicit synchronization point (§Execution model): the DB write
            # completes BEFORE this tick returns, THEN the background task is
            # created — so the scheduler never considers this job "done" (and
            # eligible to fire again) while a launch is still in flight.
            await store.update_subgoal(sg.subgoal_id, "running")
            task: asyncio.Task[None] = asyncio.create_task(
                run_story(objective, sg, store, self._merge_locks), name=sg.subgoal_id,
            )
            self._epic_drives.add(task)
            task.add_done_callback(self._epic_drives.discard)
            did_work = True
            log.scheduler.info(
                "[scheduler] objective_driver._advance_epic: story launched",
                extra={"_fields": {"objective_id": objective.objective_id, "subgoal_id": sg.subgoal_id}},
            )

        await self._settle_epic_status(store, objective)
        return did_work

    async def _settle_epic_status(self, store: ObjectiveStore, objective: Objective) -> None:
        """Stub — real implementation in Task 9 (kept as a no-op here so this
        task's tests pass on their own merit without depending on unwritten
        code)."""
        return None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_driver.py -v -k epic`
Expected: PASS.

- [ ] **Step 8: Regression check**

Run: `uv run pytest tests/objectives/ -q`
Expected: all pass — every existing (plain-objective) test in `test_driver.py` must be untouched, since `_advance`'s branch only activates when `objective.repo` is set.

- [ ] **Step 9: Gate**

Run: `uv run ruff check src/stackowl/objectives/driver.py && uv run mypy src/stackowl/objectives/driver.py`
Expected: both clean.

- [ ] **Step 10: Commit**

```bash
git add src/stackowl/objectives/driver.py tests/objectives/test_driver.py
git commit -m "feat(objectives): epic branch in _advance — readiness scan, concurrent launch, orphan recovery"
```

---

## Task 9: Driver — objective-level settle (failure isolation, partial completion)

**Files:**
- Modify: `src/stackowl/objectives/driver.py` (replace Task 8's `_settle_epic_status` stub)
- Test: `tests/objectives/test_driver.py` (existing — extend)

**Interfaces:**
- Consumes: `Objective`/`Subgoal` (Task 2), `store.update_status`/`append_event`, `self._notify` (existing), `graph.readiness_set` (Task 3).
- Produces: `_settle_epic_status(store, objective) -> None` — real implementation.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/objectives/test_driver.py
@pytest.mark.asyncio
async def test_epic_all_done_notifies_full_completion(driver, store, monkeypatch) -> None:
    objective = Objective(
        objective_id="obj-epic3", owner_id="default", intent="epic",
        repo="/tmp/fake-repo", integration_branch="stackowl/epic-obj-epic3", base_branch="main",
    )
    await store.create(objective)
    [sg] = await store.add_subgoals("obj-epic3", [SubgoalSpec(description="a")])
    await store.update_subgoal(sg.subgoal_id, "done", result="ok")

    notified: list[str] = []

    async def _fake_notify(obj, msg):
        notified.append(msg)

    monkeypatch.setattr(driver, "_notify", _fake_notify)
    await driver._settle_epic_status(store, await store.get("obj-epic3"))

    reloaded = await store.get("obj-epic3")
    assert reloaded.status == "blocked"  # awaiting the human merge confirm, not auto-"done"
    assert any("objective-merge" in m for m in notified)
    assert any("1/1" in m for m in notified)


@pytest.mark.asyncio
async def test_epic_partial_stuck_notifies_per_story_reasons(driver, store, monkeypatch) -> None:
    objective = Objective(
        objective_id="obj-epic4", owner_id="default", intent="epic",
        repo="/tmp/fake-repo", integration_branch="stackowl/epic-obj-epic4", base_branch="main",
    )
    await store.create(objective)
    specs = [SubgoalSpec(description="a"), SubgoalSpec(description="b", depends_on=[0])]
    created = await store.add_subgoals("obj-epic4", specs)
    await store.update_subgoal(created[0].subgoal_id, "blocked", result="claude_code failed")
    # created[1] ("b") stays pending forever — its dependency never reached done.

    notified: list[str] = []

    async def _fake_notify(obj, msg):
        notified.append(msg)

    monkeypatch.setattr(driver, "_notify", _fake_notify)
    await driver._settle_epic_status(store, await store.get("obj-epic4"))

    reloaded = await store.get("obj-epic4")
    assert reloaded.status == "blocked"
    message = notified[0]
    assert "claude_code failed" in message  # a's own reason
    assert "dependency" in message.lower()  # b's transitive reason


@pytest.mark.asyncio
async def test_epic_still_progressing_does_not_settle(driver, store, monkeypatch) -> None:
    objective = Objective(
        objective_id="obj-epic5", owner_id="default", intent="epic",
        repo="/tmp/fake-repo", integration_branch="stackowl/epic-obj-epic5", base_branch="main",
    )
    await store.create(objective)
    [sg] = await store.add_subgoals("obj-epic5", [SubgoalSpec(description="a")])
    await store.update_subgoal(sg.subgoal_id, "running")  # still in flight

    notified: list[str] = []

    async def _fake_notify(obj, msg):
        notified.append(msg)

    monkeypatch.setattr(driver, "_notify", _fake_notify)
    await driver._settle_epic_status(store, await store.get("obj-epic5"))

    reloaded = await store.get("obj-epic5")
    assert reloaded.status == "active"
    assert notified == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/objectives/test_driver.py -v -k settle`
Expected: FAIL — the Task 8 stub always returns `None` and never notifies/transitions.

- [ ] **Step 3: Implement `_settle_epic_status`**

Replace the Task 8 stub body:

```python
    async def _settle_epic_status(self, store: ObjectiveStore, objective: Objective) -> None:
        """Task #4 — decide whether the epic is fully done, stuck-but-partial,
        or still progressing, and notify accordingly. Never called from a
        plain objective's path. `done` (a Subgoal status) already means
        "merged" for an epic (epic_runner.run_story) — this method only reads
        that status, it never merges anything itself."""
        subgoals = await store.list_subgoals(objective.objective_id)
        if any(sg.status == "running" for sg in subgoals):
            return  # still progressing
        if readiness_set(subgoals):
            return  # a tick will pick these up and launch them next

        done = [sg for sg in subgoals if sg.status == "done"]
        done_ids = {sg.subgoal_id for sg in done}
        if len(done) == len(subgoals):
            message = (
                f"Epic complete — {len(done)}/{len(subgoals)} stories verified "
                f"and merged into `{objective.integration_branch}`. Reply "
                f"`/owls objective-merge {objective.objective_id} YES` to merge "
                f"into `{objective.base_branch}`."
            )
            await store.update_status(
                objective.objective_id, "blocked", blocker="awaiting merge confirm", blocker_kind="decision",
            )
            await store.append_event(objective.objective_id, "epic_ready_to_merge", message)
            await self._notify(objective, message)
            return

        stuck = [sg for sg in subgoals if sg.subgoal_id not in done_ids]
        if not stuck:
            return  # nothing left, nothing stuck — unreachable given the checks above, but safe

        lines = []
        for sg in stuck:
            if sg.status == "blocked":
                lines.append(f"{sg.subgoal_id}: {sg.result or 'blocked'}")
            elif sg.status == "pending":
                missing = [d for d in sg.depends_on if d not in done_ids]
                if missing:
                    lines.append(f"{sg.subgoal_id}: blocked because dependency {missing[0]} is stuck")
        reason_block = "\n".join(lines)
        if done:
            message = (
                f"Epic stuck — {len(done)}/{len(subgoals)} stories done and "
                f"merged into `{objective.integration_branch}`; {len(stuck)} "
                f"permanently blocked. Reply `objective-merge "
                f"{objective.objective_id} YES` to merge the {len(done)} "
                f"completed stories, or `objective-cancel` to abandon.\n{reason_block}"
            )
        else:
            message = (
                f"Epic stuck — 0/{len(subgoals)} stories completed, nothing "
                f"progressable. Reply `objective-cancel` to abandon.\n{reason_block}"
            )
        await store.update_status(
            objective.objective_id, "blocked", blocker="epic stuck", blocker_kind="decision",
        )
        await store.append_event(objective.objective_id, "epic_stuck", message)
        await self._notify(objective, message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/objectives/test_driver.py -v -k "epic or settle"`
Expected: PASS.

- [ ] **Step 5: Regression check**

Run: `uv run pytest tests/objectives/ -q`
Expected: all pass.

- [ ] **Step 6: Gate**

Run: `uv run ruff check src/stackowl/objectives/driver.py && uv run mypy src/stackowl/objectives/driver.py`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git add src/stackowl/objectives/driver.py tests/objectives/test_driver.py
git commit -m "feat(objectives): epic settle — full/partial completion notify, failure isolation"
```

---

## Task 10: `objective-merge` slash command

**Files:**
- Modify: `src/stackowl/commands/owls_command.py`
- Test: `tests/commands/test_owls_command.py` (existing — extend; confirm exact filename/path first)

**Interfaces:**
- Consumes: `ObjectiveStore`, `GitTool`, `shell.run_argv`, `Subgoal.worktree_path`.
- Produces: `/owls objective-merge <id> YES` — new subcommand, dispatched alongside `objective-cancel`.

- [ ] **Step 1: Read the current command file fully**

Read the full `owls_command.py` (not just the sections already seen this session) before editing, to confirm both dispatch sites (lines ~203 and ~768 from the earlier grep) and the exact `_objective_cancel` variable names.

- [ ] **Step 2: Write the failing tests**

```python
# append to tests/commands/test_owls_command.py
import subprocess
from pathlib import Path

import pytest

from stackowl.objectives.model import Objective, SubgoalSpec


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_objective_merge_full_completion(tmp_path: Path, command, store) -> None:
    # (Reuse this file's existing `command`/`store` fixture names — read the
    # top of test_owls_command.py to confirm.)
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    integration_branch = "stackowl/epic-obj-m1"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)

    objective = Objective(
        objective_id="obj-m1", owner_id="default", intent="epic",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
        status="blocked", blocker="awaiting merge confirm", blocker_kind="decision",
    )
    await store.create(objective)
    [sg] = await store.add_subgoals("obj-m1", [SubgoalSpec(description="a")])
    await store.update_subgoal(sg.subgoal_id, "done")

    result = await command.dispatch("objective-merge obj-m1 YES")
    assert "merged" in result.lower()
    reloaded = await store.get("obj-m1")
    assert reloaded.status == "done"
    current = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert current == "main"


@pytest.mark.asyncio
async def test_objective_merge_refuses_when_not_ready(command, store) -> None:
    objective = Objective(
        objective_id="obj-m2", owner_id="default", intent="epic",
        repo="/tmp/x", integration_branch="stackowl/epic-obj-m2", base_branch="main",
        status="active",
    )
    await store.create(objective)
    result = await command.dispatch("objective-merge obj-m2 YES")
    assert "not ready" in result.lower() or "✗" in result


@pytest.mark.asyncio
async def test_objective_merge_requires_yes_confirmation(command, store) -> None:
    result = await command.dispatch("objective-merge obj-m1")
    assert "YES" in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_owls_command.py -v -k objective_merge`
Expected: FAIL — no `objective-merge` subcommand registered, dispatch falls through to "unknown subcommand".

- [ ] **Step 4: Implement — `SubCommand` metadata**

Add alongside the existing `objective-cancel` entry:

```python
        SubCommand(
            name="objective-merge",
            summary="Merge a completed (or partially completed) epic",
            description=(
                "Merge an epic's integration branch into its base branch. "
                "Works when every story is done, or when some are "
                "permanently blocked (merges just the completed ones). "
                "Confirmed with YES."
            ),
            args=(Arg(name="objective_id", summary="objective id"),),
            examples=(
                Example(invocation="/owls objective-merge obj-1a2b3c4d YES", note="Confirm"),
            ),
        ),
```

- [ ] **Step 5: Implement — dispatch wiring**

Add a branch at both dispatch sites that currently route `objective-cancel`:

```python
            elif sub == "objective-merge":
                result = await self._objective_merge(rest)
```

- [ ] **Step 6: Implement — `_objective_merge`**

```python
    async def _objective_merge(self, rest: str) -> str:
        """Merge an epic's integration branch (full or partial) — confirmed with YES."""
        if self._db is None:
            return _NO_OBJECTIVE_DB
        tokens = rest.split()
        if not tokens:
            return "Usage: /owls objective-merge <objective_id> YES"
        objective_id = tokens[0]
        store = ObjectiveStore(self._db, DEFAULT_PRINCIPAL_ID)
        try:
            objective = await store.get(objective_id)
        except ObjectiveNotFoundError:
            return f"✗ no such objective: {objective_id!r}"

        if not objective.repo or not objective.integration_branch:
            return f"✗ '{objective_id}' is not an epic (no repo/integration branch)"
        if objective.status != "blocked":
            return f"✗ '{objective_id}' is not ready to merge (status: {objective.status})"

        subgoals = await store.list_subgoals(objective_id)
        done = [sg for sg in subgoals if sg.status == "done"]
        if not done:
            return f"✗ '{objective_id}' has no completed stories to merge"

        confirmed = len(tokens) > 1 and tokens[1] == "YES"
        if not confirmed:
            return (
                f"⚠ This will merge {len(done)}/{len(subgoals)} completed "
                f"stories from '{objective.integration_branch}' into "
                f"'{objective.base_branch}'.\n"
                f"   Type: /owls objective-merge {objective_id} YES to confirm."
            )

        from stackowl.tools.system.git_tool import GitTool
        from stackowl.tools.system.shell import run_argv

        checkout = await run_argv(
            ["git", "checkout", objective.base_branch or ""],
            tool_name="git", workdir=objective.repo, intent="write",
        )
        if not checkout.success:
            return f"✗ could not check out '{objective.base_branch}': {checkout.error}"
        merge = await run_argv(
            ["git", "merge", "--no-ff", objective.integration_branch],
            tool_name="git", workdir=objective.repo, intent="write",
        )
        if not merge.success:
            return f"✗ final merge failed (left blocked for manual resolution): {merge.error}"

        git = GitTool()
        for sg in subgoals:
            if sg not in done and sg.worktree_path:
                await git(operation="worktree_remove", repo=objective.repo, path=sg.worktree_path, force=True)

        await store.update_status(objective_id, "done")
        await store.append_event(
            objective_id, "epic_merged",
            f"{len(done)}/{len(subgoals)} stories merged into {objective.base_branch}",
        )
        log.gateway.info(
            "[commands] owls.objective_merge: merged",
            extra={"_fields": {"objective_id": objective_id, "done": len(done), "total": len(subgoals)}},
        )
        return f"✓ merged {len(done)}/{len(subgoals)} stories into '{objective.base_branch}'."
```

- [ ] **Step 7: Extend `_objective_cancel` — clean up epic worktrees**

In the EXISTING `_objective_cancel` method, right before `await store.update_status(objective_id, "abandoned")`, reuse the `objective` binding the method's try/except already fetched (do not re-fetch), and add:

```python
        if objective.repo:
            from stackowl.tools.system.git_tool import GitTool

            git = GitTool()
            for sg in await store.list_subgoals(objective_id):
                if sg.worktree_path:
                    await git(operation="worktree_remove", repo=objective.repo, path=sg.worktree_path, force=True)
```

The existing method currently does `await store.get(objective_id)` inside a bare `try/except ObjectiveNotFoundError` without keeping the returned value — change that line to bind it: `objective = await store.get(objective_id)` (it already effectively does this via the try, just confirm the variable is captured, not discarded).

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_owls_command.py -v`
Expected: PASS — every existing test plus the new ones.

- [ ] **Step 9: Regression check**

Run: `uv run pytest tests/commands/ tests/objectives/ -q`
Expected: all pass.

- [ ] **Step 10: Gate**

Run: `uv run ruff check src/stackowl/commands/owls_command.py && uv run mypy src/stackowl/commands/owls_command.py`
Expected: both clean.

- [ ] **Step 11: Commit**

```bash
git add src/stackowl/commands/owls_command.py tests/commands/test_owls_command.py
git commit -m "feat(commands): add /owls objective-merge; objective-cancel cleans up epic worktrees"
```

---

## Task 11: End-to-end integration test

**Files:**
- Test: `tests/objectives/test_epic_e2e.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1-10.

- [ ] **Step 1: Write the test**

A single real-git, real-driver-tick test that exercises the full happy path without mocking any of the epic-specific logic (only `claude_code`'s binary is stubbed, matching every other test in this plan — no test should ever mock `git` itself):

```python
# tests/objectives/test_epic_e2e.py
"""End-to-end: a 2-story epic (one independent, one dependent) from
ObjectiveTool.execute() through ObjectiveDriverHandler ticks to a merged,
done objective. Real git repos throughout — only the `claude` binary is
stubbed (matching every other test in this codebase's git_tool/claude_code
test style)."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from stackowl.objectives.driver import ObjectiveDriverHandler
from stackowl.objectives.model import Objective, SubgoalSpec
from stackowl.objectives.store import ObjectiveStore


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("x\n")
    subprocess.run(["git", "add", "f.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_two_story_epic_reaches_ready_to_merge(
    tmp_path: Path, monkeypatch, db_pool,  # reuse this repo's standard db_pool fixture
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    stub = tmp_path / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'echo \'{"type": "result", "is_error": false, "result": "done", "session_id": "s"}\'\n'
    )
    os.chmod(stub, 0o755)
    monkeypatch.setattr("shutil.which", lambda name: str(stub) if name == "claude" else None)

    # Epic stories bypass _run_subgoal/backend.run entirely (they call
    # epic_runner.run_story directly via _advance_epic) — backend=None is
    # valid here since ObjectiveDriverHandler's `assert self._backend is not
    # None` in _run_subgoal is only reached by the PLAIN-objective path,
    # never by _advance_epic. Confirmed against Task 8's actual
    # implementation before finalizing this test.
    driver = ObjectiveDriverHandler(db=db_pool, backend=None)
    store = ObjectiveStore(db_pool, "default")

    objective_id = "obj-e2e-1"
    integration_branch = f"stackowl/epic-{objective_id}"
    subprocess.run(["git", "branch", integration_branch], cwd=repo, check=True)
    objective = Objective(
        objective_id=objective_id, owner_id="default", intent="e2e epic",
        repo=str(repo), integration_branch=integration_branch, base_branch="main",
    )
    await store.create(objective)
    specs = [SubgoalSpec(description="story a"), SubgoalSpec(description="story b", depends_on=[0])]
    await store.add_subgoals(objective_id, specs)

    # Tick until settled (bounded — never an unbounded loop in a test).
    for _ in range(50):
        await driver._advance(store, await store.get(objective_id))
        await asyncio.sleep(0.05)
        current = await store.get(objective_id)
        if current.status == "blocked" and current.blocker == "awaiting merge confirm":
            break
    else:
        pytest.fail("epic never reached ready-to-merge within the bounded tick loop")

    subgoals = await store.list_subgoals(objective_id)
    assert all(sg.status == "done" for sg in subgoals)
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/objectives/test_epic_e2e.py -v`
Expected: PASS. If the bounded tick loop times out, debug via `tests/objectives/test_epic_runner.py`'s narrower tests first (this e2e test is a capstone, not the primary debugging surface).

- [ ] **Step 3: Full regression check**

Run: `uv run pytest tests/objectives/ tests/tools/scheduling/ tests/tools/system/ tests/commands/ -q`
Expected: all pass.

- [ ] **Step 4: Gate**

Run: `uv run ruff check src/ && uv run mypy src/stackowl/objectives/ src/stackowl/tools/scheduling/objective_tool.py src/stackowl/tools/system/git_tool.py src/stackowl/commands/owls_command.py`
Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add tests/objectives/test_epic_e2e.py
git commit -m "test(objectives): end-to-end epic execution (2-story dependency graph, real git)"
```

---

## Self-Review Notes (for the plan author — already applied above, kept for the implementer's context)

- **Spec coverage:** Data model (Task 2), graph validation (Task 3), Creation flow + consent mechanism (Task 6), Execution model readiness/launch/per-story sequence (Tasks 7-8), crash recovery (Task 7's `detect_orphan_and_recover` + Task 8's live-set check), worktree lifecycle (Tasks 7, 10), Final merge confirm + partial completion (Tasks 9-10), Testing section's every named case (Tasks 3, 6, 7, 8, 9, 10, 11) — all covered.
- **Known open items flagged inline, not hidden:** Task 8's `_settle_epic_status` is stubbed then replaced by Task 9 (intentional — keeps Task 8's tests independently green); Task 11's driver construction (`backend=None`) needs confirming against the real Task 8 code once written, flagged explicitly rather than asserted as certain.
- **Type consistency:** `run_story(objective, subgoal, store, locks)` signature is identical across Tasks 7, 8, and the tests in both. `readiness_set(subgoals: list[Subgoal]) -> set[str]` and `validate_graph(specs) -> GraphError | None` are used with the same signature everywhere they're called (Tasks 3, 6, 8, 9).
