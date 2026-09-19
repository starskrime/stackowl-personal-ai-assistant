---
title: 'Consent never grants a channel nobody can answer'
type: 'feature'
created: '2026-09-19'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
baseline_revision: 'a719cfaf54efe139828be3a9d1ae4f2db51ee161'
context: [
  '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md',
  '{project-root}/_bmad-output/implementation-artifacts/spec-3-3-approvals-and-questions-become-needs-you-items.md',
]
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** `RoutingPrompter.prompt()` (`tools/consent.py:668-687`) falls back to `AutonomousPrompter` whenever `req.channel` has no registered prompter — silently auto-granting ordinary consequential actions with no vouching origin. This conflates two different situations under one signal ("no channel UX"): a genuinely unattended trigger (a scheduled job), and a live channel the operator simply failed to wire a prompter for. Only the first should ever auto-grant.

**Approach:** Delete the fallback. An unwired channel now DENIES and opens one deduplicated `incident` needs_you item per channel. Unattended work instead gets an EXPLICIT, trigger-set `principal` (`autonomous:scheduler`) carried on `TraceContext`, read directly by `ConsentPolicy.request()` (mirrors how `consent_events.py` already reads `trace_id` off `TraceContext.get()` with no threaded param) — when present, the request routes straight to `AutonomousPrompter` instead of the channel-keyed prompter, regardless of what channel happens to be bound. This closes the exact conflation `AutonomousPrompter`'s own docstring already names ("RoutingPrompter routes here whenever a channel has no prompter, which covers BOTH 'nobody can be asked' ... AND 'somebody can be asked and we failed to wire the asking'") by replacing an inferred signal with a declared one.

## Boundaries & Constraints

**Always:**
- `RoutingPrompter` denies + opens one `incident` needs_you item (per-channel deduped via `target_id=channel`, mirrors Story 3.1's `ON CONFLICT ... DO NOTHING` dedupe) whenever `req.channel` has no registered prompter. `db_pool=None` (test/CLI default) is a no-op journal write, same convention as every Story 3.3 helper — DENY still happens.
- `ConsentPolicy.request()` reads `principal = TraceContext.get().get("principal")`; when it equals `PRINCIPAL_AUTONOMOUS_SCHEDULER` ("autonomous:scheduler"), the prompter used at "3. STEP" is `AutonomousPrompter()`, not `self.prompter` — bypassing `RoutingPrompter` entirely so a scheduled job's real target channel (e.g. `telegram`, set for delivery/scoping, not attendance) never causes it to wait on a human who isn't watching THIS trigger.
- `_bind_job_trace` (`scheduler/scheduler.py`) sets `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` on every `TraceContext.start(...)` it opens — explicit, never inferred from `channel`/payload.
- The provenance auto-grant (`official = channel in _gateway_channels()`, duplicated today in `ConsentPolicy.request()`'s top-of-function check and `AutonomousPrompter.prompt()`) never treats `channel in {"web", "voice"}` as official — extract one shared `_is_official_channel(channel)` helper carrying this guard, used by both call sites.
- `AutonomousPrompter`'s existing always-ask refusal (`allow_relaxation is False -> DENY`) is untouched and is what makes the new principal-based bypass safe for the six always-ask categories.

**Never:**
- Never widen `ConsentRequest`, `ConsequentialActionGate.check()`, or `PipelineState` to carry `principal` as a threaded parameter — `ConsentPolicy.request()` reads it off `TraceContext` directly (existing precedent: `consent_events.record_consent_decision` already reads `trace_id` this way).
- Never touch `batch_approve`'s "Approve all" bypass of the per-action gate — a separate, pre-existing, deliberately-designed gap (J8), out of this story's scope; log it to `deferred-work.md`.
- Never make `RoutingPrompter`'s routing decision depend on `ChannelRegistry` membership beyond the one confirmed hardcoded list (see Code Map) — gateway-role per-channel registration is already adapter-start-driven (each `consent_routing.register(...)` call sits inside that channel's own startup conditional); re-deriving it from `ChannelRegistry` risks breaking the CLI/TTY path, whose adapter registration into `ChannelRegistry` is unverified.
- Never add a DB migration — the `needs_you`/`journal_events` schema (Story 3.1) already has every column this story needs.

</intent-contract>

## Code Map

- `src/stackowl/tools/consent.py:645-687` — `RoutingPrompter`. Delete the `if prompter is None: return await AutonomousPrompter().prompt(req)` fallback and its docstring paragraph. Add `db_pool: DbPool | None = None` constructor param (mirrors `ConsentPolicy`/`ClarifyGateway`). Add `set_default(prompter: ConsentPrompter) -> None` (mirrors `register()`'s mutability). `prompt()`: `prompter = self._by_channel.get(req.channel, self._default)`; if still `None` → call new `consent_events.record_channel_unreachable(self._db_pool, channel=req.channel, tool_name=req.tool_name)`, return `ConsentScope.DENY`.
- `src/stackowl/tools/consent.py:430,545-555,807-821` — `_PROVENANCE_CATEGORIES`, the duplicated `official = bool(req.channel) and req.channel in _gateway_channels()` check in `AutonomousPrompter.prompt()` and `ConsentPolicy.request()`. Extract `_is_official_channel(channel: str) -> bool` = `bool(channel) and channel not in _NEVER_OFFICIAL_CHANNELS and channel in _gateway_channels()`, with `_NEVER_OFFICIAL_CHANNELS = frozenset({"web", "voice"})` (AC5, NFR24 — neither channel has a real adapter yet; this is a forward guard). Both call sites call the new helper instead of the inline expression.
- `src/stackowl/tools/consent.py:352` (beside `HUMAN_DECISION_TIMEOUT_SECONDS`) — add `PRINCIPAL_AUTONOMOUS_SCHEDULER = "autonomous:scheduler"`, exported in `__all__`.
- `src/stackowl/tools/consent.py:760-897` — `ConsentPolicy.request()`. At "3. STEP" (`consent.py:896`, `scope = await self.prompter.prompt(req)`), read `principal = TraceContext.get().get("principal")` (import already present via `consent_events`/similar pattern — add `from stackowl.infra.trace import TraceContext` if not already imported in this module) and select `prompter = AutonomousPrompter() if principal == PRINCIPAL_AUTONOMOUS_SCHEDULER else self.prompter`; await `prompter.prompt(req)`. Everything downstream (needs_you resolve, DENY/DENY_SESSION handling, session batch/window recording, `_finalize`) is unchanged.
- `src/stackowl/tools/consent_assembly.py:66-67` — `RoutingPrompter()` construction gains `db_pool=db_pool` (already a `build()` param, already threaded to `ConsentPolicy` two lines below — same value, new destination).
- `src/stackowl/startup/orchestrator.py:1684-1688` — the CORE-role loop `for _chan in ("cli", "telegram", "slack", "discord", "whatsapp"): consent_routing.register(_chan, socket_consent_prompter)` — THE confirmed hardcoded list (AC1/AD-18). Replace with `consent_routing.set_default(socket_consent_prompter)`: CORE has no local `ChannelRegistry` (adapters live in the gateway process in split mode), so any channel not itself hosted locally now forwards to gateway's own (already adapter-start-driven) `RoutingPrompter`, which is the real authority on "is this channel wired" — removing the closed enumeration without inventing new IPC.
- `src/stackowl/infra/trace.py:34-...,105-122,231-254` — `TraceContext`. Add a `_principal: ContextVar[str | None]` mirroring `_channel`; add `principal: str | None = None` to `start()`'s signature and `_TraceToken`/reset plumbing (mirror `channel`'s exact shape); include `"principal": cls._principal.get()` in `get()`'s returned dict.
- `src/stackowl/scheduler/scheduler.py:121-178` (`_bind_job_trace`) — add `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` to the `TraceContext.start(...)` call (import from `stackowl.tools.consent`). `channel` computation (job's real target, or `"internal"`) is untouched — it now only affects delivery/scoping and `AutonomousPrompter`'s own provenance/confined checks, never "is anyone attending."
- `src/stackowl/journal/consent_events.py` — add `ConsentChannelUnreachableAttrs(channel: str, tool_name: str)`, `_narrate_channel_unreachable`, and register `consent.channel_unreachable` (`attention_class=NEEDS_YOU`, `intensity=Intensity.HIGH`, `needs_you_kind=NeedsYouKind.INCIDENT`, `record_kind=RecordKind.CONSENT`, `table=None` — mirrors `consent.requested`'s no-owning-row rationale). Add `record_channel_unreachable(db_pool, *, channel, tool_name) -> None`: `db_pool is None` → no-op (mirrors every existing helper here); else one `journal_record(conn, JournalEvent(type="consent.channel_unreachable", target_kind=ActorKind.OWNER, target_id=channel, ...))` inside its own transaction — recorder.py's existing NEEDS_YOU wiring (Story 3.1) opens the deduped item automatically; no explicit `open_item()`/`bind_waiter()` call needed (nobody waits on an incident).
- **Tests to fix (pre-existing, now red under the new contract — not merely additive):**
  - `tests/channels/test_unwired_channel_consent_fails_closed.py` (AC6, named explicitly) — rewrite docstring (the "unwired channel GRANTS" framing) and `test_an_unwired_channel_GRANTS_an_ordinary_action` → deny + incident-opened assertion (construct `RoutingPrompter(db_pool=...)`, call twice, assert exactly one `needs_you` incident row).
  - `tests/tools/test_autonomous_consent.py:95-117` — `TestTheRouterNoLongerDeniesUnasked.test_a_turn_with_no_channel_UX_is_granted_not_denied` asserts the exact behavior being deleted. Rename the class/test to assert DENY; keep `test_a_registered_channel_still_asks_its_own_prompter` (unaffected).
  - `tests/tools/test_permission_is_real_in_both_directions.py:112-123` — `TestOrdinaryAutonomousWorkIsStillUnblocked.test_a_normal_consequential_tool_is_still_granted` calls `ConsentPolicy(prompter=RoutingPrompter()).request(channel="cron", ...)` expecting `True` via the old fallback. Rewrite to bind `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` via `TraceContext.start(...)` around the call, proving the NEW mechanism grants ordinary unattended work.
- **New tests:**
  - `tests/tools/test_the_autonomous_principal_bypasses_channel_routing.py` — AC3+AC5: a request with `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` and `channel="internal"` (no prompter) is granted for an ordinary tool; parametrized over the six always-ask categories, each still `DENY`d under the same principal (never silently run "at once").
  - `tests/tools/test_the_provenance_grant_never_reaches_web_or_voice.py` — AC5: `_is_official_channel("web")`/`("voice")` is `False` even when monkeypatched into `_gateway_channels()`'s live set.
  - `tests/tools/test_channel_comes_from_provenance_not_payload.py` — AC4: a `call_args` dict carrying a spoofed `"channel"` key never changes `ConsentRequest.channel` from `state.channel`/`ctx`-sourced value (drives `ConsequentialActionGate.check()` directly).
  - `tests/scheduler/test_scheduled_jobs_carry_the_autonomous_principal.py` — AC3: a real `JobScheduler` dispatch (`_run_job`/`.run()`, not a direct handler call) runs a job whose handler calls a real consequential tool through the real `ConsequentialActionGate`/`ConsentPolicy(prompter=RoutingPrompter())` with NO prompter registered anywhere — proves the job completes (grant via the new principal), and that `_bind_job_trace` sets the principal explicitly rather than it being inferred.
  - `tests/journal/test_consent_events.py` — extend: `record_channel_unreachable` registration + dedupe-per-channel (two calls, same channel → one open incident).

## Tasks & Acceptance

**Execution:**
- `src/stackowl/tools/consent.py` -- delete `RoutingPrompter`'s `AutonomousPrompter` fallback; add `db_pool`/`set_default`; extract `_is_official_channel` with the web/voice guard; add `PRINCIPAL_AUTONOMOUS_SCHEDULER` and the `TraceContext`-read branch in `ConsentPolicy.request()` -- AC1 (partial), AC2, AC3, AC5.
- `src/stackowl/journal/consent_events.py` -- register `consent.channel_unreachable`, add `record_channel_unreachable()` -- AC2.
- `src/stackowl/tools/consent_assembly.py` -- thread `db_pool` into `RoutingPrompter(...)` -- AC2.
- `src/stackowl/startup/orchestrator.py` -- replace the CORE-role hardcoded 5-channel loop with `consent_routing.set_default(socket_consent_prompter)` -- AC1.
- `src/stackowl/infra/trace.py` -- add `principal` to `TraceContext` (contextvar, `start()` param, `get()` output) -- AC3.
- `src/stackowl/scheduler/scheduler.py` -- `_bind_job_trace` sets `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` explicitly -- AC3.
- Fix the three pre-existing tests listed in Code Map (now red under the new contract) -- AC2, AC6.
- Add the five new/extended test files listed in Code Map -- AC1-AC5.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- log the `batch_approve` always-ask re-check gap found during investigation (out of scope here).

**Acceptance Criteria:**
- Given the consent routing, when the platform starts, then the prompter channel set is driven by real adapter-start events (gateway role: per-channel registration inside each adapter's own startup conditional, already true; core role: forwarded via a default prompter, not an enumerated list), never a hardcoded list.
- Given a consent request on a channel with no registered prompter, when it is decided, then it is denied and one deduplicated `incident` needs_you item opens for that channel, and `RoutingPrompter` never falls back to `AutonomousPrompter`.
- Given a scheduled or autonomous run, when its trigger starts it, then it carries the explicit principal `autonomous:scheduler`, never inferred from channel/payload, and that principal grants ordinary consequential actions while still refusing every always-ask category; a real scheduler-dispatched job completes an ordinary consequential action.
- Given any ingress, when a `ConsentRequest` is built, then its `channel` comes only from `TraceContext`/ingress provenance, never from tool call args.
- Given each always-ask category, when a request carries the autonomous principal, then it is still refused; given the provenance auto-grant, then it never applies to `web` or `voice`.
- Given `tests/channels/test_unwired_channel_consent_fails_closed.py`, when this story lands, then it asserts deny+incident and its docstring is corrected.

## Design Notes

**Why `principal` rides `TraceContext`, not a new `ConsentRequest`/`gate.check()` parameter.** Threading it through `PipelineState`/`ConsequentialActionGate.check()`/`ConsentRequest` would touch every call site of a widely-used gate. `ConsentPolicy.request()` already has an established, working precedent for reading ambient identity straight off `TraceContext` without a parameter: `consent_events.record_consent_decision` does exactly this for `trace_id`. `TraceContext` is already the documented channel for "tools read this, not `PipelineState`" (its own `start()` docstring). A scheduled job's handler runs inside the `TraceContext.start(...)` scope `_bind_job_trace` opens for the whole run, so every tool call made during that run sees the same `principal` with zero additional wiring.

**Why the principal check ignores `channel` entirely.** `_bind_job_trace`'s own docstring: "a job with a real target uses it -- morning_brief's consent scope is telegram, not a placeholder." A job's channel can be a genuinely live, wired channel (set for delivery/audit scoping), yet no human is watching THIS specific automated trigger. Judging "is anyone attending" by channel-liveness was the exact conflation the deleted fallback made; the explicit principal replaces it.

## Verification

**Commands:**
- `uv run pytest tests/tools/test_autonomous_consent.py tests/tools/test_permission_is_real_in_both_directions.py tests/channels/test_unwired_channel_consent_fails_closed.py tests/tools/test_a_confined_run_may_proceed_without_a_human.py -x` -- expected: all pass under the new contract.
- `uv run pytest tests/tools/test_the_autonomous_principal_bypasses_channel_routing.py tests/tools/test_the_provenance_grant_never_reaches_web_or_voice.py tests/tools/test_channel_comes_from_provenance_not_payload.py -x` -- expected: new tests pass.
- `uv run pytest tests/scheduler/ -x` -- expected: all pass, including the new gateway-driven job test.
- `uv run pytest tests/journal/test_consent_events.py -x` -- expected: `consent.channel_unreachable` registration + dedupe tests pass.
- `./scripts/tripwires.sh` -- expected: clean, before every commit.

## Auto Run Result

**Summary of implemented change:** `RoutingPrompter`'s `AutonomousPrompter` fallback is deleted outright -- an unwired channel now denies and opens one deduplicated `incident` needs_you item per channel (`consent.channel_unreachable`, mirroring Story 3.1's dedupe). Unattended work instead carries an explicit `principal="autonomous:scheduler"` on `TraceContext`, set by `scheduler.scheduler._bind_job_trace` on every job run; `ConsentPolicy.request()` reads it directly (never a threaded parameter, mirroring how `consent_events.record_consent_decision` already reads `trace_id`) and routes straight to a fresh `AutonomousPrompter()` when it matches, bypassing `RoutingPrompter` entirely regardless of the job's own delivery channel. The duplicated inline provenance check in `AutonomousPrompter.prompt()` and `ConsentPolicy.request()` is now one shared `_is_official_channel()` helper, with a forward guard (`_NEVER_OFFICIAL_CHANNELS = {"web", "voice"}`) so neither channel can become "official" even via a future live registration. CORE's startup wiring (`startup/orchestrator.py`) replaces its hardcoded 5-channel registration loop with `RoutingPrompter.set_default(socket_consent_prompter)`, so CORE forwards any channel it does not itself host to the gateway's own adapter-start-driven router instead of an enumerated list.

**Files changed:**
- `src/stackowl/tools/consent.py` -- deleted the `AutonomousPrompter` fallback; `RoutingPrompter` gained `db_pool`/`set_default()` and now denies + journals `consent.channel_unreachable` on any unmapped channel; added `PRINCIPAL_AUTONOMOUS_SCHEDULER`; extracted `_is_official_channel()`/`_NEVER_OFFICIAL_CHANNELS`; `ConsentPolicy.request()` reads `principal` off `TraceContext` at the prompt step.
- `src/stackowl/journal/consent_events.py` -- `ConsentChannelUnreachableAttrs`, `consent.channel_unreachable` registration (NEEDS_YOU/INCIDENT/HIGH, `table=None`), `record_channel_unreachable()`.
- `src/stackowl/tools/consent_assembly.py` -- threads `db_pool` into `RoutingPrompter(...)`.
- `src/stackowl/startup/orchestrator.py` -- CORE role: `consent_routing.set_default(socket_consent_prompter)` replaces the hardcoded channel loop.
- `src/stackowl/infra/trace.py` -- `principal` contextvar (`start()` param, `_TraceToken` field, `reset()`, `get()` output), mirroring `channel`'s shape.
- `src/stackowl/scheduler/scheduler.py` -- `_bind_job_trace` sets `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` explicitly on every job's `TraceContext.start(...)`.
- `tests/channels/test_unwired_channel_consent_fails_closed.py` -- rewritten: an unwired channel now denies and opens exactly one incident (was: grants).
- `tests/tools/test_autonomous_consent.py` -- `TestTheRouterNoLongerDeniesUnasked` renamed `TestTheRouterNowDeniesUnasked`, asserts DENY.
- `tests/tools/test_permission_is_real_in_both_directions.py` -- `test_a_normal_consequential_tool_is_still_granted` (spec-named) and `test_execute_code_IS_now_autonomously_granted_by_decision` (collateral break found by running the suite -- same root cause, same fix) rewritten to bind `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` via `TraceContext.start(...)`.
- `tests/test_e0_s1_consent.py` -- `test_a_channel_with_no_UX_routes_to_the_AUTONOMOUS_GRANT` (collateral break, same root cause) renamed/rewritten to prove both halves: an unwired channel denies, and a declared principal still grants.
- `tests/journal/test_consent_events.py` -- extended: `consent.channel_unreachable` registration + dedupe-per-channel coverage.
- New: `tests/tools/test_the_autonomous_principal_bypasses_channel_routing.py`, `tests/tools/test_the_provenance_grant_never_reaches_web_or_voice.py`, `tests/tools/test_channel_comes_from_provenance_not_payload.py`, `tests/scheduler/test_scheduled_jobs_carry_the_autonomous_principal.py`.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- DW-31: `batch_approve`'s "Approve all" per-action gate bypass (J8), confirmed pre-existing and out of scope per this spec's own Boundaries.

**Verification performed:**
- `tests/tools/ tests/channels/ tests/journal/ tests/interaction/ tests/scheduler/ tests/startup/ tests/runtime/` -- all green (2739 + 550 + 980 across the touched suites).
- `tests/smoke/` -- 64 passed, 2 skipped.
- `tests/journeys/ tests/test_e0_s1_consent.py` -- 384 passed.
- `./scripts/tripwires.sh` -- TRIPWIRES PASS: 727 passed, 2 skipped; B4/B8/B9 clean; ruff 29≤35, mypy 57≤65 (both baselines unchanged by this diff).
- `uv run ruff check` / `uv run mypy` on every touched source file -- clean (pre-existing findings elsewhere in `orchestrator.py`/`test_e0_s1_consent.py`/`test_autonomous_consent.py` confirmed unrelated to this diff by line-range).

**Residual risks:** none material. Two collateral pre-existing test breaks (beyond the three named in this spec) were found only by running the full suite, not by static reading of the Code Map -- both were the same root cause (a test constructing `RoutingPrompter()` with an unwired channel and expecting the deleted autonomous fallback) and were fixed the same way the spec's own named test was fixed. `db_pool=None` (test/CLI default) remains a no-op journal write for `record_channel_unreachable`, matching every other Story 3.1-3.3 helper's convention -- the DENY itself never depends on it.
