---
baseline_commit: c95647e8
---

# Story 2.4: Signal-strength-tiered mutation clamp

Status: done

## Story

As the platform,
I want a DNA mutation's allowed magnitude to scale with the strength of the signal behind it,
so that a verified win can move a trait further than an LLM's opinion of quality alone.

## Acceptance Criteria

1. **Given** a new shared `SignalStrength` enum (`VERIFIED | OUTCOME_BINARY | LLM_QUALITY`) defined once in `dna_governor.py`
   **When** any propose-stage caller computes a delta
   **Then** it tags the delta with the signal that produced it (FR-6)

2. **Given** a tagged delta
   **When** it reaches `bound_dna()`
   **Then** the effective delta passed in is scaled down for `OUTCOME_BINARY` and `LLM_QUALITY` relative to `VERIFIED`, strictly ≤ the raw proposed delta (FR-6, AD-4)

3. **Given** any signal strength
   **When** `bound_dna()`'s existing clamp (rate cap, envelope, judgment floor) applies
   **Then** that clamp is never widened by signal strength — it remains the final, unconditional ceiling (FR-7, AD-4)

## Design decision — which caller gets which tier (not pre-specified by PRD/architecture, decided here)

The PRD/architecture leave the exact tier assignment to story-time (same pattern as Story 2.5's held-out sample size). Based on direct reads of `owls/evolution.py` and `owls/dna_attribution.py`:

- **`DnaAttributor`'s statistical path (`owls/evolution.py::_evolve_one`, attribution branch)** → `SignalStrength.VERIFIED`. Its eligibility filter (`dna_attribution.py::_filter_scored_outcomes`) already requires `o.success` (verification-aware per `tools/verification.py::is_trustworthy_success` — `TaskOutcome.success` is ALREADY the verified-or-unchecked-but-claimed boolean at write time) AND a non-null `quality_score`, over ≥20 samples. This is the highest-confidence signal in the system today.
- **The LLM-fallback path (`owls/evolution_prompt.py` via `_llm_fallback`)** → `SignalStrength.LLM_QUALITY`. It has no stored `TaskOutcome` row backing the delta at all — it's a single LLM completion's raw opinion from a handful of recent conversation excerpts. Lowest-confidence signal, matches FR-6's "an LLM-judged quality score alone gets the least" wording directly (Story 3.1's `evolve_now`, per FR-13, routes here unconditionally — this tiering is what makes that safe).
- **`SignalStrength.OUTCOME_BINARY`** — defined in the shared enum (required by AD-4's "one shared enum... imported by every propose-stage caller") but has NO current producer in this story. No existing code path computes a delta from a bare success/fail signal without either the full attribution-eligibility bar (quality_score + ≥20 samples) or an LLM opinion. It's reserved for a future/simpler caller. Do not invent a synthetic caller for it just to exercise the enum member — an enum with a currently-unused member is not a defect, and manufacturing a fake caller would be scope creep. Test it directly against `bound_dna`'s scaling function instead (see Testing).
- **Multiplier constants** (operator-tunable, no existing precedent to match): `VERIFIED=1.0` (full raw delta, unchanged from today's behavior — NFR-5 backward-compat), `OUTCOME_BINARY=0.6`, `LLM_QUALITY=0.3`. Define as a `dict[SignalStrength, float]` module constant in `dna_governor.py`, named `_SIGNAL_STRENGTH_MULTIPLIER`, right next to `SignalStrength`.

## Tasks / Subtasks

- [x] Task 1: `SignalStrength` enum + effective-delta scaling (AC #1, #2, #3)
  - [x] `owls/dna_governor.py`: add `class SignalStrength(str, Enum): VERIFIED = "verified"; OUTCOME_BINARY = "outcome_binary"; LLM_QUALITY = "llm_quality"` (string enum — matches this repo's convention of JSON/log-serializable enums, check `dna_attribution.py`'s `TraitAttribution`/similar for the exact style already used nearby and mirror it)
  - [x] `_SIGNAL_STRENGTH_MULTIPLIER: dict[SignalStrength, float] = {SignalStrength.VERIFIED: 1.0, SignalStrength.OUTCOME_BINARY: 0.6, SignalStrength.LLM_QUALITY: 0.3}`
  - [x] New function `def scale_by_signal_strength(delta: float, signal: SignalStrength) -> float: return delta * _SIGNAL_STRENGTH_MULTIPLIER[signal]` — pure, no I/O, no logging needed (trivial arithmetic, matches `_scale_deltas` in `evolution.py`'s existing no-logging convention for a one-line pure scale function)
  - [x] `bound_dna()`'s signature changes to accept an OPTIONAL `signal: SignalStrength = SignalStrength.VERIFIED` parameter (default preserves EXACT current behavior for any caller not yet updated — NFR-5 backward compat is non-negotiable here). Inside `bound_dna`, apply `scale_by_signal_strength` to `prop - cur` (the raw per-trait delta) BEFORE the existing `max(-MAX_DELTA, min(MAX_DELTA, ...))` rate-cap line — i.e., insert the scaling as a NEW step strictly upstream of the existing clamp math, per AD-4 ("computes an effective delta strictly ≤ the raw proposed delta, BEFORE that effective delta is passed into bound_dna()'s existing clamp"). Read AD-4 literally: the scaling could alternatively live in the CALLER (evolution.py) rather than inside `bound_dna` itself — see the note below, this is a real design fork, pick one and justify it in Completion Notes.

  **Design fork note**: AD-4's wording ("computes an effective delta ... BEFORE that effective delta is passed into `bound_dna()`'s clamp") reads as the scaling happening OUTSIDE `bound_dna`, in the caller, with `bound_dna` itself staying signal-agnostic. But `bound_dna(current, proposed, anchor)`'s current signature takes two full `OwlDNA` snapshots (not raw deltas) and computes `prop - cur` internally per trait — there's no clean external seam to pre-scale "the delta" before calling `bound_dna`, since `bound_dna` derives deltas from the snapshot pair itself. Two valid implementations:
    (a) Add the optional `signal` param to `bound_dna` itself (scaling happens inside, immediately after computing `prop - cur`, before the existing `MAX_DELTA` clamp) — smallest diff, single choke point, but a caller can't easily see the "effective delta" as a standalone number.
    (b) Keep `bound_dna` untouched; instead have `evolution.py` pre-scale `new_dna` (the trait-by-trait mutated snapshot) toward `current` before calling `bound_dna`, by blending: `scaled_trait_value = current_trait + (mutated_trait - current_trait) * multiplier`. This keeps `bound_dna`'s signature and AD-4's literal "before bound_dna" wording intact but requires a new helper in the CALLER for something bound_dna already computes internally — more moving parts, more places to get the arithmetic wrong.

  Pick **(a)** — smaller diff, avoids duplicating the delta-derivation math that already lives in `bound_dna`, and AD-4's core guarantee ("effective delta strictly ≤ raw proposed delta, ceiling never widened") holds identically either way since `bound_dna`'s existing `MAX_DELTA`/`ENVELOPE`/`TRAIT_FLOOR` clamps still run AFTER the scaling in both designs. Document this choice explicitly in Completion Notes so a reviewer isn't surprised the scaling landed inside `bound_dna` rather than in the caller.
- [x] Task 2: Wire the two known callers (AC #1)
  - [x] `owls/evolution.py::_evolve_one`: pass `signal=SignalStrength.VERIFIED` when `bound_dna` is called and `evolution_source` started with `"attribution"` (covers both `"attribution"` and `"attribution+explore"` — the explore-margin delta is folded into the SAME per-trait dict as attribution deltas before `bound_dna` is called once for the whole batch, so it inherits the VERIFIED tier along with everything else in that call — this is a known, accepted simplification, not a bug: splitting explore's own signal strength would require calling `bound_dna` per-trait instead of once per owl, a bigger refactor out of this story's scope); pass `signal=SignalStrength.LLM_QUALITY` when `evolution_source == "llm_fallback"`
  - [x] Import `SignalStrength` from `dna_governor` in `evolution.py` (already imports `bound_dna` from the same module — add to the same import line)
- [x] Task 3: Tests (AC #1, #2, #3)
  - [x] `tests/owls/test_dna_governor.py` (or wherever `bound_dna` is tested today — find first): for a fixed `current`/`proposed`/`anchor` triple where the raw delta is well within `MAX_DELTA`, assert `bound_dna(..., signal=VERIFIED)` moves the trait by the FULL raw delta (unchanged from pre-story behavior — regression), `bound_dna(..., signal=OUTCOME_BINARY)` moves it by exactly `0.6×`, `bound_dna(..., signal=LLM_QUALITY)` moves it by exactly `0.3×`
  - [x] Assert the ceiling is NEVER widened: construct a case where the raw delta EXCEEDS `MAX_DELTA` even before scaling, and confirm the result is identical regardless of `signal` (the `MAX_DELTA` clamp already caught it — scaling a smaller number never un-caps it; this is the concrete regression test for AD-4/FR-7's "never widens" guarantee)
  - [x] Assert `bound_dna()` with NO `signal` argument at all behaves byte-identical to pre-story (calls the function positionally/keyword exactly as every existing caller does today, without `signal=`) — this IS the NFR-5 regression test, not a new special case
  - [x] `tests/owls/` evolution tests: assert `_evolve_one`'s attribution branch calls `bound_dna` with `signal=SignalStrength.VERIFIED` and the LLM-fallback branch with `signal=SignalStrength.LLM_QUALITY` (mock/spy on `bound_dna` or assert on the resulting DNA's magnitude difference between the two paths for an identical raw delta — whichever this repo's existing evolution tests already do for similar assertions, follow that convention)
- [x] Task 4: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### Architecture Compliance

- AD-4 (binds Feature 3): "tiering narrows, never widens" — `bound_dna`'s own `MAX_DELTA`/`ENVELOPE`/`TRAIT_FLOOR` constants are NEVER parameterized by `signal` (they stay exactly as `evolution_limits.py` defines them, completely untouched by this story) — only the delta feeding INTO that clamp is scaled down. Do not touch `evolution_limits.py`.
- Consistency Conventions table (spine): `SignalStrength = VERIFIED | OUTCOME_BINARY | LLM_QUALITY` "defined once in `dna_governor.py` and imported by every propose-stage caller — not re-derived per caller." Only `dna_governor.py` defines it; `evolution.py` imports it, never redefines.
- NFR-5: nightly `evolution_batch`'s behavior must stay backward-compatible except where Feature 3 explicitly changes it — the attribution path getting `VERIFIED` (multiplier 1.0, i.e. NO scaling) is exactly what keeps today's batch behavior byte-identical; only the LLM-fallback path's magnitude actually changes (now scaled to 0.3×, previously unscaled) — this is Feature 3's INTENDED behavior change for that one path, call it out explicitly in the PR/commit message so it doesn't read as an accidental regression.

### Testing Standards

- `pytest`, real fixtures, no mocking of pure functions (`bound_dna`/`scale_by_signal_strength` have zero I/O — test them directly with plain values).
- Run the specific `bound_dna` test file + the evolution test files already exercised in Story 2.3 (`tests/owls/test_evolution_feedback.py`, `tests/owls/test_dna_attribution.py`, etc. — same set, this story touches the same call sites) — do NOT run the full suite.
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/owls/dna_governor.py` (new enum + constant + function + `bound_dna` signature), `src/stackowl/owls/evolution.py` (two call-site tags). No new files, no migration.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.4] (lines 212-230)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 3] (FR-6, FR-7)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-4, Consistency Conventions "Signal strength" row)
- [Source: src/stackowl/owls/dna_governor.py] (direct read — `bound_dna`'s exact current implementation, the author-deferring floor comment block — do not disturb that logic, only add the scaling step ahead of the existing `MAX_DELTA` line)
- [Source: src/stackowl/owls/evolution_limits.py] (direct read — `MAX_DELTA`/`ENVELOPE`/`TRAIT_FLOOR`/`FLOOR_TRAITS` constants, untouched by this story)
- [Source: src/stackowl/owls/evolution.py] (direct read — `_evolve_one`'s `evolution_source` variable, already distinguishes `"attribution"`/`"attribution+explore"`/`"llm_fallback"`, this story reads that existing variable to pick the tag)
- [Source: src/stackowl/owls/dna_attribution.py] (direct read — `_filter_scored_outcomes`'s eligibility bar, the basis for tagging the attribution path VERIFIED)
- [Source: src/stackowl/tools/verification.py] (direct read — `is_trustworthy_success(success, verified)`, confirms `TaskOutcome.success` is already verification-aware at write time)

## Change Log

- 2026-07-15: Story implemented — `SignalStrength` enum + `scale_by_signal_strength()` + optional `signal` param on `bound_dna()` (scaling applied strictly before the existing MAX_DELTA/ENVELOPE/TRAIT_FLOOR clamps); the two known callers in `evolution.py::_evolve_one` wired (attribution→VERIFIED, llm_fallback→LLM_QUALITY). 6 new unit tests + 1 new wiring-integration test; 1 existing integration test's expected magnitudes updated to reflect the intended LLM-fallback scaling change (NFR-5 dev note). tonyStyle scan run — no additional defects found in touched files or their sole caller.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story workflow)

### Debug Log References

- `uv run pytest tests/owls/test_dna_governor.py tests/owls/test_evolution_feedback.py tests/owls/test_dna_attribution.py tests/owls/test_dna_attribution_approach_rating.py tests/owls/test_evolution_strategy_scaling.py tests/owls/test_f55_evolution_transient_retry.py tests/owls/test_parl_7_evolution_bounded_parallel.py -v` — 42 passed.
- `uv run ruff check src/stackowl/owls/dna_governor.py src/stackowl/owls/evolution.py tests/owls/test_dna_governor.py tests/owls/test_evolution_feedback.py tests/owls/test_evolution_strategy_scaling.py` — clean (one pre-existing E501 on `test_dna_governor.py:39`, present in HEAD before this story, left untouched — out of scope).
- `uv run mypy src/` — 0 errors in touched files (`dna_governor.py`, `evolution.py`); repo-wide pre-existing errors elsewhere unrelated to this change.

### Completion Notes List

- **Design fork resolved as option (a)** per the story's explicit instruction: the `signal` parameter was added directly to `bound_dna()`'s own signature (default `SignalStrength.VERIFIED`), with scaling applied to `prop - cur` immediately before the existing `MAX_DELTA` clamp line — inside the governor, not in the caller. This was a deliberate choice, not an oversight: `bound_dna` already derives the raw per-trait delta internally from the `(current, proposed)` snapshot pair, so there is no clean external seam to "pre-scale a delta" from the caller without duplicating that derivation math. AD-4's core guarantee (effective delta strictly ≤ raw delta; MAX_DELTA/ENVELOPE/TRAIT_FLOOR never widened by signal) holds identically under this design — those three clamps are completely untouched, only the delta feeding into them is scaled first.
- **Enum style deviation from the story's literal task text**: the story's task text writes `class SignalStrength(str, Enum)`, but `owls/` has no existing `Enum` precedent to mirror (checked `dna_attribution.py` per the task's own instruction — no Enum there). The nearest live repo convention for a JSON/log-serializable string enum is `StrEnum` (`tools/consent.py::ConsentScope`, `TrustTier`, Python ≥3.11 stdlib, this repo targets ≥3.13). Used `class SignalStrength(StrEnum)` instead of `(str, Enum)` — functionally equivalent (both are string-valued, both serialize/compare as `str`), matches the closest existing precedent instead of introducing a second enum idiom into the module.
- **`OUTCOME_BINARY` has no caller**, by design (see story's "Design decision" section) — not exercised via `EvolutionCoordinator`, tested directly against `scale_by_signal_strength()`/`bound_dna()` in `test_dna_governor.py` per the story's explicit instruction not to invent a fake caller.
- **Intended behavior change flagged, not a regression**: the LLM-fallback path's effective magnitude is now scaled to 0.3× (previously unscaled) — this is Feature 3's documented, intended change for that one path (NFR-5 dev note). `tests/owls/test_evolution_strategy_scaling.py::test_evolution_strategy_scales_real_mutation` asserted pre-story magnitudes for exactly this path (an LLM-fallback integration test) and its expected values were updated (0.01→0.003, 0.04→0.012, both = old value × 0.3) with an inline comment explaining why. The attribution/VERIFIED path (`test_evolution_feedback.py::test_evolution_refreshes_live_registry_and_bounds`, which exercises a delta that saturates MAX_DELTA regardless of signal) needed no change and stayed green untouched — confirming NFR-5 byte-identical behavior on that path.
- Added an integration test (`test_attribution_path_tagged_verified_llm_fallback_tagged_llm_quality` in `test_evolution_feedback.py`) that runs `EvolutionCoordinator.execute()` twice — once with a fixed/injected `DnaAttributor` subclass forcing the attribution branch, once via the real (signal-less) attributor falling through to LLM-fallback — and asserts the two paths land on different resulting DNA magnitudes (0.52 vs 0.506) for an identical raw +0.02 delta, per the story's "assert on the resulting DNA's magnitude difference between the two paths" instruction.
- No changes to `evolution_limits.py` (MAX_DELTA/ENVELOPE/TRAIT_FLOOR/FLOOR_TRAITS untouched, per Dev Notes).

### File List

- `src/stackowl/owls/dna_governor.py` — modified: `SignalStrength` enum, `_SIGNAL_STRENGTH_MULTIPLIER`, `scale_by_signal_strength()`, `bound_dna()` gained optional `signal` param + scaling step.
- `src/stackowl/owls/evolution.py` — modified: import `SignalStrength`; `_evolve_one` tags the `bound_dna` call with `VERIFIED` (attribution/attribution+explore) or `LLM_QUALITY` (llm_fallback).
- `tests/owls/test_dna_governor.py` — modified: added 6 new tests for `SignalStrength`/`scale_by_signal_strength`/`bound_dna`'s signal param (AC #1, #2, #3, NFR-5).
- `tests/owls/test_evolution_feedback.py` — modified: added `_FixedAttributor` test double + `test_attribution_path_tagged_verified_llm_fallback_tagged_llm_quality` (wiring assertion, AC #1).
- `tests/owls/test_evolution_strategy_scaling.py` — modified: updated `test_evolution_strategy_scales_real_mutation`'s expected magnitudes for the now-intentionally-scaled LLM-fallback path (0.3× on top of the pre-existing strategy factor), with an explanatory comment.
