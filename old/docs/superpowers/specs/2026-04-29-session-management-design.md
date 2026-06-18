# Session Management Design Spec

**Date:** 2026-04-29  
**Status:** Approved — all 7 sections  
**Context:** Platform audit Element 3. The existing `SessionManager` class (`src/gateway/handlers/session-manager.ts`) was extracted from `core.ts` but never called — `core.ts` still has its own inline session management. Messages are stored in both JSON session files AND the SQLite `messages` table. `CrossSessionStore.extractFromSession()` is never called, so the `facts` table is never auto-populated from conversations. The 50-message limit silently drops messages instead of summarizing first.

---

## Problem Summary

| Problem | Root Cause | Impact |
|---------|-----------|--------|
| `SessionManager` declared but never called | Extracted but not wired | Dead code, confusion |
| Dual storage (JSON + SQLite) | Incomplete migration | Two sources of truth |
| `extractFromSession()` never called | CrossSessionStore not wired | Facts table always empty from real conversations |
| 50-message silent drop | `slice(-50)` in `saveSession()` | Conversation context lost |
| `approach_library` / `prompt_optimization_log` never written | No caller | Dead tables |

---

## Section 1: Architecture

**New file:** `src/session/service.ts` — unified `SessionService` replacing:
- `core.ts` private methods: `getOrCreateSession()`, `saveSession()`, `evictStaleSessions()` (lines ~3372–3468)
- Dead `SessionManager` at `src/gateway/handlers/session-manager.ts` (to be deleted)
- The fact-extraction responsibility of `CrossSessionStore.extractFromSession()` (never called)

**SQLite as single source of truth.** The `messages` table (schema v11) is the authoritative store. JSON session files are migrated on startup and removed.

**Dependency chain:**
```
OwlGateway (core.ts)
  └── SessionService (src/session/service.ts)
        ├── MemoryDatabase (ctx.db) — messages + summaries + facts tables
        ├── MessageCompressor (ctx.compressor) — 20-message batch summaries
        ├── UserMemoryStore (src/session/user-memory-store.ts) — facts query layer
        └── IntelligenceRouter (ctx.intelligence) — model selection for extraction
```

`GatewayContext` gets two new optional fields:
```typescript
sessionService?: SessionService;
userMemoryStore?: UserMemoryStore;
```

`core.ts` instantiates both in the constructor if `ctx.db` is present, then the three inline private methods are removed.

---

## Section 2: SessionService Interface

```typescript
// src/session/service.ts

export interface SessionContext {
  summaryBlock: string;       // assembled from summaries table via compressor.buildContext()
  recentFacts: string;        // top-3 semantic hits from UserMemoryStore
  recentMessages: ChatMessage[];  // last 50 raw messages from DB
}

export class SessionService {
  constructor(
    private db: MemoryDatabase,
    private compressor: MessageCompressor,
    private userMemoryStore: UserMemoryStore,
    private intelligence: IntelligenceRouter | undefined,
    private fallbackProvider: string,
    private fallbackModel: string,
  ) {}

  /** Load or create session; updates lastActivity in RAM cache. */
  async getOrCreate(sessionId: string, userId: string, owlName: string): Promise<Session>

  /** Append messages to SQLite; enforce 300-message rolling window. */
  async addMessages(sessionId: string, messages: ChatMessage[], userId: string, owlName: string): Promise<void>

  /** Assemble context for engine injection: summaries + facts + recent raw messages. */
  async buildContext(sessionId: string, userId: string, lastUserText: string): Promise<SessionContext>

  /** Fire end-of-session pipeline: async fact extraction + existing endSession() hooks. */
  async endSession(sessionId: string, userId: string, owlName: string): Promise<void>

  /** Evict sessions inactive for SESSION_TIMEOUT_MS; fire endSession() for each. */
  evictStale(): void

  /** Detect if incoming message is a greeting that should trigger endSession on the current session. */
  isGreetingReset(text: string, currentMessageCount: number): boolean
}
```

**Constants:**
```typescript
const MAX_SESSION_MESSAGES = 300;    // was 50
const SESSION_TIMEOUT_MS   = 2 * 60 * 60 * 1000;  // 2 hours (unchanged)
const EVICTION_INTERVAL_MS = 30 * 60 * 1000;       // 30 min (unchanged)
const MIN_MESSAGES_FOR_EXTRACTION = 4;              // don't extract from trivial sessions
```

---

## Section 3: UserMemoryStore

Query layer over the existing SQLite `facts` table + fastembed embeddings. Keyed by `userId` (cross-channel — same user on CLI and Telegram shares the same facts).

```typescript
// src/session/user-memory-store.ts

export class UserMemoryStore {
  constructor(private db: MemoryDatabase) {}

  /** Semantic search: embed query, cosine-rank facts, return top-k. */
  async retrieve(userId: string, query: string, limit = 3): Promise<string[]>

  /** Write a new fact. Dedup: skip if cosine ≥ 0.88 vs any existing fact for this user. */
  async add(userId: string, fact: string, category: string, owlName: string): Promise<void>
}
```

**Embedding:** fastembed `BAAI/bge-small-en-v1.5` (384-dim) — already used by `src/pellets/embedder.ts`. Reuse the same singleton instance.

**Dedup threshold:** 0.88 cosine similarity (same as pellet dedup in `SemanticDeduplicator`).

**Fallback:** If fastembed is unavailable (cold start, model not downloaded), fall back to FTS5 keyword search via `facts_fts` virtual table.

**Facts table schema** (existing, schema v11):
```sql
facts(id, user_id, owl_name, fact TEXT, entity, category, confidence, source, embedding TEXT, access_count, expires_at, created_at, updated_at)
```
Embeddings stored as JSON-serialized `number[]` in the `embedding` TEXT column.

---

## Section 4: Context Injection

`buildContext()` assembles a `SessionContext` on every turn. Budget fits within the 1,200-token `MemoryFirstContextBuilder` allocation (400 preferences + 400 episodes/facts + 400 pellets):

| Layer | Source | Budget |
|-------|--------|--------|
| Summary block | `compressor.buildContext(sessionId, recentMessages)` | ~300 tokens |
| Recent facts | `userMemoryStore.retrieve(userId, lastUserText, 3)` | ~100 tokens |
| Recent messages | `db.messages` last 50 raw | passed directly to engine |

**Summary block format** (existing, from `MessageCompressor.buildContext()`):
```
<conversation_history_summary>
Task: ...
Accomplished: ...
Key facts: ...
Decisions: ...
Open questions: ...
</conversation_history_summary>
```

**Facts block format** (new):
```
<user_memory>
- [preference] Prefers concise answers
- [skill] Expert in TypeScript
- [preference] Uses dark mode
</user_memory>
```

Both blocks are injected into the system prompt before the conversation history. The engine receives the last 50 raw messages as the message array (not the full 300).

---

## Section 5: Smart End Detection + Async Fact Extraction

### End Detection — two triggers

**Trigger 1: 2-hour timeout** (already works via `evictStaleSessions()`)  
No changes needed. The eviction loop fires `endSession()` for sessions inactive ≥ 2 hours.

**Trigger 2: Greeting reset**  
When a new message arrives, `SessionService.isGreetingReset(text, messageCount)` returns `true` if:
- `messageCount >= 4` (session has real content)
- Text matches greeting patterns: `^(hi|hello|hey|good morning|good afternoon|howdy|yo|sup)\b` (case-insensitive)

If `true`: fire `endSession()` for the current session **before** `getOrCreate()` creates the new one. This ensures episodic extraction, fact extraction, and learning pipeline all run at the natural session boundary, not just at timeout.

### Async Fact Extraction — new step in `endSession()`

Add after the existing `[endSession:episodic]` block:

```typescript
// Async fact extraction → facts table
if (messages.length >= MIN_MESSAGES_FOR_EXTRACTION && this.db) {
  try {
    const { provider, model } = this.intelligence?.resolve("extraction")
      ?? { provider: this.fallbackProvider, model: this.fallbackModel };
    const facts = await extractFactsFromConversation(messages, provider, model);
    for (const f of facts) {
      await this.userMemoryStore.add(userId, f.fact, f.category, owlName);
    }
    log.engine.info(`[endSession:facts] ✓ extracted ${facts.length} facts`);
  } catch (err) {
    log.engine.warn(`[endSession:facts] ✗ failed: ${err instanceof Error ? err.message : err}`);
  }
}
```

**`extractFactsFromConversation()`** — new function in `src/session/fact-extractor.ts`:
- Calls LLM with the last 20 messages (or full history if < 20)
- Model: `intelligence.resolve("extraction")` → low tier (cheap model, e.g. haiku-class)
- Prompt asks for JSON array: `[{ fact: string, category: "preference"|"skill"|"episode"|"correction" }]`
- Returns at most 10 facts per session
- Each fact passes through `UserMemoryStore.add()` for dedup before writing

---

## Section 6: SQLite Migration

**Goal:** eliminate JSON session files; `messages` table becomes the only session store.

**Migration runs once** at startup in `OwlGateway` constructor, before `SessionService` is instantiated:

```typescript
// src/session/migrate.ts
export async function migrateJsonSessionsToSQLite(
  sessionStore: SessionStore,   // existing JSON-based store
  db: MemoryDatabase,
  owlName: string,
): Promise<void>
```

**Steps:**
1. `sessionStore.listSessions()` — enumerate all JSON session files
2. For each: `sessionStore.loadSession(id)` → read messages
3. Check if already in SQLite: `db.messages.countSession(id) > 0` → skip if already migrated
4. Write each message to `messages` table with correct `seq` ordering
5. Delete the JSON file: `sessionStore.deleteSession(id)`
6. Log: `[Migration] Migrated session {id} — {n} messages`

**After migration:** `ctx.sessionStore` JSON calls in `core.ts` are replaced by `SessionService` SQLite queries. The `SessionStore` interface and JSON implementation are kept in place for non-DB environments (tests, lightweight deploys) but `SessionService` bypasses them when `ctx.db` is present.

---

## Section 7: 300-Message Rolling Window with Summary-Before-Drop

`MAX_SESSION_MESSAGES` is raised from 50 → 300. The silent `slice(-50)` in `saveSession()` is replaced by a **summary-before-drop** check in `SessionService.addMessages()`.

### Flow when messages exceed 300

```
addMessages(sessionId, newMessages, userId, owlName):
  1. Append new messages to DB via db.messages.append(sessionId, userId, owlName, newMessages)
  2. count = db.messages.countSession(sessionId)      ← existing method
  3. if count <= MAX_SESSION_MESSAGES: return  ← normal path
  4. overflow = count - MAX_SESSION_MESSAGES   ← how many to drop
  5. oldest = db.messages.getOldestN(sessionId, overflow)  ← NEW method
  6. covered = summaries table covers oldest[0].seq through oldest[-1].seq?
     → query: SELECT * FROM summaries WHERE session_id = ? AND from_seq <= ? AND to_seq >= ?
  7. if covered:
       db.messages.deleteByIds(oldest.map(m => m.id))  ← NEW method; safe to drop
  8. if NOT covered:
       await compressor.compress(sessionId, userId, owlName, oldest)  ← summarize first
       db.messages.deleteByIds(oldest.map(m => m.id))
```

**Normal path:** `PostProcessor` already triggers `compressor.compress()` every 20 messages. By the time 300 messages accumulate, 14+ summaries already exist in the `summaries` table. The `if covered` branch fires almost always — the `if NOT covered` branch is a safety net for cases where compression was skipped (provider down, cold start, etc.).

**Context assembly for engine:** `buildContext()` returns:
- `summaryBlock` — `compressor.buildContext(sessionId, last50)` uses `summaries.getLatest(sessionId)` (one summary record covering all prior batches)
- `recentMessages` — `db.messages.getRecent(sessionId, 50)` (last 50 raw, never the full 300; existing method)

The engine never sees 300 messages; it sees the assembled summary + 50 recent. This keeps context window usage identical to today regardless of session length.

---

## Files Touched

| File | Action | Responsibility |
|------|--------|----------------|
| `src/session/service.ts` | **Create** | `SessionService` — session lifecycle, addMessages, buildContext, endSession, evictStale |
| `src/session/user-memory-store.ts` | **Create** | `UserMemoryStore` — facts query/write with fastembed dedup |
| `src/session/fact-extractor.ts` | **Create** | `extractFactsFromConversation()` — LLM-based fact extraction |
| `src/session/migrate.ts` | **Create** | One-shot JSON→SQLite migration |
| `src/gateway/core.ts` | **Modify** | Remove inline `getOrCreateSession`, `saveSession`, `evictStaleSessions`; instantiate `SessionService` + `UserMemoryStore`; wire greeting-reset check; update `endSession()` |
| `src/gateway/types.ts` | **Modify** | Add `sessionService?: SessionService`, `userMemoryStore?: UserMemoryStore` to `GatewayContext` |
| `src/gateway/handlers/session-manager.ts` | **Delete** | Dead code — replaced by `SessionService` |
| `src/memory/db.ts` | **Modify** | Add `MessagesRepo.getOldestN(sessionId, n)` and `MessagesRepo.deleteByIds(ids[])` methods |
| `__tests__/session-service.test.ts` | **Create** | Unit + integration tests for `SessionService` |
| `__tests__/user-memory-store.test.ts` | **Create** | Unit tests for `UserMemoryStore` dedup + retrieval |

---

## What Is NOT Changing

- `MessageCompressor` (`src/memory/compressor.ts`) — used as-is, no changes
- `SummariesRepo` in `src/memory/db.ts` — used as-is
- `FactsRepo` in `src/memory/db.ts` — used as-is
- Existing `endSession()` hooks (episodic, learning, evolution, etc.) — preserved, fact extraction added as a new step
- `PostProcessor` 20-message batch trigger — continues to run in parallel
- `CrossSessionStore` — kept (commitments/cross-session goals are separate from facts)
- `approach_library` / `prompt_optimization_log` tables — out of scope for this spec
