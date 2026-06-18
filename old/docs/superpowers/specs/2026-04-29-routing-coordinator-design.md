# RoutingCoordinator (Element 4) — Design Spec

**Date:** 2026-04-29  
**Status:** Approved — all 6 epics  
**Context:** Platform audit Element 4. The existing `RoutingCoordinator` is a thin handler (186 lines) that covers explicit `@mention` routing, session-pin restore, and implicit SecretaryRouter delegation. It has no persistent user knowledge, no task ownership, no autonomy, and no relationship context. Pin state lives in JSON files via `SessionStateStore`. Routing signals (GoalGraph, EpisodicMemory, FactStore, Kuzu GraphRAG) are entirely ignored. Four dead files exist in `src/delegation/` and `src/routing/`.

---

## Problem Summary

| Problem | Root Cause | Impact |
|---------|-----------|--------|
| Pin stored as JSON file | `SessionStateStore` writes `sessions/<userId>.json` | Lost on restart, no cross-channel persistence |
| Routing ignores user context | SecretaryRouter uses only keyword + LLM classification | Wrong specialist chosen for returning users |
| Task outcomes never tracked | No `tasks` table, no ownership | Owl forgets what it promised; user must re-explain |
| Background work impossible | All execution is request/response | No long-running tasks, no autonomous follow-up |
| No relationship layer | System treats every message as first contact | No proactive advice, no pattern recognition |
| No status visibility | User can't ask "what are you working on?" | Zero transparency into autonomous work |
| `delegation-decider.ts` / `llm-classifier.ts` / `session-state.ts` / `sub-owl-runner.ts` | Dead code, no live callers | Confusion, maintenance cost |

---

## Architecture

### OwlBrain — Central Routing Coordinator

**New file:** `src/routing/owl-brain.ts`

`OwlBrain` replaces `RoutingCoordinator` (`src/gateway/handlers/routing-coordinator.ts`). It is the single point where every routing decision is made. `core.ts` creates one instance and calls `owlBrain.resolve()` in place of the current `routingCoordinator.resolve()` call.

**Resolution pipeline (in order):**

```
OwlBrain.resolve(text, message, engineCtx, callbacks, session)
  1. Restore persisted pin from SQLite (user_profiles table)
  2. Explicit @mention check → pin/unpin
  3. Session pin resume (in-memory + SQLite)
  4. UserProfileService.buildSignals(userId, text) → RoutingSignals
  5. SecretaryRouter.routeWithSignals(text, userId, signals) → RoutingDecision
  6. Apply specialist / parliament / direct
  7. Persist updated pin to SQLite (async, fire-and-forget)
  8. Return RoutingResult
```

`OwlBrain` is injected into `GatewayContext` as `owlBrain?: OwlBrain` alongside the existing `routingCoordinator` field; `core.ts` prefers `owlBrain` when present.

---

### UserProfileService — Signal Aggregator

**New file:** `src/routing/user-profile-service.ts`

Queries existing systems and assembles `RoutingSignals`. Writes **no new data stores beyond `user_profiles`**. The `user_profiles` table stores only routing metadata (pin, style preference, trust level, routing history). All user knowledge (facts, goals, episodes) stays in its authoritative store.

```typescript
export interface RoutingSignals {
  activePin?: string;              // from user_profiles (SQLite)
  preferredStyle?: string;         // from FactStore / preferences
  domainStack: string[];           // from GoalGraph (active domains)
  recentEpisodes: string[];        // from EpisodicMemory (last 3 episode titles)
  relevantFacts: string[];         // from FactStore / UserMemoryStore (top 3 semantic hits)
  graphContext?: string;           // from Kuzu GraphRAG 1-hop expansion (if available)
  trustLevel: "standard" | "elevated" | "restricted";  // from user_profiles
}
```

**Signal sources (existing systems only):**

| Signal | Source | Method |
|--------|--------|--------|
| `activePin` | SQLite `user_profiles` | `db.userProfiles.getPin(userId)` |
| `preferredStyle` | `FactStore` / `UserMemoryStore` | Semantic search for "communication style preference" |
| `domainStack` | `GoalGraph` | `goalGraph.getActiveDomains(userId)` |
| `recentEpisodes` | `EpisodicMemory` | `episodicMemory.getRecent(userId, 3)` |
| `relevantFacts` | `UserMemoryStore` | `userMemoryStore.retrieve(userId, text, 3)` |
| `graphContext` | Kuzu (via `KnowledgeGraph` / `PelletStore`) | 1-hop expansion on specialist name |
| `trustLevel` | SQLite `user_profiles` | `db.userProfiles.getTrust(userId)` |

All queries are wrapped in `try/catch`; missing systems degrade gracefully to empty/default.

---

### SQLite Schema — 3 New Tables (schema v12)

**`user_profiles`** — routing metadata per user:
```sql
CREATE TABLE user_profiles (
  user_id      TEXT PRIMARY KEY,
  active_pin   TEXT,              -- current specialist pin (NULL = coordinator)
  pinned_at    TEXT,              -- ISO timestamp
  trust_level  TEXT DEFAULT 'standard',
  style_pref   TEXT,              -- e.g. "terse", "detailed", "socratic"
  routing_json TEXT DEFAULT '{}', -- JSON: last 10 routing decisions (ring buffer)
  created_at   TEXT DEFAULT (datetime('now')),
  updated_at   TEXT DEFAULT (datetime('now'))
);
```

**`tasks`** — task ownership:
```sql
CREATE TABLE tasks (
  id           TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  owl_name     TEXT NOT NULL,
  title        TEXT NOT NULL,
  description  TEXT,
  status       TEXT DEFAULT 'pending',  -- pending|active|blocked|done|abandoned
  priority     TEXT DEFAULT 'normal',   -- low|normal|high|urgent
  session_id   TEXT,
  created_at   TEXT DEFAULT (datetime('now')),
  updated_at   TEXT DEFAULT (datetime('now')),
  due_at       TEXT,
  result       TEXT
);
CREATE INDEX idx_tasks_user ON tasks(user_id, status);
```

**`jobs`** — background execution queue:
```sql
CREATE TABLE jobs (
  id           TEXT PRIMARY KEY,
  task_id      TEXT REFERENCES tasks(id),
  user_id      TEXT NOT NULL,
  owl_name     TEXT NOT NULL,
  type         TEXT NOT NULL,   -- 'proactive'|'monitor'|'research'|'followup'
  payload      TEXT NOT NULL,   -- JSON
  status       TEXT DEFAULT 'queued',  -- queued|running|done|failed
  scheduled_at TEXT NOT NULL,
  started_at   TEXT,
  completed_at TEXT,
  error        TEXT,
  result       TEXT
);
CREATE INDEX idx_jobs_status ON jobs(status, scheduled_at);
CREATE INDEX idx_jobs_user ON jobs(user_id, status);
```

---

## Epic E1 — Routing Foundation

**Goal:** Replace the current `RoutingCoordinator` + `SessionStateStore` with `OwlBrain` + SQLite pin persistence. Zero regression on existing routing behavior.

### Stories

**E1-S1: Migrate pin persistence from JSON to SQLite**
- `SessionStateStore` JSON files → `user_profiles` table
- One-shot migration at startup: read all `sessions/<userId>.json` → INSERT into `user_profiles` → delete file
- `OwlBrain.resolve()` reads pin from SQLite on every first message (not just first-ever)
- Acceptance: after restart, pin survives; old JSON files gone

**E1-S2: OwlBrain wires into core.ts**
- `OwlBrain` constructed in `OwlGateway` constructor (same position as `RoutingCoordinator`)
- `GatewayContext` gets `owlBrain?: OwlBrain`; `core.ts` uses it when present
- `RoutingCoordinator` class kept for backward compat; `OwlBrain` delegates to its `@mention` + `SessionStateStore` removal
- Acceptance: all existing routing paths pass their tests; no new test failures

**E1-S3: Delete dead code**
- Delete: `src/delegation/delegation-decider.ts`, `src/routing/llm-classifier.ts`, `src/routing/session-state.ts`, `src/delegation/sub-owl-runner.ts`
- Remove all imports of deleted files; fix TypeScript compilation
- `DelegationDecider` reference in `core.ts` (line 166, 329) removed; `buildClassifyFn` replaced by `UserProfileService` signals in `SecretaryRouter`
- Acceptance: `npm run build` green; `npm run test` green

---

## Epic E2 — Persistent User Knowledge

**Goal:** Routing decisions use real user context from GoalGraph, EpisodicMemory, FactStore, Kuzu. SecretaryRouter receives `RoutingSignals` and weights them.

### Stories

**E2-S1: UserProfileService assembles RoutingSignals**
- `src/routing/user-profile-service.ts` queries all 7 signal sources per turn
- Each query has a 200ms timeout (`Promise.race`) — routing must never block on a slow store
- Signals logged at DEBUG level: `[OwlBrain] signals for ${userId}: pin=${...} domains=[...] facts=${...}`
- Acceptance: unit test stubs all 7 sources; signals assembled in < 300ms

**E2-S2: SecretaryRouter uses signals**
- `SecretaryRouter.routeWithSignals(text, userId, signals)` — new overload
- `domainStack` boosts specialists whose `expertise` overlaps with active goals (+0.15 weight)
- `relevantFacts` boosts specialists mentioned by name in facts (+0.10 weight)
- `graphContext` (Kuzu 1-hop) surfaces related specialists not in keyword list
- Existing keyword + LLM path preserved as fallback when signals are empty
- Acceptance: routing test with seeded GoalGraph prefers correct specialist over keyword match

**E2-S3: UserProfileService writes routing_history**
- After each routing decision, append `{ ts, decision, owl, reason }` to `routing_json` (ring buffer, max 10)
- Used by E5 (RelationshipContext) for pattern recognition
- Acceptance: after 3 messages, `routing_json` contains 3 entries in correct order

---

## Epic E3 — Task Ownership

**Goal:** Owl creates, owns, and tracks tasks. User can say "remind me about X" or "follow up on Y" and the owl records it and delivers without being asked again.

### Stories

**E3-S1: TaskOwnershipManager**
- New file: `src/routing/task-ownership-manager.ts`
- `createTask(userId, owlName, title, description, priority, dueAt)` → inserts into `tasks` table
- `updateStatus(taskId, status, result?)` → updates row
- `getActiveTasks(userId)` → returns `tasks` where `status in ('pending','active','blocked')`
- Injected into `GatewayContext` as `taskOwnershipManager?: TaskOwnershipManager`
- Acceptance: unit tests for CRUD; active tasks query returns correct subset

**E3-S2: Task detection in handleCore()**
- After `OwlBrain.resolve()`, scan the response for task-creation intent
- Detection heuristics: response contains "I'll follow up", "I'll remind you", "I'll check back", "I'll research this", "I'll handle that"
- When detected: `taskOwnershipManager.createTask(...)` with title extracted from context
- No extra LLM call — extraction uses regex + first 80 chars of the commitment statement
- Acceptance: integration test; response with "I'll remind you tomorrow" creates a task row

**E3-S3: Task injection into system prompt**
- `buildContext()` in context-builder includes open tasks block when `taskOwnershipManager` present:
  ```
  <open_tasks>
  - [high] Follow up on Docker migration (due: 2026-05-01)
  - [normal] Research Redis clustering options
  </open_tasks>
  ```
- Max 5 tasks injected; oldest `updated_at` first
- Acceptance: session with seeded tasks produces correct prompt block

---

## Epic E4 — Background Execution

**Goal:** Long-running and scheduled tasks run in the background. Owl reports completion proactively via the heartbeat/event bus system.

### Stories

**E4-S1: BackgroundJobRunner**
- New file: `src/routing/background-job-runner.ts`
- Polls `jobs` table every 60 seconds for `status = 'queued' AND scheduled_at <= now()`
- Executes one job at a time (no parallel — avoids context window collisions)
- On completion: updates `jobs` row, updates linked `tasks` row, fires `GatewayEventBus.emit('job:complete', { userId, result })`
- `GatewayEventBus` subscriber delivers result to user via existing `DeliveryRouter` + `V1Shim`
- Injected into `GatewayContext` as `backgroundJobRunner?: BackgroundJobRunner`
- Acceptance: unit test; queued job with `scheduled_at = now()` executes within 65s

**E4-S2: Proactive follow-up scheduling**
- When a task is created (E3-S2), if it has a `dueAt`, schedule a follow-up job
- `BackgroundJobRunner.scheduleFollowup(task)` → inserts `jobs` row with `type='followup'`, `scheduled_at = dueAt`
- Follow-up job re-evaluates the task: if result present → deliver to user; if not → owl generates a status update
- Acceptance: task with `dueAt = now + 1min` triggers delivery within 65s

**E4-S3: Research job type**
- `type='research'` jobs execute a mini ReAct loop (max 3 tool calls)
- Uses `intelligence.resolve('extraction')` model (low tier — cheap)
- Result stored in `jobs.result` and `tasks.result`
- Acceptance: research job with web fetch tool completes and stores result

---

## Epic E5 — Relationship Layer

**Goal:** Owl builds a model of the user across sessions: communication style, expertise growth, recurring topics, emotional patterns. This informs tone, proactive advice, and routing.

### Stories

**E5-S1: RelationshipContext**
- New file: `src/routing/relationship-context.ts`
- Reads from `user_profiles.routing_json` (routing history), `FactStore` (preferences), `EpisodicMemory` (recurring topics), `GoalGraph` (long-running goals)
- Assembles `RelationshipSummary`:
  ```typescript
  export interface RelationshipSummary {
    communicationStyle: string;  // "prefers terse answers", "likes code examples"
    expertiseLevel: string;      // "senior TypeScript, beginner Rust"
    recurringTopics: string[];   // top-3 from routing_history + episodes
    openCommitments: string[];   // tasks with status 'pending' or 'active'
    lastInteraction: string;     // ISO timestamp from last session
  }
  ```
- `RelationshipContext` injected into `GatewayContext` as `relationshipContext?: RelationshipContext`
- Acceptance: unit test with seeded data assembles correct `RelationshipSummary`

**E5-S2: Relationship context injected into system prompt**
- `context-builder.ts` includes relationship block when `relationshipContext` present:
  ```
  <user_relationship>
  Style: prefers terse answers, uses code examples
  Expertise: senior TypeScript, learning Rust
  Recurring: async patterns, deployment pipelines
  Open commitments: 2 tasks pending
  </user_relationship>
  ```
- Block budget: 150 tokens max; truncated if longer
- Acceptance: prompt builder test with seeded relationship produces correct block

**E5-S3: Proactive advice trigger**
- After `endSession()`, `RelationshipContext.checkForProactiveAdvice(userId, sessionSummary)` returns a suggestion or null
- Suggestion created when: a recurring topic has no open goal (GoalGraph miss) AND appeared in ≥3 sessions
- Suggestion queued as a `jobs` row with `type='proactive'`, `scheduled_at = next morning 9am`
- Acceptance: unit test with 3 episodes of "Docker" + no Docker goal → proactive job created

---

## Epic E6 — Status and Transparency

**Goal:** User can ask "what are you working on?" or "what did you commit to?" and get a clear answer. Routing decisions are logged and explainable.

### Stories

**E6-S1: RoutingStatusReporter**
- New file: `src/routing/routing-status-reporter.ts`
- `getStatusReport(userId)` → structured `StatusReport`:
  ```typescript
  export interface StatusReport {
    activePin?: string;
    openTasks: { title: string; status: string; priority: string; dueAt?: string }[];
    queuedJobs: { type: string; scheduledAt: string }[];
    lastRoutingDecision?: { owl: string; reason: string; ts: string };
  }
  ```
- `formatForChannel(report, channelId)` → Markdown string tailored to channel (CLI: table; Telegram: HTML list)
- Injected into `GatewayContext` as `routingStatusReporter?: RoutingStatusReporter`
- Acceptance: unit test with seeded DB produces correct Markdown/HTML output

**E6-S2: Status intent detection**
- In `handleCore()`, before routing, detect status-query intent via keyword match:
  - "what are you working on", "what tasks", "what did you promise", "what are you doing", "status", "what's pending"
- When detected: call `routingStatusReporter.getStatusReport(userId)` → return directly, skip LLM call
- Acceptance: "what are you working on?" returns status report without hitting the engine

**E6-S3: Routing explanation on demand**
- `/why` slash command (existing slash command infrastructure) returns last routing decision
- Format: "I routed to @${owlName} because: ${reason}. Signals: domain match (TypeScript), fact hit (user is senior TS dev)"
- `reason` and signals taken from last `routing_json` entry
- Acceptance: integration test; send message, send `/why`, receive explanation

---

## Files Touched

| File | Action | Responsibility |
|------|--------|----------------|
| `src/routing/owl-brain.ts` | **Create** | Central routing coordinator — replaces `RoutingCoordinator` |
| `src/routing/user-profile-service.ts` | **Create** | Signal aggregator over existing stores |
| `src/routing/task-ownership-manager.ts` | **Create** | Task CRUD + open tasks query |
| `src/routing/background-job-runner.ts` | **Create** | Job queue polling + execution |
| `src/routing/relationship-context.ts` | **Create** | Cross-session user model assembly |
| `src/routing/routing-status-reporter.ts` | **Create** | Status/transparency output |
| `src/routing/secretary.ts` | **Modify** | Add `routeWithSignals()` overload using `RoutingSignals` |
| `src/gateway/core.ts` | **Modify** | Instantiate `OwlBrain` + `TaskOwnershipManager` + `BackgroundJobRunner`; wire E3-S2 task detection; wire E6-S2 status detection |
| `src/gateway/handlers/context-builder.ts` | **Modify** | Inject `open_tasks` block (E3-S3) + `user_relationship` block (E5-S2) |
| `src/gateway/types.ts` | **Modify** | Add `owlBrain`, `taskOwnershipManager`, `backgroundJobRunner`, `relationshipContext`, `routingStatusReporter` to `GatewayContext` |
| `src/memory/db.ts` | **Modify** | Add `UserProfilesRepo`, `TasksRepo`, `JobsRepo`; bump schema to v12 |
| `src/gateway/handlers/routing-coordinator.ts` | **Keep** | Preserved for backward compat; `OwlBrain` wraps it |
| `src/delegation/delegation-decider.ts` | **Delete** | Dead code — no live callers |
| `src/routing/llm-classifier.ts` | **Delete** | Superseded by `UserProfileService` signals in `SecretaryRouter` |
| `src/routing/session-state.ts` | **Delete** | Replaced by `user_profiles` SQLite table |
| `src/delegation/sub-owl-runner.ts` | **Delete** | Dead code — no live callers |

---

## What Is NOT Changing

- `SpecializedOwlRegistry` / `SpecializedOwlSpec` — file-based owl loading unchanged
- `SecretaryRouter` keyword + LLM fallback path — preserved, signals add weight on top
- Parliament detection (`RoutingWirer.classifyWithParliament`) — unchanged
- `GoalGraph`, `EpisodicMemory`, `FactStore`, `MemoryBus`, `CrossSessionStore`, Kuzu — read-only from OwlBrain's perspective
- `UserMemoryStore` (Element 3) — used as a signal source; no changes
- `SessionService` (Element 3) — used for `getUserId()`; no changes
- `IntelligenceRouter` — used for model selection in jobs; no changes
- `DeliveryRouter` / `GatewayEventBus` / `V1Shim` (Element 1) — used for job completion delivery; no changes

---

## Implementation Phases

### Phase 1 — E1 + E2 + E3 + E6 (core routing overhaul)

Delivered together: `OwlBrain`, `UserProfileService`, `TaskOwnershipManager`, `RoutingStatusReporter`, schema v12, dead code deletion.

All existing routing tests must pass. New tests: `__tests__/owl-brain.test.ts`, `__tests__/user-profile-service.test.ts`, `__tests__/task-ownership-manager.test.ts`, `__tests__/routing-status-reporter.test.ts`.

### Phase 2 — E4 + E5 (autonomy + relationship)

Delivered separately: `BackgroundJobRunner`, `RelationshipContext`. Depends on Phase 1 (tasks table, jobs table, routing_history).

New tests: `__tests__/background-job-runner.test.ts`, `__tests__/relationship-context.test.ts`.
