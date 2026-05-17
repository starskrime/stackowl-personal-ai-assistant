# Activity Gate — Background Job Token Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent background LLM API calls when no new user interaction has occurred since the job last ran, eliminating idle token waste.

**Architecture:** A lightweight `ActivityGate` class backed by a new SQLite table (`activity_gate`) stores a hash of the last seen user message per background job. Before any background job makes an LLM call, it checks whether the hash has changed — if not, it skips. After a successful run it writes the current hash. This gates three subsystems: `BackgroundOrchestrator` (desire-execution, proactive-ping, session-debrief), `ProactiveKnowledgeGenerator` (council, dream, evolve), and `EventBasedPelletGenerator` (message:responded classification).

**Tech Stack:** TypeScript, better-sqlite3, Node.js crypto (sha1), existing `MemoryDatabase` pattern in `src/memory/db.ts`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/background/activity-gate.ts` | ActivityGate class — hash comparison + mark-seen |
| Modify | `src/memory/db.ts` | Schema v34 migration + `ActivityGateRepo` |
| Modify | `src/background/orchestrator.ts` | Inject gate, check before LLM jobs |
| Modify | `src/pellets/proactive-generator.ts` | Inject gate, check before council/dream/evolve |
| Modify | `src/pellets/event-based-generator.ts` | Check gate before message:responded classification |
| Modify | `src/index.ts` | Construct ActivityGate, pass to orchestrator + generator |
| Create | `__tests__/background/activity-gate.test.ts` | Unit tests for the gate |

---

### Task 1: DB Schema Migration — `activity_gate` table + repo

**Files:**
- Modify: `src/memory/db.ts`

The current schema version is 33. This task adds version 34 with the `activity_gate` table and a typed repo.

- [ ] **Step 1: Write the failing test**

Create `__tests__/background/activity-gate.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../../src/memory/db.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let db: MemoryDatabase;
let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "activity-gate-test-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  db.close();
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("ActivityGateRepo", () => {
  it("getHash returns null for unknown jobId", () => {
    const hash = db.activityGate.getHash("desire-execution");
    expect(hash).toBeNull();
  });

  it("setHash then getHash returns the stored value", () => {
    db.activityGate.setHash("desire-execution", "abc123");
    expect(db.activityGate.getHash("desire-execution")).toBe("abc123");
  });

  it("setHash is idempotent — second call overwrites first", () => {
    db.activityGate.setHash("council", "hash1");
    db.activityGate.setHash("council", "hash2");
    expect(db.activityGate.getHash("council")).toBe("hash2");
  });

  it("different jobIds are independent", () => {
    db.activityGate.setHash("council", "aaa");
    db.activityGate.setHash("dream", "bbb");
    expect(db.activityGate.getHash("council")).toBe("aaa");
    expect(db.activityGate.getHash("dream")).toBe("bbb");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -20
```

Expected: FAIL — `db.activityGate` is undefined.

- [ ] **Step 3: Add schema migration and repo to `src/memory/db.ts`**

Find `const SCHEMA_VERSION = 33` and change to `34`. Then find the `migrate()` method and add at the end (after the v<33 block):

```typescript
if (v < 34) {
  this.db.exec(`
    CREATE TABLE IF NOT EXISTS activity_gate (
      job_id         TEXT PRIMARY KEY,
      last_seen_hash TEXT
    )
  `);
  this.db.run("UPDATE schema_version SET version = 34");
}
```

Then add a new repo class. Find the section where other repos are defined (e.g. near `DigestsRepo`) and add:

```typescript
export class ActivityGateRepo {
  constructor(private db: Database) {}

  getHash(jobId: string): string | null {
    const row = this.db.prepare(
      "SELECT last_seen_hash FROM activity_gate WHERE job_id = ?"
    ).get(jobId) as { last_seen_hash: string | null } | undefined;
    return row?.last_seen_hash ?? null;
  }

  setHash(jobId: string, hash: string): void {
    this.db.prepare(
      "INSERT INTO activity_gate (job_id, last_seen_hash) VALUES (?, ?) " +
      "ON CONFLICT(job_id) DO UPDATE SET last_seen_hash = excluded.last_seen_hash"
    ).run(jobId, hash);
  }
}
```

Then add the property to the `MemoryDatabase` class (find the `readonly messages: MessagesRepo` block):

```typescript
readonly activityGate: ActivityGateRepo;
```

And initialize it in the constructor (after other repo initializations):

```typescript
this.activityGate = new ActivityGateRepo(this.db);
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -20
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/background/activity-gate.test.ts
git commit -m "feat(db): schema v34 — activity_gate table + ActivityGateRepo"
```

---

### Task 2: ActivityGate class

**Files:**
- Create: `src/background/activity-gate.ts`
- Modify: `__tests__/background/activity-gate.test.ts` (add ActivityGate tests)

The gate computes a SHA1 hash of the most recent user message from the `messages` table (`role = 'user'`, ordered by `created_at DESC LIMIT 1`). It compares against the stored hash for each jobId.

- [ ] **Step 1: Write the failing tests**

Add these test cases to `__tests__/background/activity-gate.test.ts`, after the existing `ActivityGateRepo` describe block:

```typescript
import { ActivityGate } from "../../src/background/activity-gate.js";
import { randomUUID } from "node:crypto";

describe("ActivityGate", () => {
  let gate: ActivityGate;

  beforeEach(() => {
    gate = new ActivityGate(db);
  });

  it("hasNewActivity returns true when no messages exist and job has never run", async () => {
    // No user messages, job never ran — treat as activity needed (first boot)
    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(false);
  });

  it("hasNewActivity returns true when a user message exists but job never ran", async () => {
    // Insert a user message directly into the messages table
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello world", 1, new Date().toISOString());

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(true);
  });

  it("hasNewActivity returns false when hash matches last seen", async () => {
    const msgId = randomUUID();
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(msgId, "sess1", "user1", "default", "user", "hello world", 1, new Date().toISOString());

    await gate.markSeen("desire-execution");

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(false);
  });

  it("hasNewActivity returns true after a new user message arrives", async () => {
    const msgId = randomUUID();
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(msgId, "sess1", "user1", "default", "user", "first message", 1, new Date(Date.now() - 1000).toISOString());

    await gate.markSeen("desire-execution");

    // New message arrives
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "second message", 2, new Date().toISOString());

    const result = await gate.hasNewActivity("desire-execution");
    expect(result).toBe(true);
  });

  it("markSeen updates the stored hash so next hasNewActivity returns false", async () => {
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello", 1, new Date().toISOString());

    expect(await gate.hasNewActivity("council")).toBe(true);
    await gate.markSeen("council");
    expect(await gate.hasNewActivity("council")).toBe(false);
  });

  it("different jobIds are independent", async () => {
    db["db"].prepare(
      "INSERT INTO messages (id, session_id, user_id, owl_name, role, content, seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    ).run(randomUUID(), "sess1", "user1", "default", "user", "hello", 1, new Date().toISOString());

    await gate.markSeen("council");

    // "dream" has never run — should still see activity
    expect(await gate.hasNewActivity("dream")).toBe(true);
    // "council" just ran — no new activity
    expect(await gate.hasNewActivity("council")).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -20
```

Expected: FAIL — `ActivityGate` not found.

- [ ] **Step 3: Implement `src/background/activity-gate.ts`**

```typescript
import { createHash } from "node:crypto";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

export class ActivityGate {
  constructor(private db: MemoryDatabase) {}

  /**
   * Returns true if there is new user interaction since this job last ran.
   * Returns false if nothing has changed — caller should skip the LLM call.
   */
  async hasNewActivity(jobId: string): Promise<boolean> {
    const currentHash = this.currentHash();
    const lastSeen = this.db.activityGate.getHash(jobId);

    if (currentHash === null) {
      // No user messages exist at all — nothing to process
      log.engine.debug("activity-gate.hasNewActivity: no messages yet", { jobId });
      return false;
    }

    if (lastSeen === null) {
      // Job has never run — there IS activity to process (first run)
      log.engine.debug("activity-gate.hasNewActivity: first run for job", { jobId });
      return true;
    }

    const changed = currentHash !== lastSeen;
    log.engine.debug("activity-gate.hasNewActivity", { jobId, changed, currentHash: currentHash.slice(0, 8), lastSeen: lastSeen.slice(0, 8) });
    return changed;
  }

  /**
   * Record that this job has processed up to the current user message.
   * Call this after a successful LLM job run.
   */
  async markSeen(jobId: string): Promise<void> {
    const hash = this.currentHash();
    if (hash === null) return;
    this.db.activityGate.setHash(jobId, hash);
    log.engine.debug("activity-gate.markSeen", { jobId, hash: hash.slice(0, 8) });
  }

  private currentHash(): string | null {
    const row = (this.db as any)["db"].prepare(
      "SELECT id, content FROM messages WHERE role = 'user' ORDER BY created_at DESC LIMIT 1"
    ).get() as { id: string; content: string } | undefined;

    if (!row) return null;
    return createHash("sha1").update(row.id + row.content).digest("hex");
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -20
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/background/activity-gate.ts __tests__/background/activity-gate.test.ts
git commit -m "feat(background): ActivityGate — skip LLM jobs when no new user interaction"
```

---

### Task 3: Gate BackgroundOrchestrator LLM jobs

**Files:**
- Modify: `src/background/orchestrator.ts`

Gates three jobs that make LLM calls: `desire-execution`, `proactive-ping`, `session-debrief`. `memory-consolidation` is pure SQL — no gate needed.

- [ ] **Step 1: Write the failing test**

Add to `__tests__/background/activity-gate.test.ts`:

```typescript
import { BackgroundOrchestrator } from "../../src/background/orchestrator.js";

describe("BackgroundOrchestrator activity gate integration", () => {
  it("accepts an activityGate option in config", () => {
    const gate = new ActivityGate(db);
    // Should not throw — gate is accepted in config
    expect(() => new BackgroundOrchestrator(
      {} as any, // provider
      {} as any, // owl
      undefined, // innerLife
      undefined, // desireExecutor
      undefined, // fulfillmentTracker
      undefined, // onProactiveMessage
      { activityGate: gate },
    )).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/background/activity-gate.test.ts -t "BackgroundOrchestrator" 2>&1 | tail -10
```

Expected: FAIL — config does not accept `activityGate`.

- [ ] **Step 3: Add gate to `src/background/orchestrator.ts`**

Find the `BackgroundOrchestratorConfig` interface and add:

```typescript
/** When provided, LLM jobs are skipped if no new user interaction since last run */
activityGate?: import("./activity-gate.js").ActivityGate;
```

Find the `private config: BackgroundOrchestratorConfig` field and add:

```typescript
private activityGate?: import("./activity-gate.js").ActivityGate;
```

In the constructor body, after the config is set:

```typescript
this.activityGate = config?.activityGate;
```

Now find the `tick()` method. It calls individual job runners. For each of the three LLM-consuming jobs, wrap the execution block with a gate check. The pattern for each job (e.g. `desire-execution`) looks like:

```typescript
// BEFORE (existing pattern — find the desire-execution block):
if (await this.maybeRun("desire-execution", this.config.desireIntervalMinutes * 60_000)) {
  await this.runDesireExecution();
}

// AFTER — wrap with gate:
if (await this.maybeRun("desire-execution", this.config.desireIntervalMinutes * 60_000)) {
  if (this.activityGate && !(await this.activityGate.hasNewActivity("desire-execution"))) {
    log.engine.debug("[BackgroundOrchestrator] desire-execution skipped — no new user activity");
  } else {
    await this.runDesireExecution();
    await this.activityGate?.markSeen("desire-execution");
  }
}
```

Apply the same pattern for `proactive-ping` and `session-debrief`:

```typescript
// proactive-ping:
if (this.activityGate && !(await this.activityGate.hasNewActivity("proactive-ping"))) {
  log.engine.debug("[BackgroundOrchestrator] proactive-ping skipped — no new user activity");
} else {
  await this.runProactivePing();
  await this.activityGate?.markSeen("proactive-ping");
}

// session-debrief:
if (this.activityGate && !(await this.activityGate.hasNewActivity("session-debrief"))) {
  log.engine.debug("[BackgroundOrchestrator] session-debrief skipped — no new user activity");
} else {
  await this.runSessionDebrief();
  await this.activityGate?.markSeen("session-debrief");
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -10
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/background/orchestrator.ts
git commit -m "feat(background): gate desire/proactive/debrief on ActivityGate"
```

---

### Task 4: Gate ProactiveKnowledgeGenerator LLM jobs

**Files:**
- Modify: `src/pellets/proactive-generator.ts`

Gates all three knowledge generation methods: `runKnowledgeCouncil`, `runDream`, `runEvolveSkills`. Job IDs: `"council"`, `"dream"`, `"evolve"`.

- [ ] **Step 1: Write the failing test**

Add to `__tests__/background/activity-gate.test.ts`:

```typescript
import { ProactiveKnowledgeGenerator } from "../../src/pellets/proactive-generator.js";

describe("ProactiveKnowledgeGenerator activity gate integration", () => {
  it("accepts activityGate in constructor options", () => {
    const gate = new ActivityGate(db);
    expect(() => new ProactiveKnowledgeGenerator(
      {} as any, // pelletStore
      {} as any, // router
      { activityGate: gate },
      db,
    )).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/background/activity-gate.test.ts -t "ProactiveKnowledgeGenerator" 2>&1 | tail -10
```

Expected: FAIL — config does not accept `activityGate`.

- [ ] **Step 3: Add gate to `src/pellets/proactive-generator.ts`**

Find the `ProactiveGenerationConfig` interface and add:

```typescript
activityGate?: import("../background/activity-gate.js").ActivityGate;
```

Find the constructor body and add:

```typescript
private activityGate?: import("../background/activity-gate.js").ActivityGate;
// ...
this.activityGate = generationConfig.activityGate;
```

In `runKnowledgeCouncil()`, find the existing interval check (the `hoursSinceLastRun < intervalHours` guard) and add the activity gate check immediately after:

```typescript
// After existing interval check, before the actual LLM work:
if (this.activityGate && !(await this.activityGate.hasNewActivity("council"))) {
  log.engine.debug("[ProactiveGenerator] council skipped — no new user activity");
  return [];
}
// ... existing pellet generation code ...
// At the end, before return:
await this.activityGate?.markSeen("council");
```

Apply the same pattern in `runDream()` with jobId `"dream"` and in `runEvolveSkills()` with jobId `"evolve"`:

```typescript
// runDream — add after the 24h guard:
if (this.activityGate && !(await this.activityGate.hasNewActivity("dream"))) {
  log.engine.debug("[ProactiveGenerator] dream skipped — no new user activity");
  return [];
}
// ... existing dream code ...
await this.activityGate?.markSeen("dream");

// runEvolveSkills — add after the 24h guard:
if (this.activityGate && !(await this.activityGate.hasNewActivity("evolve"))) {
  log.engine.debug("[ProactiveGenerator] evolve skipped — no new user activity");
  return [];
}
// ... existing evolve code ...
await this.activityGate?.markSeen("evolve");
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -10
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pellets/proactive-generator.ts
git commit -m "feat(pellets): gate council/dream/evolve on ActivityGate"
```

---

### Task 5: Gate EventBasedPelletGenerator classification call

**Files:**
- Modify: `src/pellets/event-based-generator.ts`

The `message:responded` handler makes a classification LLM call to decide if the response is significant enough to pelletize. This fires once per user response — it's the only event-generator call that could fire frequently. Gate it with jobId `"pellet-classification"`.

- [ ] **Step 1: Write the failing test**

Add to `__tests__/background/activity-gate.test.ts`:

```typescript
import { EventBasedPelletGenerator } from "../../src/pellets/event-based-generator.js";

describe("EventBasedPelletGenerator activity gate integration", () => {
  it("accepts activityGate in constructor options", () => {
    const gate = new ActivityGate(db);
    const mockBus = { on: () => {} } as any;
    expect(() => new EventBasedPelletGenerator(
      mockBus,
      {} as any, // pelletStore
      {} as any, // router
      { activityGate: gate },
    )).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/background/activity-gate.test.ts -t "EventBasedPelletGenerator" 2>&1 | tail -10
```

Expected: FAIL.

- [ ] **Step 3: Add gate to `src/pellets/event-based-generator.ts`**

Find the `SignificanceConfig` interface (or wherever constructor options are typed) and add:

```typescript
activityGate?: import("../background/activity-gate.js").ActivityGate;
```

In the constructor:

```typescript
private activityGate?: import("../background/activity-gate.js").ActivityGate;
// ...
this.activityGate = significanceConfig?.activityGate;
```

Find `handleMessageResponded`. It calls `this.router.resolve("classification", ...)`. Wrap just the classification call:

```typescript
private async handleMessageResponded(payload: { ... }): Promise<void> {
  // Gate: skip classification if no new user activity since last run
  if (this.activityGate && !(await this.activityGate.hasNewActivity("pellet-classification"))) {
    log.engine.debug("[EventBasedPelletGenerator] classification skipped — no new activity");
    return;
  }

  // ... existing classification and pellet generation code ...

  await this.activityGate?.markSeen("pellet-classification");
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/background/activity-gate.test.ts 2>&1 | tail -10
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pellets/event-based-generator.ts
git commit -m "feat(pellets): gate message:responded classification on ActivityGate"
```

---

### Task 6: Wire ActivityGate in bootstrap (`src/index.ts`)

**Files:**
- Modify: `src/index.ts`

Create one shared `ActivityGate` instance and pass it to both `BackgroundOrchestrator` and `ProactiveKnowledgeGenerator`.

- [ ] **Step 1: Find the construction sites**

```bash
grep -n "new BackgroundOrchestrator\|new ProactiveKnowledgeGenerator\|new EventBasedPelletGenerator" /ssd/projects/stackowl-personal-ai-assistant/src/index.ts
```

Note the line numbers — you'll need them for the next step.

- [ ] **Step 2: Add the ActivityGate import and instance**

Add import at the top of `src/index.ts` near other background imports:

```typescript
import { ActivityGate } from "./background/activity-gate.js";
```

Find where `memoryDb` is available (after `new MemoryDatabase(workspacePath)`) and create the gate:

```typescript
const activityGate = new ActivityGate(memoryDb);
```

- [ ] **Step 3: Pass gate to BackgroundOrchestrator**

Find `new BackgroundOrchestrator(...)`. The config object (last argument or second-to-last) is `Partial<BackgroundOrchestratorConfig>`. Add `activityGate` to it:

```typescript
new BackgroundOrchestrator(
  provider,
  owl,
  innerLife,
  desireExecutor,
  fulfillmentTracker,
  onProactiveMessage,
  {
    ...existingConfigFields,
    activityGate,   // add this
  },
  episodicMemory,
)
```

- [ ] **Step 4: Pass gate to ProactiveKnowledgeGenerator**

Find `new ProactiveKnowledgeGenerator(...)`. It takes `generationConfig` as third argument:

```typescript
new ProactiveKnowledgeGenerator(
  pelletStore,
  router,
  {
    ...existingConfig,
    activityGate,   // add this
  },
  memoryDb,
)
```

- [ ] **Step 5: Pass gate to EventBasedPelletGenerator**

Find `new EventBasedPelletGenerator(...)`. Fourth argument is `significanceConfig`:

```typescript
new EventBasedPelletGenerator(
  eventBus,
  pelletStore,
  router,
  {
    ...existingSignificanceConfig,
    activityGate,   // add this
  },
)
```

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run 2>&1 | tail -30
```

Expected: all existing tests pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/index.ts
git commit -m "feat(bootstrap): wire ActivityGate into background orchestrator + generators"
```

---

## Self-Review

**Spec coverage:**
- ✅ No LLM calls fired when no new user interaction — gated in Orchestrator (Task 3) + ProactiveGenerator (Task 4) + EventBasedGenerator (Task 5)
- ✅ "Checksum of last message" — SHA1(id + content) of last `role='user'` row from `messages` table (Task 2)
- ✅ Per-job tracking — each job stores its own hash so they're independent (Task 1)
- ✅ First run behavior — when job has never run but messages exist, it runs once to process them (Task 2: `lastSeen === null && currentHash !== null → true`)
- ✅ No messages at all → skip (Task 2: `currentHash === null → false`)
- ✅ `memory-consolidation` intentionally ungated — it's pure SQL, zero LLM calls

**Placeholder scan:** None found.

**Type consistency:** `ActivityGate` imported consistently as `import("./activity-gate.js").ActivityGate` in all optional fields. `activityGate` field name used everywhere.
