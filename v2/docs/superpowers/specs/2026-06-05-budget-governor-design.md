# Epic 2 Story 4 — The Budget Governor (E2-S4)

> A **deterministic resource ceiling** for an agentic run: enforce the consumption caps of
> `BoundsSpec.caps` — **steps + time** (durable, exact) and **cost** (best-effort) — at one
> execute-step site. On breach with no human present, the run **STOPS** (partial result +
> breach note); a *present* human gets one in-memory **raise/stop** choice. Reshaped from a
> maximal draft by party-mode review (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design reshaped + approved (2026-06-05); pending spec re-review
**Builds on:** S1 `ResourceCaps`, S2 `compute_effective_bounds`, S3 durable plumbing, existing `CostTracker`/`ClarifyGateway`/`on_iteration_complete` seam
**Followed by:** **E2-S5** (durable budget *negotiation*: persisted per-resume raise + park/resume + migration) · separate later story (`max_concurrency` as `min()` at the concurrency seam) · cost-durability (persist `spent_usd_to_date`) · caps min-composition

---

## 1. Problem & honest scope

`BoundsSpec.caps` is modeled but unenforced — an agentic run can loop forever, run unbounded
wall-clock, or (on metered providers) spend unbounded money. S4 makes the **consumption** caps
real. The party-mode review established what S4 is — and is **not**:

- **S4 = enforcement.** A hard ceiling that **acts when no human is watching** (cron, a long
  autonomous run). Its irreducible duty: *a run that exceeds its bounds is terminated
  deterministically, with the breach recorded, without requiring a human.*
- **NOT S4 (→ S5 / later):** the durable human *negotiation* (park a detached task, persist a
  one-shot raise, resume with a raised cap, migration); `max_concurrency` (a structural
  spawn-admission control, not a consumption integral — and "override" would *loosen* a bound,
  which violates narrowing-only); durable cost-durability.

**Cap reliability is asymmetric and we state it honestly:**
- **`max_steps`** — perfectly measurable (an integer in the ReAct loop). Exact.
- **`max_time_s`** — perfectly measurable (wall-clock); the **universal backstop** that catches
  a no-cost local agentic loop (the box runs ollama/gemma, which *causes* such loops).
- **`max_cost_usd`** — **best-effort**: depends on provider token pricing, is **$0 on local
  models**, and (without durable spend) resets per run-attempt. Useful on metered cloud
  providers; documented as non-durable. The real durable ceiling is steps + time.

---

## 2. Approved decisions

| Fork | Decision |
|---|---|
| Scope | Deterministic ceiling: **cost (best-effort) + steps + time**. No concurrency, no durable-raise, no migration. |
| Seam | One check at the **execute-step iteration boundary**, via the **existing `on_iteration_complete` callback** (no new provider kwarg). |
| Breach, no human | **Deterministic STOP** — deliver the partial result + a structured breach note. |
| Breach, human present (interactive) | **In-memory clarify "raise/stop"** — Raise bumps the governor's caps for this run and continues; Stop / timeout / no-gateway → **fail-closed STOP**. |
| Missing signal | **Fail toward halt** for the cap we can't measure — but a missing *cost* signal never disables steps/time (time is never unknown). |
| Cost durability, concurrency, durable park-raise | **Deferred** (S5 / later) — tracked §7. |

---

## 3. Architecture

### 3.1 `BudgetGovernor` — `src/stackowl/pipeline/budget/governor.py`

Built once per drive from the acting owl's effective caps. **Stateful** (owns its own monotonic
step counter — non-durable turns have no `DurableReActContext.iteration`, so the governor does
not depend on one):

```python
@dataclass(frozen=True)
class BudgetBreach(Exception):           # raised through the iteration callback
    cap: Literal["cost", "steps", "time"]
    limit: float
    actual: float

class BudgetGovernor:
    def __init__(self, caps: ResourceCaps, *, cost_tracker, trace_id,
                 started_monotonic, clock): ...
    def raise_caps(self, **bumped) -> None:          # in-memory raise (interactive)
        ...
    def check(self) -> BudgetBreach | None:
        # called once per completed ReAct iteration. Increments the step counter,
        # then evaluates each SET cap; returns the FIRST breach or None.
        #   steps: self._step >= caps.max_steps
        #   time:  clock.monotonic() - started >= caps.max_time_s
        #   cost:  cost_tracker.turn_cost_usd(trace_id) >= caps.max_cost_usd   (best-effort)
```

- Caps source: `effective = compute_effective_bounds(state, owl_registry)`; `effective.caps`.
  `effective is None` (unbounded owl) or all-None caps → governor is a **no-op** (every current
  turn unchanged).
- **Cost is best-effort & None-safe:** if `max_cost_usd` is None, or `turn_cost_usd` returns 0
  (local/unpriced), the cost cap simply never trips — but steps/time still enforce. A missing
  *cost* signal NEVER disables the governor.
- `raise_caps` lifts the in-memory limits (used only by the interactive raise path).

### 3.2 Enforcement seam — reuse `on_iteration_complete`, no provider changes

The per-iteration check rides the **existing** `on_iteration_complete` callback that all four
provider tool loops already invoke. `execute` composes the callback:

- **Durable path** (`_call_durable`): `cb = compose(budget_gate, make_checkpoint_callback(...))`.
- **Non-durable path** (`_call_default`): wire `on_iteration_complete = budget_gate` (today it's
  unset on this path) — a one-line execute change, **no provider edits**.

`budget_gate` is `execute`'s closure: it calls `governor.check()`; a non-None breach is handled
by §3.3. **Implementer MUST verify all four providers `await on_iteration_complete`** (so a
`BudgetBreach` raised inside it propagates and breaks the loop — a fire-and-forget invocation
would swallow it; fix any that do). This is the only provider-file audit in S4.

### 3.3 Breach handling — at the `execute` layer (never in the provider loop)

`budget_gate` decides by context; the provider only ever *signals* upward (no `ClarifyGateway`
on the provider stack — clarify lives at execute where suspension is already modeled):

- **Interactive** (`state.interactive` + a `ClarifyGateway` wired): run the clarify round-trip
  *"Budget cap `<steps: 20>` reached — Raise or Stop?"*. **Raise** → `governor.raise_caps(...)`
  (a bounded in-memory increment) and **return** (the loop continues). **Stop / timeout / no
  gateway** → raise `BudgetBreach` (fail-closed). The clarify is invoked from `budget_gate`
  (execute's code) with the existing `wait_timeout_s`; the loop is briefly suspended while a
  *present* human answers — bounded by the timeout.
- **Non-interactive** (cron / parliament / detached durable): raise `BudgetBreach` immediately —
  deterministic STOP.

`complete_with_tools` breaks on the propagated `BudgetBreach`, returning the answer + tool calls
accumulated **before** the breach. `execute` catches `BudgetBreach` (alongside the existing
`DurableReplayUncertain` catch) and produces the final state: the **partial result is always
delivered**, plus a structured note — `"budget cap reached: <cap> limit=<L> actual=<A>"` — appended
to the response and recorded in `state.errors`/outcome. A durable task is finalized as a normal
incomplete/parked-stopped task (the existing machinery); S5 later adds the persisted-raise resume.

### 3.4 Caps source & in-memory raise only

Caps come from the acting owl's effective bounds (`intersect` keeps owl caps — min-composition
is a refinement, §7). The interactive **raise is in-memory only** (mutates the live governor),
scoped to this drive, never persisted, never mutates the owl's `BoundsSpec`. The *durable*
raise-and-resume (persisted override + migration) is **S5**.

---

## 4. Data flow

```
execute._run_with_tools
  effective = compute_effective_bounds(state)               # → effective.caps
  governor  = BudgetGovernor(effective.caps, cost_tracker, trace_id, t0, clock)
  budget_gate = closure(governor, state, clarify_gateway)   # §3.3

  provider.complete_with_tools(..., on_iteration_complete = compose(budget_gate, checkpoint?))
     per iteration:  await on_iteration_complete(iter_state)
                        breach = governor.check()
                        if breach: interactive? clarify → raise-in-mem+continue | STOP
                                   else            → raise BudgetBreach
  except BudgetBreach as b:
     deliver partial result + "budget cap reached: {b.cap} {b.limit}/{b.actual}"  → finalize
```

---

## 5. Error handling / invariants

| Concern | Resolution |
|---|---|
| Unbounded owl / all-None caps | governor no-op → byte-for-byte today |
| `on_iteration_complete` unset (non-S4 callers) | unchanged; governor only runs when execute wires `budget_gate` |
| Missing/zero cost signal (local, unpriced) | cost cap never trips; **steps/time still enforce** — governor never disabled by a None cost |
| Clarify timeout / no gateway / non-interactive | **fail-closed STOP** (never continue past a hard cap awaiting a human) |
| Breach must not lose work | partial answer + tool calls before the break are always delivered |
| Breach raised inside the callback must propagate | implementer verifies all 4 providers `await on_iteration_complete` (no fire-and-forget) |
| Cost resets on resume (in-memory ledger) | **documented**: cost is per run-attempt/best-effort; durable cost (`spent_usd_to_date`) is S5/later. Steps is cumulative (persisted iteration); time per-attempt. |
| Interactive raise is in-memory | never persisted, never edits owl bounds; durable raise is S5 |
| First-exceeded precedence | `check()` returns the first set cap that trips; cheap (no SQLite — `turn_cost_usd` is in-memory) |

---

## 6. Testing (TDD; only the AI provider mocked)

**`BudgetGovernor` units (`tests/pipeline/budget/`)** — steps trips at the limit not before;
time trips on elapsed; cost trips on `turn_cost_usd ≥ cap`; **None/zero cost never trips and never
disables steps/time**; all-None caps → always None; `raise_caps` lifts the limit; first-exceeded
precedence; stateful counter increments without any durable ctx.

**Seam / provider propagation** — a `BudgetBreach` raised from `on_iteration_complete` propagates
out of `complete_with_tools` and breaks the loop, returning the partial; **one test per provider
impl** (anthropic/openai/ollama/openai-compatible) proving each awaits the callback (guards the
fire-and-forget hazard); `on_iteration_complete=None` → loop unchanged (regression).

**Breach policy (`tests/pipeline/steps/`, provider mocked)** — non-interactive breach → STOP +
partial + note (no clarify); interactive breach → clarify called: Raise → governor caps bumped +
loop continues; Stop → STOP + partial; **clarify timeout → fail-closed STOP**. Each asserts the
partial result is delivered and the note names the cap + limit/actual.

**Gateway journey (`tests/journeys/`)** — a non-interactive durable task with `caps.max_steps=2`:
the scripted owl loops; at step 2 the run **stops deterministically**, delivers the partial + the
"budget cap reached: steps 2/2" note, and the task finalizes (not a crash, not a hang). A second
journey: interactive turn hits `max_steps`, clarify→Raise → continues past the cap.

---

## 7. Out of scope / deferred (tracked)

| Item | Why | Where |
|---|---|---|
| Durable budget **negotiation** — park a detached task + persist a one-shot raise + resume with a raised cap (migration) | hard cap's job is to stop without a human; raising is negotiation | **E2-S5** |
| Durable cost (`spent_usd_to_date`) → laundering-proof money cap | closes park-resume N×cap; needs persisted spend | later (with S5) |
| `max_concurrency` as `min(manifest, cap)` at the `ConcurrencyGovernor`/tool-path seam | structural control, not a consumption integral; override would loosen | separate story |
| Pre-spend cost **reservation** (bound a "fat iteration" overrun to ≤ one completion) | post-hoc per-iteration cost is a best-effort breaker; reservation is the durable money control | later (with cost-durability) |
| Caps **min-composition** across owl∩ceiling | S1 `intersect` keeps owl caps | refinement |
