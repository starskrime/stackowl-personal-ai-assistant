# Session Management (Element 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace dead `SessionManager` + dual JSON/SQLite storage with a unified `SessionService` that persists to SQLite, enforces a 300-message rolling window, auto-extracts facts at session end, and injects cross-session user memory into every turn.

**Architecture:** `SessionService` (`src/session/service.ts`) owns all session lifecycle: load/create, SQLite persistence, rolling-window enforcement, greeting-reset detection, and async fact extraction via LLM at session end. `UserMemoryStore` (`src/session/user-memory-store.ts`) is a thin query/write layer over the existing `facts` table with fastembed semantic dedup. Context injection (summaryBlock + recentFacts) is wired into `ContextBuilder`. Existing `session.messages` in-memory array and `endSession()` hooks in `core.ts` are preserved; only the persistence path and two new steps change.

**Tech Stack:** TypeScript, better-sqlite3, fastembed (`src/pellets/embedder.ts` singleton), vitest

---

## File Structure

| File | Status | Responsibility |
|------|--------|---------------|
| `src/memory/db.ts` | Modify | Add `MessagesRepo.getOldestN()` and `deleteByIds()` |
| `src/session/user-memory-store.ts` | Create | Semantic fact query/write with 0.88 cosine dedup |
| `src/session/fact-extractor.ts` | Create | LLM-based fact extraction from conversation |
| `src/session/migrate.ts` | Create | One-shot JSON session files → SQLite messages table |
| `src/session/service.ts` | Create | Session lifecycle, rolling window, greeting reset |
| `src/gateway/types.ts` | Modify | Add `sessionService?` + `userMemoryStore?` to `GatewayContext` |
| `src/gateway/core.ts` | Modify | Wire `SessionService`; greeting reset; endSession facts; update `saveSession()` |
| `src/gateway/handlers/context-builder.ts` | Modify | Inject `userMemoryStore.retrieve()` block next to compression summary |
| `src/gateway/handlers/session-manager.ts` | Delete | Dead code — replaced by `SessionService` |
| `__tests__/messages-repo.test.ts` | Create | Unit tests for `getOldestN()` + `deleteByIds()` |
| `__tests__/user-memory-store.test.ts` | Create | Unit tests for dedup + retrieval |
| `__tests__/fact-extractor.test.ts` | Create | Unit tests for JSON parsing + category mapping |
| `__tests__/session-service.test.ts` | Create | Unit tests for rolling window + greeting reset |

---

## Task 1: Extend `MessagesRepo` with `getOldestN()` and `deleteByIds()`

**Files:**
- Modify: `src/memory/db.ts` (after `getMaxSeq()` at line ~1110)
- Create: `__tests__/messages-repo.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/messages-repo.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), "__tests__/fixtures/messages-repo-test");

let db: MemoryDatabase;

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
  db = new MemoryDatabase(TEST_DIR);
});

afterEach(() => {
  (db as any).db?.close?.();
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true, force: true });
});

describe("MessagesRepo.getOldestN", () => {
  it("returns N oldest messages by seq", () => {
    db.messages.append("sess1", "user1", "owl1", [
      { role: "user", content: "msg1" },
      { role: "assistant", content: "msg2" },
      { role: "user", content: "msg3" },
    ]);
    const oldest = db.messages.getOldestN("sess1", 2);
    expect(oldest).toHaveLength(2);
    expect(oldest[0].seq).toBeLessThan(oldest[1].seq);
  });

  it("returns empty array for unknown session", () => {
    const result = db.messages.getOldestN("nonexistent", 5);
    expect(result).toHaveLength(0);
  });

  it("returns all messages when n exceeds count", () => {
    db.messages.append("sess2", "user1", "owl1", [
      { role: "user", content: "only" },
    ]);
    const result = db.messages.getOldestN("sess2", 100);
    expect(result).toHaveLength(1);
  });
});

describe("MessagesRepo.deleteByIds", () => {
  it("deletes messages by id list", () => {
    db.messages.append("sess3", "user1", "owl1", [
      { role: "user", content: "a" },
      { role: "assistant", content: "b" },
    ]);
    const oldest = db.messages.getOldestN("sess3", 1);
    db.messages.deleteByIds(oldest.map(m => m.id));
    expect(db.messages.countSession("sess3")).toBe(1);
  });

  it("does nothing for empty id list", () => {
    db.messages.append("sess4", "user1", "owl1", [{ role: "user", content: "x" }]);
    db.messages.deleteByIds([]);
    expect(db.messages.countSession("sess4")).toBe(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/messages-repo.test.ts
```

Expected: FAIL — `getOldestN is not a function`, `deleteByIds is not a function`

- [ ] **Step 3: Add `getOldestN()` and `deleteByIds()` to `MessagesRepo` in `src/memory/db.ts`**

Open `src/memory/db.ts`. Find `getMaxSeq()` at line ~1105. Add these two methods immediately after it (before the closing `}` of `MessagesRepo`):

```typescript
  getOldestN(sessionId: string, n: number): Array<{ id: string; seq: number }> {
    const rows = this.db.prepare(
      "SELECT id, seq FROM messages WHERE session_id = ? ORDER BY seq ASC LIMIT ?"
    ).all(sessionId, n) as any[];
    return rows.map(r => ({ id: r.id as string, seq: r.seq as number }));
  }

  deleteByIds(ids: string[]): void {
    if (ids.length === 0) return;
    const placeholders = ids.map(() => "?").join(",");
    this.db.prepare(`DELETE FROM messages WHERE id IN (${placeholders})`).run(...ids);
  }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/messages-repo.test.ts
```

Expected: PASS — 6 passing

- [ ] **Step 5: Commit**

```bash
git add -f __tests__/messages-repo.test.ts
git add src/memory/db.ts
git commit -m "feat(db): add MessagesRepo.getOldestN() and deleteByIds() for rolling window"
```

---

## Task 2: Create `UserMemoryStore`

**Files:**
- Create: `src/session/user-memory-store.ts`
- Create: `__tests__/user-memory-store.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/user-memory-store.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { UserMemoryStore } from "../src/session/user-memory-store.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

// Mock embed so tests don't require the fastembed model
vi.mock("../src/pellets/embedder.js", () => ({
  embed: vi.fn().mockResolvedValue(null),  // null = use FTS fallback
  initEmbedder: vi.fn().mockResolvedValue(undefined),
}));

const TEST_DIR = join(process.cwd(), "__tests__/fixtures/user-memory-store-test");
let db: MemoryDatabase;
let store: UserMemoryStore;

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
  db = new MemoryDatabase(TEST_DIR);
  store = new UserMemoryStore(db);
});

afterEach(() => {
  (db as any).db?.close?.();
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true, force: true });
});

describe("UserMemoryStore.add", () => {
  it("adds a fact to the facts table", async () => {
    await store.add("user1", "Prefers dark mode", "preference", "owl1");
    const facts = db.facts.getAllForUser("user1");
    expect(facts).toHaveLength(1);
    expect(facts[0].fact).toBe("Prefers dark mode");
    expect(facts[0].category).toBe("preference");
  });

  it("maps unknown category to 'context'", async () => {
    await store.add("user1", "Some episode fact", "episode", "owl1");
    const facts = db.facts.getAllForUser("user1");
    expect(facts[0].category).toBe("context");
  });

  it("maps 'correction' to 'context'", async () => {
    await store.add("user1", "User corrected the approach", "correction", "owl1");
    const facts = db.facts.getAllForUser("user1");
    expect(facts[0].category).toBe("context");
  });
});

describe("UserMemoryStore.retrieve", () => {
  it("returns formatted fact strings via FTS fallback", async () => {
    await store.add("user1", "Prefers TypeScript over JavaScript", "preference", "owl1");
    const results = await store.retrieve("user1", "TypeScript");
    expect(results).toHaveLength(1);
    expect(results[0]).toContain("Prefers TypeScript");
  });

  it("returns empty array when no facts exist for user", async () => {
    const results = await store.retrieve("unknown-user", "anything");
    expect(results).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/user-memory-store.test.ts
```

Expected: FAIL — `Cannot find module '../src/session/user-memory-store.js'`

- [ ] **Step 3: Create `src/session/user-memory-store.ts`**

Create the file `src/session/user-memory-store.ts`:

```typescript
import { embed } from "../pellets/embedder.js";
import type { MemoryDatabase, FactCategory } from "../memory/db.js";
import { log } from "../logger.js";

const DEDUP_THRESHOLD = 0.88;

const VALID_CATEGORIES = new Set<FactCategory>([
  "preference", "skill", "personal", "context",
  "project_detail", "goal", "habit", "relationship", "decision",
]);

const CATEGORY_MAP: Record<string, FactCategory> = {
  episode: "context",
  correction: "context",
};

function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  const denom = Math.sqrt(na) * Math.sqrt(nb);
  return denom === 0 ? 0 : dot / denom;
}

export class UserMemoryStore {
  constructor(private db: MemoryDatabase) {}

  /** Semantic search: embed query → cosine-rank facts → return top-k as formatted strings.
   *  Falls back to FTS5 keyword search when fastembed is unavailable. */
  async retrieve(userId: string, query: string, limit = 3): Promise<string[]> {
    const queryEmbed = await embed(query);

    if (queryEmbed) {
      const facts = this.db.facts.semanticSearch(queryEmbed, userId, limit);
      return facts.map(f => `[${f.category}] ${f.fact}`);
    }

    // FTS fallback
    const facts = this.db.facts.search(query, userId, limit);
    return facts.map(f => `[${f.category}] ${f.fact}`);
  }

  /** Write a fact. Skips if cosine ≥ 0.88 vs any existing fact for this user (dedup).
   *  Falls through to FTS dedup when embeddings are unavailable. */
  async add(userId: string, fact: string, category: string, owlName: string): Promise<void> {
    const factEmbed = await embed(fact);

    if (factEmbed) {
      const existing = this.db.facts.getAllForUser(userId);
      for (const e of existing) {
        if (!e.embedding) continue;
        const sim = cosineSimilarity(factEmbed, e.embedding);
        if (sim >= DEDUP_THRESHOLD) {
          log.engine.info(`[UserMemoryStore] Skipping near-duplicate fact (cosine: ${sim.toFixed(3)})`);
          return;
        }
      }
    }

    const mapped: FactCategory = CATEGORY_MAP[category]
      ?? (VALID_CATEGORIES.has(category as FactCategory) ? (category as FactCategory) : "context");

    this.db.facts.add({
      userId,
      owlName,
      fact,
      category: mapped,
      confidence: 0.8,
      source: "inferred",
      embedding: factEmbed ?? undefined,
    });
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/user-memory-store.test.ts
```

Expected: PASS — 5 passing

- [ ] **Step 5: Commit**

```bash
git add src/session/user-memory-store.ts
git add -f __tests__/user-memory-store.test.ts
git commit -m "feat(session): add UserMemoryStore — semantic fact query/write with cosine dedup"
```

---

## Task 3: Create `fact-extractor.ts`

**Files:**
- Create: `src/session/fact-extractor.ts`
- Create: `__tests__/fact-extractor.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/fact-extractor.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { extractFactsFromConversation } from "../src/session/fact-extractor.js";
import type { ChatMessage } from "../src/providers/base.js";

const mockMessages: ChatMessage[] = [
  { role: "user", content: "I prefer TypeScript and use dark mode" },
  { role: "assistant", content: "Got it! I'll keep that in mind." },
  { role: "user", content: "I'm an expert in React" },
  { role: "assistant", content: "Great, I'll tailor my explanations accordingly." },
];

describe("extractFactsFromConversation", () => {
  it("returns empty array when provider returns invalid JSON", async () => {
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({ content: "not json at all" }),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
    };
    const result = await extractFactsFromConversation(mockMessages, mockProvider as any);
    expect(result).toEqual([]);
  });

  it("returns empty array when provider throws", async () => {
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockRejectedValue(new Error("network error")),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
    };
    const result = await extractFactsFromConversation(mockMessages, mockProvider as any);
    expect(result).toEqual([]);
  });

  it("returns parsed facts from valid JSON response", async () => {
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify([
          { fact: "Prefers TypeScript", category: "preference" },
          { fact: "Expert in React", category: "skill" },
        ]),
      }),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
    };
    const result = await extractFactsFromConversation(mockMessages, mockProvider as any);
    expect(result).toHaveLength(2);
    expect(result[0].fact).toBe("Prefers TypeScript");
    expect(result[0].category).toBe("preference");
    expect(result[1].category).toBe("skill");
  });

  it("caps results at 10 facts", async () => {
    const manyFacts = Array.from({ length: 20 }, (_, i) => ({
      fact: `Fact ${i}`,
      category: "preference",
    }));
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({ content: JSON.stringify(manyFacts) }),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
    };
    const result = await extractFactsFromConversation(mockMessages, mockProvider as any);
    expect(result.length).toBeLessThanOrEqual(10);
  });

  it("skips entries that are not valid objects with fact string", async () => {
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify([
          { fact: "Valid fact", category: "skill" },
          { notAFact: "bad" },
          null,
          42,
        ]),
      }),
      chatWithTools: vi.fn(),
      chatStream: vi.fn(),
    };
    const result = await extractFactsFromConversation(mockMessages, mockProvider as any);
    expect(result).toHaveLength(1);
    expect(result[0].fact).toBe("Valid fact");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/fact-extractor.test.ts
```

Expected: FAIL — `Cannot find module '../src/session/fact-extractor.js'`

- [ ] **Step 3: Create `src/session/fact-extractor.ts`**

```typescript
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

export interface ExtractedFact {
  fact: string;
  category: string;
}

/**
 * Calls a low-tier LLM to extract user facts from the last N messages.
 * Returns up to 10 facts. Never throws — returns [] on any failure.
 */
export async function extractFactsFromConversation(
  messages: ChatMessage[],
  provider: ModelProvider,
  model?: string,
): Promise<ExtractedFact[]> {
  try {
    const relevant = messages
      .filter(m => m.role === "user" || m.role === "assistant")
      .slice(-20)
      .map(m => `${m.role.toUpperCase()}: ${(typeof m.content === "string" ? m.content : "").slice(0, 400)}`)
      .join("\n");

    const prompt = `Extract persistent facts about the user from this conversation. Return ONLY a JSON array, no markdown.

Conversation:
${relevant}

Return a JSON array of up to 10 facts (fewer is fine). Each entry:
{ "fact": "short statement about the user", "category": "preference" | "skill" | "personal" | "context" }

Rules:
- Only include facts that are stable and reusable across future conversations
- Skip transient task details (what file they edited, etc.)
- Skip facts already obvious from the conversation topic
- Return [] if there are no useful facts

Example: [{"fact":"Prefers concise responses","category":"preference"},{"fact":"Expert in TypeScript","category":"skill"}]`;

    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      model,
      { maxTokens: 300, temperature: 0 },
    );

    const raw = response.content.trim()
      .replace(/^```json\s*/i, "")
      .replace(/^```\s*/i, "")
      .replace(/\s*```$/, "");

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter((e): e is ExtractedFact =>
        e !== null &&
        typeof e === "object" &&
        typeof e.fact === "string" &&
        e.fact.trim().length > 0,
      )
      .map(e => ({ fact: e.fact.trim(), category: e.category ?? "context" }))
      .slice(0, 10);
  } catch (err) {
    log.engine.debug(`[FactExtractor] Extraction failed: ${err instanceof Error ? err.message : err}`);
    return [];
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/fact-extractor.test.ts
```

Expected: PASS — 5 passing

- [ ] **Step 5: Commit**

```bash
git add src/session/fact-extractor.ts
git add -f __tests__/fact-extractor.test.ts
git commit -m "feat(session): add extractFactsFromConversation() — LLM-based fact extraction"
```

---

## Task 4: Create `migrate.ts`

**Files:**
- Create: `src/session/migrate.ts`
- Create: `__tests__/migrate.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/migrate.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { migrateJsonSessionsToSQLite } from "../src/session/migrate.js";
import { SessionStore } from "../src/memory/store.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), "__tests__/fixtures/migrate-test");

let sessionStore: SessionStore;
let db: MemoryDatabase;

beforeEach(async () => {
  mkdirSync(join(TEST_DIR, "sessions"), { recursive: true });
  sessionStore = new SessionStore(TEST_DIR);
  db = new MemoryDatabase(TEST_DIR);
});

afterEach(() => {
  (db as any).db?.close?.();
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true, force: true });
});

describe("migrateJsonSessionsToSQLite", () => {
  it("migrates messages from JSON session to SQLite messages table", async () => {
    const session = sessionStore.createSession("owl1");
    session.messages = [
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi there!" },
    ];
    await sessionStore.saveSession(session);

    await migrateJsonSessionsToSQLite(sessionStore, db, "user1", "owl1");

    expect(db.messages.countSession(session.id)).toBe(2);
  });

  it("skips sessions already in SQLite", async () => {
    const session = sessionStore.createSession("owl1");
    session.messages = [{ role: "user", content: "Already migrated" }];
    await sessionStore.saveSession(session);

    // Pre-populate SQLite
    db.messages.append(session.id, "user1", "owl1", session.messages);

    await migrateJsonSessionsToSQLite(sessionStore, db, "user1", "owl1");

    // Still only 1 message — not duplicated
    expect(db.messages.countSession(session.id)).toBe(1);
  });

  it("does nothing if no JSON sessions exist", async () => {
    await expect(
      migrateJsonSessionsToSQLite(sessionStore, db, "user1", "owl1"),
    ).resolves.not.toThrow();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/migrate.test.ts
```

Expected: FAIL — `Cannot find module '../src/session/migrate.js'`

- [ ] **Step 3: Create `src/session/migrate.ts`**

```typescript
import type { SessionStore } from "../memory/store.js";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

/**
 * One-shot migration: reads all JSON session files and writes their messages
 * into the SQLite messages table. Skips sessions already in SQLite.
 * Called once at startup in OwlGateway constructor.
 */
export async function migrateJsonSessionsToSQLite(
  sessionStore: SessionStore,
  db: MemoryDatabase,
  userId: string,
  owlName: string,
): Promise<void> {
  let sessions: Awaited<ReturnType<SessionStore["listSessions"]>>;
  try {
    sessions = await sessionStore.listSessions();
  } catch {
    return; // No sessions directory yet — nothing to migrate
  }

  for (const session of sessions) {
    if (!session.messages?.length) continue;

    // Skip sessions already in SQLite
    if (db.messages.countSession(session.id) > 0) {
      log.engine.debug(`[Migration] Session ${session.id} already in SQLite — skipping`);
      continue;
    }

    try {
      db.messages.append(
        session.id,
        userId,
        session.metadata?.owlName ?? owlName,
        session.messages,
      );
      log.engine.info(`[Migration] Migrated session ${session.id} — ${session.messages.length} messages`);
    } catch (err) {
      log.engine.warn(
        `[Migration] Failed to migrate session ${session.id}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/migrate.test.ts
```

Expected: PASS — 3 passing

- [ ] **Step 5: Commit**

```bash
git add src/session/migrate.ts
git add -f __tests__/migrate.test.ts
git commit -m "feat(session): add migrateJsonSessionsToSQLite() — one-shot JSON→SQLite migration"
```

---

## Task 5: Create `SessionService`

**Files:**
- Create: `src/session/service.ts`
- Create: `__tests__/session-service.test.ts`

- [ ] **Step 1: Write failing tests**

Create `__tests__/session-service.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SessionService } from "../src/session/service.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

// Mock embed — no fastembed model needed in unit tests
vi.mock("../src/pellets/embedder.js", () => ({
  embed: vi.fn().mockResolvedValue(null),
  initEmbedder: vi.fn().mockResolvedValue(undefined),
}));

const TEST_DIR = join(process.cwd(), "__tests__/fixtures/session-service-test");
let db: MemoryDatabase;
let service: SessionService;

const mockCompressor = {
  compress: vi.fn().mockResolvedValue(null),
  buildContext: vi.fn().mockReturnValue(""),
};

const mockUserMemoryStore = {
  retrieve: vi.fn().mockResolvedValue([]),
  add: vi.fn().mockResolvedValue(undefined),
};

beforeEach(() => {
  mkdirSync(TEST_DIR, { recursive: true });
  db = new MemoryDatabase(TEST_DIR);
  service = new SessionService(
    db,
    mockCompressor as any,
    mockUserMemoryStore as any,
    undefined,
    { name: "mock", chat: vi.fn(), chatWithTools: vi.fn(), chatStream: vi.fn() } as any,
    undefined,
  );
});

afterEach(() => {
  (db as any).db?.close?.();
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true, force: true });
  vi.clearAllMocks();
});

describe("SessionService.getOrCreate", () => {
  it("creates a new session when none exists", async () => {
    const session = await service.getOrCreate("sess-new", "user1", "owl1");
    expect(session.id).toBe("sess-new");
    expect(session.messages).toEqual([]);
  });

  it("loads existing messages from SQLite", async () => {
    db.messages.append("sess-existing", "user1", "owl1", [
      { role: "user", content: "Hello" },
    ]);
    const session = await service.getOrCreate("sess-existing", "user1", "owl1");
    expect(session.messages).toHaveLength(1);
    expect(session.messages[0].content).toBe("Hello");
  });

  it("returns same session from cache on second call", async () => {
    const s1 = await service.getOrCreate("sess-cache", "user1", "owl1");
    const s2 = await service.getOrCreate("sess-cache", "user1", "owl1");
    expect(s1).toBe(s2); // same object reference
  });
});

describe("SessionService.addMessages", () => {
  it("appends messages to SQLite", async () => {
    await service.getOrCreate("sess-add", "user1", "owl1");
    await service.addMessages("sess-add", [
      { role: "user", content: "Hello" },
      { role: "assistant", content: "Hi" },
    ]);
    expect(db.messages.countSession("sess-add")).toBe(2);
  });

  it("enforces 300-message rolling window", async () => {
    const MAX = 300;
    // Pre-populate 300 messages
    const msgs = Array.from({ length: MAX }, (_, i) => ({
      role: (i % 2 === 0 ? "user" : "assistant") as "user" | "assistant",
      content: `msg ${i}`,
    }));
    db.messages.append("sess-window", "user1", "owl1", msgs);
    // Simulate a summary covering seq 0-19 so covered branch fires
    db.summaries.add({
      sessionId: "sess-window", userId: "user1", owlName: "owl1",
      fromSeq: 0, toSeq: 19, messageCount: 20,
      summaryText: "test summary", keyFacts: [], decisions: [],
      failedApproaches: [], openQuestions: [], tokensSaved: 0,
    });

    await service.getOrCreate("sess-window", "user1", "owl1");
    await service.addMessages("sess-window", [
      { role: "user", content: "one more" },
    ]);

    expect(db.messages.countSession("sess-window")).toBeLessThanOrEqual(MAX);
  });
});

describe("SessionService.isGreetingPattern", () => {
  it("matches hello", () => {
    expect(SessionService.isGreetingPattern("hello")).toBe(true);
  });

  it("matches hi with trailing space", () => {
    expect(SessionService.isGreetingPattern("hi there")).toBe(true);
  });

  it("does not match mid-conversation text", () => {
    expect(SessionService.isGreetingPattern("Can you help me with TypeScript?")).toBe(false);
  });

  it("is case-insensitive", () => {
    expect(SessionService.isGreetingPattern("HELLO")).toBe(true);
  });
});

describe("SessionService.buildContext", () => {
  it("returns empty strings when no messages exist", async () => {
    await service.getOrCreate("sess-ctx", "user1", "owl1");
    const ctx = await service.buildContext("sess-ctx", "user1", "anything");
    expect(ctx.summaryBlock).toBe("");
    expect(ctx.recentFacts).toBe("");
    expect(ctx.recentMessages).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/session-service.test.ts
```

Expected: FAIL — `Cannot find module '../src/session/service.js'`

- [ ] **Step 3: Create `src/session/service.ts`**

```typescript
import type { ChatMessage } from "../providers/base.js";
import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { MessageCompressor } from "../memory/compressor.js";
import type { Session } from "../memory/store.js";
import type { UserMemoryStore } from "./user-memory-store.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ProviderRegistry } from "../providers/registry.js";
import { extractFactsFromConversation } from "./fact-extractor.js";
import { log } from "../logger.js";

const MAX_SESSION_MESSAGES = 300;
const SESSION_TIMEOUT_MS = 2 * 60 * 60 * 1000;
const EVICTION_INTERVAL_MS = 30 * 60 * 1000;
const MIN_MESSAGES_FOR_EXTRACTION = 4;

interface CacheEntry {
  session: Session;
  userId: string;
  owlName: string;
  lastActivity: number;
}

export interface SessionContext {
  summaryBlock: string;
  recentFacts: string;
  recentMessages: ChatMessage[];
}

export class SessionService {
  private cache = new Map<string, CacheEntry>();
  private evictionTimer: NodeJS.Timeout;

  constructor(
    private db: MemoryDatabase,
    private compressor: MessageCompressor,
    private userMemoryStore: UserMemoryStore,
    private intelligence: IntelligenceRouter | undefined,
    private provider: ModelProvider,
    private providerRegistry: ProviderRegistry | undefined,
  ) {
    this.evictionTimer = setInterval(() => this.evictStale(), EVICTION_INTERVAL_MS);
    this.evictionTimer.unref?.();
  }

  /** Load or create session; populates internal cache. */
  async getOrCreate(sessionId: string, userId: string, owlName: string): Promise<Session> {
    const cached = this.cache.get(sessionId);
    if (cached) {
      cached.lastActivity = Date.now();
      return cached.session;
    }

    // Try SQLite first
    const messages = this.db.messages.getSession(sessionId);
    const session: Session = {
      id: sessionId,
      messages,
      metadata: {
        owlName,
        startedAt: Date.now(),
        lastUpdatedAt: Date.now(),
      },
    };

    this.cache.set(sessionId, { session, userId, owlName, lastActivity: Date.now() });
    return session;
  }

  /** Append messages to SQLite; enforce 300-message rolling window. */
  async addMessages(sessionId: string, messages: ChatMessage[]): Promise<void> {
    if (messages.length === 0) return;
    const entry = this.cache.get(sessionId);
    if (!entry) return;

    this.db.messages.append(sessionId, entry.userId, entry.owlName, messages);
    entry.lastActivity = Date.now();

    const count = this.db.messages.countSession(sessionId);
    if (count <= MAX_SESSION_MESSAGES) return;

    const overflow = count - MAX_SESSION_MESSAGES;
    const oldest = this.db.messages.getOldestN(sessionId, overflow);
    if (oldest.length === 0) return;

    const fromSeq = oldest[0].seq;
    const toSeq = oldest[oldest.length - 1].seq;

    // Check if summaries table already covers this range
    const covered = this.db.rawDb.prepare(
      "SELECT 1 FROM summaries WHERE session_id = ? AND from_seq <= ? AND to_seq >= ? LIMIT 1"
    ).get(sessionId, fromSeq, toSeq);

    if (!covered) {
      const fullMessages = this.db.messages.getSession(sessionId).slice(0, overflow);
      await this.compressor.compress(
        sessionId, entry.userId, entry.owlName, fullMessages,
      );
    }

    this.db.messages.deleteByIds(oldest.map(m => m.id));
  }

  /** Assemble context: compression summary + top-3 semantic user facts + last 50 raw messages. */
  async buildContext(sessionId: string, userId: string, lastUserText: string): Promise<SessionContext> {
    const summaryBlock = this.compressor.buildContext(sessionId, []);
    const recentMessages = this.db.messages.getRecent(sessionId, 50);

    let recentFacts = "";
    try {
      const factLines = await this.userMemoryStore.retrieve(userId, lastUserText, 3);
      if (factLines.length > 0) {
        recentFacts = `<user_memory>\n${factLines.map(f => `- ${f}`).join("\n")}\n</user_memory>`;
      }
    } catch {
      // Non-fatal
    }

    return { summaryBlock, recentFacts, recentMessages };
  }

  /** Get userId for a cached session (used by endSession hooks). */
  getUserId(sessionId: string): string | undefined {
    return this.cache.get(sessionId)?.userId;
  }

  /** Remove session from cache (called by core.ts after endSession). */
  evictFromCache(sessionId: string): void {
    this.cache.delete(sessionId);
  }

  /** Evict sessions inactive for SESSION_TIMEOUT_MS. Returns evicted session IDs. */
  evictStale(): string[] {
    const now = Date.now();
    const evicted: string[] = [];
    for (const [key, entry] of this.cache) {
      if (now - entry.lastActivity > SESSION_TIMEOUT_MS) {
        this.cache.delete(key);
        evicted.push(key);
      }
    }
    return evicted;
  }

  /** Static utility: true if text matches a greeting pattern. */
  static isGreetingPattern(text: string): boolean {
    return /^(hi|hello|hey|good morning|good afternoon|howdy|yo|sup)\b/i.test(text.trim());
  }

  /** Async fact extraction — called from core.ts endSession() after episodic block. */
  async extractAndStoreFacts(
    sessionId: string,
    userId: string,
    owlName: string,
    messages: ChatMessage[],
  ): Promise<void> {
    if (messages.length < MIN_MESSAGES_FOR_EXTRACTION) return;

    const { provider: providerName, model } = this.intelligence?.resolve("extraction")
      ?? { provider: this.provider.name, model: "" };

    let extractProvider = this.provider;
    if (this.providerRegistry && providerName !== this.provider.name) {
      try { extractProvider = this.providerRegistry.get(providerName); } catch {}
    }

    const facts = await extractFactsFromConversation(messages, extractProvider, model || undefined);
    for (const f of facts) {
      await this.userMemoryStore.add(userId, f.fact, f.category, owlName);
    }
    log.engine.info(`[endSession:facts] ✓ extracted ${facts.length} facts for user ${userId}`);
  }

  destroy(): void {
    clearInterval(this.evictionTimer);
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/session-service.test.ts
```

Expected: PASS — 9 passing

- [ ] **Step 5: Commit**

```bash
git add src/session/service.ts
git add -f __tests__/session-service.test.ts
git commit -m "feat(session): add SessionService — lifecycle, rolling window, context, fact extraction"
```

---

## Task 6: Update `GatewayContext` types

**Files:**
- Modify: `src/gateway/types.ts`

- [ ] **Step 1: Open `src/gateway/types.ts` and find the `GatewayContext` interface**

The file at `src/gateway/types.ts` has a large `GatewayContext` interface. Find the block near the bottom that has `intelligence?: IntelligenceRouter` (added in the IntelligenceRouter implementation). Add the two new fields immediately after it.

Current end of GatewayContext (look for the `intelligence?` field):
```typescript
  // ─── Intelligence Router (tiered model routing) ───────────────
  intelligence?: import("../intelligence/router.js").IntelligenceRouter;
```

- [ ] **Step 2: Add `sessionService` and `userMemoryStore` fields**

After the `intelligence?` field, add:

```typescript
  // ─── Session Service (unified session lifecycle) ───────────────
  sessionService?: import("../session/service.js").SessionService;
  userMemoryStore?: import("../session/user-memory-store.js").UserMemoryStore;
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
npm run build 2>&1 | head -30
```

Expected: no new errors

- [ ] **Step 4: Commit**

```bash
git add src/gateway/types.ts
git commit -m "feat(gateway): add sessionService + userMemoryStore to GatewayContext"
```

---

## Task 7: Wire `SessionService` into `core.ts` and `context-builder.ts`

**Files:**
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/context-builder.ts`

This task has 8 sub-steps. Complete each one in sequence, running `npm run build` after each to catch type errors early.

### Step 7a: Import `SessionService` and `UserMemoryStore` in `core.ts`

- [ ] **Step 1: Add imports at the top of `src/gateway/core.ts`**

Find the import block near line 22 (`import { IntelligenceRouter }`). Add after it:

```typescript
import { SessionService } from "../session/service.js";
import { UserMemoryStore } from "../session/user-memory-store.js";
import { migrateJsonSessionsToSQLite } from "../session/migrate.js";
```

### Step 7b: Instantiate `SessionService` and `UserMemoryStore` in the constructor

- [ ] **Step 2: Find where `IntelligenceRouter` is instantiated (around line 344)**

The block looks like:
```typescript
    // ─── Intelligence Router (tiered model routing) ────────────
    if (ctx.config.intelligence) {
      ctx.intelligence = new IntelligenceRouter( ... );
      log.engine.info("[IntelligenceRouter] Tiered model routing active");
    }
```

Add the following block AFTER the `ctx.db` and `ctx.compressor` initialization (around line 466, after `"[memory] MessageCompressor initialized"`):

```typescript
    // ─── Session Service (unified session lifecycle) ───────────
    if (ctx.db && ctx.compressor) {
      ctx.userMemoryStore = new UserMemoryStore(ctx.db);
      ctx.sessionService = new SessionService(
        ctx.db,
        ctx.compressor,
        ctx.userMemoryStore,
        ctx.intelligence,
        ctx.provider,
        ctx.providerRegistry,
      );
      log.engine.info("[SessionService] Session lifecycle + rolling window active");

      // One-shot JSON→SQLite migration (fire-and-forget, non-blocking)
      migrateJsonSessionsToSQLite(
        ctx.sessionStore,
        ctx.db,
        "system",               // placeholder userId for migrated sessions
        ctx.owl.persona.name,
      ).catch(err =>
        log.engine.warn(`[SessionService] JSON migration failed: ${err instanceof Error ? err.message : err}`),
      );
    }
```

### Step 7c: Update `getOrCreateSession()` to delegate to `SessionService`

- [ ] **Step 3: Replace the body of `getOrCreateSession()` (lines ~3372–3391)**

The current method at line 3372:
```typescript
  private async getOrCreateSession(message: GatewayMessage): Promise<Session> {
    const key = message.sessionId;
    const cached = this.sessions.get(key);
    if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
      cached.lastActivity = Date.now();
      return cached.session;
    }
    let session = await this.ctx.sessionStore.loadSession(key);
    if (!session) {
      session = this.ctx.sessionStore.createSession(this.ctx.owl.persona.name);
      session.id = key;
      await this.ctx.sessionStore.saveSession(session);
    }
    this.sessions.set(key, { session, lastActivity: Date.now() });
    return session;
  }
```

Replace the body with:

```typescript
  private async getOrCreateSession(message: GatewayMessage): Promise<Session> {
    const key = message.sessionId;
    if (this.ctx.sessionService) {
      const session = await this.ctx.sessionService.getOrCreate(
        key,
        message.userId,
        this.ctx.owl.persona.name,
      );
      this.sessions.set(key, { session, lastActivity: Date.now() });
      return session;
    }
    // Fallback: no SessionService (tests, lightweight envs)
    const cached = this.sessions.get(key);
    if (cached && Date.now() - cached.lastActivity <= SESSION_TIMEOUT_MS) {
      cached.lastActivity = Date.now();
      return cached.session;
    }
    let session = await this.ctx.sessionStore.loadSession(key);
    if (!session) {
      session = this.ctx.sessionStore.createSession(this.ctx.owl.persona.name);
      session.id = key;
      await this.ctx.sessionStore.saveSession(session);
    }
    this.sessions.set(key, { session, lastActivity: Date.now() });
    return session;
  }
```

### Step 7d: Update `saveSession()` to delegate persistence to `SessionService`

- [ ] **Step 4: Replace the body of `saveSession()` (lines ~3393–3425)**

The current private method at line 3393:
```typescript
  private async saveSession(
    session: Session,
    userText: string,
    newMessages: ChatMessage[],
    userAlreadySaved = false,
    finalContent?: string,
  ): Promise<void> {
    const snapshot = session.messages.slice();
    try {
      if (!userAlreadySaved) {
        session.messages.push({ role: "user", content: userText });
      }
      for (const msg of newMessages) {
        session.messages.push(msg);
      }
      if (finalContent?.trim()) {
        session.messages.push({ role: "assistant", content: finalContent });
      }
      if (session.messages.length > MAX_SESSION_HISTORY) {
        session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
      }
      await this.ctx.sessionStore.saveSession(session);
      const key = session.id;
      const cached = this.sessions.get(key);
      if (cached) cached.lastActivity = Date.now();
    } catch (err) {
      session.messages = snapshot;
      log.engine.error(
        `[Session] Save failed, rolled back: ${err instanceof Error ? err.message : err}`,
      );
      throw err;
    }
  }
```

Replace the body with:

```typescript
  private async saveSession(
    session: Session,
    userText: string,
    newMessages: ChatMessage[],
    userAlreadySaved = false,
    finalContent?: string,
  ): Promise<void> {
    const snapshot = session.messages.slice();
    try {
      const added: ChatMessage[] = [];
      if (!userAlreadySaved) {
        const m: ChatMessage = { role: "user", content: userText };
        session.messages.push(m);
        added.push(m);
      }
      for (const msg of newMessages) {
        session.messages.push(msg);
        added.push(msg);
      }
      if (finalContent?.trim()) {
        const m: ChatMessage = { role: "assistant", content: finalContent };
        session.messages.push(m);
        added.push(m);
      }

      if (this.ctx.sessionService && added.length > 0) {
        await this.ctx.sessionService.addMessages(session.id, added);
      } else {
        // Fallback for tests / non-DB envs
        if (session.messages.length > MAX_SESSION_HISTORY) {
          session.messages = session.messages.slice(-MAX_SESSION_HISTORY);
        }
        await this.ctx.sessionStore.saveSession(session);
      }
      const cached = this.sessions.get(session.id);
      if (cached) cached.lastActivity = Date.now();
    } catch (err) {
      session.messages = snapshot;
      log.engine.error(
        `[Session] Save failed, rolled back: ${err instanceof Error ? err.message : err}`,
      );
      throw err;
    }
  }
```

### Step 7e: Update `evictStaleSessions()` to use `SessionService`

- [ ] **Step 5: Find `evictStaleSessions()` at line ~3433 and add SessionService cache cleanup**

Find this line inside `evictStaleSessions()`:
```typescript
        this.sessions.delete(key);
        this.stuckStreak.delete(key);
        this.attemptLogs.delete(key);
```

Add one line after `this.sessions.delete(key)`:

```typescript
        this.ctx.sessionService?.evictFromCache(key);
```

The result should look like:
```typescript
        this.sessions.delete(key);
        this.ctx.sessionService?.evictFromCache(key);
        this.stuckStreak.delete(key);
        this.attemptLogs.delete(key);
```

### Step 7f: Add greeting-reset check in `handleCore()`

- [ ] **Step 6: Find the start of `handleCore()` at line ~811**

The current beginning:
```typescript
  private async handleCore(
    message: GatewayMessage,
    callbacks: GatewayCallbacks,
  ): Promise<GatewayResponse> {
    const session = await this.getOrCreateSession(message);
```

Insert greeting-reset logic BEFORE `const session = await this.getOrCreateSession(message)`:

```typescript
    // Greeting-reset: if the message is a greeting and the current session
    // has ≥ 4 messages, fire endSession() first so facts/episodic/learning
    // all run at the natural boundary before starting a fresh session.
    if (this.ctx.sessionService && SessionService.isGreetingPattern(message.text)) {
      const msgCount = this.ctx.db?.messages.countSession(message.sessionId) ?? 0;
      if (msgCount >= 4) {
        await this.endSession(message.sessionId).catch(err =>
          log.engine.warn(`[greeting-reset] endSession failed: ${err instanceof Error ? err.message : err}`)
        );
      }
    }

    const session = await this.getOrCreateSession(message);
```

### Step 7g: Add fact extraction step in `endSession()`

- [ ] **Step 7: Find `endSession()` at line ~2050. Locate the episodic block ending around line 2073**

The episodic block ends at:
```typescript
      } catch (err) {
        log.engine.warn(
          `[endSession:episodic] Extraction failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
```

Insert the following block IMMEDIATELY after that closing `}`:

```typescript
    // Async fact extraction → writes to facts table via UserMemoryStore
    if (this.ctx.sessionService && messages.length >= 4) {
      const userId = this.ctx.sessionService.getUserId(sessionId);
      if (userId) {
        this.ctx.sessionService.extractAndStoreFacts(
          sessionId,
          userId,
          this.ctx.owl.persona.name,
          messages,
        ).catch(err =>
          log.engine.warn(`[endSession:facts] ✗ failed: ${err instanceof Error ? err.message : err}`)
        );
      }
    }
```

### Step 7h: Inject user memory context in `context-builder.ts`

- [ ] **Step 8: Open `src/gateway/handlers/context-builder.ts`. Find the `compressionSummaryContext` block around line 215**

The current block:
```typescript
    let compressionSummaryContext = "";
    if (this.ctx.compressor && this.ctx.db) {
      try {
        compressionSummaryContext = this.ctx.compressor.buildContext(
          session.id,
          session.messages,
        );
      } catch {
        // Non-fatal
      }
    }
```

Add a new `userMemoryContext` block IMMEDIATELY after this block:

```typescript
    let userMemoryContext = "";
    if (this.ctx.userMemoryStore && userId) {
      try {
        const facts = await this.ctx.userMemoryStore.retrieve(userId, userMessage, 3);
        if (facts.length > 0) {
          userMemoryContext = `<user_memory>\n${facts.map(f => `- ${f}`).join("\n")}\n</user_memory>`;
        }
      } catch {
        // Non-fatal
      }
    }
```

Then find the `enrichedMemoryContext` array assembly (around line 640). Find the line:
```typescript
      compressionSummaryContext, // L2: compressed history of older messages
```

Add `userMemoryContext` on the line AFTER it:
```typescript
      compressionSummaryContext, // L2: compressed history of older messages
      userMemoryContext,          // L2.5: persistent user facts (cross-session)
```

### Step 7i: Final build check and run tests

- [ ] **Step 9: Build and run full test suite**

```bash
npm run build 2>&1 | tail -20
```

Expected: no errors

```bash
npm test 2>&1 | tail -30
```

Expected: all tests pass (same count as before)

- [ ] **Step 10: Commit**

```bash
git add src/gateway/core.ts src/gateway/handlers/context-builder.ts
git commit -m "feat(gateway): wire SessionService — SQLite persistence, greeting reset, fact extraction, user memory context"
```

---

## Task 8: Delete dead `session-manager.ts` and move `SessionCache` inline

`src/gateway/handlers/session-manager.ts` is dead code: `SessionManager` is instantiated (line 55 in core.ts: `import { SessionManager }`) but never called. `SessionCache` is imported and used as the type for `this.sessions`. Both must be handled before deleting the file.

**Files:**
- Modify: `src/gateway/core.ts`
- Delete: `src/gateway/handlers/session-manager.ts`

- [ ] **Step 1: Define `SessionCache` inline in `src/gateway/core.ts`**

Find this import line in `core.ts` (around line 55):
```typescript
import { SessionManager } from "./handlers/session-manager.js";
```

Replace the entire line with this inline interface (placed just before the class declaration or near the top constants):

```typescript
// SessionCache was in session-manager.ts (now deleted — SessionService owns persistence)
interface SessionCache {
  session: import("../memory/store.js").Session;
  lastActivity: number;
}
```

Then delete the `import { SessionManager }` line entirely.

- [ ] **Step 2: Verify no other references to `SessionManager` remain in `core.ts`**

```bash
grep -n "SessionManager" src/gateway/core.ts
```

Expected: no output

- [ ] **Step 3: Build to verify no compile errors**

```bash
npm run build 2>&1 | tail -10
```

Expected: clean build

- [ ] **Step 4: Delete `src/gateway/handlers/session-manager.ts`**

```bash
rm src/gateway/handlers/session-manager.ts
```

- [ ] **Step 5: Build again to confirm no broken imports**

```bash
npm run build 2>&1 | tail -10
```

Expected: clean build

- [ ] **Step 6: Run full test suite**

```bash
npm test 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/gateway/core.ts
git rm src/gateway/handlers/session-manager.ts
git commit -m "refactor(gateway): delete dead SessionManager; move SessionCache inline to core.ts"
```

---

## Self-Review Checklist

After all tasks complete, verify:

| Spec requirement | Task that covers it |
|-----------------|---------------------|
| `SessionManager` dead code removed | Task 8 |
| SQLite single source of truth for messages | Task 5 + Task 7c/d |
| JSON→SQLite migration on startup | Task 4 + Task 7b |
| `extractFromSession()` never called → facts table empty | Task 3 + Task 7g |
| 50-msg silent drop → 300-msg with summary-before-drop | Task 1 + Task 5 + Task 7d |
| Smart end detection (greeting reset) | Task 5 + Task 7f |
| Async fact extraction at session end | Task 3 + Task 5 + Task 7g |
| `UserMemoryStore` per-turn context injection | Task 2 + Task 7h |
| `GatewayContext` updated with new fields | Task 6 |

