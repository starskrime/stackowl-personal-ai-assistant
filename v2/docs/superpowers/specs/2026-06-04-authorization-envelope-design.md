# Epic 2 Story 2 — The Authorization Envelope (E2-S2)

**Status:** Design approved (forks resolved via brainstorming, 2026-06-04)
**Builds on:** E2-S1 (`BoundsSpec` closed enumeration + tools-axis enforcement, commit `1ae9663`)
**Followed by:** E2-S3 preflight planner (computes goal-aware envelopes), E2-S4 authorizer/budget
**FRs:** FR34-adjacent (task envelope), narrowing-only guarantee (FR35-adjacent)

---

## 1. Problem & Goal

S1 shipped `BoundsSpec` and enforced its TOOLS axis against the **owl's own** bounds at
the dispatch seam (`pipeline/steps/execute.py`). `BoundsSpec.intersect()` exists and is
tested, and its docstring already specifies the contract:

> the effective bounds for a call are `owl_bounds.intersect(task_bounds)` — a task
> envelope can only TIGHTEN, never widen, an owl's bounds.

But `intersect()` is **not wired into dispatch**, and there is **no task-level bounds
carrier** anywhere (`PipelineState` has no `task_bounds`; `DurableTask` has no envelope
field). S2 delivers the **authorization envelope**: a task-scoped `BoundsSpec` that
narrows the acting owl's bounds for the lifetime of a task, threaded through the pipeline,
persisted across durable kill/resume, and propagated to delegated children.

**Non-goal:** *deciding* an envelope's contents from a goal is the E2-S3 preflight
planner's job. S2 ships the mechanism plus one deterministic **default derivation**
(below), which S3 later replaces with goal-aware logic.

---

## 2. Approved design decisions (brainstorming forks)

| Fork | Decision |
|---|---|
| S2 scope | **Mechanism + a default derivation** (not a passive `None` carrier) |
| Default envelope | **Pin-at-creation owl snapshot** — durable task creation snapshots the owl's *current* bounds as the task envelope |
| Durable persistence | **Persist in S2** — migration `0048` + `DurableTask.task_bounds` + recovery round-trip, proven by a kill/resume test |
| Delegation | **Propagate** the task envelope into delegated/child `PipelineState`s (narrowing-only, safe). Does **not** touch FR35 parent∩child *owl*-bounds (still Epic 3) |

### The default derivation: pin-at-creation snapshot

When a durable task is created, snapshot the acting owl's current `bounds` into the task's
`task_bounds` and persist it. On every subsequent run (including resume after a kill), the
effective bounds are `current_owl_bounds ∩ snapshot`. This gives a real **TOCTOU guard**:
widening an owl's bounds *after* a task starts cannot retroactively escalate an in-flight or
resumed task — it still runs under the authorization it was created with. For a plain
non-durable turn, no snapshot is taken and `task_bounds` stays `None` (pure no-op,
byte-for-byte legacy behavior). S3's planner will later *replace* this default with a
goal-derived (and typically tighter) envelope.

---

## 3. Architecture & components

The envelope is just a `BoundsSpec` carried at task scope. Four touch-points, each small
and independently testable.

### 3.1 Carrier — `PipelineState.task_bounds`

Add one additive field (mirrors `task_id`, `durable_owner_id` — `None` default, carried
across `evolve()`):

```python
# pipeline/state.py
task_bounds: BoundsSpec | None = None
```

`None` = no task envelope (the owl's own bounds apply unchanged).

### 3.2 Composition at the dispatch seam — `authz/bounds_guard.py` + `execute.py`

Introduce a pure combiner so the two `Optional[BoundsSpec]` are merged with narrowing-only
semantics (the `None` cases that `BoundsSpec.intersect` alone does not cover):

```python
# authz/bounds_guard.py
def effective_bounds(
    owl_bounds: BoundsSpec | None,
    task_bounds: BoundsSpec | None,
) -> BoundsSpec | None:
    """Narrowing-only combine of owl + task envelope.

    None ∩ None      → None         (unrestricted)
    owl  ∩ None      → owl
    None ∩ task      → task         (a task CAN restrict an unbounded owl)
    owl  ∩ task      → owl.intersect(task)
    """
```

Refactor the existing tools-axis check to operate on an *effective* `BoundsSpec` rather
than re-reading `manifest.bounds`:

```python
def check_effective_bounds(effective: BoundsSpec | None, tool_name: str) -> str | None:
    # None → unrestricted; else effective.permits_tool(tool_name)
```

`check_tool_bounds(manifest, name)` is **kept** as a thin wrapper
(`check_effective_bounds(manifest.bounds if manifest else None, name)`) so no other caller
breaks; the seam switches to the effective path.

In `execute.py::_run_with_tools._dispatch`, replace the S1 owl-only check with:

```python
owl_bounds = acting_owl_manifest.bounds if acting_owl_manifest else None
effective = effective_bounds(owl_bounds, state.task_bounds)
bounds_block = check_effective_bounds(effective, name)
```

Everything around it (the `denied_this_run` loop-stop, the single authoritative WARNING
log, the "before consent" ordering) is unchanged. The block-reason string already reads
"not permitted by this owl's bounds" — when the *task* is the narrower, a block is still
truthful (the task narrowed what this owl may do); the log already carries `axis="tools"`.
We add a `source` field (`"owl"` vs `"task"`) to the WARNING for debuggability, computed by
comparing whether the owl alone would have permitted the tool.

### 3.3 Default derivation + durable persistence

**Model** — add to `DurableTask` (additive, `None` default):
```python
task_bounds: BoundsSpec | None = None
```

**Migration `0048_tasks_authorization_envelope.sql`**:
```sql
ALTER TABLE tasks ADD COLUMN task_bounds TEXT;  -- JSON of BoundsSpec, NULL = no envelope
```
(Idempotent additive column, matching the 0046/0047 pattern. NULL on every legacy row →
recovery treats it as no envelope, byte-for-byte legacy behavior.)

**Store** (`durable/store.py`):
- `create()` writes `task_bounds` as `task.task_bounds.model_dump_json()` or `None`.
- `_SELECT_FIELDS` gains `task_bounds`; `_row_to_task()` reads it back via
  `BoundsSpec.model_validate_json(raw)` when non-NULL, else `None`. (Pydantic round-trips
  `frozenset`/`tuple` axes correctly through JSON.)

**Creation snapshot** (`durable/task_runner.py::run`): before `store.create(...)`, resolve
the acting owl's bounds from the owl registry (`owl_registry.get(state.owl_name).bounds`,
best-effort — registry/owl miss → `None`, logged not raised) and use it as the task's
envelope:
```python
snapshot = _resolve_owl_bounds(state.owl_name)      # BoundsSpec | None
task = DurableTask(..., task_bounds=snapshot, ...)
durable_state = state.evolve(
    task_id=task_id, durable_owner_id=owner_id, task_bounds=snapshot,
)
```

**Recovery** (`durable/recovery.py::_reconstruct_state`): the loaded `task.task_bounds`
is threaded into the reconstructed state's `evolve()` (both the no-checkpoint and
mid-transcript branches) so a resumed drive re-applies the envelope:
```python
base.evolve(task_id=task_id, durable_owner_id=self._owner_id,
            task_bounds=task.task_bounds, ...)
```

### 3.4 Delegation propagation

The three child-spawning sites build a fresh `PipelineState`; each threads the parent's
envelope down (narrowing-only, safe):
- `tools/agents/delegate_task.py` (~:238)
- `tools/agents/sessions_spawn.py` (~:205)
- `tools/agents/sessions_send.py` (~:223)

Add `task_bounds=parent_state.task_bounds` to each child `PipelineState(...)` (or carry it
on the existing `evolve`). No FR35 owl∩owl intersection is introduced here — only the
*task* envelope rides down. (Documented explicitly so the FR35 Epic-3 gap stays visible.)

---

## 4. Data flow

```
Durable task creation (task_runner.run)
  owl snapshot = registry.get(owl).bounds
  DurableTask(task_bounds=snapshot)  ──persist──▶  tasks.task_bounds (JSON)
  PipelineState.evolve(task_id, task_bounds=snapshot)
        │
        ▼
Dispatch seam (execute._dispatch)
  effective = effective_bounds(owl.bounds, state.task_bounds)
  block? = check_effective_bounds(effective, tool_name)   ── blocked → clean report
        │
        ▼ (delegation)
Child spawn  PipelineState(task_bounds=parent.task_bounds)  ── same seam applies

Kill ─▶ Resume (recovery._reconstruct_state)
  load tasks.task_bounds (JSON) ─▶ BoundsSpec
  base.evolve(task_id, task_bounds=loaded)  ── envelope survives the kill
```

---

## 5. Error handling & edge cases

- **Owl/registry miss during snapshot** → `None` envelope, logged at DEBUG (never raise;
  an unbounded owl stays unbounded). Same best-effort posture as the existing
  `acting_owl_manifest` lookup in `execute.py`.
- **Empty allowlist via narrowing** → if owl `{A,B}` ∩ task `{C}` → `frozenset()` (deny-all).
  This is the existing documented fail-closed footgun in `BoundsSpec`; correct and intended.
- **Legacy task rows (pre-0048)** → `task_bounds` column NULL → `None` → no envelope.
- **Non-durable turns** → no snapshot taken; `task_bounds` is `None`; dispatch is
  byte-for-byte identical to S1. (No persistence path touched.)
- **JSON corruption on load** → `BoundsSpec.model_validate_json` raises; recovery already
  runs under its own error handling — surface loudly (a corrupt persisted envelope must not
  silently degrade to "unbounded"; fail the recovery of that task rather than escalate).
- **`source` computation** must not change the block decision — it is logging only.

---

## 6. Testing

All driven from business outcomes; only the AI provider is mocked.

**Unit (`tests/authz/`)**
- `effective_bounds()` — all four None/set combinations, incl. `None ∩ task → task` and
  disjoint → `frozenset()`.
- `check_effective_bounds()` — None → permit; allowlist hit/miss.

**Dispatch (`tests/authz/test_bounds_dispatch.py` extension)**
- Owl `{A,B}`, `state.task_bounds={A}` → A dispatches, B returns the clean block string
  (blocked **by the task**, even though the owl alone permits B).
- Owl `{A,B}`, `state.task_bounds={C}` → C blocked (task cannot widen).

**Durable persistence (`tests/.../durable/`)**
- Store `create → get` round-trips a non-trivial `task_bounds` (frozenset + a network/ caps
  axis) byte-for-byte.
- `task_runner.run` snapshots the owl's bounds into the created task + the durable state.

**Recovery / kill-resume**
- `_reconstruct_state` threads persisted `task_bounds` into the resumed state (both
  checkpoint-present and checkpoint-absent branches).
- **TOCTOU test:** create a durable task under owl bounds `{A}`; *widen* the owl to `{A,B}`;
  resume → effective bounds still forbid B (snapshot held the line).

**Gateway integration journey (`tests/journeys/`)** — extends the J4 pattern (real adapter
→ scanner → AsyncioBackend, scripted owl as the only mock):
- A bounded owl runs under a task envelope; the scripted owl calls an allowed tool (runs)
  and a task-forbidden tool (blocked cleanly); the session continues and delivers a final
  answer. Asserts user-visible outcomes (allowed tool ran, forbidden one didn't, reply
  delivered) — not tool return shapes.

---

## 7. Out of scope (tracked)

| Item | Why deferred | Revisit |
|---|---|---|
| Goal-aware envelope computation | E2-S3 preflight planner | E2-S3 |
| Authorizer / consequential-by-default policy | E2-S4 | E2-S4 |
| FR35 parent∩child **owl**-bounds at delegation | Epic 3 (egress/sandbox) | Epic 3 |
| fs/network/data/caps axis *enforcement* | Epic 3+ / budget governor | Epic 3, E2-S4/S5 |

The `intersect()` stub for the non-tools axes is unchanged (keeps `self`); S2 only wires
the TOOLS axis through, consistent with S1.
