# Design Spec — Circuit-Aware Answer Routing + Provider-Fallback Recovery

**Date:** 2026-06-11 · **Branch:** new slice off `feat/agentic-os-stage1` · **Theme:** reliability spine — pillar ② self-healing (the answer survives a provider outage) + pillar ④ explainability (tell the user a backup answered).
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` suite green (currently 88 passed / 1 skipped).
**Builds on:** the recovery-explainability slice (`[[project_ranked_applied_lessons]]` slice 2) — reuses `infra/recovery_context.py`, `surface_recovery`, the `[recovery] turn summary` log, all unchanged.

## Problem

The user's main answer provider is resolved by `_select_tool_provider` → `registry.get_by_tier()` (`execute.py:1168`), which is **NOT circuit-aware** (its docstring says "Use `get_with_cascade()` for circuit-aware traversal"). If the tier's provider has an OPEN circuit, the answer still routes to it and the call fails — instead of falling back to a healthy provider. Circuit-aware cascade exists (`get_with_cascade`) but is only used by the judge path. So a provider outage degrades the user's answer even when a healthy backup is available. This is a self-healing gap on the single most important call.

## Goal & scope

- **Fix (pillar ②):** tier-routed answer selection becomes circuit-aware — an OPEN-circuit tier provider falls back to a healthy provider so the answer survives.
- **Surface (pillar ④):** when that fallback happens, tell the user a backup completed it (generic phrasing, no internal provider names), reusing the recovery infra.
- **Preserve:** happy-path routing is byte-identical when circuits are healthy (the fallback only triggers on OPEN).

### Decisions (locked in brainstorming)
- **Tier routing only.** Explicit pins (owl-named binding `_select_tool_provider` Step 0; manifest `provider_name` Step 2) are HONORED even if their circuit is open — a pin is intentional ("use THIS provider"), so it's never silently swapped; it fails honestly into the floor.
- **User-visible**, but **generic** — *"The usual model was unavailable, so a backup completed this."* — NO provider/tier names leaked to the user (names go to the log only).
- **All-open → floor at selection** (raise `AllProvidersUnavailableError`, caught in `execute.run`), cleaner than routing to a dead provider.

### Non-goals
- No change to `get_by_tier` itself (other callers untouched) or to `get_with_cascade`.
- No circuit-awareness for the config-degrade case (no provider serves the tier) — that's a persistent misconfiguration, not a per-turn recovery.
- No change to pin resolution.
- No `PipelineState` fields, no migration.

## Architecture

### A. `registry.resolve_tier_with_fallback(tier: str) -> tuple[ModelProvider, str | None]` (new method)
Preserves `get_by_tier`'s selection; adds circuit fallback only when the chosen provider is OPEN.
1. Find the first provider whose tier == `tier` (config order) = `primary`.
2. **No tier match** → existing config-degrade (first registered provider), return `(that, None)` (unchanged — misconfig path, no circuit logic).
3. `primary`'s breaker is `None` or not `OPEN` → return `(primary, None)` — **identical to `get_by_tier` when healthy** (zero routing change).
4. `primary`'s breaker is `OPEN` → `healthy = self.get_with_cascade(tier)` (the existing, tested circuit-aware primitive: skips OPEN, walks `fast→standard→powerful→local` from `tier`, prefers a healthy same-tier sibling first, raises `AllProvidersUnavailableError` if all open); return `(healthy, primary_name)`. (Cascade is only ever invoked here — when `primary` is already known OPEN — so happy-path selection is never routed through it.)

This composes two existing primitives: `get_by_tier`'s choice for the healthy happy path (step 3, byte-identical to today), and `get_with_cascade` for the fallback walk (step 4). The method itself only adds the breaker check on `primary` + the `degraded_from` name plumbing. 4-point logging; reads `self._providers`/`self._tiers`/`self._breakers` with the same atomic-snapshot discipline as the existing methods.

### B. `_select_tool_provider` Step 4 (tier path)
Replace `provider = registry.get_by_tier(desired)` with:
```python
provider, degraded_from = registry.resolve_tier_with_fallback(desired)
if degraded_from is not None:
    recovery_context.record_recovery(
        kind="provider_fallback", failed=degraded_from,
        recovered_via=provider.name, user_visible=True,
    )
```
Steps 0 (owl-named) and 2 (manifest pin) are UNCHANGED — pins honored.

### C. `execute.run` — handle all-open at selection
Wrap the `provider = _select_tool_provider(registry, services, state)` call (`execute.py:1187`) in `try/except AllProvidersUnavailableError`: on raise, log + return `state.evolve(errors=(*state.errors, "execute: AllProvidersUnavailableError: ..."))` so the existing critical-failure/floor path produces an honest "couldn't complete" message (NOT a recovery claim). Import `AllProvidersUnavailableError` from the providers package.

### D. `surface_recovery` — per-kind template (no-name-leak)
Select the localize template by `event.kind`:
- `"substitution"` → existing `self_heal_recovery_note` with `failed`/`recovered_via` slots (tool names — acceptable).
- `"provider_fallback"` → **new** `self_heal_recovery_provider`, a generic fixed string with NO slots: *"ℹ️ The usual model was unavailable, so a backup completed this."*
- unknown kind → skip (defensive; never emit an un-templated line).
The per-kind mapping is a small dict `{kind: localize_key}`; an event whose kind isn't mapped is not surfaced.

### E. localize — add `self_heal_recovery_provider` (en/de/fr/es)
Generic, no provider/tier names.

### F. Carrier + unified log — UNCHANGED
`record_recovery`/`get_recovery` are kind-agnostic; the `[recovery] turn summary` log already serializes any event (so provider-fallback's real `failed`/`recovered_via` names land in the LOG, just not the user line).

## Honesty/safety invariants
1. Happy-path routing byte-identical when circuits healthy (fallback only on OPEN) — `resolve_tier_with_fallback` step 3.
2. Explicit pins never silently swapped (Steps 0/2 unchanged).
3. User line is generic — no internal provider/tier name leaked.
4. Recovery line only on a real answer; an all-open floored turn gets the floor, never a recovery claim (the `has_real_answer` guard + the floor path).
5. Machinery-recorded (the selection code records it); never model-narrated.
6. No silent excepts; all-open raise is caught and floored, logged.

## Functional requirements (Given/When/Then)
- **FR1 (fix):** *Given* the tier's provider has an OPEN circuit and a healthy provider exists, *when* the answer is generated, *then* it is produced by the healthy provider (not failed).
- **FR2 (happy path unchanged):** *Given* all circuits healthy, *when* the answer provider is resolved, *then* the SAME provider as `get_by_tier(desired)` is chosen and NO recovery is recorded.
- **FR3 (surface, generic):** *Given* a tier fallback occurred and the turn produced an answer, *when* it completes, *then* the user response contains a generic backup-model line and NO provider/tier name.
- **FR4 (pins honored):** *Given* an explicit pin (owl-named or manifest) whose circuit is open, *when* resolving, *then* the pinned provider is used (no fallback, no recovery line).
- **FR5 (all-open floors):** *Given* every provider's circuit is open, *when* resolving, *then* the turn floors with an honest failure message and NO recovery line.
- **FR6 (log has names):** *Given* a tier fallback, *when* the turn ends, *then* the `[recovery] turn summary` log record carries the real `failed`/`recovered_via` provider names.
- **FR7 (zero regression):** full `tests/journeys/` stays green.

## Testing (gateway-driven, provider-mock-only)
- Registry unit (`tests/providers/`): healthy → `(primary, None)` equal to `get_by_tier`; primary OPEN + healthy sibling → `(healthy, primary_name)`; all OPEN → raises `AllProvidersUnavailableError`; no-tier-match → existing degrade `(first, None)`.
- Render unit (extend `tests/pipeline/test_recovery_summary_render.py`): a `provider_fallback` user-visible event on a real answer → generic line appended, assert NO provider name in it; substitution still uses the named template.
- `execute.run` all-open: a registry where every breaker is OPEN → selection raises → state carries an error → floored (no recovery line). (unit or journey)
- Journey (`tests/journeys/`): force a tier provider's breaker OPEN with a healthy backup registered, scripted provider answers via the backup → assert the answer is delivered AND the generic backup line is present AND (caplog) the `[recovery] turn summary` carries the real names. Negative: all healthy → no line (FR2).
- Full `tests/journeys/` regression (FR7).

## House rules
Strict mypy; 4-point logging in the new registry method + the capture; no silent excepts (all-open raise caught + logged); i18n via localize (generic provider line localized en/de/fr/es); no `PipelineState`/DB change.

## Rollback
Pure-additive: revert `_select_tool_provider` Step 4 to `get_by_tier`, drop `resolve_tier_with_fallback`, the per-kind template branch, the `self_heal_recovery_provider` key, and the `execute.run` try/except. No data to reverse.
