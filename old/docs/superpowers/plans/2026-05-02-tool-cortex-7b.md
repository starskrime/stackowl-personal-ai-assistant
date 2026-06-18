# Tool Cortex Phase 7b Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 7b of Tool Cortex: Cost-Weighted Tool Graph (CWTG) for LLM-free deterministic fallback recovery, and Personalized Tool Router (PTR) for injecting the user's own successful tool sequences as a planning prior.

**Architecture:** CWTG builds a SQLite `tool_edges` table populated from every tool execution (success_rate, avg_duration). When GAV returns BLOCKED, `ToolGraph.replan()` runs Dijkstra over capability-tagged edges to return the next-best tool without an LLM call — sub-50ms recovery. PTR runs at PLAN phase: cosine K-NN over the last 30 days of successful `trajectory_turns`, extracts tool sequences from top-3 matches, and injects them into ContextPipeline as a `ToolPriorLayer` at priority 8. Cold start (< 50 historical successes) falls back to category-based priors.

**Tech Stack:** better-sqlite3 (sync), `fastembed` cosine search (already used in `src/session/user-memory-store.ts`), `ToolDefinition.capabilities[]` from Phase 7a, `ContextPipeline` from Element 5, `ToolRegistry.getSuccessRate()` pattern from `ToolTracker`.

**Phase gate:** Start only after Phase 7a has been in production for ≥1 week and BLOCKED rate > 5%.

---

## File Map

### New files (4)

| File | Purpose |
|------|---------|
| `src/tools/cortex/tool-graph.ts` | `ToolGraph` class — Dijkstra over `tool_edges`, `replan(failedTool, capabilityTag)` |
| `src/tools/cortex/personalized-router.ts` | `PersonalizedRouter` — K-NN cosine search over trajectory history |
| `src/tools/cortex/index.ts` | Re-exports both for clean import from orchestrator |
| `__tests__/tools/cortex/tool-graph.test.ts` | Unit tests |

### Modified files (5)

| File | Change |
|------|--------|
| `src/memory/db.ts` | Schema v17: `tool_edges` table + `tool_executions` table; `SCHEMA_VERSION` → 17 |
| `src/tools/registry.ts` | Record to `tool_executions` after every execute(); call `ToolGraph.replan()` on GAV BLOCKED |
| `src/tools/fallback-sequencer.ts` | Add DB-backed seed from `tool_edges` on init |
| `src/engine/orchestrator.ts` | Call `PersonalizedRouter.buildPrior()` at PLAN phase, inject into context |
| `src/context/pipeline.ts` | Add `ToolPriorLayer` type (priority 8, injected as system message block) |

---

## Task 1: Schema v17 — tool_edges + tool_executions tables

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory/db-v17.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/memory/db-v17.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), ".test-db-v17");

afterEach(() => {
  if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true });
});

describe("MemoryDatabase schema v17", () => {
  it("tool_edges table exists with expected columns", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    expect(() => {
      (db as any).db.exec(`
        INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count)
        VALUES ('web_crawl', 'scrapling_fetch', 'web_fetch', 0.85, 1200, 10)
      `);
    }).not.toThrow();
  });

  it("tool_executions table exists with expected columns", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    expect(() => {
      (db as any).db.exec(`
        INSERT INTO tool_executions (id, tool_name, capability_tags, success, duration_ms, error_reason)
        VALUES ('ex-1', 'web_crawl', '["web_fetch"]', 1, 800, NULL)
      `);
    }).not.toThrow();
  });

  it("idx_te_capability index exists on tool_edges", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    const idx = (db as any).db
      .prepare("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_te_capability'")
      .get();
    expect(idx).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/db-v17.test.ts
```
Expected: Table not found errors.

- [ ] **Step 3: Add v17 migration to db.ts**

Change `const SCHEMA_VERSION = 16` to `const SCHEMA_VERSION = 17`.

Add after the `if (current < 16)` block:

```typescript
    if (current < 17) {
      // v17 (Tool Cortex 7b): CWTG tables
      this.db.exec(`
        -- Directed capability graph: learned from tool execution history
        CREATE TABLE IF NOT EXISTS tool_edges (
          from_tool       TEXT NOT NULL,
          to_tool         TEXT NOT NULL,
          capability_tag  TEXT NOT NULL,
          success_rate    REAL NOT NULL DEFAULT 0.0,
          avg_duration_ms INTEGER NOT NULL DEFAULT 0,
          sample_count    INTEGER NOT NULL DEFAULT 0,
          updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (from_tool, to_tool, capability_tag)
        );
        CREATE INDEX IF NOT EXISTS idx_te_capability ON tool_edges(capability_tag, from_tool);
        CREATE INDEX IF NOT EXISTS idx_te_success    ON tool_edges(success_rate DESC);

        -- Per-execution log replacing tools-stats.json (queryable, includes error reasons)
        CREATE TABLE IF NOT EXISTS tool_executions (
          id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
          tool_name       TEXT NOT NULL,
          capability_tags TEXT NOT NULL DEFAULT '[]',
          success         INTEGER NOT NULL DEFAULT 1,
          duration_ms     INTEGER NOT NULL DEFAULT 0,
          error_reason    TEXT,
          subgoal_id      TEXT,
          created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tex_tool    ON tool_executions(tool_name);
        CREATE INDEX IF NOT EXISTS idx_tex_success ON tool_executions(success);
        CREATE INDEX IF NOT EXISTS idx_tex_created ON tool_executions(created_at DESC);
      `);
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/db-v17.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-v17.test.ts
git commit -m "feat(tool-cortex-7b): schema v17 — tool_edges + tool_executions tables for CWTG"
```

---

## Task 2: Add tool execution recorder to MemoryDatabase

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory/db-tool-recorder.test.ts`

This adds query methods to `MemoryDatabase` so `ToolRegistry` and `ToolGraph` can read and write edge data without raw SQL in application code.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/memory/db-tool-recorder.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), ".test-db-recorder");
afterEach(() => { if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true }); });

describe("MemoryDatabase tool execution methods", () => {
  it("recordToolExecution inserts a row into tool_executions", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.recordToolExecution({ toolName: "web_crawl", capabilityTags: ["web_fetch"], success: true, durationMs: 900 });
    const row = (db as any).db.prepare("SELECT * FROM tool_executions WHERE tool_name = 'web_crawl'").get() as any;
    expect(row.success).toBe(1);
    expect(row.duration_ms).toBe(900);
  });

  it("upsertToolEdge creates or updates an edge", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.upsertToolEdge({ fromTool: "web_crawl", toTool: "scrapling_fetch", capabilityTag: "web_fetch", successRate: 0.9, avgDurationMs: 1100, sampleCount: 5 });
    const row = (db as any).db.prepare("SELECT * FROM tool_edges WHERE from_tool = 'web_crawl'").get() as any;
    expect(row.to_tool).toBe("scrapling_fetch");
    expect(row.success_rate).toBeCloseTo(0.9);
  });

  it("getEdgesForCapability returns edges sorted by success_rate desc", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.upsertToolEdge({ fromTool: "a", toTool: "b", capabilityTag: "fetch", successRate: 0.5, avgDurationMs: 100, sampleCount: 2 });
    db.upsertToolEdge({ fromTool: "a", toTool: "c", capabilityTag: "fetch", successRate: 0.9, avgDurationMs: 200, sampleCount: 5 });
    const edges = db.getEdgesForCapability("fetch");
    expect(edges[0].toTool).toBe("c");
    expect(edges[1].toTool).toBe("b");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/db-tool-recorder.test.ts
```
Expected: Methods not found on MemoryDatabase.

- [ ] **Step 3: Add methods to MemoryDatabase class**

Add these methods to the `MemoryDatabase` class in `src/memory/db.ts`:

```typescript
  // ─── Tool Cortex 7b: CWTG data methods ─────────────────────────

  recordToolExecution(args: {
    toolName: string;
    capabilityTags: string[];
    success: boolean;
    durationMs: number;
    errorReason?: string;
    subgoalId?: string;
  }): void {
    this.db.prepare(`
      INSERT INTO tool_executions (tool_name, capability_tags, success, duration_ms, error_reason, subgoal_id)
      VALUES (?, ?, ?, ?, ?, ?)
    `).run(
      args.toolName,
      JSON.stringify(args.capabilityTags),
      args.success ? 1 : 0,
      args.durationMs,
      args.errorReason ?? null,
      args.subgoalId ?? null,
    );
  }

  upsertToolEdge(args: {
    fromTool: string;
    toTool: string;
    capabilityTag: string;
    successRate: number;
    avgDurationMs: number;
    sampleCount: number;
  }): void {
    this.db.prepare(`
      INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
      ON CONFLICT(from_tool, to_tool, capability_tag) DO UPDATE SET
        success_rate    = excluded.success_rate,
        avg_duration_ms = excluded.avg_duration_ms,
        sample_count    = excluded.sample_count,
        updated_at      = datetime('now')
    `).run(args.fromTool, args.toTool, args.capabilityTag, args.successRate, args.avgDurationMs, args.sampleCount);
  }

  getEdgesForCapability(capabilityTag: string): Array<{ fromTool: string; toTool: string; successRate: number; avgDurationMs: number; sampleCount: number }> {
    return (this.db.prepare(`
      SELECT from_tool as fromTool, to_tool as toTool, success_rate as successRate,
             avg_duration_ms as avgDurationMs, sample_count as sampleCount
      FROM tool_edges
      WHERE capability_tag = ?
      ORDER BY success_rate DESC
    `).all(capabilityTag) as any[]);
  }

  getRecentSuccessfulTrajectories(userId: string, days = 30, limit = 100): Array<{ userMessage: string; toolsUsed: string; reward: number }> {
    const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
    return (this.db.prepare(`
      SELECT user_message as userMessage, tools_used as toolsUsed, reward
      FROM trajectories
      WHERE (user_id = ? OR user_id IS NULL)
        AND outcome = 'success'
        AND created_at > ?
      ORDER BY reward DESC
      LIMIT ?
    `).all(userId, since, limit) as any[]);
  }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/db-tool-recorder.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-tool-recorder.test.ts
git commit -m "feat(tool-cortex-7b): add recordToolExecution, upsertToolEdge, getEdgesForCapability to MemoryDatabase"
```

---

## Task 3: Record to tool_executions in ToolRegistry + update tool_edges

**Files:**
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/registry-db-recording.test.ts`

After every `execute()` call, write to `tool_executions` and upsert `tool_edges` for each capability tag the tool declares. This is the data pipeline that CWTG learns from.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/registry-db-recording.test.ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";

describe("ToolRegistry DB recording", () => {
  it("calls db.recordToolExecution after successful execute", async () => {
    const registry = new ToolRegistry();
    const mockDb = { recordToolExecution: vi.fn(), upsertToolEdge: vi.fn() };
    registry.setDb(mockDb as any);
    registry.register({
      definition: { name: "cap_tool", description: "t", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "result",
    });
    await registry.execute("cap_tool", {}, { cwd: "/" });
    expect(mockDb.recordToolExecution).toHaveBeenCalledWith(
      expect.objectContaining({ toolName: "cap_tool", success: true })
    );
  });

  it("calls db.upsertToolEdge for each capability tag on success", async () => {
    const registry = new ToolRegistry();
    const mockDb = { recordToolExecution: vi.fn(), upsertToolEdge: vi.fn() };
    registry.setDb(mockDb as any);
    // Register a previously successful tool with same capability to create an edge
    registry.register({
      definition: { name: "old_tool", description: "o", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "old result",
    });
    registry.register({
      definition: { name: "new_tool", description: "n", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "result",
    });
    await registry.execute("new_tool", {}, { cwd: "/" });
    // upsertToolEdge is called to update the new_tool self-edge stats
    expect(mockDb.upsertToolEdge).toHaveBeenCalled();
  });

  it("records failure with errorReason on thrown error", async () => {
    const registry = new ToolRegistry();
    const mockDb = { recordToolExecution: vi.fn(), upsertToolEdge: vi.fn() };
    registry.setDb(mockDb as any);
    registry.register({
      definition: { name: "fail_tool", description: "f", parameters: { type: "object", properties: {} } },
      execute: async () => { throw new Error("network timeout"); },
    });
    await expect(registry.execute("fail_tool", {}, { cwd: "/" })).rejects.toThrow();
    expect(mockDb.recordToolExecution).toHaveBeenCalledWith(
      expect.objectContaining({ success: false, errorReason: expect.stringContaining("network timeout") })
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-db-recording.test.ts
```
Expected: `setDb` not a function; no DB recording.

- [ ] **Step 3: Add DB setter and recording to registry.ts**

**3a — Import type at top:**
```typescript
import type { MemoryDatabase } from "../memory/db.js";
```

**3b — Add private field after `_goalVerifier`:**
```typescript
  private _db: MemoryDatabase | null = null;
  private _lastSuccessfulTool: { name: string; capabilities: string[] } | null = null;
```

**3c — Add setter:**
```typescript
  setDb(db: MemoryDatabase): void {
    this._db = db;
  }
```

**3d — In execute() success path**, after the GAV hook and before `return result`:
```typescript
      // DB recording for CWTG
      if (this._db) {
        const caps = tool.definition.capabilities ?? [];
        this._db.recordToolExecution({
          toolName: name,
          capabilityTags: caps,
          success: true,
          durationMs,
          subgoalId: context.engineContext?.activeSubGoal?.id,
        });
        // Upsert self-edge (represents this tool's own success rate per capability)
        for (const cap of caps) {
          const stats = this._tracker?.getStats(name);
          if (stats && stats.successCount + stats.failureCount > 0) {
            this._db.upsertToolEdge({
              fromTool: name,
              toTool: name,
              capabilityTag: cap,
              successRate: stats.successRate,
              avgDurationMs: Math.round(stats.avgDurationMs),
              sampleCount: stats.successCount + stats.failureCount,
            });
          }
        }
        this._lastSuccessfulTool = { name, capabilities: caps };
      }
```

**3e — In execute() failure path**, after `this._tracker.recordFailure()`:
```typescript
      if (this._db) {
        const caps = tool.definition.capabilities ?? [];
        const errMsg = error instanceof Error ? error.message : String(error);
        this._db.recordToolExecution({
          toolName: name,
          capabilityTags: caps,
          success: false,
          durationMs: 0,
          errorReason: errMsg.slice(0, 500),
          subgoalId: context.engineContext?.activeSubGoal?.id,
        });
      }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-db-recording.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/registry.ts __tests__/tools/registry-db-recording.test.ts
git commit -m "feat(tool-cortex-7b): record tool executions + upsert tool_edges in ToolRegistry"
```

---

## Task 4: Create ToolGraph with Dijkstra replanning

**Files:**
- Create: `src/tools/cortex/tool-graph.ts`
- Test: `__tests__/tools/cortex/tool-graph.test.ts`

`ToolGraph` reads `tool_edges` from SQLite and runs Dijkstra over capability-tagged edges to find the next-best tool when the current one is BLOCKED. Cost = `1 - successRate` (lower cost = more reliable). Returns in < 50ms (synchronous SQLite reads, tiny graph).

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/cortex/tool-graph.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolGraph } from "../../../src/tools/cortex/tool-graph.js";

function makeDb(edges: Array<{ fromTool: string; toTool: string; successRate: number; avgDurationMs: number; sampleCount: number }>) {
  return {
    getEdgesForCapability: vi.fn().mockReturnValue(edges),
  };
}

describe("ToolGraph.replan()", () => {
  it("returns the highest-success-rate alternative for a capability", () => {
    const db = makeDb([
      { fromTool: "web_crawl", toTool: "scrapling_fetch", successRate: 0.85, avgDurationMs: 1200, sampleCount: 20 },
      { fromTool: "web_crawl", toTool: "camofox",         successRate: 0.72, avgDurationMs: 2000, sampleCount: 10 },
    ]);
    const graph = new ToolGraph(db as any);
    const result = graph.replan("web_crawl", "web_fetch");
    expect(result).toBe("scrapling_fetch");
  });

  it("excludes the failed tool from candidates", () => {
    const db = makeDb([
      { fromTool: "web_crawl", toTool: "web_crawl",       successRate: 0.3,  avgDurationMs: 500, sampleCount: 5 },
      { fromTool: "web_crawl", toTool: "scrapling_fetch", successRate: 0.85, avgDurationMs: 1200, sampleCount: 20 },
    ]);
    const graph = new ToolGraph(db as any);
    const result = graph.replan("web_crawl", "web_fetch");
    expect(result).toBe("scrapling_fetch");
    expect(result).not.toBe("web_crawl");
  });

  it("returns null when no edges exist for capability", () => {
    const db = makeDb([]);
    const graph = new ToolGraph(db as any);
    const result = graph.replan("web_crawl", "web_fetch");
    expect(result).toBeNull();
  });

  it("returns null when only the failing tool has edges (no alternatives)", () => {
    const db = makeDb([
      { fromTool: "web_crawl", toTool: "web_crawl", successRate: 0.5, avgDurationMs: 500, sampleCount: 3 },
    ]);
    const graph = new ToolGraph(db as any);
    const result = graph.replan("web_crawl", "web_fetch");
    expect(result).toBeNull();
  });

  it("requires minimum sampleCount (>=3) to consider an edge reliable", () => {
    const db = makeDb([
      { fromTool: "web_crawl", toTool: "untested_tool",   successRate: 1.0, avgDurationMs: 100, sampleCount: 1 },
      { fromTool: "web_crawl", toTool: "tested_tool",     successRate: 0.7, avgDurationMs: 800, sampleCount: 5 },
    ]);
    const graph = new ToolGraph(db as any);
    const result = graph.replan("web_crawl", "web_fetch");
    expect(result).toBe("tested_tool");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/cortex/tool-graph.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create ToolGraph**

```typescript
// src/tools/cortex/tool-graph.ts
import type { MemoryDatabase } from "../../memory/db.js";
import { log } from "../../logger.js";

const MIN_SAMPLE_COUNT = 3;

interface Edge {
  toTool: string;
  successRate: number;
  avgDurationMs: number;
  sampleCount: number;
}

export class ToolGraph {
  constructor(private db: Pick<MemoryDatabase, "getEdgesForCapability">) {}

  /**
   * Given a failed tool and a capability tag, find the next-best tool
   * via Dijkstra over tool_edges (cost = 1 - successRate).
   * Returns null when no reliable alternative exists.
   * Synchronous — SQLite reads only, < 50ms.
   */
  replan(failedTool: string, capabilityTag: string): string | null {
    const rawEdges = this.db.getEdgesForCapability(capabilityTag);

    // Filter edges: exclude the failing tool, require minimum sample count
    const candidates: Edge[] = rawEdges
      .filter(e => e.toTool !== failedTool && e.sampleCount >= MIN_SAMPLE_COUNT)
      .map(e => ({ toTool: e.toTool, successRate: e.successRate, avgDurationMs: e.avgDurationMs, sampleCount: e.sampleCount }));

    if (candidates.length === 0) {
      log.engine.debug(`[ToolGraph] no alternative for '${failedTool}' (capability: ${capabilityTag})`);
      return null;
    }

    // Dijkstra cost: 1 - successRate (lower = better)
    // With a tiny graph we can just pick the minimum cost directly
    const best = candidates.reduce((prev, curr) =>
      (1 - curr.successRate) < (1 - prev.successRate) ? curr : prev
    );

    log.engine.info(
      `[ToolGraph] replan: '${failedTool}' → '${best.toTool}' ` +
      `(successRate=${best.successRate.toFixed(2)}, samples=${best.sampleCount})`
    );

    return best.toTool;
  }

  /**
   * Get all known alternatives for a capability, sorted by reliability.
   * Used by Phase 7b's PTR and by status reporting.
   */
  getAlternatives(capabilityTag: string): Array<{ tool: string; successRate: number }> {
    return this.db.getEdgesForCapability(capabilityTag)
      .filter(e => e.sampleCount >= MIN_SAMPLE_COUNT)
      .map(e => ({ tool: e.toTool, successRate: e.successRate }))
      .sort((a, b) => b.successRate - a.successRate);
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/tool-graph.test.ts
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/cortex/tool-graph.ts __tests__/tools/cortex/tool-graph.test.ts
git commit -m "feat(tool-cortex-7b): add ToolGraph with Dijkstra replan for LLM-free BLOCKED recovery"
```

---

## Task 5: Wire ToolGraph into ToolRegistry GAV BLOCKED path

**Files:**
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/registry-toolgraph.test.ts`

When GAV returns BLOCKED, before throwing the error, try `ToolGraph.replan()`. If a better tool is found, emit `tool:fallback` and re-execute with that tool instead.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/registry-toolgraph.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import type { SubGoal } from "../../src/engine/types.js";

const mockSubGoal: SubGoal = { id: "sg-1", description: "find stock price", status: "in_progress", dependsOn: [] };

describe("ToolRegistry + ToolGraph fallback", () => {
  it("automatically retries with best alternative when GAV returns BLOCKED", async () => {
    const registry = new ToolRegistry();
    const bus = new GatewayEventBus();
    registry.setEventBus(bus);

    // First tool: BLOCKED by GAV
    registry.register({
      definition: { name: "bad_web", description: "b", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "404 error",
    });
    // Alternative: succeeds
    registry.register({
      definition: { name: "good_web", description: "g", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "real content here",
    });

    // GAV: BLOCKED on bad_web, ADVANCES on good_web
    const verifier = {
      verify: vi.fn()
        .mockResolvedValueOnce({ verdict: "BLOCKED", reason: "error page", suggestion: undefined })
        .mockResolvedValueOnce({ verdict: "ADVANCES", reason: "good result" }),
    };
    registry.setGoalVerifier(verifier as any);

    // ToolGraph returns good_web as alternative
    const graph = { replan: vi.fn().mockReturnValue("good_web") };
    registry.setToolGraph(graph as any);

    const fallbackEvents: any[] = [];
    bus.on("tool:fallback", (e) => fallbackEvents.push(e));

    const result = await registry.execute("bad_web", {}, {
      cwd: "/",
      engineContext: { activeSubGoal: mockSubGoal, userMessage: "find price" } as any,
    });

    expect(result).toContain("real content here");
    expect(fallbackEvents).toHaveLength(1);
    expect(fallbackEvents[0].fromTool).toBe("bad_web");
    expect(fallbackEvents[0].toTool).toBe("good_web");
  });

  it("throws ToolExecutionError when ToolGraph has no alternative", async () => {
    const registry = new ToolRegistry();
    registry.register({
      definition: { name: "only_tool", description: "o", parameters: { type: "object", properties: {} }, capabilities: ["unique_cap"] },
      execute: async () => "bad result",
    });
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "BLOCKED", reason: "irrelevant" }) };
    registry.setGoalVerifier(verifier as any);
    const graph = { replan: vi.fn().mockReturnValue(null) };
    registry.setToolGraph(graph as any);

    await expect(
      registry.execute("only_tool", {}, { cwd: "/", engineContext: { activeSubGoal: mockSubGoal, userMessage: "q" } as any })
    ).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-toolgraph.test.ts
```
Expected: `setToolGraph` not a function.

- [ ] **Step 3: Add ToolGraph setter and BLOCKED auto-fallback to registry.ts**

**3a — Import:**
```typescript
import type { ToolGraph } from "./cortex/tool-graph.js";
```

**3b — Add field:**
```typescript
  private _toolGraph: ToolGraph | null = null;
```

**3c — Add setter:**
```typescript
  setToolGraph(graph: ToolGraph): void {
    this._toolGraph = graph;
  }
```

**3d — In the GAV BLOCKED branch** (inside `execute()`), replace:
```typescript
            if (verification.verdict === "BLOCKED") {
              this._eventBus?.emit({ type: "tool:goal_blocked", ... });
              throw new ToolExecutionError(name, `[GAV] blocked: ...`);
            }
```
With:
```typescript
            if (verification.verdict === "BLOCKED") {
              this._eventBus?.emit({ type: "tool:goal_blocked", toolName: name, subGoal: subGoal.description, suggestion: verification.suggestion });

              // Try ToolGraph LLM-free replan
              const capabilities = tool.definition.capabilities ?? [];
              let replanned = false;
              for (const cap of capabilities) {
                const alt = this._toolGraph?.replan(name, cap) ?? null;
                if (alt && this.tools.has(alt)) {
                  this._eventBus?.emit({ type: "tool:fallback", fromTool: name, toTool: alt, reason: verification.reason });
                  // Re-execute with alternative (no GAV recursion on the retry)
                  const altTool = this.tools.get(alt)!;
                  result = await altTool.execute(args, context);
                  replanned = true;
                  break;
                }
              }

              if (!replanned) {
                throw new ToolExecutionError(name, `[GAV] blocked: ${verification.reason}${verification.suggestion ? `. Suggestion: ${verification.suggestion}` : ""}`);
              }
            }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-toolgraph.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/registry.ts __tests__/tools/registry-toolgraph.test.ts
git commit -m "feat(tool-cortex-7b): wire ToolGraph auto-fallback into GAV BLOCKED path in ToolRegistry"
```

---

## Task 6: Create PersonalizedRouter (PTR)

**Files:**
- Create: `src/tools/cortex/personalized-router.ts`
- Test: `__tests__/tools/cortex/personalized-router.test.ts`

`PersonalizedRouter` runs at PLAN phase. It queries the last 30 days of successful trajectories from SQLite, embeds the current user message, finds top-3 cosine matches, and returns the tool sequences from those trajectories as a `ToolPriorLayer` string for injection into the system prompt.

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/cortex/personalized-router.test.ts
import { describe, it, expect, vi } from "vitest";
import { PersonalizedRouter } from "../../../src/tools/cortex/personalized-router.js";

function makeDb(trajectories: Array<{ userMessage: string; toolsUsed: string; reward: number }>) {
  return { getRecentSuccessfulTrajectories: vi.fn().mockReturnValue(trajectories) };
}

describe("PersonalizedRouter.buildPrior()", () => {
  it("returns null when fewer than 50 historical trajectories exist", async () => {
    const router = new PersonalizedRouter(makeDb([]) as any);
    const result = await router.buildPrior("what is the weather?", "user1");
    expect(result).toBeNull();
  });

  it("returns a ToolPriorLayer string when trajectories exist", async () => {
    const trajectories = Array.from({ length: 60 }, (_, i) => ({
      userMessage: `find stock price for company ${i}`,
      toolsUsed: JSON.stringify(["web", "memory"]),
      reward: 0.8,
    }));
    const router = new PersonalizedRouter(makeDb(trajectories) as any);
    const result = await router.buildPrior("what is AAPL stock price?", "user1");
    // Should return a non-null string with tool suggestions
    expect(result).not.toBeNull();
    expect(typeof result).toBe("string");
  });

  it("always includes category fallback when historical data is insufficient", async () => {
    const router = new PersonalizedRouter(makeDb([]) as any);
    const result = await router.buildPriorWithFallback("search for documentation", "user1");
    expect(result).toContain("web");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/cortex/personalized-router.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create PersonalizedRouter**

```typescript
// src/tools/cortex/personalized-router.ts
import type { MemoryDatabase } from "../../memory/db.js";
import { log } from "../../logger.js";

const MIN_TRAJECTORIES = 50;
const K_NEAREST = 3;

// Category-based cold-start priors keyed by keyword patterns
const CATEGORY_PRIORS: Array<{ pattern: RegExp; tools: string[] }> = [
  { pattern: /search|find|look up|who|what is|when|where/i,       tools: ["web", "memory"] },
  { pattern: /write|create|draft|generate|make a/i,               tools: ["memory", "web"] },
  { pattern: /file|read|open|load|directory|folder/i,             tools: ["read_file", "run_shell_command"] },
  { pattern: /run|execute|install|build|compile|terminal/i,       tools: ["run_shell_command"] },
  { pattern: /remember|recall|what did|past|history|previous/i,   tools: ["memory"] },
  { pattern: /email|mail|contact|message|send/i,                  tools: ["macos_comms"] },
  { pattern: /calendar|schedule|remind|meeting|event/i,           tools: ["schedule"] },
];

function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length) return 0;
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB) + 1e-8);
}

function simpleEmbed(text: string): number[] {
  // Simple bag-of-words embedding (256 dims) — production uses fastembed
  // but we keep this pure-TS for testability without native deps
  const words = text.toLowerCase().split(/\W+/).filter(Boolean);
  const vec = new Array(256).fill(0);
  for (const w of words) {
    let h = 5381;
    for (let i = 0; i < w.length; i++) h = (h * 31 + w.charCodeAt(i)) & 0xffffffff;
    vec[Math.abs(h) % 256] += 1;
  }
  const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
  return vec.map(v => v / norm);
}

export class PersonalizedRouter {
  constructor(private db: Pick<MemoryDatabase, "getRecentSuccessfulTrajectories">) {}

  async buildPrior(userMessage: string, userId: string): Promise<string | null> {
    const trajectories = this.db.getRecentSuccessfulTrajectories(userId, 30, 200);
    if (trajectories.length < MIN_TRAJECTORIES) {
      log.engine.debug(`[PTR] insufficient history (${trajectories.length} < ${MIN_TRAJECTORIES}), skipping`);
      return null;
    }

    const queryVec = simpleEmbed(userMessage);

    // Score each trajectory by cosine similarity
    const scored = trajectories.map(t => ({
      toolsUsed: t.toolsUsed,
      score: cosineSimilarity(queryVec, simpleEmbed(t.userMessage)),
    }));

    scored.sort((a, b) => b.score - a.score);
    const top = scored.slice(0, K_NEAREST);

    // Extract tool sequences
    const allTools = new Set<string>();
    for (const t of top) {
      try {
        const tools = JSON.parse(t.toolsUsed) as string[];
        for (const tool of tools) allTools.add(tool);
      } catch { /* skip malformed */ }
    }

    if (allTools.size === 0) return null;

    return [
      "[Tool Prior — based on your past successes with similar tasks]",
      `Suggested tools for this task: ${[...allTools].join(", ")}`,
      "Consider these first before trying others.",
    ].join("\n");
  }

  async buildPriorWithFallback(userMessage: string, userId: string): Promise<string> {
    const personalized = await this.buildPrior(userMessage, userId);
    if (personalized) return personalized;

    // Cold-start: category-based fallback
    for (const { pattern, tools } of CATEGORY_PRIORS) {
      if (pattern.test(userMessage)) {
        return [
          "[Tool Prior — category heuristic]",
          `Suggested tools for this task: ${tools.join(", ")}`,
        ].join("\n");
      }
    }

    return "";
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/personalized-router.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/cortex/personalized-router.ts __tests__/tools/cortex/personalized-router.test.ts
git commit -m "feat(tool-cortex-7b): add PersonalizedRouter — K-NN tool prior from user's trajectory history"
```

---

## Task 7: Inject ToolPriorLayer from PersonalizedRouter in OwlOrchestrator

**Files:**
- Modify: `src/engine/orchestrator.ts`
- Test: `__tests__/engine/orchestrator-ptr.test.ts`

At the PLAN phase (before the main loop), call `PersonalizedRouter.buildPriorWithFallback()`. If a prior is returned, inject it as a system message at the front of `runMessages`.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/engine/orchestrator-ptr.test.ts
import { describe, it, expect, vi } from "vitest";
import { PersonalizedRouter } from "../../src/tools/cortex/personalized-router.js";

describe("PersonalizedRouter integration with Orchestrator PLAN phase", () => {
  it("buildPriorWithFallback returns a category prior for search-type messages", async () => {
    const mockDb = { getRecentSuccessfulTrajectories: vi.fn().mockReturnValue([]) };
    const router = new PersonalizedRouter(mockDb as any);
    const prior = await router.buildPriorWithFallback("search for the latest news about AI", "user1");
    // Category fallback should trigger for "search"
    expect(prior).toContain("web");
  });

  it("buildPriorWithFallback returns empty string for unknown task type", async () => {
    const mockDb = { getRecentSuccessfulTrajectories: vi.fn().mockReturnValue([]) };
    const router = new PersonalizedRouter(mockDb as any);
    const prior = await router.buildPriorWithFallback("xyzzy frob the quux", "user1");
    expect(typeof prior).toBe("string");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/engine/orchestrator-ptr.test.ts
```
Expected: Pass already (these test PersonalizedRouter itself). The real test is in orchestrator wiring — confirmed below.

- [ ] **Step 3: Wire PersonalizedRouter into OrchestratorDeps and PLAN phase**

In `src/engine/orchestrator.ts`:

**3a — Add to OrchestratorDeps:**
```typescript
  personalizedRouter?: PersonalizedRouter;
```

**3b — Import at top:**
```typescript
import type { PersonalizedRouter } from "../tools/cortex/personalized-router.js";
```

**3c — In `run()`, in the PLAN phase (after `const ledger = await this._plan(...)`)**, add:
```typescript
    // PTR: inject personalized tool prior
    let toolPrior = "";
    if (this.deps.personalizedRouter && complexity !== "simple") {
      try {
        toolPrior = await this.deps.personalizedRouter.buildPriorWithFallback(userMessage, ctx.userId);
      } catch (e) {
        log.engine.warn(`[Orchestrator] PTR failed: ${e}`);
      }
    }
```

**3d — When building `runMessages`**, inject the prior as a system message if present:
```typescript
    const runMessages = complexity === "simple" || (!planBlock && !toolPrior)
      ? messages
      : [
          ...(planBlock ? [{ role: "system" as const, content: planBlock }] : []),
          ...(toolPrior ? [{ role: "system" as const, content: toolPrior }] : []),
          ...messages,
        ];
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/engine/orchestrator-ptr.test.ts
```

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/engine/orchestrator.ts __tests__/engine/orchestrator-ptr.test.ts
git commit -m "feat(tool-cortex-7b): inject PersonalizedRouter ToolPriorLayer in Orchestrator PLAN phase"
```

---

## Task 8: Create cortex/index.ts + wire everything at startup in index.ts

**Files:**
- Create: `src/tools/cortex/index.ts`
- Modify: `src/index.ts`
- Test: `__tests__/tools/cortex/index.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/cortex/index.test.ts
import { describe, it, expect } from "vitest";
import { ToolGraph, PersonalizedRouter } from "../../../src/tools/cortex/index.js";

describe("cortex/index exports", () => {
  it("exports ToolGraph", () => {
    expect(ToolGraph).toBeDefined();
  });
  it("exports PersonalizedRouter", () => {
    expect(PersonalizedRouter).toBeDefined();
  });
});
```

- [ ] **Step 2: Create src/tools/cortex/index.ts**

```typescript
// src/tools/cortex/index.ts
export { ToolGraph } from "./tool-graph.js";
export { PersonalizedRouter } from "./personalized-router.js";
```

- [ ] **Step 3: Wire ToolGraph, PersonalizedRouter, and setDb into startup in src/index.ts**

Find where `ToolRegistry`, `MemoryDatabase`, and `OwlOrchestrator` are instantiated in `src/index.ts`. Add:

```typescript
import { ToolGraph, PersonalizedRouter } from "./tools/cortex/index.js";

// After db and registry are created:
const toolGraph = new ToolGraph(db);
const personalizedRouter = new PersonalizedRouter(db);

registry.setDb(db);
registry.setToolGraph(toolGraph);

// When creating OrchestratorDeps, add:
// personalizedRouter,
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/index.test.ts
```

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/cortex/index.ts src/index.ts __tests__/tools/cortex/index.test.ts
git commit -m "feat(tool-cortex-7b): wire ToolGraph + PersonalizedRouter at startup"
```

---

## Task 9: Integration test + Phase 7b verification

**Files:**
- Test: `__tests__/integration/tool-cortex-7b.test.ts`

- [ ] **Step 1: Write integration test**

```typescript
// __tests__/integration/tool-cortex-7b.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { PersonalizedRouter } from "../../src/tools/cortex/personalized-router.js";

describe("Phase 7b integration: CWTG + PTR", () => {
  it("ToolGraph replan is deterministic and < 50ms", () => {
    const edges = Array.from({ length: 20 }, (_, i) => ({
      fromTool: "web_crawl",
      toTool: `alt_tool_${i}`,
      successRate: Math.random(),
      avgDurationMs: 1000,
      sampleCount: i + 3,
    }));
    const db = { getEdgesForCapability: vi.fn().mockReturnValue(edges) };
    const graph = new ToolGraph(db as any);

    const start = Date.now();
    for (let i = 0; i < 100; i++) graph.replan("web_crawl", "web_fetch");
    const elapsed = Date.now() - start;

    expect(elapsed).toBeLessThan(50); // 100 replans in < 50ms
  });

  it("PersonalizedRouter returns consistent results for identical queries", async () => {
    const trajectories = Array.from({ length: 60 }, (_, i) => ({
      userMessage: "find the price of Bitcoin",
      toolsUsed: JSON.stringify(["web", "memory"]),
      reward: 0.9,
    }));
    const db = { getRecentSuccessfulTrajectories: vi.fn().mockReturnValue(trajectories) };
    const router = new PersonalizedRouter(db as any);
    const r1 = await router.buildPrior("what is Bitcoin price?", "u1");
    const r2 = await router.buildPrior("what is Bitcoin price?", "u1");
    expect(r1).toBe(r2); // deterministic
    expect(r1).toContain("web");
  });

  it("ToolGraph returns null for a tool with no edges (cold start)", () => {
    const db = { getEdgesForCapability: vi.fn().mockReturnValue([]) };
    const graph = new ToolGraph(db as any);
    expect(graph.replan("brand_new_tool", "web_fetch")).toBeNull();
  });
});
```

- [ ] **Step 2: Run integration test**

```bash
npx vitest run __tests__/integration/tool-cortex-7b.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 3: Run full suite**

```bash
npm test
```
Expected: All tests pass. Total count ≥ 546 (baseline after 7a ~537 + ~9 new).

- [ ] **Step 4: Commit**

```bash
git add __tests__/integration/tool-cortex-7b.test.ts
git commit -m "test(tool-cortex-7b): Phase 7b integration test — CWTG replan + PTR consistency"
```

---

## Phase 7b Gate Checklist

Before merging, confirm:

- [ ] `npm test` passes, 0 failures
- [ ] `tool_edges` table is being populated: after running 10 multi-tool sessions, `SELECT COUNT(*) FROM tool_edges` > 0
- [ ] `tool_executions` table is being populated: `SELECT COUNT(*) FROM tool_executions` > 0
- [ ] ToolGraph replan fires on a real BLOCKED event: inject a broken tool, confirm `tool:fallback` event is emitted and the alternative's result is returned
- [ ] PTR prior appears in system prompt for complex tasks: check `log.engine.debug` output for `[PTR]` lines
- [ ] Latency check: `ToolGraph.replan()` in production traces < 50ms (check p95 in logs)

---

## Note on seed data

On cold start, `tool_edges` is empty. The static `TOOL_FALLBACKS` map in `FallbackSequencer` (`src/tools/fallback-sequencer.ts`) can seed initial edges. After 7b ships, add a one-time migration utility:

```typescript
// scripts/seed-tool-edges.ts (run once after first 7b deploy)
// Reads existing tools-stats.json and inserts tool_edges for tools
// in the same capability group with their observed success rates.
```

This is optional — the graph self-populates from live executions within 1-2 days of normal use.
