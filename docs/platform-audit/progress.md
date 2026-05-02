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
| 7 | Tool layer (registry, execution, permissions) | 🔄 design approved — spec written | 2026-05-02 |
| 8 | PostProcessor (save, learn, evolve, queue) | ⬜ pending | — |
| 9 | Parliament (multi-owl debate) | ⬜ pending | — |
| 10 | Pellet system (generate, store, retrieve, dedup) | ⬜ pending | — |
| 11 | Heartbeat (proactive messages, job queue) | ⬜ pending | — |
| 12 | Learning Engine (reactive + proactive self-study) | ⬜ pending | — |
| 13 | Evolution (DNA mutation, reflexion, APO) | ⬜ pending | — |
| 14 | Memory DB (SQLite facts, episodes, attempts) | ⬜ pending | — |
| 15 | Perches (file watchers, event broadcast) | ⬜ pending | — |
| 16 | Owl system (DNA, inner life, specialization) | ⬜ pending | — |
| 17 | Providers (model routing, health, cost) | ⬜ pending | — |
| 18 | Skills engine (match, inject, synthesize) | ⬜ pending | — |

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

### Status: 🔄 Design approved — spec written — implementation plans pending

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

### Commits
- `02762fc` — spec written (1067 lines, 16 sections)

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
