# Research Plan

Root-cause & architecture study of the 88 audit findings (`.ralph/FINDINGS.md`).
One theme per ADR. `[ ]` = not yet designed, `[x]` = root cause + ADR written.
Ordered by leverage (findings explained × depth), deepest first.
Dependency note: T1 is upstream of T2/T5/T6/T7 (they all need a trustworthy
"did it actually work?" signal). Build order follows this list.

## Theme map (each finding maps to exactly one primary theme; ⤷ = cross-cutting)

- [ ] **T1 — Asserted-not-measured success → one AcceptanceAuthority** (ADR-1)
  Hypothesis: "success" is reported by the actor, never observed against the intended effect;
  and "did it work / deliver / overclaim?" is split across ≥6 disjoint proxies. (Merges H1+H2.)
  Generates: F-1, F-11, F-12, F-13, F-14, F-15, F-20, F-23, F-25, F-29, F-30, F-31, F-32, F-33,
  F-34, F-75, F-80, F-81, F-82, F-83. ⤷F-10.

- [ ] **T2 — Scattered give-up → one RecoveryActuator ladder** (ADR-2)
  Hypothesis: retry/fallback/substitution/replan/re-arm exist as ~12 point-solutions with gaps;
  no single authority turns a "not-trustworthy result" into a bounded recovery ladder.
  Generates: F-5, F-6, F-7, F-8, F-16, F-17, F-18, F-21, F-24, F-35, F-37, F-40, F-41, F-55,
  F-60, F-62, F-64, F-65, F-66, F-67. (Depends on T1.)

- [ ] **T3 — Ask-first reflex → one ReversibilityResolver** (ADR-3)
  Hypothesis: trivial, reversible decisions bounce to the user because there is no single
  reversibility/stakes signal + default-resolution authority; "act-first" lives only in prompt text.
  Generates: F-3, F-27, F-44, F-56, F-68, F-69, F-70, F-71.

- [ ] **T4 — Registered ≠ reachable → a Reachability invariant at boot** (ADR-4)
  Hypothesis: capabilities are wired as dangling half-edges that ship green because reachability
  is never asserted; the census exists but isn't run.
  Generates: F-45, F-76, F-77, F-78, F-86. ⤷F-87.

- [ ] **T5 — Learning on an untrustworthy signal (positive-only) → verified-gated learning** (ADR-5)
  Hypothesis: the learning loop mines a self-asserted success signal and (per the hard directive)
  may not store negatives; once T1 makes success *measured*, mining/recall become trustworthy and
  within-turn failure-avoidance becomes possible without persisting negatives.
  Generates: F-26, F-43, F-46, F-47, F-48, F-50, F-51, F-54, F-72. (Depends on T1.)

- [ ] **T6 — Detect-only lifecycle → closed detect→heal→verify loop** (ADR-6)
  Hypothesis: boot/health/supervisor/restart observe and report but never drive recovery toward a
  goal; no closed loop acts on a degraded signal and re-verifies.
  Generates: F-36, F-39, F-73, F-74, F-85, F-87, F-88. (Heal step depends on T1/T2.)

- [ ] **T7 — No decision trace → one DecisionLedger the authorities emit to** (ADR-7)
  Hypothesis: routing/recovery/acceptance/heuristic decisions aren't reconstructable because each
  decider logs ad-hoc or not at all; once T1–T6 introduce authorities, each emits a typed verdict
  to one ledger. (Largely downstream of T1–T6.)
  Generates: F-9, F-19, F-28. ⤷F-10, F-39, F-47, F-72.

## Status
Theme map built (iteration 1). 0/7 ADRs written. Next: T1 (AcceptanceAuthority) — the keystone.
