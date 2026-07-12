---
title: 'Retry Queue Migration'
type: 'feature'
created: '2026-07-12'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
final_revision: 'cc4a02a53fb8d413b4b49d70c7aa5a93ff29d1d1'
context: []
warnings: []
baseline_revision: 'd71fc1de96fb9956ac5319ebf9ec08ba6841d1c7'
---

<intent-contract>

## Intent

**Problem:** Floored turns ("I couldn't fully complete this") have no durable record, so nothing can track them for automatic retry. There is no `retry_queue` table yet.

**Approach:** Add a new idempotent SQLite migration `0082_retry_queue.sql` creating the `retry_queue` table plus its three indexes, exactly per the schema fixed in `docs/superpowers/plans/2026-07-12-failure-retry-loop.md` (Task 1, Step 1). This migration only creates schema — no application code reads/writes the table yet (that's Story 1.2+).

## Boundaries & Constraints

**Always:**
- Use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` — migration must be safe to re-run.
- Follow existing migration numbering: 4-digit zero-padded, next available is `0082`, filename `0082_retry_queue.sql`.
- `id` is `TEXT PRIMARY KEY` (app-generated UUID hex), matching how `RetryQueueStore` (future Story 1.2) will construct rows before insert — not `INTEGER PRIMARY KEY AUTOINCREMENT`.
- `owner_id TEXT NOT NULL` for multi-tenancy scoping, consistent with every other queue/store table in this codebase.
- No foreign key to `messages` — a floored turn intentionally never persists a `messages` row; the retry record stands alone, correlated by `trace_id`.
- Header comment on the migration file explains the root cause/why (mirroring `0073_undelivered_outbox.sql` / `0081_skills_fts.sql` style) and explicitly notes idempotency and the "no semicolons inside comments" runner constraint.
- Add a migration test at `tests/db/test_migration_0082.py` verifying the table exists after running `MigrationRunner`, plus a raw-SQL-executed-twice idempotency check (mirroring `tests/db/test_migration_0081_skills_fts.py`).

**Block If:** N/A — schema and file path are fully determined by the plan doc; no undetermined decision requires human input.

**Never:**
- Do not add application code that reads or writes `retry_queue` (RetryQueueStore, pipeline wiring) — that is Story 1.2 and later, out of scope here.
- Do not deviate from the plan doc's schema (e.g. do not use the older design-doc schema variant that omits `owner_id` or uses bare `CREATE TABLE`).
- Do not add a foreign key to `messages`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh DB | Migration runner applied to a DB without `retry_queue` | Table + 3 indexes created, `schema_migrations` row inserted for version 0082 | No error expected |
| Migration re-run via runner | Runner invoked again on a DB where 0082 is already recorded in `schema_migrations` | Runner skips (no-op), no error | No error expected |
| Raw SQL executed twice | Migration file's raw SQL text run twice directly against the same connection (bypassing runner's version-skip) | Second run is a no-op due to `IF NOT EXISTS` guards, no error, no duplicate table/index | No error expected |

</intent-contract>

## Code Map

- `src/stackowl/db/migrations/0082_retry_queue.sql` -- new migration file, creates `retry_queue` table + 3 indexes
- `src/stackowl/db/migrations/0081_skills_fts.sql` -- reference: most recent migration, header-comment style to mirror
- `src/stackowl/db/migrations/0073_undelivered_outbox.sql` -- reference: closest schema analog (queue table, `owner_id` scoping, same idempotency pattern)
- `src/stackowl/db/migrations/runner.py` -- `MigrationRunner`: discovers `*.sql` files by filename version prefix, applies unapplied versions inside `BEGIN EXCLUSIVE ... COMMIT`, records `schema_migrations` row
- `tests/db/test_migration_0081_skills_fts.py` -- reference: test pattern to mirror (runner-based table-exists check + raw-SQL-twice idempotency check)
- `tests/db/test_migration_0082.py` -- new test file for this migration

## Tasks & Acceptance

**Execution:**
- [x] `src/stackowl/db/migrations/0082_retry_queue.sql` -- create migration with header comment (root cause/why + idempotency + no-semicolons-in-comments note) and the exact schema below -- schema is fixed by the plan doc, only file needs authoring
- [x] `tests/db/test_migration_0082.py` -- add test: apply via `MigrationRunner`, assert `retry_queue` exists in `sqlite_master`; separately execute the migration's raw SQL text twice via `conn.executescript(...)`, assert no error and no duplicate rows in `sqlite_master` for the table/indexes -- proves both runner-level and SQL-level idempotency

Exact schema for `0082_retry_queue.sql`:
```sql
CREATE TABLE IF NOT EXISTS retry_queue (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    banned_capabilities TEXT NOT NULL DEFAULT '[]',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    next_retry_at TEXT NOT NULL,
    last_error TEXT,
    channel TEXT NOT NULL DEFAULT 'telegram',
    channel_chat_id TEXT,
    channel_message_id TEXT,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retry_queue_status_due ON retry_queue(status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_retry_queue_session ON retry_queue(owner_id, session_id, status);
CREATE INDEX IF NOT EXISTS idx_retry_queue_trace ON retry_queue(trace_id);
```

**Acceptance Criteria:**
- Given a fresh SQLite DB, when `MigrationRunner(db_path=...).run()` is called, then `retry_queue` exists in `sqlite_master` with all 14 columns and all 3 indexes exist.
- Given a DB where migration 0082 is already applied, when the runner is invoked again, then no error occurs and the table is not recreated or altered.
- Given the migration's raw SQL text, when executed twice in a row against the same connection, then no error occurs and no duplicate table/index rows appear in `sqlite_master`.

## Spec Change Log

## Review Triage Log

### 2026-07-12 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0high, medium 2medium, low 2low)
- defer: 8: (high 1high, medium 4medium, low 3low)
- reject: 4: (high 0high, medium 0medium, low 4low)
- addressed_findings:
  - `[medium]` `[patch]` Test asserted only column names via PRAGMA table_info, missing type/NOT NULL/DEFAULT — extended with an explicit constraint-tuple assertion covering id/owner_id/attempt_count/status/channel/banned_capabilities.
  - `[medium]` `[patch]` No test ever performed an INSERT, leaving defaults and the `status` CHECK constraint fully unverified — added `test_0082_insert_applies_defaults_and_enforces_status_check` (valid insert asserts default values; invalid status asserts `sqlite3.IntegrityError`).
  - `[low]` `[patch]` `assert len(_EXPECTED_COLUMNS) == 15  # noqa: PLR2004` was a tautology re-checking the test's own literal — removed.
  - `[low]` `[patch]` `next(r for r in results if r.version == "0082")` would raise an opaque `StopIteration` if the version were ever missing from results — switched to `next(..., None)` with an explicit assertion and diagnostic message.



**Commands:**
- `uv run pytest tests/db/test_migration_0082.py` -- expected: all tests pass
- `uv run ruff check src/stackowl/db/migrations/ tests/db/test_migration_0082.py` -- expected: no lint errors
- `uv run mypy tests/db/test_migration_0082.py` -- expected: no type errors (migration `.sql` file itself is not type-checked)

## Auto Run Result

**Summary:** Added idempotent SQLite migration `0082_retry_queue.sql` creating the `retry_queue` table (15 columns, 3 indexes) exactly per the plan doc's fixed schema, plus a migration test file covering table/index creation, runner-level skip-on-reapply, raw-SQL idempotency, and (added during review) constraint/default verification via direct INSERT. No application code reads/writes the table yet — that's Story 1.2+.

**Files changed:**
- `src/stackowl/db/migrations/0082_retry_queue.sql` -- new migration, `retry_queue` table + 3 indexes, idempotent
- `tests/db/test_migration_0082.py` -- new test file, 4 tests (table/index shape, constraints/defaults via INSERT, runner skip, raw-SQL idempotency)
- `_bmad-output/implementation-artifacts/epic-1-context.md` -- new, compiled Epic 1 context (planning artifact, cached for future stories in this epic)
- `_bmad-output/implementation-artifacts/deferred-work.md` -- new ledger, 8 schema-design findings deferred (see below)

**Review findings breakdown:**
- 4 patches applied (2 medium, 2 low) -- all test-quality gaps in the new test file, fixed in-line, re-verified green.
- 8 findings deferred to `deferred-work.md` -- all schema-design suggestions (claimed/in-flight status for concurrent workers, max-attempts cap, `trace_id` uniqueness, `channel` default safety, timestamp format enforcement, `banned_capabilities` JSON validity, richer failure context capture, `attempt_count` non-negativity). All are out of scope for this story because the spec's `Never` boundary locks the schema to the plan doc's exact definition — the plan doc, not this story, owns these decisions.
- 4 findings rejected as noise -- generic `CREATE ... IF NOT EXISTS` hypotheticals shared by every migration in the repo (not introduced by this diff), plus one over-engineered test ask (`EXPLAIN QUERY PLAN` assertion) with no existing convention.

**Verification performed:** `uv run pytest tests/db/test_migration_0082.py -v` -- 4 passed. `uv run ruff check tests/db/test_migration_0082.py` -- all checks passed. `uv run mypy tests/db/test_migration_0082.py` -- 1 pre-existing repo-wide `import-untyped` finding on `stackowl.db.migrations.runner` (identical to the reference test `test_migration_0081_skills_fts.py`, not introduced by this change).

**Residual risks:** None blocking this story. The 8 deferred schema-design items are real risks for the *retry loop as a whole* (most notably: no claimed-state means concurrent/crash-recovering workers could double-process a row) but do not affect this story's scope (schema creation only, no readers/writers yet) — they should be weighed before or during Story 1.2/1.5 when `RetryQueueStore`/`RetryActuator` are built.
