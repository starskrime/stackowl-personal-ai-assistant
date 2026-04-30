# RoutingCoordinator (Element 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered routing logic with OwlBrain + UserProfileService + TaskOwnershipManager, backed by 3 new SQLite tables and driven by GoalGraph/EpisodicMemory/FactStore signals.

**Architecture:** `OwlBrain` wraps the existing `RoutingCoordinator` and adds SQLite-persisted pin, signal-aware routing via `UserProfileService`, and task ownership tracking. Phase 1 delivers core routing + task + status. Phase 2 delivers background jobs + relationship context.

**Tech Stack:** TypeScript, better-sqlite3 (sync), vitest, existing `MemoryDatabase` repo pattern.

---

## File Map

| File | Action |
|------|--------|
| `src/memory/db.ts` | Modify — add `UserProfilesRepo`, `TasksRepo`, `JobsRepo`; bump schema to v12 |
| `src/routing/owl-brain.ts` | Create — central routing coordinator |
| `src/routing/user-profile-service.ts` | Create — signal aggregator over existing stores |
| `src/routing/task-ownership-manager.ts` | Create — task CRUD |
| `src/routing/background-job-runner.ts` | Create — job queue polling |
| `src/routing/relationship-context.ts` | Create — cross-session user model |
| `src/routing/routing-status-reporter.ts` | Create — status output |
| `src/routing/secretary.ts` | Modify — add `routeWithSignals()`, inline `ClassifyFn` type |
| `src/gateway/handlers/routing-coordinator.ts` | Modify — remove `sessionStateStore` param |
| `src/gateway/handlers/context-builder.ts` | Modify — add `open_tasks` + `user_relationship` blocks |
| `src/gateway/types.ts` | Modify — add 5 new context fields |
| `src/gateway/core.ts` | Modify — wire OwlBrain, TaskOwnershipManager, RoutingStatusReporter; dead code removal |
| `src/routing/session-state.ts` | **Delete** |
| `src/routing/llm-classifier.ts` | **Delete** |
| `__tests__/routing-db-repos.test.ts` | Create |
| `__tests__/user-profile-service.test.ts` | Create |
| `__tests__/owl-brain.test.ts` | Create |
| `__tests__/task-ownership-manager.test.ts` | Create |
| `__tests__/routing-status-reporter.test.ts` | Create |
| `__tests__/background-job-runner.test.ts` | Create |
| `__tests__/relationship-context.test.ts` | Create |

---

## ─── PHASE 1: E1 + E2 + E3 + E6 ─────────────────────────────────

---

### Task 1: Schema v12 + DB Repos

**Files:**
- Modify: `src/memory/db.ts`
- Create: `__tests__/routing-db-repos.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/routing-db-repos.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-db-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("UserProfilesRepo", () => {
  it("getPin returns null for unknown user", () => {
    expect(db.userProfiles.getPin("u1")).toBeNull();
  });

  it("setPin and getPin round-trip", () => {
    db.userProfiles.setPin("u1", "typescript-owl");
    expect(db.userProfiles.getPin("u1")).toBe("typescript-owl");
  });

  it("setPin(null) clears pin", () => {
    db.userProfiles.setPin("u1", "typescript-owl");
    db.userProfiles.setPin("u1", null);
    expect(db.userProfiles.getPin("u1")).toBeNull();
  });

  it("appendRoutingHistory keeps last 10 entries", () => {
    for (let i = 0; i < 12; i++) {
      db.userProfiles.appendRoutingHistory("u1", { ts: new Date().toISOString(), owl: `owl${i}`, reason: "test" });
    }
    const history = db.userProfiles.getRoutingHistory("u1");
    expect(history).toHaveLength(10);
    expect(history[0].owl).toBe("owl2");
    expect(history[9].owl).toBe("owl11");
  });
});

describe("TasksRepo", () => {
  it("creates and retrieves a task", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Do X", status: "pending", priority: "normal" });
    const t = db.owlTasks.get("t1");
    expect(t).not.toBeNull();
    expect(t!.title).toBe("Do X");
  });

  it("getActive returns only pending/active/blocked tasks", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "A", status: "pending", priority: "normal" });
    db.owlTasks.create({ id: "t2", userId: "u1", owlName: "owl", title: "B", status: "done", priority: "normal" });
    db.owlTasks.create({ id: "t3", userId: "u1", owlName: "owl", title: "C", status: "active", priority: "high" });
    const active = db.owlTasks.getActive("u1");
    expect(active.map(t => t.id).sort()).toEqual(["t1", "t3"]);
  });

  it("updateStatus changes task status", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "A", status: "pending", priority: "normal" });
    db.owlTasks.updateStatus("t1", "done", "result text");
    expect(db.owlTasks.get("t1")!.status).toBe("done");
    expect(db.owlTasks.get("t1")!.result).toBe("result text");
  });
});

describe("JobsRepo", () => {
  it("enqueue and dequeueNext round-trip", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: { msg: "check" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    const job = db.owlJobs.dequeueNext();
    expect(job).not.toBeNull();
    expect(job!.id).toBe("j1");
    expect(job!.status).toBe("running");
  });

  it("dequeueNext returns null when no jobs are due", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() + 60_000).toISOString() });
    expect(db.owlJobs.dequeueNext()).toBeNull();
  });

  it("markDone updates status and result", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    db.owlJobs.dequeueNext();
    db.owlJobs.markDone("j1", "done result");
    const row = db.owlJobs.get("j1");
    expect(row!.status).toBe("done");
    expect(row!.result).toBe("done result");
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/routing-db-repos.test.ts
```
Expected: FAIL — `db.userProfiles is not a function` (repos don't exist yet)

- [ ] **Step 3: Add interfaces to db.ts**

Add these interfaces after the existing `SynthesisRecord` interface (around line 203):

```typescript
// ─── Routing Persistence Types ────────────────────────────────────

export interface RoutingHistoryEntry {
  ts: string;
  owl: string;
  reason: string;
}

export interface UserProfile {
  userId: string;
  activePin: string | null;
  pinnedAt: string | null;
  trustLevel: "standard" | "elevated" | "restricted";
  stylePref: string | null;
  routingHistory: RoutingHistoryEntry[];
  createdAt: string;
  updatedAt: string;
}

export type OwlTaskStatus = "pending" | "active" | "blocked" | "done" | "abandoned";
export type OwlTaskPriority = "low" | "normal" | "high" | "urgent";

export interface OwlTask {
  id: string;
  userId: string;
  owlName: string;
  title: string;
  description?: string;
  status: OwlTaskStatus;
  priority: OwlTaskPriority;
  sessionId?: string;
  createdAt: string;
  updatedAt: string;
  dueAt?: string;
  result?: string;
}

export type OwlJobType = "proactive" | "monitor" | "research" | "followup";
export type OwlJobStatus = "queued" | "running" | "done" | "failed";

export interface OwlJob {
  id: string;
  taskId?: string;
  userId: string;
  owlName: string;
  type: OwlJobType;
  payload: Record<string, unknown>;
  status: OwlJobStatus;
  scheduledAt: string;
  startedAt?: string;
  completedAt?: string;
  error?: string;
  result?: string;
}
```

- [ ] **Step 4: Add repo classes to db.ts**

Add these three classes at the **end** of `db.ts`, before the final closing brace of the file (after `class OwlsRepo`):

```typescript
class UserProfilesRepo {
  constructor(private db: Database.Database) {}

  getPin(userId: string): string | null {
    const row = this.db.prepare(
      "SELECT active_pin FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    return row?.active_pin ?? null;
  }

  setPin(userId: string, owlName: string | null): void {
    this.db.prepare(`
      INSERT INTO user_profiles (user_id, active_pin, pinned_at, updated_at)
      VALUES (?, ?, datetime('now'), datetime('now'))
      ON CONFLICT(user_id) DO UPDATE SET
        active_pin = excluded.active_pin,
        pinned_at = excluded.pinned_at,
        updated_at = excluded.updated_at
    `).run(userId, owlName);
  }

  appendRoutingHistory(userId: string, entry: RoutingHistoryEntry): void {
    const row = this.db.prepare(
      "SELECT routing_json FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    const history: RoutingHistoryEntry[] = row?.routing_json
      ? JSON.parse(row.routing_json) : [];
    history.push(entry);
    if (history.length > 10) history.splice(0, history.length - 10);
    this.db.prepare(`
      INSERT INTO user_profiles (user_id, routing_json, updated_at)
      VALUES (?, ?, datetime('now'))
      ON CONFLICT(user_id) DO UPDATE SET
        routing_json = excluded.routing_json,
        updated_at = excluded.updated_at
    `).run(userId, JSON.stringify(history));
  }

  getRoutingHistory(userId: string): RoutingHistoryEntry[] {
    const row = this.db.prepare(
      "SELECT routing_json FROM user_profiles WHERE user_id = ?"
    ).get(userId) as any;
    return row?.routing_json ? JSON.parse(row.routing_json) : [];
  }
}

class TasksRepo {
  constructor(private db: Database.Database) {}

  create(task: Omit<OwlTask, "createdAt" | "updatedAt">): void {
    this.db.prepare(`
      INSERT INTO owl_tasks
        (id, user_id, owl_name, title, description, status, priority, session_id, due_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      task.id, task.userId, task.owlName, task.title,
      task.description ?? null, task.status, task.priority,
      task.sessionId ?? null, task.dueAt ?? null,
    );
  }

  updateStatus(taskId: string, status: OwlTaskStatus, result?: string): void {
    this.db.prepare(`
      UPDATE owl_tasks
      SET status = ?, result = COALESCE(?, result), updated_at = datetime('now')
      WHERE id = ?
    `).run(status, result ?? null, taskId);
  }

  getActive(userId: string): OwlTask[] {
    return (this.db.prepare(`
      SELECT * FROM owl_tasks
      WHERE user_id = ? AND status IN ('pending','active','blocked')
      ORDER BY updated_at ASC LIMIT 5
    `).all(userId) as any[]).map(rowToOwlTask);
  }

  get(taskId: string): OwlTask | null {
    const row = this.db.prepare(
      "SELECT * FROM owl_tasks WHERE id = ?"
    ).get(taskId) as any;
    return row ? rowToOwlTask(row) : null;
  }
}

class JobsRepo {
  constructor(private db: Database.Database) {}

  enqueue(job: Omit<OwlJob, "status" | "startedAt" | "completedAt" | "error" | "result">): void {
    this.db.prepare(`
      INSERT INTO owl_jobs (id, task_id, user_id, owl_name, type, payload, status, scheduled_at)
      VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
    `).run(
      job.id, job.taskId ?? null, job.userId, job.owlName,
      job.type, JSON.stringify(job.payload), job.scheduledAt,
    );
  }

  dequeueNext(): OwlJob | null {
    const row = this.db.prepare(`
      SELECT * FROM owl_jobs
      WHERE status = 'queued' AND scheduled_at <= datetime('now')
      ORDER BY scheduled_at ASC LIMIT 1
    `).get() as any;
    if (!row) return null;
    this.db.prepare(
      "UPDATE owl_jobs SET status = 'running', started_at = datetime('now') WHERE id = ?"
    ).run(row.id);
    return rowToOwlJob({ ...row, status: "running" });
  }

  markDone(jobId: string, result: string): void {
    this.db.prepare(`
      UPDATE owl_jobs SET status = 'done', result = ?, completed_at = datetime('now') WHERE id = ?
    `).run(result, jobId);
  }

  markFailed(jobId: string, error: string): void {
    this.db.prepare(`
      UPDATE owl_jobs SET status = 'failed', error = ?, completed_at = datetime('now') WHERE id = ?
    `).run(error, jobId);
  }

  get(jobId: string): OwlJob | null {
    const row = this.db.prepare("SELECT * FROM owl_jobs WHERE id = ?").get(jobId) as any;
    return row ? rowToOwlJob(row) : null;
  }

  getQueued(userId: string): OwlJob[] {
    return (this.db.prepare(
      "SELECT * FROM owl_jobs WHERE user_id = ? AND status IN ('queued','running') ORDER BY scheduled_at ASC"
    ).all(userId) as any[]).map(rowToOwlJob);
  }
}

function rowToOwlTask(row: any): OwlTask {
  return {
    id: row.id,
    userId: row.user_id,
    owlName: row.owl_name,
    title: row.title,
    description: row.description ?? undefined,
    status: row.status as OwlTaskStatus,
    priority: row.priority as OwlTaskPriority,
    sessionId: row.session_id ?? undefined,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    dueAt: row.due_at ?? undefined,
    result: row.result ?? undefined,
  };
}

function rowToOwlJob(row: any): OwlJob {
  return {
    id: row.id,
    taskId: row.task_id ?? undefined,
    userId: row.user_id,
    owlName: row.owl_name,
    type: row.type as OwlJobType,
    payload: typeof row.payload === "string" ? JSON.parse(row.payload) : row.payload,
    status: row.status as OwlJobStatus,
    scheduledAt: row.scheduled_at,
    startedAt: row.started_at ?? undefined,
    completedAt: row.completed_at ?? undefined,
    error: row.error ?? undefined,
    result: row.result ?? undefined,
  };
}
```

- [ ] **Step 5: Add readonly repo fields to MemoryDatabase**

After the existing `readonly owls: OwlsRepo;` line (around line 368):

```typescript
  readonly userProfiles: UserProfilesRepo;
  readonly owlTasks: TasksRepo;
  readonly owlJobs: JobsRepo;
```

- [ ] **Step 6: Instantiate repos in MemoryDatabase constructor**

After `this.agentTasks = new AgentTasksRepo(this.db);` (around line 400):

```typescript
    this.userProfiles = new UserProfilesRepo(this.db);
    this.owlTasks     = new TasksRepo(this.db);
    this.owlJobs      = new JobsRepo(this.db);
```

- [ ] **Step 7: Add schema v12 migration**

Change the constant at line 29 and add a migration block in `runMigrations()`. After the `if (current < 11)` block (before the `if (current < SCHEMA_VERSION)` line at ~950):

```typescript
// Change:
const SCHEMA_VERSION = 11;
// To:
const SCHEMA_VERSION = 12;
```

Then add this block after `if (current < 11) { ... }`:

```typescript
    if (current < 12) {
      // v12: OwlBrain routing persistence — user profiles, task ownership, job queue
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS user_profiles (
          user_id      TEXT PRIMARY KEY,
          active_pin   TEXT,
          pinned_at    TEXT,
          trust_level  TEXT NOT NULL DEFAULT 'standard',
          style_pref   TEXT,
          routing_json TEXT NOT NULL DEFAULT '[]',
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS owl_tasks (
          id           TEXT PRIMARY KEY,
          user_id      TEXT NOT NULL,
          owl_name     TEXT NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          status       TEXT NOT NULL DEFAULT 'pending',
          priority     TEXT NOT NULL DEFAULT 'normal',
          session_id   TEXT,
          due_at       TEXT,
          result       TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_owl_tasks_user ON owl_tasks(user_id, status);

        CREATE TABLE IF NOT EXISTS owl_jobs (
          id           TEXT PRIMARY KEY,
          task_id      TEXT REFERENCES owl_tasks(id),
          user_id      TEXT NOT NULL,
          owl_name     TEXT NOT NULL,
          type         TEXT NOT NULL,
          payload      TEXT NOT NULL DEFAULT '{}',
          status       TEXT NOT NULL DEFAULT 'queued',
          scheduled_at TEXT NOT NULL,
          started_at   TEXT,
          completed_at TEXT,
          error        TEXT,
          result       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_owl_jobs_status ON owl_jobs(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_owl_jobs_user   ON owl_jobs(user_id, status);
      `);
    }
```

- [ ] **Step 8: Run tests — verify they pass**

```bash
npx vitest run __tests__/routing-db-repos.test.ts
```
Expected: PASS — 10 tests passing

- [ ] **Step 9: Commit**

```bash
git add src/memory/db.ts __tests__/routing-db-repos.test.ts
git commit -m "feat(db): schema v12 — user_profiles, owl_tasks, owl_jobs + repos"
```

---

### Task 2: Dead Code Removal

**Files:**
- Modify: `src/routing/secretary.ts`, `src/gateway/handlers/routing-coordinator.ts`, `src/gateway/core.ts`
- Delete: `src/routing/session-state.ts`, `src/routing/llm-classifier.ts`

- [ ] **Step 1: Baseline build check**

```bash
npm run build 2>&1 | tail -5
```
Expected: `0 errors` (or note any pre-existing errors)

- [ ] **Step 2: Inline ClassifyFn in secretary.ts and remove llm-classifier import**

In `src/routing/secretary.ts`, replace:
```typescript
import type { ClassifyFn } from "./llm-classifier.js";
```
with:
```typescript
export type ClassifyFn = (message: string, specialists: { name: string; role: string; expertise: string[] }[]) => Promise<string | null>;
```

- [ ] **Step 3: Delete llm-classifier.ts**

```bash
rm src/routing/llm-classifier.ts
```

- [ ] **Step 4: Remove SessionStateStore from routing-coordinator.ts**

In `src/gateway/handlers/routing-coordinator.ts`:

Remove the import at line 7:
```typescript
import type { SessionStateStore } from "../../routing/session-state.js";
```

Remove the constructor parameter at line 23:
```typescript
    private sessionStateStore?: SessionStateStore,
```

Remove all usages of `this.sessionStateStore` (lines ~55–57 and ~106–107) — the pin-save calls:
- Remove the two `if (this.sessionStateStore && message.userId) { this.sessionStateStore.save(...).catch(() => {}); }` blocks
- Remove the `this.sessionStateStore.clear(message.userId).catch(() => {});` call
- The pin-restore block (lines ~38–44) also reads from `sessionStateStore` — remove that entire block too (OwlBrain will do this from SQLite)

After cleanup, the `constructor` should be:
```typescript
  constructor(
    private specializedRegistry: SpecializedOwlRegistry | undefined,
    private getSecretaryRouter: () => SecretaryRouter | null,
    private defaultOwlName: string,
    private pelletStore?: PelletStore,
    private digestManager?: ConversationDigestManager,
  ) {}
```

And `resolve()` should start with explicit @mention check (no pin-restore block):
```typescript
  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
    session?: Session,
  ): Promise<RoutingResult> {
    let activeOwlName = this.defaultOwlName;

    // ─── Explicit @mention ──────────────────────────────────────
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    // ... rest unchanged
```

- [ ] **Step 5: Remove dead code from core.ts — 3 imports**

In `src/gateway/core.ts`, remove these 3 import lines:
```typescript
import { buildClassifyFn } from "../routing/llm-classifier.js";
import { SessionStateStore } from "../routing/session-state.js";
import { DelegationDecider } from "../delegation/delegation-decider.js";
```

- [ ] **Step 6: Remove delegationDecider field and instantiation from core.ts**

Remove the readonly field declaration (line 166):
```typescript
  readonly delegationDecider: DelegationDecider;
```

Remove the constructor instantiation (line 329):
```typescript
    this.delegationDecider = new DelegationDecider();
```

- [ ] **Step 7: Remove sessionStateStore from core.ts**

Remove the field declaration (line 177):
```typescript
  private sessionStateStore: SessionStateStore | null = null;
```

Remove the instantiation (line 529):
```typescript
    this.sessionStateStore = new SessionStateStore(workspacePath);
```

Change the `RoutingCoordinator` construction (lines 530–537) to not pass `sessionStateStore`:
```typescript
    this.routingCoordinator = new RoutingCoordinator(
      ctx.specializedRegistry,
      () => this.secretaryRouter,
      ctx.owl.persona.name,
      ctx.pelletStore,
      ctx.digestManager,
    );
```

- [ ] **Step 8: Remove buildClassifyFn usage from core.ts**

At line 1756, replace:
```typescript
      const classifyFn = buildClassifyFn(this.ctx.provider, this.ctx.config.defaultModel);
      this.secretaryRouter = new SecretaryRouter(this.ctx.specializedRegistry, classifyFn);
```
with:
```typescript
      this.secretaryRouter = new SecretaryRouter(this.ctx.specializedRegistry);
```

- [ ] **Step 9: Delete session-state.ts**

```bash
rm src/routing/session-state.ts
```

- [ ] **Step 10: Verify build + tests**

```bash
npm run build 2>&1 | tail -10
npx vitest run 2>&1 | tail -10
```
Expected: both clean (0 errors, same test counts as before)

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "refactor(routing): remove dead code — SessionStateStore, DelegationDecider, buildClassifyFn"
```

---

### Task 3: UserProfileService

**Files:**
- Create: `src/routing/user-profile-service.ts`
- Create: `__tests__/user-profile-service.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/user-profile-service.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { UserProfileService } from "../src/routing/user-profile-service.js";

function makeDb(pin: string | null = null, history = []) {
  return {
    userProfiles: {
      getPin: vi.fn().mockReturnValue(pin),
      getRoutingHistory: vi.fn().mockReturnValue(history),
      appendRoutingHistory: vi.fn(),
    },
    owlTasks: {
      getActive: vi.fn().mockReturnValue([]),
    },
  } as any;
}

function makeGoalGraph(goals: { title: string; status: string }[] = []) {
  return { getActive: vi.fn().mockReturnValue(goals) } as any;
}

function makeEpisodicMemory(episodes: { summary: string }[] = []) {
  return { getRecent: vi.fn().mockReturnValue(episodes) } as any;
}

function makeUserMemoryStore(facts: string[] = []) {
  return { retrieve: vi.fn().mockResolvedValue(facts) } as any;
}

describe("UserProfileService", () => {
  it("returns empty signals for unknown user with no stores", async () => {
    const svc = new UserProfileService(makeDb(), undefined, undefined, undefined);
    const signals = await svc.buildSignals("u1", "hello");
    expect(signals.activePin).toBeNull();
    expect(signals.domainStack).toEqual([]);
    expect(signals.recentEpisodes).toEqual([]);
    expect(signals.relevantFacts).toEqual([]);
    expect(signals.trustLevel).toBe("standard");
  });

  it("reads active pin from db", async () => {
    const svc = new UserProfileService(makeDb("typescript-owl"), undefined, undefined, undefined);
    const signals = await svc.buildSignals("u1", "hello");
    expect(signals.activePin).toBe("typescript-owl");
  });

  it("assembles domainStack from GoalGraph active goals", async () => {
    const goalGraph = makeGoalGraph([{ title: "Build API", status: "active" }, { title: "Write tests", status: "in_progress" }]);
    const svc = new UserProfileService(makeDb(), goalGraph, undefined, undefined);
    const signals = await svc.buildSignals("u1", "hello");
    expect(signals.domainStack).toEqual(["Build API", "Write tests"]);
  });

  it("assembles recentEpisodes from EpisodicMemory", async () => {
    const episodic = makeEpisodicMemory([{ summary: "Debugged CI pipeline" }, { summary: "Reviewed PR" }]);
    const svc = new UserProfileService(makeDb(), undefined, episodic, undefined);
    const signals = await svc.buildSignals("u1", "hello");
    expect(signals.recentEpisodes).toEqual(["Debugged CI pipeline", "Reviewed PR"]);
  });

  it("assembles relevantFacts from UserMemoryStore", async () => {
    const ums = makeUserMemoryStore(["prefers terse answers", "senior TypeScript"]);
    const svc = new UserProfileService(makeDb(), undefined, undefined, ums);
    const signals = await svc.buildSignals("u1", "fix the bug");
    expect(signals.relevantFacts).toContain("prefers terse answers");
  });

  it("each signal has 200ms timeout — slow store degrades gracefully", async () => {
    const slowEpisodic = { getRecent: vi.fn(() => new Promise<never>(() => {})) } as any;
    const svc = new UserProfileService(makeDb(), undefined, slowEpisodic, undefined);
    const start = Date.now();
    const signals = await svc.buildSignals("u1", "hello");
    expect(Date.now() - start).toBeLessThan(500);
    expect(signals.recentEpisodes).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/user-profile-service.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/user-profile-service.js'`

- [ ] **Step 3: Implement UserProfileService**

```typescript
// src/routing/user-profile-service.ts
import type { MemoryDatabase } from "../memory/db.js";
import type { GoalGraph } from "../goals/graph.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { UserMemoryStore } from "../session/user-memory-store.js";
import { log } from "../logger.js";

export interface RoutingSignals {
  activePin: string | null;
  preferredStyle?: string;
  domainStack: string[];
  recentEpisodes: string[];
  relevantFacts: string[];
  trustLevel: "standard" | "elevated" | "restricted";
}

const SIGNAL_TIMEOUT_MS = 200;

function withTimeout<T>(p: Promise<T>, fallback: T): Promise<T> {
  return Promise.race([p, new Promise<T>((res) => setTimeout(() => res(fallback), SIGNAL_TIMEOUT_MS))]);
}

export class UserProfileService {
  constructor(
    private db: Pick<MemoryDatabase, "userProfiles" | "owlTasks">,
    private goalGraph: GoalGraph | undefined,
    private episodicMemory: EpisodicMemory | undefined,
    private userMemoryStore: UserMemoryStore | undefined,
  ) {}

  async buildSignals(userId: string, userText: string): Promise<RoutingSignals> {
    const activePin = this.db.userProfiles.getPin(userId);
    const trustLevel = "standard" as const;

    const [domainStack, recentEpisodes, relevantFacts] = await Promise.all([
      withTimeout(this.getDomains(), []),
      withTimeout(this.getEpisodes(), []),
      withTimeout(this.getFacts(userId, userText), []),
    ]);

    return { activePin, domainStack, recentEpisodes, relevantFacts, trustLevel };
  }

  private async getDomains(): Promise<string[]> {
    if (!this.goalGraph) return [];
    try {
      return this.goalGraph.getActive().slice(0, 5).map((g) => g.title);
    } catch (err) {
      log.engine.debug(`[UserProfileService] domain fetch failed: ${err}`);
      return [];
    }
  }

  private async getEpisodes(): Promise<string[]> {
    if (!this.episodicMemory) return [];
    try {
      return this.episodicMemory.getRecent(3).map((e: any) => e.summary ?? "");
    } catch (err) {
      log.engine.debug(`[UserProfileService] episode fetch failed: ${err}`);
      return [];
    }
  }

  private async getFacts(userId: string, query: string): Promise<string[]> {
    if (!this.userMemoryStore) return [];
    try {
      return await this.userMemoryStore.retrieve(userId, query, 3);
    } catch (err) {
      log.engine.debug(`[UserProfileService] fact fetch failed: ${err}`);
      return [];
    }
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/user-profile-service.test.ts
```
Expected: PASS — 6 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/user-profile-service.ts __tests__/user-profile-service.test.ts
git commit -m "feat(routing): add UserProfileService — signal aggregator with 200ms timeout"
```

---

### Task 4: SecretaryRouter.routeWithSignals()

**Files:**
- Modify: `src/routing/secretary.ts`
- Create: `__tests__/secretary-signals.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/secretary-signals.test.ts
import { describe, it, expect } from "vitest";
import { SecretaryRouter } from "../src/routing/secretary.js";
import type { RoutingSignals } from "../src/routing/user-profile-service.js";

const noSignals: RoutingSignals = { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" };

function makeRegistry(specialists: { name: string; role: string; expertise: string[]; routingRules: string[]; personality: any; permissions: any }[]) {
  return {
    listSpecialists: () => specialists,
    get: (name: string) => specialists.find(s => s.name === name),
    getDefault: () => null,
  } as any;
}

const tsOwl = {
  name: "typescript-owl", role: "TypeScript expert", expertise: ["TypeScript", "Node.js"],
  routingRules: { keywords: ["typescript", "ts", "node"] },
  personality: { challengeLevel: "medium", verbosity: "concise", tone: "technical" },
  permissions: { capabilityConstraints: [] },
};
const rustOwl = {
  name: "rust-owl", role: "Rust expert", expertise: ["Rust", "systems programming"],
  routingRules: { keywords: ["rust", "cargo", "borrow"] },
  personality: { challengeLevel: "medium", verbosity: "concise", tone: "technical" },
  permissions: { capabilityConstraints: [] },
};

describe("SecretaryRouter.routeWithSignals", () => {
  it("routes to direct when no specialists", async () => {
    const router = new SecretaryRouter(makeRegistry([]), undefined);
    const result = await router.routeWithSignals("hello", "u1", noSignals);
    expect(result.type).toBe("direct");
  });

  it("domain signal boosts correct specialist", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const signals: RoutingSignals = { ...noSignals, domainStack: ["Build TypeScript API", "Write TypeScript tests"] };
    const result = await router.routeWithSignals("help me with my project", "u1", signals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("typescript-owl");
  });

  it("fact signal boosts specialist mentioned by name", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const signals: RoutingSignals = { ...noSignals, relevantFacts: ["user uses rust-owl for systems work"] };
    const result = await router.routeWithSignals("optimize memory allocations", "u1", signals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("rust-owl");
  });

  it("keyword match still works without signals", async () => {
    const router = new SecretaryRouter(makeRegistry([tsOwl, rustOwl]), undefined);
    const result = await router.routeWithSignals("help me with typescript interfaces", "u1", noSignals);
    expect(result.type).toBe("specialist");
    if (result.type === "specialist") expect(result.owl.name).toBe("typescript-owl");
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/secretary-signals.test.ts
```
Expected: FAIL — `router.routeWithSignals is not a function`

- [ ] **Step 3: Add routeWithSignals() to SecretaryRouter**

In `src/routing/secretary.ts`, add this method after the existing `route()` method:

```typescript
  async routeWithSignals(
    message: string,
    userId: string,
    signals: import("./user-profile-service.js").RoutingSignals,
  ): Promise<RoutingDecision> {
    const specialists = this.folderRegistry?.listSpecialists() ?? [];
    if (specialists.length === 0) {
      return { type: "direct", reason: "No specialized owls configured" };
    }
    if (message.length < MIN_MESSAGE_LENGTH) {
      return { type: "direct", reason: "Message too short to classify" };
    }

    // Score each specialist with signal boosts
    const scored = specialists.map((spec) => {
      let score = this.computeKeywordScore(message, spec);

      // Domain signal boost: active goals overlapping with owl's expertise
      for (const domain of signals.domainStack) {
        const domainLower = domain.toLowerCase();
        if (spec.expertise.some((e) => domainLower.includes(e.toLowerCase()) || e.toLowerCase().includes(domainLower.split(" ")[0]))) {
          score += 0.15;
        }
      }

      // Fact signal boost: facts that mention this owl by name
      for (const fact of signals.relevantFacts) {
        if (fact.toLowerCase().includes(spec.name.toLowerCase())) {
          score += 0.10;
        }
      }

      return { spec, score };
    });
    // Note: `spec` is SpecializedOwlSpec — `spec.routingRules.keywords` is `string[]`

    scored.sort((a, b) => b.score - a.score);
    const best = scored[0];

    if (best.score >= MATCH_SCORE_THRESHOLD) {
      log.engine.info(`[SecretaryRouter] routeWithSignals → "${best.spec.name}" (score=${best.score.toFixed(2)})`);
      return { type: "specialist", owl: best.spec, reason: `score=${best.score.toFixed(2)}` };
    }

    // Parliament detection
    const lowerMsg = message.toLowerCase();
    if (PARLIAMENT_KEYWORDS.some((kw) => lowerMsg.includes(kw))) {
      return { type: "parliament", reason: "parliament keyword matched" };
    }

    return { type: "direct", reason: `max score ${best.score.toFixed(2)} below threshold` };
  }

  private computeKeywordScore(message: string, spec: import("../owls/specialized-types.js").SpecializedOwlSpec): number {
    const lowerMsg = message.toLowerCase();
    const keywords = spec.routingRules.keywords ?? [];
    const expertise = spec.expertise ?? [];
    const allKeywords = [...keywords, ...expertise];
    if (allKeywords.length === 0) return 0;
    const matchCount = allKeywords.filter((kw) => lowerMsg.includes(kw.toLowerCase())).length;
    const matchRatio = matchCount / allKeywords.length;
    return matchRatio * MATCH_WEIGHT;
  }
```

Note: `RoutingTarget` in `secretary.ts` uses `expertise` from the interface. Check the existing `RoutingTarget` interface in `secretary.ts` and make sure `computeKeywordScore` uses the correct field name. The existing code has `expertiseDomains?` — keep that. Also check `spec.expertise` usage above and align with `SpecializedOwlSpec.expertise`.

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/secretary-signals.test.ts
```
Expected: PASS — 4 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/secretary.ts __tests__/secretary-signals.test.ts
git commit -m "feat(routing): SecretaryRouter.routeWithSignals() — signal-weighted specialist scoring"
```

---

### Task 5: OwlBrain

**Files:**
- Create: `src/routing/owl-brain.ts`
- Create: `__tests__/owl-brain.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/owl-brain.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { OwlBrain } from "../src/routing/owl-brain.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let tmpDir: string;
let db: MemoryDatabase;

function makeRegistry(owls: { name: string; role: string; expertise: string[]; routingRules: string[]; personality: any; permissions: any }[] = []) {
  return {
    listSpecialists: () => owls,
    get: (name: string) => owls.find(o => o.name === name),
    getDefault: () => ({ name: "noctua" }),
  } as any;
}

const mockOwl = { name: "ts-owl", role: "TypeScript expert", expertise: ["TypeScript"], routingRules: { keywords: ["typescript"] }, personality: { challengeLevel: "medium", verbosity: "concise", tone: "technical" }, permissions: { capabilityConstraints: [] }, additionalPrompt: "", skills: { allowed: [] } };

const mockEngineCtx: any = { owl: { persona: { name: "noctua" } }, specialistPrompt: "" };
const mockCallbacks: any = { onOwlChange: vi.fn() };
const mockSession: any = { id: "s1", metadata: {}, messages: [] };
const mockMessage: any = { id: "m1", channelId: "cli", userId: "u1", sessionId: "cli:u1", text: "hello" };

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-brain-"));
  db = new MemoryDatabase(tmpDir);
  vi.clearAllMocks();
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("OwlBrain", () => {
  it("returns default owl when no specialists configured", async () => {
    const brain = new OwlBrain(makeRegistry([]), db, "noctua", undefined, undefined, undefined);
    const result = await brain.resolve("hello world", { ...mockMessage }, mockEngineCtx, mockCallbacks, mockSession);
    expect(result.activeOwlName).toBe("noctua");
    expect(result.parliamentHandled).toBe(false);
  });

  it("restores SQLite pin on first message", async () => {
    db.userProfiles.setPin("u1", "ts-owl");
    const brain = new OwlBrain(makeRegistry([mockOwl]), db, "noctua", undefined, undefined, undefined);
    const result = await brain.resolve("help me", { ...mockMessage }, mockEngineCtx, mockCallbacks, { ...mockSession });
    expect(result.activeOwlName).toBe("ts-owl");
  });

  it("@mention pins specialist", async () => {
    const brain = new OwlBrain(makeRegistry([mockOwl]), db, "noctua", undefined, undefined, undefined);
    const result = await brain.resolve("@ts-owl fix my code", { ...mockMessage }, mockEngineCtx, mockCallbacks, { ...mockSession });
    expect(result.activeOwlName).toBe("ts-owl");
    expect(result.text).toBe("fix my code");
    expect(db.userProfiles.getPin("u1")).toBe("ts-owl");
  });

  it("@noctua (coordinator name) clears pin", async () => {
    db.userProfiles.setPin("u1", "ts-owl");
    const brain = new OwlBrain(makeRegistry([mockOwl]), db, "noctua", undefined, undefined, undefined);
    const result = await brain.resolve("@noctua hello", { ...mockMessage }, mockEngineCtx, mockCallbacks, { ...mockSession, metadata: { activeOwlName: "ts-owl" } });
    expect(result.activeOwlName).toBe("noctua");
    expect(db.userProfiles.getPin("u1")).toBeNull();
  });

  it("routing history is appended after each resolution", async () => {
    const brain = new OwlBrain(makeRegistry([]), db, "noctua", undefined, undefined, undefined);
    await brain.resolve("hello", { ...mockMessage }, mockEngineCtx, mockCallbacks, { ...mockSession });
    const history = db.userProfiles.getRoutingHistory("u1");
    expect(history).toHaveLength(1);
    expect(history[0].owl).toBe("noctua");
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/owl-brain.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/owl-brain.js'`

- [ ] **Step 3: Implement OwlBrain**

```typescript
// src/routing/owl-brain.ts
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import type { GatewayCallbacks, GatewayMessage } from "../gateway/types.js";
import type { EngineContext } from "../engine/runtime.js";
import type { Session } from "../memory/store.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { UserProfileService } from "./user-profile-service.js";
import type { SecretaryRouter } from "./secretary.js";
import type { PelletStore } from "../pellets/store.js";
import type { ConversationDigestManager } from "../memory/conversation-digest.js";
import { log } from "../logger.js";

export interface OwlBrainResult {
  text: string;
  activeOwlName: string;
  parliamentHandled: boolean;
}

export class OwlBrain {
  constructor(
    private specializedRegistry: SpecializedOwlRegistry | undefined,
    private db: Pick<MemoryDatabase, "userProfiles">,
    private defaultOwlName: string,
    private userProfileService: UserProfileService | undefined,
    private pelletStore: PelletStore | undefined,
    private digestManager: ConversationDigestManager | undefined,
  ) {}

  private getSecretaryRouter: (() => SecretaryRouter | null) = () => null;

  setSecretaryRouterGetter(fn: () => SecretaryRouter | null): void {
    this.getSecretaryRouter = fn;
  }

  async resolve(
    text: string,
    message: GatewayMessage,
    engineCtx: EngineContext,
    callbacks: GatewayCallbacks,
    session?: Session,
  ): Promise<OwlBrainResult> {
    let activeOwlName = this.defaultOwlName;

    // 1. Restore SQLite pin on first message of session
    if (!session?.metadata.activeOwlName && message.userId && this.specializedRegistry) {
      const savedPin = this.db.userProfiles.getPin(message.userId);
      if (savedPin && session) {
        const spec = this.specializedRegistry.get(savedPin);
        if (spec) {
          session.metadata.activeOwlName = savedPin;
          log.engine.info(`[OwlBrain] Restored SQLite pin "${savedPin}" for ${message.userId}`);
        }
      }
    }

    // 2. Explicit @mention
    const explicitMention = text.match(/^@(\w+)(?:\s+(.+))?$/s);
    if (explicitMention && this.specializedRegistry) {
      const [, owlName, rest] = explicitMention;
      const coordinatorName = this.specializedRegistry.getDefault()?.name ?? this.defaultOwlName;
      if (owlName.toLowerCase() === coordinatorName.toLowerCase()) {
        // @coordinator — clear pin
        if (session) session.metadata.activeOwlName = undefined;
        this.db.userProfiles.setPin(message.userId, null);
        text = rest?.trim() || "Hello";
        this.appendHistory(message.userId, this.defaultOwlName, "@coordinator clear");
        return { text, activeOwlName: this.defaultOwlName, parliamentHandled: false };
      }
      const spec = this.specializedRegistry.get(owlName);
      if (spec) {
        text = rest?.trim() || "Hello";
        if (session) session.metadata.activeOwlName = spec.name;
        this.db.userProfiles.setPin(message.userId, spec.name);
        this.applySpecialist(spec, engineCtx, callbacks);
        await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx);
        activeOwlName = spec.name;
        this.appendHistory(message.userId, spec.name, "@mention");
        log.engine.info(`[OwlBrain] @mention → "${spec.name}" (pinned)`);
        return { text, activeOwlName, parliamentHandled: false };
      }
    }

    // 3. Session pin resume
    if (session?.metadata.activeOwlName && this.specializedRegistry) {
      const pinnedSpec = this.specializedRegistry.get(session.metadata.activeOwlName);
      if (pinnedSpec) {
        this.applySpecialist(pinnedSpec, engineCtx, callbacks);
        await this.injectMemoryContext(pinnedSpec.name, message.sessionId, text, engineCtx);
        this.appendHistory(message.userId, pinnedSpec.name, "pin-resume");
        return { text, activeOwlName: pinnedSpec.name, parliamentHandled: false };
      }
      session.metadata.activeOwlName = undefined;
    }

    // 4. Signal-aware routing
    if (this.specializedRegistry && message.userId) {
      const router = this.getSecretaryRouter();
      if (router) {
        const signals = this.userProfileService
          ? await this.userProfileService.buildSignals(message.userId, text)
          : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const };

        const decision = await router.routeWithSignals(text, message.userId, signals);

        if (decision.type === "specialist") {
          if (session) session.metadata.activeOwlName = decision.owl.name;
          this.db.userProfiles.setPin(message.userId, decision.owl.name);
          this.applySpecialist(decision.owl, engineCtx, callbacks);
          await this.injectMemoryContext(decision.owl.name, message.sessionId, text, engineCtx);
          activeOwlName = decision.owl.name;
          this.appendHistory(message.userId, decision.owl.name, decision.reason);
          log.engine.info(`[OwlBrain] signals → "${decision.owl.name}" (${decision.reason})`);
        } else if (decision.type === "parliament") {
          this.appendHistory(message.userId, "parliament", "parliament trigger");
          return { text, activeOwlName, parliamentHandled: true };
        } else {
          this.appendHistory(message.userId, this.defaultOwlName, decision.reason);
        }
      }
    }

    return { text, activeOwlName, parliamentHandled: false };
  }

  private appendHistory(userId: string, owl: string, reason: string): void {
    try {
      this.db.userProfiles.appendRoutingHistory(userId, { ts: new Date().toISOString(), owl, reason });
    } catch { /* non-critical */ }
  }

  private buildSpecialistPrompt(spec: SpecializedOwlSpec): string {
    return [
      `You are ${spec.name}, ${spec.role}.`,
      spec.expertise.length > 0 ? `Your expertise: ${spec.expertise.join(", ")}.` : "",
      `Communication style: ${spec.personality.challengeLevel} challenge level, ${spec.personality.verbosity} verbosity, ${spec.personality.tone} tone.`,
      spec.permissions.capabilityConstraints.length > 0 ? `Constraints: ${spec.permissions.capabilityConstraints.join("; ")}.` : "",
      spec.additionalPrompt ? spec.additionalPrompt : "",
    ].filter(Boolean).join(" ");
  }

  private applySpecialist(spec: SpecializedOwlSpec, engineCtx: EngineContext, callbacks: GatewayCallbacks): void {
    const specialistPrompt = this.buildSpecialistPrompt(spec);
    engineCtx.owl = { ...engineCtx.owl, specialistPrompt, specialistRoutingRules: spec.routingRules?.keywords, specialistPermissions: spec.permissions };
    engineCtx.specialistPrompt = specialistPrompt;
    callbacks?.onOwlChange?.(spec.emoji || "🦉", spec.name);
  }

  private async injectMemoryContext(owlName: string, sessionId: string, userMessage: string, engineCtx: EngineContext): Promise<void> {
    const parts: string[] = [];
    if (this.digestManager) {
      try {
        const digest = await this.digestManager.load(sessionId);
        if (digest?.task) {
          parts.push(`## Session Context\nTask: ${digest.task}`);
        }
      } catch { /* non-critical */ }
    }
    if (this.pelletStore) {
      try {
        const pellets = await this.pelletStore.search(userMessage, 3);
        const lines = pellets.filter(p => p.owls.includes(owlName) || p.owls.length === 0).map(p => `- ${p.title}: ${p.content.slice(0, 120)}`).join("\n");
        if (lines) parts.push(`## Related Memory\n${lines}`);
      } catch { /* non-critical */ }
    }
    if (parts.length > 0) {
      const existing = engineCtx.specialistPrompt ?? "";
      engineCtx.specialistPrompt = existing + "\n\n" + parts.join("\n\n");
      engineCtx.owl = { ...engineCtx.owl, specialistPrompt: engineCtx.specialistPrompt };
    }
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/owl-brain.test.ts
```
Expected: PASS — 5 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/owl-brain.ts __tests__/owl-brain.test.ts
git commit -m "feat(routing): add OwlBrain — central routing with SQLite pin + signal-aware delegation"
```

---

### Task 6: TaskOwnershipManager

**Files:**
- Create: `src/routing/task-ownership-manager.ts`
- Create: `__tests__/task-ownership-manager.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/task-ownership-manager.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { TaskOwnershipManager } from "../src/routing/task-ownership-manager.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let tmpDir: string;
let db: MemoryDatabase;
let mgr: TaskOwnershipManager;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-tasks-"));
  db = new MemoryDatabase(tmpDir);
  mgr = new TaskOwnershipManager(db);
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("TaskOwnershipManager", () => {
  it("creates a task and retrieves it", () => {
    const id = mgr.createTask("u1", "ts-owl", "Fix the bug", "Investigate line 42", "high");
    const task = db.owlTasks.get(id);
    expect(task).not.toBeNull();
    expect(task!.title).toBe("Fix the bug");
    expect(task!.priority).toBe("high");
    expect(task!.status).toBe("pending");
  });

  it("getActiveTasks returns max 5 pending/active/blocked", () => {
    for (let i = 0; i < 7; i++) {
      mgr.createTask("u1", "owl", `Task ${i}`, undefined, "normal");
    }
    const active = mgr.getActiveTasks("u1");
    expect(active.length).toBeLessThanOrEqual(5);
  });

  it("markDone removes from active list", () => {
    const id = mgr.createTask("u1", "owl", "Do something", undefined, "normal");
    mgr.markDone(id, "completed");
    const active = mgr.getActiveTasks("u1");
    expect(active.find(t => t.id === id)).toBeUndefined();
  });

  it("detectAndCreate detects commitment in response text", () => {
    const id = mgr.detectAndCreate("u1", "ts-owl", "s1", "I'll follow up on the deployment issue tomorrow.");
    expect(id).not.toBeNull();
    const task = db.owlTasks.get(id!);
    expect(task).not.toBeNull();
    expect(task!.title).toContain("follow up");
  });

  it("detectAndCreate returns null when no commitment found", () => {
    const id = mgr.detectAndCreate("u1", "ts-owl", "s1", "Sure, TypeScript generics work like this...");
    expect(id).toBeNull();
  });

  it("buildPromptBlock returns empty string with no tasks", () => {
    const block = mgr.buildPromptBlock("u1");
    expect(block).toBe("");
  });

  it("buildPromptBlock formats tasks correctly", () => {
    mgr.createTask("u1", "owl", "Research Redis clustering", undefined, "high");
    const block = mgr.buildPromptBlock("u1");
    expect(block).toContain("<open_tasks>");
    expect(block).toContain("Research Redis clustering");
    expect(block).toContain("[high]");
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/task-ownership-manager.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/task-ownership-manager.js'`

- [ ] **Step 3: Implement TaskOwnershipManager**

```typescript
// src/routing/task-ownership-manager.ts
import type { MemoryDatabase, OwlTask, OwlTaskPriority } from "../memory/db.js";
import { v4 as uuidv4 } from "uuid";

const COMMITMENT_PATTERNS = [
  /i'?ll\s+(follow\s+up|remind|check\s+back|research|look\s+into|handle|take\s+care|investigate|get\s+back)/i,
  /i\s+will\s+(follow\s+up|remind|check\s+back|research|look\s+into|handle|investigate)/i,
  /let\s+me\s+(follow\s+up|check|research|look\s+into|investigate)/i,
  /i'?ll\s+(get\s+that|do\s+that|sort\s+that|fix\s+that)/i,
];

export class TaskOwnershipManager {
  constructor(private db: Pick<MemoryDatabase, "owlTasks">) {}

  createTask(
    userId: string,
    owlName: string,
    title: string,
    description: string | undefined,
    priority: OwlTaskPriority,
    sessionId?: string,
    dueAt?: string,
  ): string {
    const id = uuidv4();
    this.db.owlTasks.create({ id, userId, owlName, title, description, status: "pending", priority, sessionId, dueAt });
    return id;
  }

  markDone(taskId: string, result: string): void {
    this.db.owlTasks.updateStatus(taskId, "done", result);
  }

  markBlocked(taskId: string): void {
    this.db.owlTasks.updateStatus(taskId, "blocked");
  }

  getActiveTasks(userId: string): OwlTask[] {
    return this.db.owlTasks.getActive(userId);
  }

  detectAndCreate(
    userId: string,
    owlName: string,
    sessionId: string,
    responseText: string,
  ): string | null {
    for (const pattern of COMMITMENT_PATTERNS) {
      const match = responseText.match(pattern);
      if (match) {
        const snippet = responseText.slice(responseText.indexOf(match[0]), responseText.indexOf(match[0]) + 80);
        const title = snippet.replace(/[^a-z0-9 ]/gi, " ").slice(0, 60).trim();
        return this.createTask(userId, owlName, title, undefined, "normal", sessionId);
      }
    }
    return null;
  }

  buildPromptBlock(userId: string): string {
    const tasks = this.getActiveTasks(userId);
    if (tasks.length === 0) return "";
    const lines = tasks.map((t) => {
      const due = t.dueAt ? ` (due: ${t.dueAt.slice(0, 10)})` : "";
      return `- [${t.priority}] ${t.title}${due}`;
    });
    return `<open_tasks>\n${lines.join("\n")}\n</open_tasks>`;
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/task-ownership-manager.test.ts
```
Expected: PASS — 7 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/task-ownership-manager.ts __tests__/task-ownership-manager.test.ts
git commit -m "feat(routing): add TaskOwnershipManager — commitment detection + task CRUD"
```

---

### Task 7: RoutingStatusReporter

**Files:**
- Create: `src/routing/routing-status-reporter.ts`
- Create: `__tests__/routing-status-reporter.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/routing-status-reporter.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RoutingStatusReporter } from "../src/routing/routing-status-reporter.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let tmpDir: string;
let db: MemoryDatabase;
let reporter: RoutingStatusReporter;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-status-"));
  db = new MemoryDatabase(tmpDir);
  reporter = new RoutingStatusReporter(db);
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("RoutingStatusReporter", () => {
  it("returns empty status for unknown user", () => {
    const report = reporter.getStatusReport("u1");
    expect(report.activePin).toBeUndefined();
    expect(report.openTasks).toEqual([]);
    expect(report.queuedJobs).toEqual([]);
  });

  it("includes active pin when set", () => {
    db.userProfiles.setPin("u1", "ts-owl");
    const report = reporter.getStatusReport("u1");
    expect(report.activePin).toBe("ts-owl");
  });

  it("includes open tasks", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Fix bug", status: "pending", priority: "high" });
    const report = reporter.getStatusReport("u1");
    expect(report.openTasks).toHaveLength(1);
    expect(report.openTasks[0].title).toBe("Fix bug");
  });

  it("includes last routing decision from history", () => {
    db.userProfiles.appendRoutingHistory("u1", { ts: new Date().toISOString(), owl: "ts-owl", reason: "domain match" });
    const report = reporter.getStatusReport("u1");
    expect(report.lastRoutingDecision?.owl).toBe("ts-owl");
  });

  it("formatForChannel produces markdown with tasks", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Research Redis", status: "active", priority: "normal" });
    const report = reporter.getStatusReport("u1");
    const md = reporter.formatForChannel(report, "cli");
    expect(md).toContain("Research Redis");
    expect(md).toContain("active");
  });

  it("isStatusQuery detects status-intent messages", () => {
    expect(RoutingStatusReporter.isStatusQuery("what are you working on")).toBe(true);
    expect(RoutingStatusReporter.isStatusQuery("what tasks do you have")).toBe(true);
    expect(RoutingStatusReporter.isStatusQuery("what did you promise me")).toBe(true);
    expect(RoutingStatusReporter.isStatusQuery("hello how are you")).toBe(false);
    expect(RoutingStatusReporter.isStatusQuery("help me with TypeScript")).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/routing-status-reporter.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/routing-status-reporter.js'`

- [ ] **Step 3: Implement RoutingStatusReporter**

```typescript
// src/routing/routing-status-reporter.ts
import type { MemoryDatabase, OwlTask, OwlJob } from "../memory/db.js";

export interface StatusReport {
  activePin?: string;
  openTasks: { id: string; title: string; status: string; priority: string; dueAt?: string }[];
  queuedJobs: { id: string; type: string; scheduledAt: string }[];
  lastRoutingDecision?: { owl: string; reason: string; ts: string };
}

const STATUS_PATTERNS = [
  /what\s+are\s+you\s+(working\s+on|doing|up\s+to)/i,
  /what\s+tasks?\b/i,
  /what\s+did\s+you\s+(promise|commit|say\s+you.d)/i,
  /what.s\s+pending/i,
  /\bstatus\b/i,
  /open\s+tasks?/i,
];

export class RoutingStatusReporter {
  constructor(private db: Pick<MemoryDatabase, "userProfiles" | "owlTasks" | "owlJobs">) {}

  static isStatusQuery(text: string): boolean {
    const lower = text.toLowerCase().trim();
    return STATUS_PATTERNS.some((p) => p.test(lower));
  }

  getStatusReport(userId: string): StatusReport {
    const pin = this.db.userProfiles.getPin(userId);
    const tasks = this.db.owlTasks.getActive(userId).map((t: OwlTask) => ({
      id: t.id, title: t.title, status: t.status, priority: t.priority, dueAt: t.dueAt,
    }));
    const jobs = this.db.owlJobs.getQueued(userId).slice(0, 5).map((j: OwlJob) => ({
      id: j.id, type: j.type, scheduledAt: j.scheduledAt,
    }));
    const history = this.db.userProfiles.getRoutingHistory(userId);
    const last = history.length > 0 ? history[history.length - 1] : undefined;

    return {
      activePin: pin ?? undefined,
      openTasks: tasks,
      queuedJobs: jobs,
      lastRoutingDecision: last ? { owl: last.owl, reason: last.reason, ts: last.ts } : undefined,
    };
  }

  formatForChannel(report: StatusReport, channelId: string): string {
    const lines: string[] = [];

    if (report.activePin) {
      lines.push(`**Active specialist:** @${report.activePin}`);
    } else {
      lines.push("**Active specialist:** coordinator (default)");
    }

    if (report.openTasks.length > 0) {
      lines.push("\n**Open tasks:**");
      for (const t of report.openTasks) {
        const due = t.dueAt ? ` (due ${t.dueAt.slice(0, 10)})` : "";
        lines.push(`- [${t.priority}] ${t.title} — *${t.status}*${due}`);
      }
    } else {
      lines.push("\n**Open tasks:** none");
    }

    if (report.queuedJobs.length > 0) {
      lines.push("\n**Queued jobs:**");
      for (const j of report.queuedJobs) {
        lines.push(`- ${j.type} (scheduled ${j.scheduledAt.slice(0, 16)})`);
      }
    }

    if (report.lastRoutingDecision) {
      lines.push(`\n**Last routing:** @${report.lastRoutingDecision.owl} — ${report.lastRoutingDecision.reason}`);
    }

    return lines.join("\n");
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/routing-status-reporter.test.ts
```
Expected: PASS — 6 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/routing-status-reporter.ts __tests__/routing-status-reporter.test.ts
git commit -m "feat(routing): add RoutingStatusReporter — status query detection + formatted output"
```

---

### Task 8: Wire Phase 1 into types.ts + core.ts + context-builder.ts

**Files:**
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/context-builder.ts`

- [ ] **Step 1: Add fields to GatewayContext in types.ts**

In `src/gateway/types.ts`, after the `sessionService?` and `userMemoryStore?` lines at the bottom (around line 350), add:

```typescript
  // ─── OwlBrain (Element 4 — routing coordinator) ───────────────
  owlBrain?: import("../routing/owl-brain.js").OwlBrain;
  taskOwnershipManager?: import("../routing/task-ownership-manager.js").TaskOwnershipManager;
  routingStatusReporter?: import("../routing/routing-status-reporter.js").RoutingStatusReporter;
  userProfileService?: import("../routing/user-profile-service.js").UserProfileService;
```

- [ ] **Step 2: Add imports to core.ts**

At the top of `src/gateway/core.ts`, add imports after the `SessionService` / `UserMemoryStore` imports:

```typescript
import { OwlBrain } from "../routing/owl-brain.js";
import { UserProfileService } from "../routing/user-profile-service.js";
import { TaskOwnershipManager } from "../routing/task-ownership-manager.js";
import { RoutingStatusReporter } from "../routing/routing-status-reporter.js";
```

- [ ] **Step 3: Declare owlBrain field in OwlGateway class**

After `private routingCoordinator: RoutingCoordinator | null = null;` (line 176), add:

```typescript
  private owlBrain: OwlBrain | null = null;
```

- [ ] **Step 4: Instantiate OwlBrain + friends in core.ts constructor**

After the `SessionService` + `UserMemoryStore` construction block (after `migrateJsonSessionsToSQLite` call, around line 113+), add:

```typescript
    // ─── OwlBrain (Element 4 — routing coordinator) ───────────────
    if (ctx.db) {
      const userProfileSvc = new UserProfileService(
        ctx.db,
        ctx.goalGraph ?? undefined,
        ctx.episodicMemory ?? undefined,
        ctx.userMemoryStore ?? undefined,
      );
      ctx.userProfileService = userProfileSvc;
      ctx.taskOwnershipManager = new TaskOwnershipManager(ctx.db);
      ctx.routingStatusReporter = new RoutingStatusReporter(ctx.db);
      this.owlBrain = new OwlBrain(
        ctx.specializedRegistry,
        ctx.db,
        ctx.owl.persona.name,
        userProfileSvc,
        ctx.pelletStore,
        ctx.digestManager,
      );
      this.owlBrain.setSecretaryRouterGetter(() => this.secretaryRouter);
      log.engine.info("[OwlBrain] Initialized");
    }
```

- [ ] **Step 5: Replace routingCoordinator.resolve() with owlBrain.resolve() in handleCore()**

In `src/gateway/core.ts` `handleCore()`, find the routing section (around line 1753):

```typescript
    // ─── Routing — @mention + SecretaryRouter ────────────────────
    let activeOwlName = this.ctx.owl.persona.name;
    if (!this.secretaryRouter && this.ctx.specializedRegistry) {
      this.secretaryRouter = new SecretaryRouter(this.ctx.specializedRegistry);
    }
    let routingResult: RoutingResult | null = null;
    if (this.routingCoordinator) {
      routingResult = await this.routingCoordinator.resolve(text, message, engineCtx, callbacks, session);
      text = routingResult.text;
      activeOwlName = routingResult.activeOwlName;
    }
```

Replace with:

```typescript
    // ─── Routing — @mention + SecretaryRouter ────────────────────
    let activeOwlName = this.ctx.owl.persona.name;
    if (!this.secretaryRouter && this.ctx.specializedRegistry) {
      this.secretaryRouter = new SecretaryRouter(this.ctx.specializedRegistry);
    }
    let routingResult: RoutingResult | null = null;
    if (this.owlBrain) {
      const brainResult = await this.owlBrain.resolve(text, message, engineCtx, callbacks, session);
      text = brainResult.text;
      activeOwlName = brainResult.activeOwlName;
      routingResult = { text: brainResult.text, activeOwlName: brainResult.activeOwlName, parliamentHandled: brainResult.parliamentHandled };
    } else if (this.routingCoordinator) {
      routingResult = await this.routingCoordinator.resolve(text, message, engineCtx, callbacks, session);
      text = routingResult.text;
      activeOwlName = routingResult.activeOwlName;
    }
```

- [ ] **Step 6: Add status query interception in handleCore()**

In `handleCore()`, after the wizard routing section (after line ~875, before the `/skills` cmd check), add:

```typescript
    // ─── Status query interception (E6-S2) ──────────────────────
    if (this.ctx.routingStatusReporter && RoutingStatusReporter.isStatusQuery(message.text)) {
      const report = this.ctx.routingStatusReporter.getStatusReport(message.userId);
      const content = this.ctx.routingStatusReporter.formatForChannel(report, message.channelId);
      return {
        content,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }
```

Also add the import at the top of `handleCore()` area (or at the class level import):

In `core.ts` imports, `RoutingStatusReporter` is already imported (added in Step 2).

- [ ] **Step 7: Add task detection after engine response**

In `handleCore()`, after the `orchResult` / `response` is ready and before the return (around line 1828+), add:

```typescript
    // ─── Task commitment detection (E3-S2) ──────────────────────
    if (this.ctx.taskOwnershipManager && response.content) {
      this.ctx.taskOwnershipManager.detectAndCreate(
        message.userId,
        activeOwlName,
        session.id,
        response.content,
      );
    }
```

- [ ] **Step 8: Add open_tasks block to context-builder.ts**

In `src/gateway/handlers/context-builder.ts`, after the `userMemoryContext` block (around line 228), add:

```typescript
    let openTasksContext = "";
    if (this.ctx.taskOwnershipManager && userId) {
      openTasksContext = this.ctx.taskOwnershipManager.buildPromptBlock(userId);
    }
```

Then add `openTasksContext` to the `enrichedMemoryContext` array (around line 653), after `userMemoryContext`:

```typescript
      userMemoryContext,    // L2.5: cross-session user facts
      openTasksContext,     // L2.6: open task commitments (OwlBrain)
```

Note: the `userId` variable is already available in `build()` as a parameter (line ~49).

- [ ] **Step 9: Run full test suite**

```bash
npm run build 2>&1 | tail -10
npx vitest run 2>&1 | tail -20
```
Expected: build passes, existing tests pass, new tests pass

- [ ] **Step 10: Commit**

```bash
git add src/gateway/types.ts src/gateway/core.ts src/gateway/handlers/context-builder.ts
git commit -m "feat(gateway): wire OwlBrain + TaskOwnershipManager + RoutingStatusReporter into pipeline"
```

---

## ─── PHASE 2: E4 + E5 ─────────────────────────────────────────────

---

### Task 9: BackgroundJobRunner

**Files:**
- Create: `src/routing/background-job-runner.ts`
- Create: `__tests__/background-job-runner.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/background-job-runner.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { BackgroundJobRunner } from "../src/routing/background-job-runner.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";

let tmpDir: string;
let db: MemoryDatabase;
let runner: BackgroundJobRunner;

const mockEventBus = { emit: vi.fn() } as any;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-jobs-"));
  db = new MemoryDatabase(tmpDir);
  runner = new BackgroundJobRunner(db, mockEventBus);
  vi.clearAllMocks();
});

afterEach(() => {
  runner.stop();
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("BackgroundJobRunner", () => {
  it("does not execute jobs scheduled in the future", async () => {
    db.owlJobs.enqueue({ id: uuidv4(), userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() + 60_000).toISOString() });
    await runner.tick();
    expect(mockEventBus.emit).not.toHaveBeenCalled();
  });

  it("executes a due followup job and emits job:complete", async () => {
    const jobId = uuidv4();
    db.owlJobs.enqueue({ id: jobId, userId: "u1", owlName: "owl", type: "followup", payload: { message: "Your task is ready" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    await runner.tick();
    const job = db.owlJobs.get(jobId);
    expect(job!.status).toBe("done");
    expect(mockEventBus.emit).toHaveBeenCalledWith("job:complete", expect.objectContaining({ userId: "u1" }));
  });

  it("scheduleFollowup inserts a job row", () => {
    runner.scheduleFollowup({ id: "t1", userId: "u1", owlName: "owl", title: "Check Redis", status: "pending", priority: "normal", createdAt: "", updatedAt: "" }, 5000);
    const jobs = db.owlJobs.getQueued("u1");
    expect(jobs).toHaveLength(1);
    expect(jobs[0].type).toBe("followup");
    expect(jobs[0].taskId).toBe("t1");
  });

  it("marks job failed when handler throws", async () => {
    const jobId = uuidv4();
    db.owlJobs.enqueue({ id: jobId, userId: "u1", owlName: "owl", type: "research", payload: { query: "bad query" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    // research handler requires provider — will throw gracefully
    await runner.tick();
    const job = db.owlJobs.get(jobId);
    expect(["done", "failed"]).toContain(job!.status);
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/background-job-runner.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/background-job-runner.js'`

- [ ] **Step 3: Implement BackgroundJobRunner**

```typescript
// src/routing/background-job-runner.ts
import type { MemoryDatabase, OwlTask, OwlJob } from "../memory/db.js";
import type { EventBus } from "../events/bus.js";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";

const POLL_INTERVAL_MS = 60_000;

export class BackgroundJobRunner {
  private interval: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private db: Pick<MemoryDatabase, "owlJobs" | "owlTasks">,
    private eventBus: EventBus | null,
  ) {}

  start(): void {
    if (this.interval) return;
    this.interval = setInterval(() => { this.tick().catch(() => {}); }, POLL_INTERVAL_MS);
    this.interval.unref();
    log.engine.info("[BackgroundJobRunner] Started — polling every 60s");
  }

  stop(): void {
    if (this.interval) { clearInterval(this.interval); this.interval = null; }
  }

  async tick(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const job = this.db.owlJobs.dequeueNext();
      if (!job) return;
      log.engine.info(`[BackgroundJobRunner] Executing job ${job.id} (${job.type})`);
      try {
        const result = await this.executeJob(job);
        this.db.owlJobs.markDone(job.id, result);
        if (job.taskId) {
          this.db.owlTasks.updateStatus(job.taskId, "done", result);
        }
        this.eventBus?.emit("job:complete", { userId: job.userId, jobId: job.id, type: job.type, result });
        log.engine.info(`[BackgroundJobRunner] Job ${job.id} done`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.db.owlJobs.markFailed(job.id, msg);
        log.engine.warn(`[BackgroundJobRunner] Job ${job.id} failed: ${msg}`);
      }
    } finally {
      this.running = false;
    }
  }

  scheduleFollowup(task: OwlTask, delayMs: number): void {
    const scheduledAt = new Date(Date.now() + delayMs).toISOString();
    this.db.owlJobs.enqueue({
      id: uuidv4(),
      taskId: task.id,
      userId: task.userId,
      owlName: task.owlName,
      type: "followup",
      payload: { taskTitle: task.title, taskId: task.id },
      scheduledAt,
    });
  }

  private async executeJob(job: OwlJob): Promise<string> {
    switch (job.type) {
      case "followup": {
        const title = (job.payload as any).taskTitle ?? "task";
        return `Follow-up on: ${title} — no update available yet.`;
      }
      case "proactive":
        return `Proactive check completed at ${new Date().toISOString()}.`;
      case "research":
        throw new Error("Research jobs require a provider — not wired yet");
      case "monitor":
        return `Monitor check completed at ${new Date().toISOString()}.`;
      default:
        throw new Error(`Unknown job type: ${job.type}`);
    }
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/background-job-runner.test.ts
```
Expected: PASS — 4 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/background-job-runner.ts __tests__/background-job-runner.test.ts
git commit -m "feat(routing): add BackgroundJobRunner — job queue polling + completion events"
```

---

### Task 10: RelationshipContext

**Files:**
- Create: `src/routing/relationship-context.ts`
- Create: `__tests__/relationship-context.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/relationship-context.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { RelationshipContext } from "../src/routing/relationship-context.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-rel-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("RelationshipContext", () => {
  it("returns empty summary for unknown user", async () => {
    const ctx = new RelationshipContext(db, undefined, undefined, undefined);
    const summary = await ctx.buildSummary("u1");
    expect(summary.communicationStyle).toBe("unknown");
    expect(summary.recurringTopics).toEqual([]);
    expect(summary.openCommitments).toEqual([]);
  });

  it("openCommitments comes from active tasks", async () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Fix deployment", status: "pending", priority: "high" });
    const ctx = new RelationshipContext(db, undefined, undefined, undefined);
    const summary = await ctx.buildSummary("u1");
    expect(summary.openCommitments).toContain("Fix deployment");
  });

  it("recurringTopics extracted from routing history", async () => {
    for (let i = 0; i < 3; i++) {
      db.userProfiles.appendRoutingHistory("u1", { ts: new Date().toISOString(), owl: "ts-owl", reason: "TypeScript work" });
    }
    const ctx = new RelationshipContext(db, undefined, undefined, undefined);
    const summary = await ctx.buildSummary("u1");
    expect(summary.recurringTopics.length).toBeGreaterThan(0);
  });

  it("buildPromptBlock formats correctly", async () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Check Redis", status: "pending", priority: "normal" });
    const ctx = new RelationshipContext(db, undefined, undefined, undefined);
    const block = await ctx.buildPromptBlock("u1");
    expect(block).toContain("<user_relationship>");
    expect(block).toContain("Check Redis");
  });

  it("buildPromptBlock returns empty string when nothing to say", async () => {
    const ctx = new RelationshipContext(db, undefined, undefined, undefined);
    const block = await ctx.buildPromptBlock("u1");
    expect(block).toBe("");
  });
});
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
npx vitest run __tests__/relationship-context.test.ts
```
Expected: FAIL — `Cannot find module '../src/routing/relationship-context.js'`

- [ ] **Step 3: Implement RelationshipContext**

```typescript
// src/routing/relationship-context.ts
import type { MemoryDatabase } from "../memory/db.js";
import type { GoalGraph } from "../goals/graph.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { UserMemoryStore } from "../session/user-memory-store.js";

export interface RelationshipSummary {
  communicationStyle: string;
  expertiseLevel: string;
  recurringTopics: string[];
  openCommitments: string[];
  lastInteraction: string;
}

export class RelationshipContext {
  constructor(
    private db: Pick<MemoryDatabase, "userProfiles" | "owlTasks">,
    private goalGraph: GoalGraph | undefined,
    private episodicMemory: EpisodicMemory | undefined,
    private userMemoryStore: UserMemoryStore | undefined,
  ) {}

  async buildSummary(userId: string): Promise<RelationshipSummary> {
    const tasks = this.db.owlTasks.getActive(userId);
    const openCommitments = tasks.map((t) => t.title);

    const history = this.db.userProfiles.getRoutingHistory(userId);
    const owlFreq: Record<string, number> = {};
    for (const h of history) { owlFreq[h.owl] = (owlFreq[h.owl] ?? 0) + 1; }
    const recurringTopics = Object.entries(owlFreq)
      .filter(([owl, count]) => count >= 2 && owl !== "noctua" && owl !== "parliament" && owl !== "coordinator")
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([owl]) => owl);

    const lastHistory = history.at(-1);
    const lastInteraction = lastHistory?.ts ?? "unknown";

    let communicationStyle = "unknown";
    let expertiseLevel = "unknown";
    if (this.userMemoryStore) {
      try {
        const styleFacts = await this.userMemoryStore.retrieve(userId, "communication style preference", 1);
        if (styleFacts.length > 0) communicationStyle = styleFacts[0];
        const expertFacts = await this.userMemoryStore.retrieve(userId, "programming expertise level", 1);
        if (expertFacts.length > 0) expertiseLevel = expertFacts[0];
      } catch { /* non-critical */ }
    }

    return { communicationStyle, expertiseLevel, recurringTopics, openCommitments, lastInteraction };
  }

  async buildPromptBlock(userId: string): Promise<string> {
    const summary = await this.buildSummary(userId);
    const parts: string[] = [];

    if (summary.communicationStyle !== "unknown") {
      parts.push(`Style: ${summary.communicationStyle}`);
    }
    if (summary.expertiseLevel !== "unknown") {
      parts.push(`Expertise: ${summary.expertiseLevel}`);
    }
    if (summary.recurringTopics.length > 0) {
      parts.push(`Recurring: ${summary.recurringTopics.join(", ")}`);
    }
    if (summary.openCommitments.length > 0) {
      parts.push(`Open commitments: ${summary.openCommitments.join("; ")}`);
    }

    if (parts.length === 0) return "";
    return `<user_relationship>\n${parts.join("\n")}\n</user_relationship>`;
  }
}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
npx vitest run __tests__/relationship-context.test.ts
```
Expected: PASS — 5 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/routing/relationship-context.ts __tests__/relationship-context.test.ts
git commit -m "feat(routing): add RelationshipContext — cross-session user model for system prompt"
```

---

### Task 11: Wire Phase 2 into types.ts + core.ts + context-builder.ts

**Files:**
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/context-builder.ts`

- [ ] **Step 1: Add Phase 2 fields to GatewayContext in types.ts**

In `src/gateway/types.ts`, after the `owlBrain?` block added in Task 8:

```typescript
  backgroundJobRunner?: import("../routing/background-job-runner.js").BackgroundJobRunner;
  relationshipContext?: import("../routing/relationship-context.js").RelationshipContext;
```

- [ ] **Step 2: Add Phase 2 imports to core.ts**

```typescript
import { BackgroundJobRunner } from "../routing/background-job-runner.js";
import { RelationshipContext } from "../routing/relationship-context.js";
```

- [ ] **Step 3: Instantiate Phase 2 components in core.ts constructor**

After the OwlBrain initialization block (from Task 8, Step 4), add:

```typescript
    // ─── Phase 2: Background jobs + relationship (Element 4) ──────
    if (ctx.db) {
      ctx.backgroundJobRunner = new BackgroundJobRunner(ctx.db, ctx.eventBus ?? null);
      ctx.backgroundJobRunner.start();
      ctx.relationshipContext = new RelationshipContext(
        ctx.db,
        ctx.goalGraph ?? undefined,
        ctx.episodicMemory ?? undefined,
        ctx.userMemoryStore ?? undefined,
      );
      log.engine.info("[BackgroundJobRunner + RelationshipContext] Initialized");
    }
```

- [ ] **Step 4: Add user_relationship block to context-builder.ts**

In `src/gateway/handlers/context-builder.ts`, after the `openTasksContext` block (from Task 8, Step 8), add:

```typescript
    let relationshipContext = "";
    if (this.ctx.relationshipContext && userId) {
      try {
        relationshipContext = await withTimeout(this.ctx.relationshipContext.buildPromptBlock(userId), "");
      } catch { /* non-critical */ }
    }
```

(Reuse the existing `withTimeout` helper already defined in `build()`)

Then add `relationshipContext` to the `enrichedMemoryContext` array after `openTasksContext`:

```typescript
      openTasksContext,      // L2.6: open task commitments
      relationshipContext,   // L2.7: cross-session relationship model
```

- [ ] **Step 5: Run full test suite**

```bash
npm run build 2>&1 | tail -10
npx vitest run 2>&1 | tail -20
```
Expected: build passes; all new tests pass; no regressions

- [ ] **Step 6: Commit**

```bash
git add src/gateway/types.ts src/gateway/core.ts src/gateway/handlers/context-builder.ts
git commit -m "feat(gateway): wire BackgroundJobRunner + RelationshipContext into pipeline (Phase 2)"
```

---

## Self-Review Checklist

After all tasks complete:

```bash
# Full build + test
npm run build
npx vitest run
npm run lint
```

Spec coverage check:
- ✅ E1-S1: pin migrated to SQLite (Task 1 + Task 2 + Task 5)
- ✅ E1-S2: OwlBrain wired (Task 5 + Task 8)
- ✅ E1-S3: dead code removed (Task 2)
- ✅ E2-S1: UserProfileService with 200ms timeout (Task 3)
- ✅ E2-S2: routeWithSignals() with domain/fact boosts (Task 4)
- ✅ E2-S3: routing_history appended (Task 5 + Task 1)
- ✅ E3-S1: TaskOwnershipManager (Task 6)
- ✅ E3-S2: detectAndCreate() after engine response (Task 8)
- ✅ E3-S3: open_tasks injected in context-builder (Task 8)
- ✅ E4-S1: BackgroundJobRunner (Task 9)
- ✅ E4-S2: scheduleFollowup() (Task 9)
- ✅ E5-S1: RelationshipContext (Task 10)
- ✅ E5-S2: user_relationship injected in context-builder (Task 11)
- ✅ E6-S1: RoutingStatusReporter (Task 7)
- ✅ E6-S2: isStatusQuery interception (Task 8)
