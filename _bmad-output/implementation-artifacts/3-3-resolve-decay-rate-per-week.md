---
baseline_commit: 96076ee6
---

# Story 3.3: Resolve decay_rate_per_week

Status: done

## Story

As an owl,
I want an unreinforced trait to drift back toward my authored baseline over time,
so that a stale, unconfirmed personality shift doesn't persist indefinitely.

## Acceptance Criteria

1. **Given** `OwlDNA.decay_rate_per_week` (currently defined, zero readers)
   **When** this story ships
   **Then** a decay function moves an unreinforced trait toward its authored baseline at that rate, itself passing through Epic 2's clamp/gate pipeline like any other mutation (FR-15, AD-1, AD-6)

2. **Given** a trait that has been reinforced recently (within the decay window)
   **When** the decay function runs
   **Then** that trait is not decayed — only genuinely unreinforced traits move

## Design decision — no new table; "unreinforced this cycle" is the decay window (AD-6, PRD sizing note calls this "trivial")

The PRD's own sizing note calls Feature 6 "plausibly single-story," and the build-order decision log calls it "trivial." Do not build a new `dna_trait_last_touched` table with per-trait timestamps to implement a literal rolling "decay window" — that's over-engineering for what the architecture spine itself frames as a small, bounded change. Instead:

- **"Unreinforced" = "not among this nightly batch cycle's proposed `deltas`."** Every trait the current cycle's `deltas` dict touches is, by definition, being reinforced RIGHT NOW — skip it. Every OTHER mutable trait is unreinforced for this cycle and is a decay candidate. This needs zero new persisted state — `deltas` is already computed in `_evolve_one` every cycle.
- **Decay cadence**: the nightly batch runs DAILY (`evolution_batch`, seeded 02:00 UTC), but `decay_rate_per_week` is a WEEKLY rate. Apply `decay_rate_per_week / 7` as the fraction moved toward baseline PER DAILY RUN (exponential decay toward the authored anchor, compounding daily — this is the simplest correct reading of "decay_rate_per_week" given the job's actual daily cadence, and does not require tracking "how many days since last run" separately). Document this `/ 7` choice in a code comment so a future reader doesn't mistake it for a bug.
- **Skip if already at baseline**: if `current_trait_value == anchor_trait_value` (already at the authored baseline), there's nothing to decay — don't checkpoint/promote a no-op.

## Tasks / Subtasks

- [x] Task 1: `_apply_decay` on `EvolutionCoordinator` (AC #1, #2)
  - [x] New private method `owls/evolution.py`: `async def _apply_decay(self, manifest: OwlAgentManifest, reinforced_traits: frozenset[str]) -> bool`
  - [x] `anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()` (same pattern `_checkpoint_validate_and_promote` already uses)
  - [x] Build `decayed_dna` by looping `_MUTABLE_TRAITS`: for any trait IN `reinforced_traits`, keep it unchanged (AC #2 — genuinely skipped, not just decayed by ~0); for a trait NOT in `reinforced_traits`, if `current == anchor` for that trait, keep unchanged (nothing to decay); otherwise compute `new_value = current + (anchor - current) * (manifest.dna.decay_rate_per_week / 7)` and set it. Use `manifest.dna.model_copy(update={...})` for the final `decayed_dna` (mirrors `bound_dna`'s own `model_copy` pattern), building the update dict only from traits that actually changed (an empty update dict → `decayed_dna == manifest.dna`, handle that as "nothing to decay," see below)
  - [x] If `decayed_dna == manifest.dna` (no trait needed decay this cycle — either everything was reinforced, or every unreinforced trait was already at baseline): return `False`, do NOT call `_checkpoint_validate_and_promote` (no pointless checkpoint/gate cycle for a no-op)
  - [x] Otherwise: `return await self._checkpoint_validate_and_promote(manifest, decayed_dna, evolution_source="decay", signal=SignalStrength.VERIFIED)` — routes through the SAME gated pipeline as any other mutation (AC #1's explicit requirement). `SignalStrength.VERIFIED` is used here NOT because decay is "verified" in the outcome-verification sense, but because its multiplier is 1.0 (no additional scaling) — decay's own rate constant (`decay_rate_per_week / 7`) is already the intended, fully-configured step size; layering a SECOND scaling factor on top (as `OUTCOME_BINARY`/`LLM_QUALITY` would) would silently under-decay relative to the operator's configured rate. Documented in a code comment at the call site so a future reader doesn't assume decay is claiming to be a "verified outcome."
  - [x] Call `_apply_decay` from `_evolve_one`, right after the main `deltas`-based promotion attempt (whichever branch: attribution, LLM-fallback, or no-deltas-skip) — **re-fetch the manifest first**: `current_manifest = self._owl_registry.get(manifest.name)` (the promotion attempt may have overlaid a NEW dna onto the live registry via `apply_dna_overlay`, or restored the original on a gate rejection — either way, `_apply_decay` must operate on the registry's TRUE current state, not the stale local `manifest` variable `_evolve_one` started with). Pass `reinforced_traits=frozenset(deltas.keys())` (the empty frozenset if `deltas` was empty — meaning every trait is a decay candidate that cycle).
  - [x] `_evolve_one`'s own return value (used by `execute()`'s `mutated_owls`/`skipped_owls` bookkeeping) reflects ONLY the main deltas-based promotion result, unchanged — do not fold decay's own True/False into that return value (decay is a background drift correction, not "the owl mutated" in the sense that bookkeeping tracks; changing that return semantics would be an undisclosed behavior change to `execute()`'s existing `mutated=/skipped=/stuck=` output). If decay itself promotes something, it's still visible via its own `evolution_source="decay"` checkpoint row and the existing `[owls] evolution.delta` audit log line (which already logs `source=evolution_source` per trait) — that's sufficient visibility, no new reporting needed.
  - [x] 4-point logging, `log.owls` namespace.
- [x] Task 2: Tests (AC #1, #2)
  - [x] New file `tests/owls/test_dna_decay.py`: a trait NOT in `deltas`, current value away from the authored anchor → decays by exactly `(anchor - current) * (decay_rate_per_week / 7)`, promoted via a checkpoint with `reason="decay"`
  - [x] A trait IN `deltas` (reinforced this cycle) → untouched by `_apply_decay` even though it might be far from its anchor (AC #2's explicit regression test)
  - [x] A trait already AT its anchor value → no decay, no spurious checkpoint (assert `_apply_decay` returns `False` and no NEW `learning_artifacts` row with `reason="decay"` appears)
  - [x] Decay itself goes through the gate: stub `ShadowValidator` to reject → assert the decayed value is NOT applied (restored), same auto-restore machinery Story 2.6 already tests — this is the concrete proof for "passing through Epic 2's clamp/gate pipeline like any other mutation"
  - [x] Regression: full `_evolve_one` cycle (existing tests) still produce the SAME `mutated=`/`skipped=` bookkeeping as before this story — decay must not silently change what `execute()` reports
- [x] Task 3: QA + dev review, tests/ruff/mypy green — **do NOT commit**, leave status=review; the orchestrating session runs independent review and commits (same process note as prior Epic 3 stories)

## Dev Notes

### Architecture Compliance

- AD-1/AD-6: decay is implemented as just another caller of `_checkpoint_validate_and_promote` — no new write path, no bypass of clamp/gate/checkpoint/audit.
- AD-4: decay's delta is still subject to `bound_dna`'s existing `MAX_DELTA`/`ENVELOPE`/`TRAIT_FLOOR` ceiling (inherited automatically since it goes through the same function) — a large `decay_rate_per_week` value cannot move a trait further than the governor's rate cap in one cycle, same as any other mutation source. This is a genuine, useful safety property of reusing the shared pipeline rather than writing decay as a direct DB write.

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`, reuse Story 2.6's `AlwaysPassShadowValidator`/`AlwaysFailShadowValidator` stubs from `tests/_story_2_6_helpers.py`.
- Run: `tests/owls/test_evolution_feedback.py`, `tests/owls/test_dna_attribution.py`, `tests/owls/test_evolve_one_owl_now.py` (regression — confirm decay doesn't interfere with the per-task path), plus whatever new decay test file you create. Do NOT run the full suite.
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/owls/evolution.py` only (new `_apply_decay` method + one new call site in `_evolve_one`). No new files, no migration.

### Process note (same as prior Epic 3 stories)

Implement + test + verify gates green, set status=review, and STOP. Do NOT `git commit`.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 3.3] (lines 332-347)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 6] (FR-15)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-6 — `[ASSUMPTION: implement, not delete]`)
- [Source: src/stackowl/owls/dna.py] (`decay_rate_per_week` field, default 0.05, `[0,1]`)
- [Source: src/stackowl/owls/evolution.py] (direct read — `_evolve_one`'s current structure, `_checkpoint_validate_and_promote`'s exact signature from Story 2.6)
- [Source: src/stackowl/owls/dna_governor.py] (direct read — `bound_dna`'s clamp, which decay's delta inherits automatically)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story, Amelia persona)

### Debug Log References

- `uv run pytest tests/owls/test_dna_decay.py tests/owls/test_evolution_feedback.py tests/owls/test_dna_attribution.py tests/owls/test_evolve_one_owl_now.py -v` — 25 passed
- `uv run ruff check src/stackowl/owls/evolution.py tests/owls/test_dna_decay.py` — all checks passed
- `uv run mypy src/stackowl/owls/evolution.py` — no issues (full `src/` run confirms 79 pre-existing errors elsewhere, none in touched files)
- `tonyStyle` scan of `evolution.py` + adjacent `owls/` modules it now depends on (`learning_artifact_store.py`, `dna_governor.py`, `dna_authored.py`, `dna_hydrator.py`, `dna_defaults.py`) — clean, no defects found, no changes needed.

### Completion Notes List

- Added `EvolutionCoordinator._apply_decay` (`src/stackowl/owls/evolution.py`): loops `_MUTABLE_TRAITS`, skips reinforced traits and traits already at the authored anchor, computes `current + (anchor - current) * (decay_rate_per_week / 7)` for the rest, and routes any resulting change through the existing `_checkpoint_validate_and_promote` gate with `evolution_source="decay"` / `SignalStrength.VERIFIED`.
- Restructured `_evolve_one`'s early-return ("no deltas from any path") into an `if/else` so decay always runs after the main promotion attempt, regardless of which branch fired (attribution / LLM-fallback / no-deltas). `promoted` (the function's return value) is set only by the main deltas-based promotion, per spec — decay's own True/False is never folded in.
- Decay re-fetches the manifest from `self._owl_registry.get(manifest.name)` before running, per the story's explicit requirement, since the main promotion attempt may have overlaid new DNA (or restored on gate rejection) onto the live registry.
- No new table, no new migration, no new files besides the test file — matches the story's "no new persisted state" design decision.

### File List

- `src/stackowl/owls/evolution.py` (modified — new `_apply_decay` method + call site wiring in `_evolve_one`)
- `tests/owls/test_dna_decay.py` (new — 5 tests covering AC #1/#2, gate-rejection, and `_evolve_one` bookkeeping regression)

### Change Log

- 2026-07-15: Story 3.3 implemented — `_apply_decay` wired into `_evolve_one`, resolving `OwlDNA.decay_rate_per_week`'s zero-readers gap (FR-15, AD-1, AD-6). Status → review.
