---
title: 'The consent address survives the gateway↔core link'
type: 'feature'
created: '2026-09-19'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
baseline_revision: '1237fefe7d5e2127846139389ae718c4bf07c305'
context: [
  '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md',
  '{project-root}/_bmad-output/implementation-artifacts/spec-3-4-consent-never-grants-a-channel-nobody-can-answer.md',
]
warnings: [oversized]
deferred:
  - summary: >-
      No test in this diff exercises a real TelegramConsentPrompter/adapter
      actually delivering to a chat id — delivery is verified only up to the
      router-invocation boundary (a spy/fake router recording the correct
      reply_target value).
    evidence: |-
      Same pre-existing, project-wide test-proof substitution pattern already
      reviewed and accepted in spec-3-3's own triage log (in-process/real-socket
      test doubles standing in for scripts/dev_ingress.py's literal live-process
      proof); not caused by this story. TelegramConsentPrompter's address-first
      targeting (reading req.reply_target, resolving the chat id) is already
      covered by its own pre-existing tests, and this diff's real-socket
      tests/runtime/test_split_consent.py tests go further than most stories'
      pure in-process smoke simulation — the two suites compose to cover the
      full path even though no single test in this diff exercises both ends at
      once.
    location: >-
      tests/runtime/test_split_consent.py, tests/channels/telegram/consent.py
    severity: low
---

<intent-contract>

## Intent

**Problem:** `ConsentRequest.reply_target` ("WHERE to ask", threaded from `IngressMessage.chat_id`/`PipelineState.reply_target`) already exists in-process — added after it denied Bakir every `owl_build` from Telegram on 2026-08-19 (`chat_id.py` docstring). But `ConsentRequestFrame` (core→gateway, `ipc/frames.py:251`) never carries it: `SocketConsentPrompter.prompt()` (`runtime/socket_consent.py:60`) drops `req.reply_target` when building the frame, and `GatewayLink._handle_consent` (`runtime/gateway_link.py:888`) rebuilds `ConsentRequest` without it — so in split mode (core role, the live topology) every consent prompt that crosses the socket loses its address and the channel prompter falls back to guessing a chat id from `session_key`, exactly the class of bug the in-process fix closed, now reopened at the process boundary. Two more call sites still populate `ConsentRequest` in-process without ever reading the already-available `reply_target` off `TraceContext`, so hardening the wire (refuse rather than guess) would newly regress their live delivery.

**Approach:** Add `reply_target: int | str | None = None` to `ConsentRequestFrame`, bump `PROTOCOL_VERSION`. Thread it through `SocketConsentPrompter.prompt()` (frame) and `GatewayLink._handle_consent` (rebuilt `ConsentRequest`). A frame arriving with `reply_target is None` is refused (DENY) and logged, without ever invoking the channel prompter — never routed to a guessed destination. Fix the two live-turn call sites (`shell.py`, `tool_build.py`) that build `ConsentRequest`/call `gate.policy.request()` without reading `ctx.get("reply_target")` (mirrors the existing `objective_tool.py`/`cronjob.py` pattern); `owl_build.py`'s equivalent call site already threads it as of this diff too.

## Boundaries & Constraints

**Always:**
- `ConsentRequestFrame` gains `reply_target: int | str | None = None` (same type as `ConsentRequest.reply_target`/`IngressFrame.chat_id`); `PROTOCOL_VERSION` bumps from 4 to 5 (frame-shape change, per the existing convention documented on the constant itself).
- `SocketConsentPrompter.prompt()` passes `reply_target=req.reply_target` into the frame it sends.
- `GatewayLink._handle_consent`: when `frame.reply_target is None`, deny and log at ERROR (structured `consent_id`/`channel`/`tool_name`) WITHOUT calling `self._consent_router.prompt(...)` at all — the router/prompter never sees the request, so it can never fall back to a session-key guess for a socket-originated request. Otherwise rebuild `ConsentRequest` with `reply_target=frame.reply_target` and proceed exactly as today.
- `_handle_consent` gets 4-point structured logging (entry/decision/step/exit) — it currently has none.
- `shell.py::_gate_catastrophic` and `tool_build.py`'s create-consent call thread `reply_target=ctx.get("reply_target")` into `gate.policy.request(...)`, same as `owl_build.py` (fixed in this diff) and the existing `objective_tool.py`/`cronjob.py` precedent. All three are live/interactive-only paths (never reached by a scheduled/autonomous principal, which bypasses the prompter entirely per Story 3.4) — so `reply_target` is always the right, available value when these run.
- A pre-existing frame (or a peer running an older protocol version) that omits `reply_target` on the wire decodes it as `None` via the field default — it hits the same refuse-and-log path as a live one, never a decode error.

**Never:**
- Never widen `ConsentRequest`'s own dataclass shape or `ConsentPolicy.request()`'s signature — both already carry `reply_target`; only the frame and its two call/rebuild sites change.
- Never let `_handle_consent` fall back to `req.session_key` guessing on a missing `reply_target` — that fallback stays exactly where it already is (`TelegramConsentPrompter`, for in-process/proactive callers that legitimately have no turn target), never re-added on the socket path.
- Never touch `skills/authoring.py`'s `_consent_or_refuse` / `SkillWriteRequest` — confirmed scheduled-only (its own docstring: "unlike a live tool call ... has no ambient Tool/pipeline dispatch"), so it is always reached through the Story 3.4 principal bypass (`AutonomousPrompter`, never the socket/frame path) and never needs `reply_target`. Log it to `deferred-work.md` as a residual gap if a future live call path is ever added to that module.
- Never add a DB migration — this story touches only the wire frame and in-process call sites, no schema.
- Never implement Telegram forum-topic (`message_thread_id`) addressing — not implemented anywhere in this codebase today (`send_inline_keyboard`/`send_text` take only `chat_id`); "group or thread" in the AC is proven at the level this codebase already addresses conversations: a chat id (negative for a Telegram group).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| DM reply_target crosses the link | `ConsentRequest(reply_target=72055773, channel="telegram", ...)` via `SocketConsentPrompter` | Gateway's rebuilt `ConsentRequest.reply_target == 72055773`; router is invoked | No error expected |
| Group reply_target crosses the link | `reply_target=-100123456789` (negative Telegram group id) | Same as above, exact negative id preserved | No error expected |
| Frame arrives with no reply_target | `ConsentRequestFrame(..., reply_target=None)` (or an old peer's frame that omits the key) | `_handle_consent` denies, logs ERROR with consent_id/channel/tool, never calls the router | `ConsentResponseFrame(scope="deny")` returned; router.seen stays empty |
| Old-shape JSON (key absent) decodes | Wire bytes with no `"reply_target"` key | `decode_frame` succeeds, `reply_target is None` (field default) | Falls into the "arrives without" row above, not a decode error |

</intent-contract>

## Code Map

- `src/stackowl/ipc/frames.py:33` — `PROTOCOL_VERSION = 4` → `5`.
- `src/stackowl/ipc/frames.py:251-267` — `ConsentRequestFrame`: add `reply_target: int | str | None = None` field (after `session_key`, mirroring `ConsentRequest`'s field order) and a short docstring note (Story 3.5, mirrors `ConsentRequest.reply_target`'s own comment).
- `src/stackowl/runtime/socket_consent.py:59-69` — `SocketConsentPrompter.prompt()`: add `reply_target=req.reply_target,` to the `ConsentRequestFrame(...)` constructor call.
- `src/stackowl/runtime/gateway_link.py:882-909` — `_handle_consent`: add 4-point logging; before building `ConsentRequest`, check `frame.reply_target is None` → log ERROR + skip straight to sending `ConsentResponseFrame(consent_id=frame.consent_id, scope=ConsentScope.DENY.value)`, never touching `self._consent_router`. Otherwise add `reply_target=frame.reply_target` to the rebuilt `ConsentRequest(...)`.
- `src/stackowl/tools/system/shell.py:658-664` (`_gate_catastrophic`) — add `reply_target=ctx.get("reply_target"),` to the `gate.policy.request(...)` call (same `ctx = TraceContext.get()` already in scope at line 617).
- `src/stackowl/tools/meta/tool_build.py:408-419` — add `reply_target=ctx.get("reply_target"),` to the `gate.policy.request(...)` call (same `ctx` already in scope at line 384).
- `src/stackowl/tools/meta/owl_build.py:1059-1067` — add `reply_target=ctx.get("reply_target"),` to the `gate.policy.request(...)` call (same `ctx` already in scope at line 1027) — this is the exact call site named in the 2026-08-19 incident.
- **Tests to extend (pre-existing, going red or under-tested under the new contract):**
  - `tests/runtime/test_split_consent.py` — `test_consent_granted_round_trip` currently builds `ConsentRequest(...)` with no `reply_target`; under the new refuse-on-missing rule it would now DENY before ever reaching `_FakeRouter`, breaking the test's SESSION assertion. Add `reply_target=555` (DM) to that request; extend the assertion to check `router.seen[0].reply_target == 555`.
  - `tests/ipc/test_codec.py` — `ConsentRequestFrame`/`ConsentResponseFrame` are missing from `ALL_FRAMES` entirely (pre-existing gap, not story-caused, but the natural place to close it while touching this frame). Add both, including a `ConsentRequestFrame` with a negative `reply_target` (group id).
- **New tests:**
  - `tests/runtime/test_split_consent.py` — `test_consent_refused_when_reply_target_missing`: a `ConsentRequest` with `reply_target=None` crosses the link; assert the returned scope is DENY and `router.seen == []` (the router was never invoked — proves "never routed to a guessed destination", not merely "ends up denied").
  - `tests/runtime/test_split_consent.py` — `test_consent_reply_target_survives_the_link_group`: same round trip as the granted test but with a negative (group) `reply_target`; assert the exact value survives to `router.seen[0].reply_target`.
  - `tests/ipc/test_codec.py` — `test_consent_request_frame_without_reply_target_decodes_to_none`: hand-construct wire JSON for a `consent_request` frame with no `reply_target` key at all (simulating an old-protocol peer) and assert `decode_frame(...).reply_target is None` rather than raising.
  - `tests/tools/system/test_shell_catastrophic_consent_carries_reply_target.py` (or nearest existing shell-consent test file) — a spy prompter records `req.reply_target`; a `TraceContext.start(..., reply_target=...)` scope around a catastrophic shell call proves the value reaches `gate.policy.request`.
  - `tests/tools/meta/test_owl_build_reply_target.py` (or extend `test_owl_build_reversible_consent.py`'s `_SpyPrompter` to record the request) — proves `owl_build`'s create-consent call now threads `reply_target` from `TraceContext`.
  - `tests/tools/meta/test_tool_build_reply_target.py` (or nearest existing tool_build-consent test) — same proof for `tool_build.py`.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/ipc/frames.py` -- add `reply_target` to `ConsentRequestFrame`, bump `PROTOCOL_VERSION` -- AC1.
- `src/stackowl/runtime/socket_consent.py` -- thread `req.reply_target` into the outgoing frame -- AC1.
- `src/stackowl/runtime/gateway_link.py` -- rebuild `ConsentRequest` with `reply_target`; refuse + log when the frame carries none, never invoking the router -- AC1, AC3.
- `src/stackowl/tools/system/shell.py`, `src/stackowl/tools/meta/tool_build.py`, `src/stackowl/tools/meta/owl_build.py` -- thread `reply_target=ctx.get("reply_target")` into their direct `gate.policy.request()` calls, so the AC3 hardening does not regress these live delivery paths -- AC2.
- Fix `tests/runtime/test_split_consent.py`'s pre-existing test now red under the new contract; add the new frame/refusal/reply-target tests listed in Code Map -- AC1-AC3.
- Add `ConsentRequestFrame`/`ConsentResponseFrame` to `tests/ipc/test_codec.py`'s round-trip coverage -- AC1.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- log `skills/authoring.py`'s live-path gap as a residual, currently-unreachable risk (confirmed out of scope; see Boundaries).

**Acceptance Criteria:**
- Given a consent request raised in core for a turn that arrived through the gateway, when `ConsentRequestFrame` crosses the link, then it carries `reply_target`, the frame stays typed with `extra=forbid`, and `protocol_version` is bumped.
- Given a turn from a Telegram group (a negative chat id, standing in for "group or thread" — this codebase addresses both the same way), when consent is requested, then the exact `reply_target` value survives the round trip through the real `GatewayLink`/`SocketConsentPrompter` socket link unchanged, reaching the channel prompter that would deliver to it.
- Given a frame that arrives without a `reply_target`, when it is handled, then the request is refused, the refusal is logged, and the channel router/prompter is never invoked — never routed to a guessed destination.

## Review Triage Log

### 2026-09-19 — Review pass
- verdicts: 14 findings — high 1, medium 0, low 10, false 3, maybe-false 0
- findings:
  - `[low]` `defer` (intent-alignment) No test in this diff exercises a real `TelegramConsentPrompter`/adapter actually delivering to a chat id — the delivery-behavior claim is verified only up to the router-invocation boundary (spy/fake). Same pre-existing, project-wide test-proof substitution pattern already reviewed and accepted in spec-3-3's own triage log (in-process/real-socket doubles standing in for `scripts/dev_ingress.py`'s literal live-process proof); not caused by this story. `TelegramConsentPrompter`'s address-first targeting is already covered by its own pre-existing tests, and this diff's real-socket `test_split_consent.py` tests go further than most stories' pure in-process smoke simulation — the two suites compose to cover the full path.
  - `[false]` `reject` (intent-alignment) Reading (d) — "every live consent call site should be audited" — is rejected by the diff, which fixes only the three known sites plus leaves `skills/authoring.py` untouched. Accurately restates an already-deliberate, already-logged scope boundary (DW-32, spec Boundaries), not a newly-identified defect: confirmed by direct code read that Story 3.4's principal bypass (`ConsentPolicy.request` routes to `AutonomousPrompter` before `self.prompter` when `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER`) makes that path genuinely unreachable via the socket today.
  - `[false]` `reject` (intent-alignment) Minor test file-placement note (spec's Code Map named a new file; diff extended an existing one instead) — the auditor itself states this is "not a divergence," functionally equivalent.
  - `[high]` `patch` (verification-gap) `src/stackowl/tools/scheduling/objective_tool.py:320-326` (`_gate_epic_consent`) calls `gate.policy.request(...)` without `reply_target=ctx.get("reply_target")`, despite requiring the same live/interactive-only shape (`interactive`+`session_key`+`channel`) as the three sites this diff already fixed, and despite `ctx` already being in scope two lines above. Confirmed by direct read: the spec's own Design Notes incorrectly claimed this file "already does" — that claim conflated an unrelated `_resolve_durable_target` usage with the consent-gate call, which never reads `reply_target`. In split mode this diff's own AC3 hardening now hard-denies the platform's one gate on an entire unattended objective/epic run, unconditionally, with no test catching it (the test double in `test_objective_tool.py` discards kwargs).
  - `[false]` `reject` (blind-hunter) `deferred: []` frontmatter stays empty despite DW-32 being logged to `deferred-work.md`. The spec's `deferred:` field is explicitly scoped (spec-template.md's own comment) to review-triage-produced deferred findings — a deliberately separate mechanism from the `deferred-work.md` backlog ledger DW-items use. Spec-3-4 uses the identical convention. Conflating the two would misuse the field, not fix a gap.
  - `[low]` `reject` (blind-hunter) Code Map cites `ctx = TraceContext.get()` as in scope "at line 617" (shell.py) and "at line 384" (tool_build.py); actual lines are 618 and 385. Fix is to edit this build's spec — rejected per the explicit rule against spec-edit fixes.
  - `[low]` `reject` (blind-hunter) The "old-protocol peer" framing for the decode-to-None fallback is weaker than the real justification (same-version bug / malformed frame / future omission), which isn't written down. Cosmetic rationale-wording preference; the code docstring's actual claim ("old-shape wire bytes, or the field default") already covers the broader, accurate justification — not a functional defect.
  - `[low]` `reject` (blind-hunter) `log.gateway.error` on missing `reply_target` fires unconditionally with no metric/rate-limit note. Unlikely in everyday use — Hello's `protocol_version` check already refuses a genuinely cross-version link before any `ConsentRequestFrame` can arrive, so this should fire only for a same-version bug or a future caller forgetting `reply_target`; adding metrics/rate-limiting is more than a direct correction.
  - `[low]` `patch` (blind-hunter) `tests/tools/meta/test_owl_build_reply_target.py` and `tests/tools/meta/test_tool_build_reply_target.py` both define `async def test_create_consent_carries_reply_target` — confirmed identical names across files, defeating a flat `-k` search. Trivial rename, no public surface.
  - `[low]` `patch` (blind-hunter) AC1 claims the frame "stays typed with `extra=forbid`" but no test in the diff (or pre-existing in `tests/ipc/test_codec.py`) constructs a frame with an unexpected key and asserts rejection. Confirmed: no test anywhere in `tests/ipc/` verifies `extra="forbid"` for any frame type. This story's own AC1 text explicitly claims the property for `ConsentRequestFrame`, so add one direct test.
  - `[low]` `patch` (blind-hunter) `test_tool_build_reply_target.py`'s test is about consent but omits `@pytest.mark.no_official_origin`, unlike its sibling `test_owl_build_reply_target.py` and the documented convention in `tests/tools/meta/conftest.py`'s `_official_origin` fixture. Confirmed harmless today only because `channel="telegram"` isn't the fixture's registered `"cli"` channel — trivial one-line fix for robustness/consistency.
  - `[low]` `reject` (edge-case-hunter, grouped: `shell.py:665`, `tool_build.py:415`, `owl_build.py:1064`) When `ctx.get("reply_target")` is `None` despite `interactive`+`channel`+`session_key` being present, the three sites this diff touches still call `gate.policy.request(reply_target=None)`, which now hard-denies over the wire with no call-site-local diagnostic. Same defect across all three (grouped). The resulting DENY is the correct, spec-mandated AC3 behavior (never guess), already logged with `consent_id`/`channel`/`tool_name` at `GatewayLink._handle_consent`; `reply_target` being unset for a live, channel-wired interactive turn indicates a separate ingress bug outside this diff's scope, unlikely in everyday use, and a call-site-local pre-check is more than a direct correction (new guard/branch x3).

## Design Notes

**Why fix `shell.py`/`tool_build.py`/`owl_build.py`, not just the frame.** All three are interactive-only, human-attended consent calls (each explicitly requires a live channel+session, never the scheduled/autonomous principal path that bypasses the prompter — Story 3.4). Before this diff, all three silently sent `reply_target=None`, relying entirely on the `TelegramConsentPrompter`'s `session_key` fallback — the exact fragile guess AC3 removes on the wire. Hardening `_handle_consent` to refuse a missing `reply_target` without also fixing these three would turn "sometimes wrong" (a guess that happens to work for a bare numeric session_key) into "always refused" for three real, owner-facing approval flows — one of which (`owl_build`) is the literal incident this whole mechanism exists to prevent. `ctx.get("reply_target")` is already populated on every live turn's `TraceContext` (`pipeline/backends/shared.py::bind_turn_context`); these three call sites simply never read it, unlike `tools/scheduling/objective_tool.py`/`cronjob.py`, which already do.

**Why `authoring.py` is out of scope.** Its own docstring states it is reached only where "a scheduled skill-authoring pass has no ambient Tool/pipeline dispatch" — i.e., never a live turn. A scheduled caller's `TraceContext` carries `principal=PRINCIPAL_AUTONOMOUS_SCHEDULER` (Story 3.4), so `ConsentPolicy.request()` routes straight to `AutonomousPrompter()`, bypassing `self.prompter` (and therefore the socket/frame path) entirely — `reply_target` is never consulted on that branch. If a future change ever lets a live turn reach this module directly (bypassing `ConsequentialActionGate.check()`), it would need the same fix; not needed today.

## Verification

**Commands:**
- `uv run pytest tests/runtime/test_split_consent.py tests/ipc/test_codec.py -x` -- expected: all pass, including the new reply_target/refusal tests.
- `uv run pytest tests/tools/system/ tests/tools/meta/ -x` -- expected: all pass, including the new reply_target-threading tests for shell/owl_build/tool_build.
- `uv run pytest tests/tools/ tests/channels/ tests/journal/ tests/runtime/ tests/ipc/ tests/scheduler/ tests/startup/ -x` -- expected: all pass (no regression in the areas touched by Story 3.4's neighboring contract).
- `uv run pytest tests/smoke/ -x` -- expected: unaffected, all pass.
- `./scripts/tripwires.sh` -- expected: clean, before every commit.

## Auto Run Result

**Summary of implemented change:** `ConsentRequestFrame` now carries `reply_target` (`PROTOCOL_VERSION` bumped 4->5). `SocketConsentPrompter.prompt()` threads `req.reply_target` into the outgoing frame. `GatewayLink._handle_consent` gained 4-point structured logging and now refuses (DENY, logged at ERROR with `consent_id`/`channel`/`tool_name`) any frame whose `reply_target is None` WITHOUT ever invoking `self._consent_router` -- the router/prompter can never fall back to guessing a chat id from `session_key` for a socket-originated request. A frame that does carry `reply_target` rebuilds `ConsentRequest` with it and proceeds exactly as before. Four live/interactive-only call sites that built a consent request without ever reading `TraceContext`'s already-populated `reply_target` (`shell.py::_gate_catastrophic`, `tool_build.py`'s create-consent call, `owl_build.py`'s create-consent call -- the exact site named in the 2026-08-19 incident -- and `objective_tool.py::_gate_epic_consent`, found by review) now thread `reply_target=ctx.get("reply_target")` into `gate.policy.request(...)`.

**Files changed:**
- `src/stackowl/ipc/frames.py` -- `ConsentRequestFrame` gains `reply_target: int | str | None = None`; `PROTOCOL_VERSION` 4->5.
- `src/stackowl/runtime/socket_consent.py` -- `SocketConsentPrompter.prompt()` passes `reply_target=req.reply_target` into the outgoing frame.
- `src/stackowl/runtime/gateway_link.py` -- `_handle_consent` rewritten: 4-point logging, refuse-before-routing when `frame.reply_target is None`, otherwise rebuilds `ConsentRequest` with `reply_target=frame.reply_target`.
- `src/stackowl/tools/system/shell.py` -- `_gate_catastrophic` threads `reply_target=ctx.get("reply_target")` into `gate.policy.request(...)`.
- `src/stackowl/tools/meta/tool_build.py` -- create-consent call threads `reply_target=ctx.get("reply_target")`.
- `src/stackowl/tools/meta/owl_build.py` -- create-consent call threads `reply_target=ctx.get("reply_target")`.
- `src/stackowl/tools/scheduling/objective_tool.py` -- `_gate_epic_consent` threads `reply_target=ctx.get("reply_target")` (review patch, VG-1).
- `tests/runtime/test_split_consent.py` -- `test_consent_granted_round_trip` now sets `reply_target=555` and asserts it survives to `router.seen[0]`; new `test_consent_refused_when_reply_target_missing` (proves the router is never invoked, not merely that the outcome is DENY) and `test_consent_reply_target_survives_the_link_group` (negative/group id survives the real socket round trip).
- `tests/ipc/test_codec.py` -- `ConsentRequestFrame`/`ConsentResponseFrame` added to `ALL_FRAMES` (including a negative-`reply_target` case), closing a pre-existing coverage gap; new `test_consent_request_frame_without_reply_target_decodes_to_none` hand-constructs old-shape wire JSON with no `reply_target` key and asserts it decodes to `None` rather than raising; new `test_consent_request_frame_rejects_unknown_field` proves AC1's `extra=forbid` claim (review patch, BH-6).
- `tests/tools/system/test_shell_consent.py` -- new `test_catastrophic_consent_carries_reply_target` (spy prompter records `req.reply_target` sourced from a `TraceContext.start(..., reply_target=...)` scope).
- `tests/tools/meta/test_tool_build_reply_target.py` -- new file: proves `tool_build`'s create-consent call threads `reply_target`; test renamed to `test_tool_build_create_consent_carries_reply_target` and given `@pytest.mark.no_official_origin` (review patches, BH-5/BH-7).
- `tests/tools/meta/test_owl_build_reply_target.py` -- new file: proves `owl_build`'s create-consent call threads `reply_target` (the 2026-08-19 incident's own call site); test renamed to `test_owl_build_create_consent_carries_reply_target` (review patch, BH-5).
- `tests/tools/scheduling/test_objective_tool.py` -- new `test_repo_bearing_call_consent_carries_reply_target` proves the `objective_tool.py` fix (review patch, VG-1); `_FakePolicy` gained a `calls` list to record kwargs.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- DW-32: `skills/authoring.py`'s live-path gap, confirmed scheduled-only and out of scope per this spec's own Boundaries.

**Review findings breakdown (2026-09-19 pass, 14 findings from 4 layers):**
- Patched (4): VG-1 `[high]` `objective_tool.py` missing `reply_target` thread (the review's one real functional gap -- a fourth live call site the implementation missed, on the platform's one consent gate for an entire unattended objective/epic run); BH-5 `[low]` duplicate test function name across two new files; BH-6 `[low]` AC1's `extra=forbid` claim was untested; BH-7 `[low]` missing `@pytest.mark.no_official_origin` for consistency with the sibling test.
- Deferred (1): IA-1 `[low]` no test in this diff exercises a real `TelegramConsentPrompter`/adapter delivering to a chat id (verified only to the router-invocation boundary) -- same pre-existing, project-wide test-proof substitution pattern already reviewed and accepted in spec-3-3's own triage log; recorded in this spec's `deferred:` frontmatter.
- Rejected (9): IA-2 `[false]` "every call site should be audited" reading -- accurately restates the already-deliberate DW-32 scope boundary, not a new defect; IA-3 `[false]` minor test-file-placement note, auditor itself says not a divergence; BH-1 `[false]` `deferred: []` vs DW-32 -- two deliberately separate mechanisms (spec-3-4 uses the same convention); BH-2 `[low]` Code Map line-citation off-by-one -- fix is to edit this build's spec, rejected per that explicit rule; BH-3 `[low]` decode-to-None rationale wording preference -- not a functional defect; BH-4 `[low]` unconditional ERROR log with no metric/rate-limit -- unlikely in everyday use (Hello's protocol check already prevents genuine cross-version frames), fix is more than a direct correction; EC-1/EC-2/EC-3 `[low]` (grouped) no call-site-local diagnostic when `reply_target` is `None` at the three originally-fixed sites -- the resulting DENY is the correct, spec-mandated AC3 behavior, already logged at `GatewayLink._handle_consent`, and a missing `reply_target` on a live wired-channel turn indicates a separate ingress bug outside this diff's scope.

**Follow-up review recommendation:** `true`. One patched entry (VG-1) was `high` at entry verdict. Named unverified risk: the `objective_tool.py::_gate_epic_consent` fix was verified by direct code read, a new passing test (`test_repo_bearing_call_consent_carries_reply_target`), and a full regression run (see below) -- but, being a fourth call site the original implementation missed, it raises the question of whether a fifth still-undiscovered live call site exists elsewhere in the codebase that builds a `ConsentRequest`/calls `gate.policy.request(...)` without threading `reply_target`. This pass's `grep -rn "policy.request(" src/` (re-run during triage) found no further candidates beyond the five now-accounted-for call sites (`registry.py`'s wrapper, plus the four direct sites: `shell.py`, `tool_build.py`, `owl_build.py`, `objective_tool.py`) and the confirmed-out-of-scope `skills/authoring.py` -- but a second independent review pass was not run against the patched diff.

**Verification performed (independently re-run against the patched tree, not the implementer's self-report):**
- `tests/runtime/test_split_consent.py tests/ipc/test_codec.py` -- 30 passed.
- `tests/tools/system/ tests/tools/meta/ tests/tools/scheduling/` -- 702 passed, 3 warnings (pre-existing, unrelated).
- `tests/tools/ tests/channels/ tests/journal/ tests/runtime/ tests/ipc/ tests/scheduler/ tests/startup/` -- 4078 passed, 4 skipped, 120 warnings (pre-existing, unrelated), in 392.89s.
- `tests/smoke/` -- 64 passed, 2 skipped.
- `./scripts/tripwires.sh` -- TRIPWIRES PASS: 727 passed, 2 skipped; B4/B8/B9 clean; ruff 29<=35, mypy 57<=65 (both baselines unchanged by this diff).

**Residual risks:** the named follow-up-review risk above (a possible fifth undiscovered call site, judged unlikely given the `grep` re-check). The one deliberately-scoped-out gap (`skills/authoring.py`'s scheduled-only path never reading `reply_target`) is logged as DW-32. `sprint-status.yaml` was NOT edited by the implementing subagent or this run -- earlier in this run the implementation subagent (against explicit dispatch-time instructions) edited it to mark this story `done`; that edit was reverted before review, and `sprint-status.yaml` bookkeeping is left to the dispatching session, consistent with the Story 3.4 precedent (commit `1237fefe`).

**Residual risks:** none material. The one deliberately-scoped-out gap (`skills/authoring.py`'s scheduled-only path never reading `reply_target`) is logged as DW-32, matching Story 3.4's own precedent for `authoring.py`'s always-autonomous-principal routing.
