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
| 3 | SessionManager (load / create) | 🔄 in progress — brainstorming design | 2026-04-29 |
| 4 | RoutingCoordinator (owl selection + pin) | ⬜ pending | — |
| 5 | ContextBuilder (memory + pellets + skills) | ⬜ pending | — |
| 6 | OwlEngine — ReAct loop | ⬜ pending | — |
| 7 | Tool layer (registry, execution, permissions) | ⬜ pending | — |
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

### Status: 🔄 Design in progress

### Findings
- `SessionManager` class exists and is instantiated but **never called** — core.ts has duplicate inline session management
- Messages stored in two places: JSON session files AND SQLite `messages` table
- `CrossSessionStore.extractFromSession()` never called — facts never auto-populated from conversations
- SQLite `summaries` table exists but 50-message limit silently drops messages instead of summarizing
- Fact extraction only triggered by user 👍/👎 feedback — never automatic
- `approach_library` and `prompt_optimization_log` tables created but never written

### Design Decisions (approved)
- **Option B — Unified `SessionService`** replacing `SessionManager` + `CrossSessionStore`
- SQLite as single source of truth for session messages (migrate from JSON files)
- Session message limit raised from 50 → 300; LLM summarization into `summaries` table before dropping
- Smart end detection: `CompletionTracker.isComplete()` + greeting reset + 2h timeout
- Async LLM extraction after session ends → writes to existing `facts` table (keyed by `userId`)
- **`UserMemoryStore`** — query layer over SQLite `facts` table + fastembed semantic search
- Context injection: summary at session start + top-3 semantic hits per turn (shared 400-token budget)
- Deduplication: 0.88 cosine threshold (same as pellet dedup)

### Design Sections Status
- ✅ Section 1: Architecture
- ✅ Section 2: SessionService interface
- ✅ Section 3: UserMemoryStore (revised after deep research)
- ✅ Section 4: Context injection
- ✅ Section 5: Smart end detection + async extraction (revised)
- ✅ Section 6: SQLite migration
- ⬜ Section 7: 300-message summarization
- ⬜ Spec write + implementation plan

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
- ⬜ Implementation plan (writing-plans)
- ⬜ Backlog: update `start.sh` onboarding to configure intelligence tiers interactively

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
