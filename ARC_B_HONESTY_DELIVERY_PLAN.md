# Arc B — Agentic Honesty & Delivery Reliability

Full context, root-cause evidence, and BMAD party verdict: see the approved plan at
`/home/boss/.claude/plans/i-want-you-analayze-quizzical-stonebraker.md` (read it before starting any story).

Prior arc docs (Persistence/Trust/Learning/UniOwl + this arc's design doc `PA5B_DESIGN.md`) are archived at
`docs_archive_ralph_2026-06-30/` (was `.ralph/`) for reference — the Ralph Wiggum script/skill/harness has been
removed from this repo. **This arc is now implemented directly in-session (no autonomous loop script), one story
at a time, subagent-driven per project convention** (fresh implementer subagent per story → QA+dev review →
verify → commit → push). This file is the resumable source of truth for story status.

**Do not rebuild the scheduler.** `src/stackowl/scheduler/scheduler.py` is already ONE poll loop over ONE `jobs`
table (migration `0009_scheduler_jobs.sql`) dispatching all 27 handler types — internal sweeps, cognitive jobs,
and user cronjobs alike. Confirmed by research, not assumed. The gap is the honesty/verification layer on top
of it, not the scheduling primitive itself.

**PB0a is DONE** — gateway process tree was manually restarted live during planning (2026-06-30 23:20 UTC),
confirmed `[telegram] adapter.receive: entry` resumed. **PB1 is DONE @9fd28768** — the underlying timeout bug
that caused the 30+ hour hang is fixed in code, not just symptom-relieved.

## Status (2026-07-01, subagent-driven in worktree `arc-b-honesty-delivery`) — ALL 13 STORIES COMPLETE
13 of 13 stories done, all reviewed APPROVED. On main pre-worktree: PB1 @9fd28768, PB5 @92379d07. In this
worktree: PB7a @85056c1c, PB2 @93da573b, PB3 @d09dc7bc, PB0b @1e7796af (RC0 centerpiece), PB0c @c360797f,
PB6a @ea8a43ed (spine), PB6b @ea8a43ed..d67d255c (32 commits, all 33 handlers classified), PB7b @5695d732/
@5700d920 (undeliverable-outbox chokepoint), PB4 @4dbc461f (clarify_pump recovery routing), PBC @50fa46f4/
@78eaee1e (retrieval-intent floor, model-driven classifier), PB7c @d73b28ed (owner-scoped banner fix),
PB-CANARY @64d32a72/@56da8a0b/@d5076b4a (final acceptance gate). Also fixed 2 pre-existing bugs found
incidentally during PB6b's test run (`ab3f1dbc`, `00d2e4f4`), per `feedback_no_skipping_preexisting_fails`.
Full detail per story in `.superpowers/sdd/progress.md`. Worktree branched from main @a4ad781e; merging to
main + pushing next, followed by a live re-test against the real Telegram bot.

## Stories

- [x] **PB1** — DONE @9fd28768. `ApplicationBuilder` now carries bounded connect/read/write/pool timeouts
      (10/20/20/10s) plus `get_updates_*` siblings, and `start_polling` is called with `timeout=30s` (get_updates
      read timeout = 40s, strictly greater per PTB requirement). A stalled long-poll now raises
      `telegram.error.TimedOut` and PTB auto-reconnects instead of hanging forever (RC0 fix). 4 new tests in
      `tests/channels/telegram/test_bot_timeouts.py` (invariant + polling-mode witness + webhook-mode witness +
      black-hole fault-injection); 29 adapter+wiring regression tests green; ruff + mypy clean on changed files.
      Adapter-side API calls (`send_message`, `send_chat_action`, `set_webhook`, `get_me`, etc.) inherit the
      builder timeouts automatically — no per-call wrappers needed.
- [x] **PB0b** — DONE @1e7796af (worktree, review APPROVED). RC0 centerpiece: migration 0074 channel_liveness
      (channel-keyed) + `ChannelLivenessStore` + gateway periodic heartbeat (gated `updater.running`, seeded at
      start) + core-side `ChannelLivenessContributor` (separate contributor, approved deviation from the literal
      "ChannelRegistry.health_check" wording — registry stays channel-agnostic). Stale→degraded @T+300s witness +
      2-connection cross-process proof. Detection only. Original spec below:
      Cross-process liveness timestamp for the Telegram channel + a real `ChannelRegistry.health_check`
      that reads it (`channels/registry.py:127-142`, `scheduler/assembly.py:320`). MUST be cross-process state
      (DB row or shared file) — gateway and core are split processes, an in-proc variable won't be visible to
      the health-check caller. Spec the storage location before coding.
- [x] **PB0c** — DONE @c360797f (worktree, review APPROVED). Bounded gateway-local auto-restart of the dead
      telegram poll loop via `RecoveryActuator` (ADR-2; retry-once→surrender), one attempt per outage episode
      (`_recovery_attempted`, re-arms on running tick). New `_bot.restart_polling(app)` = restart-IN-PLACE
      (guarded `updater.stop()`+`start_polling()`, NOT `stop_bot` which shutdown()s the updater). Plan-text
      correction: PA3/`@d95ed926` is LLM model-tier escalation, NOT the generic ladder — reused `RecoveryActuator`.
      Minor deferred: webhook-mode fires a spurious one-time ERROR (guard on updater-not-None later).
- [x] **PB2** — DONE @93da573b (worktree, review APPROVED). `SocketChannelAdapter.send_text` takes keyword `chat_id`
      → stamps `SendTextFrame.target`; gateway_link threads it to the real adapter — full mirror of the send_file
      seam. Backlog (pre-existing, not new): gateway_link calls telegram(chat_id=)/whatsapp(target=) uniformly with
      chat_id= — send_file has the same latent whatsapp bug. Original spec below:
      Thread `chat_id`/`target` through `SocketChannelAdapter.send_text()` (`channels/socket_adapter.py:55-56`)
      and `gateway_link.py:351-354`, mirroring the already-fixed `send_file` path (lines 58-78, 355-363).
      Add a parity test so text/file send paths can't silently diverge again.
- [x] **PB3** — DONE @d09dc7bc (worktree, review APPROVED). INTERIM (superseded by PB6a/6b, gaps (a)/(b) NOT
      marked done). One shared helper `job_success_for_rollup` (proactive_job.py): delivered/suppressed/
      undeliverable/batched→True, partial/failed/unknown→False (fail-closed). `batched` caught mid-review as a
      reachable router-deferral rollup (retry would duplicate). Original spec below:
      Fix `check_in.py:115-127` / `morning_brief.py:166-192` hardcoded `JobResult(success=True)`; reuse
      `goal_execution.py:438-451`'s `outcome.rollup → success` mapping. INTERIM fix — flag explicitly in the
      commit message that this gets superseded (not just reused) by PB6a/6b, do not mark gap (a)/(b) done here.
- [x] **PB4** — DONE @4dbc461f (review APPROVED, no Critical/Important findings). Design decision: no new
      `RecoveryActuator` entry point needed — `recover(failure)` called with ZERO rungs already emits an ADR-7
      ledger decision on surrender (nothing to retry, the stream is already consumed) — that IS "route into
      RecoveryActuator." `_cleanup` now guards `task.cancelled()` before `.exception()` (plain teardown stays
      silent), and on a genuine exception logs loudly + builds `Failure(consequential=True, kind="send_task")` +
      fires the recovery call via `asyncio.create_task` with a strong ref (renamed `_close_tasks`→`_bg_tasks`,
      now covers both writer-close and recovery tasks). Sibling `create_task` audit (this file only, per the
      scope-creep guard): both other sites call never-raising functions, need no done-callback.
- [x] **PB6a** — DONE @ea8a43ed (worktree, review APPROVED, no findings). `JobResult` += `verified: bool | None
      = None` (tri-state, mirrors `ToolResult.verified`), `effect_class: JobEffectClass = "state_change"`
      (`JobEffectClass = Literal["delivery", "state_change", "read_only"]`), `post_condition: str | None = None`.
      Scheduler dispatch veto reuses `tools.verification.is_trustworthy_success` (not reimplemented) — a job
      reporting `success=True` with `verified=False` now routes to retry/terminal-fail, never `_mark_completed`.
      All 80 pre-existing `JobResult(...)` call sites untouched; zero `JobHandler` subclasses touched.
- [x] **PB6b** — DONE (32 commits, `ea8a43ed..d67d255c`, review APPROVED, no Critical/Important findings). All 33
      `JobHandler` subclasses classified: 8 `delivery`, 2 `read_only`, 22 `state_change`, 1 confirmed pure
      pass-through (untouched). `WebhookHandlerJob.trigger_kind` fixed to `"on_demand"` (matches its actual
      per-event enqueue pattern, no boot-time seed) + pinning test — dangling-handler finding resolved, not
      dropped. `verified`/`post_condition` correctly left unpopulated (PB7b's scope).
      Incidental, approved separately: 2 pre-existing test failures surfaced during this story's test run
      (confirmed byte-identical to `main`, not caused by PB6a/PB6b) were root-caused and fixed per
      `feedback_no_skipping_preexisting_fails` — `ab3f1dbc` (`/memory delete` couldn't find committed-only
      facts) and `00d2e4f4` (phaseB self-improvement mock fixture — 2 stale-fixture bugs, confirmed NOT
      product regressions on review).
- [x] **PB5** — DONE @92379d07. `cronjob.py` `verify()` re-reads `JobScheduler.list_jobs()` to confirm the
      claimed `job_id` row exists (tri-state: True observed / False missing / None unobservable-fail-closed).
      8 new verify tests + ratchet enforcement (cronjob removed from `_KNOWN_UNVERIFIED`), 9 regression green,
      ruff+mypy clean. PB5 is `ToolResult`-based (mirrors `owl_build.py`/`skill_manage.py`), NOT `JobResult`-based
      — it never actually depended on PB6a landing first.
- [x] **PB7a** — DONE @85056c1c (worktree), review APPROVED. `record_undelivered` wired additively into all 3
      silent-drop seams (`deliverer.py` terminal-failed, `router.py` suppressed, `morning_brief.py` no-deliverer);
      next-contact banner surfaced in `assemble.py`'s `parts` composition gated on `delegation_depth == 0` (verified
      to exclude proactive/scheduled turns, not just delegated children); 6 gate tests (DB read-back, all 4 spec
      scenarios incl. distinctness) + 94 regression green.
- [x] **PB7c** — DONE @d73b28ed (review APPROVED, zero findings). Design (Opus,
      `.superpowers/sdd/task-PB7c-design.md`) found this was a write/read KEY MISMATCH, not a missing resolver —
      `owner_id` is already `DEFAULT_PRINCIPAL_ID` at every write+read site (single-user assistant), so it IS the
      stable cross-channel identity. Fix: `list_pending()` drops the `identity_key` exact-match predicate, scopes
      by `owner_id` only. Zero write-site edits, no migration. Also fixed Minor(b): `render_banner` now caps
      per-row body length (`MAX_BANNER_BODY_CHARS=500`) at render time. Minor(a) (router/deliverer construction
      asymmetry) confirmed a non-bug, left as-is.
- [x] **PB7b** — DONE (commits `5695d732`, `5700d920`, review APPROVED, no Critical/Important findings). Design
      (Opus, `.superpowers/sdd/task-PB7b-design.md`) found gap (c) was one rollup value ("undeliverable") on one
      shared path, not 8 separate handler edits: `deliver_for_job`'s undeliverable block now writes a
      `record_undelivered` NACK per unresolvable channel (covers all 6 rollup handlers at once) + a secondary
      dead-letter NACK site in `NotificationDigestJob` (bypasses the router/deliverer seams). Deliberately did
      NOT flip `verified=False` for undeliverable (would reverse PB3's no-retry decision). `identity_key=
      DEFAULT_PRINCIPAL_ID` at both sites — rows write durably now, surface once PB7c lands (same as PB7a's
      `no_deliverer` rows today). `HealthSweepHandler` (operator alert, no user identity) explicitly out of scope.
- [x] **PBC** — DONE (commits `50fa46f4`, `78eaee1e`, review APPROVED, no Critical/Important findings). Design
      (Opus, `.superpowers/sdd/task-PBC-design.md`) added a NEW single-purpose `RetrievalIntentClassifier`
      (mirrors `ClarifyIntentClassifier`'s one-token verdict shape, fast tier, fail-safe→False), a conservative
      LOOKUP/KNOWN prompt (math/code/definitions/opinions all KNOWN), and trigger 3 appended at the tail of
      `_is_overclaim` (after `delivered_successes`/give-up — measured truth always wins over the classifier
      guess). Classifier call lives only in the async wrapper, cost-gated to skip conversational/retrieved/
      delivered turns — `_is_overclaim` stays pure/sync. Zero hardcoded keyword lists (the entire point of the
      story) — verified as the top review check.
- [x] **PB-CANARY** — DONE (commits `64d32a72`, `56da8a0b`, `d5076b4a`, review APPROVED, no Critical/Important
      findings). `TelegramCanaryHandler` sends a fixed marker every 20m via the existing delivery seam; only
      `rollup=="delivered"` stamps send-path liveness. `ChannelLivenessContributor` generalized (`kind`/
      `stale_after_s`, backward-compat) to alert on absence via the pre-existing `HealthSweepHandler`/`AlertSink`
      path — zero new alert code. Implementer caught and fixed a real bug in the design (the literal seed call
      would have produced a permanently-undeliverable, never-stamping job — reviewer independently confirmed the
      bug and the fix, which mirrors `_seed_daily_schedule`/`check_in.py`'s existing patterns exactly).

## Test strategy (apply per-story, not just at the end)
- Convert every failure path into a witness (log record / ledger entry / outbox row / captured exception) and
  assert on the witness in a test — never let "we added logging" count as proof.
- Fault-injection, ranked: (1) black-hole the long-poll socket, (2) throw inside the fire-and-forget send task,
  (3) drop/garble `target`/`chat_id` at the gateway boundary AND test the inverse (wrong-chat delivery must not
  regress), (4) force delivery to fail after a handler already decided "success", (5) kill the process mid-delivery
  before/after the outbox commit.
- RC0 staleness: unit-test the decision function with an injectable clock (no real 30-hour wait); one real soak
  test with the staleness threshold lowered via test config.
- PA5(b) gate test per `docs_archive_ralph_2026-06-30/PA5B_DESIGN.md` section 5, verbatim, for PB7a; an
  equivalent gate test designed fresh for PB7b.

## Completion
Stop ONLY when: every story above is checked done with a commit hash, targeted tests + `uv run ruff check` +
`uv run mypy` pass on changed files, the PB-CANARY round-trip is live and alerting on absence, and a live
re-test against the real Telegram bot confirms a message sent now gets a response.
