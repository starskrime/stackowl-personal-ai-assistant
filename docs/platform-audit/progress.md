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
| 13 | Learning Engine (reactive + proactive self-study) | ⬜ pending | — |
| 14 | Evolution (DNA mutation, reflexion, APO) | ⬜ pending | — |
| 15 | Memory DB (SQLite facts, episodes, attempts) | ⬜ pending | — |
| 16 | Perches (file watchers, event broadcast) | ⬜ pending | — |
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

## Backlog / Cross-cutting Issues Found

*(Issues that affect multiple elements — tracked here to avoid losing them)*

---

## Legend
- ⬜ pending
- 🔄 in progress  
- ✅ reviewed — no action needed
- 🔧 reviewed — improvements committed
- ⚠️ reviewed — deferred (needs bigger rework)
