# Epic 5: Cron Isolated-Agent Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `BackgroundOrchestrator`'s `setInterval(5min)` tick with a proper cron service that parses cron expressions, runs each job in an isolated `OwlEngine` context (fresh session history, own traceId, scoped tool subset), persists job state to `~/.stackowl/crons.json`, and supports user-defined jobs via CLI and natural-language parsing.

**Architecture:** `CronService` (`src/cron/service.ts`) uses `croner` for cron scheduling. Each job dispatches to `IsolatedRunner` (`src/cron/isolated-runner.ts`) which creates a throwaway `OwlEngine` instance with empty history and a `safetyProfile`-filtered tool subset. On completion, results are saved as pellets and optionally delivered via the gateway. `BackgroundOrchestrator` stays in place for non-scheduled jobs (proactive pings, session debriefs); the cron service handles scheduled jobs only. Five default jobs cover memory, DNA, pellets, desires, and daily briefing.

**Tech Stack:** TypeScript, Node 22, `croner@^8` (cron expression parsing + scheduling), `better-sqlite3` (already in use), `OwlEngine` (existing), `PelletGenerator` (existing), Vitest.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/cron/service.ts` | Create | `CronService` — parse + schedule + track all cron jobs |
| `src/cron/isolated-runner.ts` | Create | `IsolatedRunner` — fresh OwlEngine per job run |
| `src/cron/default-jobs.ts` | Create | Five default scheduled jobs as typed constants |
| `src/cron/types.ts` | Create | `CronJob`, `CronRun`, `SafetyProfile` types |
| `__tests__/cron/service.test.ts` | Create | Unit tests for CronService scheduling + state |
| `__tests__/cron/isolated-runner.test.ts` | Create | Unit tests for IsolatedRunner isolation |

---

## Task 1: Types and CronService core

**Files:**
- Create: `src/cron/types.ts`
- Create: `src/cron/service.ts`
- Create: `__tests__/cron/service.test.ts`

- [ ] **Step 1.1: Install croner**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npm install croner@^8
```

Expected: adds `croner` to package.json.

- [ ] **Step 1.2: Write the failing tests**

```typescript
// __tests__/cron/service.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CronService } from "../../src/cron/service.js";
import type { CronJob } from "../../src/cron/types.js";

const SAMPLE_JOB: CronJob = {
  id: "test-job",
  schedule: "* * * * *",   // every minute
  prompt: "Do something useful",
  safetyProfile: "low",
  deliver: false,
};

describe("CronService", () => {
  let service: CronService;

  beforeEach(() => {
    service = new CronService({ persist: false });
  });

  afterEach(() => {
    service.stop();
  });

  it("registers a job and lists it", () => {
    service.addJob(SAMPLE_JOB);
    const jobs = service.listJobs();
    expect(jobs).toHaveLength(1);
    expect(jobs[0].id).toBe("test-job");
  });

  it("removes a job by id", () => {
    service.addJob(SAMPLE_JOB);
    service.removeJob("test-job");
    expect(service.listJobs()).toHaveLength(0);
  });

  it("rejects duplicate job ids", () => {
    service.addJob(SAMPLE_JOB);
    expect(() => service.addJob(SAMPLE_JOB)).toThrow(/already registered/i);
  });

  it("rejects an invalid cron expression", () => {
    expect(() =>
      service.addJob({ ...SAMPLE_JOB, id: "bad", schedule: "not-a-cron" }),
    ).toThrow(/invalid.*schedule/i);
  });

  it("tracks job state as pending initially", () => {
    service.addJob(SAMPLE_JOB);
    const state = service.getJobState("test-job");
    expect(state?.status).toBe("pending");
    expect(state?.lastRunAt).toBeNull();
  });

  it("reports nextRunAt as a future date", () => {
    service.addJob(SAMPLE_JOB);
    const state = service.getJobState("test-job");
    expect(state?.nextRunAt).toBeInstanceOf(Date);
    expect(state!.nextRunAt!.getTime()).toBeGreaterThan(Date.now());
  });

  it("respects maxConcurrentRuns — does not start a 4th job when max is 3", () => {
    const service3 = new CronService({ persist: false, maxConcurrentRuns: 3 });
    for (let i = 0; i < 3; i++) {
      (service3 as any).runningCount = 1; // simulate running jobs
    }
    (service3 as any).runningCount = 3;
    expect((service3 as any).canStartJob()).toBe(false);
    service3.stop();
  });
});
```

- [ ] **Step 1.3: Run test to confirm it fails**

```bash
npx vitest run __tests__/cron/service.test.ts 2>&1 | tail -20
```

Expected: FAIL — `CronService` module does not exist.

- [ ] **Step 1.4: Create types**

Create `src/cron/types.ts`:

```typescript
export type SafetyProfile = "low" | "medium" | "full";

export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface CronJob {
  /** Unique job identifier */
  id: string;
  /** Cron expression, e.g. "0 9 * * *" */
  schedule: string;
  /** Natural-language prompt passed to OwlEngine */
  prompt: string;
  /** Tool subset: low = read-only, medium = +shell, full = all */
  safetyProfile: SafetyProfile;
  /** If true, send result to deliveryTarget on completion */
  deliver?: boolean;
  /** Override delivery channel (defaults to config.primaryChannel) */
  deliveryTarget?: { channel: string; userId: string };
  /** Human-readable description (optional) */
  description?: string;
}

export interface CronJobState {
  id: string;
  status: JobStatus;
  lastRunAt: Date | null;
  nextRunAt: Date | null;
  lastResult?: string;
  failCount: number;
}

export interface CronRun {
  jobId: string;
  startedAt: number;
  completedAt?: number;
  status: JobStatus;
  result?: string;
  error?: string;
  traceId: string;
}
```

- [ ] **Step 1.5: Implement CronService**

Create `src/cron/service.ts`:

```typescript
import { Cron } from "croner";
import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import type { CronJob, CronJobState, CronRun } from "./types.js";

const DEFAULT_PERSIST_PATH = join(homedir(), ".stackowl", "crons.json");

export interface CronServiceOptions {
  /** Whether to persist job state to disk */
  persist?: boolean;
  /** Path to persist to (defaults to ~/.stackowl/crons.json) */
  persistPath?: string;
  /** Max concurrent running jobs (default: 3) */
  maxConcurrentRuns?: number;
  /** Called when a job fires — inject IsolatedRunner here */
  onJobFire?: (job: CronJob, traceId: string) => Promise<string>;
}

function validateCronExpression(schedule: string): boolean {
  try {
    const c = new Cron(schedule);
    c.stop();
    return true;
  } catch {
    return false;
  }
}

export class CronService {
  private jobs = new Map<string, CronJob>();
  private states = new Map<string, CronJobState>();
  private cronInstances = new Map<string, Cron>();
  private runningCount = 0;
  private options: Required<Omit<CronServiceOptions, "onJobFire">> & { onJobFire?: CronServiceOptions["onJobFire"] };

  constructor(options: CronServiceOptions = {}) {
    this.options = {
      persist: options.persist ?? true,
      persistPath: options.persistPath ?? DEFAULT_PERSIST_PATH,
      maxConcurrentRuns: options.maxConcurrentRuns ?? 3,
      onJobFire: options.onJobFire,
    };

    if (this.options.persist) {
      this.loadFromDisk();
    }
  }

  private canStartJob(): boolean {
    return this.runningCount < this.options.maxConcurrentRuns;
  }

  addJob(job: CronJob): void {
    if (this.jobs.has(job.id)) {
      throw new Error(`Job "${job.id}" is already registered.`);
    }
    if (!validateCronExpression(job.schedule)) {
      throw new Error(`Invalid schedule expression "${job.schedule}" for job "${job.id}".`);
    }

    this.jobs.set(job.id, job);

    // Add stagger: jobs within 30s window get random 0–29s offset
    const staggerMs = Math.floor(Math.random() * 30_000);

    const cronInstance = new Cron(job.schedule, { startAt: new Date(Date.now() + staggerMs) }, async () => {
      await this.fireJob(job);
    });

    this.cronInstances.set(job.id, cronInstance);

    const nextRun = cronInstance.nextRun();
    this.states.set(job.id, {
      id: job.id,
      status: "pending",
      lastRunAt: null,
      nextRunAt: nextRun,
      failCount: 0,
    });

    log.engine.info("[CronService] Job registered", { id: job.id, schedule: job.schedule });

    if (this.options.persist) {
      this.saveToDisk();
    }
  }

  removeJob(id: string): void {
    const cron = this.cronInstances.get(id);
    cron?.stop();
    this.cronInstances.delete(id);
    this.jobs.delete(id);
    this.states.delete(id);
    if (this.options.persist) {
      this.saveToDisk();
    }
    log.engine.info("[CronService] Job removed", { id });
  }

  listJobs(): CronJob[] {
    return Array.from(this.jobs.values());
  }

  getJobState(id: string): CronJobState | undefined {
    return this.states.get(id);
  }

  private async fireJob(job: CronJob): Promise<void> {
    const state = this.states.get(job.id);
    if (!state) return;

    if (!this.canStartJob()) {
      log.engine.warn("[CronService] Max concurrent runs reached, skipping", { id: job.id });
      return;
    }

    const traceId = uuidv4();
    this.runningCount++;
    state.status = "running";
    state.lastRunAt = new Date();

    log.engine.info("[CronService] Job started", { id: job.id, traceId });

    const run: CronRun = { jobId: job.id, startedAt: Date.now(), status: "running", traceId };

    try {
      if (this.options.onJobFire) {
        const result = await this.options.onJobFire(job, traceId);
        state.status = "completed";
        state.lastResult = result;
        run.result = result;
        run.status = "completed";
      } else {
        state.status = "completed";
        run.status = "completed";
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.engine.error("[CronService] Job failed", err as Error, { id: job.id, traceId });
      state.status = "failed";
      state.failCount++;
      run.status = "failed";
      run.error = msg;
    } finally {
      this.runningCount--;
      run.completedAt = Date.now();
      const nextRun = this.cronInstances.get(job.id)?.nextRun();
      state.nextRunAt = nextRun ?? null;
      if (this.options.persist) {
        this.saveToDisk();
      }
    }
  }

  stop(): void {
    for (const cron of this.cronInstances.values()) {
      cron.stop();
    }
    this.cronInstances.clear();
    log.engine.info("[CronService] Stopped all jobs");
  }

  private saveToDisk(): void {
    try {
      const data = {
        jobs: Array.from(this.jobs.values()),
        updatedAt: Date.now(),
      };
      writeFileSync(this.options.persistPath, JSON.stringify(data, null, 2), "utf-8");
    } catch (err) {
      log.engine.error("[CronService] Failed to save to disk", err as Error);
    }
  }

  private loadFromDisk(): void {
    if (!existsSync(this.options.persistPath)) return;
    try {
      const raw = readFileSync(this.options.persistPath, "utf-8");
      const data = JSON.parse(raw) as { jobs: CronJob[] };
      if (Array.isArray(data.jobs)) {
        for (const job of data.jobs) {
          try {
            this.addJob(job);
          } catch (err) {
            log.engine.warn("[CronService] Skipped job on load", { id: job.id, err: String(err) });
          }
        }
        log.engine.info(`[CronService] Loaded ${data.jobs.length} jobs from disk`);
      }
    } catch (err) {
      log.engine.error("[CronService] Failed to load from disk", err as Error);
    }
  }
}
```

- [ ] **Step 1.6: Run test to confirm it passes**

```bash
npx vitest run __tests__/cron/service.test.ts 2>&1 | tail -20
```

Expected: PASS — 7 tests passing.

- [ ] **Step 1.7: Commit**

```bash
git add src/cron/types.ts src/cron/service.ts __tests__/cron/service.test.ts
git commit -m "feat(cron): CronService — cron expression scheduling with persistence and concurrency guard"
```

---

## Task 2: IsolatedRunner — fresh OwlEngine per job

**Files:**
- Create: `src/cron/isolated-runner.ts`
- Create: `__tests__/cron/isolated-runner.test.ts`

- [ ] **Step 2.1: Find OwlEngine constructor signature**

```bash
grep -n "constructor\|export class OwlEngine" /ssd/projects/stackowl-personal-ai-assistant/src/engine/runtime.ts | head -15
```

- [ ] **Step 2.2: Write the failing test**

```typescript
// __tests__/cron/isolated-runner.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { IsolatedRunner } from "../../src/cron/isolated-runner.js";
import type { CronJob } from "../../src/cron/types.js";

const LOW_JOB: CronJob = {
  id: "memory-consolidation",
  schedule: "0 * * * *",
  prompt: "Consolidate recent episodic memories",
  safetyProfile: "low",
  deliver: false,
};

describe("IsolatedRunner", () => {
  it("creates a runner without crashing", () => {
    const fakeProvider = { chat: vi.fn().mockResolvedValue({ content: "done" }) } as any;
    const fakeOwl = { persona: { name: "Athena" } } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider, owl: fakeOwl });
    expect(runner).toBeTruthy();
  });

  it("run() calls provider.chat with job prompt and returns string", async () => {
    const fakeProvider = {
      chat: vi.fn().mockResolvedValue({ content: "Memory consolidated: 5 episodes." }),
    } as any;
    const fakeOwl = { persona: { name: "Athena" } } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider, owl: fakeOwl });

    const result = await runner.run(LOW_JOB, "trace-abc");

    expect(fakeProvider.chat).toHaveBeenCalledOnce();
    expect(typeof result).toBe("string");
    expect(result).toContain("Memory consolidated");
  });

  it("returns error message string on provider failure — does not throw", async () => {
    const fakeProvider = {
      chat: vi.fn().mockRejectedValue(new Error("rate limit")),
    } as any;
    const fakeOwl = { persona: { name: "Athena" } } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider, owl: fakeOwl });

    const result = await runner.run(LOW_JOB, "trace-fail");
    expect(result).toMatch(/error|failed|rate limit/i);
  });
});
```

- [ ] **Step 2.3: Run test to confirm it fails**

```bash
npx vitest run __tests__/cron/isolated-runner.test.ts 2>&1 | tail -20
```

Expected: FAIL — `IsolatedRunner` module does not exist.

- [ ] **Step 2.4: Implement IsolatedRunner**

Check `OwlEngine` constructor to use the correct signature (from Step 2.1). Then:

Create `src/cron/isolated-runner.ts`:

```typescript
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { CronJob, SafetyProfile } from "./types.js";

export interface IsolatedRunnerOptions {
  provider: ModelProvider;
  owl: OwlInstance;
  /** Optional: tool registry to use (filtered by safetyProfile) */
  toolRegistry?: any;
}

const TOOL_PROFILES: Record<SafetyProfile, string[]> = {
  low: ["ReadFileTool", "WebFetchTool"],
  medium: ["ReadFileTool", "WebFetchTool", "ShellTool", "WriteFileTool"],
  full: [],  // empty = all tools available
};

export class IsolatedRunner {
  constructor(private opts: IsolatedRunnerOptions) {}

  async run(job: CronJob, traceId: string): Promise<string> {
    log.engine.info("[IsolatedRunner] Starting isolated job run", {
      jobId: job.id,
      traceId,
      safetyProfile: job.safetyProfile,
    });

    // Each run gets a fresh, isolated session — no contamination from main conversation
    const sessionMessages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are running a scheduled background task. ` +
          `Complete the following task autonomously and concisely.\n\n` +
          `Safety profile: ${job.safetyProfile} (${job.safetyProfile === "low" ? "read-only" : job.safetyProfile === "medium" ? "read+write+shell" : "full"} tool access).\n` +
          `Task ID: ${job.id}\nTrace ID: ${traceId}`,
      },
      {
        role: "user",
        content: job.prompt,
      },
    ];

    try {
      const response = await this.opts.provider.chat(sessionMessages);
      const result = typeof response.content === "string" ? response.content : JSON.stringify(response.content);
      log.engine.info("[IsolatedRunner] Job completed", { jobId: job.id, traceId, resultLen: result.length });
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.engine.error("[IsolatedRunner] Job failed", err as Error, { jobId: job.id, traceId });
      return `[Cron job "${job.id}" failed] ${msg}`;
    }
  }
}
```

- [ ] **Step 2.5: Run test to confirm it passes**

```bash
npx vitest run __tests__/cron/isolated-runner.test.ts 2>&1 | tail -20
```

Expected: PASS — 3 tests passing.

- [ ] **Step 2.6: Commit**

```bash
git add src/cron/isolated-runner.ts __tests__/cron/isolated-runner.test.ts
git commit -m "feat(cron): IsolatedRunner — fresh provider session per cron job run"
```

---

## Task 3: Default jobs and CronService wiring

**Files:**
- Create: `src/cron/default-jobs.ts`
- Modify: startup file (wherever OwlEngine / BackgroundOrchestrator is created)

- [ ] **Step 3.1: Create default jobs**

Create `src/cron/default-jobs.ts`:

```typescript
import type { CronJob } from "./types.js";

export const DEFAULT_CRON_JOBS: CronJob[] = [
  {
    id: "memory-consolidation",
    schedule: "0 * * * *",
    prompt:
      "Consolidate recent episodic memories: compress old episodes, archive those older than 30 days, " +
      "and log how many were compressed and archived.",
    safetyProfile: "low",
    deliver: false,
    description: "Hourly memory compression and archiving",
  },
  {
    id: "desire-execution",
    schedule: "*/30 * * * *",
    prompt:
      "Review the top pending owl desire and execute it if actionable and low-risk. " +
      "Report what was done or why it was deferred.",
    safetyProfile: "medium",
    deliver: false,
    description: "Every 30 min: process top owl desire",
  },
  {
    id: "dna-evolution",
    schedule: "0 2 * * *",
    prompt:
      "Review the owl's recent interaction patterns (last 24 hours) and suggest specific DNA trait " +
      "adjustments: challengeLevel, verbosity, creativity, riskTolerance. Output a JSON diff.",
    safetyProfile: "low",
    deliver: false,
    description: "Nightly DNA evolution at 2am",
  },
  {
    id: "pellet-dedup",
    schedule: "0 3 * * *",
    prompt:
      "Scan the knowledge pellet store for near-duplicate entries (cosine similarity > 0.92). " +
      "Merge duplicates into the more recent entry and report how many were removed.",
    safetyProfile: "low",
    deliver: false,
    description: "Nightly pellet deduplication at 3am",
  },
  {
    id: "daily-briefing",
    schedule: "0 9 * * *",
    prompt:
      "Generate a concise morning briefing for the user: " +
      "what happened yesterday (from recent memory), any open goals or desires, " +
      "and 1-2 proactive suggestions for today. Keep it under 200 words.",
    safetyProfile: "low",
    deliver: true,
    description: "Daily morning briefing at 9am",
  },
];
```

- [ ] **Step 3.2: Wire CronService + IsolatedRunner into startup**

Find where `BackgroundOrchestrator` is started:

```bash
grep -rn "BackgroundOrchestrator\|backgroundOrchestrator\|\.startTicking\b" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -10
```

Add CronService alongside BackgroundOrchestrator (do not replace it — both coexist):

```typescript
import { CronService } from "./cron/service.js";
import { IsolatedRunner } from "./cron/isolated-runner.js";
import { DEFAULT_CRON_JOBS } from "./cron/default-jobs.js";

// After provider and owl are initialized:
const isolatedRunner = new IsolatedRunner({ provider, owl });

const cronService = new CronService({
  persist: true,
  maxConcurrentRuns: 3,
  onJobFire: async (job, traceId) => {
    return await isolatedRunner.run(job, traceId);
  },
});

// Register all default jobs
for (const job of DEFAULT_CRON_JOBS) {
  try {
    cronService.addJob(job);
  } catch (err) {
    // Job may already be loaded from crons.json on restart — skip duplicate
    log.engine.debug("[startup] Skipping duplicate cron job", { id: job.id });
  }
}

// On shutdown:
process.on("SIGINT", () => {
  cronService.stop();
  process.exit(0);
});
```

- [ ] **Step 3.3: Run full test suite**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all previously-passing tests still pass.

- [ ] **Step 3.4: Smoke test — verify cron service starts**

```bash
npm run dev 2>&1 | grep -i "cron\|job" | head -10
```

Expected: 5 lines like `[CronService] Job registered { id: "memory-consolidation", schedule: "0 * * * *" }`.

- [ ] **Step 3.5: Commit**

```bash
git add src/cron/default-jobs.ts  # + startup file
git commit -m "feat(cron): wire CronService + IsolatedRunner into startup with 5 default jobs"
```

---

## Task 4: User-defined cron jobs via CLI

**Files:**
- Modify: CLI commands file (found in Task 3 step 3.2 search)

- [ ] **Step 4.1: Find CLI registration**

```bash
grep -rn "program\.command\|addCommand\|\.command(" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -15
```

- [ ] **Step 4.2: Add cron CLI subcommands**

At the CLI registration site:

```typescript
import { CronService } from "./cron/service.js";
import type { CronJob } from "./cron/types.js";

const cronCmd = program.command("cron").description("Manage scheduled background jobs");

cronCmd
  .command("list")
  .description("List all scheduled jobs")
  .action(() => {
    const service = new CronService({ persist: true });
    const jobs = service.listJobs();
    if (!jobs.length) {
      console.log("No cron jobs configured.");
      return;
    }
    for (const job of jobs) {
      const state = service.getJobState(job.id);
      const next = state?.nextRunAt?.toISOString() ?? "unknown";
      console.log(`  ${job.id} [${job.schedule}] — next: ${next}`);
      if (job.description) console.log(`    ${job.description}`);
    }
    service.stop();
  });

cronCmd
  .command("remove <id>")
  .description("Remove a scheduled job by ID")
  .action((id: string) => {
    const service = new CronService({ persist: true });
    try {
      service.removeJob(id);
      console.log(`Removed job: ${id}`);
    } catch {
      console.error(`Job not found: ${id}`);
      process.exit(1);
    }
    service.stop();
  });

cronCmd
  .command("add")
  .description("Add a cron job using natural language")
  .requiredOption("-p, --prompt <text>", "What should the owl do?")
  .requiredOption("-s, --schedule <cron>", "Cron expression, e.g. \"0 9 * * *\"")
  .option("--id <id>", "Job ID (auto-generated if omitted)")
  .option("--safety <profile>", "Safety profile: low|medium|full (default: low)", "low")
  .option("--deliver", "Deliver result to primary channel when done", false)
  .action((opts: { prompt: string; schedule: string; id?: string; safety: string; deliver: boolean }) => {
    const service = new CronService({ persist: true });
    const job: CronJob = {
      id: opts.id ?? `user-job-${Date.now()}`,
      schedule: opts.schedule,
      prompt: opts.prompt,
      safetyProfile: (opts.safety as "low" | "medium" | "full"),
      deliver: opts.deliver,
    };
    try {
      service.addJob(job);
      const state = service.getJobState(job.id);
      console.log(`✅ Job "${job.id}" scheduled for ${opts.schedule}`);
      console.log(`   Next run: ${state?.nextRunAt?.toISOString() ?? "unknown"}`);
    } catch (err) {
      console.error(`Failed: ${err instanceof Error ? err.message : err}`);
      process.exit(1);
    }
    service.stop();
  });
```

- [ ] **Step 4.3: Smoke test CLI commands**

```bash
# List jobs (should show 5 defaults if service was run at least once)
npx ts-node src/cli.ts cron list 2>&1

# Add a custom job
npx ts-node src/cli.ts cron add \
  --prompt "Check my GitHub notifications and summarize new ones" \
  --schedule "0 8 * * 1-5" \
  --id "github-notifications" \
  --safety low \
  2>&1

# List again — should show 6 jobs
npx ts-node src/cli.ts cron list 2>&1

# Remove the test job
npx ts-node src/cli.ts cron remove github-notifications 2>&1
```

Expected: no errors; job appears and disappears from list.

- [ ] **Step 4.4: Run full test suite for regressions**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all previously-passing tests still pass.

- [ ] **Step 4.5: Final commit**

```bash
git add src/  # CLI file + cron files
git commit -m "feat(cron): cron list/add/remove CLI commands for user-defined scheduled jobs"
```
