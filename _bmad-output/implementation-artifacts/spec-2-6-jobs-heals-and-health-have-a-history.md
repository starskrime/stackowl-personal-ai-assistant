---
title: 'Jobs, heals and health have a history'
type: 'feature'
created: '2026-09-18'
status: 'done'
baseline_revision: 'c3ba0cf547de4b6dc167de52fc806825459af022'
review_loop_iteration: 1
followup_review_recommended: false
context:
  - '_bmad-output/implementation-artifacts/epic-2-context.md'
  - '_bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-09-12/ARCHITECTURE-SPINE.md'
warnings: ['oversized']
deferred: []
---

<intent-contract>

## Intent

**Problem:** Scheduler job runs, self-healing attempts and health-status transitions exist only as ephemeral logs and a narrow in-memory alert-dedup map — the journal (2.1/2.2) has no `job.*`, `heal.*` or `health.changed` event types, so none of this history is durable or queryable.

**Approach:** Register `job.started/finished/failed/parked`, `heal.attempted/healed/exhausted`, `health.changed` in the journal registry (new `RecordKind.JOB/HEAL/HEALTH`). Wrap scheduler mutation call sites (currently auto-committing single statements) in `DbPool.transaction()` so each state change and its `journal.record()` commit together. Add two new small domain tables (`heal_attempts`, `health_status_changes`) via migration as the `record_ref` target for heal/health events (jobs already have `jobs`/`job_runs`). Build the missing previous-vs-new status comparison `health_sweep.py` needs for FR84, sourced from `health_status_changes`'s own last row per subsystem (no new in-memory cache). Add a provisional `journal/retention.py` constant plus a tripwire test proving no task/job/job-run prune window undercuts it.

## Boundaries & Constraints

**Always:** Every new `journal.record()` call runs inside the SAME `DbPool.transaction()` block as the state change it records (AD-24) — refactor the auto-committing scheduler sites, do not add a second, separate write. `job.parked` and `heal.exhausted` are `NEEDS_YOU`/`HIGH` (AD-5's named examples); every other new type is `AMBIENT`. `health.changed`'s `attrs` carry `subsystem`, `previous_status`, `new_status`, an `error_code` from a NEW closed enum (`journal/enums.py`) — never `str(exc)` or any exception text (FR84). `heal.*`/`health.changed` `target_id` is the subsystem's bounded label (identity-resolvable, no DB lookup needed for narration); their `record_ref` points at the new `heal_attempts`/`health_status_changes` row. `job.*` `record_ref` points at the `jobs` row (`{table:"jobs", job_id}`) — present at every one of the four recording call sites, even for a one-shot job whose row is deleted moments later by the SAME code path (AD-4's disclosed "target already gone -> `expired`" contract covers that, not a defect). `health.changed` fires only on an ACTUAL transition (a subsystem with no prior row in `health_status_changes` records nothing on first observation — avoids false "changed from nothing" noise). `heal.exhausted` fires only for a subsystem where `ensure_available()` was attempted AND is still unhealthy after the sweep's own re-verify (`attempted - healed`, a currently-missing branch in `health_sweep.py`'s `execute()`). Recurring jobs never park (owner decision, F-60, no circuit breaker) — `job.parked` fires only from the one-shot terminal-failure branch; do not invent a parked state for recurring jobs. Add `RecordKind.JOB`/`HEAL`/`HEALTH` (additive, AD-3) and a `NameResolver` per new kind (mirrors `pipeline/durable/journal_names.py`).

**Never:** Do not change `_RUN_HISTORY_RETENTION_DAYS` (db_reclaim.py, 7 days) or `prune_completed_after_days` (task_loop_settings.py, 1 day) — both are real, owner-authorized production values; this story does not own or widen them. Do not build the real journal retention *setting* or a prune job for `journal_events` itself — that is Story 2.11/2.12's scope; this story's `journal/retention.py` constant is explicitly provisional (see Design Notes) and used ONLY by this story's own tripwire. Do not build a stateful multi-attempt "incident" lifecycle beyond one `heal_attempts` row per subsystem-down-episode. Do not touch `IncidentEscalationHandler`'s RCA machinery. Do not add a schema-version upcaster framework (nothing here evolves past `schema_version=1`, matching Story 2.5's precedent). Do not restart the live production core/gateway process.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One-shot job finishes | `_retire_completed_one_shot` runs | `job.finished` recorded (same transaction as the row's outcome write), `record_ref` to `jobs`/`job_id`, then the row is deleted by the same call path | n/a — deletion after commit is the disclosed `expired`-on-open case |
| One-shot job exhausts retries | `_mark_failed`'s one-shot terminal branch | `job.parked` recorded (`NEEDS_YOU`/`HIGH`), same transaction as `write_audit`'s `job_failed_terminal` and the row delete | n/a |
| Recurring job fails but re-arms | `_mark_failed` recurring re-arm branch | `job.failed` recorded (`AMBIENT`), never `job.parked` | n/a |
| Heal attempted then recovers | `_heal_and_verify` calls `ensure_available()`, re-collect shows healthy | `heal.attempted` then `heal.healed`, both `AMBIENT`, same `heal_attempts` row | n/a |
| Heal attempted, still unhealthy after re-verify | subsystem in `attempted - healed` | `heal.exhausted` recorded, `NEEDS_YOU`/`HIGH` | n/a |
| Subsystem status unchanged tick-to-tick | `new_status == last row's new_status` for that subsystem | No `health.changed` recorded | n/a |
| Subsystem's first-ever observed status | no prior row in `health_status_changes` | No `health.changed` recorded (nothing to compare against) | n/a |
| `journal.record()` raises mid-transaction | any new call site | The scheduler/health-sweep transaction rolls back; the state change is never left half-applied (proven by construction, Story 2.1 precedent) | n/a |

</intent-contract>

## Code Map

- `src/stackowl/journal/enums.py` — add `RecordKind.JOB="job"`, `RecordKind.HEAL="heal"`, `RecordKind.HEALTH="health"`; add closed `HealthErrorCode` `StrEnum` (e.g. `TIMEOUT`, `CONNECTION_REFUSED`, `PERMISSION_DENIED`, `RESOURCE_EXHAUSTED`, `NOT_FOUND`, `UNKNOWN`) derived the same way `status.py::remedy_for` already classifies exceptions (type name / errno / sqlite phrase table) — reuse that classification's branch structure, return a code not a string.
- `src/stackowl/journal/job_events.py` (NEW) — `JobStartedAttrs`, `JobFinishedAttrs`, `JobFailedAttrs`, `JobParkedAttrs` (fields: `handler_name` bounded label, `attempt_count`/`failure_count` ints as applicable — mirrors `TaskDeadLetteredAttrs`'s shape). `_register()` registering all four, `job.parked` as `NEEDS_YOU`/`HIGH`.
- `src/stackowl/journal/heal_events.py` (NEW) — `HealAttemptedAttrs`, `HealHealedAttrs`, `HealExhaustedAttrs` (fields: `attempt_count` int). `heal.exhausted` as `NEEDS_YOU`/`HIGH`.
- `src/stackowl/journal/health_events.py` (NEW) — `HealthChangedAttrs` (`previous_status`, `new_status` — both from `health.status.HealthState`'s closed literal, stored as bounded strings; `error_code: str | None` from the new `HealthErrorCode`). `AMBIENT`.
- `src/stackowl/journal/__init__.py` — import the three new event modules for their registration side effect (same pattern as line 27's `task_events` import).
- `src/stackowl/journal/retention.py` (NEW) — `PROVISIONAL_JOURNAL_RETENTION_DAYS = 1` with a docstring stating explicitly: matches `task_loop_settings.py::prune_completed_after_days`'s current value (the tightest real production prune window today), chosen so this story's own AD-4/NFR45 tripwire passes without touching owner-authorized retention constants; Story 2.11 raises this to the real setting (architecturally ~30 days) and must simultaneously reconcile every subsystem prune window this tripwire checks — logged to `deferred-work.md`.
- `src/stackowl/db/migrations/0145_heal_attempts_and_health_status_changes.sql` (NEW) — `CREATE TABLE heal_attempts (id TEXT PRIMARY KEY, subsystem TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)`; `CREATE TABLE health_status_changes (id TEXT PRIMARY KEY, subsystem TEXT NOT NULL, previous_status TEXT NOT NULL, new_status TEXT NOT NULL, error_code TEXT, occurred_at TEXT NOT NULL)`; index each on `subsystem`.
- `src/stackowl/pipeline/durable/journal_names.py`-analog: `src/stackowl/scheduler/journal_names.py` (NEW) — `register_job_name_resolver(db_pool)` (resolves `job_id` -> `handler_name` via `Scheduler.list_jobs()`/a direct `jobs` read) and `register_subsystem_name_resolver()` for `RecordKind.HEAL`/`RecordKind.HEALTH` (identity passthrough — the subsystem label already IS the display name, `_MAX_LABEL_LEN`-bounded).
- `src/stackowl/scheduler/scheduler.py` — wrap `_run_job`'s CAS (job.started), `_mark_completed`/`_retire_completed_one_shot` (job.finished), `_settle`'s retry branch and `_mark_failed`'s re-arm branches (job.failed), and `_mark_failed`'s one-shot terminal branch (job.parked) in `async with self._db.transaction() as conn:`, calling `journal.record(conn, ...)` alongside each existing `UPDATE`/`DELETE`.
- `src/stackowl/scheduler/scheduler_mutations.py` — same transaction wrap for `run_now`'s started/`_record_run`'s finished, so a manually-triggered run is covered identically.
- `src/stackowl/scheduler/handlers/health_sweep.py` — `HealthSweepHandler.__init__` gains a required `db: DbPool` param (assembly already has `db` in scope at the construction site). New `_heal_and_verify` return shape distinguishes `attempted` (unchanged) so `execute()` can compute `exhausted = attempted - healed` and record `heal.attempted`/`heal.healed`/`heal.exhausted` inside `async with self._db.transaction() as conn:` around each `heal_attempts` upsert. New `_record_health_changes(conn, statuses)` helper: for each status in `statuses`, `SELECT new_status FROM health_status_changes WHERE subsystem=? ORDER BY occurred_at DESC LIMIT 1`; if a prior row exists and differs, insert a new row + `journal.record(conn, health.changed)`; called once after the FIRST `collect()` and again after the heal-triggered re-collect (so a heal-driven recovery is captured too).
- `src/stackowl/scheduler/assembly.py` — pass `db=db` into the `HealthSweepHandler(...)` construction (db already in local scope, used two lines above for `AuditAlertRecord(db, ...)`).
- `src/stackowl/startup/orchestrator.py` — call `register_job_name_resolver(db_pool)` / `register_subsystem_name_resolver()` alongside the existing `register_task_name_resolver(...)` call (~line 1112 area).
- `_bmad-output/implementation-artifacts/deferred-work.md` — new entry: provisional 1-day journal retention (see `journal/retention.py`); Story 2.11 must raise it and reconcile task/job_run prune windows together.
- `tests/journal/test_job_events.py`, `tests/journal/test_heal_events.py`, `tests/journal/test_health_events.py` (NEW) — registration shape, attention classification, narration.
- `tests/journal/test_retention_tripwire.py` (NEW) — proves the tripwire passes today against real `_RUN_HISTORY_RETENTION_DAYS`/`prune_completed_after_days`, and FAILS when a fake prune window shorter than `PROVISIONAL_JOURNAL_RETENTION_DAYS` is constructed (mirrors `tests/test_a_tunable_states_its_value_in_one_place.py`'s style of asserting against the real constant, not a copy).
- `tests/scheduler/test_job_lifecycle_journal.py` (NEW) — real tmp-DB: start/finish/fail/park each write a journal row in the same transaction as the state change; a forced rollback leaves neither (Story 2.1 precedent).
- `tests/scheduler/handlers/test_health_sweep_journal.py` (NEW) — heal.attempted/healed/exhausted branch coverage including the new `attempted - healed` exhaustion case; health.changed fires only on real transitions, not on first observation or an unchanged tick; a rollback test.

## Tasks & Acceptance

**Execution:** `journal/enums.py` (RecordKind + HealthErrorCode) -> `journal/retention.py` -> migration `0145` -> `journal/job_events.py`/`heal_events.py`/`health_events.py` -> `journal/__init__.py` exports -> `scheduler/journal_names.py` -> `scheduler/scheduler.py`/`scheduler_mutations.py` (transaction wraps) -> `scheduler/handlers/health_sweep.py` (db param, exhaustion branch, health-change detection) -> `scheduler/assembly.py` (db wiring) -> `startup/orchestrator.py` (resolver registration) -> `deferred-work.md` -> all tests.

**Acceptance Criteria:**
- Given the scheduler runs a job, when a run starts, finishes, fails or is parked, then the matching `job.*` event is recorded at the action site in the same transaction, with a `record_ref` to the `jobs` row, and `job.parked` fires only from the scheduler's own retry-exhaustion branch.
- Given a healer attempts a heal, when it attempts, succeeds, or exhausts retries after re-verify, then `heal.attempted`/`heal.healed`/`heal.exhausted` are recorded by `health_sweep.py`, with `heal.exhausted` `NEEDS_YOU`/`HIGH` and the other two `AMBIENT`.
- Given the health sweep runs, when a subsystem's status differs from its last recorded status, then `health.changed` records subsystem/previous/new status and a bounded error code, never exception text; an unchanged status records nothing.
- Given a `job.finished`/`heal.attempted`/`health.changed` event committed within `PROVISIONAL_JOURNAL_RETENTION_DAYS`, when its `record_ref` is opened, then the referenced row is present; a new tripwire test fails if any checked prune window (task/job-run) is shorter than that constant.

## Spec Change Log

## Review Triage Log

### 2026-09-18 — Review pass
- verdicts: 4 findings — high 0, medium 1, low 3, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` (adversarial-review) Two self-heal retirement paths deleted a permanently-completed/terminally-failed one-shot job's row without ever recording the matching `job.finished`/`job.parked` event: `_advance_past_serviced_occurrence`'s one-shot branch (`scheduler.py`, healing a completion whose original delete had failed) and `_retire_recorded_terminal_one_shots` (the terminal-failure heal sweep, healing a delete that failed inside `_mark_failed`'s own transaction). Both call `delete_finished_one_shot` with no `conn=` and no `journal_record(...)`, so the ONLY chance to record the transition (the happy-path call sites are already no-ops when their own delete first fails) was silently skipped. Evidence: verified directly by reading both functions and their callers, and confirmed the existing regression test for this exact scenario (`test_the_poll_cycle_HEALS_a_terminal_one_shot_whose_delete_failed`) asserted on `jobs`/`audit_log` but never checked `journal_events`. Fix: wrapped both sites in `self._db.transaction()`, passing `conn=` through to `delete_finished_one_shot`, and recording `job.finished` (completion heal) / `job.parked` (terminal-failure heal, NEEDS_YOU/HIGH, using `retry_count` from a widened SELECT for `attempt_count`) on a successful delete — mirroring the happy-path sites' own pattern exactly. Added journal assertions to both existing tests (`test_a_delete_that_FAILS_does_not_run_the_job_again`, `test_the_poll_cycle_HEALS_a_terminal_one_shot_whose_delete_failed`) proving the fix; both pass, full `tests/journal/`+`tests/scheduler/` suite re-run green (731 passed) and `./scripts/tripwires.sh` re-run PASS (ruff 29/35, mypy 57/65, unchanged) after the patch.
  - `[low]` `[defer]` (adversarial-review) `job.started` can be recorded with no resolution event when the claimed handler isn't registered at claim time — the row reverts to `pending` via a plain, unjournaled write in both `_run_job` and `run_now`. Evidence: verified directly (scheduler.py `_run_job`, scheduler_mutations.py `run_now`). Not patched: purely an observability gap (an orphaned `job.started` until the next successful claim), no double-write or data corruption, and the underlying race window (handler registration ordering at boot) is narrow — logged to `deferred-work.md` (DW-18) rather than expanding this story's scope.
  - `[low]` `[reject]` (adversarial-review) `JobFailedAttrs.failure_count` carries a different meaning (mid-run retry counter vs. backoff-ladder attempt index vs. the real `failure_count` column) across its three `job.failed` call sites, same field name. Evidence: verified by reading all three sites. Rejected per the low-finding rule: metadata/narration only, never read back for control flow, and unifying the semantics is a larger refactor than a direct correction.
  - `[low]` `[reject]` (adversarial-review) Three new `health_sweep.py` helpers (`_record_heal_attempted`, `_resolve_heal_attempts`, `_record_health_changes`) lack explicit numbered 4-point (entry/decision/step/exit) log markers, unlike sibling methods in the same file. Evidence: verified by reading the functions — each still logs meaningfully at its one real decision point (via the journal's own 4-point-logged `record()`), just without the inline numbered comments. Rejected per the low-finding rule: cosmetic convention gap, not a missing observability path.

## Design Notes

**Why a provisional 1-day retention constant instead of the architecture's eventual ~30-day default:** the epic's own Technical Decisions state concrete retention values "stay provisional until Story 2.12's benchmark." Today's REAL production prune windows are `prune_completed_after_days=1` (tasks, owner-authorized 2026-09) and `_RUN_HISTORY_RETENTION_DAYS=7` (job_runs, owner-authorized 2026-09-02) — both far below 30. A tripwire compared against 30 would fail immediately against values this story does not own and has no basis to change unilaterally. Setting the provisional constant to the tightest existing real window (1 day) makes the check honest (it genuinely fails on a real regression, e.g. a future 0-day window) without forcing a change to authorized production behavior; Story 2.11 is explicitly where the real value and the corresponding prune-window increases belong.

**Why `job.*`'s `record_ref` targets `jobs` even for a self-deleting one-shot row:** AD-4 states a missing target opens as `expired`, never an error — this is a disclosed, designed edge case, not a gap this story needs to engineer around. The alternative (pointing at `job_runs`) does not avoid the problem: `job_runs` is `ON DELETE CASCADE` from `jobs` (migration 0080) and is deleted in the SAME one-shot retirement path.

**Why `heal_attempts`/`health_status_changes` are new tables rather than reusing `jobs`/`job_runs`:** heals and health changes have no existing owning store (AD-4: "a target with no owning store gains a table by migration"); `job_runs`' schema and reader are job-domain-specific and unrelated.

## Verification

**Commands:**
- `uv run pytest tests/journal/ tests/scheduler/ -q` — expected: all new and existing tests pass.
- `./scripts/tripwires.sh` — expected: exits 0, ruff/mypy baselines unchanged or improved.

**Manual checks (if no CLI):**
- Drive a real job through the scheduler and a real health-sweep tick against a live install and confirm `journal_events` gains `job.*`/`heal.*`/`health.changed` rows — left to the coordinator, per this dispatch's explicit instruction not to restart the live production process from this session (Story 2.1–2.5 precedent).

## Auto Run Result

**Summary of implemented change:** `job.started/finished/failed/parked`, `heal.attempted/healed/exhausted` and `health.changed` are registered in the journal registry (`RecordKind.JOB/HEAL/HEALTH`, additive per AD-3). Every scheduler job-lifecycle mutation site (`scheduler.py`'s CAS claim, completion/retirement, retry/re-arm and terminal-park branches; `scheduler_mutations.py`'s `run_now`/`_record_run`) was refactored from an auto-committing `DbPool.execute()` into a `DbPool.transaction()` block pairing the state change with its `journal.record()` call (AD-24). `health_sweep.py` gained a required `DbPool` and now records `heal.attempted` at each recycle attempt, resolves it to `heal.healed`/`heal.exhausted` after re-verify (the previously-missing `attempted - healed` exhaustion branch), and compares every collected status against `health_status_changes`'s own last row per subsystem to journal real transitions only (FR84) — a closed `HealthErrorCode` enum keeps `error_code` free of exception text. Two new tables (`heal_attempts`, `health_status_changes`, migration 0145) give heal/health events a durable `record_ref` target, the same role `jobs`/`job_runs` already play for `job.*`. A provisional `journal/retention.py` constant (1 day, matching the tightest real production prune window) backs a new tripwire test proving no task/job-run prune window undercuts journal retention, with a control test proving the check is genuinely falsifiable.

**Files changed:**
- `src/stackowl/journal/enums.py` — `RecordKind.JOB/HEAL/HEALTH`; `HealthErrorCode` closed enum + `classify_health_error()`.
- `src/stackowl/journal/job_events.py`, `heal_events.py`, `health_events.py` (new) — the eight new event types and their registration; `job.parked`/`heal.exhausted` `NEEDS_YOU`/`HIGH`, everything else `AMBIENT`.
- `src/stackowl/journal/retention.py` (new) — `PROVISIONAL_JOURNAL_RETENTION_DAYS`.
- `src/stackowl/journal/__init__.py` — registration-side-effect imports for the three new event modules.
- `src/stackowl/db/migrations/0145_heal_attempts_and_health_status_changes.sql` (new).
- `src/stackowl/scheduler/journal_names.py` (new) — `RecordKind.JOB`/`HEAL`/`HEALTH` `NameResolver`s.
- `src/stackowl/scheduler/scheduler.py`, `scheduler_mutations.py` — transaction-wrapped job-lifecycle recording at every dispatch/completion/retry/park site, including (review-pass fix) the two self-heal retirement paths (`_advance_past_serviced_occurrence`, `_retire_recorded_terminal_one_shots`) that had silently skipped `job.finished`/`job.parked` when a job's ORIGINAL retirement delete had failed.
- `src/stackowl/scheduler/scheduler_helpers.py`, `src/stackowl/audit/logger.py` — `write_audit`/`chain_append_via_pool`/`delete_finished_one_shot` gained an optional `conn=` so a job's terminal audit row, delete and `job.parked` event commit atomically.
- `src/stackowl/scheduler/handlers/health_sweep.py` — required `db`; heal attempt/resolve + health-change-detection helpers.
- `src/stackowl/scheduler/assembly.py`, `startup/orchestrator.py` — `db=` wiring, resolver registration.
- `src/stackowl/health/store_cadence.py` — `heal_attempts`/`health_status_changes` declared `ON_DEMAND`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — DW-17 (provisional retention constant), DW-18 (review-pass finding: `job.started` can be left unresolved on a handler-registry-miss race).
- New tests: `tests/journal/test_job_events.py`, `test_heal_events.py`, `test_health_events.py`, `test_retention_tripwire.py`; `tests/scheduler/test_job_lifecycle_journal.py`; `tests/scheduler/handlers/test_health_sweep_journal.py` — plus mechanical `db=`/`tmp_db` threading across 8 existing test files whose `HealthSweepHandler(...)` construction the new required param touched, and two review-pass journal assertions added to `tests/scheduler/test_a_one_shot_job_does_not_re_arm.py`.

**Review findings breakdown:** 4 findings from one independent adversarial-review pass — 1 medium, 3 low.
- **Patched (1 medium):** the two self-heal retirement paths silently skipping `job.finished`/`job.parked` when the job's original retirement delete had failed — fixed by wrapping both in `self._db.transaction()` and recording the event on a successful delete, mirroring the happy-path sites; new assertions added to the two existing tests covering these exact heal scenarios; independently re-verified by a full `tests/journal/`+`tests/scheduler/` re-run (731 passed) and `./scripts/tripwires.sh` (PASS, ruff 29/35, mypy 57/65, both unchanged) after the patch.
- **Deferred (1 low, DW-18):** `job.started` left unresolved on a handler-registry-miss race — narrow window, observability-only, no data corruption.
- **Rejected (2 low):** `JobFailedAttrs.failure_count`'s differing meaning across its three call sites (metadata-only, no control-flow impact); missing inline 4-point numbered comments on three new `health_sweep.py` helpers (each still logs meaningfully through `journal.record()`'s own 4-point logging).

**Verification performed:**
- `uv run pytest tests/journal/ tests/scheduler/ -q` (run twice — before and after the review-pass patch) → 731 passed both times, 0 failed.
- `./scripts/tripwires.sh` (run twice — before and after the patch) → TRIPWIRES PASS both times: 709 passed/2 skipped (`-m tripwire`), B4/B8/B9 boundary checks pass, ruff findings 29 (baseline 35), mypy errors 57 (baseline 65), unchanged by this diff.
- `uv run ruff check` / `uv run mypy` on every touched file directly (not just the baseline count) — clean.
- Matrix Test Audit: all 7 I/O & Edge-Case Matrix rows covered by at least one passing test, confirmed by direct reading of `tests/scheduler/test_job_lifecycle_journal.py` and `tests/scheduler/handlers/test_health_sweep_journal.py`.
- Full diff read directly (not just the implementer's report) against this spec's own Code Map before dispatching the independent review subagent, and again after applying its one patch.
- Checked for a live gateway/core/scheduler process before and after every verification run — none running in this session's environment. Deliberately did **not** start or restart the live production process at any point, per this dispatch's explicit instruction.

**Residual risks:** DW-17 (provisional 1-day retention constant — disclosed, Story 2.11's scope to resolve); DW-18 (the deferred low finding above); the manual live-process check named in Verification above, left to the coordinator by explicit instruction.
