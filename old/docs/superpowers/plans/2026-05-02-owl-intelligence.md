# Owl Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the owl professional and human-like — it persists tasks across restarts, learns from failure via Reflexion, selects tools intelligently, and never pretends to succeed when it hasn't.

**Architecture:** New `src/intelligence/` module with nine focused files. Sync components (SemanticToolGate, CritiqueRetriever, HITLEscalator) on the critical path; async components (ReflexionEngine, SentimentProbe, SleepTimeConsolidator, FactInvalidator) as GatewayEventBus subscribers that never block a response. Three new SQLite tables, two column additions.

**Tech Stack:** TypeScript (NodeNext), better-sqlite3, fastembed (already used by UserMemoryStore), GatewayEventBus (typed pub/sub, already exists), ContextPipeline layer system (already exists), IntelligenceRouter cheap-tier model calls.

**Spec:** `docs/superpowers/specs/2026-05-02-owl-intelligence-design.md`

---

## Phase A — Foundation

### Task 1: Schema v17 Migration

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory/db-schema-v17.test.ts`

Read `src/memory/db.ts` first. Find `SCHEMA_VERSION` constant and the migration block (look for `if (currentVersion < N)` pattern). Add our three new tables and two column additions.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/memory/db-schema-v17.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";

describe("schema v17 migration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db); // must export this, or test via createDatabase()
  });

  afterEach(() => db.close());

  it("creates owl_task_ledger table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_task_ledger'"
    ).get();
    expect(row).toBeDefined();
  });

  it("creates reflexion_critiques table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='reflexion_critiques'"
    ).get();
    expect(row).toBeDefined();
  });

  it("creates skill_templates table", () => {
    const row = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_templates'"
    ).get();
    expect(row).toBeDefined();
  });

  it("facts table has invalidated_at column", () => {
    const cols = db.prepare("PRAGMA table_info(facts)").all() as { name: string }[];
    expect(cols.map(c => c.name)).toContain("invalidated_at");
  });

  it("outcome_journal table has challenge_instances column", () => {
    const cols = db.prepare("PRAGMA table_info(outcome_journal)").all() as { name: string }[];
    expect(cols.map(c => c.name)).toContain("challenge_instances");
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/memory/db-schema-v17.test.ts
```
Expected: FAIL — tables and columns don't exist yet.

- [ ] **Step 3: Add migration to `src/memory/db.ts`**

Find the `SCHEMA_VERSION` constant and increment it. Find the migration guard block (pattern: `if (currentVersion < 16) { ... }`). Add after the last existing migration block:

```typescript
// Change the constant at top of file:
const SCHEMA_VERSION = 17; // was 16

// Add at the END of the migration function, after all existing `if (currentVersion < N)` blocks:
if (currentVersion < 17) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_task_ledger (
      id            TEXT PRIMARY KEY,
      session_id    TEXT NOT NULL,
      user_id       TEXT NOT NULL,
      task_id       TEXT NOT NULL,
      subgoal_index INTEGER NOT NULL,
      subgoal_text  TEXT NOT NULL,
      state_json    TEXT NOT NULL,
      status        TEXT NOT NULL DEFAULT 'in_progress',
      attempt_count INTEGER NOT NULL DEFAULT 0,
      created_at    TEXT NOT NULL,
      resumed_at    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_task_ledger_user
      ON owl_task_ledger(user_id, status);

    CREATE TABLE IF NOT EXISTS reflexion_critiques (
      id               TEXT PRIMARY KEY,
      task_category    TEXT NOT NULL,
      complexity_tier  TEXT NOT NULL,
      tool_sequence    TEXT NOT NULL,
      critique_text    TEXT NOT NULL,
      embedding        BLOB NOT NULL,
      used_count       INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_critiques_category
      ON reflexion_critiques(task_category, complexity_tier);

    CREATE TABLE IF NOT EXISTS skill_templates (
      id            TEXT PRIMARY KEY,
      name          TEXT UNIQUE NOT NULL,
      source        TEXT NOT NULL DEFAULT 'auto',
      template_text TEXT NOT NULL,
      trigger_desc  TEXT NOT NULL,
      embedding     BLOB NOT NULL,
      success_count INTEGER NOT NULL DEFAULT 0,
      installed_at  TEXT NOT NULL,
      last_used_at  TEXT
    );
  `);

  // Add columns to existing tables (use ALTER TABLE; SQLite doesn't support IF NOT EXISTS on columns)
  const factsColumns = (db.prepare("PRAGMA table_info(facts)").all() as {name:string}[]).map(c => c.name);
  if (!factsColumns.includes("invalidated_at")) {
    db.exec("ALTER TABLE facts ADD COLUMN invalidated_at TEXT;");
  }

  const journalColumns = (db.prepare("PRAGMA table_info(outcome_journal)").all() as {name:string}[]).map(c => c.name);
  if (!journalColumns.includes("challenge_instances")) {
    db.exec("ALTER TABLE outcome_journal ADD COLUMN challenge_instances INTEGER NOT NULL DEFAULT 0;");
  }

  db.prepare("PRAGMA user_version = 17").run();
}
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
npx vitest run __tests__/memory/db-schema-v17.test.ts
```
Expected: 5 tests PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
npm test
```
Expected: All existing tests PASS plus 5 new.

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-schema-v17.test.ts
git commit -m "feat(db): schema v17 — owl_task_ledger, reflexion_critiques, skill_templates tables"
```

---

### Task 2: Add New EventBus Events

**Files:**
- Modify: `src/gateway/event-bus.ts`
- Test: `__tests__/gateway/event-bus-events.test.ts`

Read `src/gateway/event-bus.ts` first. The `GatewaySystemEvent` union type needs three new variants: `task:failed`, `fact:extracted`, `session:ended`.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/gateway/event-bus-events.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("new GatewaySystemEvent types", () => {
  it("emits and receives task:failed event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("task:failed", handler);
    bus.emit({ type: "task:failed", userId: "u1", taskDescription: "test task", toolSequence: ["web"], errorSummary: "404", category: "research", complexityTier: "medium" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "task:failed", userId: "u1" }));
  });

  it("emits and receives fact:extracted event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("fact:extracted", handler);
    bus.emit({ type: "fact:extracted", userId: "u1", factText: "user likes TypeScript", factId: "f1" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "fact:extracted", factText: "user likes TypeScript" }));
  });

  it("emits and receives session:ended event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("session:ended", handler);
    bus.emit({ type: "session:ended", userId: "u1", sessionId: "s1" });
    expect(handler).toHaveBeenCalledWith(expect.objectContaining({ type: "session:ended", sessionId: "s1" }));
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/gateway/event-bus-events.test.ts
```
Expected: FAIL — TypeScript type errors on unknown event types.

- [ ] **Step 3: Add new event types to `src/gateway/event-bus.ts`**

Find the `GatewaySystemEvent` type union. Add three new variants at the end of the union:

```typescript
// Add to GatewaySystemEvent union (after existing last entry):
  | { type: "task:failed";     userId: string; taskDescription: string; toolSequence: string[]; errorSummary: string; category: string; complexityTier: string }
  | { type: "fact:extracted";  userId: string; factText: string; factId: string }
  | { type: "session:ended";   userId: string; sessionId: string }
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
npx vitest run __tests__/gateway/event-bus-events.test.ts
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/event-bus.ts __tests__/gateway/event-bus-events.test.ts
git commit -m "feat(event-bus): add task:failed, fact:extracted, session:ended events"
```

---

### Task 3: TaskLedger SQLite Persistence

**Files:**
- Modify: `src/engine/task-ledger.ts`
- Modify: `src/engine/orchestrator.ts`
- Test: `__tests__/engine/task-ledger-persistence.test.ts`

Read `src/engine/task-ledger.ts` fully first. The `TaskLedgerStore` class already has `save()` and `load()` methods — we're adding `persistSubgoal()` (called at each subgoal transition) and `loadIncomplete()` (called at session start).

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/engine/task-ledger-persistence.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { TaskLedgerStore } from "../../src/engine/task-ledger.js";

describe("TaskLedgerStore persistence", () => {
  let db: InstanceType<typeof Database>;
  let store: TaskLedgerStore;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
    store = new TaskLedgerStore(db as any);
  });

  afterEach(() => db.close());

  it("persistSubgoal writes row to owl_task_ledger", async () => {
    await store.persistSubgoal({
      id: "ledger-1",
      sessionId: "s1",
      userId: "u1",
      taskId: "task-1",
      subgoalIndex: 0,
      subgoalText: "Search for TypeScript docs",
      stateJson: JSON.stringify({ tools: [] }),
      status: "in_progress",
      attemptCount: 1,
    });
    const row = db.prepare("SELECT * FROM owl_task_ledger WHERE id = 'ledger-1'").get() as any;
    expect(row).toBeDefined();
    expect(row.subgoal_text).toBe("Search for TypeScript docs");
  });

  it("loadIncomplete returns in_progress tasks for user", async () => {
    await store.persistSubgoal({
      id: "ledger-2",
      sessionId: "s2",
      userId: "u2",
      taskId: "task-2",
      subgoalIndex: 1,
      subgoalText: "Fetch results",
      stateJson: "{}",
      status: "in_progress",
      attemptCount: 1,
    });
    const result = await store.loadIncomplete("u2");
    expect(result).not.toBeNull();
    expect(result!.subgoalText).toBe("Fetch results");
  });

  it("loadIncomplete returns null when no incomplete tasks", async () => {
    const result = await store.loadIncomplete("no-such-user");
    expect(result).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/engine/task-ledger-persistence.test.ts
```
Expected: FAIL — `persistSubgoal` and `loadIncomplete` do not exist.

- [ ] **Step 3: Add methods to `src/engine/task-ledger.ts`**

Add the `PersistSubgoalArgs` interface and two new methods to `TaskLedgerStore`. Add after the existing `_parse` method:

```typescript
export interface PersistSubgoalArgs {
  id: string;
  sessionId: string;
  userId: string;
  taskId: string;
  subgoalIndex: number;
  subgoalText: string;
  stateJson: string;
  status: string;
  attemptCount: number;
}

// Add these methods to TaskLedgerStore class:

async persistSubgoal(args: PersistSubgoalArgs): Promise<void> {
  this.db.prepare(`
    INSERT INTO owl_task_ledger
      (id, session_id, user_id, task_id, subgoal_index, subgoal_text, state_json, status, attempt_count, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      subgoal_index = excluded.subgoal_index,
      subgoal_text  = excluded.subgoal_text,
      state_json    = excluded.state_json,
      status        = excluded.status,
      attempt_count = excluded.attempt_count
  `).run(
    args.id, args.sessionId, args.userId, args.taskId,
    args.subgoalIndex, args.subgoalText, args.stateJson,
    args.status, args.attemptCount, new Date().toISOString()
  );
}

async loadIncomplete(userId: string): Promise<PersistSubgoalArgs | null> {
  const row = this.db.prepare(`
    SELECT * FROM owl_task_ledger
    WHERE user_id = ? AND status = 'in_progress'
    ORDER BY created_at DESC
    LIMIT 1
  `).get(userId) as any;
  if (!row) return null;
  return {
    id: row.id,
    sessionId: row.session_id,
    userId: row.user_id,
    taskId: row.task_id,
    subgoalIndex: row.subgoal_index,
    subgoalText: row.subgoal_text,
    stateJson: row.state_json,
    status: row.status,
    attemptCount: row.attempt_count,
  };
}

async markComplete(id: string): Promise<void> {
  this.db.prepare(
    "UPDATE owl_task_ledger SET status = 'complete', resumed_at = ? WHERE id = ?"
  ).run(new Date().toISOString(), id);
}
```

- [ ] **Step 4: Wire into `src/engine/orchestrator.ts`**

Read `src/engine/orchestrator.ts`. At the start of `run()`, after the ledger is created, add incomplete-task resumption. At each subgoal transition (find where `subGoals` are iterated), call `persistSubgoal`.

In `run()` method, add after `const ledger = this.ledgerStore.create(...)`:

```typescript
// Check for incomplete task from prior session
const incomplete = await this.ledgerStore.loadIncomplete(ctx.userId);
if (incomplete && incomplete.taskId !== ledger.id) {
  const resumeMsg = `Picking up your task from a prior session — I was on step ${incomplete.subgoalIndex + 1}: "${incomplete.subgoalText}". Continuing now.`;
  await ctx.onProgress?.(resumeMsg);
}
```

After each subGoal execution attempt, call `persistSubgoal`:

```typescript
await this.ledgerStore.persistSubgoal({
  id: ledger.id,
  sessionId: ctx.sessionId,
  userId: ctx.userId,
  taskId: ledger.id,
  subgoalIndex: currentSubgoalIndex,
  subgoalText: currentSubgoal.description ?? "",
  stateJson: JSON.stringify({ toolsUsed, lastError }),
  status: "in_progress",
  attemptCount: attemptCount,
});
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/engine/task-ledger-persistence.test.ts
npm test
```
Expected: New tests PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/engine/task-ledger.ts src/engine/orchestrator.ts __tests__/engine/task-ledger-persistence.test.ts
git commit -m "feat(task-ledger): SQLite persistence — survive restarts, resume incomplete tasks"
```

---

### Task 4: HITLEscalator

**Files:**
- Create: `src/intelligence/hitl-escalator.ts`
- Test: `__tests__/intelligence/hitl-escalator.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/hitl-escalator.test.ts
import { describe, it, expect } from "vitest";
import { HITLEscalator } from "../../src/intelligence/hitl-escalator.js";

describe("HITLEscalator", () => {
  it("does not escalate below threshold", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404 not found", "search docs");
    e.onBlocked("web", "timeout", "search docs");
    expect(e.shouldEscalate(6)).toBe(false); // threshold = 3 at challengeLevel 6
  });

  it("escalates at threshold", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "timeout", "search");
    e.onBlocked("memory", "not found", "search");
    expect(e.shouldEscalate(6)).toBe(true);
  });

  it("escalates after 1 failure when challengeLevel is 2", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    expect(e.shouldEscalate(2)).toBe(true);
  });

  it("buildNarration includes attempt summaries", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404 not found", "find docs");
    e.onBlocked("memory", "no match", "find docs");
    e.onBlocked("web", "timeout", "find docs");
    const narration = e.buildNarration();
    expect(narration).toContain("3 approaches");
    expect(narration).toContain("web: 404 not found");
    expect(narration).toContain("genuinely stuck");
  });

  it("buildQuestion returns binary choice", () => {
    const e = new HITLEscalator();
    const q = e.buildQuestion(["try the API directly", "search for a cached version"]);
    expect(q).toContain("(A) try the API directly");
    expect(q).toContain("(B) search for a cached version");
  });

  it("reset clears state", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "404", "search");
    e.reset();
    expect(e.shouldEscalate(6)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/hitl-escalator.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/hitl-escalator.ts`**

```typescript
export class HITLEscalator {
  private blockedAttempts = 0;
  private attemptSummaries: string[] = [];

  onBlocked(toolName: string, reason: string, _subgoal: string): void {
    this.blockedAttempts++;
    this.attemptSummaries.push(`${toolName}: ${reason}`);
  }

  shouldEscalate(challengeLevel: number): boolean {
    const threshold = Math.max(1, Math.min(5, Math.round(challengeLevel / 2)));
    return this.blockedAttempts >= threshold;
  }

  buildNarration(): string {
    const count = this.blockedAttempts;
    const lines = [
      `I've tried ${count} approach${count !== 1 ? "es" : ""}:`,
      ...this.attemptSummaries.map((s, i) => `  ${i + 1}. ${s}`),
      `I'm genuinely stuck. Let me ask you one focused question.`,
    ];
    return lines.join("\n");
  }

  buildQuestion(alternatives: string[]): string {
    if (alternatives.length < 2) return "How should I proceed?";
    return `Should I try (A) ${alternatives[0]} or (B) ${alternatives[1]}?`;
  }

  reset(): void {
    this.blockedAttempts = 0;
    this.attemptSummaries = [];
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/intelligence/hitl-escalator.test.ts
```
Expected: 6 tests PASS.

- [ ] **Step 5: Wire into `src/engine/orchestrator.ts`**

Read `src/engine/orchestrator.ts` around the HITL section (lines 130-140). Find the `// TODO: call this.deps.hitlChannel.pause(request)` comment. Replace the stub with actual HITLEscalator usage.

Add import at top of orchestrator:
```typescript
import { HITLEscalator } from "../intelligence/hitl-escalator.js";
```

In the `OwlOrchestrator` class, add a private field:
```typescript
private hitlEscalator = new HITLEscalator();
```

After each `GoalVerifier` returns `BLOCKED` verdict (find the `tool:goal_blocked` emit or the `BLOCKED` check), call:
```typescript
this.hitlEscalator.onBlocked(toolName, verifierReason, activeSubGoal ?? "");

if (this.hitlEscalator.shouldEscalate(this.deps.owl.dna?.challengeLevel ?? 6)) {
  const narration = this.hitlEscalator.buildNarration();
  await ctx.onProgress?.(narration);

  if (this.deps.hitlChannel) {
    const response = await this.deps.hitlChannel.pause({
      kind: "choice",
      memo: {
        whatIDid: narration,
        whatINeed: "Direction on how to proceed",
        options: ["try an alternative approach", "stop and report what was found"],
        recommendation: "try an alternative approach",
      },
      ledgerSnapshot: ledger,
      pendingAction: "continue task",
    });
    if (!response.approved) {
      finalDecision = "SYNTHESIZE";
      break;
    }
  }
  this.hitlEscalator.reset();
}
```

- [ ] **Step 6: Run full tests**

```bash
npm test
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/intelligence/hitl-escalator.ts src/engine/orchestrator.ts __tests__/intelligence/hitl-escalator.test.ts
git commit -m "feat(intelligence): HITLEscalator — narrate struggle, ask once after N blocked attempts"
```

---

### Task 5: SemanticToolGate

**Files:**
- Create: `src/intelligence/semantic-tool-gate.ts`
- Modify: `src/tools/registry.ts`
- Test: `__tests__/intelligence/semantic-tool-gate.test.ts`

Read `src/tools/registry.ts` lines 45-100 first. The `ToolRegistry` class needs a new `getRelevantTools(query, limit)` method and startup embedding initialization.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/semantic-tool-gate.test.ts
import { describe, it, expect } from "vitest";
import { SemanticToolGate } from "../../src/intelligence/semantic-tool-gate.js";
import type { ToolDefinition } from "../../src/providers/base.js";

const mockTools: ToolDefinition[] = [
  { name: "web", description: "Search the web and fetch URLs", parameters: { type: "object", properties: {}, required: [] } },
  { name: "memory", description: "Store and retrieve user memories and facts", parameters: { type: "object", properties: {}, required: [] } },
  { name: "calendar", description: "Read and write Apple Calendar events", parameters: { type: "object", properties: {}, required: [] } },
  { name: "shell", description: "Execute shell commands and scripts", parameters: { type: "object", properties: {}, required: [] } },
  { name: "vision", description: "Analyze images using multimodal AI", parameters: { type: "object", properties: {}, required: [] } },
];

describe("SemanticToolGate", () => {
  it("returns at most limit tools", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("search the internet for news", 2);
    expect(result.length).toBeLessThanOrEqual(2);
  });

  it("returns web tool for a search query", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("find information on the web", 3);
    expect(result.map(t => t.name)).toContain("web");
  });

  it("returns memory tool for a memory query", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("remember this for later", 3);
    expect(result.map(t => t.name)).toContain("memory");
  });

  it("returns all tools when query is empty string", async () => {
    const gate = new SemanticToolGate();
    await gate.index(mockTools);
    const result = await gate.getRelevant("", mockTools.length);
    expect(result.length).toBe(mockTools.length);
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/semantic-tool-gate.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/semantic-tool-gate.ts`**

```typescript
import type { ToolDefinition } from "../providers/base.js";

function cosineSimilarity(a: Float32Array, b: Float32Array): number {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i]! * b[i]!;
    normA += a[i]! * a[i]!;
    normB += b[i]! * b[i]!;
  }
  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dot / denom;
}

export class SemanticToolGate {
  private embeddings = new Map<string, Float32Array>();
  private tools: ToolDefinition[] = [];
  private embedFn?: (text: string) => Promise<number[]>;

  async index(tools: ToolDefinition[], embedFn?: (text: string) => Promise<number[]>): Promise<void> {
    this.tools = tools;
    this.embedFn = embedFn;
    this.embeddings.clear();
    if (!embedFn) return; // No embedding function — fallback to returning all
    for (const tool of tools) {
      const vec = await embedFn(`${tool.name}: ${tool.description}`);
      this.embeddings.set(tool.name, new Float32Array(vec));
    }
  }

  async getRelevant(query: string, limit: number): Promise<ToolDefinition[]> {
    if (!this.embedFn || this.embeddings.size === 0 || !query.trim()) {
      return this.tools.slice(0, limit);
    }
    const queryVec = new Float32Array(await this.embedFn(query));
    const scored = this.tools.map(tool => {
      const toolVec = this.embeddings.get(tool.name);
      const score = toolVec ? cosineSimilarity(queryVec, toolVec) : 0;
      return { tool, score };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, limit).map(s => s.tool);
  }
}
```

- [ ] **Step 4: Add `getRelevantTools()` to `src/tools/registry.ts`**

Add import at top of `registry.ts`:
```typescript
import { SemanticToolGate } from "../intelligence/semantic-tool-gate.js";
```

Add private field to `ToolRegistry` class:
```typescript
private _semanticGate = new SemanticToolGate();
private _gateIndexed = false;
```

Add new method to `ToolRegistry` class (after `getAllDefinitions()`):
```typescript
async getRelevantTools(query: string, limit = 8): Promise<ToolDefinition[]> {
  const allDefs = this.getAllDefinitions();
  if (!this._gateIndexed) {
    const embedFn = this._intentRouter
      ? async (text: string) => {
          // Reuse the intent router's embedding pipeline if available
          const encoded = await (this._intentRouter as any).encode?.(text);
          return encoded ?? [];
        }
      : undefined;
    await this._semanticGate.index(allDefs, embedFn?.toString() ? embedFn : undefined);
    this._gateIndexed = true;
  }
  return this._semanticGate.getRelevant(query, limit);
}

invalidateGateIndex(): void {
  this._gateIndexed = false;
}
```

Call `this.invalidateGateIndex()` inside `register()` and `unregister()` methods.

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/intelligence/semantic-tool-gate.test.ts
npm test
```
Expected: New tests PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/intelligence/semantic-tool-gate.ts src/tools/registry.ts __tests__/intelligence/semantic-tool-gate.test.ts
git commit -m "feat(intelligence): SemanticToolGate — top-K relevant tools per query, reduces LLM tool overload"
```

---

### Task 6: CritiqueRetriever ContextLayer

**Files:**
- Create: `src/intelligence/critique-retriever.ts`
- Modify: `src/context/pipeline.ts` (or wherever layers are registered)
- Test: `__tests__/intelligence/critique-retriever.test.ts`

Read `src/context/layer.ts` for the `ContextLayer` interface. Read how existing layers implement `shouldFire()` and `build()`.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/critique-retriever.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { CritiqueRetriever } from "../../src/intelligence/critique-retriever.js";

describe("CritiqueRetriever", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("returns empty string when no critiques exist", async () => {
    const retriever = new CritiqueRetriever(db as any);
    const result = await retriever.retrieve("research TypeScript docs", "research", "medium");
    expect(result).toBe("");
  });

  it("returns past_lessons block when matching critique exists", async () => {
    // Insert a critique manually
    const buf = Buffer.alloc(4 * 4); // 4-dimensional fake embedding
    [0.9, 0.1, 0.1, 0.1].forEach((v, i) => buf.writeFloatLE(v, i * 4));
    db.prepare(`
      INSERT INTO reflexion_critiques (id, task_category, complexity_tier, tool_sequence, critique_text, embedding, used_count, created_at)
      VALUES ('c1', 'research', 'medium', 'web', 'I searched too broadly. Next time use specific terms.', ?, 0, ?)
    `).run(buf, new Date().toISOString());

    const retriever = new CritiqueRetriever(db as any);
    // Inject a mock embed that returns a similar vector
    (retriever as any).embedFn = async () => [0.9, 0.1, 0.1, 0.1];
    const result = await retriever.retrieve("research TypeScript docs", "research", "medium");
    expect(result).toContain("<past_lessons>");
    expect(result).toContain("I searched too broadly");
  });

  it("shouldFire returns true for non-conversational requests", () => {
    const retriever = new CritiqueRetriever(db as any);
    const layer = retriever.asContextLayer();
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(true);
  });

  it("shouldFire returns false for conversational messages", () => {
    const retriever = new CritiqueRetriever(db as any);
    const layer = retriever.asContextLayer();
    expect(layer.shouldFire({ isConversational: true } as any)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/critique-retriever.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/critique-retriever.ts`**

```typescript
import type { ContextLayer, TriageSignals, ContextRequest, LayerResults } from "../context/layer.js";
import type { MemoryDatabase } from "../memory/db.js";

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += (a[i] ?? 0) * (b[i] ?? 0);
    na += (a[i] ?? 0) ** 2;
    nb += (b[i] ?? 0) ** 2;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d === 0 ? 0 : dot / d;
}

export class CritiqueRetriever {
  embedFn?: (text: string) => Promise<number[]>;

  constructor(private readonly db: MemoryDatabase) {}

  async retrieve(query: string, _category: string, _tier: string): Promise<string> {
    const rows = this.db.prepare(
      "SELECT critique_text, embedding FROM reflexion_critiques WHERE used_count < 20 ORDER BY created_at DESC LIMIT 20"
    ).all() as { critique_text: string; embedding: Buffer }[];

    if (rows.length === 0 || !this.embedFn) return "";

    const queryVec = await this.embedFn(query);
    const scored = rows.map(row => {
      const arr = new Float32Array(row.embedding.buffer, row.embedding.byteOffset, row.embedding.byteLength / 4);
      const rowVec = Array.from(arr);
      return { text: row.critique_text, score: cosineSim(queryVec, rowVec) };
    });
    scored.sort((a, b) => b.score - a.score);
    const top = scored.filter(s => s.score > 0.70).slice(0, 2);
    if (top.length === 0) return "";

    const lessons = top.map(s => s.text).join("\n");
    return `<past_lessons>\n${lessons}\n</past_lessons>`;
  }

  asContextLayer(): ContextLayer {
    return {
      name: "critique-retriever",
      priority: 9,
      maxTokens: 200,
      produces: ["past_lessons"],
      dependsOn: [],
      shouldFire: (triage: TriageSignals) => !triage.isConversational,
      build: async (req: ContextRequest, triage: TriageSignals, _deps: LayerResults) => {
        const msg = req.session?.messages?.at(-1)?.content ?? "";
        return this.retrieve(msg, "general", "medium");
      },
    };
  }
}
```

- [ ] **Step 4: Register the layer in the context pipeline**

Read how other layers are registered (look for where `ContextPipeline` is constructed in `src/index.ts` or `src/gateway/core.ts`). Add the `CritiqueRetriever` layer there, passing the db instance and embed function.

Find the layers array construction and add:
```typescript
import { CritiqueRetriever } from "./intelligence/critique-retriever.js";
// ...
const critiqueRetriever = new CritiqueRetriever(db);
// If an embed function is available from UserMemoryStore, inject it:
// critiqueRetriever.embedFn = userMemoryStore.embed.bind(userMemoryStore);
const layers: ContextLayer[] = [
  // ... existing layers ...
  critiqueRetriever.asContextLayer(),
];
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/intelligence/critique-retriever.test.ts
npm test
```
Expected: New tests PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add src/intelligence/critique-retriever.ts __tests__/intelligence/critique-retriever.test.ts
git commit -m "feat(intelligence): CritiqueRetriever — inject past failure lessons before LLM context"
```

---

## Phase B — Learning Loop

### Task 7: ReflexionEngine

**Files:**
- Create: `src/intelligence/reflexion-engine.ts`
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/intelligence/reflexion-engine.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/reflexion-engine.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { ReflexionEngine } from "../../src/intelligence/reflexion-engine.js";

describe("ReflexionEngine", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("writes a critique row after task failure", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "I searched too broadly. Next time use specific terms.", finishReason: "stop", model: "test" }),
    };
    const mockEmbedFn = vi.fn().mockResolvedValue(new Array(4).fill(0.1));

    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);
    await engine.onTaskFailed({
      userId: "u1",
      taskDescription: "Find TypeScript docs",
      toolSequence: ["web", "web"],
      errorSummary: "all fetches returned 404",
      category: "research",
      complexityTier: "medium",
    });

    const rows = db.prepare("SELECT * FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
    expect((rows[0] as any).critique_text).toContain("searched too broadly");
  });

  it("deduplicates identical tool sequence + category", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "same critique", finishReason: "stop", model: "test" }),
    };
    const mockEmbedFn = vi.fn().mockResolvedValue(new Array(4).fill(0.1));
    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);

    const args = { userId: "u1", taskDescription: "find docs", toolSequence: ["web"], errorSummary: "404", category: "research", complexityTier: "medium" };
    await engine.onTaskFailed(args);
    await engine.onTaskFailed(args); // duplicate

    const rows = db.prepare("SELECT * FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
  });

  it("skips writing when qualityScore too low", async () => {
    const mockProvider = { chat: vi.fn() };
    const mockEmbedFn = vi.fn();
    const engine = new ReflexionEngine(db as any, mockProvider as any, mockEmbedFn);

    await engine.onTaskFailed({
      userId: "u1", taskDescription: "x", toolSequence: [], errorSummary: "", category: "research", complexityTier: "low", qualityScore: 0.2,
    });

    expect(mockProvider.chat).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/reflexion-engine.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/reflexion-engine.ts`**

```typescript
import { randomUUID } from "node:crypto";
import type { MemoryDatabase } from "../memory/db.js";
import type { ModelProvider } from "../providers/base.js";

export interface TaskFailedArgs {
  userId: string;
  taskDescription: string;
  toolSequence: string[];
  errorSummary: string;
  category: string;
  complexityTier: string;
  qualityScore?: number;
}

export class ReflexionEngine {
  constructor(
    private readonly db: MemoryDatabase,
    private readonly provider: ModelProvider,
    private readonly embedFn: (text: string) => Promise<number[]>,
  ) {}

  async onTaskFailed(args: TaskFailedArgs): Promise<void> {
    if ((args.qualityScore ?? 1) < 0.3) return;

    const toolKey = args.toolSequence.join(",");
    const existing = this.db.prepare(
      "SELECT id FROM reflexion_critiques WHERE task_category = ? AND tool_sequence = ? LIMIT 1"
    ).get(args.category, toolKey);
    if (existing) return;

    const prompt = [
      `Task attempted: ${args.taskDescription}`,
      `Tools used in sequence: ${toolKey || "none"}`,
      `Final error encountered: ${args.errorSummary}`,
      `Write exactly 2 sentences: (1) why this failed, (2) what to try differently next time.`,
    ].join("\n");

    let critiqueText: string;
    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { maxTokens: 120, temperature: 0.3 },
      );
      critiqueText = response.content.trim();
    } catch {
      return;
    }

    const embedding = await this.embedFn(critiqueText);
    const buf = Buffer.allocUnsafe(embedding.length * 4);
    embedding.forEach((v, i) => buf.writeFloatLE(v, i * 4));

    this.db.prepare(`
      INSERT INTO reflexion_critiques
        (id, task_category, complexity_tier, tool_sequence, critique_text, embedding, used_count, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 0, ?)
    `).run(
      randomUUID(), args.category, args.complexityTier,
      toolKey, critiqueText, buf, new Date().toISOString(),
    );
  }
}
```

- [ ] **Step 4: Wire into `src/gateway/handlers/post-processor.ts`**

Read `post-processor.ts` lines 100-160. Find the `taskQueue.enqueue` pattern. Add ReflexionEngine wiring after the outcome journal section.

Add import:
```typescript
import type { ReflexionEngine } from "../../intelligence/reflexion-engine.js";
```

Add to `PostProcessor` constructor params and class field:
```typescript
private reflexionEngine: ReflexionEngine | null = null
// Add to constructor: reflexionEngine?: ReflexionEngine
// Assign: this.reflexionEngine = reflexionEngine ?? null;
```

Add in `process()` method, after the outcome journal task:
```typescript
if (this.reflexionEngine && metadata?.loopExhausted) {
  const toolsUsed = metadata.toolsUsed ?? [];
  const errorSummary = "Task loop exhausted without completion";
  this.taskQueue.enqueue("reflexion-write", async () => {
    await this.reflexionEngine!.onTaskFailed({
      userId: metadata.userId ?? "",
      taskDescription: messages[0]?.content?.slice(0, 200) ?? "",
      toolSequence: toolsUsed,
      errorSummary,
      category: "general",
      complexityTier: "medium",
    });
  }, "low");
}
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/intelligence/reflexion-engine.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/intelligence/reflexion-engine.ts src/gateway/handlers/post-processor.ts __tests__/intelligence/reflexion-engine.test.ts
git commit -m "feat(intelligence): ReflexionEngine — write self-critiques on failure for future retrieval"
```

---

### Task 8: SentimentProbe

**Files:**
- Create: `src/intelligence/sentiment-probe.ts`
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/intelligence/sentiment-probe.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/sentiment-probe.test.ts
import { describe, it, expect } from "vitest";
import { SentimentProbe, classifySentiment } from "../../src/intelligence/sentiment-probe.js";

describe("classifySentiment", () => {
  it("classifies correction signals", () => {
    expect(classifySentiment("no, that's wrong")).toBe("correction");
    expect(classifySentiment("actually it should be")).toBe("correction");
    expect(classifySentiment("incorrect, try again")).toBe("correction");
  });

  it("classifies positive signals", () => {
    expect(classifySentiment("thanks, perfect!")).toBe("positive");
    expect(classifySentiment("that worked great")).toBe("positive");
    expect(classifySentiment("exactly what I needed")).toBe("positive");
  });

  it("classifies neutral signals", () => {
    expect(classifySentiment("ok")).toBe("neutral");
    expect(classifySentiment("what's next?")).toBe("neutral");
    expect(classifySentiment("")).toBe("neutral");
  });
});

describe("SentimentProbe", () => {
  it("increments challenge_instances on correction", () => {
    const updates: Array<{ sentiment: string; challengeIncrement: boolean }> = [];
    const probe = new SentimentProbe((s, c) => { updates.push({ sentiment: s, challengeIncrement: c }); });
    probe.onNextMessage("no that's not right");
    expect(updates[0]?.sentiment).toBe("correction");
    expect(updates[0]?.challengeIncrement).toBe(true);
  });

  it("does not increment challenge_instances on positive", () => {
    const updates: Array<{ sentiment: string; challengeIncrement: boolean }> = [];
    const probe = new SentimentProbe((s, c) => { updates.push({ sentiment: s, challengeIncrement: c }); });
    probe.onNextMessage("perfect, thanks!");
    expect(updates[0]?.sentiment).toBe("positive");
    expect(updates[0]?.challengeIncrement).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/sentiment-probe.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/sentiment-probe.ts`**

```typescript
const CORRECTION_SIGNALS = ["no,", "no ", "wrong", "actually", "that's not", "thats not", "incorrect", "not right", "try again", "that's wrong", "not what i"];
const POSITIVE_SIGNALS = ["thanks", "thank you", "perfect", "exactly", "great job", "that worked", "worked great", "well done", "exactly what", "👍", "✅"];

export function classifySentiment(text: string): "positive" | "correction" | "neutral" {
  const lower = text.toLowerCase();
  if (CORRECTION_SIGNALS.some(s => lower.includes(s))) return "correction";
  if (POSITIVE_SIGNALS.some(s => lower.includes(s))) return "positive";
  return "neutral";
}

type SentimentCallback = (sentiment: "positive" | "correction" | "neutral", incrementChallenge: boolean) => void;

export class SentimentProbe {
  private pendingUserId: string | null = null;

  constructor(private readonly onResult: SentimentCallback) {}

  arm(userId: string): void {
    this.pendingUserId = userId;
  }

  onNextMessage(text: string): void {
    if (!this.pendingUserId) return;
    this.pendingUserId = null;
    const sentiment = classifySentiment(text);
    const incrementChallenge = sentiment === "correction";
    this.onResult(sentiment, incrementChallenge);
  }
}
```

- [ ] **Step 4: Wire into `src/gateway/handlers/post-processor.ts`**

Add import:
```typescript
import { SentimentProbe } from "../../intelligence/sentiment-probe.js";
```

Add to PostProcessor constructor and field:
```typescript
private sentimentProbe: SentimentProbe | null = null;
```

In constructor body, initialize:
```typescript
this.sentimentProbe = new SentimentProbe((sentiment, incrementChallenge) => {
  if (!metadata?.userId) return;
  // Call OutcomeJournal.updateSentiment if available
  this.taskQueue.enqueue("sentiment-update", async () => {
    if (incrementChallenge && this.ctx.db) {
      this.ctx.db.prepare(
        "UPDATE outcome_journal SET challenge_instances = challenge_instances + 1 WHERE session_id = (SELECT MAX(session_id) FROM outcome_journal WHERE user_id = ?)"
      ).run(metadata?.userId);
    }
  }, "low");
});
```

After task completion in `process()`, arm the probe:
```typescript
if (this.sentimentProbe && metadata?.userId) {
  this.sentimentProbe.arm(metadata.userId);
}
```

Subscribe to next message in the gateway event bus (or call `probe.onNextMessage()` at the top of `process()` if a probe is armed):
```typescript
// At the START of process(), before everything else:
if (this.sentimentProbe) {
  const lastUserMsg = messages.findLast(m => m.role === "user")?.content ?? "";
  this.sentimentProbe.onNextMessage(lastUserMsg);
}
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/intelligence/sentiment-probe.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/intelligence/sentiment-probe.ts src/gateway/handlers/post-processor.ts __tests__/intelligence/sentiment-probe.test.ts
git commit -m "feat(intelligence): SentimentProbe — detect user corrections/approvals, feed challenge_instances"
```

---

### Task 9: SkillTemplateLayer + PatternMiner Extension

**Files:**
- Create: `src/intelligence/skill-template-layer.ts`
- Modify: `src/skills/pattern-miner.ts`
- Modify: `src/tools/invoke-skill.ts` (new tool)
- Modify: `src/skills/wizard.ts`
- Test: `__tests__/intelligence/skill-template-layer.test.ts`

Read `src/skills/pattern-miner.ts` fully. Read `src/skills/executor.ts` lines 1-40. Read `src/skills/wizard.ts` lines 1-50.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/skill-template-layer.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { SkillTemplateLayer } from "../../src/intelligence/skill-template-layer.js";

describe("SkillTemplateLayer", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("returns empty string when no templates exist", async () => {
    const layer = new SkillTemplateLayer(db as any);
    const result = await layer.retrieve("research TypeScript");
    expect(result).toBe("");
  });

  it("returns proven_approach block when matching template exists", async () => {
    const buf = Buffer.alloc(4 * 4);
    [0.9, 0.1, 0.1, 0.1].forEach((v, i) => buf.writeFloatLE(v, i * 4));
    db.prepare(`
      INSERT INTO skill_templates (id, name, source, template_text, trigger_desc, embedding, success_count, installed_at)
      VALUES ('t1', 'web-research', 'auto', 'To research a topic: web(search) → web(fetch) → summarize', 'research, find information, look up', ?, 3, ?)
    `).run(buf, new Date().toISOString());

    const layer = new SkillTemplateLayer(db as any);
    (layer as any).embedFn = async () => [0.9, 0.1, 0.1, 0.1];
    const result = await layer.retrieve("find information about Node.js");
    expect(result).toContain("<proven_approach>");
    expect(result).toContain("web(search)");
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/skill-template-layer.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/skill-template-layer.ts`**

```typescript
import { randomUUID } from "node:crypto";
import type { MemoryDatabase } from "../memory/db.js";
import type { ContextLayer, TriageSignals, ContextRequest, LayerResults } from "../context/layer.js";

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += (a[i] ?? 0) * (b[i] ?? 0);
    na += (a[i] ?? 0) ** 2;
    nb += (b[i] ?? 0) ** 2;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d === 0 ? 0 : dot / d;
}

export class SkillTemplateLayer {
  embedFn?: (text: string) => Promise<number[]>;

  constructor(private readonly db: MemoryDatabase) {}

  async storeTemplate(name: string, templateText: string, triggerDesc: string, source: "auto" | "marketplace" | "user" = "auto"): Promise<void> {
    if (!this.embedFn) return;
    const embedding = await this.embedFn(triggerDesc);
    const buf = Buffer.allocUnsafe(embedding.length * 4);
    embedding.forEach((v, i) => buf.writeFloatLE(v, i * 4));
    this.db.prepare(`
      INSERT INTO skill_templates (id, name, source, template_text, trigger_desc, embedding, success_count, installed_at)
      VALUES (?, ?, ?, ?, ?, ?, 0, ?)
      ON CONFLICT(name) DO UPDATE SET template_text = excluded.template_text, trigger_desc = excluded.trigger_desc
    `).run(randomUUID(), name, source, templateText, triggerDesc, buf, new Date().toISOString());
  }

  async retrieve(query: string): Promise<string> {
    const rows = this.db.prepare(
      "SELECT template_text, trigger_desc, embedding FROM skill_templates WHERE source = 'auto' OR source = 'marketplace' OR source = 'user' ORDER BY success_count DESC LIMIT 30"
    ).all() as { template_text: string; trigger_desc: string; embedding: Buffer }[];

    if (rows.length === 0 || !this.embedFn) return "";

    const queryVec = await this.embedFn(query);
    const scored = rows.map(row => {
      const arr = new Float32Array(row.embedding.buffer, row.embedding.byteOffset, row.embedding.byteLength / 4);
      return { text: row.template_text, score: cosineSim(queryVec, Array.from(arr)) };
    });
    scored.sort((a, b) => b.score - a.score);
    const top = scored.find(s => s.score > 0.75);
    if (!top) return "";
    return `<proven_approach>\n${top.text}\n</proven_approach>`;
  }

  asContextLayer(): ContextLayer {
    return {
      name: "skill-template",
      priority: 8,
      maxTokens: 150,
      produces: ["proven_approach"],
      dependsOn: [],
      shouldFire: (triage: TriageSignals) => !triage.isConversational,
      build: async (req: ContextRequest, _triage: TriageSignals, _deps: LayerResults) => {
        const msg = req.session?.messages?.at(-1)?.content ?? "";
        return this.retrieve(msg);
      },
    };
  }
}
```

- [ ] **Step 4: Extend `src/skills/pattern-miner.ts`**

Read the full file. Find the `SkillPatternMiner` class. Add `onOutcomeSuccess()` method:

```typescript
// Add to SkillPatternMiner class — call this from PostProcessor on outcome:success with quality > 0.8
async onOutcomeSuccess(toolSequence: string[], taskDescription: string, qualityScore: number, templateLayer?: import("../intelligence/skill-template-layer.js").SkillTemplateLayer): Promise<void> {
  if (qualityScore < 0.8 || toolSequence.length < 2) return;
  if (!templateLayer) return;

  const toolSummary = toolSequence.map(t => `${t}()`).join(" → ");
  const taskType = taskDescription.slice(0, 50).toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
  const templateText = `To ${taskType}: ${toolSummary}`;
  const triggerDesc = `${taskType} tasks involving ${toolSequence.join(", ")}`;
  const name = `auto-${taskType.replace(/\s+/g, "-").slice(0, 40)}`;

  await templateLayer.storeTemplate(name, templateText, triggerDesc, "auto");
}
```

- [ ] **Step 5: Create `src/tools/invoke-skill.ts`**

```typescript
import type { ToolImplementation, ToolContext } from "./registry.js";
import { toolError, toolSuccess } from "./tool-error.js";

export function createInvokeSkillTool(skillExecutor?: { executeByName(name: string, params: Record<string, unknown>): Promise<string> }): ToolImplementation {
  return {
    definition: {
      name: "invoke_skill",
      description: "Explicitly invoke a named skill. Use when you know the exact skill name to execute. Example: invoke_skill with name='web-research' to run the web research skill template.",
      parameters: {
        type: "object",
        properties: {
          name: { type: "string", description: "Name of the skill to invoke" },
          params: { type: "string", description: "JSON string of parameters to pass to the skill" },
        },
        required: ["name"],
      },
      capabilities: ["skill_invoke"],
    },
    category: "cognitive" as any,
    execute: async (args: Record<string, unknown>, _ctx: ToolContext): Promise<string> => {
      const name = args["name"] as string;
      let params: Record<string, unknown> = {};
      if (args["params"]) {
        try { params = JSON.parse(args["params"] as string); } catch { /* ignore */ }
      }
      if (!skillExecutor) {
        return toolError("NO_EXECUTOR", "Skill executor not configured.", "Skills engine may not be running.");
      }
      try {
        const result = await skillExecutor.executeByName(name, params);
        return toolSuccess({ skillName: name, result });
      } catch (err) {
        return toolError("SKILL_FAILED", `Skill '${name}' failed: ${String(err)}`);
      }
    },
  };
}
```

- [ ] **Step 6: Add `browse` to `src/skills/wizard.ts`**

Read `wizard.ts`. Find the wizard's command handler. Add a `browse` case that calls `clawhub.search("", limit)` and formats results:

```typescript
// Add to the wizard's command dispatch (find the switch/if-else on verb):
case "browse":
case "marketplace": {
  const category = args[0] ?? "";
  try {
    const results = await this.clawhub.search(category || "popular", 10);
    if (results.length === 0) return "No skills found in marketplace for that query.";
    const lines = results.map((r: any) => `• **${r.name}** — ${r.description}\n  Install: /skills install ${r.slug}`);
    return `**Skill Marketplace** (showing ${results.length}):\n\n${lines.join("\n\n")}`;
  } catch (err) {
    return `Marketplace unavailable: ${String(err)}`;
  }
}
```

- [ ] **Step 7: Run tests**

```bash
npx vitest run __tests__/intelligence/skill-template-layer.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/intelligence/skill-template-layer.ts src/skills/pattern-miner.ts src/tools/invoke-skill.ts src/skills/wizard.ts __tests__/intelligence/skill-template-layer.test.ts
git commit -m "feat(intelligence): SkillTemplateLayer — auto-generate NL templates from successful tool sequences"
```

---

### Task 10: Anti-Sycophancy + Evolution Signal

**Files:**
- Modify: `src/context/pipeline.ts` (or layer registration site)
- Modify: `src/owls/evolution.ts`
- Test: `__tests__/intelligence/anti-sycophancy.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/anti-sycophancy.test.ts
import { describe, it, expect } from "vitest";
import { buildChallengeDirective, CHALLENGE_DIRECTIVES } from "../../src/intelligence/challenge-directive.js";

describe("buildChallengeDirective", () => {
  it("returns supportive for low challengeLevel", () => {
    expect(buildChallengeDirective(1)).toBe(CHALLENGE_DIRECTIVES.low);
    expect(buildChallengeDirective(3)).toBe(CHALLENGE_DIRECTIVES.low);
  });

  it("returns honest for mid challengeLevel", () => {
    expect(buildChallengeDirective(4)).toBe(CHALLENGE_DIRECTIVES.medium);
    expect(buildChallengeDirective(6)).toBe(CHALLENGE_DIRECTIVES.medium);
  });

  it("returns assertive for high challengeLevel", () => {
    expect(buildChallengeDirective(7)).toBe(CHALLENGE_DIRECTIVES.high);
    expect(buildChallengeDirective(10)).toBe(CHALLENGE_DIRECTIVES.high);
  });
});
```

- [ ] **Step 2: Create `src/intelligence/challenge-directive.ts`**

```typescript
export const CHALLENGE_DIRECTIVES = {
  low:    "Be supportive and encouraging in your responses.",
  medium: "Be honest, including when you disagree. State disagreement diplomatically with clear reasoning.",
  high:   "Challenge the user's assumptions when you have good reason to. Be direct and assertive — act as a trusted advisor, not a yes-man.",
} as const;

export function buildChallengeDirective(challengeLevel: number): string {
  if (challengeLevel <= 3) return CHALLENGE_DIRECTIVES.low;
  if (challengeLevel <= 6) return CHALLENGE_DIRECTIVES.medium;
  return CHALLENGE_DIRECTIVES.high;
}
```

- [ ] **Step 3: Run test to confirm it passes**

```bash
npx vitest run __tests__/intelligence/anti-sycophancy.test.ts
```
Expected: 3 tests PASS.

- [ ] **Step 4: Register as ContextLayer**

Find where ContextPipeline layers are assembled (in `src/index.ts` or `src/gateway/core.ts`). Add:

```typescript
import { buildChallengeDirective } from "./intelligence/challenge-directive.js";
import type { ContextLayer } from "./context/layer.js";

const challengeLayer: ContextLayer = {
  name: "challenge-directive",
  priority: 2,
  maxTokens: 60,
  produces: ["challenge_style"],
  dependsOn: [],
  shouldFire: () => true,
  build: async (req, _triage, _deps) => {
    const challengeLevel = (req.deps as any)?.owl?.dna?.challengeLevel ?? 6;
    return buildChallengeDirective(challengeLevel);
  },
};
// Add challengeLayer to layers array
```

- [ ] **Step 5: Add challenge_instances signal to `src/owls/evolution.ts`**

Read `evolution.ts`. Find the `evolve()` or mutation method. Add at the end of mutation logic:

```typescript
// Read challenge_instances signal from recent sessions
if (deps?.db) {
  const recentJournal = deps.db.prepare(`
    SELECT AVG(challenge_instances) as avg_challenge,
           COUNT(*) as session_count
    FROM outcome_journal
    WHERE user_id = ? AND created_at > datetime('now', '-10 days')
  `).get(userId) as { avg_challenge: number; session_count: number } | undefined;

  if (recentJournal && recentJournal.session_count >= 3) {
    const currentLevel = owl.dna.challengeLevel ?? 6;
    if (recentJournal.avg_challenge > 2 && currentLevel < 9) {
      mutations.push({ field: "challengeLevel", delta: +1, reason: "user responds positively to pushback" });
    } else if (recentJournal.avg_challenge === 0 && currentLevel > 2) {
      mutations.push({ field: "challengeLevel", delta: -1, reason: "reducing challenge — low pushback requests" });
    }
  }
}
```

- [ ] **Step 6: Run full tests**

```bash
npm test
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/intelligence/challenge-directive.ts src/owls/evolution.ts __tests__/intelligence/anti-sycophancy.test.ts
git commit -m "feat(intelligence): anti-sycophancy directive + challenge_instances evolution signal"
```

---

## Phase C — Memory Depth

### Task 11: FactInvalidator

**Files:**
- Create: `src/intelligence/fact-invalidator.ts`
- Modify: `src/memory/fact-store.ts` (emit fact:extracted + add invalidated_at filter)
- Test: `__tests__/intelligence/fact-invalidator.test.ts`

Read `src/memory/fact-store.ts` or wherever facts are written. Find the `store()` or `upsert()` method.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/fact-invalidator.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { FactInvalidator } from "../../src/intelligence/fact-invalidator.js";

describe("FactInvalidator", () => {
  let db: InstanceType<typeof Database>;

  function insertFact(id: string, text: string, embedding: number[]) {
    const buf = Buffer.allocUnsafe(embedding.length * 4);
    embedding.forEach((v, i) => buf.writeFloatLE(v, i * 4));
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at, embedding)
      VALUES (?, 'u1', 'aria', ?, 'personal', 0.9, 'explicit', 0, datetime('now'), datetime('now'), ?)
    `).run(id, text, buf);
  }

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("invalidates old location fact when new location extracted with temporal trigger", async () => {
    insertFact("f1", "User lives in London", [0.9, 0.1, 0.0, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.9, 0.1, 0.0, 0.0]; // similar to London fact

    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f1'").get() as any;
    expect(row.invalidated_at).not.toBeNull();
  });

  it("does NOT invalidate when no temporal trigger present", async () => {
    insertFact("f2", "User likes TypeScript", [0.8, 0.1, 0.1, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.8, 0.1, 0.1, 0.0];

    await invalidator.check("User prefers TypeScript over JavaScript", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f2'").get() as any;
    expect(row.invalidated_at).toBeNull();
  });

  it("does NOT invalidate when similarity is below threshold", async () => {
    insertFact("f3", "User lives in London", [0.9, 0.1, 0.0, 0.0]);

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.1, 0.9, 0.0, 0.0]; // different vector

    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f3'").get() as any;
    expect(row.invalidated_at).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/fact-invalidator.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/fact-invalidator.ts`**

```typescript
import type { MemoryDatabase } from "../memory/db.js";

const TEMPORAL_TRIGGERS = [
  "moved to", "now at", "now works at", "switched to",
  "no longer", "changed to", "actually", "left ", "quit ",
  "joined ", "starting at", "used to ", "recently moved",
];

function cosineSim(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) {
    dot += (a[i] ?? 0) * (b[i] ?? 0);
    na += (a[i] ?? 0) ** 2;
    nb += (b[i] ?? 0) ** 2;
  }
  const d = Math.sqrt(na) * Math.sqrt(nb);
  return d === 0 ? 0 : dot / d;
}

function entityOverlap(a: string, b: string): number {
  const aWords = new Set(a.toLowerCase().split(/\s+/).filter(w => w.length > 3));
  const bWords = new Set(b.toLowerCase().split(/\s+/).filter(w => w.length > 3));
  if (aWords.size === 0 || bWords.size === 0) return 0;
  const intersection = [...aWords].filter(w => bWords.has(w)).length;
  return intersection / Math.max(aWords.size, bWords.size);
}

export class FactInvalidator {
  embedFn?: (text: string) => Promise<number[]>;

  constructor(private readonly db: MemoryDatabase) {}

  async check(newFactText: string, userId: string): Promise<void> {
    const hasTrigger = TEMPORAL_TRIGGERS.some(t => newFactText.toLowerCase().includes(t));
    if (!hasTrigger || !this.embedFn) return;

    const newVec = await this.embedFn(newFactText);

    const candidates = this.db.prepare(`
      SELECT id, fact, embedding
      FROM facts
      WHERE user_id = ? AND invalidated_at IS NULL
      ORDER BY created_at DESC
      LIMIT 30
    `).all(userId) as { id: string; fact: string; embedding: Buffer }[];

    for (const candidate of candidates) {
      const arr = new Float32Array(candidate.embedding.buffer, candidate.embedding.byteOffset, candidate.embedding.byteLength / 4);
      const sim = cosineSim(newVec, Array.from(arr));
      const overlap = entityOverlap(candidate.fact, newFactText);
      if (sim > 0.85 && overlap > 0.7) {
        this.db.prepare(
          "UPDATE facts SET invalidated_at = ? WHERE id = ?"
        ).run(new Date().toISOString(), candidate.id);
      }
    }
  }
}
```

- [ ] **Step 4: Emit `fact:extracted` and filter invalidated_at**

Find where facts are written to the DB (likely `src/memory/fact-store.ts` or similar). After each successful insert, emit `fact:extracted` event:

```typescript
// After: this.db.prepare("INSERT INTO facts ...").run(...)
if (this.eventBus) {
  this.eventBus.emit({ type: "fact:extracted", userId, factText: fact, factId: id });
}
```

Find all `SELECT ... FROM facts` queries. Add `AND invalidated_at IS NULL` to each WHERE clause that doesn't already have it.

- [ ] **Step 5: Subscribe FactInvalidator to event bus**

In `src/index.ts` or wherever event bus subscriptions are wired:

```typescript
import { FactInvalidator } from "./intelligence/fact-invalidator.js";
// ...
const factInvalidator = new FactInvalidator(db);
factInvalidator.embedFn = /* same embed fn as UserMemoryStore */;
eventBus.on("fact:extracted", async (e) => {
  await factInvalidator.check(e.factText, e.userId);
});
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/intelligence/fact-invalidator.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/intelligence/fact-invalidator.ts src/memory/fact-store.ts __tests__/intelligence/fact-invalidator.test.ts
git commit -m "feat(intelligence): FactInvalidator — mark contradicted facts invalid via temporal trigger heuristic"
```

---

### Task 12: memory_write + memory_invalidate Actions

**Files:**
- Modify: `src/tools/memory-unified.ts`
- Test: `__tests__/tools/memory-unified-extended.test.ts`

Read `src/tools/memory-unified.ts` fully first.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/tools/memory-unified-extended.test.ts
import { describe, it, expect, vi } from "vitest";
import { createMemoryUnifiedTool } from "../../src/tools/memory-unified.js";

describe("memory_unified — write and invalidate actions", () => {
  it("calls write dep on action:write", async () => {
    const writeFn = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: { id: "f1" } }));
    const tool = createMemoryUnifiedTool({ write: writeFn });
    await tool.execute({ action: "write", content: "prefers TypeScript", category: "preference", confidence: "0.9" }, {} as any);
    expect(writeFn).toHaveBeenCalledWith(expect.objectContaining({ content: "prefers TypeScript" }), expect.anything());
  });

  it("calls invalidate dep on action:invalidate", async () => {
    const invalidateFn = vi.fn().mockResolvedValue(JSON.stringify({ success: true, data: { invalidated: 1 } }));
    const tool = createMemoryUnifiedTool({ invalidate: invalidateFn });
    await tool.execute({ action: "invalidate", query: "lives in London" }, {} as any);
    expect(invalidateFn).toHaveBeenCalledWith(expect.objectContaining({ query: "lives in London" }), expect.anything());
  });

  it("returns ACTION_NOT_SUPPORTED for unsupported action", async () => {
    const tool = createMemoryUnifiedTool({});
    const result = await tool.execute({ action: "write" }, {} as any);
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACTION_NOT_SUPPORTED");
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/tools/memory-unified-extended.test.ts
```
Expected: FAIL — write and invalidate deps don't exist.

- [ ] **Step 3: Extend `src/tools/memory-unified.ts`**

Add `write` and `invalidate` to `MemoryUnifiedDeps`:

```typescript
export interface MemoryUnifiedDeps {
  search?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  store?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  get?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  write?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
  invalidate?: (args: Record<string, unknown>, ctx: ToolContext) => Promise<string>;
}
```

Update tool definition to add new actions in the enum:

```typescript
action: {
  type: "string",
  description: "One of: search, store, get, write, invalidate",
  enum: ["search", "store", "get", "write", "invalidate"]
},
// Add new parameters:
category: { type: "string", description: "Memory category for action:write (e.g. preference, personal, skill)" },
confidence: { type: "string", description: "Confidence score 0-1 for action:write" },
```

Update description:
```
"...action:write to store an inferred fact, action:invalidate to mark a stale fact invalid when user corrects something."
```

The existing `execute` dispatch already routes via `deps[action]` so no other change needed — the new deps `write` and `invalidate` are auto-routed.

- [ ] **Step 4: Wire write + invalidate implementations in `src/index.ts`**

Find where `createMemoryUnifiedTool` is called. Add write and invalidate implementations:

```typescript
const memoryUnified = createMemoryUnifiedTool({
  // ... existing search, store, get ...
  write: async (args, _ctx) => {
    const content = args["content"] as string;
    const category = (args["category"] as string) ?? "preference";
    const confidence = parseFloat((args["confidence"] as string) ?? "0.8");
    // Store fact using FactStore or direct DB insert
    const id = randomUUID();
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, 'owl_inferred', 0, datetime('now'), datetime('now'))
    `).run(id, currentUserId, owlName, content, category, confidence);
    return toolSuccess({ id, stored: content });
  },
  invalidate: async (args, _ctx) => {
    const query = args["query"] as string;
    // Semantic search then invalidate top match
    const rows = db.prepare("SELECT id, fact FROM facts WHERE user_id = ? AND invalidated_at IS NULL LIMIT 20").all(currentUserId) as {id:string, fact:string}[];
    const match = rows.find(r => r.fact.toLowerCase().includes(query.toLowerCase()));
    if (!match) return toolSuccess({ invalidated: 0, message: "No matching fact found" });
    db.prepare("UPDATE facts SET invalidated_at = datetime('now') WHERE id = ?").run(match.id);
    return toolSuccess({ invalidated: 1, fact: match.fact });
  },
});
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/tools/memory-unified-extended.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tools/memory-unified.ts __tests__/tools/memory-unified-extended.test.ts
git commit -m "feat(tools): memory_unified — add write and invalidate actions for owl-controlled memory"
```

---

### Task 13: SleepTimeConsolidator

**Files:**
- Create: `src/intelligence/sleep-time-consolidator.ts`
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/intelligence/sleep-time-consolidator.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/sleep-time-consolidator.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { SleepTimeConsolidator } from "../../src/intelligence/sleep-time-consolidator.js";

describe("SleepTimeConsolidator", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("runs consolidation on session:ended", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "User works best in short focused bursts.", finishReason: "stop", model: "test" }),
    };
    const mockPelletStore = { store: vi.fn().mockResolvedValue("p1") };

    const consolidator = new SleepTimeConsolidator(db as any, mockProvider as any, mockPelletStore as any);
    await consolidator.onSessionEnded("u1", "s1");

    // Provider should have been called (or not, if no sessions)
    // If no prior sessions, provider is NOT called (nothing to consolidate)
    expect(mockProvider.chat.mock.calls.length).toBeLessThanOrEqual(1);
  });

  it("debounces — second call within 60min is skipped", async () => {
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: "insight", finishReason: "stop", model: "test" }) };
    const mockPelletStore = { store: vi.fn().mockResolvedValue("p1") };

    // Pre-seed a digest so provider actually gets called
    db.prepare(`
      INSERT INTO summaries (id, session_id, user_id, owl_name, from_seq, to_seq, message_count, summary_text, key_facts, decisions, failed_approaches, open_questions, tokens_saved, created_at)
      VALUES ('sum1', 's0', 'u1', 'aria', 0, 5, 5, 'Previous session summary', '[]', '[]', '[]', '[]', 100, datetime('now', '-2 hours'))
    `).run();

    const consolidator = new SleepTimeConsolidator(db as any, mockProvider as any, mockPelletStore as any);
    await consolidator.onSessionEnded("u1", "s1");
    await consolidator.onSessionEnded("u1", "s2"); // should be debounced

    expect(mockProvider.chat.mock.calls.length).toBeLessThanOrEqual(1);
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/sleep-time-consolidator.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/sleep-time-consolidator.ts`**

```typescript
import { randomUUID } from "node:crypto";
import type { MemoryDatabase } from "../memory/db.js";
import type { ModelProvider } from "../providers/base.js";

interface PelletStore {
  store(pellet: { id: string; userId: string; content: string; tags: string[]; source: string; confidence: number; createdAt: string }): Promise<string>;
}

export class SleepTimeConsolidator {
  private lastRunAt = new Map<string, number>();
  private readonly DEBOUNCE_MS = 60 * 60 * 1000; // 60 minutes

  constructor(
    private readonly db: MemoryDatabase,
    private readonly provider: ModelProvider,
    private readonly pelletStore: PelletStore,
  ) {}

  async onSessionEnded(userId: string, _sessionId: string): Promise<void> {
    const last = this.lastRunAt.get(userId) ?? 0;
    if (Date.now() - last < this.DEBOUNCE_MS) return;
    this.lastRunAt.set(userId, Date.now());

    const recentSummaries = this.db.prepare(`
      SELECT summary_text FROM summaries
      WHERE user_id = ? ORDER BY created_at DESC LIMIT 5
    `).all(userId) as { summary_text: string }[];

    if (recentSummaries.length === 0) return;

    const context = recentSummaries.map((s, i) => `Session ${i + 1}: ${s.summary_text}`).join("\n\n");
    const prompt = `Based on these recent sessions with this user:\n\n${context}\n\nWhat 1-3 new patterns or insights about this user can you infer that aren't explicitly stated? Be specific and concise. Each insight on its own line.`;

    let insights: string;
    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { maxTokens: 200, temperature: 0.4 },
      );
      insights = response.content.trim();
    } catch {
      return;
    }

    const lines = insights.split("\n").map(l => l.trim()).filter(l => l.length > 10);
    for (const line of lines.slice(0, 3)) {
      await this.pelletStore.store({
        id: randomUUID(),
        userId,
        content: line,
        tags: ["sleep_consolidation", "pattern"],
        source: "sleep_consolidation",
        confidence: 0.7,
        createdAt: new Date().toISOString(),
      });
    }
  }
}
```

- [ ] **Step 4: Wire into `src/gateway/handlers/post-processor.ts`**

Add import and field:
```typescript
import type { SleepTimeConsolidator } from "../../intelligence/sleep-time-consolidator.js";
// In PostProcessor class:
private sleepConsolidator: SleepTimeConsolidator | null = null;
```

In `process()`, add at the end:
```typescript
if (this.sleepConsolidator && metadata?.userId && sessionId) {
  this.taskQueue.enqueue("sleep-consolidation", async () => {
    await this.sleepConsolidator!.onSessionEnded(metadata.userId!, sessionId);
  }, "low");
}
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/intelligence/sleep-time-consolidator.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/intelligence/sleep-time-consolidator.ts src/gateway/handlers/post-processor.ts __tests__/intelligence/sleep-time-consolidator.test.ts
git commit -m "feat(intelligence): SleepTimeConsolidator — surface cross-session insights on session:ended"
```

---

### Task 14: OwlStateReporter + `/owl` Command

**Files:**
- Create: `src/intelligence/owl-state-reporter.ts`
- Modify: `src/cli/commands.ts`
- Modify: `src/gateway/adapters/telegram.ts`
- Test: `__tests__/intelligence/owl-state-reporter.test.ts`

Read `src/cli/commands.ts` lines 200-220 (where `/skills` or other commands are registered). Read `src/gateway/adapters/telegram.ts` around the `/skills` command handler.

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/intelligence/owl-state-reporter.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { OwlStateReporter } from "../../src/intelligence/owl-state-reporter.js";

describe("OwlStateReporter", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("reports zero counts when db is empty", async () => {
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("Memory:");
    expect(report).toContain("0 facts");
    expect(report).toContain("0 pellets");
  });

  it("includes fact count when facts exist", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at)
      VALUES ('f1', 'u1', 'aria', 'user likes TypeScript', 'preference', 0.9, 'explicit', 0, datetime('now'), datetime('now'))
    `).run();
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("1 fact");
  });

  it("includes active task when in_progress task exists", async () => {
    db.prepare(`
      INSERT INTO owl_task_ledger (id, session_id, user_id, task_id, subgoal_index, subgoal_text, state_json, status, attempt_count, created_at)
      VALUES ('l1', 's1', 'u1', 't1', 1, 'Search TypeScript docs', '{}', 'in_progress', 2, datetime('now'))
    `).run();
    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("Active task");
    expect(report).toContain("Search TypeScript docs");
  });
});
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
npx vitest run __tests__/intelligence/owl-state-reporter.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create `src/intelligence/owl-state-reporter.ts`**

```typescript
import type { MemoryDatabase } from "../memory/db.js";

export class OwlStateReporter {
  constructor(private readonly db: MemoryDatabase) {}

  async report(userId: string, owlName: string, dna?: Record<string, unknown>): Promise<string> {
    const factCount = (this.db.prepare(
      "SELECT COUNT(*) as n FROM facts WHERE user_id = ? AND invalidated_at IS NULL"
    ).get(userId) as { n: number }).n;

    const pelletCount = (() => {
      try {
        return (this.db.prepare(
          "SELECT COUNT(*) as n FROM pellets WHERE user_id = ?"
        ).get(userId) as { n: number }).n;
      } catch { return 0; }
    })();

    const lastFact = this.db.prepare(
      "SELECT updated_at FROM facts WHERE user_id = ? AND invalidated_at IS NULL ORDER BY updated_at DESC LIMIT 1"
    ).get(userId) as { updated_at: string } | undefined;

    const activeTask = this.db.prepare(
      "SELECT subgoal_text, subgoal_index, created_at FROM owl_task_ledger WHERE user_id = ? AND status = 'in_progress' ORDER BY created_at DESC LIMIT 1"
    ).get(userId) as { subgoal_text: string; subgoal_index: number; created_at: string } | undefined;

    const recentLearning = this.db.prepare(
      "SELECT fact FROM facts WHERE user_id = ? AND source = 'owl_inferred' AND invalidated_at IS NULL ORDER BY created_at DESC LIMIT 1"
    ).get(userId) as { fact: string } | undefined;

    const lines: string[] = [];
    lines.push(`Owl: ${owlName}`);

    if (dna) {
      const dnaFields = ["challengeLevel", "verbosity"].map(k => `${k}=${dna[k] ?? "?"}`).join(" · ");
      lines[0] += `  |  DNA: ${dnaFields}`;
    }

    const ago = lastFact ? timeSince(lastFact.updated_at) : "never";
    lines.push(`Memory: ${factCount} fact${factCount !== 1 ? "s" : ""} · ${pelletCount} pellet${pelletCount !== 1 ? "s" : ""} · last updated ${ago}`);

    if (activeTask) {
      lines.push(`Active task: step ${activeTask.subgoal_index + 1} — "${activeTask.subgoal_text}"`);
    }

    if (recentLearning) {
      lines.push(`Recent learning: "${recentLearning.fact}"`);
    }

    return lines.join("\n");
  }
}

function timeSince(isoDate: string): string {
  const ms = Date.now() - new Date(isoDate).getTime();
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
```

- [ ] **Step 4: Add `/owl` to CLI**

Read `src/cli/commands.ts`. Find where `/skills` or other slash commands are registered. Add:

```typescript
import { OwlStateReporter } from "../intelligence/owl-state-reporter.js";
// In the commands registration section:
program
  .command("owl [subcommand]")
  .description("Show owl status and memory")
  .action(async (subcommand = "status") => {
    if (subcommand === "status" || !subcommand) {
      const reporter = new OwlStateReporter(db);
      const report = await reporter.report(currentUserId, currentOwlName);
      console.log(report);
    }
  });
```

Or if using the `/command` style (find existing pattern in commands.ts and match it exactly):

```typescript
case "/owl":
case "/owl status": {
  const reporter = new OwlStateReporter(ctx.db);
  const report = await reporter.report(ctx.userId, ctx.owlName, ctx.owl?.dna);
  console.log(report);
  break;
}
```

- [ ] **Step 5: Add `/owl status` to Telegram**

Read `src/gateway/adapters/telegram.ts` around the `/skills` command. Add adjacent to it:

```typescript
bot.command("owl", async (ctx) => {
  const subcommand = ctx.message?.text?.split(" ")[1] ?? "status";
  if (subcommand === "status" || subcommand === "") {
    const reporter = new OwlStateReporter(db);
    const report = await reporter.report(userId, owlName, owl?.dna);
    await ctx.reply(report);
  }
});
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/intelligence/owl-state-reporter.test.ts
npm test
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/intelligence/owl-state-reporter.ts src/cli/commands.ts src/gateway/adapters/telegram.ts __tests__/intelligence/owl-state-reporter.test.ts
git commit -m "feat(intelligence): OwlStateReporter + /owl status command — observable owl state"
```

---

### Task 15: Wire Intelligence Module + Integration Test

**Files:**
- Modify: `src/index.ts`
- Create: `__tests__/integration/owl-intelligence.test.ts`

This task wires all intelligence components together in `src/index.ts` and verifies the full pipeline works end-to-end.

- [ ] **Step 1: Read `src/index.ts`**

Find where tools are registered, where event bus is set up, and where ContextPipeline layers are assembled.

- [ ] **Step 2: Wire all components**

Add imports at top of `src/index.ts`:

```typescript
import { FactInvalidator } from "./intelligence/fact-invalidator.js";
import { SleepTimeConsolidator } from "./intelligence/sleep-time-consolidator.js";
import { ReflexionEngine } from "./intelligence/reflexion-engine.js";
import { SentimentProbe } from "./intelligence/sentiment-probe.js";
import { SkillTemplateLayer } from "./intelligence/skill-template-layer.js";
import { CritiqueRetriever } from "./intelligence/critique-retriever.js";
import { OwlStateReporter } from "./intelligence/owl-state-reporter.js";
import { createInvokeSkillTool } from "./tools/invoke-skill.js";
```

After db and eventBus are initialized, add:

```typescript
// ── Intelligence Module ──────────────────────────────────────────
const embedFn = async (text: string): Promise<number[]> => {
  // Reuse provider embed if available, or return empty (gate falls back gracefully)
  try { return (await provider.embed(text, undefined)).embedding; } catch { return []; }
};

const factInvalidator = new FactInvalidator(db);
factInvalidator.embedFn = embedFn;
eventBus.on("fact:extracted", async (e) => {
  await factInvalidator.check(e.factText, e.userId).catch(() => {});
});

const skillTemplateLayer = new SkillTemplateLayer(db);
skillTemplateLayer.embedFn = embedFn;

const critiqueRetriever = new CritiqueRetriever(db);
critiqueRetriever.embedFn = embedFn;

const reflexionEngine = new ReflexionEngine(db, provider, embedFn);
const sleepConsolidator = new SleepTimeConsolidator(db, provider, pelletStore);
```

Register `invoke_skill` tool:

```typescript
toolRegistry.register(createInvokeSkillTool(skillsExecutor));
```

Add layers to ContextPipeline layers array:

```typescript
critiqueRetriever.asContextLayer(),
skillTemplateLayer.asContextLayer(),
```

Wire PostProcessor with new components:

```typescript
const postProcessor = new PostProcessor(
  ctx, taskQueue, eventBus, coordinator, anticipator, costTracker, innerLifeBridge,
  reflexionEngine,    // new
  sleepConsolidator,  // new
);
```

- [ ] **Step 3: Write integration test**

```typescript
// __tests__/integration/owl-intelligence.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { ReflexionEngine } from "../../src/intelligence/reflexion-engine.js";
import { FactInvalidator } from "../../src/intelligence/fact-invalidator.js";
import { SemanticToolGate } from "../../src/intelligence/semantic-tool-gate.js";
import { HITLEscalator } from "../../src/intelligence/hitl-escalator.js";
import { OwlStateReporter } from "../../src/intelligence/owl-state-reporter.js";
import type { ToolDefinition } from "../../src/providers/base.js";

describe("intelligence module integration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
  });

  afterEach(() => db.close());

  it("SemanticToolGate + HITLEscalator do not share state", () => {
    const gate = new SemanticToolGate();
    const esc1 = new HITLEscalator();
    const esc2 = new HITLEscalator();
    esc1.onBlocked("web", "404", "task");
    expect(esc1.shouldEscalate(2)).toBe(true);
    expect(esc2.shouldEscalate(2)).toBe(false); // independent instances
    expect(gate).toBeDefined();
  });

  it("ReflexionEngine writes critique + CritiqueRetriever finds it", async () => {
    let capturedEmbedding: number[] = [];
    const embedFn = async (text: string) => {
      capturedEmbedding = [0.8, 0.2, 0.1, 0.0];
      return capturedEmbedding;
    };
    const mockProvider = {
      chat: async () => ({ content: "Searched too broadly. Use specific terms next time.", finishReason: "stop" as const, model: "test" }),
    };

    const engine = new ReflexionEngine(db as any, mockProvider as any, embedFn);
    await engine.onTaskFailed({ userId: "u1", taskDescription: "find TypeScript docs", toolSequence: ["web"], errorSummary: "404", category: "research", complexityTier: "medium" });

    const rows = db.prepare("SELECT critique_text FROM reflexion_critiques").all();
    expect(rows).toHaveLength(1);
    expect((rows[0] as any).critique_text).toContain("Searched too broadly");
  });

  it("FactInvalidator invalidates London fact when Tokyo extracted", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at, embedding)
      VALUES ('f1', 'u1', 'aria', 'User lives in London', 'personal', 0.9, 'explicit', 0, datetime('now'), datetime('now'), ?)
    `).run(Buffer.from(new Float32Array([0.9, 0.1, 0.0, 0.0]).buffer));

    const invalidator = new FactInvalidator(db as any);
    (invalidator as any).embedFn = async () => [0.9, 0.1, 0.0, 0.0];
    await invalidator.check("User moved to Tokyo", "u1");

    const row = db.prepare("SELECT invalidated_at FROM facts WHERE id = 'f1'").get() as any;
    expect(row.invalidated_at).not.toBeNull();
  });

  it("OwlStateReporter renders correctly with mixed data", async () => {
    db.prepare(`
      INSERT INTO facts (id, user_id, owl_name, fact, category, confidence, source, access_count, created_at, updated_at)
      VALUES ('f1', 'u1', 'aria', 'prefers TypeScript', 'preference', 0.9, 'owl_inferred', 0, datetime('now'), datetime('now'))
    `).run();

    const reporter = new OwlStateReporter(db as any);
    const report = await reporter.report("u1", "aria");
    expect(report).toContain("aria");
    expect(report).toContain("1 fact");
    expect(report).toContain("prefers TypeScript");
  });
});
```

- [ ] **Step 4: Run integration test**

```bash
npx vitest run __tests__/integration/owl-intelligence.test.ts
```
Expected: 4 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
npm test
```
Expected: All 633+ existing tests PASS plus all new intelligence tests.

- [ ] **Step 6: Commit**

```bash
git add src/index.ts __tests__/integration/owl-intelligence.test.ts
git commit -m "feat(intelligence): wire full intelligence module — Reflexion, SentimentProbe, SemanticToolGate, FactInvalidator, SleepTime, OwlState"
```

---

## Phase Gate Verification

After all tasks complete, verify the three user stories manually:

**Story A — Task resumption:**
```bash
npm run dev
# In CLI: ask for a multi-step task
# Kill the process mid-task (Ctrl+C)
# Restart: npm run dev
# Verify: owl says "Picking up your task from [time] — I was on step N..."
```

**Story B — Preference persistence:**
```bash
# Via Telegram or CLI: give a response, then say "actually, make that shorter"
# Next session: owl should not revert
# /owl status — should show recent learning entry
```

**Story C — Trustworthy challenge:**
```bash
# Check owl DNA challengeLevel > 6
# Ask owl for feedback on an idea with a clear flaw
# Owl should push back rather than agree
```

**Final test count check:**
```bash
npm test -- --reporter=verbose | tail -5
```
Expected: 650+ tests passing (633 existing + ~20 new intelligence tests).
