# Epic 2 Story 2 — Tool-Scope Envelope + Resume-Monotonicity (E2-S2)

> Story name in the roadmap: "the authorization envelope." **Honest scope name:** a
> *tool-scope envelope with a resume-monotonicity guarantee.* S2 enforces exactly ONE axis
> (TOOLS). The other four `BoundsSpec` axes are modeled but **not** enforced until Epic 3 —
> and S2 makes it structurally impossible for a *task envelope* to silently imply a
> guarantee on an unenforced axis.

**Status:** Design approved + hardened via party mode (Winston/Murat/Dr. Quinn/Amelia), 2026-06-04
**Builds on:** E2-S1 (`BoundsSpec` + tools-axis enforcement, commit `1ae9663`)
**Followed by:** E2-S3 preflight planner (populates the `task_envelope` slot with a goal-derived, tighter spec), E2-S4 authorizer/budget governor
**FRs:** FR34-adjacent (task envelope), FR35-adjacent (no-escalation-via-delegation — partial/runtime form wired here)

---

## 1. Problem (root cause, per Dr. Quinn)

The threat an "authorization envelope" addresses is **drift**: a ReAct loop wanders, a
delegated child inherits the parent's full reach, a confused owl reaches for a tool the
*goal* never justified. The job is **least-privilege-per-task** — collapse the gap between
"what this owl *can* do" and "what this task *needs*." That gap is the attack surface.

S1 enforces an owl's *standing* bounds. It does nothing about drift within a task, about
delegation escalation, or about an owl's bounds widening mid-task. S2 supplies the
mechanism. **The intelligent, goal-derived envelope contents are E2-S3's job** (the
preflight planner); S2 ships the carrier, the composition, the TOCTOU guard, the delegation
floor, and the honesty guard.

---

## 2. The effective-bounds model (the load-bearing decision)

The panel rejected a single `task_bounds` field doing double duty. Effective bounds for any
dispatch are a **three-way narrowing intersection**:

```
effective = owl.bounds(now)  ∩  creation_ceiling  ∩  task_envelope
```

| Term | Source | S2 value | Purpose |
|---|---|---|---|
| `owl.bounds(now)` | live owl manifest | as today (S1) | the owl's standing bounds — always a factor |
| `creation_ceiling` | snapshot of `owl.bounds` at **durable task creation**, persisted | the snapshot (durable tasks); `None` otherwise | **resume-monotonicity / TOCTOU ratchet** — a resumed task can't gain powers the owl acquired *after* it started |
| `task_envelope` | E2-S3 preflight planner | **always `None` in S2** (the slot) | least-privilege-per-task; S3 fills the hole, S2 never fights it |

Two crucial properties fall out of always keeping `owl.bounds(now)` in the intersection:

- **The snapshot is honest.** `creation_ceiling == owl.bounds` at creation, so on a normal
  run it narrows nothing (`owl ∩ owl = owl`). Its *only* real effect is on **resume after
  the owl's bounds widened** — then `owl.bounds(now) ∩ creation_ceiling` clamps to the
  narrower historical set. We ship it for exactly that property and name it that way. No
  overselling: it is a ratchet, not least-privilege.
- **Legacy/NULL `creation_ceiling` is NOT a fail-open.** A missing ceiling → `None` → no
  extra clamp → `effective = owl.bounds(now)`. A resumed pre-migration task therefore runs
  under the owl's *current* bounds (a real floor), never global-unrestricted. This
  structurally dissolves the single highest-probability escalation Murat flagged.

---

## 3. Components (each small, independently testable)

### 3.1 Carrier — two additive fields on `PipelineState`

```python
# pipeline/state.py  (frozen=True; additive; carried across evolve())
creation_ceiling: BoundsSpec | None = None   # durable TOCTOU snapshot (persisted)
task_envelope:    BoundsSpec | None = None   # E2-S3 slot; always None in S2
```

Both are security-load-bearing now. Mitigation for "silent omission on a new `evolve()`/
construction site" (Winston): a **single** `compute_effective_bounds(state, owl_registry)`
helper is the one source of truth, used at *both* the dispatch seam and the delegation-spawn
sites, and there is **one** child-state builder helper (§3.4) so a new spawn path can't drop
the fields by omission.

### 3.2 The combiner + enforcement honesty — `authz/`

```python
# authz/bounds_guard.py
def effective_bounds(*specs: BoundsSpec | None) -> BoundsSpec | None:
    """Narrowing-only fold of N optional specs. None terms are skipped.
    All-None → None (the owl is genuinely unbounded). Total + narrowing:
    every defined term can only tighten. (9-cell truth table pinned in tests,
    incl. disjoint tools → frozenset() deny-all.)"""

def check_effective_bounds(effective: BoundsSpec | None, tool_name: str) -> str | None:
    """None → permit (unrestricted owl). Else effective.permits_tool()."""
```

`check_tool_bounds(manifest, name)` is **kept** as a thin back-compat wrapper:
`check_effective_bounds(effective_bounds(manifest.bounds if manifest else None), name)`.
**Invariant (Amelia's highest-risk line):** `effective_bounds(owl) == owl` exactly — the
single-arg fold is identity, so no existing caller silently tightens. Pinned by test.

**Enforcement honesty (Dr. Quinn / Murat P0-1) — `authz/enforcement.py`:**
```python
ENFORCED_AXES = frozenset({"tools"})   # auto-grows as Epic 3 wires fs/network seams

def assert_task_narrowing_enforceable(owl: BoundsSpec | None, task: BoundsSpec) -> None:
    """Fail CLOSED if a TASK-scoped spec narrows an axis no seam enforces.
    A task envelope that tightens fs/network/data/caps relative to the owl is
    REFUSED (DomainError) — the model may never imply a guarantee it can't keep.
    The creation_ceiling (an exact copy of owl.bounds) narrows nothing, so it
    passes trivially; only a genuinely-narrowing task envelope can trip this."""
```
Called wherever a `task_envelope` is *accepted* (the E2-S3 entry, and any test/caller that
sets `task_envelope`). In S2 `task_envelope` is always `None`, so this is a guard exercised
by unit tests and ready for S3 — it can never silently honor an unenforceable narrowing.

### 3.3 The dispatch seam — `pipeline/steps/execute.py`

Replace the S1 owl-only check in `_dispatch` with the shared helper:

```python
effective = compute_effective_bounds(state, owl_registry)   # owl ∩ ceiling ∩ envelope
bounds_block = check_effective_bounds(effective, name)
```

`compute_effective_bounds` resolves `owl.bounds(now)` from the registry and folds in
`state.creation_ceiling` and `state.task_envelope`. **Fail-closed hardening (Murat P0-4/5):**
- No registry / owl found with `bounds=None` → unrestricted, **byte-for-byte S1** (an
  unbounded owl stays unbounded; non-bounds flows untouched).
- A *bounded* owl whose effective-bounds computation **raises** → **DENY** (catch-and-deny,
  loud log) — never fall through on an error in a security path.
The surrounding machinery (`denied_this_run` loop-stop, the single authoritative WARNING,
bounds-before-consent ordering) is unchanged. On the **deny branch only**, log
`denied_by: "owl" | "ceiling" | "task"` (computed once on the error path — not a per-dispatch
recompute, per Amelia).

### 3.4 Default derivation, durable persistence, delegation floor

**Model:** add to `DurableTask`: `creation_ceiling: BoundsSpec | None = None`.

**Migration `0048_tasks_creation_ceiling.sql`:**
```sql
ALTER TABLE tasks ADD COLUMN creation_ceiling TEXT;  -- JSON BoundsSpec; NULL = no ceiling
```
Additive nullable column (0046/0047 pattern). NULL → `None` → owl-bounds-only on resume.

**Store** (`durable/store.py`): `_SELECT_FIELDS` += `creation_ceiling`; `create()` writes
`spec.model_dump_json()` **or SQL `NULL`** (never the string `"null"`); `_row_to_task()`
treats SQL `NULL` as `None` *before* calling `BoundsSpec.model_validate_json` (which raises
on `None`). Empty allowlist `frozenset()` (deny-all) and `None` (unrestricted) are opposite
and both must survive the round-trip distinctly (tested).

**Snapshot at creation (placement per Amelia (d)):** taken at the durable-goal routing seam
that *already holds the owl* and constructs the `DurableTask` (verify against
`durable/task_runner.py` + B3 routing `1ffd587`); the owl's `bounds` are passed in as data —
**do not** import `owl_registry` into the runner just for this. Both the persisted
`DurableTask.creation_ceiling` and `durable_state.creation_ceiling` get the snapshot.

**Recovery** (`durable/recovery.py::_reconstruct_state`): thread `task.creation_ceiling` into
the reconstructed state's `evolve()` in **both** branches (checkpoint-present and absent).
Corrupt JSON → fail the task's recovery loudly (never degrade to unbounded).

**Delegation floor (FR35-lite, Murat P0-2):** the three child-spawn sites
(`delegate_task.py`, `sessions_spawn.py`, `sessions_send.py`) build the child via the single
child-state helper, which sets:
```python
child.creation_ceiling = compute_effective_bounds(parent_state, owl_registry)  # parent EFFECTIVE
```
So `child_effective = child_owl.bounds(now) ∩ parent_effective ∩ None ⊆ parent_effective` —
a narrow parent can no longer reach a broad capability through a broad child owl. This is the
**runtime** no-escalation guarantee; full owl-manifest reconciliation (parent_owl ∩ child_owl
at the manifest layer) remains Epic 3 FR35, documented.

---

## 4. Data flow

```
Durable task creation (routing seam, holds owl)
  ceiling = owl.bounds                       ──persist──▶ tasks.creation_ceiling (JSON|NULL)
  state.evolve(task_id, creation_ceiling=ceiling)
        │
        ▼
Dispatch seam (_dispatch)
  effective = compute_effective_bounds(state)   # owl(now) ∩ ceiling ∩ envelope(None)
  check_effective_bounds(effective, tool)       # block → clean report; error → DENY
        │
        ▼ delegation (single child-state helper)
  child.creation_ceiling = parent_effective     # child ⊆ parent_effective
        │
Kill ─▶ Resume (recovery._reconstruct_state)
  load tasks.creation_ceiling → BoundsSpec|None
  base.evolve(task_id, creation_ceiling=loaded) # NULL → owl-bounds-only (safe floor)
```

---

## 5. Error handling & fail-closed invariants (ranked, from Murat)

| Rank | Invariant | Resolution in this design |
|---|---|---|
| P0 | NULL/legacy ceiling ≠ global-unrestricted | three-way model: `owl.bounds(now)` always a factor → NULL ceiling resumes under owl bounds |
| P0 | child_effective ⊆ parent_effective | child ceiling = parent's computed effective (§3.4) |
| P0 | combiner total + narrowing; `None`-handling | `effective_bounds` skips None terms; all-None→None; 9-cell truth table test |
| P0 | registry miss / check exception on a **bounded** owl → DENY | catch-and-deny in `compute_effective_bounds`; unbounded owls unchanged |
| P0 | ceiling survives kill/resume | persisted on the task row + re-threaded by recovery (independent of checkpoint blob) |
| P0 | empty-allowlist vs null survive JSON distinctly | store mapper branches on SQL NULL; round-trip test |
| P0 | task envelope can't silently narrow an unenforced axis | `assert_task_narrowing_enforceable` fails closed at acceptance |
| P1 | consent can't re-admit an out-of-bounds tool | bounds-before-consent unchanged; add a test pinning it |
| P1 | replayed tool calls re-checked vs current effective | resume drives through the same seam; add a test |
| — | back-compat: `effective_bounds(owl) == owl`; unbounded owls byte-for-byte | single-arg-identity test + wrapper-verdict-unchanged test |

---

## 6. Testing (TDD red-first; only the AI provider mocked)

**`tests/authz/test_bounds_guard.py`** — `effective_bounds` 9-cell truth table (incl.
disjoint→deny-all, single-arg identity, all-None→None); `check_effective_bounds` allow/deny;
back-compat wrapper verdict unchanged {allowed, denied, unbounded-owl}.
**`tests/authz/test_enforcement.py`** — `assert_task_narrowing_enforceable`: tools-narrowing
passes; fs/network/data/caps narrowing on a task envelope raises; ceiling-as-owl-copy passes.
**`tests/authz/test_bounds_spec_roundtrip.py`** — JSON round-trip (frozenset/tuple), `None` ≠
`frozenset()` distinct, model equality order-insensitive.
**`tests/pipeline/test_pipeline_state_bounds.py`** — fields default `None`; `evolve` carries
by identity (`is`), confirming `model_copy` not dump/reload.
**`tests/authz/test_bounds_dispatch.py`** (extend) — owl `{A,B}`, ceiling `{A}` → A runs, B
blocked; bounded-owl computation error → DENY; unbounded owl unchanged.
**`tests/durable/test_store_ceiling.py`** — persists JSON; `None`→SQL NULL (raw column `IS
NULL`); NULL→`None`; create→get round-trip.
**`tests/durable/test_recovery_ceiling.py`** — threads ceiling both branches; NULL→`None`
resumes under owl bounds; corrupt JSON fails recovery loudly.
**`tests/durable/test_goal_routing_ceiling.py`** — routing seam snapshots owl bounds into the
created task + durable state.
**`tests/pipeline/test_child_floor.py`** — delegate/spawn/send: child ceiling = parent
effective; **narrow parent + broad child owl → child denied the broad tool** (the P0-2 proof).
**`tests/journeys/test_tool_scope_envelope.py`** (J4-pattern gateway) — task-scoped tool
denied end-to-end; **kill→resume preserves the ceiling** (the resume-monotonicity proof:
widen the owl after creation, resume, assert the new tool stays denied).

---

## 7. Commit split (bisectable, per Amelia — steps 3/4 separate so an enforcement regression bisects to one commit)

1. `BoundsSpec` JSON round-trip tests (+ any serialization fix) — pure, no schema.
2. `PipelineState.creation_ceiling` + `task_envelope` fields + evolve-carry tests.
3. `authz/bounds_guard.effective_bounds` + `check_effective_bounds` + wrapper + `authz/enforcement` — **no seam wiring.**
4. Seam: `_dispatch` uses `compute_effective_bounds` + fail-closed DENY — behavior change, alone.
5. Migration `0048` + `DurableTaskStore` create/read + store tests.
6. Routing-seam snapshot + `recovery` threading + durable tests.
7. Single child-state helper + delegation floor at the three spawn sites + child-floor tests.
8. Gateway journeys (tool-scope deny + kill/resume monotonicity).

---

## 8. Out of scope (tracked — Phase-2 / later epics)

| Item | Why deferred | Revisit |
|---|---|---|
| Goal-aware `task_envelope` contents | E2-S3 preflight planner fills the slot | E2-S3 |
| Authorizer / consequential-by-default policy | E2-S4 | E2-S4 |
| fs/network/data/caps **enforcement** seams (+ auto-growing `ENFORCED_AXES`) | Epic 3 (sandbox/egress) / budget governor | Epic 3, E2-S4/S5 |
| Full FR35 manifest-layer parent_owl ∩ child_owl reconciliation | Epic 3 | Epic 3 |
| Enforcement-coverage auto-demotion of unmodeled→declared axes (Dr. Quinn's systemic guard) | nice-to-have; `ENFORCED_AXES` constant suffices for S2 | Epic 3 |

The non-tools `intersect()` axis stubs are unchanged (keep `self`); S2 wires only TOOLS,
consistent with S1, and now *guards* against a task envelope pretending otherwise.
