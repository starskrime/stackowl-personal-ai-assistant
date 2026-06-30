# Audit Plan

Read-only Jarvis-vs-chatbot diagnostic audit. One module per iteration.
`[ ]` = not yet audited, `[x]` = audited. Coverage (all `[x]`) = done.

## Module map (ordered by reliability-criticality)

- [x] Pipeline — ReAct execution core — src/stackowl/pipeline/execute*.py, supervisor.py, state.py, streaming.py, registry.py, services.py
- [x] Pipeline — recovery & containment — src/stackowl/pipeline/recovery*.py, capability_substitution.py, critical_failure.py, step_error.py, giveup_floor.py, overclaim_gate.py
- [x] Pipeline — acceptance & verification — src/stackowl/pipeline/acceptance.py, acceptance_llm.py, turn_persist.py, persistence.py, applied_lessons.py, lesson_context.py
- [x] Providers — LLM routing & gateway — src/stackowl/providers/llm_gateway.py, router-related, registry.py, _react.py, _resilient_round.py, react_callback.py, rate_limiter.py, resume_validation.py
- [x] Providers — backend adapters — src/stackowl/providers/{anthropic,openai,gemini,mock}_provider.py, base.py, model_window.py, cost_tracker*.py, _truncate.py, _wrapup.py
- [x] Tools — execution framework — src/stackowl/tools/base.py, registry.py, verification.py, consent*.py, child_exclusion.py
- [x] Tools — tool implementations — src/stackowl/tools/*.py (individual action tools: shell, file, web, media, etc.)
- [x] Runtime — gateway/core split — src/stackowl/runtime/*.py (core_process, gateway_process, drain, code_watcher, links, turn_client, supervisor)
- [x] Objectives — goal persistence — src/stackowl/objectives/*.py (driver, store, model, decomposer)
- [x] Learning — outcome mining & lessons — src/stackowl/learning/*.py (tool_outcome_miner, lesson, heuristic_*, lessons_*)
- [x] Memory — read/write & reflection — src/stackowl/memory/*.py (recall_ranker, reflection*, fact_extractor, outcome_store, pruner, conversation_miner)
- [x] Owls — DNA, evolution & routing — src/stackowl/owls/*.py (evolution, dna*, router, revalidator, guards, delegation)
- [x] Parliament — multi-owl debate — src/stackowl/parliament/*.py (orchestrator, round_runner, cross_examination, synthesizer, convergence)
- [x] Scheduler — proactive jobs — src/stackowl/scheduler/*.py (scheduler, assembly, job, mutations)
- [x] Channels — adapters & delivery — src/stackowl/channels/*.py (cli_adapter, socket_adapter, splitter, registry, base)
- [x] Gateway — turn routing & clarify — src/stackowl/gateway/*.py (turn_router, clarify_pump, inflight_router, scanner, parked_intakes)
- [x] Interaction — clarify/consent/instincts — src/stackowl/interaction/*.py
- [x] Supervisor — turn progress tracking — src/stackowl/supervisor/*.py
- [x] Notifications & brief — proactive surface — src/stackowl/notifications/*.py, src/stackowl/brief/*.py
- [x] Messaging — heartbeat & proactive msgs — src/stackowl/messaging/*.py
- [x] CLI & commands — src/stackowl/cli/*.py, src/stackowl/commands/*.py
- [x] MCP & integrations — src/stackowl/mcp/*.py, src/stackowl/integrations/*.py, src/stackowl/web_search/*.py, src/stackowl/vision/*.py
- [x] Setup, health & service — src/stackowl/setup/*.py, src/stackowl/health/*.py, src/stackowl/service/*.py, src/stackowl/startup/*.py
