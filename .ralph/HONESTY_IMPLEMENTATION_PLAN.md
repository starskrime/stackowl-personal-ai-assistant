# Arc B — Agentic Honesty & Delivery Reliability

Full context, root-cause evidence, and BMAD party verdict: see the approved plan at
`/home/boss/.claude/plans/i-want-you-analayze-quizzical-stonebraker.md` (read it before starting any story).

**Do not rebuild the scheduler.** `src/stackowl/scheduler/scheduler.py` is already ONE poll loop over ONE `jobs`
table (migration `0009_scheduler_jobs.sql`) dispatching all 27 handler types — internal sweeps, cognitive jobs,
and user cronjobs alike. Confirmed by research, not assumed. The gap is the honesty/verification layer on top
of it, not the scheduling primitive itself.

**PB0a is already DONE** — gateway process tree was manually restarted live during planning (2026-06-30 23:20
UTC), confirmed `[telegram] adapter.receive: entry` resumed. The underlying timeout bug (PB1) that caused the
30+ hour hang is NOT yet fixed — PB0a was the symptom-relief restart, not the fix.

## Stories

- [ ] **PB1** — Add timeout to the Telegram long-poll/API calls (`channels/telegram/_bot.py`,
      `channels/telegram/adapter.py`) so a network stall fails loud and reconnects instead of hanging forever.
      This is the actual RC0 fix. Land before PB0c.
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
      land before PB5/PB7b). `grep -r "JobResult(" src/stackowl/` and `grep -rn "class.*JobHandler" src/stackowl/`
      first for the real fan-out count.
- [ ] **PB6b** — Migrate `JobHandler` subclasses to the new contract, one handler per commit, bisectable. Include
      the `webhook_handler` dangling-handler finding (wiring_audit warning: "registered as seeded but has NO
      standing jobs row") found during the PB0a restart — fix or explicitly defer with a ticket, don't drop it.
- [ ] **PB5** — `cronjob.py:137` `verify()`/`post_condition()` reading back the inserted job row. Build AFTER PB6a,
      against the new contract, mirroring `owl_build.py:277`/`skill_manage.py:606`. Watch for a read-after-write
      race against the job store's commit boundary.
- [ ] **PB7a** — Build `undelivered_outbox` exactly per `.ralph/PA5B_DESIGN.md` (already fully specified, do not
      redesign). Migration `0073_undelivered_outbox.sql` (tip is 0072).
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
- PA5(b) gate test per `.ralph/PA5B_DESIGN.md` section 5, verbatim, for PB7a; an equivalent gate test designed
  fresh for PB7b.

## Completion
Stop ONLY when: every story above is checked done with a commit hash, targeted tests + `uv run ruff check` +
`uv run mypy` pass on changed files, the PB-CANARY round-trip is live and alerting on absence, and a live
re-test against the real Telegram bot confirms a message sent now gets a response.
