# Epic 2 Story 4 — The Budget Governor (E2-S4)

> Enforces the `caps` axis of `BoundsSpec` (modeled in S1, unenforced until now): per-run
> resource ceilings on **cost, steps, time, and concurrency**. A *hard* governor that
> **seeks a human decision** on breach (park / clarify-raise / fail-safe stop) — distinct
> from S5's soft cost-pause.

**Status:** Design approved (forks resolved, 2026-06-05); pending party-mode hardening
**Builds on:** E2-S1 (`ResourceCaps` model), E2-S2 (`creation_ceiling`, `compute_effective_bounds`), E2-S3 (durable-task plumbing, `task_runner`/`recovery`), existing `CostTracker`/`OwlResourceGuard`/`ConcurrencyGovernor`/`ClarifyGateway`
**Followed by:** E2-S5 stop-policy (the soft pause already exists as `CostPauseGuard`); caps min-composition (refinement)

---

## 1. Problem

`BoundsSpec.caps` (`ResourceCaps`: `max_cost_usd`, `max_time_s`, `max_steps`, `max_concurrency`)
is modeled but **never enforced** — an agentic turn can spend unbounded money, loop forever,
run unbounded time, or fan out unbounded concurrency. S4 wires the `caps` axis to real
enforcement, reusing the cost/guard/governor infrastructure that already exists, and on
breach **suspends to seek a human decision** rather than silently killing or silently
continuing.

**S4 vs S5:** S4 is the *hard* budget governor — a ceiling that stops without a soft nudge.
S5's *soft* per-turn cost pause already ships as `CostPauseGuard` (a configurable lower
threshold that asks once and fails open). They compose: the soft pause asks at a low
threshold; the hard cap suspends at the owl's authorized ceiling.

---

## 2. Approved decisions

| Fork | Decision |
|---|---|
| Caps in scope | **All four**: cost, steps, time (per-iteration) + concurrency (construction-time) |
| Breach action | **Seek a human decision** (park / clarify-raise / fail-safe stop), always delivering the partial result + a clear note |
| Gating | **All agentic (tool-using) turns** — via a new `budget_check` hook on `complete_with_tools` across all providers |
| `max_concurrency` | **Overrides** `manifest.max_concurrent_requests` when set (caps value wins) |
| Raise mechanism | **Per-resume override persisted on the durable task** (one-shot; does not mutate the owl's standing bounds) |

> ⚑ **Flagged for party-mode pressure-testing:** (a) "override" can *loosen* an owl whose
> manifest concurrency is tighter than `caps.max_concurrency` — in tension with bounds being
> narrowing-only; (b) `max_concurrency`'s real enforcement surface (a single ReAct drive
> makes sequential provider calls — concurrency mostly arises from delegation/parliament
> fan-out, not within one drive), so its value needs scrutiny; (c) the all-providers hook is
> real blast radius; (d) cost/time reset per run-attempt while steps is cumulative.

---

## 3. Architecture

### 3.1 `BudgetGovernor` — the per-run caps checker (cost/steps/time)

`src/stackowl/pipeline/budget/governor.py`. Built once per drive from the acting owl's
effective caps. Pure-ish (reads `CostTracker`, the clock):

```python
class BudgetVerdict:   # OK | Exceeded
    cap: Literal["cost", "steps", "time"] | None
    limit: float | None
    actual: float | None

class BudgetGovernor:
    def __init__(self, caps: ResourceCaps, *, cost_tracker, trace_id, started_monotonic, clock): ...
    def raise_caps(self, override: ResourceCaps) -> None: ...   # one-shot raise (interactive / per-resume)
    def check(self, iteration: int) -> BudgetVerdict:
        # cost:  cost_tracker.turn_cost_usd(trace_id) >= caps.max_cost_usd
        # steps: iteration >= caps.max_steps
        # time:  clock.monotonic() - started_monotonic >= caps.max_time_s
        # returns the FIRST exceeded cap, else OK. None caps are skipped.
```

Caps source: `effective = compute_effective_bounds(state, owl_registry)`; `effective.caps`
(the S1 `intersect` keeps the owl's caps — proper min-composition across owl∩ceiling is a
documented refinement, §7). When `effective is None` (unbounded owl), caps are all-None → the
governor is a no-op.

### 3.2 The `budget_check` hook — per-iteration enforcement across all providers

Add to the `ModelProvider.complete_with_tools` ABC and every impl
(anthropic/openai/ollama/openai-compatible) a keyword param:

```python
budget_check: Callable[[int], Awaitable[bool]] | None = None
```

After each completed ReAct iteration (the same point `on_iteration_complete` fires), the loop
calls `if budget_check is not None and not await budget_check(iteration): break` — stopping
the loop cleanly and returning the accumulated answer + tool calls so far. Mirrors the
existing `persistence_check`/`on_iteration_complete` pattern; default `None` → byte-for-byte
unchanged for every current caller. A shared base-class default keeps impls thin.

### 3.3 Breach handling — `execute` owns the context decision

`execute` builds the `_budget_check` closure (it has the governor, the state, the
`ClarifyGateway`). On an `Exceeded` verdict the closure decides by context, then records the
trip on the governor (so post-loop code knows why the loop ended):

- **Interactive turn** (`state.interactive`): clarify round-trip — "Budget cap `<cost $5>`
  reached. Raise budget or Stop?" via `ClarifyGateway` (the `CostPauseGuard` pattern). *Raise*
  → `governor.raise_caps(<a bounded increment>)`, return `True` (continue). *Stop* / timeout /
  no gateway → record trip, return `False`.
- **Non-interactive durable** (`state.task_id` set, not interactive): record trip, return
  `False` → after the loop, `execute._call_durable` sets `durable_parked=True` + a
  `budget:park:<cap>:limit=<L>:actual=<A>` marker (exactly like the uncertain-replay park).
  The task is finalized **parked**, resumable with a raised cap.
- **Non-interactive non-durable** (cron/parliament): record trip, return `False` → **fail-safe
  stop**, deliver the partial result + a "budget cap reached" note.

In all cases the partial answer accumulated before the breach is delivered; the note names
the cap and the limit/actual.

### 3.4 `max_concurrency` — override the per-owl limit (construction-time)

When `caps.max_concurrency is not None`, it **replaces** `manifest.max_concurrent_requests`
for the acting owl's provider-call semaphore. Concretely: thread an effective concurrency
value into the per-owl `OwlResourceGuard` (and apply the same semaphore on the tool path,
which today bypasses the guard), so concurrent provider calls attributable to this owl are
bounded by `caps.max_concurrency`. `None` → no override (manifest value stands). (Delegation/
parliament fan-out remains bounded by the existing global `ConcurrencyGovernor`; per-owl
caps tightening of that fan-out is noted as a refinement — see §7.)

### 3.5 The raise mechanism — per-resume override on the durable task

A human "raise the budget" on a parked task records a **one-shot `ResourceCaps` override**
persisted on the durable task (new nullable column, migration `0050`,
`DurableTask.budget_override: ResourceCaps | None`). On the next resume, `task_runner.resume`
/ `recovery` applies it: `governor.raise_caps(task.budget_override)` (or threads it into the
effective caps for that run). It does **not** mutate the owl's `BoundsSpec` — the raise is
scoped to this task's next attempt, auditable, and expires after the run (cleared on a clean
finish). The human action that sets it (a command / a parked-task review UI) is wired minimally
(set the column); the rich review UX is S5.

---

## 4. Data flow

```
execute._run_with_tools
  effective = compute_effective_bounds(state)            # → effective.caps
  governor  = BudgetGovernor(effective.caps, cost_tracker, trace_id, t0, clock)
  governor.raise_caps(state.budget_override)             # if a parked task was resumed raised
  concurrency = effective.caps.max_concurrency or manifest.max_concurrent_requests   # §3.4

  provider.complete_with_tools(..., budget_check=_budget_check)   # §3.2 per-iteration
     loop iteration i:
        ... reason + tools ...
        if not await _budget_check(i): break             # Exceeded → context decision §3.3
  post-loop:
     governor.tripped? → durable: PARK (marker) ; non-durable: deliver partial + note

Parked task → human raises → DurableTask.budget_override persisted (mig 0050)
           → resume → governor.raise_caps(override) → continues from persisted iteration
```

---

## 5. Error handling / invariants

| Concern | Resolution |
|---|---|
| Unbounded owl / no caps | `effective` None or all-None caps → governor no-op → byte-for-byte today |
| `budget_check=None` (every current caller) | loop unchanged; no per-iteration check |
| Cost ledger is per-process | `cost`/`time` caps are **per run-attempt** (reset on resume); `steps` is **cumulative** (persisted `iteration`) — correct for park→raise→resume |
| Clarify unavailable / non-interactive | no human → fail-safe **stop** (durable: park) — never silently continue past a hard cap |
| Breach must not lose work | partial answer + tool calls accumulated before the break are always delivered |
| Park marker distinguishable | `budget:park:<cap>` marker distinct from `durable:park:uncertain` so the router/UX can tell them apart |
| Raise is one-shot + scoped | persisted override on the task, cleared on clean finish; never edits the owl's bounds |
| Provider hook back-compat | base-class default `None`; all impls accept + ignore when None |
| Cap precedence | first-exceeded cap reported; checks are cheap (no SQLite on the hot path — `turn_cost_usd` is in-memory) |

---

## 6. Testing (TDD; only the AI provider mocked)

**`BudgetGovernor` units (`tests/pipeline/budget/`)** — each cap trips at its limit, not
before; None caps skip; first-exceeded precedence; `raise_caps` lifts the limit;
all-None/no-caps → always OK.

**Provider hook** — `complete_with_tools(budget_check=...)`: the loop calls it per iteration
and breaks on `False`, returning the partial answer; `None` → unchanged (regression on an
existing provider test). At least the primary provider + the base contract; a fake provider
exercises the loop semantics.

**Breach contexts (`tests/pipeline/steps/`)** — interactive: clarify→Raise continues,
clarify→Stop halts+partial; non-interactive durable: parks with the `budget:park` marker;
non-interactive non-durable: halts+partial. Each asserts the partial result is delivered.

**max_concurrency** — `caps.max_concurrency` overrides `manifest.max_concurrent_requests` in
the per-owl semaphore; None → manifest stands.

**Raise round-trip (`tests/pipeline/durable/`)** — migration 0050 column round-trips
`budget_override`; resume applies it (a task parked at cost-cap, raised, resumes and continues
past the old cap); override cleared on clean finish.

**Gateway journey (`tests/journeys/`)** — a durable task with `caps.max_steps=2`: the scripted
owl loops; at step 2 the task **parks** with the budget marker (not failed), partial delivered;
a follow-up sets `budget_override` and resumes → the task continues past step 2.

---

## 7. Out of scope / refinements (tracked)

| Item | Why | Revisit |
|---|---|---|
| Caps **min-composition** across owl∩ceiling (effective caps = element-wise min) | S1 `intersect` keeps owl caps; S4 uses acting owl caps | follow-up after S4 |
| Per-owl tightening of delegation/parliament fan-out by `caps.max_concurrency` | global `ConcurrencyGovernor` already bounds it; per-owl needs a per-owl semaphore | follow-up |
| Rich parked-task review/raise UX | S4 wires the persisted override + minimal set path | E2-S5 |
| Soft per-turn cost pause | already ships as `CostPauseGuard` | shipped (S5 owns) |
| `max_concurrency` semantics if it proves low-value within a single drive | party-mode to assess | party-mode |
