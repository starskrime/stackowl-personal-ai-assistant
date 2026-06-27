# Backlog (by severity)

All 88 findings from FINDINGS.md re-sorted by severity. The unifying theme:
StackOwl is **honest** about failure (B1–B4 verification/recovery shipped) but not yet
**agentic** — the recurring chatbot pattern is *give up cleanly and hand control back*
rather than *retry / route-around / resume the goal*. Two meta-roots dominate:
(1) **silent give-up** — failures swallowed into success-shaped results or surrendered
after one attempt; (2) **registered≠reachable** — learning/proactive machinery built but
never read/fired on the live path.

Total: 14×S1 · 30×S2 · 35×S3 · 9×S4

---

## S1 — Critical (agent silently gives up or loses the goal; user must rescue)

- **F-16** Provider escalation loop has no try/except — a raised provider fault dead-ends to the user, no tier fallback. `providers/llm_gateway.py:97-114`
- **F-17** Failure-fallback only fires on a *pre-call* OPEN breaker — the first request that trips a provider still errors to the user. `providers/registry.py:534-545`
- **F-29** `send_file` returns `success=True` even when delivery `failed`/`deferred`. `tools/scheduling/send_file.py:226-238`
- **F-30** `send_message` returns `success=True` on a failed/deferred send. `tools/scheduling/send_message.py:220-228`
- **F-35** In-flight turn lost on core crash — `finalize` ends the reader but never replays the already-sent turn. `runtime/gateway_link.py:138-172`
- **F-40** Failed/parked sub-goal blocks the objective with no retry/replan/escalation. `objectives/driver.py:169-175`
- **F-42** Sub-goal with no declared acceptance criterion completes on no-error self-assertion. `objectives/driver.py:184-211`
- **F-45** SQLite heuristic store is write-only — `find_for_tool` has zero callers on the live path. `learning/tool_heuristic_store.py:113-132`
- **F-60** Recurring proactive job marked terminal `failed` after 3 retries — never re-arms, no notice. `scheduler/scheduler.py:196-240`
- **F-64** Slack on-turn reply silently dropped on transport failure (swallowed in `_post_text`). `channels/slack/adapter.py:904-916`
- **F-67** Reaped wedged turn drops its `original_input` objective — no goal resume. `gateway/turn_registry.py:650-687`
- **F-76** `/urgent` reports "delivered" while `router.deliver` never touches a channel adapter. `commands/urgent_command.py:117-148`
- **F-77** `notification_digest` handler has no scheduler seed — batched notifications never flush. `notifications/digest_job.py:66-76`
- **F-82** MCP `call_tool` connection failures silently return `""` as success — no retry/fallback. `mcp/client.py:112-136`

## S2 — Major (autonomy badly degraded; works only with hand-holding)

- **F-3** Clarify verdict surfaced to interactive user with no autonomous default attempt. `pipeline/steps/execute.py:1772-1806`
- **F-6** Substitution capped at one sibling per capability class per turn. `pipeline/steps/execute.py:429-449`
- **F-7** Retry-once restricted to unverified effects; transient genuine failures get no retry. `pipeline/steps/execute.py:1130-1149`
- **F-11** Normal user turns never verify acceptance (checker is objectives-only). `pipeline/state.py:181-184`
- **F-12** Acceptance verification covers only "saved file" — every non-file effect unverifiable. `pipeline/acceptance.py:132-147`
- **F-15** Delivery judge fails OPEN — judge error/unparse ⇒ `delivered=True`. `pipeline/persistence.py:398-421`
- **F-18** Rate-limiter cap refusal propagates with no alternate-provider fallback. `providers/rate_limiter.py:153-168`
- **F-24** `Tool.__call__` wraps failures with no retry/fallback/sibling substitution. `tools/base.py:167-176`
- **F-25** Verify seam skipped when a tool self-stamps `verified=True` (claim trusted as proof). `tools/base.py:181-197`
- **F-31** `shell` reports success on `returncode==0` with no effect read-back. `tools/system/shell.py:485-500`
- **F-32** `web_fetch` self-asserts success without checking HTTP status (404/500 staged as fact). `tools/io/web_fetch.py:144-190`
- **F-36** Crash respawn does not re-arm the boot-timeout guard (hung reboot ⇒ infinite buffering). `startup/orchestrator.py:188-204`
- **F-37** Drain stragglers abandoned on restart with no replay or user notice. `runtime/drain.py:55-66`
- **F-41** Active-only scan + terminal `blocked` ⇒ a stalled objective is never resumed. `objectives/driver.py:102-113`
- **F-44** Sub-goal clarifications (even trivial) hard-block the whole objective. `objectives/driver.py:158-167`
- **F-48** Positive-only reflection store: failures never remembered, so mistakes recur. `memory/reflection_store.py:47-63`
- **F-49** Reflection recall fails open to empty — agent acts blind when memory errors. `pipeline/steps/classify.py:166-184`
- **F-52** No DNA trait drives persistence/initiative — traits are tone-only. `owls/dna_defaults.py:6-8`
- **F-58** Synthesis parser silently downgrades a contract violation to a fake verdict. `parliament/synthesis_parser.py:69-89`
- **F-59** Fabricated fallback "consensus" staged into long-lived memory as a pellet. `parliament/pellet_generator.py:108-145`
- **F-61** `_mark_failed` writes no audit row and no notification — outage is trace-only. `scheduler/scheduler.py:236-240`
- **F-62** Unknown-handler job marked terminally `failed`, not retained for late registration. `scheduler/scheduler.py:169-177`
- **F-65** Telegram no-target file send is a silent `return`, never reaches ledger as `failed`. `channels/telegram/adapter.py:628-660`
- **F-66** Discord/WhatsApp file-upload transport failure swallowed (no retry, no ledger signal). `channels/discord/adapter.py:300-309`
- **F-70** Reversible soft cost-pause bounces a trivial "Continue?" to the user. `interaction/cost_pause.py:117-192`
- **F-71** Clarify gateway has no instinct/auto-answer path — every ambiguity parks on the human. `interaction/clarify_gateway.py:142-220`
- **F-80** `/connect` reports "connected" without calling the available `is_connected()`. `commands/connect_command.py:116-130`
- **F-81** Config/provider writes claim `✓` without re-reading to confirm the write landed. `commands/provider_command.py:286-296`
- **F-83** `McpTool` reports `success=True` with empty output when the underlying call failed. `mcp/_tool.py:103-128`
- **F-84** MCP discovery dead-ends to `[]` on connect failure and caches it (blip ⇒ "no tools"). `mcp/client.py:54-82`
- **F-85** Watchdog pings systemd alive on a blind timer with no liveness/health gating. `service/watchdog.py:105-117`
- **F-86** Reachability census is built but never run at boot. `health/reachability/census.py:62-87`
- **F-87** Health is detect-only/on-demand — ResilienceContributor never wired, no heal trigger. `health/aggregator.py:34-54`
- **F-88** Mono-role boot has no crash supervision — a recoverable crash just dies. `startup/orchestrator.py:2856-2859`

## S3 — Minor (partial autonomy; recoverable but clunky)

- **F-1** Goal-level acceptance OFF by default on normal turns. `pipeline/acceptance.py:19-21`
- **F-2** Normal pipeline is single-pass; no decompose/plan step. `pipeline/registry.py:21-29`
- **F-4** Prior-failure outcomes not read in the execute loop before acting. `pipeline/steps/execute.py / assemble.py:170`
- **F-5** Substitution actuator absorbs all exceptions and surrenders. `pipeline/steps/execute.py:464-470`
- **F-8** Apology cascade failure ⇒ static neutral marker, no retry across tiers. `pipeline/critical_failure.py:193-208`
- **F-9** Recovery-summary explanation hardwired to English. `pipeline/recovery_summary.py:18-23`
- **F-10** Recovery annotation suppressed whenever the answer is a floor. `pipeline/recovery_summary.py:31-40`
- **F-13** Deterministic acceptance fails OPEN to "no opinion" on FS error. `pipeline/acceptance.py:106-107`
- **F-14** LLM-derived acceptance is flag-OFF by default. `config/settings.py:790`
- **F-19** Escalation cascade is success-driven only — no failure trace on give-up. `providers/llm_gateway.py:104-114`
- **F-20** Anthropic `complete()` accepts empty generation as success. `providers/anthropic_provider.py:644-653`
- **F-21** Anthropic catches only `anthropic.APIError`; other transport faults escape unwrapped. `providers/anthropic_provider.py:124-130`
- **F-23** Gemini returns empty/blocked text as success (no `finish_reason` check). `providers/gemini_provider.py:193-203`
- **F-26** Tool registry never consults prior outcomes before dispatch. `tools/registry.py:270-290`
- **F-27** Consent gate fails closed with no reversibility/triviality tier. `tools/consent.py:203-219`
- **F-33** `write_file` does not read back content; verify only checks existence/freshness. `tools/io/write_file.py:60-98`
- **F-34** `image_generate`/`tts` assert success before observing bytes (verify hook redeems). `tools/media/image_generate.py:152-161`
- **F-38** Buffered/flushed messages swallow exceptions with no retry/feedback. `runtime/gateway_link.py:174-185`
- **F-39** Core dispatch crash closes the stream but never tells the user the turn failed. `runtime/core_link.py:108-123`
- **F-43** Objectives driver never reads prior outcomes before retrying/advancing. `objectives/driver.py:135-155`
- **F-46** `ToolHeuristic.mean_quality` persisted but feeds no decision. `learning/heuristic_matcher.py:11-15`
- **F-47** Heuristics surface into the prompt with no provenance/evidence on the decision path. `pipeline/steps/classify.py:386-403`
- **F-50** Reflection retrieval recency-only, not semantic. `memory/reflection_store.py:225-240`
- **F-51** Outcome capture positive-only — failed runs never scored/mined. `memory/outcome_store.py:140-169`
- **F-53** Residual ask-first bias: `curiosity` still framed as a clarify-gate. `owls/dna_injector.py:22-26`
- **F-54** Evolution learns from successes only — failures excluded. `owls/dna_attribution.py:139-146`
- **F-56** Router still encodes a `clarify` verdict; a2a default-denies it, forcing ask-up. `owls/router.py:188-195`
- **F-57** Single-pass synthesis with no verification the LLM honored the contract. `parliament/synthesizer.py:124-147`
- **F-63** Idempotency skip can pin a recurring occurrence at a past instant. `scheduler/scheduler.py:128-139`
- **F-68** Clarify timeout bounces a defaultable decision to the model with no auto-resume. `tools/interaction/clarify.py:56-60`
- **F-72** Intent classifier stateless across turns — no learning from misclassifications. `interaction/intent_classifier.py:152-200`
- **F-73** Supervisor restarts blindly; detects stuck but never nudges toward a goal. `supervisor/supervisor.py:90-135`
- **F-74** Supervisor give-up floor on max failures with no escalation. `supervisor/supervisor.py:114-132`
- **F-78** EventDeliveryBridge dormant — empty allow-list, no event-driven proactivity. `notifications/event_bridge.py:44-85`
- **F-79** Brief assemblers omit-on-empty off one hardcoded query — reports state, doesn't anticipate. `brief/assemblers.py:140-195`

## S4 — Cosmetic (explainability/trace gaps; no behavioral impact)

- **F-22** OpenAI empty-generation retry re-issues the identical call (no variation). `providers/openai_provider.py:814-831`
- **F-28** No proactive/next-step hook in the tool execution seam. `tools/base.py:156-205`
- **F-55** Per-owl evolution failures swallowed and counted "stuck" with no retry. `owls/evolution.py:354-359`
- **F-69** Clarify tool always parks; "act on most likely" lives only in the description string. `tools/interaction/clarify.py:200-213`
- **F-75** Supervisor: no verification a "completed" task did useful work (spin risk). `supervisor/supervisor.py:99-106`

---

## Recommended remediation order (highest leverage first)

1. **Self-assert→measured success at the boundary tools** (F-29, F-30, F-31, F-32, F-83) —
   the send_*/shell/web_fetch/MCP tools that report success on a failed effect are the most
   direct "lies to the user." Map delivery/HTTP/transport status onto `success`/`verified`.
2. **Provider-fault fallback in the gateway loop** (F-16, F-17, F-18) — wrap per-tier calls
   so a hard provider error cascades instead of dead-ending. Single highest-impact self-heal.
3. **Objective + turn resumption** (F-40, F-42, F-35, F-67, F-41) — make a blocked goal
   recoverable and replay a turn lost to a crash/wedge, instead of stranding it.
4. **Close registered≠reachable gaps** (F-45, F-76, F-77, F-86) — wire the learning/proactive
   machinery that exists but never fires on the live path.
5. **Reduce ask-first reflexes with safe defaults** (F-3, F-44, F-70, F-71, F-27) — resolve
   reversible/trivial ambiguity autonomously; reserve human prompts for irreversible actions.

---

## ADR implementation log (findings closed by each shipped ADR)

### ADR-1 — AcceptanceAuthority (SHIPPED 2026-06-27, flag `acceptance_authority` ON in prod)
The single authority that answers "did the declared effect happen?" now owns the success
truth; the ≥6 proxies DELEGATE (they read the one `verified`/`is_trustworthy_success`
signal the authority writes — none deleted). Closures:
- **Closed by the unification** (proxies now read ONE verdict instead of re-deriving;
  self-stamp ignored; the asserted→measured invariant has tests per kind):
  F-1, F-11, F-12, F-13, F-14, F-15, F-25, F-75. ⤷F-10.
- **Closed THROUGH the authority on the live path** (truth now flows through it):
  F-29, F-30 (send_message/send_file declare DeliveryAck — the transport ack, distinct
  from the success bool, sets `verified`).
- **Already mitigated per-seam by S1–S4, now UNIFIABLE under the authority** (each already
  self-verifies correctly; the authority is now available to express them as PostConditions,
  incremental hardening, not a regression risk): F-20, F-23 (provider empty→NonEmptyText),
  F-31 (shell exit/artifact), F-32 (web_fetch already gates HTTP status→HttpOk),
  F-33, F-34 (write/media verify()→ArtifactFresh), F-80, F-81 (CLI), F-82, F-83 (MCP already
  distinguishes empty-success from transport error).
- **Incremental follow-on (optional, authority now enables it):** migrate the already-honest
  tools above to DECLARE post_condition() so their truth also routes through the authority
  (web_fetch→HttpOk/NonEmptyText, write/media→ArtifactFresh, a provider-empty path→NonEmptyText).
  Not blockers — those seams already self-verify; this only centralizes the derivation.
