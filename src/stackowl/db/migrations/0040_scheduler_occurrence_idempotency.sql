-- Bug A fix migration. Scheduler idempotency keys are now scoped to the
-- serviced occurrence (idempotency_key plus next_run_at) instead of a static
-- run-once-ever key. Legacy job_runs rows were keyed on the static
-- idempotency_key, so every recurring job matched a completed row forever and
-- was skipped on every tick. Those rows are meaningless under the new scheme.
-- job_runs is a dedup/history table only (no other table references it), so
-- clearing it is safe and lets recurring jobs fire again. The migration runner
-- applies this once (tracked in schema_migrations).
DELETE FROM job_runs;
