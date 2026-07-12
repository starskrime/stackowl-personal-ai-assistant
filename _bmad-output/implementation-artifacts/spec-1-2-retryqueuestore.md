---
title: 'RetryQueueStore'
type: 'feature'
created: '2026-07-12'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
final_revision: 'a2375ee644ae3786f6dbda337f8df1bfa2466e53'
context: []
warnings: []
baseline_revision: '939a1a1a5ad7ec2e006208c2e1090075d98c97c8'
---

<intent-contract>

## Intent

**Problem:** The `retry_queue` table (migration 0082, Story 1.1) exists but nothing reads or writes it — no code layer wraps its CRUD, so later stories (insert-on-floor, backfill, sweep, manual retry) would each hand-roll raw SQL against it.

**Approach:** Add `RetryQueueStore`, an owner-scoped repository subclassing the existing `stackowl.tenancy.OwnedRepository` (mirroring the established shape in `stackowl/memory/outcome_store.py`), exposing the six operations later stories need: `insert_pending`, `backfill_channel_message`, `get_due`, `get_latest_pending_for_session`, `mark_completed`, `mark_attempt_failed`.

## Boundaries & Constraints

**Always:**
- Every query scopes to `self._owner_id` (owner-scoped repository invariant — no cross-owner read/write).
- `banned_capabilities` round-trips through the column as JSON text (`json.dumps`/`json.loads`), matching migration 0082's `TEXT NOT NULL DEFAULT '[]'` column.
- `mark_attempt_failed` increments `attempt_count`, appends the newly-failed capability (dedup — skip if already present), and caps status at `'failed'` once `attempt_count >= 3` (else re-arms `status='pending'` and `next_retry_at` = now + 1 minute).
- 4-point logging (entry/decision/step/exit) on every method per CLAUDE.md, using `log.memory`.
- `last_error` is truncated to 2000 chars before writing (unbounded exception text must not bloat the row).

**Block If:** none — scope and interfaces are fully fixed by the plan doc (`docs/superpowers/plans/2026-07-12-failure-retry-loop.md`, Task 2) and story 1.1's shipped schema.

**Never:**
- Do not modify migration 0082 or the `retry_queue` schema (fixed by Story 1.1, already `done`).
- Do not wire this store into `turn_persist.py`, the scheduler, or any caller — that's Stories 1.3–1.7, out of scope here.
- Do not add a `max_attempts` column, claimed-state, or trace_id uniqueness guard — those are deferred-work items tracked in `deferred-work.md` against the migration, not this store.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Insert then fetch due | `insert_pending(...)` with a past-due implicit `next_retry_at` (now) | `get_due()` returns the row with `status='pending'` and `banned_capabilities` deserialized to a list | No error expected |
| Backfill channel message | Existing pending row for `trace_id`, then `backfill_channel_message(trace_id=..., channel_chat_id=555, channel_message_id=999)` | `get_latest_pending_for_session` returns the row with `channel_chat_id=="555"`, `channel_message_id=="999"` (stringified) | No error expected |
| mark_attempt_failed below cap | Row at `attempt_count=0`, call once | Returns row with `status='pending'`, `attempt_count==1`, new capability appended to `banned_capabilities` | No error expected |
| mark_attempt_failed reaches cap | Row at `attempt_count=2`, call once more (3rd total) | Returns row with `status='failed'`, `attempt_count==3` | No error expected |
| mark_attempt_failed on missing row | `retry_id` not present in table | Raises `ValueError` | Caller (future stories) must catch/log — out of scope here |
| Duplicate banned capability | `newly_failed_capability` already in `banned_capabilities` | Capability is NOT duplicated in the list | No error expected |

</intent-contract>

## Code Map

- `src/stackowl/memory/retry_queue_store.py` -- NEW: `RetryQueueRow` frozen dataclass + `RetryQueueStore(OwnedRepository)` with the six methods.
- `src/stackowl/memory/outcome_store.py` -- reference: sibling Store subclassing `OwnedRepository`, same shape (raw `self._db.execute`/`fetch_all` with explicit `owner_id = ?` binding, dataclass row projection, 4-point logging) to mirror exactly.
- `src/stackowl/tenancy/owned_repository.py` -- base class: `__init__(db, owner_id)`, exposes `self._db`, `self._owner_id`; confirmed existing Stores do NOT use its `_insert_owned`/`_fetch_owned`/`_update_owned` helpers, they hand-roll SQL — follow that established convention, not the helper methods.
- `src/stackowl/tenancy/__init__.py` -- exposes `DEFAULT_PRINCIPAL_ID`, `OwnedRepository` (import path confirmed).
- `src/stackowl/db/pool.py` -- `DbPool`: confirmed `__init__(db_path: Path | None)`, `async def open()`, `async def close()`, `async def execute(sql, params)`, `async def fetch_all(sql, params) -> list[dict]`.
- `src/stackowl/db/migrations/0082_retry_queue.sql` -- shipped schema this store wraps (Story 1.1, `done`).
- `tests/memory/test_retry_queue_store.py` -- NEW test file.

## Tasks & Acceptance

**Execution:**
- [x] `src/stackowl/memory/retry_queue_store.py` -- create `RetryQueueRow` dataclass and `RetryQueueStore` with `insert_pending`, `backfill_channel_message`, `get_due`, `get_latest_pending_for_session`, `mark_completed`, `mark_attempt_failed` -- per plan doc Task 2 Step 3, adapted to add 4-point logging on every method (plan draft only logs entry/exit on some)
- [x] `tests/memory/test_retry_queue_store.py` -- unit tests covering every I/O matrix row above, against a real `DbPool` + the migration's actual schema (apply via `MigrationRunner` or inline `CREATE TABLE`, either is acceptable) -- proves the store against the real column set, not a hand-typed mock

**Acceptance Criteria:**
- Given a fresh `retry_queue` table, when `insert_pending` is called, then a row is created with `status='pending'`, `attempt_count=0`, and the returned id is a non-empty string.
- Given a pending row, when `get_due(limit=25)` is called, then the row is returned with `banned_capabilities` as a Python list (not a JSON string).
- Given a pending row for a `trace_id`, when `backfill_channel_message` is called, then `channel_chat_id`/`channel_message_id` are updated (stringified) on that row only.
- Given a row with `attempt_count` at 0, 1, 2, when `mark_attempt_failed` is called 3 times in sequence, then the row's `status` is `'pending'`, `'pending'`, `'failed'` respectively, and `attempt_count` ends at 3.
- Given a `retry_id` that doesn't exist, when `mark_attempt_failed` is called, then a `ValueError` is raised.

## Design Notes

Row projection follows `_row_to_model(row: dict) -> RetryQueueRow` (private module function), mirroring `_row_to_outcome` in `outcome_store.py`. `mark_attempt_failed` reads-then-writes (SELECT current row, compute next state, UPDATE, return the computed `RetryQueueRow`) rather than a single UPDATE-and-reselect, since the caller needs the post-update `banned_capabilities`/`status` immediately to decide whether to notify.

## Spec Change Log

## Review Triage Log

### 2026-07-12 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0high, medium 3medium, low 5low)
- defer: 3: (high 1high, medium 1medium, low 1low)
- reject: 3: (high 0high, medium 0medium, low 3low)
- addressed_findings:
  - `[medium]` `[patch]` `mark_attempt_failed` was a non-atomic SELECT-then-UPDATE — two overlapping calls for the same `retry_id` could both read the same `attempt_count` and silently lose an increment/banned-capability append. Wrapped the read-compute-write in one `DbPool.transaction()` unit (existing primitive already used by 5 other stores for exactly this). Added `test_mark_attempt_failed_concurrent_calls_do_not_lose_an_increment` as a regression test.
  - `[medium]` `[patch]` `mark_attempt_failed` never checked `current.status`, so repeat calls on an already-terminal row (`failed`/`completed`) kept incrementing `attempt_count` past `_MAX_ATTEMPTS` indefinitely. Added a guard raising `ValueError` when the row isn't `pending`. Added `test_mark_attempt_failed_on_already_failed_row_raises_value_error` and `test_mark_attempt_failed_on_completed_row_raises_value_error`.
  - `[medium]` `[patch]` `backfill_channel_message` matched on `trace_id` alone with no `LIMIT`, so a duplicate `trace_id` (schema has no UNIQUE constraint — deferred item) would stamp every matching pending row instead of one. Changed to select-then-update the single most-recent matching row by `id`, inside one transaction. Added `test_backfill_channel_message_duplicate_trace_id_stamps_only_one_row`.
  - `[low]` `[patch]` `mark_completed` and `backfill_channel_message` silently no-op'd when no row matched (wrong id, wrong owner, row already advanced) — no signal anything went wrong. Switched `mark_completed` to `execute_returning_rowcount` and logged a warning on zero rows affected; `backfill_channel_message`'s new select-then-update path logs a warning on a missing match. Added `test_mark_completed_on_missing_row_does_not_raise` and `test_backfill_channel_message_on_missing_row_does_not_raise`.
  - `[low]` `[patch]` `get_due(limit=...)` passed a non-validated `limit` straight into SQL `LIMIT` — a non-positive value is unbounded in SQLite, defeating the batch cap. Added a `ValueError` guard for `limit < 1`. Added `test_get_due_rejects_non_positive_limit`.
  - `[low]` `[patch]` `get_latest_pending_for_session` had no tiebreaker for `created_at` ties (same-timestamp concurrent inserts). Added `rowid DESC` as a deterministic secondary sort key (also applied to `backfill_channel_message`'s row-selection query for the same reason).
  - `[low]` `[patch]` `goal` was written unbounded while `last_error` was truncated — same free-text-column-must-not-bloat-the-row concern applies. Added `_GOAL_MAX_LEN = 4000` truncation in `insert_pending`. Added `test_insert_pending_truncates_goal_to_4000_chars`.
  - `[low]` `[patch]` `_row_to_model` called `json.loads` on `banned_capabilities` with no exception handling — a corrupted column value (schema has no `json_valid()` CHECK — deferred item) would raise a bare `JSONDecodeError` instead of a clear store-level error. Wrapped in try/except raising `ValueError` with context, logged via `log.memory.error`.



**Commands:**
- `uv run pytest tests/memory/test_retry_queue_store.py -v` -- expected: all tests pass
- `uv run ruff check src/stackowl/memory/retry_queue_store.py tests/memory/test_retry_queue_store.py` -- expected: no lint errors
- `uv run mypy src/stackowl/memory/retry_queue_store.py tests/memory/test_retry_queue_store.py` -- expected: no type errors

## Auto Run Result

**Summary:** Added `RetryQueueStore`, an owner-scoped repository (subclassing `OwnedRepository`, mirroring `outcome_store.py`'s shape) wrapping the `retry_queue` table (migration 0082, Story 1.1) with the six operations later retry-loop stories need: `insert_pending`, `backfill_channel_message`, `get_due`, `get_latest_pending_for_session`, `mark_completed`, `mark_attempt_failed`. Not wired into any caller — that's Stories 1.3-1.7.

**Files changed:**
- `src/stackowl/memory/retry_queue_store.py` (new) -- `RetryQueueRow` dataclass + `RetryQueueStore`, all six CRUD methods, 4-point logging throughout.
- `tests/memory/test_retry_queue_store.py` (new) -- 19 tests: original 11 covering the I/O matrix + 8 added during review to cover the patched behaviors (concurrency, terminal-row guard, duplicate-trace bound, no-op logging, limit validation, goal truncation).
- `_bmad-output/implementation-artifacts/deferred-work.md` -- 3 new entries (pre-existing schema gaps surfaced by this store: no claimed-state on `retry_queue`, no `trace_id` uniqueness, lexicographic ISO-8601 timestamp comparison bug).

**Review findings breakdown:** 8 patches applied (0 high, 3 medium, 5 low) -- all fixed in this pass, see Review Triage Log above. 3 deferred (1 high, 1 medium, 1 low) -- pre-existing schema gaps out of this story's scope, logged to `deferred-work.md`. 3 rejected (all low) -- an intentional silent-dedup behavior, a test-rigor nitpick, and the `OwnedRepository` raw-SQL-vs-helper-methods pattern (confirmed to match the established codebase convention across all sibling stores, not a defect).

**Verification performed:**
- `uv run pytest tests/memory/test_retry_queue_store.py -v` -- 19 passed (re-run after every patch).
- `uv run ruff check src/stackowl/memory/retry_queue_store.py tests/memory/test_retry_queue_store.py` -- clean.
- `uv run mypy src/stackowl/memory/retry_queue_store.py tests/memory/test_retry_queue_store.py` -- clean (strict mode).
- Broader `tests/memory/ tests/db/` run attempted for regression coverage but timed out (known pre-existing house issue on this box, per project convention: never run full/broad pytest here, targeted paths only) -- not a regression from this change.

**Residual risks:** The three deferred schema gaps (no claimed-state, no `trace_id` uniqueness, timestamp comparison edge case) remain live once Stories 1.5/1.6 wire a real scheduler sweep and actuator against this store -- those stories should re-read `deferred-work.md` before assuming single-worker, single-attempt semantics.
