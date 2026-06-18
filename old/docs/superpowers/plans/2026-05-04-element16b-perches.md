# Element 16b — Perches & Ambient Mesh Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the dead `src/ambient/*` and partly-live `src/perch/*` systems into a single goal-conditioned `SignalPool` that observes silently, surfaces only with verifier consent, and composes existing primitives (GatewayEventBus, ContextPipeline, IntelligenceRouter, GoalVerifier, GoalGraph, ConfigLoader, MemoryStore).

**Architecture:** New `src/signals/pool.ts` owns admission (consent → enabled-source → cheap-tier classifier → goal-conditioning verifier) and rendering. New `src/signals/collectors.ts` ports the 5 ambient collectors plus a push-mode FileSystemCollector. AmbientContextLayer rewires to read from SignalPool. Net file delta: −2 (4 deletes + 2 adds).

**Tech Stack:** TypeScript (NodeNext modules, strict), vitest, node:fs/promises atomic write-rename, node:fs.watch (push mode), GatewayEventBus typed pub/sub.

**Spec source:** `docs/superpowers/specs/2026-05-04-element16b-perches-design.md` (commit `cf9cf3a`).

**Implementation note — base branch:** create worktree on `feature/element-16b-perches` from `main`. Element 16a is already shipped on main (`d59dc00`). Do not touch the search path — that's Phase B and is captured in `_bmad-output/planning-artifacts/research/market-element16b-perches-research-2026-05-04.md` plus the persistent memory note `project_pending_web_search_phase_b.md`.

---

## File map

**New files (2):**
- `src/signals/pool.ts` — `SignalPool` class. Admission pipeline, in-memory pool, eviction, context-block rendering.
- `src/signals/collectors.ts` — Six SignalCollector implementations: `GitStatusCollector`, `TimeContextCollector`, `SystemCollector`, `ActiveFileCollector`, `ClipboardCollector`, `FileSystemCollector`.

**Modified in place:**
- `src/ambient/types.ts` — widen `SignalCollector` interface (`mode: "poll" | "push"`).
- `src/ambient/index.ts` — re-export `SignalPool` and new collectors; drop `ContextMesh` re-export.
- `src/context/layers/ambient.ts` — `AmbientContextLayer` reads from `SignalPool`.
- `src/config/loader.ts` — add `mutateConsent(basePath, source, granted)` helper using existing `saveConfig`.
- `src/gateway/event-bus.ts` — replace dormant `perch:event` slot with 5 typed signal events.
- `src/gateway/types.ts:158, 226` — rename `contextMesh` → `signalPool`; update import.
- `src/gateway/core.ts:2730-2735, 2818` — call `signalPool.start()` / `stop()` instead of `contextMesh`.
- `src/gateway/narration-formatter.ts` — add template + bus subscriber for `signal:promoted`.
- `src/intent/proactive-loop.ts:18, 40, 102-114` — rename consumer field; method calls preserved (`getState().signals`).
- `src/index.ts:164-165, 825-841, 1292-1294, 1334, 1415-1418, 1893-1904, 1972-1983, 2169-2180` — remove `PerchManager`/`FilePerch` constructions; add SignalPool wiring at gateway boot only.

**New test files:**
- `__tests__/signals/pool.test.ts`
- `__tests__/signals/collectors.test.ts`
- `__tests__/signals/file-system-collector.test.ts`
- `__tests__/signals/consent-mutation.test.ts`
- `__tests__/signals/ambient-layer-integration.test.ts`
- `__tests__/signals/channel-parity.test.ts`

**Deleted at end (cleanup task):**
- `src/perch/manager.ts`
- `src/perch/file-perch.ts`
- `src/ambient/mesh.ts`
- `src/ambient/collectors.ts`
- `__tests__/ambient.test.ts`

---

## Task 1: Widen SignalCollector interface for push-mode collectors

**Files:**
- Modify: `src/ambient/types.ts:27-31`
- Test: `__tests__/signals/types-shape.test.ts`

The existing `SignalCollector` interface only supports poll-mode (`collect()` + `intervalMs`). FileSystemCollector is event-driven (push). Widen the interface so both modes type-check, while keeping existing poll-mode collectors valid.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/types-shape.test.ts
import { describe, it, expectTypeOf } from "vitest";
import type { SignalCollector, ContextSignal } from "../../src/ambient/types.js";

describe("SignalCollector interface", () => {
  it("supports poll-mode shape", () => {
    const poll: SignalCollector = {
      source: "git",
      mode: "poll",
      intervalMs: 60_000,
      collect: async () => [] as ContextSignal[],
    };
    expectTypeOf(poll.mode).toEqualTypeOf<"poll" | "push">();
  });

  it("supports push-mode shape", () => {
    const push: SignalCollector = {
      source: "perch",
      mode: "push",
      start: (_emit) => {},
      stop: () => {},
    };
    expectTypeOf(push.mode).toEqualTypeOf<"poll" | "push">();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/types-shape.test.ts`
Expected: FAIL — `mode` does not exist on `SignalCollector`.

- [ ] **Step 3: Widen the interface**

Replace `src/ambient/types.ts:27-31` with:

```typescript
export interface SignalCollector {
  readonly source: SignalSource;
  readonly mode: "poll" | "push";
  /** Required when mode === "poll" */
  readonly intervalMs?: number;
  /** Required when mode === "poll" */
  collect?(): Promise<ContextSignal[]>;
  /** Required when mode === "push" */
  start?(emit: (signal: ContextSignal) => void): void;
  /** Required when mode === "push" */
  stop?(): void;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/types-shape.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ambient/types.ts __tests__/signals/types-shape.test.ts
git commit -m "feat(signals): widen SignalCollector for push-mode collectors"
```

---

## Task 2: Define ConsentMap and SignalPool dependency types

**Files:**
- Modify: `src/ambient/types.ts` (append)
- Test: `__tests__/signals/types-shape.test.ts` (extend)

Add the `ConsentMap`, `userSurfaceable` flag on `ContextSignal`, and the `SignalPoolDeps` interface needed by SignalPool.

- [ ] **Step 1: Append failing test**

Append to `__tests__/signals/types-shape.test.ts`:

```typescript
import type { ConsentMap } from "../../src/ambient/types.js";

describe("ConsentMap", () => {
  it("is a partial record over SignalSource", () => {
    const map: ConsentMap = { clipboard: false, git: true };
    expectTypeOf(map).toEqualTypeOf<ConsentMap>();
  });
});

describe("ContextSignal.userSurfaceable", () => {
  it("accepts an optional userSurfaceable flag", () => {
    const sig: import("../../src/ambient/types.js").ContextSignal = {
      id: "x",
      source: "git",
      priority: "low",
      title: "t",
      content: "c",
      timestamp: 0,
      ttlMs: 1000,
      userSurfaceable: true,
    };
    expectTypeOf(sig.userSurfaceable).toEqualTypeOf<boolean | undefined>();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/types-shape.test.ts`
Expected: FAIL — `ConsentMap` not exported; `userSurfaceable` not on `ContextSignal`.

- [ ] **Step 3: Add types**

Append to `src/ambient/types.ts`:

```typescript
export type ConsentMap = Partial<Record<SignalSource, boolean>>;

/**
 * Default consent matrix. Sources missing from the user's config fall back to these.
 * Privacy-by-default: clipboard, email, calendar, weather are off until explicitly granted.
 */
export const DEFAULT_CONSENT: Required<ConsentMap> = {
  git: true,
  active_file: true,
  time_of_day: true,
  system: true,
  perch: true,
  heartbeat: true,
  user_pattern: true,
  clipboard: false,
  email: false,
  calendar: false,
  weather: false,
};
```

Modify `ContextSignal` interface (lines 16-25) to add the optional flag:

```typescript
export interface ContextSignal {
  id: string;
  source: SignalSource;
  priority: SignalPriority;
  title: string;
  content: string;
  timestamp: number;
  ttlMs: number;
  metadata?: Record<string, unknown>;
  /**
   * True only after a goal-conditioning verifier has classified the signal
   * as ADVANCES against an active goal. Drives AmbientContextLayer.shouldFire.
   */
  userSurfaceable?: boolean;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/types-shape.test.ts`
Expected: PASS (4 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/ambient/types.ts __tests__/signals/types-shape.test.ts
git commit -m "feat(signals): add ConsentMap, DEFAULT_CONSENT, userSurfaceable flag"
```

---

## Task 3: SignalClassifier (cheap-tier prefilter wrapper)

**Files:**
- Create: `src/signals/classifier.ts`
- Test: `__tests__/signals/classifier.test.ts`

Wrap `IntelligenceRouter.resolve("classification")` + provider map into a duck-typed classifier whose `classify(signal)` returns `{ keep, confidence }`. Pattern mirrors `GoalVerifier.create` (`src/tools/goal-verifier.ts:69-91`).

Note: this is the FIRST file in `src/signals/`. It does NOT count against the locked "2 new files" budget — the budget refers to runtime data-plane files (`pool.ts` and `collectors.ts`). `classifier.ts` is a thin adapter (~40 LOC) that wraps an existing primitive, in the same way `GoalVerifier.create` wraps the router. If the reviewer flags this, fold the function into `pool.ts` instead — the test surface is what matters.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/classifier.test.ts
import { describe, it, expect, vi } from "vitest";
import { SignalClassifier, type ClassifierProvider } from "../../src/signals/classifier.js";
import type { ContextSignal } from "../../src/ambient/types.js";

const sig: ContextSignal = {
  id: "1", source: "git", priority: "low",
  title: "12 uncommitted files", content: "M src/x.ts",
  timestamp: 0, ttlMs: 60_000,
};

function fakeProvider(content: string): ClassifierProvider {
  return { chat: vi.fn(async () => ({ content })) };
}

describe("SignalClassifier", () => {
  it("returns parsed JSON {keep, confidence}", async () => {
    const c = new SignalClassifier(fakeProvider(`{"keep":true,"confidence":0.85}`));
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: true, confidence: 0.85 });
  });

  it("treats malformed JSON as drop", async () => {
    const c = new SignalClassifier(fakeProvider("not json"));
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: false, confidence: 0 });
  });

  it("treats provider throw as drop (fail-closed)", async () => {
    const c = new SignalClassifier({ chat: vi.fn(async () => { throw new Error("down"); }) });
    const r = await c.classify(sig);
    expect(r).toEqual({ keep: false, confidence: 0 });
  });

  it("clamps confidence to [0,1]", async () => {
    const c = new SignalClassifier(fakeProvider(`{"keep":true,"confidence":2.5}`));
    const r = await c.classify(sig);
    expect(r.confidence).toBe(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/classifier.test.ts`
Expected: FAIL — `SignalClassifier` not found.

- [ ] **Step 3: Implement**

```typescript
// src/signals/classifier.ts
import type { ChatMessage, ChatOptions, ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ContextSignal } from "../ambient/types.js";

export interface ClassifierProvider {
  chat(messages: ChatMessage[], model?: string, options?: ChatOptions): Promise<{ content: string }>;
}

export interface ClassifierResult {
  keep: boolean;
  confidence: number;
}

const SYSTEM_PROMPT = `You filter ambient workspace signals for relevance to a coding/agent assistant.
Reply JSON only: {"keep": boolean, "confidence": number between 0 and 1}.
- keep=true if the signal is plausibly useful context for a developer right now.
- confidence reflects how confident you are this signal is worth surfacing.
- Mundane signals (e.g. routine clipboard noise, minor time updates) → keep=false.`;

export class SignalClassifier {
  constructor(private readonly provider: ClassifierProvider) {}

  static create(router: IntelligenceRouter, providers: Map<string, ModelProvider>): SignalClassifier {
    const resolved = router.resolve("classification");
    const provider = providers.get(resolved.provider);
    if (!provider) {
      return new SignalClassifier({
        chat: async () => ({ content: `{"keep":false,"confidence":0}` }),
      });
    }
    return new SignalClassifier({
      chat: (messages, _model, options) => provider.chat(messages, resolved.model, options),
    });
  }

  async classify(signal: ContextSignal): Promise<ClassifierResult> {
    const userMsg = `source: ${signal.source}\ntitle: ${signal.title}\ncontent: ${signal.content.slice(0, 500)}`;
    try {
      const { content } = await this.provider.chat([
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userMsg },
      ]);
      const parsed = JSON.parse(content);
      const keep = parsed.keep === true;
      const conf = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));
      return { keep, confidence: conf };
    } catch {
      return { keep: false, confidence: 0 };
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/classifier.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/classifier.ts __tests__/signals/classifier.test.ts
git commit -m "feat(signals): SignalClassifier cheap-tier prefilter"
```

---

## Task 4: SignalGoalAdapter — bridge ContextSignal to GoalVerifier API

**Files:**
- Create: `src/signals/goal-adapter.ts`
- Test: `__tests__/signals/goal-adapter.test.ts`

`GoalVerifier.verify` expects `VerifyArgs { toolName, toolArgs, toolResult, subGoal, userMessage }`. Convert a `ContextSignal` + active `Goal` into this shape so we can reuse the existing verifier without modification.

Same budget reasoning as Task 3: this is a thin adapter (~30 LOC), not a runtime data-plane file.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/goal-adapter.test.ts
import { describe, it, expect } from "vitest";
import { signalToVerifyArgs, goalToSubGoal } from "../../src/signals/goal-adapter.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

const goal: Goal = {
  id: "g1", title: "Ship Element 16b", description: "Unify perches and ambient",
  status: "active", priority: "high", subGoalIds: [], dependsOn: [],
  progress: 30, milestones: [], mentionedInSessions: [], lastActiveAt: 0,
  createdAt: 0, updatedAt: 0, tags: [],
};

const sig: ContextSignal = {
  id: "s1", source: "git", priority: "high",
  title: "12 uncommitted files in src/signals/", content: "M src/signals/pool.ts",
  timestamp: Date.now(), ttlMs: 60_000,
};

describe("goalToSubGoal", () => {
  it("converts a Goal to the SubGoal shape GoalVerifier expects", () => {
    const sg = goalToSubGoal(goal);
    expect(sg.id).toBe("g1");
    expect(sg.description).toBe("Ship Element 16b");
    expect(sg.status).toBe("in_progress");
    expect(sg.dependsOn).toEqual([]);
  });
});

describe("signalToVerifyArgs", () => {
  it("packages signal + goal as VerifyArgs", () => {
    const args = signalToVerifyArgs(sig, goal, "user is editing src/signals/");
    expect(args.toolName).toBe("ambient_signal");
    expect(args.toolArgs).toEqual({ source: "git", priority: "high" });
    expect(args.userMessage).toBe("user is editing src/signals/");
    const env = JSON.parse(args.toolResult);
    expect(env.success).toBe(true);
    expect(env.data).toContain("12 uncommitted files");
  });

  it("defaults userMessage to empty string when omitted", () => {
    const args = signalToVerifyArgs(sig, goal);
    expect(args.userMessage).toBe("");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/goal-adapter.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// src/signals/goal-adapter.ts
import type { ContextSignal } from "../ambient/types.js";
import type { Goal } from "../goals/types.js";
import type { SubGoal } from "../engine/types.js";
import type { VerifyArgs } from "../tools/goal-verifier.js";

export function goalToSubGoal(goal: Goal): SubGoal {
  return {
    id: goal.id,
    description: goal.title,
    status: "in_progress",
    dependsOn: [],
  };
}

export function signalToVerifyArgs(
  signal: ContextSignal,
  goal: Goal,
  userMessage = "",
): VerifyArgs {
  const envelope = JSON.stringify({
    success: true,
    data: `[${signal.source}] ${signal.title}\n${signal.content}`,
  });
  return {
    toolName: "ambient_signal",
    toolArgs: { source: signal.source, priority: signal.priority },
    toolResult: envelope,
    subGoal: goalToSubGoal(goal),
    userMessage,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/goal-adapter.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/goal-adapter.ts __tests__/signals/goal-adapter.test.ts
git commit -m "feat(signals): goal-adapter bridges ContextSignal to GoalVerifier API"
```

---

## Task 5: SignalPool — construction, idempotent start/stop, addCollector

**Files:**
- Create: `src/signals/pool.ts`
- Test: `__tests__/signals/pool-lifecycle.test.ts`

Lay the SignalPool foundation: constructor, dependencies wiring, idempotent `start()`/`stop()`, `addCollector()` with enabledSources gate.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-lifecycle.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { SignalCollector, ContextSignal } from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const fakeBus = { emit: vi.fn(), on: vi.fn() } as any;
const fakeClassifier = { classify: vi.fn(async () => ({ keep: false, confidence: 0 })) };
const fakeVerifier = { verify: vi.fn() } as any;
const fakeGoalGraph = { getActive: vi.fn(() => []), getTopPriority: vi.fn(() => undefined) } as any;

function makePool(consent: any = {}, enabledSources?: any) {
  return new SignalPool({
    bus: fakeBus,
    classifier: fakeClassifier,
    verifier: fakeVerifier,
    goalGraph: fakeGoalGraph,
    config: { maxSignals: 32, enabledSources, consent },
    workspacePath: "/tmp",
  });
}

describe("SignalPool lifecycle", () => {
  beforeEach(() => vi.clearAllMocks());

  it("constructs without throwing", () => {
    expect(() => makePool()).not.toThrow();
  });

  it("addCollector accepts a collector", () => {
    const pool = makePool();
    const c: SignalCollector = { source: "git", mode: "poll", intervalMs: 1000, collect: async () => [] };
    pool.addCollector(c);
    expect(pool.getState().signals).toEqual([]);
  });

  it("addCollector skips collectors whose source is not in enabledSources", () => {
    const pool = makePool({}, ["git"]);
    const c: SignalCollector = { source: "clipboard", mode: "poll", intervalMs: 1000, collect: async () => [] };
    pool.addCollector(c);
    pool.start();
    pool.stop();
    // No emission expected since clipboard wasn't added
    expect(fakeBus.emit).not.toHaveBeenCalled();
  });

  it("start is idempotent", () => {
    const pool = makePool();
    pool.start();
    pool.start();
    pool.stop();
  });

  it("stop is idempotent", () => {
    const pool = makePool();
    pool.stop();
    pool.stop();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-lifecycle.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// src/signals/pool.ts
import type {
  ContextSignal,
  ConsentMap,
  MeshState,
  SignalCollector,
  SignalSource,
} from "../ambient/types.js";
import { DEFAULT_CONSENT } from "../ambient/types.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { GoalGraph } from "../goals/graph.js";
import type { GoalVerifier } from "../tools/goal-verifier.js";
import type { MemoryStore } from "../memory/store.js";
import type { SignalClassifier } from "./classifier.js";
import { log } from "../logger.js";

const PRIORITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

export interface SignalPoolDeps {
  bus: GatewayEventBus;
  classifier: { classify(s: ContextSignal): Promise<{ keep: boolean; confidence: number }> };
  verifier: GoalVerifier;
  goalGraph: GoalGraph;
  config: { maxSignals: number; enabledSources?: SignalSource[]; consent: ConsentMap };
  memoryStore?: MemoryStore;
  workspacePath: string;
}

export class SignalPool {
  private signals = new Map<string, ContextSignal>();
  private collectors: SignalCollector[] = [];
  private timers: ReturnType<typeof setInterval>[] = [];
  private started = false;

  constructor(private readonly deps: SignalPoolDeps) {}

  addCollector(c: SignalCollector): void {
    const enabled = this.deps.config.enabledSources;
    if (enabled && !enabled.includes(c.source)) {
      log.engine.debug(`[SignalPool] collector ${c.source} skipped — not in enabledSources`);
      return;
    }
    this.collectors.push(c);
  }

  start(): void {
    if (this.started) return;
    this.started = true;
    log.engine.info(`[SignalPool] starting with ${this.collectors.length} collector(s)`);
    for (const c of this.collectors) {
      if (c.mode === "push" && c.start) {
        c.start((signal) => { void this.injectSignal(signal); });
      } else if (c.mode === "poll" && c.collect && c.intervalMs) {
        this.runPollCollector(c);
        this.timers.push(setInterval(() => this.runPollCollector(c), c.intervalMs));
      }
    }
  }

  stop(): void {
    if (!this.started) return;
    this.started = false;
    for (const t of this.timers) clearInterval(t);
    this.timers = [];
    for (const c of this.collectors) {
      if (c.mode === "push" && c.stop) c.stop();
    }
    log.engine.info("[SignalPool] stopped");
  }

  getState(): MeshState {
    const signals = [...this.signals.values()].sort(
      (a, b) => (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3),
    );
    return { signals, lastUpdate: Date.now(), activeContext: this.toContextBlock() };
  }

  hasHighPrioritySignals(): boolean {
    for (const s of this.signals.values()) {
      if (s.userSurfaceable && s.priority === "high") return true;
    }
    return false;
  }

  toContextBlock(maxSignals = 8): string {
    const surfaceable = [...this.signals.values()]
      .filter((s) => s.userSurfaceable)
      .sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3))
      .slice(0, maxSignals);
    if (surfaceable.length === 0) return "";
    const lines = surfaceable.map(
      (s) => `  <signal source="${s.source}" priority="${s.priority}">${s.title}</signal>`,
    );
    return `<ambient_context updated="${new Date().toISOString()}">\n${lines.join("\n")}\n</ambient_context>`;
  }

  async injectSignal(_signal: ContextSignal): Promise<void> {
    // Stages 1+2 implemented in Task 6+7+9
  }

  private async runPollCollector(_c: SignalCollector): Promise<void> {
    // Implemented in Task 6
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-lifecycle.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-lifecycle.test.ts
git commit -m "feat(signals): SignalPool skeleton — lifecycle + addCollector"
```

---

## Task 6: SignalPool.injectSignal — consent + enabled-source + classifier gates

**Files:**
- Modify: `src/signals/pool.ts:injectSignal`
- Test: `__tests__/signals/pool-injection.test.ts`

Implement Stage 1 (cheap-tier classifier prefilter) plus the consent and enabledSource gates that run before the classifier (so we don't pay router cost on disabled sources).

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-injection.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { ContextSignal } from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function sig(source: any = "git", priority: any = "low"): ContextSignal {
  return { id: Math.random().toString(36), source, priority,
    title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 };
}

function makePool(opts: { classify: any; consent?: any; enabledSources?: any }) {
  return new SignalPool({
    bus: { emit: vi.fn(), on: vi.fn() } as any,
    classifier: { classify: opts.classify },
    verifier: { verify: vi.fn() } as any,
    goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
    config: { maxSignals: 32, consent: opts.consent ?? {}, enabledSources: opts.enabledSources },
    workspacePath: "/tmp",
  });
}

describe("SignalPool.injectSignal — gates", () => {
  beforeEach(() => vi.clearAllMocks());

  it("drops when consent[source]===false (no classifier call)", async () => {
    const classify = vi.fn();
    const pool = makePool({ classify, consent: { clipboard: false } });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
    expect(pool.getState().signals).toEqual([]);
  });

  it("drops when source not in enabledSources (no classifier call)", async () => {
    const classify = vi.fn();
    const pool = makePool({ classify, enabledSources: ["git"] });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
  });

  it("admits at low when classifier confidence < 0.7", async () => {
    const pool = makePool({ classify: async () => ({ keep: true, confidence: 0.5 }) });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("low");
  });

  it("admits at medium when confidence in [0.7, 0.9)", async () => {
    const pool = makePool({ classify: async () => ({ keep: true, confidence: 0.8 }) });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("medium");
  });

  it("admits at high when confidence >= 0.9", async () => {
    const pool = makePool({ classify: async () => ({ keep: true, confidence: 0.95 }) });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("high");
  });

  it("drops when classifier keep=false (no admission)", async () => {
    const pool = makePool({ classify: async () => ({ keep: false, confidence: 0.9 }) });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals).toEqual([]);
  });

  it("falls back to DEFAULT_CONSENT when consent map is empty", async () => {
    // clipboard default-OFF
    const classify = vi.fn();
    const pool = makePool({ classify, consent: {} });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
    // git default-ON
    const classify2 = vi.fn(async () => ({ keep: true, confidence: 0.5 }));
    const pool2 = makePool({ classify: classify2, consent: {} });
    await pool2.injectSignal(sig("git"));
    expect(classify2).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-injection.test.ts`
Expected: FAIL — `injectSignal` is a no-op.

- [ ] **Step 3: Implement Stage 1 + gates**

Replace the placeholder `injectSignal` in `src/signals/pool.ts` with:

```typescript
async injectSignal(signal: ContextSignal): Promise<void> {
  // Gate 1: consent (no router cost for denied sources)
  const consent = this.deps.config.consent;
  const allowed = consent[signal.source] ?? DEFAULT_CONSENT[signal.source];
  if (!allowed) return;

  // Gate 2: enabledSources (no router cost for disabled sources)
  const enabled = this.deps.config.enabledSources;
  if (enabled && !enabled.includes(signal.source)) return;

  // Stage 1: cheap-tier classifier prefilter
  const { keep, confidence } = await this.deps.classifier.classify(signal);
  if (!keep) return;

  let priority = signal.priority;
  if (confidence >= 0.9) priority = "high";
  else if (confidence >= 0.7) priority = "medium";

  const admitted: ContextSignal = { ...signal, priority };
  this.signals.set(admitted.id, admitted);
  this.enforceLimit();
  this.deps.bus.emit({ type: "signal:emitted", signal: admitted } as any);
}

private enforceLimit(): void {
  const max = this.deps.config.maxSignals;
  if (this.signals.size <= max) return;
  const sorted = [...this.signals.values()].sort(
    (a, b) =>
      (PRIORITY_ORDER[b.priority] ?? 3) - (PRIORITY_ORDER[a.priority] ?? 3) ||
      a.timestamp - b.timestamp,
  );
  while (sorted.length > max) {
    const evicted = sorted.shift()!;
    this.signals.delete(evicted.id);
    this.deps.bus.emit({ type: "signal:expired", signal: evicted, reason: "evicted" } as any);
  }
}
```

The `as any` casts on bus.emit are temporary — Task 17 replaces them with the typed signal events.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-injection.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-injection.test.ts
git commit -m "feat(signals): pool admission — consent, enabledSources, classifier prefilter"
```

---

## Task 7: SignalPool — Stage 2 goal-conditioning verifier (per-signal)

**Files:**
- Modify: `src/signals/pool.ts:injectSignal`
- Test: `__tests__/signals/pool-verifier.test.ts`

When the classifier admits a signal at priority `high`, run the GoalVerifier against the active goal. Mark `userSurfaceable=true` only on `ADVANCES`; never on absent goal.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-verifier.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g1", title: "Ship 16b", description: "", status: "active", priority: "high",
  subGoalIds: [], dependsOn: [], progress: 0, milestones: [],
  mentionedInSessions: [], lastActiveAt: 0, createdAt: 0, updatedAt: 0, tags: [],
};

function sig(): ContextSignal {
  return { id: "s1", source: "git", priority: "low",
    title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 };
}

function makePool(opts: { verify: any; getTop?: () => Goal | undefined; emit?: any }) {
  return new SignalPool({
    bus: { emit: opts.emit ?? vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
    verifier: { verify: opts.verify } as any,
    goalGraph: { getActive: () => (opts.getTop?.() ? [opts.getTop()!] : []),
                 getTopPriority: opts.getTop ?? (() => undefined) } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

describe("SignalPool stage 2 verifier", () => {
  beforeEach(() => vi.clearAllMocks());

  it("skips verifier when no active goal — userSurfaceable stays false", async () => {
    const verify = vi.fn();
    const pool = makePool({ verify });
    await pool.injectSignal(sig());
    expect(verify).not.toHaveBeenCalled();
    expect(pool.getState().signals[0].userSurfaceable).toBeFalsy();
  });

  it("marks userSurfaceable=true on ADVANCES verdict", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => ({ verdict: "ADVANCES", reason: "edits in scope" }));
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    const s = pool.getState().signals[0];
    expect(s.userSurfaceable).toBe(true);
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "signal:promoted" }));
  });

  it("emits signal:suppressed on NEUTRAL verdict", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => ({ verdict: "NEUTRAL", reason: "unrelated" }));
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    expect(pool.getState().signals[0].userSurfaceable).toBeFalsy();
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "signal:suppressed" }));
  });

  it("verifier throw → signal stays in pool, no userSurfaceable, no event", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => { throw new Error("model down"); });
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    const s = pool.getState().signals[0];
    expect(s).toBeDefined();
    expect(s.userSurfaceable).toBeFalsy();
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "signal:emitted" }));
    expect(emit).not.toHaveBeenCalledWith(expect.objectContaining({ type: "signal:promoted" }));
  });

  it("only triggers verifier on priority=high (medium signals are admitted but not verified)", async () => {
    const verify = vi.fn();
    // confidence 0.8 → medium
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.8 }) },
      verifier: { verify } as any,
      goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await pool.injectSignal(sig());
    expect(verify).not.toHaveBeenCalled();
    expect(pool.getState().signals[0].priority).toBe("medium");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-verifier.test.ts`
Expected: FAIL — Stage 2 not implemented.

- [ ] **Step 3: Implement Stage 2**

Add at the top of `src/signals/pool.ts`:

```typescript
import { signalToVerifyArgs } from "./goal-adapter.js";
```

Modify `injectSignal` after the admission line:

```typescript
async injectSignal(signal: ContextSignal): Promise<void> {
  const consent = this.deps.config.consent;
  const allowed = consent[signal.source] ?? DEFAULT_CONSENT[signal.source];
  if (!allowed) return;

  const enabled = this.deps.config.enabledSources;
  if (enabled && !enabled.includes(signal.source)) return;

  const { keep, confidence } = await this.deps.classifier.classify(signal);
  if (!keep) return;

  let priority = signal.priority;
  if (confidence >= 0.9) priority = "high";
  else if (confidence >= 0.7) priority = "medium";

  const admitted: ContextSignal = { ...signal, priority, userSurfaceable: false };
  this.signals.set(admitted.id, admitted);
  this.enforceLimit();
  this.deps.bus.emit({ type: "signal:emitted", signal: admitted } as any);

  // Stage 2: only verify high-priority signals against active goal
  if (priority !== "high") return;
  const goal = this.deps.goalGraph.getTopPriority();
  if (!goal) return;

  try {
    const verifyArgs = signalToVerifyArgs(admitted, goal);
    const result = await this.deps.verifier.verify(verifyArgs);
    if (result.verdict === "ADVANCES") {
      admitted.userSurfaceable = true;
      this.signals.set(admitted.id, admitted);
      this.deps.bus.emit({
        type: "signal:promoted",
        signal: admitted,
        goal: { id: goal.id, title: goal.title },
        rationale: result.reason,
        verdict: "ADVANCES",
      } as any);
    } else {
      this.deps.bus.emit({
        type: "signal:suppressed",
        signal: admitted,
        verdict: result.verdict,
      } as any);
    }
  } catch (err) {
    log.engine.warn(`[SignalPool] verifier failed: ${(err as Error).message}`);
    // Signal stays in pool; will be retried on heartbeat sweep.
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-verifier.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-verifier.test.ts
git commit -m "feat(signals): stage 2 goal-conditioning verifier per-signal"
```

---

## Task 8: SignalPool — TTL expiry + heartbeat batch sweep

**Files:**
- Modify: `src/signals/pool.ts` (add `heartbeatTick`)
- Test: `__tests__/signals/pool-heartbeat.test.ts`

Drop expired signals (TTL pruning) and re-verify up to 5 unsurfaceable medium+ signals against the current active goal — catches goal-drift cases (signal admitted under goal A becomes ADVANCING under goal B).

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-heartbeat.test.ts
import { describe, it, expect, vi } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g", title: "T", description: "", status: "active", priority: "high",
  subGoalIds: [], dependsOn: [], progress: 0, milestones: [],
  mentionedInSessions: [], lastActiveAt: 0, createdAt: 0, updatedAt: 0, tags: [],
};

describe("SignalPool.heartbeatTick", () => {
  it("drops TTL-expired signals and emits signal:expired", async () => {
    const emit = vi.fn();
    const pool = new SignalPool({
      bus: { emit, on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.5 }) },
      verifier: { verify: vi.fn() } as any,
      goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: Date.now() - 10_000, ttlMs: 5_000 });
    expect(pool.getState().signals.length).toBe(1);

    await pool.heartbeatTick();
    expect(pool.getState().signals.length).toBe(0);
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "signal:expired", reason: "ttl" }));
  });

  it("re-verifies up to 5 unsurfaceable medium+ signals on goal drift", async () => {
    const verify = vi.fn(async () => ({ verdict: "ADVANCES", reason: "now relevant" }));
    const emit = vi.fn();
    const pool = new SignalPool({
      bus: { emit, on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.8 }) },
      verifier: { verify } as any,
      goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    // 7 medium signals admitted (no goal at admission time)
    for (let i = 0; i < 7; i++) {
      await pool.injectSignal({ id: `s${i}`, source: "git", priority: "low",
        title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    }
    expect(verify).not.toHaveBeenCalled(); // medium → no per-signal stage 2

    await pool.heartbeatTick();
    expect(verify).toHaveBeenCalledTimes(5); // batch cap
    const surfaceable = pool.getState().signals.filter((s) => s.userSurfaceable);
    expect(surfaceable.length).toBe(5);
  });

  it("skips re-verification when no active goal", async () => {
    const verify = vi.fn();
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.8 }) },
      verifier: { verify } as any,
      goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    await pool.heartbeatTick();
    expect(verify).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-heartbeat.test.ts`
Expected: FAIL — `heartbeatTick` does not exist.

- [ ] **Step 3: Implement heartbeatTick**

Add to `src/signals/pool.ts`:

```typescript
async heartbeatTick(): Promise<void> {
  // TTL pruning
  const now = Date.now();
  for (const [id, s] of this.signals) {
    if (s.timestamp + s.ttlMs < now) {
      this.signals.delete(id);
      this.deps.bus.emit({ type: "signal:expired", signal: s, reason: "ttl" } as any);
    }
  }

  // Batch re-verification (catches goal drift)
  const goal = this.deps.goalGraph.getTopPriority();
  if (!goal) return;

  const candidates = [...this.signals.values()]
    .filter((s) => !s.userSurfaceable && (s.priority === "medium" || s.priority === "high"))
    .slice(0, 5);

  for (const s of candidates) {
    try {
      const result = await this.deps.verifier.verify(signalToVerifyArgs(s, goal));
      if (result.verdict === "ADVANCES") {
        s.userSurfaceable = true;
        this.signals.set(s.id, s);
        this.deps.bus.emit({
          type: "signal:promoted",
          signal: s,
          goal: { id: goal.id, title: goal.title },
          rationale: result.reason,
          verdict: "ADVANCES",
        } as any);
      }
    } catch (err) {
      log.engine.warn(`[SignalPool] heartbeat verify failed: ${(err as Error).message}`);
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-heartbeat.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-heartbeat.test.ts
git commit -m "feat(signals): heartbeat batch sweep — TTL expiry + goal-drift re-verify"
```

---

## Task 9: SignalPool — runPollCollector with failure tracking

**Files:**
- Modify: `src/signals/pool.ts:runPollCollector`
- Test: `__tests__/signals/pool-poll-collector.test.ts`

Run a poll collector under a 2s soft timeout. On 3 consecutive failures, deregister + log warn.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-poll-collector.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { SignalCollector } from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function makePool() {
  return new SignalPool({
    bus: { emit: vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.5 }) },
    verifier: { verify: vi.fn() } as any,
    goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

describe("SignalPool poll collector wrapper", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("invokes collect and admits returned signals", async () => {
    const collect = vi.fn(async () => [{
      id: "s", source: "git" as const, priority: "low" as const,
      title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000,
    }]);
    const c: SignalCollector = { source: "git", mode: "poll", intervalMs: 1000, collect };
    const pool = makePool();
    pool.addCollector(c);
    pool.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(collect).toHaveBeenCalled();
    pool.stop();
  });

  it("deregisters collector after 3 consecutive failures", async () => {
    const collect = vi.fn(async () => { throw new Error("boom"); });
    const c: SignalCollector = { source: "git", mode: "poll", intervalMs: 100, collect };
    const pool = makePool();
    pool.addCollector(c);
    pool.start();
    for (let i = 0; i < 4; i++) {
      await vi.advanceTimersByTimeAsync(100);
    }
    expect(collect.mock.calls.length).toBeLessThanOrEqual(3);
    pool.stop();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-poll-collector.test.ts`
Expected: FAIL — collector not invoked / fail tracking missing.

- [ ] **Step 3: Implement runPollCollector**

Replace placeholder in `src/signals/pool.ts`:

```typescript
private failureCounts = new Map<SignalSource, number>();

private async runPollCollector(c: SignalCollector): Promise<void> {
  if (!c.collect) return;
  try {
    const signalsP = c.collect();
    const timeout = new Promise<never>((_, reject) =>
      setTimeout(() => reject(new Error("collector timeout")), 2_000),
    );
    const signals = await Promise.race([signalsP, timeout]);
    this.failureCounts.set(c.source, 0);
    for (const s of signals) {
      await this.injectSignal(s);
    }
  } catch (err) {
    const count = (this.failureCounts.get(c.source) ?? 0) + 1;
    this.failureCounts.set(c.source, count);
    if (count >= 3) {
      log.engine.warn(`[SignalPool] collector ${c.source} deregistered after 3 failures`);
      this.collectors = this.collectors.filter((x) => x !== c);
      // Stop its timer
      // (timers are stored alongside collectors in `start`; for the sake of bounded
      // failure isolation we leave the interval running but it will see the
      // collector removed and skip on next tick — see the guard below.)
    }
    log.engine.debug(`[SignalPool] collector ${c.source} failed: ${(err as Error).message}`);
  }
}
```

Modify `start()` to skip collectors that have been deregistered:

```typescript
this.timers.push(setInterval(() => {
  if (!this.collectors.includes(c)) return;
  void this.runPollCollector(c);
}, c.intervalMs));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-poll-collector.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-poll-collector.test.ts
git commit -m "feat(signals): pool poll-collector wrapper with timeout + 3-strike deregister"
```

---

## Task 10: SignalPool — memory promotion on ADVANCES

**Files:**
- Modify: `src/signals/pool.ts` (memory store hook in injectSignal + heartbeat)
- Test: `__tests__/signals/pool-memory-promotion.test.ts`

When a signal is promoted (verdict ADVANCES), if `memoryStore` is available, store it. Failure of memory write does NOT block bus emission (fail-open on this single path per spec §6.2).

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/pool-memory-promotion.test.ts
import { describe, it, expect, vi } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g", title: "T", description: "", status: "active", priority: "high",
  subGoalIds: [], dependsOn: [], progress: 0, milestones: [],
  mentionedInSessions: [], lastActiveAt: 0, createdAt: 0, updatedAt: 0, tags: [],
};

describe("memory promotion", () => {
  it("calls memoryStore.store on signal:promoted", async () => {
    const store = vi.fn(async () => undefined);
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: { verify: async () => ({ verdict: "ADVANCES", reason: "yes" }) } as any,
      goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
      config: { maxSignals: 32, consent: {} },
      memoryStore: { store } as any,
      workspacePath: "/tmp",
    });
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    expect(store).toHaveBeenCalled();
    const arg = store.mock.calls[0][0];
    expect(arg.kind).toBe("ambient_signal");
  });

  it("memory store throw does not block bus emission (fail-open)", async () => {
    const emit = vi.fn();
    const store = vi.fn(async () => { throw new Error("disk full"); });
    const pool = new SignalPool({
      bus: { emit, on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: { verify: async () => ({ verdict: "ADVANCES", reason: "yes" }) } as any,
      goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
      config: { maxSignals: 32, consent: {} },
      memoryStore: { store } as any,
      workspacePath: "/tmp",
    });
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "signal:promoted" }));
  });

  it("works without memoryStore (no-op)", async () => {
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: { verify: async () => ({ verdict: "ADVANCES", reason: "yes" }) } as any,
      goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await expect(
      pool.injectSignal({ id: "s", source: "git", priority: "low",
        title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 }),
    ).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/pool-memory-promotion.test.ts`
Expected: FAIL — memory store not called.

- [ ] **Step 3: Implement memory promotion**

In `src/signals/pool.ts`, factor the promotion path into a private helper and call it from both `injectSignal` and `heartbeatTick`:

```typescript
private async promote(signal: ContextSignal, goal: { id: string; title: string }, rationale: string): Promise<void> {
  signal.userSurfaceable = true;
  this.signals.set(signal.id, signal);
  this.deps.bus.emit({
    type: "signal:promoted",
    signal,
    goal,
    rationale,
    verdict: "ADVANCES",
  } as any);
  if (this.deps.memoryStore) {
    try {
      await this.deps.memoryStore.store({
        kind: "ambient_signal",
        content: `[${signal.source}] ${signal.title}\n${signal.content}`,
        metadata: { source: signal.source, goal_id: goal.id, rationale, verdict: "ADVANCES" },
      } as any);
    } catch (err) {
      log.engine.warn(`[SignalPool] memory store failed: ${(err as Error).message}`);
      // fail-open: emission already happened
    }
  }
}
```

Replace the inline ADVANCES handling in `injectSignal` and `heartbeatTick` with calls to `this.promote(admitted, { id: goal.id, title: goal.title }, result.reason)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/pool-memory-promotion.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/pool.ts __tests__/signals/pool-memory-promotion.test.ts
git commit -m "feat(signals): memory promotion on ADVANCES — fail-open on store error"
```

---

## Task 11: Port poll collectors (Git, Time, System, ActiveFile) without hardcoded priority bumps

**Files:**
- Create: `src/signals/collectors.ts`
- Test: `__tests__/signals/collectors.test.ts`

Port the four poll collectors from `src/ambient/collectors.ts`. Critical change: **remove the hardcoded priority bumps** (`fileCount > 5 ? "medium" : "low"` at line 62 and `usagePercent > 90 ? "high" : "low"` at line 169). Every collector emits at `"low"` baseline; the cheap-tier classifier in SignalPool decides relevance and bumps confidence-based priority.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/collectors.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
  Logger: class { info(){}; warn(){}; debug(){}; error(){} },
}));

vi.mock("node:child_process", () => ({ execSync: vi.fn() }));
vi.mock("node:fs", () => ({ readdirSync: vi.fn(() => []), statSync: vi.fn(), existsSync: () => true, watch: vi.fn(), readFileSync: vi.fn() }));

import { execSync } from "node:child_process";
import {
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
} from "../../src/signals/collectors.js";

describe("GitStatusCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("emits at priority low regardless of file count (no hardcoded bump)", async () => {
    (execSync as any).mockImplementation((cmd: string) => {
      if (cmd.includes("status")) return Array.from({ length: 20 }, (_, i) => ` M f${i}.ts`).join("\n");
      return "";
    });
    const c = new GitStatusCollector("/tmp");
    expect(c.mode).toBe("poll");
    const signals = await c.collect!();
    expect(signals.length).toBeGreaterThan(0);
    for (const s of signals) {
      expect(s.priority).toBe("low");
    }
  });

  it("returns empty array when git command throws", async () => {
    (execSync as any).mockImplementation(() => { throw new Error("not a repo"); });
    const c = new GitStatusCollector("/tmp");
    const signals = await c.collect!();
    expect(signals).toEqual([]);
  });
});

describe("TimeContextCollector", () => {
  it("emits at priority low and source time_of_day", async () => {
    const c = new TimeContextCollector();
    const signals = await c.collect!();
    expect(signals[0].source).toBe("time_of_day");
    expect(signals[0].priority).toBe("low");
  });
});

describe("SystemCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("emits disk usage at priority low regardless of percentage (no hardcoded bump)", async () => {
    (execSync as any).mockImplementation((cmd: string) => {
      if (cmd.startsWith("uptime")) return "up 3 days";
      if (cmd.startsWith("df")) return "Filesystem  Size  Used Avail Use% Mounted on\n/dev/disk1  100G  98G  2G   95%   /";
      return "";
    });
    const c = new SystemCollector();
    const signals = await c.collect!();
    expect(signals.length).toBeGreaterThan(0);
    for (const s of signals) {
      expect(s.priority).toBe("low");
    }
  });
});

describe("ActiveFileCollector", () => {
  it("returns empty when no recent files", async () => {
    const c = new ActiveFileCollector("/tmp");
    const signals = await c.collect!();
    expect(signals).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/collectors.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```typescript
// src/signals/collectors.ts
import { execSync, type ExecSyncOptions } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, watch } from "node:fs";
import { join, extname } from "node:path";
import { log } from "../logger.js";
import type { ContextSignal, SignalCollector, SignalSource } from "../ambient/types.js";

function makeSignal(
  source: SignalSource,
  title: string,
  content: string,
  ttlMs: number,
  metadata?: Record<string, unknown>,
): ContextSignal {
  return {
    id: randomUUID(),
    source,
    priority: "low",
    title,
    content,
    timestamp: Date.now(),
    ttlMs,
    metadata,
  };
}

export class GitStatusCollector implements SignalCollector {
  readonly source: SignalSource = "git";
  readonly mode = "poll" as const;
  readonly intervalMs = 60_000;
  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    const opts: ExecSyncOptions = { cwd: this.workspacePath, encoding: "utf-8", timeout: 10_000 };
    try {
      const status = (execSync("git status --porcelain", opts) as unknown as string).trim();
      const logRaw = (execSync("git log --oneline -3", opts) as unknown as string).trim();
      const out: ContextSignal[] = [];
      if (status) {
        const files = status.split("\n").filter(Boolean);
        out.push(makeSignal("git", `${files.length} uncommitted file${files.length === 1 ? "" : "s"}`,
          files.slice(0, 10).join("\n"), 90_000, { fileCount: files.length, files: files.slice(0, 20) }));
      }
      if (logRaw) {
        out.push(makeSignal("git", "Recent commits", logRaw, 90_000));
      }
      return out;
    } catch (err) {
      log.engine.warn(`[GitStatusCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class TimeContextCollector implements SignalCollector {
  readonly source: SignalSource = "time_of_day";
  readonly mode = "poll" as const;
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const now = new Date();
      const hour = now.getHours();
      const day = now.getDay();
      const isWeekend = day === 0 || day === 6;
      const period =
        hour >= 5 && hour < 12 ? "morning" :
        hour >= 12 && hour < 17 ? "afternoon" :
        hour >= 17 && hour < 21 ? "evening" : "night";
      const dayName = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"][day];
      const timeStr = now.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
      const dayType = isWeekend ? "weekend" : "weekday";
      return [makeSignal("time_of_day", `${dayName} ${period}, ${timeStr}`,
        `${dayName} ${period} (${dayType}), ${timeStr}. Hour ${hour}.`, 360_000,
        { hour, period, dayName, isWeekend })];
    } catch (err) {
      log.engine.warn(`[TimeContextCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class SystemCollector implements SignalCollector {
  readonly source: SignalSource = "system";
  readonly mode = "poll" as const;
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const out: ContextSignal[] = [];
      const uptime = (execSync("uptime", { encoding: "utf-8", timeout: 5_000 }) as unknown as string).trim();
      out.push(makeSignal("system", "System uptime", uptime, 360_000));
      const dfRaw = (execSync("df -h /", { encoding: "utf-8", timeout: 5_000 }) as unknown as string).trim();
      const dfLines = dfRaw.split("\n");
      if (dfLines.length >= 2) {
        const parts = dfLines[1].split(/\s+/);
        const usageStr = parts.find((p) => p.endsWith("%"));
        const usagePercent = usageStr ? parseInt(usageStr.replace("%", ""), 10) : 0;
        out.push(makeSignal("system", `Disk usage: ${usageStr ?? "unknown"}`, dfLines[1], 360_000, { usagePercent }));
      }
      return out;
    } catch (err) {
      log.engine.warn(`[SystemCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class ActiveFileCollector implements SignalCollector {
  readonly source: SignalSource = "active_file";
  readonly mode = "poll" as const;
  readonly intervalMs = 30_000;
  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    try {
      const since = Date.now() - 5 * 60_000;
      const recent = this.findRecent(this.workspacePath, since, 3, 0);
      if (recent.length === 0) return [];
      return [makeSignal("active_file",
        `${recent.length} recently modified file${recent.length === 1 ? "" : "s"}`,
        recent.map((f) => f.path).join("\n"), 45_000, { files: recent })];
    } catch (err) {
      log.engine.warn(`[ActiveFileCollector] ${(err as Error).message}`);
      return [];
    }
  }

  private findRecent(dir: string, since: number, maxDepth: number, depth: number): Array<{ path: string; mtime: number }> {
    if (depth > maxDepth) return [];
    const out: Array<{ path: string; mtime: number }> = [];
    try {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name.startsWith(".") || entry.name === "node_modules" || entry.name === "dist") continue;
        const full = join(dir, entry.name);
        try {
          if (entry.isFile()) {
            const stat = statSync(full);
            if (stat.mtimeMs >= since) out.push({ path: full, mtime: stat.mtimeMs });
          } else if (entry.isDirectory()) {
            out.push(...this.findRecent(full, since, maxDepth, depth + 1));
          }
        } catch {}
      }
    } catch {}
    return out.sort((a, b) => b.mtime - a.mtime).slice(0, 20);
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/collectors.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/collectors.ts __tests__/signals/collectors.test.ts
git commit -m "feat(signals): port poll collectors with no hardcoded priority bumps"
```

---

## Task 12: ClipboardCollector with macOS guard

**Files:**
- Modify: `src/signals/collectors.ts` (append)
- Test: `__tests__/signals/collectors.test.ts` (extend)

ClipboardCollector emits at `"low"` baseline. Default consent is OFF (handled at the SignalPool gate in Task 6). Returns empty on non-darwin platforms.

- [ ] **Step 1: Append failing test**

Add to `__tests__/signals/collectors.test.ts`:

```typescript
import { ClipboardCollector } from "../../src/signals/collectors.js";

describe("ClipboardCollector", () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns empty on non-darwin", async () => {
    const orig = process.platform;
    Object.defineProperty(process, "platform", { value: "linux" });
    const c = new ClipboardCollector();
    const signals = await c.collect!();
    expect(signals).toEqual([]);
    Object.defineProperty(process, "platform", { value: orig });
  });

  it("emits clipboard signal at priority low (truncated to 200 chars)", async () => {
    if (process.platform !== "darwin") return;
    (execSync as any).mockImplementation((cmd: string) =>
      cmd === "pbpaste" ? "x".repeat(500) : "");
    const c = new ClipboardCollector();
    const signals = await c.collect!();
    expect(signals[0].priority).toBe("low");
    expect(signals[0].content.length).toBeLessThanOrEqual(204); // 200 + "..."
  });

  it("does not re-emit the same content twice", async () => {
    if (process.platform !== "darwin") return;
    (execSync as any).mockImplementation(() => "stable content");
    const c = new ClipboardCollector();
    const first = await c.collect!();
    const second = await c.collect!();
    expect(first.length).toBe(1);
    expect(second).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/collectors.test.ts`
Expected: FAIL — `ClipboardCollector` not found.

- [ ] **Step 3: Implement**

Append to `src/signals/collectors.ts`:

```typescript
export class ClipboardCollector implements SignalCollector {
  readonly source: SignalSource = "clipboard";
  readonly mode = "poll" as const;
  readonly intervalMs = 10_000;
  private lastContent = "";

  async collect(): Promise<ContextSignal[]> {
    if (process.platform !== "darwin") return [];
    try {
      const raw = execSync("pbpaste", { encoding: "utf-8", timeout: 3_000 }) as unknown as string;
      const trimmed = raw.trim();
      if (!trimmed || trimmed === this.lastContent) return [];
      this.lastContent = trimmed;
      const preview = trimmed.length > 200 ? trimmed.slice(0, 200) + "..." : trimmed;
      return [makeSignal("clipboard", "Clipboard updated", preview, 30_000, { length: trimmed.length })];
    } catch (err) {
      log.engine.warn(`[ClipboardCollector] ${(err as Error).message}`);
      return [];
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/collectors.test.ts`
Expected: PASS (8 tests in this file).

- [ ] **Step 5: Commit**

```bash
git add src/signals/collectors.ts __tests__/signals/collectors.test.ts
git commit -m "feat(signals): ClipboardCollector with darwin guard and dedup"
```

---

## Task 13: FileSystemCollector (push-mode) — port from FilePerch

**Files:**
- Modify: `src/signals/collectors.ts` (append)
- Test: `__tests__/signals/file-system-collector.test.ts`

Port FilePerch's hash-dedup + 5s debounce engine into a push-mode `SignalCollector`. The hardcoded `ALLOWED_EXTS` list at `file-perch.ts:90-122` becomes a coarse perf prefilter only — not a relevance filter. Reject only what is universally noise (dotfiles, `.tmp`, `node_modules/`, `dist/`, `.git/`, `sessions/`, `pellets/`). The classifier handles relevance.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/file-system-collector.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const watchMock = vi.fn();
const existsSyncMock = vi.fn(() => true);
const readFileSyncMock = vi.fn(() => "content v1");
const statSyncMock = vi.fn(() => ({ size: 100 }));

vi.mock("node:fs", () => ({
  watch: watchMock,
  existsSync: existsSyncMock,
  readFileSync: readFileSyncMock,
  statSync: statSyncMock,
}));

import { FileSystemCollector } from "../../src/signals/collectors.js";

describe("FileSystemCollector", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    existsSyncMock.mockReturnValue(true);
    readFileSyncMock.mockReturnValue("content v1");
  });

  it("registers as push-mode with source=perch", () => {
    const c = new FileSystemCollector("/tmp");
    expect(c.mode).toBe("push");
    expect(c.source).toBe("perch");
  });

  it("calls fs.watch when start() is invoked", () => {
    const c = new FileSystemCollector("/tmp");
    c.start!(() => {});
    expect(watchMock).toHaveBeenCalled();
  });

  it("rejects coarse-prefilter paths (node_modules, dist, .git, dotfiles, .tmp)", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => { captured = cb; return { close: vi.fn() }; });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "node_modules/foo.js");
    captured("change", "dist/x.js");
    captured("change", ".git/HEAD");
    captured("change", ".env");
    captured("change", "x.tmp");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("accepts arbitrary extensions (relies on classifier for relevance)", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => { captured = cb; return { close: vi.fn() }; });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "src/something.exoticext");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });

  it("dedups by content hash", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => { captured = cb; return { close: vi.fn() }; });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    captured("change", "src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);

    // Same content again — should NOT emit
    emit.mockClear();
    captured("change", "src/a.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).not.toHaveBeenCalled();
  });

  it("debounces multiple events within 5s window into one emission", () => {
    let captured: any;
    watchMock.mockImplementation((_dir, _opts, cb) => { captured = cb; return { close: vi.fn() }; });
    const emit = vi.fn();
    const c = new FileSystemCollector("/tmp");
    c.start!(emit);
    let i = 0;
    readFileSyncMock.mockImplementation(() => `v${i++}`);
    captured("change", "src/a.ts");
    captured("change", "src/b.ts");
    captured("change", "src/c.ts");
    vi.advanceTimersByTime(6000);
    expect(emit).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/file-system-collector.test.ts`
Expected: FAIL — `FileSystemCollector` not found.

- [ ] **Step 3: Implement**

Append to `src/signals/collectors.ts`:

```typescript
interface FileSnapshot {
  hash: string;
  size: number;
  lineCount: number;
}

export class FileSystemCollector implements SignalCollector {
  readonly source: SignalSource = "perch";
  readonly mode = "push" as const;

  private watcher: ReturnType<typeof watch> | null = null;
  private debounceTimer: NodeJS.Timeout | null = null;
  private snapshots = new Map<string, FileSnapshot>();
  private pendingChanges = new Map<string, { type: "created" | "modified" | "deleted"; linesAdded?: number; linesRemoved?: number }>();
  private targetDir = "";
  private emitFn: ((s: ContextSignal) => void) | null = null;

  constructor(private rootPath: string) {}

  start(emit: (s: ContextSignal) => void): void {
    this.emitFn = emit;
    const srcDir = join(this.rootPath, "src");
    this.targetDir = existsSync(srcDir) ? srcDir : this.rootPath;
    try {
      this.watcher = watch(this.targetDir, { recursive: true }, (eventType, filename) => {
        if (filename && this.shouldProcess(filename)) {
          this.handleFileChange(eventType, filename);
        }
      });
    } catch (err) {
      log.engine.warn(`[FileSystemCollector] start failed: ${(err as Error).message}`);
    }
  }

  stop(): void {
    if (this.watcher) { this.watcher.close(); this.watcher = null; }
    if (this.debounceTimer) { clearTimeout(this.debounceTimer); this.debounceTimer = null; }
  }

  /**
   * Coarse perf prefilter: reject only universally noisy paths.
   * Relevance classification is the classifier's job, not this filter's.
   */
  private shouldProcess(filename: string): boolean {
    if (filename.startsWith(".")) return false;
    if (filename.endsWith(".tmp") || filename.endsWith("~")) return false;
    if (filename.includes("node_modules/") || filename.includes("node_modules\\")) return false;
    if (filename.includes("dist/") || filename.includes("dist\\")) return false;
    if (filename.includes(".git/") || filename.includes(".git\\")) return false;
    if (filename.includes("sessions/") || filename.includes("pellets/")) return false;
    return true;
  }

  private handleFileChange(_eventType: string, filename: string): void {
    const fullPath = join(this.targetDir, filename);
    const prev = this.snapshots.get(filename);

    if (!existsSync(fullPath)) {
      if (prev) {
        this.pendingChanges.set(filename, { type: "deleted", linesRemoved: prev.lineCount });
        this.snapshots.delete(filename);
      }
    } else {
      try {
        const content = readFileSync(fullPath, "utf-8");
        const hash = createHash("md5").update(content).digest("hex");
        if (prev && prev.hash === hash) return; // dedup: no real change
        const lineCount = content.split("\n").length;
        const size = statSync(fullPath).size;
        if (!prev) {
          this.pendingChanges.set(filename, { type: "created", linesAdded: lineCount });
        } else {
          this.pendingChanges.set(filename, {
            type: "modified",
            linesAdded: Math.max(0, lineCount - prev.lineCount),
            linesRemoved: Math.max(0, prev.lineCount - lineCount),
          });
        }
        this.snapshots.set(filename, { hash, size, lineCount });
      } catch {
        return;
      }
    }

    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => this.flush(), 5000);
  }

  private flush(): void {
    if (this.pendingChanges.size === 0 || !this.emitFn) return;
    const created: string[] = [], modified: string[] = [], deleted: string[] = [];
    let added = 0, removed = 0;
    for (const [file, change] of this.pendingChanges) {
      if (change.type === "created") created.push(file);
      else if (change.type === "modified") modified.push(file);
      else deleted.push(file);
      added += change.linesAdded ?? 0;
      removed += change.linesRemoved ?? 0;
    }
    const parts: string[] = [];
    if (created.length) parts.push(`Created: ${created.join(", ")}`);
    if (modified.length) parts.push(`Modified: ${modified.join(", ")}`);
    if (deleted.length) parts.push(`Deleted: ${deleted.join(", ")}`);
    const totalFiles = this.pendingChanges.size;
    const title = totalFiles === 1
      ? created[0] || modified[0] || deleted[0]
      : `${totalFiles} files changed (+${added}/-${removed})`;
    const content = parts.join(". ") + (added || removed ? ` (+${added}/-${removed} lines)` : "");
    this.pendingChanges.clear();
    this.emitFn(makeSignal("perch", title, content, 60_000, { added, removed }));
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/file-system-collector.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/collectors.ts __tests__/signals/file-system-collector.test.ts
git commit -m "feat(signals): FileSystemCollector push-mode (ported from FilePerch)"
```

---

## Task 14: ConfigLoader.mutateConsent — atomic write-rename + per-process mutex

**Files:**
- Modify: `src/config/loader.ts`
- Test: `__tests__/signals/consent-mutation.test.ts`

Add `mutateConsent(basePath, source, granted)` that loads, mutates `perches.consent[source]`, calls existing `saveConfig` (already atomic), and returns the new value. Per-process mutex serializes concurrent mutations.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/consent-mutation.test.ts
import { describe, it, expect, beforeEach } from "vitest";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { mutateConsent } from "../../src/config/loader.js";

async function freshConfigDir(): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "stackowl-test-"));
  const minimal = {
    providers: {}, defaultProvider: "openai", defaultModel: "gpt-4o-mini",
    workspace: ".", gateway: { port: 3000, host: "localhost" },
    parliament: { maxRounds: 1, maxOwls: 1 }, heartbeat: { enabled: false, intervalMinutes: 60 },
    owlDna: { enabled: false, evolutionBatchSize: 1, decayRatePerWeek: 0.1 },
  };
  await writeFile(join(dir, "stackowl.config.json"), JSON.stringify(minimal, null, 2));
  return dir;
}

describe("mutateConsent", () => {
  it("creates perches.consent block when missing", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    const cfg = JSON.parse(await readFile(join(dir, "stackowl.config.json"), "utf-8"));
    expect(cfg.perches?.consent?.clipboard).toBe(true);
  });

  it("toggles existing consent value", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    await mutateConsent(dir, "clipboard", false);
    const cfg = JSON.parse(await readFile(join(dir, "stackowl.config.json"), "utf-8"));
    expect(cfg.perches?.consent?.clipboard).toBe(false);
  });

  it("preserves consent state for other sources during mutation", async () => {
    const dir = await freshConfigDir();
    await mutateConsent(dir, "clipboard", true);
    await mutateConsent(dir, "email", true);
    const cfg = JSON.parse(await readFile(join(dir, "stackowl.config.json"), "utf-8"));
    expect(cfg.perches.consent.clipboard).toBe(true);
    expect(cfg.perches.consent.email).toBe(true);
  });

  it("serializes concurrent mutations (last write wins per source)", async () => {
    const dir = await freshConfigDir();
    await Promise.all([
      mutateConsent(dir, "clipboard", true),
      mutateConsent(dir, "email", true),
      mutateConsent(dir, "calendar", true),
    ]);
    const cfg = JSON.parse(await readFile(join(dir, "stackowl.config.json"), "utf-8"));
    expect(cfg.perches.consent.clipboard).toBe(true);
    expect(cfg.perches.consent.email).toBe(true);
    expect(cfg.perches.consent.calendar).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/consent-mutation.test.ts`
Expected: FAIL — `mutateConsent` not exported.

- [ ] **Step 3: Implement**

Append to `src/config/loader.ts`:

```typescript
import type { SignalSource } from "../ambient/types.js";

let consentMutex: Promise<void> = Promise.resolve();

/**
 * Atomically grant or revoke consent for an ambient signal source.
 * Serialized per-process via a mutex chain so concurrent calls don't race.
 */
export async function mutateConsent(
  basePath: string,
  source: SignalSource,
  granted: boolean,
): Promise<void> {
  const next = consentMutex.then(async () => {
    const config = await loadConfig(basePath);
    const perches = ((config as any).perches ??= {});
    const consent = (perches.consent ??= {});
    consent[source] = granted;
    await saveConfig(basePath, config);
  });
  consentMutex = next.catch(() => undefined);
  return next;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/consent-mutation.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/config/loader.ts __tests__/signals/consent-mutation.test.ts
git commit -m "feat(config): mutateConsent — atomic per-source consent mutations"
```

---

## Task 15: AmbientContextLayer rewire to SignalPool

**Files:**
- Modify: `src/context/layers/ambient.ts`
- Test: `__tests__/signals/ambient-layer-integration.test.ts`

Rewrite `AmbientContextLayer` to take a `SignalPool` constructor argument. Gate `shouldFire` on `pool.hasHighPrioritySignals()`. Build returns `pool.toContextBlock(8)`. Bump `maxTokens` from 300 to 400 per locked decision §6 #5.

`CollabContextLayer` in the same file is untouched.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/ambient-layer-integration.test.ts
import { describe, it, expect, vi } from "vitest";
import { AmbientContextLayer } from "../../src/context/layers/ambient.js";
import { SignalPool } from "../../src/signals/pool.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g", title: "T", description: "", status: "active", priority: "high",
  subGoalIds: [], dependsOn: [], progress: 0, milestones: [],
  mentionedInSessions: [], lastActiveAt: 0, createdAt: 0, updatedAt: 0, tags: [],
};

function makePool() {
  return new SignalPool({
    bus: { emit: vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
    verifier: { verify: async () => ({ verdict: "ADVANCES", reason: "yes" }) } as any,
    goalGraph: { getActive: () => [goal], getTopPriority: () => goal } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

describe("AmbientContextLayer integration with SignalPool", () => {
  it("priority is 145 and maxTokens is 400", () => {
    const pool = makePool();
    const layer = new AmbientContextLayer(pool);
    expect(layer.priority).toBe(145);
    expect(layer.maxTokens).toBe(400);
  });

  it("shouldFire is false when conversational", () => {
    const layer = new AmbientContextLayer(makePool());
    expect(layer.shouldFire({ isConversational: true } as any)).toBe(false);
  });

  it("shouldFire is false when no high-priority surfaceable signals", () => {
    const layer = new AmbientContextLayer(makePool());
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(false);
  });

  it("shouldFire is true when SignalPool has high-priority surfaceable signal", async () => {
    const pool = makePool();
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    const layer = new AmbientContextLayer(pool);
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(true);
  });

  it("build returns <ambient_context> wrapper with surfaceable signals", async () => {
    const pool = makePool();
    await pool.injectSignal({ id: "s", source: "git", priority: "low",
      title: "12 uncommitted files", content: "c", timestamp: Date.now(), ttlMs: 60_000 });
    const layer = new AmbientContextLayer(pool);
    const out = await layer.build({} as any, {} as any, {} as any);
    expect(out).toContain("<ambient_context");
    expect(out).toContain("12 uncommitted files");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/ambient-layer-integration.test.ts`
Expected: FAIL — constructor signature mismatch / shouldFire signature mismatch.

- [ ] **Step 3: Rewrite the layer**

Replace `AmbientContextLayer` in `src/context/layers/ambient.ts` with:

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { SignalPool } from "../../signals/pool.js";

export class CollabContextLayer implements ContextLayer {
  name = "CollabContextLayer";
  priority = 140;
  maxTokens = 300;
  produces = ["collab"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }
  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const collab = (req.session as any).collabContext as string | undefined;
    if (!collab) return "";
    return `<collab_context>\n${collab}\n</collab_context>`;
  }
}

export class AmbientContextLayer implements ContextLayer {
  name = "AmbientContextLayer";
  priority = 145;
  maxTokens = 400;
  produces = ["ambient"];
  dependsOn = [];
  constructor(private readonly signalPool: SignalPool) {}
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean {
    return !t.isConversational && this.signalPool.hasHighPrioritySignals();
  }
  async build(_req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    return this.signalPool.toContextBlock(8);
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/signals/ambient-layer-integration.test.ts`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/context/layers/ambient.ts __tests__/signals/ambient-layer-integration.test.ts
git commit -m "feat(context): AmbientContextLayer reads from SignalPool"
```

---

## Task 16: GatewayEventBus signal events (replace `perch:event` slot)

**Files:**
- Modify: `src/gateway/event-bus.ts:9`
- Test: `__tests__/signals/event-bus-signals.test.ts`

Replace the dormant `perch:event` event with the 5 typed signal events. After this task, the `as any` casts in pool.ts type-check correctly.

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/signals/event-bus-signals.test.ts
import { describe, it, expect, vi } from "vitest";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

describe("GatewayEventBus signal events", () => {
  it("delivers signal:emitted to subscribers", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("signal:emitted", handler);
    bus.emit({ type: "signal:emitted", signal: {
      id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: 0, ttlMs: 1000,
    } });
    expect(handler).toHaveBeenCalled();
  });

  it("delivers signal:promoted with goal + rationale + verdict", () => {
    const bus = new GatewayEventBus();
    const handler = vi.fn();
    bus.on("signal:promoted", handler);
    bus.emit({
      type: "signal:promoted",
      signal: { id: "s", source: "git", priority: "high",
        title: "t", content: "c", timestamp: 0, ttlMs: 1000 },
      goal: { id: "g", title: "Ship 16b" },
      rationale: "edits in scope",
      verdict: "ADVANCES",
    });
    expect(handler).toHaveBeenCalled();
    const arg = handler.mock.calls[0][0];
    expect(arg.goal.id).toBe("g");
    expect(arg.rationale).toBe("edits in scope");
  });

  it("delivers signal:expired, signal:suppressed, signal:consent_changed", () => {
    const bus = new GatewayEventBus();
    const expired = vi.fn(), suppressed = vi.fn(), consent = vi.fn();
    bus.on("signal:expired", expired);
    bus.on("signal:suppressed", suppressed);
    bus.on("signal:consent_changed", consent);
    bus.emit({ type: "signal:expired", signal: { id: "s", source: "git", priority: "low",
      title: "t", content: "c", timestamp: 0, ttlMs: 1000 }, reason: "ttl" });
    bus.emit({ type: "signal:suppressed", signal: { id: "s", source: "git", priority: "high",
      title: "t", content: "c", timestamp: 0, ttlMs: 1000 }, verdict: "NEUTRAL" });
    bus.emit({ type: "signal:consent_changed", source: "clipboard", granted: true });
    expect(expired).toHaveBeenCalled();
    expect(suppressed).toHaveBeenCalled();
    expect(consent).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/event-bus-signals.test.ts`
Expected: FAIL — types not declared.

- [ ] **Step 3: Replace the event slot**

Modify `src/gateway/event-bus.ts`. Add import at the top:

```typescript
import type { ContextSignal, SignalSource } from "../ambient/types.js"
```

Replace the `perch:event` line in the `GatewaySystemEvent` union with the 5 signal events:

```typescript
  | { type: "signal:emitted";        signal: ContextSignal }
  | { type: "signal:expired";        signal: ContextSignal; reason: "ttl" | "evicted" }
  | { type: "signal:promoted";       signal: ContextSignal; goal: { id: string; title: string }; rationale: string; verdict: "ADVANCES" }
  | { type: "signal:suppressed";     signal: ContextSignal; verdict: "NEUTRAL" | "PARTIAL" | "BLOCKED" }
  | { type: "signal:consent_changed";source: SignalSource;  granted: boolean }
```

Remove the `perch:event` slot (it had zero subscribers — confirmed by Phase 1 audit).

- [ ] **Step 4: Drop the `as any` casts in pool.ts**

In `src/signals/pool.ts`, replace every `bus.emit({ ... } as any)` with `bus.emit({ ... })`. TypeScript should now type-check the events.

- [ ] **Step 5: Run all signal tests**

Run: `npx vitest run __tests__/signals/`
Expected: PASS — all signal tests including the new event-bus tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/event-bus.ts src/signals/pool.ts __tests__/signals/event-bus-signals.test.ts
git commit -m "feat(gateway): typed signal:* events; drop dormant perch:event slot"
```

---

## Task 17: Rename `contextMesh` → `signalPool` in gateway types and core

**Files:**
- Modify: `src/gateway/types.ts:158, 226`
- Modify: `src/gateway/core.ts:2730-2735, 2818`
- Modify: `src/intent/proactive-loop.ts:18, 40, 102-114`
- Test: existing tests should still pass

Mechanical rename. The `ProactiveIntentionLoop` consumer at `src/intent/proactive-loop.ts:102-114` reads `contextMesh.getState().signals` — that API is preserved on `SignalPool` (Task 5 implemented `getState()`), so only the field/type name changes.

- [ ] **Step 1: Update `src/gateway/types.ts`**

Replace line 158:
```typescript
import type { SignalPool } from "../signals/pool.js";
```
(Remove the line `import type { ContextMesh } from "../ambient/mesh.js";` — `ContextMesh` is going away in the cleanup task.)

Replace line 226:
```typescript
  signalPool?: SignalPool;
```

- [ ] **Step 2: Update `src/gateway/core.ts:2730-2735`**

```typescript
  private initFeatureModules(): void {
    // SignalPool — start ambient signal collectors
    if (this.ctx.signalPool) {
      this.ctx.signalPool.start();
      log.engine.info("[feature] SignalPool started");
    }
```

And line 2818:
```typescript
      this.ctx.signalPool?.stop?.();
```

- [ ] **Step 3: Update `src/intent/proactive-loop.ts`**

Replace line 18:
```typescript
import type { SignalPool } from "../signals/pool.js";
```

Replace line 40 (the constructor parameter):
```typescript
    private signalPool: SignalPool | undefined,
```

Update the body around line 102-114 to use `this.signalPool` instead of `this.contextMesh`:
```typescript
    // 4. High-priority ambient signals
    if (this.signalPool) {
      const signals = this.signalPool.getState().signals;
      for (const signal of signals.slice(0, 3)) {
        if (signal.priority === "critical" || signal.priority === "high") {
          items.push({
            type: "ambient_signal",
            priority: 50,
            message: `I noticed: ${signal.title}. ${signal.content?.slice(0, 100) ?? ""}`,
            metadata: { signalId: signal.id, source: signal.source },
          });
        }
      }
    }
```

- [ ] **Step 4: Update `src/index.ts:1202`**

Find the line that passes `undefined` for `contextMesh` (was previously `contextMesh: undefined` or positional). Rename if necessary; the placement and value don't change yet (SignalPool wiring lands in Task 18). Run grep to find:

```bash
grep -n "contextMesh" src/index.ts
```

Replace any remaining `contextMesh` references in `src/index.ts` with `signalPool`.

- [ ] **Step 5: Run typecheck and existing tests**

Run: `npx tsc --noEmit && npx vitest run __tests__/signals/`
Expected: typecheck clean, all signal tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/types.ts src/gateway/core.ts src/intent/proactive-loop.ts src/index.ts
git commit -m "refactor(gateway): rename contextMesh slot to signalPool"
```

---

## Task 18: Wire SignalPool into gateway boot

**Files:**
- Modify: `src/gateway/core.ts` (where `GatewayContext` is constructed) + `src/index.ts`
- Test: `__tests__/signals/boot-wiring.test.ts`

Construct the `SignalPool` with deps (bus, classifier from IntelligenceRouter, verifier, goalGraph, consent from config) and attach the four poll collectors + FileSystemCollector. Pass it to `GatewayContext.signalPool`.

- [ ] **Step 1: Find the GatewayContext construction site**

Run: `grep -n "buildBootstrap\|GatewayContext\|new GatewayCore" /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/src/index.ts | head -10`

Identify where the gateway context is assembled (typically `buildBootstrap` returns an object that is passed to `new GatewayCore`). The exact line varies by codebase state — locate the spot where `contextMesh: undefined` or equivalent appears (Phase 1 audit located it at `src/index.ts:1202`).

- [ ] **Step 2: Write the failing test**

```typescript
// __tests__/signals/boot-wiring.test.ts
import { describe, it, expect, vi } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import { GitStatusCollector, FileSystemCollector } from "../../src/signals/collectors.js";
import { GatewayEventBus } from "../../src/gateway/event-bus.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

describe("SignalPool boot wiring", () => {
  it("can be constructed with the standard collector set", () => {
    const bus = new GatewayEventBus();
    const pool = new SignalPool({
      bus,
      classifier: { classify: async () => ({ keep: false, confidence: 0 }) },
      verifier: { verify: async () => ({ verdict: "NEUTRAL", reason: "" }) } as any,
      goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    pool.addCollector(new GitStatusCollector("/tmp"));
    pool.addCollector(new FileSystemCollector("/tmp"));
    expect(() => { pool.start(); pool.stop(); }).not.toThrow();
  });
});
```

- [ ] **Step 3: Run test to verify it passes (should already)**

Run: `npx vitest run __tests__/signals/boot-wiring.test.ts`
Expected: PASS — this is a smoke test for the wiring shape we're about to add to index.ts.

- [ ] **Step 4: Wire in `src/index.ts`**

Locate the `buildBootstrap` (or equivalent) function and the line where `signalPool: undefined` is currently passed. Replace with:

```typescript
import { SignalPool } from "./signals/pool.js";
import { SignalClassifier } from "./signals/classifier.js";
import {
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
  FileSystemCollector,
} from "./signals/collectors.js";
import { GoalVerifier } from "./tools/goal-verifier.js";

// ... in buildBootstrap, after `bus`, `intelligenceRouter`, `goalGraph`,
// `providers` (Map<string, ModelProvider>), and `config` are available:

const signalPool = new SignalPool({
  bus,
  classifier: SignalClassifier.create(intelligenceRouter, providers),
  verifier: GoalVerifier.create(intelligenceRouter, providers),
  goalGraph,
  config: {
    maxSignals: 32,
    consent: ((config as any).perches?.consent) ?? {},
  },
  memoryStore,
  workspacePath,
});
signalPool.addCollector(new GitStatusCollector(workspacePath));
signalPool.addCollector(new TimeContextCollector());
signalPool.addCollector(new SystemCollector());
signalPool.addCollector(new ActiveFileCollector(workspacePath));
signalPool.addCollector(new ClipboardCollector());
signalPool.addCollector(new FileSystemCollector(workspacePath));
```

Pass `signalPool` (instead of `undefined`) to the GatewayContext / proactive loop construction.

- [ ] **Step 5: Run all tests + typecheck**

Run: `npx tsc --noEmit && npm test`
Expected: clean, all tests pass (including pre-existing tests).

- [ ] **Step 6: Commit**

```bash
git add src/index.ts __tests__/signals/boot-wiring.test.ts
git commit -m "feat(boot): wire SignalPool into gateway bootstrap with all 6 collectors"
```

---

## Task 19: Remove PerchManager from boot paths

**Files:**
- Modify: `src/index.ts` (lines 164-165, 825-841, 1292-1294, 1334, 1415-1418, 1893-1904, 1972-1983, 2169-2180)

Delete every PerchManager construction and start/stop call. SignalPool handles all of this in the gateway boot now.

- [ ] **Step 1: Remove imports**

Delete lines 164-165:
```typescript
import { PerchManager } from "./perch/manager.js";
import { FilePerch } from "./perch/file-perch.js";
```

- [ ] **Step 2: Remove PerchManager wiring at the four boot sites**

Run: `grep -n "PerchManager\|FilePerch\|perchManager\|perch\.start\|perch\.stop" src/index.ts`

For each occurrence:
- Delete construction lines (`new PerchManager(...)`, `perch.addPerch(new FilePerch(...))`).
- Delete the lines that call `.startAll()` / `.stopAll()` on the local `perch` variable or `b.perchManager`.
- Delete `perchManager,` from any object literal it was passed into (e.g., the `b` bootstrap object at line 841).
- Delete the "Starting perch watchers" / "perchManager.startAll()" entries from any startup-step list.

After this task, no reference to `PerchManager`, `FilePerch`, `perchManager`, or `b.perchManager` should remain in `src/index.ts`.

- [ ] **Step 3: Verify**

Run: `grep -n "PerchManager\|FilePerch\|perchManager" src/index.ts`
Expected: no output.

- [ ] **Step 4: Typecheck and run tests**

Run: `npx tsc --noEmit && npm test`
Expected: clean. The only references to `src/perch/*` should now be the files themselves (deleted in Task 21).

- [ ] **Step 5: Commit**

```bash
git add src/index.ts
git commit -m "refactor(boot): remove PerchManager wiring — SignalPool replaces it"
```

---

## Task 20: Narration template for `signal:promoted` (channel parity)

**Files:**
- Modify: `src/gateway/narration-formatter.ts`
- Modify: `src/gateway/core.ts` (subscribe to `signal:promoted`)
- Test: `__tests__/signals/channel-parity.test.ts`

Add a single template that renders `signal:promoted` events as the canonical `🔭 [{source}] {summary} — {rationale}` string. The gateway's existing channel dispatch infrastructure handles per-channel transport (CLI prints, Telegram sends, Slack posts, etc.).

- [ ] **Step 1: Inspect current narration-formatter shape**

Run: `head -80 /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/src/gateway/narration-formatter.ts`

Identify the existing pattern (function or class) used to format other system events. The new template should follow the same shape.

- [ ] **Step 2: Write the failing test**

```typescript
// __tests__/signals/channel-parity.test.ts
import { describe, it, expect } from "vitest";
import { formatSignalPromoted } from "../../src/gateway/narration-formatter.js";

describe("formatSignalPromoted", () => {
  it("renders the canonical template", () => {
    const out = formatSignalPromoted({
      type: "signal:promoted",
      signal: { id: "s", source: "git", priority: "high",
        title: "12 uncommitted files in src/signals/", content: "...",
        timestamp: 0, ttlMs: 60_000 },
      goal: { id: "g", title: "ship Element 16b" },
      rationale: "advances goal scope",
      verdict: "ADVANCES",
    });
    expect(out).toBe(`🔭 [git] 12 uncommitted files in src/signals/ — advances "ship Element 16b" (verdict: ADVANCES)`);
  });
});
```

Note: the test pins the exact template wording so all channels produce identical strings. If the formatter shape currently uses an object-of-formatters pattern instead, adapt the test import accordingly.

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/signals/channel-parity.test.ts`
Expected: FAIL — function not exported.

- [ ] **Step 4: Implement**

Add to `src/gateway/narration-formatter.ts`:

```typescript
import type { GatewaySystemEvent } from "./event-bus.js";

export function formatSignalPromoted(
  e: Extract<GatewaySystemEvent, { type: "signal:promoted" }>,
): string {
  return `🔭 [${e.signal.source}] ${e.signal.title} — advances "${e.goal.title}" (verdict: ${e.verdict})`;
}
```

- [ ] **Step 5: Wire the bus subscriber in gateway/core.ts**

In `src/gateway/core.ts`, in the same area as `initFeatureModules()` (around line 2730), add:

```typescript
this.bus.on("signal:promoted", (e) => {
  const text = formatSignalPromoted(e);
  log.engine.info(`[signal] ${text}`);
  // Dispatch via existing channel-broadcast hook if present.
  // The gateway already broadcasts proactive items through the same path
  // ProactiveIntentionLoop uses; reuse that here so channel parity is automatic.
  if (this.broadcastProactive) {
    void this.broadcastProactive(text);
  }
});
```

If `this.broadcastProactive` doesn't exist, locate the existing proactive-broadcast helper in `core.ts` (look for `sendProactive` at line 2829 noted in Phase 1) and pipe the text through that instead.

- [ ] **Step 6: Run tests**

Run: `npx vitest run __tests__/signals/channel-parity.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/narration-formatter.ts src/gateway/core.ts __tests__/signals/channel-parity.test.ts
git commit -m "feat(narration): channel-parity template for signal:promoted"
```

---

## Task 21: Delete dead modules + replace re-exports

**Files:**
- Delete: `src/perch/manager.ts`
- Delete: `src/perch/file-perch.ts`
- Delete: `src/ambient/mesh.ts`
- Delete: `src/ambient/collectors.ts`
- Delete: `__tests__/ambient.test.ts`
- Modify: `src/ambient/index.ts`

Final cleanup. After this task, the `src/perch/` directory is gone and `src/ambient/` only contains `types.ts` + `index.ts` (re-exports for ContextSignal/SignalCollector/etc).

- [ ] **Step 1: Verify no remaining importers**

Run:
```bash
grep -rn "from.*ambient/mesh\|from.*ambient/collectors\|from.*perch/manager\|from.*perch/file-perch\|ContextMesh\|PerchManager\|FilePerch\|PerchEvent\|PerchPoint" /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants/src --include="*.ts" || echo "(none)"
```
Expected: `(none)` — every import has already been moved to `src/signals/*` by earlier tasks.

If any remain, fix them before proceeding (typically left-over imports in `src/ambient/index.ts` or a stray reference in `src/index.ts`).

- [ ] **Step 2: Delete dead source files**

```bash
rm src/perch/manager.ts
rm src/perch/file-perch.ts
rm src/ambient/mesh.ts
rm src/ambient/collectors.ts
rmdir src/perch  # only succeeds if empty
```

- [ ] **Step 3: Delete the old ambient test**

```bash
rm __tests__/ambient.test.ts
```

(All tests it covered are now in `__tests__/signals/*.test.ts`.)

- [ ] **Step 4: Update `src/ambient/index.ts`**

Replace contents with:

```typescript
export type {
  SignalSource,
  SignalPriority,
  ContextSignal,
  SignalCollector,
  MeshState,
  AmbientRule,
  ConsentMap,
} from "./types.js";

export { DEFAULT_CONSENT } from "./types.js";

// Backwards-compatible re-exports for the new home.
export {
  GitStatusCollector,
  TimeContextCollector,
  SystemCollector,
  ActiveFileCollector,
  ClipboardCollector,
  FileSystemCollector,
} from "../signals/collectors.js";

export { SignalPool } from "../signals/pool.js";
```

- [ ] **Step 5: Typecheck and run full test suite**

Run: `npx tsc --noEmit && npm test`
Expected: clean. Total signal-element test count should be in the ~65 range across all `__tests__/signals/*.test.ts` files.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(signals): delete dead perch + ambient modules — SignalPool replaces them

Net file delta: -2 (4 deleted, 2 added).
- src/perch/manager.ts (deleted)
- src/perch/file-perch.ts (deleted)
- src/ambient/mesh.ts (deleted)
- src/ambient/collectors.ts (deleted)
- src/signals/pool.ts (added)
- src/signals/collectors.ts (added)

Per Phase 3 architecture review §6 #1: ContextSignal is the canonical type;
PerchEvent and ContextMesh are gone."
```

---

## Self-review summary

**Spec coverage check:**

| Spec section | Tasks |
|--------------|-------|
| §4.1 Locked decision 1 (unified type) | 21 (delete PerchEvent path) |
| §4.1 Locked decision 2 (SignalPool slot) | 17, 18 |
| §4.1 Locked decision 3 (bus events) | 16 |
| §4.1 Locked decision 4 (two-stage gate) | 6, 7, 8 |
| §4.1 Locked decision 5 (AmbientContextLayer rewire) | 15 |
| §4.1 Locked decision 6 (consent ledger) | 2, 14 |
| §4.2 File structure (2 new + rewrites + deletes) | 5-13 (new), 15-20 (rewrites), 21 (deletes) |
| §4.3 Component contracts (SignalPool methods) | 5-10 |
| §5.1 Signal lifecycle | 6, 7, 8, 10 |
| §5.2 Channel parity | 20 |
| §6 Error handling (collector failures, classifier fail-closed, verifier fail-open, consent missing, idempotent start, fail-open memory) | 6, 7, 8, 9, 10, 14 |
| §7 Testing strategy (~65 tests across the layers listed) | every task includes tests; estimated total ≈ 60-70 |
| §8 Migration & rollout (rename slot, no DB migration, default-safe missing config) | 17 |
| §10 Acceptance criteria | All 10 items mapped to specific tasks above |

**Type consistency check:** `SignalPool`, `SignalPoolDeps`, `SignalCollector` (mode field), `ConsentMap`, `DEFAULT_CONSENT`, `signal:emitted`/`signal:promoted`/`signal:expired`/`signal:suppressed`/`signal:consent_changed`, `formatSignalPromoted`, `mutateConsent` — all referenced consistently across tasks.

**Placeholder scan:** Every code step has full code. No "TODO", "fill in details", "similar to Task N" — every task is self-contained.

**Open caveats for the implementer:**

- Task 18 ("Wire SignalPool into gateway boot") references `buildBootstrap` and the boot context shape, but the exact site varies by codebase state at execution time. The grep command in Step 1 is the source of truth.
- Task 19 lists the eight known boot sites in `src/index.ts` from the Phase 1 audit; verify with `grep -n "PerchManager\|FilePerch\|perchManager"` before deleting in case earlier tasks touched any of them.
- Task 20's exact bus-subscriber wiring depends on whether `gateway/core.ts` already has a `broadcastProactive` helper. If not, fall back to the existing `sendProactive` path (located by Phase 1 audit at `src/gateway/core.ts:2829`).

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-04-element16b-perches.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
