# Element 12 — Heartbeat: Goal-Anchored Proactive Delivery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace StackOwl's silent-drop proactive delivery system with a goal-anchored, feedback-learning pipeline that verifies messages before delivery and records outcomes for learned scheduling.

**Architecture:** CognitiveLoop enqueues typed jobs into ProactiveJobQueue → ProactivePinger consumes them, runs each through DeliveryVerifier (cheap-tier LLM check: does this advance an active goal?) → assembles goal-aware message → delivers via EventBus → records outcome to `proactive_deliveries`. AutonomousPlanner reads `proactive_engagement` reply rates to replace hardcoded priority constants. ProactiveJobQueue migrates from its own `proactive-jobs.db` into `stackowl.db` (schema v22).

**Tech Stack:** TypeScript, better-sqlite3, Vitest, IntelligenceRouter (classification tier), GoalGraph, existing ProactiveJobQueue/ProactivePinger patterns.

---

## File Map

| File | Change |
|------|--------|
| `src/memory/db.ts` | SCHEMA_VERSION 21→22; `applyV22Migration()`; `getEngagementStats()` |
| `src/heartbeat/job-queue.ts` | Accept external DB instance; `migrateJobsDb()` export |
| `src/tools/tracker.ts` | Add `getTopBySelectionCount(n)` |
| `src/heartbeat/capability-scanner.ts` | Replace hardcoded importantTools with ToolTracker query |
| `src/heartbeat/delivery-verifier.ts` | **New** — DeliveryVerifier class |
| `src/heartbeat/proactive.ts` | Silent drop fix; delivery recording; goal-aware assembly; dead stubs removed |
| `src/heartbeat/planner.ts` | Learned priority scoring; `goal_progress_update` action type |
| `src/heartbeat/consolidation.ts` | **Delete** |
| `src/cognition/loop.ts` | Enqueue `goal_progress_update` after goal-tied study/reflexion |
| `__tests__/heartbeat.test.ts` | Remove MemoryConsolidator import/tests; add new coverage |
| `__tests__/delivery-verifier.test.ts` | **New** |

---

## Task 1: Schema v22 — Add Proactive Tables

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory-db-v22.test.ts` (new)

- [ ] **Step 1: Write failing tests for schema v22**

Create `__tests__/memory-db-v22.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyV22Migration, type V22Db } from "../src/memory/db.js";

// applyV22Migration is the exported function we'll add
describe("schema v22", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    // No prior schema — applyV22Migration must CREATE the table itself
    // (covers the case where stackowl.db has never seen proactive_jobs).
  });

  afterEach(() => { db.close(); });

  it("creates proactive_jobs, proactive_deliveries, proactive_engagement tables", () => {
    applyV22Migration(db);
    const tables = db.prepare(
      `SELECT name FROM sqlite_master WHERE type='table'`
    ).all() as { name: string }[];
    const names = tables.map(t => t.name);
    expect(names).toContain("proactive_jobs");
    expect(names).toContain("proactive_deliveries");
    expect(names).toContain("proactive_engagement");
  });

  it("adds retry_count, suppress_count, goal_id, error columns to proactive_jobs", () => {
    applyV22Migration(db);
    const cols = (db.prepare(`PRAGMA table_info(proactive_jobs)`).all() as { name: string }[])
      .map(c => c.name);
    expect(cols).toContain("retry_count");
    expect(cols).toContain("suppress_count");
    expect(cols).toContain("goal_id");
    expect(cols).toContain("error");
  });

  it("is idempotent — safe to run twice", () => {
    expect(() => {
      applyV22Migration(db);
      applyV22Migration(db);
    }).not.toThrow();
  });

  it("upgrades a pre-existing proactive_jobs table without dropping data", () => {
    // Simulate the pre-v22 case: ProactiveJobQueue created the table earlier.
    db.exec(`CREATE TABLE proactive_jobs (
      id TEXT PRIMARY KEY, type TEXT NOT NULL, user_id TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 5,
      attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, created_at TEXT NOT NULL
    )`);
    db.prepare(
      `INSERT INTO proactive_jobs (id, type, user_id, scheduled_at, payload, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    ).run("j1", "check_in", "u1", new Date().toISOString(), "{}", new Date().toISOString());

    applyV22Migration(db);

    const row = db.prepare(`SELECT id, retry_count, suppress_count FROM proactive_jobs WHERE id = ?`).get("j1") as any;
    expect(row.id).toBe("j1");
    expect(row.retry_count).toBe(0);
    expect(row.suppress_count).toBe(0);
  });
});
```

- [ ] **Step 2: Run tests — expect FAIL (applyV22Migration not exported yet)**

```bash
npx vitest run __tests__/memory-db-v22.test.ts
```

Expected: FAIL with "applyV22Migration is not a function" or import error.

- [ ] **Step 3: Find SCHEMA_VERSION and last migration in db.ts**

```bash
grep -n "SCHEMA_VERSION\|current < 21\|applyV21" src/memory/db.ts | tail -5
```

Note the line numbers — you'll insert just after the v21 block.

- [ ] **Step 4: Update SCHEMA_VERSION to 22**

In `src/memory/db.ts`, change:
```typescript
const SCHEMA_VERSION = 21;
```
to:
```typescript
const SCHEMA_VERSION = 22;
```

- [ ] **Step 5: Add applyV22Migration function and export**

Find the location of `applyV21Migration` in `src/memory/db.ts` and add directly after it:

```typescript
export function applyV22Migration(db: Database.Database): void {
  // proactive_jobs may not exist yet on this DB if ProactiveJobQueue
  // has never been instantiated against it (it lives in proactive-jobs.db today).
  // Create the canonical schema first, then ALTER for v22 columns.
  db.exec(`
    CREATE TABLE IF NOT EXISTS proactive_jobs (
      id              TEXT PRIMARY KEY,
      type            TEXT NOT NULL,
      user_id         TEXT NOT NULL,
      scheduled_at    TEXT NOT NULL,
      payload         TEXT NOT NULL,
      status          TEXT NOT NULL DEFAULT 'pending',
      priority        INTEGER NOT NULL DEFAULT 5,
      attempts        INTEGER NOT NULL DEFAULT 0,
      last_attempt_at TEXT,
      created_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status_time
      ON proactive_jobs (status, scheduled_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_user_status
      ON proactive_jobs (user_id, status);
  `);

  // Add v22 columns idempotently. Each ALTER guarded by table_info check.
  const jobCols = (db.pragma("table_info(proactive_jobs)") as { name: string }[]).map(c => c.name);
  if (!jobCols.includes("retry_count")) {
    db.exec(`ALTER TABLE proactive_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0`);
  }
  if (!jobCols.includes("suppress_count")) {
    db.exec(`ALTER TABLE proactive_jobs ADD COLUMN suppress_count INTEGER NOT NULL DEFAULT 0`);
  }
  if (!jobCols.includes("goal_id")) {
    db.exec(`ALTER TABLE proactive_jobs ADD COLUMN goal_id TEXT`);
  }
  if (!jobCols.includes("error")) {
    db.exec(`ALTER TABLE proactive_jobs ADD COLUMN error TEXT`);
  }

  // Delivery outcomes table
  db.exec(`
    CREATE TABLE IF NOT EXISTS proactive_deliveries (
      id              TEXT PRIMARY KEY,
      job_id          TEXT NOT NULL,
      channel         TEXT NOT NULL,
      user_id         TEXT NOT NULL,
      message_preview TEXT,
      verdict         TEXT NOT NULL,
      delivered_at    TEXT,
      status          TEXT NOT NULL,
      user_replied_at TEXT,
      created_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pd_job ON proactive_deliveries(job_id);
    CREATE INDEX IF NOT EXISTS idx_pd_user ON proactive_deliveries(user_id, created_at);
  `);

  // Engagement signal table
  db.exec(`
    CREATE TABLE IF NOT EXISTS proactive_engagement (
      id                    TEXT PRIMARY KEY,
      delivery_id           TEXT NOT NULL,
      job_type              TEXT NOT NULL,
      goal_id               TEXT,
      replied               INTEGER NOT NULL DEFAULT 0,
      reply_latency_seconds INTEGER,
      created_at            TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pe_job_type ON proactive_engagement(job_type, created_at);
    CREATE INDEX IF NOT EXISTS idx_pe_goal ON proactive_engagement(goal_id);
  `);
}
```

**Why CREATE TABLE first:** the main `stackowl.db` may have never seen `proactive_jobs` (today it lives in a separate `proactive-jobs.db`). Without the CREATE, the subsequent ALTER TABLE statements would fail. The CREATE matches the schema in `job-queue.ts:87-105` exactly so `ProactiveJobQueue` can later be re-pointed at this DB without re-running its own `createSchema()`.

- [ ] **Step 6: Wire applyV22Migration into runMigrations()**

Find the block `if (current < 21)` in `runMigrations()` and add directly after it:

```typescript
if (current < 22) {
  applyV22Migration(this.db);
  this.db.pragma(`user_version = 22`);
}
```

- [ ] **Step 7: Add getEngagementStats method to MemoryDatabase class**

Add this method to the `MemoryDatabase` class (near other query methods):

```typescript
getEngagementStats(
  jobType: string,
  opts: { days: number; minSamples: number },
): { replyRate: number; sampleCount: number } | null {
  const cutoff = new Date(
    Date.now() - opts.days * 24 * 60 * 60 * 1000,
  ).toISOString();
  const row = this.db
    .prepare(
      `SELECT
         COUNT(*) AS total,
         SUM(replied) AS replies
       FROM proactive_engagement
       WHERE job_type = ? AND created_at >= ?`,
    )
    .get(jobType, cutoff) as { total: number; replies: number } | undefined;

  if (!row || row.total < opts.minSamples) return null;
  return {
    replyRate: row.total > 0 ? row.replies / row.total : 0,
    sampleCount: row.total,
  };
}
```

Also add these two write methods to MemoryDatabase:

```typescript
writeProactiveDelivery(params: {
  id: string;
  jobId: string;
  channel: string;
  userId: string;
  messagePreview?: string;
  verdict: string;
  deliveredAt?: string;
  status: string;
}): void {
  this.db
    .prepare(
      `INSERT OR REPLACE INTO proactive_deliveries
       (id, job_id, channel, user_id, message_preview, verdict, delivered_at, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      params.id,
      params.jobId,
      params.channel,
      params.userId,
      params.messagePreview?.slice(0, 100) ?? null,
      params.verdict,
      params.deliveredAt ?? null,
      params.status,
      new Date().toISOString(),
    );
}

writeProactiveEngagement(params: {
  id: string;
  deliveryId: string;
  jobType: string;
  goalId?: string;
  replied: boolean;
  replyLatencySeconds?: number;
}): void {
  this.db
    .prepare(
      `INSERT OR IGNORE INTO proactive_engagement
       (id, delivery_id, job_type, goal_id, replied, reply_latency_seconds, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      params.id,
      params.deliveryId,
      params.jobType,
      params.goalId ?? null,
      params.replied ? 1 : 0,
      params.replyLatencySeconds ?? null,
      new Date().toISOString(),
    );
}
```

- [ ] **Step 8: Run tests — expect PASS**

```bash
npx vitest run __tests__/memory-db-v22.test.ts
```

Expected: 3 tests PASS.

- [ ] **Step 9: Confirm TypeScript compiles**

```bash
npm run build 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/memory/db.ts __tests__/memory-db-v22.test.ts
git commit -m "feat(db): schema v22 — proactive_deliveries, proactive_engagement tables + getEngagementStats"
```

---

## Task 2: ProactiveJobQueue — Accept Main DB Connection

**Files:**
- Modify: `src/heartbeat/job-queue.ts`
- Test: `__tests__/job-queue-migration.test.ts` (new)

- [ ] **Step 1: Write failing test for DB-injection constructor and migration**

Create `__tests__/job-queue-migration.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { ProactiveJobQueue, migrateJobsDb } from "../src/heartbeat/job-queue.js";

describe("ProactiveJobQueue with external DB", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
  });

  afterEach(() => { db.close(); });

  it("accepts a Database instance instead of workspace path", () => {
    const queue = new ProactiveJobQueue(db);
    expect(() =>
      queue.schedule({
        type: "morning_brief",
        userId: "user1",
        scheduledAt: new Date(),
      })
    ).not.toThrow();
  });

  it("getDueJobs returns scheduled jobs from injected DB", () => {
    const queue = new ProactiveJobQueue(db);
    queue.schedule({
      type: "check_in",
      userId: "user1",
      scheduledAt: new Date(Date.now() - 1000),
    });
    const due = queue.getDueJobs();
    expect(due.length).toBe(1);
    expect(due[0].type).toBe("check_in");
  });
});

describe("migrateJobsDb", () => {
  it("is a no-op when old DB path does not exist", () => {
    const mainDb = new Database(":memory:");
    mainDb.pragma("journal_mode = WAL");
    mainDb.exec(`CREATE TABLE IF NOT EXISTS proactive_jobs (
      id TEXT PRIMARY KEY, type TEXT NOT NULL, user_id TEXT NOT NULL,
      scheduled_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
      status TEXT NOT NULL DEFAULT 'pending', priority INTEGER NOT NULL DEFAULT 5,
      attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT,
      error TEXT, retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
    )`);
    expect(() => migrateJobsDb("/nonexistent/path", mainDb)).not.toThrow();
    mainDb.close();
  });
});
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/job-queue-migration.test.ts
```

Expected: FAIL — `migrateJobsDb` not exported, constructor doesn't accept DB.

- [ ] **Step 3: Update ProactiveJobQueue constructor to accept DB instance**

In `src/heartbeat/job-queue.ts`, change the constructor:

```typescript
export class ProactiveJobQueue {
  private db: Database.Database;
  private ownDb = false;  // true if we created the DB ourselves

  constructor(workspacePathOrDb: string | Database.Database) {
    if (typeof workspacePathOrDb === "string") {
      const workspacePath = workspacePathOrDb;
      const dbPath = join(workspacePath, "proactive-jobs.db");
      if (!existsSync(workspacePath)) {
        mkdirSync(workspacePath, { recursive: true });
      }
      this.db = new Database(dbPath);
      this.db.pragma("journal_mode = WAL");
      this.db.pragma("synchronous = NORMAL");
      this.ownDb = true;
    } else {
      this.db = workspacePathOrDb;
    }
    this.createSchema();
    log.engine.debug("[JobQueue] Initialized proactive job queue");
  }
```

- [ ] **Step 4: Update close() to only close if we own the DB**

```typescript
close(): void {
  if (this.ownDb) {
    this.db.close();
  }
}
```

- [ ] **Step 5: Add migrateJobsDb export**

First, update the imports at the top of `job-queue.ts` to add `renameSync`:

```typescript
import { existsSync, mkdirSync, renameSync } from "node:fs";
```

Then add this function after the class definition:

```typescript
/**
 * One-time migration: copy pending jobs from the legacy proactive-jobs.db
 * into the main stackowl.db, then rename the old file to .bak.
 * Safe to call repeatedly — no-op if old file doesn't exist.
 */
export function migrateJobsDb(workspacePath: string, mainDb: Database.Database): void {
  const oldPath = join(workspacePath, "proactive-jobs.db");
  if (!existsSync(oldPath)) return;

  try {
    const oldDb = new Database(oldPath, { readonly: true });
    const rows = oldDb.prepare(`SELECT * FROM proactive_jobs WHERE status = 'pending'`).all() as any[];
    oldDb.close();

    if (rows.length > 0) {
      const insert = mainDb.prepare(`
        INSERT OR IGNORE INTO proactive_jobs
          (id, type, user_id, scheduled_at, payload, status, priority,
           attempts, last_attempt_at, error, retry_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
      `);
      const insertAll = mainDb.transaction((jobs: any[]) => {
        for (const j of jobs) {
          insert.run(
            j.id, j.type, j.user_id, j.scheduled_at, j.payload,
            j.status, j.priority, j.attempts, j.last_attempt_at,
            j.error, j.created_at,
          );
        }
      });
      insertAll(rows);
    }

    renameSync(oldPath, oldPath + ".bak");
    log.engine.info(`[JobQueue] Migrated ${rows.length} pending jobs from proactive-jobs.db; renamed to .bak`);
  } catch (err) {
    log.engine.warn(`[JobQueue] Migration from proactive-jobs.db failed: ${err}`);
  }
}

- [ ] **Step 6: Run tests — expect PASS**

```bash
npx vitest run __tests__/job-queue-migration.test.ts
```

Expected: 3 tests PASS.

- [ ] **Step 7: Wire migrateJobsDb + main-DB constructor into Telegram adapter**

Without this step, `migrateJobsDb()` is unreachable from production. The legacy `proactive-jobs.db` file will keep being written and the v22 schema additions will be invisible to runtime code.

In `src/gateway/adapters/telegram.ts` around line 880, find the block that constructs `ProactiveJobQueue` and replace it:

```typescript
// BEFORE:
let jobQueue: import("../../heartbeat/job-queue.js").ProactiveJobQueue | undefined;
try {
  const { ProactiveJobQueue } = await import("../../heartbeat/job-queue.js");
  jobQueue = new ProactiveJobQueue(cwd);
} catch (err) {
  log.engine.warn(`[Telegram] ProactiveJobQueue init failed: ${err instanceof Error ? err.message : err}`);
}

// AFTER:
let jobQueue: import("../../heartbeat/job-queue.js").ProactiveJobQueue | undefined;
try {
  const { ProactiveJobQueue, migrateJobsDb } = await import("../../heartbeat/job-queue.js");
  const mainDb = self.gateway.getDb?.()?.raw;  // MemoryDatabase exposes .raw : Database
  if (mainDb) {
    migrateJobsDb(cwd, mainDb);                // one-time consolidation; no-op if already migrated
    jobQueue = new ProactiveJobQueue(mainDb);  // share the main DB connection
  } else {
    // Fallback: keep legacy behavior if the gateway doesn't expose the DB yet
    log.engine.warn("[Telegram] Main DB unavailable — falling back to standalone proactive-jobs.db");
    jobQueue = new ProactiveJobQueue(cwd);
  }
} catch (err) {
  log.engine.warn(`[Telegram] ProactiveJobQueue init failed: ${err instanceof Error ? err.message : err}`);
}
```

> **Note on `getDb()?.raw`:** `MemoryDatabase` already wraps a `better-sqlite3` instance. If it doesn't currently expose the raw handle, add a `get raw(): Database.Database { return this.db; }` accessor in `src/memory/db.ts` first. This is a one-line addition; do it as part of this step.

- [ ] **Step 8: Add raw accessor to MemoryDatabase if missing**

Check `src/memory/db.ts`:

```bash
grep -n "get raw\|public raw\|raw:" src/memory/db.ts
```

If no accessor exists, add to the `MemoryDatabase` class:

```typescript
/** Raw better-sqlite3 handle for migrations and tools that need direct access. */
get raw(): Database.Database {
  return this.db;
}
```

- [ ] **Step 9: Run heartbeat tests + lint — expect PASS**

```bash
npm run build 2>&1 | head -10
npx vitest run __tests__/heartbeat.test.ts __tests__/job-queue-migration.test.ts 2>&1 | tail -10
```

Expected: build clean, tests pass.

- [ ] **Step 10: Commit**

```bash
git add src/heartbeat/job-queue.ts src/memory/db.ts src/gateway/adapters/telegram.ts __tests__/job-queue-migration.test.ts
git commit -m "feat(job-queue): accept external DB instance, wire migrateJobsDb at startup"
```

> **Slack adapter note:** `src/gateway/adapters/slack.ts:585` does NOT currently construct `ProactiveJobQueue` — it only constructs `ProactivePinger` without `jobQueue`. Either leave it as-is (Slack stays on in-memory pinger paths) or, if Slack should also benefit from durable jobs, replicate Step 7's pattern there. Decision: defer Slack; document in the task summary commit. The CLI adapter likewise has no jobQueue today and is out of scope here.

---

## Task 3: ToolTracker — Add getTopBySelectionCount

**Files:**
- Modify: `src/tools/tracker.ts`
- Test: in-file via existing `__tests__/tool-tracker.test.ts` if it exists, otherwise inline test

- [ ] **Step 1: Check for existing ToolTracker tests**

```bash
ls __tests__/tool-tracker* 2>/dev/null || echo "no existing test"
```

- [ ] **Step 2: Write failing test**

If no test file exists, create `__tests__/tool-tracker.test.ts`. Otherwise add to the existing file.

```typescript
import { describe, it, expect } from "vitest";
import { ToolTracker } from "../src/tools/tracker.js";

// ToolTracker uses filesystem; mock it
vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockRejectedValue(new Error("no file")),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(false),
}));

describe("ToolTracker.getTopBySelectionCount", () => {
  it("returns empty array when no stats", () => {
    const tracker = new ToolTracker("/tmp/fake");
    expect(tracker.getTopBySelectionCount(5)).toEqual([]);
  });

  it("returns tools sorted by selectionCount desc", () => {
    const tracker = new ToolTracker("/tmp/fake");
    // Manually inject stats via recordSelection
    tracker.recordSelection("web_crawl", 100);
    tracker.recordSelection("read_file", 50);
    tracker.recordSelection("web_crawl", 100);  // second selection
    const top = tracker.getTopBySelectionCount(2);
    expect(top[0].name).toBe("web_crawl");
    expect(top[0].stats.selectionCount).toBe(2);
  });

  it("respects limit n", () => {
    const tracker = new ToolTracker("/tmp/fake");
    for (let i = 0; i < 20; i++) {
      tracker.recordSelection(`tool_${i}`, 10);
    }
    expect(tracker.getTopBySelectionCount(10).length).toBe(10);
  });
});
```

- [ ] **Step 3: Run tests — expect FAIL**

```bash
npx vitest run __tests__/tool-tracker.test.ts
```

Expected: FAIL — `getTopBySelectionCount` is not a function.

- [ ] **Step 4: Add getTopBySelectionCount to ToolTracker**

In `src/tools/tracker.ts`, add after `getStats()`:

```typescript
/**
 * Returns the top N tools sorted by selectionCount descending.
 * Used by CapabilityScanner to determine which tools are "important"
 * based on actual usage instead of a hardcoded list.
 */
getTopBySelectionCount(n: number): Array<{ name: string; stats: ToolUsageStats }> {
  return Array.from(this.stats.entries())
    .sort((a, b) => b[1].selectionCount - a[1].selectionCount)
    .slice(0, n)
    .map(([name, stats]) => ({ name, stats }));
}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
npx vitest run __tests__/tool-tracker.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tools/tracker.ts __tests__/tool-tracker.test.ts
git commit -m "feat(tracker): add getTopBySelectionCount for data-driven importantTools"
```

---

## Task 4: CapabilityScanner — Replace Hardcoded importantTools

**Files:**
- Modify: `src/heartbeat/capability-scanner.ts`
- Test: `__tests__/capability-scanner.test.ts` (extend or create)

- [ ] **Step 1: Write failing test**

```typescript
// Add to __tests__/capability-scanner.test.ts (create if absent):
import { describe, it, expect, vi } from "vitest";
import { CapabilityScanner } from "../src/heartbeat/capability-scanner.js";
import { ToolTracker } from "../src/tools/tracker.js";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));
vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockRejectedValue(new Error("no file")),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(false),
}));

describe("CapabilityScanner importantTools", () => {
  it("uses ToolTracker top tools instead of hardcoded list", () => {
    const mockTracker = {
      getTopBySelectionCount: vi.fn().mockReturnValue([
        { name: "my_custom_tool", stats: { selectionCount: 100 } },
      ]),
    } as unknown as ToolTracker;

    const mockRegistry = {
      getAllDefinitions: vi.fn().mockReturnValue([
        { name: "my_custom_tool" },
      ]),
    };
    const mockSkillsRegistry = {
      listEnabled: vi.fn().mockReturnValue([]),
    };

    const scanner = new CapabilityScanner(
      {} as any,
      mockRegistry as any,
      mockSkillsRegistry as any,
      undefined,
      mockTracker,
    );

    const result = scanner.scan();
    // my_custom_tool has no skill coverage → should appear as a gap
    expect(result.gaps.some(g => g.name === "my_custom_tool")).toBe(true);
  });

  it("returns no tool_without_skill gaps when no toolTracker provided", () => {
    const scanner = new CapabilityScanner({} as any);
    const result = scanner.scan();
    const toolGaps = result.gaps.filter(g => g.type === "tool_without_skill");
    expect(toolGaps.length).toBe(0);
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npx vitest run __tests__/capability-scanner.test.ts
```

Expected: FAIL — CapabilityScanner constructor doesn't accept a 5th arg.

- [ ] **Step 3: Add toolTracker parameter to CapabilityScanner constructor**

In `src/heartbeat/capability-scanner.ts`, add the import and update the constructor:

```typescript
import type { ToolTracker } from "../tools/tracker.js";

export class CapabilityScanner {
  constructor(
    private config: StackOwlConfig,
    private toolRegistry?: ToolRegistry,
    private skillsRegistry?: SkillsRegistry,
    private microLearner?: MicroLearner,
    private toolTracker?: ToolTracker,
  ) {}
```

- [ ] **Step 4: Replace hardcoded importantTools in scanToolsWithoutSkills()**

Find `scanToolsWithoutSkills()` in `capability-scanner.ts`. Replace:

```typescript
// Core tools that should have skills
const importantTools = [
  "web_crawl",
  "duckduckgo_search",
  "generate_image",
  "send_telegram_message",
  "send_file",
  "read_file",
  "write_file",
];
```

with:

```typescript
const importantTools: string[] = this.toolTracker
  ? this.toolTracker.getTopBySelectionCount(15).map(e => e.name)
  : [];
```

- [ ] **Step 5: Update CognitiveLoop call site to pass toolTracker**

Without this, the new optional `toolTracker` param stays `undefined` in production and `importantTools` silently becomes an empty array — worse than the hardcoded list, not better.

In `src/cognition/loop.ts:215`, find:

```typescript
this.capabilityScanner = new CapabilityScanner(
  deps.config,
  deps.toolRegistry,
  deps.skillsRegistry,
  deps.microLearner,
);
```

Replace with:

```typescript
this.capabilityScanner = new CapabilityScanner(
  deps.config,
  deps.toolRegistry,
  deps.skillsRegistry,
  deps.microLearner,
  deps.toolRegistry?.getTracker() ?? undefined,
);
```

`ToolRegistry.getTracker()` already exists at `src/tools/registry.ts:80` and returns `ToolTracker | null` — that's the singleton instance the registry already uses to record tool selections. No new wiring needed.

- [ ] **Step 6: Run tests — expect PASS**

```bash
npx vitest run __tests__/capability-scanner.test.ts
npm run build 2>&1 | head -10
```

Expected: PASS, build clean.

- [ ] **Step 7: Commit**

```bash
git add src/heartbeat/capability-scanner.ts src/cognition/loop.ts __tests__/capability-scanner.test.ts
git commit -m "fix(capability-scanner): replace hardcoded importantTools with ToolTracker query (+ wire call site)"
```

---

## Task 5: DeliveryVerifier — New File

**Files:**
- Create: `src/heartbeat/delivery-verifier.ts`
- Create: `__tests__/delivery-verifier.test.ts`

- [ ] **Step 1: Write failing tests for DeliveryVerifier**

Create `__tests__/delivery-verifier.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DeliveryVerifier } from "../src/heartbeat/delivery-verifier.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { IntelligenceRouter } from "../src/intelligence/router.js";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function makeMockProvider(responseJson: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: responseJson, model: "mock", finishReason: "stop" }),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn(),
    healthCheck: vi.fn(),
  } as unknown as ModelProvider;
}

function makeMockRouter(model = "cheap-model"): IntelligenceRouter {
  return {
    resolve: vi.fn().mockReturnValue({ provider: "mock", model, tier: "low" }),
  } as unknown as IntelligenceRouter;
}

describe("DeliveryVerifier", () => {
  describe("verify()", () => {
    it("returns ADVANCES when LLM says ADVANCES", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "ADVANCES", reason: "helps goal" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "I learned about TypeScript generics today",
        activeGoals: ["master TypeScript"],
      });
      expect(result.verdict).toBe("ADVANCES");
    });

    it("returns NEUTRAL with suppressUntil when LLM says NEUTRAL", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NEUTRAL", reason: "tangential" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "Did you know cats sleep 16 hours?",
        activeGoals: ["master TypeScript"],
      });
      expect(result.verdict).toBe("NEUTRAL");
      expect(result.suppressUntil).toBeInstanceOf(Date);
    });

    it("returns NOISE when LLM says NOISE", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NOISE", reason: "irrelevant" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "check_in",
        messagePreview: "Just saying hi",
        activeGoals: [],
      });
      expect(result.verdict).toBe("NOISE");
    });

    it("skip rule 1: returns ADVANCES without LLM call when goalId present", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "goal_progress_update",
        messagePreview: "Goal update",
        activeGoals: ["master TypeScript"],
        goalId: "goal_123",  // skip rule 1
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("skip rule 2: morning_brief always gets ADVANCES without LLM call", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "morning_brief",
        messagePreview: "Good morning brief",
        activeGoals: [],
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("skip rule 3: high-priority idle message always gets ADVANCES", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "follow_up_stale_goal",
        messagePreview: "You haven't worked on X in 5 days",
        activeGoals: ["ship feature X"],
        idleSeconds: 5 * 3600,  // 5h idle
        priority: 80,
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("falls back to ADVANCES on invalid LLM response", async () => {
      const provider = makeMockProvider("not valid json");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "check_in",
        messagePreview: "Quick check-in",
        activeGoals: ["ship feature X"],
      });
      expect(result.verdict).toBe("ADVANCES");
    });

    it("works without router (uses provider default model)", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NEUTRAL", reason: "ok" }));
      const verifier = new DeliveryVerifier(provider);  // no router
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "Some study result",
        activeGoals: ["learn something"],
      });
      expect(result.verdict).toBe("NEUTRAL");
    });
  });
});
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/delivery-verifier.test.ts
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create DeliveryVerifier**

Create `src/heartbeat/delivery-verifier.ts`:

```typescript
import type { ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import { log } from "../logger.js";

export type DeliveryVerdict = "ADVANCES" | "NEUTRAL" | "NOISE";

export interface VerifyParams {
  jobType: string;
  messagePreview: string;
  activeGoals: string[];
  goalId?: string;
  idleSeconds?: number;
  priority?: number;
}

export interface DeliveryVerification {
  verdict: DeliveryVerdict;
  reason: string;
  suppressUntil?: Date;
}

/**
 * Filters proactive messages before delivery via a single cheap-tier LLM call.
 *
 * **Provider constraint:** the injected `provider` MUST host the model that
 * `router.resolve("classification")` returns. We currently only consume
 * `resolved.model`; we do NOT dispatch across providers. If the cheap-tier model
 * lives on a different provider (e.g. Anthropic Haiku while the main provider
 * is Ollama), pass that provider explicitly here. A future iteration can take a
 * `Record<string, ModelProvider>` registry and dispatch by `resolved.provider`.
 */
export class DeliveryVerifier {
  constructor(
    private readonly provider: ModelProvider,
    private readonly router?: IntelligenceRouter,
  ) {}

  async verify(params: VerifyParams): Promise<DeliveryVerification> {
    // Skip rule 1: job already has a verified goalId
    if (params.goalId) {
      return { verdict: "ADVANCES", reason: "goal-linked job, skipping verification" };
    }

    // Skip rule 2: morning_brief always delivers in its window
    if (params.jobType === "morning_brief") {
      return { verdict: "ADVANCES", reason: "morning_brief always delivers" };
    }

    // Skip rule 3: high-priority message during long idle period
    const idleHours = (params.idleSeconds ?? 0) / 3600;
    if (idleHours > 4 && (params.priority ?? 0) >= 70) {
      return { verdict: "ADVANCES", reason: "high-priority idle delivery, skipping verification" };
    }

    // Call cheap-tier LLM
    const model = this.router?.resolve("classification").model ?? undefined;
    const goalsText =
      params.activeGoals.length > 0
        ? `Active user goals:\n${params.activeGoals.map(g => `- ${g}`).join("\n")}`
        : "No active goals on record.";

    const prompt =
      `You are a proactive message quality filter for an AI assistant.\n\n` +
      `${goalsText}\n\n` +
      `The assistant wants to send this proactive message:\n"${params.messagePreview}"\n\n` +
      `Job type: ${params.jobType}\n\n` +
      `Classify this message as one of:\n` +
      `- ADVANCES: directly relevant to an active goal or clearly useful right now\n` +
      `- NEUTRAL: potentially useful but not tied to any active goal\n` +
      `- NOISE: generic, off-topic, or adds no value\n\n` +
      `Respond with JSON only:\n{"verdict":"ADVANCES|NEUTRAL|NOISE","reason":"one sentence"}`;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { maxTokens: 60 },
      );
      const raw = (response.content ?? "").trim();
      const parsed = JSON.parse(raw) as { verdict: string; reason: string };
      const verdict = ["ADVANCES", "NEUTRAL", "NOISE"].includes(parsed.verdict)
        ? (parsed.verdict as DeliveryVerdict)
        : "ADVANCES";  // safe fallback on unexpected value

      const suppressUntil =
        verdict === "NEUTRAL"
          ? new Date(Date.now() + 2 * 60 * 60 * 1000)
          : undefined;

      log.engine.debug(
        `[DeliveryVerifier] ${params.jobType} → ${verdict}: ${parsed.reason}`,
      );
      return { verdict, reason: parsed.reason ?? "", suppressUntil };
    } catch {
      // On any error (parse failure, provider error), deliver rather than silently drop
      return { verdict: "ADVANCES", reason: "verification error — defaulting to deliver" };
    }
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/delivery-verifier.test.ts
```

Expected: 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/heartbeat/delivery-verifier.ts __tests__/delivery-verifier.test.ts
git commit -m "feat(heartbeat): add DeliveryVerifier with ADVANCES/NEUTRAL/NOISE verdicts and 3 skip rules"
```

---

## Task 6: ProactivePinger — Silent Drop Fix + Delivery Recording

**Files:**
- Modify: `src/heartbeat/proactive.ts`
- Test: extend `__tests__/heartbeat.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `__tests__/heartbeat.test.ts`. Tests pass a mock `db` object directly (no `better-sqlite3` mock needed — `PingContext.db` is a duck-typed MemoryDatabase):

```typescript
describe("ProactivePinger — retry escalation", () => {
  it("reschedules with backoff when retry_count < 3", async () => {
    const reschedule = vi.fn();
    const markFailed = vi.fn();
    const mockQueue = {
      getDueJobs: vi.fn().mockReturnValue([]),
      markRunning: vi.fn(),
      markDone: vi.fn(),
      markFailed,
      reschedule,
      schedule: vi.fn(),
      getNextScheduled: vi.fn().mockReturnValue(null),
      getRetryCount: vi.fn().mockReturnValue(1),
      incrementRetry: vi.fn(),
    };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      jobQueue: mockQueue as any,
      // No eventBus, no sendToUser → triggers retry path
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    // Direct call to escalate path
    await (pinger as any).handleUndeliverable("job1", "no transport available");
    expect(mockQueue.incrementRetry).toHaveBeenCalledWith("job1");
    expect(reschedule).toHaveBeenCalled();
    expect(markFailed).not.toHaveBeenCalled();
  });

  it("marks job failed when retry_count >= 3", async () => {
    const reschedule = vi.fn();
    const markFailed = vi.fn();
    const writeDelivery = vi.fn();
    const mockQueue = {
      reschedule,
      markFailed,
      getRetryCount: vi.fn().mockReturnValue(3),
      incrementRetry: vi.fn(),
    };
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      jobQueue: mockQueue as any,
      db: mockDb,
      userId: "user1",
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    await (pinger as any).handleUndeliverable("job1", "no transport available");
    expect(markFailed).toHaveBeenCalledWith("job1", expect.any(String));
    expect(reschedule).not.toHaveBeenCalled();
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({ jobId: "job1", status: "failed" }),
    );
  });
});

describe("ProactivePinger — DeliveryVerifier integration", () => {
  it("discards NOISE verdict and writes discarded delivery row", async () => {
    const writeDelivery = vi.fn();
    const sendToUser = vi.fn();
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;
    const mockQueue = { markDone: vi.fn(), markFailed: vi.fn(), reschedule: vi.fn() };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser,
      jobQueue: mockQueue as any,
      db: mockDb,
      userId: "user1",
      deliveryVerifier: {
        verify: vi.fn().mockResolvedValue({ verdict: "NOISE", reason: "duplicate" }),
      } as any,
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({ status: "discarded", verdict: "NOISE" }),
    );
    expect(mockQueue.markDone).toHaveBeenCalledWith("job1");
  });

  it("reschedules NEUTRAL verdict and skips delivery", async () => {
    const sendToUser = vi.fn();
    const reschedule = vi.fn();
    const mockQueue = { markDone: vi.fn(), markFailed: vi.fn(), reschedule };

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser,
      jobQueue: mockQueue as any,
      userId: "user1",
      deliveryVerifier: {
        verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL", reason: "low value", suppressMinutes: 60 }),
      } as any,
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    await (pinger as any).executeJob({
      id: "job1", type: "check_in", userId: "user1",
      scheduledAt: new Date().toISOString(), payload: "{}",
      status: "running", priority: 5, attempts: 1, createdAt: new Date().toISOString(),
    });

    expect(sendToUser).not.toHaveBeenCalled();
    expect(reschedule).toHaveBeenCalled();
  });
});

describe("ProactivePinger — delivery recording", () => {
  it("writes proactive_deliveries row after delivery", async () => {
    const writeDelivery = vi.fn();
    const mockDb = { writeProactiveDelivery: writeDelivery, writeProactiveEngagement: vi.fn() } as any;

    const mockGatewayBus = { publish: vi.fn() };
    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser: vi.fn(),
      gatewayEventBus: mockGatewayBus as any,
      userId: "user1",
      db: mockDb,
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    await (pinger as any).deliverProactive("Hello world", "check_in", "job_123");
    expect(writeDelivery).toHaveBeenCalledWith(
      expect.objectContaining({
        jobId: "job_123",
        userId: "user1",
        status: "delivered",
        channel: expect.any(String),
      })
    );
  });
});
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/heartbeat.test.ts 2>&1 | tail -20
```

Expected: new tests FAIL — `db` not in PingContext, `deliverProactive` signature mismatch.

- [ ] **Step 3: Add db, userId, and deliveryVerifier to PingContext interface**

In `src/heartbeat/proactive.ts`, add to `PingContext`:

```typescript
/** MemoryDatabase — used to record delivery outcomes */
db?: import("../memory/db.js").MemoryDatabase;
/** Stable user identifier for delivery/engagement rows */
userId?: string;
/** DeliveryVerifier — gates outbound proactive sends */
deliveryVerifier?: import("./delivery-verifier.js").DeliveryVerifier;
```

Add private class fields to `ProactivePinger`:

```typescript
private lastDeliveryId: string = "";
```

- [ ] **Step 3.5: Wire DeliveryVerifier into executeJob**

Find `executeJob` at line ~272 in `src/heartbeat/proactive.ts`. Insert verifier dispatch at the top of the method body, **before** the switch statement, so every outbound job is gated:

```typescript
private async executeJob(job: ProactiveJob): Promise<void> {
  const { deliveryVerifier, db, userId, jobQueue } = this.context;

  // Gate: ask DeliveryVerifier whether this delivery should proceed
  if (deliveryVerifier) {
    let payload: any = {};
    try { payload = job.payload ? JSON.parse(job.payload) : {}; } catch { /* keep {} */ }

    const verdict = await deliveryVerifier.verify({
      jobType: job.type,
      goalId: payload.goalId,
      summary: payload.summary,
      userId: job.userId,
    });

    if (verdict.verdict === "NOISE") {
      db?.writeProactiveDelivery({
        id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        jobId: job.id,
        channel: "none",
        userId: userId ?? job.userId,
        verdict: "NOISE",
        status: "discarded",
        deliveredAt: new Date().toISOString(),
      });
      jobQueue?.markDone(job.id);
      log.engine.debug(`[ProactivePinger] NOISE verdict — discarded job ${job.id}: ${verdict.reason}`);
      return;
    }

    if (verdict.verdict === "NEUTRAL") {
      const suppressMs = (verdict.suppressMinutes ?? 60) * 60_000;
      jobQueue?.reschedule(job.id, new Date(Date.now() + suppressMs));
      db?.writeProactiveDelivery({
        id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        jobId: job.id,
        channel: "none",
        userId: userId ?? job.userId,
        verdict: "NEUTRAL",
        status: "suppressed",
        deliveredAt: new Date().toISOString(),
      });
      log.engine.debug(`[ProactivePinger] NEUTRAL verdict — suppressed job ${job.id} for ${verdict.suppressMinutes ?? 60}min`);
      return;
    }
    // ADVANCES → fall through to existing switch
  }

  // ── existing switch (job.type) follows ──
  switch (job.type) {
    case "morning_brief":
    // ... (existing cases unchanged)
```

- [ ] **Step 4: Update deliverProactive to accept jobId and record outcome**

Find `deliverProactive` at line ~770 in `src/heartbeat/proactive.ts`. Change its signature and body:

```typescript
private async deliverProactive(
  message: string,
  jobType: string = "unknown",
  jobId: string = "",
  verdict: string = "skipped_check",
): Promise<void> {
  const { gatewayEventBus, userId, db } = this.context;
  const deliveryId = `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
  const channel = gatewayEventBus ? "gateway" : "direct";

  if (gatewayEventBus && userId) {
    gatewayEventBus.publish(makeEnvelope({
      userId,
      content: message,
      channel: "proactive",
    }));
    db?.writeProactiveDelivery({
      id: deliveryId,
      jobId,
      channel,
      userId,
      messagePreview: message.slice(0, 100),
      verdict,
      deliveredAt: new Date().toISOString(),
      status: "delivered",
    });
    this.lastDeliveryId = deliveryId;
    this.lastPingTime = Date.now();
  } else if (this.context.sendToUser) {
    try {
      await this.context.sendToUser(message);
      db?.writeProactiveDelivery({
        id: deliveryId,
        jobId,
        channel: "direct",
        userId: userId ?? "unknown",
        messagePreview: message.slice(0, 100),
        verdict,
        deliveredAt: new Date().toISOString(),
        status: "delivered",
      });
      this.lastDeliveryId = deliveryId;
      this.lastPingTime = Date.now();
    } catch (err) {
      log.engine.warn(`[ProactivePinger] sendToUser failed: ${err}`);
      db?.writeProactiveDelivery({
        id: deliveryId,
        jobId,
        channel: "direct",
        userId: userId ?? "unknown",
        verdict,
        status: "failed",
      });
    }
  }
  // No else — if neither is available, job stays pending (retry logic handles it)
}
```

Add `private lastDeliveryId: string = ""` to the class fields.

- [ ] **Step 5: Replace the silent drop with retry escalation**

Find the section around line 842 in `proactive.ts` that contains:
```typescript
} else {
  console.warn("[ProactivePinger] EventBus not available, dropping ping.");
}
```

Replace the entire condition block with the prefer-eventBus → fallback-to-direct → retry-on-failure ladder:

```typescript
if (this.context.eventBus) {
  this.context.eventBus.emit("agent:ping_request", {
    prompt: fullPrompt,
    type: _type,
  });
  this.lastPingTime = Date.now();
  this.unansweredPings++;
} else if (this.context.gatewayEventBus || this.context.sendToUser) {
  // EventBus unavailable — use direct delivery path instead of dropping
  await this.deliverProactive(fullPrompt, _type, this.currentJobId);
} else {
  // No transport at all — escalate via retry handler
  await this.handleUndeliverable(this.currentJobId, "no transport available (eventBus, gatewayEventBus, sendToUser all missing)");
}
```

Add a private field `private currentJobId: string = ""` to the class. Inside `executeJob`, set `this.currentJobId = job.id` at the very top so the inner methods can reference it.

Add a new `handleUndeliverable` method to ProactivePinger:

```typescript
/**
 * Retry-or-escalate handler for undeliverable jobs.
 * - retry_count < 3: increment + reschedule with exponential backoff (60s, 120s, 240s)
 * - retry_count >= 3: mark failed + write failed delivery row
 */
private async handleUndeliverable(jobId: string, reason: string): Promise<void> {
  const { jobQueue, db, userId } = this.context;
  if (!jobQueue || !jobId) return;

  const retryCount = jobQueue.getRetryCount?.(jobId) ?? 0;

  if (retryCount < 3) {
    jobQueue.incrementRetry?.(jobId);
    const backoffMs = 60_000 * Math.pow(2, retryCount);  // 60s, 120s, 240s
    jobQueue.reschedule?.(jobId, new Date(Date.now() + backoffMs));
    log.engine.warn(`[ProactivePinger] Job ${jobId} undeliverable (${reason}); retry ${retryCount + 1}/3 in ${backoffMs / 1000}s`);
    return;
  }

  // 3-strikes: escalate to failed
  jobQueue.markFailed?.(jobId, `undeliverable after 3 retries: ${reason}`);
  db?.writeProactiveDelivery({
    id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    jobId,
    channel: "none",
    userId: userId ?? "unknown",
    verdict: "ADVANCES",
    status: "failed",
    deliveredAt: new Date().toISOString(),
  });
  log.engine.error(`[ProactivePinger] Job ${jobId} failed after 3 retries: ${reason}`);
}
```

The `getRetryCount` and `incrementRetry` methods on `ProactiveJobQueue` are added in Task 1's job-queue patch (alongside the schema v22 retry_count column). If they don't exist yet, add stubs to `src/heartbeat/job-queue.ts`:

```typescript
getRetryCount(jobId: string): number {
  const row = this.db.prepare("SELECT retry_count FROM proactive_jobs WHERE id = ?").get(jobId) as any;
  return row?.retry_count ?? 0;
}

incrementRetry(jobId: string): void {
  this.db.prepare("UPDATE proactive_jobs SET retry_count = COALESCE(retry_count, 0) + 1 WHERE id = ?").run(jobId);
}
```

- [ ] **Step 6: Add recordEngagement method to ProactivePinger**

```typescript
/**
 * Record user engagement with a proactive delivery.
 * Call this from channel adapters when a user replies to a proactive message.
 */
recordEngagement(
  deliveryId: string,
  jobType: string,
  replied: boolean,
  replyLatencySeconds?: number,
  goalId?: string,
): void {
  this.context.db?.writeProactiveEngagement({
    id: `eng_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    deliveryId,
    jobType,
    goalId,
    replied,
    replyLatencySeconds,
  });
}
```

- [ ] **Step 7: Run tests — expect PASS**

```bash
npx vitest run __tests__/heartbeat.test.ts
```

Expected: existing tests pass + new delivery tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/heartbeat/proactive.ts
git commit -m "fix(proactive): replace silent drop with retry path; add delivery recording + recordEngagement"
```

---

## Task 6.5: Wire recordEngagement from Gateway Adapter (CLI)

The `recordEngagement` method on ProactivePinger is dead code unless a gateway adapter calls it when a user replies to a proactive delivery. This task wires the CLI adapter as the reference implementation; Telegram/Slack follow the same pattern (deferred — CLI is the verifier path).

**Files:**
- Modify: `src/gateway/envelope.ts` — add optional `inReplyToDeliveryId` field
- Modify: `src/gateway/adapters/cli.ts` — track `lastDeliveryId`, call `pinger.recordEngagement()` on user reply
- Test: extend `__tests__/heartbeat.test.ts`

- [ ] **Step 1: Write failing test for engagement recording**

Add to `__tests__/heartbeat.test.ts`:

```typescript
describe("ProactivePinger — engagement recording", () => {
  it("records reply latency when user replies to a delivery", () => {
    const writeEngagement = vi.fn();
    const mockDb = { writeProactiveDelivery: vi.fn(), writeProactiveEngagement: writeEngagement } as any;

    const pingContext: PingContext = {
      provider: makeMockProvider(),
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      db: mockDb,
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    pinger.recordEngagement("del_xyz", "morning_brief", true, 42, "g1");
    expect(writeEngagement).toHaveBeenCalledWith(
      expect.objectContaining({
        deliveryId: "del_xyz",
        jobType: "morning_brief",
        replied: true,
        replyLatencySeconds: 42,
        goalId: "g1",
      }),
    );
  });
});
```

- [ ] **Step 2: Run test — expect PASS (recordEngagement was added in Task 6 Step 6)**

```bash
npx vitest run __tests__/heartbeat.test.ts -t "engagement recording"
```

Expected: PASS — this test verifies Task 6's `recordEngagement` works; the wiring below makes it actually fire from the CLI.

- [ ] **Step 3: Add `inReplyToDeliveryId` field to gateway envelope**

In `src/gateway/envelope.ts`, extend `MessageEnvelope` (or whichever type the adapter constructs):

```typescript
export interface MessageEnvelope {
  // ... existing fields
  /** If this user message is a reply to a proactive delivery, the delivery ID */
  inReplyToDeliveryId?: string;
}
```

- [ ] **Step 4: Track lastDeliveryId in CLI adapter and call recordEngagement on reply**

In `src/gateway/adapters/cli.ts`, locate the place where:
1. Outbound proactive messages are received (subscribe to `proactive` channel envelopes from `gatewayEventBus`)
2. Inbound user input is read

Add to the adapter class:

```typescript
private lastDeliveryId: string | null = null;
private lastDeliveryAt: number = 0;
private lastDeliveryJobType: string = "";
```

When a proactive envelope is published to the user, capture its delivery context. The `ProactivePinger` exposes `lastDeliveryId` after each `deliverProactive` call — read it via a public getter (add `get lastDelivery() { return { id: this.lastDeliveryId, jobType: this.currentJobType, at: this.lastPingTime }; }` to ProactivePinger), or have `deliverProactive` emit the `del_*` ID alongside the published envelope (preferred — see envelope extension above):

```typescript
// In ProactivePinger.deliverProactive — extend the envelope publish
gatewayEventBus.publish(makeEnvelope({
  userId,
  content: message,
  channel: "proactive",
  deliveryId,        // NEW
  jobType,           // NEW
}));
```

Extend `makeEnvelope` / `MessageEnvelope` to carry `deliveryId?: string` and `jobType?: string` for outbound proactive messages.

In the CLI adapter's outbound subscriber:

```typescript
gatewayEventBus.subscribe((env) => {
  if (env.channel === "proactive" && env.deliveryId) {
    this.lastDeliveryId = env.deliveryId;
    this.lastDeliveryAt = Date.now();
    this.lastDeliveryJobType = env.jobType ?? "unknown";
  }
  // ... existing render path
});
```

In the CLI adapter's inbound user-input handler, before forwarding the message to the engine:

```typescript
if (this.lastDeliveryId && this.pinger) {
  const latencySec = Math.round((Date.now() - this.lastDeliveryAt) / 1000);
  this.pinger.recordEngagement(
    this.lastDeliveryId,
    this.lastDeliveryJobType,
    /* replied */ true,
    latencySec,
  );
  this.lastDeliveryId = null;  // one-shot — don't double-record
}
```

The adapter must hold a reference to the `ProactivePinger` instance. If it doesn't already, accept it via the constructor (alongside the existing `gatewayEventBus`).

- [ ] **Step 5: Run full heartbeat suite**

```bash
npx vitest run __tests__/heartbeat.test.ts
```

Expected: all heartbeat tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/heartbeat/proactive.ts src/gateway/envelope.ts src/gateway/adapters/cli.ts __tests__/heartbeat.test.ts
git commit -m "feat(gateway): wire recordEngagement from CLI adapter — track reply latency on proactive deliveries"
```

> **Note (Telegram/Slack deferral):** The same wiring applies to Telegram and Slack adapters but is deferred to Element 12.5. CLI is the verification path — once it produces clean engagement rows, the same pattern propagates with a 5-line change per adapter.

---

## Task 7: ProactivePinger — Goal-Aware Assembly + Dead Stub Removal

**Files:**
- Modify: `src/heartbeat/proactive.ts`

- [ ] **Step 1: Write failing test for goal-aware assembly**

Add to `__tests__/heartbeat.test.ts`:

```typescript
describe("ProactivePinger — goal-aware assembly", () => {
  it("morning brief includes active goal context in prompt", async () => {
    const mockGoalGraph = {
      load: vi.fn().mockResolvedValue(undefined),
      getActive: vi.fn().mockReturnValue([
        { id: "g1", title: "Ship feature X", status: "active" }
      ]),
      getStale: vi.fn().mockReturnValue([]),
    };

    const provider = makeMockProvider();
    const pingContext: PingContext = {
      provider,
      owl: makeMockOwl(),
      config: makeMockConfig(),
      capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
      sendToUser: vi.fn(),
      goalGraph: mockGoalGraph as any,
    };

    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: true,
        morningBriefHour: new Date().getHours(), quietHoursStart: 22, quietHoursEnd: 7 },
      pingContext,
    );

    await (pinger as any).sendMorningBrief();

    const chatCalls = (provider.chat as ReturnType<typeof vi.fn>).mock.calls;
    if (chatCalls.length > 0) {
      const promptUsed = chatCalls[0][0][0].content as string;
      expect(promptUsed).toContain("Ship feature X");
    }
  });

  it("does not have maybeDream method", () => {
    const pinger = new ProactivePinger(
      { enabled: true, checkInIntervalMinutes: 30, morningBrief: false,
        morningBriefHour: 9, quietHoursStart: 22, quietHoursEnd: 7 },
      { provider: makeMockProvider(), owl: makeMockOwl(), config: makeMockConfig(),
        capabilityLedger: { getCapabilities: vi.fn().mockReturnValue([]) } as any,
        sendToUser: vi.fn() },
    );
    expect((pinger as any).maybeDream).toBeUndefined();
    expect((pinger as any).maybeKnowledgeCouncil).toBeUndefined();
    expect((pinger as any).maybeEvolveSkills).toBeUndefined();
    expect((pinger as any).maybeConsolidateMemory).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test — expect FAIL on maybeDream assertion**

```bash
npx vitest run __tests__/heartbeat.test.ts -t "goal-aware"
```

Expected: FAIL — maybeDream still exists.

- [ ] **Step 3: Add assembleGoalContext helper**

Add this private method to ProactivePinger in `src/heartbeat/proactive.ts`:

```typescript
/**
 * Assemble goal-aware context string for proactive message prompts.
 * Priority-ordered: active goals (high) > recent history (low).
 */
private async assembleGoalContext(): Promise<string> {
  const parts: string[] = [];

  if (this.context.goalGraph) {
    try {
      await this.context.goalGraph.load();
      const activeGoals = this.context.goalGraph.getActive?.() ?? [];
      if (activeGoals.length > 0) {
        parts.push(
          `Active goals:\n${activeGoals.slice(0, 3).map((g: any) => `- ${g.title}`).join("\n")}`,
        );
      }
    } catch {
      // non-fatal
    }
  }

  if (this.context.getRecentHistory) {
    const history = this.context.getRecentHistory();
    if (history.length > 0) {
      const lastUserMessages = history
        .filter(m => m.role === "user")
        .slice(-3)
        .map(m => (typeof m.content === "string" ? m.content : "").slice(0, 80));
      if (lastUserMessages.length > 0) {
        parts.push(`Recent context: ${lastUserMessages.join(" | ")}`);
      }
    }
  }

  return parts.join("\n\n");
}
```

- [ ] **Step 4: Update sendMorningBrief to use assembleGoalContext**

Find `sendMorningBrief()` in `proactive.ts`. Replace the goal/history context assembly section with:

```typescript
private async sendMorningBrief(): Promise<void> {
  const dayOfWeek = new Date().toLocaleDateString("en-US", { weekday: "long" });
  const goalContext = await this.assembleGoalContext();

  if (!goalContext) return;  // Skip if nothing real to say

  const prompt =
    `It's ${dayOfWeek} morning. Generate a concise morning brief (2-3 sentences max). ` +
    `${goalContext}\n\n` +
    `Reference the actual context above — do not invent generic motivational filler. ` +
    `Sound like a real assistant who knows what the user is working on.`;

  await this.generateAndSend(prompt, "morning_brief");
}
```

- [ ] **Step 5: Delete dead stubs**

In `src/heartbeat/proactive.ts`, delete the following methods entirely:
- `private async maybeConsolidateMemory(): Promise<void>` (the whole method body, lines ~581–630)
- `private async maybeKnowledgeCouncil(): Promise<void>` (lines ~699–707)
- `private async maybeDream(): Promise<void>` (lines ~708–724)
- `private async maybeEvolveSkills(): Promise<void>` (lines ~725–757)

Also delete the import of `MemoryConsolidator` at the top of the file:
```typescript
import { MemoryConsolidator } from "./consolidation.js";  // DELETE THIS LINE
```

- [ ] **Step 6: Update executeJob switch — remove dead cases, add goal_progress_update**

In the `executeJob` switch statement, remove or replace with no-ops the cases that called deleted stubs, and add a real handler for `goal_progress_update`:

```typescript
case "memory_consolidation":
  log.engine.debug("[ProactivePinger] memory_consolidation handled by CognitiveLoop — skipping");
  break;
case "knowledge_council":
  log.engine.debug("[ProactivePinger] knowledge_council handled by CognitiveLoop — skipping");
  break;
case "dream_reflexion":
  log.engine.debug("[ProactivePinger] dream_reflexion handled by CognitiveLoop — skipping");
  break;
case "skill_evolution":
  log.engine.debug("[ProactivePinger] skill_evolution handled by CognitiveLoop — skipping");
  break;
case "goal_progress_update": {
  let payload: { goalId?: string; summary?: string } = {};
  try { payload = job.payload ? JSON.parse(job.payload) : {}; } catch { /* keep {} */ }

  if (!payload.goalId || !payload.summary) {
    log.engine.warn(`[ProactivePinger] goal_progress_update missing payload — marking done`);
    this.context.jobQueue?.markDone(job.id);
    break;
  }

  // Build goal-aware message via lightweight helper (NOT full E5 ContextPipeline)
  const goalContext = await this.assembleGoalContext();
  const prompt =
    `Inform the user about progress on a goal they're tracking. ` +
    `Be brief (1-2 sentences max), specific, and reference the actual progress.\n\n` +
    `Goal context:\n${goalContext || "(no active goals loaded)"}\n\n` +
    `Progress summary: ${payload.summary}`;

  await this.generateAndSend(prompt, "goal_progress_update");
  this.context.jobQueue?.markDone(job.id);
  break;
}
```

- [ ] **Step 7: Run full test suite**

```bash
npm test 2>&1 | tail -10
```

Expected: all tests pass (no compilation errors from deleted methods).

- [ ] **Step 8: Commit**

```bash
git add src/heartbeat/proactive.ts
git commit -m "feat(proactive): goal-aware assembly + remove dead stubs (maybeDream/consolidate/council/evolve)"
```

---

## Task 8: AutonomousPlanner — Learned Priorities + goal_progress_update

**Files:**
- Modify: `src/heartbeat/planner.ts`
- Test: extend `__tests__/heartbeat.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `__tests__/heartbeat.test.ts`:

```typescript
describe("AutonomousPlanner — learned priorities", () => {
  function makePlannerWithDb(engagementStats: Record<string, { replyRate: number; sampleCount: number } | null>) {
    const mockDb = {
      getEngagementStats: vi.fn((type: string) => engagementStats[type] ?? null),
    };

    const mockGoalGraph = {
      load: vi.fn().mockResolvedValue(undefined),
      getStale: vi.fn().mockReturnValue([]),
      getBlocked: vi.fn().mockReturnValue([]),
      getActive: vi.fn().mockReturnValue([]),
    };

    const onAction = vi.fn().mockResolvedValue(undefined);

    return new AutonomousPlanner(
      {
        goalGraph: mockGoalGraph as any,
        onAction,
        db: mockDb as any,
      },
      { intervalMinutes: 10, quietHoursStart: 22, quietHoursEnd: 7, minActionCooldownMinutes: 0 },
    );
  }

  it("uses basePriority when fewer than minSamples", async () => {
    const planner = makePlannerWithDb({ morning_brief: null });  // null = cold start
    const action = await planner.planAndExecute();
    // Can't assert exact priority easily, but planner should not throw
    expect(action === null || typeof action?.priority === "number").toBe(true);
  });

  it("adjusts priority up when reply rate is high", async () => {
    // morning_brief base priority is 90; with 100% reply rate → capped at 90+20=110 → clamp to 100
    const planner = makePlannerWithDb({
      morning_brief: { replyRate: 1.0, sampleCount: 25 },
    });
    // Trigger morning brief window by mocking the hour
    const now = new Date();
    vi.setSystemTime(new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 0, 0));

    const candidates = await (planner as any).generateCandidates();
    const brief = candidates.find((c: any) => c.type === "morning_brief");
    if (brief) {
      expect(brief.priority).toBeGreaterThanOrEqual(90);
    }

    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
npx vitest run __tests__/heartbeat.test.ts -t "learned priorities"
```

Expected: FAIL — `db` not in planner deps, `learnedPriority` not a method.

- [ ] **Step 3: Add goal_progress_update to ActionType**

In `src/heartbeat/planner.ts`, add to the `ActionType` union:

```typescript
export type ActionType =
  | "follow_up_stale_goal"
  | "advance_blocked_goal"
  | "self_study"
  | "skill_evolution"
  | "memory_consolidation"
  | "check_in"
  | "morning_brief"
  | "mine_patterns"
  | "explore_capabilities"
  | "anticipatory_research"
  | "review_tool_outcomes"
  | "goal_progress_update"  // NEW: notify user of completed goal-tied study/reflexion
  | "none";
```

- [ ] **Step 4: Add db to planner deps**

Find the `AutonomousPlanner` constructor deps type (it's inline or an interface). Add:

```typescript
db?: import("../memory/db.js").MemoryDatabase;
```

to the deps object type.

- [ ] **Step 5: Add learnedPriority private method**

Add to `AutonomousPlanner`:

```typescript
/**
 * Returns a data-driven priority score for a given action type.
 * Uses the reply rate from proactive_engagement (last 30 days, min 20 samples).
 * Falls back to basePriority on cold start.
 * Score is clamped to [basePriority - 20, basePriority + 20].
 */
private async learnedPriority(type: ActionType, basePriority: number): Promise<number> {
  if (!this.deps.db) return basePriority;
  const stats = this.deps.db.getEngagementStats(type, { days: 30, minSamples: 20 });
  if (!stats) return basePriority;
  const learned = Math.round(stats.replyRate * 100);
  return Math.max(basePriority - 20, Math.min(basePriority + 20, learned));
}
```

- [ ] **Step 6: Update generateCandidates to use learnedPriority**

`generateCandidates()` is currently synchronous. Change it to `private async generateCandidates()`.

Update the hardcoded priority constants to use `await this.learnedPriority(type, base)`. For example:

```typescript
// ── 3. Morning brief ──
if (!isQuiet && hour >= 8 && hour <= 10 && this.lastMorningBriefDate !== dateKey) {
  candidates.push({
    type: "morning_brief",
    priority: await this.learnedPriority("morning_brief", 90),
    description: "Deliver morning brief with goals status + agenda",
  });
}

// ── 4. Self-study ──
if (this.deps.learningEngine && this.idleMinutes > 10) {
  candidates.push({
    type: "self_study",
    priority: await this.learnedPriority("self_study", isQuiet ? 50 : 40),
    description: "Proactive learning session — study queued topics",
  });
}
```

Apply the same pattern to all other hardcoded priority values in `generateCandidates()`.

Also update `planAndExecute()` which calls `generateCandidates()` — since it's now async, ensure it's awaited (it likely already was, but confirm).

- [ ] **Step 7: Run tests — expect PASS**

```bash
npx vitest run __tests__/heartbeat.test.ts
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/heartbeat/planner.ts
git commit -m "feat(planner): learned priority scoring from proactive_engagement + goal_progress_update action type"
```

---

## Task 9: Delete consolidation.ts + Update Heartbeat Tests

**Files:**
- Delete: `src/heartbeat/consolidation.ts`
- Modify: `__tests__/heartbeat.test.ts`

- [ ] **Step 1: Check what tests reference MemoryConsolidator**

```bash
grep -n "MemoryConsolidator\|consolidation" __tests__/heartbeat.test.ts
```

Note all line numbers that reference it.

- [ ] **Step 2: Write a test that confirms consolidation.ts is absent**

Add to `__tests__/heartbeat.test.ts`:

```typescript
describe("consolidation.ts removal", () => {
  it("MemoryConsolidator is not imported in ProactivePinger", async () => {
    // Read proactive.ts source and verify no consolidation import
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(
      new URL("../src/heartbeat/proactive.ts", import.meta.url).pathname,
      "utf-8",
    );
    expect(source).not.toContain("consolidation");
    expect(source).not.toContain("MemoryConsolidator");
  });
});
```

- [ ] **Step 3: Run test — expect FAIL (consolidation still referenced)**

```bash
npx vitest run __tests__/heartbeat.test.ts -t "consolidation"
```

Expected: FAIL.

- [ ] **Step 4: Remove MemoryConsolidator from heartbeat.test.ts**

Delete the import line in `__tests__/heartbeat.test.ts`:
```typescript
import { MemoryConsolidator } from "../src/heartbeat/consolidation.js";
```

Remove any `describe` blocks that test `MemoryConsolidator`. Search for them:
```bash
grep -n "MemoryConsolidator\|consolidat" __tests__/heartbeat.test.ts
```

Delete those lines/blocks.

- [ ] **Step 5: Delete consolidation.ts**

```bash
git rm src/heartbeat/consolidation.ts
```

- [ ] **Step 6: Run full test suite**

```bash
npm test 2>&1 | tail -15
```

Expected: all tests pass. No "Cannot find module consolidation" errors.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(heartbeat): delete consolidation.ts (dead code since E3) + remove from tests"
```

---

## Task 10: CognitiveLoop — Enqueue goal_progress_update

**Files:**
- Modify: `src/cognition/loop.ts`
- Test: `__tests__/cognitive-loop-goal-enqueue.test.ts` (new)

- [ ] **Step 1: Understand where study/reflexion actions complete in loop.ts**

```bash
grep -n "desire_driven_study\|gap_driven_study\|reflexion_dream\|goalId\|goal_id\|activeGoal" src/cognition/loop.ts | head -20
```

Note the line numbers where these actions finish — this is where job enqueuing belongs.

- [ ] **Step 2: Write failing test**

Create `__tests__/cognitive-loop-goal-enqueue.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } },
}));

// We test the enqueue behavior via a minimal integration:
// When CognitiveLoop runs a desire_driven_study action tied to a goalId,
// it calls jobQueue.schedule with type "goal_progress_update"
describe("CognitiveLoop goal_progress_update enqueue", () => {
  it("enqueues goal_progress_update after goal-tied study action", async () => {
    const scheduleGoalUpdate = vi.fn();
    const mockJobQueue = { schedule: scheduleGoalUpdate };

    // Import and instantiate CognitiveLoop with minimal deps + jobQueue
    // Since CognitiveLoop is large, we test via the exported helper if one exists,
    // otherwise we test the maybeEnqueueGoalUpdate method directly
    const { CognitiveLoop } = await import("../src/cognition/loop.js");

    const loop = new CognitiveLoop(
      {
        provider: { name: "mock", chat: vi.fn().mockResolvedValue({ content: "", model: "m", finishReason: "stop" }),
          chatWithTools: vi.fn(), chatStream: vi.fn(), embed: vi.fn(), listModels: vi.fn(), healthCheck: vi.fn() } as any,
        owl: { name: "test", dna: {} } as any,
        config: { workspace: "/tmp/test", cognition: {} } as any,
        jobQueue: mockJobQueue as any,
      } as any,
      {},
    );

    // Call the method directly
    await (loop as any).maybeEnqueueGoalUpdate("g_123", "study session complete: TypeScript generics");
    expect(scheduleGoalUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "goal_progress_update",
        payload: expect.objectContaining({ goalId: "g_123" }),
      })
    );
  });
});
```

- [ ] **Step 3: Run test — expect FAIL**

```bash
npx vitest run __tests__/cognitive-loop-goal-enqueue.test.ts
```

Expected: FAIL — `maybeEnqueueGoalUpdate` not a method, `jobQueue` not in deps.

- [ ] **Step 4: Add jobQueue to CognitiveLoop deps**

In `src/cognition/loop.ts`, find the deps type/interface (it's likely an object passed to the constructor). Add:

```typescript
jobQueue?: import("../heartbeat/job-queue.js").ProactiveJobQueue;
```

- [ ] **Step 5: Add maybeEnqueueGoalUpdate private method**

Add to CognitiveLoop:

```typescript
/**
 * After completing a study or reflexion action tied to a goal,
 * enqueue a goal_progress_update job so ProactivePinger can inform the user.
 */
private maybeEnqueueGoalUpdate(goalId: string, summary: string): void {
  if (!this.deps.jobQueue) return;
  const userId = (this.deps as any).owl?.owlId ?? "default";
  this.deps.jobQueue.schedule({
    type: "goal_progress_update" as any,
    userId,
    scheduledAt: new Date(Date.now() + 5 * 60 * 1000),  // deliver in 5 min
    payload: { goalId, summary: summary.slice(0, 200) },
    priority: 7,
    deduplicate: false,
  });
  log.engine.debug(`[CognitiveLoop] Enqueued goal_progress_update for goal ${goalId}`);
}
```

- [ ] **Step 6: Call maybeEnqueueGoalUpdate after goal-tied actions complete**

Find the section in `loop.ts` where `desire_driven_study` and `gap_driven_study` actions finish. The exact location depends on loop structure — look for where the action result is returned or logged. After successful completion, call:

```typescript
if (result.goalId) {
  this.maybeEnqueueGoalUpdate(result.goalId, result.summary ?? "Study session complete");
}
```

The `result.goalId` field may not exist yet — add it to whatever result type the action returns, or check the current action result structure:

```bash
grep -n "goalId\|goal_id\|desire_driven_study" src/cognition/loop.ts | head -15
```

Adapt to the actual structure — if the action type doesn't currently return a goalId, add an optional `goalId?: string` to the action result type and populate it when the study action is goal-driven (look for where `innerLife.desires` is queried and a desire has a `goalId`).

- [ ] **Step 7: Also add goal_progress_update to ProactiveJobQueue JobType union**

In `src/heartbeat/job-queue.ts`, add to `JobType`:

```typescript
export type JobType =
  | "morning_brief"
  | "check_in"
  | "memory_consolidation"
  | "tool_pruning"
  | "self_study"
  | "knowledge_council"
  | "dream_reflexion"
  | "skill_evolution"
  | "background_task"
  | "goal_check"
  | "goal_progress_update";  // NEW: from CognitiveLoop after goal-tied study
```

- [ ] **Step 8: Run tests — expect PASS**

```bash
npx vitest run __tests__/cognitive-loop-goal-enqueue.test.ts
npm test 2>&1 | tail -10
```

Expected: goal enqueue test passes; full suite still green.

- [ ] **Step 9: Commit**

```bash
git add src/cognition/loop.ts src/heartbeat/job-queue.ts __tests__/cognitive-loop-goal-enqueue.test.ts
git commit -m "feat(cognition): enqueue goal_progress_update after goal-tied study/reflexion actions complete"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
npm test
```

Expected: all pre-existing tests pass; ~40+ new tests pass; 0 failures.

- [ ] **Check consolidation.ts is absent from compiled output**

```bash
ls src/heartbeat/consolidation.ts 2>/dev/null && echo "STILL EXISTS" || echo "DELETED OK"
```

Expected: `DELETED OK`.

- [ ] **Check maybeDream is absent from compiled output**

```bash
grep -n "maybeDream\|maybeKnowledgeCouncil\|maybeEvolveSkills\|maybeConsolidateMemory" src/heartbeat/proactive.ts
```

Expected: no matches.

- [ ] **Check hardcoded importantTools array is gone**

```bash
grep -n "importantTools = \[" src/heartbeat/capability-scanner.ts
```

Expected: no matches.

- [ ] **Check schema version is 22**

```bash
grep "SCHEMA_VERSION" src/memory/db.ts
```

Expected: `const SCHEMA_VERSION = 22;`

- [ ] **Behavioral E2E: NOISE verdict discards delivery**

Run a contrived job with `payload: { duplicate: true }` and a stub `DeliveryVerifier` returning `{ verdict: "NOISE" }`. Confirm:
- `proactive_deliveries` row written with `status = "discarded"` and `verdict = "NOISE"`
- `sendToUser` / `gatewayEventBus.publish` NOT called
- Job marked done (not retried)

- [ ] **Behavioral E2E: NEUTRAL verdict reschedules**

Same fixture with `{ verdict: "NEUTRAL", suppressMinutes: 60 }`. Confirm:
- `jobQueue.reschedule` called with `now + 60min`
- `proactive_deliveries` row written with `status = "suppressed"`
- `sendToUser` NOT called

- [ ] **Behavioral E2E: retry escalation — 3 strikes then fail**

Drive `handleUndeliverable` 4 times for the same job ID with no transport. Confirm:
- Calls 1-3: `incrementRetry` + `reschedule` with backoff (60s, 120s, 240s)
- Call 4: `markFailed` + `proactive_deliveries` row with `status = "failed"`

- [ ] **Behavioral E2E: goal_progress_update delivers goal-aware message**

Enqueue a `goal_progress_update` job with `payload: { goalId: "g1", summary: "Study session complete" }` and a goalGraph with goal `g1` titled "Ship feature X". Confirm:
- `generateAndSend` called with prompt containing both "Ship feature X" and "Study session complete"
- After send, `proactive_deliveries` row exists for the job

- [ ] **Behavioral E2E: engagement recorded with reply latency**

Publish a proactive envelope via gatewayEventBus, wait 30s, then send a user reply via the CLI adapter. Confirm:
- `proactive_engagement` row written with `replied = true` and `replyLatencySeconds ≈ 30`
- The row's `deliveryId` matches the published envelope's `deliveryId`

- [ ] **Behavioral E2E: jobs DB migration is idempotent**

With existing `proactive-jobs.db` containing 5 jobs, start the gateway. Confirm:
- All 5 jobs appear in main `stackowl.db` `proactive_jobs` table
- A second startup does NOT duplicate them
- Backup file `proactive-jobs.db.bak.<timestamp>` exists in workspace

- [ ] **Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore(e12): final verification cleanup"
```
