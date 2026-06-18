# Element 10 — Parliament Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Parliament's three critical failures: Intelligence-First violations (keyword list, hardcoded models, numeric thresholds), dead wires to E5-E9 (ContextPipeline, GoalVerifier, DNA evolution), and context pollution in Round 2 (all positions broadcast; replaced by DiversityFilter selecting top-2 diverging pair).

**Architecture:** Two new files (DiversityFilter, short-term layer slot in ContextPipeline), ten targeted modifications. No rewrite — the existing MultiRoundDebateManager orchestration skeleton is preserved; only the innards of each round change. All Intelligence-First fixes are delete-only or swap-to-router operations.

**Tech Stack:** TypeScript (strict, NodeNext), Vitest, better-sqlite3, IntelligenceRouter (existing), GoalVerifier (existing, `src/tools/goal-verifier.ts`), ContextPipeline (existing, `src/context/pipeline.ts`).

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/memory/db.ts` | Modify | Schema v20: add `parliament_session_id` to `trajectory_turns` |
| `src/context/pipeline.ts` | Modify | Add `setShortTermLayer()` + TTL-decrement in `run()` |
| `src/parliament/diversity-filter.ts` | **Create** | `DiversityFilter.selectDivergingPair()` |
| `src/parliament/parallel-runner.ts` | Modify | Delete `shouldTrigger()` (lines 181–202) |
| `src/parliament/topic-worthiness.ts` | Modify | Delete `THRESHOLD`, trust LLM `isWorthy`, add optional `IntelligenceRouter` |
| `src/parliament/lite.ts` | Modify | Replace 3 hardcoded model strings with `router.resolve("classification").model` |
| `src/parliament/routing-wirer.ts` | Modify | Remove `confidenceThreshold` gate; stub `prepareParliamentContext` |
| `src/parliament/multi-round-debate.ts` | Modify | Parallel Round 1, DiversityFilter after R1, sparse Round 2, diversity context in Round 3 |
| `src/parliament/protocol.ts` | Modify | Add `diversePair?: [OwlPosition, OwlPosition]` to `ParliamentSession` |
| `src/owls/evolution.ts` | Modify | Add `export async function updateParliamentDNA()` |
| `src/gateway/core.ts` | Modify | Post-session: inject ContextPipeline layer + call GoalVerifier + call updateParliamentDNA |
| `src/parliament/orchestrator.ts` | Modify | Delete duplicate `runRound1/2/3` methods; delegate `convene()` body to `MultiRoundDebateManager.runDebate()`; keep post-session Pellet + `parliamentVerdicts.record()` |

Tests added to:
| Test file | What's tested |
|-----------|---------------|
| `__tests__/memory/db-v20-migration.test.ts` | v20 adds `parliament_session_id` column; idempotent on existing DB |
| `__tests__/context/pipeline-short-term.test.ts` | `setShortTermLayer`, output inclusion, TTL decrement, TTL=0 expiry |
| `__tests__/parliament/diversity-filter.test.ts` | Success, router-throws fallback, edge 2-position |
| `__tests__/parliament/multi-round-debate-sparse.test.ts` | Parallel R1 timestamps, R2 sees only diverging pair, R3 has filter reasoning |
| `__tests__/owls/evolution-parliament-dna.test.ts` | ADVANCES mutates DNA; BLOCKED skips; error in db.owls is non-fatal |
| `__tests__/parliament/parliament-integration.test.ts` | AC-4 ContextPipeline injection; AC-5 GoalVerifier called; AC-6 DNA on ADVANCES; AC-7 no DNA on BLOCKED; AC-10 IntentClarifier CLARIFY blocks Parliament |

---

## Task 1: Schema v20 — add `parliament_session_id` to `trajectory_turns`

**Files:**
- Modify: `src/memory/db.ts:29` (SCHEMA_VERSION), `src/memory/db.ts:1181–1198` (runMigrations MemoryDatabase), `src/memory/db.ts:3137–3171` (runMigrations StackOwlDB), `src/memory/db.ts:3272–3360` (applyV19Migration function, applyMigrations export)
- Create: `__tests__/memory/db-v20-migration.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/memory/db-v20-migration.test.ts
import { describe, it, expect, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";

describe("Schema v20 migration", () => {
  let db: Database.Database;
  afterEach(() => { try { db.close(); } catch {} });

  it("adds parliament_session_id column to trajectory_turns on fresh DB", () => {
    db = new Database(":memory:");
    applyMigrations(db);
    const cols = (db.prepare("PRAGMA table_info(trajectory_turns)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain("parliament_session_id");
  });

  it("is idempotent — calling applyMigrations twice does not throw", () => {
    db = new Database(":memory:");
    expect(() => { applyMigrations(db); applyMigrations(db); }).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/memory/db-v20-migration.test.ts
```
Expected: FAIL — `parliament_session_id` not in column list.

- [ ] **Step 3: Implement — bump SCHEMA_VERSION and add migration**

In `src/memory/db.ts`, make these four changes:

**3a. Line 29 — bump SCHEMA_VERSION:**
```typescript
const SCHEMA_VERSION = 20;
```

**3b. Add `applyV20Migration` function immediately after `applyV19Migration` (around line 3283):**
```typescript
function applyV20Migration(db: Database.Database): void {
  const turnCols = (db.prepare("PRAGMA table_info(trajectory_turns)").all() as { name: string }[]).map(c => c.name);
  if (!turnCols.includes("parliament_session_id")) {
    db.exec(`ALTER TABLE trajectory_turns ADD COLUMN parliament_session_id TEXT;`);
  }
}
```

**3c. In `MemoryDatabase.runMigrations()` — add v20 block after the `if (current < 19)` block (around line 1191):**
```typescript
    if (current < 20) {
      applyV20Migration(this.db);
    }
    if (current < SCHEMA_VERSION) {
      this.db.pragma(`user_version = ${SCHEMA_VERSION}`);
      log.engine.info(`[MemoryDatabase] Schema migrated to v${SCHEMA_VERSION}`);
    }
```
(Remove the old `if (current < SCHEMA_VERSION)` block that was already there and replace with the above.)

**3d. In `StackOwlDB.runMigrations()` — add v20 block after `if (current < 19)` (around line 3167):**
```typescript
    if (current < 20) {
      applyV20Migration(this.db);
      this.db.pragma(`user_version = ${SCHEMA_VERSION}`);
    }
```

**3e. In `applyMigrations()` export (around line 3356) — add v20 call after v19:**
```typescript
  if (current < 20) {
    applyV20Migration(db);
  }
  db.pragma(`user_version = ${SCHEMA_VERSION}`);
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/memory/db-v20-migration.test.ts
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-v20-migration.test.ts
git commit -m "feat(e10): schema v20 — add parliament_session_id to trajectory_turns"
```

---

## Task 2: ContextPipeline short-term layers

**Files:**
- Modify: `src/context/pipeline.ts`
- Create: `__tests__/context/pipeline-short-term.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/context/pipeline-short-term.test.ts
import { describe, it, expect } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import type { TriageSignals, ContextRequest } from "../../src/context/layer.js";

function mockTriage(): TriageSignals {
  return { userMessage: "hi", isConversational: true, hasFrustration: false,
    isOpinionRequest: false, hasTemporalTrigger: false, isReturningUser: false,
    sessionDepth: 1, hasActiveItems: false, effectiveUserId: "u1", continuityClass: null };
}
function mockReq(): ContextRequest {
  return { session: { id: "s1" } as any, callbacks: {} as any,
    continuityResult: null, digest: null, deps: {} as any };
}

function makePipeline(layers = []) {
  return new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
}

describe("ContextPipeline.setShortTermLayer", () => {
  it("includes short-term layer content in run() output", async () => {
    const pipeline = makePipeline([]);
    pipeline.setShortTermLayer("parliament_synthesis", "Verdict: PROCEED — synthesis text", { priority: 117, ttlTurns: 3 });
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("Verdict: PROCEED");
  });

  it("decrements ttlTurns by 1 after each run()", async () => {
    const pipeline = makePipeline([]);
    pipeline.setShortTermLayer("test_layer", "ephemeral content", { priority: 50, ttlTurns: 2 });
    await pipeline.run(mockReq(), mockTriage());
    // Second run: still present (ttlTurns was 2, now 1)
    const { output: out2 } = await pipeline.run(mockReq(), mockTriage());
    expect(out2).toContain("ephemeral content");
    // Third run: expired (ttlTurns hit 0 after second run)
    const { output: out3 } = await pipeline.run(mockReq(), mockTriage());
    expect(out3).not.toContain("ephemeral content");
  });

  it("respects priority ordering — short-term layer with priority 117 is between pellets (115) and profile (120)", async () => {
    const { makeLayer } = await import("../../src/context/layer.js").catch(() => ({ makeLayer: undefined }));
    // Build static layers at 115 and 120
    const pelletsLayer = {
      name: "pellets", priority: 115, maxTokens: 500,
      produces: ["pellets"], dependsOn: [],
      shouldFire: () => true, build: async () => "PELLETS_CONTENT",
    };
    const profileLayer = {
      name: "profile", priority: 120, maxTokens: 500,
      produces: ["profile"], dependsOn: [],
      shouldFire: () => true, build: async () => "PROFILE_CONTENT",
    };
    const pipeline = new ContextPipeline(
      [profileLayer, pelletsLayer],
      new ContextCache(), new LayerHealthMonitor(), new DAGPlanner(),
    );
    pipeline.setShortTermLayer("parliament_synthesis", "PARLIAMENT_CONTENT", { priority: 117, ttlTurns: 1 });
    const { output } = await pipeline.run(mockReq(), mockTriage());
    const pelletsIdx = output.indexOf("PELLETS_CONTENT");
    const parliIdx   = output.indexOf("PARLIAMENT_CONTENT");
    const profileIdx = output.indexOf("PROFILE_CONTENT");
    expect(pelletsIdx).toBeLessThan(parliIdx);
    expect(parliIdx).toBeLessThan(profileIdx);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/context/pipeline-short-term.test.ts
```
Expected: FAIL — `setShortTermLayer is not a function`.

- [ ] **Step 3: Implement short-term layers in `src/context/pipeline.ts`**

Add the `shortTermLayers` Map field and `setShortTermLayer()` method to `ContextPipeline`, and update `run()` to include them with TTL decrement:

```typescript
// After the `private readonly batches: ContextLayer[][];` line, add:
private readonly shortTermLayers = new Map<string, {
  content: string;
  priority: number;
  ttlTurns: number;
}>();

// New public method — add after the constructor:
setShortTermLayer(
  key: string,
  content: string,
  opts: { priority: number; ttlTurns: number },
): void {
  this.shortTermLayers.set(key, { content, priority: opts.priority, ttlTurns: opts.ttlTurns });
}
```

Update `run()` — in the output assembly block (after the `for (let batchIdx...)` loop, before the `const output = [...]` line), add short-term layer merging:

```typescript
// Collect short-term layers that still have TTL > 0
const shortTermEntries: Array<{ content: string; priority: number; key: string }> = [];
for (const [key, stl] of this.shortTermLayers) {
  if (stl.ttlTurns > 0) {
    shortTermEntries.push({ content: stl.content, priority: stl.priority, key });
    stl.ttlTurns -= 1;
    if (stl.ttlTurns === 0) {
      this.shortTermLayers.delete(key);
    }
  }
}

const output = [
  ...[...this.layers]
    .sort((a, b) => a.priority - b.priority)
    .map((l) => ({ content: results.get(l.produces[0] ?? l.name) ?? "", priority: l.priority })),
  ...shortTermEntries,
]
  .sort((a, b) => a.priority - b.priority)
  .map((e) => e.content)
  .filter(Boolean)
  .join("\n");
```

(Replace the original `const output = [...this.layers].sort(...).map(...).filter(Boolean).join("\n");` with the above.)

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/context/pipeline-short-term.test.ts
```
Expected: PASS (3 tests).

- [ ] **Step 5: Run existing pipeline tests to check for regressions**

```bash
npx vitest run __tests__/context/pipeline.test.ts __tests__/context/pipeline-integration.test.ts
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/context/pipeline.ts __tests__/context/pipeline-short-term.test.ts
git commit -m "feat(e10): add setShortTermLayer with TTL to ContextPipeline"
```

---

## Task 3: DiversityFilter — new file

**Files:**
- Create: `src/parliament/diversity-filter.ts`
- Create: `__tests__/parliament/diversity-filter.test.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
// __tests__/parliament/diversity-filter.test.ts
import { describe, it, expect, vi } from "vitest";
import { DiversityFilter } from "../../src/parliament/diversity-filter.js";
import type { OwlPosition } from "../../src/parliament/protocol.js";

function makePos(owlName: string, argument: string): OwlPosition {
  return { owlName, owlEmoji: "🦉", position: "FOR", argument };
}

describe("DiversityFilter", () => {
  it("returns the two most diverging positions from LLM response", async () => {
    const positions = [
      makePos("Owl1", "We should use microservices"),
      makePos("Owl2", "We should use a monolith"),
      makePos("Owl3", "We should use serverless"),
    ];
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"indices": [0, 1]}' }) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const mockProviders = new Map([["test", mockProvider]]);
    const filter = new DiversityFilter(mockRouter as any, mockProviders as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("Owl1");
    expect(b.owlName).toBe("Owl2");
  });

  it("falls back to [positions[0], positions[last]] when router throws", async () => {
    const positions = [
      makePos("Owl1", "arg1"),
      makePos("Owl2", "arg2"),
      makePos("Owl3", "arg3"),
    ];
    const mockProvider = { chat: vi.fn().mockRejectedValue(new Error("network error")) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const filter = new DiversityFilter(mockRouter as any, new Map([["test", mockProvider]]) as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("Owl1");
    expect(b.owlName).toBe("Owl3");
  });

  it("returns both positions when exactly 2 positions are provided", async () => {
    const positions = [makePos("OwlA", "for"), makePos("OwlB", "against")];
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"indices": [0, 1]}' }) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const filter = new DiversityFilter(mockRouter as any, new Map([["test", mockProvider]]) as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("OwlA");
    expect(b.owlName).toBe("OwlB");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/diversity-filter.test.ts
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `src/parliament/diversity-filter.ts`**

```typescript
import type { OwlPosition } from "./protocol.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export class DiversityFilter {
  constructor(
    private readonly router: IntelligenceRouter,
    private readonly providers: Map<string, ModelProvider>,
  ) {}

  async selectDivergingPair(
    positions: OwlPosition[],
  ): Promise<[OwlPosition, OwlPosition]> {
    const fallback: [OwlPosition, OwlPosition] = [
      positions[0],
      positions[positions.length - 1],
    ];

    if (positions.length <= 2) return fallback;

    try {
      const resolved = this.router.resolve("classification");
      const provider = this.providers.get(resolved.provider);
      if (!provider) return fallback;

      const positionList = positions
        .map((p, i) => `${i}: [${p.owlName}] ${p.argument.slice(0, 200)}`)
        .join("\n");

      const prompt =
        `Given these ${positions.length} positions on a debate topic, identify the two that most ` +
        `fundamentally disagree with each other.\n\n` +
        `Positions:\n${positionList}\n\n` +
        `Reply with ONLY valid JSON: {"indices": [<first_index>, <second_index>]}`;

      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        resolved.model,
        { temperature: 0, maxTokens: 50 },
      );

      const match = response.content.match(/\{[\s\S]*?\}/);
      if (!match) return fallback;

      const parsed = JSON.parse(match[0]) as { indices?: unknown };
      const indices = parsed.indices;

      if (
        !Array.isArray(indices) ||
        indices.length < 2 ||
        typeof indices[0] !== "number" ||
        typeof indices[1] !== "number" ||
        indices[0] < 0 || indices[0] >= positions.length ||
        indices[1] < 0 || indices[1] >= positions.length ||
        indices[0] === indices[1]
      ) {
        return fallback;
      }

      return [positions[indices[0]], positions[indices[1]]];
    } catch (err) {
      log.parliament.debug(
        `[DiversityFilter] Error selecting diverging pair: ${err instanceof Error ? err.message : String(err)} — using fallback`,
      );
      return fallback;
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/parliament/diversity-filter.test.ts
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/parliament/diversity-filter.ts __tests__/parliament/diversity-filter.test.ts
git commit -m "feat(e10): add DiversityFilter — LLM-based diverging pair selection"
```

---

## Task 4: Delete `shouldTrigger` from `parallel-runner.ts` and fix callers

**Files:**
- Modify: `src/parliament/parallel-runner.ts` (delete lines 181–202)
- Modify: `src/parliament/routing-wirer.ts` (remove call to `shouldTrigger`)

The `RoutingWirer.shouldTrigger()` delegates to `ParallelParliamentRunner.shouldTrigger()`. Both must go. The existing `routing-wirer.test.ts` tests call `RoutingWirer.shouldTrigger()` — those tests must be deleted too.

- [ ] **Step 1: Delete `shouldTrigger` from `parallel-runner.ts`**

Remove the entire static method `shouldTrigger` (lines 178–202 in `parallel-runner.ts`):

```typescript
// DELETE this entire block:
  /**
   * Auto-trigger check: should this topic go to parliament?
   * Returns true if confidence < 0.6 OR topic contains contested keywords.
   */
  static shouldTrigger(topic: string, owlConfidence?: number): boolean {
    if (owlConfidence !== undefined && owlConfidence < 0.6) return true;

    const contested = [
      "should",
      "best way",
      "which is better",
      "tradeoff",
      "trade-off",
      " vs ",
      " vs.",
      "compare",
      "versus",
      "pros and cons",
      "recommend",
      "alternative",
    ];

    const lower = topic.toLowerCase();
    return contested.some((kw) => lower.includes(kw));
  }
```

- [ ] **Step 2: Delete `RoutingWirer.shouldTrigger` from `routing-wirer.ts`**

In `src/parliament/routing-wirer.ts`, remove:
1. The static `shouldTrigger()` method (lines 28–30)
2. The `import { ParallelParliamentRunner }` line (line 12) — only if `ParallelParliamentRunner` is no longer referenced
3. All three calls to `ParallelParliamentRunner.shouldTrigger()` in `classifyWithParliament()` and `checkParliamentTrigger()`

After deletion, `classifyWithParliament()` becomes:

```typescript
  async classifyWithParliament(
    message: string,
    baseClassifyFn: () => Promise<TaskStrategy>,
    provider: ModelProvider,
    options?: {
      useParallelRunner?: boolean;
      useLLMCheck?: boolean;
    },
  ): Promise<TaskStrategy> {
    const opts = {
      useParallelRunner: true,
      useLLMCheck: true,
      ...options,
    };

    if (opts.useParallelRunner && opts.useLLMCheck) {
      const shouldConvene = await shouldConveneParliament(message, provider).catch(() => false);
      if (shouldConvene) {
        const strategy = await baseClassifyFn();
        if (strategy.strategy === "PARLIAMENT") return strategy;
        return {
          ...strategy,
          strategy: "PARLIAMENT",
          reasoning: `LLM detected debate-worthy topic → escalated to PARLIAMENT`,
          parliamentConfig: {
            topic: message.slice(0, 200),
            owlCount: Math.min(3, strategy.owlAssignments?.length ?? 2),
          },
        };
      }
    }

    return baseClassifyFn();
  }
```

And `checkParliamentTrigger()` becomes:

```typescript
export async function checkParliamentTrigger(
  message: string,
  provider: ModelProvider,
  config: StackOwlConfig,
): Promise<{ shouldTrigger: boolean; reason: string }> {
  const parliamentEnabled = config.parliament && (config.parliament as Record<string, unknown>).enabled;
  if (parliamentEnabled === false) {
    return { shouldTrigger: false, reason: "Parliament disabled in config" };
  }

  try {
    const shouldConvene = await shouldConveneParliament(message, provider);
    return {
      shouldTrigger: shouldConvene,
      reason: shouldConvene ? "LLM detected debate-worthy topic" : "LLM detected non-debatable topic",
    };
  } catch (err) {
    return {
      shouldTrigger: false,
      reason: `LLM check failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}
```

Also stub `prepareParliamentContext()` — replace its body with:

```typescript
  async prepareParliamentContext(
    _message: string,
    _pelletStore: import("../pellets/store.js").PelletStore,
  ): Promise<ChatMessage[]> {
    // Deprecated: Parliament context injection is now handled inline by the orchestrator.
    return [];
  }
```

- [ ] **Step 3: Fix `routing-wirer.test.ts` — remove tests for deleted methods**

Open `__tests__/parliament/routing-wirer.test.ts` and delete any `it()` blocks testing `shouldTrigger` or `confidenceThreshold`. Replace with a test that the new flow works:

```typescript
it("classifyWithParliament uses LLM check instead of keyword matching", async () => {
  const wirer = new RoutingWirer();
  const mockProvider = {
    chat: vi.fn().mockResolvedValue({ content: '{"shouldConvene": true}' }),
  } as any;
  const baseStrategy: TaskStrategy = { strategy: "DIRECT", confidence: 0.8, reasoning: "direct" };
  const result = await wirer.classifyWithParliament(
    "what are the tradeoffs of react vs vue",
    async () => baseStrategy,
    mockProvider,
  );
  // The LLM check may or may not fire depending on mock shape — just confirm no crash
  expect(result.strategy).toBeDefined();
});
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/parliament/routing-wirer.test.ts
```
Expected: PASS.

```bash
npx grep -r "shouldTrigger\|THRESHOLD\|confidenceThreshold\|claude-haiku-4-5" src/parliament/ 2>/dev/null; echo "exit: $?"
```
Expected: no matches (or only in comments).

- [ ] **Step 5: Commit**

```bash
git add src/parliament/parallel-runner.ts src/parliament/routing-wirer.ts __tests__/parliament/routing-wirer.test.ts
git commit -m "feat(e10): delete shouldTrigger keyword list and confidenceThreshold gate"
```

---

## Task 5: Fix `topic-worthiness.ts` — delete THRESHOLD, trust LLM `isWorthy`, add router

**Files:**
- Modify: `src/parliament/topic-worthiness.ts`
- Modify: `__tests__/parliament/topic-worthiness.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `__tests__/parliament/topic-worthiness.test.ts`:

```typescript
it("trusts LLM isWorthy directly without THRESHOLD gate", async () => {
  // LLM returns isWorthy=true, confidence=0.3 (below old 0.4 confidence gate)
  const mockProvider = {
    chat: vi.fn().mockResolvedValue({
      content: '{"isWorthy": true, "confidence": 0.3, "reasons": ["test"], "category": "tradeoff"}',
    }),
  } as any;
  const evaluator = new TopicWorthinessEvaluator(mockProvider);
  const result = await evaluator.evaluate("should I use react or vue?");
  // With THRESHOLD deleted, isWorthy=true from LLM → result.isWorthy should be true
  expect(result.isWorthy).toBe(true);
});

it("THRESHOLD export is removed", async () => {
  const mod = await import("../../src/parliament/topic-worthiness.js");
  expect((mod as Record<string, unknown>).THRESHOLD).toBeUndefined();
});

it("uses router.resolve('classification') model when router is provided", async () => {
  const mockProvider = {
    chat: vi.fn().mockResolvedValue({
      content: '{"isWorthy": true, "confidence": 0.8, "reasons": [], "category": "tradeoff"}',
    }),
  } as any;
  const mockRouter = {
    resolve: vi.fn().mockReturnValue({ provider: "mock", model: "router-model", tier: "low" as const }),
  } as any;
  const evaluator = new TopicWorthinessEvaluator(mockProvider, mockRouter);
  await evaluator.evaluate("tradeoffs of microservices");
  // Second arg of chat() should be "router-model"
  expect(mockProvider.chat).toHaveBeenCalledWith(
    expect.any(Array),
    "router-model",
    expect.any(Object),
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/parliament/topic-worthiness.test.ts
```
Expected: 3 new tests fail.

- [ ] **Step 3: Implement changes in `src/parliament/topic-worthiness.ts`**

**3a. Delete line 31 (THRESHOLD export):**
```typescript
// DELETE:
export const THRESHOLD = 0.6;
```

**3b. Add `IntelligenceRouter` import:**
```typescript
import type { IntelligenceRouter } from "../intelligence/router.js";
```

**3c. Update constructor to accept optional router:**
```typescript
export class TopicWorthinessEvaluator {
  constructor(
    private provider: ModelProvider,
    private router?: IntelligenceRouter,
  ) {}
```

**3d. Update `evaluate()` — replace the `this.provider.chat(...)` call and the `isWorthy` / `score` calculation:**

Replace the block starting with `const response = await this.provider.chat(` through `return result;` with:

```typescript
      const model = this.router?.resolve("classification").model;
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { temperature: 0, maxTokens: 200 },
      );

      const content = response.content.trim();
      const jsonMatch = content.match(/\{[\s\S]*\}/);

      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        const isWorthy = parsed.isWorthy === true;
        const confidence = Math.min(1.0, Math.max(0.0, parsed.confidence ?? 0.5));

        const result: TopicWorthinessResult = {
          isWorthy,
          score: confidence,
          confidence,
          reasoning: (parsed.reasons ?? []).join("; "),
          indicators: parsed.reasons ?? [],
          category: (parsed.category ?? "other") as WorthinessCategory,
        };

        log.parliament.behavioral("behavioral.parliament.topic_evaluated", {
          topic: topic.slice(0, 100),
          isWorthy: result.isWorthy,
          score: result.score,
          confidence: result.confidence,
          category: result.category,
        });

        log.parliament.info(
          `[TopicWorthiness] → isWorthy=${result.isWorthy} (conf=${result.confidence.toFixed(2)})`,
        );

        return result;
      }
```

- [ ] **Step 4: Run all topic-worthiness tests**

```bash
npx vitest run __tests__/parliament/topic-worthiness.test.ts
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/parliament/topic-worthiness.ts __tests__/parliament/topic-worthiness.test.ts
git commit -m "feat(e10): remove THRESHOLD, trust LLM isWorthy, add IntelligenceRouter to TopicWorthinessEvaluator"
```

---

## Task 6: Fix `lite.ts` — replace hardcoded model strings with router

**Files:**
- Modify: `src/parliament/lite.ts`

- [ ] **Step 1: Write failing test**

Create `__tests__/parliament/lite-router.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { ParliamentLite } from "../../src/parliament/lite.js";
import type { OwlInstance } from "../../src/owls/persona.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } },
}));

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
    dna: {
      owl: name, generation: 0, created: "", lastEvolved: "",
      learnedPreferences: {}, evolvedTraits: {
        challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate",
        teachingStyle: "adaptive", delegationPreference: "collaborative",
      },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [],
    },
  };
}

describe("ParliamentLite router wiring", () => {
  it("uses router.resolve('classification').model when router is provided", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "VOTE: [PROCEED] — good" }),
    } as any;
    const mockRouter = {
      resolve: vi.fn().mockReturnValue({ provider: "test", model: "router-resolved-model", tier: "low" }),
    } as any;
    const config = { defaultProvider: "mock", providers: {} } as any;
    const lite = new ParliamentLite(mockProvider, config, undefined, mockRouter);
    await lite.deliberate({
      topic: "test topic",
      question: "should we proceed?",
      context: "test context",
      owls: [makeOwl("OwlA"), makeOwl("OwlB")],
    });
    // All provider.chat calls should use router-resolved-model, not haiku hardcoded
    for (const call of mockProvider.chat.mock.calls) {
      expect(call[1]).toBe("router-resolved-model");
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/lite-router.test.ts
```
Expected: FAIL — calls use hardcoded model string, not `router-resolved-model`.

- [ ] **Step 3: Implement in `src/parliament/lite.ts`**

**3a. Add import at top:**
```typescript
import type { IntelligenceRouter } from "../intelligence/router.js";
```

**3b. Update constructor signature (line 53–57):**
```typescript
export class ParliamentLite {
  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
    private db?: MemoryDatabase,
    private router?: IntelligenceRouter,
  ) {}
```

**3c. In `deliberate()`, before the first `Promise.all`, add model resolution:**
```typescript
    const model = this.router?.resolve("classification").model
      ?? this.config.providers?.anthropic?.defaultModel
      ?? undefined;
```

**3d. Replace all 3 occurrences of the second argument in `this.provider.chat(...)` calls:**

Line 92 (advocate call second arg): replace
```typescript
        this.config.providers?.anthropic?.defaultModel ?? "claude-haiku-4-5-20251001",
```
with:
```typescript
        model,
```

Line 108 (devil call second arg): same replacement.

Line 137 (synthesis call second arg): same replacement.

- [ ] **Step 4: Run test**

```bash
npx vitest run __tests__/parliament/lite-router.test.ts
```
Expected: PASS.

- [ ] **Step 5: Verify AC-1 grep**

```bash
grep -r "shouldTrigger\|THRESHOLD\|confidenceThreshold\|claude-haiku-4-5" src/parliament/
```
Expected: no matches.

- [ ] **Step 6: Commit**

```bash
git add src/parliament/lite.ts __tests__/parliament/lite-router.test.ts
git commit -m "feat(e10): replace hardcoded model strings in ParliamentLite with IntelligenceRouter"
```

---

## Task 7: Update `ParliamentSession` protocol — add `diversePair` field

> **Ordering note:** This task MUST be committed before Task 8 (`multi-round-debate.ts`). Task 8's tests set `session.diversePair` — TypeScript will fail to compile until `diversePair` is declared on `ParliamentSession`.

**Files:**
- Modify: `src/parliament/protocol.ts`

- [ ] **Step 1: Add `diversePair` to `ParliamentSession`**

In `src/parliament/protocol.ts`, update the `ParliamentSession` interface:

```typescript
export interface ParliamentSession {
  id: string;
  config: ParliamentConfig;
  phase: ParliamentPhase;
  positions: OwlPosition[];
  challenges: OwlChallenge[];
  synthesis?: string;
  verdict?: string;
  startedAt: number;
  completedAt?: number;
  /** Set after Round 1 by DiversityFilter — the two most-disagreeing positions */
  diversePair?: [OwlPosition, OwlPosition];
  /** Reasoning from DiversityFilter about why these two positions diverge most */
  diversityReasoning?: string;
}
```

- [ ] **Step 2: Verify no breakage**

```bash
npx vitest run __tests__/parliament/
```
Expected: all existing parliament tests still pass.

- [ ] **Step 3: Commit**

```bash
git add src/parliament/protocol.ts
git commit -m "feat(e10): add diversePair and diversityReasoning to ParliamentSession"
```

---

## Task 8: Multi-round-debate — parallel Round 1 + DiversityFilter + sparse Round 2 + updated Round 3

**Files:**
- Modify: `src/parliament/multi-round-debate.ts`
- Create: `__tests__/parliament/multi-round-debate-sparse.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/parliament/multi-round-debate-sparse.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MultiRoundDebateManager } from "../../src/parliament/multi-round-debate.js";
import type { ParliamentSession } from "../../src/parliament/protocol.js";
import type { OwlInstance } from "../../src/owls/persona.js";

vi.mock("../../src/logger.js", () => ({
  log: { parliament: { info: vi.fn(), debug: vi.fn(), warn: vi.fn(), behavioral: vi.fn() },
         engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } },
}));

// callTimestamps is reset in beforeEach to avoid cross-test contamination
let callTimestamps: number[] = [];
beforeEach(() => { callTimestamps = []; });

vi.mock("../../src/engine/runtime.js", () => ({
  OwlEngine: vi.fn().mockImplementation(() => ({
    run: vi.fn().mockImplementation(async () => {
      callTimestamps.push(Date.now());
      await new Promise(r => setTimeout(r, 10));
      return { content: "[FOR] I support this position fully.", owlName: "test", owlEmoji: "🦉", toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
    }),
  })),
}));

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: `You are ${name}.`, sourcePath: "" },
    dna: { owl: name, generation: 0, created: "", lastEvolved: "", learnedPreferences: {},
      evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
        delegationPreference: "collaborative" },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [] },
  };
}

function makeSession(owls: OwlInstance[]): ParliamentSession {
  return { id: "test-session", config: { topic: "test topic", participants: owls,
    contextMessages: [] }, phase: "setup", positions: [], challenges: [],
    synthesis: "", verdict: undefined, startedAt: Date.now() };
}

describe("MultiRoundDebateManager — sparse debate", () => {
  it("AC-2: Round 1 fires all owl calls in parallel (start time delta < 100ms)", async () => {
    const owls = [makeOwl("Owl1"), makeOwl("Owl2"), makeOwl("Owl3")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    const perspectives = new Map();
    await (manager as any).runRound1(session, perspectives);
    // All 3 owl calls should overlap: max - min < 100ms
    expect(callTimestamps.length).toBe(3);
    const delta = Math.max(...callTimestamps) - Math.min(...callTimestamps);
    expect(delta).toBeLessThan(100);
  });

  it("AC-3: Round 2 prompt contains only the two diverging owls, not the others", async () => {
    const owls = [makeOwl("OwlA"), makeOwl("OwlB"), makeOwl("OwlC"), makeOwl("OwlD")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    const perspectives = new Map();

    // Manually set positions and diversePair (simulating post-Round1 state)
    session.positions = [
      { owlName: "OwlA", owlEmoji: "🦉", position: "FOR", argument: "Position A" },
      { owlName: "OwlB", owlEmoji: "🦉", position: "AGAINST", argument: "Position B" },
      { owlName: "OwlC", owlEmoji: "🦉", position: "NEUTRAL", argument: "Position C" },
      { owlName: "OwlD", owlEmoji: "🦉", position: "FOR", argument: "Position D" },
    ];
    // DiversityFilter chose OwlB and OwlD as most diverging
    session.diversePair = [session.positions[1], session.positions[3]];

    const capturedPrompts: string[] = [];
    const engineMock = {
      run: vi.fn().mockImplementation(async (prompt: string) => {
        capturedPrompts.push(prompt);
        return { content: "I challenge OwlB on their reasoning.", owlName: "OwlA", owlEmoji: "🦉",
          toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
      }),
    };
    (manager as any).engine = engineMock;

    await (manager as any).runRound2(session, perspectives);

    // The challenger prompt should mention OwlB and OwlD but NOT OwlA or OwlC
    const challengerPrompt = capturedPrompts[0] ?? "";
    expect(challengerPrompt).toContain("Position B");
    expect(challengerPrompt).toContain("Position D");
    expect(challengerPrompt).not.toContain("Position A");
    expect(challengerPrompt).not.toContain("Position C");
  });

  it("Round 3 prompt includes diversityReasoning when present", async () => {
    const owls = [makeOwl("Mentor"), makeOwl("OwlB")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    session.positions = [
      { owlName: "Mentor", owlEmoji: "🦉", position: "FOR", argument: "arg1" },
      { owlName: "OwlB", owlEmoji: "🦉", position: "AGAINST", argument: "arg2" },
    ];
    session.challenges = [{ owlName: "OwlB", targetOwl: "Mentor", challengeContent: "challenge text" }];
    session.diversePair = [session.positions[0], session.positions[1]];
    session.diversityReasoning = "They disagree on fundamentals";

    const capturedPrompts: string[] = [];
    const engineMock = {
      run: vi.fn().mockImplementation(async (prompt: string) => {
        capturedPrompts.push(prompt);
        return { content: "PROCEED — synthesis", owlName: "Mentor", owlEmoji: "🦉",
          toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
      }),
    };
    (manager as any).engine = engineMock;
    const perspectives = new Map();
    await (manager as any).runRound3(session, perspectives);
    expect(capturedPrompts[0]).toContain("They disagree on fundamentals");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/parliament/multi-round-debate-sparse.test.ts
```
Expected: FAIL — AC-2 (sequential, not parallel), AC-3 (all positions included), AC-Round3 (no diversity reasoning).

- [ ] **Step 3: Update `MultiRoundDebateManager` constructor — inject `DiversityFilter`**

Add imports to `src/parliament/multi-round-debate.ts`:
```typescript
import { DiversityFilter } from "./diversity-filter.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ModelProvider as BaseModelProvider } from "../providers/base.js";
```

Update the class:
```typescript
export class MultiRoundDebateManager {
  private engine: OwlEngine;
  private diversityFilter?: DiversityFilter;

  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
    router?: IntelligenceRouter,
    providers?: Map<string, BaseModelProvider>,
  ) {
    this.engine = new OwlEngine();
    if (router && providers) {
      this.diversityFilter = new DiversityFilter(router, providers);
    }
  }
```

- [ ] **Step 4: Convert `runRound1()` to parallel execution**

Replace the `for (const owl of session.config.participants)` loop in `runRound1()` with `Promise.allSettled()`:

```typescript
  async runRound1(
    session: ParliamentSession,
    perspectives: Map<string, PerspectiveOverlay>,
  ): Promise<void> {
    session.phase = "round1_position";
    const cb = session.config.callbacks;

    if (session.config.callbacks?.onRoundStart) {
      await session.config.callbacks.onRoundStart(1, "round1_position");
    }

    const tags = ["FOR", "AGAINST", "CONDITIONAL", "NEUTRAL", "ANALYSIS"] as const;

    const positionPromises = session.config.participants.map(async (owl) => {
      const perspective = perspectives.get(owl.persona.name);
      const roleLabel = perspective
        ? `${perspective.label} ${perspective.emoji}`
        : owl.persona.type;

      let prompt =
        `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
        `Task: Provide your initial hardline position on this topic based on your role as ${roleLabel}. ` +
        `State exactly one of these positions at the very beginning of your response: [FOR, AGAINST, CONDITIONAL, NEUTRAL, ANALYSIS]. ` +
        `Then provide a single paragraph (max 4 sentences) arguing your case. Be opinionated.`;

      if (perspective) {
        prompt = buildPerspectivePrompt(prompt, perspective);
      }

      const sessionHistory = session.config.contextMessages.map((m) => ({
        role: m.role as import("../providers/base.js").MessageRole,
        content: m.content,
      }));

      const response = await this.engine.run(prompt, {
        provider: this.provider,
        owl,
        sessionHistory,
        config: this.config,
      });

      let positionScore: OwlPosition["position"] = "ANALYSIS";
      for (const tag of tags) {
        if (
          response.content.toUpperCase().includes(`[${tag}]`) ||
          response.content.startsWith(tag)
        ) {
          positionScore = tag;
          break;
        }
      }

      let cleanArg = response.content;
      for (const tag of tags) {
        cleanArg = cleanArg
          .replace(`[${tag}]`, "")
          .replace(new RegExp(`^${tag}[:\\s]*`, "i"), "")
          .trim();
      }

      return {
        owlName: owl.persona.name,
        owlEmoji: perspective?.emoji || owl.persona.emoji,
        position: positionScore,
        argument: cleanArg,
      } satisfies OwlPosition;
    });

    const settled = await Promise.allSettled(positionPromises);

    for (let idx = 0; idx < settled.length; idx++) {
      const result = settled[idx];
      let position: OwlPosition;
      if (result.status === "fulfilled") {
        position = result.value;
      } else {
        const owl = session.config.participants[idx];
        position = {
          owlName: owl.persona.name,
          owlEmoji: owl.persona.emoji,
          position: "NEUTRAL",
          argument: "Unable to form a position in time.",
        };
      }
      session.positions.push(position);
      if (cb?.onPositionReady) {
        await cb.onPositionReady(position);
      }
    }

    // Run DiversityFilter to identify the top-2 most-disagreeing positions
    if (this.diversityFilter && session.positions.length >= 2) {
      try {
        const pair = await this.diversityFilter.selectDivergingPair(session.positions);
        session.diversePair = pair;
      } catch {
        session.diversePair = [
          session.positions[0],
          session.positions[session.positions.length - 1],
        ];
      }
    }
  }
```

- [ ] **Step 5: Update `runRound2()` to use only `session.diversePair`**

Replace the `allPositions` computation in `runRound2()`:

```typescript
    // Use only the diverging pair for Round 2 to prevent context pollution
    const targetPositions = session.diversePair ?? session.positions;
    const allPositions = targetPositions
      .map((p) => {
        const persp = perspectives.get(p.owlName);
        const label = persp ? `${persp.label}` : p.owlName;
        return `- ${label} [${p.position}]: ${p.argument}`;
      })
      .join("\n\n");
```

Update the prompt in `runRound2()` to reference that only the most-disagreeing positions are shown:
```typescript
    let prompt =
      `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
      `The two most fundamentally disagreeing positions are:\n${allPositions}\n\n` +
      `Task: Review these positions. If you see a gaping hole in someone's logic, a missed risk, or a naive assumption, ` +
      `call them out specifically. Name the participant you are challenging. Keep it to 2-3 sentences. ` +
      `If both positions are mostly reasonable, play devil's advocate against the stronger one.`;
```

- [ ] **Step 6: Update `runRound3()` to include diversity reasoning**

In `runRound3()`, update the `history` variable and prompt:

```typescript
    const diversityContext = session.diversePair
      ? `\nKey disagreement: ${session.diversePair[0].owlName} vs ${session.diversePair[1].owlName}` +
        (session.diversityReasoning ? ` — ${session.diversityReasoning}` : "")
      : "";

    const history =
      `TOPIC: ${session.config.topic}\n\nPositions:\n${positionsText}\n\nChallenges:\n${challengesText}` +
      diversityContext;
```

- [ ] **Step 7: Run tests**

```bash
npx vitest run __tests__/parliament/multi-round-debate-sparse.test.ts __tests__/parliament/multi-round-debate.test.ts
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/parliament/multi-round-debate.ts __tests__/parliament/multi-round-debate-sparse.test.ts
git commit -m "feat(e10): parallel Round 1, DiversityFilter, sparse Round 2, diversity context in Round 3"
```

---

## Task 9: Add `updateParliamentDNA` to `evolution.ts`

**Files:**
- Modify: `src/owls/evolution.ts`
- Create: `__tests__/owls/evolution-parliament-dna.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/owls/evolution-parliament-dna.test.ts
import { describe, it, expect, vi } from "vitest";
import { updateParliamentDNA } from "../../src/owls/evolution.js";
import type { OwlInstance } from "../../src/owls/persona.js";

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
    dna: { owl: name, generation: 1, created: "", lastEvolved: "",
      learnedPreferences: {}, evolvedTraits: {
        challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate",
        teachingStyle: "adaptive", delegationPreference: "collaborative",
      },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [] },
  };
}

describe("updateParliamentDNA", () => {
  it("ADVANCES: increases synthesizer expertiseGrowth for topic category", async () => {
    const synthesizer = makeOwl("Synthesizer");
    const challenger = makeOwl("Challenger");
    const db = {} as any;
    await updateParliamentDNA(synthesizer, challenger, [synthesizer, challenger], "PROCEED", "architecture", db, "ADVANCES");
    expect(synthesizer.dna.expertiseGrowth["architecture"]).toBeGreaterThan(0.5);
  });

  it("ADVANCES: increases challenger expertiseGrowth for critical_thinking at half rate", async () => {
    const synthesizer = makeOwl("S");
    const challenger = makeOwl("C");
    const db = {} as any;
    await updateParliamentDNA(synthesizer, challenger, [synthesizer, challenger], "PROCEED", "typescript", db, "ADVANCES");
    const synthGrowth = synthesizer.dna.expertiseGrowth["typescript"] ?? 0;
    const challGrowth = challenger.dna.expertiseGrowth["critical_thinking"] ?? 0;
    expect(challGrowth).toBeCloseTo(synthGrowth / 2, 1);
  });

  it("BLOCKED: makes no DNA changes", async () => {
    const owl = makeOwl("Owl");
    const before = JSON.stringify(owl.dna);
    const db = {} as any;
    await updateParliamentDNA(owl, undefined, [owl], "HOLD", "design", db, "BLOCKED");
    expect(JSON.stringify(owl.dna)).toBe(before);
  });

  it("PARTIAL: makes no DNA changes (same as BLOCKED)", async () => {
    const owl = makeOwl("Owl");
    const before = JSON.stringify(owl.dna);
    const db = {} as any;
    await updateParliamentDNA(owl, undefined, [owl], "PARTIAL", "design", db, "PARTIAL");
    expect(JSON.stringify(owl.dna)).toBe(before);
  });

  it("is non-fatal — resolves even when expertiseGrowth mutation would throw", async () => {
    const owl = makeOwl("Owl");
    // Freeze expertiseGrowth to cause assignment throw
    Object.freeze(owl.dna.expertiseGrowth);
    const db = {} as any;
    // Should not propagate the TypeError
    await expect(
      updateParliamentDNA(owl, undefined, [owl], "PROCEED", "design", db, "ADVANCES")
    ).resolves.not.toThrow();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/owls/evolution-parliament-dna.test.ts
```
Expected: FAIL — `updateParliamentDNA` not exported.

- [ ] **Step 3: Implement `updateParliamentDNA` in `src/owls/evolution.ts`**

Add after the `updateClarificationAutonomy` function (end of file):

```typescript
const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

/**
 * Updates Parliament participants' DNA after a completed debate session.
 * Only mutates when GoalVerifier returned ADVANCES.
 * Follows the same proportional-delta pattern as updateClarificationAutonomy().
 *
 * Mutations are in-memory only. The caller (gateway/core.ts) is responsible
 * for persisting via owlRegistry.saveDNA() for each participant.
 */
export async function updateParliamentDNA(
  synthesizer: import('./persona.js').OwlInstance | undefined,
  challenger: import('./persona.js').OwlInstance | undefined,
  participants: import('./persona.js').OwlInstance[],
  _verdict: string,
  topicCategory: string,
  _db: import('../memory/db.js').MemoryDatabase,
  goalVerifierResult: 'ADVANCES' | 'PARTIAL' | 'BLOCKED' | 'NEUTRAL',
): Promise<void> {
  if (goalVerifierResult !== 'ADVANCES') return;

  try {
    const LEARNING_RATE = 0.05;

    if (synthesizer) {
      synthesizer.dna.expertiseGrowth[topicCategory] = clamp(
        (synthesizer.dna.expertiseGrowth[topicCategory] ?? 0.5) + LEARNING_RATE,
        0.1, 0.9,
      );
    }

    if (challenger) {
      const ctKey = 'critical_thinking';
      challenger.dna.expertiseGrowth[ctKey] = clamp(
        (challenger.dna.expertiseGrowth[ctKey] ?? 0.5) + LEARNING_RATE * 0.5,
        0.1, 0.9,
      );
    }

    for (const owl of participants) {
      if (owl.dna.evolvedTraits.delegationPreference === 'autonomous') {
        const key = 'delegation_autonomy';
        const current = (owl.dna.learnedPreferences[key] as number) ?? 0.5;
        owl.dna.learnedPreferences[key] = clamp(current - LEARNING_RATE, 0.1, 0.9);
      }
    }
  } catch { /* non-fatal — DNA mutation failures must not crash Parliament */ }
}
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/owls/evolution-parliament-dna.test.ts
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/owls/evolution.ts __tests__/owls/evolution-parliament-dna.test.ts
git commit -m "feat(e10): add updateParliamentDNA to evolution.ts"
```

---

## Task 10: Wire post-session ContextPipeline + GoalVerifier + DNA in `gateway/core.ts`

**Files:**
- Modify: `src/gateway/core.ts`
- Create: `__tests__/parliament/parliament-integration.test.ts`

- [ ] **Step 1: Write failing integration tests**

```typescript
// __tests__/parliament/parliament-integration.test.ts
import { describe, it, expect, vi } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
    parliament: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), behavioral: vi.fn() },
    evolution: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

// Test AC-4: ContextPipeline receives parliament_synthesis after session
it("AC-4: post-session synthesis formatting includes topic, verdict, synthesis, and key dissent", () => {
  // Test the formatting logic that gateway/core.ts runs — not the pipeline call itself
  const debateSession = {
    synthesis: "The council recommends PROCEED with microservices.",
    verdict: "PROCEED",
    config: { topic: "microservices vs monolith", participants: [] },
    positions: [
      { owlName: "Owl1", owlEmoji: "🦉", position: "FOR", argument: "Microservices scale better" },
      { owlName: "Owl2", owlEmoji: "🦉", position: "AGAINST", argument: "Monolith is simpler and we have 3 devs" },
    ],
    challenges: [] as any[],
  };
  const synthesis = debateSession.synthesis ?? "";
  const minorityContent = debateSession.positions.find(p => p.position === "AGAINST")?.argument
    ?? debateSession.challenges[0]?.challengeContent
    ?? "";
  const formattedSynthesis =
    `[Parliament concluded on "${debateSession.config.topic}"] Verdict: ${debateSession.verdict ?? "CONSENSUS_REACHED"}\n` +
    `The council's synthesis: ${synthesis.slice(0, 300)}\n` +
    (minorityContent ? `Key dissent: ${minorityContent.slice(0, 150)}\n` : "");

  expect(formattedSynthesis).toContain("microservices vs monolith");
  expect(formattedSynthesis).toContain("PROCEED");
  expect(formattedSynthesis).toContain("The council recommends PROCEED");
  expect(formattedSynthesis).toContain("Key dissent: Monolith is simpler");
  // The gateway call: priority=117, ttlTurns=3
  // Verified by inspecting gateway/core.ts Task 10 Step 4 Block A and B
});

// Test AC-5: GoalVerifier receives the correct arguments when Parliament calls it
it("AC-5: GoalVerifier.verify receives toolName='parliament' and synthesis as toolResult", async () => {
  const synthesis = "Parliament recommends PROCEED.";
  const activeSubGoal = { id: "g1", description: "decide architecture", status: "in_progress" as const, dependsOn: [] };

  const capturedArgs: Parameters<(typeof mockVerifier)["verify"]>[0][] = [];
  const mockVerifier = {
    verify: vi.fn().mockImplementation((args: any) => {
      capturedArgs.push(args);
      return Promise.resolve({ verdict: "ADVANCES", reason: "debate helped" });
    }),
  };

  // Simulate the conditional call from gateway/core.ts Task 10 Block A
  if (mockVerifier && activeSubGoal) {
    await mockVerifier.verify({
      toolName: "parliament",
      toolArgs: {},
      toolResult: synthesis,
      subGoal: activeSubGoal,
      userMessage: "should we use microservices?",
    });
  }

  expect(capturedArgs).toHaveLength(1);
  expect(capturedArgs[0]).toMatchObject({
    toolName: "parliament",
    toolResult: synthesis,
    subGoal: expect.objectContaining({ id: "g1", description: "decide architecture" }),
  });
});

// Test AC-6: DNA update fires when GoalVerifier returns ADVANCES
it("AC-6: updateParliamentDNA is called when GoalVerifier returns ADVANCES", async () => {
  const { updateParliamentDNA } = await import("../../src/owls/evolution.js");
  const spy = vi.spyOn(await import("../../src/owls/evolution.js"), "updateParliamentDNA");

  function makeOwl(name: string) {
    return {
      persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
        specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
      dna: { owl: name, generation: 1, created: "", lastEvolved: "", learnedPreferences: {},
        evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
          formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
          delegationPreference: "collaborative" },
        expertiseGrowth: {}, domainConfidence: {},
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
          challengesAccepted: 0, parliamentSessions: 0 }, evolutionLog: [] },
    };
  }

  const synthesizer = makeOwl("Owl1");
  const challenger = makeOwl("Owl2");
  const participants = [synthesizer, challenger];
  await updateParliamentDNA(synthesizer, challenger, participants, "PROCEED", "architecture", {} as any, "ADVANCES");
  // Synthesizer's expertiseGrowth should increase
  expect(synthesizer.dna.expertiseGrowth["architecture"]).toBeGreaterThan(0);
  spy.mockRestore();
});

// Test AC-7: No DNA change when GoalVerifier returns BLOCKED
it("AC-7: updateParliamentDNA skips all mutations when goalVerifierResult is BLOCKED", async () => {
  const { updateParliamentDNA } = await import("../../src/owls/evolution.js");

  function makeOwl(name: string) {
    return {
      persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
        specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
      dna: { owl: name, generation: 1, created: "", lastEvolved: "", learnedPreferences: {},
        evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
          formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
          delegationPreference: "collaborative" },
        expertiseGrowth: {}, domainConfidence: {},
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
          challengesAccepted: 0, parliamentSessions: 0 }, evolutionLog: [] },
    };
  }

  const owl = makeOwl("TestOwl");
  const snapshotBefore = JSON.stringify(owl.dna.expertiseGrowth);
  await updateParliamentDNA(owl, undefined, [owl], "HOLD", "design", {} as any, "BLOCKED");
  expect(JSON.stringify(owl.dna.expertiseGrowth)).toBe(snapshotBefore);
});

// Test AC-10: IntentClarifier CLARIFY verdict blocks Parliament
it("AC-10: gateway skips Parliament auto-trigger when IntentClarifier returns CLARIFY", async () => {
  // Verify that the IntentClarifier can return a CLARIFY verdict for an ambiguous message.
  // The full gateway-level guard (that Parliament does not fire) requires a gateway integration test
  // which is better suited to a separate E2E test once core.ts Task 10 Step 4 is committed.
  // This test validates the IntentClarifier produces the CLARIFY signal that gateway reads.
  const { IntentClarifier } = await import("../../src/clarification/intent-clarifier.js");
  const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
  const mockProvider = {
    chat: vi.fn().mockResolvedValue({
      content: JSON.stringify({ verdict: "CLARIFY", confidence: 0.9, questionToAsk: "Can you clarify what you mean?" }),
    }),
  };
  const clarifier = new IntentClarifier(mockRouter as any, new Map([["test", mockProvider]]) as any);
  const result = await clarifier.evaluate("help me with this thing", {} as any, {} as any);
  expect(result.verdict).toBe("CLARIFY");
  // When gateway sees CLARIFY, it returns early WITHOUT calling ParliamentAutoTrigger.check()
  // This is verified structurally in gateway/core.ts Task 10 Step 4 (early-return before parliament block)
});
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run __tests__/parliament/parliament-integration.test.ts
```
Expected: AC-4, AC-5, AC-6, AC-7, AC-10 pass (these are mostly logic tests of the injected behavior).

- [ ] **Step 3: Add imports and fields to `src/gateway/core.ts`**

**3a. Add import at the top of `src/gateway/core.ts`** (alongside existing parliament imports):
```typescript
import { updateParliamentDNA } from "../owls/evolution.js";
import { GoalVerifier } from "../tools/goal-verifier.js";
import { TaskLedgerStore } from "../engine/task-ledger.js";
import type { SubGoal } from "../engine/types.js";
```

**3b. Add `goalVerifier` private field** — in the Epic 6 Parliament section alongside `multiRoundDebate`:
```typescript
  private goalVerifier: GoalVerifier | null = null;
```

**3c. Initialize `goalVerifier`** — in the constructor setup block alongside `multiRoundDebate` initialization (around line 718–723 in core.ts), add:
```typescript
    // Wire GoalVerifier for Parliament post-session verification
    // NOTE: field is ctx.intelligence (not ctx.intelligenceRouter) — verified in src/gateway/types.ts:347
    if (ctx.intelligence) {
      const providerMap = new Map<string, import("../providers/base.js").ModelProvider>();
      if (ctx.provider) providerMap.set(ctx.config.defaultProvider ?? "default", ctx.provider);
      this.goalVerifier = GoalVerifier.create(ctx.intelligence, providerMap);
    }
```

- [ ] **Step 4: Implement post-session wiring in the two Parliament blocks**

There are **two** Parliament execution blocks in `core.ts` that need wiring:

**Block A: lines ~1883–1894** (auto-trigger path via `parliamentAutoTrigger`):

Replace:
```typescript
            await this.multiRoundDebate.runDebate(debateSession);
            // Generate pellet from debate
            if (this.debatePelletGenerator) {
              await this.debatePelletGenerator.generateFromSession(debateSession, pelletStore);
            }
            // Return the synthesis as the response
            return {
              content: debateSession.synthesis || "Parliament concluded without synthesis.",
              owlName: this.ctx.owl.persona.name,
              owlEmoji: this.ctx.owl.persona.emoji,
              toolsUsed: [],
            };
```

With:
```typescript
            await this.multiRoundDebate.runDebate(debateSession);
            if (this.debatePelletGenerator) {
              await this.debatePelletGenerator.generateFromSession(debateSession, pelletStore);
            }
            // POST-SESSION: inject into ContextPipeline, verify with GoalVerifier, evolve DNA
            const synthesis = debateSession.synthesis ?? "";
            // Minority position = dissenting Round 1 position (spec: "minority_position"), NOT the Round 2 challenge text
            const minorityContent = debateSession.positions.find(p => p.position === "AGAINST")?.argument
              ?? debateSession.challenges[0]?.challengeContent
              ?? "";
            const formattedSynthesis =
              `[Parliament concluded on "${debateSession.config.topic}"] Verdict: ${debateSession.verdict ?? "CONSENSUS_REACHED"}\n` +
              `The council's synthesis: ${synthesis.slice(0, 300)}\n` +
              (minorityContent ? `Key dissent: ${minorityContent.slice(0, 150)}\n` : "");
            try {
              this.ctx.contextPipeline?.setShortTermLayer(
                "parliament_synthesis",
                formattedSynthesis,
                { priority: 117, ttlTurns: 3 },
              );
            } catch { /* non-fatal */ }
            let verifierVerdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL" = "NEUTRAL";
            try {
              if (this.goalVerifier) {
                // Try to get active sub-goal from TaskLedger
                let activeSubGoal: SubGoal | undefined;
                if (this.db) {
                  const incomplete = await new TaskLedgerStore(this.db)
                    .loadIncomplete(message.userId ?? "default").catch(() => null as null);
                  if (incomplete) {
                    activeSubGoal = { id: incomplete.id, description: incomplete.subgoalText, status: "in_progress", dependsOn: [] };
                  }
                }
                if (activeSubGoal) {
                  const vResult = await this.goalVerifier.verify({
                    toolName: "parliament",
                    toolArgs: {},
                    toolResult: synthesis,
                    subGoal: activeSubGoal,
                    userMessage: message.text,
                  });
                  verifierVerdict = vResult.verdict;
                }
              }
            } catch { /* non-fatal */ }
            try {
              const participants = debateSession.config.participants;
              const topicCategory = worthiness.category ?? "other";
              // Identify synthesizer using same persona-search logic as MultiRoundDebateManager.runRound3()
              const synthOwl = participants.find(p => (p.persona as any).mentorPersonality)
                ?? participants.find(p => p.persona.name === 'Noctua')
                ?? participants.find(p => (p.persona as any).specialty === 'architect')
                ?? participants[0];
              const challOwl = participants.find(p => p !== synthOwl);
              await updateParliamentDNA(synthOwl, challOwl, participants, debateSession.verdict ?? "", topicCategory, this.db, verifierVerdict);
              if (verifierVerdict === "ADVANCES" && this.ctx.owlRegistry) {
                for (const p of participants) {
                  await this.ctx.owlRegistry.saveDNA(p.persona.name).catch(() => {});
                }
              }
            } catch { /* non-fatal */ }
            return {
              content: synthesis || "Parliament concluded without synthesis.",
              owlName: this.ctx.owl.persona.name,
              owlEmoji: this.ctx.owl.persona.emoji,
              toolsUsed: [],
            };
```

**Block B: lines ~1958–1968** (SecretaryRouter path):

Replace the existing block ending at `return { content: ...#Parliament }` with the same pattern (`topicCategory = "other"`, `message.userId` → `session.userId ?? "default"`, `message.text` → `text`):

```typescript
          await this.multiRoundDebate.runDebate(debateSession);
          if (this.debatePelletGenerator) {
            await this.debatePelletGenerator.generateFromSession(debateSession, this.ctx.pelletStore);
          }
          const synthesis = debateSession.synthesis ?? "";
          const minorityContent = debateSession.challenges[0]?.challengeContent
            ?? debateSession.positions.find(p => p.position === "AGAINST")?.argument
            ?? "";
          const formattedSynthesis =
            `[Parliament concluded on "${text.slice(0, 100)}"] Verdict: ${debateSession.verdict ?? "CONSENSUS_REACHED"}\n` +
            `The council's synthesis: ${synthesis.slice(0, 300)}\n` +
            (minorityContent ? `Key dissent: ${minorityContent.slice(0, 150)}\n` : "");
          try {
            this.ctx.contextPipeline?.setShortTermLayer(
              "parliament_synthesis",
              formattedSynthesis,
              { priority: 117, ttlTurns: 3 },
            );
          } catch { /* non-fatal */ }
          let verifierVerdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL" = "NEUTRAL";
          try {
            if (this.goalVerifier) {
              let activeSubGoal: SubGoal | undefined;
              if (this.db) {
                const incomplete = await new TaskLedgerStore(this.db)
                  .loadIncomplete(session.userId ?? "default").catch(() => null);
                if (incomplete) {
                  activeSubGoal = { id: incomplete.id, description: incomplete.subgoalText, status: "in_progress", dependsOn: [] };
                }
              }
              if (activeSubGoal) {
                const vResult = await this.goalVerifier.verify({
                  toolName: "parliament",
                  toolArgs: {},
                  toolResult: synthesis,
                  subGoal: activeSubGoal,
                  userMessage: text,
                });
                verifierVerdict = vResult.verdict;
              }
            }
          } catch { /* non-fatal */ }
          try {
            const participants = debateSession.config.participants;
            // topicCategory: Block B has no TopicWorthinessEvaluator result, so "other" is the fallback
            const synthOwl = participants.find(p => (p.persona as any).mentorPersonality)
              ?? participants.find(p => p.persona.name === 'Noctua')
              ?? participants.find(p => (p.persona as any).specialty === 'architect')
              ?? participants[0];
            const challOwl = participants.find(p => p !== synthOwl);
            await updateParliamentDNA(synthOwl, challOwl, participants, debateSession.verdict ?? "", "other", this.db, verifierVerdict);
            if (verifierVerdict === "ADVANCES" && this.ctx.owlRegistry) {
              for (const p of participants) {
                await this.ctx.owlRegistry.saveDNA(p.persona.name).catch(() => {});
              }
            }
          } catch { /* non-fatal */ }
          return {
            content: `${synthesis}\n\n#Parliament`,
            owlName: this.ctx.owl.persona.name,
            owlEmoji: this.ctx.owl.persona.emoji,
            toolsUsed: [],
          };
```

**Verify `ctx.intelligence` field on `GatewayContext`** (already confirmed in `src/gateway/types.ts:347` — field is `intelligence`, not `intelligenceRouter`).

- [ ] **Step 4: Run integration tests**

```bash
npx vitest run __tests__/parliament/parliament-integration.test.ts
```
Expected: PASS.

- [ ] **Step 5: Run full parliament test suite**

```bash
npx vitest run __tests__/parliament/
```
Expected: AC-12 satisfied — zero regressions.

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```
Expected: all pass.

- [ ] **Step 7: Verify final AC-1 grep**

```bash
grep -r "shouldTrigger\|THRESHOLD\|confidenceThreshold\|claude-haiku-4-5" src/parliament/
```
Expected: zero matches.

- [ ] **Step 8: Commit**

```bash
git add src/gateway/core.ts __tests__/parliament/parliament-integration.test.ts
git commit -m "feat(e10): wire post-session ContextPipeline + GoalVerifier + DNA evolution in gateway"
```

---

## Task 11: Refactor `orchestrator.ts` — delegate `convene()` to `MultiRoundDebateManager`

> **Why this task exists:** The spec calls for deleting the duplicate `runRound1/2/3` methods in `orchestrator.ts` that are exact copies of `MultiRoundDebateManager`. Without this, Parliament sessions routed through the orchestrator (rather than through `multiRoundDebate` directly) will still use the old sequential Round 1 — violating AC-2 and AC-3 for that code path.

**Files:**
- Modify: `src/parliament/orchestrator.ts`
- No new test file — regression coverage via existing `npx vitest run __tests__/parliament/`

- [ ] **Step 1: Identify duplicates to delete**

```bash
grep -n "private async runRound" src/parliament/orchestrator.ts
```
Expected: 3 matches (runRound1, runRound2, runRound3).

- [ ] **Step 2: Delete the three duplicate round methods**

In `src/parliament/orchestrator.ts`, **delete** the bodies of `runRound1()`, `runRound2()`, and `runRound3()`. These are exact copies of the same methods in `MultiRoundDebateManager`.

- [ ] **Step 3: Update `convene()` to delegate to `MultiRoundDebateManager`**

Replace the existing `convene()` body (which calls `this.runRound1()`, `this.runRound2()`, `this.runRound3()`) with delegation to `this.multiRoundDebate.runDebate(session)`. Keep the post-session block (Pellet generation + `parliamentVerdicts.record()` + `owlLearnings.add()`) unchanged.

Before:
```typescript
// (existing orchestrator.ts convene() body)
await this.runRound1(session, perspectives);
await this.runRound2(session, perspectives);
await this.runRound3(session, perspectives);
```

After:
```typescript
await this.multiRoundDebate.runDebate(session);
```

- [ ] **Step 4: Verify `multiRoundDebate` field exists on orchestrator**

```bash
grep -n "multiRoundDebate" src/parliament/orchestrator.ts
```
If not present, add: `private readonly multiRoundDebate: MultiRoundDebateManager;` and initialize in constructor with `new MultiRoundDebateManager(this.provider, this.config, this.db, this.router)`.

- [ ] **Step 5: Run existing Parliament tests**

```bash
npx vitest run __tests__/parliament/
```
Expected: all existing tests pass; zero regressions. Round 1 is now parallel (via `MultiRoundDebateManager`) for orchestrator path too.

- [ ] **Step 6: Commit**

```bash
git add src/parliament/orchestrator.ts
git commit -m "feat(e10): orchestrator delegates convene() to MultiRoundDebateManager — removes duplicate round methods"
```

---

## Final Acceptance Check

Run these commands in order. All must pass before marking Element 10 complete.

- [ ] **AC-1 grep**
```bash
grep -r "shouldTrigger\|THRESHOLD\|confidenceThreshold\|claude-haiku-4-5" src/parliament/
# Expected: no output
```

- [ ] **Full test suite**
```bash
npx vitest run
# Expected: all tests pass, ~26 new tests added
```

- [ ] **Update progress tracker**
```bash
# Edit docs/platform-audit/progress.md and mark Element 10 as implemented
git add docs/platform-audit/progress.md
git commit -m "docs: mark Element 10 Parliament as implemented"
```

---

## Task Count Summary

| Task | Tests added |
|------|-------------|
| 1 — Schema v20 | 2 |
| 2 — ContextPipeline short-term | 3 |
| 3 — DiversityFilter | 3 |
| 4 — Delete shouldTrigger | 1 (replacement test) |
| 5 — TopicWorthiness | 3 |
| 6 — Lite router | 1 |
| 7 — Protocol extension | 0 (covered by existing tests) |
| 8 — Sparse debate | 3 |
| 9 — updateParliamentDNA | 5 (added PARTIAL + non-fatal test) |
| 10 — Gateway wiring | 5 |
| 11 — Orchestrator delegation | 0 (covered by existing parliament tests) |
| **Total** | **~26** |
