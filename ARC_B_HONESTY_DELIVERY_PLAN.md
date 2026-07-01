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

## Status (2026-06-30, subagent-driven in worktree `arc-b-honesty-delivery`, PAUSED at user request)
3 of 13 stories done (PB1, PB5 on main; PB7a in this worktree @85056c1c, reviewed APPROVED). Worktree branched
from main @a4ad781e. Remaining: PB0b, PB0c, PB2, PB3, PB4, PB6a, PB6b, PB7b, PBC, PB-CANARY + new follow-up PB7c.
Session paused at ~$224 cost. Resume: continue subagent-driven from this worktree, next story PB0b (or PB2/PB3
which are independent and cheap). Progress ledger: `.superpowers/sdd/progress.md`.

## Stories

- [x] **PB1** — DONE @9fd28768. `ApplicationBuilder` now carries bounded connect/read/write/pool timeouts
      (10/20/20/10s) plus `get_updates_*` siblings, and `start_polling` is called with `timeout=30s` (get_updates
      read timeout = 40s, strictly greater per PTB requirement). A stalled long-poll now raises
      `telegram.error.TimedOut` and PTB auto-reconnects instead of hanging forever (RC0 fix). 4 new tests in
      `tests/channels/telegram/test_bot_timeouts.py` (invariant + polling-mode witness + webhook-mode witness +
      black-hole fault-injection); 29 adapter+wiring regression tests green; ruff + mypy clean on changed files.
      Adapter-side API calls (`send_message`, `send_chat_action`, `set_webhook`, `get_me`, etc.) inherit the
      builder timeouts automatically — no per-call wrappers needed.
- [ ] **PB0b** — Cross-process liveness timestamp for the Telegram channel + a real `ChannelRegistry.health_check`
      that reads it (`channels/registry.py:127-142`, `scheduler/assembly.py:320`). MUST be cross-process state
      (DB row or shared file) — gateway and core are split processes, an in-proc variable won't be visible to
      the health-check caller. Spec the storage location before coding.
- [ ] **PB0c** — Bounded auto-restart on staleness. Reuse the PA3 breaker→escalation-ladder pattern (`@d95ed926`).
      Do NOT hand-roll a second bespoke retry/backoff implementation.
- [ ] **PB2** — Thread `chat_id`/`target` through `SocketChannelAdapter.send_text()` (`channels/socket_adapter.py:55-56`)
      and `gateway_link.py:351-354`, mirroring the already-fixed `send_file` path (lines 58-78, 355-363).
      Add a parity test so text/file send paths can't silently diverge again.
- [ ] **PB3** — Fix `check_in.py:115-127` / `morning_brief.py:166-192` hardcoded `JobResult(success=True)`; reuse
      `goal_execution.py:438-451`'s `outcome.rollup → success` mapping. INTERIM fix — flag explicitly in the
      commit message that this gets superseded (not just reused) by PB6a/6b, do not mark gap (a)/(b) done here.
- [ ] **PB4** — `clarify_pump.py` `_cleanup` (lines 199-210): inspect `task.exception()`, log loudly, route into
      `RecoveryActuator`. FIRST write a one-paragraph design note: does `_cleanup` synthesize a minimal
      verification-failure record to feed the existing ToolResult-shaped ladder, or does `RecoveryActuator` need
      a narrower entry point for non-tool async failures? Land that decision before touching the file. Audit for
      sibling bare `asyncio.create_task()` calls without exception-inspecting done-callbacks; fix this call site,
      file the rest as backlog (do not scope-creep into an asyncio-wide audit in this story).
- [ ] **PB6a** — Define the unified `verified`/`effect_class`/`post_condition` contract on `JobResult` (the spine —
      land before PB7b). `grep -r "JobResult(" src/stackowl/` and `grep -rn "class.*JobHandler" src/stackowl/`
      first for the real fan-out count.
- [ ] **PB6b** — Migrate `JobHandler` subclasses to the new contract, one handler per commit, bisectable. Include
      the `webhook_handler` dangling-handler finding (wiring_audit warning: "registered as seeded but has NO
      standing jobs row") found during the PB0a restart — fix or explicitly defer with a ticket, don't drop it.
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
- [ ] **PB7c** (NEW, follow-up from PB7a review — Important) — the outbox row is durably written on every seam, but
      the next-contact BANNER can only surface on telegram: `identity_key` falls back to `DEFAULT_PRINCIPAL_ID` for
      any non-telegram channel (Slack etc) because `router_helpers.py:72 _SESSION_IS_CHAT_ID_CHANNELS={"telegram"}`
      makes `resolve_target_chat_id()` return None, and `assemble.py:193` keys surfacing on `identity_key or
      session_id` which never equals the static principal constant. Fix needs a per-notification cross-channel
      identity resolver (does not exist in the codebase today — its own design task). Not a regression: row is
      written, not dropped; telegram (the box's only live channel) surfaces correctly. Also fold in the 2 Minor
      findings: router builds outbox unconditionally vs deliverer's optional inject (style asymmetry); `render_banner`
      caps row COUNT (20) but not per-row body size.
- [ ] **PB7b** — Design + build outbox generalization to scheduled-job failures (gap c). Separate design pass,
      hard-blocked on PB6a (no `verified` signal to gate on until the contract exists). Do not silently fold into
      PB7a's scope.
- [ ] **PBC** — `overclaim_gate.py` third trigger (lines 15-56, alongside the existing unverified-effects check at
      line 42): floor responses where the turn's classified intent requires retrieval but `state.tool_calls` has
      no `web_search`/`web_fetch` call. MUST route through the existing model-driven intent-classification pattern
      (same family as the clarify/feedback classifiers) — NOT a hardcoded English keyword scan
      ("research"/"look up"/etc). Violating this is a hard stop per project convention
      (`feedback_no_hardcoded_keyword_lists`, `feedback_no_hardcoded_english`).
- [ ] **PB-CANARY** — Synthetic heartbeat message round-tripping the real Telegram path on an interval, with
      alerting on its own absence. Final acceptance gate for the whole arc.

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
