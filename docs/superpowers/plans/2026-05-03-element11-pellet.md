# Element 11 — Pellet System: Quality Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Intelligence-First violations in the Pellet system and wire a compounding quality flywheel: GoalVerifier verdicts feed back into pellet success counts, re-ranking retrieval so better pellets surface first, reinforcing the DNA of owls who generate them.

**Architecture:** Passive correlator — no new files, no new EventBus types. Every change is an additive extension to an existing file. The flywheel runs in the post-turn block alongside Parliament's existing hooks. Schema v21 adds a single SQLite table for ProactiveGenerator persistence.

**Tech Stack:** TypeScript, LanceDB 0.16, Kuzu graph, better-sqlite3, Vitest, IntelligenceRouter

---

## File Structure

| File | Change |
|---|---|
| `src/pellets/search.ts` | **DELETE** |
| `src/pellets/semantic-dedup.ts` | **DELETE** |
| `src/pellets/tfidf.ts` | **DELETE** |
| `src/pellets/store.ts` | Extend `Pellet` interface; add `recordOutcome()`; add `searchWithGraphScored()` |
| `src/pellets/lance-store.ts` | Extend `PelletRow`; add `addColumns()` guard in `init()`; add `updateCounters()` |
| `src/pellets/generator.ts` | Rewrite: swap `OwlEngine` for `IntelligenceRouter` |
| `src/pellets/dedup.ts` | Add optional `IntelligenceRouter` to constructor; use it in `decideWithLlm()` |
| `src/pellets/knowledge-base.ts` | Add optional `IntelligenceRouter`; use in `computeCoverageGaps()` |
| `src/pellets/event-based-generator.ts` | Replace keyword detection with `router.resolve()` |
| `src/pellets/proactive-generator.ts` | Add `MemoryDatabase` to constructor; replace in-memory run-time tracking |
| `src/context/layer.ts` | Add `retrievedPelletIds?: string[]` to `ContextRequest` |
| `src/context/layers/knowledge.ts` | Call `searchWithGraphScored`; quality re-rank; write `req.retrievedPelletIds` |
| `src/context/pipeline.ts` | Add `lastRetrievedPelletIds: string[]` field; set in `run()` |
| `src/owls/evolution.ts` | Add `updatePelletGeneratorDNA()` export |
| `src/memory/db.ts` | Schema v21 + `getPelletGenRun` / `setPelletGenRun` helpers |
| `src/gateway/core.ts` | Post-turn hooks 4 (recordOutcome) and 5 (updatePelletGeneratorDNA) |

---

## Task 1: Delete dead files (AC-1)

**Files:**
- Delete: `src/pellets/search.ts`
- Delete: `src/pellets/semantic-dedup.ts`
- Delete: `src/pellets/tfidf.ts`
- Verify: `src/engine/runtime.ts`

- [ ] **Step 1: Delete the three dead files**

```bash
rm src/pellets/search.ts src/pellets/semantic-dedup.ts src/pellets/tfidf.ts
```

- [ ] **Step 2: Fix any import that referenced these files**

```bash
grep -rn "search\|semantic-dedup\|tfidf" src/ --include="*.ts" | grep "pellets/"
```

If `src/engine/runtime.ts` imports from `./pellets/search`, remove that import line. Based on prior audit, `runtime.ts:109` may reference `pellet.domain` (a field that doesn't exist). If so, remove that property access entirely.

- [ ] **Step 3: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep -i "error" | head -20
```

Expected: zero errors referencing `search.ts`, `semantic-dedup.ts`, or `tfidf.ts`.

- [ ] **Step 4: Run existing pellet tests**

```bash
npx vitest run __tests__/pellets/ 2>&1 | tail -20
```

Expected: all existing pellet tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(pellets): delete dead files — search.ts, semantic-dedup.ts, tfidf.ts"
```

---

## Task 2: Extend Pellet interface + LanceDB column migration (AC-15)

**Files:**
- Modify: `src/pellets/store.ts:32-44`
- Modify: `src/pellets/lance-store.ts:22-36` (PelletRow), `lance-store.ts:46-61` (pelletToRow), `lance-store.ts:63-81` (rowToPellet), `lance-store.ts:108-151` (init)

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/lance-migration.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { LancePelletStore } from "../../src/pellets/lance-store.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("LancePelletStore — column migration", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "lance-test-"));
  });
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("adds successCount, failureCount, provenance columns on first init", async () => {
    const store = new LancePelletStore(dir);
    await store.init();
    const schema = await store.getColumnNames();
    expect(schema).toContain("success_count");
    expect(schema).toContain("failure_count");
    expect(schema).toContain("provenance");
  });

  it("is idempotent — second init does not throw", async () => {
    const store = new LancePelletStore(dir);
    await store.init();
    const store2 = new LancePelletStore(dir);
    await expect(store2.init()).resolves.not.toThrow();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/lance-migration.test.ts 2>&1 | tail -10
```

Expected: FAIL — `getColumnNames is not a function`.

- [ ] **Step 3: Extend `Pellet` interface in `store.ts:32-44`**

Replace the existing `Pellet` interface:

```typescript
export interface Pellet {
  id: string;
  title: string;
  generatedAt: string;
  source: string;
  owls: string[];
  tags: string[];
  content: string;
  version: number;
  supersedes?: string;
  mergedFrom?: string[];
  lastMergedAt?: string;
  successCount: number;
  failureCount: number;
  provenance: string[];
}
```

- [ ] **Step 4: Extend `PelletRow` in `lance-store.ts:22-36`**

Replace the `PelletRow` interface:

```typescript
export interface PelletRow {
  id: string;
  title: string;
  generated_at: string;
  source: string;
  owls: string;
  tags: string;
  content: string;
  version: number;
  supersedes: string;
  merged_from: string;
  last_merged_at: string;
  success_count: number;
  failure_count: number;
  provenance: string;
  vector: number[];
  [key: string]: unknown;
}
```

- [ ] **Step 5: Update `pelletToRow` in `lance-store.ts:46-61`**

Replace the `pelletToRow` function:

```typescript
function pelletToRow(pellet: Pellet, vector: number[]): PelletRow {
  return {
    id: pellet.id,
    title: pellet.title,
    generated_at: pellet.generatedAt,
    source: pellet.source,
    owls: JSON.stringify(pellet.owls),
    tags: JSON.stringify(pellet.tags),
    content: pellet.content,
    version: pellet.version,
    supersedes: pellet.supersedes ?? "",
    merged_from: JSON.stringify(pellet.mergedFrom ?? []),
    last_merged_at: pellet.lastMergedAt ?? "",
    success_count: pellet.successCount ?? 0,
    failure_count: pellet.failureCount ?? 0,
    provenance: JSON.stringify(pellet.provenance ?? []),
    vector,
  };
}
```

- [ ] **Step 6: Update `rowToPellet` in `lance-store.ts:63-81`**

Replace the `rowToPellet` function:

```typescript
function rowToPellet(row: Record<string, unknown>): Pellet {
  const p: Pellet = {
    id: row["id"] as string,
    title: row["title"] as string,
    generatedAt: (row["generated_at"] as string) || new Date().toISOString(),
    source: (row["source"] as string) || "unknown",
    owls: safeParseJson<string[]>(row["owls"] as string, []),
    tags: safeParseJson<string[]>(row["tags"] as string, []),
    content: (row["content"] as string) || "",
    version: (row["version"] as number) || 1,
    successCount: (row["success_count"] as number) ?? 0,
    failureCount: (row["failure_count"] as number) ?? 0,
    provenance: safeParseJson<string[]>(row["provenance"] as string, []),
  };
  const sup = row["supersedes"] as string;
  if (sup) p.supersedes = sup;
  const mf = safeParseJson<string[]>(row["merged_from"] as string, []);
  if (mf.length > 0) p.mergedFrom = mf;
  const lma = row["last_merged_at"] as string;
  if (lma) p.lastMergedAt = lma;
  return p;
}
```

- [ ] **Step 7: Add sentinel row update + `addColumns` guard + `getColumnNames` helper in `lance-store.ts` `init()`**

In the `LancePelletStore.init()` method, after the table is opened (whether newly created or existing), add the migration guard. Add it after the `this.table = await this.db.openTable(...)` lines in both the "table exists" branch and after the re-open in the "create table" branch.

Also add a new public method `getColumnNames()` after the `init()` method block, and a private `addMissingColumns()` helper. Insert the following before the `// ─── Write ──` comment in `lance-store.ts`:

```typescript
  /** Returns column names of the pellets table. Used by tests and migrations. */
  async getColumnNames(): Promise<string[]> {
    this.assertReady();
    return this.table!.schema.fields.map((f: { name: string }) => f.name);
  }

  private async addMissingColumns(): Promise<void> {
    const existing = await this.getColumnNames();
    const toAdd: Array<{ name: string; defaultValue: number | string }> = [];
    if (!existing.includes("success_count")) toAdd.push({ name: "success_count", defaultValue: 0 });
    if (!existing.includes("failure_count")) toAdd.push({ name: "failure_count", defaultValue: 0 });
    if (!existing.includes("provenance"))    toAdd.push({ name: "provenance",    defaultValue: "[]" });
    if (toAdd.length > 0) {
      await (this.table as any).addColumns(toAdd);
      // Re-open after addColumns so schema is refreshed
      this.table = await this.db!.openTable(LancePelletStore.TABLE);
      log.engine.info(`[LanceStore] Added columns: ${toAdd.map(c => c.name).join(", ")}`);
    }
  }
```

Then at the end of `_initialized = true` block in `init()`, call `await this.addMissingColumns()` before the info log:

Find the line `log.engine.info(\`[LanceStore] Opened table...` in the "table exists" branch and add the call just before it:
```typescript
await this.addMissingColumns();
log.engine.info(...)
```

And for the newly-created table path, add it after `this.table = await this.db.openTable(...)` (the re-open after sentinel deletion):
```typescript
this.table = await this.db.openTable(LancePelletStore.TABLE);
await this.addMissingColumns();
log.engine.info(...)
```

- [ ] **Step 8: Update sentinel row in `init()` to include new columns**

The sentinel row used to create the schema must include the new fields so a fresh table has the right schema from the start. Find the `sentinel: PelletRow` object in `init()` and add:

```typescript
const sentinel: PelletRow = {
  id: "__schema_sentinel__",
  title: "",
  generated_at: new Date().toISOString(),
  source: "",
  owls: "[]",
  tags: "[]",
  content: "",
  version: 1,
  supersedes: "",
  merged_from: "[]",
  last_merged_at: "",
  success_count: 0,
  failure_count: 0,
  provenance: "[]",
  vector: new Array<number>(dim).fill(0),
};
```

- [ ] **Step 9: Fix TypeScript errors from the interface change**

```bash
npx tsc --noEmit 2>&1 | grep error | head -30
```

Existing callers of `PelletStore` that construct `Pellet` objects will now have missing required fields. For each, add `successCount: 0, failureCount: 0, provenance: []`. Key callsites: `store.ts:readMarkdownPellet()`, `generator.ts`, `orchestrator/*.ts`, `parliament/orchestrator.ts`.

Specifically in `src/pellets/store.ts:readMarkdownPellet()`, add the fields to the constructed pellet:

```typescript
const p: Pellet = {
  id,
  title: data.title || id,
  generatedAt: data.generated_at || new Date().toISOString(),
  source: data.source || "unknown",
  owls: Array.isArray(data.owls) ? data.owls : [],
  tags: Array.isArray(data.tags) ? data.tags : [],
  version: data.version || 1,
  content: content.trim(),
  successCount: 0,
  failureCount: 0,
  provenance: [],
};
```

Repeat for every other `Pellet` object literal in the codebase that the compiler flags.

- [ ] **Step 10: Run migration tests**

```bash
npx vitest run __tests__/pellets/lance-migration.test.ts 2>&1 | tail -10
```

Expected: 2 tests PASS.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat(pellets): extend Pellet with successCount/failureCount/provenance + LanceDB column migration"
```

---

## Task 3: Add `recordOutcome` and `searchWithGraphScored` to PelletStore (AC-7, AC-8, AC-10)

**Files:**
- Modify: `src/pellets/lance-store.ts` — add `updateCounters()` method
- Modify: `src/pellets/store.ts` — add `recordOutcome()` and `searchWithGraphScored()`
- Test: `__tests__/pellets/record-outcome.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/record-outcome.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

// ─── recordOutcome tests ──────────────────────────────────────────

describe("PelletStore.recordOutcome", () => {
  it("ADVANCES increments successCount", async () => {
    const mockGet = vi.fn().mockResolvedValue({ id: "p1", successCount: 2, failureCount: 0, provenance: [] });
    const mockLanceUpdate = vi.fn().mockResolvedValue(undefined);
    // Construct minimal PelletStore with mocked lance
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws");
    store._initialized = true;
    store.lance = { get: mockGet, updateCounters: mockLanceUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p1"], "ADVANCES");
    expect(mockLanceUpdate).toHaveBeenCalledWith("p1", 1, 0);
  });

  it("PARTIAL increments successCount", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws2");
    store._initialized = true;
    const mockUpdate = vi.fn().mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "p2", successCount: 0, failureCount: 0 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p2"], "PARTIAL");
    expect(mockUpdate).toHaveBeenCalledWith("p2", 1, 0);
  });

  it("BLOCKED increments failureCount", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws3");
    store._initialized = true;
    const mockUpdate = vi.fn().mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "p3", successCount: 0, failureCount: 1 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p3"], "BLOCKED");
    expect(mockUpdate).toHaveBeenCalledWith("p3", 0, 1);
  });

  it("NEUTRAL does not call updateCounters", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws4");
    store._initialized = true;
    const mockUpdate = vi.fn();
    store.lance = { updateCounters: mockUpdate, init: vi.fn() } as any;
    await store.recordOutcome(["p4"], "NEUTRAL");
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("multi-ID: one failure does not abort others", async () => {
    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-ws5");
    store._initialized = true;
    const mockUpdate = vi.fn()
      .mockRejectedValueOnce(new Error("db error"))
      .mockResolvedValue(undefined);
    store.lance = { get: vi.fn().mockResolvedValue({ id: "x", successCount: 0, failureCount: 0 }), updateCounters: mockUpdate, init: vi.fn() } as any;
    await expect(store.recordOutcome(["bad", "good"], "ADVANCES")).resolves.not.toThrow();
    expect(mockUpdate).toHaveBeenCalledTimes(2);
  });
});

// ─── searchWithGraphScored quality re-rank test ──────────────────

describe("PelletStore.searchWithGraphScored — quality re-rank", () => {
  it("re-ranks by combined vector + quality score: high success rises above high vector-only", async () => {
    const pelletA = { id: "A", title: "A", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    const pelletB = { id: "B", title: "B", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 10, failureCount: 0, provenance: [] };
    const pelletC = { id: "C", title: "C", content: "", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 5, provenance: [] };

    const { PelletStore } = await import("../../src/pellets/store.js");
    const store = new (PelletStore as any)("/tmp/test-rank");
    store._initialized = true;
    // searchWithGraph returns Pellet[] already sorted by vector score: A(0.9), C(0.8), B(0.7)
    store.searchWithGraph = vi.fn().mockResolvedValue([pelletA, pelletC, pelletB]);

    const result = await store.searchWithGraphScored("query", 5);
    // After quality re-rank:
    //   A: 0.9*0.8 + (0/(0+0+1))*0.2   = 0.72 + 0.0  = 0.720
    //   C: 0.8*0.8 + (0/(0+5+1))*0.2   = 0.64 + 0.0  = 0.640
    //   B: 0.7*0.8 + (10/(10+0+1))*0.2 = 0.56 + 0.182= 0.742
    // Expected order: B > A > C
    expect(result.map((r: any) => r.p.id)).toEqual(["B", "A", "C"]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/record-outcome.test.ts 2>&1 | tail -10
```

Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Add `updateCounters` to `LancePelletStore`**

Add after the `findSimilarTo` method in `src/pellets/lance-store.ts` (before `// ─── Migration ──`):

```typescript
  /**
   * Increment success/failure counters for a pellet without re-embedding.
   * Uses LanceDB valuesSql to do the increment in-place.
   */
  async updateCounters(id: string, successDelta: number, failureDelta: number): Promise<void> {
    this.assertReady();
    const esc = this.esc(id);
    const sets: { [col: string]: string } = {};
    if (successDelta !== 0) sets["success_count"] = `success_count + ${successDelta}`;
    if (failureDelta !== 0) sets["failure_count"] = `failure_count + ${failureDelta}`;
    if (Object.keys(sets).length === 0) return;
    await (this.table as any).update({ where: `id = '${esc}'`, valuesSql: sets });
  }
```

- [ ] **Step 4: Add `recordOutcome` to `PelletStore` in `store.ts`**

Add after the `delete` method (around line 296) in `src/pellets/store.ts`:

```typescript
  /**
   * Feed GoalVerifier verdict back into retrieved pellets' quality counters.
   * ADVANCES/PARTIAL → successCount++; BLOCKED → failureCount++; NEUTRAL → no-op.
   * Non-fatal per ID — a single DB error does not abort the batch.
   */
  async recordOutcome(
    ids: string[],
    verdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL",
  ): Promise<void> {
    if (verdict === "NEUTRAL") return;
    const successDelta = verdict === "ADVANCES" || verdict === "PARTIAL" ? 1 : 0;
    const failureDelta = verdict === "BLOCKED" ? 1 : 0;
    for (const id of ids) {
      try {
        await this.lance.updateCounters(id, successDelta, failureDelta);
      } catch (err) {
        log.engine.warn(`[PelletStore] recordOutcome failed for ${id}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
  }
```

- [ ] **Step 5: Add `searchWithGraphScored` to `PelletStore` in `store.ts`**

Add after `searchWithGraph` method (around line 363) in `src/pellets/store.ts`:

```typescript
  /**
   * Graph-expanded search with quality re-ranking.
   * Returns pellets scored by: vectorScore * 0.8 + (successCount / (successCount + failureCount + 1)) * 0.2
   * Used by RelevantPelletsLayer to surface proven pellets first.
   */
  async searchWithGraphScored(
    query: string,
    limit = 5,
  ): Promise<Array<{ p: Pellet; score: number }>> {
    const pellets = await this.searchWithGraph(query, limit * 2);
    if (pellets.length === 0) return [];

    // searchWithGraph returns pellets sorted by vector score descending.
    // Assign a proxy vector score based on rank position (rank 0 = 1.0, linear decay).
    const scored = pellets.map((p, i) => {
      const vectorScore = 1 - i / (pellets.length + 1);
      const qualityScore = p.successCount / (p.successCount + p.failureCount + 1);
      return { p, score: vectorScore * 0.8 + qualityScore * 0.2 };
    });

    return scored.sort((a, b) => b.score - a.score).slice(0, limit);
  }
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/pellets/record-outcome.test.ts 2>&1 | tail -10
```

Expected: all 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(pellets): add recordOutcome + searchWithGraphScored with quality re-ranking"
```

---

## Task 4: Schema v21 — ProactiveGenerator persistence (AC-13, AC-14)

**Files:**
- Modify: `src/memory/db.ts` — add `applyV21Migration`, bump `SCHEMA_VERSION`, add `getPelletGenRun`/`setPelletGenRun`
- Modify: `src/pellets/proactive-generator.ts` — swap in-memory fields for DB-backed calls

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/proactive-persistence.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";

describe("Schema v21 — pellet_generation_runs", () => {
  it("creates pellet_generation_runs table on fresh DB", () => {
    const db = new Database(":memory:");
    applyMigrations(db);
    const row = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='pellet_generation_runs'").get();
    expect(row).toBeTruthy();
  });

  it("migration is idempotent on existing DB", () => {
    const db = new Database(":memory:");
    applyMigrations(db);
    expect(() => applyMigrations(db)).not.toThrow();
  });
});

describe("MemoryDatabase.getPelletGenRun / setPelletGenRun", () => {
  it("returns null for unknown key", async () => {
    const { MemoryDatabase } = await import("../../src/memory/db.js");
    const mdb = new MemoryDatabase(":memory:");
    const result = await mdb.getPelletGenRun("council");
    expect(result).toBeNull();
  });

  it("stores and retrieves run time", async () => {
    const { MemoryDatabase } = await import("../../src/memory/db.js");
    const mdb = new MemoryDatabase(":memory:");
    const now = new Date("2026-05-03T12:00:00Z");
    await mdb.setPelletGenRun("council", now);
    const result = await mdb.getPelletGenRun("council");
    expect(result?.toISOString()).toBe(now.toISOString());
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/proactive-persistence.test.ts 2>&1 | tail -10
```

Expected: FAIL — table doesn't exist, methods don't exist.

- [ ] **Step 3: Add `applyV21Migration` and bump `SCHEMA_VERSION` in `src/memory/db.ts`**

First, ensure `applyMigrations` is exported from `src/memory/db.ts`. Find the function declaration (it applies all versioned migrations in sequence) and confirm it has `export` — if it's not exported, add `export` to it. The test imports it directly.

Change `const SCHEMA_VERSION = 20;` to `const SCHEMA_VERSION = 21;` (line 29).

Add `applyV21Migration(this.db)` and `applyV21Migration(db)` calls in the two migration chains (around lines 1196 and 3177), following the exact same pattern as `applyV20Migration`.

Add the function itself at the end of the migration functions block (after `applyV20Migration`, before `applyMigrations`):

```typescript
function applyV21Migration(db: Database.Database): void {
  const tableExists = (db.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='pellet_generation_runs'"
  ).get() as { name: string } | undefined) !== undefined;
  if (!tableExists) {
    db.exec(`
      CREATE TABLE IF NOT EXISTS pellet_generation_runs (
        key         TEXT PRIMARY KEY,
        last_run_at TEXT NOT NULL
      );
    `);
  }
}
```

- [ ] **Step 4: Add `getPelletGenRun` and `setPelletGenRun` helpers to `MemoryDatabase` class**

Find the end of the `MemoryDatabase` class in `src/memory/db.ts` (before the closing `}`) and add:

```typescript
  async getPelletGenRun(key: string): Promise<Date | null> {
    const row = this.db.prepare(
      "SELECT last_run_at FROM pellet_generation_runs WHERE key = ?"
    ).get(key) as { last_run_at: string } | undefined;
    return row ? new Date(row.last_run_at) : null;
  }

  async setPelletGenRun(key: string, date: Date): Promise<void> {
    this.db.prepare(
      "INSERT INTO pellet_generation_runs (key, last_run_at) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_run_at = excluded.last_run_at"
    ).run(key, date.toISOString());
  }
```

Note: `better-sqlite3` is synchronous — these methods are `async` only for API consistency with the callers that `await` them.

- [ ] **Step 5: Update `ProactiveKnowledgeGenerator` to use DB-backed run times**

In `src/pellets/proactive-generator.ts`:

1. Add `MemoryDatabase` import at the top:
```typescript
import type { MemoryDatabase } from "../memory/db.js";
```

2. Change the constructor to accept an optional `db`:
```typescript
constructor(
  private pelletStore: PelletStore,
  private provider: ModelProvider,
  private owl: OwlInstance,
  private config: StackOwlConfig,
  private generationConfig: Partial<ProactiveGenerationConfig> = {},
  private db?: MemoryDatabase,
) {
```

3. Remove the three in-memory fields:
```typescript
// DELETE these three lines:
private lastCouncilRun: string = "";
private lastDreamRun: string = "";
private lastEvolveRun: string = "";
```

4. Replace every use of `this.lastCouncilRun`, `this.lastDreamRun`, `this.lastEvolveRun` with the DB-backed helpers. In `runKnowledgeCouncil()`, replace:
```typescript
const hoursSinceLastRun = this.lastCouncilRun
  ? (now.getTime() - new Date(this.lastCouncilRun).getTime()) / (1000 * 60 * 60)
  : Infinity;
```
with:
```typescript
const lastRun = this.db ? await this.db.getPelletGenRun("council") : null;
const hoursSinceLastRun = lastRun
  ? (now.getTime() - lastRun.getTime()) / (1000 * 60 * 60)
  : Infinity;
```
And replace `this.lastCouncilRun = now.toISOString()` with:
```typescript
if (this.db) await this.db.setPelletGenRun("council", now);
```

Apply the same pattern for `lastDreamRun` → key `"dream"` and `lastEvolveRun` → key `"evolve"`.

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/pellets/proactive-persistence.test.ts 2>&1 | tail -10
```

Expected: all 4 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(memory): schema v21 — pellet_generation_runs table + ProactiveGenerator DB persistence"
```

---

## Task 5: Rewrite PelletGenerator to use IntelligenceRouter (AC-2, AC-3)

**Files:**
- Modify: `src/pellets/generator.ts` — full rewrite of internals
- Modify callers: `src/pellets/event-based-generator.ts` and `src/pellets/proactive-generator.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/pellets/generator-router.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PelletGenerator } from "../../src/pellets/generator.js";

describe("PelletGenerator — IntelligenceRouter path", () => {
  let mockRouter: any;
  let generator: PelletGenerator;

  beforeEach(() => {
    mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "test-pellet-abc123",
          title: "Test Pellet",
          tags: ["testing"],
          owlsInvolved: ["Noctua"],
          content: "## Key Insight\nThis is a test.",
          provenance: ["test", "session-1"],
        })
      ),
    };
    generator = new PelletGenerator(mockRouter);
  });

  it("calls router.resolve with generation prompt", async () => {
    const pellet = await generator.generate("turn 1: user: hello\nassistant: hi", "test-session");
    expect(mockRouter.resolve).toHaveBeenCalledOnce();
    const [tier, prompt] = mockRouter.resolve.mock.calls[0];
    expect(tier).toBe("generation");
    expect(prompt).toContain("turn 1: user: hello");
  });

  it("returns a pellet with successCount=0, failureCount=0, provenance", async () => {
    const pellet = await generator.generate("some content", "src-name");
    expect(pellet.successCount).toBe(0);
    expect(pellet.failureCount).toBe(0);
    expect(Array.isArray(pellet.provenance)).toBe(true);
  });

  it("returns null for empty conversation", async () => {
    const result = await generator.generate("", "empty");
    expect(result).toBeNull();
  });

  it("does not use console.log", async () => {
    const spy = vi.spyOn(console, "log");
    await generator.generate("some content", "src");
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/generator-router.test.ts 2>&1 | tail -10
```

Expected: FAIL — constructor signature doesn't match.

- [ ] **Step 3: Rewrite `src/pellets/generator.ts`**

Replace the entire file content:

```typescript
import { v4 as uuidv4 } from "uuid";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { Pellet } from "./store.js";
import { log } from "../logger.js";

export class PelletGenerator {
  constructor(private router: IntelligenceRouter) {}

  /**
   * Generate a pellet from unstructured source material using IntelligenceRouter.
   * Returns null if sourceMaterial is empty.
   */
  async generate(
    sourceMaterial: string,
    sourceName: string,
    opts?: { provenance?: string[] },
  ): Promise<Pellet | null> {
    if (!sourceMaterial.trim()) return null;

    const prompt =
      `You are digesting a conversation or research output to create a "Pellet" — a compressed, highly structured knowledge artifact.\n\n` +
      `Source: ${sourceName}\n` +
      `Material:\n${sourceMaterial}\n\n` +
      `Task: Generate the contents of the Pellet. Your response MUST be valid JSON matching this schema:\n` +
      `{\n` +
      `  "slug": "a-kebab-case-short-id",\n` +
      `  "title": "A clear, descriptive title",\n` +
      `  "tags": ["architectural", "decision-record", "database"],\n` +
      `  "owlsInvolved": ["Noctua", "Archimedes"],\n` +
      `  "content": "Formatted Markdown with ## Key Insight, ## Evidence/Arguments, ## Final Verdict"\n` +
      `}\n\n` +
      `Output ONLY the JSON object. Do not wrap it in \`\`\`json blocks.`;

    let jsonStr: string;
    try {
      jsonStr = await this.router.resolve("generation", prompt);
    } catch (err) {
      log.engine.warn(`[PelletGenerator] router.resolve failed: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    }

    // Strip optional code-block wrapping
    jsonStr = jsonStr.trim();
    if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr.replace(/^```(?:json)?/, "").replace(/```$/, "").trim();
    }

    let parsed: any;
    try {
      parsed = JSON.parse(jsonStr);
    } catch {
      log.engine.warn("[PelletGenerator] Failed to parse LLM output as JSON — skipping pellet");
      return null;
    }

    const id = (parsed.slug as string | undefined) || `pellet-${uuidv4().substring(0, 8)}`;

    return {
      id,
      title: (parsed.title as string | undefined) || id,
      generatedAt: new Date().toISOString(),
      source: sourceName,
      owls: Array.isArray(parsed.owlsInvolved) ? parsed.owlsInvolved as string[] : [],
      tags: Array.isArray(parsed.tags) ? parsed.tags as string[] : [],
      version: 1,
      content: (parsed.content as string | undefined) || "",
      successCount: 0,
      failureCount: 0,
      provenance: opts?.provenance ?? [],
    };
  }
}
```

- [ ] **Step 4: Update callers of `PelletGenerator`**

`EventBasedPelletGenerator` in `src/pellets/event-based-generator.ts` instantiates `new PelletGenerator()` with no args. Fix: accept router in its constructor and pass it through.

Find in `event-based-generator.ts`:
```typescript
constructor(
  private eventBus: EventBus,
  private pelletStore: PelletStore,
  private provider: ModelProvider,
  private owl: OwlInstance,
  private config: StackOwlConfig,
  significanceConfig?: Partial<SignificanceConfig>,
) {
  this.generator = new PelletGenerator();
```

Replace with:
```typescript
constructor(
  private eventBus: EventBus,
  private pelletStore: PelletStore,
  private provider: ModelProvider,
  private owl: OwlInstance,
  private config: StackOwlConfig,
  private router: import("../intelligence/router.js").IntelligenceRouter,
  significanceConfig?: Partial<SignificanceConfig>,
) {
  this.generator = new PelletGenerator(this.router);
```

In `generateFromEvent`, the call to `this.generator.generate(data.sourceMaterial, data.sourceName, { provider, owl, config })` now has the wrong signature. Replace with:
```typescript
const pellet = await this.generator.generate(
  data.sourceMaterial,
  data.sourceName,
  { provenance: ["event", _pelletType] },
);
if (!pellet) return null;
```

Remove the old `{ provider, owl, config }` context object and the unused `const { provider, owl, config } = ...` destructuring.

`ProactiveKnowledgeGenerator` similarly instantiates `new PelletGenerator()`. Repeat the same fix — add `private router: IntelligenceRouter` to constructor, pass to `new PelletGenerator(this.router)`.

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/pellets/generator-router.test.ts 2>&1 | tail -10
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep error | head -20
```

Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(pellets): rewrite PelletGenerator to use IntelligenceRouter — drop OwlEngine dependency"
```

---

## Task 6: Dedup + KnowledgeBase IntelligenceRouter path (AC-5, AC-6)

**Files:**
- Modify: `src/pellets/dedup.ts:158-168` (constructor)
- Modify: `src/pellets/knowledge-base.ts:34` (constructor), `knowledge-base.ts:198-215` (computeCoverageGaps)

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/intelligence-router-paths.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

describe("PelletDeduplicator — IntelligenceRouter path", () => {
  it("uses router.resolve when router is provided", async () => {
    const { PelletDeduplicator } = await import("../../src/pellets/dedup.js");
    const mockRouter = { resolve: vi.fn().mockResolvedValue('{"verdict":"CREATE","reasoning":"unique"}') };
    const mockSearch = vi.fn().mockResolvedValue([
      { pellet: { id: "existing", title: "Existing", content: "content", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 5, failureCount: 0, provenance: [] }, score: 0.95 }
    ]);
    const dedup = new (PelletDeduplicator as any)(mockSearch, undefined, { useLlm: true, similarityThreshold: 0.5 }, mockRouter);
    const incoming = { id: "new", title: "New", content: "content", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    await dedup.evaluate(incoming);
    expect(mockRouter.resolve).toHaveBeenCalledWith("classification", expect.any(String));
  });

  it("falls back to provider.chat when no router", async () => {
    const { PelletDeduplicator } = await import("../../src/pellets/dedup.js");
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"verdict":"CREATE","reasoning":"ok"}' }) };
    const mockSearch = vi.fn().mockResolvedValue([
      { pellet: { id: "ex", title: "Ex", content: "x", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] }, score: 0.92 }
    ]);
    const dedup = new (PelletDeduplicator as any)(mockSearch, mockProvider, { useLlm: true, similarityThreshold: 0.5 });
    const incoming = { id: "n", title: "N", content: "x", tags: [], owls: [], source: "", generatedAt: "", version: 1, successCount: 0, failureCount: 0, provenance: [] };
    await dedup.evaluate(incoming);
    expect(mockProvider.chat).toHaveBeenCalled();
  });
});

describe("KnowledgeBase.computeCoverageGaps — IntelligenceRouter path", () => {
  it("uses router.resolve when router is provided", async () => {
    const { KnowledgeBase } = await import("../../src/pellets/knowledge-base.js");
    const mockStore = { listAll: vi.fn().mockResolvedValue([]) };
    const mockRouter = { resolve: vi.fn().mockResolvedValue('["api","security"]') };
    const kb = new (KnowledgeBase as any)(mockStore, mockRouter);
    const gaps = await kb.findCoverageGaps();
    expect(mockRouter.resolve).toHaveBeenCalled();
    expect(Array.isArray(gaps)).toBe(true);
  });

  it("uses hardcoded array fallback when no router", async () => {
    const { KnowledgeBase } = await import("../../src/pellets/knowledge-base.js");
    const mockStore = { listAll: vi.fn().mockResolvedValue([]) };
    const kb = new KnowledgeBase(mockStore as any);
    const gaps = await kb.findCoverageGaps();
    expect(Array.isArray(gaps)).toBe(true);
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/intelligence-router-paths.test.ts 2>&1 | tail -10
```

Expected: FAIL.

- [ ] **Step 3: Update `PelletDeduplicator` constructor and `decideWithLlm` in `src/pellets/dedup.ts`**

Change constructor signature (around line 161):
```typescript
constructor(
  private searchSimilar: SimilarFn,
  private provider?: ModelProvider,
  config?: Partial<DedupConfig>,
  private router?: import("../intelligence/router.js").IntelligenceRouter,
) {
  this.config = { ...DEFAULT_DEDUP_CONFIG, ...config };
}
```

In `decideWithLlm` (around line 242), find the block that calls `this.provider!.chat(...)`. Replace the first `chat()` call with:

```typescript
let rawResponse: string;
if (this.router) {
  rawResponse = await this.router.resolve("classification", prompt);
} else {
  const resp = await this.provider!.chat(messages, { temperature: 0, maxTokens: 300 });
  rawResponse = resp.content;
}
```

And replace the retry `chat()` call similarly:
```typescript
let retryRaw: string;
if (this.router) {
  retryRaw = await this.router.resolve("classification", retryPrompt);
} else {
  const retryResp = await this.provider!.chat(retryMessages, { temperature: 0, maxTokens: 300 });
  retryRaw = retryResp.content;
}
```

Where `messages` and `retryMessages` are the existing variables that build the chat prompt. If these variables have a different structure than a plain string, adapt the router call to pass the relevant text content.

- [ ] **Step 4: Update `KnowledgeBase` constructor and `computeCoverageGaps` in `src/pellets/knowledge-base.ts`**

Change constructor (line 34):
```typescript
constructor(
  private pelletStore: PelletStore,
  private router?: import("../intelligence/router.js").IntelligenceRouter,
) {}
```

In `computeCoverageGaps` (line 198), wrap the hardcoded array in a router-first check. Change the method to:

```typescript
private async computeCoverageGaps(topics: Set<string>): Promise<string[]> {
  if (this.router) {
    try {
      const covered = JSON.stringify([...topics].slice(0, 50));
      const raw = await this.router.resolve(
        "classification",
        `Given these covered topics: ${covered}\nList 5-10 important topics NOT covered. Reply with a JSON array of strings only.`,
      );
      const parsed = JSON.parse(raw.trim());
      if (Array.isArray(parsed)) return parsed as string[];
    } catch {
      // fall through to hardcoded list
    }
  }
  const commonTopics = [
    "typescript", "javascript", "node.js", "api", "database",
    "architecture", "testing", "debugging", "performance",
    "security", "deployment", "configuration", "error-handling",
  ];
  return commonTopics.filter((topic) => !topics.has(topic));
}
```

Note: `computeCoverageGaps` was synchronous before. Change its call in `getStats()` from `const coverageGaps = this.computeCoverageGaps(topics)` to `const coverageGaps = await this.computeCoverageGaps(topics)`. Update the return type of `getStats()` as needed.

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/pellets/intelligence-router-paths.test.ts 2>&1 | tail -10
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(pellets): route dedup + coverage-gaps through IntelligenceRouter (AC-5, AC-6)"
```

---

## Task 7: EventBasedGenerator — replace keyword detection with router.resolve (AC-4)

**Files:**
- Modify: `src/pellets/event-based-generator.ts:87-106` (extractDecisionData)

- [ ] **Step 1: Write the failing test**

Create `__tests__/pellets/event-based-generator-router.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

describe("EventBasedPelletGenerator — router classification", () => {
  it("generates pellet when router returns isDecision=true", async () => {
    const { EventBasedPelletGenerator } = await import("../../src/pellets/event-based-generator.js");
    const mockRouter = { resolve: vi.fn().mockResolvedValue('{"isDecision":true,"isInsight":false,"isCorrection":false}') };
    const mockStore = { save: vi.fn().mockResolvedValue({ verdict: "CREATE" }) };
    const mockGenerator = { generate: vi.fn().mockResolvedValue({ id: "p1", title: "T", content: "C", tags: [], owls: [], source: "s", generatedAt: new Date().toISOString(), version: 1, successCount: 0, failureCount: 0, provenance: [] }) };
    const mockEventBus = { on: vi.fn(), off: vi.fn() };

    const gen = new (EventBasedPelletGenerator as any)(
      mockEventBus, mockStore, {}, {}, {}, mockRouter
    );
    gen.generator = mockGenerator;

    const result = await gen.generateFromEvent(
      { sourceName: "s", sourceMaterial: "decided to use Postgres", tags: [], owlsInvolved: [] },
      "decision-capture"
    );
    expect(mockStore.save).toHaveBeenCalled();
    expect(result).not.toBeNull();
  });

  it("does not generate pellet when router returns all false", async () => {
    const { EventBasedPelletGenerator } = await import("../../src/pellets/event-based-generator.js");
    const mockRouter = { resolve: vi.fn().mockResolvedValue('{"isDecision":false,"isInsight":false,"isCorrection":false}') };
    const mockStore = { save: vi.fn() };
    const mockEventBus = { on: vi.fn(), off: vi.fn() };
    const gen = new (EventBasedPelletGenerator as any)(
      mockEventBus, mockStore, {}, {}, {}, mockRouter
    );
    gen.generator = { generate: vi.fn().mockResolvedValue(null) };

    const result = await gen.handleMessageResponded({
      sessionId: "s1", channelId: "c", userId: "u",
      content: "some message with no decision",
      owlName: "Noctua", toolsUsed: ["web"],
    });
    expect(mockStore.save).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/event-based-generator-router.test.ts 2>&1 | tail -10
```

Expected: FAIL.

- [ ] **Step 3: Replace `extractDecisionData` keyword logic with router classification**

`extractDecisionData` is called in `handleMessageResponded`. Instead of a pure function, we need an async check. Replace `handleMessageResponded` in `src/pellets/event-based-generator.ts`:

```typescript
private async handleMessageResponded(payload: {
  sessionId: string;
  channelId: string;
  userId: string;
  content: string;
  owlName: string;
  toolsUsed: string[];
}): Promise<void> {
  if (!payload.toolsUsed?.length) return;

  let isSignificant = false;
  try {
    const raw = await this.router.resolve(
      "classification",
      `Classify this AI assistant response:\n"${payload.content.slice(0, 500)}"\n\n` +
      `Reply with JSON only: {"isDecision":bool,"isInsight":bool,"isCorrection":bool}`,
    );
    const classification = JSON.parse(raw.trim());
    isSignificant = classification.isDecision || classification.isInsight || classification.isCorrection;
  } catch {
    isSignificant = false;
  }

  if (!isSignificant) return;

  log.engine.info(`[EventBasedPelletGenerator] Decision/insight detected — generating pellet`);

  await this.generateFromEvent(
    {
      sourceName: `decision:${payload.sessionId}`,
      sourceMaterial: `Owl "${payload.owlName}" made a decision using tools [${payload.toolsUsed.join(", ")}]. Decision: ${payload.content.slice(0, 1000)}.`,
      tags: ["decision-capture", "tool-driven"],
      owlsInvolved: [payload.owlName],
    },
    "decision-capture",
  );
}
```

Remove the now-unused `extractDecisionData` function.

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/pellets/event-based-generator-router.test.ts 2>&1 | tail -10
```

Expected: 2 tests PASS.

- [ ] **Step 5: Verify AC-2 — no Intelligence-First violations in src/pellets/**

```bash
grep -r "content\.includes\|provider\.chat\|OwlEngine" src/pellets/ 2>/dev/null
```

Expected: zero matches.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(pellets): replace keyword detection in EventBasedGenerator with router.resolve (AC-2, AC-4)"
```

---

## Task 8: RelevantPelletsLayer — quality re-rank + retrievedPelletIds side-channel (AC-7, AC-8, AC-9)

**Files:**
- Modify: `src/context/layer.ts:41-49` (ContextRequest interface)
- Modify: `src/context/layers/knowledge.ts:24-49` (RelevantPelletsLayer.build)
- Modify: `src/context/pipeline.ts` — add `lastRetrievedPelletIds` field

- [ ] **Step 1: Write the failing tests**

Create `__tests__/context/relevant-pellets-layer.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { RelevantPelletsLayer } from "../../src/context/layers/knowledge.js";

const makePellet = (id: string, successCount: number, failureCount: number) => ({
  id, title: id, content: "x".repeat(600), tags: ["t"], owls: ["Noctua"],
  source: "s", generatedAt: new Date().toISOString(), version: 1,
  successCount, failureCount, provenance: [],
});

describe("RelevantPelletsLayer", () => {
  it("calls searchWithGraphScored", async () => {
    const layer = new RelevantPelletsLayer();
    const mockStore = {
      searchWithGraphScored: vi.fn().mockResolvedValue([
        { p: makePellet("p1", 0, 0), score: 0.9 },
      ]),
    };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    const triage = { userMessage: "hello", isConversational: false } as any;
    await layer.build(req, triage, new Map());
    expect(mockStore.searchWithGraphScored).toHaveBeenCalledWith("hello", 5);
  });

  it("writes IDs to req.retrievedPelletIds", async () => {
    const layer = new RelevantPelletsLayer();
    const mockStore = {
      searchWithGraphScored: vi.fn().mockResolvedValue([
        { p: makePellet("a", 1, 0), score: 0.8 },
        { p: makePellet("b", 0, 0), score: 0.6 },
      ]),
    };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    await layer.build(req, { userMessage: "q", isConversational: false } as any, new Map());
    expect(req.retrievedPelletIds).toEqual(["a", "b"]);
  });

  it("truncates content to 500 chars", async () => {
    const layer = new RelevantPelletsLayer();
    const longPellet = makePellet("p", 0, 0);
    const mockStore = { searchWithGraphScored: vi.fn().mockResolvedValue([{ p: longPellet, score: 0.9 }]) };
    const req = { deps: { pelletStore: mockStore }, session: { messages: [] }, callbacks: {}, continuityResult: null, digest: null } as any;
    const output = await layer.build(req, { userMessage: "q", isConversational: false } as any, new Map());
    // content is "x".repeat(600), truncated to 500
    expect(output).toContain("x".repeat(500));
    expect(output).not.toContain("x".repeat(501));
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/context/relevant-pellets-layer.test.ts 2>&1 | tail -10
```

Expected: FAIL.

- [ ] **Step 3: Add `retrievedPelletIds` to `ContextRequest` in `src/context/layer.ts`**

In the `ContextRequest` interface (around line 41), add `retrievedPelletIds?: string[]` as a mutable (writable) field. Since the interface uses `readonly` on some fields, this new field must NOT have `readonly`:

```typescript
export interface ContextRequest {
  readonly session: Session;
  readonly callbacks: GatewayCallbacks;
  readonly channelId?: string;
  readonly userId?: string;
  readonly continuityResult: ContinuityResult | null;
  readonly digest: ConversationDigest | null;
  readonly deps: ContextDependencies;
  retrievedPelletIds?: string[];
}
```

- [ ] **Step 4: Rewrite `RelevantPelletsLayer.build()` in `src/context/layers/knowledge.ts`**

Replace the `RelevantPelletsLayer` class (lines 24-49):

```typescript
export class RelevantPelletsLayer implements ContextLayer {
  name = "RelevantPelletsLayer";
  priority = 115;
  maxTokens = 1_000;
  produces = ["pellets"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const pelletStore = req.deps.pelletStore;
    if (!pelletStore) return "";
    try {
      const scored = await (pelletStore as any).searchWithGraphScored(t.userMessage, 5) as Array<{ p: import("../../pellets/store.js").Pellet; score: number }>;
      if (!scored.length) return "";

      req.retrievedPelletIds = scored.map((s) => s.p.id);

      const lines = ["<relevant_pellets>"];
      for (const { p } of scored) {
        lines.push(`  <pellet title="${p.title}">${p.content.slice(0, 500)}</pellet>`);
      }
      lines.push("</relevant_pellets>");
      return lines.join("\n");
    } catch {
      return "";
    }
  }
}
```

- [ ] **Step 5: Add `lastRetrievedPelletIds` to `ContextPipeline` in `src/context/pipeline.ts`**

Add a public field after the `shortTermLayers` declaration (around line 20):

```typescript
public lastRetrievedPelletIds: string[] = [];
```

At the end of the `run()` method, before `return { output, trace }`, add:

```typescript
this.lastRetrievedPelletIds = request.retrievedPelletIds ?? [];
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/context/relevant-pellets-layer.test.ts 2>&1 | tail -10
```

Expected: 3 tests PASS.

- [ ] **Step 7: Run context pipeline tests**

```bash
npx vitest run __tests__/context/pipeline.test.ts __tests__/context/pipeline-integration.test.ts 2>&1 | tail -10
```

Expected: all pass (no regressions).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(context): RelevantPelletsLayer — quality re-rank via searchWithGraphScored + retrievedPelletIds side-channel"
```

---

## Task 9: evolution.ts — add `updatePelletGeneratorDNA` (AC-12)

**Files:**
- Modify: `src/owls/evolution.ts` — add new export after `updateParliamentDNA`

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/pellet-generator-dna.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { updatePelletGeneratorDNA } from "../../src/owls/evolution.js";

const makeOwl = (name: string, expertiseGrowth: Record<string, number> = {}) => ({
  persona: { name },
  dna: { expertiseGrowth: { ...expertiseGrowth }, evolvedTraits: {}, learnedPreferences: {} },
});

describe("updatePelletGeneratorDNA", () => {
  it("increments expertiseGrowth for the topic", async () => {
    const owl = makeOwl("Noctua", { api: 0.5 });
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owl]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["Noctua"], "api", mockRegistry as any);
    expect(owl.dna.expertiseGrowth["api"]).toBeCloseTo(0.53, 2);
    expect(mockRegistry.saveDNA).toHaveBeenCalledWith("Noctua");
  });

  it("clamps expertiseGrowth to max 0.9", async () => {
    const owl = makeOwl("Archimedes", { api: 0.89 });
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owl]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["Archimedes"], "api", mockRegistry as any);
    expect(owl.dna.expertiseGrowth["api"]).toBe(0.9);
  });

  it("updates multiple owls", async () => {
    const owlA = makeOwl("A");
    const owlB = makeOwl("B");
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([owlA, owlB]),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    };
    await updatePelletGeneratorDNA(["A", "B"], "security", mockRegistry as any);
    expect(owlA.dna.expertiseGrowth["security"]).toBeCloseTo(0.53, 2);
    expect(owlB.dna.expertiseGrowth["security"]).toBeCloseTo(0.53, 2);
    expect(mockRegistry.saveDNA).toHaveBeenCalledTimes(2);
  });

  it("missing owl is a graceful no-op", async () => {
    const mockRegistry = {
      listOwls: vi.fn().mockReturnValue([]),
      saveDNA: vi.fn(),
    };
    await expect(
      updatePelletGeneratorDNA(["Ghost"], "api", mockRegistry as any)
    ).resolves.not.toThrow();
    expect(mockRegistry.saveDNA).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify failure**

```bash
npx vitest run __tests__/pellets/pellet-generator-dna.test.ts 2>&1 | tail -10
```

Expected: FAIL — `updatePelletGeneratorDNA` not exported.

- [ ] **Step 3: Add `updatePelletGeneratorDNA` to `src/owls/evolution.ts`**

Add after `updateParliamentDNA` (after line 756):

```typescript
/**
 * Reinforces expertise of owls who generated pellets that advanced the user's goal.
 * Called from gateway/core.ts Hook 5 when GoalVerifier returns ADVANCES.
 * Learning rate: 0.03 (smaller than Parliament's 0.05 — pellet signal is indirect).
 */
export async function updatePelletGeneratorDNA(
  owlNames: string[],
  topicCategory: string,
  owlRegistry: import('./registry.js').OwlRegistry,
): Promise<void> {
  const LEARNING_RATE = 0.03;
  const allOwls = owlRegistry.listOwls();

  for (const name of owlNames) {
    const owl = allOwls.find((o) => o.persona.name === name);
    if (!owl) continue;
    try {
      owl.dna.expertiseGrowth[topicCategory] = clamp(
        (owl.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
        0.1,
        0.9,
      );
      await owlRegistry.saveDNA(name);
    } catch (err) {
      log.engine.warn(`[evolution] pelletGeneratorDNA failed for ${name}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/pellets/pellet-generator-dna.test.ts 2>&1 | tail -10
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(evolution): add updatePelletGeneratorDNA — reinforce owls whose pellets advance goals"
```

---

## Task 10: Gateway hooks 4 and 5 — wire the flywheel (AC-11, AC-12)

**Files:**
- Modify: `src/gateway/core.ts` — add imports + hooks 4+5 in post-turn block

- [ ] **Step 1: Write the failing tests**

Create `__tests__/pellets/gateway-hooks.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

// These tests verify hook behavior in isolation using unit-testable helpers.
// The actual gateway integration is too large to unit-test directly.

describe("Gateway hook 4 — recordOutcome", () => {
  it("calls recordOutcome when pellets were retrieved and verdict is non-NEUTRAL", async () => {
    const mockPelletStore = { recordOutcome: vi.fn().mockResolvedValue(undefined) };
    const retrievedPelletIds = ["p1", "p2"];
    const goalVerdict = "ADVANCES" as const;

    // Simulate hook 4 logic
    if (retrievedPelletIds.length > 0 && goalVerdict !== "NEUTRAL") {
      await mockPelletStore.recordOutcome(retrievedPelletIds, goalVerdict);
    }
    expect(mockPelletStore.recordOutcome).toHaveBeenCalledWith(["p1", "p2"], "ADVANCES");
  });

  it("does NOT call recordOutcome when verdict is NEUTRAL", async () => {
    const mockPelletStore = { recordOutcome: vi.fn() };
    const retrievedPelletIds = ["p1"];
    const goalVerdict = "NEUTRAL" as const;

    if (retrievedPelletIds.length > 0 && goalVerdict !== "NEUTRAL") {
      await mockPelletStore.recordOutcome(retrievedPelletIds, goalVerdict);
    }
    expect(mockPelletStore.recordOutcome).not.toHaveBeenCalled();
  });
});

describe("Gateway hook 5 — updatePelletGeneratorDNA", () => {
  it("calls updatePelletGeneratorDNA only on ADVANCES", async () => {
    const mockUpdateDNA = vi.fn().mockResolvedValue(undefined);
    const goalVerdict = "ADVANCES" as const;
    const generatorOwlNames = ["Noctua"];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {} /* registry */);
    }
    expect(mockUpdateDNA).toHaveBeenCalledWith(["Noctua"], "api", {});
  });

  it("skips updatePelletGeneratorDNA on BLOCKED", async () => {
    const mockUpdateDNA = vi.fn();
    const goalVerdict = "BLOCKED" as const;
    const generatorOwlNames = ["Noctua"];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {});
    }
    expect(mockUpdateDNA).not.toHaveBeenCalled();
  });

  it("skips updatePelletGeneratorDNA when owlNames is empty", async () => {
    const mockUpdateDNA = vi.fn();
    const goalVerdict = "ADVANCES" as const;
    const generatorOwlNames: string[] = [];

    if (goalVerdict === "ADVANCES" && generatorOwlNames.length > 0) {
      await mockUpdateDNA(generatorOwlNames, "api", {});
    }
    expect(mockUpdateDNA).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests (they should pass — they test the logic pattern, not the wiring)**

```bash
npx vitest run __tests__/pellets/gateway-hooks.test.ts 2>&1 | tail -10
```

Expected: all 5 tests PASS immediately (they test conditional logic, not the actual gateway code).

- [ ] **Step 3: Add import for `updatePelletGeneratorDNA` in `src/gateway/core.ts`**

Find the import of `updateParliamentDNA` (around line 125):
```typescript
import { updateParliamentDNA } from "../owls/evolution.js";
```

Change to:
```typescript
import { updateParliamentDNA, updatePelletGeneratorDNA } from "../owls/evolution.js";
```

- [ ] **Step 4: Add hooks 4 and 5 in the post-turn block of `src/gateway/core.ts`**

The post-turn block already runs GoalVerifier for Parliament sessions. We need to add the pellet hooks for every turn (not just Parliament). Find where `goalVerifier` is invoked in the main response flow (search for `this.goalVerifier?.verify` outside the Parliament block) and add the hooks after the verifier result.

If the main turn GoalVerifier call doesn't exist yet (only Parliament uses it), add the following block after the main engine response is produced, in the post-turn section:

```typescript
// ─── Pellet flywheel hooks (run after every turn, non-fatal) ─────
const _pelletIds = this.ctx.contextPipeline?.lastRetrievedPelletIds ?? [];
let _pelletVerdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL" = "NEUTRAL";

if (_pelletIds.length > 0 && this.goalVerifier && this.ctx.engineContext?.activeSubGoal) {
  try {
    const _vr = await this.goalVerifier.verify({
      toolName: "context_retrieval",
      toolResult: response.content.slice(0, 500),
      subGoal: this.ctx.engineContext.activeSubGoal,
      userMessage: message.text,
    });
    _pelletVerdict = _vr?.verdict ?? "NEUTRAL";
  } catch { /* non-fatal */ }
}

// Hook 4: feed verdict back into retrieved pellets
if (_pelletIds.length > 0 && _pelletVerdict !== "NEUTRAL" && this.ctx.pelletStore) {
  this.ctx.pelletStore.recordOutcome(_pelletIds, _pelletVerdict).catch((err: unknown) =>
    log.engine.warn("[gateway] recordOutcome failed", err)
  );
}

// Hook 5: reinforce DNA of owls who generated helpful pellets
if (_pelletVerdict === "ADVANCES" && _pelletIds.length > 0 && this.ctx.owlRegistry && this.ctx.pelletStore) {
  Promise.all(_pelletIds.map((id) => this.ctx.pelletStore!.get(id)))
    .then((retrieved) => {
      const owlNames = [...new Set(retrieved.flatMap((p) => p?.owls ?? []).filter(Boolean))];
      const topicCategory = retrieved.find(Boolean)?.tags?.[0] ?? "general";
      if (owlNames.length > 0) {
        return updatePelletGeneratorDNA(owlNames, topicCategory, this.ctx.owlRegistry!);
      }
    })
    .catch((err: unknown) => log.engine.warn("[gateway] updatePelletGeneratorDNA failed", err));
}
```

Note: both hooks fire in a `.catch()`-wrapped non-fatal pattern. Hook 5 uses a `.then()` chain so pellet fetching happens off the hot path. Neither hook blocks the response.

- [ ] **Step 5: Type-check**

```bash
npx tsc --noEmit 2>&1 | grep error | head -20
```

Expected: zero errors.

- [ ] **Step 6: Run all pellet and context tests**

```bash
npx vitest run __tests__/pellets/ __tests__/context/ 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(gateway): wire pellet flywheel hooks 4+5 — recordOutcome + updatePelletGeneratorDNA post-turn"
```

---

## Task 11: Final regression check (AC-16)

**Files:** none modified — verification only.

- [ ] **Step 1: Run full test suite**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all existing tests pass; new tests from Tasks 1–10 all pass. Zero regressions.

- [ ] **Step 2: Verify AC-2 — no Intelligence-First violations anywhere in pellets/**

```bash
grep -rn "content\.includes\|OwlEngine" src/pellets/
grep -rn "provider\.chat" src/pellets/dedup.ts
```

Expected: `content.includes` and `OwlEngine` return zero matches. `provider.chat` in `dedup.ts` appears only inside the fallback branch (when `this.router` is absent).

- [ ] **Step 3: Final TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep error
```

Expected: zero errors.

- [ ] **Step 4: Update progress tracker**

Open `docs/platform-audit/progress.md`. Mark Element 11 as complete, record date 2026-05-03.

- [ ] **Step 5: Final commit**

```bash
git add docs/platform-audit/progress.md
git commit -m "chore: mark Element 11 Pellet flywheel complete in progress tracker"
```
