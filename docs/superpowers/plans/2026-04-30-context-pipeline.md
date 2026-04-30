# ContextPipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 762-line monolithic `ContextBuilder.build()` with a typed, parallel DAG-based `ContextPipeline` that is fast, resilient, budget-aware, and human-like.

**Architecture:** 28 `ContextLayer` instances declared in a registry; `DAGPlanner` (Kahn's topological sort) groups them into parallel batches; `ContextPipeline.run()` executes batches via `Promise.all()` with per-layer circuit breakers, LRU cache, and token budget. Two net-new capabilities: `InnerMonologueLayer` (owl voice persists across turns) and `UserPersonaSynthesizer` (LLM-synthesised user character card).

**Tech Stack:** TypeScript ESM, better-sqlite3, vitest, Node.js EventEmitter, cosine-similarity

---

## File Map

**Create:**
- `src/context/layer.ts` — all core types
- `src/context/triage.ts` — computeTriage()
- `src/context/utils.ts` — resolveUserId(), hash()
- `src/context/budget-controller.ts` — BudgetController
- `src/context/dag-planner.ts` — DAGPlanner
- `src/context/cache.ts` — ContextCache (LRU + userIndex)
- `src/context/circuit-breaker.ts` — LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore
- `src/context/pipeline.ts` — ContextPipeline runner
- `src/context/user-persona-synthesizer.ts` — UserPersonaSynthesizer
- `src/context/unified-memory-retriever.ts` — UnifiedMemoryRetriever
- `src/context/layers/identity.ts` — SynthesisIdentityLayer
- `src/context/layers/inner-monologue.ts` — InnerMonologueLayer
- `src/context/layers/working-memory.ts` — WorkingMemoryDigestLayer, ContinuityPriorResponseLayer, CompressionSummaryLayer
- `src/context/layers/user-memory.ts` — CrossSessionFactsLayer, OpenTasksLayer, RelationshipContextLayer
- `src/context/layers/user-persona.ts` — UserPersonaLayer
- `src/context/layers/behavioral.ts` — BehavioralPatchLayer, ActiveIntentsLayer, OwlLearningsLayer
- `src/context/layers/infrastructure.ts` — TemporalAwarenessLayer, ChannelFormatHintLayer, ModeDirectiveLayer, SocraticModeLayer
- `src/context/layers/memory-retrieval.ts` — UnifiedMemoryRetrievalLayer
- `src/context/layers/knowledge.ts` — KnowledgeGraphLayer, RelevantPelletsLayer
- `src/context/layers/profile.ts` — UserBehaviorProfileLayer, InferredPreferencesLayer, PredictedNeedsLayer
- `src/context/layers/ambient.ts` — CollabContextLayer, AmbientContextLayer
- `src/context/layers/calibration.ts` — DepthDirectiveLayer, OpinionInjectionLayer, UserMentalModelLayer, EchoChamberGuardLayer, GroundStateLayer
- `src/context/index.ts` — barrel + createContextPipeline()
- `__tests__/context/budget-controller.test.ts`
- `__tests__/context/dag-planner.test.ts`
- `__tests__/context/cache.test.ts`
- `__tests__/context/circuit-breaker.test.ts`
- `__tests__/context/pipeline.test.ts`
- `__tests__/context/triage.test.ts`
- `__tests__/context/user-persona-synthesizer.test.ts`
- `__tests__/context/unified-memory-retriever.test.ts`
- `__tests__/context/pipeline-integration.test.ts`

**Modify:**
- `src/events/bus.ts` — add 4 new event types
- `src/memory/db.ts` — schema v13 (user_personas + idx_pellets_tag)
- `src/memory/conversation-digest.ts` — add StoredMonologue + lastInnerMonologue
- `src/gateway/handlers/context-builder.ts` — thin adapter (~120 lines)
- `src/gateway/types.ts` — add contextPipeline?, contextCache?, userPersonaSynthesizer?
- `src/gateway/core.ts` — instantiate pipeline subsystems
- `src/gateway/handlers/post-processor.ts` — store InnerMonologue after each response

**Delete:**
- `src/memory/context-builder.ts`

---

### Task 1: Core Types

**Files:**
- Create: `src/context/layer.ts`
- Test: `__tests__/context/triage.test.ts` (partial — types validated by compilation)

- [ ] **Step 1: Create `src/context/layer.ts`**

```typescript
import type { Session } from "../memory/store.js";
import type { GatewayCallbacks } from "../gateway/types.js";
import type { ConversationDigest } from "../memory/conversation-digest.js";
import type { ContinuityResult } from "../cognition/continuity-engine.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { PelletStore } from "../pellets/store.js";
import type { MemoryBus } from "../memory/bus.js";
import type { SessionStore } from "../memory/store.js";
import type { EventBus } from "../events/bus.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ContinuityClass } from "../cognition/continuity-engine.js";

export type { ContinuityClass };

export interface ContextDependencies {
  intelligenceRouter: IntelligenceRouter;
  pelletStore: PelletStore;
  memoryBus: MemoryBus;
  sessionStore: SessionStore;
  eventBus: EventBus;
  config: StackOwlConfig;
}

export interface TriageSignals {
  userMessage: string;
  isConversational: boolean;
  hasFrustration: boolean;
  isOpinionRequest: boolean;
  hasTemporalTrigger: boolean;
  isReturningUser: boolean;
  sessionDepth: number;
  hasActiveItems: boolean;
  effectiveUserId: string;
  continuityClass: ContinuityClass | null;
}

export interface ContextRequest {
  readonly session: Session;
  readonly callbacks: GatewayCallbacks;
  readonly channelId?: string;
  readonly userId?: string;
  readonly continuityResult: ContinuityResult | null;
  readonly digest: ConversationDigest | null;
  readonly deps: ContextDependencies;
}

export type LayerResults = ReadonlyMap<string, string>;

export type SkippedReason =
  | "shouldFire=false"
  | "circuit_open"
  | "budget_exhausted"
  | "pipeline_timeout"
  | `error: ${string}`;

export interface ContextBuildTraceEntry {
  layerName: string;
  priority: number;
  batchIndex: number;
  fired: boolean;
  cacheHit: boolean;
  tokensUsed: number;
  durationMs: number;
  skippedReason?: SkippedReason;
}

export type ContextBuildTrace = ContextBuildTraceEntry[];

export interface ContextLayer {
  name: string;
  priority: number;
  maxTokens: number;
  produces: string[];
  dependsOn: string[];
  alwaysInclude?: boolean;
  shouldFire(triage: TriageSignals): boolean;
  build(req: ContextRequest, triage: TriageSignals, deps: LayerResults): Promise<string>;
  getCacheKey?(req: ContextRequest, triage: TriageSignals): string | null;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /Users/bakirtalibov/Desktop/stackowl-personal-ai-assistants
npx tsc --noEmit --project tsconfig.json 2>&1 | head -20
```

Expected: zero errors from `src/context/layer.ts` (other pre-existing errors OK).

- [ ] **Step 3: Commit**

```bash
git add src/context/layer.ts
git commit -m "feat(context): add ContextLayer, ContextDependencies, TriageSignals, ContextRequest types"
```

---

### Task 2: Triage + Utils

**Files:**
- Create: `src/context/triage.ts`
- Create: `src/context/utils.ts`
- Create: `__tests__/context/triage.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/triage.test.ts
import { describe, it, expect } from "vitest";
import { computeTriage } from "../../src/context/triage.js";
import { hash, resolveUserId } from "../../src/context/utils.js";

describe("computeTriage", () => {
  const base = {
    sessionDepth: 3,
    continuityClass: null as any,
    userId: "u1",
    sessionId: "s1",
    hasActiveItems: false,
  };

  it("marks short message as conversational", () => {
    const t = computeTriage({ ...base, userMessage: "hey there" });
    expect(t.isConversational).toBe(true);
  });

  it("marks long message as non-conversational", () => {
    const msg = "Please help me debug this issue with my trading bot that keeps crashing";
    const t = computeTriage({ ...base, userMessage: msg });
    expect(t.isConversational).toBe(false);
  });

  it("detects frustration keywords", () => {
    const t = computeTriage({ ...base, userMessage: "still not working again" });
    expect(t.hasFrustration).toBe(true);
  });

  it("detects opinion request", () => {
    const t = computeTriage({ ...base, userMessage: "what do you think about this?" });
    expect(t.isOpinionRequest).toBe(true);
  });

  it("detects temporal trigger", () => {
    const t = computeTriage({ ...base, userMessage: "remember last time we did this?" });
    expect(t.hasTemporalTrigger).toBe(true);
  });

  it("marks FRESH_START as returning user", () => {
    const t = computeTriage({ ...base, userMessage: "hi", continuityClass: "FRESH_START" });
    expect(t.isReturningUser).toBe(true);
  });

  it("uses sessionId as effectiveUserId when userId absent", () => {
    const t = computeTriage({ ...base, userMessage: "hi", userId: undefined });
    expect(t.effectiveUserId).toBe("s1");
  });
});

describe("hash", () => {
  it("returns same string for same input", () => {
    expect(hash("abc")).toBe(hash("abc"));
  });
  it("returns different strings for different inputs", () => {
    expect(hash("abc")).not.toBe(hash("xyz"));
  });
});
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
npx vitest run __tests__/context/triage.test.ts 2>&1 | tail -10
```

Expected: FAIL with "Cannot find module".

- [ ] **Step 3: Create `src/context/utils.ts`**

```typescript
export function resolveUserId(userId?: string, sessionId?: string): string {
  return userId ?? sessionId ?? "anonymous";
}

export function hash(input: string): string {
  let h = 5381;
  for (let i = 0; i < input.length; i++) {
    h = ((h << 5) + h) ^ input.charCodeAt(i);
  }
  return (h >>> 0).toString(36);
}
```

- [ ] **Step 4: Create `src/context/triage.ts`**

```typescript
import type { TriageSignals, ContinuityClass } from "./layer.js";
import { resolveUserId } from "./utils.js";

const FRUSTRATION = /\b(still|again|not working|doesn't work|broken|failed|keeps|why is|why does|wtf)\b/i;
const OPINION = /\b(what do you think|what's your (take|opinion|view)|do you (think|agree|believe)|your thoughts)\b/i;
const TEMPORAL = /\b(last time|yesterday|remember when|before|previously|earlier|last week|back then)\b/i;
const ACTION_KW = /\b(create|build|fix|debug|write|generate|analyze|deploy|install|setup|configure|run|execute)\b/i;

interface TriageInput {
  userMessage: string;
  sessionDepth: number;
  continuityClass: ContinuityClass | null;
  userId?: string;
  sessionId?: string;
  hasActiveItems: boolean;
}

export function computeTriage(input: TriageInput): TriageSignals {
  const { userMessage, sessionDepth, continuityClass, hasActiveItems } = input;
  const isShort = userMessage.trim().length < 80;
  const hasAction = ACTION_KW.test(userMessage);

  return {
    userMessage,
    isConversational: isShort && !hasAction,
    hasFrustration: FRUSTRATION.test(userMessage),
    isOpinionRequest: OPINION.test(userMessage),
    hasTemporalTrigger: TEMPORAL.test(userMessage),
    isReturningUser: continuityClass === "FRESH_START" || continuityClass === "TOPIC_SWITCH",
    sessionDepth,
    hasActiveItems,
    effectiveUserId: resolveUserId(input.userId, input.sessionId),
    continuityClass,
  };
}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/triage.test.ts 2>&1 | tail -5
```

Expected: all 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/context/triage.ts src/context/utils.ts __tests__/context/triage.test.ts
git commit -m "feat(context): add computeTriage() and utils (hash, resolveUserId)"
```

---

### Task 3: BudgetController

**Files:**
- Create: `src/context/budget-controller.ts`
- Create: `__tests__/context/budget-controller.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/budget-controller.test.ts
import { describe, it, expect } from "vitest";
import { BudgetController, estimateTokens } from "../../src/context/budget-controller.js";

describe("estimateTokens", () => {
  it("estimates based on char/3.8 ratio", () => {
    expect(estimateTokens("hello")).toBe(Math.ceil(5 / 3.8));
  });
});

describe("BudgetController", () => {
  it("allows output within layer cap", () => {
    const b = new BudgetController(1000);
    const out = b.apply("L1", "hello world", 50);
    expect(out).toBe("hello world");
    expect(b.consumed).toBeGreaterThan(0);
  });

  it("trims output exceeding layer maxTokens", () => {
    const b = new BudgetController(10000);
    const long = "word ".repeat(200); // ~1000 tokens
    const out = b.apply("L1", long, 10);
    expect(estimateTokens(out)).toBeLessThanOrEqual(11); // slight margin
    expect(out).toContain("…[trimmed]");
  });

  it("returns empty string when global ceiling exhausted", () => {
    const b = new BudgetController(5);
    b.apply("L1", "hello world entire long string", 100);
    const out = b.apply("L2", "anything", 100);
    expect(out).toBe("");
  });

  it("reset() clears consumed counter", () => {
    const b = new BudgetController(1000);
    b.apply("L1", "hello world", 50);
    expect(b.consumed).toBeGreaterThan(0);
    b.reset();
    expect(b.consumed).toBe(0);
  });

  it("trims at sentence boundary when possible", () => {
    const b = new BudgetController(10000);
    const text = "First sentence. Second sentence. Third sentence.";
    const out = b.apply("L1", text, 4); // ~4 tokens = ~15 chars
    expect(out.endsWith(".") || out.endsWith("…[trimmed]")).toBe(true);
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/budget-controller.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/budget-controller.ts`**

```typescript
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.8);
}

export class BudgetController {
  private _consumed = 0;

  constructor(private globalCeiling: number = 8_000) {}

  get remaining(): number { return Math.max(0, this.globalCeiling - this._consumed); }
  get consumed(): number { return this._consumed; }

  reset(): void { this._consumed = 0; }

  apply(layerName: string, text: string, maxTokens: number): string {
    if (this.remaining <= 0) return "";

    const cap = Math.min(maxTokens, this.remaining);
    const capChars = Math.floor(cap * 3.8);

    if (text.length <= capChars) {
      this._consumed += estimateTokens(text);
      return text;
    }

    // Try to trim at sentence boundary
    const trimmed = this.trimAtBoundary(text, capChars);
    this._consumed += estimateTokens(trimmed);
    return trimmed;
  }

  private trimAtBoundary(text: string, maxChars: number): string {
    const hard = text.slice(0, maxChars - 12); // reserve room for suffix
    const lastSentence = Math.max(
      hard.lastIndexOf(". "),
      hard.lastIndexOf("! "),
      hard.lastIndexOf("? "),
      hard.lastIndexOf(".\n"),
    );
    if (lastSentence > maxChars * 0.5) {
      return hard.slice(0, lastSentence + 1) + " …[trimmed]";
    }
    return hard + "…[trimmed]";
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/budget-controller.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/budget-controller.ts __tests__/context/budget-controller.test.ts
git commit -m "feat(context): add BudgetController with per-layer cap + global ceiling"
```

---

### Task 4: DAGPlanner

**Files:**
- Create: `src/context/dag-planner.ts`
- Create: `__tests__/context/dag-planner.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/dag-planner.test.ts
import { describe, it, expect } from "vitest";
import { DAGPlanner, CircularDependencyError } from "../../src/context/dag-planner.js";
import type { ContextLayer } from "../../src/context/layer.js";

function makeLayer(name: string, produces: string[], dependsOn: string[], priority = 50): ContextLayer {
  return {
    name, priority, maxTokens: 100, produces, dependsOn,
    shouldFire: () => true,
    build: async () => name,
  };
}

describe("DAGPlanner", () => {
  it("puts independent layers in batch 0", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], []),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches[0]).toHaveLength(2);
    expect(batches).toHaveLength(1);
  });

  it("puts dependent layer in next batch", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], ["a"]),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches).toHaveLength(2);
    expect(batches[0][0].name).toBe("A");
    expect(batches[1][0].name).toBe("B");
  });

  it("sorts by priority ascending within batch", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], [], 80),
      makeLayer("B", ["b"], [], 10),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches[0][0].name).toBe("B");
    expect(batches[0][1].name).toBe("A");
  });

  it("throws CircularDependencyError on cycle", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], ["b"]),
      makeLayer("B", ["b"], ["a"]),
    ];
    expect(() => planner.buildBatches(layers)).toThrow(CircularDependencyError);
  });

  it("handles 3-level chain", () => {
    const planner = new DAGPlanner();
    const layers = [
      makeLayer("A", ["a"], []),
      makeLayer("B", ["b"], ["a"]),
      makeLayer("C", ["c"], ["b"]),
    ];
    const batches = planner.buildBatches(layers);
    expect(batches).toHaveLength(3);
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/dag-planner.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/dag-planner.ts`**

```typescript
import type { ContextLayer } from "./layer.js";

export class CircularDependencyError extends Error {
  constructor(public cycle: string[]) {
    super(`Circular dependency detected: ${cycle.join(" → ")}`);
    this.name = "CircularDependencyError";
  }
}

export class DAGPlanner {
  buildBatches(layers: ContextLayer[]): ContextLayer[][] {
    // Map: produced-key → layer that produces it
    const producers = new Map<string, ContextLayer>();
    for (const layer of layers) {
      for (const key of layer.produces) {
        producers.set(key, layer);
      }
    }

    // In-degree map: how many unresolved deps each layer has
    const inDegree = new Map<string, number>();
    const dependants = new Map<string, string[]>(); // produced-key → layers that need it

    for (const layer of layers) {
      let deg = 0;
      for (const dep of layer.dependsOn) {
        if (producers.has(dep)) {
          deg++;
          const list = dependants.get(dep) ?? [];
          list.push(layer.name);
          dependants.set(dep, list);
        }
        // deps with no producer are treated as always-available (empty string)
      }
      inDegree.set(layer.name, deg);
    }

    const byName = new Map(layers.map((l) => [l.name, l]));
    const batches: ContextLayer[][] = [];
    let remaining = new Set(layers.map((l) => l.name));

    while (remaining.size > 0) {
      const ready = [...remaining].filter((name) => inDegree.get(name) === 0);
      if (ready.length === 0) {
        // Cycle — report remaining names as cycle
        throw new CircularDependencyError([...remaining]);
      }
      const batch = ready
        .map((name) => byName.get(name)!)
        .sort((a, b) => a.priority - b.priority);
      batches.push(batch);

      for (const name of ready) {
        remaining.delete(name);
        const layer = byName.get(name)!;
        for (const produced of layer.produces) {
          for (const dependant of dependants.get(produced) ?? []) {
            inDegree.set(dependant, (inDegree.get(dependant) ?? 1) - 1);
          }
        }
      }
    }

    return batches;
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/dag-planner.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/dag-planner.ts __tests__/context/dag-planner.test.ts
git commit -m "feat(context): add DAGPlanner with Kahn's topological sort + cycle detection"
```

---

### Task 5: ContextCache

**Files:**
- Create: `src/context/cache.ts`
- Create: `__tests__/context/cache.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/cache.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ContextCache } from "../../src/context/cache.js";

describe("ContextCache", () => {
  let cache: ContextCache;
  beforeEach(() => { cache = new ContextCache(3); });

  it("returns null for cache miss", () => {
    expect(cache.get("L1", "key1")).toBeNull();
  });

  it("returns stored value within TTL", () => {
    cache.set("L1", "key1", "hello", 60_000);
    expect(cache.get("L1", "key1")).toBe("hello");
  });

  it("returns null after TTL expires", () => {
    vi.useFakeTimers();
    cache.set("L1", "key1", "hello", 100);
    vi.advanceTimersByTime(200);
    expect(cache.get("L1", "key1")).toBeNull();
    vi.useRealTimers();
  });

  it("evicts oldest entry when over maxEntries", () => {
    cache.set("L1", "k1", "v1", 60_000);
    cache.set("L2", "k2", "v2", 60_000);
    cache.set("L3", "k3", "v3", 60_000);
    cache.set("L4", "k4", "v4", 60_000); // evicts k1
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L4", "k4")).toBe("v4");
  });

  it("invalidate() removes all entries for a layer", () => {
    cache.set("L1", "k1", "v1", 60_000);
    cache.set("L1", "k2", "v2", 60_000);
    cache.invalidate("L1");
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L1", "k2")).toBeNull();
  });

  it("invalidateUser() removes all entries for a userId via reverse index", () => {
    cache.set("L1", "k1", "v1", 60_000, "user42");
    cache.set("L2", "k2", "v2", 60_000, "user42");
    cache.set("L3", "k3", "v3", 60_000, "user99");
    cache.invalidateUser("user42");
    expect(cache.get("L1", "k1")).toBeNull();
    expect(cache.get("L2", "k2")).toBeNull();
    expect(cache.get("L3", "k3")).toBe("v3"); // untouched
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/cache.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/cache.ts`**

```typescript
interface CacheEntry {
  output: string;
  tokensUsed: number;
  cachedAt: number;
  ttlMs: number;
}

export class ContextCache {
  // Primary store: fullKey → entry. Map insertion order = LRU order.
  private store = new Map<string, CacheEntry>();
  // Reverse index: userId → Set of fullKeys
  private userIndex = new Map<string, Set<string>>();
  private hits = 0;
  private misses = 0;
  private evictions = 0;

  constructor(private maxEntries: number = 200) {}

  private fullKey(layerName: string, cacheKey: string): string {
    return `${layerName}:${cacheKey}`;
  }

  get(layerName: string, cacheKey: string): string | null {
    const key = this.fullKey(layerName, cacheKey);
    const entry = this.store.get(key);
    if (!entry) { this.misses++; return null; }
    if (Date.now() - entry.cachedAt > entry.ttlMs) {
      this.store.delete(key);
      this.misses++;
      return null;
    }
    // Refresh LRU position
    this.store.delete(key);
    this.store.set(key, entry);
    this.hits++;
    return entry.output;
  }

  set(layerName: string, cacheKey: string, output: string, ttlMs: number, userId?: string): void {
    const key = this.fullKey(layerName, cacheKey);
    // Evict oldest if at capacity
    if (this.store.size >= this.maxEntries) {
      const oldest = this.store.keys().next().value as string;
      this.store.delete(oldest);
      this.evictions++;
    }
    this.store.set(key, {
      output,
      tokensUsed: Math.ceil(output.length / 3.8),
      cachedAt: Date.now(),
      ttlMs,
    });
    if (userId) {
      const keys = this.userIndex.get(userId) ?? new Set();
      keys.add(key);
      this.userIndex.set(userId, keys);
    }
  }

  invalidate(layerName: string): void {
    const prefix = `${layerName}:`;
    for (const key of [...this.store.keys()]) {
      if (key.startsWith(prefix)) this.store.delete(key);
    }
  }

  invalidateUser(userId: string): void {
    const keys = this.userIndex.get(userId);
    if (!keys) return;
    for (const key of keys) this.store.delete(key);
    this.userIndex.delete(userId);
  }

  stats(): { size: number; hitRate: number; evictions: number } {
    const total = this.hits + this.misses;
    return {
      size: this.store.size,
      hitRate: total > 0 ? this.hits / total : 0,
      evictions: this.evictions,
    };
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/cache.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/cache.ts __tests__/context/cache.test.ts
git commit -m "feat(context): add ContextCache with LRU eviction + userIndex O(1) invalidation"
```

---

### Task 6: Circuit Breaker + Health Monitor + Quality Score

**Files:**
- Create: `src/context/circuit-breaker.ts`
- Create: `__tests__/context/circuit-breaker.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/circuit-breaker.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore } from "../../src/context/circuit-breaker.js";
import type { ContextBuildTrace } from "../../src/context/layer.js";

describe("LayerCircuitBreaker", () => {
  it("starts CLOSED", () => {
    const cb = new LayerCircuitBreaker();
    expect(cb.state).toBe("CLOSED");
    expect(cb.shouldBypass()).toBe(false);
  });

  it("trips OPEN after >40% error rate", () => {
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 12; i++) cb.recordFailure(); // 12/20 = 60%
    for (let i = 0; i < 8; i++) cb.recordSuccess(100);
    expect(cb.state).toBe("OPEN");
    expect(cb.shouldBypass()).toBe(true);
  });

  it("transitions to HALF_OPEN after cooldown", () => {
    vi.useFakeTimers();
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 20; i++) cb.recordFailure();
    expect(cb.state).toBe("OPEN");
    vi.advanceTimersByTime(61_000);
    expect(cb.state).toBe("HALF_OPEN");
    vi.useRealTimers();
  });

  it("closes from HALF_OPEN on success probe", () => {
    vi.useFakeTimers();
    const cb = new LayerCircuitBreaker();
    for (let i = 0; i < 20; i++) cb.recordFailure();
    vi.advanceTimersByTime(61_000);
    expect(cb.state).toBe("HALF_OPEN");
    cb.recordSuccess(100);
    expect(cb.state).toBe("CLOSED");
    vi.useRealTimers();
  });
});

describe("LayerHealthMonitor", () => {
  it("returns same breaker for same name", () => {
    const m = new LayerHealthMonitor();
    expect(m.getBreaker("L1")).toBe(m.getBreaker("L1"));
  });

  it("shouldBypass delegates to breaker", () => {
    const m = new LayerHealthMonitor();
    for (let i = 0; i < 20; i++) m.getBreaker("L1").recordFailure();
    expect(m.shouldBypass("L1")).toBe(true);
    expect(m.shouldBypass("L2")).toBe(false);
  });
});

describe("ContextQualityScore", () => {
  it("returns 1.0 for perfect trace", () => {
    const qs = new ContextQualityScore();
    const trace: ContextBuildTrace = [
      { layerName: "L1", priority: 10, batchIndex: 0, fired: true, cacheHit: false, tokensUsed: 100, durationMs: 50 },
      { layerName: "L2", priority: 20, batchIndex: 0, fired: true, cacheHit: false, tokensUsed: 100, durationMs: 50 },
    ];
    const score = qs.compute(trace, 2);
    expect(score).toBeCloseTo(1.0, 1);
  });

  it("returns < 0.6 when most layers skipped", () => {
    const qs = new ContextQualityScore();
    const trace: ContextBuildTrace = Array.from({ length: 10 }, (_, i) => ({
      layerName: `L${i}`, priority: i * 10, batchIndex: 0,
      fired: i < 2, cacheHit: false, tokensUsed: i < 2 ? 50 : 0, durationMs: 10,
    }));
    const score = qs.compute(trace, 10);
    expect(score).toBeLessThan(0.6);
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/circuit-breaker.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/circuit-breaker.ts`**

```typescript
import type { ContextBuildTrace } from "./layer.js";
import type { EventBus } from "../events/bus.js";

type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

const WINDOW = 20;
const ERROR_RATE_THRESHOLD = 0.4;
const LATENCY_P95_THRESHOLD = 1_800;
const COOLDOWN_MS = 60_000;

export class LayerCircuitBreaker {
  private window: Array<{ success: boolean; latencyMs: number }> = [];
  private _state: CircuitState = "CLOSED";
  private openedAt: number | null = null;

  get state(): CircuitState {
    if (this._state === "OPEN" && this.openedAt !== null) {
      if (Date.now() - this.openedAt >= COOLDOWN_MS) {
        this._state = "HALF_OPEN";
      }
    }
    return this._state;
  }

  shouldBypass(): boolean { return this.state === "OPEN"; }

  recordSuccess(latencyMs: number): void {
    this.window.push({ success: true, latencyMs });
    if (this.window.length > WINDOW) this.window.shift();
    if (this._state === "HALF_OPEN") { this._state = "CLOSED"; this.openedAt = null; }
    this.evaluate();
  }

  recordFailure(): void {
    this.window.push({ success: false, latencyMs: 9999 });
    if (this.window.length > WINDOW) this.window.shift();
    if (this._state === "HALF_OPEN") { this._state = "OPEN"; this.openedAt = Date.now(); }
    else this.evaluate();
  }

  private evaluate(): void {
    if (this.window.length < 5) return;
    const errorRate = this.window.filter((e) => !e.success).length / this.window.length;
    const latencies = this.window.map((e) => e.latencyMs).sort((a, b) => a - b);
    const p95 = latencies[Math.floor(latencies.length * 0.95)] ?? 0;
    if (errorRate > ERROR_RATE_THRESHOLD || p95 > LATENCY_P95_THRESHOLD) {
      this._state = "OPEN";
      this.openedAt = Date.now();
    }
  }
}

export class LayerHealthMonitor {
  private breakers = new Map<string, LayerCircuitBreaker>();

  getBreaker(layerName: string): LayerCircuitBreaker {
    let cb = this.breakers.get(layerName);
    if (!cb) { cb = new LayerCircuitBreaker(); this.breakers.set(layerName, cb); }
    return cb;
  }

  shouldBypass(layerName: string): boolean {
    return this.getBreaker(layerName).shouldBypass();
  }

  getReport(): Record<string, { state: string; errorRate: number }> {
    const out: Record<string, { state: string; errorRate: number }> = {};
    for (const [name, cb] of this.breakers) {
      out[name] = { state: cb.state, errorRate: 0 };
    }
    return out;
  }
}

export class ContextQualityScore {
  constructor(private eventBus?: EventBus) {}

  compute(trace: ContextBuildTrace, totalLayers: number): number {
    if (totalLayers === 0) return 1;
    const fired = trace.filter((e) => e.fired);
    const signalRatio = fired.length / totalLayers;
    const totalTokens = trace.reduce((s, e) => s + e.tokensUsed, 0);
    const tokenEfficiency = totalTokens > 0 ? Math.min(1, totalTokens / 8000) : 0.5;
    const score = signalRatio * 0.4 + tokenEfficiency * 0.3 + 0.3; // dupScore=1 (no dedup tracking here)
    const clamped = Math.min(1, Math.max(0, score));
    if (clamped < 0.6 && this.eventBus) {
      this.eventBus.emit("context:quality_degraded" as any, { score: clamped, trace } as any);
    }
    return clamped;
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/circuit-breaker.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/circuit-breaker.ts __tests__/context/circuit-breaker.test.ts
git commit -m "feat(context): add LayerCircuitBreaker + LayerHealthMonitor + ContextQualityScore"
```

---

### Task 7: ContextPipeline Runner

**Files:**
- Create: `src/context/pipeline.ts`
- Create: `__tests__/context/pipeline.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/pipeline.test.ts
import { describe, it, expect, vi } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import type { ContextLayer, TriageSignals, ContextRequest, LayerResults } from "../../src/context/layer.js";

function mockTriage(): TriageSignals {
  return { userMessage: "hi", isConversational: true, hasFrustration: false,
    isOpinionRequest: false, hasTemporalTrigger: false, isReturningUser: false,
    sessionDepth: 1, hasActiveItems: false, effectiveUserId: "u1", continuityClass: null };
}
function mockReq(): ContextRequest {
  return { session: { id: "s1" } as any, callbacks: {} as any,
    continuityResult: null, digest: null, deps: {} as any };
}
function makeLayer(name: string, produces: string[], dependsOn: string[], output: string, priority = 50): ContextLayer {
  return { name, priority, maxTokens: 500, produces, dependsOn,
    shouldFire: () => true,
    build: async () => output };
}

describe("ContextPipeline", () => {
  it("runs all layers and concatenates output", async () => {
    const layers = [makeLayer("A", ["a"], [], "hello "), makeLayer("B", ["b"], [], "world")];
    const pipeline = new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("hello");
    expect(output).toContain("world");
  });

  it("skips layer when shouldFire returns false", async () => {
    const layer: ContextLayer = { name: "Skip", priority: 10, maxTokens: 100,
      produces: ["x"], dependsOn: [], shouldFire: () => false, build: async () => "SHOULD_NOT_APPEAR" };
    const pipeline = new ContextPipeline([layer], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output, trace } = await pipeline.run(mockReq(), mockTriage());
    expect(output).not.toContain("SHOULD_NOT_APPEAR");
    expect(trace[0].fired).toBe(false);
    expect(trace[0].skippedReason).toBe("shouldFire=false");
  });

  it("isolates layer error — other layers still run", async () => {
    const bad: ContextLayer = { name: "Bad", priority: 10, maxTokens: 100,
      produces: ["bad"], dependsOn: [], shouldFire: () => true, build: async () => { throw new Error("boom"); } };
    const good = makeLayer("Good", ["good"], [], "good output", 20);
    const pipeline = new ContextPipeline([bad, good], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { output } = await pipeline.run(mockReq(), mockTriage());
    expect(output).toContain("good output");
  });

  it("returns cache hit on second run", async () => {
    const buildFn = vi.fn(async () => "cached value");
    const layer: ContextLayer = { name: "Cached", priority: 10, maxTokens: 100,
      produces: ["c"], dependsOn: [], shouldFire: () => true, build: buildFn,
      getCacheKey: () => "stable-key" };
    const cache = new ContextCache();
    const pipeline = new ContextPipeline([layer], cache, new LayerHealthMonitor(), new DAGPlanner());
    await pipeline.run(mockReq(), mockTriage());
    const { trace } = await pipeline.run(mockReq(), mockTriage());
    expect(buildFn).toHaveBeenCalledTimes(1);
    expect(trace[0].cacheHit).toBe(true);
  });

  it("includes trace entry per layer", async () => {
    const layers = [makeLayer("A", ["a"], [], "a"), makeLayer("B", ["b"], [], "b")];
    const pipeline = new ContextPipeline(layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner());
    const { trace } = await pipeline.run(mockReq(), mockTriage());
    expect(trace).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/pipeline.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/pipeline.ts`**

```typescript
import { BudgetController } from "./budget-controller.js";
import type { ContextCache } from "./cache.js";
import type { LayerHealthMonitor } from "./circuit-breaker.js";
import { DAGPlanner } from "./dag-planner.js";
import type { ContextLayer, ContextRequest, TriageSignals, ContextBuildTrace, ContextBuildTraceEntry } from "./layer.js";
import { log } from "../logger.js";

const LAYER_TIMEOUT_MS = 2_000;
const PIPELINE_TIMEOUT_MS = 5_000;

export class ContextPipeline {
  private readonly batches: ContextLayer[][];

  constructor(
    private readonly layers: ContextLayer[],
    private readonly cache: ContextCache,
    private readonly healthMonitor: LayerHealthMonitor,
    dagPlanner: DAGPlanner,
  ) {
    this.batches = dagPlanner.buildBatches(layers);
  }

  async run(
    request: ContextRequest,
    triage: TriageSignals,
    options?: { timeoutMs?: number; globalTokenCeiling?: number },
  ): Promise<{ output: string; trace: ContextBuildTrace }> {
    const budget = new BudgetController(options?.globalTokenCeiling ?? 8_000);
    const results = new Map<string, string>();
    const trace: ContextBuildTrace = [];
    const pipelineDeadline = Date.now() + (options?.timeoutMs ?? PIPELINE_TIMEOUT_MS);

    for (let batchIdx = 0; batchIdx < this.batches.length; batchIdx++) {
      const batch = this.batches[batchIdx];
      await Promise.all(batch.map((layer) =>
        this.executeLayer(layer, request, triage, results, budget, trace, batchIdx, pipelineDeadline)
      ));
    }

    const output = this.layers
      .sort((a, b) => a.priority - b.priority)
      .map((l) => results.get(l.produces[0] ?? l.name) ?? "")
      .filter(Boolean)
      .join("\n");

    const fired = trace.filter((e) => e.fired).length;
    const cacheHits = trace.filter((e) => e.cacheHit).length;
    log.engine.info(
      `[ContextPipeline] ${fired} fired (${cacheHits} cache hits), ` +
      `${trace.length - fired} skipped — ${budget.consumed}/${budget.consumed + budget.remaining} tokens`
    );

    return { output, trace };
  }

  private async executeLayer(
    layer: ContextLayer,
    request: ContextRequest,
    triage: TriageSignals,
    results: Map<string, string>,
    budget: BudgetController,
    trace: ContextBuildTrace,
    batchIndex: number,
    deadline: number,
  ): Promise<void> {
    const start = Date.now();

    const skip = (skippedReason: ContextBuildTraceEntry["skippedReason"]) => {
      for (const key of layer.produces) results.set(key, "");
      trace.push({ layerName: layer.name, priority: layer.priority, batchIndex,
        fired: false, cacheHit: false, tokensUsed: 0, durationMs: Date.now() - start, skippedReason });
    };

    if (Date.now() > deadline) return skip("pipeline_timeout");

    const shouldFire = layer.alwaysInclude || layer.shouldFire(triage);
    if (!shouldFire) return skip("shouldFire=false");

    if (!layer.alwaysInclude && this.healthMonitor.shouldBypass(layer.name)) return skip("circuit_open");

    // Resolve deps map for this layer
    const deps = new Map<string, string>();
    for (const depKey of layer.dependsOn) deps.set(depKey, results.get(depKey) ?? "");

    // Cache check
    const cacheKey = layer.getCacheKey?.(request, triage) ?? null;
    if (cacheKey) {
      const cached = this.cache.get(layer.name, cacheKey);
      if (cached !== null) {
        const budgeted = budget.apply(layer.name, cached, layer.maxTokens);
        for (const key of layer.produces) results.set(key, budgeted);
        trace.push({ layerName: layer.name, priority: layer.priority, batchIndex,
          fired: true, cacheHit: true, tokensUsed: Math.ceil(budgeted.length / 3.8), durationMs: Date.now() - start });
        return;
      }
    }

    try {
      const output = await Promise.race([
        layer.build(request, triage, deps as any),
        new Promise<string>((_, reject) => setTimeout(() => reject(new Error("timeout")), LAYER_TIMEOUT_MS)),
      ]);

      this.healthMonitor.getBreaker(layer.name).recordSuccess(Date.now() - start);
      const budgeted = budget.apply(layer.name, output, layer.maxTokens);

      if (cacheKey && budgeted) {
        const userId = triage.effectiveUserId;
        this.cache.set(layer.name, cacheKey, budgeted, 300_000, userId);
      }

      for (const key of layer.produces) results.set(key, budgeted);
      trace.push({ layerName: layer.name, priority: layer.priority, batchIndex,
        fired: true, cacheHit: false, tokensUsed: Math.ceil(budgeted.length / 3.8), durationMs: Date.now() - start });
    } catch (err) {
      this.healthMonitor.getBreaker(layer.name).recordFailure();
      for (const key of layer.produces) results.set(key, "");
      trace.push({ layerName: layer.name, priority: layer.priority, batchIndex,
        fired: false, cacheHit: false, tokensUsed: 0, durationMs: Date.now() - start,
        skippedReason: `error: ${err instanceof Error ? err.message : String(err)}` });
    }
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/pipeline.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/pipeline.ts __tests__/context/pipeline.test.ts
git commit -m "feat(context): add ContextPipeline runner with DAG batching, cache, circuit breaker, trace"
```

---

### Task 8: EventBus New Events + Schema v13

**Files:**
- Modify: `src/events/bus.ts`
- Modify: `src/memory/db.ts`

- [ ] **Step 1: Add 4 new event types to `src/events/bus.ts`**

In `EventPayloads` interface, after the `"job:complete"` entry (line ~190), add:

```typescript
  // ─── Context Pipeline Events ─────────────────────────────────
  "pellet:written": { id: string; tag: string };
  "persona:refreshed": { userId: string };
  "learning:recorded": { owlName: string; category: string };
  "context:quality_degraded": { score: number; trace: unknown };
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npx tsc --noEmit 2>&1 | grep "bus.ts" | head -5
```

Expected: no errors from `bus.ts`.

- [ ] **Step 3: Update `src/memory/db.ts` — bump SCHEMA_VERSION and add v13 migration**

Change line 29: `const SCHEMA_VERSION = 12;` → `const SCHEMA_VERSION = 13;`

After the `if (current < 12)` block (around line 1059), add before the final pragma block:

```typescript
    if (current < 13) {
      // v13: ContextPipeline — user persona cache + pellets tag index
      this.db.exec(`
        CREATE TABLE IF NOT EXISTS user_personas (
          user_id        TEXT PRIMARY KEY,
          persona_json   TEXT NOT NULL,
          synthesized_at TEXT NOT NULL,
          expires_at     INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_pellets_tag ON pellets(tag);
      `);
    }
```

- [ ] **Step 4: Add UserPersonasRepo methods to MemoryDatabase class in `src/memory/db.ts`**

Near the end of the `MemoryDatabase` class, before the closing `}`, add:

```typescript
  // ── UserPersonas ────────────────────────────────────────────────

  getUserPersona(userId: string): { personaJson: string; expiresAt: number } | null {
    const row = this.db.prepare(
      "SELECT persona_json, expires_at FROM user_personas WHERE user_id = ?"
    ).get(userId) as { persona_json: string; expires_at: number } | undefined;
    return row ? { personaJson: row.persona_json, expiresAt: row.expires_at } : null;
  }

  setUserPersona(userId: string, personaJson: string, ttlMs: number): void {
    const expiresAt = Date.now() + ttlMs;
    this.db.prepare(`
      INSERT INTO user_personas (user_id, persona_json, synthesized_at, expires_at)
      VALUES (?, ?, datetime('now'), ?)
      ON CONFLICT(user_id) DO UPDATE SET
        persona_json   = excluded.persona_json,
        synthesized_at = excluded.synthesized_at,
        expires_at     = excluded.expires_at
    `).run(userId, personaJson, expiresAt);
  }
```

- [ ] **Step 5: Run existing tests to confirm schema migration works**

```bash
npx vitest run __tests__/memory/ 2>&1 | tail -10
```

Expected: all existing memory tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/events/bus.ts src/memory/db.ts
git commit -m "feat(context): add EventBus context events + schema v13 (user_personas, idx_pellets_tag)"
```

---

### Task 9: ConversationDigest Extension

**Files:**
- Modify: `src/memory/conversation-digest.ts`

- [ ] **Step 1: Add `StoredMonologue` type and `lastInnerMonologue` field**

In `src/memory/conversation-digest.ts`, after the `DigestArtifact` interface (around line 25), add:

```typescript
export interface StoredMonologue {
  thoughts: string;
  responseIntent: string;
  moodCurrent?: string;
  storedAt: string;
}
```

In the `ConversationDigest` interface, add after `lastAssistantResponse?`:

```typescript
  /** Last turn's inner monologue — written by PostProcessor, read by InnerMonologueLayer */
  lastInnerMonologue?: StoredMonologue;
```

- [ ] **Step 2: Add `setLastMonologue()` method to `ConversationDigestManager`**

Inside `ConversationDigestManager`, after the `update()` method, add:

```typescript
  async setLastMonologue(sessionId: string, monologue: StoredMonologue): Promise<void> {
    const digest = await this.load(sessionId);
    if (!digest) return;
    digest.lastInnerMonologue = monologue;
    await this.save(digest);
  }
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
npx tsc --noEmit 2>&1 | grep "conversation-digest" | head -5
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/memory/conversation-digest.ts
git commit -m "feat(context): extend ConversationDigest with StoredMonologue + setLastMonologue()"
```

---

### Task 10: UserPersonaSynthesizer

**Files:**
- Create: `src/context/user-persona-synthesizer.ts`
- Create: `__tests__/context/user-persona-synthesizer.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/user-persona-synthesizer.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { UserPersonaSynthesizer } from "../../src/context/user-persona-synthesizer.js";

function mockDb() {
  return { getUserPersona: vi.fn(() => null), setUserPersona: vi.fn() } as any;
}
function mockProvider() {
  const persona = JSON.stringify({
    communicationStyle: "technical", expertiseLevel: "expert",
    currentProjects: ["trading bot"], recurringPatterns: ["prefers code first"],
    emotionalTendencies: "direct", emotionalTrajectory: ["focused (2026-04-30)"],
    preferredApproach: "show code first", lastUpdated: new Date().toISOString(),
  });
  return { chat: vi.fn(async () => ({ content: persona })) } as any;
}

describe("UserPersonaSynthesizer", () => {
  it("returns null for new user with < 3 facts", async () => {
    const db = mockDb();
    const synth = new UserPersonaSynthesizer(mockProvider(), db);
    const result = await synth.getPersona("u1", [], [], "");
    expect(result).toBeNull();
  });

  it("calls LLM and caches when > 3 facts and no cache", async () => {
    const db = mockDb();
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const facts = Array.from({ length: 5 }, (_, i) => ({ fact: `fact ${i}`, confidence: 0.9 } as any));
    const result = await synth.getPersona("u1", facts, [], "");
    expect(provider.chat).toHaveBeenCalledOnce();
    expect(db.setUserPersona).toHaveBeenCalledOnce();
    expect(result?.expertiseLevel).toBe("expert");
  });

  it("returns cached persona when not expired", async () => {
    const db = mockDb();
    db.getUserPersona.mockReturnValue({
      personaJson: JSON.stringify({ communicationStyle: "casual", expertiseLevel: "novice",
        currentProjects: [], recurringPatterns: [], emotionalTendencies: "",
        emotionalTrajectory: [], preferredApproach: "", lastUpdated: "" }),
      expiresAt: Date.now() + 60_000,
    });
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const result = await synth.getPersona("u1", [{}] as any, [], "");
    expect(provider.chat).not.toHaveBeenCalled();
    expect(result?.communicationStyle).toBe("casual");
  });

  it("returns stale persona and triggers background refresh when expired", async () => {
    const db = mockDb();
    const stale = { communicationStyle: "verbose", expertiseLevel: "intermediate",
      currentProjects: [], recurringPatterns: [], emotionalTendencies: "",
      emotionalTrajectory: [], preferredApproach: "", lastUpdated: "" };
    db.getUserPersona.mockReturnValue({ personaJson: JSON.stringify(stale), expiresAt: Date.now() - 1 });
    const provider = mockProvider();
    const synth = new UserPersonaSynthesizer(provider, db);
    const facts = Array.from({ length: 5 }, (_, i) => ({ fact: `f${i}`, confidence: 0.9 } as any));
    const result = await synth.getPersona("u1", facts, [], "");
    expect(result?.communicationStyle).toBe("verbose"); // stale returned immediately
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/user-persona-synthesizer.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/user-persona-synthesizer.ts`**

```typescript
import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import type { StoredFact } from "../memory/fact-store.js";
import type { Episode } from "../memory/episodic.js";

export interface UserPersona {
  communicationStyle: "concise" | "verbose" | "technical" | "casual";
  expertiseLevel: "novice" | "intermediate" | "expert";
  currentProjects: string[];
  recurringPatterns: string[];
  emotionalTendencies: string;
  emotionalTrajectory: string[];
  preferredApproach: string;
  lastUpdated: string;
}

const PERSONA_TTL_MS = 30 * 60 * 1000; // 30 minutes
const MIN_FACTS_FOR_PERSONA = 3;

export class UserPersonaSynthesizer {
  private pending = new Set<string>(); // userId → background synthesis in flight

  constructor(
    private provider: ModelProvider,
    private db: MemoryDatabase,
  ) {}

  async getPersona(
    userId: string,
    facts: StoredFact[],
    episodes: Episode[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    if (facts.length < MIN_FACTS_FOR_PERSONA) return null;

    const cached = this.db.getUserPersona(userId);
    if (cached) {
      if (Date.now() < cached.expiresAt) {
        return JSON.parse(cached.personaJson) as UserPersona;
      }
      // Stale-while-revalidate: return stale, refresh in background
      if (!this.pending.has(userId)) {
        this.pending.add(userId);
        setImmediate(() => {
          this.synthesize(userId, facts, episodes, preferenceContext)
            .finally(() => this.pending.delete(userId));
        });
      }
      return JSON.parse(cached.personaJson) as UserPersona;
    }

    // No cache — synthesize synchronously (first-time user)
    return this.synthesize(userId, facts, episodes, preferenceContext);
  }

  async synthesize(
    userId: string,
    facts: StoredFact[],
    episodes: Episode[],
    preferenceContext: string,
  ): Promise<UserPersona | null> {
    try {
      const topFacts = facts
        .sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0))
        .slice(0, 10)
        .map((f) => `- ${f.fact}`)
        .join("\n");
      const topEpisodes = episodes
        .slice(0, 3)
        .map((e) => `- ${e.summary}`)
        .join("\n");

      const prompt = `You are analyzing a user to create a persona profile.

Facts about them:
${topFacts || "None yet"}

Recent episodes:
${topEpisodes || "None yet"}

Preferences:
${preferenceContext || "None recorded"}

Respond with ONLY valid JSON matching this exact schema:
{
  "communicationStyle": "concise|verbose|technical|casual",
  "expertiseLevel": "novice|intermediate|expert",
  "currentProjects": ["project1"],
  "recurringPatterns": ["pattern1"],
  "emotionalTendencies": "one sentence",
  "emotionalTrajectory": ["mood (date)"],
  "preferredApproach": "one sentence",
  "lastUpdated": "ISO date"
}`;

      const response = await this.provider.chat(
        [{ role: "system", content: prompt }, { role: "user", content: "Generate persona." }],
        undefined,
        { temperature: 0.3, maxTokens: 400 },
      );

      const text = response.content.replace(/<\/?(?:think|reasoning)>/gi, "").trim();
      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;

      const persona: UserPersona = { ...JSON.parse(jsonMatch[0]), lastUpdated: new Date().toISOString() };
      this.db.setUserPersona(userId, JSON.stringify(persona), PERSONA_TTL_MS);
      return persona;
    } catch {
      return null;
    }
  }
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/user-persona-synthesizer.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/user-persona-synthesizer.ts __tests__/context/user-persona-synthesizer.test.ts
git commit -m "feat(context): add UserPersonaSynthesizer with stale-while-revalidate persona cache"
```

---

### Task 11: UnifiedMemoryRetriever

**Files:**
- Create: `src/context/unified-memory-retriever.ts`
- Create: `__tests__/context/unified-memory-retriever.test.ts`

- [ ] **Step 1: Write failing tests**

```typescript
// __tests__/context/unified-memory-retriever.test.ts
import { describe, it, expect, vi } from "vitest";
import { UnifiedMemoryRetriever } from "../../src/context/unified-memory-retriever.js";

function mockBus(results: any[] = []) {
  return { recall: vi.fn(async () => results) } as any;
}
function mockFactStore(facts: any[] = []) {
  return { search: vi.fn(() => facts) } as any;
}
function mockEpisodic(episodes: any[] = []) {
  return { search: vi.fn(async () => episodes) } as any;
}

describe("UnifiedMemoryRetriever", () => {
  it("returns empty string when all stores empty", async () => {
    const retriever = new UnifiedMemoryRetriever(mockBus(), mockFactStore(), mockEpisodic());
    const result = await retriever.retrieve("anything", "u1");
    expect(result).toBe("");
  });

  it("returns labeled XML with facts tier", async () => {
    const facts = [{ id: "f1", fact: "User prefers Python", confidence: 0.9, userId: "u1" }];
    const retriever = new UnifiedMemoryRetriever(mockBus(), mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("python", "u1");
    expect(result).toContain("<memory>");
    expect(result).toContain('tier="long_term"');
    expect(result).toContain("User prefers Python");
  });

  it("deduplicates near-identical results across stores", async () => {
    const facts = [{ id: "f1", fact: "User builds trading bots", confidence: 0.9, userId: "u1" }];
    const busResults = [{ id: "bus1", content: "User builds trading bots", source: "reflexion", relevance: 0.8, category: "fact", timestamp: "" }];
    const retriever = new UnifiedMemoryRetriever(mockBus(busResults), mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("trading", "u1");
    // Should only appear once
    const count = (result.match(/trading bots/gi) ?? []).length;
    expect(count).toBe(1);
  });

  it("isolates store failure — other stores still return", async () => {
    const badBus = { recall: vi.fn(async () => { throw new Error("bus down"); }) } as any;
    const facts = [{ id: "f1", fact: "resilience test", confidence: 0.9, userId: "u1" }];
    const retriever = new UnifiedMemoryRetriever(badBus, mockFactStore(facts), mockEpisodic());
    const result = await retriever.retrieve("resilience", "u1");
    expect(result).toContain("resilience test");
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

```bash
npx vitest run __tests__/context/unified-memory-retriever.test.ts 2>&1 | tail -5
```

- [ ] **Step 3: Create `src/context/unified-memory-retriever.ts`**

```typescript
import type { MemoryBus, UnifiedMemory } from "../memory/bus.js";
import type { FactStore } from "../memory/fact-store.js";
import type { EpisodicMemory, Episode } from "../memory/episodic.js";
import { log } from "../logger.js";

export class UnifiedMemoryRetriever {
  constructor(
    private memoryBus: MemoryBus,
    private factStore: FactStore,
    private episodic: EpisodicMemory,
  ) {}

  async retrieve(query: string, userId: string): Promise<string> {
    const [busResults, facts, episodes] = await Promise.all([
      this.memoryBus.recall(query, 10, 2000).catch(() => [] as UnifiedMemory[]),
      Promise.resolve(this.factStore.search(query, userId, 10)).catch(() => []),
      this.episodic.search(query, 5, undefined).catch(() => [] as Episode[]),
    ]);

    if (facts.length === 0 && episodes.length === 0 && busResults.length === 0) return "";

    // Collect all content for dedup
    const seen = new Map<string, { content: string; relevance: number; tier: string }>();

    for (const f of facts) {
      const key = normalize(f.fact);
      if (!seen.has(key)) seen.set(key, { content: f.fact, relevance: f.confidence ?? 0.7, tier: "long_term" });
    }
    for (const e of episodes) {
      const key = normalize(e.summary);
      const existing = seen.get(key);
      if (!existing || e.importance > existing.relevance) {
        seen.set(key, { content: e.summary, relevance: e.importance, tier: "episodic" });
      }
    }
    for (const b of busResults) {
      const key = normalize(b.content);
      const existing = [...seen.values()].find((v) => cosineSim(normalize(v.content), key) > 0.9);
      if (!existing) seen.set(key, { content: b.content, relevance: b.relevance, tier: "semantic" });
    }

    const all = [...seen.values()].sort((a, b) => b.relevance - a.relevance).slice(0, 10);
    const byTier = new Map<string, string[]>();
    for (const item of all) {
      const list = byTier.get(item.tier) ?? [];
      list.push(item.content);
      byTier.set(item.tier, list);
    }

    const lines = ["<memory>"];
    if (byTier.has("long_term")) {
      lines.push(`  <facts tier="long_term" confidence="high">`);
      for (const c of byTier.get("long_term")!) lines.push(`    ${c}`);
      lines.push("  </facts>");
    }
    if (byTier.has("episodic")) {
      lines.push(`  <episodes tier="episodic" recency="recent">`);
      for (const c of byTier.get("episodic")!) lines.push(`    ${c}`);
      lines.push("  </episodes>");
    }
    if (byTier.has("semantic")) {
      lines.push(`  <bus tier="semantic" relevance="high">`);
      for (const c of byTier.get("semantic")!) lines.push(`    ${c}`);
      lines.push("  </bus>");
    }
    lines.push("</memory>");
    return lines.join("\n");
  }
}

function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim().slice(0, 120);
}

function cosineSim(a: string, b: string): number {
  const aWords = new Set(a.split(" ").filter((w) => w.length > 3));
  const bWords = new Set(b.split(" ").filter((w) => w.length > 3));
  if (aWords.size === 0 || bWords.size === 0) return 0;
  const intersection = [...aWords].filter((w) => bWords.has(w)).length;
  return intersection / Math.sqrt(aWords.size * bWords.size);
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
npx vitest run __tests__/context/unified-memory-retriever.test.ts 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/context/unified-memory-retriever.ts __tests__/context/unified-memory-retriever.test.ts
git commit -m "feat(context): add UnifiedMemoryRetriever with tier-labeled XML and cosine dedup"
```

---

### Task 12: Layers — Identity + InnerMonologue

**Files:**
- Create: `src/context/layers/identity.ts`
- Create: `src/context/layers/inner-monologue.ts`

- [ ] **Step 1: Create `src/context/layers/identity.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class SynthesisIdentityLayer implements ContextLayer {
  name = "SynthesisIdentityLayer";
  priority = 10;
  maxTokens = 500;
  produces = ["identity"];
  dependsOn = [];
  alwaysInclude = true;

  shouldFire(_t: TriageSignals): boolean { return true; }

  getCacheKey(req: ContextRequest, _t: TriageSignals): string | null {
    const owlName = (req.session as any).owlName ?? "default";
    return hash(owlName + "v1");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const session = req.session as any;
    const owlName = session.owlName ?? "Assistant";
    const owlPersonality = session.owlPersonality ?? "";
    if (!owlPersonality) return "";
    return `<owl_identity>\n${owlPersonality}\n</owl_identity>`;
  }
}
```

- [ ] **Step 2: Create `src/context/layers/inner-monologue.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

const STALENESS_MS = 10 * 60 * 1000; // 10 minutes

export class InnerMonologueLayer implements ContextLayer {
  name = "InnerMonologueLayer";
  priority = 15;
  maxTokens = 300;
  produces = ["inner_voice"];
  dependsOn = [];

  shouldFire(_t: TriageSignals): boolean { return true; } // alwaysInclude handles firing

  getCacheKey(): string | null { return null; } // never cacheable

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const monologue = req.digest?.lastInnerMonologue;
    if (!monologue) return "";

    const age = Date.now() - new Date(monologue.storedAt).getTime();
    if (age > STALENESS_MS) return ""; // stale — session gap

    return [
      "<owl_inner_voice>",
      `My approach this turn: ${monologue.responseIntent}`,
      monologue.moodCurrent ? `My current disposition: ${monologue.moodCurrent}` : "",
      "</owl_inner_voice>",
    ].filter(Boolean).join("\n");
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/context/layers/identity.ts src/context/layers/inner-monologue.ts
git commit -m "feat(context/layers): add SynthesisIdentityLayer + InnerMonologueLayer"
```

---

### Task 13: Layers — Working Memory Group

**Files:**
- Create: `src/context/layers/working-memory.ts`

- [ ] **Step 1: Create `src/context/layers/working-memory.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class WorkingMemoryDigestLayer implements ContextLayer {
  name = "WorkingMemoryDigestLayer";
  priority = 20;
  maxTokens = 600;
  produces = ["digest"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }

  shouldFire(t: TriageSignals): boolean { return t.sessionDepth > 0; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    if (!req.digest) return "";
    const d = req.digest;
    const lines = ["<conversation_digest>"];
    if (d.task) lines.push(`  <current_task>${d.task}</current_task>`);
    if (d.artifacts?.length) {
      lines.push("  <artifacts_from_last_response>");
      for (const a of d.artifacts.slice(0, 6)) {
        lines.push(`    <artifact type="${a.type}">${a.value}</artifact>`);
      }
      lines.push("  </artifacts_from_last_response>");
    }
    if (d.decisions?.length) {
      lines.push("  <decisions_made>");
      for (const dec of d.decisions) lines.push(`    <decision>${dec}</decision>`);
      lines.push("  </decisions_made>");
    }
    if (d.failed?.length) {
      lines.push("  <already_tried>");
      for (const f of d.failed) lines.push(`    <attempt>${f}</attempt>`);
      lines.push("  </already_tried>");
    }
    lines.push("</conversation_digest>");
    return lines.join("\n");
  }
}

export class ContinuityPriorResponseLayer implements ContextLayer {
  name = "ContinuityPriorResponseLayer";
  priority = 25;
  maxTokens = 2000;
  produces = ["continuity"];
  dependsOn = ["digest"];
  getCacheKey(): string | null { return null; }

  shouldFire(t: TriageSignals): boolean {
    return t.continuityClass === "CONTINUATION" || t.continuityClass === "FOLLOW_UP";
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const lastResponse = req.digest?.lastAssistantResponse;
    if (!lastResponse) return "";
    return `<prior_response>\n${lastResponse.slice(0, 1800)}\n</prior_response>`;
  }
}

export class CompressionSummaryLayer implements ContextLayer {
  name = "CompressionSummaryLayer";
  priority = 30;
  maxTokens = 800;
  produces = ["compression"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }

  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const summary = (req.session as any).compressionSummary as string | undefined;
    if (!summary) return "";
    return `<session_summary>\n${summary}\n</session_summary>`;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/context/layers/working-memory.ts
git commit -m "feat(context/layers): add WorkingMemoryDigest, ContinuityPriorResponse, CompressionSummary layers"
```

---

### Task 14: Layers — User Memory + UserPersona

**Files:**
- Create: `src/context/layers/user-memory.ts`
- Create: `src/context/layers/user-persona.ts`

- [ ] **Step 1: Create `src/context/layers/user-memory.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class CrossSessionFactsLayer implements ContextLayer {
  name = "CrossSessionFactsLayer";
  priority = 35;
  maxTokens = 400;
  produces = ["cross_session_facts"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const userMemoryContext = (req.session as any).userMemoryContext as string | undefined;
    if (!userMemoryContext) return "";
    return `<cross_session_facts>\n${userMemoryContext}\n</cross_session_facts>`;
  }
}

export class OpenTasksLayer implements ContextLayer {
  name = "OpenTasksLayer";
  priority = 40;
  maxTokens = 300;
  produces = ["open_tasks"];
  dependsOn = ["digest"];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasActiveItems; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const tasks = (req.session as any).owlTasks as Array<{ title: string; status: string }> | undefined;
    if (!tasks?.length) return "";
    const lines = ["<open_tasks>"];
    for (const task of tasks.filter((t) => t.status !== "complete").slice(0, 5)) {
      lines.push(`  <task status="${task.status}">${task.title}</task>`);
    }
    lines.push("</open_tasks>");
    return lines.join("\n");
  }
}

export class RelationshipContextLayer implements ContextLayer {
  name = "RelationshipContextLayer";
  priority = 45;
  maxTokens = 300;
  produces = ["relationship"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const relationship = (req.session as any).relationshipContext as string | undefined;
    if (!relationship) return "";
    return `<user_relationship>\n${relationship}\n</user_relationship>`;
  }
}
```

- [ ] **Step 2: Create `src/context/layers/user-persona.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { UserPersonaSynthesizer } from "../user-persona-synthesizer.js";
import { hash } from "../utils.js";

export class UserPersonaLayer implements ContextLayer {
  name = "UserPersonaLayer";
  priority = 50;
  maxTokens = 400;
  produces = ["user_persona"];
  dependsOn = [];

  constructor(private synthesizer: UserPersonaSynthesizer) {}

  shouldFire(t: TriageSignals): boolean { return !!t.effectiveUserId; }

  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "persona");
  }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const facts = (req.session as any).userFacts ?? [];
    const episodes = (req.session as any).userEpisodes ?? [];
    const prefs = (req.session as any).preferenceContext ?? "";

    const persona = await this.synthesizer.getPersona(t.effectiveUserId, facts, episodes, prefs);
    if (!persona) return "";

    return [
      "<user_persona>",
      `Communication: ${persona.communicationStyle}, ${persona.expertiseLevel}`,
      persona.currentProjects.length ? `Current focus: ${persona.currentProjects.join(", ")}` : "",
      persona.recurringPatterns.length ? `Patterns: ${persona.recurringPatterns.join(", ")}` : "",
      `Approach: ${persona.preferredApproach}`,
      persona.emotionalTrajectory.length ? `Emotional arc: ${persona.emotionalTrajectory.slice(-2).join(" → ")}` : "",
      "</user_persona>",
    ].filter(Boolean).join("\n");
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/context/layers/user-memory.ts src/context/layers/user-persona.ts
git commit -m "feat(context/layers): add CrossSessionFacts, OpenTasks, RelationshipContext, UserPersona layers"
```

---

### Task 15: Layers — Behavioral + Infrastructure

**Files:**
- Create: `src/context/layers/behavioral.ts`
- Create: `src/context/layers/infrastructure.ts`

- [ ] **Step 1: Create `src/context/layers/behavioral.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class BehavioralPatchLayer implements ContextLayer {
  name = "BehavioralPatchLayer";
  priority = 80;
  maxTokens = 500;
  produces = ["behavioral_rules"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, _t: TriageSignals): string | null {
    return hash("behavioral_v1");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const patches = (req.session as any).behavioralPatches as string[] | undefined;
    if (!patches?.length) return "";
    return `<behavioral_rules>\n${patches.map((p) => `  - ${p}`).join("\n")}\n</behavioral_rules>`;
  }
}

export class ActiveIntentsLayer implements ContextLayer {
  name = "ActiveIntentsLayer";
  priority = 90;
  maxTokens = 300;
  produces = ["intents"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasActiveItems; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const intents = (req.session as any).activeIntents as string[] | undefined;
    if (!intents?.length) return "";
    return `<active_intents>\n${intents.map((i) => `  - ${i}`).join("\n")}\n</active_intents>`;
  }
}

export class OwlLearningsLayer implements ContextLayer {
  name = "OwlLearningsLayer";
  priority = 95;
  maxTokens = 400;
  produces = ["learnings"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational || t.isReturningUser; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "learnings");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const learnings = (req.session as any).owlLearnings as string[] | undefined;
    if (!learnings?.length) return "";
    return `<owl_learnings>\n${learnings.slice(0, 5).map((l) => `  - ${l}`).join("\n")}\n</owl_learnings>`;
  }
}
```

- [ ] **Step 2: Create `src/context/layers/infrastructure.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class TemporalAwarenessLayer implements ContextLayer {
  name = "TemporalAwarenessLayer";
  priority = 60;
  maxTokens = 200;
  produces = ["temporal"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    const hourBucket = Math.floor(Date.now() / 3_600_000);
    return hash(t.effectiveUserId + hourBucket);
  }

  async build(_req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const now = new Date();
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return `<temporal>\nCurrent time: ${now.toLocaleString("en-US", { timeZone: tz, dateStyle: "full", timeStyle: "short" })}\nTimezone: ${tz}\n</temporal>`;
  }
}

export class ChannelFormatHintLayer implements ContextLayer {
  name = "ChannelFormatHintLayer";
  priority = 65;
  maxTokens = 100;
  produces = ["channel_hint"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(req: ContextRequest, _t: TriageSignals): string | null {
    return hash(req.channelId ?? "default");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const hints: Record<string, string> = {
      telegram: "Format for Telegram: use short paragraphs, bold with **asterisks**, code in backticks. Max 4096 chars per message.",
      slack: "Format for Slack: use mrkdwn, *bold*, `code`. Keep messages scannable.",
      cli: "Format for CLI: plain text, no markdown rendering.",
    };
    const hint = hints[req.channelId ?? ""] ?? "";
    return hint ? `<channel_format>${hint}</channel_format>` : "";
  }
}

export class ModeDirectiveLayer implements ContextLayer {
  name = "ModeDirectiveLayer";
  priority = 70;
  maxTokens = 200;
  produces = ["mode"];
  dependsOn = [];
  shouldFire(_t: TriageSignals): boolean { return true; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + String(t.hasActiveItems));
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const mode = (req.session as any).mode as string | undefined;
    if (!mode) return "";
    return `<mode_directive>${mode}</mode_directive>`;
  }
}

export class SocraticModeLayer implements ContextLayer {
  name = "SocraticModeLayer";
  priority = 75;
  maxTokens = 200;
  produces = ["socratic"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean {
    return true; // build() returns "" when socratic not enabled in session
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const socratic = (req.session as any).socraticMode as boolean | undefined;
    if (!socratic) return "";
    return "<socratic_mode>Guide the user to discover answers through questions rather than stating them directly.</socratic_mode>";
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/context/layers/behavioral.ts src/context/layers/infrastructure.ts
git commit -m "feat(context/layers): add Behavioral (3 layers) + Infrastructure (4 layers)"
```

---

### Task 16: Layers — Memory Retrieval + Knowledge

**Files:**
- Create: `src/context/layers/memory-retrieval.ts`
- Create: `src/context/layers/knowledge.ts`

- [ ] **Step 1: Create `src/context/layers/memory-retrieval.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import type { UnifiedMemoryRetriever } from "../unified-memory-retriever.js";

export class UnifiedMemoryRetrievalLayer implements ContextLayer {
  name = "UnifiedMemoryRetrievalLayer";
  priority = 100;
  maxTokens = 800;
  produces = ["memory"];
  dependsOn = ["user_persona"];
  getCacheKey(): string | null { return null; }

  constructor(private retriever: UnifiedMemoryRetriever) {}

  shouldFire(t: TriageSignals): boolean {
    return !t.isConversational || t.isReturningUser || t.hasTemporalTrigger;
  }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    return this.retriever.retrieve(t.userMessage, t.effectiveUserId);
  }
}
```

- [ ] **Step 2: Create `src/context/layers/knowledge.ts`**

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

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const kg = (req.session as any).knowledgeGraphContext as string | undefined;
    if (!kg) return "";
    return `<knowledge_graph>\n${kg}\n</knowledge_graph>`;
  }
}

export class RelevantPelletsLayer implements ContextLayer {
  name = "RelevantPelletsLayer";
  priority = 115;
  maxTokens = 500;
  produces = ["pellets"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, t: TriageSignals, _deps: LayerResults): Promise<string> {
    const pelletStore = req.deps.pelletStore;
    if (!pelletStore) return "";
    try {
      const pellets = await pelletStore.search(t.userMessage);
      if (!pellets.length) return "";
      const lines = ["<relevant_pellets>"];
      for (const p of pellets.slice(0, 3)) {
        lines.push(`  <pellet title="${p.title}">${p.content.slice(0, 300)}</pellet>`);
      }
      lines.push("</relevant_pellets>");
      return lines.join("\n");
    } catch {
      return "";
    }
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/context/layers/memory-retrieval.ts src/context/layers/knowledge.ts
git commit -m "feat(context/layers): add UnifiedMemoryRetrieval, KnowledgeGraph, RelevantPellets layers"
```

---

### Task 17: Layers — Profile + Ambient + Calibration

**Files:**
- Create: `src/context/layers/profile.ts`
- Create: `src/context/layers/ambient.ts`
- Create: `src/context/layers/calibration.ts`

- [ ] **Step 1: Create `src/context/layers/profile.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";
import { hash } from "../utils.js";

export class UserBehaviorProfileLayer implements ContextLayer {
  name = "UserBehaviorProfileLayer";
  priority = 120;
  maxTokens = 300;
  produces = ["user_profile"];
  dependsOn = [];
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }
  getCacheKey(_req: ContextRequest, t: TriageSignals): string | null {
    return hash(t.effectiveUserId + "profile");
  }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const profile = (req.session as any).userBehaviorProfile as string | undefined;
    if (!profile) return "";
    return `<user_behavior_profile>\n${profile}\n</user_behavior_profile>`;
  }
}

export class InferredPreferencesLayer implements ContextLayer {
  name = "InferredPreferencesLayer";
  priority = 125;
  maxTokens = 300;
  produces = ["preferences"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const prefs = (req.session as any).inferredPreferences as string[] | undefined;
    if (!prefs?.length) return "";
    return `<inferred_preferences>\n${prefs.map((p) => `  - ${p}`).join("\n")}\n</inferred_preferences>`;
  }
}

export class PredictedNeedsLayer implements ContextLayer {
  name = "PredictedNeedsLayer";
  priority = 130;
  maxTokens = 300;
  produces = ["predictions"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const predicted = (req.session as any).predictedNeeds as Array<{ need: string; confidence: number }> | undefined;
    if (!predicted?.length) return "";
    const high = predicted.filter((p) => p.confidence >= 0.7);
    if (!high.length) return "";
    return `<predicted_needs>\n${high.map((p) => `  - ${p.need}`).join("\n")}\n</predicted_needs>`;
  }
}
```

- [ ] **Step 2: Create `src/context/layers/ambient.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

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
  maxTokens = 300;
  produces = ["ambient"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return !t.isConversational; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const ambient = (req.session as any).ambientContext as string | undefined;
    if (!ambient) return "";
    return `<ambient_context>\n${ambient}\n</ambient_context>`;
  }
}
```

- [ ] **Step 3: Create `src/context/layers/calibration.ts`**

```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../layer.js";

export class DepthDirectiveLayer implements ContextLayer {
  name = "DepthDirectiveLayer";
  priority = 150;
  maxTokens = 150;
  produces = ["depth"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(_t: TriageSignals): boolean { return true; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const depth = (req.session as any).depthDirective as string | undefined;
    if (!depth) return "";
    return `<depth_directive>${depth}</depth_directive>`;
  }
}

export class OpinionInjectionLayer implements ContextLayer {
  name = "OpinionInjectionLayer";
  priority = 155;
  maxTokens = 200;
  produces = ["opinion"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.isOpinionRequest; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const opinion = (req.session as any).owlOpinionContext as string | undefined;
    if (!opinion) return "";
    return `<owl_opinion_context>${opinion}</owl_opinion_context>`;
  }
}

export class UserMentalModelLayer implements ContextLayer {
  name = "UserMentalModelLayer";
  priority = 160;
  maxTokens = 200;
  produces = ["mental_model"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.hasFrustration; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const model = (req.session as any).userMentalModel as string | undefined;
    if (!model) return "";
    return `<user_mental_model>${model}</user_mental_model>`;
  }
}

export class EchoChamberGuardLayer implements ContextLayer {
  name = "EchoChamberGuardLayer";
  priority = 165;
  maxTokens = 150;
  produces = ["echo_guard"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.isOpinionRequest; }

  async build(_req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    return "<echo_chamber_guard>When asked for opinions, offer a balanced perspective and gently challenge assumptions when appropriate. Avoid pure validation loops.</echo_chamber_guard>";
  }
}

export class GroundStateLayer implements ContextLayer {
  name = "GroundStateLayer";
  priority = 170;
  maxTokens = 500;
  produces = ["ground_state"];
  dependsOn = [];
  getCacheKey(): string | null { return null; }
  shouldFire(t: TriageSignals): boolean { return t.sessionDepth >= 5; }

  async build(req: ContextRequest, _t: TriageSignals, _deps: LayerResults): Promise<string> {
    const groundState = (req.session as any).groundStateContext as string | undefined;
    if (!groundState) return "";
    return `<ground_state>\n${groundState}\n</ground_state>`;
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add src/context/layers/profile.ts src/context/layers/ambient.ts src/context/layers/calibration.ts
git commit -m "feat(context/layers): add Profile (3), Ambient (2), Calibration (5) layers — all 28 layers complete"
```

---

### Task 18: Barrel Export + createContextPipeline() Factory

**Files:**
- Create: `src/context/index.ts`

- [ ] **Step 1: Create `src/context/index.ts`**

```typescript
export type { ContextLayer, ContextRequest, ContextDependencies, TriageSignals,
  LayerResults, ContextBuildTrace, ContextBuildTraceEntry } from "./layer.js";
export { computeTriage } from "./triage.js";
export { resolveUserId, hash } from "./utils.js";
export { BudgetController, estimateTokens } from "./budget-controller.js";
export { DAGPlanner, CircularDependencyError } from "./dag-planner.js";
export { ContextCache } from "./cache.js";
export { LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore } from "./circuit-breaker.js";
export { ContextPipeline } from "./pipeline.js";
export { UserPersonaSynthesizer } from "./user-persona-synthesizer.js";
export type { UserPersona } from "./user-persona-synthesizer.js";
export { UnifiedMemoryRetriever } from "./unified-memory-retriever.js";

import { SynthesisIdentityLayer } from "./layers/identity.js";
import { InnerMonologueLayer } from "./layers/inner-monologue.js";
import { WorkingMemoryDigestLayer, ContinuityPriorResponseLayer, CompressionSummaryLayer } from "./layers/working-memory.js";
import { CrossSessionFactsLayer, OpenTasksLayer, RelationshipContextLayer } from "./layers/user-memory.js";
import { UserPersonaLayer } from "./layers/user-persona.js";
import { BehavioralPatchLayer, ActiveIntentsLayer, OwlLearningsLayer } from "./layers/behavioral.js";
import { TemporalAwarenessLayer, ChannelFormatHintLayer, ModeDirectiveLayer, SocraticModeLayer } from "./layers/infrastructure.js";
import { UnifiedMemoryRetrievalLayer } from "./layers/memory-retrieval.js";
import { KnowledgeGraphLayer, RelevantPelletsLayer } from "./layers/knowledge.js";
import { UserBehaviorProfileLayer, InferredPreferencesLayer, PredictedNeedsLayer } from "./layers/profile.js";
import { CollabContextLayer, AmbientContextLayer } from "./layers/ambient.js";
import { DepthDirectiveLayer, OpinionInjectionLayer, UserMentalModelLayer, EchoChamberGuardLayer, GroundStateLayer } from "./layers/calibration.js";
import { DAGPlanner } from "./dag-planner.js";
import { ContextCache } from "./cache.js";
import { LayerHealthMonitor } from "./circuit-breaker.js";
import { ContextPipeline } from "./pipeline.js";
import type { UserPersonaSynthesizer } from "./user-persona-synthesizer.js";
import type { UnifiedMemoryRetriever } from "./unified-memory-retriever.js";

export interface ContextPipelineDeps {
  userPersonaSynthesizer: UserPersonaSynthesizer;
  unifiedMemoryRetriever: UnifiedMemoryRetriever;
}

export function createContextPipeline(deps: ContextPipelineDeps): ContextPipeline {
  const layers = [
    new SynthesisIdentityLayer(),
    new InnerMonologueLayer(),
    new WorkingMemoryDigestLayer(),
    new ContinuityPriorResponseLayer(),
    new CompressionSummaryLayer(),
    new CrossSessionFactsLayer(),
    new OpenTasksLayer(),
    new RelationshipContextLayer(),
    new UserPersonaLayer(deps.userPersonaSynthesizer),
    new TemporalAwarenessLayer(),
    new ChannelFormatHintLayer(),
    new ModeDirectiveLayer(),
    new SocraticModeLayer(),
    new BehavioralPatchLayer(),
    new ActiveIntentsLayer(),
    new OwlLearningsLayer(),
    new UnifiedMemoryRetrievalLayer(deps.unifiedMemoryRetriever),
    new KnowledgeGraphLayer(),
    new RelevantPelletsLayer(),
    new UserBehaviorProfileLayer(),
    new InferredPreferencesLayer(),
    new PredictedNeedsLayer(),
    new CollabContextLayer(),
    new AmbientContextLayer(),
    new DepthDirectiveLayer(),
    new OpinionInjectionLayer(),
    new UserMentalModelLayer(),
    new EchoChamberGuardLayer(),
    new GroundStateLayer(),
  ];

  return new ContextPipeline(
    layers,
    new ContextCache(),
    new LayerHealthMonitor(),
    new DAGPlanner(),
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
npx tsc --noEmit 2>&1 | grep "src/context" | head -10
```

Expected: zero errors in `src/context/`.

- [ ] **Step 3: Commit**

```bash
git add src/context/index.ts
git commit -m "feat(context): add barrel export + createContextPipeline() factory wiring all 28 layers"
```

---

### Task 19: context-builder.ts Thin Adapter + gateway/types.ts + EventBus Cache Wiring

**Files:**
- Modify: `src/gateway/handlers/context-builder.ts`
- Modify: `src/gateway/types.ts`

- [ ] **Step 1: Read current `src/gateway/types.ts` to find where to add new fields**

```bash
grep -n "contextPipeline\|contextCache\|userPersonaSynthesizer\|intelligenceRouter" \
  src/gateway/types.ts | head -10
```

If none found, add to the `GatewayContext` interface (search for it):

```bash
grep -n "export interface GatewayContext" src/gateway/types.ts
```

- [ ] **Step 2: Add 3 optional fields to `GatewayContext` in `src/gateway/types.ts`**

Find the `GatewayContext` interface and add before its closing `}`:

```typescript
  contextPipeline?: import("../context/pipeline.js").ContextPipeline;
  contextCache?: import("../context/cache.js").ContextCache;
  userPersonaSynthesizer?: import("../context/user-persona-synthesizer.js").UserPersonaSynthesizer;
```

- [ ] **Step 3: Replace `src/gateway/handlers/context-builder.ts` with thin adapter**

The current file is 762 lines. Replace it entirely with the following (~120 lines):

```typescript
/**
 * ContextBuilder — thin adapter over ContextPipeline.
 * Translates gateway types → ContextRequest, runs the pipeline,
 * returns the assembled system prompt string as EngineContext.
 */
import type { Session } from "../../memory/store.js";
import type { GatewayContext, GatewayCallbacks } from "../types.js";
import type { EngineContext } from "../../engine/runtime.js";
import type { ContinuityResult } from "../../cognition/continuity-engine.js";
import type { ConversationDigest } from "../../memory/conversation-digest.js";
import { computeTriage } from "../../context/triage.js";
import { resolveUserId } from "../../context/utils.js";
import { log } from "../../logger.js";

export class ContextBuilder {
  constructor(private ctx: GatewayContext) {}

  async build(
    session: Session,
    callbacks: GatewayCallbacks,
    _dynamicSkillsContext: string = "",
    _isolatedTask: boolean = false,
    _attemptLog?: unknown,
    channelId?: string,
    userId?: string,
    continuityResult?: ContinuityResult | null,
    digest?: ConversationDigest | null,
  ): Promise<EngineContext> {
    const pipeline = this.ctx.contextPipeline;

    if (!pipeline) {
      // Pipeline not initialised — return minimal context (shouldn't happen in prod)
      log.engine.warn("[ContextBuilder] contextPipeline not set on GatewayContext — returning empty context");
      return this.minimalContext(session);
    }

    const effectiveUserId = resolveUserId(userId, session.id);
    const triage = computeTriage({
      userMessage: session.messages?.at(-1)?.content ?? "",
      sessionDepth: session.messages?.length ?? 0,
      continuityClass: continuityResult?.classification ?? null,
      userId: effectiveUserId,
      sessionId: session.id,
      hasActiveItems: false, // populated by OwlBrain before context build
    });

    const deps = {
      intelligenceRouter: (this.ctx as any).intelligenceRouter,
      pelletStore: (this.ctx as any).pelletStore,
      memoryBus: (this.ctx as any).memoryBus,
      sessionStore: this.ctx.sessionStore,
      eventBus: (this.ctx as any).eventBus,
      config: this.ctx.config,
    };

    const { output, trace } = await pipeline.run(
      { session, callbacks, channelId, userId, continuityResult: continuityResult ?? null, digest: digest ?? null, deps },
      triage,
      { globalTokenCeiling: (this.ctx.config as any).context?.globalTokenCeiling },
    );

    log.engine.debug(`[ContextBuilder] trace: ${trace.length} layers, ` +
      `${trace.filter(e => e.fired).length} fired`);

    return {
      ...this.minimalContext(session),
      systemPrompt: output,
    };
  }

  private minimalContext(session: Session): EngineContext {
    return {
      systemPrompt: "",
      sessionId: session.id,
      messages: session.messages ?? [],
    };
  }
}
```

- [ ] **Step 4: Run full test suite to check for regressions**

```bash
npx vitest run 2>&1 | tail -20
```

Expected: all previously-passing tests still pass. New context tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/handlers/context-builder.ts src/gateway/types.ts
git commit -m "feat(context): replace 762-line ContextBuilder with thin adapter over ContextPipeline"
```

---

### Task 20: Wire core.ts + post-processor.ts + Delete MemoryFirstContextBuilder

**Files:**
- Modify: `src/gateway/core.ts`
- Modify: `src/gateway/handlers/post-processor.ts`
- Delete: `src/memory/context-builder.ts`
- Create: `__tests__/context/pipeline-integration.test.ts`

- [ ] **Step 1: Find pipeline instantiation point in `src/gateway/core.ts`**

```bash
grep -n "ContextBuilder\|new Context\|intelligenceRouter\|pelletStore" src/gateway/core.ts | head -20
```

- [ ] **Step 2: Add pipeline instantiation in `src/gateway/core.ts`**

Find where `GatewayContext` is assembled (search for `ctx = {` or `this.ctx`). After the existing subsystems are constructed, add:

```typescript
import { createContextPipeline } from "../context/index.js";
import { UserPersonaSynthesizer } from "../context/user-persona-synthesizer.js";
import { UnifiedMemoryRetriever } from "../context/unified-memory-retriever.js";
import { ContextCache } from "../context/cache.js";

// Inside the constructor / init method, after memoryBus and pelletStore exist:
const userPersonaSynthesizer = new UserPersonaSynthesizer(
  provider,          // the default ModelProvider already available
  this.memoryDb,     // MemoryDatabase already available
);
const unifiedMemoryRetriever = new UnifiedMemoryRetriever(
  this.memoryBus,    // MemoryBus already available
  this.factStore,    // FactStore already available
  this.episodicMemory, // EpisodicMemory already available
);
const contextCache = new ContextCache();
const contextPipeline = createContextPipeline({ userPersonaSynthesizer, unifiedMemoryRetriever });

// Wire EventBus cache invalidation
eventBus.on("pellet:written",    () => contextCache.invalidate("BehavioralPatchLayer"));
eventBus.on("synthesis:created", () => contextCache.invalidate("SynthesisIdentityLayer"));
eventBus.on("persona:refreshed", (e) => contextCache.invalidateUser((e as any).userId));
eventBus.on("learning:recorded", () => contextCache.invalidate("OwlLearningsLayer"));
eventBus.on("session:ended",     (e) => contextCache.invalidateUser(e.sessionId));

// Add to ctx object:
// contextPipeline, contextCache, userPersonaSynthesizer
```

- [ ] **Step 3: Find InnerMonologue fire-and-forget in `src/gateway/handlers/post-processor.ts`**

```bash
grep -n "thinkInBackground\|innerLife\|InnerMonologue\|digestManager" \
  src/gateway/handlers/post-processor.ts | head -20
```

- [ ] **Step 4: Add monologue persistence in `src/gateway/handlers/post-processor.ts`**

After the existing `innerLife.thinkInBackground(...)` call (or where the inner monologue is generated), add:

```typescript
// After thinkInBackground fires, capture the result for next-turn injection.
// Uses setImmediate to avoid blocking the response path.
setImmediate(async () => {
  // Wait one tick for pendingThink to resolve, then store
  await new Promise((r) => setTimeout(r, 100));
  const monologue = innerLife.getLastMonologue();
  if (monologue && digestManager) {
    await digestManager.setLastMonologue(session.id, {
      thoughts: monologue.thoughts,
      responseIntent: monologue.responseIntent,
      moodCurrent: monologue.moodShift?.current,
      storedAt: new Date().toISOString(),
    });
  }
});
```

- [ ] **Step 5: Delete `src/memory/context-builder.ts`**

```bash
rm src/memory/context-builder.ts
```

- [ ] **Step 6: Remove the now-dead import from `src/gateway/handlers/context-builder.ts`** (old line 24):

```bash
grep -n "MemoryFirstContextBuilder" src/gateway/handlers/context-builder.ts
```

The new thin adapter doesn't import it — confirm no references remain:

```bash
grep -rn "MemoryFirstContextBuilder" src/ --include="*.ts"
```

Expected: zero matches.

- [ ] **Step 7: Write integration test**

```typescript
// __tests__/context/pipeline-integration.test.ts
import { describe, it, expect, vi } from "vitest";
import { ContextPipeline } from "../../src/context/pipeline.js";
import { DAGPlanner } from "../../src/context/dag-planner.js";
import { ContextCache } from "../../src/context/cache.js";
import { LayerHealthMonitor } from "../../src/context/circuit-breaker.js";
import { computeTriage } from "../../src/context/triage.js";
import { SynthesisIdentityLayer } from "../../src/context/layers/identity.js";
import { WorkingMemoryDigestLayer } from "../../src/context/layers/working-memory.js";
import { TemporalAwarenessLayer } from "../../src/context/layers/infrastructure.js";

describe("ContextPipeline integration", () => {
  it("runs identity + working memory + temporal in correct batch order", async () => {
    const layers = [
      new SynthesisIdentityLayer(),
      new WorkingMemoryDigestLayer(),
      new TemporalAwarenessLayer(),
    ];
    const pipeline = new ContextPipeline(
      layers, new ContextCache(), new LayerHealthMonitor(), new DAGPlanner()
    );

    const triage = computeTriage({
      userMessage: "help me debug this",
      sessionDepth: 2,
      continuityClass: null,
      userId: "u1",
      sessionId: "s1",
      hasActiveItems: false,
    });

    const req: any = {
      session: { id: "s1", owlName: "Atlas", owlPersonality: "You are Atlas.",
        messages: [{ role: "user", content: "help me debug this" }] },
      callbacks: {},
      channelId: "cli",
      userId: "u1",
      continuityResult: null,
      digest: { sessionId: "s1", task: "debug issue", artifacts: [], decisions: [], failed: [], openQuestions: [], updatedAt: new Date().toISOString() },
      deps: { pelletStore: null, memoryBus: null, sessionStore: null, eventBus: null, config: {}, intelligenceRouter: null },
    };

    const { output, trace } = await pipeline.run(req, triage);

    expect(output).toContain("Atlas");
    expect(output).toContain("temporal");
    expect(trace.length).toBe(3);
    expect(trace.filter((t) => t.fired).length).toBeGreaterThanOrEqual(2);
  });

  it("budget ceiling prevents context overflow", async () => {
    const layer: any = {
      name: "Big", priority: 10, maxTokens: 9000, produces: ["big"], dependsOn: [],
      shouldFire: () => true, build: async () => "x".repeat(40000),
    };
    const pipeline = new ContextPipeline(
      [layer], new ContextCache(), new LayerHealthMonitor(), new DAGPlanner()
    );
    const triage = computeTriage({ userMessage: "hi", sessionDepth: 0, continuityClass: null,
      userId: "u1", sessionId: "s1", hasActiveItems: false });
    const { output } = await pipeline.run({ session: { id: "s1" }, callbacks: {}, continuityResult: null, digest: null, deps: {} } as any, triage, { globalTokenCeiling: 100 });
    expect(output.length).toBeLessThan(600); // 100 tokens * ~3.8 chars + trim marker
  });
});
```

- [ ] **Step 8: Run full test suite**

```bash
npx vitest run 2>&1 | tail -20
```

Expected: all tests pass including new integration test. Zero regressions.

- [ ] **Step 9: Commit**

```bash
git add src/gateway/core.ts src/gateway/handlers/post-processor.ts \
  __tests__/context/pipeline-integration.test.ts
git rm src/memory/context-builder.ts
git commit -m "feat(context): wire ContextPipeline into core.ts + post-processor + delete MemoryFirstContextBuilder"
```

---

### Task 21: Update Progress Tracker

**Files:**
- Modify: `docs/platform-audit/progress.md`

- [ ] **Step 1: Update Element 5 status**

In `docs/platform-audit/progress.md`, find Element 5 row and update:

```markdown
| 5 | ContextBuilder (memory + pellets + skills) | 🔧 reviewed — improvements committed | 2026-04-30 |
```

- [ ] **Step 2: Add Element 5 detail section** (after Element 4 section)

Add the following after the Element 4 section:

```markdown
## Element 5: ContextBuilder → ContextPipeline

### Scope
`src/gateway/handlers/context-builder.ts` (762 lines replaced by ~120-line adapter)
`src/context/` (new module: 22 source files)

### Findings
- 762-line god-method with 28 inline signal blocks
- Sequential execution: ~4,200ms wall time per cold request
- Triple memory duplication (factContext + memoryBus + memoryFirstContext)
- No token budget — context silently overflows LLM window
- InnerMonologue generated but discarded every turn
- No user persona synthesis — owl knows fragments, not the person
- Zero test coverage on context assembly logic

### Improvements Implemented
- **ContextPipeline** — typed registry of 28 ContextLayer instances executed via DAG
- **DAGPlanner** — Kahn's topological sort; layers declare produces[]/dependsOn[]; parallel batches via Promise.all()
- **BudgetController** — per-layer token cap + 8,000-token global ceiling; sentence-boundary trim
- **ContextCache** — LRU (200 entries), per-layer TTL, event-driven invalidation, O(1) userIndex
- **LayerCircuitBreaker** — CLOSED→OPEN→HALF_OPEN→CLOSED; trips at errorRate>40% OR p95>1800ms
- **ContextQualityScore** — composite 0–1 score; emits context:quality_degraded on EventBus when <0.6
- **InnerMonologueLayer** — owl's last-turn thoughts persisted in ConversationDigest; injected at priority 15
- **UserPersonaSynthesizer** — LLM synthesis of user character card; 30min SQLite cache; stale-while-revalidate
- **UnifiedMemoryRetriever** — single parallel query across FactStore + EpisodicMemory + MemoryBus; cosine dedup + tier-labeled XML
- **ContextDependencies interface** — src/context/ never imports GatewayContext; clean module boundary
- **Schema v13** — user_personas table + idx_pellets_tag
- **Deleted** src/memory/context-builder.ts (MemoryFirstContextBuilder superseded)

### Design
- Spec: `docs/superpowers/specs/2026-04-30-context-pipeline-design.md`
- Plan: `docs/superpowers/plans/2026-04-30-context-pipeline.md`
```

- [ ] **Step 3: Commit**

```bash
git add docs/platform-audit/progress.md
git commit -m "docs: update progress tracker — Element 5 ContextPipeline complete"
```
