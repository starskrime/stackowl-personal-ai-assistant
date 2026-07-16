---
baseline_commit: 91b58a75
---

# Story 3.4: Skill success rate feeds DNA attribution (advisory)

Status: done

## Story

As the platform,
I want a skill's measured success rate available as an input signal to DNA attribution,
so that DNA evolution has more signal than turn-level outcomes alone.

## Acceptance Criteria

1. **Given** a skill's tracked `success_rate` (`skills/store.py`'s `success_rate` column, set via `set_success_rate`)
   **When** `DnaAttributor` runs
   **Then** this signal is read and factored into at least one real attribution decision per cycle — not merely exposed as an unused getter — while never gating or vetoing what the existing positive-only-learning filter already decides (FR-16, FR-17, AD-7)

## Design decision — advisory magnitude nudge on `_attribute_one_trait`'s proposed_delta (deferred mechanism, decided here)

The architecture spine explicitly defers the exact consumption mechanism to story-time. Design: `DnaAttributor.attribute()` gains an optional `skill_success_rate: float | None = None` parameter (the OWL's average success rate across its owned, execution-tested skills). Inside `_attribute_one_trait`, AFTER the existing trait-band gap analysis computes `proposed_delta` (completely unchanged logic — the decision of WHETHER to propose a delta, and which direction, is 100% unaffected by this story), apply a small bounded multiplicative nudge: `effective_delta = proposed_delta * (0.85 + 0.3 * skill_success_rate)` when `skill_success_rate is not None`, else `proposed_delta` unchanged. `skill_success_rate ∈ [0,1]` → multiplier ∈ `[0.85, 1.15]` — a ±15% nudge, never zero, never sign-flipping, never able to manufacture a delta where the existing band analysis proposed none (`0.0 * anything == 0.0`, still no-signal). This satisfies AD-7 literally: additive/advisory weight on the EXISTING decision function, not a new veto — a skill's success rate can make an already-proposed delta slightly bigger or smaller, never turn a "no signal" into a "signal" or vice versa.

## Tasks / Subtasks

- [x] Task 1: Compute the owl's aggregate skill success rate (AC #1)
  - [x] New helper in `owls/dna_attribution.py` (or `owls/evolution.py`, wherever the caller lives — your call, keep it near its one caller): given `owl_name`, read the owl's owned skill names (`self._owl_registry.get(owl_name).skills` — same pattern `classify.py`'s `_gather_relevant_skills` already uses for owned-skill lookups), fetch those skills via `SkillIndexStore.get_many_by_name(tuple(owned_names))` (reuse — do not write a new N+1 loop of `.get()` calls), average the `success_rate` field across skills where it's NOT `None` (unexecuted skills have `success_rate=None` — exclude them from the average, don't treat as 0). Zero eligible skills (owl owns none, or none have executed enough to have a rate) → `None` (no signal, not a fabricated 0.5 or 0.0 — matches this repo's existing convention of `None` meaning "no opinion" throughout `dna_attribution.py`/`classify.py`).
- [x] Task 2: Thread it into `DnaAttributor.attribute()` (AC #1)
  - [x] `attribute(self, owl_name, current_dna, outcomes, *, skill_success_rate: float | None = None)` — new keyword-only param, default `None` preserves EXACT current behavior for any caller that doesn't pass it (NFR-5-style backward compat, same discipline as Story 2.4's `bound_dna` signal param)
  - [x] `_attribute_one_trait` (or wherever the multiplication is cleanest — could stay in `attribute()`'s loop instead of threading the param one level deeper, your call) applies the bounded nudge described above, ONLY on a non-zero `proposed_delta`
  - [x] `EvolutionCoordinator._try_attribution` (the sole current caller of `self._attributor.attribute(...)`) computes Task 1's aggregate and passes it through
  - [x] 4-point logging: log the computed `skill_success_rate` (or `None`) and whether a nudge was applied, at the existing `_try_attribution`/`_attribute_one_trait` log points — don't add a whole new logging block, extend the existing `_fields` dicts
- [x] Task 3: Tests (AC #1)
  - [x] `tests/owls/test_dna_attribution.py`: a trait with a genuine band gap (would propose a non-zero delta with `skill_success_rate=None`) → passing `skill_success_rate=1.0` increases the magnitude by exactly 15%, `skill_success_rate=0.0` decreases it by exactly 15%, `skill_success_rate=0.5` leaves it unchanged (multiplier exactly 1.0 at the midpoint — verify the formula's own arithmetic, `0.85 + 0.3*0.5 = 1.0`)
  - [x] A trait with NO band gap (`proposed_delta == 0.0` with `skill_success_rate=None`) → still `0.0` regardless of `skill_success_rate` value (AC #1's "never gates or vetoes" — a nudge cannot manufacture signal from nothing)
  - [x] `attribute()` called with the default (no `skill_success_rate` kwarg) → byte-identical output to pre-story (regression test, same discipline as every signal-tiering story in this epic)
  - [x] The aggregate-computation helper: owl with no owned skills → `None`; owl with owned skills but none executed (`success_rate=None` for all) → `None`; mixed → correct average excluding `None`s
- [x] Task 4: QA + dev review, tests/ruff/mypy green — **do NOT commit**, leave status=review; the orchestrating session runs independent review and commits (same process note as prior Epic 3 stories)

## Dev Notes

### This is the PRD's explicitly lowest-priority feature — keep it small

The PRD's own build-order decision log calls Feature 7 "lowest value, first cut if scope tightens." Do not expand this beyond the bounded advisory nudge described above — no new persisted state, no new table, no change to the positive-only-learning filter (`_filter_scored_outcomes` stays completely untouched), no change to WHICH traits get deltas or in which direction.

### Architecture Compliance

- AD-7 (binds this story): "both cross-signal inputs are additive weights consumed by the existing decision function, never a new veto/gate of their own." The `0.85–1.15` multiplier is exactly that — it can only ever scale an EXISTING non-zero decision, never create or destroy one.
- The positive-only-learning filter (`_filter_scored_outcomes`, `dna_attribution.py`) is NOT touched by this story — it governs which OUTCOMES feed the trait-band analysis; this story's signal is a completely separate, skill-level input applied AFTER that filter has already done its job.

### Testing Standards

- `pytest`, real `tmp_db` where a DB is needed (skill lookups), pure-function tests for the multiplier math (no I/O needed there).
- Run: `tests/owls/test_dna_attribution.py`, `tests/owls/test_evolution_feedback.py` (regression). Do NOT run the full suite.
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/owls/dna_attribution.py`, `src/stackowl/owls/evolution.py`. No new files, no migration (reuses the existing `skills.success_rate` column).

### Process note (same as prior Epic 3 stories)

Implement + test + verify gates green, set status=review, and STOP. Do NOT `git commit`.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 3.4] (lines 348-359)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 7] (FR-16, FR-17)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-7, Deferred section)
- [Source: src/stackowl/owls/dna_attribution.py] (direct read — `_attribute_one_trait`'s exact current logic, `TraitAttribution.proposed_delta`)
- [Source: src/stackowl/skills/store.py] (direct read — `Skill.success_rate`, `SkillIndexStore.get_many_by_name`)
- [Source: src/stackowl/pipeline/steps/classify.py] (direct read — `_gather_relevant_skills`'s existing `owl_registry.get(name).skills` owned-skill lookup pattern, reused here)

## Dev Agent Record

### Agent Model Used

Amelia (bmad-dev-story), Claude Sonnet 5

### Debug Log References

- `uv run pytest tests/owls/test_dna_attribution.py tests/owls/test_evolution_feedback.py tests/owls/test_evolve_one_owl_now.py -v` → 29 passed
- `uv run ruff check src/stackowl/owls/dna_attribution.py src/stackowl/owls/evolution.py tests/owls/test_dna_attribution.py tests/owls/test_evolution_feedback.py tests/owls/test_evolve_one_owl_now.py` → All checks passed
- `uv run mypy src/stackowl/owls/dna_attribution.py src/stackowl/owls/evolution.py` → Success: no issues found

### Completion Notes List

- `DnaAttributor.attribute()` gained a keyword-only `skill_success_rate: float | None = None` param, threaded into `_attribute_one_trait`. The bounded advisory multiplier (`0.85 + 0.3 * skill_success_rate` → `[0.85, 1.15]`) is applied ONLY to the already-computed non-zero `proposed_delta` in `_attribute_one_trait`'s final return — never to the `0.0` short-circuit branches ("<2 bands qualify", "gap too small", "already in best band"), so it can only scale an existing decision, never manufacture one. `DnaAttributor` itself stays DB-free per its existing "pure logic" contract.
- New `EvolutionCoordinator._owl_skill_success_rate(manifest)` helper (in `evolution.py`, next to its one caller `_try_attribution`) averages `success_rate` across the owl's owned skills (`SkillIndexStore.get_many_by_name`, no N+1), excluding `None` (unexecuted) skills from the average. Returns `None` when the owl owns no skills, none have executed, or the DB lookup itself fails (advisory-only signal — a lookup failure must never block the main attribution outcome, so it's caught, logged at WARNING with `exc_info`, and degrades to "no signal" rather than propagating).
- `_try_attribution` now computes this aggregate and passes it through to `self._attributor.attribute(...)`.
- 4-point logging extended (not duplicated) on `attribute()`'s entry, `_attribute_one_trait`'s entry + delta-exit, `_try_attribution`'s exit, and all 4 points of the new `_owl_skill_success_rate` helper.
- Positive-only-learning filter (`_filter_scored_outcomes`) is byte-for-byte untouched — confirmed via `git diff` showing zero changes to that function.
- Two pre-existing test doubles (`_FixedAttributor` in `test_evolution_feedback.py`, `_SpyAttributor` in `test_evolve_one_owl_now.py`) subclass `DnaAttributor` and override `.attribute()` without the new kwarg; both updated to accept `*, skill_success_rate: float | None = None` so the new keyword call from `_try_attribution` doesn't raise `TypeError` against them. No behavior change to either test's assertions.
- Deviation from the story's literal Task-1 wording: the story suggested the helper take `owl_name` and re-look-up the manifest via `self._owl_registry.get(owl_name)`. Since the sole caller (`_try_attribution`) already has the `manifest` in hand, the helper takes `manifest: OwlAgentManifest` directly instead — avoids a redundant registry lookup, same data, smaller diff. Still satisfies "keep it near its one caller."
- `uv run ruff check src/ tests/` and `uv run mypy src/` (repo-wide, per Dev Notes instruction) both surface pre-existing failures in unrelated files (e.g. `src/stackowl/mcp/server.py`, `src/stackowl/scheduler/assembly.py`, `src/stackowl/startup/orchestrator.py`, several `tests/owls/` files with unrelated import-order/Yoda-condition lint issues) — confirmed via targeted `ruff`/`mypy` runs scoped to only this story's touched files, which are 100% clean. These pre-existing issues are outside this story's scope (unrelated subsystems, not touched by this diff) and are left for the orchestrating session's judgment rather than folded into this small, deliberately-scoped story.

### File List

- `src/stackowl/owls/dna_attribution.py` (modified)
- `src/stackowl/owls/evolution.py` (modified)
- `tests/owls/test_dna_attribution.py` (modified)
- `tests/owls/test_evolution_feedback.py` (modified)
- `tests/owls/test_evolve_one_owl_now.py` (modified)
