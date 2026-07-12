# Story LAT.4: Batch chatty per-row background writes into bounded transactions

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the StackOwl DB layer,
I want long per-row background write loops (reflection_writer, skill-assembly catalog writes) to commit in bounded chunked transactions instead of one autocommit per row,
so that the single-writer SQLite connection isn't held/re-acquired hundreds of times in a row, starving the foreground turn's writes and the gateway process's cross-process heartbeat writes.

## Acceptance Criteria

1. `reflection_writer_handler.py`'s per-row loop (`_reflect_one` called once per pending outcome, line ~231) commits in chunks of a bounded size (default 50-100 rows, configurable), using the existing `DbPool.transaction()` (`pool.py:306`) — not one `execute()`-per-row autocommit as today.
2. The SkillsAssembly catalog-scan writer referenced in `pool.py`'s existing starvation-documentation comment (lines 27-38) is similarly converted to chunked transactions.
3. No single transaction spans the entire pending set unbounded — chunk size has an explicit upper bound so one background job can't itself starve the foreground turn by holding `_write_lock` for a long unbroken span.
4. Foreground turn writes and the gateway process's liveness heartbeat writes no longer show the multi-second+ contention documented in `pool.py:27-38`'s existing comment, under a reproducible load test (background job with ≥300 pending rows running concurrently with simulated foreground writes).
5. Crash-safety tradeoff is documented and accepted: a crash mid-chunk loses at most one chunk's worth of writes (not the whole pending set, not just one row) — call this out explicitly in code comments given WAL's `synchronous=NORMAL` durability semantics (already in use, not changed by this story).
6. Do NOT change WAL mode, `busy_timeout`, or scheduler/heartbeat tick cadences (30s poll, 30s liveness, 15m reflection) — profiling found these are already correctly tuned; this story is scoped to write-batching only.

## Tasks / Subtasks

- [ ] Task 1: Confirm `DbPool.transaction()` chunking contract (AC: #1, #3)
  - [ ] Read `pool.py:306`'s `transaction()` context manager fully; confirm it already supports being entered/exited repeatedly in a loop (once per chunk) without leaking connections or leaving `_write_lock` in a bad state between chunks
- [ ] Task 2: Batch `reflection_writer_handler.py`'s per-row loop (AC: #1, #5)
  - [ ] Replace the `for outcome in pending: await self._reflect_one(...)` loop (line ~231) with a chunked version: slice `pending` into groups of `CHUNK_SIZE` (named constant, default 50-100), wrap each chunk's `_reflect_one` calls in one `async with pool.transaction():` block
  - [ ] Add a code comment documenting the crash-safety tradeoff (AC #5) at the chunking call site
- [ ] Task 3: Batch the SkillsAssembly catalog-scan writer (AC: #2)
  - [ ] Locate the specific write loop `pool.py:27-38`'s comment references (the ~300-row catalog scan) and apply the same chunked-transaction pattern
- [ ] Task 4: Load test / regression guard (AC: #4)
  - [ ] Write a test that runs a simulated background job with ≥300 pending rows concurrently with simulated foreground writes, and asserts the foreground writes complete within a bounded time budget (not blocked for the whole background job's duration) — this is the direct regression guard for the starvation this story fixes
- [ ] Task 5: Explicit non-changes (AC: #6)
  - [ ] Add a code comment or story-linked note near the WAL/busy_timeout config confirming it was evaluated and intentionally left unchanged by this story (prevents a future dev from "helpfully" touching it while working nearby)

## Dev Notes

- **Root cause:** the DB is one aiosqlite connection per process, single-writer, WAL already enabled (`pool.py:121,253` — one shared `_write_lock` around every write; all reads share the same connection/worker thread). Two axes both funnel through it: (a) in-process — foreground turn and scheduler both run in the same `core`/`mono` process (`orchestrator.py:596,676`) and cannot do DB I/O concurrently by construction; (b) cross-process — the gateway process runs its own `DbPool` for the Telegram adapter + liveness heartbeat (`orchestrator.py:688`), competing for the single SQLite file-writer via `busy_timeout`. `pool.py:27-38` already documents this exact failure mode: a 24-40s catalog scan writing ~300 rows from one process starved a liveness write in the other.
- **What's NOT the problem (confirmed, do not touch):** cadences are fine — scheduler poll 30s (`scheduler.py:31`), liveness heartbeat 30s (`telegram/adapter.py:56`), reflection_writer every 15m (`assembly.py:834`). Per existing project measurement, raw DB call latency is negligible (~0.26ms/call) — the interleaving seen in logs is mostly cosmetic tick overlap, not a latency problem by itself.
- **The actual cost is writer-*hold* duration, not tick frequency:** `reflection_writer_handler.py:231`'s `for outcome in pending: await self._reflect_one(...)` does one write per row in a separate autocommit — each `_reflect_one` call is its own commit + WAL fsync + writer acquire/release. A long chatty background loop like this is what interleaves into and stalls the foreground/cross-process writer, not the scheduler's tick cadence.
- **Why bounded chunks, not one mega-transaction:** wrapping the *entire* pending set in one transaction would collapse N commits to 1, but that single transaction would then hold `_write_lock` for its *entire* span — itself starving the foreground turn for the whole duration, just via a different mechanism. Bounded chunks (50-100 rows) balance "far fewer commits" against "never hold the writer for too long in one go."
- **Rejected alternatives:**
  - **Dedicated read connection** (second aiosqlite connection for reads, since WAL allows concurrent readers) — legitimate, but writes still serialize either way, and self-heal/recycle logic would need to extend to N connections for a comparatively small gain. Worth a separate story only if profiling after this story still shows foreground *reads* (not writes) blocked.
  - **Yield-to-foreground gate** (an `asyncio.Event` the scheduler's `_poll_cycle` defers on when a turn is in flight) — only helps the in-process case (scheduler vs. turn); does nothing for the cross-process gateway heartbeat, which is the case `pool.py:27-38` actually documents as having caused real starvation. Adds coupling for narrower benefit than batching.
  - **Touching WAL mode / `busy_timeout` / tick cadences** — explicitly rejected; these are already correctly tuned per research, and touching them risks regressing durability or responsiveness for no benefit.
- **Risk:** WAL's `synchronous=NORMAL` (already in use, unchanged) means a crash mid-chunk loses at most that chunk's writes, not corruption — acceptable for reflections/liveness data, but must be called out explicitly in AC/comments so a future reviewer doesn't mistake it for a durability regression introduced by this story (it isn't — chunking makes the *blast radius* of an existing risk one chunk instead of one row, which is a small, disclosed, and acceptable tradeoff for this data class).

### Project Structure Notes

- No new files. Two call-site changes (`reflection_writer_handler.py`, the SkillsAssembly catalog-scan writer) both converted to use the *existing* `DbPool.transaction()` context manager — no new DB abstraction needed.
- Explicitly scoped away from `pool.py`'s WAL/busy_timeout config and the scheduler's cadence constants — do not touch those files' tuning values as part of this story.

### References

- [Source: src/stackowl/db/pool.py#L27-38] — existing comment documenting the exact starvation failure mode this story fixes
- [Source: src/stackowl/db/pool.py#L121,253] — `_write_lock`, single-writer serialization
- [Source: src/stackowl/db/pool.py#L306] — `transaction()`, the existing chunking primitive this story reuses
- [Source: src/stackowl/memory/reflection_writer_handler.py#L231] — `_reflect_one` per-row loop, primary change site
- [Source: src/stackowl/scheduler/scheduler.py#L31] — poll cadence (confirmed fine, not touched)
- [Source: src/stackowl/channels/telegram/adapter.py#L56] — liveness heartbeat cadence (confirmed fine, not touched)
- [Source: src/stackowl/skills/assembly.py#L834] — reflection_writer cadence (confirmed fine, not touched)
- [Source: src/stackowl/startup/orchestrator.py#L596,676,688] — core/mono process vs. gateway process DB pool split

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (research); implementer TBD

### Debug Log References

### Completion Notes List

### File List
