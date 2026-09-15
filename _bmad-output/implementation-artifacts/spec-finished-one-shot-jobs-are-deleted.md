---
title: 'A finished one-shot job is deleted, not parked as enabled and completed'
type: 'bugfix'
created: '2026-09-12'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '83f6aeb99cd488c90e1642b5e9bffd836fff7f57'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On the owner's box 136 of 169 `enabled=1` jobs are `rollover_summary` one-shots with `status='completed'` and a `next_run_at` frozen in the past, so every reader (scheduler health, `/api/v1/schedules`, the future Bridge) reports finished work as live schedules. WHY: commit 465c17ac (2026-08-31) parked finished one-shots as `completed` to keep their `job_runs` history, a reason already stale because migration 0080 made `job_runs.job_id` `ON DELETE CASCADE` and `goal_execution` one-shots already delete themselves — two rules for one fact, and the one `rollover_summary` follows never retires anything. A second copy lives in `scheduler_mutations._restore_after_run`, which re-arms a `run_once` job to `pending` with a +1 day slot after `run_now`.

**Approach:** One rule for every one-shot: when a `run_once` job finishes — successfully, or failed with its retries exhausted — its row is deleted in the same place the outcome is recorded, and `run_now` on a one-shot deletes rather than re-arms. A terminal failure is first recorded where failures belong (audit log / incident path), then the row goes. An idempotent migration deletes the already-finished one-shot rows (and their `job_runs`) on every install. The owner approved deleting the 136 rows (2026-09-12).

**Decisions (human, 2026-09-12):** J1 — failed one-shots are deleted too, after their failure is recorded.

## Boundaries & Constraints

**Always:** the migration's predicate matches only terminal one-shots — `status` in the scheduler's terminal set (`completed`, and the terminal failure status the code actually writes) AND `params.run_once = 1` (guarded by `json_valid(params)` so a malformed row cannot abort a customer's migration); it deletes those jobs' `job_runs` rows explicitly because the migration runner does not enforce foreign keys; one migration file `0143_*.sql`, idempotent (a second run deletes nothing); the scheduler path deletes through the pool that enforces the cascade; a terminal failure is recorded (audit log / incident path) before its row is deleted; rollover summaries stay exactly-once after their UNIQUE `idempotency_key` row disappears — the de-duplication that remains (`sessions.summary_enqueued_for` and the double-announce guard) must be proven to hold, including when the marker write fails; 4-point logging on the changed methods; failures log with a remedy, never silently.

**Never:** delete or disable a recurring job, a `pending`/`running`/retrying one-shot, or any job not `run_once`; keep `ONE_SHOT_TERMINAL_STATUS` or the "terminal, not deleted" contract (retired means deleted — constant, comments and the tests asserting it go); add a disabled-but-kept state; touch `src/stackowl/control_plane/`, `src/stackowl/config/`, `src/stackowl/cli/app.py`, `src/stackowl/startup/orchestrator.py` (another change is landing there).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One-shot succeeds | `run_once` job runs ok | row deleted; no `completed` row remains | delete failure logged with remedy; job not re-run |
| One-shot fails terminally | `run_once` job exhausts its retries | failure recorded in audit/incident path, then row deleted | if recording fails, row kept and the error logged with remedy |
| One-shot fails with retries left | `run_once` job fails, retry pending | row kept for the retry (unchanged) | N/A |
| Recurring job succeeds | cadence job runs ok | written back `pending` with next slot (unchanged) | N/A |
| `run_now` on a one-shot | owner triggers a `run_once` job | runs once, row deleted, never re-armed +1 day | N/A |
| Migration on dirty install | 136 completed `run_once` rows + 32 `job_runs` | all 136 and their runs deleted; 33 live jobs untouched | malformed `params` row skipped, migration completes |
| Migration re-run | already clean | deletes nothing | N/A |
| Summary marker write fails | consumer enqueues, marker write raises | no second summary for the same session end | logged loudly |
| Pending one-shot at upgrade | `run_once`, `status='pending'` | untouched by migration | N/A |

</frozen-after-approval>

## Code Map

- `src/stackowl/scheduler/scheduler.py:60-61` -- `ONE_SHOT_TERMINAL_STATUS` and its stale rationale comment: delete.
- `src/stackowl/scheduler/scheduler.py:668-700` -- `_mark_completed`; one-shot branch (:685-694) parks as completed: becomes delete. `:581` caller; `:349` claim query and `:1170` `recover()` select only `pending` (unchanged); `:758-760` terminal-failure path that leaves dead rows by design: record, then delete for `run_once`; `:1330` `list_jobs`.
- `src/stackowl/scheduler/scheduler_mutations.py:239-259` -- `_restore_after_run` re-arms `run_once`: delete instead.
- `src/stackowl/memory/rollover_summary_handler.py:485-507` -- `enqueue_rollover_summary` (`schedule="manual"`, `run_once: True`, UNIQUE `rollover:{lane}:{ended}`); `:569` consumer; `:580-590` comment relying on the UNIQUE key when the marker write fails.
- `src/stackowl/scheduler/handlers/conversation_sweep.py:218` -- backstop enqueuer.
- `src/stackowl/sessions/store.py:576-598` -- `summary_enqueued_for` marker.
- `src/stackowl/scheduler/handlers/goal_execution.py:387` -- existing self-delete for one-shots: the rule to converge on.
- `src/stackowl/db/pool.py:57` foreign keys ON; `src/stackowl/db/migrations/runner.py:485` not enforced in migrations, `:512` backup, `:589` skip applied; latest migration 0142; template `0134_drop_the_retry_sweep_job.sql` + `tests/scheduler/test_the_retry_sweep_is_gone_for_good.py`.
- `src/stackowl/health/contributors.py:1198` -- `scheduler_progress` counts `enabled=1` (169 → 33 after).
- Tests: `tests/scheduler/test_a_one_shot_job_does_not_re_arm.py` (asserts the old contract at :140, :206, :240 — rewrite as the regression test), `tests/memory/test_rollover_summary.py:430-486`, `tests/scheduler/test_run_once_self_delete_completion.py`.

## Tasks & Acceptance

**Execution:**
- [x] `tests/scheduler/test_a_one_shot_job_does_not_re_arm.py` (+ new migration test beside the 0134 one, + rollover dedupe test, + terminal-failure test) -- failing tests first for every matrix row.
- [x] `src/stackowl/scheduler/scheduler.py` -- one-shot completion deletes the row; terminal failure of a `run_once` job records then deletes; remove `ONE_SHOT_TERMINAL_STATUS` and the stale contract text.
- [x] `src/stackowl/scheduler/scheduler_mutations.py` -- `_restore_after_run` deletes a `run_once` job instead of re-arming.
- [x] `src/stackowl/memory/rollover_summary_handler.py` -- keep exactly-once without the UNIQUE row (update the `:580-590` reasoning to what actually guards it).
- [x] `src/stackowl/db/migrations/0143_finished_one_shot_jobs_are_deleted.sql` -- the terminal `run_once` predicate, `job_runs` first, then `jobs`.

**Acceptance Criteria:**
- Given the owner's live DB after restart, when the migration has run, then no terminal `run_once` job remains, the 33 live jobs remain, and health `scheduler_progress` reports 33 enabled.
- Given the next real rollover summary on the live platform, when it finishes, then its job row is gone and exactly one summary was produced.

## Implementation Notes

- Implemented in worktree branch `worktree-agent-a7e1e684b3a2b4939`: spec suites green except pre-existing `test_the_convention_is_written_where_a_human_reads_it` (reads deleted CLAUDE.md; fixed in the stray-database spec); adjacent 628 passed; ruff clean; mypy has 3 pre-existing errors in `scheduler/assembly.py:618-620` (untouched; queued). Beyond the Code Map: summary job writes its own `summary_enqueued_for` marker first (closed a proven duplicate-summary hole); startup reaper keeps a one-shot's own slot; failed `run_now` returns to its own slot; runner logs per-statement row counts.

- Runs in an isolated git worktree from `baseline_commit`, because the Q29 login change is uncommitted in the main tree; merged after that change lands. Live acceptance (restart, DB check) happens in the main tree after the merge.
- Approved by the owner 2026-09-12 ("Approve both", J1–J3 "yes to all").

- Live acceptance 2026-09-12 20:18 CDT after ./start.sh: boot log `0143 ... applied successfully — rows changed by its 2 data statement(s): [31, 136]`; live DB 33 enabled (all pending), 0 terminal run_once rows, no rollover_summary rows; the one `failed` row is the paused non-one-shot (untouched by design). AC2 (next real rollover summary deletes its own row, exactly one summary) awaits a real conversation boundary.

## Spec Change Log

## Review Triage Log

Review (2026-09-12). Layers: BH = blind hunter, VG = verification gap, EC = edge-case hunter. Routes: P = patch (sent to the implementer), R = rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| BH1 | `run_now` deletes a one-shot whose success was never verified (next poll's idempotent skip) | high | `_record_run` keys on `result.success`; VG probe: after one poll `jobs []`, handler calls 1 | P |
| BH2 | `recover()` / `resume()` recompute `next_run_at` for one-shots ('manual' → +1 d), changing the dedup key → double run | medium | `recover()` else-branch past replay window | P |
| BH3 | 0143 deletes failed one-shots without checking their failure was recorded | medium | runtime rule is record-then-delete (J1); 0075 pattern exists | P (require `job_failed_terminal` audit row) |
| BH3b | Old one-shots failed on transient errors could be re-queued instead of deleted | low | owner decision J1: failed one-shots are deleted | R |
| BH4 | Fallback paths keep `failed`/enabled rows forever with no heal | medium | no retry after audit/delete failure; self-healing rule | P |
| BH5 | Successful one-shots leave no durable record; run history loses rollover runs | low | frozen Problem accepts losing cascaded history; owner approved deleting runs; reminders already self-deleted before this change | R |
| BH6 | Runner row-count log misses zero-match; counts not on `MigrationResult`; untested | low | `if any(changed)` | P |
| BH7 | `SessionStore.save()` upsert can reset `summary_enqueued_for` after the job row is gone | medium | ON CONFLICT SET includes the marker | P |
| BH8 | Marker logs claim writes on zero match; double logs; wrong namespace | low | helper + store + handler each log | P |
| BH9 | `goal_execution._delete_job` duplicate self-delete remains | medium | two homes for one rule; retired-means-deleted | P |
| BH10 | Stale docstrings/comments; `'completed'` still allowed by the `jobs` CHECK constraint | low | comments P; CHECK change needs a table rebuild migration for a value nothing writes | P (comments) / R (CHECK) |
| BH11 | Failed `run_now` on a one-shot bypasses the retry ladder and re-runs immediately | medium | row back to pending at past slot without retry state | P |
| BH12 | Marker-failure test cannot tell which guard held | low | module-wide monkeypatch, single poll | P |
| BH13 | Untested branches (audit ok/delete fails, double failure, no handler, reap keeps slot, scoped marker zero match) | medium | grep of tests | P |
| VG1 | Vetoed `run_now` one-shot untested | medium | pre-verified; duplicate of BH1 | P |
| VG2 | `AND conversation_id = ?` marker scope unpinned | medium | pre-verified | P |
| VG3 | Audit written but delete fails — untested | medium | pre-verified | P |
| VG4 | `run_now` retires vetoed one-shot (other finding) | high | duplicate of BH1 | P |
| VG5 | Key does not survive a boot past the replay window (other finding) | medium | duplicate of BH2 | P |
| EC1 | Vetoed `run_now` one-shot deleted on next poll | high | duplicate of BH1 | P |
| EC2 | Failed `run_now` never advances retry ladder | medium | duplicate of BH11 | P |
| EC3 | `run_now` on a finished-but-undeleted one-shot runs it again | medium | claim path does not check a completed occurrence | P |
| EC4 | `_mark_failed` fallback UPDATE raising after notify → duplicate alerts | low | order: notify then update | P |
| EC5 | `save()` overwrites the marker | medium | duplicate of BH7 | P |
| EC6 | Run history silently excludes one-shots | low | duplicate of BH5 | R |
| EC7 | Claim: key survives boot | medium | duplicate of BH2 | P |
| EC8 | Claim: never delete pending/retrying one-shot — idempotent skip deletes vetoed one | high | duplicate of BH1 | P |
| EC9 | 4-point logging missing on new methods | low | no entry/decision logs | P |

Cascade: no intent_gap or bad_spec entries; patches sent to the implementer.

## Verification

**Commands:**
- `uv run pytest tests/scheduler tests/memory/test_rollover_summary.py tests/db -q` -- expected: all pass
- `uv run ruff check src/stackowl/scheduler src/stackowl/memory && uv run mypy src/stackowl/scheduler src/stackowl/memory` -- expected: clean

**Manual checks (if no CLI):**
- After restart: read-only query shows 0 terminal `run_once` jobs and 33 enabled; the boot log shows migration 0143 applied with its deleted-row counts.
