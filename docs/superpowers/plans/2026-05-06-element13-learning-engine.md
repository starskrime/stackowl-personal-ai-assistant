# Element 13 — Learning Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 11 adaptive learning gaps in StackOwl — collapse twin-engine, wire proactive sessions with evidence-based triggers, close the BLOCKED/PARTIAL → owl_learnings feedback loop, add memory admission/eviction, and delete ~1,116 lines of dead code.

**Architecture:** Zero new files; −3 deleted files (self-study.ts, approach-library.ts, mistake-detector.ts); 9 modified files. All learning is inference-time only. New DB methods are added to existing repo classes in db.ts. OwlLearningsLayer is wired to db via constructor injection (same pattern as CritiqueRetriever).

**Tech Stack:** TypeScript, better-sqlite3, Vitest (real SQLite, no mocks), existing ModelProvider.chat() for critique generation.

**Spec:** `docs/superpowers/specs/2026-05-05-element13-learning-engine-design.md`

> **Note for implementer:** The spec has 4 discrepancies from actual code that this plan corrects:
> 1. `approach_library` column is `task_keywords`, not `task_description`
> 2. `db.trajectories` is the accessor (not `db.trajectoryTurns`) — `TrajectoriesRepo` lives at `db.trajectories`
> 3. `getSessionFailures` requires a JOIN with `trajectories` table (no direct `session_id` on `trajectory_turns`)
> 4. D3 critique generation uses `this.ctx.provider.chat()` (returns `ChatResponse`), not `ctx.intelligenceRouter.classify()`

---

## File Structure

### Files Deleted (3)
| File | Why |
|------|-----|
| `src/learning/self-study.ts` | Twin-engine collapse (D1). 647 lines. Deleted in Task 7 after all callers migrated in Tasks 5–6. |
| `src/learning/approach-library.ts` | Dead code. 195 lines. Deleted in Task 2. |
| `src/learning/mistake-detector.ts` | Dead code. 401 lines. Jaccard ported to `ApproachLibraryRepo`. Deleted in Task 2. |

### Files Modified (9)
| File | Task | Changes |
|------|------|---------|
| `src/memory/db.ts` | 1 | Add `computeSimilarity`, `tokenize`; add methods to `TrajectoriesRepo`, `OwlLearningsRepo`, `ApproachLibraryRepo` |
| `src/learning/orchestrator.ts` | 3 | Add `ProactiveContext` interface; implement `runProactiveSession()` |
| `src/gateway/handlers/post-processor.ts` | 4 | Add `"learning-failure-critique"` background job |
| `src/heartbeat/idle-engine.ts` | 5 | Migrate from `LearningEngine` to `LearningOrchestrator + MemoryDatabase` |
| `src/index.ts` | 6 | Remove `LearningEngine` import + factory |
| `src/cognition/loop.ts` | 7 | Remove `LearningEngine` import + optional field |
| `src/heartbeat/proactive.ts` | 7 | Remove legacy `learningEngine` branch |
| `src/heartbeat/planner.ts` | 7 | Remove `learningEngine` dep |
| `src/cli/commands.ts` | 7 | Use `getLearningOrchestrator()` + `getFullReport()` |
| `src/gateway/types.ts` | 7 | Remove `learningEngine?: LearningEngine` from `GatewayContext` |
| `src/gateway/core.ts` | 7 | Remove `getLearningEngine()`, remove fallback branches |
| `src/intelligence/sleep-time-consolidator.ts` | 8 | Add eviction SQL before LLM pellet step |
| `src/gateway/core.ts` | 8 | Fix `core.ts:2353` G5 success/failure signal |
| `src/context/layers/behavioral.ts` | 8 | Wire `OwlLearningsLayer` to DB; change slice to 6 |
| `src/context/index.ts` | 8 | Pass `deps.db` to `OwlLearningsLayer` constructor |
| `src/learning/micro-learner.ts` | 8 | Emit `style` + `temporal` signals |

### Test Files Created/Extended (5)
| File | Task |
|------|------|
| `__tests__/memory-db-learning.test.ts` | 1 (create) |
| `__tests__/learning-orchestrator-proactive.test.ts` | 3 (create) |
| `__tests__/post-processor-critique.test.ts` | 4 (create) |
| `__tests__/idle-engine-orchestrator.test.ts` | 5 (create) |
| `__tests__/micro-learner.test.ts` | 8 (extend) |

---

## Task 1: DB Methods + Helper Functions

**Files:**
- Create: `__tests__/memory-db-learning.test.ts`
- Modify: `src/memory/db.ts`

- [ ] **Step 1.1: Write the failing test file**

```typescript
// __tests__/memory-db-learning.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { MemoryDatabase } from "../src/memory/db.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-learning-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ─── TrajectoriesRepo.getFailureDensityTopics ─────────────────────

describe("TrajectoriesRepo.getFailureDensityTopics", () => {
  function insertTurn(
    trajectoryId: string,
    toolName: string,
    verificationResult: string,
    createdAt?: string,
  ) {
    (db as any).rawDb.prepare(`
      INSERT INTO trajectories (id, session_id, owl_name, user_message)
      VALUES (?, 'sess1', 'owl1', 'test')
    `).run(trajectoryId);
    (db as any).rawDb.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, verification_result, created_at)
      VALUES (?, ?, 0, ?, '', '', 0, ?, ?)
    `).run(
      `turn_${Math.random()}`,
      trajectoryId,
      toolName,
      verificationResult,
      createdAt ?? new Date().toISOString(),
    );
  }

  it("returns tools meeting threshold", () => {
    insertTurn("t1", "web_fetch", "BLOCKED");
    insertTurn("t2", "web_fetch", "BLOCKED");
    insertTurn("t3", "web_fetch", "BLOCKED");
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).toContain("web_fetch");
  });

  it("excludes tools below min occurrences", () => {
    insertTurn("t4", "rare_tool", "BLOCKED");
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).not.toContain("rare_tool");
  });

  it("respects daysBack window", () => {
    const old = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();
    insertTurn("t5", "old_tool", "BLOCKED", old);
    insertTurn("t6", "old_tool", "BLOCKED", old);
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).not.toContain("old_tool");
  });

  it("returns [] gracefully on missing table", () => {
    // Raw drop to simulate missing table
    (db as any).rawDb.prepare("DROP TABLE IF EXISTS trajectory_turns").run();
    const result = db.trajectories.getFailureDensityTopics(7, 2);
    expect(result).toEqual([]);
  });
});

// ─── TrajectoriesRepo.getSessionFailures ─────────────────────────

describe("TrajectoriesRepo.getSessionFailures", () => {
  function insertTurnForSession(
    sessionId: string,
    toolName: string,
    verificationResult: string,
  ) {
    const tId = `traj_${Math.random()}`;
    (db as any).rawDb.prepare(`
      INSERT INTO trajectories (id, session_id, owl_name, user_message)
      VALUES (?, ?, 'owl1', 'test')
    `).run(tId, sessionId);
    (db as any).rawDb.prepare(`
      INSERT INTO trajectory_turns
        (id, trajectory_id, turn_index, tool_name, args_snapshot, result_snapshot, success, verification_result)
      VALUES (?, ?, 0, ?, '', '', 0, ?)
    `).run(`turn_${Math.random()}`, tId, toolName, verificationResult);
  }

  it("returns only BLOCKED and PARTIAL turns for the given session", () => {
    insertTurnForSession("sess_a", "web_fetch", "BLOCKED");
    insertTurnForSession("sess_a", "shell", "PARTIAL");
    insertTurnForSession("sess_a", "read", "ADVANCES");
    insertTurnForSession("sess_b", "web_fetch", "BLOCKED");

    const result = db.trajectories.getSessionFailures("sess_a");
    expect(result).toHaveLength(2);
    expect(result.map((r) => r.tool_name)).toEqual(
      expect.arrayContaining(["web_fetch", "shell"]),
    );
  });

  it("returns [] when no failures for session", () => {
    const result = db.trajectories.getSessionFailures("nonexistent_sess");
    expect(result).toEqual([]);
  });
});

// ─── OwlLearningsRepo.admitIfWorthy ───────────────────────────────

describe("OwlLearningsRepo.admitIfWorthy", () => {
  it("admits a novel entry", () => {
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "never use web_fetch for large files",
      "failure",
      0.6,
    );
    expect(result).not.toBeNull();
    expect(result?.id).toBeTruthy();
  });

  it("rejects near-duplicate within 30 days (Jaccard >= 0.6)", () => {
    db.owlLearnings.admitIfWorthy(
      "owl1",
      "avoid using web fetch for large downloads",
      "failure",
      0.6,
    );
    // Highly similar sentence
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "avoid using web fetch for large downloads",
      "failure",
      0.6,
    );
    expect(result).toBeNull();
  });

  it("admits the same text if the prior entry is older than 30 days", () => {
    // Insert old entry manually
    (db as any).rawDb.prepare(`
      INSERT INTO owl_learnings (id, owl_name, learning, category, confidence, reinforcement_count, created_at, updated_at)
      VALUES ('old1', 'owl1', 'web fetch fails on large files', 'failure', 0.6, 1,
              datetime('now', '-31 days'), datetime('now', '-31 days'))
    `).run();
    const result = db.owlLearnings.admitIfWorthy(
      "owl1",
      "web fetch fails on large files",
      "failure",
      0.6,
    );
    expect(result).not.toBeNull();
  });
});

// ─── OwlLearningsRepo.evictStale ──────────────────────────────────

describe("OwlLearningsRepo.evictStale", () => {
  function insertLearning(
    id: string,
    confidence: number,
    reinforcement: number,
    createdAt: string,
  ) {
    (db as any).rawDb.prepare(`
      INSERT INTO owl_learnings
        (id, owl_name, learning, category, confidence, reinforcement_count, created_at, updated_at)
      VALUES (?, 'owl1', 'test learning', 'insight', ?, ?, ?, ?)
    `).run(id, confidence, reinforcement, createdAt, createdAt);
  }

  it("deletes entries meeting all 3 stale criteria", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("stale1", 0.2, 1, old);
    const count = db.owlLearnings.evictStale();
    expect(count).toBe(1);
  });

  it("keeps entries failing any single criterion", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("keep1", 0.5, 1, old);      // confidence too high
    insertLearning("keep2", 0.2, 5, old);      // reinforcement too high
    insertLearning("keep3", 0.2, 1, new Date().toISOString()); // too recent
    const count = db.owlLearnings.evictStale();
    expect(count).toBe(0);
  });

  it("is idempotent — second call returns 0", () => {
    const old = new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString();
    insertLearning("stale2", 0.2, 1, old);
    db.owlLearnings.evictStale();
    const second = db.owlLearnings.evictStale();
    expect(second).toBe(0);
  });
});

// ─── ApproachLibraryRepo.getEffectivenessScore ────────────────────

describe("ApproachLibraryRepo.getEffectivenessScore", () => {
  it("returns 0.5 on no history", () => {
    const score = db.approachLibrary.getEffectivenessScore("owl1", "unknown_tool");
    expect(score).toBe(0.5);
  });

  it("returns > 0.5 for 100% success history", () => {
    db.approachLibrary.record("owl1", "web_fetch", "fetch pdf", "url=x", "success");
    db.approachLibrary.record("owl1", "web_fetch", "fetch html", "url=y", "success");
    const score = db.approachLibrary.getEffectivenessScore("owl1", "web_fetch");
    expect(score).toBeGreaterThan(0.5);
  });

  it("applies recency decay — older successes score lower than fresh ones", () => {
    // Insert an old success
    const old = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, created_at)
      VALUES ('old_s', 'owl1', 'old_tool', 'kw', 'args', 'success', ?)
    `).run(old);
    const oldScore = db.approachLibrary.getEffectivenessScore("owl1", "old_tool");

    // Insert a fresh success
    db.approachLibrary.record("owl1", "new_tool", "kw", "args", "success");
    const freshScore = db.approachLibrary.getEffectivenessScore("owl1", "new_tool");

    expect(freshScore).toBeGreaterThan(oldScore);
  });
});

// ─── ApproachLibraryRepo.getRepeatFailureWarning ─────────────────

describe("ApproachLibraryRepo.getRepeatFailureWarning", () => {
  it("returns null when no similar failures exist", () => {
    const result = db.approachLibrary.getRepeatFailureWarning("web_fetch", [
      "download", "pdf",
    ]);
    expect(result).toBeNull();
  });

  it("returns warning string when Jaccard >= 0.6", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f1', 'owl1', 'web_fetch', 'download large file pdf', 'url=x', 'failure', 'timeout')
    `).run();
    const result = db.approachLibrary.getRepeatFailureWarning("web_fetch", [
      "download", "large", "file",
    ]);
    expect(result).not.toBeNull();
    expect(result).toContain("web_fetch");
    expect(result).toContain("timeout");
  });

  it("returns null on second call within 1 hour (cooldown)", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f2', 'owl1', 'shell', 'run bash script', 'cmd=x', 'failure', 'permission denied')
    `).run();
    // First call triggers cooldown
    db.approachLibrary.getRepeatFailureWarning("shell", ["run", "bash", "script"]);
    // Second call within cooldown window
    const result = db.approachLibrary.getRepeatFailureWarning("shell", [
      "run", "bash", "script",
    ]);
    expect(result).toBeNull();
  });

  it("new db instance resets cooldown", () => {
    (db as any).rawDb.prepare(`
      INSERT INTO approach_library (id, owl_name, tool_name, task_keywords, args_summary, outcome, failure_reason)
      VALUES ('f3', 'owl1', 'write_file', 'write config file', 'path=x', 'failure', 'disk full')
    `).run();
    // First db instance triggers cooldown
    db.approachLibrary.getRepeatFailureWarning("write_file", ["write", "config", "file"]);

    // New db instance has fresh cooldown
    const db2 = new MemoryDatabase(tmpDir);
    const result = db2.approachLibrary.getRepeatFailureWarning("write_file", [
      "write", "config", "file",
    ]);
    expect(result).not.toBeNull();
  });
});
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory-db-learning.test.ts
```

Expected: Multiple FAIL — methods not found on `db.trajectories`, `db.owlLearnings`, `db.approachLibrary`.

- [ ] **Step 1.3: Add `computeSimilarity` + `tokenize` module-level helpers to `db.ts`**

Add these two functions just before `class OwlLearningsRepo` (around line 2083 in `src/memory/db.ts`):

```typescript
// ─── Jaccard similarity helpers (ported from mistake-detector.ts) ─

function computeSimilarity(setA: string[], setB: string[]): number {
  const a = new Set(setA.map((w) => w.toLowerCase()));
  const b = new Set(setB.map((w) => w.toLowerCase()));
  const intersection = [...a].filter((w) => b.has(w)).length;
  const union = new Set([...a, ...b]).size;
  return union === 0 ? 0 : intersection / union;
}

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length > 2);
}
```

- [ ] **Step 1.4: Add methods to `TrajectoriesRepo`**

In `src/memory/db.ts`, add the following two methods inside `class TrajectoriesRepo`, just before the closing `}` at line 2622:

```typescript
  /**
   * Tools with ≥ minOccurrences BLOCKED/PARTIAL turns in the last daysBack days.
   * Returns [] gracefully if trajectory_turns table doesn't exist yet.
   */
  getFailureDensityTopics(daysBack: number, minOccurrences: number): string[] {
    try {
      const rows = this.db.prepare(`
        SELECT tool_name
        FROM trajectory_turns
        WHERE verification_result IN ('BLOCKED', 'PARTIAL')
          AND created_at > datetime('now', '-' || ? || ' days')
          AND tool_name IS NOT NULL
        GROUP BY tool_name
        HAVING COUNT(*) >= ?
        ORDER BY COUNT(*) DESC
        LIMIT 10
      `).all(daysBack, minOccurrences) as Array<{ tool_name: string }>;
      return rows.map((r) => r.tool_name);
    } catch {
      return [];
    }
  }

  /**
   * BLOCKED and PARTIAL turns for a given session — used by the failure critique job.
   * Requires a JOIN because trajectory_turns has no direct session_id column.
   */
  getSessionFailures(sessionId: string): Array<{
    tool_name: string | null;
    verification_result: string;
    verifier_reason: string | null;
  }> {
    const rows = this.db.prepare(`
      SELECT tt.tool_name, tt.verification_result, tt.verifier_reason
      FROM trajectory_turns tt
      JOIN trajectories t ON t.id = tt.trajectory_id
      WHERE t.session_id = ?
        AND tt.verification_result IN ('BLOCKED', 'PARTIAL')
    `).all(sessionId) as any[];
    return rows;
  }
```

- [ ] **Step 1.5: Add methods to `OwlLearningsRepo`**

In `src/memory/db.ts`, add these three methods inside `class OwlLearningsRepo`, just before its closing `}`:

```typescript
  /**
   * Admit a new learning only if no near-duplicate exists within the last 30 days.
   * Returns { id } on admission, null on rejection.
   */
  admitIfWorthy(
    owlName: string,
    learning: string,
    category: LearningCategory,
    confidence: number,
  ): { id: string } | null {
    const recent = this.db.prepare(`
      SELECT learning FROM owl_learnings
      WHERE owl_name = ? AND created_at > datetime('now', '-30 days')
    `).all(owlName) as Array<{ learning: string }>;

    const newTokens = tokenize(learning);
    for (const row of recent) {
      const existingTokens = tokenize(row.learning);
      if (computeSimilarity(newTokens, existingTokens) >= 0.6) {
        return null;
      }
    }

    const result = this.add(owlName, learning, category, undefined, confidence);
    return { id: result.id };
  }

  /**
   * Delete stale owl_learnings: low confidence + rarely reinforced + old.
   * Returns count of deleted rows. Safe to call repeatedly (idempotent).
   */
  evictStale(): number {
    const result = this.db.prepare(`
      DELETE FROM owl_learnings
      WHERE confidence < 0.3
        AND reinforcement_count <= 1
        AND created_at < datetime('now', '-14 days')
    `).run();
    return result.changes;
  }

  /**
   * Top 6 learnings for an owl, failure category first, then by confidence + reinforcement.
   * Used by OwlLearningsLayer to inject into context.
   */
  getForOwlSorted(owlName: string): string[] {
    const rows = this.db.prepare(`
      SELECT learning FROM owl_learnings
      WHERE owl_name = ?
      ORDER BY
        CASE category WHEN 'failure' THEN 0 ELSE 1 END,
        confidence DESC,
        reinforcement_count DESC
      LIMIT 6
    `).all(owlName) as Array<{ learning: string }>;
    return rows.map((r) => r.learning);
  }
```

- [ ] **Step 1.6: Add fields and methods to `ApproachLibraryRepo`**

In `src/memory/db.ts`, inside `class ApproachLibraryRepo` (at line 2359):

First, add a private cooldown map after the constructor (around line 2361):
```typescript
  private readonly cooldown = new Map<string, number>();
```

Then add these two methods just before the closing `}` of `ApproachLibraryRepo` (before line 2412):

```typescript
  /**
   * Effectiveness score for owl+tool combination, with 14-day recency decay.
   * Returns 0.5 (neutral) when no history exists.
   */
  getEffectivenessScore(owlName: string, toolName: string): number {
    const row = this.db.prepare(`
      SELECT
        COUNT(*) FILTER (WHERE outcome = 'success') AS success_count,
        COUNT(*) FILTER (WHERE outcome = 'failure') AS failure_count,
        MAX(created_at) FILTER (WHERE outcome = 'success') AS last_success
      FROM approach_library
      WHERE owl_name = ? AND tool_name = ?
    `).get(owlName, toolName) as {
      success_count: number;
      failure_count: number;
      last_success: string | null;
    } | undefined;

    if (!row || row.success_count + row.failure_count === 0) return 0.5;

    const baseScore = row.success_count / (row.success_count + row.failure_count);
    const ageMs = Date.now() - new Date(row.last_success ?? 0).getTime();
    const decayFactor = Math.pow(0.5, ageMs / (14 * 24 * 60 * 60 * 1000));
    return baseScore * decayFactor + (1 - decayFactor) * 0.5;
  }

  /**
   * Returns a warning string if a similar task has previously failed with this tool.
   * Cooldown: 1 hour per tool to avoid repeated warnings in a session.
   */
  getRepeatFailureWarning(toolName: string, taskKeywords: string[]): string | null {
    const last = this.cooldown.get(toolName);
    if (last !== undefined && Date.now() - last < 3_600_000) return null;

    const rows = this.db.prepare(`
      SELECT task_keywords, failure_reason
      FROM approach_library
      WHERE tool_name = ? AND outcome = 'failure'
      ORDER BY created_at DESC
      LIMIT 20
    `).all(toolName) as Array<{ task_keywords: string; failure_reason: string | null }>;

    for (const row of rows) {
      const similarity = computeSimilarity(
        taskKeywords,
        tokenize(row.task_keywords),
      );
      if (similarity >= 0.6) {
        this.cooldown.set(toolName, Date.now());
        return (
          `Warning: similar task failed previously with ${toolName}. ` +
          `Past failure: ${row.failure_reason ?? "unknown reason"}. ` +
          `Consider an alternative approach.`
        );
      }
    }
    return null;
  }
```

- [ ] **Step 1.7: Run tests to verify they pass**

```bash
npx vitest run __tests__/memory-db-learning.test.ts
```

Expected: All tests PASS.

- [ ] **Step 1.8: Commit**

```bash
git add src/memory/db.ts __tests__/memory-db-learning.test.ts
git commit -m "feat(learning): add DB methods for admission, eviction, effectiveness scoring, failure density"
```

---

## Task 2: Delete Dead Code

**Files:**
- Delete: `src/learning/approach-library.ts`
- Delete: `src/learning/mistake-detector.ts`

- [ ] **Step 2.1: Confirm zero import sites**

```bash
grep -rn "approach-library\|ApproachLibrary\b" src/ --include="*.ts" | grep -v "approach-library.ts"
grep -rn "mistake-detector\|MistakePattern" src/ --include="*.ts" | grep -v "mistake-detector.ts"
```

Expected: Zero matches (no other files import these).

- [ ] **Step 2.2: Delete both files**

```bash
rm src/learning/approach-library.ts
rm src/learning/mistake-detector.ts
```

- [ ] **Step 2.3: Confirm compile passes**

```bash
npx tsc --noEmit
```

Expected: No TypeScript errors.

- [ ] **Step 2.4: Commit**

```bash
git add -A
git commit -m "chore(learning): delete dead code — approach-library.ts and mistake-detector.ts"
```

---

## Task 3: Orchestrator Proactive Session

**Files:**
- Create: `__tests__/learning-orchestrator-proactive.test.ts`
- Modify: `src/learning/orchestrator.ts`

- [ ] **Step 3.1: Write the failing test**

```typescript
// __tests__/learning-orchestrator-proactive.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LearningOrchestrator } from "../src/learning/orchestrator.js";
import type { ProactiveContext } from "../src/learning/orchestrator.js";

function makeMockOrchestrator() {
  const mockSynthesize = vi.fn().mockResolvedValue({
    pelletsCreated: 1,
    insightsGenerated: 0,
    connectionsFormed: 0,
  });

  const mockGetStudyQueue = vi.fn().mockReturnValue([]);
  const mockTouchDomain = vi.fn();

  const mockProvider = {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: "mock", model: "m", finishReason: "stop" }),
    chatWithTools: vi.fn(),
    stream: vi.fn(),
    listModels: vi.fn(),
    healthCheck: vi.fn(),
  };

  const mockOwl = {
    persona: { name: "test-owl", systemPrompt: "", traits: {}, dna: {} },
    config: {},
  } as any;

  const orch = new LearningOrchestrator(
    mockProvider as any,
    mockOwl,
    {} as any,
    undefined as any,
    "/tmp",
    undefined,
  );

  // Inject mock graph manager
  (orch as any).graphManager = {
    getStudyQueue: mockGetStudyQueue,
    touchDomain: mockTouchDomain,
    getGraph: vi.fn().mockReturnValue({ nodes: [], edges: [] }),
  };
  // Inject mock synthesizer
  (orch as any).synthesizer = { synthesize: mockSynthesize };

  return { orch, mockSynthesize, mockGetStudyQueue };
}

describe("LearningOrchestrator.runProactiveSession", () => {
  it("returns zeroed cycle when no context and empty KG queue", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([]);
    const cycle = await orch.runProactiveSession();
    expect(mockSynthesize).not.toHaveBeenCalled();
    expect(cycle.topicsPrioritized).toBe(0);
  });

  it("calls synthesizer with failureDensityTopics when present", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([]);
    const ctx: ProactiveContext = { failureDensityTopics: ["web_fetch", "shell"] };
    await orch.runProactiveSession(ctx);
    expect(mockSynthesize).toHaveBeenCalledOnce();
  });

  it("calls synthesizer with KG topics when no failure topics", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "TypeScript generics", priority: 0.8 },
    ]);
    await orch.runProactiveSession({});
    expect(mockSynthesize).toHaveBeenCalledOnce();
  });

  it("failure topics take priority over KG topics when both present", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "TypeScript generics", priority: 0.8 },
    ]);
    const ctx: ProactiveContext = {
      failureDensityTopics: ["web_fetch"],
      maxTopics: 3,
    };
    const cycle = await orch.runProactiveSession(ctx);
    // Synthesizer called with failure topic, not KG topic
    const synthesizeArg = mockSynthesize.mock.calls[0][0] as string[];
    expect(synthesizeArg).toContain("web_fetch");
  });

  it("respects maxTopics cap", async () => {
    const { orch, mockSynthesize, mockGetStudyQueue } = makeMockOrchestrator();
    mockGetStudyQueue.mockReturnValue([
      { normalizedName: "topic1", priority: 0.9 },
      { normalizedName: "topic2", priority: 0.8 },
      { normalizedName: "topic3", priority: 0.7 },
    ]);
    await orch.runProactiveSession({ maxTopics: 1 });
    const topics = mockSynthesize.mock.calls[0][0] as string[];
    expect(topics).toHaveLength(1);
  });
});
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
npx vitest run __tests__/learning-orchestrator-proactive.test.ts
```

Expected: FAIL — `ProactiveContext` not exported; `runProactiveSession` still returns zeroed cycle stub.

- [ ] **Step 3.3: Add `ProactiveContext` interface and implement `runProactiveSession`**

In `src/learning/orchestrator.ts`, add the interface export before or after the existing imports/types section:

```typescript
export interface ProactiveContext {
  /** Tool names with ≥ minOccurrences failures in the last daysBack days */
  failureDensityTopics?: string[];
  /** Patterns from ToolOutcomeStore or pattern miner (optional enrichment) */
  upcomingPatterns?: string[];
  /** Topics from owl_learnings where confidence < 0.5 (optional enrichment) */
  lowConfidenceTopics?: string[];
  /** Cap on topics to study. Default: 3 */
  maxTopics?: number;
}
```

Then find `runProactiveSession()` (currently a no-op stub, lines ~257–270) and replace it with:

```typescript
async runProactiveSession(context?: ProactiveContext): Promise<LearningCycle> {
  const max = context?.maxTopics ?? 3;

  // Quality gate: if no context, check KG queue — if also empty, bail immediately
  if (!context) {
    const kgQueue = this.graphManager.getStudyQueue(1);
    if (kgQueue.length === 0) {
      return {
        sessionId: uuidv4(),
        topicsPrioritized: 0,
        topicsStudied: 0,
        trigger: "scheduled",
        startedAt: new Date().toISOString(),
        completedAt: new Date().toISOString(),
      };
    }
  }

  // Topic selection — priority order: failureDensityTopics > KG frontier > upcomingPatterns
  const allEmpty =
    !context?.failureDensityTopics?.length &&
    !context?.upcomingPatterns?.length &&
    !context?.lowConfidenceTopics?.length;

  let topics: string[] = [];

  if (context?.failureDensityTopics?.length) {
    topics = context.failureDensityTopics.slice(0, max);
  }

  if (topics.length < max) {
    const kgQueue = this.graphManager.getStudyQueue(max - topics.length);
    topics = [
      ...topics,
      ...kgQueue.map((t: { normalizedName: string }) => t.normalizedName),
    ].slice(0, max);
  }

  if (topics.length < max && context?.upcomingPatterns?.length) {
    topics = [
      ...topics,
      ...context.upcomingPatterns,
    ].slice(0, max);
  }

  if (topics.length === 0) {
    return {
      sessionId: uuidv4(),
      topicsPrioritized: 0,
      topicsStudied: 0,
      trigger: "scheduled",
      startedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
    };
  }

  const startedAt = new Date().toISOString();
  try {
    const synthesisReport = await this.synthesizer.synthesize(topics);
    topics.forEach((t) => this.graphManager.touchDomain(t, "self-study"));
    return {
      sessionId: uuidv4(),
      topicsPrioritized: topics.length,
      topicsStudied: topics.length,
      trigger: "scheduled",
      synthesisReport,
      startedAt,
      completedAt: new Date().toISOString(),
    };
  } catch (err) {
    log.evolution.warn(`[ProactiveSession] Failed: ${err}`);
    return {
      sessionId: uuidv4(),
      topicsPrioritized: topics.length,
      topicsStudied: 0,
      trigger: "scheduled",
      error: String(err),
      startedAt,
      completedAt: new Date().toISOString(),
    };
  }
}
```

> **Note:** If `uuidv4` is not already imported in `orchestrator.ts`, import it: `import { v4 as uuidv4 } from "uuid";`. Check existing imports first.

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
npx vitest run __tests__/learning-orchestrator-proactive.test.ts
```

Expected: All tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add src/learning/orchestrator.ts __tests__/learning-orchestrator-proactive.test.ts
git commit -m "feat(learning): implement evidence-based proactive session with ProactiveContext interface"
```

---

## Task 4: Post-Processor Failure Critique Job

**Files:**
- Create: `__tests__/post-processor-critique.test.ts`
- Modify: `src/gateway/handlers/post-processor.ts`

- [ ] **Step 4.1: Write the failing test**

```typescript
// __tests__/post-processor-critique.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { MemoryDatabase } from "../src/memory/db.js";
import { PostProcessor } from "../src/gateway/handlers/post-processor.js";
import type { GatewayContext } from "../src/gateway/types.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-critique-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function makeMockCtx(
  provider: any,
  trajectoryGetterOverride?: () => any[],
): Partial<GatewayContext> {
  return {
    db: {
      ...db,
      trajectories: {
        ...db.trajectories,
        getSessionFailures: trajectoryGetterOverride
          ? trajectoryGetterOverride
          : () => [],
      },
      owlLearnings: {
        ...db.owlLearnings,
        admitIfWorthy: vi.fn().mockReturnValue({ id: "test_id" }),
      },
    } as any,
    provider: provider,
    owl: { persona: { name: "test-owl" } } as any,
  };
}

function makeTaskQueue() {
  const jobs: Array<() => Promise<void>> = [];
  return {
    enqueue: (_name: string, _priority: any, fn: () => Promise<void>) => {
      jobs.push(fn);
    },
    flush: async () => {
      for (const fn of jobs) await fn();
      jobs.length = 0;
    },
  };
}

describe("PostProcessor failure critique job", () => {
  it("does not call provider when no BLOCKED/PARTIAL turns", async () => {
    const mockProvider = { chat: vi.fn() };
    const ctx = makeMockCtx(mockProvider);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess1", { owlName: "test-owl" });
    await queue.flush();
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("calls provider once when one BLOCKED turn exists", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Sentence 1. Sentence 2.",
        model: "m",
        finishReason: "stop",
      }),
    };
    const turns = [
      { tool_name: "web_fetch", verification_result: "BLOCKED", verifier_reason: "bot detection" },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess2", { owlName: "test-owl" });
    await queue.flush();
    expect(mockProvider.chat).toHaveBeenCalledOnce();
    expect((ctx.db as any).owlLearnings.admitIfWorthy).toHaveBeenCalledOnce();
  });

  it("handles admitIfWorthy returning null without error", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Critique text.",
        model: "m",
        finishReason: "stop",
      }),
    };
    const turns = [
      { tool_name: "web_fetch", verification_result: "BLOCKED", verifier_reason: null },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    (ctx.db as any).owlLearnings.admitIfWorthy = vi.fn().mockReturnValue(null);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess3", { owlName: "test-owl" });
    await expect(queue.flush()).resolves.not.toThrow();
  });

  it("catches provider throw and completes job without rethrowing", async () => {
    const mockProvider = {
      chat: vi.fn().mockRejectedValue(new Error("model unavailable")),
    };
    const turns = [
      { tool_name: "shell", verification_result: "PARTIAL", verifier_reason: "exit code 1" },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess4", { owlName: "test-owl" });
    await expect(queue.flush()).resolves.not.toThrow();
  });
});
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
npx vitest run __tests__/post-processor-critique.test.ts
```

Expected: FAIL — `"learning-failure-critique"` job doesn't exist yet.

- [ ] **Step 4.3: Add the critique job to `post-processor.ts`**

In `src/gateway/handlers/post-processor.ts`, add a `CRITIQUE_PROMPT_TEMPLATE` constant near the top of the file (after the imports, before the class):

```typescript
const CRITIQUE_PROMPT_TEMPLATE =
  `You are a learning assistant. In exactly two sentences:\n` +
  `Sentence 1: What went wrong when the assistant called "{tool_name}" and received a "{verdict}" result? (Context: "{verifier_reason}")\n` +
  `Sentence 2: In one concrete action, how should the assistant approach this differently next time?\n` +
  `Write only the two sentences. No headers, no explanation.`;
```

Then in the `process()` method, after the existing `"learning-orchestrator"` job block (after the `else if (this.ctx.learningEngine)` block, around line 214), add:

```typescript
    // ── Failure critique: BLOCKED/PARTIAL → owl_learnings ──────────
    // Reads trajectory_turns for this session, generates a 2-sentence critique
    // per failure via cheap LLM call, stores into owl_learnings via admitIfWorthy.
    if (this.ctx.db && sessionId) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      this.enqueueJob("learning-failure-critique", "background", async () => {
        const failedTurns =
          this.ctx.db!.trajectories.getSessionFailures(sessionId!) ?? [];
        if (failedTurns.length === 0) return;

        for (const turn of failedTurns.slice(0, 3)) {
          const prompt = CRITIQUE_PROMPT_TEMPLATE
            .replace("{tool_name}", turn.tool_name ?? "unknown")
            .replace("{verdict}", turn.verification_result)
            .replace("{verifier_reason}", turn.verifier_reason ?? "");

          try {
            const response = await this.ctx.provider.chat([
              { role: "user", content: prompt },
            ]);
            const critique = response.content.trim();
            if (critique) {
              this.ctx.db!.owlLearnings.admitIfWorthy(
                owlName,
                critique,
                "failure",
                0.6,
              );
            }
          } catch (err) {
            log.evolution.warn(
              `[PostProcessor:critique] Failed to generate critique: ${err instanceof Error ? err.message : err}`,
            );
          }
        }
      });
    }
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
npx vitest run __tests__/post-processor-critique.test.ts
```

Expected: All tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/post-processor-critique.test.ts
git commit -m "feat(learning): add failure critique job — BLOCKED/PARTIAL turns generate owl_learnings entries"
```

---

## Task 5: Idle-Engine Migration

**Files:**
- Create: `__tests__/idle-engine-orchestrator.test.ts`
- Modify: `src/heartbeat/idle-engine.ts`

- [ ] **Step 5.1: Write the failing test**

```typescript
// __tests__/idle-engine-orchestrator.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  IdleActivityEngine,
  type IdleEngineCallbacks,
} from "../src/heartbeat/idle-engine.js";
import type { MemoryDatabase } from "../src/memory/db.js";
import type { LearningOrchestrator } from "../src/learning/orchestrator.js";

function makeMockOrchestrator(): LearningOrchestrator {
  return {
    runProactiveSession: vi.fn().mockResolvedValue({
      topicsPrioritized: 2,
      topicsStudied: 2,
      trigger: "scheduled",
      startedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
    }),
  } as any;
}

function makeMockDb(topics: string[] = []): MemoryDatabase {
  return {
    trajectories: {
      getFailureDensityTopics: vi.fn().mockReturnValue(topics),
    },
  } as any;
}

describe("IdleActivityEngine — runAnticipatoryResearch", () => {
  it("calls getFailureDensityTopics(7, 2) then runProactiveSession with those topics", async () => {
    const orch = makeMockOrchestrator();
    const db = makeMockDb(["web_fetch"]);
    const results: any[] = [];
    const callbacks: IdleEngineCallbacks = {
      onResult: (r) => results.push(r),
      learningOrchestrator: orch,
      db,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    // Force isIdle = true by setting lastUserActivity to long ago
    (engine as any).lastUserActivity = Date.now() - 999_999_999;

    await (engine as any).runAnticipatoryResearch();

    expect(db.trajectories.getFailureDensityTopics).toHaveBeenCalledWith(7, 2);
    expect(orch.runProactiveSession).toHaveBeenCalledWith({
      failureDensityTopics: ["web_fetch"],
      maxTopics: 3,
    });
  });

  it("passes empty failureDensityTopics when db is missing", async () => {
    const orch = makeMockOrchestrator();
    const callbacks: IdleEngineCallbacks = {
      onResult: () => {},
      learningOrchestrator: orch,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    await (engine as any).runAnticipatoryResearch();
    expect(orch.runProactiveSession).toHaveBeenCalledWith({
      failureDensityTopics: [],
      maxTopics: 3,
    });
  });

  it("returns success:false when learningOrchestrator is missing", async () => {
    const callbacks: IdleEngineCallbacks = { onResult: () => {} };
    const engine = new IdleActivityEngine({} as any, callbacks);
    const result = await (engine as any).runAnticipatoryResearch();
    expect(result.success).toBe(false);
  });
});

describe("IdleActivityEngine — runKnowledgeRefresh", () => {
  it("calls runProactiveSession with maxTopics:1 and no DB query", async () => {
    const orch = makeMockOrchestrator();
    const db = makeMockDb();
    const callbacks: IdleEngineCallbacks = {
      onResult: () => {},
      learningOrchestrator: orch,
      db,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    await (engine as any).runKnowledgeRefresh();
    expect(orch.runProactiveSession).toHaveBeenCalledWith({ maxTopics: 1 });
    expect(db.trajectories.getFailureDensityTopics).not.toHaveBeenCalled();
  });

  it("returns success:false when learningOrchestrator is missing", async () => {
    const callbacks: IdleEngineCallbacks = { onResult: () => {} };
    const engine = new IdleActivityEngine({} as any, callbacks);
    const result = await (engine as any).runKnowledgeRefresh();
    expect(result.success).toBe(false);
  });
});
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
npx vitest run __tests__/idle-engine-orchestrator.test.ts
```

Expected: FAIL — `IdleEngineCallbacks.learningOrchestrator` doesn't exist.

- [ ] **Step 5.3: Update `idle-engine.ts`**

Replace the full file at `src/heartbeat/idle-engine.ts` with:

```typescript
import type { StackOwlConfig } from "../config/loader.js";
import type { PatternMiner } from "../skills/pattern-miner.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { ToolOutcomeStore } from "../tools/outcome-store.js";
import type { CapabilityScanner, ScanResult } from "./capability-scanner.js";

// ─── Types ────────────────────────────────────────────────────────

export interface IdleEngineConfig {
  /** Minutes of user inactivity before "idle" mode activates. Default: 15 */
  idleThresholdMinutes: number;
  /** How often the idle cycle checks for work. Default: 5 */
  cycleLengthMinutes: number;
  /** Which activity types are enabled */
  enabled: {
    patternMining: boolean;
    capabilityExploration: boolean;
    anticipatoryResearch: boolean;
    toolOutcomeReview: boolean;
    knowledgeRefresh: boolean;
  };
}

const DEFAULT_CONFIG: IdleEngineConfig = {
  idleThresholdMinutes: 15,
  cycleLengthMinutes: 5,
  enabled: {
    patternMining: true,
    capabilityExploration: true,
    anticipatoryResearch: true,
    toolOutcomeReview: true,
    knowledgeRefresh: true,
  },
};

export interface IdleActivityResult {
  activity: string;
  success: boolean;
  artifacts?: string[];
  durationMs?: number;
}

export interface IdleEngineCallbacks {
  onResult: (result: IdleActivityResult) => void;
  patternMiner?: PatternMiner;
  capabilityScanner?: CapabilityScanner;
  learningOrchestrator?: LearningOrchestrator;
  db?: MemoryDatabase;
  toolOutcomeStore?: ToolOutcomeStore;
}

// ─── IdleActivityEngine ───────────────────────────────────────────

export class IdleActivityEngine {
  private config: IdleEngineConfig;
  private callbacks: IdleEngineCallbacks;
  private lastUserActivity: number = Date.now();
  private timer: ReturnType<typeof setInterval> | null = null;
  private recentResults: IdleActivityResult[] = [];
  private running = false;

  constructor(
    private readonly stackConfig: StackOwlConfig,
    callbacks: IdleEngineCallbacks,
    idleConfig?: Partial<IdleEngineConfig>,
  ) {
    this.callbacks = callbacks;
    this.config = {
      ...DEFAULT_CONFIG,
      ...idleConfig,
      enabled: { ...DEFAULT_CONFIG.enabled, ...(idleConfig?.enabled ?? {}) },
    };
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    const intervalMs = this.config.cycleLengthMinutes * 60_000;
    this.timer = setInterval(() => this.tick(), intervalMs);
  }

  stop(): void {
    this.running = false;
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  onUserActivity(): void {
    this.lastUserActivity = Date.now();
  }

  isIdle(): boolean {
    const elapsedMs = Date.now() - this.lastUserActivity;
    return elapsedMs >= this.config.idleThresholdMinutes * 60_000;
  }

  getRecentResults(limit = 10): IdleActivityResult[] {
    return this.recentResults.slice(-limit);
  }

  private async tick(): Promise<void> {
    if (!this.isIdle()) return;
    const activity = this.pickNextActivity();
    if (!activity) return;

    const result = await this.runActivity(activity);
    this.recentResults.push(result);
    if (this.recentResults.length > 100) this.recentResults.shift();
    this.callbacks.onResult(result);
  }

  private pickNextActivity(): string | null {
    if (!this.isIdle()) return null;

    const { enabled } = this.config;
    const { patternMiner, capabilityScanner, learningOrchestrator, toolOutcomeStore } =
      this.callbacks;

    if (enabled.patternMining && patternMiner) return "pattern_mining";
    if (enabled.capabilityExploration && capabilityScanner) return "capability_exploration";
    if (enabled.anticipatoryResearch && learningOrchestrator) return "anticipatory_research";
    if (enabled.toolOutcomeReview && toolOutcomeStore) return "tool_outcome_review";
    if (enabled.knowledgeRefresh && learningOrchestrator) return "knowledge_refresh";

    return null;
  }

  private async runActivity(activity: string): Promise<IdleActivityResult> {
    const start = Date.now();
    try {
      switch (activity) {
        case "pattern_mining":
          return await this.runPatternMining();
        case "capability_exploration":
          return await this.runCapabilityExploration();
        case "anticipatory_research":
          return await this.runAnticipatoryResearch();
        case "tool_outcome_review":
          return await this.runToolOutcomeReview();
        case "knowledge_refresh":
          return await this.runKnowledgeRefresh();
        default:
          return { activity, success: false, durationMs: Date.now() - start };
      }
    } catch {
      return { activity, success: false, durationMs: Date.now() - start };
    }
  }

  private async runPatternMining(): Promise<IdleActivityResult> {
    if (!this.callbacks.patternMiner) {
      return { activity: "pattern_mining", success: false };
    }
    const patterns = await this.callbacks.patternMiner.mine();
    return {
      activity: "pattern_mining",
      success: true,
      artifacts: patterns.map((p: any) => (typeof p === "string" ? p : p.name ?? p.id ?? String(p))),
    };
  }

  private async runCapabilityExploration(): Promise<IdleActivityResult> {
    if (!this.callbacks.capabilityScanner) {
      return { activity: "capability_exploration", success: false };
    }
    const result: ScanResult = this.callbacks.capabilityScanner.scan();
    return {
      activity: "capability_exploration",
      success: true,
      artifacts: result.gaps.map((g: any) => g.name),
    };
  }

  private async runAnticipatoryResearch(): Promise<IdleActivityResult> {
    if (!this.callbacks.learningOrchestrator) {
      return { activity: "anticipatory_research", success: false };
    }
    const failureDensityTopics = this.callbacks.db
      ? (this.callbacks.db.trajectories.getFailureDensityTopics(7, 2) ?? [])
      : [];
    await this.callbacks.learningOrchestrator.runProactiveSession({
      failureDensityTopics,
      maxTopics: 3,
    });
    return { activity: "anticipatory_research", success: true };
  }

  private async runToolOutcomeReview(): Promise<IdleActivityResult> {
    if (!this.callbacks.toolOutcomeStore) {
      return { activity: "tool_outcome_review", success: false };
    }
    const patterns = this.callbacks.toolOutcomeStore.getTopPatterns();
    const lowSuccessTools = patterns
      .filter((p: any) => (p.successRate ?? 1) < 0.5)
      .map((p: any) => p.requestType ?? p.name ?? String(p));
    return {
      activity: "tool_outcome_review",
      success: true,
      artifacts: lowSuccessTools,
    };
  }

  private async runKnowledgeRefresh(): Promise<IdleActivityResult> {
    if (!this.callbacks.learningOrchestrator) {
      return { activity: "knowledge_refresh", success: false };
    }
    await this.callbacks.learningOrchestrator.runProactiveSession({ maxTopics: 1 });
    return { activity: "knowledge_refresh", success: true };
  }
}
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
npx vitest run __tests__/idle-engine-orchestrator.test.ts
```

Expected: All tests PASS.

- [ ] **Step 5.5: Run full test suite to confirm no regressions**

```bash
npm run test
```

Expected: All previously passing tests still pass.

- [ ] **Step 5.6: Commit**

```bash
git add src/heartbeat/idle-engine.ts __tests__/idle-engine-orchestrator.test.ts
git commit -m "feat(learning): migrate IdleActivityEngine to LearningOrchestrator — retire learningEngine callbacks"
```

---

## Task 6: index.ts Cleanup

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 6.1: Remove `LearningEngine` import**

In `src/index.ts`, find and remove line 151:
```typescript
import { LearningEngine } from "./learning/self-study.js";
```

- [ ] **Step 6.2: Remove `learningEngineFactory`**

In `src/index.ts`, find and remove lines 468–480 (the `learningEngineFactory` arrow function and its comment):
```typescript
  // Learning Engine — instantiated here so bootstrap can share it across CLI + Telegram
  // (actual owl binding happens after owl selection, so we expose a factory)
  const learningEngineFactory = (
    owl: import("./owls/persona.js").OwlInstance,
  ) =>
    new LearningEngine(
      providerRegistry.getDefault(),
      owl,
      config,
      pelletStore,
      workspacePath,
      providerRegistry,
    );
```

- [ ] **Step 6.3: Remove `learningEngineFactory` from the builder object**

In `src/index.ts`, find line 830 and remove:
```typescript
    learningEngineFactory,
```

- [ ] **Step 6.4: Remove `learningEngine` from GatewayContext construction sites**

In `src/index.ts`, find and remove line 1094:
```typescript
      learningEngine: b.learningEngineFactory(owl),
```

Find and remove line 1124:
```typescript
    learningEngine: b.learningEngineFactory(owl),
```

- [ ] **Step 6.5: Confirm TypeScript compiles**

```bash
npx tsc --noEmit
```

Expected: No errors referencing `learningEngineFactory` or `LearningEngine` from `self-study`.

> If there are TypeScript errors about `learningEngine` missing from `GatewayContext`, proceed to Task 7 which removes the field from `gateway/types.ts`.

- [ ] **Step 6.6: Run tests**

```bash
npm run test
```

Expected: All tests pass.

- [ ] **Step 6.7: Commit**

```bash
git add src/index.ts
git commit -m "chore(learning): remove learningEngineFactory from index.ts — orchestrator is the only factory"
```

---

## Task 7: Delete self-study.ts + Clean Up All Callers

**Files:**
- Delete: `src/learning/self-study.ts`
- Modify: `src/cognition/loop.ts`
- Modify: `src/heartbeat/proactive.ts`
- Modify: `src/heartbeat/planner.ts`
- Modify: `src/cli/commands.ts`
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/post-processor.ts`

- [ ] **Step 7.1: Remove `LearningEngine` from `cognition/loop.ts`**

In `src/cognition/loop.ts`, remove line 35:
```typescript
import type { LearningEngine } from "../learning/self-study.js";
```

Remove line 100 from the options/deps interface:
```typescript
  learningEngine?: LearningEngine;
```

- [ ] **Step 7.2: Remove legacy `learningEngine` branch from `heartbeat/proactive.ts`**

In `src/heartbeat/proactive.ts`, remove line 16:
```typescript
import type { LearningEngine } from "../learning/self-study.js";
```

In the `ProactivePingerContext` (or similar context type around line 56), remove:
```typescript
  /** Learning engine for proactive self-study sessions */
  learningEngine?: LearningEngine;
```

In `maybeSelfStudy()` (around line 727–735), remove the `else` branch that uses `learningEngine`:
```typescript
      } else {
        const result = await this.context.learningEngine!.runStudySession(4);
        if (result.studied.length > 0) {
          console.log(
            `[ProactivePinger] ✓ Self-study done: studied [${result.studied.join(", ")}], ` +
              `${result.pelletsCreated} pellets created, ` +
              `${result.newFrontierTopics.length} new topics discovered`,
          );
        }
      }
```

Also update the guard at line 697 from:
```typescript
    if (!this.context.learningEngine && !this.context.learningOrchestrator)
```
to:
```typescript
    if (!this.context.learningOrchestrator)
```

- [ ] **Step 7.3: Remove `learningEngine` from `heartbeat/planner.ts`**

In `src/heartbeat/planner.ts`, remove line 18:
```typescript
import type { LearningEngine } from "../learning/self-study.js";
```

Remove the `learningEngine?: LearningEngine;` field (around line 75).

Replace the two candidate-generation guards that check `learningEngine`:

Around line 255, change:
```typescript
    if (this.deps.learningEngine && this.idleMinutes > 10) {
```
to:
```typescript
    if (this.deps.learningOrchestrator && this.idleMinutes > 10) {
```

Around line 326, change:
```typescript
    if (this.deps.learningEngine && this.idleMinutes > 5) {
```
to:
```typescript
    if (this.deps.learningOrchestrator && this.idleMinutes > 5) {
```

- [ ] **Step 7.4: Update `cli/commands.ts` to use orchestrator**

In `src/cli/commands.ts`, find `cmdLearning` (around line 289) and change:

```typescript
const cmdLearning: CommandFn = async (_args, ui, gateway) => {
  const learning = gateway.getLearningEngine();
  if (!learning) {
    ui.printInfo("Learning engine not available.");
    return true;
  }

  const report = await learning.getLearningReport();
  const lines = ["", YB("Learning Report"), sep(), ...report.split("\n"), ""];
  ui.printLines(lines);
  return true;
};
```

to:

```typescript
const cmdLearning: CommandFn = async (_args, ui, gateway) => {
  const orchestrator = gateway.getLearningOrchestrator();
  if (!orchestrator) {
    ui.printInfo("Learning engine not available.");
    return true;
  }

  const report = orchestrator.getFullReport();
  const lines = ["", YB("Learning Report"), sep(), ...report.split("\n"), ""];
  ui.printLines(lines);
  return true;
};
```

- [ ] **Step 7.5: Remove `learningEngine` from `gateway/types.ts`**

In `src/gateway/types.ts`, find and remove the import for `LearningEngine` (look for `import type { LearningEngine }` from self-study).

Find the `GatewayContext` interface and remove line 213:
```typescript
  learningEngine?: LearningEngine;
```

- [ ] **Step 7.6: Remove `getLearningEngine()` and legacy fallbacks from `gateway/core.ts`**

In `src/gateway/core.ts`:

**A)** Remove `getLearningEngine()` method (lines 2965–2966):
```typescript
  getLearningEngine() {
    return this.ctx.learningEngine;
  }
```

**B)** Remove the legacy `else if` fallback in the post-processing block (around lines 2473–2476):
```typescript
    } else if (this.ctx.learningEngine) {
      await this.ctx.learningEngine.processConversation(messages);
      log.engine.debug("[Owlet:legacy-learning] processConversation completed");
    }
```
(or similar — find the branch that calls `this.ctx.learningEngine.processConversation`)

**C)** At `handleLearnRequest` (around line 3480), change:
```typescript
    if (!this.ctx.learningEngine && !this.ctx.learningOrchestrator) return null;
```
to:
```typescript
    if (!this.ctx.learningOrchestrator) return null;
```

- [ ] **Step 7.7: Remove legacy fallback from `post-processor.ts`**

In `src/gateway/handlers/post-processor.ts`, find and remove the `else if (this.ctx.learningEngine)` fallback block (around lines 209–213):
```typescript
    } else if (this.ctx.learningEngine) {
      this.enqueueJob("learning", "standard", async () => {
        await this.ctx.learningEngine!.processConversation(messages);
        log.engine.info("[PostProcessor:learning] Legacy engine completed");
      });
    }
```

- [ ] **Step 7.8: Delete `self-study.ts`**

```bash
rm src/learning/self-study.ts
```

- [ ] **Step 7.9: Confirm TypeScript compiles cleanly**

```bash
npx tsc --noEmit
```

Expected: Zero errors. If any residual `self-study` import errors remain, remove them before proceeding.

- [ ] **Step 7.10: Run full test suite**

```bash
npm run test
```

Expected: All tests pass.

- [ ] **Step 7.11: Commit**

```bash
git add -A
git commit -m "feat(learning): delete self-study.ts — twin-engine collapse complete, all callers migrated to LearningOrchestrator"
```

---

## Task 8: Minor Fixes

**Files:**
- Modify: `src/intelligence/sleep-time-consolidator.ts`
- Modify: `src/gateway/core.ts`
- Modify: `src/context/layers/behavioral.ts`
- Modify: `src/context/index.ts`
- Modify: `src/learning/micro-learner.ts`
- Extend: `__tests__/micro-learner.test.ts`

- [ ] **Step 8.1: Write failing micro-learner tests**

Find the existing `__tests__/micro-learner.test.ts` (or create it if absent). Add:

```typescript
// Append to __tests__/micro-learner.test.ts
import { describe, it, expect } from "vitest";
import { MicroLearner } from "../src/learning/micro-learner.js";

describe("MicroLearner — style and temporal signal emission", () => {
  it("emits at least one style signal and one temporal signal per message", async () => {
    const learner = new MicroLearner("/tmp");
    const signals = await learner.processMessage("How do I set up TypeScript?");
    const types = signals.map((s: any) => s.type);
    expect(types).toContain("style");
    expect(types).toContain("temporal");
  });

  it("verbosity value is <= 1.0 for any message length", async () => {
    const learner = new MicroLearner("/tmp");
    // Very long message
    const longMsg = "word ".repeat(200);
    const signals = await learner.processMessage(longMsg);
    const verbosity = signals.find((s: any) => s.key === "verbosity");
    expect(verbosity).toBeDefined();
    expect(verbosity!.value).toBeLessThanOrEqual(1.0);
  });

  it("temporal signal has key 'hour' and value in [0, 1]", async () => {
    const learner = new MicroLearner("/tmp");
    const signals = await learner.processMessage("run the build");
    const temporal = signals.find((s: any) => s.type === "temporal" && s.key === "hour");
    expect(temporal).toBeDefined();
    expect(temporal!.value).toBeGreaterThanOrEqual(0);
    expect(temporal!.value).toBeLessThanOrEqual(1);
  });
});
```

- [ ] **Step 8.2: Run micro-learner tests to verify they fail**

```bash
npx vitest run __tests__/micro-learner.test.ts
```

Expected: FAIL — style and temporal signals not emitted.

- [ ] **Step 8.3: Add signal emissions to `micro-learner.ts`**

In `src/learning/micro-learner.ts`, find `processMessage()`. After line 212 (the end of the style-profile update block), add:

```typescript
    // Emit style signals for SignalBus → UserPreferenceModel
    const len = typeof message === "string" ? message.length : 0;
    const isQuestion = /\?/.test(typeof message === "string" ? message : "");
    const isCommand = /^(run|execute|build|test|deploy|start|stop)\b/i.test(
      typeof message === "string" ? message : "",
    );
    signals.push({ timestamp, type: "style", key: "verbosity", value: Math.min(len / 300, 1) });
    signals.push({ timestamp, type: "style", key: "question_rate", value: isQuestion ? 1 : 0 });
    signals.push({ timestamp, type: "style", key: "command_rate", value: isCommand ? 1 : 0 });
    // Emit temporal signal
    signals.push({ timestamp, type: "temporal", key: "hour", value: new Date(timestamp).getHours() / 23 });
```

> **Note:** Check where `timestamp` is already defined in `processMessage()`. It's likely `const timestamp = Date.now()`. Use it directly. Do not redefine it.

- [ ] **Step 8.4: Run micro-learner tests to verify they pass**

```bash
npx vitest run __tests__/micro-learner.test.ts
```

Expected: All tests PASS.

- [ ] **Step 8.5: Add eviction to `SleepTimeConsolidator.onSessionEnded()`**

In `src/intelligence/sleep-time-consolidator.ts`, find `onSessionEnded()` (around line 58). After the debounce check and before the `recentSummaries.length === 0` check (or before any LLM call), add:

```typescript
    // SCM-style eviction: prune stale owl_learnings before consolidation
    try {
      const evicted = this.raw
        .prepare(
          `DELETE FROM owl_learnings
           WHERE confidence < 0.3
             AND reinforcement_count <= 1
             AND created_at < datetime('now', '-14 days')`,
        )
        .run().changes;
      if (evicted > 0) {
        log.memory.info(`[SleepConsolidator] Evicted ${evicted} stale owl_learnings`);
      }
    } catch {
      // owl_learnings table may not exist in older DB schemas — silently skip
    }
```

> **Note:** Verify that `this.raw` and `log.memory` are available in scope. `this.raw` is the BetterSqlite3 instance. `log` should be imported at the top of the file.

- [ ] **Step 8.6: Fix `core.ts:2353` — G5 domain expertise success signal**

In `src/gateway/core.ts`, find line 2353 (the `recordToolExecution` call inside the domain expertise tracker block). It will look like:

```typescript
this.domainExpertise!.recordToolExecution(domain, true);
```

Change it to:
```typescript
this.domainExpertise!.recordToolExecution(domain, (result as any)?.success !== false);
```

The expression `(result as any)?.success !== false` evaluates to `true` when `success` is `undefined` (legacy tools) and `false` only when `success` is explicitly `false`.

- [ ] **Step 8.7: Wire `OwlLearningsLayer` to the database**

In `src/context/layers/behavioral.ts`, update `OwlLearningsLayer` to accept and use a `MemoryDatabase`:

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { MemoryDatabase } from "../../memory/db.js";
import { hash } from "../utils.js";

// ... BehavioralPatchLayer and ActiveIntentsLayer unchanged ...

export class OwlLearningsLayer implements ContextLayer {
  name = "OwlLearningsLayer";
  priority = 95;
  maxTokens = 400;
  produces = ["learnings"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational || t.isReturningUser; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "learnings_v2");
  }

  constructor(private readonly db?: MemoryDatabase) {}

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    let learnings: string[] | undefined;

    if (this.db) {
      const owlName =
        req.session.metadata.activeOwlName ?? req.session.metadata.owlName;
      learnings = this.db.owlLearnings.getForOwlSorted(owlName);
    } else {
      learnings = (req.session as any).owlLearnings as string[] | undefined;
    }

    if (!learnings?.length) return "";
    return `<owl_learnings>\n${learnings.slice(0, 6).map((l) => `  - ${l}`).join("\n")}\n</owl_learnings>`;
  }
}
```

- [ ] **Step 8.8: Pass `deps.db` to `OwlLearningsLayer` in `context/index.ts`**

In `src/context/index.ts`, find line 70:
```typescript
    new OwlLearningsLayer(),
```

Change it to:
```typescript
    new OwlLearningsLayer(deps.db),
```

No import change needed — `MemoryDatabase` is already imported at line 36.

- [ ] **Step 8.9: Run full test suite**

```bash
npm run test
```

Expected: All tests pass, including the new micro-learner tests.

- [ ] **Step 8.10: Commit**

```bash
git add src/intelligence/sleep-time-consolidator.ts \
        src/gateway/core.ts \
        src/context/layers/behavioral.ts \
        src/context/index.ts \
        src/learning/micro-learner.ts \
        __tests__/micro-learner.test.ts
git commit -m "feat(learning): wire OwlLearningsLayer to db, add style/temporal signals, fix domain expertise, add sleep eviction"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec requirement | Task |
|---|---|
| D1: Delete self-study.ts | Task 7 |
| D2: ProactiveContext interface + runProactiveSession | Task 3 |
| D3: post-processor failure critique job | Task 4 |
| D4: admitIfWorthy + evictStale; eviction in SleepTimeConsolidator | Tasks 1 + 8 |
| D5: getEffectivenessScore (SQL aggregation, no schema change) | Task 1 |
| D6: Delete mistake-detector.ts + approach-library.ts; Jaccard ported | Tasks 1 + 2 |
| G5: domain expertise hardcoded `true` → actual success signal | Task 8 |
| G6: style + temporal signal emission | Task 8 |
| G7: twin-engine collapse | Tasks 5–7 |
| behavioral.ts slice(0,5) → slice(0,6) + failure-first ordering | Task 8 |
| getFailureDensityTopics + getSessionFailures | Task 1 |
| getForOwlSorted | Task 1 |

### Invariants Verified

- **No hardcoded keyword arrays** — `computeSimilarity` operates on caller-provided word sets only.
- **Channel parity** — `OwlLearningsLayer` reads from DB via `ContextPipeline`, same path for all channels.
- **Net file delta** — 3 deleted, 0 created = −3. ✓
- **No schema migration** — `getEffectivenessScore` uses SQL aggregation on existing `approach_library` columns.
- **Non-blocking critique job** — errors caught per-turn; loop continues; job completes.
- **admitIfWorthy is not a gate for existing callers** — `compressor.ts`, `parliament/orchestrator.ts`, `tools/remember.ts` still use `db.owlLearnings.add()` directly.
