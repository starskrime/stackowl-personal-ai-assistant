# ADR-5 — Trustworthy learning: mine measured success, read it on the live path, avoid repeats within-turn

- **Status:** Proposed
- **Theme:** T5. Closes/strengthens F-26, F-43, F-46, F-47, F-48, F-50, F-51, F-54, F-72.
- **Depends on:** ADR-1 (measured success) + ADR-4 (reachable learned signal).

## Context
The learning loop (`tool_outcome_miner`, `reflection_store`, `outcome_store`, `dna_attribution`,
heuristic store) gates on `success = 1 AND failure_class IS NULL` (`outcome_store.py:176`) — a
*self-asserted* success (T1), so it can mine false wins; and most learned signal is unreachable at
decision time (T4: F-45/46/47 written, never read). The positive-only directive forbids the obvious
"learn from failures" fix. Directives honored exactly: **never store negatives**; nothing removed
(every store/column kept; the unused `mean_quality` is *consumed*, not dropped — F-46).

## Decision
Three moves, all directive-compatible:
1. **Gate mining on *measured* success.** Replace the `success=1` predicate's meaning: an outcome is
   mineable only when its ADR-1 `Verdict.accepted is True`. Positive-only is unchanged; the *positives*
   just become trustworthy. (Closes the corruption at the source — the deepest-root diagnosis.)
2. **Make learned signal reachable on the live path** (per ADR-4): a decision-time read of
   heuristics/reflections/outcomes before acting — per-call consult now affordable on the powerful
   machine (re-opens the F-4 latency deferral), live semantic (ANN) recall instead of recency-only
   (F-50), and `mean_quality` consumed in ranking (F-46). Reads, never writes negatives.
3. **Within-turn failure awareness (not persisted).** A turn-scoped, ephemeral "approaches that failed
   *this turn*" set the agent consults before re-trying — allowed because it is never written to the
   store. Closes "repeats a known-bad approach this turn" (F-26/43/72) without violating positive-only.

## Why this, not the alternatives
1. *Store failure lessons.* Rejected — violates the hard directive.
2. *Leave mining on self-asserted success.* Rejected — mines false wins; the learner reinforces broken
   tools (the documented deepest-root failure mode).
Powerful machine removes the latency reason heuristic consults / ANN recall were cut.

## Shape
- Mining predicate reads ADR-1 verdicts (the B4b `failure_class="unachieved_effect"` exclusion becomes
  a *positive* assertion: mine only `Verdict.accepted`). Subsumes by delegation — the SQL stays, its
  truth source changes.
- A `LearnedContext.consult(tool, goal)` read hook on the live decision path (ADR-4 makes it reachable);
  `find_for_tool` gains a live caller (F-45); heuristic provenance surfaced in the trace (F-47, via ADR-7).
- `TurnScratch.failed_approaches` — ephemeral, per-turn, never persisted.

## Invariant established
**The learning loop only ever mines measured successes, always reads what it learned before acting, and
never persists a negative.** Within a turn, a failed approach is not retried blindly; across turns, only
verified wins shape behavior.

## Migration plan (flag-gated; default ON once verified)
1. After ADR-1 lands, switch the mining predicate's truth source to verdicts (no schema change).
2. Wire the decision-time consult + ANN recall + `mean_quality` ranking behind a flag (default ON on the
   powerful machine).
3. Add the ephemeral within-turn failed-approaches scratch.

## Verification
- A test that a tool with `Verdict.accepted=False` is never mined as a win (false-win exclusion at
  source).
- A test that a mined heuristic for tool X actually changes the next decision (reachability).
- Live: an approach that failed earlier in a turn is not blindly repeated later in the same turn.

## Blast radius, risk, rollback
Learning subsystem; flag-gated. Risk: ANN recall surfaces noise (mitigated: ranked + evidence-gated).
Rollback: flag off → recency-only, no live consult. No data dropped.

## Effort & dependencies
**L.** Strictly after ADR-1 (truth source) and ADR-4 (reachability).
