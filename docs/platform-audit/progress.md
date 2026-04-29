# StackOwl Platform Audit — Progress Tracker

**Goal:** Reverse-engineer every pipeline element, identify architectural problems, and define improvements toward a fully autonomous, self-healing, continuously learning assistant.

**Audit started:** 2026-04-28  
**Flow map:** `docs/platform-audit/platform-flow-map.html`

---

## Pipeline Order (from flow map)

| # | Element | Status | Session |
|---|---------|--------|---------|
| 1 | **Channels** (CLI, Telegram, Slack, Voice, Web) | 🔧 reviewed — improvements committed | 2026-04-28 |
| 2 | GatewayMessage creation | ⬜ pending | — |
| 3 | SessionManager (load / create) | ⬜ pending | — |
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

## Backlog / Cross-cutting Issues Found

*(Issues that affect multiple elements — tracked here to avoid losing them)*

---

## Legend
- ⬜ pending
- 🔄 in progress  
- ✅ reviewed — no action needed
- 🔧 reviewed — improvements committed
- ⚠️ reviewed — deferred (needs bigger rework)
