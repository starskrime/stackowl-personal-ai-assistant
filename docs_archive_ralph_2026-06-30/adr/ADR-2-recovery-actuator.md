# ADR-2 — RecoveryActuator: one ladder any failing operation hands a failure to

- **Status:** Proposed
- **Theme:** T2. Closes/strengthens F-5, F-6, F-7, F-8, F-16, F-17, F-18, F-21, F-24, F-35, F-37,
  F-40, F-41, F-55, F-60, F-62, F-64, F-65, F-66, F-67.
- **Depends on:** ADR-1 (a trustworthy "this failed" signal). Pairs with ADR-6.

## Context
Retry / tier-fallback / substitution / replan / re-arm / re-dispatch exist as ~12 point-solutions
(execute loop, `llm_gateway`, registry cascade, scheduler `_mark_failed`, channel adapters, objective
driver, gateway turn registry), each with gaps the audit found. The B4 recovery ladder
(`pipeline/steps/execute.py`) is real but bound to tool dispatch. Directives: nothing removed (each
site keeps working; it gains a shared policy); verification > representation (recovery only counts when
ADR-1 re-verifies the retried effect).

## Decision
Introduce one **`RecoveryActuator`** with a typed **`Failure`** input and a bounded **ladder**:
`classify → retry(once, transient) → reroute(alt provider/channel/tier) → substitute(capability sibling)
→ replan(decompose remainder) → escalate/honest-surrender`. Any operation that can fail (tool dispatch,
provider call, delivery, objective step, scheduled job, in-flight turn) hands its `Failure` +
`recover()` thunk to the actuator instead of returning/raising to the user. The actuator re-verifies
each rung's result through the ADR-1 `AcceptanceAuthority` and stops at the first trustworthy success
or honest surrender.

## Why this, not the alternatives
1. *Keep per-site recovery, fix each gap.* Rejected: that is the current state; every new failing
   subsystem re-surrenders by default (F-16/40/64 are three different sites with the same bug).
2. *A decorator/retry-library at each call site.* Rejected: retries without classification re-issue
   consequential actions and re-issue deterministic failures (F-7/F-22); recovery needs the
   capability-graph (substitution) and the goal (replan), which a generic decorator lacks.
The powerful-machine context lets the ladder try more rungs (reroute+substitute+replan) without a
latency budget cutting it short.

## Shape
- `Failure{kind, consequential, transient, capability_tag, goal_ref, attempt_history}`. `kind` is
  derived, not keyword-matched (reuse `is_transient_failure` / `DEFAULT_DEAD_HANDLE_MARKERS`, no new
  word lists).
- `RecoveryActuator.recover(failure, attempt) -> Outcome` — `attempt` is the idempotent thunk to re-run.
- Subsumes by delegation: the B4 execute-loop ladder becomes the actuator's tool-dispatch caller;
  `llm_gateway` hands provider faults to it (closes F-16/17/18/21); channel adapters hand transport
  failures (F-64/65/66); scheduler hands exhausted jobs (F-60/62); objective driver hands blocked
  sub-goals (F-40/41) and replans; gateway hands lost in-flight turns (F-35/67/37) for replay. None of
  these sites are deleted — they delegate.
- Consequential actions are never auto-retried (preserve the existing guard); they go straight to
  reroute/substitute/escalate.

## Invariant established
**A recoverable failure is never surrendered to the user before the ladder is exhausted, and a
"recovered" result is itself ADR-1-verified.** No call site may convert a failure into a user-visible
give-up on its own.

## Migration plan (flag-gated; default ON once verified)
1. Extract the B4 ladder into `RecoveryActuator` (behavior-preserving) → off = identical.
2. Route provider faults (F-16/17/18), then channel transport (F-64/65/66), then scheduler/objective/
   gateway, one subsystem per step, each behind the flag.
3. Each rung re-verifies via ADR-1; "recovered" is only claimed on a trustworthy verdict.

## Verification
- A fault-injection matrix: for each subsystem, inject a transient fault and assert the ladder recovers
  + re-verifies; inject a consequential fault and assert *no* auto-retry.
- Live: kill the core mid-turn → the turn replays and completes (F-35/67), not a dead spinner.

## Blast radius, risk, rollback
Touches every failure path; flag-gated per subsystem. Risk: an over-eager reroute masks a real outage
(mitigated: bounded attempts + ADR-6 escalation + the ledger records every rung). Rollback: flag off.

## Effort & dependencies
**L.** After ADR-1. Shares the verify step with ADR-1 and the heal step with ADR-6.
