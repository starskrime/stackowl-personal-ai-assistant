# Element 14 — Evolution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Evolution Engine so DNA mutations actually reach prompts, dead code is purged, mid-session failures trigger adaptation, and trait decay is correctly calibrated.

**Architecture:** Three modified files (evolution.ts, post-processor.ts, core.ts), eleven deleted files (7 dead src + 4 dead tests), 0 new files. All changes are inline — no new abstractions, no new primitives. Total net: −789 LOC.

**Tech Stack:** TypeScript, Vitest, SQLite (better-sqlite3), existing `db.owlLearnings`, `db.trajectories`, `PostProcessor.enqueueJob`, `OwlEvolutionEngine.evolve`.

---

## File Structure

**Files deleted (D1):**
- `src/evolution/mutation-engine.ts` — hardcoded keyword strings, no callers outside cluster
- `src/evolution/batch-manager.ts` — never wired
- `src/evolution/outcome-recorder.ts` — queries non-existent `outcome_journal`
- `src/evolution/trend-analyzer.ts` — no callers
- `src/evolution/optimize.ts` — dead APO draft
- `src/evolution/index.ts` — re-exports dead cluster
- `src/evolution/types.ts` — types for dead cluster only
- `__tests__/evolution/mutation-engine.test.ts` — tests dead code
- `__tests__/evolution/batch-manager.test.ts` — tests dead code
- `__tests__/evolution/outcome-recorder.test.ts` — tests dead code
- `__tests__/evolution/trend-analyzer.test.ts` — tests dead code

**Files modified:**
- `src/gateway/core.ts` — add `ReflexionEngine` construction before `PostProcessor` (D2, ~3 lines)
- `src/gateway/handlers/post-processor.ts` — add mid-session evolution trigger at end of `process()` (D4, ~18 lines)
- `src/owls/evolution.ts` — add signal digest (D5), EMA blending (D6), decay rate correction (D6, ~20 lines total)

**New test files:**
- `__tests__/evolution/reflexion-wiring.test.ts` — D2 unit test
- `__tests__/evolution/mid-session-trigger.test.ts` — D4 unit test
- `__tests__/evolution/signal-digest.test.ts` — D5 unit test
- `__tests__/evolution/ema-decay.test.ts` — D6 unit test

---

### Task 1: Delete dead code cluster — D1

**Files:**
- Delete: `src/evolution/mutation-engine.ts`, `batch-manager.ts`, `outcome-recorder.ts`, `trend-analyzer.ts`, `optimize.ts`, `index.ts`, `types.ts`
- Delete: `__tests__/evolution/mutation-engine.test.ts`, `batch-manager.test.ts`, `outcome-recorder.test.ts`, `trend-analyzer.test.ts`

- [ ] **Step 1: Confirm zero callers in src/**

```bash
grep -r \
  "from.*evolution/mutation-engine\|from.*evolution/batch-manager\|from.*evolution/outcome-recorder\|from.*evolution/trend-analyzer\|from.*evolution/optimize\|from.*evolution/index\|from.*evolution/types" \
  src/
```

Expected: no output (zero matches).

- [ ] **Step 2: Delete 7 src files**

```bash
rm src/evolution/mutation-engine.ts \
   src/evolution/batch-manager.ts \
   src/evolution/outcome-recorder.ts \
   src/evolution/trend-analyzer.ts \
   src/evolution/optimize.ts \
   src/evolution/index.ts \
   src/evolution/types.ts
```

- [ ] **Step 3: Delete 4 test files**

```bash
rm __tests__/evolution/mutation-engine.test.ts \
   __tests__/evolution/batch-manager.test.ts \
   __tests__/evolution/outcome-recorder.test.ts \
   __tests__/evolution/trend-analyzer.test.ts
```

- [ ] **Step 4: Run TypeScript build to confirm no broken imports**

```bash
npm run build 2>&1 | head -30
```

Expected: build succeeds (exit 0), no errors referencing deleted files.

- [ ] **Step 5: Run the full test suite**

```bash
npx vitest run 2>&1 | tail -20
```

Expected: all tests pass. The 4 deleted test files are gone so their tests no longer run — overall count should decrease by the number of tests in those 4 files but no test failures.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(evolution): delete 808-LOC dead code cluster (D1) — mutation-engine, batch-manager, outcome-recorder, trend-analyzer, optimize, index, types"
```

---

### Task 2: Wire ReflexionEngine in core.ts — D2

**Files:**
- Modify: `src/gateway/core.ts` (add import + 3-line construction, before line ~385)
- Create: `__tests__/evolution/reflexion-wiring.test.ts`

**Context:** `src/evolution/reflexion.ts` exports `ReflexionEngine` with `reflectOnFailure()`. `post-processor.ts:601–628` calls `ctx.reflexionEngine.reflectOnFailure()` but `ctx.reflexionEngine` is never set in `core.ts`. `GatewayContext` declares `reflexionEngine?: ReflexionEngine` (imported from `../evolution/reflexion.js`). The `ReflexionEngine` constructor signature: `(provider: ModelProvider, sessionStore: SessionStore, pelletStore: PelletStore)`. All three are available on `ctx`.

- [ ] **Step 1: Write the failing test**

Create `__tests__/evolution/reflexion-wiring.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { ReflexionEngine } from "../../src/evolution/reflexion.js";
import type { ModelProvider } from "../../src/providers/base.js";

describe("ReflexionEngine — wiring contract", () => {
  it("can be constructed with (provider, sessionStore, pelletStore)", () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "{}", model: "test", usage: undefined }),
    } as unknown as ModelProvider;
    const mockSessionStore = { listSessions: vi.fn().mockResolvedValue([]) } as any;
    const mockPelletStore = { save: vi.fn().mockResolvedValue(undefined) } as any;

    const engine = new ReflexionEngine(mockProvider, mockSessionStore, mockPelletStore);
    expect(engine).toBeDefined();
    expect(typeof engine.reflectOnFailure).toBe("function");
    expect(typeof engine.dream).toBe("function");
  });

  it("reflectOnFailure accepts the exact context shape PostProcessor passes", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: '{"analysis":"test","heuristic":"Use absolute paths"}',
        model: "test",
        usage: undefined,
      }),
    } as unknown as ModelProvider;
    const mockSessionStore = { listSessions: vi.fn().mockResolvedValue([]) } as any;
    const mockPelletStore = { save: vi.fn().mockResolvedValue(undefined) } as any;

    const engine = new ReflexionEngine(mockProvider, mockSessionStore, mockPelletStore);

    await expect(
      engine.reflectOnFailure({
        userMessage: "list my files",
        toolsAttempted: "run_shell_command",
        reason: "loop_exhausted",
        sessionId: "sess-001",
      }),
    ).resolves.not.toThrow();

    expect(mockPelletStore.save).toHaveBeenCalledOnce();
    const savedPellet = mockPelletStore.save.mock.calls[0][0];
    expect(savedPellet.tags).toContain("reflexion");
    expect(savedPellet.content).toBe("Use absolute paths");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/reflexion-wiring.test.ts
```

Expected: both tests PASS (the constructor already exists — we're verifying the interface, not changing it). If they pass here, that's fine — move to step 3.

- [ ] **Step 3: Add import + construction in core.ts**

In `src/gateway/core.ts`, add the import near the existing evolution imports (around line 124):

```typescript
import { ReflexionEngine } from "../evolution/reflexion.js";
```

Then, just before the `this.postProcessor = new PostProcessor(...)` block (around line 383), add:

```typescript
    // Wire ReflexionEngine so PostProcessor's reflectOnFailure path is active.
    // ctx.reflexionEngine is typed as ReflexionEngine from evolution/reflexion.ts.
    if (!ctx.reflexionEngine && ctx.pelletStore) {
      ctx.reflexionEngine = new ReflexionEngine(ctx.provider, ctx.sessionStore, ctx.pelletStore);
    }
```

The full context around the insertion point (after line 382):

```typescript
    let intelligenceReflexion: IntelligenceReflexionEngine | undefined;
    if (ctx.db && ctx.provider) {
      const embedFn = async (text: string): Promise<number[]> => {
        try { return (await ctx.provider.embed(text)).embedding; } catch { return []; }
      };
      intelligenceReflexion = new IntelligenceReflexionEngine(ctx.db, ctx.provider, embedFn);
    }

    // Wire ReflexionEngine so PostProcessor's reflectOnFailure path is active.
    // ctx.reflexionEngine is typed as ReflexionEngine from evolution/reflexion.ts.
    if (!ctx.reflexionEngine && ctx.pelletStore) {
      ctx.reflexionEngine = new ReflexionEngine(ctx.provider, ctx.sessionStore, ctx.pelletStore);
    }

    this.postProcessor = new PostProcessor(
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/evolution/reflexion-wiring.test.ts
```

Expected: both tests PASS.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
npx vitest run 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/core.ts __tests__/evolution/reflexion-wiring.test.ts
git commit -m "feat(evolution): wire ReflexionEngine in core.ts so reflectOnFailure fires on loop exhaustion (D2)"
```

---

### Task 3: Mid-session evolution trigger — D4

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts` (add trigger block before closing `}` of `process()`, around line 692)
- Create: `__tests__/evolution/mid-session-trigger.test.ts`

**Context:** `process()` increments `messageCount` on every call. The trigger check fires when `messageCount % 5 === 0` (i.e. every 5th message). It reads `db.trajectories.getRecent(owlName, 10)`, computes average reward, and enqueues "mid-session-evolution" if avg < −0.2 AND `hoursSinceEvolved > 2`. `enqueueJob` is private but calls `taskQueue.enqueue(name, wrappedFn, priority)` — spy on `taskQueue.enqueue` to observe job names.

- [ ] **Step 1: Write the failing tests**

Create `__tests__/evolution/mid-session-trigger.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { PostProcessor } from "../../src/gateway/handlers/post-processor.js";
import type { GatewayContext } from "../../src/gateway/types.js";
import type { TaskQueue } from "../../src/queue/task-queue.js";

function makeCtx(avgReward: number, lastEvolvedHoursAgo: number): GatewayContext {
  const lastEvolved = new Date(Date.now() - lastEvolvedHoursAgo * 60 * 60 * 1000).toISOString();
  const trajectories = Array.from({ length: 5 }, () => ({ reward: avgReward }));
  return {
    owl: {
      persona: { name: "test-owl" },
      dna: { lastEvolved },
    },
    db: {
      trajectories: {
        getRecent: vi.fn().mockReturnValue(trajectories),
        getSessionFailures: vi.fn().mockReturnValue([]),
      },
    },
    evolutionEngine: {
      evolve: vi.fn().mockResolvedValue(true),
    },
  } as unknown as GatewayContext;
}

function makeTaskQueue(): { mock: TaskQueue; enqueued: () => string[] } {
  const names: string[] = [];
  const mock = {
    enqueue: vi.fn((name: string) => { names.push(name); return "task-id"; }),
  } as unknown as TaskQueue;
  return { mock, enqueued: () => names };
}

describe("PostProcessor — mid-session evolution trigger (D4)", () => {
  it("enqueues mid-session-evolution after 5 messages when avg reward < -0.2 and evolved > 2h ago", () => {
    const ctx = makeCtx(-0.5, 3); // avg reward -0.5, evolved 3 hours ago
    const { mock: taskQueue, enqueued } = makeTaskQueue();
    const processor = new PostProcessor(ctx, taskQueue, null, null, null, null);

    const messages = [{ role: "user" as const, content: "test" }];
    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(enqueued()).toContain("mid-session-evolution");
  });

  it("does NOT enqueue mid-session-evolution when avg reward >= -0.2", () => {
    const ctx = makeCtx(0.1, 3); // avg reward positive, evolved 3h ago
    const { mock: taskQueue, enqueued } = makeTaskQueue();
    const processor = new PostProcessor(ctx, taskQueue, null, null, null, null);

    const messages = [{ role: "user" as const, content: "test" }];
    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(enqueued()).not.toContain("mid-session-evolution");
  });

  it("does NOT enqueue mid-session-evolution when evolved < 2h ago", () => {
    const ctx = makeCtx(-0.5, 1); // avg reward -0.5, but evolved only 1h ago
    const { mock: taskQueue, enqueued } = makeTaskQueue();
    const processor = new PostProcessor(ctx, taskQueue, null, null, null, null);

    const messages = [{ role: "user" as const, content: "test" }];
    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(enqueued()).not.toContain("mid-session-evolution");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/mid-session-trigger.test.ts
```

Expected: FAIL — "mid-session-evolution" is never enqueued because the trigger code doesn't exist yet.

- [ ] **Step 3: Add the trigger block in post-processor.ts**

In `src/gateway/handlers/post-processor.ts`, find the end of `process()`. The last block before the closing `}` of `process()` is the failure critique block:

```typescript
    // ── Failure critique: BLOCKED/PARTIAL → owl_learnings ──────────
    if (this.ctx.db && sessionId) {
      ...
    }
  }  // <-- this is the closing brace of process()
```

Insert the mid-session trigger BEFORE the closing `}` of `process()` (after the failure critique block):

```typescript
    // ── Mid-session evolution: adapt during sustained failure streaks ──────────
    // Fires every 5 messages when recent trajectory reward is below threshold
    // and the owl hasn't evolved in the last 2 hours. Rate-limited to prevent
    // cascading evolution runs within a single session.
    if (this.ctx.evolutionEngine && this.ctx.db && this.messageCount % 5 === 0) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      const recent = this.ctx.db.trajectories.getRecent(owlName, 10);
      if (recent.length >= 5) {
        const avgReward = recent.reduce((s: number, t: { reward: number }) => s + t.reward, 0) / recent.length;
        const lastEvolved = this.ctx.owl.dna.lastEvolved
          ? new Date(this.ctx.owl.dna.lastEvolved).getTime()
          : 0;
        const hoursSinceEvolved = (Date.now() - lastEvolved) / (1000 * 60 * 60);

        if (avgReward < -0.2 && hoursSinceEvolved > 2) {
          this.enqueueJob("mid-session-evolution", "background", async () => {
            await this.ctx.evolutionEngine!.evolve(owlName);
            log.engine.info(
              `[PostProcessor:mid-session-evolution] avg_reward=${avgReward.toFixed(2)} triggered evolution for ${owlName}`,
            );
          });
        }
      }
    }
  }
```

The complete end of `process()` after this edit (showing the last two blocks and closing brace):

```typescript
    // ── Failure critique: BLOCKED/PARTIAL → owl_learnings ──────────
    if (this.ctx.db && sessionId) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      this.enqueueJob("learning-failure-critique", "background", async () => {
        const failedTurns =
          this.ctx.db!.trajectories.getSessionFailures(sessionId!) ?? [];
        if (failedTurns.length === 0) return;
        // ... existing critique code ...
      });
    }

    // ── Mid-session evolution: adapt during sustained failure streaks ──────────
    if (this.ctx.evolutionEngine && this.ctx.db && this.messageCount % 5 === 0) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      const recent = this.ctx.db.trajectories.getRecent(owlName, 10);
      if (recent.length >= 5) {
        const avgReward = recent.reduce((s: number, t: { reward: number }) => s + t.reward, 0) / recent.length;
        const lastEvolved = this.ctx.owl.dna.lastEvolved
          ? new Date(this.ctx.owl.dna.lastEvolved).getTime()
          : 0;
        const hoursSinceEvolved = (Date.now() - lastEvolved) / (1000 * 60 * 60);

        if (avgReward < -0.2 && hoursSinceEvolved > 2) {
          this.enqueueJob("mid-session-evolution", "background", async () => {
            await this.ctx.evolutionEngine!.evolve(owlName);
            log.engine.info(
              `[PostProcessor:mid-session-evolution] avg_reward=${avgReward.toFixed(2)} triggered evolution for ${owlName}`,
            );
          });
        }
      }
    }
  }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/evolution/mid-session-trigger.test.ts
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
npx vitest run 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/evolution/mid-session-trigger.test.ts
git commit -m "feat(evolution): add mid-session evolution trigger in PostProcessor — fires on avg_reward < -0.2 with 2h cooldown (D4)"
```

---

### Task 4: Signal digest in evolve() — D5

**Files:**
- Modify: `src/owls/evolution.ts` (add learningsSection block after trajectory section, add to prompt)
- Create: `__tests__/evolution/signal-digest.test.ts`

**Context:** `evolve()` builds `profileSection`, `performanceSection`, `trajectorySection`, `memorySection` and concatenates them into the LLM prompt. The `db.owlLearnings.getForOwlSorted(owlName)` method already exists at `db.ts:2221` — returns `string[]` sorted failure-first, confidence desc, limit 6. We inject the top 5 as `learningsSection` AFTER `performanceSection` and BEFORE `trajectorySection` in the prompt. The test spies on `provider.chat` to verify the prompt contains "RECENT LEARNINGS".

- [ ] **Step 1: Write the failing test**

Create `__tests__/evolution/signal-digest.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { OwlEvolutionEngine } from "../../src/owls/evolution.js";
import type { ModelProvider } from "../../src/providers/base.js";

function makeMockProvider(responseContent: string): ModelProvider {
  return {
    chat: vi.fn().mockResolvedValue({
      content: responseContent,
      model: "test",
      usage: undefined,
    }),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
  } as unknown as ModelProvider;
}

function makeOwlRegistry(owl: any) {
  return {
    get: vi.fn().mockReturnValue(owl),
    saveDNA: vi.fn().mockResolvedValue(undefined),
  } as any;
}

function makeSessionStore(messages: any[] = []) {
  return {
    listSessions: vi.fn().mockResolvedValue([
      {
        id: "sess-001",
        metadata: { owlName: "test-owl" },
        messages: [
          { role: "user", content: "hello" },
          { role: "assistant", content: "hi" },
          { role: "user", content: "help" },
          { role: "assistant", content: "ok" },
        ],
      },
    ]),
  } as any;
}

describe("OwlEvolutionEngine — signal digest (D5)", () => {
  it("injects RECENT LEARNINGS from owl_learnings into the evolve prompt", async () => {
    const mockProvider = makeMockProvider(JSON.stringify({
      newPreferences: {},
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: { adviceAccepted: false },
      promptRules: [],
      evolutionReasoning: "no change needed",
    }));

    const owl = {
      persona: { name: "test-owl", emoji: "🦉" },
      dna: {
        generation: 1,
        lastEvolved: null,
        learnedPreferences: {},
        expertiseGrowth: {},
        evolvedTraits: { verbosity: "balanced", challengeLevel: "medium" },
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0.5, challengesGiven: 0 },
        promptSections: [],
        evolutionLog: [],
      },
    };

    const mockDb = {
      owlLearnings: {
        getForOwlSorted: vi.fn().mockReturnValue([
          "Always use absolute paths when calling file tools.",
          "Don't suggest python unless user explicitly asked for it.",
          "For shell errors: read the full stderr before retrying.",
        ]),
      },
      owlPerf: {
        getSummary: vi.fn().mockReturnValue({ totalInteractions: 0 }),
      },
      trajectories: {
        getRecent: vi.fn().mockReturnValue([]),
        getLowReward: vi.fn().mockReturnValue([]),
      },
    } as any;

    const engine = new OwlEvolutionEngine(
      mockProvider,
      { owlDna: { decayRatePerWeek: 0.1 } } as any,
      makeSessionStore(),
      makeOwlRegistry(owl),
      undefined, // no userProfileProvider
      undefined, // no episodicMemory
      mockDb,
    );

    await engine.evolve("test-owl");

    // The prompt sent to provider.chat must contain RECENT LEARNINGS
    const chatCalls = (mockProvider.chat as ReturnType<typeof vi.fn>).mock.calls;
    const promptCall = chatCalls.find((c: any[]) =>
      Array.isArray(c[0]) && c[0].some((m: any) =>
        typeof m.content === "string" && m.content.includes("RECENT LEARNINGS"),
      ),
    );
    expect(promptCall).toBeDefined();

    // All 3 learnings must appear in the prompt
    const allPromptText = promptCall![0].map((m: any) => m.content).join("\n");
    expect(allPromptText).toContain("Always use absolute paths");
    expect(allPromptText).toContain("Don't suggest python");
    expect(allPromptText).toContain("For shell errors");
  });

  it("skips learnings section gracefully when db.owlLearnings returns empty", async () => {
    const mockProvider = makeMockProvider(JSON.stringify({
      newPreferences: {},
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: { adviceAccepted: false },
      promptRules: [],
      evolutionReasoning: "no change needed",
    }));

    const owl = {
      persona: { name: "test-owl", emoji: "🦉" },
      dna: {
        generation: 1,
        lastEvolved: null,
        learnedPreferences: {},
        expertiseGrowth: {},
        evolvedTraits: { verbosity: "balanced", challengeLevel: "medium" },
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0.5, challengesGiven: 0 },
        promptSections: [],
        evolutionLog: [],
      },
    };

    const mockDb = {
      owlLearnings: {
        getForOwlSorted: vi.fn().mockReturnValue([]), // empty
      },
      owlPerf: { getSummary: vi.fn().mockReturnValue({ totalInteractions: 0 }) },
      trajectories: {
        getRecent: vi.fn().mockReturnValue([]),
        getLowReward: vi.fn().mockReturnValue([]),
      },
    } as any;

    const engine = new OwlEvolutionEngine(
      mockProvider,
      { owlDna: { decayRatePerWeek: 0.1 } } as any,
      makeSessionStore(),
      makeOwlRegistry(owl),
      undefined,
      undefined,
      mockDb,
    );

    // Must not throw even with empty learnings
    await expect(engine.evolve("test-owl")).resolves.not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/signal-digest.test.ts
```

Expected: FAIL — first test fails because `provider.chat` prompt does not contain "RECENT LEARNINGS" yet.

- [ ] **Step 3: Add learningsSection to evolve() in evolution.ts**

In `src/owls/evolution.ts`, add the learningsSection block AFTER the `trajectorySection` block (after the `try/catch` that ends around line 327) and BEFORE the `const prompt = ...` at line 329:

```typescript
    // ── Signal digest: top-ranked owl learnings from failure critique ─
    let learningsSection = "";
    if (this.db) {
      try {
        const learnings = this.db.owlLearnings.getForOwlSorted(owlName);
        if (learnings.length > 0) {
          learningsSection =
            `\nRECENT LEARNINGS (failure-first, ranked by confidence):\n` +
            learnings.slice(0, 5).map((l: string, i: number) => `${i + 1}. ${l}`).join("\n") +
            `\nApply these learnings when proposing trait mutations.\n\n`;
        }
      } catch {
        // Non-fatal — learnings may not exist yet
      }
    }
```

Then update the `const prompt = ...` concatenation (currently at line 329) to include `learningsSection` after `performanceSection`:

Change:
```typescript
    const prompt =
      `You are the subconscious of "${owl.persona.name}", analyzing a recent conversation to learn and evolve.\n\n` +
      `CURRENT DNA STATE:\n${JSON.stringify(owl.dna, null, 2)}\n\n` +
      profileSection +
      performanceSection +
      trajectorySection +
      memorySection +
```

To:
```typescript
    const prompt =
      `You are the subconscious of "${owl.persona.name}", analyzing a recent conversation to learn and evolve.\n\n` +
      `CURRENT DNA STATE:\n${JSON.stringify(owl.dna, null, 2)}\n\n` +
      profileSection +
      performanceSection +
      learningsSection +
      trajectorySection +
      memorySection +
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/evolution/signal-digest.test.ts
```

Expected: both tests PASS.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
npx vitest run 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/owls/evolution.ts __tests__/evolution/signal-digest.test.ts
git commit -m "feat(evolution): inject top-5 owl_learnings as RECENT LEARNINGS in evolve() prompt (D5)"
```

---

### Task 5: Decay rate correction + EMA blending — D6

**Files:**
- Modify: `src/owls/evolution.ts` (change 0.01→0.1, add EMA to newPreferences and expertiseGrowth)
- Create: `__tests__/evolution/ema-decay.test.ts`

**Context:**
- `applyDecayIfNeeded()` at `evolution.ts:60` uses `decayRatePerWeek ?? 0.01` — change fallback to `0.1`.
- `newPreferences` mutation loop at `evolution.ts:400`: currently `owl.dna.learnedPreferences[k] = Number(v)` — change to EMA blend.
- `expertiseGrowth` mutation loop at `evolution.ts:437`: currently `current + Number(amount)` — change to EMA blend.
- EMA formula: `newVal = 0.7 * proposed + 0.3 * current`.
- The clamping loop at lines 528–533 (learnedPreferences) and 534–539 (expertiseGrowth) remains unchanged — it runs after EMA blending and keeps values in bounds.

- [ ] **Step 1: Write the failing tests**

Create `__tests__/evolution/ema-decay.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { OwlEvolutionEngine } from "../../src/owls/evolution.js";
import type { ModelProvider } from "../../src/providers/base.js";

function makeMinimalEngine(
  providerResponse: string,
  owlLearningsData: string[] = [],
) {
  const owl = {
    persona: { name: "test-owl", emoji: "🦉" },
    dna: {
      generation: 1,
      lastEvolved: null,
      learnedPreferences: { verbosity_preference: 0.4 },
      expertiseGrowth: { typescript: 0.5 },
      evolvedTraits: { verbosity: "balanced", challengeLevel: "medium" },
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0.5, challengesGiven: 0 },
      promptSections: [],
      evolutionLog: [],
    },
  };

  const mockProvider = {
    chat: vi.fn().mockResolvedValue({
      content: providerResponse,
      model: "test",
      usage: undefined,
    }),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
  } as unknown as ModelProvider;

  const mockSessionStore = {
    listSessions: vi.fn().mockResolvedValue([
      {
        id: "sess-001",
        metadata: { owlName: "test-owl" },
        messages: [
          { role: "user", content: "hello" },
          { role: "assistant", content: "hi" },
          { role: "user", content: "help me" },
          { role: "assistant", content: "sure" },
        ],
      },
    ]),
  } as any;

  const mockRegistry = {
    get: vi.fn().mockReturnValue(owl),
    saveDNA: vi.fn().mockResolvedValue(undefined),
  } as any;

  const mockDb = {
    owlLearnings: {
      getForOwlSorted: vi.fn().mockReturnValue(owlLearningsData),
    },
    owlPerf: { getSummary: vi.fn().mockReturnValue({ totalInteractions: 0 }) },
    trajectories: {
      getRecent: vi.fn().mockReturnValue([]),
      getLowReward: vi.fn().mockReturnValue([]),
    },
  } as any;

  return { engine: new OwlEvolutionEngine(
    mockProvider,
    {} as any, // no config — tests default decay rate via applyDecayIfNeeded separately
    mockSessionStore,
    mockRegistry,
    undefined,
    undefined,
    mockDb,
  ), owl, mockRegistry };
}

describe("OwlEvolutionEngine — EMA blending (D6)", () => {
  it("blends newPreferences with EMA β=0.7: new = 0.7*proposed + 0.3*current", async () => {
    const { engine, owl, mockRegistry } = makeMinimalEngine(JSON.stringify({
      newPreferences: { verbosity_preference: 1.0 }, // proposed = 1.0
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: { adviceAccepted: false },
      promptRules: [],
      evolutionReasoning: "user wants verbose responses",
    }));

    // current = 0.4 (set in makeMinimalEngine)
    // expected = 0.7 * 1.0 + 0.3 * 0.4 = 0.82
    await engine.evolve("test-owl");

    const savedOwl = mockRegistry.get.mock.results[0].value;
    expect(savedOwl.dna.learnedPreferences.verbosity_preference).toBeCloseTo(0.82, 2);
  });

  it("blends expertiseGrowth with EMA β=0.7: new = 0.7*(current+amount) + 0.3*current", async () => {
    const { engine, owl, mockRegistry } = makeMinimalEngine(JSON.stringify({
      newPreferences: {},
      traitAdjustments: {},
      expertiseGrowth: { typescript: 0.2 }, // amount = +0.2
      statsUpdate: { adviceAccepted: false },
      promptRules: [],
      evolutionReasoning: "typescript topic discussed",
    }));

    // current = 0.5, proposed = min(1.0, 0.5 + 0.2) = 0.7
    // expected = 0.7 * 0.7 + 0.3 * 0.5 = 0.49 + 0.15 = 0.64
    await engine.evolve("test-owl");

    const savedOwl = mockRegistry.get.mock.results[0].value;
    expect(savedOwl.dna.expertiseGrowth.typescript).toBeCloseTo(0.64, 2);
  });

  it("EMA smoothing prevents leap to extreme: proposed 1.0 from base 0.4 lands at 0.82 not 1.0", async () => {
    const { engine, owl, mockRegistry } = makeMinimalEngine(JSON.stringify({
      newPreferences: { verbosity_preference: 1.0 },
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: { adviceAccepted: false },
      promptRules: [],
      evolutionReasoning: "test extreme jump prevention",
    }));

    await engine.evolve("test-owl");

    const savedOwl = mockRegistry.get.mock.results[0].value;
    // Must NOT be 1.0 (no smoothing) and must NOT be 0.4 (no update)
    const val = savedOwl.dna.learnedPreferences.verbosity_preference;
    expect(val).toBeGreaterThan(0.4);
    expect(val).toBeLessThan(1.0);
  });
});

describe("OwlEvolutionEngine — decay rate default (D6)", () => {
  it("default decayRatePerWeek is 0.1 (not 0.01)", async () => {
    // applyDecayIfNeeded reads: this.config.owlDna?.decayRatePerWeek ?? DEFAULT
    // Verify the default by constructing with empty config and checking behavior
    const owl = {
      persona: { name: "decay-owl", emoji: "🦉" },
      dna: {
        generation: 1,
        lastEvolved: new Date(Date.now() - 14 * 24 * 60 * 60 * 1000).toISOString(), // 14 days ago
        learnedPreferences: { test_pref: 1.0 }, // starts at 1.0
        expertiseGrowth: {},
        evolvedTraits: { verbosity: "balanced", challengeLevel: "medium" },
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0.5, challengesGiven: 0 },
        promptSections: [],
        evolutionLog: [],
      },
    };

    const mockRegistry = {
      get: vi.fn().mockReturnValue(owl),
      saveDNA: vi.fn().mockResolvedValue(undefined),
    } as any;

    const engine = new OwlEvolutionEngine(
      {} as any, // provider not needed for applyDecayIfNeeded
      {}, // empty config — forces fallback to default
      {} as any,
      mockRegistry,
    );

    await engine.applyDecayIfNeeded("decay-owl");

    // With 14 days elapsed = 2 weeks, factor = decayRate * 2 weeks
    // At 0.1/week * 2 weeks = 0.2 factor
    // new = 1.0 + (0.5 - 1.0) * 0.2 = 1.0 - 0.1 = 0.9
    const stored = mockRegistry.get.mock.results[0].value;
    expect(stored.dna.learnedPreferences.test_pref).toBeCloseTo(0.9, 2);
    // Sanity: at old 0.01/week it would have been 0.99 — verify we're NOT getting that
    expect(stored.dna.learnedPreferences.test_pref).toBeLessThan(0.95);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/evolution/ema-decay.test.ts
```

Expected: 
- EMA tests FAIL — current code writes `Number(v)` directly (no blending), so `verbosity_preference` ends up at 1.0 not 0.82.
- Decay test FAIL — default rate is 0.01, not 0.1, so decay moves by 0.01 not 0.1.

- [ ] **Step 3: Apply decay rate + EMA changes in evolution.ts**

**Change 1:** In `src/owls/evolution.ts` at line 60, change the fallback from `0.01` to `0.1`:

```typescript
    const decayRate = this.config.owlDna?.decayRatePerWeek ?? 0.1;
```

**Change 2:** In the `newPreferences` mutation loop (currently at ~line 400), apply EMA blending:

Change:
```typescript
      if (mutations.newPreferences) {
        for (const [k, v] of Object.entries(mutations.newPreferences)) {
          owl.dna.learnedPreferences[k] = Number(v);
          logEntries.push(`Learned preference: ${k} = ${v}`);
          changed = true;
        }
      }
```

To:
```typescript
      if (mutations.newPreferences) {
        for (const [k, v] of Object.entries(mutations.newPreferences)) {
          const proposed = Number(v);
          const current = owl.dna.learnedPreferences[k] ?? 0.5;
          owl.dna.learnedPreferences[k] = 0.7 * proposed + 0.3 * current;
          logEntries.push(`Learned preference: ${k} = ${owl.dna.learnedPreferences[k].toFixed(3)} (EMA from ${current.toFixed(3)})`);
          changed = true;
        }
      }
```

**Change 3:** In the `expertiseGrowth` mutation loop (currently at ~line 434), apply EMA blending:

Change:
```typescript
      if (mutations.expertiseGrowth) {
        for (const [k, amount] of Object.entries(mutations.expertiseGrowth)) {
          const current = owl.dna.expertiseGrowth[k] || 0;
          owl.dna.expertiseGrowth[k] = Math.min(1.0, current + Number(amount));
          logEntries.push(`Grew expertise in ${k} (+${amount})`);
          changed = true;
        }
      }
```

To:
```typescript
      if (mutations.expertiseGrowth) {
        for (const [k, amount] of Object.entries(mutations.expertiseGrowth)) {
          const current = owl.dna.expertiseGrowth[k] || 0;
          const proposed = Math.min(1.0, current + Number(amount));
          owl.dna.expertiseGrowth[k] = 0.7 * proposed + 0.3 * current;
          logEntries.push(`Grew expertise in ${k} (+${amount}, EMA blend → ${owl.dna.expertiseGrowth[k].toFixed(3)})`);
          changed = true;
        }
      }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/evolution/ema-decay.test.ts
```

Expected: all 4 tests PASS.

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
npx vitest run 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/owls/evolution.ts __tests__/evolution/ema-decay.test.ts
git commit -m "feat(evolution): decay rate 0.01→0.1, EMA β=0.7 blending on learnedPreferences and expertiseGrowth (D6)"
```

---

## Self-Review

### Spec coverage

| Spec section | Task covering it |
|---|---|
| D1 — Delete 7 dead src files + 4 test files | Task 1 |
| D2 — Wire ctx.reflexionEngine in core.ts | Task 2 |
| D3 — No change (already wired) | Not implemented — correct |
| D4 — Mid-session evolution trigger | Task 3 |
| D5 — Signal digest (owl_learnings → evolve prompt) | Task 4 |
| D6 — Decay rate 0.01→0.1, EMA β=0.7 | Task 5 |
| G5 — outcome_journal confirmed resolved by D1 deletion | Covered by Task 1 |

### Type consistency

- `PostProcessor` constructor in D4 test: `new PostProcessor(ctx, taskQueue, null, null, null, null)` — matches the constructor signature `(ctx, taskQueue, eventBus, coordinator, anticipator, costTracker, innerLifeBridge?, intelligenceReflexion?, sleepConsolidator?)`. Six positional args + optional rest. ✅
- `OwlEvolutionEngine` constructor in D5/D6 tests: `(provider, config, sessionStore, owlRegistry, userProfileProvider?, episodicMemory?, db?)` — matches declaration at `evolution.ts:32–50`. ✅
- `ReflexionEngine` constructor in D2 test: `(provider, sessionStore, pelletStore)` — matches `reflexion.ts:23–27`. ✅
- `db.trajectories.getRecent(owlName, 10)` — returns `Trajectory[]` with `.reward: number`. Confirmed at `db.ts:2697`. ✅
- `db.owlLearnings.getForOwlSorted(owlName)` — returns `string[]`. Confirmed at `db.ts:2221`. ✅

### Placeholder scan

No TBD, TODO, or incomplete sections found. All code blocks are complete.
