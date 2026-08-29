# CLAUDE.md

Rebuilt from what work in this repo has actually needed. Add a line only when it has
been earned — something non-obvious that cost time or nearly caused damage. Keep it
short; the previous version grew to 180 lines and was cleared on 2026-08-10.

## Read these first

`progress.yml` is the state of record — `current` says where we are. Then
`docs/reference-mapping/PROCESS.md` (the method) and `DOC_STANDARD.md` (what every
document must contain). They carry the working rules; this file only holds what they
do not.

## Landmines

**Never run `graphify update` or `graphify hook install`.** Both were tried and
rejected: the packaged incremental path collapses a 12k-node graph to under 700 on a
single-file change, and `update <path>` writes to `<path>/graphify-out/` instead of the
repo-root graph this project uses. The PreToolUse hook actively suggests `graphify
update` when the graph is stale — **ignore that suggestion.** To refresh, re-run
`/graphify src` manually. Confirmed still live 2026-08-11: the hook fired repeatedly
across a full session.

**`graphify query` is oriented at `src/` only.** It cannot answer questions about
`progress.yml`, `docs/`, or `tests/`, and on a broad question it returns ~1,100 nodes,
which is worse than useless. Use it for "where does X live in src", not for everything.

**Never a full `pytest` run** — it hangs on this box. Targeted paths with timeouts.
A hanging test is a failing test.

**Restart with `./start.sh`, then verify via `~/.stackowl/logs/stackowl.jsonl`, never a
PID.** A deletion is not live until the process holding the old code is gone. Check the
core's start time against your last commit before believing anything you measure.

## Loop-oriented, and never a second engine

**Bakir's standing rule, 2026-08-17: everything the platform does is a TASK on ONE loop,
and no implementation may duplicate logic or code that already runs work.**

Every trigger is a task — a chat question, a scheduled run, a sub-goal an agent creates for
itself. One table, one loop, claimed atomically and run in PARALLEL (five pending rows =
five concurrent workers, no ordering). A failure returns the row to pending *with what
failed*, so the next attempt is constrained rather than blind.

**A task is complete when its outcome reached its DESTINATION, not when the function
returned.** Ask a question on Telegram and the task is done only once the answer is
delivered there. Every task therefore carries a destination and an achievement condition.
This is the same rule as "measure the EFFECT" below, applied to work instead of tools.

**Before building anything that runs, retries, schedules or tracks work: find the existing
loop and extend it.** This rule is earned — the tree already accumulated FOUR overlapping
engines: `tasks` (live), `retry_queue` (live), `objectives`/`objective_subgoals` (~2,400
lines, driver firing every 60s against an empty table), and `job_queue` (**zero references
anywhere in `src/`**). Never add a second queue, a second retry path, or a second status
column. Sub-tasks are rows with a parent and `depends_on`, so a graph is edges between
rows — not a second system.

The one place the claim-and-dispatch is already correct is `scheduler.py`: `asyncio.gather`
over due rows behind a CAS claim (`UPDATE … SET status='running' WHERE status='pending'`)
so concurrent dispatchers can never double-run. Copy that shape; do not invent another.

## Logging

Named loggers via `stackowl.infra.observability` (`log.tool`, `log.engine`,
`log.memory`, `log.gateway`, …), 4-point on every `execute()`, and every `except` logs.

**Production runs at INFO. A `log.*.debug` line does not exist when you need it.** This
is not a style note: D08.1's fourth acceptance check sat open for days because its only
evidence line was DEBUG, and no volume of live traffic could ever have closed it. If a
log line is the evidence for a claim, it must be INFO — and run the query that would
close the claim *before* you need it, to confirm it returns something.

## Measuring is the job, and the instrument lies too

**Check what a denominator is MADE OF, not just that it is non-zero.** "0 exemptions
over 7 browser calls" looked like a failure and was 0-over-0: all seven were
read-severity tools that never reach the branch. A zero numerator over a zero
denominator is not a pass either.

**A test that passes immediately may be vacuous.** Three round-trip tests passed
because `get()` uses `SELECT *` and picks up new columns for free; the LOOP's
`claimable()` builds an explicit column list and returned `None`. Test the path
production takes.

**A fixture that cannot show the bug proves nothing.** A fake page with no
`close()` made eviction "pass" through its own except branch.

**Log greps: the field is `"msg": "` WITH A SPACE after the colon**, and the inner
key is `fields`. A regex without the space returns empty against a 14MB file — four
false negatives in one session before a control on a known-present string exposed
the instrument rather than the system.

**Count incidents, not log lines.** "19 database-is-locked events" was 19 LINES; one
contention moment emits four.

**Sweep EVERY engine before claiming silence.** Parking `tasks` looked like it
stopped a runaway; `retry_queue` and `objective_subgoals` were still armed and the
user was still being messaged hours later.

**A gateway-side fix is not live until `./start.sh`.** CodeWatcher exec-replaces the
CORE only. A heartbeat fix sat dead for an hour while being reported as shipped.

## The shapes that account for nearly every real defect here

1. **A write with no reader**, or an actuator wired on only some paths. Measure the
   EFFECT, never trust the call.
2. **Test doubles that stopped resembling the real thing.** Generate fixtures from the
   same constants the code uses where you can.
3. **Two copies of one rule.** One source; have the other ask it.
4. **No decay.** Anything that only appends will poison its reader. And when you remove
   a writer, ask what was *bounding* — or *triggering* — the thing it fed.
5. **Built but not wired.** The capability exists, works, and nothing calls it.
   `stackowl.supervisor` did backoff-restart and escalation while every channel
   receive loop ran as a bare task. `idempotency_key` was stored, read back, and
   had no unique index. `committed_facts_fts` still indexes 1,112 rows of content
   that no longer exists because its writer was removed. **A feature ships ON: if
   nothing sets the flag, you shipped decoration** — D03.4's result cap went out
   with no tool declaring one and could never fire.
6. **Deleting a row while its writer lives.** Migration 0125 deleted the
   `retry_sweep` job at 00:31:02; scheduler assembly re-seeded it at 00:31:33.
   Every boot, for thirty-one seconds. Remove the writer, not the row.
