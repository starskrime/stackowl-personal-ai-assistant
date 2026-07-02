# Addendum — Technical Implementation Plan + Audit Findings

Companion to `prd.md`. Holds the technical-how the PRD deliberately omits: exact files, line references (as of commit a72c40a7, 2026-07-01), constants, commit sequencing, and the raw audit findings. FR numbers reference the PRD.

---

## Part A — Audit findings (the "issues in design/code/implementation")

### A.1 Hot-path audit (live turn pipeline)

One Telegram message crosses ~19 hops before a token is produced:
adapter (`channels/telegram/adapter.py:940` → `:307`) → `startup/orchestrator.py:2024 _handle_ingress` → `gateway/scanner.py:303` → `gateway/clarify_pump.py:94` → `orchestrator.py:1810 _intake` → `orchestrator.py:1488 _dispatch_turn` → `gateway/turn_registry.py` → `runtime/turn_client.py:41` → `pipeline/backends/asyncio_backend.py:171` → 8 steps (`pipeline/registry.py:34`) → gate cascade → `pipeline/steps/deliver.py`.

- **3–4 LLM calls per trivial turn**: `SecretaryRouter.route` (`owls/router.py:281`, every turn), `FeedbackClassifier.classify` (`pipeline/steps/feedback.py:67`, every turn ≥2), primary answer, persistence give-up judge (`pipeline/steps/execute.py:97`, +fallback judge tier on judge failure).
- **≥9 overlapping honesty components** all run in sequence: persistence judge, structural veto (`pipeline/supervisor.py:36`), never-empty floor (`synthesize_floor`, execute.py:1877/1925/1970), `giveup_floor.py:281`, `overclaim_gate.py:170`, `grounding_gate.py:250`, `critical_failure.py:333`, `persistence_handoff.py:212`, `AcceptanceAuthority` (`acceptance_authority.py` — its own docstring admits the concern was "re-decided by ≥6 disjoint proxies"; it became a 9th instead of replacing them; only ~2 tools declare `post_condition()`), `AcceptanceChecker`/`acceptance_llm.py` (flag-OFF).
- **Duplicated backend**: `langgraph_backend.py` mirrors the whole loop incl. its own copy of the gate cascade (`_deliver_with_surfacing`, lines 47–84).
- **Dead/forwarding-only**: `steps/parliament_step.py` pure pass-through stub (11 lines); `assemble.py:49` re-runs `select_tool_provider` only for a telemetry field; `deliver.py:87` notes `is_final` on content chunks is dead.
- **Same-session follow-ups** pay an extra LLM hop (`TurnRouter.route` STEER/STOP/NEW classifier).
- Steps run strictly sequentially; no parallelism on a normal turn.

### A.2 Learning/proactivity audit

Historical "registered ≠ reachable" disease is CURED — everything is wired. New failure mode: over-hedged into invisibility.

- **Memory**: 4 stores (LanceDB vectors, Kuzu graph, SQLite FTS/SoT/staged/outcomes/reflections/jobs, LanceDB `lessons_index`). Closed loop: `turn_persist.py:117` → dream_worker promote (`memory/assembly.py:263-274`) → recall in `classify.py:573-652` → prompt `assemble.py:220`. **Smell**: reflections double-surfaced per turn (`classify.py:599` SQLite + `classify.py:652` lessons_index; `:453` filters skills but not reflections).
- **6 learning loops**: reflection_writer (15m, `scheduler/assembly.py:478`), tool_outcome_miner (daily, `:547`), feedback step (live), DNA evolution (nightly 02:00, `:434`), skill_synthesizer (daily, `:541`), critic_scorer (10m, `:472`). Three funnel into the same lessons_index.
- **DNA evolution inert by construction**: ±0.1 clamp (`owls/evolution.py:49-50,110`) + governor `bound_dna` (`:310`) + hysteresis latch HIGH_ENTER=0.70/HIGH_EXIT=0.60 (`owls/directive_latch.py:12-13`) from 0.5 neutral start ⇒ a trait needs +0.2 cumulative drift (2+ perfect nights) before ANY directive fires (`owls/dna_injector.py:83-113`). Injection itself works (`assemble.py:80`).
- **Preference learning capped**: `feedback.py:110-116` no-ops tone/length/content; only 3 output_style format fields learned (`feedback.py:280` `OUTPUT_STYLE_KEY`). Surfacing half (`classify._gather_preferences`, `:596`) already exists and is starving.
- **Job slippage**: heavy jobs defer to live turns (`orchestrator.py:1182-1184`), bounded by `_MAX_DEFER_SEC = 900` (`scheduler/scheduler.py:35`) — chronic 15-min slippage on an active box, not total starvation.
- **Proactivity Telegram-coupled**: `scheduler/assembly.py:392-403` — check_in never seeded and morning_brief no-ops without a resolvable Telegram owner (`notifications/recipient.resolve_owner_addresses`). Delivery seam itself is solid: `ProactiveDeliverer` (`notifications/deliverer.py:143,182,199,262`) with retry + reroute + `UndeliveredOutbox` + exactly-once `DeliveryLedger` (`scheduler/assembly.py:146`); NACK/batched paths drain via notification_digest (5m).
- **Skill loop**: mostly closed already — LS7 `_update_skill_success_rates` (`asyncio_backend.py:43-89`, called at `:474`) nudges `success_rate` (EWMA α=0.3) per applied skill (application seam = `skill_view` tool calls). Missing: `increment_n_executions` caller — refine/deprecate phases still unreachable.
- **Instincts**: never built; zero references in src.

### A.3 Structural audit

- 744 files / ~142k lines / 40+ top-level packages; tests 1037 files / ~173k lines (148 with skips, 162 with sleeps, only 5 conftest.py).
- **Concept smearing**: messaging across `events/` + `messaging/` + `notifications/` + `ipc/` (notifications/event_bridge.py literally bridges two of them); supervision across `supervisor/` + `runtime/` + `pipeline/supervisor.py` + `process/`; observability across `audit/` + `health/` + `infra/`; ≥3 ledgers (`infra/decision_ledger`, `infra/tool_outcome_ledger`, `pipeline/decision_store` + DB `side_effect_ledger`).
- **Config**: `config/settings.py` 946 lines / 117 fields / 47 bool flags across 14 config modules; `cloud_enabled` defined 3× (lines ~201, 289, 341); only ~4 real behavior gates found by grep.
- **DB**: 74 migrations, ~57 tables; 4 stale shadow tables (`skills_new`, `staged_facts_new`, `tool_heuristics_new`, `user_preferences_new`).
- **Size hotspots**: `startup/orchestrator.py` 3375, `pipeline/steps/execute.py` 2344, `cli/app.py` 1316, `config/settings.py` 946, `scheduler/assembly.py` 894, `interaction/clarify_gateway.py` 885, `gateway/turn_registry.py` 814.
- **Abstraction smells**: 14 registries (several wrap ≤4 items), `providers/registry_accessors.py` accessor-over-registry, `owls/base.py OwlSource` ABC with 1 impl, 123 files referencing "fallback".
- Peripheral for single-user: `tenancy/`, `authz/`, `parliament/` (13 files), `export/`, `webhooks/`, `ipc/` (custom framing).

### A.4 Non-issues (verified during design pass)

- `_drain_next` holds session lock across clarify LLM await (`orchestrator.py:1614-1620`) — documented §4.3 race fix, same-session only. By design.
- Learning starvation bounded (`_MAX_DEFER_SEC=900`).

---

## Part B — Implementation plan (per FR)

### FR-1 Un-damp DNA evolution (S, 1 commit)
- `owls/evolution.py:49-50`: `_DELTA_LOWER/_DELTA_UPPER` ±0.1 → ±0.25.
- `owls/directive_latch.py:12-13`: bands 0.70/0.60 → 0.62/0.55.
- Keep governor `bound_dna` (evolution.py:310) and latch mechanism.
- Verify: `uv run pytest tests/owls/test_evolution_feedback.py tests/journeys/test_persona_evolution_journey.py tests/journeys/test_evolution_promotion_off_turn_path.py --timeout=120`; manual nightly dry-run → `[dna] injector.inject: exit — directives appended` in logs.
- Multi-night simulation harness: invoke the evolution batch handler repeatedly against synthetic `task_outcomes` rows with a consistent trait-correlated signal (dna_snapshot + quality_score seeded), asserting a directive fires within ≤3 simulated nights under the new constants.

### FR-2 Widen preference learning (M, 2 commits)
- Commit 1: `pipeline/steps/feedback.py:110-116` — on confident verdict with aspect ∈ {tone,length,content}, polarity set, referent="last": write short NL preference entry via existing `PreferenceStore` (identity-scoped owner key, cap ~20, newest-wins merge).
- Commit 2: confirm `classify._gather_preferences` (classify.py:596) renders entries in prefs_block; adjust rendering if needed.
- Verify: `uv run pytest tests/interaction/test_feedback_classifier.py tests/pipeline/ -k feedback --timeout=120`; manual "be more concise" → next-turn prefs_block.

### FR-3 De-dup lesson surfacing (S, 1 commit)
- Remove `_gather_recent_reflections` block usage at `classify.py:599`; keep `_gather_lessons` (`:652`) and `actions_block` (`:606`).
- Verify: `uv run pytest tests/pipeline/ -k classify tests/acceptance/test_learning_acceptance.py --timeout=120`.

### FR-4 Loop consolidation + skill loop (M, 2-3 commits)
- Merge `memory/critic_scorer_handler.py` into `memory/reflection_writer_handler.py`; single registration in `scheduler/assembly.py` (drop one of `:472`/`:478`).
- Add `increment_n_executions` call inside `_update_skill_success_rates` (currently `asyncio_backend.py:43` — lands in shared seam per FR-12).
- Verify: `uv run pytest tests/scheduler/test_scheduler_assembly.py tests/memory/ -k "reflection or critic" --timeout=120`.

### FR-5 Reflection no-defer (XS, 1 commit)
- Set `defer_under_load = False` on reflection_writer handler (see `scheduler/scheduler.py:35` `_MAX_DEFER_SEC`); dream_worker/kuzu_sync unchanged.
- Verify: `uv run pytest tests/scheduler/ --timeout=120`.

### FR-6/FR-7 Proactivity (M-L, 2-3 commits)
- Commit 1: channel-agnostic owner identity in `config/settings.py`; deterministic resolution in `notifications/recipient.py` (replace allowlist inference).
- Commit 2: `scheduler/assembly.py:392-403` — always seed check_in/morning_brief targeting owner; on delivery-time resolution failure route to `UndeliveredOutbox` (deliverer.py:182 path). Preserve DeliveryLedger exactly-once.
- Verify: `uv run pytest tests/scheduler/test_scheduler_assembly.py tests/notifications/ tests/scheduler/test_scheduler_idempotency.py --timeout=120`.

### FR-8 Feedback pre-filter (XS, 1 commit)
- `pipeline/steps/feedback.py:93` — skip LLM classify when message ≥ 200 chars (PRD constant; tune only with owner sign-off). Comment the ceiling (verbose reactions missed — accepted).

### FR-9 Sticky routing (M, 1 commit)
- `pipeline/steps/triage.py` + `owls/router.py:281`: mechanical bypass rule (per PRD FR-9): sticky iff same-session previous turn resolved to an owl within 30 min AND scanner found no direct address AND message < 200 chars. Any condition false → LLM route as today. No new-topic detection is attempted — the length ceiling is the heuristic; risk accepted, guarded by CM-2. `intent_class` MUST carry forward (gates classify's heavy blocks).
- Verify router/triage suites + journeys; watch CM-2.

### FR-10 Conditional give-up judge (S-M, 1 commit)
- `pipeline/steps/execute.py:97 build_persistence_check`: gate LLM judge on (≥1 failed tool) OR (0 tool calls AND refusal-shaped draft) OR (empty draft). `apply_structural_veto` (pipeline/supervisor.py:36) + `synthesize_floor` stay unconditional.
- Verify: `uv run pytest tests/pipeline/ tests/journeys/test_no_dressed_up_giveup_journey.py --timeout=180`.

### FR-11/FR-12 Delivery gate + shared seam (L, phased; also IS FR-13's parity work)
- Today the cascade is duplicated: `asyncio_backend.py:183-217` inline and `langgraph_backend.py:47-84 _deliver_with_surfacing`.
- Phase A (byte-identical, 2-3 commits):
  - New `pipeline/delivery_gate.py`: compute structural facts ONCE (tool failure/success tallies, `consequential_failures` − `recovered_consequential`, retrieval ledger) then one precedence ladder: applied_lessons → recovery → persistence_handoff → giveup floor → overclaim → grounding → critical_failure → command_hint.
  - New `pipeline/backends/shared.py`: delivery_gate call, `persist_turn` ordering (F088 — after floors, inside ledger ContextVar binding), `_verify_turn_acceptance`, `_capture_outcome`, `_update_skill_success_rates` (moved from asyncio_backend).
  - Re-home cross-imports: `overclaim_gate` imports from `giveup_floor`/`grounding_gate`; `turn_persist.py`, `providers/*_provider.py`, `objectives/driver.py` import gate helpers.
  - Move gate tests onto merged module; delete the 5 old files last.
- Phase B (ongoing): per-tool `post_condition()` migration → AcceptanceAuthority real.
- Keep: AcceptanceAuthority, structural veto, never-empty floor, AcceptanceChecker (feeds outcome capture → learning corpus).
- Verify per commit: `uv run pytest tests/pipeline/ tests/journeys/test_no_dressed_up_giveup_journey.py tests/journeys/test_overclaim_gate_journey.py tests/journeys/test_budget_cap_overclaim_floor_journey.py tests/acceptance/test_trust_acceptance.py --timeout=180`.

### FR-13/14/15 LangGraph promotion (M, 3 commits + soak)
- Verified parity gaps (direct read): langgraph passes `acceptance=None` always (no `_verify_turn_acceptance`); `step_durations` empty (noted at langgraph_backend.py:258-262); imports private `_capture_outcome` from asyncio sibling (`:263`).
- Commit 1: langgraph `_deliver_with_surfacing` → call FR-12 shared seam; add per-node timing in `_wrap_step` (`:321`) accumulating into state → `step_durations`.
- Commit 2: flip default `orchestrator.backend` "asyncio" → "langgraph" (`config/settings.py:378`). Full gateway restart (gateway durable=no — landmine). Live Telegram smoke.
- Commit 3 (after soak exit): delete `asyncio_backend.py`, simplify `pipeline/backends/factory.py`. Soak exit = 7 calendar days where both queries return zero rows over `logs/stackowl-*.log`: `jq 'select(.msg | startswith("[langgraph_backend] run: graph invocation failed"))'` and `jq 'select(.msg | contains("checkpointer: sqlite init failed"))'`. Any hit resets the clock.
- Notes: checkpointer = AsyncSqliteSaver at StackowlHome.db_path(), thread per `session::task_id` (`:177-181`) — durable-turn resume substrate. MemorySaver fallback exists. recursion_limit=50.
- Verify: `uv run pytest tests/pipeline/ -k backend tests/journeys/ -x --timeout=300` (targeted only).

### FR-16..FR-20 Tier 3 details
- FR-16: dedupe `cloud_enabled` (settings.py ~201/289/341); then per-flag: grep callers → delete flag + dead branch; real-feature flags → batched consent ask. Verify `tests/config/` + mypy.
- FR-17: one idempotent migration `DROP TABLE IF EXISTS` for `skills_new`, `staged_facts_new`, `tool_heuristics_new`, `user_preferences_new` (separate statements). Verify `tests/db/ tests/migration/`.
- FR-18: fold `pipeline/decision_store.py` into `infra/decision_ledger` (both backends' persist blocks call TurnDecisionStore — after FR-12 only the shared seam does).
- FR-19 [consent]: events+messaging+notifications/event_bridge → one package; supervisor/+process/ → runtime/; ipc/ vs stdlib asyncio streams. Park list: tenancy/, authz/, export/, webhooks/, parliament/.
- FR-20: inline registries wrapping ≤4 static items; keep TurnRegistry / scheduler HandlerRegistry / ProviderRegistry.

### FR-21/FR-22
- FR-21: remove `assemble.py:49` duplicate `select_tool_provider` call; remove dead `is_final` comment in `deliver.py:87`. parliament_step STAYS.
- FR-22: remove/clarify instincts in docs. CAUTION: root CLAUDE.md's "Instincts" bullet + `src/instincts/engine.ts` table row describe the ARCHIVED v1 TS app (under `old/`) — re-scope/clarify that section, don't blindly delete.

---

## Part C — Global execution rules for the implementing session

- Subagent-driven development (implementer → QA → dev review → smoke → commit); main thread orchestrates.
- Never full pytest (box hangs) — always targeted paths + `--timeout`.
- `uv run ruff check src/` + `uv run mypy src/` per commit.
- Small commits, merge to main + push when green batch complete.
- Gateway-side behavior changes need FULL gateway restart to go live (gateway not durable).
- Live smoke over Telegram for behavior deltas (FR-1, FR-2, FR-8/9/10, FR-14).
- Models run remote (192.168.1.81 / 172.30.60.31) — never pull models locally on the Jetson.
- Rejected-alternative record: original design proposed deleting LangGraph and keeping asyncio; owner reversed it (see `.decision-log.md` #1). The delivery-gate consolidation was deliberately designed backend-agnostic so the reversal cost nothing.
