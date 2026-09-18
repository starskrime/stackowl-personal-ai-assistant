---
title: 'The journal stays small on small hardware'
type: 'feature'
created: '2026-09-18'
status: 'done'
baseline_revision: 'e6ea3b554bf6b0959ee29555c807409f6b8072c3'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '_bmad-output/implementation-artifacts/epic-2-context.md'
  - '_bmad-output/planning-artifacts/architecture/architecture-stackowl-personal-ai-assistant-2026-09-12/ARCHITECTURE-SPINE.md'
warnings: ['oversized']
deferred:
  - summary: >-
      assembly.py derives the journal WAL sidecar path via a fresh,
      independent default_db_path() call rather than from the actual
      registered DbPool instance; a pool ever opened against a non-default
      path would silently report 0 WAL bytes forever.
    evidence: |-
      Verified true. Mirrors several existing sibling call sites in the same
      file (not a new pattern this diff introduced), and the real fix is
      deeper path-plumbing through assembly.py's callers -- more than a
      direct correction.
    location: 'src/stackowl/scheduler/assembly.py'
    severity: low
  - summary: >-
      One registered retention-hold-source checker raising or returning a
      bad value would abort held_cursors() entirely, blocking journal_prune's
      delete+checkpoint every pass.
    evidence: |-
      Verified real, but RetentionHoldRegistry has zero real registrations
      today (Epic 3 doesn't exist yet) -- matches Story 2.10's own
      established "not reachable today, fix is non-trivial" precedent for
      the sibling RecordReaderRegistry.
    location: 'src/stackowl/journal/retention_holds.py::RetentionHoldRegistry.held_cursors'
    severity: low
  - summary: >-
      Whether the pre-existing test_retention_tripwire.py's job_runs check
      should be narrowed, since AD-4 does not actually reach job_runs (no
      journal event references it) -- narrowing it would let
      _RUN_HISTORY_RETENTION_DAYS revert from 30 back to 7.
    evidence: |-
      Verified: journal/coverage.py excuses job_runs as unjournaled
      (_REASON_PRE_EPOCH), and job.* events' record_ref points at the jobs
      table, not job_runs. The 7->30 raise this story made satisfies the
      pre-existing tripwire (Story 2.6), not a direct AD-4 reference --
      logged in full as DW-30. A real production-retention increase made to
      satisfy a test rather than the architecture rule the test proxies for;
      narrowing the tripwire's scope is a deliberate policy call for the
      owner, not this story to make unilaterally.
    location: 'tests/journal/test_retention_tripwire.py; deferred-work.md DW-30'
    severity: medium
  - summary: >-
      epics.md's Story 2.11 AC4 (the per-turn/command journal write budget --
      WARNING + health degrade, p95/per-command dimensions) is not touched by
      this diff.
    evidence: |-
      Verified epics.md literally lists AC4 under Story 2.11. Its core
      behavior (event-count budget, WARNING, health degrade, never-drop) was
      already built by Story 2.7 (journal/turn_budget.py,
      journal/health.py::note_budget_exceeded); the specific remaining gaps
      (decay/reset, p95 dimension, per-command scope, settings-surfacing) are
      already explicitly pre-assigned to Story 2.12 by name in DW-19/DW-21/
      DW-22, written during Story 2.7's own review. A cross-reference note
      was added to DW-19 closing this story's own "verify against epics.md"
      instruction.
    location: 'src/stackowl/journal/turn_budget.py; deferred-work.md DW-19/DW-21/DW-22'
    severity: low
---

<intent-contract>

## Intent

**Problem:** The journal has no retention/prune mechanism at all -- `journal_events` grows forever, `journal/retention.py`'s `PROVISIONAL_JOURNAL_RETENTION_DAYS = 1` is an admitted disconnected placeholder that no production job reads, and two real subsystem prune windows (`TaskLoopSettings.prune_completed_after_days=1`, `db_reclaim._RUN_HISTORY_RETENTION_DAYS=7`) sit below AD-6's real 30-day default, so raising retention alone would strand journal events referencing already-pruned rows (DW-17, AD-4).

**Approach:** Add a `journal` settings section (`retention_days=30`, a provisional `wal_size_budget_bytes`), raise `journal/retention.py`'s constant to derive from it, and in the SAME change raise both real subsystem windows to 30 (DW-17). Add one idempotently-seeded scheduler job (`journal_prune`, mirroring `db_reclaim.py`'s shape) that batch-deletes `journal_events` rows older than retention -- excluding cursors a new, currently-callerless `RetentionHoldRegistry` reports held (mirrors `records.py::RecordReaderRegistry`'s shape) -- under `PRAGMA secure_delete=ON`, then runs `PRAGMA wal_checkpoint(TRUNCATE)` and logs its result. Extend `JournalHealthContributor` to degrade when the WAL file stays over budget across consecutive passes.

## Boundaries & Constraints

**Always:** `journal_prune` is the only writer that ever issues `DELETE FROM journal_events` (proven by a tripwire scan); it runs under the shared `DbPool`, never a second connection. Held cursors are excluded from every delete, computed once per pass via `RetentionHoldRegistry.held_cursors()`. The prune job reads the live `Settings().journal.retention_days`/`wal_size_budget_bytes`, never `journal/retention.py`'s constant (that constant stays tripwire-only, matching its existing documented contract). Raising `TaskLoopSettings.prune_completed_after_days` and `db_reclaim._RUN_HISTORY_RETENTION_DAYS` to 30 happens in this same change, with their stale-value-stating docstrings rewritten (not left stale) per `scripts/superseded_constants.py`'s existing 100->7 precedent in that same file. `docs/stackowl.yaml.example` is regenerated after the new settings section is added. 4-point logging on `execute()` and `health_check()`'s new branch.

**Never:** Do not wire real retention-hold callers -- Epic 3 (Needs-you items) does not exist yet; the registry ships provably correct and currently vacuous, exactly like Story 2.10's reader registry shipped before `bridge/` existed. Do not implement AD-38's p95/per-command write-budget dimensions or the budget-exceeded-count decay/reset semantics (DW-21, DW-22) -- both explicitly deferred to Story 2.12 by epics.md's own AC5 ("budget values are provisional settings until spike B2 reports") and pre-existing deferred-work entries. Do not touch `PROVISIONAL_TURN_EVENT_BUDGET` (DW-19) -- a different budget, a different story. Do not restart the live production core/gateway process. Do not edit `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Normal prune pass | rows older/newer than retention mixed | only rows older than `retention_days` deleted, in <=5000-row batches, capped at 50000/pass | never raises; logs+degrades on failure |
| Held old row | a row older than retention, its cursor registered as held | row survives the pass | n/a |
| Repeated boot | scheduler seeds twice | exactly one `journal_prune` job row exists | n/a |
| Checkpoint after prune | prune deletes >=1 row | `PRAGMA wal_checkpoint(TRUNCATE)` runs once; busy/log/checkpointed logged | n/a |
| WAL over budget, 3 consecutive passes | `-wal` file size > `wal_size_budget_bytes` each time | health contributor reports `degraded` with a remedy | resets once a pass is back under budget |
| Duplicate hold-source registration | same name registered twice | second call raises `ValueError`, first stays registered | raises loudly |
| Nothing else deletes | any other module | tripwire scan finds no other `DELETE FROM journal_events` | fails the tripwire, names the file |

</intent-contract>

## Code Map

- `src/stackowl/config/journal_settings.py` (NEW) -- `JournalSettings(BaseModel, frozen, extra=forbid)` mirroring `config/task_loop_settings.py`'s shape exactly: `retention_days: int = Field(default=30, ge=1, description=...)` (AD-6/NFR44); `wal_size_budget_bytes: int = Field(default=67_108_864, ge=1, description="provisional pending Story 2.12's benchmark, mirrors turn_budget.py's PROVISIONAL_* precedent")`.
- `src/stackowl/config/settings.py:34,1114` -- import `JournalSettings`; add `journal: JournalSettings = Field(default_factory=JournalSettings)` to `Settings`, same pattern as `task_loop`.
- `src/stackowl/journal/retention.py` -- rename `PROVISIONAL_JOURNAL_RETENTION_DAYS` -> `JOURNAL_RETENTION_DAYS = JournalSettings().retention_days` (default-instance derived, single source of truth with the settings model); rewrite module docstring: this is no longer a disconnected placeholder, it mirrors the real AD-6 default, but stays tripwire-only per its existing "no production code reads this constant" contract (the real job reads live `Settings()`, not this default-only derivation, so a `stackowl.yaml` override doesn't silently desync the tripwire's own honesty check).
- `src/stackowl/journal/retention_holds.py` (NEW) -- `RetentionHoldRegistry` class: `dict[str, Callable[[], frozenset[int]] | Callable[[], Awaitable[frozenset[int]]]]` + `threading.RLock`, `register_hold_source(name, checker)` refuses a duplicate name (raises `ValueError`, 4-point logged, mirrors `records.py::RecordReaderRegistry.register`'s exact shape at lines 106-131), `async def held_cursors() -> frozenset[int]` awaits/calls every registered checker and unions results, `reset_for_tests()`. Module singleton + `get_retention_hold_registry()`/`reset_retention_holds_for_tests()` free functions, same wrapper shape as `records.py:153-165`.
- `src/stackowl/journal/health.py` -- add `wal_over_budget_streak: int = 0`, `last_wal_bytes: int | None`, `last_wal_budget_bytes: int | None` to `JournalHealthState`; add `note_wal_size(bytes_, budget_bytes)` writer (increments/resets the streak, mirrors `note_budget_exceeded`'s lock+log shape); `health_check()` gains a third branch (after the existing two, priority: failure > budget > WAL) degrading at `wal_over_budget_streak >= 3` with a remedy naming the WAL file and budget; extend `reset_for_tests()`.
- `src/stackowl/db/migrations/0149_journal_events_occurred_at_index.sql` (NEW) -- `CREATE INDEX IF NOT EXISTS idx_journal_events_occurred_at ON journal_events (occurred_at);` (no index on `occurred_at` alone exists today; the prune query filters on it directly, not through `type`).
- `src/stackowl/scheduler/handlers/journal_prune.py` (NEW) -- `JournalPruneHandler(JobHandler)` mirroring `db_reclaim.py`'s full shape (`handler_name="journal_prune"`, `self._pool: DbPool`, `self._db_path: Path` for WAL-sidecar stat): `execute()` -- ENTRY log; reads `get_settings().journal.{retention_days,wal_size_budget_bytes}`; `cutoff = now - retention_days`; `held = await get_retention_hold_registry().held_cursors()`; batched `DELETE FROM journal_events WHERE occurred_at < ? AND cursor NOT IN (held...) ORDER BY cursor LIMIT 5000` loop (`_PRUNE_BATCH=5_000`, `_PRUNE_MAX_PER_PASS=50_000`, same cap style as `db_reclaim.py:_PRUNE_MAX_PER_PASS`), `PRAGMA secure_delete=ON` issued once before the loop via `self._pool.execute(...)`; after the loop (even if 0 rows deleted, to still checkpoint and measure WAL), `rows = await self._pool.fetch_all("PRAGMA wal_checkpoint(TRUNCATE)")`, unpack `(busy, log_frames, checkpointed_frames)`; stat `Path(str(self._db_path) + "-wal")` (`FileNotFoundError` caught specifically -> size 0, logged at DEBUG, never a silent bare except); `note_wal_size(wal_bytes, wal_size_budget_bytes)`; on any `Exception` from the delete/checkpoint work, log ERROR with the exception, call `journal.health.note_failure(remedy)`, return a failed-but-non-raising `JobResult` (mirrors `db_reclaim.py`'s never-fail-a-tick contract); EXIT log with `pruned_count`, `busy`, `log_frames`, `checkpointed_frames`, `wal_bytes`, `duration_ms`. `register_journal_prune_handler(pool: DbPool, db_path: Path) -> None` factory mirroring `db_reclaim.py:538-545`.
- `src/stackowl/scheduler/assembly.py:~313` -- register via `register_journal_prune_handler(pool, db_path)` alongside the `db_reclaim` registration; `:~998-1001` -- seed via `_seed_minutes_schedule(db, handler_name="journal_prune", schedule="every 1h", interval_minutes=60)`, same call shape as the adjacent `db_reclaim` seed.
- `src/stackowl/config/task_loop_settings.py:71-80` -- raise `prune_completed_after_days` default `1` -> `30`; rewrite the value-stating docstring prose (states the new value/rationale: DW-17's AD-4 reconciliation, not the old "Bakir: one day" text left stale).
- `src/stackowl/scheduler/handlers/db_reclaim.py:172-232` -- raise `_RUN_HISTORY_RETENTION_DAYS` `7` -> `30`; rewrite the extensive value-stating docstring the same way this file's own prior 100->7 rewrite did ("remove the other statements, not sync them" -- its own established precedent).
- `tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py:90` -- update `== 7` -> `== 30`.
- `tests/audit/test_a_tunable_states_its_value_in_one_place.py` -- run `uv run python scripts/superseded_constants.py` after the two constant changes; add `_ACCEPTED` entries (format matches the existing 100->7 entries at lines ~97-122) for any newly-flagged stale-prose site the rewrite didn't already fix.
- `tests/journal/test_retention_tripwire.py` -- update the two `PROVISIONAL_JOURNAL_RETENTION_DAYS` references to `JOURNAL_RETENTION_DAYS`; real windows (now 30/30) still pass against 30; control test unchanged in spirit.
- `docs/stackowl.yaml.example` -- regenerate via `uv run python scripts/gen_config_example.py --write` after the `journal` settings section is added (fails `tests/test_the_example_config_cannot_go_stale.py` otherwise).
- `tests/journal/test_retention.py` (NEW) -- settings default is 30; `JOURNAL_RETENTION_DAYS` derives from it, not a second hand-typed literal.
- `tests/journal/test_retention_holds.py` (NEW) -- register/duplicate-refusal/`held_cursors` union (sync and async checkers)/`reset_for_tests`.
- `tests/scheduler/handlers/test_journal_prune.py` (NEW) -- every I/O matrix row; idempotent-seeding test mirrors `test_the_run_history_is_finally_bounded.py`'s own style; the "nothing else deletes" scan is `@pytest.mark.tripwire`.
- `tests/journal/test_health_contributor.py` -- extend with the WAL-budget-streak degrade/reset cases.

## Tasks & Acceptance

**Execution:** `journal_settings.py` + `settings.py` wiring -> `retention.py` rename/rederivation -> `retention_holds.py` -> `health.py` WAL state -> `0149_*.sql` -> `journal_prune.py` -> `assembly.py` registration+seeding -> `task_loop_settings.py`/`db_reclaim.py` value raises + docstring rewrites -> update the two dependent test files -> `superseded_constants.py` reconciliation -> regenerate `stackowl.yaml.example` -> new tests.

**Acceptance Criteria:**
- Given the `journal` settings section, when no value is set, then retention defaults to 30 days and a setting may change it (AD-6, NFR44).
- Given the scheduler's idempotent job seeding, when the platform boots repeatedly, then exactly one `journal_prune` job exists.
- Given the prune job runs, when events are older than retention, then it deletes them in bounded batches with `secure_delete` on, checkpoints the WAL afterward, logs the checkpoint result, and nothing else deletes journal rows.
- Given a registered retention hold, when the prune job runs, then the held event is never deleted.
- Given the WAL file stays above its size budget across consecutive prune passes, then the journal health contributor degrades with a remedy.
- Given `TaskLoopSettings.prune_completed_after_days` and `db_reclaim._RUN_HISTORY_RETENTION_DAYS`, when this change lands, then neither is shorter than the new 30-day journal retention (`tests/journal/test_retention_tripwire.py` still passes, honestly, against real values).

## Spec Change Log

## Review Triage Log

- **medium, patch** (orchestrator, own Verify-step reading of the diff) — `journal_prune.py` sets `PRAGMA secure_delete=ON` on `DbPool`'s single, process-lifetime connection (confirmed: `db/pool.py`'s own module docstring, "single aiosqlite connection for StackOwl's runtime lifetime") and never resets it. From the first prune pass onward this silently applies secure-delete overhead to every DELETE/UPDATE anywhere in the app, not just journal_prune's own deletes. Fix: reset it after the batch-delete loop (try/finally so a mid-loop failure still resets it).
- **medium, patch** (Blind Hunter, verified by me directly against a live SQLite connection) — the prune cutoff is built via SQL `datetime('now','-{N} days')` (space-separated), but `occurred_at` is written as Python `datetime.now(UTC).isoformat()` (T-separated). Verified: a row from earlier the same calendar day as the cutoff lexicographically compares as NOT older, delaying its prune by up to ~1 day. Fix: compute the cutoff in Python matching `occurred_at`'s own write format, bind as a parameter (also keeps the new index usable, unlike wrapping the column in `datetime()`).
- **low, patch** (Blind Hunter) — `deferred-work.md`'s DW-17 entry stays `status: open` with pre-diff text even though this diff delivers exactly what its own "what would settle it" clause asked for. Fix: mark it resolved with an update note (mirrors DW-20's own precedent).
- **low, patch** (Blind Hunter) — `journal/retention.py`'s new docstring claims the prune job "reads `get_settings().journal.retention_days` directly," but no `get_settings()` function exists anywhere in this codebase (confirmed by grep) — the real code calls `Settings().journal.retention_days`. Fix: correct the docstring.
- **low, reject** (Blind Hunter) — the WAL checkpoint's `busy` flag is logged but never wired into the WAL-degraded health message to disambiguate "reader holding it open" vs "budget too tight." Real but cosmetic; the fix threads a new parameter through `note_wal_size`/state/message. Rejected: unlikely to be hit in everyday use (only fires after 3 consecutive degraded passes) and the fix is more than a direct correction.
- **low, reject** (Blind Hunter) — the WAL-degraded message reports raw byte counts, not "64 MiB"-style human-readable text. Rejected: cosmetic, fix requires a new formatting helper — more than a direct correction.
- **low, reject** (Blind Hunter) — `JournalHealthState` records WAL byte counts but not which WAL file path was measured; an operator must cross-reference the scheduler log (which already carries `wal_path`). Rejected: marginal benefit, information already available elsewhere, fix adds a new field for no verified gap.
- **low, reject** (Blind Hunter) — `note_wal_size` is only called on a successful pass; on `execute()` failure the streak freezes rather than resetting. Verified but benign: the failure branch already calls `note_failure`, which takes priority in `health_check()` and is a more informative signal than the frozen WAL streak; once pruning resumes, the streak resumes accurately. Rejected: no bad outcome — the higher-priority signal already covers it.
- **medium, patch** (Blind Hunter) — no test asserts `PRAGMA secure_delete=ON` is actually issued during a prune pass (only batch-size/cap markers are checked). Fix: add a test verifying the PRAGMA is toggled during the delete and reset afterward, tied directly to the secure_delete-reset fix above.
- **medium, patch** (Blind Hunter) — `_wal_bytes()`'s blocking `Path.stat()` runs directly inside an `async def` with no `asyncio.to_thread` offload, unlike this codebase's own established fix for the same situation in `health/contributors.py`; the B9 boundary guard's pattern list doesn't catch filesystem stat calls, so it passes today anyway. Fix: wrap the stat call in `asyncio.to_thread`.
- **low, defer** (Blind Hunter) — `assembly.py` derives the WAL sidecar path via a fresh, independent `default_db_path()` call rather than from the actual registered `db: DbPool` instance; if that pool is ever opened against a non-default path, the WAL signal would silently stat the wrong file and report 0 bytes forever. Verified true, but mirrors several existing sibling call sites in the same file (not a new pattern this diff introduced) and the real fix is deeper path-plumbing through `assembly.py`'s callers — more than a direct correction. Deferred.
- **medium, patch** (Edge Case Hunter) — `settings = Settings().journal` runs BEFORE the `try:` block in `execute()`, so a `Settings()` construction failure escapes uncaught, breaking the handler's own never-fail-a-tick contract. Fix: move the line inside `try:`.
- **medium, patch** (Edge Case Hunter) — the `except Exception` branch always returns `metadata={"pruned_count": 0}`, even when the DELETE loop already committed real rows before a later step (checkpoint/WAL stat) failed, misreporting a partial success as a total failure with 0 rows pruned. Fix: track the real deleted count and report it in the except branch.
- **medium, patch** (Edge Case Hunter) — `_wal_bytes()` only catches `FileNotFoundError`; a `PermissionError`/other `OSError` propagates to the outer `except Exception`, misreporting an otherwise-successful prune+checkpoint pass as a total failure. Fix: broaden the catch to `OSError`.
- **low, defer** (Edge Case Hunter) — one registered hold-source checker raising or returning a bad value aborts `held_cursors()` entirely, blocking prune's delete+checkpoint every pass. Verified real, but `RetentionHoldRegistry` has zero real registrations today (Epic 3 doesn't exist yet) — matches Story 2.10's own established "not reachable today, fix is non-trivial" precedent for the sibling `RecordReaderRegistry`. Deferred.
- **medium, patch** (Edge Case Hunter) — `health_check()` checks the pre-existing `budget_exceeded_count > 0` branch (a lifetime counter that never resets, DW-21) BEFORE the new `wal_over_budget_streak` branch, so once that counter ticks up even once, the WAL-over-budget signal can never surface again for the life of the process. Fix: move the WAL-budget check above the budget_exceeded_count check (the WAL streak is self-correcting and should not be permanently masked by a stale lifetime counter).
- **medium, patch** (Edge Case Hunter) — `journal_prune.py`'s `except Exception` branch calls the shared `note_failure(remedy)`, whose own docstring says "Called only from recorder.py" — journal_prune failures are conflated with `journal.record()` failures into the same counter/message, and an unrelated successful `record()` call can silently clear an active prune-failure signal. Fix: add a separate state field/writer/health-check branch for journal_prune's own failures, mirroring the WAL-streak shape already built in the same diff.
- **medium, patch** (Verification Gap) — no test drives `execute()` through 3 real consecutive passes with an actual over-budget WAL to prove the health-degrade wiring end-to-end; existing tests only unit-test `note_wal_size` directly or call `execute()` once and assert "ok". Fix: add an integration test forcing the measured WAL over budget across 3 real `execute()` calls, asserting `health_check().status == "degraded"`.
- **low, patch + medium, defer** (Verification Gap, "Other findings"; independently verified by me against `journal/job_events.py` and `journal/coverage.py`) — the new docstring/test-comment claim "journal events reference `job_runs` rows (`job.*` events, `journal/job_events.py`)" is factually wrong: `job.*` events' `record_ref` points at `jobs` (`journal/job_events.py:32`, `_TABLE = "jobs"`), and `job_runs` is itself explicitly excused as unjournaled in `journal/coverage.py` (`"job_runs": _REASON_PRE_EPOCH`) — no journal event references `job_runs` at all. The **doc-correction** (patch, low, caused by this diff): fix the stated reason to the accurate one — the pre-existing `test_retention_tripwire.py` (Story 2.6) already checks `_RUN_HISTORY_RETENTION_DAYS` against journal retention, and DW-17 assigned this story to reconcile every window that tripwire checks, regardless of whether AD-4 strictly requires it for `job_runs` specifically. The **open design question** (defer, medium, real but beyond a direct correction) — whether the pre-existing tripwire's inclusion of `job_runs` should be narrowed since AD-4 does not actually reach it, which would let `_RUN_HISTORY_RETENTION_DAYS` revert to 7 — is a deliberate production-retention policy call this story does not make unilaterally; logged to `deferred-work.md` for the owner.
- **defer** (Intent Alignment) — AC4 (the per-turn/command journal write budget: WARNING + health degrade, p95/per-command dimensions) is not touched by this diff. Verified: epics.md literally lists it under Story 2.11's own AC block. However, its core behavior (event-count budget, WARNING, health degrade, never-drop) was already built by Story 2.7 (`journal/turn_budget.py`, `journal/health.py::note_budget_exceeded`), and the specific remaining gaps (decay/reset semantics, p95 dimension, per-command scope, "provisional settings" surfacing) are already explicitly pre-assigned to Story 2.12 by name in `deferred-work.md`'s DW-19/DW-21/DW-22, written during Story 2.7's own review. Not re-scoped into this diff. A cross-reference note added to DW-19 closes the "verify against epics.md" loop this story's own dispatch asked for.
- **false** (Intent Alignment) — the diff's `retention_days` field docstring calls 30 "the architecture default... provisional until Story 2.12's benchmark," which the auditor flagged as short of the task's own paraphrase "real, considered value." Verified: the diff's framing matches epics.md/Story 2.12's own text exactly (Story 2.12 AC2 explicitly may still revise "the resulting values for... the retention default"). The divergence is against an external paraphrase, not the intent itself. No defect.
- **low, patch** (Intent Alignment) — `journal_prune.py::execute()`'s 4-point logging comments mark only `# 1. ENTRY` / `# 4. EXIT`, omitting `# 2. DECISION` / `# 3. STEP` markers this same diff's `retention_holds.py::register_hold_source` carries. Fix: add the two missing numbered comments at the existing decision/loop points.

### 2026-09-18 — Review pass

- verdicts: 23 findings — high 0, medium 11, low 10, false 1, maybe-false 0 (defer counted within their listed severities above)
- findings: the 23 rows above, one per finding from every layer including the orchestrator's own secure_delete finding surfaced during step-03's Verify.

### 2026-09-18 — Post-patch-round independent re-verification (orchestrator)

- **medium, patch** (orchestrator, my own full `./scripts/tripwires.sh` run after the 14-item patch round landed) — `journal_prune.py::_wal_bytes()`'s `FileNotFoundError` branch logged at DEBUG while its `OSError` branch (added during the patch round, finding above) logs at WARNING -- an asymmetric decline `tests/audit/test_a_background_subsystem_that_declines_still_says_so.py`'s ratchet tripwire correctly flagged (`test_no_background_subsystem_declines_without_a_record` FAILED, naming `journal_prune.py::_wal_bytes`; production writes no DEBUG at all). Verified real and fixed directly by me (not routed back through the implementation subagent, given its triviality and my own full-suite re-verification authority at this stage): promoted the branch to INFO, since a fully-checkpointed WAL genuinely reaching 0 bytes is a real, healthy, production-worthy record, not silence. Re-ran the specific tripwire test and the full `./scripts/tripwires.sh` afterward: both green.

## Design Notes

**Why the retention-hold registry ships now with zero callers:** epics.md's AC text for this story explicitly requires "it never deletes an event held by a registered retention hold" as a present-tense behavior, not a deferred one -- Epic 3 (Needs-you items, the first real hold source) doesn't exist yet, exactly the shape Story 2.10 already established as acceptable (`RecordReaderRegistry` shipped with zero live callers, tracked by DW-29). Mirroring `records.py`'s class shape (not a bare module dict) keeps the two registries consistent for a future reader.

**Why `journal/retention.py`'s constant derives from `JournalSettings()` instead of hand-typing `30`:** the exact drift class `test_retention_tripwire.py`'s own docstring warns against -- "not restated as a copy" -- a second literal `30` a settings-model default could silently diverge from.

**Why raising the two real subsystem windows to exactly 30 (not higher):** DW-17's own text only requires "none is shorter than the new value" -- 30 is the minimal change satisfying that, avoiding an unbounded task-table disk-growth increase beyond what AD-4 actually requires on a Jetson-class host. This is a real production-retention increase (tasks: 1->30 days, job_runs: 7->30 days), previously owner-authorized at the lower values specifically to keep disk use tight -- flagged for the coordinator's awareness in the final report, not silently absorbed.

## Verification

**Commands:**
- `uv run pytest tests/journal/ tests/scheduler/ tests/audit/test_a_tunable_states_its_value_in_one_place.py -q` -- expected: all pass.
- `./scripts/tripwires.sh` -- expected: exits 0, ruff/mypy baselines unchanged or improved.
- `uv run python scripts/gen_config_example.py --write && git diff --stat docs/stackowl.yaml.example` -- expected: regenerated file matches what's committed (no diff after commit).

**Manual checks (if no CLI):**
- Left to the coordinator: none required -- this story adds a new scheduled job and settings but does not touch the live core/gateway process; the seeded job only takes effect on the platform's own next real boot.

## Auto Run Result

**Summary of implemented change:** The journal gained a real, considered retention default (`config/journal_settings.py::JournalSettings`, `retention_days=30`, AD-6/NFR44), replacing `journal/retention.py`'s admitted 1-day placeholder (now `JOURNAL_RETENTION_DAYS`, derived from that settings model's default rather than hand-typed, staying tripwire-only per its own documented contract). A new hourly, idempotently-seeded scheduler job (`scheduler/handlers/journal_prune.py::JournalPruneHandler`, mirroring `db_reclaim.py`'s shape) deletes `journal_events` rows past retention in bounded batches (`_PRUNE_BATCH=5000`, `_PRUNE_MAX_PER_PASS=50000`) under `PRAGMA secure_delete=ON` (reset back to `OFF` afterward), excludes cursors a new, currently-callerless `RetentionHoldRegistry` (`journal/retention_holds.py`, mirrors `records.py::RecordReaderRegistry`'s shape) reports held, then runs `PRAGMA wal_checkpoint(TRUNCATE)` and logs its result. `journal/health.py` gained two new independent signals: a WAL-over-budget streak (degrades after 3 consecutive over-budget passes, self-correcting) and `journal_prune`'s own failure streak (kept separate from `journal.record()`'s failure signal to avoid conflating the two). Per DW-17, the same change reconciled both real subsystem prune windows the pre-existing `test_retention_tripwire.py` (Story 2.6) checks: `TaskLoopSettings.prune_completed_after_days` (1->30, genuinely AD-4-motivated -- tasks are referenced by `task.*` events) and `db_reclaim._RUN_HISTORY_RETENTION_DAYS` (7->30, satisfying that pre-existing tripwire rather than a direct AD-4 reference -- `job_runs` is not actually referenced by any journal event, logged as DW-30).

**Files changed:**
- `src/stackowl/config/journal_settings.py` (new) -- `JournalSettings`: `retention_days=30`, `wal_size_budget_bytes=64MiB` (provisional).
- `src/stackowl/config/settings.py` -- wires `journal: JournalSettings` into `Settings`.
- `src/stackowl/journal/retention.py` -- constant renamed `JOURNAL_RETENTION_DAYS`, derives from `JournalSettings()`'s default.
- `src/stackowl/journal/retention_holds.py` (new) -- `RetentionHoldRegistry`.
- `src/stackowl/journal/health.py` -- WAL-over-budget streak + `journal_prune`'s own independent failure streak; `health_check()` branch priority reordered (failures > prune failures > WAL streak > lifetime budget-exceeded count, so a stale lifetime signal can never permanently mask a fresher one).
- `src/stackowl/db/migrations/0149_journal_events_occurred_at_index.sql` (new) -- index the prune query needs.
- `src/stackowl/scheduler/handlers/journal_prune.py` (new) -- the prune job itself.
- `src/stackowl/scheduler/assembly.py` -- registers + seeds `journal_prune` hourly, alongside `db_reclaim`.
- `src/stackowl/config/task_loop_settings.py` -- `prune_completed_after_days` 1->30, docstring rewritten.
- `src/stackowl/scheduler/handlers/db_reclaim.py` -- `_RUN_HISTORY_RETENTION_DAYS` 7->30, docstring rewritten with the accurate (tripwire-satisfying, not AD-4-direct) reason.
- `tests/journal/test_retention.py`, `test_retention_holds.py` (new); `tests/scheduler/handlers/test_journal_prune.py` (new) -- full I/O-matrix coverage plus the 14 review-round fixes' own tests.
- `tests/journal/test_retention_tripwire.py`, `tests/journal/test_health_contributor.py`, `tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py`, `tests/scheduler/handlers/test_the_note_that_asked_for_this_sweep_was_never_re_read.py` -- updated for the renamed constant, raised values, and the accurate reason.
- `docs/stackowl.yaml.example` -- regenerated (new `journal:` section, updated `prune_completed_after_days`).
- `_bmad-output/implementation-artifacts/deferred-work.md` -- DW-17 resolved; DW-30 (new, the job_runs/AD-4 nuance) added; DW-19 cross-referenced.

**Review findings breakdown:** One independent review pass ran (blind-hunter, edge-case-hunter, verification-gap, intent-alignment), plus my own finding from step-03's Verify-step diff reading. 23 findings total, every one independently re-verified against the actual code before a verdict was rendered.
- **Patched (11 medium, 3 low across 14 entries, all applied by the implementation subagent then independently re-verified by me):** `secure_delete=ON` left on forever on the shared single connection (now reset in a `finally`); a cutoff-format mismatch (SQL space-separated `datetime()` vs Python T-separated `isoformat()`) that silently delayed same-day pruning by up to a day (now computed in Python, bound as a parameter, keeping the new index usable); `Settings()` construction outside the `try:` (moved inside); the except branch always reporting `pruned_count=0` even after a real partial delete (now tracks and reports the real count); `_wal_bytes()` only catching `FileNotFoundError` (broadened to `OSError`) and blocking `Path.stat()` with no `asyncio.to_thread` offload (added, matching `health/contributors.py`'s precedent); the new WAL-budget health signal being permanently maskable by the pre-existing, never-resetting `budget_exceeded_count` (branch order swapped); `journal_prune` failures being conflated with `journal.record()`'s own failure signal via a shared `note_failure()` (split into an independent `note_prune_failure`/`note_prune_success` signal and health branch); a stale DW-17 entry and a docstring naming a nonexistent `get_settings()` function (both corrected); missing test coverage for `secure_delete` actually toggling and for the WAL-degrade wiring end-to-end (both added); incomplete 4-point-logging comment numbering (completed); and a factually wrong claim that `job.*` events reference `job_runs` rows (corrected in 3 files, with the accurate reason -- satisfying a pre-existing tripwire, not AD-4 directly -- and a new deferred-work entry, DW-30).
- **A 15th fix, found by my own full `tripwires.sh` run after the patch round (not a reviewer-layer finding):** `_wal_bytes()`'s `FileNotFoundError` branch logged at DEBUG while its `OSError` branch logs at WARNING, an asymmetry `tests/audit/test_a_background_subsystem_that_declines_still_says_so.py`'s ratchet tripwire correctly flagged (production writes no DEBUG at all). Fixed directly: promoted to INFO, since a fully-checkpointed WAL genuinely reaching 0 bytes is a real, healthy outcome worth a production-visible record, not silence.
- **Deferred (4 entries, logged to this spec's frontmatter `deferred:` and, where warranted, `deferred-work.md`):** `assembly.py`'s WAL-path derivation being independent of the actual registered pool (mirrors an existing pattern, non-trivial fix); a hold-source-checker failure aborting `held_cursors()` entirely (zero real callers today, matches Story 2.10's own precedent for the sibling registry); whether `test_retention_tripwire.py`'s `job_runs` check should be narrowed since AD-4 doesn't actually reach it (a real, medium-severity, deliberate production-retention policy question for the owner -- DW-30); and epics.md's Story 2.11 AC4 (the write-budget AC) being untouched, which is already fully chartered to Story 2.12 by DW-19/DW-21/DW-22.
- **Rejected (4 low, all Blind Hunter -- cosmetic/marginal, each verified real but not worth the added surface):** the WAL checkpoint's `busy` flag not disambiguating the degrade message; raw-byte vs human-readable WAL sizes in the message; the WAL file path not being recorded in health state; and the WAL streak freezing (not resetting) on a `journal_prune` failure (verified benign -- the higher-priority prune-failure signal already covers it).
- **False (1, Intent Alignment):** the diff's "architecture default, still provisional" framing for `retention_days=30` was flagged against the dispatch's own paraphrase "real, considered value" -- verified the diff's framing matches epics.md/Story 2.12's own text exactly; the divergence was against an external paraphrase, not the intent itself.

**Follow-up review recommendation: `true`.** 11 medium-severity findings were patched on this first pass (well over the two-or-more threshold). Specific unverified risk: the patch round's new code (the `secure_delete` reset, the cutoff-format fix, the independent prune-failure signal, the reordered `health_check()` branches, and their new tests) has been independently re-verified by me directly (full diff read both before and after the patch round, full targeted suite, full `./scripts/tripwires.sh` -- all green, including a 15th fix I found and applied myself post-patch-round) but not yet examined by a fresh, independent reviewer-subagent pass looking specifically at this patch round's code, as opposed to the pre-patch implementation.

**Verification performed:**
- `uv run pytest tests/journal/ tests/scheduler/ tests/audit/test_a_tunable_states_its_value_in_one_place.py -q` (run independently by me, both before and after the patch round) -- 901 passed pre-patch-fix, 905 passed after adding `tests/audit/test_a_background_subsystem_that_declines_still_says_so.py` to the targeted set post-fix, 0 failed.
- `./scripts/tripwires.sh` (run independently by me, both before and after my own INFO-level fix) -- FAILED once (`test_no_background_subsystem_declines_without_a_record`, the 15th fix above), then TRIPWIRES PASS: 727 passed, 2 skipped, B4/B8/B9 PASS, ruff 29 (baseline <=35), mypy 57 (baseline <=65).
- `uv run python scripts/gen_config_example.py --write` run twice in a row -- stable, no drift beyond this diff's own `journal:` section addition.
- Full diff read directly by me (not taken from the implementation subagent's own report) both before and after the patch round; every one of the 14 patched findings re-verified against the actually-patched source via direct `Read` of the diff, not trusted from the patch-round agent's own self-report.
- Matrix Test Audit: all 7 I/O & Edge-Case Matrix rows covered by at least one passing test that ran in the verification output, confirmed by direct reading of `tests/scheduler/handlers/test_journal_prune.py` and `tests/journal/test_health_contributor.py`.
- 4 independent review-layer subagents (blind-hunter, edge-case-hunter, verification-gap, intent-alignment) launched fresh against the full diff since `baseline_revision`; every one of their 22 combined findings, plus my own secure_delete finding, individually re-verified by me against the actual code (including reproducing the datetime string-comparison bug directly against a live SQLite connection) before a verdict was rendered.
- Checked for a live gateway/core/scheduler process before and after every verification run -- none running in this session's environment; did not start or restart the live production process at any point, per this dispatch's explicit instruction.

**Residual risks:** the 4 deferred items above (most notably DW-30's open policy question: whether `job_runs`'s 7->30 retention raise should be reverted by narrowing the pre-existing tripwire, since AD-4 doesn't actually require it); the follow-up-review-recommendation risk named above (the patch round's own code not yet independently re-reviewed by a fresh subagent pass); and the real, deliberate production-retention increases this story makes (`tasks`: 1->30 days, `job_runs`: 7->30 days) -- both previously owner-authorized at the lower values specifically to keep disk use tight on Jetson-class hardware, flagged here for the coordinator's awareness rather than silently absorbed.
