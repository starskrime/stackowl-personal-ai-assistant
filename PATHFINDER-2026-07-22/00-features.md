# StackOwl Feature Inventory — Pathfinder 2026-07-22

Scope of this audit: how a user message travels channel → gateway → pipeline →
owl/tool/skill/provider → back to the user, plus retries, self-observability,
and the learning/evolution feedback loop. 20 features confirmed in Phase 0
(subagent report below); 11 are being flowcharted in Phase 1 (marked ▶) because
they are directly tied to the complaints driving this audit (unclear message
flow, unclear retries, duplicate solutions, provider "blindness" to
agent/owl/tool/skill/learning state) or to a Phase-0-flagged duplication
candidate. The remaining 9 are recorded for completeness but not traced deeply
in this pass.

## Flowcharted in Phase 1 (▶)

1. ▶ **Channel adapters → ingress** — `channels/base.py`, `channels/telegram/adapter.py:83`, `channels/cli_adapter.py:88`, `channels/registry.py:16`
2. ▶ **Gateway ingress routing** — `gateway/scanner.py:148`, `gateway/turn_router.py:221`, `gateway/turn_registry.py:114`
3. ▶ **Turn pipeline orchestration** — `pipeline/registry.py:34`, `pipeline/backends/asyncio_backend.py:95`, `pipeline/backends/langgraph_backend.py`, `pipeline/backends/factory.py:18`
4. ▶ **Owl routing / triage** — `pipeline/steps/triage.py:32`, `owls/router.py:147`, `owls/sticky_route_cache.py:38`
5. ▶ **Classify / context assembly** — `pipeline/steps/classify.py:600`, `pipeline/steps/assemble.py:56`
6. ▶ **Execute step (tool-use ReAct loop + plain-stream path)** — `pipeline/steps/execute.py:2557` (`_run_with_tools` L987, `_open_stream` L2301), `providers/_react.py:184`, `owls/guards.py:69`
7. ▶ **Provider abstraction + escalation + resilience** — `providers/llm_gateway.py:111`, `providers/registry.py:94`, `providers/tier_selector.py:24`, `providers/circuit_breaker.py:33`, `providers/_resilient_round.py:233`
8. ▶ **Delivery gates / honesty floors** — `pipeline/delivery_gate.py` (giveup L329, grounding L617, overclaim L918, critical-failure L1311, persistence-handoff L1475), `pipeline/steps/deliver.py:15`
9. ▶ **Retry/failure-recovery (application + durable layers)** — `pipeline/retry_actuator.py:70`, `memory/retry_queue_store.py:136`, `scheduler/handlers/retry_sweep.py:20`, `pipeline/durable/recovery.py:94,506`
10. ▶ **Self-observability** — `pipeline/steps/classify.py:278` (`_gather_recent_actions`), `pipeline/applied_lessons.py:22`, `pipeline/lesson_context.py:76`, `memory/outcome_store.py:89`
11. ▶ **Learning (DNA/evolution + failure/tool-outcome mining)** — `owls/evolution.py:160`, `learning/failure_outcome_miner.py:103`, `learning/tool_outcome_miner.py`

## Recorded, not deep-traced this pass

12. **Plain-stream path detail** — folded into #6 (same file, two branches of one decision).
13. **Provider tier/circuit-breaker** — folded into #7 (tightly coupled, one call chain).
14. **Turn persistence (non-durable)** — folded into #9 (`turn_persist.py` is the write-side of the same retry/recovery picture).
15. **Scheduler / proactive jobs** — `scheduler/scheduler.py:71`, 24 handlers under `scheduler/handlers/`. Only `retry_sweep` traced (via #9); the other 23 handlers not individually opened.
16. **Notifications (decision + transport)** — `notifications/router.py:111`, `notifications/deliverer.py:105`. No Phase-0 duplication flag.
17. **Skills subsystem** — `skills/store.py:159`, `skills/instruction_injector.py:95`. No Phase-0 duplication flag.
18. **Objectives / durable tasks (goal decomposition)** — `objectives/driver.py:108`. Phase-0 notes the codebase *itself* documents this as the sole decomposition owner (registry.py:22-33) — a self-flagged fragile boundary, carried forward as a note, not re-traced.
19. **Parliament (multi-owl debate)** — `parliament/orchestrator.py:27`. No Phase-0 duplication flag.
20. **Out of scope entirely this pass**: `authz/`, `tenancy/`, `webhooks/`, `audit/`, `plugins/`, `vision/`, `web_search/`, `mcp/`, `integrations/`, `ipc/`, `supervisor/`, `process/`, `setup/`, `service/`, `export/`, `media/`, `messaging/`, `embeddings/`, `health/` — exist per the source tree, not examined.

## Phase 0 duplication candidates carried into Phase 2

- `pipeline/backends/asyncio_backend.py:95` vs `pipeline/backends/langgraph_backend.py` — two orchestration backends driving the same `PIPELINE_STEPS`.
- `pipeline/retry_actuator.py:70` vs `providers/_resilient_round.py:233` vs `pipeline/durable/recovery.py:94,506` vs `scheduler/handlers/retry_sweep.py:20` — 4 independent retry/recovery mechanisms at different layers.
- `pipeline/durable/react_runner.py:75` (`DurableReActRunner`) vs `providers/_react.py:275` (`LoopGuard`) — two candidate loop-detection mechanisms.
- `gateway/scanner.py:148` vs `gateway/turn_router.py:221` vs `owls/router.py:147` — three "which owl handles this" routing concepts at different layers.
- `owls/evolution.py:160` vs `learning/failure_outcome_miner.py` + `tool_outcome_miner.py` — two learning/mining pipelines, unclear layering.
- `memory/retry_queue_store.py:136` vs `memory/message_ledger_store.py:85` — two persisted "pending delivery" tables, unclear overlap.
- CLAUDE.md documents a `read_logs` tool for AI self-query of logs — **confirmed not to exist** anywhere in `src/stackowl/tools/`. Either dead docs or a real missing capability; directly relevant to "provider is blind to what's happening."
- CLAUDE.md claims the channel adapter mints `TraceContext.start` — **confirmed false**; actual mint sites are `pipeline/backends/asyncio_backend.py:111` and `pipeline/backends/langgraph_backend.py:117`.

## Phase 0 subagent metadata

Sources consulted, full method, and confidence notes are in the Phase 0 agent
transcript (not duplicated here — see this file's git history / session log).
Known gaps: `LangGraphBackend` liveness not confirmed (exists + wired into
`create_backend()`, but whether any config path actually selects it over
`AsyncioBackend` is unverified — Phase 1 to confirm). Discord/Slack/WhatsApp
adapter parity with Telegram/CLI not verified. 23 of 24 scheduler handlers not
individually opened.
