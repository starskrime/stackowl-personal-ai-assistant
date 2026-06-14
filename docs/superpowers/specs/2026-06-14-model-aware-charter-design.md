# Design Spec — Model-Aware Lean Charter + DNA (Slice 3)

**Date:** 2026-06-14 · **Branch:** `feat/model-aware-charter` off `main` · **Theme:** reliability spine — pillar ③/persona. The final slice of the weak-model reliability arc (S1 per-model context budget shipped @1cf5e2d, S2 answer-quality judge shipped @5b1f17f; plus intent-classification hardening @08406f4).
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` green + existing `tests/owls/` (charter/dna) + `tests/pipeline/steps/` (assemble/execute) tests green.

## Origin

A weak local model (9B) is handed the same charter + DNA persona as a 200k-window cloud model. Two harms: (1) the ~355-token charter+DNA add load a small-window model can ill afford; (2) some DNA directives BACKFIRE on weak models — notably `precision` → *"cite sources, file paths, line numbers… whenever possible"* (`dna_injector.py:35`), which makes a weak model FABRICATE citations to sound precise; the high-trait behavioral directives (challenge/curiosity/creativity) push behaviors a weak model follows poorly. The user chose a per-model-aware fix (charter + DNA adapt to the routed model's window), accepting the tension with the global-prompt rule.

## Why it needs new wiring

`pipeline/steps/assemble.py` builds the system prompt (charter + persona/DNA + skills + memory) but runs BEFORE `pipeline/steps/execute.py`, where the provider is selected (`_select_tool_provider`) and the window resolved (`resolve_window`, Slice 1). So assemble has no model/window signal today. To be model-aware, assemble must resolve the turn's window itself, reusing the SAME selection so it never diverges from the model execute actually calls.

## Goal

Small-window/weak models get a leaner charter and a DNA persona with the backfiring directives suppressed; capable models are byte-identical to today. The lean/full decision is driven by the resolved model window, shared with execute's budget so the two never disagree.

### Decisions (locked in brainstorming)
- **Per-model-aware** (user choice, over the global-lean alternative): branch the charter + DNA on the resolved window.
- **Shared provider-selection:** extract `_select_tool_provider` to a shared module so assemble + execute use ONE selection path (no divergence, no assemble→execute coupling); add `log_selection: bool` so assemble's resolution is quiet.
- **`state.model_window`** carries the resolved window from assemble; execute reuses it (memoized resolve_window keeps them equal regardless).
- **Threshold `LEAN_WINDOW_THRESHOLD = 8192`:** window ≤ 8192 → lean (the fallback/unknown + small local models); ≥ 16384 (capable / cloud clamped) → full.
- **Fail-safe to FULL:** any error resolving the window / selecting the provider → `lean=False` (the full, current prompt). A model-aware miss degrades to the richer prompt, never a crash.
- **Lean charter:** a tightened `behavioral_charter_lean()` (~40% shorter) keeping the load-bearing principles (own-it / act-and-verify / persist / deliver-don't-hand-back / no-AI-excuses / communicate-clearly), compressing the verbose memory + act-over-assert prose. The `operational_adapter` (ACTION protocol) is UNCHANGED (load-bearing, parser-locked).
- **Lean DNA:** suppress the backfiring directives for weak models — the `precision` citation demand and the high-token behavioral directives (challenge/curiosity/creativity). Strong models keep the full directive set.

### Non-goals
- Changing strong-model behavior (must be byte-identical).
- Turn-language i18n (separate slice).
- Reworking the DNA trait model, the directive latch, or skill injection.
- No DB/migration.

## Architecture

### A. Shared provider selection — `pipeline/provider_select.py` (new)
Move `_select_tool_provider(registry, services, state)` from `execute.py` into `provider_select.py` as `select_tool_provider(registry, services, state, *, log_selection: bool = True)` (the INFO "tool provider selected" log fires only when `log_selection`). `execute.py` imports it (call unchanged → `log_selection=True`). Pure move + one new gated-log param; no behavior change for execute.

### B. `PipelineState.model_window: int | None = None` (new field)
Carries the window assemble resolved. Default None → unaffected paths unchanged.

### C. `assemble.run` — resolve window, pick lean
After the manifest is resolved and before building the base prompt:
- `provider = select_tool_provider(registry, services, state, log_selection=False)` (quiet); `window = await resolve_window(provider_name=provider.name, base_url=provider._config.base_url, model=provider._config.default_model, context_chars=provider._config.context_chars, protocol=provider.protocol)`; `state = state.evolve(model_window=window)`; `lean = window <= LEAN_WINDOW_THRESHOLD`.
- Wrap in try/except → on ANY failure `lean = False`, `model_window` left None (fail-safe to full; logged, never crash).
- Pass `lean` to `build_base_prompt(now, lean=lean)` and `_injector.inject(manifest, manifest.dna, lean=lean)`.

### D. `base_prompt` — lean charter
- Add `behavioral_charter_lean() -> str` (the tightened charter). `build_base_prompt(now, *, lean: bool = False)` selects `behavioral_charter_lean()` when lean, else `behavioral_charter()` (unchanged). `operational_adapter` unchanged. `LEAN_WINDOW_THRESHOLD` constant lives in `base_prompt.py` (or a shared constants spot) and is imported by assemble.

### E. `dna_injector` — lean directive suppression
`inject(self, manifest, dna, *, lean: bool = False)`. When `lean`, drop the backfiring/high-token directives: define `_LEAN_SUPPRESSED_TRAITS = {"precision", "challenge_level", "curiosity", "creativity"}` and skip those in the HIGH loop when lean (keep `formality` HIGH/LOW + `verbosity` LOW — register/length directives are cheap + safe). When not lean, behavior is byte-identical to today.

### F. `execute` — reuse the shared select + window
`execute._run_with_tools` calls `select_tool_provider(...)` (the moved function) and, for the budget, uses `state.model_window` if set (assemble resolved it) else `resolve_window` (memoized → identical). No behavioral change beyond the import move.

## Invariants
1. **Strong models byte-identical:** `lean=False` → `behavioral_charter()` + full DNA directives, exactly as today (window ≥ 16384 or any unknown/error path).
2. **Lean only below threshold:** `lean=True` ⟺ resolved window ≤ 8192.
3. **Fail-safe to full:** any window-resolution / provider-selection error in assemble → `lean=False` (richer prompt), never a crash (assemble already wraps persona/skill/base building in try/except — extend the same discipline).
4. **No divergence:** assemble's lean decision and execute's budget use the SAME window (shared selection + memoized resolve_window).
5. **Lean drops only the backfiring/heavy directives:** the citation demand + challenge/curiosity/creativity; cheap register/length directives (formality, verbosity) remain.
6. No silent excepts; 4-point logging; language-agnostic (no keyword lists); operational ACTION protocol unchanged.

## Functional requirements (Given/When/Then)
- **FR1 (lean charter):** *Given* a resolved window ≤ 8192, *when* assemble builds the prompt, *then* the lean charter is used (assert it's shorter AND retains the load-bearing principles).
- **FR2 (lean DNA):** *Given* lean + an owl with high `precision`, *when* DNA is injected, *then* the "cite … line numbers" directive is ABSENT; *given* full (window ≥ 16384), *then* it's present (unchanged).
- **FR3 (strong byte-identical):** *Given* a window ≥ 16384, *when* assemble builds, *then* charter + DNA are byte-identical to the pre-change output.
- **FR4 (fail-safe):** *Given* provider-selection / window-resolution raises in assemble, *when* it builds, *then* `lean=False` (full prompt) and the turn proceeds (no crash).
- **FR5 (shared selection):** `_select_tool_provider` moved to `provider_select.py`; execute uses it with `log_selection=True`; assemble with `log_selection=False`; execute's provider choice unchanged.
- **FR6 (state carries window):** assemble stamps `state.model_window`; execute's budget uses it (or the memoized equal).
- **FR7 (zero regression):** full `tests/journeys/` + owls/charter/dna + assemble/execute tests green.

## Testing (unit for builders/parsing; gateway for the end-to-end branch)
- **base_prompt units:** `behavioral_charter_lean()` is non-empty, shorter than `behavioral_charter()`, retains key principles (own-it / persist / deliver-don't-hand-back / no-AI-excuses); `build_base_prompt(now, lean=True)` uses it; `lean=False` byte-identical to today.
- **dna_injector units:** lean=True + high precision → citation directive absent; lean=True drops challenge/curiosity/creativity; formality/verbosity still apply; lean=False byte-identical (the existing dna tests must stay green).
- **provider_select unit:** `select_tool_provider(..., log_selection=False)` returns the same provider as before and emits no "tool provider selected" INFO (capture via caplog); `log_selection=True` emits it.
- **assemble unit:** with a mock provider whose window resolves ≤ 8192 → `state.model_window` set + lean charter/DNA used; window ≥ 16384 → full; provider-selection raises → lean=False + no crash (FR4).
- **gateway journey:** a small-window owl turn → the assembled system prompt is the lean charter + no citation directive; a large-window owl turn → full charter + directives. Assert via the assembled `state.system_prompt` content.
- Full `tests/journeys/` regression (FR7).

## House rules
Strict mypy; 4-point logging; no silent excepts (assemble fail-safe logged); charter/DNA stay GLOBAL within each tier (no per-example tuning); named constant `LEAN_WINDOW_THRESHOLD`; reuse `resolve_window`/`select_tool_provider`; no DB/migration; no vendor names.

## Rollback
Revert: the `provider_select.py` extraction (move back into execute), the `state.model_window` field, the assemble window-resolution block, the `lean` params on `build_base_prompt`/`inject` + the lean charter/`_LEAN_SUPPRESSED_TRAITS`. Strong-model path is byte-identical so a revert is low-risk.

## Composition note
Completes the weak-model arc (budget + judge + classification + now persona). Reuses Slice 1's `resolve_window` (the shared selection makes the window a first-class turn fact on `state`). Builds on [[project_reliability_spine_backlog]]. Tension with [[feedback_global_highlevel_prompt]] acknowledged + accepted by the user (per-model tiering, not per-example tuning; each tier's prompt stays global within the tier).

## Verification constraint
Unit + gateway tests gate this. Whether the lean charter/DNA measurably improves a real 9B's answers is LIVE verification — deferred until the model box is reachable or a local model is pulled.
