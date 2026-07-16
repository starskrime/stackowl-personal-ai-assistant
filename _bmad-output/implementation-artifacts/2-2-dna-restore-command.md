---
baseline_commit: 782ece2c
---

# Story 2.2: DNA restore command

Status: done

## Story

As the operator,
I want to restore an owl's DNA to any specific prior checkpoint, not only the authored baseline,
so that a bad evolution cycle is fully and precisely reversible.

## Acceptance Criteria

1. **Given** Story 2.1's `LearningArtifactStore`
   **When** the operator runs `/owls dna-restore <name> <checkpoint_id> YES` (new subcommand in `commands/owls_command.py`, mirroring the existing `reset-dna` handler's confirm-with-YES UX)
   **Then** that owl's live DNA is restored to exactly the trait values in that checkpoint (FR-2)
   **And** the existing `/owls reset-dna` (restore-to-authored-baseline) command is untouched and still works (NFR-1)

2. **Given** an invalid or unknown `checkpoint_id`
   **When** the command is run
   **Then** it fails loudly with a clear error — no silent no-op

## Tasks / Subtasks

- [x] Task 1: `_dna_restore` handler on `OwlsCommand` (AC #1, #2)
  - [x] New method `async def _dna_restore(self, rest: str) -> str` on `OwlsCommand` in `src/stackowl/commands/owls_command.py`, placed right after `_reset_dna` (mirrors its shape almost exactly — see Dev Notes)
  - [x] Parse `rest` as `<name> <checkpoint_id> [YES]` (three whitespace-separated tokens; `checkpoint_id` is a UUID4 hex string, no spaces, so plain `.split()` is sufficient — do not reach for `shlex` here, `reset-dna`'s two-token parse doesn't either)
  - [x] Missing name/checkpoint_id → return a `Usage: /owls dna-restore <name> <checkpoint_id> YES` string (same style as `reset-dna`'s usage line), not an exception
  - [x] `self._registry.get(name)` first (raises `OwlNotFoundError` on miss, caught by `handle()`'s existing dispatch — don't re-catch it here, `reset-dna` doesn't either)
  - [x] No `YES` token → same confirm-prompt UX as `reset-dna`: `f"⚠ This restores owl '{name}' DNA to checkpoint '{checkpoint_id}' (current evolution discarded).\n   Type: /owls dna-restore {name} {checkpoint_id} YES to confirm."`
  - [x] `self._db is None` → `"DNA store unavailable."` (matches `reset-dna`'s guard)
  - [x] Confirmed: construct `LearningArtifactStore(self._db)`, call `await store.restore("dna", name, checkpoint_id)` → returns the payload dict → `OwlDNA.model_validate(payload)` (used `model_validate` instead of `OwlDNA(**payload)` — mypy strict rejects `**dict[str, object]` against typed float kwargs; `model_validate` is the same pattern `_edit` already uses in this file for a dict→model rebuild). For THIS story's own tests, checkpoints are written via `store.checkpoint("dna", name, dna.model_dump(), reason=...)` directly, matching the shape Story 2.3 will produce.
  - [x] `ManifestValidationError` on an unknown `checkpoint_id` is raised BY `LearningArtifactStore.restore()` itself and propagates unmodified — `handle()`'s existing `except (ManifestValidationError, OwlNotFoundError)` block already turns it into `"✗ /owls dna-restore: {exc}"` (AC #2). No redundant try/except added.
  - [x] On success: same persist+refresh sequence as `reset-dna` — `await upsert_owl_dna(self._db, name, restored_dna, table="owl_dna")`, `apply_dna_overlay(self._registry, name, restored_dna)`, `DIRECTIVE_LATCH.reset_owl(name)`
  - [x] Return `f"✓ Owl '{name}' DNA restored to checkpoint '{checkpoint_id}'."`
  - [x] 4-point logging matching `_reset_dna`'s existing shape (entry/decision-awaiting-confirm/exit), `log.gateway` namespace
- [x] Task 2: Wire routing + metadata in BOTH `OwlsCommand` and `OwlCommand` (AC #1)
  - [x] `_OWLS_META`'s `subcommands` tuple: added `dna-restore` `SubCommand` right after the existing `reset-dna` entry
  - [x] `OwlsCommand.handle()`: added `elif sub == "dna-restore": result = await self._dna_restore(rest)` right after `elif sub == "reset-dna":`
  - [x] `_OWL_META`'s `subcommands` tuple: same new `SubCommand` entry, right after ITS `reset-dna` entry (separate tuple, added independently)
  - [x] `OwlCommand.handle()`: added `if sub == "dna-restore": return await self._dna_restore(rest)` right after ITS `if sub == "reset-dna":` line, matching that method's `if`/`return` style (not homogenized with `OwlsCommand.handle()`'s `elif`/`result=`)
- [x] Task 3: Tests (AC #1, #2, NFR-1)
  - [x] Extended `tests/commands/test_owls_reset_dna.py` (where `_reset_dna` is already tested) rather than creating a new file — shares the `_cmd`/`_state` fixtures
  - [x] Happy path: `test_dna_restore_reverts_to_checkpoint_and_live_refreshes` — checkpoint via `LearningArtifactStore.checkpoint`, mutate live DNA, `dna-restore ... YES`, assert both registry and `owl_dna` table match the checkpointed value (0.3), not the mutated one (0.9)
  - [x] Unconfirmed: `test_dna_restore_unconfirmed_leaves_dna_unchanged` — confirm-prompt returned, live DNA (registry + DB) unchanged
  - [x] Unknown `checkpoint_id`: `test_dna_restore_unknown_checkpoint_fails_loud` — asserts `"✗ /owls dna-restore:"` prefix, live DNA unchanged
  - [x] Also added `test_dna_restore_requires_confirm` (no-YES prompt shape)
  - [x] Regression (NFR-1): all 5 pre-existing `reset-dna`/`dna` tests in the same file still pass unmodified
- [x] Task 4: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### This is a "dry run" story — Story 2.3 hasn't wired real checkpoint writes yet

At the time this story ships, `EvolutionCoordinator` (the nightly batch) is STILL calling `DNACheckpointer.checkpoint()` (the OLD table, `dna_checkpoints`) — Story 2.3 (`2-3-migrate-callsites-to-learning-artifact-store`, the NEXT story) is what switches it onto `LearningArtifactStore`. So `/owls dna-restore` will have **no real checkpoints to restore from in production** until Story 2.3 lands — this story builds and tests the command against `LearningArtifactStore` directly (you write test checkpoints yourself via `store.checkpoint("dna", ...)`), and the command becomes reachable end-to-end the moment Story 2.3 ships. This mirrors the PRD Background's own framing of the ORIGINAL bug ("checkpoint() called; restore() has no caller") — don't be surprised that this story's command is temporarily "half-wired" from the production-data side; that's the epics doc's own explicit sequencing (2.2 before 2.3), not a mistake to fix here.

### Pattern to mirror — read `_reset_dna` first (`owls_command.py:441-484`)

`_reset_dna` is copied almost verbatim in shape:
1. Registry guard → `_NO_REGISTRY`
2. Parse tokens, usage-string on empty
3. `self._registry.get(name)` (raises `OwlNotFoundError`, let it propagate)
4. YES-confirm gate → prompt string if absent
5. `self._db is None` guard
6. Do the restore
7. Persist (`upsert_owl_dna`) + live-refresh (`apply_dna_overlay`) + `DIRECTIVE_LATCH.reset_owl(name)`
8. Success string

The ONLY structural difference: `_reset_dna` sources its replacement DNA from `read_authored_dna(self._db, name)`; `_dna_restore` sources it from `LearningArtifactStore(self._db).restore("dna", name, checkpoint_id)`. Everything else — the persist/refresh tail — is identical. Do not duplicate that tail's logic; call the same three functions (`upsert_owl_dna`, `apply_dna_overlay`, `DIRECTIVE_LATCH.reset_owl`) `_reset_dna` already calls, imported the same way (local imports inside the method, matching `_reset_dna`'s existing style — not module-level, that's this file's established convention for these particular imports, likely to avoid an import cycle).

### `/owls` vs `/owl` — TWO metadata blocks, TWO routing methods

`OwlCommand` (the live `/owl` command) subclasses `OwlsCommand` (the base, registered as `/owls`) and REUSES the inherited `_reset_dna`/`_dna` handler methods, but defines its own `_OWL_META` (separate `CommandMeta`, separate subcommand list) and its own `handle()` override (different dispatch style: `if/return` chain, not `elif/result=`). A new `_dna_restore` method belongs on the BASE `OwlsCommand` class (inherited by both), but the SubCommand metadata entry and the routing line must be added in BOTH places — `_OWLS_META` + `OwlsCommand.handle()`, AND `_OWL_META` + `OwlCommand.handle()` — or `/owl dna-restore` will 404 with "unknown subcommand" while `/owls dna-restore` works. This exact double-registration is why `reset-dna` appears twice in the file (lines ~102-110 and ~770-778 for metadata; ~210-211 and ~862-863 for routing) — follow that precedent precisely.

### Architecture Compliance

- AD-1 (no side doors): `_dna_restore` reaches storage ONLY via `LearningArtifactStore.restore()` (read) then the existing `upsert_owl_dna` (write) — it does not touch `owl_dna`/`dna_checkpoints` tables directly with hand-written SQL.
- AD-2: uses `LearningArtifactStore` (Story 2.1), not a second restore mechanism.
- NFR-1: `reset-dna` must be byte-identical in behavior after this story — it isn't touched at all except possibly nothing (verify with its existing tests).

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db`, no mocking — established convention.
- Run the specific test file/path (find it first — do not assume a name) plus `tests/owls/test_learning_artifact_store.py` (regression) before the full suite. Do not run the full `uv run pytest` (hangs on this box).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete.

### Project Structure Notes

- Modified: `src/stackowl/commands/owls_command.py` only (new method + two metadata additions + two routing lines). No new files, no migration (Story 2.1 already created the table this story reads from).

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.2] (lines 179-195)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 1] (FR-2)
- [Source: _bmad-output/implementation-artifacts/2-1-learning-artifact-store.md] (Story 2.1 — `LearningArtifactStore.restore()` signature/behavior this story calls)
- [Source: src/stackowl/commands/owls_command.py] (direct read — `_reset_dna`, `_OWLS_META`/`_OWL_META`, both `handle()` methods)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (Amelia, bmad-dev-story)

### Debug Log References

- `uv run pytest tests/commands/test_owls_reset_dna.py -v` — 9/9 passed (5 pre-existing reset-dna/dna + 4 new dna-restore)
- `uv run pytest tests/commands/test_owls_meta.py -v` — 4/4 passed (updated `_EXPECTED` set)
- `uv run pytest tests/journeys/test_dna_completion_journey.py -v` — 1/1 passed (fixed pre-existing threshold drift, see notes)
- `uv run pytest tests/owls/test_learning_artifact_store.py tests/journeys/commands/test_owls_command.py -q` — all passed
- `uv run ruff check src/ tests/` — 0 errors in touched files (349 pre-existing repo-wide, untouched files)
- `uv run mypy src/` — 0 errors in `owls_command.py` (79 pre-existing repo-wide, untouched files)

### Completion Notes List

- Implemented `_dna_restore` on `OwlsCommand`, mirroring `_reset_dna`'s structure exactly per Dev Notes (registry guard → parse → YES-confirm → db guard → restore via `LearningArtifactStore` → persist/refresh tail → success string).
- Deviation: used `OwlDNA.model_validate(payload)` instead of the Dev-Notes-suggested `OwlDNA(**payload)` — `payload` is typed `dict[str, object]` from `LearningArtifactStore.restore()`, and `**dict[str, object]` fails mypy strict against `OwlDNA`'s typed float fields. `model_validate` is the exact pattern `_edit` already uses elsewhere in this same file for dict→model reconstruction, so no new convention introduced.
- Wired routing + `SubCommand` metadata in both `_OWLS_META`/`OwlsCommand.handle()` and `_OWL_META`/`OwlCommand.handle()` per the double-registration precedent.
- Extended `tests/commands/test_owls_reset_dna.py` (not a new file) with 4 new tests; all 5 pre-existing tests in that file still pass unmodified (NFR-1).
- tonyStyle wide-codebase scan (via the broader regression run named in Dev Notes) surfaced two **pre-existing, unrelated** test failures, both confirmed unaffected by this story's diff (isolated single-file reruns fail identically without any dna-restore code in play):
  1. **Fixed** — `tests/journeys/test_dna_completion_journey.py::test_dna_completion_full_loop`: hardcoded hysteresis-latch thresholds (0.70/0.60) were stale against `directive_latch.py`'s `HIGH_ENTER=0.62`/`HIGH_EXIT=0.55`, retuned by commit `47069e05` ("FR-1 — retune DNA evolution damping constants") without updating this test's assertions. Updated the enter/hold/exit trait values and one stale comment to match the current constants — no production code changed.
  2. **Not fixed, flagged for follow-up** — `tests/test_owls_command_registration.py::test_owls_command_registered_list_and_health`: calls `Settings()` unmocked, so it reads this box's real `stackowl.yaml` instead of an isolated config. On this dev box the user has renamed the secretary owl's `display_name` to "Mary", so `/owls list`'s roster (which renders `display`, not `name`) no longer contains the literal string "secretary". This is a genuine test-isolation gap (Settings not fixture-isolated), unrelated to DNA/owls_command logic, and out of this story's single-file scope (`owls_command.py` only per Dev Notes) — recommend a dedicated fixture (e.g. `STACKOWL_CONFIG_FILE` monkeypatched to a temp file) in a follow-up, not scoped here.

### File List

- `src/stackowl/commands/owls_command.py` — new `_dna_restore` method + `dna-restore` `SubCommand` metadata and routing in both `OwlsCommand`/`_OWLS_META` and `OwlCommand`/`_OWL_META`
- `tests/commands/test_owls_reset_dna.py` — 4 new tests for `dna-restore` (confirm gate, happy path, unconfirmed-no-op, unknown-checkpoint error)
- `tests/commands/test_owls_meta.py` — added `"dna-restore"` to `_EXPECTED` subcommand set
- `tests/journeys/test_dna_completion_journey.py` — fixed pre-existing stale hysteresis-latch threshold values/comments (unrelated pre-existing bug, root-caused and fixed per CLAUDE.md's no-skipping-pre-existing-failures rule)

## Change Log

- 2026-07-15: Story 2.2 implemented — `/owls dna-restore` and `/owl dna-restore` (new `_dna_restore` handler + double-registered metadata/routing), 4 new tests, all `reset-dna` regression tests pass unmodified (NFR-1). tonyStyle scan found+fixed one pre-existing stale-threshold test bug in `test_dna_completion_journey.py` (unrelated subsystem, root-caused to commit `47069e05`); flagged (not fixed, out of scope) a pre-existing `Settings()` test-isolation gap in `test_owls_command_registration.py`. All tasks complete, tests/ruff/mypy green on touched files. Status → review.
