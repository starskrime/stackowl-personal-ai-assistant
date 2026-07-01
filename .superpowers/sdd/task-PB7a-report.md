# Task PB7a report — undelivered_outbox wiring

## Status: DONE

## What was implemented

Wired the (pre-existing, correct, untouched) `UndeliveredOutbox` store into its
3 silent-drop seams and added the next-contact surfacing hook, per
`docs_archive_ralph_2026-06-30/PA5B_DESIGN.md` sections 3–5.

### 1. Emission seams (all additive — no control-flow/return/logging changes)

- **`src/stackowl/notifications/deliverer.py`** — `ProactiveDeliverer.__init__`
  gained an optional `outbox: UndeliveredOutbox | None = None` param (default
  keeps every existing construction site, incl. tests, byte-identical). In
  `deliver()`, right after `_maybe_reroute` (retry + fallback-reroute both
  exhausted), a terminal `result == "failed"` now calls
  `record_undelivered(reason="transport_failed", ...)` before the existing
  `_log_exit`/`return`.
- **`src/stackowl/notifications/router.py`** — `NotificationRouter.__init__`
  now builds `self._outbox = UndeliveredOutbox(db)` (router already owned
  `db`, no new dependency to wire). In `_apply_decision`'s `else: # suppressed`
  branch, `record_undelivered(reason="suppressed", ...)` is called after the
  existing debug log.
- **`src/stackowl/scheduler/handlers/morning_brief.py`** — `MorningBriefHandler.__init__`
  builds `self._outbox = UndeliveredOutbox(db)`. In `_deliver()`'s
  `if self._job_deliverer is None:` branch (the exact no-deliverer-wired
  seam named in the brief), `record_undelivered(reason="no_deliverer", ...)`
  is called before the existing `return ProactiveDeliveryOutcome(rollup="undeliverable")`.
- **`src/stackowl/notifications/assembly.py`** — `NotificationAssembly.build()`
  now constructs `UndeliveredOutbox(db)` and passes it into
  `ProactiveDeliverer(..., outbox=...)` — the one wiring change needed since
  `ProactiveDeliverer` doesn't own a `db` handle itself.

**`identity_key` resolution** (reuse, not invention): each seam that has a
`Notification.target` (the channel-native recipient these call sites already
use to know WHO to deliver to) uses `str(notification.target)`. For telegram
this is *exactly* the pipeline's `session_id`/`identity_key` convention
(`session_id == str(chat_id) == str(user_id)`, confirmed in
`channels/telegram/adapter.py`), so the banner surfaces correctly on the next
turn from the same chat with an unconfigured (default) identity-alias setup.
When no target is resolvable at all (`target is None`, or the morning-brief
no-deliverer branch which has no notification/target object whatsoever), the
seam falls back to `DEFAULT_PRINCIPAL_ID` (the store's own default owner
scope) — this is a single-user personal assistant, so the owner IS the one
identity to eventually surface to; there is no live-session identity to key by
in that branch. This fallback is a documented judgment call, not a new lookup:
no per-notification identity resolver exists anywhere in the codebase today
(confirmed via full-repo grep) — inventing a cross-channel resolver for these
3 sites would have been the actual "invent a new lookup" the brief forbids.

### 2. Next-contact banner (surfacing)

**`src/stackowl/pipeline/steps/assemble.py`** — chosen injection point: **the
existing `parts` list at the tail of `assemble.run()`** (not a new
`PIPELINE_STEPS` entry). Reasoning: `assemble` already runs once per turn,
strictly before `execute` (the owl's first response) generates anything;
adding a dedicated pipeline step would mean a second DB round-trip and a
second place that has to reason about `delegation_depth`, for zero benefit —
the banner is exactly the same shape as `capabilities`/`persona`/`skills_block`
(an optional prompt fragment), so it belongs in the same fail-open,
part-filtering assembly this function already does.

Gate: `state.delegation_depth == 0`. Scheduled/proactive job handlers
(`morning_brief`, `check_in`, etc.) are plain `JobHandler.execute()` calls
driven by `JobScheduler` polling — they never invoke the chat
`PIPELINE_STEPS`/`assemble.run()` at all, so no separate "not a proactive
turn" gate was needed; `delegation_depth == 0` alone correctly captures "real
top-level user turn, not a delegated child turn" (delegated sub-turns
increment `delegation_depth`, confirmed in `pipeline/state.py`).

Flow: `owner_key = state.identity_key or state.session_id` (the same fallback
pattern already used in `classify.py`/`deliver.py`) → construct
`UndeliveredOutbox(services.db_pool)` locally (mirrors the existing
`persistence_handoff.py` pattern of pulling `services.db_pool` directly inside
a step rather than threading a new field through `StepServices` — the store
is a stateless thin wrapper, so a fresh instance per turn is free) →
`list_pending(owner_key)` → if non-empty, `render_banner(rows)` is inserted
into `parts` (between `capabilities` and `persona`) and `mark_surfaced([row
ids])` is called so the banner shows exactly once. The whole block is wrapped
in a `no-hidden-errors` try/except (matches every other optional part in this
function) — any failure degrades to no banner, never crashes the turn.

## Tests — `tests/notifications/test_undelivered_outbox_gate.py`

All read the DB back directly (`tmp_db.fetch_all`), never a mock/log
assertion. 6 tests, all passing:

1. `test_transport_failed_writes_durable_row_with_body_and_reason` — an
   always-failing channel adapter + `ProactiveDeliverer(outbox=...)` →
   `deliver()` returns `"failed"` → row exists with body + `reason ==
   "transport_failed"` + `identity_key == str(target)`.
2. `test_suppressed_router_path_writes_durable_row_with_body` — `focus_mode
   "hard"` + `urgency "low"` → router decision `"suppressed"` → row exists
   with the full body (not a hash).
3. `test_next_contact_banner_surfaces_once_then_clears` — seed a pending row,
   drive `assemble.run(state)` for that identity → banner text appears in
   `system_prompt` AND `surfaced_at` is non-NULL; a second `assemble.run` for
   the same state does NOT re-include the banner text.
4. `test_delegated_child_turn_does_not_surface_banner` (extra, not required by
   the brief but proves the delegation gate) — `delegation_depth=1` → banner
   never appears, row stays `surfaced_at IS NULL`.
5. `test_f62_pending_job_does_not_create_outbox_row` — a job with an
   unregistered handler polled via the real `JobScheduler._poll()` stays
   `status == "pending"` (self-recovers) and creates **zero**
   `undelivered_outbox` rows.
6. `test_quiet_hours_batched_deferral_does_not_create_outbox_row` —
   `focus_mode "soft"` → router decision `"batched"` → the existing
   `notification_queue` row is created (its own correct recovery path) and
   **zero** `undelivered_outbox` rows are created.

Tests 5+6 together satisfy the brief's single "DISTINCTNESS guard" bullet
(F-62 pending job + quiet-hours/batched-deferred item both proven absent from
the outbox) — split into two focused tests rather than one combined test for
clarity and independent failure diagnosis.

```
6 passed in 11.25s
```

### Regression run (targeted, not full suite per project convention)

- `tests/notifications/` (all 11 files, 64 tests): **64 passed**
- `tests/pipeline/test_assemble_skills.py`,
  `tests/pipeline/test_plan_a_assemble.py`,
  `tests/pipeline/steps/test_assemble_model_aware.py` (21 tests): **21 passed**
- `tests/journeys/test_morning_brief_delivers.py`,
  `tests/scheduler/test_s2_failure_notify_and_missing_handler.py` (9 tests):
  **9 passed**

No TDD RED phase was used — the store + migration (carryover) already existed
and were verified correct first; the 3 emission seams and the surfacing hook
were implemented directly against the approved design, then the gate test was
written and run GREEN on the first pass (all 6 assertions passed without
needing implementation fixes). This is wiring against a pre-approved,
already-designed contract rather than new behavior discovery, so RED-first
would have added a throwaway step without additional confidence.

## Verification

- `uv run ruff check` on all 5 changed source files + the new test file: **All
  checks passed**.
- `uv run mypy` on all 5 changed source files: **Success: no issues found in 5
  source files**.
- `uv run mypy` on the new test file standalone reports `import-untyped`
  noise (missing `py.typed` marker when mypy is invoked on a single test file
  outside the package's normal `mypy src/` scope) — confirmed this is a
  **pre-existing** artifact of single-file invocation, not something this
  change introduced, by running the identical command against the untouched
  `tests/notifications/test_deliverer.py` (same class of errors).

## Files changed

- `src/stackowl/notifications/deliverer.py` (+24/-0)
- `src/stackowl/notifications/router.py` (+19/-0)
- `src/stackowl/notifications/assembly.py` (+3/-0)
- `src/stackowl/scheduler/handlers/morning_brief.py` (+17/-0)
- `src/stackowl/pipeline/steps/assemble.py` (+39/-1)
- `tests/notifications/test_undelivered_outbox_gate.py` (new, 6 tests)

Untouched (carryover, verified correct, not rewritten):
- `src/stackowl/notifications/undelivered_outbox.py`
- `src/stackowl/db/migrations/0073_undelivered_outbox.sql`

## Self-review

- **Completeness**: all 3 seams wired + surfacing hook + all 4 brief-required
  test assertions (plus 2 extra tests: delegation-gate, and the distinctness
  guard split into 2 for clarity) — done.
- **Quality**: every new call site mirrors the existing 4-point logging /
  B5 best-effort convention already present in each file; no new abstractions
  (no shared identity-resolution helper module — the ternary is 4 lines, used
  3 times, in 3 different files with 3 different surrounding contexts; a
  shared util would be premature and each site's log_fields differ).
- **Discipline**: zero refactoring of surrounding functions; every diff is
  strictly additive (`git diff --stat`: 101 insertions, 1 line changed — the
  `parts` tuple gaining `banner`). No new pipeline step, no new `StepServices`
  field, no new config.
- **Testing**: all assertions are DB read-backs; adapter failures are real
  (raise inside a fake `send_text`), not mocked-away; the F-62 test drives the
  real `JobScheduler._poll()`, not a stub.

## Concerns

1. **`no_deliverer` banner reachability**: the `DEFAULT_PRINCIPAL_ID` fallback
   identity_key (used only when morning_brief has zero deliverer/ledger wired
   — a defensive/legacy-construction branch, not the normal production path
   since `startup/orchestrator.py` always injects both) will only surface on
   a live turn whose `state.identity_key`/`session_id` happens to equal
   `DEFAULT_PRINCIPAL_ID`, which a normal channel session_id (e.g. a telegram
   chat id) will not. This is a known, documented limitation inherent to that
   specific branch having zero recipient information at write time — not a
   regression, and not exercised by any brief-required test scenario (the
   brief's 4 assertions use `transport_failed`/`suppressed`, both of which
   key on the real recipient and surface correctly, as tests 1–3 prove).
2. Per the "COST CRITICAL" hook messages that fired repeatedly during this
   session (session cost reported as $218.18), flagging here since I cannot
   interactively pause as a subagent — please review cost before further
   large tasks in this session.

## Commit

`fix(honesty): PB7a — wire undelivered_outbox into deliverer/router/morning_brief seams + next-contact banner`
