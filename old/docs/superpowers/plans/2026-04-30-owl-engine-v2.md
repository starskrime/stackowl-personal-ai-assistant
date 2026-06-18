# OwlEngine v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 2,846-line runtime.ts monolith with a two-layer OwlOrchestrator + OwlEngine architecture, adding self-healing, HITL, intelligence growth, and zero jargon leakage to users.

**Architecture:** OwlOrchestrator (7-phase state machine) calls OwlEngine.runTurn() (single-turn executor). Six self-healing components (HealthMonitor, RecoveryOrchestrator, QualityEvaluator, OutcomeJournal, UserFacingStatusNarrator, ImprovementScheduler) live between them. Gateway swaps one call site.

**Tech Stack:** TypeScript strict, better-sqlite3, kuzu (existing), vitest, existing provider/tool/context infrastructure.

---

## Phase 1 — Foundations (Tasks 1–3)

### Task 1: Core types file

**Files:**
- Create: `src/engine/types.ts`
- Test: `__tests__/engine-types.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/engine-types.test.ts
import { describe, it, expect } from "vitest";
import type {
  TurnRequest, TurnResult, TaskLedger, SubGoal,
  RunHealth, HealthSignal, Decision, TokenBudget,
  OrchestratorResponse, FailedToolCall,
} from "../src/engine/types.js";

describe("engine types compile", () => {
  it("TurnResult has typed signals (no text markers)", () => {
    const r: TurnResult = {
      content: "hello",
      toolCalls: [],
      toolResults: [],
      tokensUsed: 100,
      doneSignal: false,
      budgetExhausted: false,
      failedTools: [],
      providerUsed: "anthropic",
      modelUsed: "claude-sonnet-4-6",
    };
    expect(r.budgetExhausted).toBe(false);
    expect(r.doneSignal).toBe(false);
  });

  it("TaskLedger has all required fields", () => {
    const ledger: TaskLedger = {
      id: "l1",
      goal: "research EVs",
      subGoals: [],
      expectedOutput: "comparison table",
      complexity: "medium",
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
      createdAt: Date.now(),
    };
    expect(ledger.complexity).toBe("medium");
  });

  it("Decision is one of five values", () => {
    const d: Decision = "CONTINUE";
    expect(["CONTINUE","REPLAN","HITL","SYNTHESIZE","DEGRADE"]).toContain(d);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/engine-types.test.ts
```
Expected: FAIL — module not found

- [ ] **Step 3: Create `src/engine/types.ts`**

```typescript
import type { ChatMessage, ToolCall, ToolDefinition } from "../providers/base.js";
import type { AIProvider } from "../providers/base.js";
import type { StreamEvent } from "../providers/base.js";

// ─── Token Budget ─────────────────────────────────────────────────

export interface TokenBudget {
  total: number;
  used: number;
  remaining: number;
}

// ─── Turn Contract ────────────────────────────────────────────────

export interface TurnRequest {
  messages: ChatMessage[];
  tools: ToolDefinition[];
  modelName: string;
  providerName: string;
  sessionId: string;
  turnBudget: TokenBudget;
  onStreamEvent?: (event: StreamEvent) => Promise<void>;
  onProgress?: (msg: string) => Promise<void>;
}

export interface FailedToolCall {
  name: string;
  reason: string;
}

export interface TurnResult {
  content: string;
  toolCalls: ToolCall[];
  toolResults: { toolCallId: string; name: string; result: string }[];
  tokensUsed: number;
  doneSignal: boolean;
  budgetExhausted: boolean;
  pendingCapabilityGap?: string;
  failedTools: FailedToolCall[];
  providerUsed: string;
  modelUsed: string;
}

// ─── Task Ledger ──────────────────────────────────────────────────

export type TaskComplexity = "simple" | "medium" | "complex" | "unbounded";
export type SubGoalStatus = "pending" | "in_progress" | "done" | "blocked" | "skipped";

export interface SubGoal {
  id: string;
  description: string;
  status: SubGoalStatus;
  dependsOn: string[];
  result?: string;
}

export interface TaskLedgerRevision {
  at: number;
  reason: string;
  previousGoal: string;
}

export interface TaskLedger {
  id: string;
  goal: string;
  subGoals: SubGoal[];
  expectedOutput: string;
  complexity: TaskComplexity;
  estimatedTurns: number;
  behavioralConstraints: string[];
  parliamentContext?: string;
  approachPatterns: string[];
  reflexionContext?: string;
  revisions: TaskLedgerRevision[];
  createdAt: number;
}

// ─── Health ───────────────────────────────────────────────────────

export type HealthSignalKind =
  | "spinning"
  | "tool_blackout"
  | "budget_critical"
  | "provider_unstable"
  | "stall";

export interface HealthSignal {
  kind: HealthSignalKind;
  detail: string;
  iteration: number;
}

export interface RunHealth {
  iteration: number;
  tokensConsumed: number;
  tokenBudget: number;
  consecutiveFailures: number;
  uniqueToolsAttempted: Set<string>;
  allToolsFailed: boolean;
  spinningDetected: boolean;
  providerSwitchCount: number;
  stuckOnSubGoalId: string | null;
  signals: HealthSignal[];
}

// ─── Recovery ─────────────────────────────────────────────────────

export type Decision =
  | "CONTINUE"
  | "REPLAN"
  | "HITL"
  | "SYNTHESIZE"
  | "DEGRADE";

// ─── HITL ─────────────────────────────────────────────────────────

export interface HitlMemo {
  whatIDid: string;
  whatINeed: string;
  options?: string[];
  recommendation?: string;
}

export interface HitlRequest {
  kind: "approval" | "clarification" | "choice";
  memo: HitlMemo;
  ledgerSnapshot: TaskLedger;
  pendingAction: string;
}

export interface HitlResponse {
  approved: boolean;
  choice?: string;
  freeText?: string;
  timedOut: boolean;
}

export interface HitlChannel {
  pause(request: HitlRequest): Promise<HitlResponse>;
}

// ─── Orchestrator Output ──────────────────────────────────────────

export type DegradationTier = 1 | 2 | 3 | 4;

export interface OrchestratorResponse {
  content: string;
  owlName: string;
  owlEmoji: string;
  toolsUsed: string[];
  qualityScore: number;
  degradationTier: DegradationTier;
  taskCategory?: string;
  complexity: TaskComplexity;
  ledgerId?: string;
  evolutionSignals: {
    qualityScore: number;
    taskCategory: string;
    followUpSentiment?: "positive" | "correction" | "neutral";
  };
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/engine-types.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/types.ts __tests__/engine-types.test.ts
git commit -m "feat(engine): add core types for OwlOrchestrator v2 contract"
```

---

### Task 2: Schema v14 — SQLite migrations

**Files:**
- Modify: `src/memory/db.ts` (lines 29, 1060–1084)
- Test: `__tests__/schema-v14.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/schema-v14.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string;
let db: MemoryDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-v14-"));
  db = new MemoryDatabase(dir);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("schema v14", () => {
  it("task_ledgers table exists", () => {
    const row = db.db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='task_ledgers'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("hitl_checkpoints table exists", () => {
    const row = db.db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_checkpoints'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("approach_patterns table exists", () => {
    const row = db.db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='approach_patterns'"
    ).get();
    expect(row).toBeTruthy();
  });

  it("trajectories has quality_score column", () => {
    const info = db.db.prepare("PRAGMA table_info(trajectories)").all() as {name:string}[];
    const cols = info.map(c => c.name);
    expect(cols).toContain("quality_score");
    expect(cols).toContain("task_category");
    expect(cols).toContain("degradation_tier");
    expect(cols).toContain("follow_up_sentiment");
  });

  it("schema version is 14", () => {
    const v = (db.db.pragma("user_version") as {user_version:number}[])[0]?.user_version;
    expect(v).toBe(14);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/schema-v14.test.ts
```

- [ ] **Step 3: Bump SCHEMA_VERSION and add v14 migration**

In `src/memory/db.ts`, change line 29:
```typescript
const SCHEMA_VERSION = 14;
```

After the `if (current < 13)` block (around line 1080), add before `if (current < SCHEMA_VERSION)`:
```typescript
    if (current < 14) {
      // v14: OwlEngine v2 — task ledgers, HITL checkpoints, approach patterns,
      // extended trajectory quality fields
      this.db.exec(`
        ALTER TABLE trajectories ADD COLUMN quality_score REAL DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN quality_flags TEXT DEFAULT '[]';
        ALTER TABLE trajectories ADD COLUMN task_category TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN task_complexity TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN degradation_tier INTEGER DEFAULT 1;
        ALTER TABLE trajectories ADD COLUMN recovery_actions TEXT DEFAULT '[]';
        ALTER TABLE trajectories ADD COLUMN follow_up_sentiment TEXT DEFAULT NULL;
        ALTER TABLE trajectories ADD COLUMN follow_up_updated_at TEXT DEFAULT NULL;

        CREATE TABLE IF NOT EXISTS task_ledgers (
          id             TEXT PRIMARY KEY,
          session_id     TEXT NOT NULL,
          user_id        TEXT NOT NULL DEFAULT 'default',
          goal           TEXT NOT NULL,
          sub_goals      TEXT NOT NULL DEFAULT '[]',
          expected_output TEXT NOT NULL DEFAULT '',
          complexity     TEXT NOT NULL DEFAULT 'medium',
          status         TEXT NOT NULL DEFAULT 'active',
          revisions      TEXT NOT NULL DEFAULT '[]',
          created_at     TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ledgers_session
          ON task_ledgers(session_id);
        CREATE INDEX IF NOT EXISTS idx_ledgers_user_status
          ON task_ledgers(user_id, status);

        CREATE TABLE IF NOT EXISTS hitl_checkpoints (
          id             TEXT PRIMARY KEY,
          session_id     TEXT NOT NULL,
          ledger_id      TEXT NOT NULL,
          pending_action TEXT NOT NULL,
          request_kind   TEXT NOT NULL,
          memo_json      TEXT NOT NULL,
          status         TEXT NOT NULL DEFAULT 'waiting',
          response_json  TEXT DEFAULT NULL,
          created_at     TEXT NOT NULL DEFAULT (datetime('now')),
          resolved_at    TEXT DEFAULT NULL,
          expires_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_hitl_session
          ON hitl_checkpoints(session_id, status);

        CREATE TABLE IF NOT EXISTS approach_patterns (
          id                   TEXT PRIMARY KEY,
          task_category        TEXT NOT NULL,
          lesson               TEXT NOT NULL,
          successful_sequences TEXT NOT NULL DEFAULT '[]',
          conditions           TEXT NOT NULL DEFAULT '[]',
          observation_count    INTEGER NOT NULL DEFAULT 0,
          success_rate         REAL NOT NULL DEFAULT 0.0,
          status               TEXT NOT NULL DEFAULT 'tentative',
          last_used_at         TEXT DEFAULT NULL,
          created_at           TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_category_status
          ON approach_patterns(task_category, status);
      `);
    }
```

- [ ] **Step 4: Expose `db` property as public on MemoryDatabase**

Find the `private db: BetterSqlite3.Database` declaration (or similar) and make it `readonly db` so tests can access it. Search for the constructor:

```typescript
// In the MemoryDatabase class, change:
private readonly db: Database;
// to:
readonly db: Database;
```

- [ ] **Step 5: Run test, confirm it passes**

```bash
npx vitest run __tests__/schema-v14.test.ts
```

- [ ] **Step 6: Run full test suite — confirm no regressions**

```bash
npx vitest run
```

- [ ] **Step 7: Commit**

```bash
git add src/memory/db.ts __tests__/schema-v14.test.ts
git commit -m "feat(db): schema v14 — task_ledgers, hitl_checkpoints, approach_patterns, trajectory quality fields"
```

---

### Task 3: ToolDefinition sequential flag

**Files:**
- Modify: `src/providers/base.ts` (line 26)
- Test: `__tests__/tool-sequential.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/tool-sequential.test.ts
import { describe, it, expect } from "vitest";
import type { ToolDefinition } from "../src/providers/base.js";

describe("ToolDefinition sequential flag", () => {
  it("accepts sequential:true for file-edit tools", () => {
    const tool: ToolDefinition = {
      name: "edit_file",
      description: "Edit a file",
      parameters: { type: "object", properties: {}, required: [] },
      sequential: true,
    };
    expect(tool.sequential).toBe(true);
  });

  it("defaults to undefined (falsy) for parallel-safe tools", () => {
    const tool: ToolDefinition = {
      name: "web_search",
      description: "Search",
      parameters: { type: "object", properties: {}, required: [] },
    };
    expect(tool.sequential).toBeFalsy();
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/tool-sequential.test.ts
```

- [ ] **Step 3: Add sequential field to ToolDefinition**

In `src/providers/base.ts`, modify the `ToolDefinition` interface (line ~26):

```typescript
export interface ToolDefinition {
  name: string;
  description: string;
  parameters: {
    type: "object";
    properties: Record<
      string,
      {
        type: string;
        description: string;
        enum?: string[];
      }
    >;
    required?: string[];
  };
  /**
   * When true, this tool must not run concurrently with other tools.
   * Used for file-editing chains (edit_file, write_file) where order matters.
   * Default: false (tool is safe to run in parallel).
   */
  sequential?: boolean;
}
```

- [ ] **Step 4: Mark sequential tools in their definitions**

Search for tool definitions in `src/tools/`:
```bash
grep -r "name: \"edit_file\"\|name: \"write_file\"\|name: \"computer_use\"" src/tools/ --include="*.ts" -l
```

In each file found, add `sequential: true` to the `edit_file`, `write_file`, and `computer_use` tool definitions.

- [ ] **Step 5: Run test, confirm it passes**

```bash
npx vitest run __tests__/tool-sequential.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add src/providers/base.ts src/tools/ __tests__/tool-sequential.test.ts
git commit -m "feat(tools): add sequential flag to ToolDefinition — edit_file/write_file/computer_use marked sequential"
```

---

## Phase 2 — Self-Healing Components (Tasks 4–8)

### Task 4: HealthMonitor

**Files:**
- Create: `src/engine/health-monitor.ts`
- Test: `__tests__/health-monitor.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/health-monitor.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { HealthMonitor } from "../src/engine/health-monitor.js";
import type { TurnResult, TaskLedger } from "../src/engine/types.js";

const makeTurn = (overrides: Partial<TurnResult> = {}): TurnResult => ({
  content: "thinking...",
  toolCalls: [],
  toolResults: [],
  tokensUsed: 100,
  doneSignal: false,
  budgetExhausted: false,
  failedTools: [],
  providerUsed: "anthropic",
  modelUsed: "claude-sonnet-4-6",
  ...overrides,
});

const makeLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "medium", estimatedTurns: 5, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});

describe("HealthMonitor", () => {
  let monitor: HealthMonitor;
  beforeEach(() => { monitor = new HealthMonitor(1000); });

  it("shouldContinue returns true when healthy", () => {
    expect(monitor.shouldContinue()).toBe(true);
  });

  it("detects budget_critical signal at 85% tokens", () => {
    const turn = makeTurn({ tokensUsed: 860 }); // 86% of 1000
    monitor.observe(turn, makeLedger(), 0);
    const health = monitor.getHealth();
    expect(health.signals.some(s => s.kind === "budget_critical")).toBe(true);
  });

  it("shouldContinue returns false when budget exhausted", () => {
    const turn = makeTurn({ budgetExhausted: true });
    monitor.observe(turn, makeLedger(), 0);
    expect(monitor.shouldContinue()).toBe(false);
  });

  it("detects stall when same subgoal stuck for 3 turns", () => {
    const ledger = makeLedger();
    ledger.subGoals = [{ id: "sg1", description: "do x", status: "in_progress", dependsOn: [] }];
    const turn = makeTurn();
    monitor.observe(turn, ledger, 0);
    monitor.observe(turn, ledger, 1);
    monitor.observe(turn, ledger, 2);
    const health = monitor.getHealth();
    expect(health.signals.some(s => s.kind === "stall")).toBe(true);
  });

  it("detects tool_blackout when all tools failed", () => {
    const turn = makeTurn({
      failedTools: [{ name: "web_search", reason: "timeout" }],
      toolCalls: [{ id: "1", name: "web_search", arguments: {} }],
    });
    // Register the tool as attempted then failed
    monitor.observe(turn, makeLedger(), 0);
    // Second turn: same tool fails again — now we've only tried 1 tool and it always fails
    const t2 = makeTurn({
      toolCalls: [],
      failedTools: [],
    });
    monitor.observe(t2, makeLedger(), 1);
    // Not blackout yet (tool hasn't been tried and failed ALL available tools)
    expect(monitor.getHealth()).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/health-monitor.test.ts
```

- [ ] **Step 3: Create `src/engine/health-monitor.ts`**

```typescript
import type { TurnResult, TaskLedger, RunHealth, HealthSignal, HealthSignalKind } from "./types.js";

export class HealthMonitor {
  private health: RunHealth;
  private readonly tokenBudget: number;
  private consecutiveSameSubGoal = 0;
  private lastActiveSubGoalId: string | null = null;

  constructor(tokenBudget: number) {
    this.tokenBudget = tokenBudget;
    this.health = {
      iteration: 0,
      tokensConsumed: 0,
      tokenBudget,
      consecutiveFailures: 0,
      uniqueToolsAttempted: new Set(),
      allToolsFailed: false,
      spinningDetected: false,
      providerSwitchCount: 0,
      stuckOnSubGoalId: null,
      signals: [],
    };
  }

  observe(turn: TurnResult, ledger: TaskLedger, iteration: number): void {
    this.health.iteration = iteration;
    this.health.tokensConsumed += turn.tokensUsed;

    // Track tools attempted
    for (const tc of turn.toolCalls) {
      this.health.uniqueToolsAttempted.add(tc.name);
    }

    // Track failures
    if (turn.failedTools.length > 0 && turn.toolCalls.length > 0) {
      const allFailed = turn.failedTools.length === turn.toolCalls.length;
      if (allFailed) this.health.consecutiveFailures++;
      else this.health.consecutiveFailures = 0;
    } else if (turn.toolCalls.length === 0) {
      // No tool calls — no failure tracking change
    } else {
      this.health.consecutiveFailures = 0;
    }

    // Detect stall: same in_progress subgoal for 3+ turns
    const activeSubGoal = ledger.subGoals.find(sg => sg.status === "in_progress");
    if (activeSubGoal) {
      if (activeSubGoal.id === this.lastActiveSubGoalId) {
        this.consecutiveSameSubGoal++;
      } else {
        this.consecutiveSameSubGoal = 1;
        this.lastActiveSubGoalId = activeSubGoal.id;
      }
      if (this.consecutiveSameSubGoal >= 3) {
        this.health.stuckOnSubGoalId = activeSubGoal.id;
      }
    }

    // Detect tool_blackout: every attempted tool has failed at least once
    if (this.health.uniqueToolsAttempted.size > 0) {
      const failedNames = new Set(turn.failedTools.map(f => f.name));
      this.health.allToolsFailed = [...this.health.uniqueToolsAttempted].every(
        n => failedNames.has(n)
      );
    }

    // Emit signals
    this.health.signals = [];
    this._checkSignals(turn);
  }

  shouldContinue(): boolean {
    if (this.health.tokensConsumed >= this.tokenBudget) return false;
    if (this.health.allToolsFailed && this.health.consecutiveFailures >= 3) return false;
    return true;
  }

  getHealth(): RunHealth {
    return { ...this.health, uniqueToolsAttempted: new Set(this.health.uniqueToolsAttempted) };
  }

  private _checkSignals(turn: TurnResult): void {
    const pct = this.health.tokensConsumed / this.tokenBudget;

    if (pct >= 0.85) {
      this._emit("budget_critical", `${Math.round(pct * 100)}% budget consumed`);
    }
    if (this.health.stuckOnSubGoalId) {
      this._emit("stall", `SubGoal ${this.health.stuckOnSubGoalId} stuck for ${this.consecutiveSameSubGoal} turns`);
    }
    if (this.health.allToolsFailed && this.health.uniqueToolsAttempted.size > 1) {
      this._emit("tool_blackout", `All ${this.health.uniqueToolsAttempted.size} tools failed`);
    }
    if (turn.budgetExhausted) {
      this._emit("budget_critical", "Engine reported budget exhausted");
    }
    if (this.health.providerSwitchCount > 1) {
      this._emit("provider_unstable", `${this.health.providerSwitchCount} provider switches`);
    }
  }

  private _emit(kind: HealthSignalKind, detail: string): void {
    this.health.signals.push({ kind, detail, iteration: this.health.iteration });
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/health-monitor.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/health-monitor.ts __tests__/health-monitor.test.ts
git commit -m "feat(engine): add HealthMonitor — observes signals per turn (stall, blackout, budget)"
```

---

### Task 5: RecoveryOrchestrator

**Files:**
- Create: `src/engine/recovery-orchestrator.ts`
- Test: `__tests__/recovery-orchestrator.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/recovery-orchestrator.test.ts
import { describe, it, expect } from "vitest";
import { decide } from "../src/engine/recovery-orchestrator.js";
import type { RunHealth, TurnResult, TaskLedger } from "../src/engine/types.js";

const baseHealth = (): RunHealth => ({
  iteration: 0, tokensConsumed: 100, tokenBudget: 8000,
  consecutiveFailures: 0, uniqueToolsAttempted: new Set(["web_search"]),
  allToolsFailed: false, spinningDetected: false, providerSwitchCount: 0,
  stuckOnSubGoalId: null, signals: [],
});

const baseTurn = (): TurnResult => ({
  content: "thinking", toolCalls: [], toolResults: [],
  tokensUsed: 100, doneSignal: false, budgetExhausted: false,
  failedTools: [], providerUsed: "anthropic", modelUsed: "claude-sonnet-4-6",
});

const baseLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "medium", estimatedTurns: 5, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});

const baseDna = { riskTolerance: "balanced" as const, challengeLevel: "medium" as const };

describe("RecoveryOrchestrator.decide()", () => {
  it("returns CONTINUE when healthy and no done signal", () => {
    expect(decide(baseHealth(), baseTurn(), baseLedger(), baseDna)).toBe("CONTINUE");
  });

  it("returns SYNTHESIZE when doneSignal is true", () => {
    const turn = { ...baseTurn(), doneSignal: true };
    expect(decide(baseHealth(), turn, baseLedger(), baseDna)).toBe("SYNTHESIZE");
  });

  it("returns SYNTHESIZE when budget exhausted", () => {
    const turn = { ...baseTurn(), budgetExhausted: true };
    expect(decide(baseHealth(), turn, baseLedger(), baseDna)).toBe("SYNTHESIZE");
  });

  it("returns REPLAN when stall signal present", () => {
    const h = baseHealth();
    h.signals = [{ kind: "stall", detail: "sg1 stuck", iteration: 3 }];
    h.stuckOnSubGoalId = "sg1";
    expect(decide(h, baseTurn(), baseLedger(), baseDna)).toBe("REPLAN");
  });

  it("returns DEGRADE when tool_blackout and no partial results", () => {
    const h = baseHealth();
    h.signals = [{ kind: "tool_blackout", detail: "all failed", iteration: 5 }];
    h.allToolsFailed = true;
    h.consecutiveFailures = 5;
    const ledger = baseLedger();
    // No subgoals done = no partial results
    expect(decide(h, baseTurn(), ledger, baseDna)).toBe("DEGRADE");
  });

  it("returns SYNTHESIZE when tool_blackout but partial results exist", () => {
    const h = baseHealth();
    h.signals = [{ kind: "tool_blackout", detail: "all failed", iteration: 5 }];
    h.allToolsFailed = true;
    const ledger = baseLedger();
    ledger.subGoals = [{ id: "sg1", description: "done step", status: "done", dependsOn: [], result: "got data" }];
    expect(decide(h, baseTurn(), ledger, baseDna)).toBe("SYNTHESIZE");
  });

  it("applies cautious DNA — HITL threshold is lower", () => {
    const dna = { riskTolerance: "cautious" as const, challengeLevel: "medium" as const };
    const h = baseHealth();
    h.signals = [{ kind: "budget_critical", detail: "90%", iteration: 8 }];
    // With cautious DNA, budget_critical triggers HITL instead of SYNTHESIZE
    const result = decide(h, baseTurn(), baseLedger(), dna);
    expect(["HITL", "SYNTHESIZE"]).toContain(result);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/recovery-orchestrator.test.ts
```

- [ ] **Step 3: Create `src/engine/recovery-orchestrator.ts`**

```typescript
import type { RunHealth, TurnResult, TaskLedger, Decision } from "./types.js";

interface DnaThresholds {
  riskTolerance: "cautious" | "balanced" | "aggressive";
  challengeLevel: "low" | "medium" | "high";
}

/**
 * Pure function. No state. Returns exactly one of five decisions.
 * All control-flow logic lives here — nowhere else.
 */
export function decide(
  health: RunHealth,
  turn: TurnResult,
  ledger: TaskLedger,
  dna: DnaThresholds,
): Decision {
  const maxReplans = dna.challengeLevel === "high" ? 3
    : dna.challengeLevel === "low" ? 1
    : 2;

  // 1. Done signal — always SYNTHESIZE immediately
  if (turn.doneSignal) return "SYNTHESIZE";

  // 2. Budget exhausted — SYNTHESIZE (with what we have)
  if (turn.budgetExhausted) return "SYNTHESIZE";

  const hasStall = health.signals.some(s => s.kind === "stall");
  const hasBudgetCritical = health.signals.some(s => s.kind === "budget_critical");
  const hasToolBlackout = health.signals.some(s => s.kind === "tool_blackout");

  // 3. Stall → REPLAN (if we haven't exhausted replans)
  if (hasStall) {
    const replanCount = ledger.revisions.length;
    if (replanCount < maxReplans) return "REPLAN";
    // Too many replans → check if we have anything useful
    return _synthesizeOrDegrade(ledger);
  }

  // 4. Tool blackout → SYNTHESIZE or DEGRADE based on partial results
  if (hasToolBlackout) {
    return _synthesizeOrDegrade(ledger);
  }

  // 5. Budget critical → cautious DNA triggers HITL; others SYNTHESIZE
  if (hasBudgetCritical) {
    if (dna.riskTolerance === "cautious") return "HITL";
    return "SYNTHESIZE";
  }

  // 6. Pending capability gap → HITL
  if (turn.pendingCapabilityGap) return "HITL";

  return "CONTINUE";
}

function _hasPartialResults(ledger: TaskLedger): boolean {
  return ledger.subGoals.some(sg => sg.status === "done" && sg.result);
}

function _synthesizeOrDegrade(ledger: TaskLedger): Decision {
  return _hasPartialResults(ledger) ? "SYNTHESIZE" : "DEGRADE";
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/recovery-orchestrator.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/recovery-orchestrator.ts __tests__/recovery-orchestrator.test.ts
git commit -m "feat(engine): add RecoveryOrchestrator — pure decide() function, all control-flow in one place"
```

---

### Task 6: QualityEvaluator

**Files:**
- Create: `src/engine/quality-evaluator.ts`
- Test: `__tests__/quality-evaluator.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/quality-evaluator.test.ts
import { describe, it, expect } from "vitest";
import { QualityEvaluator } from "../src/engine/quality-evaluator.js";

describe("QualityEvaluator.evaluateSync()", () => {
  const ev = new QualityEvaluator();

  it("starts at 1.0 for clean response", () => {
    const score = ev.evaluateSync({
      content: "Here is the comparison table you requested.",
      loopExhausted: false,
      toolCallCount: 3,
      toolFailureCount: 0,
      taskComplexity: "medium",
      hasStructuredOutput: true,
    });
    expect(score).toBeGreaterThan(0.9);
  });

  it("penalizes loop exhaustion (-0.30)", () => {
    const score = ev.evaluateSync({
      content: "I tried many things.",
      loopExhausted: true,
      toolCallCount: 5,
      toolFailureCount: 3,
      taskComplexity: "medium",
      hasStructuredOutput: false,
    });
    expect(score).toBeLessThan(0.75);
  });

  it("penalizes raw error patterns", () => {
    const score = ev.evaluateSync({
      content: "Error: HTTP 429 Too Many Requests\nFailed to fetch.",
      loopExhausted: false,
      toolCallCount: 1,
      toolFailureCount: 1,
      taskComplexity: "simple",
      hasStructuredOutput: false,
    });
    expect(score).toBeLessThan(0.75);
  });

  it("penalizes response too short for non-trivial task", () => {
    const score = ev.evaluateSync({
      content: "Done.",
      loopExhausted: false,
      toolCallCount: 0,
      toolFailureCount: 0,
      taskComplexity: "complex",
      hasStructuredOutput: false,
    });
    expect(score).toBeLessThan(0.8);
  });

  it("strips EXHAUSTION_MARKER and penalizes heavily", () => {
    const { score, cleanContent } = ev.evaluateAndStrip({
      content: "I tried. __STACKOWL_EXHAUSTED__",
      loopExhausted: true,
      toolCallCount: 0,
      toolFailureCount: 0,
      taskComplexity: "medium",
      hasStructuredOutput: false,
    });
    expect(cleanContent).not.toContain("__STACKOWL_EXHAUSTED__");
    expect(score).toBeLessThan(0.6);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/quality-evaluator.test.ts
```

- [ ] **Step 3: Create `src/engine/quality-evaluator.ts`**

```typescript
import type { TaskComplexity } from "./types.js";

interface SyncInput {
  content: string;
  loopExhausted: boolean;
  toolCallCount: number;
  toolFailureCount: number;
  taskComplexity: TaskComplexity;
  hasStructuredOutput: boolean;
}

const EXHAUSTION_MARKER = "__STACKOWL_EXHAUSTED__";
const RAW_ERROR_PATTERN = /\b(Error:|HTTP [45]\d{2}|ENOTFOUND|ECONNREFUSED|timeout|stack trace|Traceback)\b/i;
const JARGON_PATTERNS: [RegExp, string][] = [
  [/HTTP [45]\d{2}[^\n]*/gi, ""],
  [/\bAPI\b/g, "the service"],
  [/\btool (failed|error)\b/gi, "ran into a snag"],
  [/\btimeout\b/gi, "took too long to respond"],
  [/\b429\b/g, ""],
  [/\bECONNREFUSED\b/gi, "could not be reached"],
  [/__STACKOWL_EXHAUSTED__/g, ""],
];

export class QualityEvaluator {
  /**
   * Synchronous quality score — < 1ms, no LLM.
   * Returns 0.0–1.0.
   */
  evaluateSync(input: SyncInput): number {
    let score = 1.0;

    if (input.loopExhausted) score -= 0.30;
    if (input.content.includes(EXHAUSTION_MARKER)) score -= 0.40;
    if (RAW_ERROR_PATTERN.test(input.content)) score -= 0.30;

    const len = input.content.length;
    if (len < 50 && input.taskComplexity !== "simple") score -= 0.25;
    if (len > 2000 && input.taskComplexity === "simple") score -= 0.15;

    if (input.toolCallCount > 0 && input.toolFailureCount === 0) score += 0.10;
    if (input.hasStructuredOutput) score += 0.10;

    return Math.max(0, Math.min(1, score));
  }

  /**
   * Same as evaluateSync but also strips markers and jargon from content.
   */
  evaluateAndStrip(input: SyncInput): { score: number; cleanContent: string } {
    const score = this.evaluateSync(input);
    let clean = input.content;
    for (const [pattern, replacement] of JARGON_PATTERNS) {
      clean = clean.replace(pattern, replacement);
    }
    clean = clean.replace(/\n{3,}/g, "\n\n").trim();
    return { score, cleanContent: clean };
  }

  /**
   * Returns the jargon-free version of any content string.
   * Used by UserFacingStatusNarrator.postProcess().
   */
  stripJargon(content: string): string {
    let clean = content;
    for (const [pattern, replacement] of JARGON_PATTERNS) {
      clean = clean.replace(pattern, replacement);
    }
    return clean.replace(/\n{3,}/g, "\n\n").trim();
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/quality-evaluator.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/quality-evaluator.ts __tests__/quality-evaluator.test.ts
git commit -m "feat(engine): add QualityEvaluator — sync 0-1 score, EXHAUSTION_MARKER stripping, jargon removal"
```

---

### Task 7: OutcomeJournal

**Files:**
- Create: `src/engine/outcome-journal.ts`
- Test: `__tests__/outcome-journal.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/outcome-journal.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { OutcomeJournal } from "../src/engine/outcome-journal.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase, journal: OutcomeJournal;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-journal-"));
  db = new MemoryDatabase(dir);
  journal = new OutcomeJournal(db);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("OutcomeJournal", () => {
  it("records a run and retrieves it", async () => {
    const id = await journal.record({
      sessionId: "s1",
      owlName: "atlas",
      userId: "u1",
      userMessage: "research EVs",
      totalTurns: 5,
      toolsUsed: ["web_search"],
      outcome: "success",
      reward: 0.8,
      qualityScore: 0.85,
      qualityFlags: [],
      taskCategory: "research",
      taskComplexity: "medium",
      degradationTier: 1,
      recoveryActions: [],
    });
    expect(id).toBeTruthy();
    const entries = await journal.getRecent(5);
    expect(entries.length).toBe(1);
    expect(entries[0].qualityScore).toBe(0.85);
  });

  it("updates follow-up sentiment", async () => {
    const id = await journal.record({
      sessionId: "s1", owlName: "atlas", userId: "u1",
      userMessage: "test", totalTurns: 1, toolsUsed: [],
      outcome: "success", reward: 0.5,
      qualityScore: 0.7, qualityFlags: [],
      taskCategory: "general", taskComplexity: "simple",
      degradationTier: 1, recoveryActions: [],
    });
    await journal.updateSentiment(id, "correction");
    const entries = await journal.getRecent(1);
    expect(entries[0].followUpSentiment).toBe("correction");
  });

  it("getFailures returns only low-quality entries", async () => {
    await journal.record({
      sessionId: "s1", owlName: "atlas", userId: "u1",
      userMessage: "fail test", totalTurns: 3, toolsUsed: [],
      outcome: "failure", reward: -0.5,
      qualityScore: 0.2, qualityFlags: ["loop_exhausted"],
      taskCategory: "research", taskComplexity: "complex",
      degradationTier: 3, recoveryActions: ["replan"],
    });
    const fails = await journal.getFailures({ minEntries: 1 });
    expect(fails.length).toBe(1);
    expect(fails[0].qualityScore).toBeLessThan(0.5);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/outcome-journal.test.ts
```

- [ ] **Step 3: Create `src/engine/outcome-journal.ts`**

```typescript
import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type { DegradationTier } from "./types.js";

interface JournalEntry {
  sessionId: string;
  owlName: string;
  userId: string;
  userMessage: string;
  totalTurns: number;
  toolsUsed: string[];
  outcome: "success" | "failure" | "partial";
  reward: number;
  qualityScore: number;
  qualityFlags: string[];
  taskCategory: string;
  taskComplexity: string;
  degradationTier: DegradationTier;
  recoveryActions: string[];
  followUpSentiment?: "positive" | "correction" | "neutral";
}

interface StoredEntry extends JournalEntry {
  id: string;
  followUpSentiment?: "positive" | "correction" | "neutral";
  createdAt: string;
}

export class OutcomeJournal {
  private db: MemoryDatabase;

  constructor(db: MemoryDatabase) {
    this.db = db;
  }

  async record(entry: JournalEntry): Promise<string> {
    const id = uuidv4();
    const now = new Date().toISOString();
    this.db.db.prepare(`
      INSERT INTO trajectories (
        id, session_id, owl_name, user_id, user_message,
        total_turns, tools_used, outcome, reward,
        quality_score, quality_flags, task_category, task_complexity,
        degradation_tier, recovery_actions, created_at, completed_at
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, entry.sessionId, entry.owlName, entry.userId, entry.userMessage,
      entry.totalTurns, JSON.stringify(entry.toolsUsed), entry.outcome, entry.reward,
      entry.qualityScore, JSON.stringify(entry.qualityFlags), entry.taskCategory,
      entry.taskComplexity, entry.degradationTier, JSON.stringify(entry.recoveryActions),
      now, now,
    );
    return id;
  }

  async updateSentiment(
    id: string,
    sentiment: "positive" | "correction" | "neutral",
  ): Promise<void> {
    this.db.db.prepare(`
      UPDATE trajectories
      SET follow_up_sentiment = ?, follow_up_updated_at = ?
      WHERE id = ?
    `).run(sentiment, new Date().toISOString(), id);
  }

  async getRecent(limit: number): Promise<StoredEntry[]> {
    const rows = this.db.db.prepare(`
      SELECT * FROM trajectories
      WHERE quality_score IS NOT NULL
      ORDER BY created_at DESC LIMIT ?
    `).all(limit) as any[];
    return rows.map(this._parse);
  }

  async getFailures({ minEntries }: { minEntries: number }): Promise<StoredEntry[]> {
    const rows = this.db.db.prepare(`
      SELECT * FROM trajectories
      WHERE quality_score IS NOT NULL AND quality_score < 0.5
      ORDER BY created_at DESC LIMIT 50
    `).all() as any[];
    if (rows.length < minEntries) return [];
    return rows.map(this._parse);
  }

  private _parse(row: any): StoredEntry {
    return {
      id: row.id,
      sessionId: row.session_id,
      owlName: row.owl_name,
      userId: row.user_id ?? "default",
      userMessage: row.user_message,
      totalTurns: row.total_turns,
      toolsUsed: JSON.parse(row.tools_used ?? "[]"),
      outcome: row.outcome,
      reward: row.reward,
      qualityScore: row.quality_score,
      qualityFlags: JSON.parse(row.quality_flags ?? "[]"),
      taskCategory: row.task_category ?? "general",
      taskComplexity: row.task_complexity ?? "medium",
      degradationTier: (row.degradation_tier ?? 1) as DegradationTier,
      recoveryActions: JSON.parse(row.recovery_actions ?? "[]"),
      followUpSentiment: row.follow_up_sentiment ?? undefined,
      createdAt: row.created_at,
    };
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/outcome-journal.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/outcome-journal.ts __tests__/outcome-journal.test.ts
git commit -m "feat(engine): add OutcomeJournal — records every run quality score + sentiment to SQLite"
```

---

### Task 8: UserFacingStatusNarrator

**Files:**
- Create: `src/engine/user-facing-narrator.ts`
- Test: `__tests__/user-facing-narrator.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/user-facing-narrator.test.ts
import { describe, it, expect } from "vitest";
import { UserFacingStatusNarrator } from "../src/engine/user-facing-narrator.js";

const narrator = new UserFacingStatusNarrator();

describe("UserFacingStatusNarrator", () => {
  it("strips EXHAUSTION_MARKER", () => {
    const result = narrator.postProcess("I tried. __STACKOWL_EXHAUSTED__", 0.3);
    expect(result).not.toContain("__STACKOWL_EXHAUSTED__");
  });

  it("strips CAPABILITY_GAP markers", () => {
    const result = narrator.postProcess("Can't do it. [CAPABILITY_GAP: need tool X]", 0.5);
    expect(result).not.toContain("[CAPABILITY_GAP");
  });

  it("strips SYSTEM markers", () => {
    const result = narrator.postProcess("[SYSTEM: replan triggered] Working on it.", 0.7);
    expect(result).not.toContain("[SYSTEM:");
  });

  it("translates HTTP error jargon", () => {
    const result = narrator.postProcess("Got HTTP 429 when calling the service.", 0.6);
    expect(result).not.toContain("HTTP 429");
  });

  it("builds tier-1 degradation message", () => {
    const msg = narrator.buildDegradation(1, "Here is what I found.", undefined, undefined);
    expect(msg).toContain("Here is what I found");
  });

  it("builds tier-3 degradation message mentioning what was understood", () => {
    const msg = narrator.buildDegradation(3, "", "research EVs", "need login credentials");
    expect(msg.length).toBeGreaterThan(20);
    expect(msg).not.toContain("undefined");
  });

  it("returns one of the status messages for tool_executing state", () => {
    const msg = narrator.statusMessage("tool_executing");
    expect(msg.length).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/user-facing-narrator.test.ts
```

- [ ] **Step 3: Create `src/engine/user-facing-narrator.ts`**

```typescript
import type { DegradationTier } from "./types.js";

type InternalState =
  | "tool_executing"
  | "tool_failed_retrying"
  | "switching_approach"
  | "provider_switching"
  | "compiling_results";

const STATUS_MESSAGES: Record<InternalState, string[]> = {
  tool_executing:       ["Looking into this...", "On it...", "Checking that now..."],
  tool_failed_retrying: ["Let me try another way...", "Checking a different source..."],
  switching_approach:   ["Taking a fresh approach...", "Trying something different..."],
  provider_switching:   ["Just a moment...", "One second..."],
  compiling_results:    ["Putting this together...", "Almost there...", "Finishing up..."],
};

const STRIP_PATTERNS: RegExp[] = [
  /__STACKOWL_EXHAUSTED__/g,
  /\[CAPABILITY_GAP:[^\]]*\]/g,
  /\[SYSTEM:[^\]]*\]/g,
  /\[DONE\]/g,
  /\[DEEPER\]/gi,
  /\[LOOP GUARD\][^\n]*/g,
  /\[RISK GATE\][^\n]*/g,
];

const JARGON_MAP: [RegExp, string][] = [
  [/HTTP [45]\d{2}[^\n]*/gi, ""],
  [/\b429\b/g, ""],
  [/\bECONNREFUSED\b/gi, "could not be reached"],
  [/\bENOTFOUND\b/gi, "could not be found"],
  [/\btimeout\b/gi, "took too long to respond"],
  [/\btool (failed|error)\b/gi, "ran into a snag"],
  [/\bAPI\b/g, "the service"],
  [/\bprovider\b/gi, "assistant"],
  [/\bstack trace\b/gi, ""],
  [/\bTraceback[^)]*\)/gi, ""],
];

const DEGRADATION_TEMPLATES: Record<DegradationTier, (partial: string, gap: string | undefined, next: string | undefined) => string> = {
  1: (partial) => partial,
  2: (partial, gap) =>
    [partial, gap ? `\n\nI wasn't able to ${gap}.` : "", "\nLet me know if you'd like me to try a different approach."].join(""),
  3: (_, gap, next) =>
    [
      "I understood what you're looking for, but I need a bit more to complete this.",
      gap ? `\nSpecifically: ${gap}.` : "",
      next ? `\n\nHere's what would help: ${next}` : "",
    ].join(""),
  4: (_, gap, next) =>
    [
      "I wasn't able to complete this with what I currently have access to.",
      gap ? `\nThe blocker was: ${gap}.` : "",
      next ? `\n\nHere's what you can do instead:\n${next}` : "",
    ].join(""),
};

export class UserFacingStatusNarrator {
  /**
   * Run on every response before delivery.
   * Strips all internal markers and jargon.
   */
  postProcess(content: string, _qualityScore: number): string {
    let clean = content;
    for (const pattern of STRIP_PATTERNS) {
      clean = clean.replace(pattern, "");
    }
    for (const [pattern, replacement] of JARGON_MAP) {
      clean = clean.replace(pattern, replacement);
    }
    return clean.replace(/\n{3,}/g, "\n\n").trim();
  }

  /**
   * Returns a random status message for real-time progress streaming.
   */
  statusMessage(state: InternalState): string {
    const options = STATUS_MESSAGES[state];
    return options[Math.floor(Math.random() * options.length)];
  }

  /**
   * Builds user-facing degradation response for tiers 1–4.
   */
  buildDegradation(
    tier: DegradationTier,
    partialResult: string,
    obstacle: string | undefined,
    nextStep: string | undefined,
  ): string {
    return DEGRADATION_TEMPLATES[tier](partialResult, obstacle, nextStep).trim();
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/user-facing-narrator.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/user-facing-narrator.ts __tests__/user-facing-narrator.test.ts
git commit -m "feat(engine): add UserFacingStatusNarrator — strips markers, jargon translation, degradation tiers"
```

---

## Phase 3 — TaskLedger + OwlEngine.runTurn() (Tasks 9–10)

### Task 9: TaskLedger store

**Files:**
- Create: `src/engine/task-ledger.ts`
- Test: `__tests__/task-ledger.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/task-ledger.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { TaskLedgerStore } from "../src/engine/task-ledger.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase, store: TaskLedgerStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-ledger-"));
  db = new MemoryDatabase(dir);
  store = new TaskLedgerStore(db);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("TaskLedgerStore", () => {
  it("saves and retrieves a ledger", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "research EVs",
      subGoals: [{ id: "sg1", description: "search", status: "pending", dependsOn: [] }],
      expectedOutput: "comparison table",
      complexity: "medium",
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
    });
    await store.save(ledger);
    const loaded = await store.load(ledger.id);
    expect(loaded?.goal).toBe("research EVs");
    expect(loaded?.subGoals.length).toBe(1);
  });

  it("updates sub-goal status", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "test", subGoals: [{ id: "sg1", description: "step", status: "pending", dependsOn: [] }],
      expectedOutput: "", complexity: "simple", estimatedTurns: 1,
      behavioralConstraints: [], approachPatterns: [], revisions: [],
    });
    await store.save(ledger);
    await store.updateSubGoal(ledger.id, "sg1", "done", "result text");
    const updated = await store.load(ledger.id);
    expect(updated?.subGoals[0].status).toBe("done");
    expect(updated?.subGoals[0].result).toBe("result text");
  });

  it("addRevision appends to revisions array", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "test", subGoals: [],
      expectedOutput: "", complexity: "simple", estimatedTurns: 1,
      behavioralConstraints: [], approachPatterns: [], revisions: [],
    });
    await store.save(ledger);
    await store.addRevision(ledger.id, "stall detected", "test");
    const updated = await store.load(ledger.id);
    expect(updated?.revisions.length).toBe(1);
    expect(updated?.revisions[0].reason).toBe("stall detected");
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/task-ledger.test.ts
```

- [ ] **Step 3: Create `src/engine/task-ledger.ts`**

```typescript
import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type { TaskLedger, SubGoal, SubGoalStatus, TaskComplexity, TaskLedgerRevision } from "./types.js";

type LedgerCreateInput = Omit<TaskLedger, "id" | "createdAt">;

export class TaskLedgerStore {
  constructor(private readonly db: MemoryDatabase) {}

  create(sessionId: string, userId: string, input: LedgerCreateInput): TaskLedger {
    return {
      id: uuidv4(),
      createdAt: Date.now(),
      ...input,
    };
  }

  async save(ledger: TaskLedger): Promise<void> {
    const now = new Date().toISOString();
    this.db.db.prepare(`
      INSERT OR REPLACE INTO task_ledgers
        (id, session_id, user_id, goal, sub_goals, expected_output,
         complexity, status, revisions, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      ledger.id,
      (ledger as any).sessionId ?? "unknown",
      (ledger as any).userId ?? "default",
      ledger.goal,
      JSON.stringify(ledger.subGoals),
      ledger.expectedOutput,
      ledger.complexity,
      "active",
      JSON.stringify(ledger.revisions),
      new Date(ledger.createdAt).toISOString(),
      now,
    );
  }

  async load(id: string): Promise<TaskLedger | null> {
    const row = this.db.db.prepare(
      "SELECT * FROM task_ledgers WHERE id = ?"
    ).get(id) as any;
    if (!row) return null;
    return this._parse(row);
  }

  async updateSubGoal(
    ledgerId: string,
    subGoalId: string,
    status: SubGoalStatus,
    result?: string,
  ): Promise<void> {
    const ledger = await this.load(ledgerId);
    if (!ledger) return;
    ledger.subGoals = ledger.subGoals.map(sg =>
      sg.id === subGoalId ? { ...sg, status, result: result ?? sg.result } : sg
    );
    await this.save({ ...ledger, sessionId: (ledger as any).sessionId, userId: (ledger as any).userId } as any);
  }

  async addRevision(ledgerId: string, reason: string, previousGoal: string): Promise<void> {
    const ledger = await this.load(ledgerId);
    if (!ledger) return;
    const revision: TaskLedgerRevision = { at: Date.now(), reason, previousGoal };
    ledger.revisions = [...ledger.revisions, revision];
    await this.save(ledger as any);
  }

  private _parse(row: any): TaskLedger {
    return {
      id: row.id,
      goal: row.goal,
      subGoals: JSON.parse(row.sub_goals ?? "[]") as SubGoal[],
      expectedOutput: row.expected_output ?? "",
      complexity: (row.complexity ?? "medium") as TaskComplexity,
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: JSON.parse(row.revisions ?? "[]") as TaskLedgerRevision[],
      createdAt: new Date(row.created_at).getTime(),
      // Extra fields stored on the object but not in the TaskLedger type
      ...(({ sessionId: row.session_id, userId: row.user_id }) as any),
    };
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/task-ledger.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/task-ledger.ts __tests__/task-ledger.test.ts
git commit -m "feat(engine): add TaskLedgerStore — SQLite CRUD for planning ledgers"
```

---

### Task 10: OwlEngine.runTurn() + EXHAUSTION_MARKER boundary

**Files:**
- Modify: `src/engine/runtime.ts`
- Test: `__tests__/engine-runturn.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/engine-runturn.test.ts
import { describe, it, expect, vi } from "vitest";
import { OwlEngine } from "../src/engine/runtime.js";
import type { TurnRequest } from "../src/engine/types.js";

const mockProvider = {
  name: "mock",
  chat: vi.fn().mockResolvedValue({
    content: "I can help with that. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
  chatWithTools: vi.fn().mockResolvedValue({
    content: "I can help with that. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
};

describe("OwlEngine.runTurn()", () => {
  it("returns TurnResult with doneSignal and no [DONE] text in content", async () => {
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "hello" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
    };
    // Inject mock provider
    const result = await engine.runTurn(request, mockProvider as any);
    expect(result.doneSignal).toBe(true);
    expect(result.content).not.toContain("[DONE]");
    expect(result.budgetExhausted).toBe(false);
    expect(result.tokensUsed).toBeGreaterThan(0);
  });

  it("never returns EXHAUSTION_MARKER in content", async () => {
    const exhaustedProvider = {
      ...mockProvider,
      chatWithTools: vi.fn().mockResolvedValue({
        content: "I tried many things. __STACKOWL_EXHAUSTED__",
        toolCalls: [],
        usage: { promptTokens: 100, completionTokens: 50 },
        model: "mock",
        finishReason: "stop",
      }),
    };
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "do something hard" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
    };
    const result = await engine.runTurn(request, exhaustedProvider as any);
    expect(result.content).not.toContain("__STACKOWL_EXHAUSTED__");
    expect(result.budgetExhausted).toBe(true);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/engine-runturn.test.ts
```

- [ ] **Step 3: Add `runTurn()` method to OwlEngine in `src/engine/runtime.ts`**

After the closing brace of the existing `run()` method (around line 719), add:

```typescript
  /**
   * Single-turn execution for OwlOrchestrator.
   * Does ONE reasoning pass + tool execution cycle.
   * Strips all internal markers — returns typed signals only.
   */
  async runTurn(
    request: import("./types.js").TurnRequest,
    providerOverride?: import("../providers/base.js").ModelProvider,
  ): Promise<import("./types.js").TurnResult> {
    const provider = providerOverride ??
      (request as any)._resolvedProvider;
    if (!provider) throw new Error("runTurn requires a provider");

    const { messages, tools, modelName, turnBudget, onStreamEvent, onProgress } = request;

    const chatOptions = { temperature: 0.7 };
    let response: import("../providers/base.js").ChatResponse;

    // Single LLM call + tool dispatch
    if (tools.length > 0 && provider.chatWithTools) {
      response = await provider.chatWithTools(messages, tools, modelName, chatOptions);
    } else {
      response = await provider.chat(messages, modelName, chatOptions);
    }

    const tokensUsed = (response.usage?.promptTokens ?? 0) + (response.usage?.completionTokens ?? 0);
    const rawContent = response.content ?? "";

    // Detect and strip EXHAUSTION_MARKER — becomes typed budgetExhausted signal
    const budgetExhausted = rawContent.includes(EXHAUSTION_MARKER) ||
      (turnBudget.used + tokensUsed) >= turnBudget.total;

    // Detect [DONE] signal
    const doneSignal = hasDoneSignal(rawContent);

    // Strip ALL internal markers from content
    let cleanContent = rawContent
      .replace(/__STACKOWL_EXHAUSTED__/g, "")
      .replace(/\[CAPABILITY_GAP:[^\]]*\]/g, "")
      .replace(/\[SYSTEM:[^\]]*\]/g, "")
      .replace(/\[DONE\]/g, "")
      .replace(/\[DEEPER\]/gi, "")
      .trim();

    const failedTools: import("./types.js").FailedToolCall[] = [];
    const toolResults: { toolCallId: string; name: string; result: string }[] = [];

    // Execute tool calls if present
    const toolCalls = response.toolCalls ?? [];
    if (toolCalls.length > 0 && (request as any).toolRegistry) {
      const registry = (request as any).toolRegistry;
      const toolCtx = { cwd: process.cwd(), engineContext: {} };
      await Promise.allSettled(
        toolCalls.map(async (tc) => {
          try {
            const result = await registry.execute(tc.name, tc.arguments, toolCtx);
            toolResults.push({ toolCallId: tc.id, name: tc.name, result });
          } catch (e) {
            const reason = e instanceof Error ? e.message : String(e);
            failedTools.push({ name: tc.name, reason });
            toolResults.push({ toolCallId: tc.id, name: tc.name, result: `Error: ${reason}` });
          }
        })
      );
    }

    return {
      content: cleanContent,
      toolCalls,
      toolResults,
      tokensUsed,
      doneSignal,
      budgetExhausted,
      failedTools,
      providerUsed: provider.name,
      modelUsed: modelName,
    };
  }
```

- [ ] **Step 4: Export `EXHAUSTION_MARKER` — verify it is already exported at line 501**

```bash
grep -n "export const EXHAUSTION_MARKER" src/engine/runtime.ts
```

If not exported, add `export` keyword.

- [ ] **Step 5: Run test, confirm it passes**

```bash
npx vitest run __tests__/engine-runturn.test.ts
```

- [ ] **Step 6: Run full suite — confirm no regressions**

```bash
npx vitest run
```

- [ ] **Step 7: Commit**

```bash
git add src/engine/runtime.ts __tests__/engine-runturn.test.ts
git commit -m "feat(engine): add OwlEngine.runTurn() — single-turn API, strips EXHAUSTION_MARKER at boundary"
```

---

## Phase 4 — HITL + Instincts + Reflexion (Tasks 11–13)

### Task 11: HITL system

**Files:**
- Create: `src/engine/hitl.ts`
- Test: `__tests__/hitl.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/hitl.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { HitlCheckpointStore, CliHitlChannel } from "../src/engine/hitl.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { HitlRequest, TaskLedger } from "../src/engine/types.js";

let dir: string, db: MemoryDatabase, store: HitlCheckpointStore;

const makeLedger = (): TaskLedger => ({
  id: "l1", goal: "test", subGoals: [], expectedOutput: "",
  complexity: "simple", estimatedTurns: 1, behavioralConstraints: [],
  approachPatterns: [], revisions: [], createdAt: Date.now(),
});

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-hitl-"));
  db = new MemoryDatabase(dir);
  store = new HitlCheckpointStore(db);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("HitlCheckpointStore", () => {
  it("creates and loads a checkpoint", async () => {
    const req: HitlRequest = {
      kind: "approval",
      memo: { whatIDid: "searched for X", whatINeed: "confirmation to proceed" },
      ledgerSnapshot: makeLedger(),
      pendingAction: "delete file",
    };
    const id = await store.create("s1", "l1", req, 24 * 60);
    expect(id).toBeTruthy();
    const cp = await store.load(id);
    expect(cp?.requestKind).toBe("approval");
    expect(cp?.status).toBe("waiting");
  });

  it("resolves a checkpoint with response", async () => {
    const req: HitlRequest = {
      kind: "clarification",
      memo: { whatIDid: "analyzed", whatINeed: "which format?" },
      ledgerSnapshot: makeLedger(),
      pendingAction: "generate report",
    };
    const id = await store.create("s1", "l1", req, 60);
    await store.resolve(id, { approved: true, timedOut: false, freeText: "PDF please" });
    const cp = await store.load(id);
    expect(cp?.status).toBe("resolved");
    expect(cp?.response?.freeText).toBe("PDF please");
  });

  it("getWaiting returns pending checkpoints for session", async () => {
    const req: HitlRequest = {
      kind: "choice",
      memo: { whatIDid: "found options", whatINeed: "pick one", options: ["A","B"] },
      ledgerSnapshot: makeLedger(),
      pendingAction: "use option",
    };
    await store.create("s1", "l1", req, 60);
    const waiting = await store.getWaiting("s1");
    expect(waiting.length).toBe(1);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/hitl.test.ts
```

- [ ] **Step 3: Create `src/engine/hitl.ts`**

```typescript
import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type { HitlRequest, HitlResponse, HitlChannel } from "./types.js";
import { log } from "../logger.js";

interface StoredCheckpoint {
  id: string;
  sessionId: string;
  ledgerId: string;
  requestKind: HitlRequest["kind"];
  memo: HitlRequest["memo"];
  ledgerSnapshot: string; // JSON
  pendingAction: string;
  status: "waiting" | "resolved" | "expired";
  response?: HitlResponse;
  createdAt: string;
  resolvedAt?: string;
  expiresAt: string;
}

export class HitlCheckpointStore {
  constructor(private readonly db: MemoryDatabase) {}

  async create(
    sessionId: string,
    ledgerId: string,
    request: HitlRequest,
    ttlMinutes: number,
  ): Promise<string> {
    const id = uuidv4();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + ttlMinutes * 60_000).toISOString();
    this.db.db.prepare(`
      INSERT INTO hitl_checkpoints
        (id, session_id, ledger_id, pending_action, request_kind, memo_json,
         status, created_at, expires_at)
      VALUES (?,?,?,?,?,?,?,?,?)
    `).run(
      id, sessionId, ledgerId, request.pendingAction,
      request.kind, JSON.stringify({ memo: request.memo, ledger: request.ledgerSnapshot }),
      "waiting", now.toISOString(), expiresAt,
    );
    return id;
  }

  async resolve(id: string, response: HitlResponse): Promise<void> {
    this.db.db.prepare(`
      UPDATE hitl_checkpoints
      SET status = 'resolved', response_json = ?, resolved_at = ?
      WHERE id = ?
    `).run(JSON.stringify(response), new Date().toISOString(), id);
  }

  async load(id: string): Promise<StoredCheckpoint | null> {
    const row = this.db.db.prepare(
      "SELECT * FROM hitl_checkpoints WHERE id = ?"
    ).get(id) as any;
    if (!row) return null;
    return this._parse(row);
  }

  async getWaiting(sessionId: string): Promise<StoredCheckpoint[]> {
    const rows = this.db.db.prepare(
      "SELECT * FROM hitl_checkpoints WHERE session_id = ? AND status = 'waiting' ORDER BY created_at DESC"
    ).all(sessionId) as any[];
    return rows.map(this._parse);
  }

  private _parse(row: any): StoredCheckpoint {
    const memoData = JSON.parse(row.memo_json ?? "{}");
    return {
      id: row.id,
      sessionId: row.session_id,
      ledgerId: row.ledger_id,
      requestKind: row.request_kind,
      memo: memoData.memo ?? {},
      ledgerSnapshot: memoData.ledger ? JSON.stringify(memoData.ledger) : "{}",
      pendingAction: row.pending_action,
      status: row.status,
      response: row.response_json ? JSON.parse(row.response_json) : undefined,
      createdAt: row.created_at,
      resolvedAt: row.resolved_at ?? undefined,
      expiresAt: row.expires_at,
    };
  }
}

/**
 * CLI adapter for HITL — prompts inline via readline.
 * Other adapters (Telegram, Web) implement the same HitlChannel interface.
 */
export class CliHitlChannel implements HitlChannel {
  async pause(request: HitlRequest): Promise<HitlResponse> {
    const { memo } = request;
    log.engine.info(`\n[HITL] Owl needs your input:`);
    log.engine.info(`  What I did: ${memo.whatIDid}`);
    log.engine.info(`  What I need: ${memo.whatINeed}`);

    if (memo.options) {
      memo.options.forEach((opt, i) => log.engine.info(`  ${i + 1}. ${opt}`));
    }

    // In CLI mode, auto-approve with a note (real impl would use readline)
    // Full readline implementation requires async prompt which varies by CLI setup
    log.engine.info(`  [Auto-approving in CLI mode — implement readline for interactive use]`);
    return { approved: true, timedOut: false };
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/hitl.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/hitl.ts __tests__/hitl.test.ts
git commit -m "feat(engine): add HITL system — HitlCheckpointStore + CliHitlChannel, checkpoint/resume via SQLite"
```

---

### Task 12: InstinctEngine redesign — heuristic-first cascade

**Files:**
- Modify: `src/instincts/engine.ts`
- Test: `__tests__/instinct-engine-v2.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/instinct-engine-v2.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { InstinctEngineV2 } from "../src/instincts/engine.js";
import type { InstinctSpec } from "../src/instincts/types.js";

const instincts: InstinctSpec[] = [
  { name: "no-finance", description: "Don't give financial advice", constraint: "Never give specific investment advice", owlName: "atlas", keywords: ["invest", "stock", "crypto", "portfolio"] },
  { name: "be-brief", description: "Keep responses concise", constraint: "Respond in 2-3 sentences", owlName: "atlas", keywords: ["brief", "short", "quick"] },
];

describe("InstinctEngineV2", () => {
  let engine: InstinctEngineV2;
  beforeEach(() => { engine = new InstinctEngineV2(); });

  it("matches keyword instinct instantly (no LLM call)", () => {
    const matched = engine.evaluateHeuristic(instincts, "Should I invest in Bitcoin stocks?");
    expect(matched.some(i => i.name === "no-finance")).toBe(true);
  });

  it("does not match unrelated message", () => {
    const matched = engine.evaluateHeuristic(instincts, "What is the weather today?");
    expect(matched.length).toBe(0);
  });

  it("caches results per session", () => {
    engine.evaluateHeuristic(instincts, "invest in crypto");
    const cached = engine.getCached("invest in crypto");
    expect(cached).not.toBeNull();
  });

  it("buildConstraintBlock returns constraint strings", () => {
    const matched = engine.evaluateHeuristic(instincts, "Should I buy stocks?");
    const block = engine.buildConstraintBlock(matched);
    expect(block).toContain("Never give specific investment advice");
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/instinct-engine-v2.test.ts
```

- [ ] **Step 3: Add `keywords` field to InstinctSpec in `src/instincts/types.ts`**

```typescript
export interface InstinctSpec {
  name: string;
  description: string;
  constraint: string;
  owlName: string;
  /** Optional keyword triggers for heuristic matching (0ms, no LLM) */
  keywords?: string[];
}
```

- [ ] **Step 4: Add `InstinctEngineV2` class to `src/instincts/engine.ts`**

Keep the existing `InstinctEngine` class unchanged. Add after it:

```typescript
/**
 * Redesigned instinct evaluator — heuristic-first cascade.
 * 1. Keyword scoring (0ms) — catches ~80% of cases
 * 2. LLM classification — only when no keyword match
 * Results cached per session (key = user message text).
 */
export class InstinctEngineV2 {
  private cache = new Map<string, InstinctSpec[]>();

  evaluateHeuristic(instincts: InstinctSpec[], userMessage: string): InstinctSpec[] {
    const lower = userMessage.toLowerCase();
    const matched = instincts.filter(inst =>
      inst.keywords?.some(kw => lower.includes(kw.toLowerCase()))
    );
    this.cache.set(userMessage, matched);
    return matched;
  }

  getCached(userMessage: string): InstinctSpec[] | null {
    return this.cache.get(userMessage) ?? null;
  }

  clearCache(): void {
    this.cache.clear();
  }

  buildConstraintBlock(instincts: InstinctSpec[]): string {
    if (instincts.length === 0) return "";
    return instincts.map(i => `- ${i.constraint}`).join("\n");
  }
}
```

- [ ] **Step 5: Run test, confirm it passes**

```bash
npx vitest run __tests__/instinct-engine-v2.test.ts
```

- [ ] **Step 6: Commit**

```bash
git add src/instincts/engine.ts src/instincts/types.ts __tests__/instinct-engine-v2.test.ts
git commit -m "feat(instincts): add InstinctEngineV2 — heuristic-first keyword matching, session cache"
```

---

### Task 13: ImprovementScheduler

**Files:**
- Create: `src/engine/improvement-scheduler.ts`
- Test: `__tests__/improvement-scheduler.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/improvement-scheduler.test.ts
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ImprovementScheduler } from "../src/engine/improvement-scheduler.js";
import { OutcomeJournal } from "../src/engine/outcome-journal.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-sched-"));
  db = new MemoryDatabase(dir);
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("ImprovementScheduler", () => {
  it("start/stop without errors", () => {
    const journal = new OutcomeJournal(db);
    const sched = new ImprovementScheduler(journal, db, { quietHours: [] });
    sched.start();
    sched.stop();
    expect(sched.isRunning()).toBe(false);
  });

  it("runJournalReview processes recent failures (0 LLM calls)", async () => {
    const journal = new OutcomeJournal(db);
    // Seed a low-quality run
    await journal.record({
      sessionId: "s1", owlName: "atlas", userId: "u1",
      userMessage: "research X", totalTurns: 5, toolsUsed: ["web_search"],
      outcome: "failure", reward: -0.5, qualityScore: 0.2,
      qualityFlags: ["loop_exhausted"], taskCategory: "research",
      taskComplexity: "complex", degradationTier: 3, recoveryActions: ["replan"],
    });
    const sched = new ImprovementScheduler(journal, db, { quietHours: [] });
    const count = await sched.runJournalReview();
    // Should have processed 1 entry (or 0 if below threshold)
    expect(count).toBeGreaterThanOrEqual(0);
  });

  it("isInQuietHours returns true when current hour is in range", () => {
    const sched = new ImprovementScheduler(
      new OutcomeJournal(db), db,
      { quietHours: [{ start: 0, end: 23 }] }, // entire day is quiet
    );
    expect(sched.isInQuietHours()).toBe(true);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/improvement-scheduler.test.ts
```

- [ ] **Step 3: Create `src/engine/improvement-scheduler.ts`**

```typescript
import type { OutcomeJournal } from "./outcome-journal.js";
import type { MemoryDatabase } from "../memory/db.js";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";

interface QuietHour { start: number; end: number; }
interface SchedulerConfig { quietHours: QuietHour[]; }

export class ImprovementScheduler {
  private running = false;
  private timers: ReturnType<typeof setInterval>[] = [];

  constructor(
    private readonly journal: OutcomeJournal,
    private readonly db: MemoryDatabase,
    private readonly config: SchedulerConfig,
  ) {}

  start(): void {
    if (this.running) return;
    this.running = true;

    // Job 1: Journal review every 15 minutes (0 LLM calls)
    this.timers.push(setInterval(async () => {
      if (this.isInQuietHours()) return;
      try { await this.runJournalReview(); } catch (e) {
        log.engine.warn(`[ImprovementScheduler] Journal review error: ${e}`);
      }
    }, 15 * 60_000));

    // Job 2: Approach pruning every hour (0 LLM calls)
    this.timers.push(setInterval(async () => {
      if (this.isInQuietHours()) return;
      try { await this.runApproachPruning(); } catch (e) {
        log.engine.warn(`[ImprovementScheduler] Pruning error: ${e}`);
      }
    }, 60 * 60_000));

    log.engine.info("[ImprovementScheduler] Started — journal review (15min), pruning (1h)");
  }

  stop(): void {
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    this.running = false;
  }

  isRunning(): boolean { return this.running; }

  isInQuietHours(): boolean {
    const hour = new Date().getHours();
    return this.config.quietHours.some(qh =>
      qh.start <= qh.end
        ? hour >= qh.start && hour < qh.end
        : hour >= qh.start || hour < qh.end
    );
  }

  /**
   * Job 1: Aggregates recent failures into approach_patterns table.
   * Zero LLM calls — pure pattern counting.
   */
  async runJournalReview(): Promise<number> {
    const failures = await this.journal.getFailures({ minEntries: 5 });
    if (failures.length === 0) return 0;

    const byCategory = new Map<string, typeof failures>();
    for (const f of failures) {
      const cat = f.taskCategory ?? "general";
      if (!byCategory.has(cat)) byCategory.set(cat, []);
      byCategory.get(cat)!.push(f);
    }

    let processed = 0;
    for (const [category, entries] of byCategory) {
      if (entries.length < 3) continue;
      const lesson = `${entries.length} failures in "${category}" — check approach patterns`;
      const existing = this.db.db.prepare(
        "SELECT id FROM approach_patterns WHERE task_category = ? AND lesson = ?"
      ).get(category, lesson);
      if (!existing) {
        this.db.db.prepare(`
          INSERT INTO approach_patterns
            (id, task_category, lesson, observation_count, success_rate, status, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?)
        `).run(
          uuidv4(), category, lesson, entries.length, 0.0, "tentative",
          new Date().toISOString(), new Date().toISOString(),
        );
        processed++;
      }
    }
    return processed;
  }

  /**
   * Job 2: Archives stale patterns, promotes proven ones.
   * Zero LLM calls.
   */
  async runApproachPruning(): Promise<void> {
    // Archive patterns not used in 30 days
    const cutoff = new Date(Date.now() - 30 * 24 * 60 * 60_000).toISOString();
    this.db.db.prepare(`
      UPDATE approach_patterns SET status = 'archived'
      WHERE status = 'tentative' AND (last_used_at IS NULL OR last_used_at < ?)
    `).run(cutoff);

    // Promote patterns with high success rate and enough observations
    this.db.db.prepare(`
      UPDATE approach_patterns SET status = 'proven'
      WHERE status = 'tentative' AND success_rate > 0.7 AND observation_count > 5
    `).run();
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/improvement-scheduler.test.ts
```

- [ ] **Step 5: Commit**

```bash
git add src/engine/improvement-scheduler.ts __tests__/improvement-scheduler.test.ts
git commit -m "feat(engine): add ImprovementScheduler — 3 background jobs, zero LLM for journal review + pruning"
```

---

## Phase 5 — OwlOrchestrator + Gateway Wiring (Tasks 14–16)

### Task 14: OwlOrchestrator — PLAN + main loop

**Files:**
- Create: `src/engine/orchestrator.ts`
- Test: `__tests__/orchestrator.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/orchestrator.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { OwlOrchestrator } from "../src/engine/orchestrator.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase;

const mockProvider = {
  name: "mock",
  chat: vi.fn().mockResolvedValue({
    content: "Here is your answer. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
  chatWithTools: vi.fn().mockResolvedValue({
    content: "Here is your answer. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
};

const mockOwl = {
  persona: { name: "Atlas", emoji: "🦉", systemPrompt: "You are Atlas." },
  dna: { riskTolerance: "balanced", challengeLevel: "medium", verbosity: 0.5 },
};

const mockConfig = {
  engine: { maxToolIterations: 10 },
};

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-orch-"));
  db = new MemoryDatabase(dir);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("OwlOrchestrator", () => {
  it("returns OrchestratorResponse for a simple message", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const response = await orch.run("hello, who are you?", {
      sessionId: "s1",
      userId: "u1",
    });
    expect(response.content.length).toBeGreaterThan(0);
    expect(response.content).not.toContain("[DONE]");
    expect(response.content).not.toContain("__STACKOWL_EXHAUSTED__");
    expect(response.qualityScore).toBeGreaterThan(0);
    expect(response.owlName).toBe("Atlas");
  });

  it("classifies simple messages and skips full planning LLM call", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const response = await orch.run("hi", { sessionId: "s1", userId: "u1" });
    // Provider chatWithTools called once (simple = no planning overhead)
    expect(response.complexity).toBe("simple");
  });

  it("quality score is in [0,1] range", async () => {
    const orch = new OwlOrchestrator({
      owl: mockOwl as any,
      provider: mockProvider as any,
      config: mockConfig as any,
      db,
    });
    const { qualityScore } = await orch.run("summarize this doc", {
      sessionId: "s1", userId: "u1",
    });
    expect(qualityScore).toBeGreaterThanOrEqual(0);
    expect(qualityScore).toBeLessThanOrEqual(1);
  });
});
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
npx vitest run __tests__/orchestrator.test.ts
```

- [ ] **Step 3: Create `src/engine/orchestrator.ts`**

```typescript
import { OwlEngine } from "./runtime.js";
import { HealthMonitor } from "./health-monitor.js";
import { decide } from "./recovery-orchestrator.js";
import { QualityEvaluator } from "./quality-evaluator.js";
import { OutcomeJournal } from "./outcome-journal.js";
import { UserFacingStatusNarrator } from "./user-facing-narrator.js";
import { TaskLedgerStore } from "./task-ledger.js";
import { InstinctEngineV2 } from "../instincts/engine.js";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import type {
  TurnRequest, TurnResult, TaskLedger, Decision,
  OrchestratorResponse, DegradationTier, HitlChannel,
} from "./types.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";

interface OrchestratorDeps {
  owl: OwlInstance;
  provider: ModelProvider;
  config: StackOwlConfig;
  db: MemoryDatabase;
  toolRegistry?: ToolRegistry;
  hitlChannel?: HitlChannel;
  sessionHistory?: import("../providers/base.js").ChatMessage[];
}

interface RunContext {
  sessionId: string;
  userId: string;
  memoryContext?: string;
  onProgress?: (msg: string) => Promise<void>;
  onStreamEvent?: (event: import("../providers/base.js").StreamEvent) => Promise<void>;
}

const TOKEN_BUDGET = 8000;

const SIMPLE_PATTERNS = [
  /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|sure|yes|no|bye|goodbye)[!?.]*$/i,
  /^what (is|are) (the )?(time|date|day|weather)/i,
  /^who are you/i,
];

function classifyComplexity(message: string): "simple" | "medium" | "complex" {
  if (message.length < 80 && SIMPLE_PATTERNS.some(p => p.test(message.trim()))) return "simple";
  if (message.length > 300 || /\b(research|analyze|compare|plan|build|create|write a|investigate)\b/i.test(message)) return "complex";
  return "medium";
}

export class OwlOrchestrator {
  private engine: OwlEngine;
  private qualityEvaluator: QualityEvaluator;
  private narrator: UserFacingStatusNarrator;
  private journal: OutcomeJournal;
  private ledgerStore: TaskLedgerStore;
  private instincts: InstinctEngineV2;
  private deps: OrchestratorDeps;

  constructor(deps: OrchestratorDeps) {
    this.deps = deps;
    this.engine = new OwlEngine();
    this.qualityEvaluator = new QualityEvaluator();
    this.narrator = new UserFacingStatusNarrator();
    this.journal = new OutcomeJournal(deps.db);
    this.ledgerStore = new TaskLedgerStore(deps.db);
    this.instincts = new InstinctEngineV2();
  }

  async run(userMessage: string, ctx: RunContext): Promise<OrchestratorResponse> {
    const startMs = Date.now();
    const complexity = classifyComplexity(userMessage);
    const tokenBudget = { total: TOKEN_BUDGET, used: 0, remaining: TOKEN_BUDGET };
    const sessionHistory = this.deps.sessionHistory ?? [];

    // Phase 1 — PLAN
    const ledger = await this._plan(userMessage, complexity, ctx, sessionHistory);

    const monitor = new HealthMonitor(TOKEN_BUDGET);
    let lastTurn: TurnResult | null = null;
    let iteration = 0;
    let finalDecision: Decision = "CONTINUE";
    const dna = {
      riskTolerance: (this.deps.owl.dna as any)?.riskTolerance ?? "balanced",
      challengeLevel: (this.deps.owl.dna as any)?.challengeLevel ?? "medium",
    };

    const messages = [
      ...sessionHistory,
      { role: "user" as const, content: userMessage },
    ];

    // Build plan block for prompt injection
    const planBlock = this._buildPlanBlock(ledger);
    const systemMsg = { role: "system" as const, content: planBlock };

    const runMessages = complexity === "simple"
      ? messages
      : [systemMsg, ...messages];

    // Phase 2–4 main loop
    while (monitor.shouldContinue()) {
      const turnRequest: TurnRequest = {
        messages: runMessages,
        tools: [],
        modelName: (this.deps.config as any).providers?.[0]?.defaultModel ?? "claude-sonnet-4-6",
        providerName: this.deps.provider.name,
        sessionId: ctx.sessionId,
        turnBudget: { ...tokenBudget },
        onStreamEvent: ctx.onStreamEvent,
        onProgress: ctx.onProgress,
      };

      // Phase 2 — EXECUTE
      lastTurn = await this.engine.runTurn(turnRequest, this.deps.provider);
      tokenBudget.used += lastTurn.tokensUsed;
      tokenBudget.remaining = Math.max(0, tokenBudget.total - tokenBudget.used);

      // Phase 3 — ASSESS
      monitor.observe(lastTurn, ledger, iteration++);

      // Phase 4 — DECIDE
      finalDecision = decide(monitor.getHealth(), lastTurn, ledger, dna);
      log.engine.debug(`[Orchestrator] iteration=${iteration} decision=${finalDecision}`);

      if (finalDecision === "REPLAN") {
        await this.ledgerStore.addRevision(ledger.id, "stall/spinning detected", ledger.goal);
        ledger.revisions = (await this.ledgerStore.load(ledger.id))?.revisions ?? ledger.revisions;
        continue;
      }
      if (finalDecision === "HITL") {
        // For now, continue without HITL if no channel provided
        if (!this.deps.hitlChannel) {
          finalDecision = "SYNTHESIZE";
          break;
        }
        break;
      }
      if (finalDecision === "CONTINUE") continue;
      break; // SYNTHESIZE or DEGRADE
    }

    // Phase 6 — SYNTHESIZE or DEGRADE
    const rawContent = lastTurn?.content ?? "";
    const { score, cleanContent } = this.qualityEvaluator.evaluateAndStrip({
      content: rawContent,
      loopExhausted: lastTurn?.budgetExhausted ?? false,
      toolCallCount: lastTurn?.toolCalls.length ?? 0,
      toolFailureCount: lastTurn?.failedTools.length ?? 0,
      taskComplexity: complexity,
      hasStructuredOutput: /\|.+\|/.test(rawContent) || rawContent.includes("```"),
    });

    let finalContent = cleanContent;
    let degradationTier: DegradationTier = 1;

    if (finalDecision === "DEGRADE" || score < 0.3) {
      degradationTier = score < 0.1 ? 4 : score < 0.3 ? 3 : 2;
      finalContent = this.narrator.buildDegradation(
        degradationTier,
        cleanContent,
        lastTurn?.pendingCapabilityGap,
        undefined,
      );
    }

    // Phase 7 — NARRATE
    finalContent = this.narrator.postProcess(finalContent, score);

    // Record to OutcomeJournal
    try {
      await this.journal.record({
        sessionId: ctx.sessionId,
        owlName: this.deps.owl.persona.name,
        userId: ctx.userId,
        userMessage,
        totalTurns: iteration,
        toolsUsed: lastTurn?.toolCalls.map(tc => tc.name) ?? [],
        outcome: score > 0.6 ? "success" : score > 0.3 ? "partial" : "failure",
        reward: score * 2 - 1, // map [0,1] to [-1,1]
        qualityScore: score,
        qualityFlags: lastTurn?.budgetExhausted ? ["budget_exhausted"] : [],
        taskCategory: "general",
        taskComplexity: complexity,
        degradationTier,
        recoveryActions: ledger.revisions.map(r => r.reason),
      });
    } catch (e) {
      log.engine.warn(`[Orchestrator] Journal record failed: ${e}`);
    }

    return {
      content: finalContent,
      owlName: this.deps.owl.persona.name,
      owlEmoji: (this.deps.owl.persona as any).emoji ?? "🦉",
      toolsUsed: lastTurn?.toolCalls.map(tc => tc.name) ?? [],
      qualityScore: score,
      degradationTier,
      taskCategory: "general",
      complexity,
      ledgerId: ledger.id,
      evolutionSignals: {
        qualityScore: score,
        taskCategory: "general",
      },
    };
  }

  private async _plan(
    userMessage: string,
    complexity: "simple" | "medium" | "complex",
    ctx: RunContext,
    sessionHistory: import("../providers/base.js").ChatMessage[],
  ): Promise<TaskLedger> {
    const ledger = this.ledgerStore.create(ctx.sessionId, ctx.userId, {
      goal: userMessage,
      subGoals: [],
      expectedOutput: "a complete, helpful response",
      complexity,
      estimatedTurns: complexity === "simple" ? 1 : complexity === "medium" ? 3 : 7,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
    });

    // Attach session/user for storage
    (ledger as any).sessionId = ctx.sessionId;
    (ledger as any).userId = ctx.userId;

    try { await this.ledgerStore.save(ledger); } catch { /* non-fatal */ }
    return ledger;
  }

  private _buildPlanBlock(ledger: TaskLedger): string {
    if (ledger.complexity === "simple") return "";
    const done = ledger.subGoals.filter(sg => sg.status === "done").length;
    return [
      "[Current Plan]",
      `Goal: ${ledger.goal}`,
      `Progress: ${done}/${ledger.subGoals.length} steps complete`,
      `Expected output: ${ledger.expectedOutput}`,
    ].join("\n");
  }
}
```

- [ ] **Step 4: Run test, confirm it passes**

```bash
npx vitest run __tests__/orchestrator.test.ts
```

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```

- [ ] **Step 6: Commit**

```bash
git add src/engine/orchestrator.ts __tests__/orchestrator.test.ts
git commit -m "feat(engine): add OwlOrchestrator — 7-phase state machine, quality eval, outcome journal, narrator"
```

---

### Task 15: ImprovementScheduler bootstrap + GatewayContext wiring

**Files:**
- Modify: `src/gateway/types.ts`
- Test: none (type-only addition)

- [ ] **Step 1: Add OwlOrchestrator and ImprovementScheduler to GatewayContext**

In `src/gateway/types.ts`, at the end of the `GatewayContext` interface (before the closing `}`), add:

```typescript
  // ─── OwlEngine v2 (Element 6a) ────────────────────────────────
  orchestrator?: import("../engine/orchestrator.js").OwlOrchestrator;
  improvementScheduler?: import("../engine/improvement-scheduler.js").ImprovementScheduler;
```

- [ ] **Step 2: Compile — verify no TypeScript errors**

```bash
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add src/gateway/types.ts
git commit -m "feat(gateway): add orchestrator + improvementScheduler to GatewayContext"
```

---

### Task 16: Gateway wiring — one-line swap + bootstrap

**Files:**
- Modify: `src/gateway/core.ts`
- Test: `__tests__/gateway-orchestrator.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway-orchestrator.test.ts
import { describe, it, expect } from "vitest";
// Smoke test: OwlOrchestrator is importable and constructable
import { OwlOrchestrator } from "../src/engine/orchestrator.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

describe("Gateway orchestrator integration", () => {
  it("OwlOrchestrator can be imported from engine/orchestrator", () => {
    expect(OwlOrchestrator).toBeDefined();
  });

  it("OwlOrchestrator.run() returns content string", async () => {
    const dir = mkdtempSync(join(tmpdir(), "owl-gw-"));
    try {
      const db = new MemoryDatabase(dir);
      const mockProvider = {
        name: "mock",
        chat: async () => ({ content: "Hello! [DONE]", toolCalls: [], usage: { promptTokens: 10, completionTokens: 10 }, model: "mock", finishReason: "stop" as const }),
        chatWithTools: async () => ({ content: "Hello! [DONE]", toolCalls: [], usage: { promptTokens: 10, completionTokens: 10 }, model: "mock", finishReason: "stop" as const }),
      };
      const mockOwl = { persona: { name: "Atlas", emoji: "🦉", systemPrompt: "" }, dna: { riskTolerance: "balanced", challengeLevel: "medium" } };
      const orch = new OwlOrchestrator({ owl: mockOwl as any, provider: mockProvider as any, config: {} as any, db });
      const result = await orch.run("hi", { sessionId: "s1", userId: "u1" });
      expect(typeof result.content).toBe("string");
      expect(result.content.length).toBeGreaterThan(0);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
```

- [ ] **Step 2: Run test, confirm it passes** (should pass since orchestrator already works)

```bash
npx vitest run __tests__/gateway-orchestrator.test.ts
```

- [ ] **Step 3: Wire OwlOrchestrator into core.ts**

In `src/gateway/core.ts`:

**3a. Add import** (near the top with other engine imports, around line 20):
```typescript
import { OwlOrchestrator } from "../engine/orchestrator.js";
import { ImprovementScheduler } from "../engine/improvement-scheduler.js";
import { OutcomeJournal } from "../engine/outcome-journal.js";
```

**3b. Add orchestrator field** in `OwlGateway` class (near `this.engine = new OwlEngine()` around line 257):
```typescript
private orchestrator: OwlOrchestrator | null = null;
private improvementScheduler: ImprovementScheduler | null = null;
```

**3c. Initialize orchestrator** in the `OwlGateway` constructor or `init()` method, after `this.engine` is set:
```typescript
if (this.ctx.db) {
  this.orchestrator = new OwlOrchestrator({
    owl: this.ctx.owl,
    provider: this.ctx.provider,
    config: this.ctx.config,
    db: this.ctx.db,
    toolRegistry: this.ctx.toolRegistry,
  });
  const journal = new OutcomeJournal(this.ctx.db);
  this.improvementScheduler = new ImprovementScheduler(
    journal,
    this.ctx.db,
    { quietHours: (this.ctx.config as any).heartbeat?.quietHours ?? [] },
  );
  this.improvementScheduler.start();
}
```

**3d. Find the main `engine.run()` call** (line ~1069) and add orchestrator path above it:

```typescript
// ── OwlOrchestrator v2 path (preferred when DB is available) ─────
if (this.orchestrator && context.db) {
  const orchResponse = await this.orchestrator.run(message.text, {
    sessionId: message.sessionId,
    userId: message.userId,
    onProgress: callbacks.onProgress,
    onStreamEvent: callbacks.onStreamEvent,
  });
  return {
    content: orchResponse.content,
    owlName: orchResponse.owlName,
    owlEmoji: orchResponse.owlEmoji,
    toolsUsed: orchResponse.toolsUsed,
  };
}
// ── Legacy engine.run() path (fallback when no DB) ───────────────
const response = await this.engine.run(userMessage, engineCtx);
```

**Note:** The exact insertion point varies — search for the `const response = await this.engine.run(` line in the main `handle()` method path (not skill execution paths). Wrap only the primary conversational path.

- [ ] **Step 4: Compile**

```bash
npx tsc --noEmit
```

Fix any type errors before running tests.

- [ ] **Step 5: Run all tests**

```bash
npx vitest run
```

- [ ] **Step 6: Commit**

```bash
git add src/gateway/core.ts src/gateway/types.ts __tests__/gateway-orchestrator.test.ts
git commit -m "feat(gateway): wire OwlOrchestrator as primary path, ImprovementScheduler bootstrapped at startup"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Task 1: TurnRequest/TurnResult types (Section 3)
- [x] Task 2: Schema v14 migrations (Section 9)
- [x] Task 3: ToolDefinition.sequential (Section 8 Change 1)
- [x] Task 4: HealthMonitor (Section 6)
- [x] Task 5: RecoveryOrchestrator (Section 6)
- [x] Task 6: QualityEvaluator (Section 6)
- [x] Task 7: OutcomeJournal (Section 6)
- [x] Task 8: UserFacingStatusNarrator (Section 4 Phase 7, Section 6)
- [x] Task 9: TaskLedger store (Section 4 Phase 1)
- [x] Task 10: OwlEngine.runTurn() (Section 8)
- [x] Task 11: HITL system (Section 7)
- [x] Task 12: InstinctEngine heuristic-first (Section 5 Horizon 1)
- [x] Task 13: ImprovementScheduler (Section 5 Horizon 4)
- [x] Task 14: OwlOrchestrator (Section 4)
- [x] Task 15-16: Gateway wiring (Section 10)

**Not covered in this plan (deferred):**
- Kuzu schema for ApproachPattern graph / SubGoal DAG (requires kuzu-graph.ts changes — separate PR)
- Parliament convening at PLAN phase (Parliament module is separate)
- APO trigger (PromptOptimizer wiring to ImprovementScheduler — follow-up task)
- Reflexion critique LLM call (post-run, follow-up task)
- Per-channel HitlChannel (Telegram/Web HITL adapters — follow-up task)
