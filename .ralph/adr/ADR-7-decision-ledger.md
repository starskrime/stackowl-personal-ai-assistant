# ADR-7 — DecisionLedger: every authority emits a typed verdict to one queryable per-turn record

- **Status:** Proposed
- **Theme:** T7. Closes/strengthens F-9, F-19, F-28; ⤷F-10, F-39, F-47, F-72.
- **Depends on:** ADR-1/2/3/5/6 (their authorities are the emitters). Largely downstream — build last.

## Context
Why the agent did what it did is not reconstructable: recovery trace is English-only and suppressed on
a floor (F-9/10), the escalation cascade logs only the ESCALATE branch (F-19), there is no next-step
signal on a tool result (F-28), heuristic influence is opaque (F-47), the classifier verdict is unlogged
(F-72), a crash is silent (F-39). The 4-point logging + `traceId`/`withSpan` observability captures
*execution*, not *decisions+verdicts*. Crucially, the decisions that need tracing are exactly the
verdicts the ADR-1–6 authorities already produce — so this is mostly a *consumption* problem, not new
instrumentation. Directives: nothing removed (logging stays; a structured ledger is added alongside).

## Decision
Introduce one **`DecisionLedger`**: a per-turn, queryable record to which every authority emits a typed
**`Decision{point, inputs, verdict, reason, alternatives_considered, evidence}`** — the
`AcceptanceAuthority` (accepted/why), `RecoveryActuator` (which rungs tried/why surrendered),
`ReversibilityResolver` (act-vs-ask/why), `LearnedContext` (which heuristic steered, with provenance),
router/classifier (verdict + confidence). The agent's "explain what you did and why" answer is then a
*read* of the ledger, not a reconstruction.

## Why this, not the alternatives
1. *Improve ad-hoc logging at each decider (F-9/19/72 individually).* Rejected: that's the current
   state — voluntary, per-site, inconsistent, and it can't answer "why" as a whole.
2. *Rely on spans/traceId.* Rejected: spans record *what executed and how long*, not *what was decided
   and why it beat the alternatives*.
Powerful machine: recording rich per-decision evidence every turn is affordable.

## Shape
- `Decision` value type + `DecisionLedger` keyed by `traceId` (reuse the existing trace context;
  `AsyncLocalStorage`-equivalent already propagates it). Each authority from ADR-1–6 emits one
  `Decision` at its verdict point. Subsumes by delegation: `recovery_summary` reads the ledger and
  localizes via `state.language` (closes F-9/10); the gateway emits a crash `Decision` + user notice
  (F-39); the next-step signal (F-28) becomes a `Decision` of kind `next_step`; heuristic provenance
  (F-47) and classifier confidence (F-72) become `Decision` rows.
- Existing 4-point logs stay; the ledger is the structured, queryable layer over them.

## Invariant established
**Every consequential decision a turn makes is a typed, queryable record with its verdict, reason, and
the alternatives it beat.** Explainability stops being voluntary/per-site.

## Migration plan (flag-gated; default ON once verified)
1. Land `Decision`/`DecisionLedger` with zero emitters → off = byte-identical (empty ledger).
2. As each ADR-1–6 authority lands, it emits its `Decision` (the ledger grows with the authorities).
3. Point `recovery_summary`, the crash path, the next-step signal, and `/explain`-style surfaces at the
   ledger.

## Verification
- A test that a turn which recovered + acted-on-assumption + was steered by a heuristic produces a
  ledger with all three `Decision`s and their reasons.
- Live: ask the agent "why did you do that?" and confirm the answer is the ledger, not a confabulation.

## Blast radius, risk, rollback
Additive (new record alongside logs); flag-gated. Risk: ledger volume (mitigated: per-turn scope +
retention policy; powerful machine). Rollback: flag off → ledger empty, logs unchanged.

## Effort & dependencies
**M.** Build last; it consumes ADR-1–6's verdicts. Low risk, high explainability payoff.
