# StackOwl Platform Audit — Progress Tracker

**Goal:** Reverse-engineer every pipeline element, identify architectural problems, and define improvements toward a fully autonomous, self-healing, continuously learning assistant.

**Audit started:** 2026-04-28  
**Flow map:** `docs/platform-audit/platform-flow-map.html`

---

## Pipeline Order (from flow map)

| # | Element | Status | Session |
|---|---------|--------|---------|
| 1 | **Channels** (CLI, Telegram, Slack, Voice, Web) | 🔧 reviewed — improvements committed | 2026-04-28 |
| 2 | GatewayMessage creation | 🔧 reviewed — improvements committed | 2026-04-28 |
| 3 | SessionManager (load / create) | 🔧 reviewed — improvements committed | 2026-04-29 |
| 4 | RoutingCoordinator (owl selection + pin) | 🔧 reviewed — improvements committed | 2026-04-29 |
| 5 | ContextBuilder (memory + pellets + skills) | 🔧 reviewed — improvements committed | 2026-04-30 |
| 6 | OwlEngine — ReAct loop | 🔧 reviewed — improvements committed | 2026-05-01 |
| 7 | Tool layer (registry, execution, permissions) | ✅ Phase 7a + 7b + 7c + 7d shipped (3496 tests). 23-task continuation plan complete 2026-05-03: schema v23/v24, ToolTracker→SQLite, multi-channel narration, FallbackSequencer DB-backed, ToolGraph (Dijkstra), PersonalizedRouter (KNN), SelfEvolver+ShadowRunner, FactEnvelope+retraction, frontmost-aware live_browser (Safari JXA + Chrome CDP w/ auto-bootstrap). | 2026-05-03 |
| 8 | PostProcessor (save, learn, evolve, queue) | 🔧 reviewed — improvements committed | 2026-05-02 |
| 9 | **Clarification & Intent Detection** | ✅ implemented | 2026-05-02 |
| 10 | Parliament (multi-owl debate) | ✅ implemented — parallel Round 1, DiversityFilter, sparse Round 2, ContextPipeline/GoalVerifier/DNA wiring. 31 new tests, 793 total passing. | 2026-05-03 |
| 11 | Pellet system (generate, store, retrieve, dedup) | ✅ implemented — quality flywheel: IntelligenceRouter routing throughout, recordOutcome + searchWithGraphScored re-ranking, schema v21, gateway hooks 4+5, updatePelletGeneratorDNA. 38 new tests, 831 total passing. | 2026-05-03 |
| 12 | Heartbeat (proactive messages, job queue) | ✅ implemented — schema v22, single proactive_jobs DB, DeliveryVerifier (ADVANCES/NEUTRAL/NOISE), retry escalation, recordEngagement wiring, AutonomousPlanner learned priorities, goal_progress_update jobs, consolidation.ts deleted. 866 tests passing. | 2026-05-03 |
| 13 | Learning Engine (reactive + proactive self-study) | ✅ shipped — −3 file delta (deleted self-study.ts, approach-library.ts, mistake-detector.ts). ProactiveContext + runProactiveSession, failure critique job, IdleEngine→Orchestrator migration, OwlLearningsLayer DB-wired, style/temporal signals, sleep eviction. 1291 tests passing. Merged 2026-05-06. | 2026-05-06 |
| 14 | Evolution (DNA mutation, reflexion, APO) | ✅ shipped — −789 LOC net delta. D1: deleted 808-LOC dead code cluster (7 files). D2: wired ReflexionEngine in core.ts. D4: mid-session evolution trigger (avg_reward < −0.2, 2h cooldown, in-flight guard). D5: top-5 owl_learnings injected as RECENT LEARNINGS in evolve() prompt. D6: decayRatePerWeek 0.01→0.1, EMA β=0.7 blending on learnedPreferences + expertiseGrowth. 1319 tests passing. Merged 2026-05-06. | 2026-05-06 |
| 15 | Memory DB (SQLite facts, episodes, attempts) | ✅ shipped — Phase J complete (Tasks 30-32), 1144 tests passing | 2026-05-03 |
| 16a | **Web Browsing Honesty & Wiring** (Phase A) | ✅ shipped — merged `d59dc00` 2026-05-04. Structured `WebToolResult` envelopes, 3-tier dispatcher (http→camofox→scrapling), `<tool_attempt_summary>`, GoalVerifier envelope-driven, channel-parity narration, schema v26. 4842 tests passing. | 2026-05-04 |
| 16b | Perches (file watchers, event broadcast) — original Element 16 scope | 🔍 Phases 1–5 complete. Spec committed `664b1c9`. Plan committed `0ab2695`. 4 tasks: D1 config schema → D3 heartbeat → D2+D4 chokidar+watchPaths → D5 setSignalPool. 0 new files, net delta 0. Awaiting Boss approval before execution. | 2026-05-06 |
| 17 | Owl system (DNA, inner life, specialization) | ⬜ pending | — |
| 18 | Providers (model routing, health, cost) | ⬜ pending | — |
| 19 | Skills engine (match, inject, synthesize) | ⬜ pending | — |

---

## Element 1: Channels

### Scope
`src/gateway/adapters/cli.ts`, `telegram.ts`, `slack.ts`, `voice.ts`, `src/server/index.ts`

### Findings
- All 5 adapters implement `ChannelAdapter` interface — clean transport separation
- Telegram is 1453 lines with voice, config menu, MCP cmds, streaming, formatting all mixed together
- Streaming logic duplicated across Telegram (~250 lines), Slack (~100 lines), Voice
- ProactivePinger only wired to Telegram + Slack — CLI/Voice users get no proactive messages

### Critical Problems
1. **Telegram streaming race condition** — `done` event fires after `handle()` returns, can silently drop final message
2. **Memory leaks** — `userState` + `processedUpdates` maps grow unbounded in Telegram
3. **Slack auto-approves tool install** — `askInstall` returns `true` unconditionally
4. **Voice TTS blocks** — `execSync('say ...')` blocks readline loop during playback
5. **No auth on REST** — `/api/chat`, `/api/parliament`, `/api/broadcast` all public
6. **No shared StreamHandler** — streaming bug must be fixed in 3 places
7. **No shared MessageFormatter** — formatting rules must be updated in 4 places

### Improvements Decided
**Option B — Thin Adapter Protocol (Phase 1 implemented):**
- 9 new `src/gateway/` files: `ChannelCapabilities`, `RichContent`, `DeliveryEnvelope`, `ChannelAdapterV2` contracts; `ChannelRegistry` (presence + routing); `GatewayEventBus` (typed pub/sub); `StreamSession` (shared throttled streaming, fixes Telegram race condition); `DeliveryRouter` (retry, TTL, SQLite delivery_log); `ChannelAdapterV1Shim` (wraps all 5 existing adapters — zero regressions)
- Heartbeat proactive messages now route through `GatewayEventBus → DeliveryRouter → V1Shim → sendToUser`
- SQLite schema v11: `delivery_log` table records every outbound delivery attempt
- Phase 2 (pending): rewrite adapters one-by-one as native `ChannelAdapterV2`; Phase 3: wire Parliament/Learning/Perches through bus

### Commits
- `a443cbf` — channel architecture design spec (Option B)
- `50fa5ba` — Phase 1 implementation plan
- `1cd409d`–`5042a76` — Phase 1 implementation (12 commits on feature branch)
- `37ad88a` — merged to main + pushed

---

## Element 2: GatewayMessage creation

### Scope
All adapter call sites that construct a `GatewayMessage` literal before calling `gateway.handle()`

### Findings
- 9 inline object literals spread across 5 files (cli, telegram ×3, slack, voice, REST ×2, WebSocket)
- No text normalization: leading/trailing whitespace passed directly to LLM
- No empty-text guard: empty or whitespace-only messages hit the full ReAct loop
- No max-length cap: unbounded input could overflow context windows

### Improvements Committed
- Added `makeMessage(channelId, userId, text, sessionId?)` factory to `core.ts`
- Trims text; returns `null` for empty/whitespace; truncates at 32,000 chars with `\n[…message truncated]` marker
- Updated all 9 call sites to use factory; null guard at each site (early return / continue)
- Removed now-unused `makeMessageId` imports from cli, slack, voice, server

### Commits
- `28660a7` — `feat(gateway): add makeMessage() factory — normalize all adapter message construction`

---

---

## Element 3: SessionManager (load / create)

### Status: 🔧 Implemented + merged

### Findings
- `SessionManager` class exists and is instantiated but **never called** — core.ts has duplicate inline session management
- Messages stored in two places: JSON session files AND SQLite `messages` table
- `CrossSessionStore.extractFromSession()` never called — facts never auto-populated from conversations
- SQLite `summaries` table exists but 50-message limit silently drops messages instead of summarizing
- Fact extraction only triggered by user 👍/👎 feedback — never automatic

### Improvements Implemented
- **`SessionService`** (`src/session/service.ts`) — unified session lifecycle replacing dead `SessionManager` + inline core.ts methods
- SQLite as single source of truth; one-shot JSON→SQLite migration on startup (`src/session/migrate.ts`)
- Session message limit raised 50 → 300; summary-before-drop via `MessageCompressor` before eviction
- Greeting-reset detection: `SessionService.isGreetingPattern()` fires `endSession()` at natural conversation boundaries
- **`UserMemoryStore`** (`src/session/user-memory-store.ts`) — fastembed semantic search over `facts` table; 0.88 cosine dedup
- **`extractFactsFromConversation()`** (`src/session/fact-extractor.ts`) — LLM-based extraction at session end → `facts` table
- `userMemoryContext` injected as L2.5 layer in `context-builder.ts` (top-3 semantic hits per turn)
- Dead `src/gateway/handlers/session-manager.ts` deleted
- New `MessagesRepo.getOldestN()`, `deleteByIds()`, `deleteSession()` methods added to `src/memory/db.ts`
- `deleteSession()` added to `SessionStore` (`src/memory/store.ts`)

### Commits (feature/session-management → merged to main)
- `e572f0b`–`5e42cf8` — MessagesRepo rolling window methods + tests
- `809336c`–`94c9ca4` — UserMemoryStore + tests
- `f4b27fb` — fact-extractor + tests
- `ed5c201`–`6c60eac` — JSON→SQLite migration + tests
- `38180d7` — SessionService + rolling window tests
- `e03b3f7` — GatewayContext types
- `63ccdc5` — core.ts + context-builder.ts wiring
- final commit — delete session-manager.ts, /reset SQLite fix, logger cleanup

---

## Cross-cutting: Tiered Intelligence Router

### Status: 🔄 Design in progress (discovered during Element 3 brainstorm)

### Problem
Every platform component (Parliament, Evolution, session extraction, episodic memory, classification, synthesis, summarization) always uses the default provider — no ability to route cheap tasks to cheap models or critical tasks to powerful models.

### Design Decisions (approved)
- **Three tiers:** `high` / `mid` / `low` — each maps to `{ provider, model }` in config
- **Named task types:** `conversation`, `parliament`, `evolution`, `extraction`, `episodic`, `classification`, `synthesis`, `summarization`, `clarification`
- **Resolution order:** overrides → defaults→tier → mid fallback → defaultProvider fallback
- **New `intelligence` block** in `stackowl.config.json` — replaces `smartRouting`
- **`IntelligenceRouter`** class at `src/intelligence/router.ts` — injected via `GatewayContext`
- `ModelRouter` stays untouched (handles conversation SIMPLE/STANDARD/HEAVY heuristics)

### Design Sections Status
- ✅ Section 1: Architecture
- ✅ Section 2: Config structure
- ✅ Section 3: IntelligenceRouter class
- ✅ Section 4: TaskType registry + defaults
- ✅ Section 5: GatewayContext injection + hard break (throws on smartRouting)
- ✅ Spec written: `docs/superpowers/specs/2026-04-29-intelligence-router-design.md`
- ✅ Implementation plan: `docs/superpowers/plans/2026-04-29-intelligence-router.md`
- ✅ Implemented + merged to main (commits `809b8f4`–`00d28f2`)
- ⬜ Backlog: update `start.sh` onboarding to configure intelligence tiers interactively

---

## Element 4: RoutingCoordinator

### Status: 🔄 Design approved — implementation pending

### Scope
`src/gateway/handlers/routing-coordinator.ts`, `src/routing/secretary.ts`, `src/routing/session-state.ts`, `src/routing/llm-classifier.ts`, `src/delegation/delegation-decider.ts`, `src/delegation/sub-owl-runner.ts`, `src/gateway/core.ts`, `src/memory/db.ts`, `src/gateway/handlers/context-builder.ts`

### Findings
- `RoutingCoordinator` (186 lines) covers @mention + session-pin + SecretaryRouter — no persistent user context
- Pin stored as JSON file via `SessionStateStore` — lost on restart, no cross-channel persistence
- Routing ignores GoalGraph, EpisodicMemory, FactStore, Kuzu — uses only keyword + LLM classification
- `DelegationDecider` field on `OwlGateway` (line 166) assigned but never called — dead
- `buildClassifyFn` / `SessionStateStore` / `DelegationDecider` imports all removable from `core.ts`
- No task ownership, no background jobs, no relationship context, no status transparency

### Design Decisions (approved)
- **`OwlBrain`** (`src/routing/owl-brain.ts`) — central coordinator replacing `RoutingCoordinator` direct usage
- **`UserProfileService`** (`src/routing/user-profile-service.ts`) — signal aggregator (not data store) over GoalGraph, EpisodicMemory, FactStore, UserMemoryStore; 200ms timeout per source
- **3 new SQLite tables** (schema v12): `user_profiles` (pin + routing history), `owl_tasks` (task ownership), `owl_jobs` (background queue)
- **`TaskOwnershipManager`**: commitment detection regex + task CRUD
- **`BackgroundJobRunner`**: 60s poll, one-at-a-time execution, fires `job:complete` event via EventBus
- **`RelationshipContext`**: reads routing_history + FactStore → `<user_relationship>` prompt block
- **`RoutingStatusReporter`**: status query detection + formatted output for `/status` and `/why`
- Dead code cleanup: `session-state.ts` and `llm-classifier.ts` deleted; `DelegationDecider` / `buildClassifyFn` removed from `core.ts`

### Commits
- `9b0cac1` — design spec (`docs/superpowers/specs/2026-04-29-routing-coordinator-design.md`)
- `9c26e69` — implementation plan (`docs/superpowers/plans/2026-04-29-routing-coordinator.md`)
- `21906ec`–`7f2e66f` — Phase 1+2 implementation (23 commits on feature branch, merged to main)

---

## Element 5: ContextBuilder → ContextPipeline

### Scope
`src/gateway/handlers/context-builder.ts` (762 lines replaced by ~140-line adapter)
`src/context/` (new module: 22 source files)

### Findings
- 762-line god-method with 28 inline signal blocks, executed sequentially
- Sequential execution: ~4,200ms wall time per cold request
- Triple memory duplication (factContext + memoryBus + memoryFirstContext)
- No token budget — context silently overflows LLM window
- InnerMonologue generated but discarded every turn
- No user persona synthesis — owl knows fragments, not the person
- Zero test coverage on context assembly logic

### Improvements Implemented
- **ContextPipeline** — typed registry of 29 ContextLayer instances executed via DAG batches
- **DAGPlanner** — Kahn's topological sort; layers declare `produces[]`/`dependsOn[]`; parallel batches via `Promise.all()`
- **BudgetController** — per-layer token cap + configurable global ceiling (default 8,000 tokens); sentence-boundary trim
- **ContextCache** — LRU (200 entries), per-layer TTL, event-driven invalidation, O(1) `userIndex` for user-scoped invalidation
- **LayerCircuitBreaker** — CLOSED→OPEN→HALF_OPEN→CLOSED; trips at errorRate>40% OR p95>1800ms
- **ContextQualityScore** — composite 0–1 score; emits `context:quality_degraded` on EventBus when <0.6
- **InnerMonologueLayer** — owl's last-turn thoughts persisted in `ConversationDigest`; injected at priority 15
- **UserPersonaSynthesizer** — LLM synthesis of user character card; 30min SQLite cache; stale-while-revalidate
- **UnifiedMemoryRetriever** — parallel query across FactStore + EpisodicMemory + MemoryBus; cosine dedup + tier-labeled XML
- **ContextDependencies interface** — `src/context/` never imports `GatewayContext`; clean module boundary
- **Schema v13** — `user_personas` table + `idx_pellets_tag`
- **EventBus cache invalidation** — `pellet:written`, `persona:refreshed`, `learning:recorded`, `session:ended` invalidate stale cache entries
- **Deleted** `src/memory/context-builder.ts` (`MemoryFirstContextBuilder` superseded)

### Commits (feature/context-pipeline → merged to main)
- `85af96b`–`b578fcf` — 30+ commits implementing all 21 plan tasks
- `caa6381` — merge commit to main

### Design
- Spec: `docs/superpowers/specs/2026-04-30-context-pipeline-design.md`
- Plan: `docs/superpowers/plans/2026-04-30-context-pipeline.md`

---

## Element 6: OwlEngine v2 — ReAct Loop (Element 6a Gateway Wiring)

### Scope
`src/engine/orchestrator.ts`, `src/engine/improvement-scheduler.ts`, `src/engine/outcome-journal.ts`,
`src/gateway/types.ts`, `src/gateway/core.ts`

### Improvements Implemented (Tasks 15–16, 2026-05-01)

**Task 15 — GatewayContext extended**
- Added `orchestrator?: OwlOrchestrator` and `improvementScheduler?: ImprovementScheduler` to `GatewayContext` (src/gateway/types.ts)

**Task 16 — Gateway wiring**
- Imported `OwlOrchestratorV2`, `ImprovementScheduler`, `OutcomeJournalV2` in `src/gateway/core.ts`
- Added `owlOrchestratorV2` and `improvementScheduler` private fields to `OwlGateway`
- `ImprovementScheduler.start()` called at boot (after `ctx.db` guaranteed available) — runs journal review every 15min + approach pruning every 1h, zero LLM calls
- `OwlOrchestrator` initialized and exposed on `ctx.orchestrator`; scheduler exposed on `ctx.improvementScheduler`
- 2 integration tests added (`__tests__/gateway-orchestrator.test.ts`)

### Test counts
- Before: 506 tests
- After: 508 tests (2 new integration tests)

### Commits
- `232233b` — `feat(gateway): add orchestrator + improvementScheduler to GatewayContext`
- `4f3e487` — `feat(gateway): wire OwlOrchestrator as primary path, ImprovementScheduler bootstrapped at startup`

---

## Element 7: Tool Layer — Tool Cortex

### Status: 🔧 Phase 7a complete — all 14 tasks implemented + integration tests passing | 2026-05-02

### Scope
`src/tools/` (all tool files), `src/tools/registry.ts`, `src/tools/mcp/`, `src/tools/cortex/` (new), `src/gateway/event-bus.ts`, `src/gateway/narration-formatter.ts` (new), `src/engine/orchestrator.ts`, `src/engine/improvement-scheduler.ts`, `src/memory/db.ts` (schema v17/v18)

### Findings
- ~65 tools registered; LLM sees full catalog every turn — 5 web tools overlap, 5 memory tools overlap, 15 macOS tools consume 3KB of context budget
- No post-execution critique hook — LLM is sole arbiter of whether tool result advanced the goal
- FallbackSequencer is in-memory only — learning evaporates on restart
- ToolTracker is JSON-file, discards error reasons, not queryable
- Live browser control (Safari/Chrome on user's screen) broken — CDP only works if user pre-launches Chrome with debug flag; Safari has no driver at all
- `/mcp` command in Telegram lacks `add/edit/remove`; CLI has zero `/mcp`; mutations don't persist across restart
- No tool scaffolding — adding a tool requires manual registry wiring

### Architecture Decisions (approved 2026-05-02)

**Platform:** Cross-platform (Windows/macOS/Linux). Every tool declares `platforms: NodeJS.Platform[]`. Enforced by `ToolRegistry.execute()`.

**Four phases (7a ships first, 7d parallel, 7b/7c gated):**

- **7a — Verification & Narration** (Week 1–2): GSN (EventBus tool:* events → real-time narration in all channels), GAV (goal-anchored verifier using cheap-tier LLM — different model from main to avoid correlated blindspots), tool catalog cleanup (web 5→1, memory 5→1, native 15→4)
- **7d — Quality & Coverage** (Week 3–5, parallel track): `live_browser` tool (Playwright CDP, all OS), MCP full CRUD + marketplace (static catalog ~40 servers), tool quality pass (30 tools get ExecutionPolicy + structured errors + capability tags), 5 new tools (vision/document/sandbox/db_query/schedule — full advanced implementations), tool scaffolder (`npm run tool:create`)
- **7b — Memory-Driven Routing** (Month 2, gated): CWTG (cost-weighted tool graph, Dijkstra LLM-free recovery, persisted in SQLite), PTR (K-NN over own trajectory history, inject as ToolPriorLayer)
- **7c — Self-Evolution** (Month 3, gated): SET (workspace model — evolved tools land in `workspace/tools/*.js`, never overwrite system tools; 40-success promotion threshold; shadow execution + auto-rollback), FPC (fact provenance chain with retroactive retraction)

### Key Design Decisions
- SET writes to `workspace/tools/` only — system tools never modified
- Workspace tool promotion: 40 successful executions → becomes primary route
- Shadow mode: both system + workspace run in parallel before promotion
- MCP persistence: every `/mcp` mutation calls `saveConfig()` — survives restart
- Secrets in MCP/DB config: stored in Credentials vault, config holds references not values
- GAV verifier must be different model tier than main LLM (correlated blindspot prevention)
- Live browser: Playwright CDP only — no OS-specific drivers in `live_browser` tool

### Schema migrations
- v17 (7a): `trajectory_turns` + 3 columns; `workspace_tools` table
- v18 (7b): `tool_edges` table; `tool_executions` table (replaces JSON ToolTracker)

### Spec
- `docs/superpowers/specs/2026-05-02-tool-cortex-design.md`

### Plans written
- `docs/superpowers/plans/2026-05-02-tool-cortex-7a.md` — GSN + GAV + catalog cleanup (9 tasks)
- `docs/superpowers/plans/2026-05-02-tool-cortex-7b.md` — CWTG + PTR (5 tasks)
- `docs/superpowers/plans/2026-05-02-tool-cortex-7c.md` — SET + FPC (6 tasks)
- `docs/superpowers/plans/2026-05-02-tool-cortex-7d.md` — MCP CRUD + 5 new tools + quality framework (12 tasks)

### Commits
- `02762fc` — spec written (1067 lines, 16 sections)

### Phase 7a Implementation (2026-05-02) — All 14 tasks complete

**Tasks completed:**
1. `ToolDefinition` extended with `deprecated`, `platforms`, `capabilities`, `executionPolicy` fields
2. `GatewayEventBus` extended with 6 `tool:*` events (`tool:start`, `tool:result`, `tool:retry`, `tool:fallback`, `tool:goal_advance`, `tool:goal_blocked`)
3. `NarrationFormatter` (`src/gateway/narration-formatter.ts`) — pure event→string function; routes to human-readable narration per tool type
4. `ToolRegistry` extended — platform guard, event emission, deprecated filter in `getAllDefinitions()`, `setEventBus()`, `setGoalVerifier()`
5. CLI narration via `wireToolNarration()` in CLI adapter
6. Schema v16 — `trajectory_turns` + `workspace_tools` tables in `src/memory/db.ts`
7. `GoalVerifier` (`src/tools/goal-verifier.ts`) — cheap-tier post-execution verification; `ADVANCES`/`PARTIAL`/`BLOCKED`/`NEUTRAL` verdicts
8. `TurnRequest` + `EngineContext` extended with `activeSubGoal` + `userMessage` propagation
9. GAV hook in `ToolRegistry.execute()` — emits `tool:goal_advance`/`tool:goal_blocked`; wraps result with `<tool_result_warning>` for BLOCKED/PARTIAL
10. Unified `web` tool (`src/tools/web-unified.ts`) — replaces 5 web tools; `action: search|fetch|interact`
11. Unified `memory` tool (`src/tools/memory-unified.ts`) — replaces 5 memory tools; `action: search|store|get`
12. macOS native tool grouping — `macos_comms` + `macos_system` unified tools
13. Superseded tools marked `deprecated: true`; unified tools registered in `src/index.ts`
14. Integration tests (`__tests__/integration/tool-cortex-7a.test.ts`) — 7 integration tests covering narration, deprecated filter, GAV end-to-end, unified tool capabilities

**Test counts:**
- Before Phase 7a: 508 tests
- After Phase 7a: 585 tests (+77 new tests across all 14 tasks)

### Phase 7d Implementation (2026-05-02) — All 12 tasks complete

**Tasks completed:**
1. `MCPManager.addServer()` — atomic connect-then-persist with `saveConfig()`
2. `MCPManager.removeServer()` + `MCPManager.updateServer()` — snapshot/restore rollback on reconnect failure
3. `McpServerConfig` extended: `enabled?`, `description?`, `installedAt?` fields in `src/config/loader.ts`
4. `McpCommandRouter` (`src/gateway/commands/mcp-router.ts`) — channel-agnostic static dispatcher for 9 MCP verbs (list, status, add, install, remove, enable, disable, tools, reconnect); `disable` calls `mcpManager.disconnect()` directly (NOT `updateServer`)
5. CLI `/mcp` command in `src/cli/commands.ts` via `McpCommandRouter.dispatch()`
6. Telegram `/mcp` refactored to single `McpCommandRouter.dispatch()` call (replaced 130-line switch block)
7. `enabled !== false` filter at `connectAll` call site in `src/index.ts`
8. `toolError` / `toolSuccess` envelope helpers (`src/tools/tool-error.ts`)
9. `VisionTool` (`src/tools/vision.ts`) — multimodal image understanding via `IntelligenceRouter`; capabilities `["vision", "multimodal"]`
10. `DocumentTool` (`src/tools/document.ts`) — unified parser for PDF/DOCX/MD/TXT; actions `parse|extract_tables|metadata`
11. `CodeSandboxTool` (`src/tools/code-sandbox.ts`) — Python/JS subprocess sandbox with SIGKILL timeout; name `"sandbox"`
12. `DbQueryTool` (`src/tools/db-query.ts`) — SQLite client via `better-sqlite3` dynamic import; `{ readonly: true, fileMustExist: true }`
13. `ScheduleTool` (`src/tools/schedule.ts`) — in-process job store; supports "in N minutes/hours/days/seconds" + ISO 8601; 4 actions
14. `scripts/create-tool.ts` scaffolder + `npm run tool:create` script
15. All 5 new tools registered in `src/index.ts`
16. Integration smoke tests (`__tests__/integration/tool-cortex-7d.test.ts`)

**Merge commit:** `6df5d3c` — feat: merge Tool Cortex Phase 7d — MCP CRUD, 5 new tools, quality framework

**Test counts:**
- Before Phase 7d: 585 tests
- After Phase 7d: 633 tests (+48 new tests)

**Phase 7b/7c status:** Gated on production data. Plans are written; implementation deferred until data justifies it.

### Phase 7b/7c/7d Continuation (2026-05-03) — All 23 tasks complete

The user opted to ship 7b + 7c inline (without measurement gates) plus the live-browser sub-track. All 23 tasks (T1–T23) committed on `feature/element-7-cortex-t2-t22`.

**Cortex (T1–T17):**
1. Schema v23 — `tool_executions` + `tool_edges` tables with indexes
2. ToolTracker JSON → SQLite migration (preserves error reasons)
3. Telegram adapter narration subscription
4. Slack adapter narration subscription
5. FallbackSequencer DB-backed (replaces in-memory `learnedSequences`)
6. MCP tool execution wrapped through `ToolRegistry.execute()` lifecycle
7. Top-30 tools backfilled with `capabilities[]` + `executionPolicy`
8. `ToolGraph` (Dijkstra/single-hop replan over capability-tagged edges)
9. `EdgeAccumulator` — writes tool→tool transitions to `tool_edges` with EWMA
10. `ToolGraph` wired into registry's `BLOCKED` path for LLM-free recovery
11. `PersonalizedRouter` — KNN over user trajectory history (`UserMemoryStore`)
12. `ToolPriorLayer` for `ContextPipeline` (priority 8)
13. `SelfEvolver` scaffolding + `CRITICAL_TOOLS` exclusion list
14. `ShadowRunner` — 100-call gated promotion, ≥5pp improvement threshold
15. `SelfEvolver.runOnce()` + weekly job in `ImprovementScheduler` (HITL-gated)
16. `FactEnvelopeStore` — in-memory provenance keyed by (sessionId, turnIndex)
17. `fact:retracted` event + `ContextPipeline.removeShortTermLayer()` + `FactRetractor`

**Live browser (T18–T22):**
18. `detectFrontmostBrowser()` (osascript via System Events) — returns "safari" | "chrome" | null
19. `SafariDriver` — JXA wrapper for `Application('Safari')` + `do JavaScript`
20. `ChromeDriver` + `PuppeteerChromeBackend` — CDP wrapper with active-page tracking
21. Chrome auto-bootstrap — detect debug port → relaunch with `--restore-last-session`
22. Unified `live_browser` tool — frontmost-aware action dispatch (single tool, 12 actions)

**Integration (T23):**
23. `live_browser` registered in `src/index.ts` with full production wiring (frontmost detector + Safari/Chrome drivers + Chrome bootstrap → BrowserBridge.connect)

**Schema migrations:** v23 (T1), v24 (T14 — `tool_evolution_runs`)

**Test counts:**
- Before T1: 866 tests
- After T23: 3496 tests passing across 384 files

**Branch:** `feature/element-7-cortex-t2-t22` (ready for merge)

#### ⏰ Phase 7b Readiness Gate — CHECK DATE: 2026-05-09

After ~1 week of real usage, run this query against `~/.stackowl/stackowl.db`:

```sql
SELECT
  verification_result,
  COUNT(*) as count,
  ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 1) as pct
FROM trajectory_turns
WHERE verification_result IS NOT NULL
  AND created_at > datetime('now', '-7 days')
GROUP BY verification_result;
```

- Sample count < 200 → insufficient data, wait longer
- BLOCKED % > 5 → start Phase 7b (CWTG + PTR)
- BLOCKED % < 2 → deprioritize Phase 7b

#### ⏰ Phase 7c Readiness Gate — CHECK DATE: ~2026-05-23

After Phase 7b has run ~2 weeks in production, evaluate Phase 7c (SET + FPC). Check `tool_edges` table has ≥50 rows with meaningful success_rate variance before starting.

---

## Element 8: PostProcessor — Priority Pipeline, Bidirectional Wiring & Telemetry

### Status: 🔧 Implemented + merged

### Scope
`src/gateway/handlers/post-processor.ts`, `src/queue/task-queue.ts`,
`src/memory/db.ts` (schema v18), `src/context/layer.ts`,
`src/context/layers/knowledge.ts`, `src/context/layers/predictive.ts` (new),
`src/context/index.ts`, `src/gateway/handlers/context-builder.ts`,
`src/knowledge/graph.ts`, `src/gateway/core.ts`

### Findings
- 23 PostProcessor jobs with no priority system; slow dna-evolve blocked fast digest-update
- 11/23 jobs had no error handling — silent failures
- 4 zombie jobs (knowledge-extract, timeline-snapshot, goal-extraction, predictive-prep) wrote to storage but no context layer ever read the output
- 3 synchronous calls (coordinator.processMessage, patternAnalyzer.recordAction, sentimentProbe) had no guard — any crash aborted process()
- KnowledgeGraphLayer read from (req.session as any).knowledgeGraphContext — a cast never populated
- PredictiveQueue had no context layer at all

### Improvements Implemented
- **Three-tier TaskQueue**: CRITICAL(high) / STANDARD(normal) / BACKGROUND(low) — drainCritical() awaited in handleCore() before next LLM call
- **Schema v18**: post_processor_job_runs telemetry table — every job records success/failure/duration
- **enqueueJob() wrapper**: all jobs converted, error telemetry automatic, no more silent failures
- **Decision 9 guards**: try/catch on coordinator.processMessage, patternAnalyzer, sentimentProbe arm/onNextMessage
- **Decision 8 null guard**: ctx.db!.rawDb → ctx.db?.rawDb optional chaining
- **Zombie removal**: timeline-snapshot, goal-extraction (+ setGoalExtractor, maybeExtractGoals) removed; knowledge-extract re-added at 10-message BACKGROUND interval
- **KnowledgeGraphLayer**: rewritten to read from req.deps.knowledgeGraph via new queryContext() method — genuinely bidirectional
- **PredictiveContextLayer**: new — reads getReadyTasks() from PredictiveQueue, injects <predicted_next> block into system prompt
- **ContextDependencies**: knowledgeGraph + predictiveQueue wired from GatewayContext via context-builder.ts

### Schema
- v18: post_processor_job_runs(job_name, tier, success, error_code, duration_ms, user_id, session_id, ts)

### Bidirectionality map: 21 active jobs, all with confirmed read-back paths
- Spec: docs/superpowers/specs/2026-05-02-postprocessor-element8-design.md

---

## Element 15: Memory DB (rewrite)

### Scope
21 files in `src/memory/`, ~9,942 LOC. `db.ts` god-class is 3,803 lines / 41 tables / 24 schema migrations / 32 `applyV*` methods. Outside consumers of `MemoryDatabase.rawDb`: 9 (5 in `src/intelligence/`).

### Phase 1 — Audit (2026-05-03, Winston)
- Audit doc: `_bmad-output/planning-artifacts/element15-memory-architecture-audit-2026-05-03.md`
- Findings: 25 hardcoded-classification violations cited with line numbers; schema drift on `trajectory_turns`/`trajectories`/`task_ledgers`; rawDb encapsulation breach at `db.ts:404`; KEEP/REWRITE/EXTEND/MERGE/DELETE/MOVE verdicts per file and per table.
- Verdicts: `db.ts` → REWRITE. `consolidator.ts`, `context-manager.ts`, `prior-context-retriever.ts` → DELETE. `preference-recognizer` + `preference-enforcer` + `fact-extractor` → MERGE. 12 tables MOVE-OUT; 10 DELETE → ~19 surviving memory tables.

### Phase 2 — Research (2026-05-03, Mary)
- Research doc: `_bmad-output/planning-artifacts/research/market-stackowl-element15-memory-db-research-2026-05-03.md`
- 10 production memory systems profiled (Mem0, Letta/MemGPT, Zep, Cognee, LangChain, LlamaIndex, ChatGPT, Claude, Cursor, Continue.dev) with cited 2024-2026 pain points.
- 10 arXiv papers analyzed (Reflexion, MemoryBank, Generative Agents, MemGPT, A-MEM, Mem0, Sleep-time Compute, LongMemEval, LoCoMo, AgentEvolver).
- Creative-gap thesis: 5 architectural moves no competitor or paper composes. Headline evidence: mem0ai/mem0#4573 — 97.8% junk in unfiltered memory extraction.

### Phase 3 — Design (2026-05-03, brainstorming)
- Spec: `docs/superpowers/specs/2026-05-03-element15-memory-architecture-design.md`
- v1 ships moves #1 (goal-conditioned writes), #4 (event-driven invalidation), #5 (TTL-layered rendering). v2 holds moves #2 (parliament retention), #3 (DNA coupling).
- 3 new files: `memory/repository.ts`, `memory/writer.ts`, `memory/layer.ts`. Existing `db.ts` shrinks to schema-owner + migration runner.
- 12 tables (down from 41): 5 memory kinds + 3 substrate (kept) + 4 linkage/audit. Single v25 migration with row-count verification + on-disk backup.
- LLM tool: `memory(action: "search" | "invalidate")`. Writes are event-driven only; no `Remember` tool.
- Operator surface: gateway-uniform `/memory` command (CLI/Telegram/web identical).
- Approval gate on invalidate: `importance ≥ 0.8` routes through HitlChannel.
- Reflexive memories explicitly excluded from prompt (no `ReflexiveMemoryLayer`).
- ~105 new tests planned across unit/integration/migration/perf layers.

### Phase 4 — Implementation (2026-05-04, in progress on `feature/element-15-memory`)
- Plan: `docs/superpowers/plans/2026-05-03-element15-memory-architecture-v1.md` (32 tasks across phases A-J)
- Phase A (typed surface skeleton) — Tasks 1-3 ✅ committed
  - `src/memory/repository.ts` introduced with full read/write API + tests
  - `randomUUID()` for `memory_invalidations` / `memory_contradictions` / `memory_access_log` IDs (avoids same-ms collisions)
- Phase B (live migration pipeline) — Tasks 4-7 ✅ committed
  - `applyV25Migration` ships with `memories` + `memory_invalidations` + `memory_contradictions` + `memory_access_log` tables, indexes, and CHECK constraints (kind enum / verdict enum / importance ∈ [0,1])
  - `backupBeforeV25(dbPath)` writes `.v24-backup-<ts>` sidecar before mutation; called from `MemoryDatabase` constructor when current_version < 25
  - Three migration entry points wired (`MemoryDatabase.runMigrations` ×2, standalone `applyMigrations`)
  - Legacy merge: `INSERT OR IGNORE` from `facts` (→ semantic), `episodes` (→ episodic), `pellets` (→ semantic), `summaries` (→ episodic). `tableHasColumns` guard handles older same-named tables created by earlier migrations.
  - Production audit before merge: real targets are facts (150 rows) / episodes (13) / summaries (3) / pellets (0). Plan's example schemas didn't exist; merge written against actual production schema.
  - Integration test: file-backed legacy db → backup → migrate → repository.search by kind. End-to-end pass.
- Test counts: 1042 passing (was 1023). 19 new v25 tests + repository tests added.

### Commits
- `4141fc0` — Phase 3 design spec
- (Phase A) Tasks 1-3 — repository skeleton, randomUUID hardening
- (Phase B) Tasks 4-7 — v25 schema + backup + legacy merge + integration test (`a26cca7` is HEAD of Phase B)

### Status
✅ Element 15 complete. Phases A-H + J shipped; Phase I scoped out. 1144 tests passing across 172 files.

### Phase J — Boot wiring + acceptance ✅ (Tasks 30-32)
- `src/index.ts`: imports `MemoryRepository`, `MemoryWriter`, `HitlCheckpointStore`, `createMemoryTool`. After gateway construction, builds the trio using `gateway.gatewayEventBus` + `gateway.ctx.intelligence`, assigns `gateway.ctx.memoryRepo` / `memoryWriter`, calls `attachBusListeners()`, and registers the canonical `memory` tool via `b.toolRegistry.register(createMemoryTool({ repo, bus, hitl }))`.
- Legacy `createMemoryUnifiedTool` registration block (~40 lines, including the raw `UPDATE facts` SQL at the old line 729) deleted entirely. The canonical tool replaces it surface-for-surface.
- Task 31: `__tests__/memory-integration.test.ts` (5 tests) — proves the end-to-end seam: bus event drives working-memory expiry through writer; HITL gate fires at importance ≥ 0.8 (and not below); CLI/Telegram surfaces produce byte-identical output via shared router; full lifecycle (insert → search via tool → get via router → invalidate via tool → list excludes invalidated → history preserves the invalidation row).
- Task 32 acceptance: full suite green (1144/1144), `tsc --noEmit` introduces no new errors (only the same 6 pre-existing main-branch errors remain).

### Phase C-G — Repository surface, writer, layers, event bus, canonical tool ✅
- Phase C-F: insertBatch + invalidate + getById + history + recordAccess + stats + searchSemanticByEmbedding + expireWorkingMemories — all committed with test coverage.
- Phase G (Tasks 13-21): MemoryWriter (classify → extract → reconcile via IntelligenceRouter cheap-tier; ADD/UPDATE/DELETE/NOOP; engine:turn_complete listener for working-memory TTL; reflexive memory ingest path) + 4 ContextLayer factories (semantic/episodic/working/procedural with token budgets, getCacheKey by sessionId, reflexive excluded by construction) + GatewayEventBus 11 memory:* variants + canonical `createMemoryTool` with HITL approval gate (importance ≥ 0.8 → checkpointId).

### Phase H — `/memory` command parity ✅ (Tasks 22-24)
- `src/gateway/commands/memory-router.ts`: channel-agnostic `dispatchMemoryCommand(verb, args, { repo })` covering list/search/stats/history/get/invalidate/export with HELP fallback (13 tests).
- `GatewayContext.memoryRepo` + `memoryWriter` fields added with `gateway.getMemoryRepo()` / `getMemoryWriter()` accessors.
- CLI `/memory` command registered in `src/cli/commands.ts` (with help-text entry + subcommand completions).
- Telegram `/memory` command in `src/gateway/adapters/telegram.ts` using `sendChunked` for 4096-char limit. Same dispatcher → identical output across CLI and Telegram (channel parity rule satisfied).

### Phase I — rawDb consumer migration ⚠️ SCOPED OUT
The audit listed 9 "rawDb memory-table consumers" needing migration. Verified each at implementation time:
- `intelligence/fact-invalidator.ts` → touches `facts` table (operational, not v25 memory)
- `intelligence/sleep-time-consolidator.ts` → touches `summaries`
- `intelligence/reflexion-engine.ts` + `critique-retriever.ts` → touch `reflexion_critiques`
- `intelligence/skill-template-layer.ts` → touches `skill_templates`
- `owls/evolution.ts:465` → touches `outcome_journal` (engine telemetry, not memory)
- `gateway/handlers/post-processor.ts:57,698` → touch `outcome_journal` + `post_processor_job_runs`
- `index.ts:729` → touches `facts` for the legacy memory-tool's invalidate-by-keyword — wholesale **replaced** by `createMemoryTool` in Phase J Task 30
- `intelligence/owl-state-reporter.ts:43` → touches `pellets` (legacy memory table; v25 non-destructive merge means it still works post-v25)

v25 is a non-destructive forward-merge: legacy tables (`facts`, `pellets`, `episodes`, `summaries`) remain populated and readable. The new `memories` table coexists. Consumers that read legacy tables are unaffected by Element 15 — they're outside its scope. The only consumer touching the new memory surface that this Element introduces is the `createMemoryTool` registration in `index.ts`, which Task 30 replaces directly.

Phase I was based on an out-of-date audit assumption that v25 would *replace* legacy tables. It doesn't. Tasks 25-29 are dropped. Reopen as a follow-up Element when/if the legacy tables are scheduled for removal.

### Commits (Phases C-H)
- Phase A-F (Tasks 1-12) — repository surface
- `6b88654` — feat(memory): writer reconcile + working-memory TTL + reflexive ingest (Tasks 13-16)
- `e13c315` — feat(memory): four ContextLayer factories with maxTokens truncation (Tasks 17-18)
- `cc01a49` — test(gateway): cover all 11 memory:* event variants + engine:turn_complete (Task 20)
- `3c48753` — feat(tools): canonical memory tool with HITL approval gate (Task 21)
- `85fe634` — feat(memory): /memory command router (Task 22)
- `d708087` — feat(cli): /memory command via MemoryCommandRouter (Task 23)
- `50d5e49` — feat(telegram): /memory command (channel parity) (Task 24)

---

## Element 16 — Web Browsing Honesty & Wiring (Phase A)

**Status:** ✅ Shipped. Merged to main as `d59dc00` on 2026-05-04. 585 files / 4842 tests passing. Phase B (Obscura/new stealth backends) deferred pending Phase A telemetry.
**Started:** 2026-05-04
**Driver:** Web tools return narrative "BLOCKED:" strings before all tiers actually run. CamoFox is registered but never observed running (missing from `package.json`, `start.sh` skips silently). Scrapling is reachable in source but invisible to the LLM (deprecated:true). The "umbrella" hides a 4-tier chain the LLM can't see, reason about, or override — so it parrots "I'm blocked" prematurely.

**Scope (Phase A only):** Honesty + wire what already exists. No new stealth backends. Phase B (Obscura/h4ckf0r0day or similar) is deferred until Phase A telemetry shows whether honesty + wiring alone closes the gap.

### Phases

- ✅ **Phase 1 — Code audit** — `_bmad-output/planning-artifacts/element16-web-tools-audit-2026-05-04.md` (3 parallel Explore agents → formalized). Findings: Tier 4 (CamoFox) is a ghost tier; tools return narrative strings not envelopes; GoalVerifier amplifies via `<tool_result_warning verdict="BLOCKED">`; Scrapling orphaned; two parallel browser stacks (live_browser vs CamoFox) without coordination.
- ✅ **Phase 2 — Market research (Mary)** — `_bmad-output/planning-artifacts/research/market-element16-web-tools-research-2026-05-04.md`. 10 production assistants/scrapers profiled, arXiv 2025-2026 frontier (AgentDebug, PALADIN: +26-50% task success from structured errors), OSS backend survey (Patchright, Camoufox, Botasaurus, Nodriver, hrequests, Obscura). Anthropic envelope contract (`is_error: true` + instructive message) confirmed as already-defined surface. Obscura flagged risky for Phase A: v0.1.2, single maintainer, ~5 months old, self-reported bench.
- ✅ **Phase 3 — Architecture review (Winston)** — `_bmad-output/planning-artifacts/element16-web-tools-architecture-review-2026-05-04.md`. 6 locked decisions: structured envelope schema, LLM tool surface, CamoFox bootstrap, Scrapling pipe, GoalVerifier coupling (key off `error.code`, not `"BLOCKED:"` substring), telemetry/narration. Surfaced third dishonesty axis: `runtime.ts:2367` Anti-Bot Override prompt names `scrapling_fetch` + `camofox` (both deprecated/hidden) — LLM is being told to call tools it cannot see.
- ✅ **Phase 4 — Design spec** — `docs/superpowers/specs/2026-05-04-element16-web-tools-honesty-design.md`. 12 sections, Boss-approved. Locked architecture: 3-tier umbrella (http → camofox → scrapling), live-browser is a peer (not in chain — only opens for auth/visual at LLM's request), `hint?: 'anti-bot'` closed enum, generic envelope-driven prompt (Flavor X), lazy classifier with status-code triggers, fine-grained per-tier bus events. 3 NEW files: `src/browser/envelope.ts`, `src/runtime/availability.ts`, `src/browser/blocking-classifier.ts`.
- ✅ **Phase 5 — Implementation plan** — `docs/superpowers/plans/2026-05-04-element16-web-tools-honesty.md`. 27 TDD tasks across 8 phases, 2828 lines. Realistic implementation time: 9-10 engineer-hours. Highest-risk task: Task 15 (`registry.ts:411-413` envelope-aware rewrite — every tool crosses this code path). Plan agent flagged 5 spec ambiguities for engineer judgement, patched 3 spec coverage gaps. `start.sh:270-333` deletion (CamoFox install moves out of dev script into the assistant's existing onboarding wizard at `src/cli/onboarding.ts` Section D). `package.json` adds `camofox-browser` to `optionalDependencies`.
- ✅ **Phase 6 — Execute** — All 26 TDD tasks executed inline (worktree `feature/element-16-web-tools`, now merged + cleaned). 25 source-only commits + merge commit `d59dc00`. New: `src/browser/envelope.ts`, `src/runtime/availability.ts`, `src/browser/blocking-classifier.ts`. 3-tier dispatcher (`http → camofox → scrapling`) wired through `webFetchEnvelope`; `<tool_attempt_summary>` replaces `BLOCKED:` narrative; GoalVerifier keys off `error.code`; CamoFox install moved from `start.sh` to `stackowl backends install`; `camofox-browser` added to `optionalDependencies`. Schema v26 (`tool_executions.attempt_metadata`) for Phase B telemetry gating.

### Commits

Merge: `d59dc00 Merge Element 16 — Web Browsing Honesty & Wiring (Phase A)` (25 commits + merge).

---

## Element 16c — Web Fetch Simplification

**Status:** ✅ DONE  
**Completed:** 2026-05-05  
**Worktree:** `feature/element-16c`

Eight architectural phases shipped, net file delta in `src/`: −2 files, zero new `src/` files beyond plan. 20 tasks, 1251 tests passing.

### Phases

- ✅ **Phase 1 — v27 host_root migration for learned routing** — Schema v27 adds `host_root` column to `tool_edges`; `EdgeAccumulator` writes canonical host roots; `FallbackSequencer.getLearnedSequence()` queries by `host_root` for portable routing memory across URL paths.
- ✅ **Phase 2 — host-aware EdgeAccumulator + FallbackSequencer** — `EdgeAccumulator` extracts host root from tool input URLs; `FallbackSequencer` queries `tool_edges` by `host_root` so learned sequences transfer across URL paths on the same domain.
- ✅ **Phase 3 — WebToolResult envelope (drops 'http', adds 'obscura' type slot)** — `WebToolResult.type` enum loses `'http'` (replaced by `'fetch'`); adds `'obscura'` slot for future stealth-backend integration; all consumers updated.
- ✅ **Phase 4 — webFetch.obscura.enabled config + type safety** — `StackOwlConfig.webFetch.obscura.enabled` boolean added to config schema and loader; `BlockingClassifier` reads the flag before routing to obscura tier.
- ✅ **Phase 5 — 3-tier dispatcher (scrapling→camofox→obscura stub), BlockingClassifier** — Dispatcher order rebalanced to `scrapling → camofox → obscura-stub`; `BlockingClassifier` replaces hardcoded CAPTCHA host list with LLM cheap-tier classification; obscura stub returns structured `UNAVAILABLE` envelope when disabled.
- ✅ **Phase 6 — web_search tool (DDG, envelope return), BlockingClassifier replaces hardcoded list** — `src/tools/search.ts` rewritten: DDG HTML scrape, structured `WebToolResult` envelope return, `BlockingClassifier` replaces the previous hardcoded CAPTCHA/bot-detection keyword array.
- ✅ **Phase 7 — web_fetch rename (web_crawl→web_fetch, TOOL_FALLBACKS, narration)** — `src/tools/web.ts` tool name changed from `web_crawl` to `web_fetch`; `TOOL_FALLBACKS` registry entry updated; narration formatter updated for new name.
- ✅ **Phase 8 — Deletions (web-unified.ts, Brave search, aliases), capability matchers, learned-text refs, one-shot scrubber** — `src/tools/web-unified.ts` deleted; Brave search provider removed; tool aliases cleaned up; capability matchers updated; `learned-text` references scrubbed; one-shot migration scrubber runs at startup to retire orphaned `tool_edges` rows referencing deleted tools.

### Net stats

- `src/` file delta: −2 (deleted `web-unified.ts` + Brave provider)
- New `src/` files: 0 (all work in existing files)
- Tasks: 20 / 20 complete
- Tests: 1251 passing, 0 failures
- TS errors: 9 (≤ 11 pre-existing baseline)

---

## Backlog / Cross-cutting Issues Found

*(Issues that affect multiple elements — tracked here to avoid losing them)*

---

## Legend
- ⬜ pending
- 🔄 in progress  
- ✅ reviewed — no action needed
- 🔧 reviewed — improvements committed
- ⚠️ reviewed — deferred (needs bigger rework)
