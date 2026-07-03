# Scheduler Single-Authority Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the reminder-scheduling gap found in production (a one-time "at 5pm today" reminder silently became a daily recurring job), confirm the scheduler's existing failure-alert path actually reaches Telegram, and add a guardrail so no future code adds a parallel ad-hoc timer outside `JobScheduler`.

**Architecture:** No new runtime components. `src/stackowl/scheduler/` (`JobScheduler` + the `jobs` table) is already the single register-and-poll authority — this plan adds one missing schedule-DSL token to it, verifies one of its existing delivery seams, and adds a CI-time test that keeps it the *only* such authority.

**Tech Stack:** Python 3.13, pytest/pytest-asyncio, SQLite (`stackowl.db`), existing `croniter`/`zoneinfo` deps — no new dependencies.

## Global Constraints

- Follow the existing 4-point logging standard (entry/decision/step/exit) on any new/modified method (per `CLAUDE.md`).
- Never catch-and-hide; every `except` logs via `log.<module>.error(..., exc_info=err, ...)`.
- Minimal diffs — extend existing patterns (`parse_in`/`parse_every`/`is_valid_schedule`), do not restructure `JobScheduler`.
- Run targeted pytest paths only (`uv run pytest <path>`), never the full suite (known to hang on this box).
- Commit after each task once its own tests pass.

---

### Task 1: Add `at HH:MM` one-shot absolute-time schedule token

Reminders like "remind me at 5pm today" have no correct DSL token today — `cronjob`'s schedule grammar only offers `in <n><unit>` (relative one-shot), `every <n><unit>`/`daily@HH:MM` (recurring), or raw 5-field cron (recurring). An LLM asked for an absolute one-time clock time picks the closest-sounding token (`0 17 * * *`), which the scheduler then treats as a **daily recurring** job forever — exactly what happened to job `goal_execution-e6a88565` (`"Reminder: Start watching Movie Durandar today at 5 PM CDT..."`, `schedule: "0 17 * * *"`). This task adds `at HH:MM` as a proper one-shot token, mirroring `daily@`'s local-time computation but wired to `run_once=True`.

**Files:**
- Modify: `src/stackowl/scheduler/scheduler_helpers.py` (add `parse_at`, factor `_next_local_hhmm`, wire into `compute_next_run`)
- Modify: `src/stackowl/tools/scheduling/cron_helpers.py` (add `at ` branch to `is_valid_schedule` and `render_recurrence`)
- Modify: `src/stackowl/tools/scheduling/cronjob.py` (wire `at ` into the `one_shot` check in `_create`, update tool description/error message)
- Test: `tests/scheduler/test_scheduler_helpers.py` (new cases for `parse_at`/`compute_next_run`)
- Test: `tests/tools/scheduling/test_cronjob_at_schedule.py` (new file — end-to-end `at HH:MM` creation)

**Interfaces:**
- Produces: `parse_at(schedule: str) -> tuple[int, int] | None` in `scheduler_helpers.py` — returns `(hour, minute)` for a valid `at HH:MM` token, `None` otherwise. Exported the same way `parse_in`/`parse_every` are (module-level function, no class).
- Consumes: existing `parse_in(schedule: str) -> timedelta | None` (already imported in `cronjob.py` and `cron_helpers.py`).

- [ ] **Step 1: Write the failing tests for `parse_at` and `compute_next_run`**

Add to `tests/scheduler/test_scheduler_helpers.py` (create the file if it does not exist; check first — this module already has scheduler tests elsewhere in `tests/scheduler/`, follow that directory's existing import style):

```python
from datetime import UTC, datetime

from stackowl.scheduler.scheduler_helpers import compute_next_run, parse_at


def test_parse_at_valid_hhmm():
    assert parse_at("at 17:00") == (17, 0)
    assert parse_at("AT 5:30") == (5, 30)


def test_parse_at_rejects_bad_input():
    assert parse_at("at 24:00") is None
    assert parse_at("at 5:70") is None
    assert parse_at("daily@17:00") is None
    assert parse_at("") is None


def test_compute_next_run_at_today_future_time():
    # now=10:00 local UTC, "at 17:00" should land TODAY at 17:00 UTC.
    now = datetime(2026, 7, 3, 10, 0, tzinfo=UTC)
    next_run = compute_next_run("at 17:00", tz="UTC", now=now)
    assert next_run.startswith("2026-07-03T17:00:00")


def test_compute_next_run_at_past_time_rolls_to_tomorrow():
    # now=18:00 local UTC, "at 17:00" already passed today -> tomorrow.
    now = datetime(2026, 7, 3, 18, 0, tzinfo=UTC)
    next_run = compute_next_run("at 17:00", tz="UTC", now=now)
    assert next_run.startswith("2026-07-04T17:00:00")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_scheduler_helpers.py -k parse_at -v`
Expected: FAIL with `ImportError: cannot import name 'parse_at'`

- [ ] **Step 3: Implement `parse_at` and thread it through `compute_next_run`**

In `src/stackowl/scheduler/scheduler_helpers.py`, add the regex and function right after `parse_in` (after line 70):

```python
# One-shot absolute-local-clock-time schedule DSL token: ``at HH:MM``. Unlike
# ``daily@HH:MM`` (recurring), this fires ONCE at the next occurrence of that
# local wall-clock time (today if still ahead, else tomorrow), then the job
# self-deletes — mirrors ``in <n><unit>`` but for an absolute time instead of
# a relative delay. An LLM asked to schedule "remind me at 5pm today" had no
# correct token before this and fell back to misusing a recurring 5-field
# cron (REMINDER-FIX-2: the resulting job never stopped recurring).
_AT_RE = re.compile(r"^at\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)


def parse_at(schedule: str) -> tuple[int, int] | None:
    """Parse an ``at HH:MM`` one-shot absolute-local-time token.

    Returns ``(hour, minute)`` when ``schedule`` is a valid ``at HH:MM`` token
    with in-range values, ``None`` otherwise (not an error) — mirrors
    :func:`parse_in`/:func:`parse_every`.
    """
    match = _AT_RE.match(schedule.strip())
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return (hour, minute)
```

Replace the `daily@` branch of `compute_next_run` (lines 133–156) with a shared helper plus two thin call sites:

```python
def _next_local_hhmm(
    hour: int, minute: int, *, tz: str, now: datetime | None
) -> str:
    """Return the next ISO-8601 UTC instant at local ``HH:MM`` in ``tz``.

    Shared by the ``daily@`` (recurring) and ``at`` (one-shot) schedule
    branches of :func:`compute_next_run` — both need the identical
    today-if-future-else-tomorrow local-time computation; only the caller's
    ``run_once`` flag (set in ``cronjob.py``) decides whether the job recurs.
    """
    try:
        zone = ZoneInfo(tz)
    except Exception as exc:  # B5 — fail open to UTC, never silent
        log.scheduler.warning(
            "[scheduler] compute_next_run: unknown tz — defaulting to UTC",
            exc_info=exc,
            extra={"_fields": {"tz": tz}},
        )
        zone = ZoneInfo("UTC")
    local_now = (now or datetime.now(UTC)).astimezone(zone)
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC).isoformat()


def compute_next_run(
    schedule: str, *, tz: str = "UTC", now: datetime | None = None
) -> str:
    """Compute the next ISO-8601 UTC run time from a schedule expression.

    For ``daily@HH:MM`` (recurring) and ``at HH:MM`` (one-shot) the candidate
    is built as a LOCAL wall-clock time in ``tz`` (the user-facing IANA
    timezone, ``settings.system.timezone``) and then stored in UTC — so "8am"
    stays 8am across DST transitions and the scheduler shares the SAME tz the
    quiet-hours clock uses (F108). ``tz`` defaults to ``"UTC"`` for
    back-compat with non-daily callers; a bad tz fails open to UTC (logged),
    matching ``in_quiet_hours``. ``now`` is injectable for tests.
    """
    log.scheduler.debug(
        "[scheduler] compute_next_run: entry",
        extra={"_fields": {"schedule": schedule, "tz": tz}},
    )
    if schedule.startswith("daily@"):
        parts = schedule[len("daily@") :].split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return _next_local_hhmm(hour, minute, tz=tz, now=now)
    at_hhmm = parse_at(schedule)
    if at_hhmm is not None:
        hour, minute = at_hhmm
        return _next_local_hhmm(hour, minute, tz=tz, now=now)
    interval = parse_every(schedule)
    if interval is not None:
        next_iso = (datetime.now(UTC) + interval).isoformat()
        log.scheduler.debug(
            "[scheduler] compute_next_run: every-interval",
            extra={"_fields": {"schedule": schedule, "next_run": next_iso}},
        )
        return next_iso
    delay = parse_in(schedule)
    if delay is not None:
        next_iso = (datetime.now(UTC) + delay).isoformat()
        log.scheduler.debug(
            "[scheduler] compute_next_run: one-shot in-delay",
            extra={"_fields": {"schedule": schedule, "next_run": next_iso}},
        )
        return next_iso
    try:
        from croniter import croniter  # type: ignore[import-untyped]

        it = croniter(schedule, datetime.now(UTC))
        next_dt: datetime = it.get_next(datetime)
        return next_dt.isoformat()
    except Exception as exc:  # B5
        log.scheduler.warning(
            "[scheduler] compute_next_run: cron parse failed — defaulting to +1d",
            exc_info=exc,
            extra={"_fields": {"schedule": schedule}},
        )
        return (datetime.now(UTC) + timedelta(days=1)).isoformat()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_scheduler_helpers.py -k parse_at -v`
Expected: PASS (all 4 new tests)

- [ ] **Step 5: Write the failing test for `is_valid_schedule` + `render_recurrence`**

Add to the existing cron-helpers test file (find it via `find tests -iname "*cron_helpers*"`; if none exists, create `tests/tools/scheduling/test_cron_helpers.py`):

```python
from stackowl.tools.scheduling.cron_helpers import is_valid_schedule, render_recurrence


def test_is_valid_schedule_accepts_at_token():
    assert is_valid_schedule("at 17:00") is True
    assert is_valid_schedule("at 24:00") is False
    assert is_valid_schedule("at 5:9") is False  # minute must be 2 digits


def test_render_recurrence_at_token_reads_as_once():
    assert render_recurrence("at 17:00") == "once, at 17:00"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/tools/scheduling/test_cron_helpers.py -k "at_token or at_schedule" -v`
Expected: FAIL — `at 17:00` is currently invalid (falls through to `croniter.is_valid`, which rejects it) and `render_recurrence` renders it as `"runs on schedule 'at 17:00', forever"`.

- [ ] **Step 7: Implement `is_valid_schedule` and `render_recurrence` branches**

In `src/stackowl/tools/scheduling/cron_helpers.py`, update the import (line 14):

```python
from stackowl.scheduler.scheduler_helpers import parse_at, parse_every, parse_in
```

In `render_recurrence` (after the existing `parse_in` check, before the `daily@`/`every`/cron branching — insert right after line 44's `return`):

```python
    at_hhmm = parse_at(text)
    if at_hhmm is not None:
        # HONESTY — a one-shot must never be echoed as recurring.
        return f"once, at {at_hhmm[0]:02d}:{at_hhmm[1]:02d}"
```

In `is_valid_schedule`, add a branch right after the `"in "` branch (after line 133, before the `daily@` check):

```python
    if lowered.startswith("at "):
        # Single source of truth with ``compute_next_run`` — a one-shot
        # absolute local time (REMINDER-FIX-2), never recurring.
        return parse_at(text) is not None
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/tools/scheduling/test_cron_helpers.py -k "at_token or at_schedule" -v`
Expected: PASS

- [ ] **Step 9: Write the failing end-to-end test for `cronjob create` with `at HH:MM`**

Create `tests/tools/scheduling/test_cronjob_at_schedule.py`. Check `tests/tools/scheduling/` for an existing `create` test (e.g. one covering the `in 5m` one-shot path — grep `run_once=True` under `tests/tools/scheduling/`) and copy its fixture/scheduler-construction setup exactly; do not invent a new fixture pattern. The assertion shape:

```python
import pytest


@pytest.mark.asyncio
async def test_cronjob_create_at_schedule_is_one_shot(cronjob_tool, scheduler, owl_session):
    """A 'remind me at 5pm today' schedule must persist run_once=True, never
    a recurring daily cadence."""
    result = await cronjob_tool.execute(
        {"action": "create", "prompt": "watch the movie", "schedule": "at 17:00"},
        session=owl_session,
    )
    assert result.success
    jobs = await scheduler.list_jobs()
    job = next(j for j in jobs if j.params.get("goal") == "watch the movie")
    assert job.params.get("run_once") is True
    assert job.schedule == "at 17:00"
```

(Replace `cronjob_tool`/`scheduler`/`owl_session` fixture names with whatever the existing `in 5m` test in this directory actually uses — match it exactly, do not guess fixture names.)

- [ ] **Step 10: Run test to verify it fails**

Run: `uv run pytest tests/tools/scheduling/test_cronjob_at_schedule.py -v`
Expected: FAIL — `job.params.get("run_once")` is `None`/falsy (schedule accepted by `is_valid_schedule` now, but `one_shot` in `cronjob.py` doesn't know about `at`).

- [ ] **Step 11: Wire `at` into `cronjob.py`'s one-shot detection and update docs/errors**

In `src/stackowl/tools/scheduling/cronjob.py`, update the import (line 34):

```python
from stackowl.scheduler.scheduler_helpers import parse_at, parse_in
```

Update line 325:

```python
        # REMINDER-FIX / REMINDER-FIX-2 — a one-shot 'in <n><unit>' OR 'at
        # HH:MM' schedule arms the existing goal_execution run_once path: fire
        # exactly once, deliver via the same proactive seam as any other cron
        # goal, then self-delete. Reuses the engine's own primitive; no new
        # tool/handler.
        one_shot = parse_in(schedule) is not None or parse_at(schedule) is not None
```

Update the rejection message at lines 294–299:

```python
        if not is_valid_schedule(schedule):
            return self._err(
                f"unparseable schedule {schedule!r} — use 'in Nm'/'in Nh' (once, "
                "relative), 'at HH:MM' (once, next occurrence of that local "
                "time), 5-field cron, 'every Nm'/'every Nh', or 'daily@HH:MM'",
                t0,
            )
```

Update the `description` property (lines 86-104) — replace the sentence describing one-time reminders:

```python
            "SCHEDULE a natural-language goal to run automatically later — once or "
            "on a recurrence. Actions: create (needs 'prompt' + 'schedule') — use "
            "this for reminders too: a ONE-TIME reminder like 'remind me in 5 "
            "minutes to go out' is schedule='in 5m' (fires once, then stops); a "
            "reminder for a SPECIFIC clock time like 'remind me at 5pm today' is "
            "schedule='at 17:00' (fires once at the next occurrence of that local "
            "time, then stops — do NOT use a cron expression for a one-time "
            "reminder, it will recur forever); "
            "watch (needs 'schedule' + either 'watch_url' to poll a web page or "
            "'watch_path' to watch a filesystem path — ping you when it changes), "
            "list (your scheduled jobs), update (by 'job_id'; re-checks the "
            "prompt), pause, resume, remove, run (execute one job now) — the last "
            "four take 'job_id'. "
            "'schedule' accepts 'in 5m'/'in 2h' (ONE-TIME, fires once, relative "
            "delay), 'at 17:00' (ONE-TIME, fires once, next occurrence of that "
            "local clock time), 5-field cron ('0 9 * * *'), 'every 30m'/'every "
            "2h', or 'daily@09:00' (all three recurring). A flagged prompt "
            "(injection/exfil) is BLOCKED with a reason; relay it and do not "
            "retry verbatim. LANE: durable background work — a one-time delayed "
            "reminder OR recurring work on a clock. ANTI-LANE: do NOT use this "
            "to wait for a user reply mid-turn (use clarify) or to run "
            "something synchronously right now (just do it)."
```

And the `schedule` parameter description (lines 113-119):

```python
                "schedule": {
                    "type": "string",
                    "description": (
                        "'in 5m'/'in 2h' for a ONE-TIME relative reminder (fires "
                        "once), 'at 17:00' for a ONE-TIME reminder at a specific "
                        "clock time (fires once, next occurrence — NEVER use a "
                        "cron expression for a one-time reminder), or recurring: "
                        "cron '0 9 * * *', 'every 30m'/'every 2h', 'daily@09:00'."
                    ),
                },
```

- [ ] **Step 12: Run test to verify it passes**

Run: `uv run pytest tests/tools/scheduling/test_cronjob_at_schedule.py -v`
Expected: PASS

- [ ] **Step 13: Run the full set of touched tests together**

Run: `uv run pytest tests/scheduler/test_scheduler_helpers.py tests/tools/scheduling/test_cron_helpers.py tests/tools/scheduling/test_cronjob_at_schedule.py tests/tools/scheduling/ -v`
Expected: PASS (including all pre-existing tests in `tests/tools/scheduling/` — confirms the `at` addition didn't regress `in`/`daily@`/cron handling)

- [ ] **Step 14: One-off repair of the already-broken movie reminder job**

This is a data fix, not a code fix — the existing bad row must be corrected so it doesn't keep firing daily. Run via `uv run python -c`:

```python
import sqlite3
c = sqlite3.connect("/home/boss/.stackowl/workspace/stackowl.db")
c.execute(
    "UPDATE jobs SET schedule = 'at 17:00', "
    "params = json_set(params, '$.run_once', json('true')) "
    "WHERE job_id = 'goal_execution-e6a88565'"
)
c.commit()
print(c.execute("SELECT job_id, schedule, params FROM jobs WHERE job_id = 'goal_execution-e6a88565'").fetchone())
```

Only run this AFTER Task 1's code is deployed and the live server has picked it up (restart required — see the deployment note at the end of this plan). Otherwise the running scheduler still doesn't know `at 17:00` and will fail to compute `next_run_at` for it, defaulting to a raw `croniter` parse failure (+1 day fallback, logged as a warning, not a crash — but still wrong).

- [ ] **Step 15: Commit**

```bash
git add src/stackowl/scheduler/scheduler_helpers.py src/stackowl/tools/scheduling/cron_helpers.py src/stackowl/tools/scheduling/cronjob.py tests/scheduler/test_scheduler_helpers.py tests/tools/scheduling/test_cron_helpers.py tests/tools/scheduling/test_cronjob_at_schedule.py
git commit -m "fix(scheduler): add 'at HH:MM' one-shot schedule token

A one-time reminder for a specific clock time ('remind me at 5pm today')
had no correct schedule token — cronjob's grammar only offered relative
'in Nm' (one-shot) or daily@/every/cron (recurring). An LLM asked for an
absolute time picked cron, producing a job that recurred forever instead
of firing once. Adds 'at HH:MM' mirroring daily@'s local-time computation
but wired to run_once=True, single source of truth with compute_next_run
via a shared _next_local_hhmm helper."
```

---

### Task 2: Confirm the retry-exhausted job-failure alert reaches Telegram

`JobScheduler._notify_failure` (`scheduler.py:517`) already pushes a proactive alert through `ProactiveJobDeliverer.deliver_for_job` on every retry-exhausted re-arm or terminal failure, and it's wired at `assembly.py:226`. `goal_execution-063ab221` (the day.az scraper) has `failure_count: 2` — meaning this path has already fired twice tonight. This task confirms whether those alerts actually reached Telegram (delivered) or were silently dropped (undeliverable/failed), and fixes the gap if one exists. Commit `3db10755` (already live, merged tonight before this session started) fixed exactly one class of this bug — a `primary_channel`-only job with no `target_channels`/`target_addresses` resolving to zero recipients. `goal_execution-063ab221` was created with `target_channels: ["telegram"]` already populated (post-dates that gap), so this task verifies the fix actually covers this job's case, or finds a distinct second cause if it doesn't.

**Files:**
- Read-only diagnostic (no source changes expected unless Step 3 finds a real gap)
- Test: `tests/notifications/test_job_failure_alert_delivery.py` (new — regression test locking in whatever the diagnostic confirms)

**Interfaces:**
- Consumes: `ProactiveJobDeliverer.deliver_for_job(job, message, category, urgency) -> ProactiveDeliveryOutcome` (`src/stackowl/notifications/proactive_job.py:142`), `DeliverySpec.from_job(job) -> DeliverySpec` and `DeliverySpec.unresolved_channels() -> list[str]` (`src/stackowl/notifications/recipient.py:76,139`).

- [ ] **Step 1: Query the delivery ledger for this job's actual outcome**

Run via `uv run python -c`:

```python
import sqlite3, json
c = sqlite3.connect("/home/boss/.stackowl/workspace/stackowl.db")
c.row_factory = sqlite3.Row
print("--- delivery_attempts ---")
for r in c.execute(
    "SELECT * FROM delivery_attempts WHERE job_id = ? ORDER BY rowid",
    ("goal_execution-063ab221",),
).fetchall():
    print(dict(r))
print("--- undelivered_outbox ---")
for r in c.execute(
    "SELECT * FROM undelivered_outbox WHERE job_id = ? ORDER BY rowid",
    ("goal_execution-063ab221",),
).fetchall():
    print(dict(r))
print("--- audit_log (job_rearmed_after_failure) ---")
for r in c.execute(
    "SELECT * FROM audit_log WHERE target = ? ORDER BY rowid",
    ("goal_execution-063ab221",),
).fetchall():
    print(dict(r))
```

Expected: PASS/FAIL branch on what this returns —

- **If `delivery_attempts` has 2 rows with a `delivered`-equivalent status for channel `telegram`**: the alert path works correctly for this job. The user's "no ping" experience is unrelated to this job (it's the day.az scraper, not a reminder) — close this task as CONFIRMED WORKING, write the regression test in Step 4 to lock in the behavior, skip Step 3.
- **If `undelivered_outbox` has rows for this job with `reason: "undeliverable"`**: `DeliverySpec.from_job` is failing to resolve `telegram` from this job's `target_channels`/`target_addresses` despite `target_channels` being populated — proceed to Step 2 to find why (likely `target_addresses` is NULL/empty even though `target_channels` is not, which `DeliverySpec.from_job` needs to actually resolve a send address).
- **If both tables have zero rows for this job**: `_notify_failure` never ran, or `self._job_deliverer` was `None` at the time — proceed to Step 2 with a different query (check whether `assembly.py:226`'s `scheduler._job_deliverer = goal_job_deliverer` assignment actually executed for this process instance, e.g. via `grep "no deliverer wired" ~/.stackowl/logs/stackowl.jsonl`).

- [ ] **Step 2: If Step 1 found a real gap, read `target_addresses` for this job and trace `DeliverySpec.from_job`**

```python
import sqlite3
c = sqlite3.connect("/home/boss/.stackowl/workspace/stackowl.db")
row = c.execute(
    "SELECT target_channels, target_addresses, primary_channel FROM jobs WHERE job_id = ?",
    ("goal_execution-063ab221",),
).fetchone()
print(row)
```

Then read `src/stackowl/notifications/recipient.py` lines 76–135 (`DeliverySpec.from_job`) to see exactly which column combination it requires to resolve a channel to `_resolved` vs `_unresolved`. Do not write a fix yet — first state the root cause as a one-sentence hypothesis (per systematic-debugging: hypothesis before fix) and confirm it against the actual column values retrieved above.

- [ ] **Step 3: Fix the confirmed root cause (only if Step 1/2 found a real gap)**

The exact diff depends on Step 2's finding — likely candidates, in order of likelihood given `3db10755`'s pattern:
- `target_addresses` is NULL/empty despite `target_channels` being populated → the gap is in `cronjob.py`'s `_resolve_durable_target` (called at job creation, line 318) not persisting an address for the `telegram` channel — trace `resolve_owner_addresses` (`src/stackowl/notifications/recipient.py`) to see why it returned no address for a channel it did include in `target_channels`.
- If Step 1 found zero delivery rows at all (deliverer never invoked) → check whether this specific server process (started 2026-07-02 21:28, before `3db10755` merged at 21:26 — a 2-minute gap, but the process was NOT restarted after that commit per this session's earlier boot-log check) is running pre-fix code in memory. If so, this is not a new bug — it's `3db10755` not yet live. State this explicitly rather than writing a redundant fix, and flag that a server restart (not a code change) is the actual remediation.

Write the actual code change here only after confirming which branch applies — do not guess ahead of Step 1/2's evidence.

- [ ] **Step 4: Write the regression test**

Create `tests/notifications/test_job_failure_alert_delivery.py`. Find the existing test pattern for `ProactiveJobDeliverer` (grep `class.*ProactiveJobDeliverer` under `tests/notifications/` — there is very likely already a fixture for a fake/in-memory `ProactiveDeliverer` + `DeliveryLedger` from the `3db10755` test file `tests/notifications/test_undelivered_outbox_gate.py`; reuse it, do not build a new fixture):

```python
import pytest


@pytest.mark.asyncio
async def test_retry_exhausted_job_alert_reaches_telegram(job_deliverer_harness):
    """A job with target_channels=['telegram'] and a resolvable durable
    address must produce a delivered (not undeliverable) row when its
    retries are exhausted — locks in the fix confirmed in Task 2."""
    outcome = await job_deliverer_harness.deliver_for_job(
        job_deliverer_harness.make_job(
            job_id="test-job-1",
            target_channels=["telegram"],
            target_addresses={"telegram": 72055773},
        ),
        message="test failure alert",
        category="job_failed",
        urgency="high",
    )
    assert outcome.per_channel.get("telegram") == "delivered"
```

(Adapt fixture/harness names to whatever `test_undelivered_outbox_gate.py` actually defines — read that file first and match its exact setup rather than inventing new fixture names.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/notifications/test_job_failure_alert_delivery.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/notifications/test_job_failure_alert_delivery.py
# add any files touched in Step 3, if a real code gap was found and fixed
git commit -m "test(notifications): lock in retry-exhausted job-alert delivery

Diagnostic confirmed [FILL IN: 'the existing delivery path already works
correctly for target_channels-populated jobs' OR 'a gap in <specific
component>, fixed at <specific line>'] for the day.az scraper job that
triggered this investigation (goal_execution-063ab221, failure_count=2)."
```

Note: the commit message's bracketed sentence must be filled in with the ACTUAL Step 1/2 finding before committing — this is the one place in this plan where the outcome genuinely isn't knowable until the diagnostic runs; do not commit with the placeholder text still in it.

---

### Task 3: Guardrail against future parallel/ad-hoc schedulers

Prevent a repeat of "a new background timer bypasses `JobScheduler`" by asserting, in CI, that every persistent poll/ping loop in the codebase is either inside `scheduler/` or on an explicit, reasoned allowlist. Scope: only genuine **periodic background loops** (`while True:` combined with `asyncio.sleep(`) — not every `while True` (many are algorithmic, e.g. parsers, and never sleep) and not every bounded `asyncio.sleep` (many are one-shot retry backoffs inside a request/response flow, not persistent schedulers).

**Files:**
- Create: `tests/scheduler/scheduler_timer_allowlist.py`
- Create: `tests/scheduler/test_no_dummy_schedulers.py`

**Interfaces:**
- Produces: `INFRA_TIMER_ALLOWLIST: dict[str, str]` in `scheduler_timer_allowlist.py` — maps a file path (relative to repo root, POSIX-style) to a one-line reason string. Consumed only by `test_no_dummy_schedulers.py`.

- [ ] **Step 1: Write the failing guardrail test**

Create `tests/scheduler/test_no_dummy_schedulers.py`:

```python
"""Guardrail: every persistent while-True/asyncio.sleep poll loop outside
scheduler/ must be an explicitly reasoned infra-liveness exception, never an
undeclared business-domain timer bypassing JobScheduler."""

from __future__ import annotations

import re
from pathlib import Path

from tests.scheduler.scheduler_timer_allowlist import INFRA_TIMER_ALLOWLIST

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "stackowl"
_SLEEP_RE = re.compile(r"asyncio\.sleep\(")
_WHILE_TRUE_RE = re.compile(r"while\s+True\s*:")


def _files_with_persistent_poll_loop() -> set[str]:
    """A file counts as a persistent poll loop only if it contains BOTH
    ``while True:`` AND ``asyncio.sleep(`` — the actual signature of a
    long-lived background timer, not any bounded wait or algorithmic loop."""
    hits: set[str] = set()
    for path in _SRC_ROOT.rglob("*.py"):
        if "scheduler" in path.relative_to(_SRC_ROOT).parts:
            continue  # the one true authority is exempt from its own check
        text = path.read_text(encoding="utf-8")
        if _WHILE_TRUE_RE.search(text) and _SLEEP_RE.search(text):
            hits.add(str(path.relative_to(_REPO_ROOT)))
    return hits


def test_every_persistent_poll_loop_is_scheduler_or_allowlisted():
    found = _files_with_persistent_poll_loop()
    allowlisted = set(INFRA_TIMER_ALLOWLIST)
    unexplained = found - allowlisted
    assert not unexplained, (
        "New persistent background poll loop(s) found outside "
        "src/stackowl/scheduler/ with no allowlist entry — either route this "
        "through JobScheduler (a proper handler + jobs row) or, if it's a "
        "genuine infra-liveness exception (must survive the scheduler itself "
        "hanging, or a channel-protocol requirement), add it to "
        f"INFRA_TIMER_ALLOWLIST with a one-line reason: {unexplained}"
    )
    stale = allowlisted - found
    assert not stale, (
        f"Allowlist entries no longer match any file — remove stale entries: {stale}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_no_dummy_schedulers.py -v`
Expected: FAIL — `ModuleNotFoundError` for `scheduler_timer_allowlist` (doesn't exist yet).

- [ ] **Step 3: Create the allowlist, seeded from this session's audit**

Create `tests/scheduler/scheduler_timer_allowlist.py`:

```python
"""Explicit allowlist of persistent background poll/ping loops that
legitimately live outside src/stackowl/scheduler/JobScheduler.

Each entry is a conscious exception, not a default — see
test_no_dummy_schedulers.py for the check this feeds. Audited 2026-07-03
(docs/superpowers/specs/2026-07-03-scheduler-single-authority-design.md).
"""

INFRA_TIMER_ALLOWLIST: dict[str, str] = {
    "src/stackowl/service/watchdog.py": (
        "systemd sd_notify WATCHDOG=1 liveness ping — must keep running even "
        "if JobScheduler itself hangs, since detecting that IS its job."
    ),
    "src/stackowl/channels/telegram/adapter.py": (
        "Telegram long-poll/heartbeat loop — a channel-protocol requirement "
        "for message delivery, not business-domain scheduling."
    ),
    "src/stackowl/channels/whatsapp/adapter.py": (
        "WhatsApp inbound message poll loop — protocol requirement (no push "
        "webhook wired), not business-domain scheduling."
    ),
    "src/stackowl/tools/browser/sessions.py": (
        "Browser session idle-timeout/TTL cleanup loop — resource lifecycle "
        "management, not user-facing scheduled work."
    ),
    "src/stackowl/ipc/client.py": (
        "IPC reconnect retry-backoff loop — connection resilience, not a "
        "competing scheduler."
    ),
    "src/stackowl/tools/process/wait_tool.py": (
        "Bounded synchronous wait-for-subprocess tool — exits on process "
        "completion or timeout, never runs indefinitely."
    ),
    "src/stackowl/startup/orchestrator.py": (
        "Startup-phase-only retry/backoff loops during the one-shot boot "
        "sequence — not a persistent runtime scheduler."
    ),
    "src/stackowl/runtime/drain.py": (
        "Shutdown drain poll loop — bounded lifecycle operation, exits when "
        "draining completes."
    ),
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_no_dummy_schedulers.py -v`
Expected: PASS. If it still fails listing files NOT in the 8 above, read those specific files (the test output names them) and add them with a real reason — do not add an entry without reading the file first; a wrong classification defeats the guardrail's purpose.

If it fails with a `stale` allowlist entry (a listed file no longer matches), verify with `grep -l "while True" <file>` and `grep -l "asyncio.sleep(" <file>` whether the code changed since this plan was written; remove the entry if so.

- [ ] **Step 5: Run the full scheduler test directory to confirm no regressions**

Run: `uv run pytest tests/scheduler/ -v`
Expected: PASS (all pre-existing scheduler tests plus the two new ones from Task 1 and this task)

- [ ] **Step 6: Commit**

```bash
git add tests/scheduler/scheduler_timer_allowlist.py tests/scheduler/test_no_dummy_schedulers.py
git commit -m "test(scheduler): guardrail against parallel ad-hoc background timers

Asserts every while-True+asyncio.sleep persistent poll loop outside
src/stackowl/scheduler/ is an explicit, reasoned infra-liveness exception
(systemd watchdog, channel protocol polls, IPC retry, session TTL cleanup)
rather than an undeclared business-domain scheduler bypassing JobScheduler.
Seeded from the 2026-07-03 audit — see the design spec for the full
classification."
```

---

## After all 3 tasks: deployment note

None of this is live until the server restarts onto the new code (mono role has no auto-restart on SIGTERM — per this session's earlier finding, manual relaunch is required: `kill -TERM -<pgid>`, confirm exit via `pgrep`, pull latest, relaunch in a fresh tmux pane). Task 1 Step 14 (the movie-reminder data repair) explicitly depends on this — do not run it against a server still running pre-Task-1 code.
