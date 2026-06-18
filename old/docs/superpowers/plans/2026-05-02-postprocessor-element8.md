# Element 8 — PostProcessor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild PostProcessor with a three-tier priority queue, universal error telemetry, zombie job removal, and two new context layers (KnowledgeGraph + PredictiveQueue) that close previously broken bidirectional feedback loops.

**Architecture:** All PostProcessor jobs are routed through `enqueueJob()` which maps tier names (`"critical" | "standard" | "background"`) to the existing `TaskPriority` (`"high" | "normal" | "low"`), wraps every job in try/catch, and records one row to `post_processor_job_runs` on completion. Three zombie enqueue calls are removed. `KnowledgeGraphLayer` and a new `PredictiveContextLayer` are wired to `ContextDependencies` so `knowledge-extract` and `predictive-prep` become genuine feedback loops.

**Tech Stack:** TypeScript, better-sqlite3, Vitest, existing TaskQueue/ContextPipeline/KnowledgeGraph/PredictiveQueue APIs.

**Spec note:** The spec lists `src/gateway/core.ts` as the ContextDependencies wiring point — the actual location is `src/gateway/handlers/context-builder.ts:68`. Plan implements the correct location.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/memory/db.ts` | Modify | Add schema v18 migration (`post_processor_job_runs` table) |
| `src/queue/task-queue.ts` | Modify | Add `activeHigh` counter, `drainCritical()` method |
| `src/gateway/handlers/post-processor.ts` | Modify | `enqueueJob()` + `TIER_PRIORITY`, convert all jobs, guards, zombie removal, `_lastSessionId` |
| `src/gateway/core.ts` | Modify | Await `taskQueue.drainCritical()` at start of `handleCore()` |
| `src/context/layer.ts` | Modify | Add `knowledgeGraph?`, `predictiveQueue?` to `ContextDependencies` |
| `src/gateway/handlers/context-builder.ts` | Modify | Wire `ctx.knowledgeGraph` + `ctx.predictiveQueue` into `deps` object |
| `src/knowledge/graph.ts` | Modify | Add `queryContext(userMessage: string): string` method |
| `src/context/layers/knowledge.ts` | Modify | Rewrite `KnowledgeGraphLayer.build()` to use `req.deps.knowledgeGraph` |
| `src/context/layers/predictive.ts` | Create | `PredictiveContextLayer` |
| `src/context/index.ts` | Modify | Register `PredictiveContextLayer` |

---

## Task 1: Schema v18 — `post_processor_job_runs` table

**Files:**
- Modify: `src/memory/db.ts` (lines ~29, ~1181, ~3115, ~3266)
- Test: `__tests__/memory/schema-v18.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/memory/schema-v18.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";

describe("schema v18 migration", () => {
  let db: InstanceType<typeof Database>;

  beforeEach(() => { db = new Database(":memory:"); });
  afterEach(() => { db.close(); });

  it("creates post_processor_job_runs table with correct columns", () => {
    applyAllMigrationsToRawDb(db);
    const cols = db.prepare(
      "PRAGMA table_info(post_processor_job_runs)"
    ).all() as { name: string }[];
    const names = cols.map(c => c.name);
    expect(names).toContain("job_name");
    expect(names).toContain("tier");
    expect(names).toContain("success");
    expect(names).toContain("error_code");
    expect(names).toContain("duration_ms");
    expect(names).toContain("user_id");
    expect(names).toContain("session_id");
    expect(names).toContain("ts");
  });

  it("creates idx_ppjr_job_ts and idx_ppjr_success indexes", () => {
    applyAllMigrationsToRawDb(db);
    const indexes = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='post_processor_job_runs'"
    ).all() as { name: string }[];
    const names = indexes.map(i => i.name);
    expect(names).toContain("idx_ppjr_job_ts");
    expect(names).toContain("idx_ppjr_success");
  });

  it("migration is idempotent — running twice does not throw", () => {
    applyAllMigrationsToRawDb(db);
    expect(() => applyAllMigrationsToRawDb(db)).not.toThrow();
  });
});
```

- [ ] **Step 2: Run to confirm it fails**

```bash
npx vitest run __tests__/memory/schema-v18.test.ts
```
Expected: FAIL — `post_processor_job_runs` table does not exist

- [ ] **Step 3: Bump SCHEMA_VERSION to 18**

In `src/memory/db.ts` line 29:
```typescript
const SCHEMA_VERSION = 18;
```

- [ ] **Step 4: Add `applyV18Migration` function**

Add after the existing `applyV17Migration` function (search for `function applyV17Migration`):

```typescript
function applyV18Migration(db: BetterSqlite3.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS post_processor_job_runs (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      job_name     TEXT    NOT NULL,
      tier         TEXT    NOT NULL,
      success      INTEGER NOT NULL,
      error_code   TEXT,
      duration_ms  INTEGER,
      user_id      TEXT,
      session_id   TEXT,
      ts           TEXT    DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_ppjr_job_ts
      ON post_processor_job_runs(job_name, ts);
    CREATE INDEX IF NOT EXISTS idx_ppjr_success
      ON post_processor_job_runs(success, ts);
  `);
}
```

- [ ] **Step 5: Wire migration in `MemoryDatabase.runMigrations()` (line ~1181)**

After the existing `if (current < 17)` block, add:
```typescript
if (current < 18) {
  applyV18Migration(this.db);
}
```

- [ ] **Step 6: Wire migration in `StackOwlDB.runMigrations()` (line ~3115)**

After the existing `if (current < 17)` block there:
```typescript
if (current < 18) {
  applyV18Migration(this.db);
  this.db.pragma(`user_version = 18`);
}
```

- [ ] **Step 7: Wire in `applyAllMigrationsToRawDb()` (line ~3266)**

After the existing `if (current < 17)` block:
```typescript
if (current < 18) {
  applyV18Migration(db);
}
db.pragma(`user_version = ${SCHEMA_VERSION}`);
```

- [ ] **Step 8: Run tests**

```bash
npx vitest run __tests__/memory/schema-v18.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 9: Run full suite for regressions**

```bash
npm test
```
Expected: all existing tests pass

- [ ] **Step 10: Commit**

```bash
git add src/memory/db.ts __tests__/memory/schema-v18.test.ts
git commit -m "feat(db): schema v18 — post_processor_job_runs telemetry table"
```

---

## Task 2: `TaskQueue.drainCritical()`

**Files:**
- Modify: `src/queue/task-queue.ts`
- Test: `__tests__/queue/task-queue-drain.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/queue/task-queue-drain.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("TaskQueue.drainCritical()", () => {
  it("resolves immediately when queue is empty", async () => {
    const q = new TaskQueue();
    await expect(q.drainCritical()).resolves.toBeUndefined();
  });

  it("resolves after all high-priority tasks complete", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    const order: string[] = [];
    q.enqueue("high-1", async () => { order.push("high-1"); }, "high");
    q.enqueue("high-2", async () => { order.push("high-2"); }, "high");
    await q.drainCritical();
    expect(order).toContain("high-1");
    expect(order).toContain("high-2");
  });

  it("does not wait for normal-priority tasks", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    let normalDone = false;
    q.enqueue("high-1", async () => {}, "high");
    q.enqueue("normal-1", async () => { normalDone = true; }, "normal");
    await q.drainCritical();
    // drainCritical must return as soon as high tasks are done,
    // not waiting for normal tasks
    expect(normalDone).toBe(false);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/queue/task-queue-drain.test.ts
```
Expected: FAIL — `drainCritical` is not a function

- [ ] **Step 3: Add `activeHigh` counter and `drainCritical()` to TaskQueue**

In `src/queue/task-queue.ts`, add `private activeHigh = 0;` alongside the existing `private active = 0;`:

```typescript
private active = 0;
private activeHigh = 0;
```

In `processNext()`, increment `activeHigh` when dequeuing a `"high"` task:
```typescript
private processNext(): void {
  while (this.active < this.config.concurrency && this.queue.length > 0) {
    const task = this.queue.shift()!;
    this.active++;
    if (task.priority === "high") this.activeHigh++;

    const startMs = Date.now();
    task
      .execute()
      .then(() => {
        this.stats.completed++;
        const elapsed = Date.now() - startMs;
        if (elapsed > 5000) {
          log.engine.info(
            `[TaskQueue] Task "${task.name}" completed in ${elapsed}ms`,
          );
        }
      })
      .catch((err) => {
        this.stats.failed++;
        const elapsed = Date.now() - startMs;
        const errMsg =
          err instanceof Error
            ? `${err.message}\n${err.stack ?? ""}`
            : String(err);
        log.engine.error(
          `[TaskQueue] Task "${task.name}" FAILED after ${elapsed}ms:\n${errMsg}`,
        );
      })
      .finally(() => {
        this.active--;
        if (task.priority === "high") this.activeHigh--;
        this.processNext();
      });
  }
}
```

Add `drainCritical()` after the existing `drain()` method:
```typescript
/** Wait until all high-priority tasks are dequeued and completed. */
async drainCritical(): Promise<void> {
  while (this.queue.some(t => t.priority === "high") || this.activeHigh > 0) {
    await new Promise((r) => setTimeout(r, 10));
  }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/queue/task-queue-drain.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 5: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/queue/task-queue.ts __tests__/queue/task-queue-drain.test.ts
git commit -m "feat(queue): add drainCritical() — drains only high-priority tasks"
```

---

## Task 3: `enqueueJob()` wrapper + `TIER_PRIORITY` map + `_lastSessionId`

This task adds the core plumbing to PostProcessor. It does NOT yet convert existing jobs — that's Task 4. This task is safe to ship independently.

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/gateway/post-processor-enqueue-job.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/gateway/post-processor-enqueue-job.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";
import { TaskQueue } from "../../src/queue/task-queue.js";

// Minimal GatewayContext stub for PostProcessor
function makeCtx(db?: InstanceType<typeof Database>) {
  return {
    owl: { persona: { name: "test-owl" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
    config: {},
    db: db ? { rawDb: db } : null,
  } as any;
}

describe("PostProcessor.enqueueJob()", () => {
  let rawDb: InstanceType<typeof Database>;

  beforeEach(() => {
    rawDb = new Database(":memory:");
    applyAllMigrationsToRawDb(rawDb);
  });

  it("records success row with correct tier in post_processor_job_runs", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = makeCtx(rawDb);
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    // Access private method via any cast for testing
    (pp as any)._lastProcessUserId = "user-1";
    (pp as any)._lastSessionId = "sess-1";
    await (pp as any).enqueueJobForTest("test-job", "critical", async () => {});
    await queue.drain();

    const rows = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE job_name='test-job'"
    ).all() as any[];
    expect(rows).toHaveLength(1);
    expect(rows[0].success).toBe(1);
    expect(rows[0].tier).toBe("critical");
    expect(rows[0].user_id).toBe("user-1");
  });

  it("records failure row with error_code when job throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = makeCtx(rawDb);
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    (pp as any)._lastProcessUserId = "user-2";
    (pp as any)._lastSessionId = "sess-2";
    await (pp as any).enqueueJobForTest("fail-job", "standard", async () => {
      throw new TypeError("oops");
    });
    await queue.drain();

    const rows = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE job_name='fail-job'"
    ).all() as any[];
    expect(rows[0].success).toBe(0);
    expect(rows[0].error_code).toBe("TypeError");
  });

  it("maps tier 'critical' to TaskPriority 'high'", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const enqueueSpy = vi.spyOn(queue, "enqueue");
    const ctx = makeCtx();
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    (pp as any).enqueueJobForTest("x", "critical", async () => {});
    expect(enqueueSpy).toHaveBeenCalledWith("x", expect.any(Function), "high");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/gateway/post-processor-enqueue-job.test.ts
```
Expected: FAIL

- [ ] **Step 3: Add `TIER_PRIORITY`, `_lastSessionId`, `enqueueJob()`, and `recordJobRun()` to PostProcessor**

In `src/gateway/handlers/post-processor.ts`, add after the imports and before the class:

```typescript
import type { TaskPriority } from "../../queue/task-queue.js";

const TIER_PRIORITY: Record<"critical" | "standard" | "background", TaskPriority> = {
  critical: "high",
  standard: "normal",
  background: "low",
};
```

Inside the `PostProcessor` class, add `private _lastSessionId: string | null = null;` alongside the existing `private _lastProcessUserId = "";`.

Add these two private methods to the class:

```typescript
private enqueueJob(
  name: string,
  tier: "critical" | "standard" | "background",
  fn: () => Promise<void>,
): void {
  const priority = TIER_PRIORITY[tier];
  this.taskQueue.enqueue(name, async () => {
    const start = Date.now();
    try {
      await fn();
      this.recordJobRun(name, tier, true, Date.now() - start);
    } catch (err) {
      const code = err instanceof Error ? err.constructor.name : "unknown";
      log.engine.warn(
        `[PostProcessor:${name}] Failed: ${err instanceof Error ? err.message : err}`,
      );
      this.recordJobRun(name, tier, false, Date.now() - start, code);
    }
  }, priority);
}

private recordJobRun(
  name: string,
  tier: string,
  success: boolean,
  durationMs: number,
  errorCode?: string,
): void {
  if (!this.ctx.db) return;
  try {
    this.ctx.db.rawDb.prepare(
      `INSERT INTO post_processor_job_runs
       (job_name, tier, success, error_code, duration_ms, user_id, session_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      name,
      tier,
      success ? 1 : 0,
      errorCode ?? null,
      durationMs,
      this._lastProcessUserId || null,
      this._lastSessionId || null,
    );
  } catch {
    // telemetry must never crash the caller
  }
}

// Test-only alias — exposes enqueueJob for unit testing
private enqueueJobForTest = this.enqueueJob.bind(this);
```

- [ ] **Step 4: Capture `_lastSessionId` at top of `process()`**

At the top of `process()` where `_lastProcessUserId` is set (line ~97), add:
```typescript
this._lastProcessUserId = metadata?.userId ?? "";
this._lastSessionId = sessionId ?? null;
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/gateway/post-processor-enqueue-job.test.ts
```
Expected: 3 PASS

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/gateway/post-processor-enqueue-job.test.ts
git commit -m "feat(post-processor): add enqueueJob() wrapper, TIER_PRIORITY map, _lastSessionId"
```

---

## Task 4: Convert all existing `taskQueue.enqueue()` calls to `enqueueJob()`

This is the largest mechanical task. It converts every `this.taskQueue.enqueue(name, fn)` call to `this.enqueueJob(name, tier, fn)` with the tier assignment from the spec.

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/gateway/post-processor-tiers.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/post-processor-tiers.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("PostProcessor job tier assignments", () => {
  it("digest-update enqueued with 'high' priority", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
      digestManager: { update: vi.fn().mockResolvedValue(undefined) },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess-1", { userId: "u1" });
    const digestCall = spy.mock.calls.find(c => c[0] === "digest-update");
    expect(digestCall).toBeDefined();
    expect(digestCall![2]).toBe("high");
  });

  it("dna-evolve enqueued with 'low' priority when interval fires", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const mockEvolutionEngine = { evolve: vi.fn().mockResolvedValue(true) };
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: { owlDna: { evolutionBatchSize: 1 } }, // fires on message 1
      db: null,
      evolutionEngine: mockEvolutionEngine,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess-1", { userId: "u1" });
    const evolveCall = spy.mock.calls.find(c => c[0].startsWith("dna-evolve"));
    expect(evolveCall).toBeDefined();
    expect(evolveCall![2]).toBe("low");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/gateway/post-processor-tiers.test.ts
```
Expected: FAIL — jobs still enqueued with default `"normal"` priority

- [ ] **Step 3: Convert all jobs — replace `this.taskQueue.enqueue(name, fn)` with `this.enqueueJob(name, tier, fn)`**

Apply these conversions in `src/gateway/handlers/post-processor.ts`:

| Old call | New tier |
|----------|----------|
| `this.taskQueue.enqueue("sentiment-challenge-update", ...)` | `"critical"` |
| `this.taskQueue.enqueue("learning-orchestrator", ...)` | `"standard"` |
| `this.taskQueue.enqueue("learning", ...)` | `"standard"` |
| `this.taskQueue.enqueue(\`dna-evolve(...)\`, ...)` | `"background"` |
| `this.taskQueue.enqueue("inner-life-dna-sync", ...)` | `"background"` |
| `this.taskQueue.enqueue("coordinator-save", ...)` | `"standard"` |
| `this.taskQueue.enqueue("dna-preference-feedback", ...)` | `"background"` |
| `this.taskQueue.enqueue("anticipation", ...)` | `"background"` |
| `this.taskQueue.enqueue("knowledge-extract", ...)` | `"background"` *(keep for now — zombie removal in Task 5)* |
| `this.taskQueue.enqueue("fact-extract", ...)` | `"standard"` |
| `this.taskQueue.enqueue("memory-decay", ...)` | `"standard"` |
| `this.taskQueue.enqueue("compress", ...)` | `"standard"` |
| `this.taskQueue.enqueue("digest-update", ...)` | `"critical"` |
| `this.taskQueue.enqueue("success-recipe", ...)` | `"standard"` |
| `this.taskQueue.enqueue("reflexion-write", ...)` | `"standard"` |
| `this.taskQueue.enqueue("quality-reflexion", ...)` | `"standard"` |
| `this.taskQueue.enqueue("pattern-save", ...)` | `"background"` |
| `this.taskQueue.enqueue("trust-save", ...)` | `"background"` |
| `this.taskQueue.enqueue("predictive-prep", ...)` | `"background"` |
| `this.taskQueue.enqueue("sleep-consolidation", ...)` | `"standard"` |
| `this.taskQueue.enqueue("gap-feedback", ...)` | `"standard"` |
| `this.taskQueue.enqueue("timeline-snapshot", ...)` | `"background"` *(keep for now — zombie removal in Task 5)* |
| Inside `maybeExtractGoals`: `this.taskQueue.enqueue("goal-extraction", ...)` | `"background"` *(zombie removal in Task 5)* |

The conversion pattern is mechanical. For example, change:
```typescript
this.taskQueue.enqueue("digest-update", async () => {
  try {
    await this.ctx.digestManager!.update(sessionId, messages);
  } catch (err) {
    log.engine.warn(...);
  }
});
```
to:
```typescript
this.enqueueJob("digest-update", "critical", async () => {
  await this.ctx.digestManager!.update(sessionId, messages);
});
```
(The try/catch is now handled by `enqueueJob()` itself — remove inner try/catch from jobs that only do `log.warn` on failure. Keep try/catch only where the inner catch has meaningful recovery logic beyond logging.)

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/gateway/post-processor-tiers.test.ts
```
Expected: 2 PASS

- [ ] **Step 5: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/gateway/post-processor-tiers.test.ts
git commit -m "feat(post-processor): convert all jobs to enqueueJob() with tier assignments"
```

---

## Task 5: Decision 9 — synchronous call guards + Decision 8 null guard

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/gateway/post-processor-guards.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/gateway/post-processor-guards.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("PostProcessor synchronous call guards", () => {
  it("process() completes even when coordinator.processMessage throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const crashCoordinator = {
      processMessage: () => { throw new Error("coordinator crash"); },
      save: vi.fn(),
      getMicroLearnerProfile: vi.fn().mockReturnValue({}),
      gateEvolution: vi.fn().mockReturnValue(null),
      flushHighConfidencePrefs: vi.fn().mockReturnValue([]),
      recordMutationStart: vi.fn(),
      recordMutationEnd: vi.fn(),
    } as any;
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, crashCoordinator, null, null);
    // Must not throw
    expect(() =>
      pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" })
    ).not.toThrow();
  });

  it("process() completes even when sentimentProbe throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    // Corrupt sentimentProbe to throw
    (pp as any).sentimentProbe = { onNextMessage: () => { throw new Error("probe crash"); }, arm: () => {} };
    expect(() =>
      pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" })
    ).not.toThrow();
  });

  it("PostProcessor constructs without crash when ctx.db is null", () => {
    // Verifies Decision 8: bare ! assertion removed from sentimentProbe callback
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    expect(() => new PostProcessor(ctx, queue, null, null, null, null)).not.toThrow();
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/gateway/post-processor-guards.test.ts
```
Expected: FAIL — throws propagate through `process()`

- [ ] **Step 3: Wrap `sentimentProbe.onNextMessage` / `arm` calls**

In `process()`, around the `sentimentProbe.onNextMessage` / `arm` block (~lines 99–112):
```typescript
if (this.sentimentProbe) {
  try {
    const lastUserContent =
      messages.findLast?.((m) => m.role === "user")?.content ??
      [...messages].reverse().find((m) => m.role === "user")?.content ??
      "";
    const textToClassify =
      typeof lastUserContent === "string" ? lastUserContent : "";
    this.sentimentProbe.onNextMessage(textToClassify);
    this.sentimentProbe.arm(metadata?.userId ?? "default");
  } catch (err) {
    log.engine.warn(
      `[PostProcessor:sentimentProbe] Failed: ${err instanceof Error ? err.message : err}`,
    );
  }
}
```

- [ ] **Step 4: Wrap `coordinator.processMessage()` call (~line 313)**

```typescript
if (lastUserMsg) {
  try {
    this.coordinator.processMessage(
      lastUserMsg.content,
      toolsUsed.length > 0 ? toolsUsed : undefined,
      metadata?.channelId,
    );
  } catch (err) {
    log.engine.warn(
      `[PostProcessor:coordinator.processMessage] Failed: ${err instanceof Error ? err.message : err}`,
    );
  }
}
```

- [ ] **Step 5: Wrap `patternAnalyzer.recordAction()` / `enrichFromProfile()` (~lines 472–485)**

```typescript
if (this.ctx.patternAnalyzer) {
  const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
  if (lastUserMsg) {
    try {
      this.ctx.patternAnalyzer.recordAction(
        lastUserMsg.content.slice(0, 100),
        [],
      );
    } catch (err) {
      log.engine.warn(
        `[PostProcessor:patternAnalyzer.recordAction] Failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
  if (this.coordinator && this.messageCount % 15 === 0) {
    try {
      const profile = this.coordinator.getMicroLearnerProfile();
      this.ctx.patternAnalyzer.enrichFromProfile(profile);
    } catch (err) {
      log.engine.warn(
        `[PostProcessor:patternAnalyzer.enrichFromProfile] Failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }
}
```

- [ ] **Step 6: Fix Decision 8 — null guard on sentimentProbe DB write**

In the constructor's SentimentProbe callback (~line 51), change:
```typescript
// before
this.ctx.db!.rawDb.prepare(`UPDATE outcome_journal ...`).run(this._lastProcessUserId);
// after
this.ctx.db?.rawDb?.prepare(`UPDATE outcome_journal ...`)?.run(this._lastProcessUserId);
```

- [ ] **Step 7: Run tests**

```bash
npx vitest run __tests__/gateway/post-processor-guards.test.ts
```
Expected: 3 PASS

- [ ] **Step 8: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/gateway/post-processor-guards.test.ts
git commit -m "fix(post-processor): add guards for synchronous calls — coordinator, patternAnalyzer, sentimentProbe"
```

---

## Task 6: Zombie job removal

Remove `timeline-snapshot`, `goal-extraction`, and the old `knowledge-extract` (5-message interval). Re-add `knowledge-extract` at 10-message BACKGROUND interval via `enqueueJob()`.

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`
- Test: `__tests__/gateway/post-processor-zombie-removal.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/gateway/post-processor-zombie-removal.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

function makeCtx(overrides: Record<string, unknown> = {}) {
  return {
    owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
    config: {},
    db: null,
    ...overrides,
  } as any;
}

describe("Zombie job removal", () => {
  it("timeline-snapshot is never enqueued", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx({ timelineManager: { autoSnapshot: vi.fn().mockReturnValue(true), save: vi.fn() } });
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      // Run 10 messages to cover interval conditions
      for (let i = 0; i < 10; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const calls = spy.mock.calls.map(c => c[0]);
      expect(calls).not.toContain("timeline-snapshot");
    });
  });

  it("goal-extraction is never enqueued", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx();
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      for (let i = 0; i < 5; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const calls = spy.mock.calls.map(c => c[0]);
      expect(calls).not.toContain("goal-extraction");
    });
  });

  it("setGoalExtractor method does not exist", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const pp = new PostProcessor(makeCtx(), queue, null, null, null, null);
    expect((pp as any).setGoalExtractor).toBeUndefined();
  });

  it("knowledge-extract fires at message 10 with 'low' priority", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx({
        knowledgeReasoner: { extractFromConversation: vi.fn().mockResolvedValue(undefined) },
        knowledgeGraph: { save: vi.fn().mockResolvedValue(undefined) },
      });
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      for (let i = 0; i < 10; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const kgCalls = spy.mock.calls.filter(c => c[0] === "knowledge-extract");
      expect(kgCalls.length).toBe(1);
      expect(kgCalls[0][2]).toBe("low");
    });
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/gateway/post-processor-zombie-removal.test.ts
```
Expected: FAIL

- [ ] **Step 3: Remove `timeline-snapshot` enqueue block**

Delete the entire `if (this.ctx.timelineManager && sessionId)` block that enqueues `timeline-snapshot` (approx lines 411–421 in current file).

- [ ] **Step 4: Remove `maybeExtractGoals()` method and `setGoalExtractor()` and `_goalExtractor` field**

Delete:
- Private field `private _goalExtractor`
- Method `setGoalExtractor()`
- Method `maybeExtractGoals()`
- The call to `this.maybeExtractGoals(...)` in `process()`

- [ ] **Step 5: Remove the old `knowledge-extract` block (5-message interval)**

Delete the `if (this.ctx.knowledgeReasoner && messages.length > 0 && this.messageCount % 5 === 0)` block that enqueues `"knowledge-extract"`.

- [ ] **Step 6: Add new `knowledge-extract` at 10-message BACKGROUND interval**

After the `fact-extract` block, add:
```typescript
// Knowledge extraction (every 10 messages, BACKGROUND — feeds KnowledgeGraphLayer)
if (
  this.ctx.knowledgeReasoner &&
  messages.length > 0 &&
  this.messageCount % 10 === 0
) {
  this.enqueueJob("knowledge-extract", "background", async () => {
    await this.ctx.knowledgeReasoner!.extractFromConversation(messages);
    await this.ctx.knowledgeGraph?.save();
  });
}
```

- [ ] **Step 7: Run tests**

```bash
npx vitest run __tests__/gateway/post-processor-zombie-removal.test.ts
```
Expected: 4 PASS

- [ ] **Step 8: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/gateway/post-processor-zombie-removal.test.ts
git commit -m "feat(post-processor): remove zombie jobs, re-add knowledge-extract as BACKGROUND at 10-message interval"
```

---

## Task 7: `drainCritical()` call in `handleCore()`

**Files:**
- Modify: `src/gateway/core.ts` (line ~949 `handleCore`)
- Test: existing integration tests cover this implicitly; add assertion in integration test (Task 11)

- [ ] **Step 1: Add `await this.taskQueue.drainCritical()` at start of `handleCore()`**

In `src/gateway/core.ts`, inside `handleCore()` immediately after the method opens (line ~949), before the greeting-reset check:

```typescript
private async handleCore(
  message: GatewayMessage,
  callbacks: GatewayCallbacks,
): Promise<GatewayResponse> {
  // Drain CRITICAL (high-priority) PostProcessor jobs from the previous turn
  // before building context so digest-update is guaranteed to have completed.
  await this.taskQueue.drainCritical();

  // Greeting-reset check: ...
```

- [ ] **Step 2: Run full suite**

```bash
npm test
```
Expected: all pass (no new tests needed — integration coverage in Task 11)

- [ ] **Step 3: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat(core): await taskQueue.drainCritical() at start of handleCore() — guarantees digest before next LLM call"
```

---

## Task 8: `KnowledgeGraph.queryContext()` method

**Files:**
- Modify: `src/knowledge/graph.ts`
- Test: `__tests__/knowledge/graph-query-context.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/knowledge/graph-query-context.test.ts
import { describe, it, expect } from "vitest";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";

describe("KnowledgeGraph.queryContext()", () => {
  it("returns empty string when graph has no nodes", () => {
    const kg = new KnowledgeGraph("/tmp");
    expect(kg.queryContext("anything")).toBe("");
  });

  it("returns top-3 matching nodes as formatted string", () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "TypeScript basics", content: "TypeScript is a typed superset of JavaScript", domain: "programming", type: "concept", confidence: 0.9, tags: [] });
    kg.addNode({ title: "React hooks", content: "React hooks allow state in function components", domain: "programming", type: "concept", confidence: 0.8, tags: [] });
    kg.addNode({ title: "Cooking pasta", content: "Boil water, add salt, cook pasta", domain: "cooking", type: "fact", confidence: 0.7, tags: [] });
    kg.addNode({ title: "TypeScript generics", content: "Generics provide type parameters", domain: "programming", type: "concept", confidence: 0.85, tags: [] });

    const result = kg.queryContext("TypeScript");
    expect(result).toContain("TypeScript basics");
    expect(result).toContain("TypeScript generics");
    expect(result).not.toContain("Cooking pasta");
    // At most 3 results
    const titleMatches = (result.match(/title=/g) ?? []).length;
    expect(titleMatches).toBeLessThanOrEqual(3);
  });

  it("returns empty string when no nodes match the query", () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "React hooks", content: "hooks", domain: "programming", type: "concept", confidence: 0.8, tags: [] });
    expect(kg.queryContext("completely unrelated xyz")).toBe("");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/knowledge/graph-query-context.test.ts
```
Expected: FAIL — `queryContext` is not a function

- [ ] **Step 3: Add `queryContext()` to `KnowledgeGraph`**

In `src/knowledge/graph.ts`, add after the existing `search()` method:

```typescript
queryContext(userMessage: string): string {
  const results = this.search(userMessage, 3);
  if (results.length === 0) return "";
  const lines = results.map(
    (n) => `  <node title="${n.title}">${n.content.slice(0, 200)}</node>`,
  );
  return lines.join("\n");
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/knowledge/graph-query-context.test.ts
```
Expected: 3 PASS

- [ ] **Step 5: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/knowledge/graph.ts __tests__/knowledge/graph-query-context.test.ts
git commit -m "feat(knowledge): add queryContext() method for ContextLayer integration"
```

---

## Task 9: `ContextDependencies` + `KnowledgeGraphLayer` rewrite

**Files:**
- Modify: `src/context/layer.ts`
- Modify: `src/context/layers/knowledge.ts`
- Modify: `src/gateway/handlers/context-builder.ts` (line ~68)
- Test: `__tests__/context/knowledge-graph-layer.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/context/knowledge-graph-layer.test.ts
import { describe, it, expect } from "vitest";
import { KnowledgeGraphLayer } from "../../src/context/layers/knowledge.js";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";

function makeReq(kg?: KnowledgeGraph) {
  return {
    session: { messages: [] },
    callbacks: {},
    continuityResult: null,
    digest: null,
    deps: { sessionStore: {} as any, config: {} as any, knowledgeGraph: kg },
  } as any;
}

const triage = {
  userMessage: "TypeScript",
  isConversational: false,
  hasFrustration: false,
  isOpinionRequest: false,
  hasTemporalTrigger: false,
  isReturningUser: false,
  sessionDepth: 1,
  hasActiveItems: false,
  effectiveUserId: "u1",
  continuityClass: null,
} as any;

describe("KnowledgeGraphLayer", () => {
  it("returns empty string when deps.knowledgeGraph is absent", async () => {
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(undefined), triage, new Map());
    expect(result).toBe("");
  });

  it("returns empty string when graph has no matching nodes", async () => {
    const kg = new KnowledgeGraph("/tmp");
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(kg), triage, new Map());
    expect(result).toBe("");
  });

  it("returns <knowledge_graph> block when nodes match", async () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "TypeScript basics", content: "TS is typed JS", domain: "prog", type: "concept", confidence: 0.9, tags: [] });
    const layer = new KnowledgeGraphLayer();
    const result = await layer.build(makeReq(kg), triage, new Map());
    expect(result).toContain("<knowledge_graph>");
    expect(result).toContain("TypeScript basics");
    expect(result).toContain("</knowledge_graph>");
  });

  it("does NOT read from (req.session as any).knowledgeGraphContext", async () => {
    const layer = new KnowledgeGraphLayer();
    const req = makeReq(undefined);
    (req.session as any).knowledgeGraphContext = "STALE_CAST_DATA";
    const result = await layer.build(req, triage, new Map());
    // Should be empty because deps.knowledgeGraph is absent, not reading the cast
    expect(result).toBe("");
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/context/knowledge-graph-layer.test.ts
```
Expected: FAIL — layer still reads from `req.session as any`

- [ ] **Step 3: Add `knowledgeGraph` and `predictiveQueue` to `ContextDependencies`**

In `src/context/layer.ts`:

```typescript
import type { KnowledgeGraph } from "../knowledge/graph.js";
import type { PredictiveQueue } from "../predictive/queue.js";

export interface ContextDependencies {
  intelligenceRouter?: IntelligenceRouter;
  pelletStore?: PelletStore;
  memoryBus?: MemoryBus;
  sessionStore: SessionStore;
  eventBus?: EventBus;
  config: StackOwlConfig;
  knowledgeGraph?: KnowledgeGraph;
  predictiveQueue?: PredictiveQueue;
}
```

- [ ] **Step 4: Rewrite `KnowledgeGraphLayer.build()`**

In `src/context/layers/knowledge.ts`:

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class KnowledgeGraphLayer implements ContextLayer {
  name = "KnowledgeGraphLayer";
  priority = 110;
  maxTokens = 300;
  produces = ["knowledge"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.userMessage.slice(0, 40) + "kg");
  }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const kg = req.deps.knowledgeGraph;
    if (!kg) return "";
    const ctx = kg.queryContext(t.userMessage);
    if (!ctx) return "";
    return `<knowledge_graph>\n${ctx}\n</knowledge_graph>`;
  }
}
```

- [ ] **Step 5: Wire `knowledgeGraph` and `predictiveQueue` into `deps` in `context-builder.ts`**

In `src/gateway/handlers/context-builder.ts`, update the `deps` object (~line 68):

```typescript
const deps = {
  intelligenceRouter: this.ctx.intelligence,
  pelletStore: this.ctx.pelletStore,
  memoryBus: this.ctx.memoryBus,
  sessionStore: this.ctx.sessionStore,
  eventBus: this.ctx.eventBus,
  config: this.ctx.config,
  knowledgeGraph: this.ctx.knowledgeGraph,
  predictiveQueue: this.ctx.predictiveQueue,
};
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/context/knowledge-graph-layer.test.ts
```
Expected: 4 PASS

- [ ] **Step 7: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/context/layer.ts src/context/layers/knowledge.ts src/gateway/handlers/context-builder.ts __tests__/context/knowledge-graph-layer.test.ts
git commit -m "feat(context): wire KnowledgeGraph into ContextDependencies — KnowledgeGraphLayer no longer reads session cast"
```

---

## Task 10: `PredictiveContextLayer`

**Files:**
- Create: `src/context/layers/predictive.ts`
- Modify: `src/context/index.ts`
- Test: `__tests__/context/predictive-layer.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/context/predictive-layer.test.ts
import { describe, it, expect } from "vitest";
import { PredictiveContextLayer } from "../../src/context/layers/predictive.js";

function makeReq(predictiveQueue?: any) {
  return {
    session: { messages: [] },
    callbacks: {},
    continuityResult: null,
    digest: null,
    deps: { sessionStore: {} as any, config: {} as any, predictiveQueue },
  } as any;
}

const triage = {
  userMessage: "what should I do next?",
  isConversational: false,
  hasFrustration: false,
  isOpinionRequest: false,
  hasTemporalTrigger: false,
  isReturningUser: false,
  sessionDepth: 3,
  hasActiveItems: false,
  effectiveUserId: "u1",
  continuityClass: null,
} as any;

describe("PredictiveContextLayer", () => {
  it("returns empty string when deps.predictiveQueue is absent", async () => {
    const layer = new PredictiveContextLayer();
    expect(await layer.build(makeReq(undefined), triage, new Map())).toBe("");
  });

  it("returns empty string when no ready tasks", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = { getReadyTasks: () => [] };
    expect(await layer.build(makeReq(mockQueue), triage, new Map())).toBe("");
  });

  it("returns <predicted_next> block with up to 3 tasks sorted by confidence", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = {
      getReadyTasks: () => [
        { action: "Check calendar", confidence: 0.9, status: "ready" },
        { action: "Review PRs", confidence: 0.7, status: "ready" },
        { action: "Send standup", confidence: 0.8, status: "ready" },
        { action: "Low priority task", confidence: 0.5, status: "ready" },
      ],
    };
    const result = await layer.build(makeReq(mockQueue), triage, new Map());
    expect(result).toContain("<predicted_next>");
    expect(result).toContain("Check calendar");
    expect(result).toContain("confidence=\"0.9\"");
    // Only top 3
    const taskCount = (result.match(/<task /g) ?? []).length;
    expect(taskCount).toBe(3);
    expect(result).not.toContain("Low priority task");
    expect(result).toContain("</predicted_next>");
  });

  it("shouldFire returns false for conversational messages", () => {
    const layer = new PredictiveContextLayer();
    expect(layer.shouldFire({ ...triage, isConversational: true })).toBe(false);
  });
});
```

- [ ] **Step 2: Run to confirm failure**

```bash
npx vitest run __tests__/context/predictive-layer.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create `src/context/layers/predictive.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class PredictiveContextLayer implements ContextLayer {
  name = "PredictiveContextLayer";
  priority = 90;
  maxTokens = 200;
  produces = ["predicted_tasks"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(): string | null { return null; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const queue = req.deps.predictiveQueue;
    if (!queue) return "";
    const ready = queue.getReadyTasks()
      .sort((a: { confidence: number }, b: { confidence: number }) => b.confidence - a.confidence)
      .slice(0, 3);
    if (ready.length === 0) return "";
    const lines = ["<predicted_next>"];
    for (const t of ready) {
      lines.push(`  <task confidence="${t.confidence}">${t.action}</task>`);
    }
    lines.push("</predicted_next>");
    return lines.join("\n");
  }
}
```

- [ ] **Step 4: Register `PredictiveContextLayer` in `src/context/index.ts`**

Add import:
```typescript
import { PredictiveContextLayer } from "./layers/predictive.js";
```

Add to the `layers` array in `createContextPipeline()`, after `KnowledgeGraphLayer`:
```typescript
new PredictiveContextLayer(),
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/context/predictive-layer.test.ts
```
Expected: 4 PASS

- [ ] **Step 6: Run full suite**

```bash
npm test
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/context/layers/predictive.ts src/context/index.ts __tests__/context/predictive-layer.test.ts
git commit -m "feat(context): add PredictiveContextLayer — wires PredictiveQueue into system prompt"
```

---

## Task 11: Integration test + progress tracker update

**Files:**
- Create: `__tests__/integration/post-processor-element8.test.ts`
- Modify: `docs/platform-audit/progress.md`

- [ ] **Step 1: Write integration test**

```typescript
// __tests__/integration/post-processor-element8.test.ts
import { describe, it, expect, vi } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";
import { TaskQueue } from "../../src/queue/task-queue.js";
import { PostProcessor } from "../../src/gateway/handlers/post-processor.js";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";
import { KnowledgeGraphLayer } from "../../src/context/layers/knowledge.js";
import { PredictiveContextLayer } from "../../src/context/layers/predictive.js";

function makeDb() {
  const db = new Database(":memory:");
  applyAllMigrationsToRawDb(db);
  return db;
}

describe("Element 8 integration", () => {
  it("failed job writes error_code row to post_processor_job_runs", async () => {
    const rawDb = makeDb();
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: { rawDb },
      digestManager: {
        update: vi.fn().mockRejectedValue(new RangeError("digest failed")),
      },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hello" }], "sess-x", { userId: "u-x" });
    await queue.drain();

    const failures = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE success=0"
    ).all() as any[];
    expect(failures.length).toBeGreaterThanOrEqual(1);
    const digestFailure = failures.find((r: any) => r.job_name === "digest-update");
    expect(digestFailure).toBeDefined();
    expect(digestFailure.error_code).toBe("RangeError");
  });

  it("CRITICAL job (digest-update) is enqueued with 'high' priority", () => {
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
      digestManager: { update: vi.fn().mockResolvedValue(undefined) },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
    const digestCall = spy.mock.calls.find(c => c[0] === "digest-update");
    expect(digestCall![2]).toBe("high");
  });

  it("KnowledgeGraphLayer reads from deps.knowledgeGraph (not session cast)", async () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "Node A", content: "About topic A", domain: "d", type: "concept", confidence: 0.9, tags: [] });
    const layer = new KnowledgeGraphLayer();
    const req = {
      session: { messages: [] },
      callbacks: {},
      continuityResult: null,
      digest: null,
      deps: { sessionStore: {} as any, config: {} as any, knowledgeGraph: kg },
    } as any;
    const result = await layer.build(req, { userMessage: "topic A", isConversational: false } as any, new Map());
    expect(result).toContain("Node A");
    expect(result).toContain("<knowledge_graph>");
  });

  it("PredictiveContextLayer returns predicted tasks", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = {
      getReadyTasks: () => [
        { action: "Check standup", confidence: 0.85, status: "ready" },
      ],
    };
    const req = {
      session: { messages: [] },
      callbacks: {},
      continuityResult: null,
      digest: null,
      deps: { sessionStore: {} as any, config: {} as any, predictiveQueue: mockQueue },
    } as any;
    const result = await layer.build(req, { userMessage: "what next", isConversational: false } as any, new Map());
    expect(result).toContain("<predicted_next>");
    expect(result).toContain("Check standup");
  });

  it("drainCritical() resolves after high-priority tasks complete", async () => {
    const queue = new TaskQueue({ concurrency: 1 });
    const completed: string[] = [];
    queue.enqueue("high-job", async () => { completed.push("high"); }, "high");
    queue.enqueue("normal-job", async () => { completed.push("normal"); }, "normal");
    await queue.drainCritical();
    expect(completed).toContain("high");
    expect(completed).not.toContain("normal");
  });
});
```

- [ ] **Step 2: Run integration tests**

```bash
npx vitest run __tests__/integration/post-processor-element8.test.ts
```
Expected: 5 PASS

- [ ] **Step 3: Run full suite**

```bash
npm test
```
Expected: ≥668 tests pass, 0 failures

- [ ] **Step 4: Update progress tracker**

In `docs/platform-audit/progress.md`, update Element 8 row:

```markdown
| 8 | PostProcessor (save, learn, evolve, queue) | 🔧 reviewed — improvements committed | 2026-05-02 |
```

Add a new section below Element 7 in the progress tracker:

```markdown
## Element 8: PostProcessor — Priority Pipeline, Bidirectional Wiring & Telemetry

### Status: 🔧 Implemented + merged

### Scope
`src/gateway/handlers/post-processor.ts`, `src/queue/task-queue.ts`,
`src/memory/db.ts` (schema v18), `src/context/layer.ts`,
`src/context/layers/knowledge.ts`, `src/context/layers/predictive.ts` (new),
`src/context/index.ts`, `src/gateway/handlers/context-builder.ts`,
`src/knowledge/graph.ts`, `src/gateway/core.ts`

### Findings
- 23 PostProcessor jobs with no priority system; slow dna-evolve blocked fast digest-update
- 11/23 jobs had no error handling — silent failures
- 4 zombie jobs (knowledge-extract, timeline-snapshot, goal-extraction, predictive-prep) wrote to storage but no context layer ever read the output
- 3 synchronous calls (coordinator.processMessage, patternAnalyzer.recordAction, sentimentProbe) had no guard — any crash aborted process()
- KnowledgeGraphLayer read from (req.session as any).knowledgeGraphContext — a cast never populated
- PredictiveQueue had no context layer at all

### Improvements Implemented
- **Three-tier TaskQueue**: CRITICAL(high) / STANDARD(normal) / BACKGROUND(low) — drainCritical() awaited in handleCore() before next LLM call
- **Schema v18**: post_processor_job_runs telemetry table — every job records success/failure/duration
- **enqueueJob() wrapper**: all jobs converted, error telemetry automatic, no more silent failures
- **Decision 9 guards**: try/catch on coordinator.processMessage, patternAnalyzer, sentimentProbe arm/onNextMessage
- **Decision 8 null guard**: ctx.db!.rawDb → ctx.db?.rawDb optional chaining
- **Zombie removal**: timeline-snapshot, goal-extraction (+ setGoalExtractor, maybeExtractGoals) removed; knowledge-extract re-added at 10-message BACKGROUND interval
- **KnowledgeGraphLayer**: rewritten to read from req.deps.knowledgeGraph via new queryContext() method — genuinely bidirectional
- **PredictiveContextLayer**: new — reads getReadyTasks() from PredictiveQueue, injects <predicted_next> block into system prompt
- **ContextDependencies**: knowledgeGraph + predictiveQueue wired from GatewayContext via context-builder.ts

### Schema
- v18: post_processor_job_runs(job_name, tier, success, error_code, duration_ms, user_id, session_id, ts)

### Bidirectionality map: 21 active jobs, all with confirmed read-back paths
- Spec: docs/superpowers/specs/2026-05-02-postprocessor-element8-design.md
```

- [ ] **Step 5: Commit**

```bash
git add __tests__/integration/post-processor-element8.test.ts docs/platform-audit/progress.md
git commit -m "test(integration): Element 8 integration tests + progress tracker update"
```
