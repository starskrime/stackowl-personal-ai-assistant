# Deferred Work Ledger

Append-only. Entries here are pre-existing issues surfaced incidentally during a story's review, not caused by that story, and not fixed in-line because fixing them would deviate from a spec's locked boundaries (e.g. a schema fixed by an upstream plan doc). Each entry should be verified against the current codebase before acting on it.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: `retry_queue` has no "in-flight/claimed" status, so two concurrent retry-worker instances (or a crash-recovering worker) can both pick up the same `pending` row and double-process it, duplicating a user-facing send.
  evidence: `status` CHECK only allows `pending`/`completed`/`failed`; nothing marks a row as claimed before a worker starts. This exact failure mode (duplicated/dropped proactive delivery) has bitten this codebase before per prior delivery-reliability work.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: No `max_attempts` column and undocumented `status` semantics — nothing in the schema stops a future worker from resetting `status` back to `pending` forever, and it's unclear whether `failed` is meant to be terminal.
  evidence: `attempt_count` is tracked but unbounded at the schema layer; the migration's header comment doesn't state `failed` is a terminal state.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: No uniqueness guard on `trace_id` — the same floored turn could be enqueued twice, compounding with the missing claimed-state above into duplicate retries.
  evidence: no `UNIQUE`/partial-unique index on `trace_id` in the schema; Story 1.3 (insert-on-floor) will be the first writer and should confirm this can't happen, or the schema should gain the guard.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: `channel TEXT NOT NULL DEFAULT 'telegram'` bakes in a single-channel assumption; a future INSERT path that omits `channel` would silently mislabel a Slack/Discord/WhatsApp floored turn as Telegram instead of failing loud.
  evidence: repo convention (per CLAUDE.md "no hidden errors") favors failing loud over silent defaults; this default hides a plausible caller bug.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: `next_retry_at`/`created_at`/`updated_at` are `TEXT` with no documented or enforced format, even though `next_retry_at` is the sort/filter key for `idx_retry_queue_status_due`, the retry worker's hot-path due-poll query.
  evidence: sibling migration `0045_durable_tasks.sql` documents its timestamp format explicitly in a comment; this migration doesn't, despite the format being more load-bearing here (incorrect lexical ordering would misorder or skip due retries).

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: `banned_capabilities` (JSON text) has no `json_valid()` CHECK guarding against malformed JSON reaching the column.
  evidence: SQLite's json1 extension is already available in this codebase; a one-line CHECK would catch a bad write at insert time instead of failing at read/deserialize time in Story 1.2's store.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: Schema captures `goal` but not the floored response text or which capabilities/tools were attempted beyond the banned list — may be insufficient context for Story 1.5's RetryActuator to safely resume a retry.
  evidence: the migration's own header comment states a floored turn intentionally never persists a `messages` row, making this table the *only* durable record of the failed attempt.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-retry-queue-migration.md`
  summary: No `CHECK(attempt_count >= 0)` guard on `attempt_count`.
  evidence: nothing in the schema prevents a future buggy UPDATE from decrementing it below zero and bypassing a future max-attempts cutoff.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-retryqueuestore.md`
  summary: `retry_queue` has no claimed/in-flight state, so `RetryQueueStore.get_due()` can return the same due row to two overlapping callers (two sweep worker instances, or a slow sweep tick overlapping the next), who then both retry the same floored turn and both call `mark_attempt_failed`/`mark_completed` on it — duplicating a user-facing send once Story 1.6's sweep is wired in.
  evidence: confirms and extends the equivalent story-1.1 entry above with the concrete manifestation point (`get_due`'s plain `SELECT ... WHERE status = 'pending'`, no claim step) that Story 1.6 (RetrySweepHandler) must address before going live.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-retryqueuestore.md`
  summary: No UNIQUE constraint on `trace_id` lets `insert_pending` be called twice for the same `trace_id`, creating two pending rows that `RetryQueueStore.backfill_channel_message`/`get_latest_pending_for_session` can only partially disambiguate (backfill now stamps just one, most-recent, row rather than both — mitigated at the store level this story — but two live pending rows for one floored turn is still possible and unintended).
  evidence: confirms and extends the equivalent story-1.1 entry above; Story 1.3 (insert-on-floor) should confirm a floored turn can never re-enter `insert_pending` with a `trace_id` already present, or the schema should gain the guard.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-retryqueuestore.md`
  summary: `RetryQueueStore` timestamps (`next_retry_at`, `created_at`, `updated_at`) are compared/sorted as TEXT via Python's `datetime.isoformat()`, which omits the fractional-seconds component entirely when `microsecond == 0` — two timestamps at the same wall-clock second can serialize as `"...T10:00:00+00:00"` vs `"...T10:00:00.500000+00:00"`, and lexicographically `'+' < '.'`, so the whole-second timestamp sorts *before* the fractional one regardless of true chronological order. `get_due`'s `next_retry_at <= ?` filter/sort inherits this.
  evidence: confirms and extends the equivalent story-1.1 entry (no documented/enforced timestamp format); low probability in practice (requires an exact-second coincidence) but directly affects `idx_retry_queue_status_due`, the retry worker's hot-path due-poll query once Story 1.6 wires in real polling.
