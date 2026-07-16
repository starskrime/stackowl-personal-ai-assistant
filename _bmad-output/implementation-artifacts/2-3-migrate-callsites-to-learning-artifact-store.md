---
baseline_commit: 7b0d94d0
---

# Story 2.3: Migrate DNA and skill mutation call sites onto LearningArtifactStore

Status: done

## Story

As the platform,
I want `owls/evolution.py`'s existing `checkpoint()` call and `skill_manage.py`'s `record_skill_mutation` internals both routed through `LearningArtifactStore`,
so that `DNACheckpointer` and skills' independent versioning logic are superseded, not left as parallel duplicates (AD-2).

## Acceptance Criteria

1. **Given** `EvolutionCoordinator`'s existing checkpoint-then-persist flow
   **When** this story ships
   **Then** it calls `LearningArtifactStore.checkpoint()` instead of `DNACheckpointer.checkpoint()` directly

2. **Given** `skill_manage.py`'s existing mutation-versioning call site
   **When** this story ships
   **Then** it calls `LearningArtifactStore` for its snapshot/restore/audit needs, and its existing wired `/skill restore` command continues to work unchanged (NFR-1)

## IMPORTANT — scope decision on AC #2 (read this before writing skill-side code)

AC #2's literal epics-doc wording ("calls `LearningArtifactStore` for its snapshot/restore/audit needs") is satisfied here as an **additive checkpoint write, not a full read-path cutover**. Direct investigation of the skill audit trail found a real, load-bearing dependency that makes a full cutover unsafe within this story's scope — documented below so the dev agent doesn't attempt (and silently half-break) a bigger migration than intended:

- `SkillIndexStore.find_audit_by_hash()` (`src/stackowl/skills/store.py:755-789`) does `WHERE skill_name = ? AND (after_hash LIKE ? OR before_hash LIKE ?)` — a SQL `LIKE`-prefix lookup on **first-class indexed hash columns** in `skill_audit`. This is what powers `/skill restore <name> <short-hash>` and `/skill diff` (a user paste a 7+ char hash prefix from prior output). `LearningArtifactStore`'s schema (Story 2.1, AD-2's "one schema for both artifact types") has no `before_hash`/`after_hash` columns — hashes would have to live buried inside `payload_json`, where a SQL `LIKE` prefix match either isn't possible or requires a much slower full-table JSON-substring scan. Adding artifact-type-specific indexed hash columns to `learning_artifacts` would break AD-2's single-shared-schema simplicity for the sake of one artifact type.
- Migrating skill_audit's write path onto `LearningArtifactStore` while KEEPING `skill_audit`/`find_audit_by_hash` as the read path for `/skill restore`/`/skill diff` would leave the two writes out of sync — worse than today.

**Decision**: `record_skill_mutation` (`commands/skill_helpers.py`) gets a NEW, ADDITIONAL call to `LearningArtifactStore.checkpoint("skill", skill_name, payload, reason=op)` (payload = `{"op": op, "actor": actor, "before_hash": before_hash, "after_hash": after_hash, "details": details, "snapshot": snapshot}`), inserted alongside its existing `store.audit_write(...)` call — NOT replacing it. `skill_audit`, `SkillIndexStore.audit_write`/`find_audit_by_hash`/`recent_audit_for_skill`, and `/skill restore`/`/skill diff` stay completely untouched, byte-identical behavior (NFR-1, zero regression risk on a live user-facing feature). This gives `LearningArtifactStore` a real skill-side row (satisfying AD-2's intent that the unified primitive isn't DNA-only) without touching the hash-prefix-dependent read path that a true single-story cutover cannot safely replicate. A full read-path migration (giving `learning_artifacts` an indexed hash column, or accepting the schema split) is a follow-up backlog item, NOT this story's job — note it in Completion Notes, do not attempt it here.

## Tasks / Subtasks

- [x] Task 1: DNA side — full migration, low risk (AC #1)
  - [x] Confirm (grep first) that `DNACheckpointer.restore()`/`list_checkpoints()` have zero callers anywhere in `src/` (per the PRD Background's own finding and Story 2.2's `_dna_restore`, which already calls `LearningArtifactStore.restore()` directly, never `DNACheckpointer.restore()`) — this confirms swapping the ONE write call site in `evolution.py` is safe and complete, no other code path depends on `DNACheckpointer`'s read side
  - [x] `owls/evolution.py`: replace `self._checkpointer = DNACheckpointer(db)` (constructor) with `self._learning_store = LearningArtifactStore(db)`; replace the `evolve_one` call site `checkpoint_id = await self._checkpointer.checkpoint(manifest.name, manifest.dna)` with `checkpoint_id = await self._learning_store.checkpoint("dna", manifest.name, manifest.dna.model_dump(), reason="evolution_batch")`
  - [x] Remove the now-unused `from stackowl.owls.dna_storage import DNACheckpointer, upsert_owl_dna` → `from stackowl.owls.dna_storage import upsert_owl_dna` (keep `upsert_owl_dna`, it's still used by `_persist_dna`); add `from stackowl.owls.learning_artifact_store import LearningArtifactStore`
  - [x] Do NOT delete the `DNACheckpointer` class itself (`owls/dna_storage.py`) or its two existing test files (`tests/test_story_4_3.py`, `tests/owls/test_dna_completion_drive.py`) — per NFR-1 and "minimal diff," deleting a still-tested, still-importable class is a separate cleanup decision, not this story's scope. It becomes dead code (superseded, per AD-2's wording) but stays in the tree.
- [x] Task 2: Skill side — additive checkpoint, zero read-path change (AC #2)
  - [x] `commands/skill_helpers.py`'s `record_skill_mutation()`: after the existing `await store.audit_write(...)` call, add a call to `LearningArtifactStore.checkpoint("skill", skill_name, {"op": op, "actor": actor, "before_hash": before_hash, "after_hash": after_hash, "details": details, "snapshot": captured}, reason=op)` — check `SkillIndexStore`'s actual attribute name for its `DbPool` (likely `self._db` via `OwnedRepository`, confirm by reading the class) rather than guessing; if `record_skill_mutation` doesn't already have a `DbPool` in scope, thread it through the function signature (it's called from `skill_manage.py` which already has `get_services().db_pool` available) — do not construct a second connection/pool
  - [x] This new call is a SECOND, additive write — do not remove or modify `store.audit_write(...)` or any of its callers
  - [x] Wrap the new `LearningArtifactStore.checkpoint()` call so a failure there does NOT fail the whole `record_skill_mutation()` operation (it's an enhancement, the skill mutation itself already succeeded via the existing audit_write path) — log at WARNING and continue, matching this repo's B5 self-healing convention for non-critical enhancement writes (see `reflection_writer_handler.py`'s `_publish_to_lessons` for the exact pattern: best-effort, logged, never blocks the primary operation)
- [x] Task 3: Tests (AC #1, #2, NFR-1)
  - [x] DNA: extend `tests/owls/` evolution tests (find the existing `EvolutionCoordinator._evolve_one` test file first) to assert a `learning_artifacts` row with `artifact_type="dna"` is written on a successful evolution, instead of asserting a `dna_checkpoints` row
  - [x] Skill: extend `tests/knowledge/test_skill_manage.py` or wherever `record_skill_mutation`/`skill_manage` create-action is tested (find first) to assert BOTH a `skill_audit` row AND a `learning_artifacts` row (`artifact_type="skill"`) are written on a `create`/`edit`/`patch` action
  - [x] Regression (NFR-1, critical): run whatever test file(s) cover `/skill restore` end-to-end (hash-prefix lookup, `find_audit_by_hash`, full restore flow) — these must pass completely unmodified. If you can't find a dedicated restore-command test, search for `find_audit_by_hash` usages in tests and run those.
  - [x] Regression: `tests/owls/test_learning_artifact_store.py` (Story 2.1) and the `dna-restore` tests (Story 2.2, `tests/commands/test_owls_reset_dna.py`) still pass — Story 2.2's tests wrote synthetic checkpoints directly; this story's real evolution flow now ALSO writes real ones, verify no collision/interference
- [x] Task 4: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### Why the DNA side is safe to fully cut over but the skill side isn't

DNA: `DNACheckpointer.checkpoint()` has exactly ONE caller (`evolution.py`), and its `restore()`/`list_checkpoints()` have ZERO callers anywhere (confirmed by the PRD's own audit and by Story 2.2 already bypassing `DNACheckpointer` entirely in favor of `LearningArtifactStore`). Swapping the one write call site is a complete, low-risk migration — no read path depends on the old table going forward.

Skill: `record_skill_mutation` → `SkillIndexStore.audit_write()` writes to `skill_audit`, which THREE read methods depend on (`find_audit_by_hash` — hash-prefix `LIKE` lookup used by `/skill restore`/`/skill diff`; `recent_audit_for_skill` — used by `/skill history`), all indexed on first-class `before_hash`/`after_hash`/`ts` columns that `LearningArtifactStore`'s generic `payload_json` schema doesn't have. A full cutover would need either a schema change to `learning_artifacts` (violates AD-2's one-schema-for-both simplicity) or a materially worse read path (JSON substring `LIKE`, no index). This story does the safe thing: additive write only. See the "IMPORTANT — scope decision" section above for the full reasoning — do not silently attempt a bigger migration than this.

### Architecture Compliance

- AD-1: neither call site bypasses Propose→Commit — `checkpoint()` is called BEFORE the mutation applies in both `evolution.py` (already true today, unchanged by this story) and `record_skill_mutation` (the new call is added inside the existing before-hash → mutate → after-hash → snapshot → audit sequence, checkpointing captures the SAME state `audit_write` already captures).
- AD-2: `LearningArtifactStore` genuinely becomes the primitive BOTH artifact types write through going forward (even though skill's OLD read path stays on `skill_audit` for now, per the documented scope decision) — this is progress toward AD-2's intent, not a violation of it, given the constraint that a full read-path cutover isn't safely achievable in one story without either breaking `/skill restore` or compromising AD-2's own schema-simplicity goal on the DNA side.
- NFR-1: the skill-side change is purely additive (a new write, wrapped to never fail the primary operation) — zero existing behavior changes. The DNA-side change swaps an internal write target but zero external behavior changes (nothing reads `dna_checkpoints` going forward; `dna-restore`, per Story 2.2, already reads `learning_artifacts`).
- NFR-3: the new `LearningArtifactStore.checkpoint()` calls inherit 4-point logging from Story 2.1's implementation — no new logging needed at THESE call sites beyond what's already there (`evolution.py`'s existing entry/step/exit logs around the checkpoint call, `record_skill_mutation`'s existing entry/exit logs).

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`, no mocking.
- Do NOT run the full `uv run pytest` suite (hangs on this box) — targeted paths only, but be thorough about which paths: this story touches two independently-tested subsystems (DNA evolution, skill management), both need their existing suites green, not just the new assertions.
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/owls/evolution.py`, `src/stackowl/commands/skill_helpers.py`. No new files, no migration (Story 2.1's table already exists).

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.3] (lines 196-211)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-1, AD-2)
- [Source: src/stackowl/owls/evolution.py] (direct read — `EvolutionCoordinator.__init__`, `_evolve_one`'s checkpoint call site)
- [Source: src/stackowl/commands/skill_helpers.py] (direct read — `record_skill_mutation`)
- [Source: src/stackowl/skills/store.py] (direct read — `SkillIndexStore.audit_write`/`find_audit_by_hash`/`recent_audit_for_skill`, the load-bearing dependency motivating the additive-not-cutover decision)
- [Source: _bmad-output/implementation-artifacts/2-1-learning-artifact-store.md], [Source: _bmad-output/implementation-artifacts/2-2-dna-restore-command.md] (prior stories in this epic)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-agent-dev / Amelia)

### Debug Log References

- Targeted regression run (6 files, 92 tests): `uv run pytest tests/test_story_4_3.py tests/tools/knowledge/test_provenance_chokepoint.py tests/tools/knowledge/test_skill_manage.py tests/skills/test_skill_restore.py tests/owls/test_learning_artifact_store.py tests/commands/test_owls_reset_dna.py -v` → 92 passed in 168.76s.
- DNA-side wider regression (7 files, 57 tests): `uv run pytest tests/test_story_4_3.py tests/owls/test_evolution_feedback.py tests/owls/test_f55_evolution_transient_retry.py tests/owls/test_parl_7_evolution_bounded_parallel.py tests/owls/test_dna_attribution.py tests/owls/test_evolution_strategy_scaling.py tests/owls/test_dna_completion_drive.py -v` → surfaced 2 pre-existing (baseline, unrelated) failures, fixed — see Completion Notes.
- `uv run ruff check src/stackowl/owls/evolution.py src/stackowl/commands/skill_helpers.py tests/test_story_4_3.py tests/tools/knowledge/test_provenance_chokepoint.py` → clean.
- `uv run mypy src/stackowl/owls/evolution.py src/stackowl/commands/skill_helpers.py src/stackowl/owls/learning_artifact_store.py` → clean, 3 source files.
- Full-repo `uv run ruff check src/ tests/` (348 pre-existing errors) and `uv run mypy src/` (79 pre-existing errors) both run; confirmed via `grep` that zero of either error set falls in any file this story touched — pre-existing repo-wide baseline debt, out of this story's scope.
- tonyStyle scan run over the diff + adjacent files (`skill_command.py`, `skill_manage.py`, `skills/store.py`, `dna_storage.py`) for silent catches / missing 4-point logging / AD-1 ordering violations — one candidate (`skill_manage.py:343`, an unlogged `except OSError`) traced out as consistent with that file's existing validation-helper convention (pre-mutation content validators return structured errors without a JSONL log line; only mutation/operational failures log), not a new or introduced defect.

### Completion Notes List

- **AC #1 (DNA, full cutover) — DONE.** `EvolutionCoordinator.__init__` now builds `LearningArtifactStore(db)` instead of `DNACheckpointer(db)`; `_evolve_one`'s checkpoint call site now calls `LearningArtifactStore.checkpoint("dna", manifest.name, manifest.dna.model_dump(), reason="evolution_batch")`. Confirmed via grep that `DNACheckpointer.restore()`/`list_checkpoints()` have zero production callers — the class and its two test files (`tests/test_story_4_3.py::TestDNACheckpointer`, `tests/owls/test_dna_completion_drive.py`) are left untouched and still pass, per the story's explicit "do not delete" instruction. `dna_checkpoints` becomes write-dead going forward (superseded, not removed).
- **AC #2 (skill, additive-only) — DONE, exactly per the documented scope decision.** `record_skill_mutation` in `commands/skill_helpers.py` gets a NEW second write to `LearningArtifactStore.checkpoint("skill", skill_name, {...}, reason=op)` immediately after the existing `store.audit_write(...)` call. `skill_audit`, `SkillIndexStore.find_audit_by_hash`/`recent_audit_for_skill`, and `/skill restore`/`/skill diff` were NOT touched. The additive call is wrapped in try/except → `log.skills.warning(..., exc_info=exc)` on failure, matching `reflection_writer_handler._publish_to_lessons`'s best-effort pattern — verified with a dedicated failure-isolation test (`test_record_skill_mutation_learning_artifact_failure_does_not_abort`) that monkeypatches `LearningArtifactStore.checkpoint` to raise and confirms the primary mutation + audit row still succeed.
- **Dependency-picture deviation from the story text (worth flagging):** the story's Task 2 first subtask anticipated needing to "thread a `DbPool` through the function signature" if `record_skill_mutation` didn't already have one in scope. It doesn't need to — `SkillIndexStore` (the `store` param already passed to `record_skill_mutation`) exposes its `DbPool` at `store._db` (via `OwnedRepository.__init__`) and its owner id at the public `store.owner_id` property. Reused both directly (`LearningArtifactStore(store._db, store.owner_id)`) rather than threading a new parameter — this keeps the diff to exactly the two files the story's "Project Structure Notes" names (`evolution.py`, `skill_helpers.py`); threading a new param would have forced changes into `skill_manage.py` and `skill_command.py` (7 call sites) too, which the story's own file list rules out. `store._db` is a private-attribute reach across the module boundary — flagged with an inline `ponytail:` comment; `SLF001` isn't in this repo's enabled ruff rule set (`select = ["E","F","I","UP","B","SIM"]`) so it doesn't trip lint.
- **Pre-existing test failures found + fixed (unrelated to this story, per the "never skip pre-existing fails" rule):** while running the wider DNA regression pass, `tests/test_story_4_3.py::TestDeltaValidator::test_values_clamped_to_range` and `::TestDNAPromptInjector::test_high_curiosity_adds_clarifying_directive` were both already red on baseline `7b0d94d0` (confirmed via `git stash`/re-run before making any change). Root causes: (1) commit `47069e05` ("FR-1 — retune DNA evolution damping constants") intentionally widened the delta clamp band from ±0.1 to ±0.25 but never updated this test's expected values; (2) the F-53 charter change moved the curiosity trait's directive wording from "clarifying" language to "explore the problem broadly / exploration breadth" language, and this test was never updated. Both fixed to assert the current, intentional behavior (test renamed to `test_high_curiosity_adds_exploration_breadth_directive`).
- **No new migration, no new files** — `learning_artifacts` (migration 0087) already exists from Story 2.1.
- **Backlog note (per story instruction, NOT done here):** a full read-path cutover of `skill_audit`/`find_audit_by_hash` onto `LearningArtifactStore` (e.g. via an indexed hash column, or accepting a schema split) remains a follow-up item, intentionally out of this story's scope.

### File List

- `src/stackowl/owls/evolution.py` — modified (DNA checkpoint call site migrated to `LearningArtifactStore`)
- `src/stackowl/commands/skill_helpers.py` — modified (additive `LearningArtifactStore` checkpoint in `record_skill_mutation`)
- `tests/test_story_4_3.py` — modified (checkpoint-row assertion updated to `learning_artifacts`; 2 pre-existing stale-test failures fixed)
- `tests/tools/knowledge/test_provenance_chokepoint.py` — modified (2 new tests: additive learning-artifact write, failure-isolation)

## Change Log

- 2026-07-15: DNA checkpoint call site (`EvolutionCoordinator`) migrated from `DNACheckpointer` to `LearningArtifactStore` (AC #1). `record_skill_mutation` gets an additive, best-effort `LearningArtifactStore` checkpoint alongside its existing `skill_audit` write (AC #2). Two pre-existing, unrelated stale-test failures in `tests/test_story_4_3.py` fixed (DeltaValidator clamp band, DNAPromptInjector curiosity wording). tonyStyle scan run, no further defects found. Tests/ruff/mypy green on all touched files.
