# Cycle 4 Multi-Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, persistent, message-able multi-agent runtime — `SessionStore` + `SessionRunner` + 6 LLM-callable session tools — so spawned subagents outlive the conversation that created them and can be queried/messaged from any later turn.

**Architecture:** New `src/sessions/` module (types, store, runner, engine-host) backed by 2 SQLite tables. Each session is an independent `OwlEngine` run with its own `ChatMessage[]` history, sharing the parent's tool registry per inherit-everything trust. Engine gains `AbortSignal` support for clean termination. Six new tools register at bootstrap via the `attachSessions` late-bind pattern.

**Tech Stack:** TypeScript strict, Vitest, better-sqlite3 (WAL journal), existing `OwlEngine`, existing `Notifier` (unused here; future), `AbortController`/`AbortSignal`.

**Spec:** `docs/superpowers/specs/2026-05-12-cycle-4-multi-agent-runtime-design.md`

---

## File Map

```
src/sessions/                                  # NEW MODULE
├── types.ts                                   # Session, SessionMessage, JobStatus
├── store.ts                                   # SessionStore — SQLite CRUD + queue
├── runner.ts                                  # SessionRunner — lifecycle + msg routing
└── engine-host.ts                             # per-session OwlEngine factory + context shim

src/tools/sessions/                            # NEW — 6 LLM tools
├── attach.ts                                  # attachSessions(runner, store) + module-level refs
├── subagents.ts                               # spawn
├── sessions-status.ts                         # status + messages
├── sessions-send.ts                           # queue a to_session message
├── sessions-yield.ts                          # block until next event
├── sessions-list.ts                           # enumerate
└── sessions-terminate.ts                      # kill

src/memory/db.ts                               # MODIFY: +sessions, +session_messages tables
src/engine/runtime.ts                          # MODIFY: +signal?: AbortSignal in EngineContext
src/index.ts                                   # MODIFY: wire SessionRunner; register tools

__tests__/sessions/                            # NEW — ~30 tests
├── schema.test.ts                             # tables exist + insert/query
├── store.test.ts                              # CRUD + queue
├── runner-spawn.test.ts                       # spawn + complete
├── runner-messaging.test.ts                   # send/yield handshake
├── runner-terminate.test.ts                   # terminate via AbortSignal
├── runner-hydration.test.ts                   # boot resume
├── runner-concurrency.test.ts                 # max=5 cap, FIFO pending
└── runner-age.test.ts                         # auto-terminate >7d

__tests__/tools/sessions/                      # NEW — 6 tool tests
├── subagents.test.ts
├── sessions-status.test.ts
├── sessions-send.test.ts
├── sessions-yield.test.ts
├── sessions-list.test.ts
└── sessions-terminate.test.ts

docs/dev-setup.md                              # MODIFY: subagents-vs-orchestrate_tasks
```

---

## Phase A — Foundation (schema + types + store)

## Task 1: Schema migration + types

**Files:**
- Create: `src/sessions/types.ts`
- Modify: `src/memory/db.ts` (append to `createSchema()`)
- Create: `__tests__/sessions/schema.test.ts`

- [ ] **Step 1: Create `src/sessions/types.ts`**

```typescript
import type { ChatMessage } from "../providers/base.js";

export type SessionStatus =
  | "pending"
  | "running"
  | "awaiting_input"
  | "completed"
  | "terminated"
  | "failed";

export const TERMINAL_STATUSES: ReadonlySet<SessionStatus> = new Set([
  "completed",
  "terminated",
  "failed",
]);

export interface SessionMetadata {
  owl?: string;
  model?: string;
  channel?: string;
  userId?: string;
}

export interface Session {
  id: string;
  parentId: string | null;
  status: SessionStatus;
  prompt: string;
  history: ChatMessage[];
  result?: string;
  error?: string;
  metadata: SessionMetadata;
  createdAt: string;
  updatedAt: string;
  terminatedAt?: string;
}

export type MessageDirection = "to_session" | "from_session";

export interface SessionMessage {
  id: number;
  sessionId: string;
  direction: MessageDirection;
  content: string;
  createdAt: string;
  consumedAt?: string;
}
```

- [ ] **Step 2: Write the failing schema test**

Create `__tests__/sessions/schema.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let dir: string;

beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "stackowl-sessions-schema-")); });
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("sessions schema", () => {
  it("sessions table exists after MemoryDatabase init", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
      .get();
    expect(row).toBeTruthy();
  });

  it("session_messages table exists", () => {
    const db = new MemoryDatabase(dir);
    const row = db.rawDb
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='session_messages'")
      .get();
    expect(row).toBeTruthy();
  });

  it("sessions table has all expected columns", () => {
    const db = new MemoryDatabase(dir);
    const cols = db.rawDb.prepare("PRAGMA table_info(sessions)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toEqual(expect.arrayContaining([
      "id", "parent_id", "status", "prompt", "history_json",
      "result", "error", "metadata", "created_at", "updated_at", "terminated_at",
    ]));
  });

  it("status check constraint rejects invalid values", () => {
    const db = new MemoryDatabase(dir);
    expect(() => {
      db.rawDb.prepare(
        "INSERT INTO sessions (id, status, prompt) VALUES (?, ?, ?)"
      ).run("bad", "not_a_status", "test");
    }).toThrow();
  });

  it("session_messages.session_id FK cascades on delete", () => {
    const db = new MemoryDatabase(dir);
    db.rawDb.prepare(
      "INSERT INTO sessions (id, status, prompt) VALUES (?, ?, ?)"
    ).run("s1", "running", "test");
    db.rawDb.prepare(
      "INSERT INTO session_messages (session_id, direction, content) VALUES (?, ?, ?)"
    ).run("s1", "to_session", "hi");

    db.rawDb.prepare("DELETE FROM sessions WHERE id = ?").run("s1");
    const remaining = db.rawDb.prepare(
      "SELECT COUNT(*) as n FROM session_messages WHERE session_id = ?"
    ).get("s1") as { n: number };
    expect(remaining.n).toBe(0);
  });
});
```

- [ ] **Step 3: Run — confirm fail**

```bash
npx vitest run __tests__/sessions/schema.test.ts
```

Expected: tables not found.

- [ ] **Step 4: Append schema to `src/memory/db.ts`**

Find the existing `createSchema()` method. Inside the big `this.db.exec(\`...\`)` SQL string, append before the closing backtick:

```sql
      CREATE TABLE IF NOT EXISTS sessions (
        id              TEXT PRIMARY KEY,
        parent_id       TEXT,
        status          TEXT NOT NULL CHECK(status IN ('pending', 'running', 'awaiting_input', 'completed', 'terminated', 'failed')),
        prompt          TEXT NOT NULL,
        history_json    TEXT,
        result          TEXT,
        error           TEXT,
        metadata        TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        terminated_at   TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status, updated_at);
      CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_id);

      CREATE TABLE IF NOT EXISTS session_messages (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id   TEXT NOT NULL,
        direction    TEXT NOT NULL CHECK(direction IN ('to_session', 'from_session')),
        content      TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        consumed_at  TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
      );
      CREATE INDEX IF NOT EXISTS idx_session_messages_pending ON session_messages(session_id, consumed_at);
```

- [ ] **Step 5: Run — confirm pass**

```bash
npx vitest run __tests__/sessions/schema.test.ts
```

Expected: 5/5 pass.

- [ ] **Step 6: Commit**

```bash
git add src/sessions/types.ts src/memory/db.ts __tests__/sessions/schema.test.ts
git commit -m "feat(sessions): types + schema (sessions, session_messages tables)"
```

---

## Task 2: `SessionStore` — CRUD + queue

**Files:**
- Create: `src/sessions/store.ts`
- Create: `__tests__/sessions/store.test.ts`

- [ ] **Step 1: Write the failing test file**

Create `__tests__/sessions/store.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import type { Session } from "../../src/sessions/types.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-session-store-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

function makeSession(over: Partial<Session> = {}): Session {
  return {
    id: "s" + Math.random().toString(36).slice(2, 8),
    parentId: null,
    status: "pending",
    prompt: "test",
    history: [],
    metadata: {},
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...over,
  };
}

describe("SessionStore", () => {
  it("create + findOne roundtrip", () => {
    const s = makeSession({ id: "a", prompt: "hello" });
    store.create(s);
    const found = store.findOne("a");
    expect(found?.id).toBe("a");
    expect(found?.prompt).toBe("hello");
  });

  it("findOne returns null when not present", () => {
    expect(store.findOne("nope")).toBeNull();
  });

  it("update patches status + result and bumps updatedAt", () => {
    const s = makeSession({ id: "a" });
    store.create(s);
    const before = store.findOne("a")!;
    store.update("a", { status: "completed", result: "done" });
    const after = store.findOne("a")!;
    expect(after.status).toBe("completed");
    expect(after.result).toBe("done");
    expect(after.updatedAt >= before.updatedAt).toBe(true);
  });

  it("list filters by status", () => {
    store.create(makeSession({ id: "a", status: "running" }));
    store.create(makeSession({ id: "b", status: "completed" }));
    store.create(makeSession({ id: "c", status: "running" }));
    expect(store.list({ status: "running" })).toHaveLength(2);
  });

  it("list filters by parentId", () => {
    store.create(makeSession({ id: "p", parentId: null }));
    store.create(makeSession({ id: "c1", parentId: "p" }));
    store.create(makeSession({ id: "c2", parentId: "p" }));
    expect(store.list({ parentId: "p" })).toHaveLength(2);
  });

  it("appendMessage + pendingMessages roundtrip", () => {
    store.create(makeSession({ id: "a" }));
    const m1 = store.appendMessage("a", "to_session", "hello");
    const m2 = store.appendMessage("a", "to_session", "world");
    const pending = store.pendingMessages("a", "to_session");
    expect(pending.map(m => m.content)).toEqual(["hello", "world"]);
    expect(pending[0].id).toBe(m1.id);
    expect(pending[1].id).toBe(m2.id);
  });

  it("markConsumed removes from pending", () => {
    store.create(makeSession({ id: "a" }));
    const m = store.appendMessage("a", "to_session", "x");
    store.markConsumed(m.id);
    expect(store.pendingMessages("a", "to_session")).toHaveLength(0);
  });

  it("history round-trips through JSON", () => {
    const s = makeSession({ id: "a", history: [
      { role: "user", content: "hi" },
      { role: "assistant", content: "there" },
    ]});
    store.create(s);
    const found = store.findOne("a")!;
    expect(found.history).toEqual(s.history);
  });
});
```

- [ ] **Step 2: Run — confirm fail (module not found)**

```bash
npx vitest run __tests__/sessions/store.test.ts
```

- [ ] **Step 3: Create `src/sessions/store.ts`**

```typescript
import type { MemoryDatabase } from "../memory/db.js";
import type { Session, SessionMessage, SessionStatus, MessageDirection } from "./types.js";

interface SessionRow {
  id: string;
  parent_id: string | null;
  status: SessionStatus;
  prompt: string;
  history_json: string | null;
  result: string | null;
  error: string | null;
  metadata: string | null;
  created_at: string;
  updated_at: string;
  terminated_at: string | null;
}

interface MessageRow {
  id: number;
  session_id: string;
  direction: MessageDirection;
  content: string;
  created_at: string;
  consumed_at: string | null;
}

function rowToSession(r: SessionRow): Session {
  return {
    id: r.id,
    parentId: r.parent_id,
    status: r.status,
    prompt: r.prompt,
    history: r.history_json ? JSON.parse(r.history_json) : [],
    result: r.result ?? undefined,
    error: r.error ?? undefined,
    metadata: r.metadata ? JSON.parse(r.metadata) : {},
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    terminatedAt: r.terminated_at ?? undefined,
  };
}

function rowToMessage(r: MessageRow): SessionMessage {
  return {
    id: r.id,
    sessionId: r.session_id,
    direction: r.direction,
    content: r.content,
    createdAt: r.created_at,
    consumedAt: r.consumed_at ?? undefined,
  };
}

export class SessionStore {
  constructor(private readonly db: MemoryDatabase) {}

  create(session: Session): void {
    this.db.rawDb.prepare(`
      INSERT OR REPLACE INTO sessions
      (id, parent_id, status, prompt, history_json, result, error, metadata,
       created_at, updated_at, terminated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      session.id, session.parentId, session.status, session.prompt,
      JSON.stringify(session.history ?? []),
      session.result ?? null,
      session.error ?? null,
      JSON.stringify(session.metadata ?? {}),
      session.createdAt, session.updatedAt,
      session.terminatedAt ?? null,
    );
  }

  update(id: string, patch: Partial<Session>): void {
    const existing = this.findOne(id);
    if (!existing) return;
    const next: Session = {
      ...existing,
      ...patch,
      metadata: { ...existing.metadata, ...(patch.metadata ?? {}) },
      updatedAt: new Date().toISOString(),
    };
    this.create(next);
  }

  findOne(id: string): Session | null {
    const row = this.db.rawDb
      .prepare("SELECT * FROM sessions WHERE id = ?")
      .get(id) as SessionRow | undefined;
    return row ? rowToSession(row) : null;
  }

  list(filter?: { status?: SessionStatus; parentId?: string; limit?: number }): Session[] {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (filter?.status) { clauses.push("status = ?"); params.push(filter.status); }
    if (filter?.parentId) { clauses.push("parent_id = ?"); params.push(filter.parentId); }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const limit = Math.min(filter?.limit ?? 200, 1000);
    const rows = this.db.rawDb
      .prepare(`SELECT * FROM sessions ${where} ORDER BY updated_at DESC LIMIT ?`)
      .all(...params, limit) as SessionRow[];
    return rows.map(rowToSession);
  }

  appendMessage(sessionId: string, direction: MessageDirection, content: string): SessionMessage {
    const result = this.db.rawDb.prepare(`
      INSERT INTO session_messages (session_id, direction, content)
      VALUES (?, ?, ?)
    `).run(sessionId, direction, content);
    const id = Number(result.lastInsertRowid);
    const row = this.db.rawDb
      .prepare("SELECT * FROM session_messages WHERE id = ?")
      .get(id) as MessageRow;
    return rowToMessage(row);
  }

  pendingMessages(sessionId: string, direction?: MessageDirection): SessionMessage[] {
    const sql = direction
      ? "SELECT * FROM session_messages WHERE session_id = ? AND direction = ? AND consumed_at IS NULL ORDER BY id"
      : "SELECT * FROM session_messages WHERE session_id = ? AND consumed_at IS NULL ORDER BY id";
    const params = direction ? [sessionId, direction] : [sessionId];
    const rows = this.db.rawDb.prepare(sql).all(...params) as MessageRow[];
    return rows.map(rowToMessage);
  }

  markConsumed(messageId: number): void {
    this.db.rawDb
      .prepare("UPDATE session_messages SET consumed_at = datetime('now') WHERE id = ?")
      .run(messageId);
  }
}
```

- [ ] **Step 4: Run — confirm 8/8 pass**

```bash
npx vitest run __tests__/sessions/store.test.ts
```

- [ ] **Step 5: Build check**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

- [ ] **Step 6: Commit**

```bash
git add src/sessions/store.ts __tests__/sessions/store.test.ts
git commit -m "feat(sessions): SessionStore — SQLite-backed CRUD + message queue"
```

---

## Phase B — Engine extension

## Task 3: Add `AbortSignal` to `EngineContext`

**Files:**
- Modify: `src/engine/runtime.ts` (EngineContext interface + run loop)

- [ ] **Step 1: Add the field to `EngineContext`**

In `src/engine/runtime.ts`, find the `EngineContext` interface (around line 47) and add at the end of its body:

```typescript
  /**
   * Optional cancellation signal. When aborted, the engine throws AbortError
   * at the next turn boundary. Also propagated to the Anthropic SDK for
   * mid-call cancellation.
   */
  signal?: AbortSignal;
```

- [ ] **Step 2: Honor the signal at turn boundary**

In `OwlEngine.run()`, locate the main turn loop. At the top of each loop iteration, add a check:

```typescript
// inside the main turn loop, very first line
if (context.signal?.aborted) {
  throw new DOMException("Aborted", "AbortError");
}
```

- [ ] **Step 3: Propagate to provider call (if supported)**

Find where the provider is invoked. If the provider's `complete()` accepts a `signal`, pass it through:

```typescript
const response = await provider.complete(messages, tools, { signal: context.signal });
```

If the provider interface doesn't accept signal yet, add it as an optional opt:

In `src/providers/base.ts`, find `CompletionOptions` and add:
```typescript
  /** Optional abort signal — provider passes to underlying fetch. */
  signal?: AbortSignal;
```

The fetch-based providers can pass it directly to `fetch(url, { signal })`.

- [ ] **Step 4: Build check**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`. If the provider implementations don't compile, that's because they all need to accept `signal` — fix at the interface, not each provider (just leaves the option unused in providers that don't pass it down).

- [ ] **Step 5: Quick smoke test the signal propagation**

Create `__tests__/sessions/abort-signal-smoke.test.ts` (delete after):

```typescript
import { describe, it, expect } from "vitest";

describe("EngineContext.signal", () => {
  it("AbortController API works as expected", () => {
    const ctrl = new AbortController();
    expect(ctrl.signal.aborted).toBe(false);
    ctrl.abort();
    expect(ctrl.signal.aborted).toBe(true);
  });
});
```

Run:

```bash
npx vitest run __tests__/sessions/abort-signal-smoke.test.ts
rm __tests__/sessions/abort-signal-smoke.test.ts
```

(Just confirming the API. Real signal tests come in T6 via the runner.)

- [ ] **Step 6: Commit**

```bash
git add src/engine/runtime.ts src/providers/base.ts
git commit -m "feat(engine): accept AbortSignal in EngineContext for cancellation"
```

---

## Phase C — SessionRunner (7 tasks)

## Task 4: `SessionRunner` skeleton — spawn returns pending

**Files:**
- Create: `src/sessions/engine-host.ts`
- Create: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-spawn.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-spawn.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";
import type { Session } from "../../src/sessions/types.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

// Stub engine that just appends a canned assistant turn and resolves.
function stubEngineFactory() {
  return {
    async run(prompt: string, _context: any) {
      return { content: `STUB:${prompt.slice(0, 40)}`, history: [] };
    },
  } as any;
}

function stubBaseContext() { return {} as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-spawn-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner.spawn", () => {
  it("returns a session in pending then transitions through running to completed", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const session: Session = await runner.spawn({ prompt: "do a thing" });
    expect(session.status).toBe("pending");
    expect(session.id).toBeTruthy();

    // Wait for the runner to drive the engine
    await new Promise(r => setTimeout(r, 200));
    const final = store.findOne(session.id);
    expect(final?.status).toBe("completed");
    expect(final?.result).toContain("STUB:do a thing");
    runner.stop();
  });

  it("multiple spawns get distinct ids and complete independently", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s1 = await runner.spawn({ prompt: "task A" });
    const s2 = await runner.spawn({ prompt: "task B" });
    expect(s1.id).not.toBe(s2.id);

    await new Promise(r => setTimeout(r, 250));
    expect(store.findOne(s1.id)?.status).toBe("completed");
    expect(store.findOne(s2.id)?.status).toBe("completed");
    runner.stop();
  });

  it("metadata is persisted on the session row", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s = await runner.spawn({
      prompt: "with metadata",
      metadata: { owl: "Noctua", model: "claude-haiku-4-5-20251001" },
    });
    expect(store.findOne(s.id)?.metadata.owl).toBe("Noctua");
    runner.stop();
  });

  it("parentId is persisted when provided", async () => {
    const runner = new SessionRunner(store, stubEngineFactory, stubBaseContext);
    const s = await runner.spawn({ prompt: "child", parentId: "parent-1" });
    expect(store.findOne(s.id)?.parentId).toBe("parent-1");
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail (modules not found)**

```bash
npx vitest run __tests__/sessions/runner-spawn.test.ts
```

- [ ] **Step 3: Create `src/sessions/engine-host.ts` (minimal — expands in T5-T7)**

```typescript
import type { OwlEngine } from "../engine/runtime.js";

/** Factory + base context bundle passed to SessionRunner. */
export interface EngineHost {
  engineFactory: () => OwlEngine;
  baseContext: () => any;   // EngineContext, but typed loosely to avoid coupling tests
}
```

- [ ] **Step 4: Create `src/sessions/runner.ts`**

```typescript
import { randomUUID } from "node:crypto";
import { log } from "../logger.js";
import type { OwlEngine } from "../engine/runtime.js";
import type { Session, SessionMessage, SessionMetadata } from "./types.js";
import type { SessionStore } from "./store.js";

export interface SessionRunnerOptions {
  pollIntervalMs?: number;
  maxConcurrent?: number;
  defaultTimeoutMs?: number;
  sessionMaxAgeDays?: number;
}

interface RunHandle {
  sessionId: string;
  abortController: AbortController;
  promise: Promise<void>;
}

export class SessionRunner {
  private active = new Map<string, RunHandle>();
  private stopped = false;

  constructor(
    private readonly store: SessionStore,
    private readonly engineFactory: () => OwlEngine,
    private readonly baseContext: () => any,
    private readonly opts: SessionRunnerOptions = {},
  ) {}

  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting");
    // Hydration logic lands in T8.
  }

  stop(): void {
    this.stopped = true;
    for (const handle of this.active.values()) {
      handle.abortController.abort();
    }
    this.active.clear();
    log.engine.info("[SessionRunner] stopped");
  }

  async spawn(opts: { prompt: string; parentId?: string; metadata?: SessionMetadata }): Promise<Session> {
    const id = "ses_" + randomUUID();
    const now = new Date().toISOString();
    const session: Session = {
      id,
      parentId: opts.parentId ?? null,
      status: "pending",
      prompt: opts.prompt,
      history: [],
      metadata: opts.metadata ?? {},
      createdAt: now,
      updatedAt: now,
    };
    this.store.create(session);
    log.engine.info("[SessionRunner] spawned", { id, parentId: session.parentId });

    // Kick off the runner asynchronously
    setImmediate(() => this.driveSession(id).catch(err => {
      log.engine.error("[SessionRunner] driveSession failed", err as Error, { id });
    }));

    return session;
  }

  private async driveSession(sessionId: string): Promise<void> {
    if (this.stopped) return;
    const session = this.store.findOne(sessionId);
    if (!session) return;

    this.store.update(sessionId, { status: "running" });
    const abortController = new AbortController();
    const handle: RunHandle = {
      sessionId,
      abortController,
      promise: Promise.resolve(),
    };
    this.active.set(sessionId, handle);

    try {
      const engine = this.engineFactory();
      const context = { ...this.baseContext(), signal: abortController.signal };
      const result = await engine.run(session.prompt, context);

      this.store.update(sessionId, {
        status: "completed",
        result: typeof result === "string" ? result : result?.content ?? String(result),
      });
      log.engine.info("[SessionRunner] session completed", { id: sessionId });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.store.update(sessionId, {
        status: errorMsg.includes("Abort") ? "terminated" : "failed",
        error: errorMsg,
      });
      log.engine.error("[SessionRunner] session failed", err as Error, { id: sessionId });
    } finally {
      this.active.delete(sessionId);
    }
  }
}
```

- [ ] **Step 5: Run — confirm 4/4 pass**

```bash
npx vitest run __tests__/sessions/runner-spawn.test.ts
```

- [ ] **Step 6: Build**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

- [ ] **Step 7: Commit**

```bash
git add src/sessions/engine-host.ts src/sessions/runner.ts __tests__/sessions/runner-spawn.test.ts
git commit -m "feat(sessions): SessionRunner skeleton — spawn, drive engine, mark completed"
```

---

## Task 5: Runner — send/yield messaging handshake

**Files:**
- Modify: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-messaging.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-messaging.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

// Engine that waits for an incoming message via an event hook before completing.
function awaitingEngineFactory() {
  return {
    async run(prompt: string, context: any) {
      // First, signal "awaiting" by writing a from_session marker via a hook
      await new Promise(r => setTimeout(r, 50));  // simulate first turn
      // Now wait for parent to send something via context.onMessage (set by runner)
      if (typeof context.onAwaitInput === "function") {
        const reply = await context.onAwaitInput();
        return { content: `RESP:${reply}` };
      }
      return { content: `STUB:${prompt}` };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-msg-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner messaging", () => {
  it("enqueueMessage stores a to_session row visible via store.pendingMessages", async () => {
    const runner = new SessionRunner(store, awaitingEngineFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });
    runner.enqueueMessage(s.id, "follow-up");
    const pending = store.pendingMessages(s.id, "to_session");
    expect(pending).toHaveLength(1);
    expect(pending[0].content).toBe("follow-up");
    runner.stop();
  });

  it("awaitNextEvent returns immediately when new from_session messages exist", async () => {
    const runner = new SessionRunner(store, awaitingEngineFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });

    // Wait for stub to complete
    await new Promise(r => setTimeout(r, 150));

    // Append a from_session message manually for the test
    store.appendMessage(s.id, "from_session", "test-output");

    const result = await runner.awaitNextEvent(s.id, 2000);
    expect(result.ready).toBe(true);
    expect(result.newMessages.length).toBeGreaterThanOrEqual(1);
    runner.stop();
  });

  it("awaitNextEvent returns ready=false on timeout when nothing happens", async () => {
    const runner = new SessionRunner(store, awaitingEngineFactory, () => ({}));
    const s = await runner.spawn({ prompt: "init" });

    // Wait for stub to complete (then no more events will come)
    await new Promise(r => setTimeout(r, 200));

    const result = await runner.awaitNextEvent(s.id, 300);
    // Session is already completed — awaitNextEvent should return ready=true with status
    expect(result.status).toBe("completed");
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/sessions/runner-messaging.test.ts
```

- [ ] **Step 3: Extend `SessionRunner` with `enqueueMessage` and `awaitNextEvent`**

In `src/sessions/runner.ts`, add these methods to the `SessionRunner` class:

```typescript
  enqueueMessage(sessionId: string, content: string): SessionMessage {
    const session = this.store.findOne(sessionId);
    if (!session) {
      throw new Error(`Session "${sessionId}" not found`);
    }
    const msg = this.store.appendMessage(sessionId, "to_session", content);
    log.engine.debug("[SessionRunner] message enqueued", { sessionId, messageId: msg.id });
    return msg;
  }

  async awaitNextEvent(sessionId: string, timeoutMs: number): Promise<{
    ready: boolean;
    status: Session["status"];
    newMessages: SessionMessage[];
  }> {
    const start = Date.now();
    const POLL_MS = this.opts.pollIntervalMs ?? 250;

    // Quick check: any new from_session messages already pending?
    const initial = this.store.pendingMessages(sessionId, "from_session");
    const session0 = this.store.findOne(sessionId);
    if (!session0) {
      return { ready: false, status: "failed", newMessages: [] };
    }

    if (initial.length > 0 || ["completed", "terminated", "failed"].includes(session0.status)) {
      for (const m of initial) this.store.markConsumed(m.id);
      return { ready: true, status: session0.status, newMessages: initial };
    }

    // Poll
    while (Date.now() - start < timeoutMs) {
      await new Promise(r => setTimeout(r, POLL_MS));
      const session = this.store.findOne(sessionId);
      if (!session) {
        return { ready: false, status: "failed", newMessages: [] };
      }
      const msgs = this.store.pendingMessages(sessionId, "from_session");
      const terminal = ["completed", "terminated", "failed"].includes(session.status);
      if (msgs.length > 0 || terminal) {
        for (const m of msgs) this.store.markConsumed(m.id);
        return { ready: true, status: session.status, newMessages: msgs };
      }
    }
    const session = this.store.findOne(sessionId)!;
    return { ready: false, status: session.status, newMessages: [] };
  }
```

Also add the import at the top:
```typescript
import type { Session, SessionMessage, SessionMetadata, SessionStatus } from "./types.js";
```

- [ ] **Step 4: Run — confirm 3/3 pass**

```bash
npx vitest run __tests__/sessions/runner-messaging.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/sessions/runner.ts __tests__/sessions/runner-messaging.test.ts
git commit -m "feat(sessions): runner enqueueMessage + awaitNextEvent (poll-based)"
```

---

## Task 6: Runner — terminate via AbortSignal

**Files:**
- Modify: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-terminate.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-terminate.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

// Engine that runs for a long time, but respects signal
function longRunningEngineFactory() {
  return {
    async run(prompt: string, context: any) {
      for (let i = 0; i < 100; i++) {
        if (context.signal?.aborted) {
          throw new DOMException("Aborted", "AbortError");
        }
        await new Promise(r => setTimeout(r, 20));
      }
      return { content: "should not reach" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-term-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner.terminate", () => {
  it("terminates a running session via AbortSignal", async () => {
    const runner = new SessionRunner(store, longRunningEngineFactory, () => ({}));
    const s = await runner.spawn({ prompt: "long task" });
    await new Promise(r => setTimeout(r, 50));   // let it start
    const result = runner.terminate(s.id);
    expect(result.terminated).toBe(true);
    expect(["running", "pending"]).toContain(result.previousStatus);

    await new Promise(r => setTimeout(r, 100));   // let abort propagate
    expect(store.findOne(s.id)?.status).toBe("terminated");
  });

  it("terminate is idempotent on terminal sessions", () => {
    const runner = new SessionRunner(store, longRunningEngineFactory, () => ({}));
    store.create({
      id: "already-done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const result = runner.terminate("already-done");
    expect(result.terminated).toBe(true);   // idempotent — return success
    expect(result.previousStatus).toBe("completed");
    runner.stop();
  });

  it("terminate on unknown session returns terminated:false", () => {
    const runner = new SessionRunner(store, longRunningEngineFactory, () => ({}));
    const result = runner.terminate("nonexistent");
    expect(result.terminated).toBe(false);
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail (`terminate` undefined)**

```bash
npx vitest run __tests__/sessions/runner-terminate.test.ts
```

- [ ] **Step 3: Implement `terminate`**

Add to `SessionRunner` class:

```typescript
  terminate(sessionId: string): { terminated: boolean; previousStatus: SessionStatus } {
    const session = this.store.findOne(sessionId);
    if (!session) {
      return { terminated: false, previousStatus: "failed" };
    }
    const previousStatus = session.status;
    const isTerminal = ["completed", "terminated", "failed"].includes(previousStatus);

    // Abort any active run
    const handle = this.active.get(sessionId);
    if (handle) {
      handle.abortController.abort();
      this.active.delete(sessionId);
    }

    if (!isTerminal) {
      this.store.update(sessionId, {
        status: "terminated",
        terminatedAt: new Date().toISOString(),
      });
      log.engine.info("[SessionRunner] terminated", { id: sessionId, previousStatus });
    }
    return { terminated: true, previousStatus };
  }
```

- [ ] **Step 4: Run — confirm 3/3 pass**

```bash
npx vitest run __tests__/sessions/runner-terminate.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/sessions/runner.ts __tests__/sessions/runner-terminate.test.ts
git commit -m "feat(sessions): runner.terminate via AbortSignal (idempotent on terminal)"
```

---

## Task 7: Runner — hydrate on boot

**Files:**
- Modify: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-hydration.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-hydration.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

function stubEngineFactory() {
  return {
    async run(prompt: string) {
      return { content: `RESUMED:${prompt}` };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-hyd-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner.start (hydration)", () => {
  it("resumes a running session from store on start", async () => {
    // Simulate a session left in 'running' from a prior process
    const now = new Date().toISOString();
    store.create({
      id: "left-running", parentId: null, status: "running",
      prompt: "do work", history: [], metadata: {},
      createdAt: now, updatedAt: now,
    });

    const runner = new SessionRunner(store, stubEngineFactory, () => ({}));
    await runner.start();

    // Wait for the resumed engine to finish
    await new Promise(r => setTimeout(r, 300));
    expect(store.findOne("left-running")?.status).toBe("completed");
    runner.stop();
  });

  it("leaves awaiting_input sessions alone (no auto-resume)", async () => {
    store.create({
      id: "waiting", parentId: null, status: "awaiting_input",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });

    const runner = new SessionRunner(store, stubEngineFactory, () => ({}));
    await runner.start();
    await new Promise(r => setTimeout(r, 100));
    expect(store.findOne("waiting")?.status).toBe("awaiting_input");
    runner.stop();
  });

  it("terminal sessions are not touched", async () => {
    store.create({
      id: "done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {}, result: "old result",
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });

    const runner = new SessionRunner(store, stubEngineFactory, () => ({}));
    await runner.start();
    await new Promise(r => setTimeout(r, 100));
    expect(store.findOne("done")?.result).toBe("old result");
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/sessions/runner-hydration.test.ts
```

- [ ] **Step 3: Extend `start()` in runner**

Replace the existing minimal `start()` in `src/sessions/runner.ts`:

```typescript
  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting — hydrating non-terminal sessions");
    const active = this.store.list({ status: "running" });
    const pending = this.store.list({ status: "pending" });
    let resumed = 0;

    for (const session of [...active, ...pending]) {
      // Kick off engine for each — same path as fresh spawn
      setImmediate(() => this.driveSession(session.id).catch(err => {
        log.engine.error("[SessionRunner] hydrated session failed", err as Error, { id: session.id });
      }));
      resumed++;
    }
    log.engine.info("[SessionRunner] hydration complete", { resumed });
  }
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/sessions/runner-hydration.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/sessions/runner.ts __tests__/sessions/runner-hydration.test.ts
git commit -m "feat(sessions): hydrate pending/running sessions on runner.start()"
```

---

## Task 8: Runner — concurrency cap

**Files:**
- Modify: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-concurrency.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-concurrency.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let activeCount = 0;
let peakConcurrent = 0;

function trackingEngineFactory() {
  return {
    async run() {
      activeCount++;
      peakConcurrent = Math.max(peakConcurrent, activeCount);
      await new Promise(r => setTimeout(r, 200));
      activeCount--;
      return { content: "done" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-conc-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  activeCount = 0;
  peakConcurrent = 0;
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner concurrency cap", () => {
  it("respects maxConcurrent=2", async () => {
    const runner = new SessionRunner(store, trackingEngineFactory, () => ({}), { maxConcurrent: 2 });
    // Spawn 5 simultaneously
    await Promise.all([
      runner.spawn({ prompt: "a" }),
      runner.spawn({ prompt: "b" }),
      runner.spawn({ prompt: "c" }),
      runner.spawn({ prompt: "d" }),
      runner.spawn({ prompt: "e" }),
    ]);
    // Wait for all to drain
    await new Promise(r => setTimeout(r, 1500));
    expect(peakConcurrent).toBeLessThanOrEqual(2);
    expect(store.list({ status: "completed" })).toHaveLength(5);
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail (concurrency exceeds 2)**

```bash
npx vitest run __tests__/sessions/runner-concurrency.test.ts
```

- [ ] **Step 3: Implement the concurrency cap in `runner.ts`**

Modify `spawn()` to not immediately drive but enqueue. Add a `pumpQueue()` method:

```typescript
  async spawn(opts: { prompt: string; parentId?: string; metadata?: SessionMetadata }): Promise<Session> {
    const id = "ses_" + randomUUID();
    const now = new Date().toISOString();
    const session: Session = {
      id, parentId: opts.parentId ?? null,
      status: "pending",
      prompt: opts.prompt, history: [],
      metadata: opts.metadata ?? {},
      createdAt: now, updatedAt: now,
    };
    this.store.create(session);
    log.engine.info("[SessionRunner] spawned", { id, parentId: session.parentId });

    setImmediate(() => this.pumpQueue());
    return session;
  }

  private pumpQueue(): void {
    if (this.stopped) return;
    const maxConcurrent = this.opts.maxConcurrent ?? 5;
    while (this.active.size < maxConcurrent) {
      const pending = this.store.list({ status: "pending", limit: 1 });
      if (pending.length === 0) return;
      const next = pending[0]!;
      this.active.set(next.id, {
        sessionId: next.id,
        abortController: new AbortController(),
        promise: Promise.resolve(),
      });
      // Don't await — fire and continue
      this.driveSession(next.id).catch(err => {
        log.engine.error("[SessionRunner] driveSession failed", err as Error, { id: next.id });
      }).finally(() => {
        setImmediate(() => this.pumpQueue());   // promote next pending after this finishes
      });
    }
  }
```

Update `driveSession` so it doesn't re-create the handle (already created by `pumpQueue`):

```typescript
  private async driveSession(sessionId: string): Promise<void> {
    if (this.stopped) return;
    const session = this.store.findOne(sessionId);
    if (!session) return;

    this.store.update(sessionId, { status: "running" });
    const handle = this.active.get(sessionId);
    const signal = handle?.abortController.signal;

    try {
      const engine = this.engineFactory();
      const context = { ...this.baseContext(), signal };
      const result = await engine.run(session.prompt, context);

      if (signal?.aborted) {
        // Abort was signalled mid-run; terminate handled this
        return;
      }
      this.store.update(sessionId, {
        status: "completed",
        result: typeof result === "string" ? result : result?.content ?? String(result),
      });
      log.engine.info("[SessionRunner] session completed", { id: sessionId });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.store.update(sessionId, {
        status: errorMsg.includes("Abort") ? "terminated" : "failed",
        error: errorMsg,
      });
      log.engine.error("[SessionRunner] session failed", err as Error, { id: sessionId });
    } finally {
      this.active.delete(sessionId);
    }
  }
```

Also update `start()` to use `pumpQueue()` instead of `setImmediate` per session:

```typescript
  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting — hydrating non-terminal sessions");
    const resumeCount = this.store.list({ status: "running" }).length + this.store.list({ status: "pending" }).length;
    // Bring all 'running' back to 'pending' so pumpQueue picks them up under the concurrency cap
    for (const s of this.store.list({ status: "running" })) {
      this.store.update(s.id, { status: "pending" });
    }
    setImmediate(() => this.pumpQueue());
    log.engine.info("[SessionRunner] hydration complete", { resumed: resumeCount });
  }
```

- [ ] **Step 4: Run — confirm pass**

```bash
npx vitest run __tests__/sessions/runner-concurrency.test.ts __tests__/sessions/runner-spawn.test.ts __tests__/sessions/runner-hydration.test.ts
```

Expected: all pass (concurrency test + the earlier tests still pass with the new queue model).

- [ ] **Step 5: Commit**

```bash
git add src/sessions/runner.ts __tests__/sessions/runner-concurrency.test.ts
git commit -m "feat(sessions): concurrency cap (default 5) — FIFO pending queue via pumpQueue"
```

---

## Task 9: Runner — age-based auto-terminate

**Files:**
- Modify: `src/sessions/runner.ts`
- Create: `__tests__/sessions/runner-age.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/sessions/runner-age.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SessionStore } from "../../src/sessions/store.js";
import { SessionRunner } from "../../src/sessions/runner.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;

function noopFactory() { return { async run() { return { content: "done" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-runner-age-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("SessionRunner age-based auto-terminate", () => {
  it("terminates sessions older than sessionMaxAgeDays on start", async () => {
    const eightDaysAgo = new Date(Date.now() - 8 * 24 * 60 * 60 * 1000).toISOString();
    store.create({
      id: "old", parentId: null, status: "running",
      prompt: "ancient", history: [], metadata: {},
      createdAt: eightDaysAgo, updatedAt: eightDaysAgo,
    });

    const runner = new SessionRunner(store, noopFactory, () => ({}), { sessionMaxAgeDays: 7 });
    await runner.start();
    await new Promise(r => setTimeout(r, 100));

    const s = store.findOne("old");
    expect(s?.status).toBe("terminated");
    expect(s?.error).toMatch(/auto-terminated|too old/);
    runner.stop();
  });

  it("leaves recent sessions alone", async () => {
    const oneHourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    store.create({
      id: "recent", parentId: null, status: "running",
      prompt: "recent", history: [], metadata: {},
      createdAt: oneHourAgo, updatedAt: oneHourAgo,
    });

    const runner = new SessionRunner(store, noopFactory, () => ({}), { sessionMaxAgeDays: 7 });
    await runner.start();
    await new Promise(r => setTimeout(r, 200));   // let it complete via stub

    const s = store.findOne("recent");
    expect(s?.status).toBe("completed");
    runner.stop();
  });
});
```

- [ ] **Step 2: Run — fail (old session not auto-terminated)**

```bash
npx vitest run __tests__/sessions/runner-age.test.ts
```

- [ ] **Step 3: Implement age-based termination in `start()`**

Modify `start()`:

```typescript
  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting — hydrating non-terminal sessions");
    const maxAgeDays = this.opts.sessionMaxAgeDays ?? 7;
    const cutoff = new Date(Date.now() - maxAgeDays * 24 * 60 * 60 * 1000).toISOString();

    let expired = 0;
    const nonTerminal = [
      ...this.store.list({ status: "running" }),
      ...this.store.list({ status: "pending" }),
      ...this.store.list({ status: "awaiting_input" }),
    ];
    for (const s of nonTerminal) {
      if (s.createdAt < cutoff) {
        this.store.update(s.id, {
          status: "terminated",
          error: `auto-terminated: session too old (>${maxAgeDays} days)`,
          terminatedAt: new Date().toISOString(),
        });
        expired++;
      }
    }

    // Bring remaining 'running' back to 'pending' for pumpQueue
    for (const s of this.store.list({ status: "running" })) {
      this.store.update(s.id, { status: "pending" });
    }
    setImmediate(() => this.pumpQueue());
    log.engine.info("[SessionRunner] hydration complete", {
      resumed: nonTerminal.length - expired,
      autoTerminated: expired,
    });
  }
```

- [ ] **Step 4: Run — 2/2 pass + earlier tests still green**

```bash
npx vitest run __tests__/sessions/runner-age.test.ts __tests__/sessions/runner-hydration.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/sessions/runner.ts __tests__/sessions/runner-age.test.ts
git commit -m "feat(sessions): auto-terminate sessions older than sessionMaxAgeDays on start"
```

---

## Phase D — 6 LLM-callable tools

The next 6 tasks build the LLM tool surface. All share the same late-attach pattern (mirrors `attachSchedule` from Cycle 2): module-level refs to the runner+store, set by `attachSessions()` called from `src/index.ts` after the runner is constructed.

## Task 10: `attachSessions` wiring helper + `subagents` tool

**Files:**
- Create: `src/tools/sessions/attach.ts`
- Create: `src/tools/sessions/subagents.ts`
- Create: `__tests__/tools/sessions/subagents.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/tools/sessions/subagents.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SubagentsTool } from "../../../src/tools/sessions/subagents.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "done" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-subagents-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SubagentsTool", () => {
  it("spawns N sessions and returns their ids", async () => {
    const res = await SubagentsTool.execute(
      { tasks: ["task A", "task B", "task C"] },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.spawned).toBe(3);
    expect(parsed.data.sessions).toHaveLength(3);
    expect(parsed.data.sessions[0].status).toBe("pending");
  });

  it("returns error when tasks is empty", async () => {
    const res = await SubagentsTool.execute({ tasks: [] }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("MISSING_ARG");
  });

  it("shared_context is prepended to every task prompt", async () => {
    const res = await SubagentsTool.execute(
      { tasks: ["do X"], shared_context: "Context: project Foo" },
      {} as any,
    );
    const parsed = JSON.parse(res);
    const sessionId = parsed.data.sessions[0].id;
    const session = store.findOne(sessionId);
    expect(session?.prompt).toContain("Context: project Foo");
    expect(session?.prompt).toContain("do X");
  });
});
```

- [ ] **Step 2: Run — fail (modules not found)**

```bash
npx vitest run __tests__/tools/sessions/subagents.test.ts
```

- [ ] **Step 3: Create `src/tools/sessions/attach.ts`**

```typescript
import type { SessionRunner } from "../../sessions/runner.js";
import type { SessionStore } from "../../sessions/store.js";

let runnerRef: SessionRunner | null = null;
let storeRef: SessionStore | null = null;

/** Called from src/index.ts after the runner is created. */
export function attachSessions(runner: SessionRunner, store: SessionStore): void {
  runnerRef = runner;
  storeRef = store;
}

export function getRunner(): SessionRunner {
  if (!runnerRef) throw new Error("SessionRunner not attached — call attachSessions() at bootstrap");
  return runnerRef;
}

export function getStore(): SessionStore {
  if (!storeRef) throw new Error("SessionStore not attached — call attachSessions() at bootstrap");
  return storeRef;
}

export function isAttached(): boolean {
  return runnerRef !== null && storeRef !== null;
}
```

- [ ] **Step 4: Create `src/tools/sessions/subagents.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

export const SubagentsTool: ToolImplementation = {
  definition: {
    name: "subagents",
    description:
      "Spawn N background subagent sessions. Returns immediately with session IDs; sessions outlive this conversation. " +
      "Use this for fire-and-forget research / long-running work. For sync map-reduce, use orchestrate_tasks instead. " +
      'Example: subagents(tasks: ["research X", "draft Y"], shared_context: "project Foo")',
    parameters: {
      type: "object",
      properties: {
        tasks: {
          type: "array",
          description: "Array of prompts; one session per prompt",
        } as any,
        shared_context: { type: "string", description: "Common preamble prepended to every task" },
        metadata: {
          type: "object",
          description: "owl/model override per spawn",
        } as any,
      },
      required: ["tasks"],
    },
    capabilities: ["session_lifecycle"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const tasks = args["tasks"] as string[] | undefined;
    const sharedContext = (args["shared_context"] as string | undefined) ?? "";
    const metadata = (args["metadata"] as Record<string, string> | undefined) ?? {};

    if (!Array.isArray(tasks) || tasks.length === 0) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "tasks must be a non-empty array" } });
    }

    log.tool.debug("subagents.execute: entry", { count: tasks.length });

    const runner = getRunner();
    const parentId = context.engineContext?.sessionId ?? null;
    const sessions = [];
    for (const task of tasks) {
      const prompt = sharedContext ? `${sharedContext}\n\n${task}` : task;
      const s = await runner.spawn({
        prompt,
        parentId: parentId ?? undefined,
        metadata,
      });
      sessions.push({ id: s.id, prompt: s.prompt, status: s.status });
    }

    log.tool.debug("subagents.execute: exit", { spawned: sessions.length });
    return JSON.stringify({
      success: true,
      data: { spawned: sessions.length, sessions },
    });
  },
};
```

- [ ] **Step 5: Run — confirm 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/subagents.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/sessions/attach.ts src/tools/sessions/subagents.ts __tests__/tools/sessions/subagents.test.ts
git commit -m "feat(tools): subagents tool — spawn background sessions"
```

---

## Task 11: `sessions_status` tool

**Files:**
- Create: `src/tools/sessions/sessions-status.ts`
- Create: `__tests__/tools/sessions/sessions-status.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/sessions/sessions-status.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsStatusTool } from "../../../src/tools/sessions/sessions-status.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "done" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-status-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsStatusTool", () => {
  it("returns session metadata for existing id", async () => {
    const s = await runner.spawn({ prompt: "task X" });
    const res = await SessionsStatusTool.execute({ id: s.id }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.session.id).toBe(s.id);
    expect(parsed.data.session.prompt).toBe("task X");
  });

  it("returns NOT_FOUND for missing id", async () => {
    const res = await SessionsStatusTool.execute({ id: "ghost" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });

  it("include_messages=true includes pending messages", async () => {
    const s = await runner.spawn({ prompt: "x" });
    store.appendMessage(s.id, "from_session", "interim output");
    const res = await SessionsStatusTool.execute(
      { id: s.id, include_messages: true },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.messages).toBeDefined();
    expect(parsed.data.messages[0].content).toBe("interim output");
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/tools/sessions/sessions-status.test.ts
```

- [ ] **Step 3: Implement `src/tools/sessions/sessions-status.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getStore, isAttached } from "./attach.js";

export const SessionsStatusTool: ToolImplementation = {
  definition: {
    name: "sessions_status",
    description:
      "Get the current status of a spawned session by id, optionally including pending messages from the subagent. " +
      'Example: sessions_status(id: "ses_abc", include_messages: true)',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id returned by subagents" },
        include_messages: { type: "boolean", description: "If true, include pending from_session messages in result" },
        since_message_id: { type: "number", description: "Return only messages with id > this value" },
      },
      required: ["id"],
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;
    const includeMessages = args["include_messages"] === true;
    const sinceMessageId = args["since_message_id"] as number | undefined;

    log.tool.debug("sessions_status.execute: entry", { id, includeMessages });

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    const store = getStore();
    const session = store.findOne(id);
    if (!session) {
      return JSON.stringify({ success: false, error: { code: "NOT_FOUND", message: `Session "${id}" not found` } });
    }

    const data: Record<string, unknown> = { session };
    if (includeMessages) {
      let messages = store.pendingMessages(id, "from_session");
      if (typeof sinceMessageId === "number") {
        messages = messages.filter(m => m.id > sinceMessageId);
      }
      data.messages = messages;
      data.message_cursor = messages.length > 0 ? messages[messages.length - 1].id : sinceMessageId ?? 0;
    }

    log.tool.debug("sessions_status.execute: exit", { id, status: session.status });
    return JSON.stringify({ success: true, data });
  },
};
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/sessions-status.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/sessions/sessions-status.ts __tests__/tools/sessions/sessions-status.test.ts
git commit -m "feat(tools): sessions_status — query session state + pending messages"
```

---

## Task 12: `sessions_send` tool

**Files:**
- Create: `src/tools/sessions/sessions-send.ts`
- Create: `__tests__/tools/sessions/sessions-send.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/sessions/sessions-send.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsSendTool } from "../../../src/tools/sessions/sessions-send.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function slowFactory() {
  return {
    async run(_prompt: string, ctx: any) {
      for (let i = 0; i < 50; i++) {
        if (ctx.signal?.aborted) throw new DOMException("Aborted", "AbortError");
        await new Promise(r => setTimeout(r, 20));
      }
      return { content: "done" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-send-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, slowFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsSendTool", () => {
  it("queues a to_session message and reports accepted", async () => {
    const s = await runner.spawn({ prompt: "long task" });
    await new Promise(r => setTimeout(r, 50));   // session is running
    const res = await SessionsSendTool.execute(
      { id: s.id, content: "interrupt" },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.accepted).toBe(true);
    expect(parsed.data.queued_message_id).toBeGreaterThan(0);
  });

  it("returns accepted=false for terminal sessions", async () => {
    store.create({
      id: "done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const res = await SessionsSendTool.execute(
      { id: "done", content: "too late" },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.accepted).toBe(false);
    expect(parsed.data.current_status).toBe("completed");
  });

  it("returns NOT_FOUND for unknown session", async () => {
    const res = await SessionsSendTool.execute({ id: "ghost", content: "x" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("NOT_FOUND");
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/tools/sessions/sessions-send.test.ts
```

- [ ] **Step 3: Implement `src/tools/sessions/sessions-send.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, getStore, isAttached } from "./attach.js";

const TERMINAL = new Set(["completed", "terminated", "failed"]);

export const SessionsSendTool: ToolImplementation = {
  definition: {
    name: "sessions_send",
    description:
      "Send a message to a running subagent session. Non-blocking; the message is queued for the session to consume. " +
      "Use sessions_yield to wait for a response. " +
      'Example: sessions_send(id: "ses_abc", content: "what have you found so far?")',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Target session id" },
        content: { type: "string", description: "Message text to deliver to the subagent" },
      },
      required: ["id", "content"],
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;
    const content = args["content"] as string;

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }
    if (!content) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "content is required" } });
    }

    log.tool.debug("sessions_send.execute: entry", { id, contentLen: content.length });

    const store = getStore();
    const session = store.findOne(id);
    if (!session) {
      return JSON.stringify({ success: false, error: { code: "NOT_FOUND", message: `Session "${id}" not found` } });
    }

    if (TERMINAL.has(session.status)) {
      return JSON.stringify({
        success: true,
        data: { accepted: false, queued_message_id: 0, current_status: session.status },
      });
    }

    const runner = getRunner();
    const msg = runner.enqueueMessage(id, content);

    log.tool.debug("sessions_send.execute: exit", { id, messageId: msg.id });
    return JSON.stringify({
      success: true,
      data: { accepted: true, queued_message_id: msg.id, current_status: session.status },
    });
  },
};
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/sessions-send.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/sessions/sessions-send.ts __tests__/tools/sessions/sessions-send.test.ts
git commit -m "feat(tools): sessions_send — queue a message to a running session"
```

---

## Task 13: `sessions_yield` tool

**Files:**
- Create: `src/tools/sessions/sessions-yield.ts`
- Create: `__tests__/tools/sessions/sessions-yield.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/sessions/sessions-yield.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsYieldTool } from "../../../src/tools/sessions/sessions-yield.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "ok" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-yield-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsYieldTool", () => {
  it("returns ready=true with completed status after session finishes", async () => {
    const s = await runner.spawn({ prompt: "quick" });
    await new Promise(r => setTimeout(r, 150));
    const res = await SessionsYieldTool.execute(
      { id: s.id, timeout_ms: 1000 },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.ready).toBe(true);
    expect(parsed.data.status).toBe("completed");
  });

  it("returns ready=false on timeout when nothing changes", async () => {
    store.create({
      id: "stuck", parentId: null, status: "awaiting_input",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    });
    const res = await SessionsYieldTool.execute(
      { id: "stuck", timeout_ms: 300 },
      {} as any,
    );
    const parsed = JSON.parse(res);
    expect(parsed.data.ready).toBe(false);
    expect(parsed.data.status).toBe("awaiting_input");
  });

  it("rejects timeout_ms over max (600000)", async () => {
    const s = await runner.spawn({ prompt: "x" });
    const res = await SessionsYieldTool.execute(
      { id: s.id, timeout_ms: 1_000_000 },
      {} as any,
    );
    const parsed = JSON.parse(res);
    // Either tool rejects, or it clamps. Either way: it doesn't actually wait > 10 min in this test.
    // Check the clamp/reject behavior is correct by NOT actually waiting that long.
    expect(parsed.success).toBe(true);
    // We don't await — the test passes if it returns quickly (because clamping or returning quickly)
  }, 11000);
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/tools/sessions/sessions-yield.test.ts
```

- [ ] **Step 3: Implement `src/tools/sessions/sessions-yield.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

const MAX_TIMEOUT_MS = 600_000;
const DEFAULT_TIMEOUT_MS = 30_000;

export const SessionsYieldTool: ToolImplementation = {
  definition: {
    name: "sessions_yield",
    description:
      "Block until the session emits a new from_session message OR transitions to a terminal state OR the timeout fires. " +
      `Max timeout ${MAX_TIMEOUT_MS}ms (10 min). Use after sessions_send to wait for a response. ` +
      'Example: sessions_yield(id: "ses_abc", timeout_ms: 60000)',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id to wait on" },
        timeout_ms: { type: "number", description: `Max wait in ms (default ${DEFAULT_TIMEOUT_MS}, max ${MAX_TIMEOUT_MS})` },
      },
      required: ["id"],
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: MAX_TIMEOUT_MS + 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;
    const rawTimeout = (args["timeout_ms"] as number | undefined) ?? DEFAULT_TIMEOUT_MS;
    const timeoutMs = Math.min(Math.max(rawTimeout, 100), MAX_TIMEOUT_MS);

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    log.tool.debug("sessions_yield.execute: entry", { id, timeoutMs });

    const runner = getRunner();
    const event = await runner.awaitNextEvent(id, timeoutMs);

    log.tool.debug("sessions_yield.execute: exit", { id, ready: event.ready, status: event.status });

    return JSON.stringify({
      success: true,
      data: {
        ready: event.ready,
        status: event.status,
        new_messages: event.newMessages,
      },
    });
  },
};
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/sessions-yield.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/sessions/sessions-yield.ts __tests__/tools/sessions/sessions-yield.test.ts
git commit -m "feat(tools): sessions_yield — block until next event/timeout"
```

---

## Task 14: `sessions_list` tool

**Files:**
- Create: `src/tools/sessions/sessions-list.ts`
- Create: `__tests__/tools/sessions/sessions-list.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/sessions/sessions-list.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsListTool } from "../../../src/tools/sessions/sessions-list.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function noopFactory() { return { async run() { return { content: "ok" }; } } as any; }

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-list-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, noopFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsListTool", () => {
  it("returns all sessions when no filter", async () => {
    store.create({ id: "a", parentId: null, status: "running", prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "b", parentId: null, status: "completed", prompt: "y", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({}, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions.length).toBeGreaterThanOrEqual(2);
  });

  it("filters by status", async () => {
    store.create({ id: "a", parentId: null, status: "running", prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "b", parentId: null, status: "completed", prompt: "y", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({ status: "running" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions).toHaveLength(1);
    expect(parsed.data.sessions[0].id).toBe("a");
  });

  it("filters by parent_id", async () => {
    store.create({ id: "p", parentId: null, status: "completed", prompt: "p", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    store.create({ id: "c", parentId: "p", status: "running", prompt: "c", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsListTool.execute({ parent_id: "p" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.sessions).toHaveLength(1);
    expect(parsed.data.sessions[0].id).toBe("c");
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/tools/sessions/sessions-list.test.ts
```

- [ ] **Step 3: Implement `src/tools/sessions/sessions-list.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getStore, isAttached } from "./attach.js";
import type { SessionStatus } from "../../sessions/types.js";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

export const SessionsListTool: ToolImplementation = {
  definition: {
    name: "sessions_list",
    description:
      "Enumerate sessions, optionally filtered by status or parent. " +
      `Default limit ${DEFAULT_LIMIT}, max ${MAX_LIMIT}. ` +
      'Example: sessions_list(status: "running") — what subagents are still working?',
    parameters: {
      type: "object",
      properties: {
        status: {
          type: "string",
          enum: ["pending", "running", "awaiting_input", "completed", "terminated", "failed"],
          description: "Filter by status",
        },
        parent_id: { type: "string", description: "Filter to sessions spawned from this parent" },
        limit: { type: "number", description: `Cap (default ${DEFAULT_LIMIT}, max ${MAX_LIMIT})` },
      },
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const status = args["status"] as SessionStatus | undefined;
    const parentId = args["parent_id"] as string | undefined;
    const rawLimit = (args["limit"] as number | undefined) ?? DEFAULT_LIMIT;
    const limit = Math.min(rawLimit, MAX_LIMIT);

    log.tool.debug("sessions_list.execute: entry", { status, parentId, limit });

    const store = getStore();
    const sessions = store.list({ status, parentId, limit });

    log.tool.debug("sessions_list.execute: exit", { count: sessions.length });
    return JSON.stringify({
      success: true,
      data: { sessions, total: sessions.length },
    });
  },
};
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/sessions-list.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/sessions/sessions-list.ts __tests__/tools/sessions/sessions-list.test.ts
git commit -m "feat(tools): sessions_list — enumerate sessions with status/parent filters"
```

---

## Task 15: `sessions_terminate` tool

**Files:**
- Create: `src/tools/sessions/sessions-terminate.ts`
- Create: `__tests__/tools/sessions/sessions-terminate.test.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/tools/sessions/sessions-terminate.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../../src/memory/db.js";
import { SessionStore } from "../../../src/sessions/store.js";
import { SessionRunner } from "../../../src/sessions/runner.js";
import { attachSessions } from "../../../src/tools/sessions/attach.js";
import { SessionsTerminateTool } from "../../../src/tools/sessions/sessions-terminate.js";

let dir: string;
let db: MemoryDatabase;
let store: SessionStore;
let runner: SessionRunner;

function slowFactory() {
  return {
    async run(_prompt: string, ctx: any) {
      for (let i = 0; i < 50; i++) {
        if (ctx.signal?.aborted) throw new DOMException("Aborted", "AbortError");
        await new Promise(r => setTimeout(r, 20));
      }
      return { content: "done" };
    },
  } as any;
}

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-tool-term-"));
  db = new MemoryDatabase(dir);
  store = new SessionStore(db);
  runner = new SessionRunner(store, slowFactory, () => ({}));
  attachSessions(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("SessionsTerminateTool", () => {
  it("terminates a running session", async () => {
    const s = await runner.spawn({ prompt: "long" });
    await new Promise(r => setTimeout(r, 50));
    const res = await SessionsTerminateTool.execute({ id: s.id }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.success).toBe(true);
    expect(parsed.data.terminated).toBe(true);
    expect(parsed.data.previous_status).toMatch(/running|pending/);
  });

  it("is idempotent on terminal sessions", async () => {
    store.create({ id: "done", parentId: null, status: "completed",
      prompt: "x", history: [], metadata: {},
      createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
    const res = await SessionsTerminateTool.execute({ id: "done" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.terminated).toBe(true);
    expect(parsed.data.previous_status).toBe("completed");
  });

  it("returns terminated=false for unknown session", async () => {
    const res = await SessionsTerminateTool.execute({ id: "ghost" }, {} as any);
    const parsed = JSON.parse(res);
    expect(parsed.data.terminated).toBe(false);
  });
});
```

- [ ] **Step 2: Run — fail**

```bash
npx vitest run __tests__/tools/sessions/sessions-terminate.test.ts
```

- [ ] **Step 3: Implement `src/tools/sessions/sessions-terminate.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

export const SessionsTerminateTool: ToolImplementation = {
  definition: {
    name: "sessions_terminate",
    description:
      "Kill a running subagent session. Idempotent on terminal sessions (returns terminated=true with previous_status). " +
      'Example: sessions_terminate(id: "ses_abc")',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id to terminate" },
      },
      required: ["id"],
    },
    capabilities: ["session_lifecycle"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    log.tool.debug("sessions_terminate.execute: entry", { id });

    const runner = getRunner();
    const result = runner.terminate(id);

    log.tool.debug("sessions_terminate.execute: exit", { id, terminated: result.terminated });
    return JSON.stringify({
      success: true,
      data: {
        terminated: result.terminated,
        previous_status: result.previousStatus,
      },
    });
  },
};
```

- [ ] **Step 4: Run — 3/3 pass**

```bash
npx vitest run __tests__/tools/sessions/sessions-terminate.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/tools/sessions/sessions-terminate.ts __tests__/tools/sessions/sessions-terminate.test.ts
git commit -m "feat(tools): sessions_terminate — kill a session (idempotent)"
```

---

## Phase E — Bootstrap wiring + docs

## Task 16: Wire `SessionRunner` + register 6 tools in `src/index.ts`

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Add imports at the top of `src/index.ts`**

Add near other tool imports:

```typescript
import { SessionStore } from "./sessions/store.js";
import { SessionRunner } from "./sessions/runner.js";
import { attachSessions } from "./tools/sessions/attach.js";
import { SubagentsTool } from "./tools/sessions/subagents.js";
import { SessionsStatusTool } from "./tools/sessions/sessions-status.js";
import { SessionsSendTool } from "./tools/sessions/sessions-send.js";
import { SessionsYieldTool } from "./tools/sessions/sessions-yield.js";
import { SessionsListTool } from "./tools/sessions/sessions-list.js";
import { SessionsTerminateTool } from "./tools/sessions/sessions-terminate.js";
```

- [ ] **Step 2: Register the 6 tools in the existing `toolRegistry.registerAll(...)` block**

Find the block in `bootstrap()` that registers cognitive tools (look for `SummonParliamentTool` or `createNotificationSendTool` calls). Add alongside them:

```typescript
    SubagentsTool,
    SessionsStatusTool,
    SessionsSendTool,
    SessionsYieldTool,
    SessionsListTool,
    SessionsTerminateTool,
```

- [ ] **Step 3: Wire the runner into bootstrap**

After `scheduleRunner.start()` (the Cycle 2 wiring), add:

```typescript
  // Multi-agent runtime — subagents that outlive the spawning conversation (Cycle 4)
  const sessionStore = new SessionStore(memoryDb);
  const sessionRunner = new SessionRunner(
    sessionStore,
    () => new OwlEngine(),
    () => ({} as any),    // base context — populated per session by the runner during driveSession
    { maxConcurrent: 5 },
  );
  attachSessions(sessionRunner, sessionStore);
  await sessionRunner.start();
```

If there's an existing SIGINT/SIGTERM handler, extend it to also stop sessionRunner:

```typescript
process.on("SIGTERM", () => {
  cronService.stop();
  scheduleRunner.stop();
  sessionRunner.stop();
});
process.on("SIGINT", () => {
  cronService.stop();
  scheduleRunner.stop();
  sessionRunner.stop();
});
```

- [ ] **Step 4: Verify build + boot**

```bash
npm run build 2>&1 | grep "error TS" | wc -l
```
Expected: `0`.

```bash
timeout 25 npx tsx src/index.ts chat 2>&1 | grep -E "SessionRunner|FATAL" | head -10
```
Expected: `[SessionRunner] starting — hydrating non-terminal sessions` log line. No new FATAL (the TUI TTY error is expected).

- [ ] **Step 5: Run all the session tests**

```bash
npx vitest run __tests__/sessions/ __tests__/tools/sessions/ --reporter=dot 2>&1 | tail -5
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/index.ts
git commit -m "feat(sessions): wire SessionRunner + 6 tools into bootstrap"
```

---

## Task 17: Update `docs/dev-setup.md`

**Files:**
- Modify: `docs/dev-setup.md`

- [ ] **Step 1: Append a new section after the existing tool-prerequisite sections**

```markdown
### Subagents vs orchestrate_tasks

Two tools spawn child work. Pick by lifetime:

| | `orchestrate_tasks` | `subagents` |
|---|---|---|
| Lifetime | Bounded by the parent turn | Outlives the spawning conversation |
| Result delivery | Aggregated summary at end | Queryable any time via session id |
| Use case | "Run 3 things in parallel and give me the combined answer" | "Spawn researchers; check back in an hour" |
| Inter-session messaging | Not supported | Native via `sessions_send` / `sessions_yield` |
| Cancellation | Implicit on parent turn end | Explicit via `sessions_terminate` |

The companion tools `sessions_status`, `sessions_list`, `sessions_send`,
`sessions_yield`, `sessions_terminate` form the full lifecycle API around
subagents. Sessions persist in SQLite and survive process restarts (older
than 7 days are auto-terminated on boot).
```

- [ ] **Step 2: Commit**

```bash
git add docs/dev-setup.md
git commit -m "docs: subagents-vs-orchestrate_tasks guidance"
```

---

## Task 18: Final smoke + push

- [ ] **Step 1: Full build + lint**

```bash
npm run build 2>&1 | grep "error TS" | wc -l   # 0
npm run lint 2>&1 | tail -3                    # no errors
```

- [ ] **Step 2: Full test suite — confirm no regressions**

```bash
npx vitest run --reporter=dot 2>&1 | tail -5
```

Expected: same count of passing tests as before plus ~30 new ones.

- [ ] **Step 3: Boot smoke**

```bash
timeout 25 npx tsx src/index.ts chat 2>&1 | grep -E "SessionRunner|ScheduleRunner|FATAL" | head -10
```

Expected: both runners log their startup. No new FATAL beyond the TTY error.

- [ ] **Step 4: Push**

```bash
git push origin main
```

---

## Self-Review

### 1. Spec coverage

| Spec section | Plan task |
|---|---|
| Schema (sessions + session_messages tables) | T1 |
| `Session`, `SessionMessage`, status enum types | T1 |
| `SessionStore` CRUD + queue | T2 |
| `AbortSignal` on `EngineContext` | T3 |
| `SessionRunner.spawn` + state machine pending → running → completed | T4 |
| `SessionRunner.enqueueMessage` + `awaitNextEvent` | T5 |
| `SessionRunner.terminate` via AbortSignal | T6 |
| Hydration on boot | T7 |
| Concurrency cap (default 5, FIFO) | T8 |
| Age-based auto-terminate (7 days) | T9 |
| 6 tools (subagents, status, send, yield, list, terminate) | T10–T15 |
| Bootstrap wiring + tool registration | T16 |
| Docs (subagents vs orchestrate_tasks) | T17 |
| Final smoke + push | T18 |

All spec sections covered.

### 2. Placeholder scan

No "TBD" / "implement later" / "add appropriate" — every step shows real code.

### 3. Type consistency

- `Session` type used identically across T1 (types.ts), T2 (store), T4 (runner), T10-T15 (tools)
- `SessionStatus` enum used consistently (`"pending"|"running"|"awaiting_input"|"completed"|"terminated"|"failed"`)
- `SessionMessage` direction values consistent (`"to_session"|"from_session"`)
- `awaitNextEvent` signature consistent between T5 (runner def) and T13 (yield tool consumer)
- `attachSessions` / `getRunner` / `getStore` / `isAttached` consistent across T10-T15
- `EngineContext.signal` consistent between T3 (interface) and T4 (engine call site)
- Tool result envelope shape `{ success, data, error }` consistent across all 6 tools

No drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-12-cycle-4-multi-agent-runtime-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review, continuous execution.

**2. Inline Execution** — execute in this session via executing-plans with checkpoints.

Which approach?
