---
baseline_commit: ad590605
---

# Story 2.6: Wire the gate into promotion, with auto-restore on failure

Status: done

## Story

As the platform,
I want the shadow-validation gate to be the *only* path that promotes a checkpoint to live DNA, with automatic rollback on failure,
so that no caller — today or in the future — can bypass validation, by accident or "just this once."

## Acceptance Criteria

1. **Given** `EvolutionCoordinator`'s existing checkpoint → persist → live-refresh → audit flow
   **When** this story ships
   **Then** Story 2.5's gate is inserted between checkpoint and persist — persist only happens after the gate passes (FR-8, AD-1, AD-3)

2. **Given** the gate fails (does not reach N consecutive non-regressions)
   **When** promotion is denied
   **Then** the pre-mutation checkpoint is automatically restored via Story 2.1's `LearningArtifactStore.restore()` — no separate rollback mechanism (FR-10)

3. **Given** the nightly `evolution_batch` job
   **When** it runs after this story ships
   **Then** its behavior is unchanged except for now passing through the gate (NFR-5) — no capability regression if the gate always passes on today's real data

## Required refactor — ONE promotion function, not inline in `_evolve_one` (AD-3, sets up Story 3.2)

AD-3 requires "exactly one promotion function in the codebase, not one per caller" — both the nightly batch (this story) AND Story 3.2's `evolve_now` (next epic) must call the SAME function. `_evolve_one`'s current tail (checkpoint → compute `safe_dna` → persist → overlay → audit-log) is caller-agnostic once you have `(manifest, unclamped_new_dna, evolution_source, signal)` — extract it into a new method:

```python
async def _checkpoint_validate_and_promote(
    self, manifest: OwlAgentManifest, new_dna: OwlDNA, *, evolution_source: str, signal: SignalStrength,
) -> bool:
    """THE single promotion path (AD-3): checkpoint -> clamp -> shadow-validate -> commit-or-restore -> observe.
    Returns True if the mutation was promoted to live, False if the gate rejected it (no-op)."""
```

This method takes the RAW mutated `new_dna` (pre-governor-clamp — the same shape `_evolve_one` currently builds by looping `deltas.items()` and calling `.mutate()`), NOT the already-`bound_dna`'d result — `bound_dna` moves INSIDE this new method (it needs to run before the gate validates, since the gate should validate what would ACTUALLY ship, i.e. the clamped DNA, not the raw unclamped proposal). `_evolve_one` keeps computing `deltas`/`new_dna` (unclamped) exactly as today, then calls this one new method instead of its current checkpoint/persist/overlay/audit tail inline. This is the seam Story 3.2's `evolve_now` reuses directly — do not let Story 3.2 duplicate this logic later; build it reusable NOW.

## Tasks / Subtasks

- [x] Task 1: Extract `_checkpoint_validate_and_promote` on `EvolutionCoordinator` (AC #1, #2, #3)
  - [x] `owls/evolution.py`: add a `ShadowValidator` instance to `__init__` (`self._shadow_validator = ShadowValidator(db, provider_registry)` — uses Story 2.5's module-level defaults, per AD-3's "single shared config, not per-caller" — do NOT pass custom `n_consecutive_required`/`sample_size` here, use the class defaults)
  - [x] Move the EXISTING tail of `_evolve_one` (from `checkpoint_id = await self._learning_store.checkpoint(...)` through the `for trait in _MUTABLE_TRAITS: ... log evolution.delta` audit block) into the new `_checkpoint_validate_and_promote(manifest, new_dna, *, evolution_source, signal) -> bool` method, with the gate inserted:
    1. `checkpoint_id = await self._learning_store.checkpoint("dna", manifest.name, manifest.dna.model_dump(), reason=evolution_source)` (unchanged from today, just relocated)
    2. `anchor = await read_authored_dna(self._db, manifest.name) or OwlDNA()` (unchanged, relocated)
    3. `safe_dna = bound_dna(manifest.dna, new_dna, anchor, signal=signal)` (unchanged, relocated — now the ONLY place `bound_dna` is called for the batch path)
    4. **NEW — the gate**: `result = await self._shadow_validator.validate(manifest.name, manifest, safe_dna)`
    5. **NEW — on failure (AC #2)**: log at WARNING (`"[dna] coordinator.promote: shadow gate REJECTED — restoring checkpoint"`, fields: owl, checkpoint_id, `result.n_replayed`, `result.consecutive_non_regressions`), then `restored_payload = await self._learning_store.restore("dna", manifest.name, checkpoint_id)`, `restored_dna = OwlDNA.model_validate(restored_payload)`, `await self._persist_dna(manifest.name, restored_dna)`, `apply_dna_overlay(self._owl_registry, manifest.name, restored_dna)` — this makes the restore-and-reaffirm EXPLICIT and self-healing (idempotent even though today's call ordering means live DNA was never actually mutated pre-gate — see Dev Notes), then `return False`
    6. **On success**: the EXISTING persist/overlay/audit-log block runs exactly as it does today (unchanged code, just now gated on `result.passed`), `return True`
  - [x] `_evolve_one`'s own body shrinks to: compute `deltas` (attribution or LLM-fallback, unchanged) → scale by strategy (unchanged) → build `new_dna` via the `.mutate()` loop (unchanged) → `return await self._checkpoint_validate_and_promote(manifest, new_dna, evolution_source=evolution_source, signal=signal)` where `signal` is exactly Story 2.4's existing `SignalStrength.VERIFIED`/`SignalStrength.LLM_QUALITY` selection (unchanged logic, just passed through instead of inlined into a direct `bound_dna` call)
- [x] Task 2: Tests (AC #1, #2, #3)
  - [x] Extend the Story 2.4/2.5-era evolution tests: gate-passes case → mutation lands in `owl_dna` + `learning_artifacts`, exactly as before this story (regression, proves NFR-5)
  - [x] Gate-fails case (inject a `ShadowValidator` — or its underlying provider stub — that always returns `passed=False`): assert `owl_dna` / the live registry's DNA is UNCHANGED after `_evolve_one` returns `False`, assert a WARNING log fired, assert `LearningArtifactStore.restore()` was actually called (not just that the outcome happens to look unchanged) — e.g. spy/assert on the call, or seed a scenario where restore's return value is distinguishable from a no-op path
  - [x] Full nightly-batch regression: rerun `EvolutionCoordinator.execute()`'s existing test suite (multi-owl, concurrent, timeout/retry paths from Story 2.3's PARL-7/F-55 tests) to confirm the gate's insertion doesn't break batch-level behavior (concurrency, per-owl timeout, stuck-owl handling) — these tests currently don't stub a `ShadowValidator`, so either inject a real one against seeded `task_outcomes` (slower, more realistic) or a stub that always passes (faster, sufficient for THESE tests' actual concerns which are about concurrency/timeout, not gate logic) — pick whichever keeps these tests' existing run time reasonable, document the choice
- [x] Task 3: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### Why "restore" is a real (if usually no-op) step, not dead code

At the point the gate runs, `safe_dna` has been computed but NOT yet persisted (persist happens only after the gate passes) — so on a rejection, the LIVE `owl_dna` row and the live registry's in-memory DNA were never actually touched by THIS evolution cycle. Calling `restore()` + re-persisting + re-overlaying is therefore usually a no-op (writing back the same values that are already live). This is intentional, not wasted work: FR-10 requires "the pre-mutation checkpoint is automatically restored... no separate rollback mechanism" as a STRUCTURAL guarantee, not a conditional one contingent on today's exact call ordering. Making the restore path explicit and unconditional-on-failure means a future refactor that changes the ordering (e.g. an optimistic-persist-then-validate variant someone proposes later) can't silently break FR-10's guarantee — the restore call is the safety net regardless of what came before it. Don't "optimize" this into a no-op skip just because it's usually redundant today.

### Architecture Compliance

- AD-1: `_checkpoint_validate_and_promote` IS now the one and only Commit-stage function for DNA — `_evolve_one` never persists directly, it always goes through this method. This is the concrete implementation of "Propose → Clamp → Validate → Commit → Observe."
- AD-3 (the hard one): this story's `_checkpoint_validate_and_promote` is deliberately shaped as a general, reusable method (not `_evolve_one`-specific) so Story 3.2's `evolve_now` can call it directly next epic, satisfying "there is exactly one promotion function in the codebase, not one per caller." If Story 3.2 later finds this method needs to move to module-level (out of `EvolutionCoordinator`) to be cleanly callable from `evolve_now.py`'s tool wrapper, that refactor is Story 3.2's call — this story just needs to NOT duplicate checkpoint/gate/persist/restore logic inline a second time anywhere.
- NFR-5: gate-passes behavior is byte-identical to pre-story (same checkpoint call, same `bound_dna` call, same persist/overlay/audit) — only a NEW gate check + a NEW failure branch are added. Call this out explicitly in the commit message.

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`.
- Run: the same evolution test set from Story 2.4 (`tests/owls/test_evolution_feedback.py`, `test_f55_evolution_transient_retry.py`, `test_parl_7_evolution_bounded_parallel.py`, `test_dna_attribution.py`, `test_evolution_strategy_scaling.py`) plus `tests/owls/test_shadow_validator.py` (regression — this story doesn't change `ShadowValidator` itself) plus `tests/owls/test_learning_artifact_store.py` (regression — `restore()` now has a real caller). Do NOT run the full suite (hangs on this box).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/owls/evolution.py` only. No new files, no migration.

### Process note (read this)

The previous story (2.5) was committed by the dev-implementation step BEFORE the independent QA/review pass ran — a process slip. For this story: implement + test + verify gates green, then STOP at status=review and report back. Do NOT commit. The orchestrating session runs an independent review and handles the commit.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.6] (lines 254-272)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 4] (FR-8, FR-10, FR-11)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-1, AD-3, paradigm diagram)
- [Source: src/stackowl/owls/evolution.py] (direct read — `_evolve_one`'s exact current tail being relocated)
- [Source: _bmad-output/implementation-artifacts/2-5-shadow-validation-gate-replay-harness.md] (Story 2.5 — `ShadowValidator.validate()`'s exact signature/return shape this story calls)
- [Source: _bmad-output/implementation-artifacts/2-1-learning-artifact-store.md] (Story 2.1 — `LearningArtifactStore.restore()`'s exact signature this story calls)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-dev-story, Amelia persona)

### Debug Log References

- `uv run pytest tests/owls/test_evolution_feedback.py tests/owls/test_f55_evolution_transient_retry.py tests/owls/test_parl_7_evolution_bounded_parallel.py tests/owls/test_dna_attribution.py tests/owls/test_evolution_strategy_scaling.py tests/owls/test_shadow_validator.py tests/owls/test_learning_artifact_store.py -v` → 38 passed
- `uv run pytest tests/test_story_4_3.py tests/journeys/test_dna_completion_journey.py tests/journeys/test_persona_evolution_journey.py -v` → 28 passed, 2 pre-existing failures (see below, confirmed via `git stash` unrelated to this story)
- `uv run ruff check src/stackowl/owls/evolution.py tests/owls/test_evolution_feedback.py tests/owls/test_evolution_strategy_scaling.py tests/test_story_4_3.py tests/journeys/test_dna_completion_journey.py tests/journeys/test_persona_evolution_journey.py tests/_story_2_6_helpers.py` → All checks passed
- `uv run mypy src/stackowl/owls/evolution.py` → Success: no issues found
- `uv run mypy src/` → 79 pre-existing errors in 16 unrelated files (plugins/context.py, mcp/server.py, telegram/notifications.py, scheduler/assembly.py, startup/orchestrator.py, cli/app.py) — zero in evolution.py or any file this story touches

### Completion Notes List

- Extracted `_checkpoint_validate_and_promote(manifest, new_dna, *, evolution_source, signal) -> bool` on `EvolutionCoordinator` exactly per the story's spec: checkpoint → `read_authored_dna` anchor → `bound_dna` clamp → `ShadowValidator.validate()` gate → on failure: WARNING log + `LearningArtifactStore.restore()` + re-persist + re-overlay + `return False`; on success: the pre-existing persist/overlay/`evolution.delta` audit block (byte-identical) + `return True`. `_evolve_one` now only computes `deltas`/`new_dna`/`signal` and delegates.
  - **Deviation (justified)**: the checkpoint's `reason` field is now `reason=evolution_source` (e.g. `"attribution"`, `"llm_fallback"`) instead of the previous hardcoded literal `"evolution_batch"`. The story's Task 1 step 1 explicitly specifies `reason=evolution_source` while its parenthetical says "unchanged from today" — the two are in tension; I followed the explicit signature since it's more informative for the audit trail (FR-3) and no test asserts the old literal string. Flagging for reviewer awareness.
  - Added `shadow_validator: ShadowValidator | None = None` to `EvolutionCoordinator.__init__`, mirroring the existing `attributor` injectable-override pattern. Production (unwired call site in `scheduler/handlers/evolution.py`) is unaffected — it never passes this param, so it always gets `ShadowValidator(db, provider_registry)` with Story 2.5's module-level defaults (AD-3 compliant). Not explicitly spelled out in Task 1's bullet text, but required by Task 2's own instruction to "inject a ShadowValidator ... stub" into regression tests — without an injectable seam there is no way to stub it.
- **Gate cold-start interaction (root-caused, fixed via test-side stubbing, not production code)**: `ShadowValidator.validate()` fails CLOSED when fewer than `sample_size` (5) eligible held-out `TaskOutcome` rows exist for an owl (Story 2.5 behavior, unchanged). Every pre-2.6 evolution test seeds conversation `messages` but never scored `task_outcomes`, so wiring the real gate in would silently turn every one of those tests' "mutation applied" assertions into "mutation rejected — cold start", a false regression. Fixed by injecting a new `AlwaysPassShadowValidator` stub (`tests/_story_2_6_helpers.py`) into every test that exercises evolution *mechanics* (mutation math/strategy scaling/concurrency/retry) rather than gate logic itself: `test_evolution_feedback.py` (both existing tests), `test_evolution_strategy_scaling.py`, `test_story_4_3.py::test_execute_with_mock_llm_applies_mutations`, `test_dna_completion_journey.py`, `test_persona_evolution_journey.py`. `test_f55_evolution_transient_retry.py` and `test_parl_7_evolution_bounded_parallel.py` needed no change — both monkeypatch `_evolve_one` directly, never reaching the gate.
- Added `AlwaysFailShadowValidator` stub + a new regression test `test_gate_rejects_mutation_restores_checkpoint_and_logs_warning` (`tests/owls/test_evolution_feedback.py`) covering AC #2/Task 2 bullet 2: asserts (a) live/DB DNA unchanged after `_evolve_one` returns `False`, (b) a WARNING containing `"shadow gate REJECTED"` fires, (c) `LearningArtifactStore.restore()` is ACTUALLY invoked (spied wrapper around the real method, not inferred from the unchanged value), (d) the restore path's `_persist_dna` call is independently observable (a fresh `owl_dna` row now exists at the pre-mutation baseline, where none existed before — since this story's ordering never persists pre-gate).
- Extended the existing gate-passes regression test with an assertion that a `learning_artifacts` checkpoint row exists (proves the checkpoint step is unchanged/still running — NFR-5).
- **Task 2 bullet 3 (full nightly-batch regression)**: `test_f55_evolution_transient_retry.py` and `test_parl_7_evolution_bounded_parallel.py` both stub `_evolve_one` at the method level, so they exercise concurrency/timeout/retry without ever reaching the gate — confirmed still green, no changes needed. Documenting per the story's "document the choice" instruction.
- **Pre-existing failures found, NOT fixed (out of scope)**: `tests/test_story_4_3.py::TestEvolutionCoordinator::test_execute_with_mock_llm_applies_mutations` and `tests/journeys/test_dna_completion_journey.py::test_dna_completion_full_loop` fail on hardcoded magnitude assertions (`0.55`/`0.90`) that don't account for Story 2.4's `LLM_QUALITY` 0.3× delta scaling (actual: `0.515`/`0.895`). Reproduced identically via `git stash` against unmodified `main` (`ad590605`) — confirms this predates Story 2.6 and is Story 2.4 debt, not introduced by this change. Per CLAUDE.md's "never skip pre-existing fails" rule, tracking here rather than silently fixing (fixing would mean asserting new expected magnitudes in tests I don't own the intent of, outside this story's reviewed scope) — recommend a follow-up story/ticket.
- Neither ruff nor mypy flagged anything in the touched files; `mypy src/` shows 79 pre-existing errors across 16 files this story never touches.
- **tonyStyle scan (also found and fixed)**: `src/stackowl/owls/shadow_validator.py`'s module docstring claimed "NOT wired into the promotion flow yet: this class is constructed and called directly by its own tests only" — false as of this story (`EvolutionCoordinator` is now a real production caller via `_checkpoint_validate_and_promote`). Fixed the docstring to state the current wiring; no behavior change, doc-only. Rest of the scan (owls/evolution.py, owls/shadow_validator.py, owls/learning_artifact_store.py, and direct callers/tests) found no silent catches, no missing 4-point logging, no architecture violations, and no other duplicate promotion paths — confirmed `_checkpoint_validate_and_promote` is the only checkpoint+gate+persist call site in `src/` (grepped for `bound_dna(` co-occurring with `ShadowValidator`).

### File List

- `src/stackowl/owls/evolution.py` (modified — the only production file the story's Dev Notes scope to; primary implementation)
- `src/stackowl/owls/shadow_validator.py` (modified — tonyStyle scan finding: module docstring said "NOT wired into the promotion flow yet", now false as of this story; doc-only fix, no behavior change)
- `tests/_story_2_6_helpers.py` (new — shared `AlwaysPassShadowValidator`/`AlwaysFailShadowValidator` stubs)
- `tests/owls/test_evolution_feedback.py` (modified — stub injection + `learning_artifacts` assertion + new gate-fails test)
- `tests/owls/test_evolution_strategy_scaling.py` (modified — stub injection)
- `tests/test_story_4_3.py` (modified — stub injection)
- `tests/journeys/test_dna_completion_journey.py` (modified — stub injection)
- `tests/journeys/test_persona_evolution_journey.py` (modified — stub injection)
- `_bmad-output/implementation-artifacts/sprint-status-owl-dna.yaml` (modified — status → review)

## Change Log

- 2026-07-15: Story 2.6 implemented — `_checkpoint_validate_and_promote` extracted as the single promotion path (AD-1/AD-3), Story 2.5's `ShadowValidator` gate wired between checkpoint and persist, restore-and-reaffirm on gate failure (FR-10). Gate-passes behavior is otherwise unchanged (NFR-5) — proven by the extended regression suite. Status → review; not committed per this story's explicit process note (independent review pass runs first).
