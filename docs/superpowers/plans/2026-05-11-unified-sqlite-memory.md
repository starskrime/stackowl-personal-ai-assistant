# Unified SQLite Memory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-maintained MEMORY.md flat file with a live SQLite query over the existing `facts` table as Tier-0 context, eliminating memory fragmentation.

**Architecture:** `SqliteTier0Layer` (new) replaces `MemoryMdLayer` — it queries `facts WHERE confidence >= 0.8 AND category IN (tier-0-set) ORDER BY confidence DESC LIMIT 30`. `UpdateMemoryTool` gains `db: MemoryDatabase` and writes structured `Fact` rows instead of appending to a text file. A one-shot migration imports existing MEMORY.md content into the facts table so nothing is lost.

**Tech Stack:** better-sqlite3, existing `FactsRepo` / `MemoryDatabase` APIs, Vitest, TypeScript strict.

---

## File Map

| File | Action |
|------|--------|
| `src/memory/db.ts` | Modify — add `getHighConfidenceFacts()` to `FactsRepo` |
| `src/context/layers/sqlite-memory.ts` | Create — `SqliteTier0Layer` |
| `src/context/layers/memory-md.ts` | Keep unchanged — used only as migration source |
| `src/context/index.ts` | Modify — swap `new MemoryMdLayer()` for `new SqliteTier0Layer(deps.db)` |
| `src/tools/update-memory.ts` | Modify — write facts to SQLite via injected `db` |
| `src/memory/memory-migration.ts` | Create — idempotent MEMORY.md → `facts` importer |
| `src/index.ts` | Modify — hold ref to `UpdateMemoryTool`, call `setDb(memoryDb)` after db init, run migration |
| `__tests__/context/sqlite-memory-layer.test.ts` | Create — tests for `SqliteTier0Layer` |
| `__tests__/tools/update-memory.test.ts` | Modify — add SQLite-backed tests |

---

## Task 1: `getHighConfidenceFacts()` in FactsRepo

**Files:**
- Modify: `src/memory/db.ts` — add method to `FactsRepo` class (after `retire()` at ~line 1943)
- Test: `__tests__/memory/facts-repo-tier0.test.ts` (create new)

### Tier-0 categories (defined once, used in both layer and method):

```typescript
// exported constant from db.ts, below the FactCategory type
export const TIER0_CATEGORIES: FactCategory[] = [
  "preference", "personal", "active_goal", "goal",
  "relationship", "habit", "decision",
];
```

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory/facts-repo-tier0.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-facts-tier0-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("FactsRepo.getHighConfidenceFacts", () => {
  it("returns facts with confidence >= 0.8 in tier-0 categories", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Prefers TypeScript strict mode",
      category: "preference", confidence: 0.9, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Has a dog named Max",
      category: "personal", confidence: 0.85, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Low confidence note",
      category: "preference", confidence: 0.5, source: "inferred" });
    db.facts.add({ userId: "default", owlName: "default", fact: "Context tidbit",
      category: "context", confidence: 0.95, source: "explicit" });

    const results = db.facts.getHighConfidenceFacts();
    expect(results).toHaveLength(2);
    expect(results.map((f) => f.fact)).toContain("Prefers TypeScript strict mode");
    expect(results.map((f) => f.fact)).toContain("Has a dog named Max");
  });

  it("excludes retired facts (confidence = 0)", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Old preference",
      category: "preference", confidence: 0.9, source: "explicit" });
    const all = db.facts.getAllForUser();
    db.facts.retire(all[0].id);

    const results = db.facts.getHighConfidenceFacts();
    expect(results).toHaveLength(0);
  });

  it("respects limit parameter", () => {
    for (let i = 0; i < 5; i++) {
      db.facts.add({ userId: "default", owlName: "default", fact: `Preference ${i}`,
        category: "preference", confidence: 0.9, source: "explicit" });
    }
    const results = db.facts.getHighConfidenceFacts(undefined, 3);
    expect(results).toHaveLength(3);
  });

  it("orders by confidence DESC", () => {
    db.facts.add({ userId: "default", owlName: "default", fact: "Medium confidence",
      category: "preference", confidence: 0.82, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default", fact: "High confidence",
      category: "preference", confidence: 0.95, source: "explicit" });

    const results = db.facts.getHighConfidenceFacts();
    expect(results[0].fact).toBe("High confidence");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/facts-repo-tier0.test.ts
```

Expected: FAIL — `db.facts.getHighConfidenceFacts is not a function`

- [ ] **Step 3: Export `TIER0_CATEGORIES` constant and add `getHighConfidenceFacts()` method**

In `src/memory/db.ts`, after the `FactCategory` type definition (around line 36), add:

```typescript
export const TIER0_CATEGORIES: FactCategory[] = [
  "preference", "personal", "active_goal", "goal",
  "relationship", "habit", "decision",
];
```

In the `FactsRepo` class, after the `retire()` method (around line 1943), add:

```typescript
getHighConfidenceFacts(userId?: string, limit = 30): Fact[] {
  const placeholders = TIER0_CATEGORIES.map(() => "?").join(",");
  const now = new Date().toISOString();
  const rows = this.db.prepare(`
    SELECT * FROM facts
    WHERE confidence >= 0.8
      AND category IN (${placeholders})
      AND (invalidated_at IS NULL)
      AND (expires_at IS NULL OR expires_at > ?)
      ${userId ? "AND user_id = ?" : ""}
    ORDER BY confidence DESC, updated_at DESC
    LIMIT ?
  `).all(
    ...TIER0_CATEGORIES,
    now,
    ...(userId ? [userId] : []),
    limit,
  ) as any[];
  return rows.map(rowToFact);
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/facts-repo-tier0.test.ts
```

Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory/facts-repo-tier0.test.ts
git commit -m "feat(memory): add getHighConfidenceFacts + TIER0_CATEGORIES to FactsRepo"
```

---

## Task 2: Create `SqliteTier0Layer`

**Files:**
- Create: `src/context/layers/sqlite-memory.ts`
- Create: `__tests__/context/sqlite-memory-layer.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/context/sqlite-memory-layer.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SqliteTier0Layer } from "../../src/context/layers/sqlite-memory.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-tier0-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("SqliteTier0Layer", () => {
  it("injects high-confidence facts as tier0_memory context", async () => {
    db.facts.add({ userId: "default", owlName: "default",
      fact: "Prefers concise responses", category: "preference",
      confidence: 0.9, source: "explicit" });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toContain("<tier0_memory>");
    expect(result).toContain("Prefers concise responses");
    expect(result).toContain("</tier0_memory>");
  });

  it("returns empty string when db has no high-confidence tier-0 facts", async () => {
    db.facts.add({ userId: "default", owlName: "default",
      fact: "Low confidence note", category: "preference",
      confidence: 0.5, source: "inferred" });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toBe("");
  });

  it("returns empty string when no db is provided", async () => {
    const layer = new SqliteTier0Layer();
    const result = await layer.build({} as any, {} as any, new Map());
    expect(result).toBe("");
  });

  it("always fires — shouldFire returns true unconditionally", () => {
    const layer = new SqliteTier0Layer(db);
    expect(layer.shouldFire({} as any)).toBe(true);
  });

  it("has priority 0 — highest in pipeline", () => {
    const layer = new SqliteTier0Layer(db);
    expect(layer.priority).toBe(0);
  });

  it("formats facts as bullet list grouped by category", async () => {
    db.facts.add({ userId: "default", owlName: "default",
      fact: "Prefers TypeScript", category: "preference", confidence: 0.9, source: "explicit" });
    db.facts.add({ userId: "default", owlName: "default",
      fact: "Goal: ship StackOwl v1", category: "active_goal", confidence: 0.85, source: "explicit" });

    const layer = new SqliteTier0Layer(db);
    const result = await layer.build({} as any, {} as any, new Map());

    expect(result).toContain("preference:");
    expect(result).toContain("active_goal:");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/context/sqlite-memory-layer.test.ts
```

Expected: FAIL — `SqliteTier0Layer` module not found

- [ ] **Step 3: Implement `SqliteTier0Layer`**

Create `src/context/layers/sqlite-memory.ts`:

```typescript
import { log } from "../../logger.js";
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { MemoryDatabase, Fact, FactCategory } from "../../memory/db.js";

export class SqliteTier0Layer implements ContextLayer {
  name = "SqliteTier0Layer";
  priority = 0;
  maxTokens = 800;
  produces = ["tier0_memory"];
  dependsOn: string[] = [];

  constructor(private readonly db?: MemoryDatabase) {}

  getCacheKey(): string | null {
    return null;
  }

  shouldFire(_triage: TriageSignals): boolean {
    return true;
  }

  async build(
    _req: ContextRequest,
    _triage: TriageSignals,
    _deps: LayerResults,
  ): Promise<string> {
    if (!this.db) return "";

    let facts: Fact[];
    try {
      facts = this.db.facts.getHighConfidenceFacts();
    } catch (err) {
      log.engine.error("[SqliteTier0Layer] Failed to query facts", err as Error);
      return "";
    }

    if (facts.length === 0) return "";

    // Group facts by category for readability
    const byCategory = new Map<FactCategory, string[]>();
    for (const f of facts) {
      const list = byCategory.get(f.category) ?? [];
      list.push(`- ${f.fact}`);
      byCategory.set(f.category, list);
    }

    const lines: string[] = [];
    for (const [category, items] of byCategory) {
      lines.push(`${category}:`);
      lines.push(...items);
    }

    log.engine.debug("[SqliteTier0Layer] Injecting tier-0 facts", {
      count: facts.length,
      categories: [...byCategory.keys()],
    });

    return `<tier0_memory>\n${lines.join("\n")}\n</tier0_memory>`;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/context/sqlite-memory-layer.test.ts
```

Expected: 6/6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/context/layers/sqlite-memory.ts __tests__/context/sqlite-memory-layer.test.ts
git commit -m "feat(context): add SqliteTier0Layer — Tier-0 from facts table"
```

---

## Task 3: Wire `SqliteTier0Layer` into the context pipeline

**Files:**
- Modify: `src/context/index.ts` — line 14 and line 55

- [ ] **Step 1: Write the failing test**

Run the existing pipeline integration test to confirm it passes before the change (baseline):

```bash
npx vitest run __tests__/context/pipeline-integration.test.ts
```

Expected: all PASS (baseline — this test should already pass)

- [ ] **Step 2: Update `src/context/index.ts`**

Replace the `MemoryMdLayer` import and its usage:

Old import at line 14:
```typescript
import { MemoryMdLayer } from "./layers/memory-md.js";
```

New import:
```typescript
import { SqliteTier0Layer } from "./layers/sqlite-memory.js";
```

Old layer at line 55:
```typescript
new MemoryMdLayer(),  // Tier 0 — fresh MEMORY.md injection every turn
```

New layer:
```typescript
new SqliteTier0Layer(deps.db),  // Tier 0 — high-confidence facts from SQLite
```

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
npx vitest run __tests__/context/
```

Expected: all PASS (pipeline-integration.test.ts, pipeline.test.ts, etc.)

- [ ] **Step 4: Commit**

```bash
git add src/context/index.ts
git commit -m "feat(context): wire SqliteTier0Layer into pipeline, replacing MemoryMdLayer"
```

---

## Task 4: Rewrite `UpdateMemoryTool` to write SQLite facts

**Files:**
- Modify: `src/tools/update-memory.ts`
- Modify: `__tests__/tools/update-memory.test.ts` — add SQLite tests

The tool's constructor changes from `(memoryPath: string)` to `(db?: MemoryDatabase)`.
The flat-file path is removed. A `setDb(db)` method is added for late injection (same pattern as `ledger.setDb(memoryDb)` in `index.ts`).

### Section → Category mapping:

```typescript
const SECTION_TO_CATEGORY: Record<string, FactCategory> = {
  preferences: "preference",
  preference: "preference",
  "about me": "personal",
  personal: "personal",
  goals: "active_goal",
  "active goals": "active_goal",
  "active_goals": "active_goal",
  relationships: "relationship",
  "key relationships": "relationship",
  relationship: "relationship",
  habits: "habit",
  habit: "habit",
  decisions: "decision",
  decision: "decision",
};
```

Default category when section doesn't match: `"preference"`.

- [ ] **Step 1: Add SQLite tests to existing test file**

Add to `__tests__/tools/update-memory.test.ts`:

```typescript
import { mkdtempSync } from "node:fs";
import { MemoryDatabase } from "../../src/memory/db.js";

// ─── SQLite-backed tests ──────────────────────────────────────────────────────

describe("UpdateMemoryTool (SQLite mode)", () => {
  let db: MemoryDatabase;
  let dbDir: string;

  beforeEach(() => {
    dbDir = mkdtempSync(join(tmpdir(), "stackowl-update-mem-db-"));
    db = new MemoryDatabase(dbDir);
  });

  afterEach(() => {
    rmSync(dbDir, { recursive: true, force: true });
  });

  it("adds a fact to the facts table", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers dark mode" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.some((f) => f.fact === "Prefers dark mode")).toBe(true);
    expect(facts.find((f) => f.fact === "Prefers dark mode")?.category).toBe("preference");
  });

  it("maps 'Goals' section to active_goal category", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Goals", content: "Ship StackOwl v2 by Q3" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.find((f) => f.fact === "Ship StackOwl v2 by Q3")?.category).toBe("active_goal");
  });

  it("remove operation retires matching facts", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers verbose logs" },
      {} as any,
    );
    await tool.execute(
      { operation: "remove", section: "Preferences", content: "verbose logs" },
      {} as any,
    );
    const remaining = db.facts.getHighConfidenceFacts();
    expect(remaining.some((f) => f.fact.includes("verbose logs"))).toBe(false);
  });

  it("update operation retires old fact and adds new one", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "About me", content: "Name: Bakir" },
      {} as any,
    );
    await tool.execute(
      { operation: "update", section: "About me", content: "Name: Bakir Talibov" },
      {} as any,
    );
    const active = db.facts.getHighConfidenceFacts();
    expect(active.some((f) => f.fact === "Name: Bakir Talibov")).toBe(true);
    expect(active.some((f) => f.fact === "Name: Bakir")).toBe(false);
  });

  it("setDb wires db after construction", async () => {
    const tool = new UpdateMemoryTool();
    tool.setDb(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers TDD" },
      {} as any,
    );
    expect(db.facts.getAllForUser().some((f) => f.fact === "Prefers TDD")).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify new tests fail**

```bash
npx vitest run __tests__/tools/update-memory.test.ts
```

Expected: existing 5 tests PASS, new 5 SQLite tests FAIL (constructor signature mismatch or no-op)

- [ ] **Step 3: Rewrite `src/tools/update-memory.ts`**

```typescript
import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";
import type { MemoryDatabase, FactCategory } from "../memory/db.js";

const SECTION_TO_CATEGORY: Record<string, FactCategory> = {
  preferences: "preference",
  preference: "preference",
  "about me": "personal",
  personal: "personal",
  goals: "active_goal",
  "active goals": "active_goal",
  active_goals: "active_goal",
  relationships: "relationship",
  "key relationships": "relationship",
  relationship: "relationship",
  habits: "habit",
  habit: "habit",
  decisions: "decision",
  decision: "decision",
};

const MAX_LINE_LENGTH = 200;

export interface UpdateMemoryInput {
  operation: "add" | "update" | "remove";
  section: string;
  content: string;
}

export class UpdateMemoryTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "update_memory",
    description:
      "Persist durable facts about the user: preferences, goals, relationships, decisions. " +
      "Operations: add (store new fact), update (replace matching fact), remove (retire matching fact). " +
      "Each fact is stored in the SQLite facts table and surfaces automatically in Tier-0 context.",
    parameters: {
      type: "object",
      properties: {
        operation: {
          type: "string",
          enum: ["add", "update", "remove"],
          description: "add — store new fact; update — replace matching; remove — retire matching",
        },
        section: {
          type: "string",
          description:
            'Semantic category: "Preferences", "About me", "Goals", "Relationships", "Habits", "Decisions"',
        },
        content: {
          type: "string",
          description: "The fact to store, update, or remove (max 200 chars)",
        },
      },
      required: ["operation", "section", "content"],
    },
  };

  category = "filesystem" as const;
  source = "builtin";

  private db?: MemoryDatabase;

  constructor(db?: MemoryDatabase) {
    this.db = db;
  }

  setDb(db: MemoryDatabase): void {
    this.db = db;
  }

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const input = args as unknown as UpdateMemoryInput;

    log.tool.debug("update_memory.execute: entry", {
      operation: input.operation,
      section: input.section,
    });

    if (input.content.length > MAX_LINE_LENGTH) {
      const err = new Error(
        `Content too long (${input.content.length} chars). Keep under ${MAX_LINE_LENGTH}.`,
      );
      log.tool.error("update_memory.execute: content too long", err, {
        contentLen: input.content.length,
      });
      throw err;
    }

    if (!this.db) {
      log.tool.warn("update_memory.execute: no db injected — operation dropped", {
        operation: input.operation,
      });
      return `Memory operation skipped (db not ready).`;
    }

    const category =
      SECTION_TO_CATEGORY[input.section.toLowerCase()] ?? "preference";

    if (input.operation === "add") {
      this.db.facts.add({
        userId: "default",
        owlName: "default",
        fact: input.content,
        category,
        confidence: 0.9,
        source: "explicit",
      });
      log.tool.info("update_memory.execute: fact added", { category, content: input.content.slice(0, 60) });
      return `Fact stored in "${category}".`;
    }

    if (input.operation === "remove") {
      const keyword = input.content.toLowerCase();
      const all = this.db.facts.getAllForUser();
      const matches = all.filter((f) => f.fact.toLowerCase().includes(keyword) && f.category === category);
      for (const f of matches) {
        this.db.facts.retire(f.id);
      }
      log.tool.info("update_memory.execute: facts retired", { count: matches.length, category });
      return `Retired ${matches.length} fact(s) matching "${input.content}".`;
    }

    if (input.operation === "update") {
      const keyword = input.content.split(":")[0].toLowerCase().trim();
      const all = this.db.facts.getAllForUser();
      const matches = all.filter(
        (f) => f.fact.toLowerCase().startsWith(keyword) && f.category === category,
      );
      for (const f of matches) {
        this.db.facts.retire(f.id);
      }
      this.db.facts.add({
        userId: "default",
        owlName: "default",
        fact: input.content,
        category,
        confidence: 0.9,
        source: "explicit",
      });
      log.tool.info("update_memory.execute: fact updated", { retired: matches.length, category });
      return `Updated fact in "${category}" (retired ${matches.length} old, added 1 new).`;
    }

    return "Unknown operation.";
  }
}
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
npx vitest run __tests__/tools/update-memory.test.ts
```

Expected: all 10 tests PASS (5 existing kept for reference — they now use the old string constructor which will compile-error; adjust them to pass `undefined` or a db as needed)

**Note:** The existing 5 tests pass a `string` to the constructor — they will fail to compile with the new signature. Update them to either use the new SQLite tests or remove the old flat-file tests:

- Remove tests: "adds a line to an existing section", "creates a new section when section does not exist", "removes a matching line", "updates an existing line matching a keyword" (now covered by SQLite tests)
- Keep: "rejects lines over 200 characters" — update it to use `new UpdateMemoryTool(db)` with a db

Full replacement of `__tests__/tools/update-memory.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { UpdateMemoryTool } from "../../src/tools/update-memory.js";
import { MemoryDatabase } from "../../src/memory/db.js";

let dbDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  dbDir = mkdtempSync(join(tmpdir(), "stackowl-update-mem-db-"));
  db = new MemoryDatabase(dbDir);
});

afterEach(() => {
  rmSync(dbDir, { recursive: true, force: true });
});

describe("UpdateMemoryTool", () => {
  it("adds a fact to the facts table", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers dark mode" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.some((f) => f.fact === "Prefers dark mode")).toBe(true);
    expect(facts.find((f) => f.fact === "Prefers dark mode")?.category).toBe("preference");
  });

  it("maps 'Goals' section to active_goal category", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Goals", content: "Ship StackOwl v2 by Q3" },
      {} as any,
    );
    const facts = db.facts.getAllForUser();
    expect(facts.find((f) => f.fact === "Ship StackOwl v2 by Q3")?.category).toBe("active_goal");
  });

  it("remove operation retires matching facts", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers verbose logs" },
      {} as any,
    );
    await tool.execute(
      { operation: "remove", section: "Preferences", content: "verbose logs" },
      {} as any,
    );
    const remaining = db.facts.getHighConfidenceFacts();
    expect(remaining.some((f) => f.fact.includes("verbose logs"))).toBe(false);
  });

  it("update operation retires old fact and adds new one", async () => {
    const tool = new UpdateMemoryTool(db);
    await tool.execute(
      { operation: "add", section: "About me", content: "Name: Bakir" },
      {} as any,
    );
    await tool.execute(
      { operation: "update", section: "About me", content: "Name: Bakir Talibov" },
      {} as any,
    );
    const active = db.facts.getHighConfidenceFacts();
    expect(active.some((f) => f.fact === "Name: Bakir Talibov")).toBe(true);
    expect(active.some((f) => f.fact === "Name: Bakir")).toBe(false);
  });

  it("setDb wires db after construction", async () => {
    const tool = new UpdateMemoryTool();
    tool.setDb(db);
    await tool.execute(
      { operation: "add", section: "Preferences", content: "Prefers TDD" },
      {} as any,
    );
    expect(db.facts.getAllForUser().some((f) => f.fact === "Prefers TDD")).toBe(true);
  });

  it("rejects content over 200 characters", async () => {
    const tool = new UpdateMemoryTool(db);
    await expect(
      tool.execute(
        { operation: "add", section: "Preferences", content: "a".repeat(201) },
        {} as any,
      ),
    ).rejects.toThrow(/too long/i);
  });

  it("returns skip message when db not injected", async () => {
    const tool = new UpdateMemoryTool();
    const result = await tool.execute(
      { operation: "add", section: "Preferences", content: "Something" },
      {} as any,
    );
    expect(result).toContain("skipped");
  });
});
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/tools/update-memory.test.ts
```

Expected: 7/7 PASS

- [ ] **Step 6: Commit**

```bash
git add src/tools/update-memory.ts __tests__/tools/update-memory.test.ts
git commit -m "feat(tools): rewrite UpdateMemoryTool to write SQLite facts instead of MEMORY.md"
```

---

## Task 5: Wire `UpdateMemoryTool` with `memoryDb` in `index.ts`

**Files:**
- Modify: `src/index.ts`

The tool registry is built at line ~382, but `memoryDb` is created at line ~534. Use `setDb()` for late injection — same pattern as `ledger.setDb(memoryDb)`.

- [ ] **Step 1: Hold a reference to `UpdateMemoryTool` in `index.ts`**

Change the instantiation at line ~411 from:

```typescript
new UpdateMemoryTool(),
```

to:

```typescript
(() => {
  const t = new UpdateMemoryTool();
  (global as any).__updateMemoryTool = t;
  return t;
})(),
```

No — that's ugly. Better: extract the tool to a `const` before `toolRegistry.registerAll()`:

Locate the `toolRegistry.registerAll([` block in `index.ts` (line ~382). Before it, add:

```typescript
const updateMemoryTool = new UpdateMemoryTool();
```

Then inside the `registerAll` array, replace `new UpdateMemoryTool()` with `updateMemoryTool`.

Then after `memoryDb` is created (after line ~534), add:

```typescript
updateMemoryTool.setDb(memoryDb);
```

Full diff for `src/index.ts`:

**Before** `toolRegistry.registerAll([` (add new line):
```typescript
const updateMemoryTool = new UpdateMemoryTool();
```

**Inside** the array, replace:
```typescript
new UpdateMemoryTool(),
```
with:
```typescript
updateMemoryTool,
```

**After** `const memoryDb = new MemoryDatabase(workspacePath);` (around line 534), add:
```typescript
updateMemoryTool.setDb(memoryDb);
```

- [ ] **Step 2: Run the build to verify no TypeScript errors**

```bash
npm run build 2>&1 | head -30
```

Expected: 0 errors

- [ ] **Step 3: Run the full test suite**

```bash
npm run test 2>&1 | tail -20
```

Expected: all previously-passing tests still pass

- [ ] **Step 4: Commit**

```bash
git add src/index.ts
git commit -m "feat(wiring): late-inject MemoryDatabase into UpdateMemoryTool via setDb()"
```

---

## Task 6: MEMORY.md migration — import flat-file facts into SQLite

**Files:**
- Create: `src/memory/memory-migration.ts`
- Modify: `src/index.ts` — call migration after `memoryDb` is initialized

This migration runs once at startup. It checks for a sentinel fact (`entity: 'migration:memory-md'`) and skips if already done. It reads the existing MEMORY.md, strips bullet prefix (`- `), and inserts each non-empty line as a `preference` fact with `confidence: 0.9`.

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory/memory-migration.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { join } from "node:path";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { migrateMemoryMd } from "../../src/memory/memory-migration.js";

let tmpDir: string;
let db: MemoryDatabase;
let memoryMdPath: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "stackowl-migration-"));
  db = new MemoryDatabase(tmpDir);
  memoryMdPath = join(tmpDir, "MEMORY.md");
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("migrateMemoryMd", () => {
  it("imports bullet lines as facts in the correct category", async () => {
    writeFileSync(memoryMdPath,
      "# Preferences\n- Concise responses\n- TypeScript strict mode\n\n# Goals\n- Ship StackOwl v2\n"
    );

    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser();
    expect(facts.some((f) => f.fact === "Concise responses" && f.category === "preference")).toBe(true);
    expect(facts.some((f) => f.fact === "TypeScript strict mode" && f.category === "preference")).toBe(true);
    expect(facts.some((f) => f.fact === "Ship StackOwl v2" && f.category === "active_goal")).toBe(true);
  });

  it("is idempotent — running twice does not double-import facts", async () => {
    writeFileSync(memoryMdPath, "# Preferences\n- Concise responses\n");

    await migrateMemoryMd(db, memoryMdPath);
    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser().filter((f) => f.fact === "Concise responses");
    expect(facts).toHaveLength(1);
  });

  it("is a no-op when MEMORY.md does not exist", async () => {
    await migrateMemoryMd(db, join(tmpDir, "nonexistent.md"));
    const facts = db.facts.getAllForUser();
    expect(facts.filter((f) => f.entity !== "migration:memory-md")).toHaveLength(0);
  });

  it("skips empty lines and section headers", async () => {
    writeFileSync(memoryMdPath, "# Preferences\n\n- Real fact\n\n# About me\n");
    await migrateMemoryMd(db, memoryMdPath);

    const facts = db.facts.getAllForUser().filter((f) => f.entity !== "migration:memory-md");
    expect(facts).toHaveLength(1);
    expect(facts[0].fact).toBe("Real fact");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/memory-migration.test.ts
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement `src/memory/memory-migration.ts`**

```typescript
import { existsSync, readFileSync } from "node:fs";
import { log } from "../logger.js";
import type { MemoryDatabase } from "./db.js";
import { SECTION_TO_CATEGORY } from "../tools/update-memory.js";
import type { FactCategory } from "./db.js";

const SENTINEL_ENTITY = "migration:memory-md";

export async function migrateMemoryMd(
  db: MemoryDatabase,
  memoryMdPath: string,
): Promise<void> {
  // Idempotency check
  const already = db.facts.getAllForUser().find((f) => f.entity === SENTINEL_ENTITY);
  if (already) {
    log.engine.debug("[MemoryMigration] Already migrated — skipping");
    return;
  }

  if (!existsSync(memoryMdPath)) {
    log.engine.debug("[MemoryMigration] No MEMORY.md found — skipping");
    markMigrated(db);
    return;
  }

  const raw = readFileSync(memoryMdPath, "utf-8");
  const lines = raw.split("\n");

  let currentCategory: FactCategory = "preference";
  let importedCount = 0;

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed.startsWith("#")) {
      const header = trimmed.replace(/^#+\s*/, "").toLowerCase();
      currentCategory = SECTION_TO_CATEGORY[header] ?? "preference";
      continue;
    }

    if (!trimmed || !trimmed.startsWith("-")) continue;

    const fact = trimmed.replace(/^-\s*/, "").trim();
    if (!fact) continue;

    db.facts.add({
      userId: "default",
      owlName: "default",
      fact,
      category: currentCategory,
      confidence: 0.9,
      source: "explicit",
    });
    importedCount++;
  }

  markMigrated(db);
  log.engine.info(`[MemoryMigration] Imported ${importedCount} facts from MEMORY.md`);
}

function markMigrated(db: MemoryDatabase): void {
  db.facts.add({
    userId: "default",
    owlName: "default",
    fact: "MEMORY.md migration completed",
    entity: SENTINEL_ENTITY,
    category: "context",
    confidence: 1.0,
    source: "explicit",
  });
}
```

**Note:** `SECTION_TO_CATEGORY` must be exported from `src/tools/update-memory.ts`. Add `export` to the const in that file.

- [ ] **Step 4: Export `SECTION_TO_CATEGORY` from `update-memory.ts`**

Change in `src/tools/update-memory.ts`:
```typescript
const SECTION_TO_CATEGORY: ...
```
to:
```typescript
export const SECTION_TO_CATEGORY: ...
```

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/memory-migration.test.ts
```

Expected: 4/4 PASS

- [ ] **Step 6: Wire migration into `src/index.ts`**

After `updateMemoryTool.setDb(memoryDb)` (added in Task 5), add:

```typescript
import { migrateMemoryMd } from "./memory/memory-migration.js";

// ...after updateMemoryTool.setDb(memoryDb):
const memoryMdPath = join(homedir(), ".stackowl", "workspace", "MEMORY.md");
migrateMemoryMd(memoryDb, memoryMdPath).catch((err) =>
  log.engine.warn(`[MemoryMigration] Migration failed: ${err}`),
);
```

(The `import` goes at the top of the file with other imports; the call goes inline after db init.)

- [ ] **Step 7: Run full test suite**

```bash
npm run test 2>&1 | tail -20
```

Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/memory/memory-migration.ts __tests__/memory/memory-migration.test.ts src/tools/update-memory.ts src/index.ts
git commit -m "feat(memory): one-shot MEMORY.md → SQLite migration on startup"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| `MemoryMdLayer` reads from `facts` table | Task 2, 3 |
| `UpdateMemoryTool` writes to `facts` table | Task 4, 5 |
| Migration: MEMORY.md → facts on first run | Task 6 |
| MEMORY.md becomes export-only (not source of truth) | Task 6 — file is read once then superceded |
| High-confidence filter (>= 0.8) | Task 1, 2 |
| Tier-0 categories constrained | Task 1 — `TIER0_CATEGORIES` constant |
| Idempotent migration | Task 6 — sentinel fact check |
| Graceful degradation when db absent | Task 2 — returns `""` when no db |
| Late injection of db into tool | Task 5 — `setDb()` pattern |

### Placeholder scan

None found.

### Type consistency

- `FactCategory` — imported from `src/memory/db.ts` in all three files
- `TIER0_CATEGORIES` — exported from `db.ts`, consumed in `sqlite-memory.ts`
- `SECTION_TO_CATEGORY` — exported from `update-memory.ts`, consumed in `memory-migration.ts`
- `FactsRepo.getHighConfidenceFacts()` — defined in Task 1, called in Task 2

### Scope check

Six focused tasks, each independently testable. No cross-task data dependencies beyond the constant exports.
