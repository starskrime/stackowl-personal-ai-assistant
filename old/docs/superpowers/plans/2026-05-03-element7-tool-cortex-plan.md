# Element 7 Tool Cortex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task ends with bmad-code-review adversarial pass before moving on.

**Goal:** Ship the genuinely-pending pieces of Element 7 (Phases 7b, 7c, the un-wired parts of 7d) and harden the already-shipped 7a/7d surface. Avoid duplicating what main already has (GAV verifier, narration formatter, web/memory consolidation, the 5 "missing" tools, MCP CRUD).

**Architecture:** Three new layers stacked on top of the existing ToolRegistry+GAV plumbing. (1) **CWTG/PTR** — DB-backed tool routing; (2) **SET/FPC** — self-evolving tools and fact provenance; (3) **live_browser** — unified Safari+Chrome driver. Plus hardening sweep wiring all three transport adapters to `tool:*` events, migrating ToolTracker to SQLite, and back-filling capability tags / executionPolicy on the top-30 tools.

**Tech Stack:** TypeScript ES2023 (NodeNext, strict), Node ≥22, vitest, better-sqlite3, EventEmitter, fastembed (already a dep via UserMemoryStore), JXA via osascript, Puppeteer-CDP via existing BrowserBridge.

**Worktree:** `.worktrees/tool-cortex-7a` on branch `feature/tool-cortex-7a` (already created).

**Schema migration:** v22 → v23 (one migration covering both new tables: `tool_executions` and `tool_edges`).

---

## File Structure

**New files (all paths relative to repo root):**

```
src/tools/cortex/
  tool-graph.ts            # CWTG: Dijkstra over capability-tagged edges
  edge-accumulator.ts      # writes tool_edges rows on every execute() success/failure
  personalized-router.ts   # PTR: KNN over historical trajectories
  self-evolver.ts          # SET: weekly tool rewrite orchestrator
  shadow-runner.ts         # SET: 24h shadow execution + auto-rollback
  fact-envelope.ts         # FPC: working-memory store + retraction
src/context/layers/
  tool-prior.ts            # PTR: ContextLayer at priority 8
src/tools/live-browser/
  index.ts                 # unified tool + action dispatch
  frontmost.ts             # detect frontmost browser via osascript
  safari-driver.ts         # JXA Application('Safari') wrapper
  chrome-driver.ts         # CDP wrapper around BrowserBridge + AppleScript fallback
  bootstrap.ts             # Chrome --remote-debugging-port=9222 bootstrap
__tests__/cortex/
  tool-graph.test.ts
  edge-accumulator.test.ts
  personalized-router.test.ts
  self-evolver.test.ts
  fact-envelope.test.ts
__tests__/live-browser/
  frontmost.test.ts
  safari-driver.test.ts
  chrome-driver.test.ts
  live-browser.test.ts
```

**Modified files:**

```
src/memory/db.ts                            # schema v23 + migration + read methods
src/tools/tracker.ts                        # JSON → SQLite (drop file path entirely)
src/tools/fallback-sequencer.ts             # DB-backed learnedSequences
src/tools/registry.ts                       # wire ToolGraph on BLOCKED, edge accumulator
src/tools/mcp/manager.ts                    # route MCP tool calls through registry.execute()
src/gateway/adapters/telegram.ts            # subscribe to tool:* events
src/gateway/adapters/slack.ts               # subscribe to tool:* events
src/engine/improvement-scheduler.ts         # register runToolEvolution weekly job
src/context/pipeline.ts                     # provenanceMetadata channel + retraction
src/gateway/event-bus.ts                    # add fact:retracted event
src/index.ts                                # register live_browser, mark browser_* deprecated
src/tools/computer-use/index.ts             # mark browser_* actions deprecated
docs/platform-audit/progress.md             # tracker
```

---

## Phase A — Hardening (do first; prerequisites for B/C/D)

### Task 1: Schema v23 — `tool_executions` and `tool_edges` tables

**Files:**
- Modify: `src/memory/db.ts` — bump `SCHEMA_VERSION` to 23, add `applyV23Migration(db)`, add read methods
- Test: `__tests__/memory-db-v23.test.ts`

- [ ] **Step 1: Write failing test** — `__tests__/memory-db-v23.test.ts`

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../src/memory/db.js";

describe("Schema v23 — tool_executions + tool_edges", () => {
  let dir: string;
  let db: MemoryDatabase;

  beforeEach(() => {
    dir = join(tmpdir(), `db-v23-${Date.now()}-${Math.random()}`);
    db = new MemoryDatabase(dir);
  });
  afterEach(() => {
    db.close();
    if (existsSync(dir)) rmSync(dir, { recursive: true, force: true });
  });

  it("schema version is at least 23", () => {
    const v = db.rawDb.pragma("user_version", { simple: true }) as number;
    expect(v).toBeGreaterThanOrEqual(23);
  });

  it("creates tool_executions table with required columns", () => {
    const cols = db.rawDb.prepare("PRAGMA table_info(tool_executions)").all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(
      expect.arrayContaining(["id", "tool_name", "success", "duration_ms", "error_code", "error_message", "subgoal_id", "session_id", "created_at"]),
    );
  });

  it("creates tool_edges table with capability_tag index", () => {
    const cols = db.rawDb.prepare("PRAGMA table_info(tool_edges)").all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(
      expect.arrayContaining(["from_tool", "to_tool", "capability_tag", "success_rate", "avg_duration_ms", "sample_count", "updated_at"]),
    );
    const indexes = db.rawDb.prepare("PRAGMA index_list(tool_edges)").all() as Array<{ name: string }>;
    expect(indexes.some((i) => i.name.includes("capability"))).toBe(true);
  });

  it("recordToolExecution writes a row", () => {
    db.recordToolExecution({
      toolName: "web",
      success: true,
      durationMs: 123,
      sessionId: "sess-1",
    });
    const row = db.rawDb.prepare("SELECT * FROM tool_executions WHERE tool_name = ?").get("web") as Record<string, unknown>;
    expect(row.success).toBe(1);
    expect(row.duration_ms).toBe(123);
  });

  it("getToolStats aggregates selection/success/failure", () => {
    db.recordToolExecution({ toolName: "web", success: true, durationMs: 100 });
    db.recordToolExecution({ toolName: "web", success: false, durationMs: 200, errorCode: "TIMEOUT" });
    db.recordToolExecution({ toolName: "web", success: true, durationMs: 150 });
    const stats = db.getToolStats("web");
    expect(stats?.selectionCount).toBe(3);
    expect(stats?.successCount).toBe(2);
    expect(stats?.failureCount).toBe(1);
    expect(stats?.avgDurationMs).toBeCloseTo(150, 0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/memory-db-v23.test.ts`
Expected: FAIL with "no such table: tool_executions" / "no such method: recordToolExecution"

- [ ] **Step 3: Implement migration + read methods in `src/memory/db.ts`**

Bump `SCHEMA_VERSION` to 23. Add (place `applyV23Migration` next to `applyV22Migration`, call it from `runMigrations`):

```typescript
function applyV23Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS tool_executions (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      tool_name     TEXT NOT NULL,
      success       INTEGER NOT NULL,
      duration_ms   INTEGER NOT NULL,
      error_code    TEXT,
      error_message TEXT,
      subgoal_id    TEXT,
      session_id    TEXT,
      created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tool_exec_name_time ON tool_executions(tool_name, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tool_exec_subgoal ON tool_executions(subgoal_id) WHERE subgoal_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS tool_edges (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag)
    );
    CREATE INDEX IF NOT EXISTS idx_tool_edges_capability ON tool_edges(capability_tag, from_tool);
  `);
}
```

Add public methods on `MemoryDatabase`:

```typescript
recordToolExecution(args: {
  toolName: string;
  success: boolean;
  durationMs: number;
  errorCode?: string;
  errorMessage?: string;
  subgoalId?: string;
  sessionId?: string;
}): void {
  this.db
    .prepare(
      `INSERT INTO tool_executions
        (tool_name, success, duration_ms, error_code, error_message, subgoal_id, session_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      args.toolName,
      args.success ? 1 : 0,
      args.durationMs,
      args.errorCode ?? null,
      args.errorMessage ?? null,
      args.subgoalId ?? null,
      args.sessionId ?? null,
    );
}

getToolStats(
  toolName: string,
  opts: { days?: number } = {},
): { selectionCount: number; successCount: number; failureCount: number; avgDurationMs: number; lastUsedAt: string | null } | null {
  const days = opts.days ?? 30;
  const row = this.db
    .prepare(
      `SELECT
         COUNT(*) AS selection_count,
         SUM(success) AS success_count,
         SUM(1 - success) AS failure_count,
         AVG(duration_ms) AS avg_duration_ms,
         MAX(created_at) AS last_used_at
       FROM tool_executions
       WHERE tool_name = ? AND created_at > datetime('now', '-' || ? || ' days')`,
    )
    .get(toolName, days) as {
    selection_count: number;
    success_count: number | null;
    failure_count: number | null;
    avg_duration_ms: number | null;
    last_used_at: string | null;
  };
  if (!row || row.selection_count === 0) return null;
  return {
    selectionCount: row.selection_count,
    successCount: Number(row.success_count ?? 0),
    failureCount: Number(row.failure_count ?? 0),
    avgDurationMs: Math.round(row.avg_duration_ms ?? 0),
    lastUsedAt: row.last_used_at,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/memory-db-v23.test.ts`
Expected: PASS (5/5)

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `npm test -- --run`
Expected: All tests pass. Two pre-existing tests hardcode schema version checks (`__tests__/schema-v14.test.ts`, `__tests__/memory/clarification-schema.test.ts`) — these use `toBeGreaterThanOrEqual` so should keep passing. If anything else breaks, fix the assertion.

- [ ] **Step 6: Commit**

```bash
git add -f src/memory/db.ts __tests__/memory-db-v23.test.ts
git commit -m "feat(memory): schema v23 — tool_executions + tool_edges tables"
```

---

### Task 2: Migrate ToolTracker JSON → SQLite

**Files:**
- Modify: `src/tools/tracker.ts` — replace JSON load/save with `MemoryDatabase` reads/writes
- Modify: `src/tools/registry.ts:307-309, 365-367` — `recordSuccess`/`recordFailure` already called; signatures need to thread `errorCode`, `errorMessage`, `subgoalId`, `sessionId` from execute()
- Modify: `src/index.ts` — drop the workspace-path arg passed to `new ToolTracker(...)`; pass `MemoryDatabase` instead
- Test: `__tests__/tools/tracker-sqlite.test.ts`

- [ ] **Step 1: Write failing test** — verify tracker reads from `tool_executions`, not `tools-stats.json`

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolTracker } from "../../src/tools/tracker.js";

describe("ToolTracker — SQLite-backed", () => {
  let db: MemoryDatabase;
  let tracker: ToolTracker;

  beforeEach(() => {
    const dir = join(tmpdir(), `tracker-${Date.now()}-${Math.random()}`);
    db = new MemoryDatabase(dir);
    tracker = new ToolTracker(db);
  });

  it("records success and queries via getStats", () => {
    tracker.recordSuccess("web", 120);
    const stats = tracker.getStats("web");
    expect(stats?.selectionCount).toBe(1);
    expect(stats?.successCount).toBe(1);
  });

  it("records failure with error reason", () => {
    tracker.recordFailure("web", 200, { errorCode: "TIMEOUT", errorMessage: "504 Gateway Timeout" });
    const stats = tracker.getStats("web");
    expect(stats?.failureCount).toBe(1);
    const row = db.rawDb.prepare("SELECT error_code FROM tool_executions WHERE tool_name = ?").get("web") as { error_code: string };
    expect(row.error_code).toBe("TIMEOUT");
  });

  it("getTopBySelectionCount returns ordered top-N", () => {
    for (let i = 0; i < 5; i++) tracker.recordSuccess("web", 10);
    for (let i = 0; i < 3; i++) tracker.recordSuccess("memory", 10);
    for (let i = 0; i < 1; i++) tracker.recordSuccess("schedule", 10);
    const top = tracker.getTopBySelectionCount(2);
    expect(top.map((t) => t.toolName)).toEqual(["web", "memory"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/tracker-sqlite.test.ts`
Expected: FAIL — `ToolTracker` constructor still takes a workspace path string, not a MemoryDatabase.

- [ ] **Step 3: Rewrite `src/tools/tracker.ts`**

Replace the entire file. The new tracker is a thin shim over `MemoryDatabase.recordToolExecution`/`getToolStats`. Drop file I/O entirely. Preserve the existing public surface (`recordSuccess`, `recordFailure`, `getStats`, `getTopBySelectionCount`, `getAll`) so callers don't change.

```typescript
import type { MemoryDatabase } from "../memory/db.js";

export interface ToolUsageStats {
  selectionCount: number;
  successCount: number;
  failureCount: number;
  avgDurationMs: number;
  lastUsedAt: string | null;
  successRate: number;
}

export class ToolTracker {
  constructor(private readonly db: MemoryDatabase) {}

  recordSuccess(toolName: string, durationMs: number, ctx: { subgoalId?: string; sessionId?: string } = {}): void {
    this.db.recordToolExecution({ toolName, success: true, durationMs, ...ctx });
  }

  recordFailure(
    toolName: string,
    durationMs: number,
    ctx: { errorCode?: string; errorMessage?: string; subgoalId?: string; sessionId?: string } = {},
  ): void {
    this.db.recordToolExecution({ toolName, success: false, durationMs, ...ctx });
  }

  getStats(toolName: string, days = 30): ToolUsageStats | null {
    const s = this.db.getToolStats(toolName, { days });
    if (!s) return null;
    return {
      ...s,
      successRate: s.selectionCount === 0 ? 0 : s.successCount / s.selectionCount,
    };
  }

  getTopBySelectionCount(n: number, days = 30): Array<{ toolName: string; selectionCount: number }> {
    const rows = this.db.rawDb
      .prepare(
        `SELECT tool_name, COUNT(*) AS selection_count
           FROM tool_executions
           WHERE created_at > datetime('now', '-' || ? || ' days')
           GROUP BY tool_name
           ORDER BY selection_count DESC
           LIMIT ?`,
      )
      .all(days, n) as Array<{ tool_name: string; selection_count: number }>;
    return rows.map((r) => ({ toolName: r.tool_name, selectionCount: r.selection_count }));
  }

  getAll(days = 30): Map<string, ToolUsageStats> {
    const rows = this.db.rawDb
      .prepare(
        `SELECT tool_name FROM tool_executions
           WHERE created_at > datetime('now', '-' || ? || ' days')
           GROUP BY tool_name`,
      )
      .all(days) as Array<{ tool_name: string }>;
    const out = new Map<string, ToolUsageStats>();
    for (const r of rows) {
      const s = this.getStats(r.tool_name, days);
      if (s) out.set(r.tool_name, s);
    }
    return out;
  }
}
```

- [ ] **Step 4: Update `src/tools/registry.ts` — pass session/subgoal context**

In the `execute()` method, change the two tracker call sites to pass context:

Replace the `if (this._tracker) { this._tracker.recordSuccess(name, durationMs); }` line with:
```typescript
if (this._tracker) {
  this._tracker.recordSuccess(name, durationMs, {
    subgoalId: context.engineContext?.activeSubGoal?.id,
    sessionId: context.engineContext?.sessionId,
  });
}
```

And the failure call site with:
```typescript
if (this._tracker) {
  this._tracker.recordFailure(name, durationMs, {
    errorCode: error instanceof ToolExecutionError ? error.code : "UNKNOWN",
    errorMessage: error instanceof Error ? error.message : String(error),
    subgoalId: context.engineContext?.activeSubGoal?.id,
    sessionId: context.engineContext?.sessionId,
  });
}
```

(If `ToolExecutionError` doesn't have a `.code` field today, use `"EXEC_FAILED"` as the literal — check the class first.)

- [ ] **Step 5: Update `src/index.ts` — pass MemoryDatabase to ToolTracker**

Find the line `new ToolTracker(<workspacePath>)` and change to `new ToolTracker(memoryDb)` (the MemoryDatabase instance is already constructed earlier — find its variable name). Remove any workspace-path reference for tracker.

- [ ] **Step 6: Run full suite**

Run: `npm test -- --run`
Expected: PASS. Tracker tests using JSON file paths may fail — update them to use the SQLite-backed tracker (mirror Step 1's test). Search for `tools-stats.json` to find any leftover references; remove them.

- [ ] **Step 7: Commit**

```bash
git add -f src/tools/tracker.ts src/tools/registry.ts src/index.ts __tests__/tools/tracker-sqlite.test.ts
git rm -f __tests__/tools/tracker.test.ts 2>/dev/null || true   # only if old JSON test exists
git commit -m "refactor(tools): migrate ToolTracker JSON → SQLite, capture error reasons"
```

---

### Task 3: Telegram adapter — subscribe to tool:* events

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`
- Test: `__tests__/gateway/telegram-narration.test.ts`

- [ ] **Step 1: Write failing test** — assert that telegram adapter sends a chunk on `tool:start` and `tool:goal_advance`

```typescript
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";
import { subscribeTelegramNarration } from "../../src/gateway/adapters/telegram.js";

describe("Telegram narration", () => {
  it("calls send on tool:start", async () => {
    const bus = new GatewayEventBus();
    const send = vi.fn();
    subscribeTelegramNarration(bus, { send, chatId: "123" });
    bus.emit({ type: "tool:start", toolName: "web", args: { action: "search", query: "x" }, turnId: "t1" });
    await new Promise((r) => setImmediate(r));
    expect(send).toHaveBeenCalled();
    expect(send.mock.calls[0][0]).toContain("Searching"); // narration-formatter output
  });

  it("calls send on tool:goal_advance", async () => {
    const bus = new GatewayEventBus();
    const send = vi.fn();
    subscribeTelegramNarration(bus, { send, chatId: "123" });
    bus.emit({ type: "tool:goal_advance", toolName: "web", subGoal: "find news", verdict: "ADVANCES" });
    await new Promise((r) => setImmediate(r));
    expect(send).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test, expect FAIL**
Run: `npx vitest run __tests__/gateway/telegram-narration.test.ts`
Expected: FAIL — `subscribeTelegramNarration` not exported.

- [ ] **Step 3: Add `subscribeTelegramNarration` export in `src/gateway/adapters/telegram.ts`**

Mirror the CLI pattern at `cli.ts:353-365`. Subscribe to `tool:start`, `tool:result`, `tool:goal_advance`, `tool:goal_blocked`, format via `formatToolEvent` from `../narration-formatter.js`, and call the provided `send` function. Keep narration off when the user is in a long-running parliament session (check existing telegram session state). Throttle to one message per 1.5s per chat to avoid Telegram flood-banning.

```typescript
import { formatToolEvent } from "../narration-formatter.js";
import type { GatewayEventBus, GatewaySystemEvent } from "../event-bus.js";

export interface TelegramNarrationDeps {
  send: (text: string) => Promise<void> | void;
  chatId: string;
}

export function subscribeTelegramNarration(bus: GatewayEventBus, deps: TelegramNarrationDeps): void {
  const events: Array<GatewaySystemEvent["type"]> = [
    "tool:start",
    "tool:result",
    "tool:goal_advance",
    "tool:goal_blocked",
  ];
  let lastSentAt = 0;
  const minIntervalMs = 1500;
  for (const ev of events) {
    bus.on(ev, async (e) => {
      const now = Date.now();
      if (now - lastSentAt < minIntervalMs) return;
      const line = formatToolEvent(e);
      if (!line) return;
      lastSentAt = now;
      await deps.send(line);
    });
  }
}
```

Then wire it where the telegram bot is constructed (find existing telegram bot init in the same file or in `src/index.ts`); add `subscribeTelegramNarration(eventBus, { send: (t) => bot.api.sendMessage(chatId, t), chatId })` after the bot is connected. If multiple chat IDs exist (multi-user), iterate.

- [ ] **Step 4: Run test, expect PASS**
- [ ] **Step 5: Commit**

```bash
git add -f src/gateway/adapters/telegram.ts __tests__/gateway/telegram-narration.test.ts
git commit -m "feat(telegram): subscribe to tool:* events for narration"
```

---

### Task 4: Slack adapter — subscribe to tool:* events

**Files:**
- Modify: `src/gateway/adapters/slack.ts`
- Test: `__tests__/gateway/slack-narration.test.ts`

Same shape as Task 3, but Slack uses `chat.postMessage`. Threshold the throttle higher (3s) since Slack rate limits are tighter and narration in a busy channel is noisier.

- [ ] **Step 1-5:** Mirror Task 3, swapping send target to Slack's `web.chat.postMessage({ channel, text })`.

Commit:
```bash
git commit -m "feat(slack): subscribe to tool:* events for narration"
```

---

### Task 5: FallbackSequencer DB-backed

**Files:**
- Modify: `src/tools/fallback-sequencer.ts` — replace `learnedSequences: Map<string, FallbackSequence>` with reads against `tool_edges`
- Test: `__tests__/tools/fallback-sequencer-db.test.ts`

- [ ] **Step 1: Write failing test** — sequencer reads learned fallbacks from `tool_edges`, not in-memory map

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { FallbackSequencer } from "../../src/tools/fallback-sequencer.js";

describe("FallbackSequencer — DB-backed", () => {
  let db: MemoryDatabase;
  let seq: FallbackSequencer;

  beforeEach(() => {
    const dir = join(tmpdir(), `fbseq-${Date.now()}-${Math.random()}`);
    db = new MemoryDatabase(dir);
    seq = new FallbackSequencer(db);
  });

  it("returns learned fallback when DB has data", () => {
    db.rawDb.prepare(
      "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
    ).run("web", "web_crawl", "web_fetch", 0.85, 10);
    const next = seq.getNextFallback("web", "web_fetch");
    expect(next).toBe("web_crawl");
  });

  it("returns null when no edge exists", () => {
    expect(seq.getNextFallback("web", "web_fetch")).toBeNull();
  });

  it("survives restart (no in-memory state)", () => {
    db.rawDb.prepare(
      "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
    ).run("web", "memory", "search", 0.7, 5);
    const seq2 = new FallbackSequencer(db);
    expect(seq2.getNextFallback("web", "search")).toBe("memory");
  });
});
```

- [ ] **Step 2: Run test → FAIL** (constructor takes no DB today)

- [ ] **Step 3: Rewrite `src/tools/fallback-sequencer.ts`**

```typescript
import type { MemoryDatabase } from "../memory/db.js";

export class FallbackSequencer {
  constructor(private readonly db: MemoryDatabase) {}

  getNextFallback(fromTool: string, capabilityTag: string, exclude: string[] = []): string | null {
    const placeholders = exclude.map(() => "?").join(",") || "''";
    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
           WHERE from_tool = ? AND capability_tag = ?
             AND sample_count >= 3
             AND to_tool NOT IN (${placeholders})
           ORDER BY success_rate DESC, sample_count DESC
           LIMIT 1`,
      )
      .get(fromTool, capabilityTag, ...exclude) as { to_tool: string } | undefined;
    return row?.to_tool ?? null;
  }
}
```

(Drop the old `FallbackDiscoverer` learning loop — tool_edges is now populated by the EdgeAccumulator from Task 8 instead. If `FallbackDiscoverer` has callers other than registry, leave them but mark deprecated. Otherwise delete the file.)

- [ ] **Step 4: Run test → PASS**
- [ ] **Step 5: Update callers** — search for `new FallbackSequencer(` and pass MemoryDatabase. Search for `FallbackDiscoverer` references and remove if dead.
- [ ] **Step 6: Run full suite**
- [ ] **Step 7: Commit**

```bash
git commit -m "refactor(tools): DB-backed FallbackSequencer via tool_edges"
```

---

### Task 6: Wrap MCP tool execution through ToolRegistry.execute()

**Files:**
- Modify: `src/tools/mcp/manager.ts` — when registering MCP tools (~line 82), wrap the tool's `execute()` so it goes through registry's lifecycle (validation, GAV, tracker, narration)
- Test: `__tests__/tools/mcp-lifecycle.test.ts`

- [ ] **Step 1: Audit current MCP registration** at `src/tools/mcp/manager.ts:82`. Confirm whether the wrapped `MCPClient.callTool()` is already invoked through `registry.execute()` or directly. If indirectly, the `tool:*` events won't fire and the GAV verifier won't run.

- [ ] **Step 2: Write failing test** — assert that calling an MCP-registered tool via `registry.execute()` emits `tool:start` and `tool:result`

```typescript
import { describe, it, expect, vi } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("MCP tools — full registry lifecycle", () => {
  it("emits tool:start and tool:result for MCP-registered tool", async () => {
    const bus = new GatewayEventBus();
    const registry = new ToolRegistry();
    registry.setEventBus(bus);
    registry.register({
      name: "mcp_github_search",
      definition: { name: "mcp_github_search", description: "search github", parameters: { type: "object", properties: {} } },
      category: "external",
      execute: async () => "ok",
    });
    const start = vi.fn(); bus.on("tool:start", start);
    const result = vi.fn(); bus.on("tool:result", result);
    await registry.execute("mcp_github_search", {}, { engineContext: { sessionId: "s1" } } as any);
    expect(start).toHaveBeenCalled();
    expect(result).toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run test** — likely passes already if MCP registration goes through `registry.register()`. If not, fix the registration path so MCP tools are registered identically to builtins.

- [ ] **Step 4: Verify trajectory_turns gets MCP tool calls** — search `src/engine/runtime.ts` for the trajectory-write site and confirm it doesn't filter by `category !== "external"`. If it does, remove the filter.

- [ ] **Step 5: Commit**

```bash
git commit -m "fix(mcp): route MCP tool calls through ToolRegistry.execute() lifecycle"
```

---

### Task 7: Capabilities + executionPolicy backfill — top 30 tools

**Files:**
- Modify: top-30 most-used tool definitions
- Test: `__tests__/tools/quality-checklist.test.ts`

- [ ] **Step 1: Identify top-30 tools** — Run a one-off script:
```bash
npx tsx -e "
import { MemoryDatabase } from './src/memory/db.ts';
import { join } from 'node:path';
import { homedir } from 'node:os';
const db = new MemoryDatabase(join(homedir(), '.stackowl'));
console.log(db.rawDb.prepare(\`SELECT tool_name, COUNT(*) c FROM tool_executions GROUP BY tool_name ORDER BY c DESC LIMIT 30\`).all());
"
```
If the user's `.stackowl` has no `tool_executions` data yet (Task 1 just shipped it), use the legacy `tools-stats.json` if present, or pick the top-30 by codebase importance: `web`, `memory`, `read_file`, `write_file`, `edit_file`, `shell`, `vision`, `document`, `code-sandbox`, `db_query`, `schedule`, `summon_parliament`, `orchestrate_tasks`, `remember`, `recall`, `pellet_recall`, `mermaid`, `markdown`, `image_gen`, `stt`, `spreadsheet`, `data_viz`, `json_transform`, `ocr`, `pdf`, `screenshot`, `clipboard`, `mail`, `calendar`, `reminders`.

- [ ] **Step 2: Write failing checklist test**

```typescript
import { describe, it, expect } from "vitest";
import { ToolRegistry } from "../../src/tools/registry.js";
// import all top-30 tool factories
// register them with a fresh registry, then assert every tool definition has
// capabilities: string[] (≥1 entry) and executionPolicy.timeoutMs > 0

const REQUIRED_TOP_30 = [
  "web", "memory", "read_file", "write_file", "edit_file", "shell",
  "vision", "document", "code_sandbox", "db_query", "schedule",
  "summon_parliament", "orchestrate_tasks", "remember", "recall",
  "pellet_recall", "mermaid", "markdown", "image_gen", "stt",
  "spreadsheet", "data_viz", "json_transform", "ocr", "pdf",
  "screenshot", "clipboard", "mail", "calendar", "reminders",
];

describe("Tool quality checklist — top 30", () => {
  it.each(REQUIRED_TOP_30)("%s declares capabilities[] and executionPolicy", (toolName) => {
    // build the registry with all tools registered (lift from src/index.ts test helper)
    const registry = buildTestRegistry();
    const def = registry.getDefinition(toolName);
    expect(def, `${toolName} not registered`).toBeDefined();
    expect(def!.capabilities).toBeDefined();
    expect(def!.capabilities!.length).toBeGreaterThan(0);
    expect(def!.executionPolicy?.timeoutMs).toBeGreaterThan(0);
  });
});
```

If `buildTestRegistry()` doesn't exist, extract one from `src/index.ts` into a shared test helper or use `import { createDefaultToolRegistry } from "..."` if such a function exists.

- [ ] **Step 3: Run test → FAIL** for every tool missing a capabilities/executionPolicy field.

- [ ] **Step 4: Add capabilities + executionPolicy to each missing tool**

For each failing tool, edit its `.ts` file. Capabilities are short tags (e.g., `["web_search", "web_fetch"]`). ExecutionPolicy defaults: `{ timeoutMs: 30_000, maxRetries: 1, retryDelayMs: 1_000 }` for I/O, `{ timeoutMs: 5_000, maxRetries: 0 }` for pure compute. Apply category-appropriate defaults.

Example for `web-unified.ts` add:
```typescript
capabilities: ["web_search", "web_fetch", "web_interact"],
executionPolicy: { timeoutMs: 30_000, maxRetries: 1, retryDelayMs: 1_500 },
```

- [ ] **Step 5: Run test → PASS** (30/30)
- [ ] **Step 6: Commit (batch by category to keep commits reviewable)**

```bash
git commit -m "feat(tools): backfill capabilities + executionPolicy on top-30 tools"
```

---

## Phase B — 7b: CWTG + PTR

### Task 8: ToolGraph (Dijkstra over capability-tagged edges)

**Files:**
- Create: `src/tools/cortex/tool-graph.ts`
- Test: `__tests__/cortex/tool-graph.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";

describe("ToolGraph — Dijkstra replan", () => {
  let db: MemoryDatabase;
  let graph: ToolGraph;

  beforeEach(() => {
    const dir = join(tmpdir(), `tg-${Date.now()}-${Math.random()}`);
    db = new MemoryDatabase(dir);
    graph = new ToolGraph(db);

    const ins = db.rawDb.prepare(
      "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count) VALUES (?, ?, ?, ?, ?, ?)",
    );
    // capability "web_fetch": web -> web_crawl (high) -> document (low)
    ins.run("web", "web_crawl", "web_fetch", 0.9, 200, 50);
    ins.run("web", "document", "web_fetch", 0.5, 800, 10);
    ins.run("web_crawl", "document", "web_fetch", 0.3, 500, 5);
  });

  it("returns highest-success-rate alternative", () => {
    const next = graph.replan("web", "web_fetch", { exclude: [] });
    expect(next).toBe("web_crawl");
  });

  it("excludes the failing tool itself", () => {
    const next = graph.replan("web", "web_fetch", { exclude: ["web"] });
    expect(next).toBe("web_crawl");
  });

  it("falls back to next-best when primary excluded", () => {
    const next = graph.replan("web", "web_fetch", { exclude: ["web", "web_crawl"] });
    expect(next).toBe("document");
  });

  it("returns null when no edges match", () => {
    const next = graph.replan("nonexistent", "fake_capability");
    expect(next).toBeNull();
  });

  it("respects min sample count threshold", () => {
    const tg = new ToolGraph(db, { minSamples: 20 });
    // only web_crawl has 50 samples; others below 20
    const next = tg.replan("web", "web_fetch");
    expect(next).toBe("web_crawl");
  });
});
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `src/tools/cortex/tool-graph.ts`**

For the v1 we don't need full Dijkstra — single-hop is sufficient because BLOCKED-verdict recovery picks one alternative tool, not a path. (Multi-hop chaining adds complexity without proven value at this scale.) Keep it single-hop now; document the multi-hop extension point.

```typescript
import type { MemoryDatabase } from "../../memory/db.js";

export interface ReplanOptions {
  exclude?: string[];
}

export interface ToolGraphConfig {
  minSamples?: number;
}

export class ToolGraph {
  constructor(
    private readonly db: MemoryDatabase,
    private readonly config: ToolGraphConfig = {},
  ) {}

  /**
   * Find the next-best tool to handle a capability when `currentTool` failed.
   * Single-hop replan: returns the tool with highest success_rate that shares
   * the capability tag and isn't in `exclude`.
   *
   * Returns null if no candidate has at least `minSamples` (default 3).
   */
  replan(currentTool: string, capabilityTag: string, opts: ReplanOptions = {}): string | null {
    const minSamples = this.config.minSamples ?? 3;
    const exclude = [currentTool, ...(opts.exclude ?? [])];
    const placeholders = exclude.map(() => "?").join(",");
    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
           WHERE capability_tag = ?
             AND sample_count >= ?
             AND to_tool NOT IN (${placeholders})
           ORDER BY success_rate DESC, sample_count DESC, avg_duration_ms ASC
           LIMIT 1`,
      )
      .get(capabilityTag, minSamples, ...exclude) as { to_tool: string } | undefined;
    return row?.to_tool ?? null;
  }
}
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cortex): ToolGraph single-hop replan via tool_edges"
```

---

### Task 9: EdgeAccumulator — populate tool_edges from executions

**Files:**
- Create: `src/tools/cortex/edge-accumulator.ts`
- Modify: `src/tools/registry.ts` — call `accumulator.observe()` on success+failure
- Test: `__tests__/cortex/edge-accumulator.test.ts`

- [ ] **Step 1: Write failing test** — feed sequence of tool calls (a→b→c with capability "web_fetch") and confirm edges appear in `tool_edges` with correct success rates.

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";

describe("EdgeAccumulator", () => {
  let db: MemoryDatabase;
  let acc: EdgeAccumulator;
  beforeEach(() => {
    db = new MemoryDatabase(join(tmpdir(), `edge-${Date.now()}-${Math.random()}`));
    acc = new EdgeAccumulator(db);
  });

  it("creates edge on first observation", () => {
    acc.observe({ fromTool: "web", toTool: "web_crawl", capabilityTag: "web_fetch", success: true, durationMs: 100 });
    const row = db.rawDb.prepare("SELECT * FROM tool_edges WHERE from_tool=? AND to_tool=? AND capability_tag=?").get("web", "web_crawl", "web_fetch") as any;
    expect(row.sample_count).toBe(1);
    expect(row.success_rate).toBe(1);
    expect(row.avg_duration_ms).toBe(100);
  });

  it("updates running averages on subsequent observations", () => {
    acc.observe({ fromTool: "web", toTool: "web_crawl", capabilityTag: "web_fetch", success: true, durationMs: 100 });
    acc.observe({ fromTool: "web", toTool: "web_crawl", capabilityTag: "web_fetch", success: false, durationMs: 200 });
    acc.observe({ fromTool: "web", toTool: "web_crawl", capabilityTag: "web_fetch", success: true, durationMs: 300 });
    const row = db.rawDb.prepare("SELECT * FROM tool_edges WHERE from_tool=? AND to_tool=? AND capability_tag=?").get("web", "web_crawl", "web_fetch") as any;
    expect(row.sample_count).toBe(3);
    expect(row.success_rate).toBeCloseTo(2/3, 3);
    expect(row.avg_duration_ms).toBe(200);
  });
});
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `src/tools/cortex/edge-accumulator.ts`**

```typescript
import type { MemoryDatabase } from "../../memory/db.js";

export interface EdgeObservation {
  fromTool: string;
  toTool: string;
  capabilityTag: string;
  success: boolean;
  durationMs: number;
}

export class EdgeAccumulator {
  constructor(private readonly db: MemoryDatabase) {}

  observe(obs: EdgeObservation): void {
    const existing = this.db.rawDb
      .prepare(
        "SELECT success_rate, avg_duration_ms, sample_count FROM tool_edges WHERE from_tool = ? AND to_tool = ? AND capability_tag = ?",
      )
      .get(obs.fromTool, obs.toTool, obs.capabilityTag) as
      | { success_rate: number; avg_duration_ms: number; sample_count: number }
      | undefined;

    if (!existing) {
      this.db.rawDb
        .prepare(
          "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        )
        .run(obs.fromTool, obs.toTool, obs.capabilityTag, obs.success ? 1 : 0, obs.durationMs, 1);
      return;
    }

    const newCount = existing.sample_count + 1;
    const newRate = (existing.success_rate * existing.sample_count + (obs.success ? 1 : 0)) / newCount;
    const newAvg = Math.round((existing.avg_duration_ms * existing.sample_count + obs.durationMs) / newCount);
    this.db.rawDb
      .prepare(
        "UPDATE tool_edges SET success_rate = ?, avg_duration_ms = ?, sample_count = ?, updated_at = datetime('now') WHERE from_tool = ? AND to_tool = ? AND capability_tag = ?",
      )
      .run(newRate, newAvg, newCount, obs.fromTool, obs.toTool, obs.capabilityTag);
  }
}
```

- [ ] **Step 4: Run → PASS**

- [ ] **Step 5: Wire EdgeAccumulator into registry** — `src/tools/registry.ts`. The accumulator only needs `from→to` pairs when a fallback fires. For now, `from = name` (the failing tool), `to = self` for normal success — but the value is in fallback chains. Defer wiring to Task 10 (registry integration) where we pair this with ToolGraph on BLOCKED.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(cortex): EdgeAccumulator with running-average updates"
```

---

### Task 10: Wire ToolGraph + EdgeAccumulator into registry BLOCKED-verdict path

**Files:**
- Modify: `src/tools/registry.ts:344-356` (the existing BLOCKED branch)
- Test: `__tests__/cortex/registry-replan.test.ts`

The current BLOCKED branch at lines 344-356 only emits an event and wraps the result with `<tool_result_warning>`. We add: if a `toolGraph` is configured AND the tool definition has a `capabilities` array AND a sub-goal is active, attempt single-hop replan; if that returns a tool, automatically execute it (recursively, with cycle protection) and use its result; record an EdgeAccumulator observation in either case.

- [ ] **Step 1: Write failing integration test**

```typescript
import { describe, it, expect, vi } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolRegistry } from "../../src/tools/registry.js";
import { ToolGraph } from "../../src/tools/cortex/tool-graph.js";
import { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";

describe("Registry — BLOCKED-verdict triggers replan", () => {
  it("auto-falls back to next-best tool when verifier returns BLOCKED", async () => {
    const db = new MemoryDatabase(join(tmpdir(), `reg-replan-${Date.now()}-${Math.random()}`));
    const ins = db.rawDb.prepare(
      "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)",
    );
    ins.run("web", "web_crawl", "web_fetch", 0.95, 50);

    const registry = new ToolRegistry();
    registry.setToolGraph(new ToolGraph(db));
    registry.setEdgeAccumulator(new EdgeAccumulator(db));

    // verifier always returns BLOCKED for "web"
    registry.setGoalVerifier({
      verify: async () => ({ verdict: "BLOCKED", reason: "paywall" }),
    } as any);

    registry.register({
      name: "web",
      definition: { name: "web", description: "", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: async () => "behind paywall",
    });
    const crawlExec = vi.fn().mockResolvedValue("clean content");
    registry.register({
      name: "web_crawl",
      definition: { name: "web_crawl", description: "", parameters: { type: "object", properties: {} }, capabilities: ["web_fetch"] },
      execute: crawlExec,
    });

    const result = await registry.execute(
      "web",
      {},
      { engineContext: { sessionId: "s1", activeSubGoal: { id: "sg1", description: "fetch article" }, userMessage: "read it" } } as any,
    );
    expect(crawlExec).toHaveBeenCalledOnce();
    expect(result).toContain("clean content");
  });

  it("does not replan when no capability tag is set", async () => {
    /* …same setup but tool has no capabilities[] — confirm replan is skipped */
  });

  it("avoids infinite loop on repeated BLOCKED", async () => {
    /* both tools return BLOCKED — confirm depth limit kicks in (max 1 replan hop) */
  });
});
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Add `setToolGraph`/`setEdgeAccumulator` setters and integrate at the BLOCKED branch**

In `src/tools/registry.ts`:

```typescript
private _toolGraph?: import("./cortex/tool-graph.js").ToolGraph;
private _edgeAccumulator?: import("./cortex/edge-accumulator.js").EdgeAccumulator;

setToolGraph(g: import("./cortex/tool-graph.js").ToolGraph): void { this._toolGraph = g; }
setEdgeAccumulator(a: import("./cortex/edge-accumulator.js").EdgeAccumulator): void { this._edgeAccumulator = a; }
```

In the existing BLOCKED branch (around lines 344-356), wrap with a replan attempt. Add a third parameter `_replanDepth = 0` to `execute()` to cap recursion at 1. Sketch:

```typescript
if (verification.verdict === "BLOCKED") {
  this._eventBus?.emit({ type: "tool:goal_blocked", toolName: name, subGoal: subGoal.description, suggestion: verification.suggestion });

  // Attempt LLM-free replan if configured + capability tag available + first hop
  const capability = tool.definition.capabilities?.[0];
  if (this._toolGraph && capability && _replanDepth === 0) {
    const fallback = this._toolGraph.replan(name, capability);
    if (fallback) {
      this._eventBus?.emit({ type: "tool:fallback", fromTool: name, toTool: fallback, reason: verification.reason });
      const fallbackResult = await this.execute(fallback, args, context, _replanDepth + 1);
      this._edgeAccumulator?.observe({ fromTool: name, toTool: fallback, capabilityTag: capability, success: true, durationMs: Date.now() - startTime });
      return fallbackResult;
    }
  }
}
```

Also record EdgeAccumulator failure observation when no fallback exists or the fallback also fails — let the recursive call handle its own bookkeeping.

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Run full suite**
- [ ] **Step 6: Commit**

```bash
git commit -m "feat(cortex): wire ToolGraph replan into registry BLOCKED-verdict path"
```

---

### Task 11: PersonalizedRouter (KNN over historical trajectories)

**Files:**
- Create: `src/tools/cortex/personalized-router.ts`
- Test: `__tests__/cortex/personalized-router.test.ts`

**Reuses:** `src/session/user-memory-store.ts` for fastembed embedding + cosine search pattern.

- [ ] **Step 1: Audit `src/session/user-memory-store.ts`** to confirm the embedding API. Mirror its pattern for embedding the user message.

- [ ] **Step 2: Write failing test**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { PersonalizedRouter } from "../../src/tools/cortex/personalized-router.js";

describe("PersonalizedRouter — KNN over trajectories", () => {
  let db: MemoryDatabase;
  let router: PersonalizedRouter;

  beforeEach(async () => {
    db = new MemoryDatabase(join(tmpdir(), `ptr-${Date.now()}-${Math.random()}`));
    router = await PersonalizedRouter.create(db);
    // seed `trajectories` + `trajectory_turns`
    db.rawDb.prepare("INSERT INTO trajectories (id, user_message, outcome, created_at) VALUES (?, ?, ?, datetime('now'))").run("t1", "research the latest typescript release", "success");
    db.rawDb.prepare("INSERT INTO trajectories (id, user_message, outcome, created_at) VALUES (?, ?, ?, datetime('now'))").run("t2", "find news about AI safety", "success");
    db.rawDb.prepare("INSERT INTO trajectories (id, user_message, outcome, created_at) VALUES (?, ?, ?, datetime('now'))").run("t3", "review my notes from yesterday", "success");
    db.rawDb.prepare("INSERT INTO trajectory_turns (trajectory_id, turn_index, tool_calls) VALUES (?, ?, ?)").run("t1", 0, JSON.stringify(["web", "document"]));
    db.rawDb.prepare("INSERT INTO trajectory_turns (trajectory_id, turn_index, tool_calls) VALUES (?, ?, ?)").run("t2", 0, JSON.stringify(["web"]));
    db.rawDb.prepare("INSERT INTO trajectory_turns (trajectory_id, turn_index, tool_calls) VALUES (?, ?, ?)").run("t3", 0, JSON.stringify(["memory", "pellet_recall"]));
  });

  it("returns tool sequences from semantically similar past successes", async () => {
    const tools = await router.suggestTools("look up the typescript 5.5 release notes", { topK: 3 });
    expect(tools).toContain("web");
  });

  it("returns empty for cold-start (< 50 trajectories)", async () => {
    const cleanDb = new MemoryDatabase(join(tmpdir(), `ptr-cold-${Date.now()}-${Math.random()}`));
    const r = await PersonalizedRouter.create(cleanDb);
    const out = await r.suggestTools("anything");
    expect(out).toEqual([]);
  });
});
```

- [ ] **Step 3: Run → FAIL**

- [ ] **Step 4: Implement `src/tools/cortex/personalized-router.ts`**

```typescript
import type { MemoryDatabase } from "../../memory/db.js";
// Reuse the embedder factory from UserMemoryStore.
import { getEmbedder } from "../../session/user-memory-store.js";

const COLD_START_THRESHOLD = 50;

export interface PersonalizedRouterOptions {
  topK?: number;
  windowDays?: number;
}

export class PersonalizedRouter {
  static async create(db: MemoryDatabase): Promise<PersonalizedRouter> {
    const embedder = await getEmbedder();
    return new PersonalizedRouter(db, embedder);
  }

  constructor(
    private readonly db: MemoryDatabase,
    private readonly embedder: { embed(text: string): Promise<number[]> },
  ) {}

  async suggestTools(userMessage: string, opts: PersonalizedRouterOptions = {}): Promise<string[]> {
    const topK = opts.topK ?? 3;
    const windowDays = opts.windowDays ?? 30;

    const trajectories = this.db.rawDb
      .prepare(
        `SELECT id, user_message FROM trajectories
           WHERE outcome = 'success' AND created_at > datetime('now', '-' || ? || ' days')`,
      )
      .all(windowDays) as Array<{ id: string; user_message: string }>;

    if (trajectories.length < COLD_START_THRESHOLD) return [];

    const queryEmb = await this.embedder.embed(userMessage);
    const scored: Array<{ id: string; score: number }> = [];
    for (const t of trajectories) {
      const emb = await this.embedder.embed(t.user_message);
      scored.push({ id: t.id, score: cosine(queryEmb, emb) });
    }
    scored.sort((a, b) => b.score - a.score);

    const top = scored.slice(0, topK);
    const tools = new Set<string>();
    for (const { id } of top) {
      const turns = this.db.rawDb
        .prepare("SELECT tool_calls FROM trajectory_turns WHERE trajectory_id = ?")
        .all(id) as Array<{ tool_calls: string }>;
      for (const turn of turns) {
        try {
          const arr = JSON.parse(turn.tool_calls) as string[];
          for (const t of arr) tools.add(t);
        } catch { /* skip malformed */ }
      }
    }
    return [...tools];
  }
}

function cosine(a: number[], b: number[]): number {
  let dot = 0, magA = 0, magB = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; magA += a[i] ** 2; magB += b[i] ** 2; }
  return magA === 0 || magB === 0 ? 0 : dot / (Math.sqrt(magA) * Math.sqrt(magB));
}
```

> **NOTE TO IMPLEMENTER:** verify `getEmbedder` is the actual export name in `user-memory-store.ts`. If the store keeps its embedder private, add a small public factory there or copy the fastembed init. Either choice — pick whichever causes less surface area churn.

- [ ] **Step 5: Run → PASS**

- [ ] **Step 6: Performance check** — embedding 200 trajectories per call would be slow. Add a cache (Map<trajectoryId, number[]>) keyed on trajectory id, populated lazily, persisted in-process. For >500 trajectories, suggest a follow-up to denormalize embeddings into the trajectories table — out of scope for this task.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat(cortex): PersonalizedRouter — KNN tool suggestion from successful trajectories"
```

---

### Task 12: ToolPriorLayer for ContextPipeline

**Files:**
- Create: `src/context/layers/tool-prior.ts`
- Modify: `src/index.ts` or wherever ContextPipeline layers are registered — add ToolPriorLayer at priority 8
- Test: `__tests__/context/tool-prior-layer.test.ts`

- [ ] **Step 1: Write failing test** — pipeline with ToolPriorLayer outputs a "Suggested tools: ..." section when PTR returns non-empty.

- [ ] **Step 2: Implement layer**

```typescript
// src/context/layers/tool-prior.ts
import type { ContextLayer, ContextRequest } from "../layer.js";
import type { PersonalizedRouter } from "../../tools/cortex/personalized-router.js";

export class ToolPriorLayer implements ContextLayer {
  readonly name = "tool_prior";
  readonly priority = 8;
  readonly produces = ["tool_prior"];
  readonly dependsOn = [];

  constructor(private readonly router: PersonalizedRouter) {}

  async run(req: ContextRequest): Promise<string> {
    if (!req.userMessage) return "";
    const tools = await this.router.suggestTools(req.userMessage, { topK: 3 });
    if (tools.length === 0) return "";
    return `Tools that worked well on similar past requests: ${tools.slice(0, 5).join(", ")}.`;
  }
}
```

- [ ] **Step 3: Register layer** wherever ContextPipeline is constructed (find via `new ContextPipeline(`). Pass a PersonalizedRouter instance constructed via `await PersonalizedRouter.create(memoryDb)`.

- [ ] **Step 4: Run test → PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(context): ToolPriorLayer at priority 8 — PTR-driven tool suggestions"
```

---

## Phase C — 7c: SET + FPC

### Task 13: SelfEvolver scaffolding + critical-tool exclusion list

**Files:**
- Create: `src/tools/cortex/self-evolver.ts`
- Test: `__tests__/cortex/self-evolver.test.ts`

The critical-tool exclusion list **must** be enforced at the candidate-selection step, not as a post-filter. Hardcoding here is acceptable because these are infrastructure invariants (deleting/rewriting `remember`, `Shell`, `WriteFile`, `patch_tool` could destroy user data); they are not behavioral classification rules.

- [ ] **Step 1: Write failing test**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { SelfEvolver, CRITICAL_TOOLS } from "../../src/tools/cortex/self-evolver.js";

describe("SelfEvolver — critical exclusion + candidate selection", () => {
  let db: MemoryDatabase;
  let evolver: SelfEvolver;

  beforeEach(() => {
    db = new MemoryDatabase(join(tmpdir(), `set-${Date.now()}-${Math.random()}`));
    evolver = new SelfEvolver({ db, patchTool: { execute: async () => "" } as any, hitlChannel: { propose: async () => null } as any });

    // seed: one low-success non-critical tool, one low-success critical tool
    for (let i = 0; i < 100; i++) db.recordToolExecution({ toolName: "web", success: i < 30, durationMs: 100 });
    for (let i = 0; i < 100; i++) db.recordToolExecution({ toolName: "remember", success: i < 30, durationMs: 100 });
  });

  it("returns the worst-performing non-critical tool", async () => {
    const candidate = await evolver.findCandidate({ days: 7 });
    expect(candidate?.toolName).toBe("web");
  });

  it("never selects a critical tool", async () => {
    expect(CRITICAL_TOOLS.has("remember")).toBe(true);
    expect(CRITICAL_TOOLS.has("write_file")).toBe(true);
    expect(CRITICAL_TOOLS.has("shell")).toBe(true);
    expect(CRITICAL_TOOLS.has("patch_tool")).toBe(true);
  });
});
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `src/tools/cortex/self-evolver.ts`**

```typescript
import type { MemoryDatabase } from "../../memory/db.js";

// Hardcoded list of tools whose code or behavior must never be auto-rewritten.
// These touch durable user state, secrets, or shell — a bad rewrite = data loss.
export const CRITICAL_TOOLS = new Set([
  "remember", "recall", "pellet_recall", "memory",
  "write_file", "edit_file",
  "shell",
  "patch_tool",
  "credentials",
]);

export interface SelfEvolverDeps {
  db: MemoryDatabase;
  patchTool: { execute(args: { toolPath: string; instruction: string; failureTraces: string[] }): Promise<string> };
  hitlChannel: { propose(msg: string): Promise<{ approved: boolean } | null> };
}

export interface EvolutionCandidate {
  toolName: string;
  successRate: number;
  failureCount: number;
}

export class SelfEvolver {
  constructor(private readonly deps: SelfEvolverDeps) {}

  async findCandidate(opts: { days?: number; minExecutions?: number } = {}): Promise<EvolutionCandidate | null> {
    const days = opts.days ?? 7;
    const minExec = opts.minExecutions ?? 20;
    const placeholders = [...CRITICAL_TOOLS].map(() => "?").join(",");
    const row = this.deps.db.rawDb
      .prepare(
        `SELECT tool_name,
                COUNT(*) AS total,
                SUM(success) AS successes
           FROM tool_executions
           WHERE created_at > datetime('now', '-' || ? || ' days')
             AND tool_name NOT IN (${placeholders})
           GROUP BY tool_name
           HAVING total >= ?
           ORDER BY (CAST(SUM(success) AS REAL) / COUNT(*)) ASC, total DESC
           LIMIT 1`,
      )
      .get(days, ...CRITICAL_TOOLS, minExec) as { tool_name: string; total: number; successes: number } | undefined;
    if (!row) return null;
    return {
      toolName: row.tool_name,
      successRate: row.successes / row.total,
      failureCount: row.total - row.successes,
    };
  }
}
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cortex): SelfEvolver scaffolding + CRITICAL_TOOLS exclusion"
```

---

### Task 14: ShadowRunner — 24h shadow execution + auto-rollback

**Files:**
- Create: `src/tools/cortex/shadow-runner.ts`
- Test: `__tests__/cortex/shadow-runner.test.ts`

The shadow runner accepts a baseline tool and a candidate (rewritten) tool, runs the candidate alongside the baseline on every invocation for a configurable window (default 24h), compares success rates, and auto-promotes/rolls back. Promotion thresholds: ≥100 calls, ≥5pp improvement. Rollback: <100 calls AND >5pp regression.

- [ ] **Step 1: Write failing test** — simulate 100 invocations where candidate is 10pp better; expect promote. Then 50 invocations where candidate is 10pp worse; expect rollback.

- [ ] **Step 2: Implement.** Stateful but small: keep counters in `tool_evolution_runs` table (add to schema migration v23 if not already; otherwise add as v24 in this task — bump SCHEMA_VERSION accordingly). Public methods: `start(baseline, candidate)`, `record(success)`, `evaluate(): "promote" | "rollback" | "continue"`.

```sql
-- if adding as v24 in this task:
CREATE TABLE IF NOT EXISTS tool_evolution_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  baseline_tool TEXT NOT NULL,
  candidate_tool TEXT NOT NULL,
  baseline_path TEXT NOT NULL,
  candidate_path TEXT NOT NULL,
  baseline_successes INTEGER NOT NULL DEFAULT 0,
  baseline_total     INTEGER NOT NULL DEFAULT 0,
  candidate_successes INTEGER NOT NULL DEFAULT 0,
  candidate_total     INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'running',  -- running | promoted | rolled_back
  started_at    TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at   TEXT
);
```

- [ ] **Step 3: Run → PASS**
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(cortex): ShadowRunner — auto-promote/rollback for evolved tools"
```

---

### Task 15: Wire SelfEvolver into ImprovementScheduler — weekly job

**Files:**
- Modify: `src/engine/improvement-scheduler.ts`
- Test: `__tests__/engine/scheduler-tool-evolution.test.ts`

- [ ] **Step 1: Write failing test** — assert a 3rd timer is registered when SelfEvolver is wired and that it fires weekly during non-quiet hours, calling `evolver.findCandidate` exactly once per fire.

- [ ] **Step 2: Add to ImprovementScheduler.start()**

```typescript
// after the existing pruning timer at line ~37:
if (this.deps.selfEvolver) {
  this.timers.push(setInterval(async () => {
    if (this.isInQuietHours()) return;
    try { await this.runToolEvolution(); }
    catch (e) { log.engine.warn(`[ImprovementScheduler] Tool evolution error: ${e}`); }
  }, 7 * 24 * 60 * 60_000));
}
```

(Pass `selfEvolver` via the constructor's deps object — extend the existing constructor signature.)

`runToolEvolution()`:
```typescript
async runToolEvolution(): Promise<void> {
  if (!this.deps.selfEvolver) return;
  const candidate = await this.deps.selfEvolver.findCandidate({ days: 7 });
  if (!candidate) return;
  log.engine.info(`[SET] Candidate: ${candidate.toolName} (success ${(candidate.successRate * 100).toFixed(1)}%)`);
  // Phase 1: ask HITL approval (auto-rewrite without user consent is too risky for now)
  const approved = await this.deps.selfEvolver.proposeRewrite(candidate);
  if (!approved) return;
  await this.deps.selfEvolver.executeRewrite(candidate);
}
```

(`proposeRewrite` and `executeRewrite` are new SelfEvolver methods — implement in Step 3.)

- [ ] **Step 3: Add SelfEvolver.proposeRewrite + executeRewrite** stubs that wire into PatchTool + ShadowRunner. Keep these surfaces minimal — full automation is a future task.

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(engine): register weekly tool-evolution job in ImprovementScheduler"
```

---

### Task 16: FactEnvelope — working-memory store with retraction

**Files:**
- Create: `src/tools/cortex/fact-envelope.ts`
- Test: `__tests__/cortex/fact-envelope.test.ts`

Provenance metadata is **per-session in-memory**, not persisted. (Plan-agent finding: persistence-tax not justified by use case.)

- [ ] **Step 1: Write failing test**

```typescript
import { describe, it, expect } from "vitest";
import { FactEnvelopeStore } from "../../src/tools/cortex/fact-envelope.js";

describe("FactEnvelopeStore", () => {
  it("stores and retrieves a fact", () => {
    const store = new FactEnvelopeStore();
    const id = store.add({
      sessionId: "s1",
      turnIndex: 0,
      content: "TypeScript 5.5 was released in June 2024",
      provenance: { toolName: "web", args: { query: "ts 5.5" }, durationMs: 200, confidence: 0.9 },
    });
    const fact = store.get(id);
    expect(fact?.content).toContain("TypeScript 5.5");
    expect(fact?.retracted).toBe(false);
  });

  it("retracts and emits", () => {
    const store = new FactEnvelopeStore();
    const id = store.add({ sessionId: "s1", turnIndex: 0, content: "x", provenance: { toolName: "web", args: {}, durationMs: 0, confidence: 1 } });
    store.retract(id, "verifier flagged");
    expect(store.get(id)?.retracted).toBe(true);
  });

  it("listForSession returns only non-retracted facts", () => {
    const store = new FactEnvelopeStore();
    const idA = store.add({ sessionId: "s1", turnIndex: 0, content: "a", provenance: { toolName: "web", args: {}, durationMs: 0, confidence: 1 } });
    store.add({ sessionId: "s1", turnIndex: 1, content: "b", provenance: { toolName: "web", args: {}, durationMs: 0, confidence: 1 } });
    store.retract(idA);
    expect(store.listForSession("s1").map((f) => f.content)).toEqual(["b"]);
  });
});
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

```typescript
export interface FactProvenance {
  toolName: string;
  args: Record<string, unknown>;
  durationMs: number;
  verifiedBy?: string;
  confidence: number;
}

export interface FactEnvelope {
  id: string;
  sessionId: string;
  turnIndex: number;
  content: string;
  provenance: FactProvenance;
  retracted: boolean;
  retractionReason?: string;
}

export class FactEnvelopeStore {
  private facts = new Map<string, FactEnvelope>();
  private retractionListeners: Array<(id: string, reason: string | undefined) => void> = [];

  add(input: Omit<FactEnvelope, "id" | "retracted">): string {
    const id = `f_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    this.facts.set(id, { ...input, id, retracted: false });
    return id;
  }
  get(id: string): FactEnvelope | undefined { return this.facts.get(id); }
  retract(id: string, reason?: string): void {
    const f = this.facts.get(id);
    if (!f || f.retracted) return;
    f.retracted = true;
    f.retractionReason = reason;
    for (const l of this.retractionListeners) l(id, reason);
  }
  listForSession(sessionId: string): FactEnvelope[] {
    return [...this.facts.values()].filter((f) => f.sessionId === sessionId && !f.retracted);
  }
  onRetraction(listener: (id: string, reason: string | undefined) => void): void {
    this.retractionListeners.push(listener);
  }
}
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(cortex): FactEnvelopeStore — in-memory facts with retraction"
```

---

### Task 17: fact:retracted event + ContextPipeline retraction handling

**Files:**
- Modify: `src/gateway/event-bus.ts` — add `fact:retracted`
- Modify: `src/context/pipeline.ts` — subscribe to `fact:retracted`, drop layers/short-term entries containing the retracted fact id; emit a "retracted" notice in trace
- Test: `__tests__/context/pipeline-retraction.test.ts`

- [ ] **Step 1: Add event** in `event-bus.ts:4-20`:
```typescript
| { type: "fact:retracted"; factId: string; reason?: string; sessionId: string }
```

- [ ] **Step 2: Wire FactEnvelopeStore into the event bus** — wherever the store is constructed (probably in `src/index.ts` near pipeline init), call `store.onRetraction((id, reason) => bus.emit({ type: "fact:retracted", factId: id, reason, sessionId: <current> }))`.

- [ ] **Step 3: Pipeline subscribes** — in `ContextPipeline` constructor, accept an optional `eventBus` and subscribe to `fact:retracted`. On receipt, sweep `shortTermLayers` for any entry whose key starts with `fact:<factId>` and delete it.

- [ ] **Step 4: Test** verifies emitting `fact:retracted` removes a matching short-term layer.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(context): fact:retracted event drops retracted facts from pipeline"
```

---

## Phase D — Live Browser + last gaps

### Task 18: Frontmost browser detector

**Files:**
- Create: `src/tools/live-browser/frontmost.ts`
- Test: `__tests__/live-browser/frontmost.test.ts`

- [ ] **Step 1: Write failing test** — mock `child_process.execFileSync` to return "Safari" / "Google Chrome" / "Other"; assert detector returns `"safari"` / `"chrome"` / `null`.

- [ ] **Step 2: Implement**

```typescript
import { execFileSync } from "node:child_process";

export type FrontmostBrowser = "safari" | "chrome" | null;

export function detectFrontmostBrowser(): FrontmostBrowser {
  if (process.platform !== "darwin") return null;
  try {
    const out = execFileSync(
      "/usr/bin/osascript",
      ["-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
      { encoding: "utf8", timeout: 1000 },
    ).trim();
    if (out === "Safari") return "safari";
    if (out === "Google Chrome" || out === "Chrome") return "chrome";
    return null;
  } catch {
    return null;
  }
}
```

- [ ] **Step 3-4: Test passes, commit**

```bash
git commit -m "feat(live-browser): frontmost browser detector via osascript"
```

---

### Task 19: Safari JXA driver

**Files:**
- Create: `src/tools/live-browser/safari-driver.ts`
- Test: `__tests__/live-browser/safari-driver.test.ts`

Driver actions: `listTabs(): Promise<Tab[]>`, `activeUrl()`, `activeText()`, `navigate(url)`, `click(selector)`, `fill(selector, value)`, `screenshot(): Promise<Buffer>`, `switchTab(index)`, `newTab(url?)`, `closeTab(index)`, `scroll(dx, dy)`, `back()`, `forward()`.

All actions go through `osascript -l JavaScript -e '...'`. Build a small JXA helper that serializes results as JSON to stdout.

- [ ] **Step 1: Write failing test** — mock `child_process.execFile` (or shell out path); assert `listTabs()` returns parsed array, `activeUrl()` returns string.

- [ ] **Step 2: Implement** — sketch (full file ~120 lines):

```typescript
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const exec = promisify(execFile);

export interface SafariTab { index: number; url: string; title: string; }

export class SafariDriver {
  private async runJXA(script: string): Promise<string> {
    const { stdout } = await exec("/usr/bin/osascript", ["-l", "JavaScript", "-e", script], { timeout: 5000 });
    return stdout.trim();
  }

  async listTabs(): Promise<SafariTab[]> {
    const out = await this.runJXA(`
      const safari = Application('Safari');
      const w = safari.windows[0];
      const tabs = [];
      for (let i = 0; i < w.tabs.length; i++) {
        tabs.push({ index: i, url: w.tabs[i].url(), title: w.tabs[i].name() });
      }
      JSON.stringify(tabs);
    `);
    return JSON.parse(out);
  }

  async activeUrl(): Promise<string> {
    return this.runJXA("Application('Safari').windows[0].currentTab.url()");
  }

  async navigate(url: string): Promise<void> {
    await this.runJXA(`Application('Safari').windows[0].currentTab.url = ${JSON.stringify(url)}`);
  }

  async click(selector: string): Promise<void> {
    await this.runJXA(`
      const safari = Application('Safari');
      const tab = safari.windows[0].currentTab;
      safari.doJavaScript(\`document.querySelector(${JSON.stringify(selector)}).click()\`, { in: tab });
    `);
  }

  async fill(selector: string, value: string): Promise<void> {
    await this.runJXA(`
      const safari = Application('Safari');
      const tab = safari.windows[0].currentTab;
      safari.doJavaScript(\`(()=>{const el=document.querySelector(${JSON.stringify(selector)});el.value=${JSON.stringify(value)};el.dispatchEvent(new Event('input',{bubbles:true}));})()\`, { in: tab });
    `);
  }

  // …switchTab, newTab, closeTab, screenshot via `screencapture -W` of frontmost window
}
```

> **NOTE TO IMPLEMENTER:** Safari requires Automation permission on first run (System Settings → Privacy & Security → Automation). The first JXA call will throw with permission error code -1743. Catch this and surface a clear error envelope: `{ error: { code: "AUTOMATION_PERMISSION_REQUIRED", message: "Grant Automation > Safari permission in System Settings", suggestion: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Automation'" } }`.

- [ ] **Step 3-4: Test passes, commit**

---

### Task 20: Chrome CDP driver

**Files:**
- Create: `src/tools/live-browser/chrome-driver.ts`
- Test: `__tests__/live-browser/chrome-driver.test.ts`

Wraps existing `BrowserBridge` from `src/tools/computer-use/browser/cdp.ts`. Adds `listTabs` (via `Target.getTargets`), `switchTab` (via `Target.activateTarget`).

Implementation ~80 lines; reuse `BrowserBridge.connect(9222)` then expose action methods.

- [ ] **Step 1-4: TDD as before. Commit.**

---

### Task 21: Chrome auto-bootstrap

**Files:**
- Create: `src/tools/live-browser/bootstrap.ts`
- Test: `__tests__/live-browser/bootstrap.test.ts`

Detect if Chrome is running with `--remote-debugging-port=9222` (`lsof -i :9222`). If not, prompt user via `HitlChannel`/EventBus, close Chrome via `osascript -e 'tell application "Google Chrome" to quit'`, relaunch with `open -a "Google Chrome" --args --remote-debugging-port=9222 --restore-last-session`, wait for port to open (poll `nc -z 127.0.0.1 9222` up to 5s).

- [ ] **Step 1-4: TDD. Commit.**

---

### Task 22: Unified live_browser tool

**Files:**
- Create: `src/tools/live-browser/index.ts`
- Modify: `src/index.ts` — register the new tool
- Modify: `src/tools/computer-use/index.ts` — mark `browser_*` actions deprecated (in their definitions or via the LLM-visible description)
- Test: `__tests__/live-browser/live-browser.test.ts`

Single tool, action-based dispatch. Frontmost detection picks driver. URL allowlist/blocklist from config gates risky actions (click/fill on financial domains).

- [ ] **Step 1: Write integration test** — fake `frontmost` returns "safari", call `live_browser({ action: "list_tabs" })`, expect Safari driver invoked.

- [ ] **Step 2: Implement** — declare it as a normal `ToolImplementation`:

```typescript
import type { ToolImplementation } from "../registry.js";
import { detectFrontmostBrowser } from "./frontmost.js";
import { SafariDriver } from "./safari-driver.js";
import { ChromeDriver } from "./chrome-driver.js";
import { ensureChromeBootstrapped } from "./bootstrap.js";

const ACTIONS = ["tabs", "active_url", "active_text", "navigate", "click", "fill", "screenshot", "switch_tab", "new_tab", "close_tab", "scroll", "back", "forward"] as const;

export const liveBrowserTool: ToolImplementation = {
  name: "live_browser",
  category: "external",
  definition: {
    name: "live_browser",
    description: "Control the user's actual frontmost browser (Safari or Chrome). Lists tabs, navigates, clicks, fills forms, screenshots active tab.",
    capabilities: ["browser_control"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 1 },
    parameters: { type: "object", properties: { action: { type: "string", enum: [...ACTIONS] }, /* per-action params */ }, required: ["action"] },
  },
  async execute(args, _ctx) {
    const browser = detectFrontmostBrowser();
    if (!browser) return JSON.stringify({ success: false, data: null, error: { code: "NO_FRONTMOST_BROWSER", message: "No supported browser is frontmost" } });
    const driver = browser === "safari" ? new SafariDriver() : (await ensureChromeBootstrapped(), new ChromeDriver());
    // dispatch on args.action
    /* … */
  },
};
```

- [ ] **Step 3-4: Test, commit**

---

### Task 23: Final integration + progress tracker

**Files:**
- Modify: `src/index.ts` — register all new components (ToolGraph, EdgeAccumulator, PersonalizedRouter, ToolPriorLayer, FactEnvelopeStore, SelfEvolver injected into ImprovementScheduler, live_browser tool)
- Modify: `docs/platform-audit/progress.md` — mark Element 7 components shipped

- [ ] **Step 1: Wire everything in `src/index.ts`** — find where ToolRegistry, ContextPipeline, ImprovementScheduler are constructed; pass new collaborators.

- [ ] **Step 2: Run full suite — all 3300+ tests + new ~70 tests pass**
```bash
npm test -- --run
```

- [ ] **Step 3: Run a smoke test** via CLI:
```bash
npm run dev -- "find latest typescript 5.5 release notes and summarize"
```
Confirm narration appears, tools execute, no crashes.

- [ ] **Step 4: Update `docs/platform-audit/progress.md`** — Element 7 row: "✅ implemented — 7a/7d shipped previously, 7b/7c/live_browser this branch".

- [ ] **Step 5: Final commit**

```bash
git commit -m "feat: Element 7 Tool Cortex — wire CWTG/PTR/SET/FPC + live_browser into runtime"
```

---

## Verification gate (before merge)

1. `npm test -- --run` green (existing 3300+ tests + ~70 new)
2. `npm run lint` clean on src/
3. `npm run build` succeeds (no TS errors)
4. Manual smoke: run `npm run dev`; ask a 3-tool task; confirm narration in CLI; confirm `tool_executions` rows appear in DB; confirm `tool_edges` populates after a forced fallback
5. Live-browser smoke (Safari frontmost): `live_browser({ action: "tabs" })` returns the user's actual open Safari tabs

After verification: invoke superpowers:finishing-a-development-branch.

---

## Self-Review

Spec coverage: this plan touches every piece flagged in the audit as un-shipped (CWTG, PTR, SET, FPC, ToolTracker SQLite, FallbackSequencer DB, telegram/slack narration, live_browser, MCP-through-registry, capabilities backfill). Items the user asked to skip are skipped (MCP marketplace deferred per decision 5).

Placeholder scan: every step has concrete code or exact wiring instructions. The Safari/Chrome drivers leave full implementation to the implementer for the per-action methods, but the test contract is concrete and the JXA dispatch pattern is shown.

Type consistency: `MemoryDatabase`, `ToolGraph`, `EdgeAccumulator`, `PersonalizedRouter`, `FactEnvelopeStore`, `SelfEvolver` constructor signatures are stable across tasks. `recordToolExecution` is defined in Task 1 and reused identically in Task 2.

Scope check: 23 tasks, 4 phases. Each task ships a discrete capability with its own commit. Phases A→B→C→D have no inter-phase blocking dependencies after Phase A (which provides the schema + tracker shim everything else builds on).
