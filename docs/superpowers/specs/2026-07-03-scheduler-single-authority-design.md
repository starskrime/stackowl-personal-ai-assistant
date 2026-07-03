# Scheduler single-authority audit — design

## Problem

User reported a missing proactive reminder ping. Two unrelated boot-time bugs
(FK crash @1ae5ebf0, bad default channel @f37093de) were already fixed and
confirmed live (HEAD @3db10755, server up since 2026-07-02 21:28 CDT, clean
boot, no restarts since). The reminder gap is a separate, fourth issue.

While investigating, found the `jobs` table has zero rows matching a personal
reminder — none of the 3 `goal_execution` jobs present are it. Instead found:

1. A "start watching Movie Durandar at 5pm today" one-time reminder was
   persisted as `schedule: "0 17 * * *"` — a **daily recurring** cron — instead
   of a one-shot. Wrong classification at creation time in the `cronjob` tool.
2. An hourly `goal_execution` job (day.az scraper) has `failure_count: 2`,
   `last_error: "Provider 'ollama-powerful' error: Request timed out."` The
   scheduler's re-arm-and-alert path (`scheduler.py:517 _notify_failure`,
   wired at `assembly.py:226`) should have pushed a Telegram alert on each
   re-arm — unconfirmed whether it actually reached the user or silently
   failed to deliver.

Broadened conversation: user wants confidence that **all** scheduled/proactive
work in the system — reminders, agent monitoring, checks, anything
time-triggered — routes through one central authority, with no parallel
ad-hoc timers ("dummy schedulers") scattered around the codebase.

## What already exists (do not rebuild)

`src/stackowl/scheduler/` (6880 lines) already IS the register-and-poll
architecture the user described:

- `jobs` table = the register (job_id, handler_name, schedule, next_run_at,
  retry_at, enabled, params, target_channels, failure_count, ...)
- `JobScheduler.run()` (`scheduler.py:96`) polls the table every 30s and
  dispatches due jobs to registered handlers (30+ handlers: morning_brief,
  check_in, health_sweep, goal_execution, website_watch, credential_rotation,
  ...)
- CAS-guarded dispatch (no double-fire across concurrent pollers), idempotency
  keys scoped to the specific scheduled occurrence, retry budget with a
  separate `retry_at` slot that never clobbers the canonical `next_run_at`
  cadence, a circuit breaker for owl-lifecycle jobs, and a proactive
  operator-alert seam (`_notify_failure`) that fires through the same
  delivery path as `morning_brief`/`check_in` on every retry-exhausted
  re-arm or terminal failure.
- Reminders are NOT a separate subsystem — "remind me in 5m" creates a
  `goal_execution` job with `schedule: "in 5m"` via the `cronjob` tool
  (comment at `cronjob.py:5-10`).

Conclusion: no new scheduler subsystem is needed or wanted. The gap is
specific handlers/producers not reliably wired through the existing
authority, plus at least one classification bug at job-creation time.

## Scope decisions (confirmed with user)

- Harden the existing `JobScheduler`, do not replace it.
- Audit ALL producers of scheduled/proactive work, not just the
  reminder/goal_execution path.
- Also sweep for parallel/hardcoded scheduling logic (loops, timers,
  interval-based background work) living OUTSIDE `JobScheduler` that should
  be business-domain jobs but aren't.
- Track as an independent bugfix arc — do not fold into the in-flight
  de-complication PRD (FR-11/12 done, FR-21 next, Week 3 pending elsewhere).

## Architecture

No new runtime components. `JobScheduler` + `jobs` table remain the single
authority for all business-domain scheduling. The drift guardrail (below)
runs at test/CI time, not boot time — the process has already had two
boot-crash incidents this week; adding scan/enforcement logic to the startup
path is the wrong place for it.

### Classification: business vs. infra timers

A preliminary sweep (`grep -rln "while True"` / `asyncio.sleep(` across
`src/stackowl/`, excluding `scheduler/` and tests) found these categories:

- **Business-domain, must route through `jobs` table**: anything that
  triggers user-visible or agent-driven work on a schedule (reminders,
  briefs, checks, sweeps, watches). Target of the audit.
- **Infra-liveness, legitimately independent**: `service/watchdog.py`
  (systemd `WATCHDOG=1` sd_notify ping — must keep running even if
  `JobScheduler` itself hangs, since its job is detecting exactly that
  failure mode), channel adapters' protocol-level poll/heartbeat loops
  (`channels/telegram/adapter.py` heartbeat, `channels/whatsapp/adapter.py`
  poll — transport requirements, not scheduling), IPC/retry backoff loops
  (`ipc/client.py`, `owls/evolution.py` retry backoff), and short bounded
  waits (`tools/process/wait_tool.py`, `tools/browser/*` TTL/timeout loops).

  These are legitimate exceptions BY DESIGN, not violations — the guardrail
  must not flag them.

The full audit (every hit from the grep sweep, not just the sample above)
happens during implementation, one file at a time, each classified and
either left alone (infra, added to the guardrail allowlist with a reason) or
migrated into a `JobScheduler` handler (business).

## Components

1. **Audit pass** — enumerate every `asyncio.sleep`/`while True` loop under
   `src/stackowl/` outside `scheduler/`, classify business vs. infra, produce
   a short table (file:line → classification → action).
2. **Migration** — any business-classified violator found gets moved into a
   proper `JobScheduler` handler + `jobs` row, following the existing handler
   pattern (`scheduler/handlers/*.py`, `HandlerRegistry`).
3. **Bug fix — one-time reminder misclassified as recurring.** Root-cause in
   the `cronjob` tool's schedule parsing (`scheduling/cronjob.py`,
   `scheduler_helpers.parse_in`/`is_valid_schedule`): a natural-language
   "at 5pm today" (one specific instant) must produce a one-shot schedule
   (`params['run_once']=True`, per `scheduler.py:386 _is_recurring`), not a
   daily cron. Fix at the parsing/classification boundary, not by patching
   the one job row.
4. **Bug fix / confirmation — failure-alert delivery.** Verify
   `_notify_failure` → `ProactiveJobDeliverer.deliver_for_job` actually
   produces a delivered (not undeliverable/dropped) row in
   `delivery_attempts` for the day.az job's 2 recorded failures. If it does
   and the user still didn't see it, the bug is downstream (channel send,
   ledger) — trace from there. If it doesn't, fix at the seam.
5. **Guardrail — `test_no_dummy_schedulers.py`.** A single pytest that scans
   `src/stackowl/**/*.py` for sleep-loop/poll-loop patterns. Any match
   outside `scheduler/` must appear in a maintained allowlist
   (`tests/scheduler_timer_allowlist.py` or similar — a flat list of
   `(file, line-anchor, reason)` entries). New unregistered loop → test
   fails → caught in review, not a runtime registry/decorator (cheapest
   mechanism that closes the gap; no startup-path changes).

## Data flow

Audit findings → fix list (bugs 3 & 4 above) → handler migrations (if any
found) → guardrail allowlist seeded from the audit's "infra, legitimate"
column → guardrail test added → CI enforces going forward.

## Error handling

Covered by components 3 and 4 above — both are root-cause fixes at the
classification/delivery boundary, not symptom patches on the specific job
rows already in the table.

## Testing

- Regression test: a one-time "in Xm" / "at specific-datetime" schedule
  string produces `run_once=True` and a non-recurring `next_run_at`.
- Test: a job that exhausts retries and re-arms (or terminally fails)
  produces a `delivered` (not `undeliverable`) row in `delivery_attempts`
  when a deliverer is wired.
- The guardrail test itself (`test_no_dummy_schedulers.py`).

## Out of scope

- De-complication PRD (FR-21, Week 3) — untouched, separate track.
- Any change to `JobScheduler`'s core dispatch/retry/circuit-breaker logic —
  it already works correctly; this arc wires things INTO it and closes gaps
  around it.
- Runtime/boot-time enforcement — the guardrail is CI-time only.
