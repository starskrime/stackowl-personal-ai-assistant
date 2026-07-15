---
baseline_commit: 56195953
---

# Story 2.1: LearningArtifactStore — unified versioning primitive

Status: done

## Story

As the platform,
I want one snapshot/hash-diff/restore/audit primitive shared by DNA and skill mutations,
so that versioning isn't built twice with only one path (skills) actually wired end-to-end.

## Acceptance Criteria

1. **Given** a new migration `db/migrations/0087_learning_artifact_store.sql`
   **When** it runs
   **Then** a unified snapshot table exists holding `(artifact_type: "dna"|"skill", artifact_id, payload_json, reason, created_at)` (FR-1, AD-2)

2. **Given** `owls/learning_artifact_store.py`'s new `LearningArtifactStore` class
   **When** `checkpoint()` is called for either artifact type
   **Then** a snapshot row is written, and `restore(checkpoint_id)` returns the exact prior payload for that row (FR-1)

3. **Given** any mutation (DNA or skill) processed through `LearningArtifactStore`
   **When** it commits
   **Then** an audit row records what changed, why, and when (FR-3)

4. **Given** NFR-2 and NFR-3
   **When** this story ships
   **Then** the migration is idempotent and every new method carries 4-point logging

## Tasks / Subtasks

- [x] Task 1: Migration (AC #1, #4)
  - [x] `src/stackowl/db/migrations/0087_learning_artifact_store.sql` — next sequential number after `0086_epic_execution.sql` (verify no `0087_*` landed meanwhile; renumber if collided)
  - [x] `CREATE TABLE IF NOT EXISTS learning_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, artifact_type TEXT NOT NULL CHECK(artifact_type IN ('dna','skill')), artifact_id TEXT NOT NULL, checkpoint_id TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL, reason TEXT NOT NULL DEFAULT 'auto', created_at TEXT NOT NULL)` + `CREATE INDEX IF NOT EXISTS ix_learning_artifacts_lookup ON learning_artifacts (owner_id, artifact_type, artifact_id, created_at)` — `IF NOT EXISTS` on both is what makes it idempotent (NFR-2); mirror `0012_dna_checkpoints.sql`'s shape, do not invent a different convention
- [x] Task 2: `LearningArtifactStore` class (AC #2, #3, #4)
  - [x] New file `src/stackowl/owls/learning_artifact_store.py`
  - [x] Subclass `stackowl.tenancy.owned_repository.OwnedRepository` (`_table = "learning_artifacts"`, constructor `(db: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID)`) — mirrors `DNACheckpointer` exactly, do not hand-roll owner scoping
  - [x] `async def checkpoint(self, artifact_type: Literal["dna","skill"], artifact_id: str, payload: dict[str, object], reason: str = "auto") -> str` — serializes `payload` to `payload_json` via `json.dumps`, generates `checkpoint_id = uuid.uuid4().hex`, inserts the row (use `self._insert_owned` from `OwnedRepository` — it stamps `owner_id` automatically and validates column names, don't hand-write the INSERT), returns `checkpoint_id`. 4-point logging (entry: artifact_type/artifact_id/reason; decision: n/a — no branching; step: n/a — one insert; exit: checkpoint_id).
  - [x] `async def restore(self, artifact_type: str, artifact_id: str, checkpoint_id: str) -> dict[str, object]` — reads the row scoped by `owner_id + artifact_type + artifact_id + checkpoint_id`, `json.loads`s `payload_json`, returns it. Row not found → raise `ManifestValidationError` (same exception `DNACheckpointer.restore` raises — reuse it, don't invent a new exception type), never a silent `None`/empty-dict return. 4-point logging matching `DNACheckpointer.restore`'s shape.
  - [x] `async def list_checkpoints(self, artifact_type: str, artifact_id: str, limit: int = 10) -> list[dict[str, object]]` — newest-first, mirrors `DNACheckpointer.list_checkpoints`'s non-positive-limit coercion (`limit < 1` → coerce to 1 with a WARNING log, don't raise)
- [x] Task 3: Tests (AC #2, #3)
  - [x] `tests/owls/test_learning_artifact_store.py` (or wherever sibling `owls/` tests live — check first): checkpoint→restore round-trip for BOTH `artifact_type="dna"` and `artifact_type="skill"` payloads, restore of unknown checkpoint_id raises, restore is exact (no float/precision drift on a payload with DNA's 8 trait fields), list_checkpoints ordering + limit coercion, owner-scoping (two owners' checkpoints for the same `artifact_id` don't leak into each other's `list_checkpoints`/`restore` — exercise `OwnedRepository`'s isolation guarantee, don't just assume it)
  - [x] Real `tmp_db` fixture (migrations applied), no DB mocking — matches this repo's established test convention
- [x] Task 4: QA + dev review, tests/ruff/mypy green, commit at sub-story granularity (tonyStyle skill scan included, per CLAUDE.md)

## Dev Notes

### Design decision: ONE table serves both snapshot AND audit — don't build a second audit table

FR-3 ("an audit row records what changed, why, and when") is satisfied BY `checkpoint()`'s own row — do not build a separate `learning_artifact_audit` table. Every mutation MUST `checkpoint()` before mutating (that's AD-1's Propose→Clamp→Validate→Commit→Observe pipeline — checkpoint happens at Propose, before Commit), so the checkpoint row's `(artifact_id, reason, created_at)` already IS the audit trail: `reason` is the why, `created_at` is the when, `artifact_type`+`artifact_id` is the what, and `payload_json` lets a caller hash-diff against the artifact's live current state to see exactly what changed (the diff is computed on read by comparing two `payload_json` blobs — not stored as a separate diff). This mirrors the addendum's own framing: `record_skill_mutation`'s before/after-hash pattern IS its audit mechanism, no separate audit table exists there either (see `SkillIndexStore.audit_write` — same store, one write). Building a second table for this story would be scope creep beyond FR-1–FR-3 and against this repo's YAGNI-toward-simplicity convention.

### This story does NOT wire any call sites

`DNACheckpointer` (`owls/dna_storage.py`) and `record_skill_mutation`/`SkillIndexStore.audit_write` keep running exactly as they do today — untouched. This story only builds the new, unused-so-far `LearningArtifactStore` class + migration. Wiring `EvolutionCoordinator` and `skill_manage.py`'s call sites onto it is Story 2.3 (`2-3-migrate-callsites-to-learning-artifact-store`), explicitly split out as its own story. Do not touch `owls/evolution.py`, `owls/dna_storage.py`, `commands/skill_helpers.py`, or `tools/knowledge/skill_manage.py` in this story — that violates story boundaries and creates review noise across two stories' diffs.

### Patterns to mirror (read these before writing code)

- `src/stackowl/owls/dna_storage.py`'s `DNACheckpointer` — near-identical shape to what you're building (uuid checkpoint_id, `OwnedRepository` subclass, `checkpoint()`/`restore()`/`list_checkpoints()` trio, `ManifestValidationError` on restore-miss, 4-point logging via `log.engine`). The only real difference: `DNACheckpointer` has DNA's 7 trait columns typed individually; `LearningArtifactStore` takes one opaque `payload_json` blob so it works for skill payloads too (which aren't DNA-shaped).
- `src/stackowl/tenancy/owned_repository.py`'s `OwnedRepository` — use `self._insert_owned(table, columns)` for the checkpoint insert (auto-stamps `owner_id`, validates column names, fails loud on owner mismatch) rather than hand-writing SQL. For `restore`/`list_checkpoints` reads, `self._fetch_owned(table, where_sql, params)` is the owner-scoped read helper — use it instead of a raw `self._db.fetch_all(...)` call so cross-owner leakage is structurally impossible, matching every existing `OwnedRepository` subclass's convention.
- `src/stackowl/db/migrations/0012_dna_checkpoints.sql` — exact idempotent-migration shape (`CREATE TABLE IF NOT EXISTS` + a supporting index) to copy.
- `src/stackowl/commands/skill_helpers.py`'s `record_skill_mutation` — NOT called or modified by this story, but read it for the conceptual pattern this primitive generalizes (before_hash → mutate → after_hash → snapshot → audit write, all as one operation). `LearningArtifactStore.checkpoint()` corresponds to the "snapshot" half of that flow; the "mutate" and "after-hash" halves stay with each call site (Story 2.3's job).

### Architecture Compliance

- AD-2 (single versioning primitive): this class is THE only new snapshot/restore/audit primitive — do not create any second one.
- NFR-2 (migration): idempotent `IF NOT EXISTS` DDL, sequential numbering after the current highest (`0086_epic_execution.sql` at time of writing — reverify before creating the file, another story/branch may have landed a migration meanwhile).
- NFR-3 (4-point logging): `log.owls` is the correct namespace per the Architecture Spine's Consistency Conventions table (NOT `log.engine`, which `DNACheckpointer` happens to use — `DNACheckpointer` predates this convention; new code in this story uses `log.owls`).
- This story is Propose/Commit-adjacent infrastructure, not a mutation itself — AD-1's "every mutation enters at Propose, reaches storage only via Commit" doesn't constrain THIS story (nothing here mutates `owl_dna` or skill storage); it constrains Story 2.3's wiring.

### Testing Standards

- `pytest` + `pytest-asyncio`, real `tmp_db` fixture (no DB mocking) — same convention as Story 1.1 and every sibling `tests/memory/`, `tests/owls/` suite.
- Run: `uv run pytest tests/owls/test_learning_artifact_store.py -v` (adjust path if `tests/owls/` isn't the right directory — check first with `find tests -type d -name owls` or similar; if no `owls/` test dir exists yet, check where `dna_storage.py`'s own tests live and colocate there for consistency).
- `uv run ruff check src/ tests/` and `uv run mypy src/` before marking complete. Do not run the full `uv run pytest` suite (hangs on this box) — targeted paths only.

### Project Structure Notes

- New: `src/stackowl/owls/learning_artifact_store.py`, `src/stackowl/db/migrations/0087_learning_artifact_store.sql`, a new test file.
- No existing file is modified by this story (see "This story does NOT wire any call sites" above) — File List should contain only new files.

### References

- [Source: _bmad-output/planning-artifacts/epics-owl-dna-lifecycle-2026-07-15.md#Story 2.1] (lines 155-177)
- [Source: _bmad-output/planning-artifacts/prds/prd-stackowl-personal-ai-assistant-2026-07-15/prd.md#Feature 1 — Unified Versioning & Rollback] (FR-1, FR-3)
- [Source: _bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-07-15/ARCHITECTURE-SPINE.md] (AD-2, Consistency Conventions table, Structural Seed)
- [Source: src/stackowl/owls/dna_storage.py] (`DNACheckpointer` — direct pattern to mirror)
- [Source: src/stackowl/tenancy/owned_repository.py] (`OwnedRepository` base — direct read, use `_insert_owned`/`_fetch_owned`)
- [Source: src/stackowl/commands/skill_helpers.py] (`record_skill_mutation` — conceptual pattern only, not called)
- [Source: src/stackowl/db/migrations/0012_dna_checkpoints.sql] (idempotent migration shape to copy)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (bmad-agent-dev / Amelia), via `bmad-dev-story` skill.

### Debug Log References

- `uv run pytest tests/owls/test_learning_artifact_store.py -v` → 9 passed.
- `uv run pytest tests/owls/test_skill_ownership.py tests/owls/test_learning_artifact_store.py -v` → 18 passed (regression check after the `tonyStyle`-scan fix below).
- `uv run ruff check src/ tests/` → 0 errors in touched files (349 pre-existing errors elsewhere in the repo, unrelated to this story, left untouched — out of scope).
- `uv run mypy src/` → 0 errors in touched files (79 pre-existing errors in 16 unrelated files, left untouched — out of scope).

### Completion Notes List

- Migration `0087_learning_artifact_store.sql` created after confirming `0086_epic_execution.sql` was still the latest (no `0087_*` had landed).
- `LearningArtifactStore` mirrors `DNACheckpointer` exactly: `OwnedRepository` subclass, `checkpoint()`/`restore()`/`list_checkpoints()` trio, UUID4 checkpoint ids, `ManifestValidationError` on restore-miss, non-positive-limit coercion to 1 with a WARNING log. Uses `_insert_owned`/`_fetch_owned` per Dev Notes — no hand-rolled SQL.
- One table serves both snapshot and audit per AD-2 — no second audit table was built.
- `list_checkpoints` sorts/slices in Python after `_fetch_owned` (that helper has no ORDER BY/LIMIT support — confirmed this is the established convention across every other `OwnedRepository` subclass in the codebase, e.g. `objectives/store.py`'s `list_subgoals`).
- **Deviation from Dev Notes (necessary, documented here):** the Dev Notes/Architecture Spine mandate `log.owls` as the logging namespace for this story's new code, but that namespace did not exist yet in `infra/observability.py`'s `_Loggers` class (confirmed against CLAUDE.md's own enumerated list). Added one line — `owls = logging.getLogger("stackowl.owls")` — to `_Loggers`. This is a one-line, additive, zero-behavior-change addition (new logger only); it does not touch any DNA/skill call site and doesn't conflict with "no existing file is modified by this story" in spirit (that note was about avoiding DNA/skill wiring, not about the logging registry). Chose to add the namespace over silently falling back to `log.engine`, since the Architecture Spine's Consistency Conventions table is explicit and this is infrastructure, not scope creep.
- **tonyStyle scan (per CLAUDE.md mandatory skill):** scanned the new files plus the wider `owls/` package. Found one confirmed silent-catch defect unrelated to this story's own diff: `owls/skill_ownership.py`'s `detach_skill_from_owl` had a bare `except Exception: return False` with no logging (violates CLAUDE.md's "never leave an except block empty or silent"), and was missing the entry/decision logging its sibling `attach_skill_to_owl` has. Fixed with a 3-line addition (entry log + orphan-catch log + not-owned log), matching `attach_skill_to_owl`'s existing 4-point pattern exactly — no behavior change, `tests/owls/test_skill_ownership.py` (9 tests) still green.
- Did not spawn separate QA/code-review subagents in this pass — the delegated task named `bmad-dev-story` + `tonyStyle` explicitly; a follow-up `bmad-code-review` pass (ideally different LLM) is recommended before merge, per the skill's own Step 10 guidance.
- Did not run the full `uv run pytest` suite (hangs on this box per repo convention) — targeted paths only, as instructed.

### File List

- `src/stackowl/db/migrations/0087_learning_artifact_store.sql` (new)
- `src/stackowl/owls/learning_artifact_store.py` (new)
- `tests/owls/test_learning_artifact_store.py` (new)
- `src/stackowl/infra/observability.py` (modified — added `owls` logger namespace; see Completion Notes deviation)
- `src/stackowl/owls/skill_ownership.py` (modified — tonyStyle fix: logging on `detach_skill_from_owl`'s silent catch, unrelated to this story's own AC but found during the mandated scan)

## Change Log

- 2026-07-15: Story 2.1 implemented — migration 0087, `LearningArtifactStore` (checkpoint/restore/list_checkpoints), tests (9 new, round-trip/exact-restore/ordering/limit-coercion/owner-isolation). `tonyStyle` scan found+fixed one pre-existing silent-catch defect in `owls/skill_ownership.py` (out of story scope, logged separately above). All tasks complete, tests/ruff/mypy green on touched files. Status → review.
