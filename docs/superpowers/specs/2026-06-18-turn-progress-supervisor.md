# Spec: Turn Progress Supervisor + Overclaim Delivery-Gate + Capability-Honest Degradation

**Date:** 2026-06-18
**Branch:** `feat/turn-progress-supervisor`
**Origin:** BMAD party (Winston/Dr. Quinn/Amelia/Murat) on the "pile of point-detectors with gaps" problem, following the P2 same-tool circuit breaker ([[project_pictures_overclaim_incident]]).
**Status:** Design grounded in code (seam map below). Approved scope: all three phases.

---

## 1. Problem (root cause, per the party)

StackOwl's ReAct loop has a *pile of overlapping "model is stuck" point-detectors*, each catching ONE failure shape, with **provable gaps in the unions between them**:

| Detector | Catches | Seam |
|---|---|---|
| LoopGuard (`_react.py:49-92`) | identical `(name,args)` repeats, ANY outcome (warn@3/break@4) | provider, pre-dispatch |
| Circuit breaker (P2, `execute.py`) | same-tool consecutive GENUINE failures (committed=True), threshold 3 | `_dispatch` |
| BudgetGovernor (`budget/governor.py`) | wall-clock / steps / cost — the LATE backstop (120s/20-step) | `on_iteration_complete` |
| Capability substitution (`_try_substitute`) | one-shot reroute around a failed capability-tagged tool | `_dispatch` |

**Proven gaps** (the "remaining things"):
- **G1 timeout-spiral:** a tool that consistently TIMES OUT returns on the early `except TimeoutError` path (`execute.py:908-925`) *before* the circuit-breaker streak update → never advances the streak → only the late budget wall catches it.
- **G2 refusal-spiral:** a tool that always fails with `side_effect_committed=False` (a no-op/validation refusal) never increments the breaker (correctly excluded from the floor's effectful tally) AND never floors → uncontained until the budget wall, and can ship nothing honestly.

**Dr. Quinn's reframe (the root):** these are not distinct bugs. They are the same event — *an iteration that produced no forward progress* (in info-gain terms: an observation informationally equivalent to a prior one). A timeout, a refusal, a repeated error are all **zero-information observations**. The detectors are point-patches over a loop with no internal progress criterion. The fix is one **progress model**, not more detectors. (We operationalize "no progress" as an explicit per-dispatch classification — deterministic — rather than fuzzy string-similarity, to avoid the false-positive risk Murat flagged. Info-gain-by-similarity is a noted future enhancement, not v1.)

## 2. Architecture (the unification)

A single per-turn **`TurnProgressTracker`**, created in `_run_with_tools` and closed over by `_dispatch` (the only per-call seam). It **subsumes the circuit breaker's `fail_streak`/`circuit_open`** and adds the missing shapes.

**Two counters, NEVER conflated (Amelia's load-bearing landmine):**
- `tool_outcome_ledger` (unchanged): `side_effect_committed` semantics drive the P0 **honest floor**. A `committed=False` failure is excluded — *this must stay clean*.
- `TurnProgressTracker.no_progress_streak` (new): increments on ANY zero-progress dispatch regardless of side-effect semantics. Drives the **circuit breaker (containment)**. The floor never reads it; the tracker never writes the ledger.

**Per-dispatch classification** (at each of the 5 `_dispatch` outcome sites, `execute.py`):
- normal success (`tr.success`) → **PROGRESS** → `no_progress_streak[name] = 0`
- normal genuine failure (`not tr.success`) → **NO_PROGRESS** → increment
- **timeout** (`except TimeoutError`) → **NO_PROGRESS** → increment  *(closes G1)*
- **no-op / validation refusal** failure (committed=False, e.g. missing-param) → **NO_PROGRESS** → increment  *(closes G2)*
- pre-execution CONTAINMENT bounces (`denied_this_run`, `circuit_open`, depth, bounds) → **NOT COUNTED** (they are our own refusals already handled by their own mechanisms; counting them would double-dip)

At `no_progress_streak[name] >= threshold` → `circuit_open.add(name)` → bounced at the top of `_dispatch` for the rest of the turn (the existing P2 bounce mechanism, honesty-safe: records nothing, no `TOOL_FAILED_MARKER`).

**State stamp (for the floor to read):** at the end of `_run_with_tools`, stamp a turn-progress summary onto `state` (mirrors `budget_capped`/`consequential_snapshot_taken`): `state.turn_made_progress: bool` (did any dispatch classify PROGRESS?) and `state.no_progress_tools: tuple[str,...]` (tools whose circuit opened). The per-iteration callback CANNOT see this (its `tool_call_records` lack `side_effect_committed`/severity), so the tracker — not the callback — is the source of truth.

**Threshold:** `NO_PROGRESS_THRESHOLD` — a named constant, default 3 (preserves P2 behavior). In Phase 3 it scales with `model_window`. Never pinned to the box.

**LoopGuard stays where it is** (provider pre-filter, structurally different: identical-args loop with *real* side effects). Do not relocate (1-week change touching provider internals; Amelia + Winston agree).

## 3. Honesty composition (one path)

The honest floor and overclaim monitor **consume** the tracker; they do not run parallel to it.

**Phase 1 — no-progress floor trigger.** Today `surface_consequential_giveup_floor` fires on `cons_failures>=1 AND cons_successes==0` (consequential path) — a pure-G2 turn (cons_failures=0) escapes it. Add a SECOND, independent trigger: a turn that **made no progress** (`turn_made_progress is False`) AND delivered nothing real (`delivered_successes==0`) AND is not already a floor → ships an honest floor naming the stuck capability (`no_progress_tools`). Must NOT regress the consequential floor and must NOT false-fire on a progressing turn or a clean conversational (0-tool) turn.

**Phase 2 — overclaim delivery-gate.** A surfacing step in the post-execute band (both backends, between `surface_consequential_giveup_floor` and `deliver.run` — the confirmed slot, `asyncio_backend.py:108-124` / `langgraph_backend.py`). STRUCTURAL detection (no fragile text analysis): if the assembled response is a confident non-floor message (`is_floor=False`, non-empty) AND `delivered_successes==0` AND tools were ATTEMPTED and failed/bounced/timed-out (effectful failures OR `no_progress_tools` non-empty) → **block delivery**, replace with the honest floor. A 0-tool conversational/clarify turn is NEVER gated. Proven **failing-first** (watch the overclaim ship without the gate). Emits structured `overclaim.detected` / `overclaim.cleared` log events on every delivery (no event bus exists — `outcome_store` + structured logs are the signal) and records an `overclaim` outcome field so a dead gate is visible (the dead-circuit-breaker lesson).

**Phase 3 — capability-honest degradation.** Wire `state.model_window` / `LEAN_WINDOW_THRESHOLD=8192` (`owls/base_prompt.py:31`) to:
- scale `NO_PROGRESS_THRESHOLD` down on a lean window (weak model → contain faster: lean→2, normal→3), capability-probed not box-pinned;
- a prevention knob (Dr. Quinn's "don't start the loop"): on a lean window, the router/execute is readier to **clarify or honestly decline up front** rather than enter an open-ended loop it likely can't drive. Narrowest seam: a window-aware check before `_run_with_tools` and/or a bias in the clarify verdict. Must stay model/infra-agnostic (a strong host is byte-identical to today).

## 4. Test strategy (Murat — completeness, not enumeration)

- **Falsification twin (the merge gate):** `SLOW_DIVERSE_SUCCESS` — 6 different tools, each succeeds, ~15s latency each → NO floor, NO bounce, delivery contains results. Twin: same-tool/same-args genuine-failure ×3 → breaker MUST fire. Plus one genuinely-long (single 90s) successful call → no intervention. These prove the supervisor discriminates slow-but-progressing from stuck.
- **G1 red→green journey:** `[A,A,A,A]` each times out → TODAY no bounce; AFTER, 3rd timeout bounces A, honest floor names the failed capability (mechanical bounce AND honest message both asserted).
- **G2 red→green journey:** `[write,write,write,write]` each no-op refusal (committed=False) → TODAY budget wall + non-honest outcome; AFTER, N refusals → bounce + honest "I attempted but the tool declined" (mechanical + message).
- **Completeness = progress-liveness invariant (property test):** over RANDOM outcome sequences (success/fail/timeout/refusal/different-tool), assert: any sequence where one tool appears ≥K times at zero progress fires intervention before K+1; any progressing sequence never fires. This replaces shape-enumeration (answers "is there a G3?").
- **Overclaim gate:** failing-first — script genuine failure + a "successfully completed!" response → assert WITHOUT the gate the overclaim ships (red), WITH it the honest floor ships and names the failed tool (green). Assert `overclaim.detected`/`cleared` events appear (dead-gate detection).
- All gateway journeys mock ONLY the provider's scripted tool-call sequence and drive the REAL `_run_with_tools` + the REAL post-execute floor/gate band (the merge-gate-journeys-drive-the-real-path rule).

## 5. Grounded seam map (from the Explore pass)

- **`_dispatch` outcome sites** (`execute.py`): denied (691), circuit_open bounce (706), depth (717), bounds (749), stop pre-check (847, records committed=False), missing-param (880, records committed=False), timeout (908-925, records committed=True default, early return — G1), normal record + breaker update (926-962).
- **Per-iteration callback:** `on_iteration_complete(ReActIterationState)` returns `list[messages]|None` (fold) or raises to stop; fires on tool-use iterations (after results, `anthropic_provider.py:389`) AND the final-answer branch (323). `tool_call_records` entries = `{id,name,args,result,failed}` — NO `side_effect_committed`/severity. → tracker must be the source of truth, stamped to state.
- **Post-execute order (both backends):** applied_lessons → recovery → **consequential_giveup_floor** → critical_failure → persist_turn → **deliver.run** → `_capture_outcome` (task_outcomes, AFTER deliver). Overclaim gate slot = between floor and deliver.
- **`deliver.run`:** writes `state.responses` to stream; `is_final=True` only on the `writer.close()` sentinel; content chunks are `is_final=False` by design.
- **task_outcomes:** `TaskOutcomeStore.record(...)` (`outcome_store.py:84`); no overclaim field yet; NO event bus exists (removed F034/F038/F049) → use structured logs + an added outcome field.
- **Lean knob:** `LEAN_WINDOW_THRESHOLD=8192` (`owls/base_prompt.py:31`); `state.model_window` stamped in `assemble.py:157`; `TOOL_FREE_CLASSES={conversational,clarify}` (`state.py:16`); clarify handled at `execute.py:1656`.

## 6. Honesty invariants (must not regress)

1. The tracker's `no_progress_streak` is INDEPENDENT of `tool_outcome_ledger` — the floor's `side_effect_committed`/`is_effectful_failure` semantics stay byte-clean. (Two counters, named `effectful` vs `no_progress`.)
2. The circuit-open bounce remains a pre-execution refusal: records nothing, no `TOOL_FAILED_MARKER` → cannot inflate the consequential tally (P2 invariant preserved).
3. No new overclaim path: a no-progress turn that delivers nothing real FLOORS; it never ships a dressed-up partial. The overclaim gate only ever REPLACES a response with the honest floor — it never manufactures a claim.
4. A progressing turn (any PROGRESS dispatch, or a clean 0-tool conversational turn) is byte-identical to today — no false floor, no false bounce, no false overclaim-block.
5. Capability-honest degradation is host-agnostic: a strong window is byte-identical to today; only a lean window changes behavior, and only toward MORE honesty (faster containment / readier clarify), never toward a silent failure.

## 7. Open decisions for the planner
1. No-progress floor: extend `surface_consequential_giveup_floor` with the second trigger, OR add a sibling `surface_no_progress_floor` step. Recommend EXTEND (one floor function = one honesty path; reuses the chunk-building + is_floor machinery).
2. Overclaim gate: standalone surfacing step vs folded into the floor. Recommend a STANDALONE step (`surface_overclaim_gate`) placed after the floor in both backends — distinct detection (structural overclaim) from the floor's give-up logic, and its failing-first test needs an independent seam.
3. Phase 3 threshold scaling: a function `resolve_no_progress_threshold(model_window) -> int` (lean→2, else→3) vs a config field. Recommend the function (capability-probed, no config churn), with the constant as the strong-window default.
4. Phase 3 prevention knob: where to attach the "lean → readier to decline/clarify" bias — confirm during planning whether it belongs in the router prompt, triage, or a pre-loop execute check (narrowest).
