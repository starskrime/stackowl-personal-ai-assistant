---
baseline_commit: d9420756
---

# Story 3.5: DNA traits inform skill retention (advisory)

Status: done

## Story

As the platform,
I want an owl's evolved traits available as an advisory weight on skill retention/synthesis decisions,
so that the two self-improvement subsystems stop operating completely blind to each other.

## Acceptance Criteria

1. **Given** `skills/synthesizer_handler.py`'s existing retention/synthesis decision
   **When** this story ships
   **Then** the owl's current DNA traits are read and factored into at least one real retention/synthesis decision per cycle — not merely exposed as an unused getter — while the existing skill security-scan gate and retention logic are not weakened or bypassed (FR-16, FR-17, AD-7)

## Design decision — advisory threshold nudge on `SkillSynthesizer.deprecate_low_performers()` (deferred mechanism, decided here)

The actual retention decision lives in `skills/synthesizer.py`'s `SkillSynthesizer.deprecate_low_performers()` (`synthesizer_handler.py` just dispatches the three phases — read the real logic in `synthesizer.py`). Its current candidate filter: `enabled AND success_rate is not None AND success_rate < _DEPRECATE_BELOW (0.4) AND n_executions >= _MIN_EXECUTIONS_FOR_RATE (5)`.

Design: for each deprecation candidate skill, look up its owning owl(s) (`owls/skill_ownership.py`'s `read_all_skill_ownership(db)` returns `{owl_name: [skill_name, ...]}` — invert it once per run into `{skill_name: [owl_name, ...]}`), read the owning owl's(s') CURRENT `completion_drive` trait (`OwlDNA`'s "persistence/initiative" trait — "HIGH → pursue the goal across blocked paths and act-first-and-persist" per its own docstring, the most semantically relevant trait for "how tolerant should we be of an underperforming-but-not-yet-proven-useless skill"), and compute a bounded advisory threshold: `effective_threshold = _DEPRECATE_BELOW * (1.0 - 0.2 * (avg_completion_drive - 0.5))`. At `completion_drive=1.0`: `effective_threshold = 0.4 * 0.9 = 0.36` (more lenient — a skill needs to be WORSE before a highly-persistent owl gives up on it). At `completion_drive=0.0`: `effective_threshold = 0.4 * 1.1 = 0.44` (stricter — a low-persistence owl deprecates sooner). At `completion_drive=0.5` (neutral/unset): `effective_threshold = 0.4` — byte-identical to today. A skill with NO owning owl (unowned/shared) keeps the unmodified `_DEPRECATE_BELOW` — no signal, no adjustment, matching the `None`-means-no-opinion convention Story 3.4 already established.

## Tasks / Subtasks

- [x] Task 1: Per-skill effective threshold (AC #1)
  - [x] New private helper on `SkillSynthesizer` (`skills/synthesizer.py`): `async def _effective_deprecate_threshold(self, skill_name: str, skill_to_owls: dict[str, list[str]]) -> float`. Looks up `skill_to_owls.get(skill_name, [])`; if empty or `self._owl_registry`/`self._db` is `None` (both already-existing constructor params, currently `None`-able — this story adds NO new required dependency), return `_DEPRECATE_BELOW` unchanged. Otherwise average `completion_drive` across the owning owl(s)' CURRENT `self._owl_registry.get(owl_name).dna.completion_drive` (skip any owl name that raises `OwlNotFoundError` — an orphaned ownership row, degrade gracefully, don't crash the whole deprecate pass over one bad row, matching this file's existing per-row fail-safe convention e.g. `hydrate_skill_ownership`'s per-row try/except), apply the bounded formula above.
  - [x] `deprecate_low_performers()`: at the top, build `skill_to_owls` ONCE via inverting `await read_all_skill_ownership(self._db)` if `self._db is not None`, else `{}` (reuse the existing `self._db`/`self._owl_registry` — no new constructor parameters added, both are already optional fields on this class per its current `__init__`)
  - [x] Change the candidate filter from the flat `s.success_rate < _DEPRECATE_BELOW` to `s.success_rate < await self._effective_deprecate_threshold(s.name, skill_to_owls)` — verified `_deprecate_one` does NOT route through `gated_skill_write`/consent gating; it's a direct `shutil.move` + `audit_write` with no consent gate at all (only discover/refine route through the shared gate). This story only changes the numeric threshold feeding INTO candidate selection — `_deprecate_one`'s mechanics are untouched, confirmed by a dedicated regression test.
  - [x] 4-point logging: extended `deprecate_low_performers`'s existing entry/exit log fields with `owned_skills` (the inverted map's size); `_effective_deprecate_threshold` logs (debug) each orphaned-row skip.
- [x] Task 2: Tests (AC #1)
  - [x] `tests/skills/test_skill_synthesizer.py` (confirmed this is the existing `deprecate_low_performers` test file, PA4b-era, 41 tests pre-story): a skill owned by a `completion_drive=0.9` owl at `success_rate=0.38` → NOT deprecated. **Corrected the story's worked numbers**: at `completion_drive=0.9` the effective threshold is `0.4*(1-0.2*(0.9-0.5))=0.368`, not `0.36` (that's the `completion_drive=1.0` endpoint value quoted in the Design Decision section, not the 0.9 test point) — `0.38` still straddles `0.368` and the flat `0.4` correctly (flat: deprecated; adjusted: spared), so the test's *outcome* was right, only the story's inline arithmetic label was off by 0.008. Verified against the actual formula before writing the test.
  - [x] Same skill shape, owl at `completion_drive=0.1`, `success_rate=0.42` → IS deprecated. Verified: effective threshold `0.4*(1-0.2*(0.1-0.5))=0.432`; `0.42<0.432` (deprecated) vs `0.42<0.4` false (flat, not deprecated) — genuine two-directional proof.
  - [x] Unowned skill (no ownership row) → flat `_DEPRECATE_BELOW`, unaffected (regression)
  - [x] `completion_drive=0.5` (neutral) → threshold computes to exactly `0.4`, byte-identical to pre-story
  - [x] Orphaned ownership row (`ghost` persisted as owner but never registered) → `OwlNotFoundError` caught, degrades that skill to the flat threshold; a sibling candidate in the same run is still processed correctly (no abort)
  - [x] Dedicated regression test confirms `_deprecate_one`'s move+audit_write path (no security-scan/consent-gate) is byte-for-byte unchanged, plus the pre-existing `test_deprecate_moves_low_performer_to_underscored_dir` passes unmodified
- [x] Task 3: QA + dev review, tests/ruff/mypy green — **NOT committed**, status=review; the orchestrating session runs independent review and commits (last story in this epic — the same process note as every prior Epic 3 story)

## Dev Notes

### This is the PRD's explicitly lowest-priority feature, second half — keep it small

Same framing as Story 3.4: Feature 7 is "lowest value, first cut if scope tightens" per the PRD's own build-order log. Do not expand beyond the bounded threshold nudge on `deprecate_low_performers` — do not touch `discover_new_skills`/`refine_midtier_skills` (the other two synthesizer phases), do not add new persisted state, do not change `_deprecate_one`'s actual deprecation mechanics (file move, audit write, consent gating — whatever they currently are).

### Architecture Compliance

- AD-7 (binds this story, same as 3.4): DNA traits are an ADDITIVE weight on the existing threshold, never a new veto. The `enabled`/`n_executions`/security-scan/consent-gate legs of the existing decision are completely untouched — a skill that would have been protected from deprecation by those existing gates stays protected regardless of any owl's `completion_drive`.
- This is the SYMMETRIC direction to Story 3.4 (Story 3.4: skill→DNA; this story: DNA→skill) — together they close FR-16/FR-17's "advisory in both directions" requirement, completing Feature 7 and the whole 14-story epic.

### Testing Standards

- `pytest`, real `tmp_db` for ownership/skill rows.
- Run: whatever existing `deprecate_low_performers`/synthesizer test file you find, plus `tests/owls/test_evolution_feedback.py` (sanity — confirm this story didn't accidentally touch anything DNA-mutation-side, since it should ONLY read DNA, never write it). Do NOT run the full suite.
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/skills/synthesizer.py` only. No new files, no migration (reuses the existing `skill_ownership` table from PA4b and `owl_dna`/registry reads — purely a read-only consumer of both).

### Process note (final story in this epic)

Implement + test + verify gates green, set status=review, and STOP. Do NOT `git commit`. After the orchestrating session's review passes and this is committed, all 14 stories across all 3 epics of the Owl DNA Self-Improvement Lifecycle are complete.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 3.5] (lines 360-371)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 7] (FR-16, FR-17)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-7)
- [Source: src/stackowl/skills/synthesizer.py] (direct read — `deprecate_low_performers`/`_deprecate_one`'s exact current logic, `SkillSynthesizer.__init__`'s already-optional `owl_registry`/`db` params — no new constructor param needed)
- [Source: src/stackowl/owls/skill_ownership.py] (direct read — `read_all_skill_ownership(db, owner_id) -> dict[owl_name, list[skill_name]]`, `hydrate_skill_ownership`'s per-row fail-safe convention to mirror)
- [Source: src/stackowl/owls/dna.py] (`completion_drive` field + its "persistence/initiative" docstring — the trait this story reads)
- [Source: _bmad-output/implementation-artifacts/3-4-skill-success-rate-feeds-dna-attribution.md] (Story 3.4 — the symmetric skill→DNA direction, same advisory-nudge pattern this story mirrors)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5) — Amelia (bmad-agent-dev)

### Debug Log References

None — implementation went green on first test run (7/7 new deprecate tests, 41/41 full synthesizer suite, 7/7 DNA-mutation sanity suite). No debugging cycle needed.

### Completion Notes List

- Read `SkillSynthesizer._deprecate_one` (`skills/synthesizer.py`) before assuming its gating shape, per the story's explicit flag: confirmed it does a direct `shutil.move` + `store.delete` + `_purge_ownership` + `audit_write`, with NO `gated_skill_write`/consent-gate/security-scan involvement at all (that shared gate is only used by discover/refine's LLM-authored writes). This story's threshold nudge therefore only affects candidate *selection*; `_deprecate_one` needed zero changes, confirmed by a dedicated regression test plus the pre-existing `test_deprecate_moves_low_performer_to_underscored_dir` passing unmodified.
- Double-checked the story's Task 2 worked numbers against the actual formula (`effective_threshold = 0.4 * (1.0 - 0.2 * (avg_completion_drive - 0.5))`) before writing tests, as explicitly asked. Formula constants (0.36 at drive=1.0, 0.44 at drive=0.0, 0.4 at drive=0.5) are exact. The story's Task 2 test-case narrative labeled the `completion_drive=0.9` test's threshold as "0.36" — that's actually the `drive=1.0` endpoint value; the real number at 0.9 is 0.368. The chosen `success_rate=0.38` still straddles the flat (0.4) and adjusted (0.368) thresholds correctly, so the intended test outcome (NOT deprecated) is unaffected — only the inline arithmetic annotation was off by 0.008. Recorded the corrected math in the test docstring and Task 2 checklist above.
- `skill_to_owls` is built once per `deprecate_low_performers()` run by inverting `read_all_skill_ownership(self._db)` (deferred import inside the method, matching this file's existing convention for `owls.skill_ownership` submodule imports elsewhere in the class).
- `OwlNotFoundError` (module-level import, no cyclic-import risk — verified both import orders in isolation) is caught per-owner inside `_effective_deprecate_threshold`; an orphaned row degrades that skill's threshold to the flat `_DEPRECATE_BELOW` without raising, and the loop continues for any other owning owl(s) on the same skill and for every other candidate skill in the run.
- `enabled` / `success_rate is not None` / `n_executions >= _MIN_EXECUTIONS_FOR_RATE` are evaluated exactly as before, ahead of the threshold lookup — the only change is that the `success_rate < X` comparison now compares against a per-skill computed value instead of the module constant directly.

### File List

- `src/stackowl/skills/synthesizer.py` (modified — `_effective_deprecate_threshold` helper, `deprecate_low_performers` per-skill threshold + skill_to_owls inversion + extended logging, `OwlNotFoundError` import)
- `tests/skills/test_skill_synthesizer.py` (modified — 7 new tests: high/low/neutral completion_drive, unowned regression, orphaned-row degradation, `_deprecate_one` mechanics regression; plus `_seed_learned_skill`/`_deprecate_env`/`_make_synth` test helpers)
