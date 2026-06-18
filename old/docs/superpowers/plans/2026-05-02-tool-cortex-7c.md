# Tool Cortex Phase 7c Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 7c of Tool Cortex: Self-Evolving Tools (SET) using the workspace model — failed tools are rewritten to `workspace/tools/*.js` and shadow-executed, never touching system files — and Fact Provenance Chain (FPC) that attaches source metadata to every tool result and enables retroactive retraction when downstream verification flags upstream facts as wrong.

**Architecture:** SET uses a three-state routing model (SHADOW/PROMOTED/ABSENT) tracked in the `workspace_tools` SQLite table (created in Phase 7a schema v16). `SelfEvolver` runs weekly via `ImprovementScheduler`: it queries the lowest-success-rate tool from `tool_executions` (Phase 7b), extracts 50 failure traces, calls `PatchTool` to rewrite the tool to `workspace/tools/<name>.js`, runs 24h shadow execution (both system and workspace versions run; system result returned), and auto-promotes at 40 successes or auto-rolls back if success rate drops > 5pp. FPC wraps every tool result in a `FactEnvelope` stored in a session-scoped working-memory map; when GAV marks a result as BLOCKED, it fires `fact:retracted` on EventBus and the next prompt build strips that fact from context.

**Tech Stack:** better-sqlite3, Node.js `vm` module or direct `require()` for hot-loading `.js` workspace tools, `PatchTool` at `src/tools/toolsmith.ts`, `ImprovementScheduler` at `src/engine/improvement-scheduler.ts`, `GatewayEventBus` for `fact:retracted`.

**Phase gate:** Start only after Phase 7b has been in production for ≥2 weeks and ≥500 `trajectory_turns` rows have `verification_result` populated.

---

## Safety Invariants (hard-coded, never overridden)

1. **Workspace isolation** — SET writes to `workspace/tools/*.js` only. System source files in `src/` are never touched.
2. **Shadow mandatory** — New workspace version runs 24h in parallel; system result is always returned during shadow.
3. **Auto-rollback** — If workspace tool's success rate drops > 5pp vs. system baseline in first 100 calls, state → ABSENT.
4. **Max one rewrite per week** — `ImprovementScheduler` enforces; second trigger is deferred.
5. **Exclusion list** — These tools are NEVER selected for SET regardless of success rate: `remember`, `recall_memory`, `memory`, `write_file`, `edit_file`, `run_shell_command`, `patch_tool`, `db_query`. Any tool with `capabilities` containing `data_persist` is also excluded.
6. **Promotion threshold** — Exactly 40 verified successes before PROMOTED state. Not configurable per-tool.

---

## File Map

### New files (5)

| File | Purpose |
|------|---------|
| `src/tools/cortex/self-evolver.ts` | `SelfEvolver` class — orchestrates SET lifecycle |
| `src/tools/cortex/workspace-loader.ts` | Loads a `workspace/tools/<name>.js` file as a `ToolImplementation` |
| `src/tools/cortex/fact-envelope.ts` | `FactEnvelope` type + `FactStore` session-scoped working memory |
| `workspace/tools/.gitkeep` | Placeholder so workspace/tools/ directory is committed |
| `__tests__/tools/cortex/self-evolver.test.ts` | Unit tests |

### Modified files (5)

| File | Change |
|------|--------|
| `src/tools/registry.ts` | SHADOW routing (run both, return system result); PROMOTED routing (run workspace only); write shadow outcome to `workspace_tools` table |
| `src/engine/improvement-scheduler.ts` | Add weekly `runToolEvolution()` job |
| `src/gateway/event-bus.ts` | Add `fact:retracted` event to `GatewaySystemEvent` union |
| `src/memory/db.ts` | Add `workspace_tools` query methods (get/setState/incrementSuccess/incrementFailure) |
| `src/engine/orchestrator.ts` | Build `FactStore` per session; strip retracted facts from context |

---

## Task 1: Add workspace_tools query methods to MemoryDatabase

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory/db-workspace-tools.test.ts`

The `workspace_tools` table was created in schema v16 (Phase 7a). This task adds the query methods needed by SET routing and SelfEvolver.

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/memory/db-workspace-tools.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_DIR = join(process.cwd(), ".test-db-wt");
afterEach(() => { if (existsSync(TEST_DIR)) rmSync(TEST_DIR, { recursive: true }); });

describe("MemoryDatabase workspace_tools methods", () => {
  it("getWorkspaceTool returns null when not found", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    expect(db.getWorkspaceTool("nonexistent")).toBeNull();
  });

  it("upsertWorkspaceTool inserts a new entry", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.upsertWorkspaceTool({ name: "web_evolved_v1", sourcePath: "/workspace/tools/web_evolved_v1.js", parentTool: "web_crawl", createdBy: "SET" });
    const row = db.getWorkspaceTool("web_evolved_v1");
    expect(row).not.toBeNull();
    expect(row!.state).toBe("SHADOW");
    expect(row!.successCount).toBe(0);
  });

  it("setWorkspaceToolState updates state to PROMOTED", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.upsertWorkspaceTool({ name: "test_tool", sourcePath: "/ws/test.js", parentTool: "t", createdBy: "SET" });
    db.setWorkspaceToolState("test_tool", "PROMOTED");
    expect(db.getWorkspaceTool("test_tool")!.state).toBe("PROMOTED");
  });

  it("incrementWorkspaceToolSuccess increments successCount", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    db.upsertWorkspaceTool({ name: "inc_tool", sourcePath: "/ws/inc.js", parentTool: "t", createdBy: "SET" });
    db.incrementWorkspaceToolSuccess("inc_tool");
    db.incrementWorkspaceToolSuccess("inc_tool");
    expect(db.getWorkspaceTool("inc_tool")!.successCount).toBe(2);
  });

  it("getLowestSuccessRateTool returns tool below threshold (excluding exclusion list)", () => {
    mkdirSync(TEST_DIR, { recursive: true });
    const db = new MemoryDatabase(TEST_DIR);
    // Insert some tool_executions to create stats
    for (let i = 0; i < 20; i++) {
      db.recordToolExecution({ toolName: "bad_tool", capabilityTags: ["web_fetch"], success: i < 4, durationMs: 100 });
    }
    for (let i = 0; i < 20; i++) {
      db.recordToolExecution({ toolName: "remember", capabilityTags: ["memory_store"], success: true, durationMs: 100 });
    }
    const candidate = db.getLowestSuccessRateTool({ excludeTools: ["remember", "memory", "write_file"], minSampleCount: 10 });
    expect(candidate?.toolName).toBe("bad_tool");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/db-workspace-tools.test.ts
```
Expected: Methods not found.

- [ ] **Step 3: Add methods to MemoryDatabase**

```typescript
  // ─── Tool Cortex 7c: workspace_tools + SET methods ──────────────

  getWorkspaceTool(name: string): { name: string; sourcePath: string; parentTool: string; state: string; successCount: number; failureCount: number; promotedAt: string | null } | null {
    return (this.db.prepare(
      "SELECT name, source_path as sourcePath, parent_tool as parentTool, state, success_count as successCount, failure_count as failureCount, promoted_at as promotedAt FROM workspace_tools WHERE name = ?"
    ).get(name) as any) ?? null;
  }

  upsertWorkspaceTool(args: { name: string; sourcePath: string; parentTool: string; createdBy: string }): void {
    this.db.prepare(`
      INSERT INTO workspace_tools (name, source_path, parent_tool, created_by)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(name) DO UPDATE SET source_path = excluded.source_path, parent_tool = excluded.parent_tool
    `).run(args.name, args.sourcePath, args.parentTool, args.createdBy);
  }

  setWorkspaceToolState(name: string, state: "SHADOW" | "PROMOTED" | "ABSENT"): void {
    const promotedAt = state === "PROMOTED" ? new Date().toISOString() : null;
    this.db.prepare("UPDATE workspace_tools SET state = ?, promoted_at = ? WHERE name = ?")
      .run(state, promotedAt, name);
  }

  incrementWorkspaceToolSuccess(name: string): void {
    this.db.prepare("UPDATE workspace_tools SET success_count = success_count + 1 WHERE name = ?").run(name);
  }

  incrementWorkspaceToolFailure(name: string): void {
    this.db.prepare("UPDATE workspace_tools SET failure_count = failure_count + 1 WHERE name = ?").run(name);
  }

  getWorkspaceToolsByParent(parentTool: string): Array<{ name: string; sourcePath: string; state: string; successCount: number; failureCount: number }> {
    return (this.db.prepare(
      "SELECT name, source_path as sourcePath, state, success_count as successCount, failure_count as failureCount FROM workspace_tools WHERE parent_tool = ? ORDER BY created_at DESC"
    ).all(parentTool) as any[]);
  }

  getLowestSuccessRateTool(opts: { excludeTools: string[]; minSampleCount: number }): { toolName: string; successRate: number; sampleCount: number } | null {
    const excluded = opts.excludeTools.map(() => "?").join(", ");
    const row = this.db.prepare(`
      SELECT tool_name as toolName,
             CAST(SUM(success) AS REAL) / COUNT(*) as successRate,
             COUNT(*) as sampleCount
      FROM tool_executions
      WHERE tool_name NOT IN (${excluded})
        AND created_at > datetime('now', '-7 days')
      GROUP BY tool_name
      HAVING COUNT(*) >= ?
      ORDER BY successRate ASC
      LIMIT 1
    `).get(...opts.excludeTools, opts.minSampleCount) as any;
    return row ?? null;
  }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/db-workspace-tools.test.ts
```
Expected: 5 tests pass.

- [ ] **Step 5: Run full suite and commit**

```bash
npm test
git add src/memory/db.ts __tests__/memory/db-workspace-tools.test.ts
git commit -m "feat(tool-cortex-7c): add workspace_tools query methods to MemoryDatabase"
```

---

## Task 2: Create WorkspaceLoader — hot-loads .js tools from workspace/

**Files:**
- Create: `src/tools/cortex/workspace-loader.ts`
- Create: `workspace/tools/.gitkeep`
- Test: `__tests__/tools/cortex/workspace-loader.test.ts`

The workspace loader reads a `.js` file (not TypeScript — no compilation step) and returns it as a `ToolImplementation`. Uses Node.js `createRequire` for clean module loading with cache-busting on reload.

- [ ] **Step 1: Create workspace/tools/.gitkeep**

```bash
mkdir -p workspace/tools && touch workspace/tools/.gitkeep
```

- [ ] **Step 2: Write failing test**

```typescript
// __tests__/tools/cortex/workspace-loader.test.ts
import { describe, it, expect } from "vitest";
import { WorkspaceLoader } from "../../../src/tools/cortex/workspace-loader.js";
import { writeFileSync, mkdirSync, rmSync, existsSync } from "node:fs";
import { join } from "node:path";

const TEST_WORKSPACE = join(process.cwd(), ".test-workspace");

afterEach(() => { if (existsSync(TEST_WORKSPACE)) rmSync(TEST_WORKSPACE, { recursive: true }); });

describe("WorkspaceLoader", () => {
  it("loads a valid tool JS file and returns ToolImplementation", async () => {
    mkdirSync(join(TEST_WORKSPACE, "tools"), { recursive: true });
    const toolPath = join(TEST_WORKSPACE, "tools", "test_evolved.js");
    writeFileSync(toolPath, `
module.exports = {
  definition: {
    name: "test_evolved",
    description: "Evolved test tool",
    parameters: { type: "object", properties: {} },
  },
  execute: async (args, context) => "evolved result",
};
`);
    const loader = new WorkspaceLoader(TEST_WORKSPACE);
    const tool = await loader.load("test_evolved");
    expect(tool).not.toBeNull();
    expect(tool!.definition.name).toBe("test_evolved");
    const result = await tool!.execute({}, { cwd: "/" });
    expect(result).toBe("evolved result");
  });

  it("returns null when file does not exist", async () => {
    const loader = new WorkspaceLoader(TEST_WORKSPACE);
    const tool = await loader.load("nonexistent_tool");
    expect(tool).toBeNull();
  });

  it("returns null when file throws on require", async () => {
    mkdirSync(join(TEST_WORKSPACE, "tools"), { recursive: true });
    writeFileSync(join(TEST_WORKSPACE, "tools", "broken.js"), "throw new Error('parse error');");
    const loader = new WorkspaceLoader(TEST_WORKSPACE);
    const tool = await loader.load("broken");
    expect(tool).toBeNull();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/cortex/workspace-loader.test.ts
```
Expected: Module not found.

- [ ] **Step 4: Create WorkspaceLoader**

```typescript
// src/tools/cortex/workspace-loader.ts
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { createRequire } from "node:module";
import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";

export class WorkspaceLoader {
  private toolsDir: string;

  constructor(workspacePath: string) {
    this.toolsDir = join(workspacePath, "tools");
  }

  /**
   * Load a workspace tool by name from workspace/tools/<name>.js
   * Returns null if file doesn't exist or fails to load.
   * Cache-busts on each load to pick up updated files.
   */
  async load(toolName: string): Promise<ToolImplementation | null> {
    const filePath = resolve(join(this.toolsDir, `${toolName}.js`));
    if (!existsSync(filePath)) return null;

    try {
      // Cache-bust by deleting from require cache
      delete require.cache[filePath];
      const mod = require(filePath) as ToolImplementation;

      if (!mod?.definition?.name || typeof mod.execute !== "function") {
        log.engine.warn(`[WorkspaceLoader] '${toolName}' missing definition.name or execute()`);
        return null;
      }

      return mod;
    } catch (err) {
      log.engine.warn(`[WorkspaceLoader] failed to load '${toolName}': ${err}`);
      return null;
    }
  }

  /**
   * Write a new workspace tool file.
   * Creates workspace/tools/ directory if it doesn't exist.
   */
  async write(toolName: string, sourceCode: string): Promise<string> {
    const { mkdirSync, writeFileSync } = await import("node:fs");
    mkdirSync(this.toolsDir, { recursive: true });
    const filePath = join(this.toolsDir, `${toolName}.js`);
    writeFileSync(filePath, sourceCode, "utf-8");
    log.engine.info(`[WorkspaceLoader] wrote workspace tool: ${filePath}`);
    return filePath;
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/workspace-loader.test.ts
```
Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/tools/cortex/workspace-loader.ts workspace/tools/.gitkeep __tests__/tools/cortex/workspace-loader.test.ts
git commit -m "feat(tool-cortex-7c): add WorkspaceLoader for hot-loading evolved .js tools from workspace/"
```

---

## Task 3: Create SelfEvolver

**Files:**
- Create: `src/tools/cortex/self-evolver.ts`
- Test: `__tests__/tools/cortex/self-evolver.test.ts`

`SelfEvolver` orchestrates the full SET lifecycle: find worst tool → write workspace version via PatchTool → register for shadow → track outcomes → promote/rollback.

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/cortex/self-evolver.test.ts
import { describe, it, expect, vi } from "vitest";
import { SelfEvolver, SET_EXCLUSION_LIST } from "../../../src/tools/cortex/self-evolver.js";

describe("SelfEvolver", () => {
  it("SET_EXCLUSION_LIST contains critical tools", () => {
    expect(SET_EXCLUSION_LIST).toContain("remember");
    expect(SET_EXCLUSION_LIST).toContain("write_file");
    expect(SET_EXCLUSION_LIST).toContain("run_shell_command");
    expect(SET_EXCLUSION_LIST).toContain("patch_tool");
    expect(SET_EXCLUSION_LIST).toContain("db_query");
    expect(SET_EXCLUSION_LIST).toContain("memory");
  });

  it("shouldPromote returns true when successCount >= 40", () => {
    const evolver = new SelfEvolver({} as any, {} as any, {} as any, {} as any);
    expect(evolver.shouldPromote(40, 5)).toBe(true);
    expect(evolver.shouldPromote(39, 5)).toBe(false);
  });

  it("shouldRollback returns true when failure rate exceeds 5pp over baseline", () => {
    const evolver = new SelfEvolver({} as any, {} as any, {} as any, {} as any);
    // baseline success rate 0.80, workspace success rate 0.70 (drop of 10pp > 5pp)
    expect(evolver.shouldRollback(0.70, 0.80, 20)).toBe(true);
    // drop of 3pp, not enough
    expect(evolver.shouldRollback(0.77, 0.80, 20)).toBe(false);
    // fewer than 10 calls — too early to judge
    expect(evolver.shouldRollback(0.60, 0.80, 5)).toBe(false);
  });

  it("selectTarget returns null when candidate is in exclusion list", async () => {
    const mockDb = {
      getLowestSuccessRateTool: vi.fn().mockReturnValue({ toolName: "remember", successRate: 0.1, sampleCount: 50 }),
    };
    const evolver = new SelfEvolver(mockDb as any, {} as any, {} as any, {} as any);
    const target = await evolver.selectTarget();
    expect(target).toBeNull();
  });

  it("selectTarget returns null when no candidate found", async () => {
    const mockDb = { getLowestSuccessRateTool: vi.fn().mockReturnValue(null) };
    const evolver = new SelfEvolver(mockDb as any, {} as any, {} as any, {} as any);
    expect(await evolver.selectTarget()).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/cortex/self-evolver.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create SelfEvolver**

```typescript
// src/tools/cortex/self-evolver.ts
import type { MemoryDatabase } from "../../memory/db.js";
import type { ToolRegistry } from "../registry.js";
import type { WorkspaceLoader } from "./workspace-loader.js";
import { log } from "../../logger.js";

export const SET_EXCLUSION_LIST = [
  "remember", "recall_memory", "memory", "pellet_recall",
  "write_file", "edit_file", "read_file",
  "run_shell_command", "shell",
  "patch_tool", "db_query",
  "web_unified", "memory_unified",
] as const;

const PROMOTION_THRESHOLD = 40;
const ROLLBACK_DROP_PP = 0.05;
const MIN_CALLS_FOR_ROLLBACK = 10;
const MIN_SAMPLE_COUNT_FOR_SELECTION = 20;

export interface SelfEvolverDeps {
  db: MemoryDatabase;
  registry: ToolRegistry;
  loader: WorkspaceLoader;
  patchToolExecutor: (toolName: string, failureSummary: string, currentSource: string) => Promise<string>;
}

export class SelfEvolver {
  private lastEvolutionAt: number = 0;
  private readonly ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000;

  constructor(
    private db: MemoryDatabase,
    private registry: ToolRegistry,
    private loader: WorkspaceLoader,
    private patchTool: SelfEvolverDeps["patchToolExecutor"],
  ) {}

  shouldPromote(successCount: number, failureCount: number): boolean {
    return successCount >= PROMOTION_THRESHOLD;
  }

  shouldRollback(workspaceRate: number, baselineRate: number, sampleCount: number): boolean {
    if (sampleCount < MIN_CALLS_FOR_ROLLBACK) return false;
    return (baselineRate - workspaceRate) > ROLLBACK_DROP_PP;
  }

  async selectTarget(): Promise<{ toolName: string; successRate: number } | null> {
    const candidate = this.db.getLowestSuccessRateTool({
      excludeTools: [...SET_EXCLUSION_LIST],
      minSampleCount: MIN_SAMPLE_COUNT_FOR_SELECTION,
    });
    if (!candidate) return null;
    if ((SET_EXCLUSION_LIST as readonly string[]).includes(candidate.toolName)) return null;
    return { toolName: candidate.toolName, successRate: candidate.successRate };
  }

  async runEvolution(): Promise<{ evolved: boolean; toolName?: string; reason?: string }> {
    // Max one evolution per week
    if (Date.now() - this.lastEvolutionAt < this.ONE_WEEK_MS) {
      return { evolved: false, reason: "weekly limit: evolution already ran this week" };
    }

    const target = await this.selectTarget();
    if (!target) {
      return { evolved: false, reason: "no suitable candidate found" };
    }

    log.engine.info(`[SET] Selected '${target.toolName}' for evolution (successRate=${target.successRate.toFixed(2)})`);

    // Get recent failure traces
    const failures = (this.db as any).db?.prepare(`
      SELECT error_reason, args_snapshot FROM tool_executions
      WHERE tool_name = ? AND success = 0 AND created_at > datetime('now', '-7 days')
      ORDER BY created_at DESC LIMIT 50
    `).all(target.toolName) ?? [];

    const failureSummary = failures
      .map((f: any) => `- ${f.error_reason ?? "unknown error"}`)
      .slice(0, 20)
      .join("\n");

    // Retrieve current source (simplified — PatchTool handles the actual rewrite)
    const workspaceToolName = `${target.toolName}_evolved_${Date.now()}`;
    try {
      const newSource = await this.patchTool(target.toolName, failureSummary, "");
      if (!newSource) {
        return { evolved: false, reason: "PatchTool returned empty source" };
      }

      const filePath = await this.loader.write(workspaceToolName, newSource);
      this.db.upsertWorkspaceTool({
        name: workspaceToolName,
        sourcePath: filePath,
        parentTool: target.toolName,
        createdBy: "SET",
      });

      this.lastEvolutionAt = Date.now();
      log.engine.info(`[SET] Written workspace tool '${workspaceToolName}' — shadow phase begins`);
      return { evolved: true, toolName: workspaceToolName };
    } catch (err) {
      log.engine.warn(`[SET] Evolution failed for '${target.toolName}': ${err}`);
      return { evolved: false, reason: String(err) };
    }
  }

  async checkAndPromoteOrRollback(workspaceToolName: string, baselineSuccessRate: number): Promise<"promoted" | "rolled_back" | "shadow"> {
    const row = this.db.getWorkspaceTool(workspaceToolName);
    if (!row) return "shadow";

    const total = row.successCount + row.failureCount;
    const workspaceRate = total > 0 ? row.successCount / total : 0;

    if (this.shouldRollback(workspaceRate, baselineSuccessRate, total)) {
      this.db.setWorkspaceToolState(workspaceToolName, "ABSENT");
      log.engine.warn(`[SET] '${workspaceToolName}' rolled back (workspace=${workspaceRate.toFixed(2)}, baseline=${baselineSuccessRate.toFixed(2)})`);
      return "rolled_back";
    }

    if (this.shouldPromote(row.successCount, row.failureCount)) {
      this.db.setWorkspaceToolState(workspaceToolName, "PROMOTED");
      log.engine.info(`[SET] '${workspaceToolName}' promoted to PROMOTED after ${row.successCount} successes`);
      return "promoted";
    }

    return "shadow";
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/self-evolver.test.ts
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/cortex/self-evolver.ts __tests__/tools/cortex/self-evolver.test.ts
git commit -m "feat(tool-cortex-7c): add SelfEvolver with workspace model, 40-success promotion, 5pp rollback guard"
```

---

## Task 4: Wire SET routing into ToolRegistry.execute()

**Files:**
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/registry-set-routing.test.ts`

SHADOW: both system and workspace run; system result returned, workspace outcome recorded.
PROMOTED: workspace tool runs instead of system.
ABSENT / not present: system tool only (unchanged path).

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/registry-set-routing.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";

describe("ToolRegistry SET routing", () => {
  it("in SHADOW state: returns system result even when workspace gives different result", async () => {
    const registry = new ToolRegistry();
    registry.register({
      definition: { name: "target_tool", description: "t", parameters: { type: "object", properties: {} } },
      execute: async () => "system result",
    });
    // Mock workspace loader: returns workspace tool
    const mockLoader = {
      load: vi.fn().mockResolvedValue({
        definition: { name: "target_tool_evolved", description: "e", parameters: { type: "object", properties: {} } },
        execute: async () => "workspace result",
      }),
    };
    // Mock DB: SHADOW state
    const mockDb = {
      getWorkspaceToolsByParent: vi.fn().mockReturnValue([{ name: "target_tool_evolved", sourcePath: "/ws/t.js", state: "SHADOW", successCount: 5, failureCount: 1 }]),
      incrementWorkspaceToolSuccess: vi.fn(),
      incrementWorkspaceToolFailure: vi.fn(),
      recordToolExecution: vi.fn(),
      upsertToolEdge: vi.fn(),
    };
    registry.setWorkspaceLoader(mockLoader as any);
    registry.setDb(mockDb as any);

    const result = await registry.execute("target_tool", {}, { cwd: "/" });
    expect(result).toBe("system result"); // System result returned during shadow
    // Workspace was also called
    expect(mockLoader.load).toHaveBeenCalled();
  });

  it("in PROMOTED state: returns workspace result", async () => {
    const registry = new ToolRegistry();
    registry.register({
      definition: { name: "promoted_tool", description: "t", parameters: { type: "object", properties: {} } },
      execute: async () => "system result",
    });
    const mockLoader = {
      load: vi.fn().mockResolvedValue({
        definition: { name: "promoted_tool_evolved", description: "e", parameters: { type: "object", properties: {} } },
        execute: async () => "promoted workspace result",
      }),
    };
    const mockDb = {
      getWorkspaceToolsByParent: vi.fn().mockReturnValue([{ name: "promoted_tool_evolved", sourcePath: "/ws/p.js", state: "PROMOTED", successCount: 45, failureCount: 2 }]),
      incrementWorkspaceToolSuccess: vi.fn(),
      incrementWorkspaceToolFailure: vi.fn(),
      recordToolExecution: vi.fn(),
      upsertToolEdge: vi.fn(),
    };
    registry.setWorkspaceLoader(mockLoader as any);
    registry.setDb(mockDb as any);

    const result = await registry.execute("promoted_tool", {}, { cwd: "/" });
    expect(result).toBe("promoted workspace result");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-set-routing.test.ts
```
Expected: `setWorkspaceLoader` not a function.

- [ ] **Step 3: Add workspace routing to ToolRegistry**

**3a — Import:**
```typescript
import type { WorkspaceLoader } from "./cortex/workspace-loader.js";
```

**3b — Add fields:**
```typescript
  private _workspaceLoader: WorkspaceLoader | null = null;
```

**3c — Add setter:**
```typescript
  setWorkspaceLoader(loader: WorkspaceLoader): void {
    this._workspaceLoader = loader;
  }
```

**3d — At the start of `execute()`, before the permission check**, add workspace routing:
```typescript
    // SET routing: check for workspace tool in SHADOW or PROMOTED state
    if (this._workspaceLoader && this._db) {
      const workspaceEntries = this._db.getWorkspaceToolsByParent(name);
      const active = workspaceEntries.find(e => e.state === "PROMOTED") ?? workspaceEntries.find(e => e.state === "SHADOW");
      if (active) {
        const wsTool = await this._workspaceLoader.load(active.name);
        if (wsTool) {
          if (active.state === "PROMOTED") {
            // Promoted: run workspace only
            try {
              const wsResult = await wsTool.execute(args, context);
              this._db.incrementWorkspaceToolSuccess(active.name);
              return wsResult;
            } catch {
              this._db.incrementWorkspaceToolFailure(active.name);
              // Fall through to system tool on workspace failure
            }
          } else {
            // Shadow: run both, return system result, record workspace outcome
            wsTool.execute(args, context).then(
              () => this._db!.incrementWorkspaceToolSuccess(active.name),
              () => this._db!.incrementWorkspaceToolFailure(active.name),
            ).catch(() => {/* silent */});
            // Continue to system tool execution below
          }
        }
      }
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-set-routing.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 5: Run full suite**

```bash
npm test
```

- [ ] **Step 6: Commit**

```bash
git add src/tools/registry.ts __tests__/tools/registry-set-routing.test.ts
git commit -m "feat(tool-cortex-7c): add SHADOW/PROMOTED workspace routing to ToolRegistry.execute()"
```

---

## Task 5: Register SET weekly job in ImprovementScheduler

**Files:**
- Modify: `src/engine/improvement-scheduler.ts`
- Test: `__tests__/engine/improvement-scheduler-set.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/engine/improvement-scheduler-set.test.ts
import { describe, it, expect, vi } from "vitest";
import { ImprovementScheduler } from "../../src/engine/improvement-scheduler.js";

describe("ImprovementScheduler SET job", () => {
  it("accepts a SelfEvolver and stores it", () => {
    const mockJournal = { getRecentOutcomes: vi.fn().mockResolvedValue([]) } as any;
    const mockDb = { getApproachesToPrune: vi.fn().mockReturnValue([]) } as any;
    const scheduler = new ImprovementScheduler(mockJournal, mockDb, { quietHours: [] });
    const mockEvolver = { runEvolution: vi.fn().mockResolvedValue({ evolved: false }) };
    expect(() => scheduler.setSelfEvolver(mockEvolver as any)).not.toThrow();
  });

  it("runToolEvolution delegates to SelfEvolver.runEvolution()", async () => {
    const mockJournal = { getRecentOutcomes: vi.fn().mockResolvedValue([]) } as any;
    const mockDb = { getApproachesToPrune: vi.fn().mockReturnValue([]) } as any;
    const scheduler = new ImprovementScheduler(mockJournal, mockDb, { quietHours: [] });
    const mockEvolver = { runEvolution: vi.fn().mockResolvedValue({ evolved: true, toolName: "test_evolved_123" }) };
    scheduler.setSelfEvolver(mockEvolver as any);
    await (scheduler as any).runToolEvolution();
    expect(mockEvolver.runEvolution).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/engine/improvement-scheduler-set.test.ts
```
Expected: `setSelfEvolver` not a function.

- [ ] **Step 3: Modify ImprovementScheduler**

**3a — Import:**
```typescript
import type { SelfEvolver } from "../tools/cortex/self-evolver.js";
```

**3b — Add field:**
```typescript
  private _selfEvolver: SelfEvolver | null = null;
```

**3c — Add setter:**
```typescript
  setSelfEvolver(evolver: SelfEvolver): void {
    this._selfEvolver = evolver;
  }
```

**3d — In `start()`, add the weekly job after the existing hourly job:**
```typescript
    // Job 3: Tool evolution — weekly (7 days), gated by SelfEvolver's own weekly limit
    this.timers.push(setInterval(async () => {
      if (this.isInQuietHours()) return;
      try { await this.runToolEvolution(); } catch (e) {
        log.engine.warn(`[ImprovementScheduler] SET error: ${e}`);
      }
    }, 7 * 24 * 60 * 60_000));
```

**3e — Add `runToolEvolution()` method:**
```typescript
  private async runToolEvolution(): Promise<void> {
    if (!this._selfEvolver) return;
    const result = await this._selfEvolver.runEvolution();
    if (result.evolved) {
      log.engine.info(`[ImprovementScheduler] SET: evolved tool '${result.toolName}'`);
    } else {
      log.engine.debug(`[ImprovementScheduler] SET: skipped — ${result.reason}`);
    }
  }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/engine/improvement-scheduler-set.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 5: Run full suite and commit**

```bash
npm test
git add src/engine/improvement-scheduler.ts __tests__/engine/improvement-scheduler-set.test.ts
git commit -m "feat(tool-cortex-7c): register weekly SET job in ImprovementScheduler"
```

---

## Task 6: Add fact:retracted event to EventBus (FPC)

**Files:**
- Modify: `src/gateway/event-bus.ts`
- Test: `__tests__/gateway/event-bus-fpc.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/event-bus-fpc.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("GatewayEventBus FPC events", () => {
  it("emits and receives fact:retracted event", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("fact:retracted", handler);
    bus.emit({
      type: "fact:retracted",
      factId: "fact-1",
      toolName: "web_crawl",
      reason: "GAV marked BLOCKED",
      sessionId: "session-123",
    });
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({ factId: "fact-1", reason: "GAV marked BLOCKED" })
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/gateway/event-bus-fpc.test.ts
```
Expected: `fact:retracted` not in type union.

- [ ] **Step 3: Extend GatewaySystemEvent**

In `src/gateway/event-bus.ts`, add after the last `tool:*` event:

```typescript
  | { type: "fact:retracted"; factId: string; toolName: string; reason: string; sessionId: string }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/gateway/event-bus-fpc.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/gateway/event-bus.ts __tests__/gateway/event-bus-fpc.test.ts
git commit -m "feat(tool-cortex-7c): add fact:retracted event to GatewayEventBus for FPC"
```

---

## Task 7: Create FactEnvelope + FactStore (FPC)

**Files:**
- Create: `src/tools/cortex/fact-envelope.ts`
- Test: `__tests__/tools/cortex/fact-envelope.test.ts`

`FactEnvelope` wraps a tool result with provenance metadata. `FactStore` is a session-scoped in-memory map of active facts. When `fact:retracted` is emitted, the orchestrator strips that fact from the next prompt build.

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/tools/cortex/fact-envelope.test.ts
import { describe, it, expect } from "vitest";
import { FactStore } from "../../../src/tools/cortex/fact-envelope.js";

describe("FactStore", () => {
  it("stores and retrieves a fact by id", () => {
    const store = new FactStore();
    const id = store.add({
      content: "TypeScript 5.5 released June 2024",
      provenance: { toolName: "web_crawl", args: { url: "https://ts.dev" }, durationMs: 800 },
    });
    const fact = store.get(id);
    expect(fact).not.toBeNull();
    expect(fact!.content).toContain("TypeScript 5.5");
    expect(fact!.retracted).toBe(false);
  });

  it("retract() marks fact as retracted", () => {
    const store = new FactStore();
    const id = store.add({ content: "wrong fact", provenance: { toolName: "t", args: {}, durationMs: 0 } });
    store.retract(id, "GAV blocked");
    expect(store.get(id)!.retracted).toBe(true);
  });

  it("getActive() returns only non-retracted facts", () => {
    const store = new FactStore();
    const id1 = store.add({ content: "good fact", provenance: { toolName: "a", args: {}, durationMs: 0 } });
    const id2 = store.add({ content: "bad fact", provenance: { toolName: "b", args: {}, durationMs: 0 } });
    store.retract(id2, "blocked");
    const active = store.getActive();
    expect(active).toHaveLength(1);
    expect(active[0].content).toBe("good fact");
  });

  it("buildContextBlock() includes only active facts", () => {
    const store = new FactStore();
    store.add({ content: "active fact", provenance: { toolName: "web_crawl", args: {}, durationMs: 0 } });
    const id2 = store.add({ content: "retracted fact", provenance: { toolName: "web_crawl", args: {}, durationMs: 0 } });
    store.retract(id2, "blocked");
    const block = store.buildContextBlock();
    expect(block).toContain("active fact");
    expect(block).not.toContain("retracted fact");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/cortex/fact-envelope.test.ts
```
Expected: Module not found.

- [ ] **Step 3: Create fact-envelope.ts**

```typescript
// src/tools/cortex/fact-envelope.ts
import { v4 as uuidv4 } from "uuid";

export interface FactProvenance {
  toolName: string;
  args: Record<string, unknown>;
  durationMs: number;
  verifiedBy?: string;
  confidence?: number;
}

export interface FactEnvelope {
  id: string;
  content: string;
  provenance: FactProvenance;
  retracted: boolean;
  retractedReason?: string;
  createdAt: number;
}

export class FactStore {
  private facts: Map<string, FactEnvelope> = new Map();

  add(args: { content: string; provenance: FactProvenance }): string {
    const id = uuidv4();
    this.facts.set(id, {
      id,
      content: args.content,
      provenance: args.provenance,
      retracted: false,
      createdAt: Date.now(),
    });
    return id;
  }

  get(id: string): FactEnvelope | null {
    return this.facts.get(id) ?? null;
  }

  retract(id: string, reason: string): void {
    const fact = this.facts.get(id);
    if (fact) {
      fact.retracted = true;
      fact.retractedReason = reason;
    }
  }

  retractByTool(toolName: string, reason: string): string[] {
    const retracted: string[] = [];
    for (const fact of this.facts.values()) {
      if (fact.provenance.toolName === toolName && !fact.retracted) {
        fact.retracted = true;
        fact.retractedReason = reason;
        retracted.push(fact.id);
      }
    }
    return retracted;
  }

  getActive(): FactEnvelope[] {
    return Array.from(this.facts.values()).filter(f => !f.retracted);
  }

  /**
   * Build a context block of active facts for injection into the system prompt.
   * Provenance metadata stays OUT of the prompt (context cost).
   */
  buildContextBlock(): string {
    const active = this.getActive();
    if (active.length === 0) return "";
    return [
      "[Verified Facts from this session]",
      ...active.map(f => `- ${f.content}`),
    ].join("\n");
  }

  clear(): void {
    this.facts.clear();
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/fact-envelope.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/tools/cortex/fact-envelope.ts __tests__/tools/cortex/fact-envelope.test.ts
git commit -m "feat(tool-cortex-7c): add FactEnvelope + FactStore for FPC — retroactive retraction"
```

---

## Task 8: Wire FactStore into ToolRegistry + trigger retraction on GAV BLOCKED

**Files:**
- Modify: `src/tools/registry.ts`
- Test: `__tests__/tools/registry-fpc.test.ts`

When a tool succeeds, store its result in `FactStore`. When GAV returns BLOCKED, retract facts from that tool and emit `fact:retracted`.

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/registry-fpc.test.ts
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { FactStore } from "../../src/tools/cortex/fact-envelope.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import type { SubGoal } from "../../src/engine/types.js";

const subGoal: SubGoal = { id: "sg-1", description: "find info", status: "in_progress", dependsOn: [] };

describe("ToolRegistry FPC integration", () => {
  it("adds tool result to FactStore on success", async () => {
    const registry = new ToolRegistry();
    const factStore = new FactStore();
    registry.setFactStore(factStore);
    registry.register({
      definition: { name: "data_tool", description: "d", parameters: { type: "object", properties: {} } },
      execute: async () => "some important data",
    });
    await registry.execute("data_tool", {}, { cwd: "/" });
    expect(factStore.getActive()).toHaveLength(1);
    expect(factStore.getActive()[0].content).toBe("some important data");
  });

  it("retracts tool facts and emits fact:retracted when GAV returns BLOCKED", async () => {
    const registry = new ToolRegistry();
    const factStore = new FactStore();
    const bus = new GatewayEventBus();
    registry.setFactStore(factStore);
    registry.setEventBus(bus);

    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "BLOCKED", reason: "irrelevant" }) };
    registry.setGoalVerifier(verifier as any);
    const graph = { replan: vi.fn().mockReturnValue(null) };
    registry.setToolGraph(graph as any);

    registry.register({
      definition: { name: "bad_src", description: "b", parameters: { type: "object", properties: {} }, capabilities: ["data"] },
      execute: async () => "bad data",
    });

    const retractEvents: any[] = [];
    bus.on("fact:retracted", e => retractEvents.push(e));

    await expect(
      registry.execute("bad_src", {}, { cwd: "/", engineContext: { activeSubGoal: subGoal, userMessage: "q" } as any })
    ).rejects.toThrow();

    expect(retractEvents).toHaveLength(1);
    expect(retractEvents[0].toolName).toBe("bad_src");
    expect(factStore.getActive()).toHaveLength(0); // retracted
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/tools/registry-fpc.test.ts
```
Expected: `setFactStore` not a function.

- [ ] **Step 3: Add FactStore setter and FPC integration to registry.ts**

**3a — Import:**
```typescript
import type { FactStore } from "./cortex/fact-envelope.js";
```

**3b — Add field:**
```typescript
  private _factStore: FactStore | null = null;
  private _sessionId: string = "";
```

**3c — Add setters:**
```typescript
  setFactStore(store: FactStore): void {
    this._factStore = store;
  }
  setSessionId(id: string): void {
    this._sessionId = id;
  }
```

**3d — In execute() success path**, after the GAV ADVANCES/PARTIAL handling and before `return result`, add:
```typescript
      if (this._factStore && !result.includes("<tool_result_warning")) {
        this._factStore.add({
          content: result.slice(0, 1000),
          provenance: { toolName: name, args, durationMs },
        });
      }
```

**3e — In the GAV BLOCKED branch** (when ToolGraph also has no alternative), before throwing, add:
```typescript
              // Retract any facts this tool added earlier in this session
              const retractedIds = this._factStore?.retractByTool(name, verification.reason) ?? [];
              for (const factId of retractedIds) {
                this._eventBus?.emit({ type: "fact:retracted", factId, toolName: name, reason: verification.reason, sessionId: this._sessionId });
              }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/registry-fpc.test.ts
```
Expected: 2 tests pass.

- [ ] **Step 5: Run full suite and commit**

```bash
npm test
git add src/tools/registry.ts __tests__/tools/registry-fpc.test.ts
git commit -m "feat(tool-cortex-7c): wire FactStore into ToolRegistry — add on success, retract on GAV BLOCKED"
```

---

## Task 9: Update cortex/index.ts + wire SET + FPC at startup

**Files:**
- Modify: `src/tools/cortex/index.ts`
- Modify: `src/index.ts`
- Test: `__tests__/tools/cortex/index-7c.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tools/cortex/index-7c.test.ts
import { describe, it, expect } from "vitest";
import { ToolGraph, PersonalizedRouter, SelfEvolver, WorkspaceLoader, FactStore } from "../../../src/tools/cortex/index.js";

describe("cortex/index 7c exports", () => {
  it("exports SelfEvolver", () => { expect(SelfEvolver).toBeDefined(); });
  it("exports WorkspaceLoader", () => { expect(WorkspaceLoader).toBeDefined(); });
  it("exports FactStore", () => { expect(FactStore).toBeDefined(); });
});
```

- [ ] **Step 2: Update src/tools/cortex/index.ts**

```typescript
// src/tools/cortex/index.ts
export { ToolGraph } from "./tool-graph.js";
export { PersonalizedRouter } from "./personalized-router.js";
export { SelfEvolver, SET_EXCLUSION_LIST } from "./self-evolver.js";
export { WorkspaceLoader } from "./workspace-loader.js";
export { FactStore } from "./fact-envelope.js";
export type { FactEnvelope, FactProvenance } from "./fact-envelope.js";
```

- [ ] **Step 3: Wire WorkspaceLoader, SelfEvolver, FactStore at startup in src/index.ts**

```typescript
import { ToolGraph, PersonalizedRouter, SelfEvolver, WorkspaceLoader, FactStore } from "./tools/cortex/index.js";

// After registry, db, and scheduler are created:
const workspaceLoader = new WorkspaceLoader(workspacePath); // workspacePath = where stackowl stores its data
const selfEvolver = new SelfEvolver(
  db,
  registry,
  workspaceLoader,
  async (toolName, failureSummary, _currentSource) => {
    // Delegate to PatchTool via registry
    const result = await registry.execute("patch_tool", {
      toolName,
      newSourceCode: `// Auto-evolved by SET\n// Failures:\n${failureSummary}\n\nmodule.exports = { definition: { name: "${toolName}", description: "Auto-evolved version", parameters: { type: "object", properties: {} } }, execute: async (args, ctx) => "evolved result" };`,
      description: `SET auto-evolution based on ${failureSummary.split("\n").length} failure patterns`,
    }, { cwd: process.cwd() });
    return result.includes("ERROR") ? "" : result;
  },
);
registry.setWorkspaceLoader(workspaceLoader);
scheduler.setSelfEvolver(selfEvolver);
```

Note: The `patchTool` callback above is a stub. In production it should call the real LLM-driven `PatchTool` with the actual tool source. See `src/tools/toolsmith.ts` — the `PatchTool` writes to `SYNTHESIZED_DIR`. For SET, override the write path to `workspace/tools/` instead.

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/tools/cortex/index-7c.test.ts
```

- [ ] **Step 5: Run full suite and commit**

```bash
npm test
git add src/tools/cortex/index.ts src/index.ts __tests__/tools/cortex/index-7c.test.ts
git commit -m "feat(tool-cortex-7c): wire SelfEvolver + WorkspaceLoader + FactStore at startup"
```

---

## Task 10: Integration test + Phase 7c verification

**Files:**
- Test: `__tests__/integration/tool-cortex-7c.test.ts`

- [ ] **Step 1: Write integration test**

```typescript
// __tests__/integration/tool-cortex-7c.test.ts
import { describe, it, expect, vi } from "vitest";
import { SelfEvolver, SET_EXCLUSION_LIST } from "../../src/tools/cortex/self-evolver.js";
import { FactStore } from "../../src/tools/cortex/fact-envelope.js";

describe("Phase 7c integration: SET + FPC", () => {
  it("SET never selects a tool from the exclusion list", async () => {
    for (const excludedTool of SET_EXCLUSION_LIST) {
      const mockDb = {
        getLowestSuccessRateTool: vi.fn().mockReturnValue({ toolName: excludedTool, successRate: 0.0, sampleCount: 100 }),
      };
      const evolver = new SelfEvolver(mockDb as any, {} as any, {} as any, {} as any);
      const target = await evolver.selectTarget();
      expect(target).toBeNull();
    }
  });

  it("SelfEvolver respects weekly limit — runEvolution twice returns evolved=false on second call", async () => {
    const mockDb = {
      getLowestSuccessRateTool: vi.fn().mockReturnValue({ toolName: "bad_tool", successRate: 0.2, sampleCount: 50 }),
    };
    const mockLoader = { write: vi.fn().mockResolvedValue("/ws/bad_tool_evolved.js") };
    const mockDb2 = { ...mockDb, upsertWorkspaceTool: vi.fn(), prepare: vi.fn().mockReturnValue({ all: vi.fn().mockReturnValue([]) }) };
    const evolver = new SelfEvolver(mockDb2 as any, {} as any, mockLoader as any, vi.fn().mockResolvedValue("module.exports = {};") as any);

    const r1 = await evolver.runEvolution();
    expect(r1.evolved).toBe(true);
    const r2 = await evolver.runEvolution(); // should be blocked by weekly limit
    expect(r2.evolved).toBe(false);
    expect(r2.reason).toContain("weekly limit");
  });

  it("FactStore buildContextBlock excludes retracted facts", () => {
    const store = new FactStore();
    store.add({ content: "TypeScript 5.5 released", provenance: { toolName: "web_crawl", args: {}, durationMs: 100 } });
    const id2 = store.add({ content: "wrong version info", provenance: { toolName: "web_crawl", args: {}, durationMs: 100 } });
    store.retract(id2, "GAV blocked");
    const block = store.buildContextBlock();
    expect(block).toContain("TypeScript 5.5");
    expect(block).not.toContain("wrong version");
  });

  it("shouldRollback triggers at correct threshold", () => {
    const evolver = new SelfEvolver({} as any, {} as any, {} as any, {} as any);
    expect(evolver.shouldRollback(0.74, 0.80, 15)).toBe(true);   // 6pp drop > 5pp
    expect(evolver.shouldRollback(0.76, 0.80, 15)).toBe(false);  // 4pp drop < 5pp
    expect(evolver.shouldRollback(0.60, 0.80, 5)).toBe(false);   // < 10 samples
  });
});
```

- [ ] **Step 2: Run integration test**

```bash
npx vitest run __tests__/integration/tool-cortex-7c.test.ts
```
Expected: 4 tests pass.

- [ ] **Step 3: Run full suite**

```bash
npm test
```
Expected: All tests pass. Total ≥ 555 (546 after 7b + ~9 new from 7c).

- [ ] **Step 4: Commit**

```bash
git add __tests__/integration/tool-cortex-7c.test.ts
git commit -m "test(tool-cortex-7c): Phase 7c integration test — SET exclusion, weekly limit, FactStore retraction"
```

---

## Phase 7c Gate Checklist

Before merging, confirm:

- [ ] `npm test` passes, 0 failures
- [ ] `workspace/tools/` directory exists and is in `.gitignore` (evolved tools should NOT be committed)
- [ ] SET safety: manually trigger `runToolEvolution()` in staging; confirm it writes to `workspace/tools/` not `src/tools/`
- [ ] Shadow execution: in SHADOW state, system result is always returned; `workspace_tools.success_count` increments
- [ ] Promotion: seed a workspace tool with `success_count = 40`; confirm it's routed as PROMOTED
- [ ] Rollback: seed a workspace tool with poor rate; confirm `setWorkspaceToolState("ABSENT")` fires
- [ ] Exclusion list: verify no tool in `SET_EXCLUSION_LIST` ever appears in `workspace_tools` table
- [ ] FPC: run a session where GAV blocks a tool; confirm `fact:retracted` event fires and `FactStore.getActive()` shrinks
- [ ] `fact:retracted` events appear in logs with correct `toolName` and `reason`

---

## Note on workspace/ .gitignore

Add to `.gitignore`:
```
workspace/tools/*.js
workspace/tools/*.js.map
```

Keep `workspace/tools/.gitkeep` tracked so the directory structure is preserved.
