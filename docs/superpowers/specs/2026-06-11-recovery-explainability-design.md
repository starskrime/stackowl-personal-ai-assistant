# Design Spec — Recovery Explainability (Substitution)

**Date:** 2026-06-11 · **Branch:** new slice off `feat/agentic-os-stage1` · **Theme:** reliability spine, pillar ④ explainability (the "what failed / how I recovered" half).
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` suite green (currently 85 passed / 1 skipped after the lessons slice).
**Builds on:** the just-shipped lessons slice (`[[project_ranked_applied_lessons]]`) — reuses the turn-scoped-carrier + pre-delivery-render pattern, and the ordering fix (render BEFORE `surface_critical_failure`).

## Problem

When self-healing recovers a turn, the user is rarely told. Capability **substitution** (a tool fails → an in-bounds sibling with the same `capability_tag` produces the result, W3 of the self-heal supervisor) is computed and logged, and a note is prepended to the model's *observation* (`execute.py` `_try_substitute`, localize key `self_heal_substituted`) — but whether the user hears about it depends on the model relaying it. That's not reliable. The theme's headline ("tell the user what failed and how it recovered") is unmet for the cleanest, most concrete recovery event.

## Goal & scope

- **User-facing:** deterministically tell the user when a substitution recovered the turn — *"'{failed}' was unavailable, so I used '{recovered_via}' to complete this."* — independent of whether the model mentions it.
- **Observability ("broad recovery in log"):** one structured per-turn recovery record (queryable via the `read_logs` tool) listing all recorded recovery events, even those not surfaced to the user.
- **Honesty:** recovery is a **machinery fact** — recorded by the code that performed the recovery, never narrated by the model (which could hallucinate it).

### Non-goals (cut / deferred — explicit)
- **Provider-fallback capture** into the unified record — DEFERRED (documented fast-follow). It needs a small `get_by_tier` degrade-signal and already has its own degrade/cascade log lines today. The carrier + render are built to accept it with one capture tap later; no rework needed.
- Retries / nudges / loop-stops — internal coaxing; already self-log; not folded in (noise/overclaim risk).
- No `PipelineState` fields; no DB migration.

## Architecture — carrier + capture + render + log

```
substitution recovers (execute) → record_recovery(user_visible=True) → surface_recovery renders user line  +  backend emits unified [recovery] log
```

### A. `infra/recovery_context.py` (new)
A turn-scoped ContextVar carrier (same idiom as `infra/trace.py` `TraceContext` and `pipeline/lesson_context.py`). **Lives in `infra/`** (the base layer) so any layer — including `providers/`-adjacent code for the deferred provider-fallback tap — can record without a `providers→pipeline` dependency inversion.

- `RecoveryEvent` frozen dataclass: `kind: str` (e.g. `"substitution"`), `failed: str`, `recovered_via: str`, `detail: str`, `user_visible: bool`.
- ContextVar `_events: tuple[RecoveryEvent, ...] | None` — `None` == NOT bound this turn (record is then a no-op).
- `bind() -> token` (installs `()`); `reset(token)`.
- `record_recovery(kind, failed, recovered_via, *, detail="", user_visible) -> None` — appends; no-op + debug log when unbound (never raises).
- `get_recovery() -> tuple[RecoveryEvent, ...]` — **non-consuming peek** (two readers: the render step and the log emit). No drain/consume; `reset()` clears at turn end.

### B. Capture site — substitution (`pipeline/steps/execute.py` `_try_substitute`)
On sibling success (right where the localized `self_heal_substituted` observation note is built, ~line 346–384), add one call:
`recovery_context.record_recovery(kind="substitution", failed=failed_tool, recovered_via=sibling_name, user_visible=True)`.
Additive — the existing model-facing observation note is unchanged.

### C. `pipeline/recovery_summary.py` (new) — `surface_recovery(state) -> state`
Pre-delivery render step. Appends a localized line per **`user_visible`** event (new localize key `self_heal_recovery_note`, en/de/fr/es; slots `{failed}`, `{recovered_via}`), capped at 2. Guards (identical to `surface_applied_lessons`): only when `get_recovery()` has a user-visible event AND there is a real (non-floor, non-empty) answer to annotate. Self-healing (B5 catch logs, never raises). Reads via `get_recovery()` (does not consume).

### D. Backend wiring (`asyncio_backend.py`, `langgraph_backend.py`)
- `recovery_context.bind()` at turn start, `reset()` in the SAME `finally` as `lesson_context` (one added line each).
- **Order at the chokepoint:** `surface_applied_lessons` → `surface_recovery` → `surface_critical_failure` → deliver. Both annotation steps run BEFORE critical-failure surfacing (the honesty-ordering fix from the lessons slice: a failed turn has no real answer yet, so annotations correctly suppress).
- **Unified recovery log:** in the `finally`, BEFORE reset, if `get_recovery()` is non-empty emit one structured record `log.engine.info("[recovery] turn summary", extra={"_fields": {"trace_id", "events": [{kind, failed, recovered_via, user_visible}, ...]}})`. This is the "broad recovery in log" — one queryable per-turn record.

## Honesty invariants
1. No recorded recovery → no user line, empty log record skipped.
2. User line only for `user_visible=True` events (currently: substitution).
3. Recovery is machinery-recorded — the model never authors it.
4. User line only on a real non-floor answer turn (a fully-failed turn is the floor's job).
5. No silent excepts; render + record never raise.

## Functional requirements (Given/When/Then — customer-visible)
- **FR1 (substitution explained):** *Given* a tool fails and an in-bounds sibling recovers the turn, *when* the turn completes with an answer, *then* the user response includes the localized "'{failed}' was unavailable, so I used '{recovered_via}'…" line.
- **FR2 (no recovery → silence):** *Given* no substitution occurred, *when* the turn completes, *then* no recovery line appears.
- **FR3 (failed turn → no annotation):** *Given* a substitution was recorded but the turn produced only a floor/critical-failure message, *when* delivery runs, *then* no recovery line is appended (the floor explains the failure instead).
- **FR4 (broad log):** *Given* any recovery event was recorded, *when* the turn ends, *then* a single structured `[recovery] turn summary` log record lists all events (including non-user-visible ones).
- **FR5 (machinery, not model):** the line is built from the recorded `failed`/`recovered_via`, not from model output.
- **FR6 (zero regression):** full `tests/journeys/` stays green.

## Testing (gateway-driven, provider-mock-only)
New journey `tests/journeys/test_recovery_explainability_journey.py`:
- **Happy (FR1/FR5):** scripted provider calls a tagged tool that FAILS; an in-bounds sibling (same `capability_tag`) succeeds (reuse the `_CapabilityTool` pattern from `test_self_heal_substitution.py`) → assert the user-visible response contains the recovery line with the real failed/sibling names.
- **Negative (FR2):** no failure → no recovery line.
- **Negative (FR3):** substitution recorded but turn floors → no recovery line.
Unit: `tests/infra/test_recovery_context.py` (record/peek/unbound-noop/reset); `tests/pipeline/test_recovery_summary_render.py` (user_visible filter, real-answer guard, cap, floor-skip). FR4 asserted via a render/backend test capturing the structured log (or asserting `get_recovery()` contents).

## House rules
- Strict mypy; 4-point logging in the capture + render; no silent excepts (B5 catches log).
- i18n via `localize`; no hardcoded English in logic.
- Runtime state stays in-memory ContextVar; no repo/DB writes; no migration.
- Reuse: mirrors `lesson_context`/`surface_applied_lessons`/backend-wiring already shipped; carrier in `infra/` for layer-neutrality.

## Rollback
Pure-additive: remove the capture call, the two backend lines (bind/reset + log + render call), the new carrier + render modules, the localize key. No data to reverse.
