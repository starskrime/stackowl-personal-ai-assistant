# Implementation Plan — Jarvis Architecture (7 ADRs)

The ADR checklist. `[ ]` = not yet shipped, `[x]` = shipped + merged to `main` + invariant
test green. **Build order is fixed** (later ADRs consume earlier ones — do NOT reorder):

> ADR-1 → ADR-4 → ADR-2 → ADR-3 → ADR-5 → ADR-6 → ADR-7

Each line: the ADR, its slug, and the findings it closes (from `RESEARCH_PLAN.md`).
Mark `[x]` only when the per-ADR Definition of Done in `IMPLEMENT_PROMPT.md` is fully met.

## Checklist (build order)

- [x] **ADR-1 — AcceptanceAuthority** (`feat/adr-1-acceptance-authority` + `feat/adr-1-effect-migrations`) — KEYSTONE. SHIPPED 2026-06-27, flag ON in prod.
  One authority answers "did it actually achieve the intended effect?"; the ≥6 disjoint proxies
  (`giveup_floor`, `overclaim_gate`, `judge_delivery`, per-tool `verify()`, `AcceptanceChecker`,
  `side_effect_committed`/progress ledger) delegate to it. Asserted → measured success.
  Closes: F-1, F-11, F-12, F-13, F-14, F-15, F-20, F-23, F-25, F-29, F-30, F-31, F-32, F-33,
  F-34, F-75, F-80, F-81, F-82, F-83. (⤷F-10) — upstream of ADR-2/5/6/7.

- [x] **ADR-4 — Reachability invariant at boot** (`feat/adr-4-reachability-invariant`). SHIPPED 2026-06-27, block mode ON in prod.
  Assert registered == reachable at boot; run the existing census; dangling half-edges fail
  closed instead of shipping green. Prevents the half-edges later ADRs would otherwise add.
  Closes: F-45, F-76, F-77, F-78, F-86. (⤷F-87)

- [x] **ADR-2 — RecoveryActuator ladder** (`feat/adr-2-*`). SHIPPED 2026-06-27, all flags ON in prod.
  One authority turns a not-trustworthy result into a bounded recovery ladder
  (retry/fallback/substitution/replan/re-arm); ALL 6 point-solutions now DELEGATE: tool dispatch
  (execute.py), provider gateway (llm_gateway.py), channel transport (deliverer.py), objective
  driver (objectives/driver.py), scheduler (scheduler.py), gateway turn replay (gateway_link.py +
  turn_registry.py). Recovery already existed at each site (the audit found point-solutions with
  gaps); ADR-2 unifies the DECISION under one `should_retry`/`Failure` authority, re-verified via
  ADR-1. Owner chose "complete literal unification" + flag default ON for the final 3 byte-identical
  routings (objective/scheduler/gateway) via AskUserQuestion. (Depends on ADR-1.)
  Closes: F-5, F-6, F-7, F-8, F-16, F-17, F-18, F-21, F-24, F-35, F-37, F-40, F-41, F-55,
  F-60, F-62, F-64, F-65, F-66, F-67.

- [x] **ADR-3 — ReversibilityResolver** (`feat/adr-3-*`). SHIPPED 2026-06-27, flag ON in code default.
  One `ReversibilityResolver` authority + declared tri-state `Reversibility` signal answers
  ACT_WITH_ASSUMPTION vs ASK for every interaction gate. ALL 5 gates DELEGATE their ask-vs-act
  decision: clarify `_resolve_default` (F-68/69/71, shared by tool pre-park + timeout + gateway
  pre-ask), objective `_park_is_irreversible` (F-44), consent `reversible`→auto-allow tier (F-27),
  cost-pause continue-and-notify (F-70), router clarify-verdict `_maybe_clarify` (F-3/56). Each
  delegation reproduces its gate's pre-ADR inline rule EXACTLY ⇒ flag ON is byte-identical (pure
  unification, the ADR-2 pattern); owner approved ON-default via AskUserQuestion. Nothing removed.
  Closes: F-3, F-27, F-44, F-56, F-68, F-69, F-70, F-71.

- [x] **ADR-5 — Trustworthy (verified-gated) learning** (`feat/adr-5-*`). SHIPPED 2026-06-27, flag ON in code default.
  Once ADR-1 makes success measured, mining/recall gate on the verified signal; add ephemeral
  within-turn failure-awareness. NEVER persists negatives (positive-only directive). (Depends on ADR-1.)
  All 3 moves shipped, each byte-identical when `trustworthy_learning` OFF: MOVE 1 (mine only MEASURED
  success) was ALREADY enforced — ADR-1/B4b collapses a `verified=False` effect to
  `failure_class="unachieved_effect"` which every learner already excludes (locked with an invariant
  test); MOVE 2 (F-50) live path reads SEMANTIC reflection recall (`semantic_for_owl`, self-degrades to
  recency on embed failure); MOVE 3 (F-26/43/72) ephemeral within-turn failed-approach scratch steers
  the model off a blind re-issue of an EXACT (tool, args) approach that already failed this turn
  (containment, never persisted). Owner approved ON-default via AskUserQuestion. F-4 decision-time
  heuristic consult left DELIBERATELY dead (weak-model amplification safety; needs its own design).
  Closes: F-26, F-43, F-46, F-47, F-48, F-50, F-51, F-54, F-72.

- [ ] **ADR-6 — Closed detect→heal→verify lifecycle** (`feat/adr-6-closed-loop-lifecycle`).
  Boot/health/supervisor/restart drive recovery toward a goal and re-verify, not just observe.
  (Heal step depends on ADR-1/ADR-2.)
  Closes: F-36, F-39, F-73, F-74, F-85, F-87, F-88.

- [ ] **ADR-7 — DecisionLedger** (`feat/adr-7-decision-ledger`).
  One ledger every authority (routing/recovery/acceptance/heuristic) emits a typed verdict to;
  decisions become reconstructable. (Largely downstream of ADR-1..6.)
  Closes: F-9, F-19, F-28. (⤷F-10, F-39, F-47, F-72)

## Completion
Emit `<promise>ALL_ADRS_SHIPPED</promise>` only when all 7 are `[x]`, each invariant test is
green on `main`, and `main` is pushed.
