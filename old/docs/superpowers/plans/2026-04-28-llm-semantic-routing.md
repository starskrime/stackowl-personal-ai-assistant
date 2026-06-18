# LLM Semantic Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace keyword-based specialist routing with LLM semantic routing so any natural-language message ("when was Washington born?", "5+5=") routes to the right specialist owl without keyword configuration.

**Architecture:** A new `ClassifyFn` is injected into `SecretaryRouter`. When specialists exist, the router calls it with the message + specialist summaries; the LLM returns one specialist name or "none". The classify function is built in `src/routing/llm-classifier.ts` and wired in `core.ts`. `route()` becomes async. All old keyword/regex matching is removed.

**Tech Stack:** TypeScript, existing `ModelProvider.chat()` interface, Vitest

---

## File Map

| File | Change |
|------|--------|
| `src/routing/secretary.ts` | Remove all keyword matching; add `ClassifyFn` injection; make `route()` async |
| `src/routing/llm-classifier.ts` | **New** — builds the classify function from a ModelProvider |
| `src/gateway/core.ts` | Pass classify fn when constructing SecretaryRouter; await `route()` |
| `__tests__/routing/secretary.test.ts` | Await `route()`; add LLM-routing tests with mocked classify fn |
| `__tests__/routing/llm-classifier.test.ts` | **New** — tests for the classify function prompt + parse logic |

---

## Task 1: Rollback today's arithmetic regex patch

**Files:**
- Modify: `src/routing/secretary.ts`

- [ ] **Step 1: Remove the arithmetic regex additions from secretary.ts**

Replace the current content of `src/routing/secretary.ts` with the clean version below (removes `ARITHMETIC_RE`, `MATH_EXPERTISE_TERMS`, `MATCH_WEIGHT`, `DNA_WEIGHT`, `isArithmetic`, and the word-level matching in `scoreMatch`):

```typescript
/**
 * StackOwl — Secretary Owl Router
 *
 * The Secretary Owl acts as a mandatory facade for all user messages.
 * It decides whether to:
 * - Answer directly as a generalist
 * - Route to a specialized owl
 * - Convene parliament for complex queries
 */

import type { MemoryDatabase, SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import { log } from "../logger.js";

const MIN_MESSAGE_LENGTH = 10;
const ROUTING_CONFIDENCE_THRESHOLD = 0.4;
const MATCH_SCORE_THRESHOLD = 0.25;
const MATCH_WEIGHT = 0.7;
const DNA_WEIGHT = 0.3;

const PARLIAMENT_KEYWORDS = [
  "compare", "versus", "vs", "difference between",
  "pros and cons", "advantages and disadvantages",
  "should we", "should i", "decision", "choose between",
  "analyze", "analysis", "evaluate", "assessment",
  "strategy", "strategic", "planning", "plan",
  "architecture", "design", "system design",
] as const;

export type RoutingDecision =
  | { type: "direct"; reason: string }
  | { type: "specialist"; owl: SpecializedOwl; reason: string; isFolderSpec?: boolean }
  | { type: "parliament"; reason: string };

interface RoutingTarget {
  name: string;
  routingRules: string[];
  expertiseDomains?: string[];
  routingQuality?: number;
  isFolderSpec?: boolean;
}

export class SecretaryRouter {
  private db: MemoryDatabase;
  private folderRegistry?: SpecializedOwlRegistry;

  constructor(db: MemoryDatabase, folderRegistry?: SpecializedOwlRegistry) {
    this.db = db;
    this.folderRegistry = folderRegistry;
  }

  private toRoutingTarget(owl: SpecializedOwl): RoutingTarget {
    return {
      name: owl.name,
      routingRules: owl.routingRules,
      expertiseDomains: owl.dna?.expertiseDomains,
      routingQuality: owl.dna?.routingQuality,
    };
  }

  route(
    message: string,
    userId: string,
  ): RoutingDecision {
    const dbOwls = this.db.owls.getByOwner(userId);
    const folderSpecs = this.folderRegistry?.listAll() ?? [];

    if (dbOwls.length === 0 && folderSpecs.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const messageLower = message.toLowerCase();

    const dbTargets = dbOwls.map((owl) => this.toRoutingTarget(owl));
    const folderTargets: RoutingTarget[] = folderSpecs.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
      isFolderSpec: true,
    }));

    const allTargets = [...dbTargets, ...folderTargets];
    const matchedTarget = this.findBestMatch(messageLower, allTargets);

    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(
          `[SecretaryRouter] Routing to ${matchedTarget.name} (confidence: ${confidence.toFixed(2)}, folder=${matchedTarget.isFolderSpec ?? false})`,
        );

        if (matchedTarget.isFolderSpec) {
          const spec = this.folderRegistry?.get(matchedTarget.name);
          const syntheticOwl: SpecializedOwl = {
            id: `folder-${matchedTarget.name}`,
            ownerId: userId,
            name: matchedTarget.name,
            specialization: spec?.role ?? matchedTarget.name,
            personalityPrompt: `You are ${matchedTarget.name}, ${spec?.role ?? "a specialized assistant"}. Your expertise: ${(matchedTarget.expertiseDomains ?? []).join(", ") || "general"}.`,
            routingRules: matchedTarget.routingRules,
            dna: {
              challengeLevel: 0.7,
              verbosity: 0.5,
              expertiseDomains: matchedTarget.expertiseDomains ?? [],
              routingQuality: 0.7,
              evolutionSpeed: 0.5,
            },
            isMainOwl: false,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          const decision = {
            type: "specialist" as const,
            owl: syntheticOwl,
            isFolderSpec: true,
            reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
          };
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }

        const matchedDbOwl = dbOwls.find((o) => o.name === matchedTarget.name);
        if (!matchedDbOwl) {
          log.engine.warn(`[SecretaryRouter] Matched target "${matchedTarget.name}" not found in dbOwls — falling back to direct`);
          const fallback = { type: "direct" as const, reason: "Matched owl not found in DB" };
          this.logRoutingDecision(userId, message, fallback, "failure");
          return fallback;
        }
        const decision = {
          type: "specialist" as const,
          owl: matchedDbOwl,
          reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
        };
        this.logRoutingDecision(userId, message, decision, "success");
        return decision;
      }
    }

    if (this.shouldConveneParliament(message)) {
      const decision = { type: "parliament" as const, reason: "Complex query detected - convening parliament" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const decision = { type: "direct" as const, reason: "No specialist match found" };
    this.logRoutingDecision(userId, message, decision, "success");
    return decision;
  }

  private findBestMatch(message: string, targets: RoutingTarget[]): RoutingTarget | null {
    let bestMatch: RoutingTarget | null = null;
    let bestScore = 0;

    for (const target of targets) {
      const score = this.scoreMatch(message, target);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = target;
      }
    }

    return bestScore >= MATCH_SCORE_THRESHOLD ? bestMatch : null;
  }

  private scoreMatch(message: string, target: RoutingTarget): number {
    const rules = target.routingRules.map((r) => r.toLowerCase());
    if (rules.length === 0) return 0;

    const messageLower = message.toLowerCase();
    let matches = 0;
    for (const rule of rules) {
      if (messageLower.includes(rule)) {
        matches++;
      }
    }

    return matches / rules.length;
  }

  private calculateConfidence(messageLower: string, target: RoutingTarget): number {
    const matchScore = this.scoreMatch(messageLower, target);
    const dnaScore = target.routingQuality ?? (target.isFolderSpec ? 0.7 : 0.5);
    return (matchScore * MATCH_WEIGHT) + (dnaScore * DNA_WEIGHT);
  }

  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;
    if (keywordCount >= 3) return true;
    if (keywordCount >= 2 && message.length > 200) return true;
    return false;
  }

  getMainOwl(userId: string): SpecializedOwl | null {
    return this.db.owls.getMainOwl(userId);
  }

  private logRoutingDecision(
    userId: string,
    message: string,
    decision: RoutingDecision,
    outcome: "success" | "failure",
  ): void {
    log.engine.info(
      `[SecretaryRouter] Routing decision: ${JSON.stringify({
        userId,
        message: message.slice(0, 100),
        decisionType: decision.type,
        targetOwl: decision.type === "specialist" ? decision.owl.name : null,
        reason: decision.reason,
        outcome,
        timestamp: new Date().toISOString(),
      })}`,
    );
  }
}
```

- [ ] **Step 2: Build and verify rollback compiles**

```bash
npm run build
```

Expected: `tsc` exits 0, no errors.

- [ ] **Step 3: Run tests — should all pass**

```bash
npm test -- --reporter=verbose 2>&1 | grep -E "✓|✗|PASS|FAIL|Tests "
```

Expected: all routing tests pass, no new failures.

- [ ] **Step 4: Commit the rollback**

```bash
git add src/routing/secretary.ts
git commit -m "revert: remove arithmetic regex patch from SecretaryRouter"
```

---

## Task 2: Create the LLM classifier module

**Files:**
- Create: `src/routing/llm-classifier.ts`
- Create: `__tests__/routing/llm-classifier.test.ts`

- [ ] **Step 1: Write the failing test for llm-classifier**

Create `__tests__/routing/llm-classifier.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { buildClassifyFn, type SpecialistSummary } from "../../src/routing/llm-classifier.js";
import type { ModelProvider, ChatResponse } from "../../src/providers/base.js";

function mockProvider(responseText: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: responseText,
      model: "mock",
      finishReason: "stop",
    } satisfies ChatResponse),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    chatStreamWithEvents: vi.fn(),
    embedText: vi.fn(),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

const specialists: SpecialistSummary[] = [
  { name: "Calculus", role: "math teacher", expertise: ["mathematics", "arithmetic"] },
  { name: "HistoryOwl", role: "history teacher", expertise: ["world history", "historical events"] },
];

describe("buildClassifyFn", () => {
  it("returns the matched specialist name when LLM responds with a valid name", async () => {
    const provider = mockProvider("Calculus");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBe("Calculus");
  });

  it("is case-insensitive when matching specialist name", async () => {
    const provider = mockProvider("calculus");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBe("Calculus");
  });

  it("returns null when LLM responds with 'none'", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("tell me a joke", specialists);

    expect(result).toBeNull();
  });

  it("returns null when LLM responds with an unknown name", async () => {
    const provider = mockProvider("UnknownOwl");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("something", specialists);

    expect(result).toBeNull();
  });

  it("returns null and does not throw when provider throws", async () => {
    const provider = {
      name: "mock",
      chat: vi.fn().mockRejectedValue(new Error("network error")),
    } as unknown as ModelProvider;
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBeNull();
  });

  it("includes all specialists in the prompt sent to the provider", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    await classify("test message", specialists);

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0];
    const messages = chatCall[0] as Array<{ role: string; content: string }>;
    const prompt = messages[0].content;

    expect(prompt).toContain("Calculus");
    expect(prompt).toContain("HistoryOwl");
    expect(prompt).toContain("test message");
  });

  it("calls provider with maxTokens: 30", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    await classify("test", specialists);

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0];
    const options = chatCall[2];
    expect(options?.maxTokens).toBe(30);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npx vitest run __tests__/routing/llm-classifier.test.ts
```

Expected: FAIL — `Cannot find module '../../src/routing/llm-classifier.js'`

- [ ] **Step 3: Create the llm-classifier module**

Create `src/routing/llm-classifier.ts`:

```typescript
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export interface SpecialistSummary {
  name: string;
  role: string;
  expertise: string[];
}

export type ClassifyFn = (
  message: string,
  specialists: SpecialistSummary[],
) => Promise<string | null>;

export function buildClassifyFn(
  provider: ModelProvider,
  model: string,
): ClassifyFn {
  return async (message: string, specialists: SpecialistSummary[]): Promise<string | null> => {
    const lines = specialists
      .map((s) => `- ${s.name} (${s.role}): ${s.expertise.join(", ") || s.role}`)
      .join("\n");

    const prompt = `You are a routing assistant. Given a list of specialists and a user message, decide which specialist should handle the message.

Specialists:
${lines}

User message: "${message}"

Reply with ONLY the specialist name that should handle this message, or "none" if no specialist is appropriate. Do not explain.`;

    try {
      const response = await provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { maxTokens: 30 },
      );
      const raw = response.content.trim();
      const match = specialists.find(
        (s) => s.name.toLowerCase() === raw.toLowerCase(),
      );
      return match ? match.name : null;
    } catch (err) {
      log.engine.warn(
        `[LLMClassifier] classify failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
npx vitest run __tests__/routing/llm-classifier.test.ts
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/routing/llm-classifier.ts __tests__/routing/llm-classifier.test.ts
git commit -m "feat: add LLM classifier module for semantic specialist routing"
```

---

## Task 3: Make SecretaryRouter use LLM routing

**Files:**
- Modify: `src/routing/secretary.ts`
- Modify: `__tests__/routing/secretary.test.ts`

- [ ] **Step 1: Update the existing secretary tests to await route() and add LLM routing tests**

Replace `__tests__/routing/secretary.test.ts` with:

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { rm, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { MemoryDatabase, type SpecializedOwl } from "../../src/memory/db.js";
import { SecretaryRouter, type RoutingDecision } from "../../src/routing/secretary.js";
import type { ClassifyFn } from "../../src/routing/llm-classifier.js";

const testSpace = join(__dirname, ".test_secretary_workspace");

async function cleanWorkspace() {
  await rm(testSpace, { recursive: true, force: true }).catch(() => {});
  await mkdir(testSpace, { recursive: true });
}

function makeOwl(overrides: Partial<{
  name: string;
  ownerId: string;
  specialization: string;
  routingRules: string[];
  routingQuality: number;
  isMainOwl: boolean;
}> = {}): Omit<SpecializedOwl, "id" | "createdAt" | "updatedAt"> {
  return {
    ownerId: overrides.ownerId ?? "user_test",
    name: overrides.name ?? "TestOwl",
    specialization: overrides.specialization ?? "General assistance",
    personalityPrompt: "You are a helpful assistant.",
    routingRules: overrides.routingRules ?? [],
    dna: {
      challengeLevel: 0.7,
      verbosity: 0.5,
      expertiseDomains: [],
      routingQuality: overrides.routingQuality ?? 0.5,
      evolutionSpeed: 0.5,
    },
    isMainOwl: overrides.isMainOwl ?? false,
  };
}

function mockClassify(returnName: string | null): ClassifyFn {
  return vi.fn().mockResolvedValue(returnName);
}

describe("SecretaryRouter", () => {
  let db: MemoryDatabase;

  beforeEach(async () => {
    await cleanWorkspace();
    db = new MemoryDatabase(testSpace);
  });

  describe("route() — no specialists", () => {
    it("returns direct immediately without calling classify", async () => {
      const classify = vi.fn();
      const router = new SecretaryRouter(db, undefined, classify as unknown as ClassifyFn);

      const decision = await router.route("Hello, how are you?", "user_test");

      expect(decision.type).toBe("direct");
      expect(decision.reason).toBe("No specialized owls configured");
      expect(classify).not.toHaveBeenCalled();
    });
  });

  describe("route() — with LLM classify", () => {
    it("routes to specialist when classify returns a matching owl name", async () => {
      db.owls.create(makeOwl({ name: "TradingBot", routingRules: [], routingQuality: 0.8 }));
      const router = new SecretaryRouter(db, undefined, mockClassify("TradingBot"));

      const decision = await router.route("I want to buy some stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });

    it("returns direct when classify returns null", async () => {
      db.owls.create(makeOwl({ name: "TradingBot" }));
      const router = new SecretaryRouter(db, undefined, mockClassify(null));

      const decision = await router.route("Tell me about the weather", "user_test");

      expect(decision.type).toBe("direct");
    });

    it("routes to parliament when classify returns null and message triggers parliament", async () => {
      db.owls.create(makeOwl({ name: "SomeOwl" }));
      const router = new SecretaryRouter(db, undefined, mockClassify(null));

      const decision = await router.route(
        "Compare two programming languages: analyze the advantages and disadvantages, then evaluate the strategy for choosing one?",
        "user_test",
      );

      expect(decision.type).toBe("parliament");
    });

    it("falls back to direct when classify throws", async () => {
      db.owls.create(makeOwl({ name: "TradingBot" }));
      const brokenClassify: ClassifyFn = vi.fn().mockRejectedValue(new Error("LLM down"));
      const router = new SecretaryRouter(db, undefined, brokenClassify);

      const decision = await router.route("I want to buy some stocks", "user_test");

      expect(decision.type).toBe("direct");
    });

    it("falls back to keyword matching when no classify fn is provided", async () => {
      db.owls.create(makeOwl({ name: "TradingBot", routingRules: ["stock", "trade", "portfolio"], routingQuality: 0.8 }));
      const router = new SecretaryRouter(db);

      const decision = await router.route("I want to buy some stocks", "user_test");

      expect(decision.type).toBe("specialist");
      if (decision.type === "specialist") {
        expect(decision.owl.name).toBe("TradingBot");
      }
    });
  });

  describe("getMainOwl()", () => {
    it("returns the main owl for a user", async () => {
      db.owls.create(makeOwl({ name: "RegularOwl", isMainOwl: false }));
      db.owls.create(makeOwl({ name: "MainOwl", isMainOwl: true }));
      const router = new SecretaryRouter(db);

      const mainOwl = router.getMainOwl("user_test");

      expect(mainOwl).not.toBeNull();
      expect(mainOwl!.name).toBe("MainOwl");
    });

    it("returns null when no main owl exists", async () => {
      db.owls.create(makeOwl({ name: "RegularOwl", isMainOwl: false }));
      const router = new SecretaryRouter(db);

      const mainOwl = router.getMainOwl("user_test");

      expect(mainOwl).toBeNull();
    });
  });

  describe("tenant isolation", () => {
    it("routes user_a to their own owl, not user_b's", async () => {
      db.owls.create(makeOwl({ name: "UserAOwl", ownerId: "user_a", routingRules: [] }));
      db.owls.create(makeOwl({ name: "UserBOwl", ownerId: "user_b", routingRules: [] }));

      const routerA = new SecretaryRouter(db, undefined, mockClassify("UserAOwl"));
      const routerB = new SecretaryRouter(db, undefined, mockClassify("UserBOwl"));

      const decisionA = await routerA.route("tell me something private", "user_a");
      const decisionB = await routerB.route("tell me something private", "user_b");

      expect(decisionA.type).toBe("specialist");
      if (decisionA.type === "specialist") {
        expect(decisionA.owl.ownerId).toBe("user_a");
        expect(decisionA.owl.name).toBe("UserAOwl");
      }

      expect(decisionB.type).toBe("specialist");
      if (decisionB.type === "specialist") {
        expect(decisionB.owl.ownerId).toBe("user_b");
        expect(decisionB.owl.name).toBe("UserBOwl");
      }
    });

    it("returns direct for a user with no owls even if another user has owls", async () => {
      db.owls.create(makeOwl({ name: "UserAOwl", ownerId: "user_a" }));
      const router = new SecretaryRouter(db, undefined, mockClassify("UserAOwl"));

      const decision = await router.route("tell me something private", "user_b");

      expect(decision.type).toBe("direct");
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail on `route()` returning a Promise**

```bash
npx vitest run __tests__/routing/secretary.test.ts
```

Expected: FAIL — `route()` is not async yet.

- [ ] **Step 3: Update secretary.ts — add ClassifyFn, make route() async**

Replace the full content of `src/routing/secretary.ts`:

```typescript
/**
 * StackOwl — Secretary Owl Router
 *
 * Routes user messages to the right specialist owl using LLM semantic
 * classification. Falls back to keyword matching if no classify fn provided.
 * Skips the LLM call entirely when no specialists are configured.
 */

import type { MemoryDatabase, SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { ClassifyFn } from "./llm-classifier.js";
import { log } from "../logger.js";

const MIN_MESSAGE_LENGTH = 10;
const ROUTING_CONFIDENCE_THRESHOLD = 0.4;
const MATCH_SCORE_THRESHOLD = 0.25;
const MATCH_WEIGHT = 0.7;
const DNA_WEIGHT = 0.3;

const PARLIAMENT_KEYWORDS = [
  "compare", "versus", "vs", "difference between",
  "pros and cons", "advantages and disadvantages",
  "should we", "should i", "decision", "choose between",
  "analyze", "analysis", "evaluate", "assessment",
  "strategy", "strategic", "planning", "plan",
  "architecture", "design", "system design",
] as const;

export type RoutingDecision =
  | { type: "direct"; reason: string }
  | { type: "specialist"; owl: SpecializedOwl; reason: string; isFolderSpec?: boolean }
  | { type: "parliament"; reason: string };

interface RoutingTarget {
  name: string;
  routingRules: string[];
  expertiseDomains?: string[];
  routingQuality?: number;
  isFolderSpec?: boolean;
}

export class SecretaryRouter {
  private db: MemoryDatabase;
  private folderRegistry?: SpecializedOwlRegistry;
  private classify?: ClassifyFn;

  constructor(
    db: MemoryDatabase,
    folderRegistry?: SpecializedOwlRegistry,
    classify?: ClassifyFn,
  ) {
    this.db = db;
    this.folderRegistry = folderRegistry;
    this.classify = classify;
  }

  private toRoutingTarget(owl: SpecializedOwl): RoutingTarget {
    return {
      name: owl.name,
      routingRules: owl.routingRules,
      expertiseDomains: owl.dna?.expertiseDomains,
      routingQuality: owl.dna?.routingQuality,
    };
  }

  async route(message: string, userId: string): Promise<RoutingDecision> {
    const dbOwls = this.db.owls.getByOwner(userId);
    const folderSpecs = this.folderRegistry?.listAll() ?? [];

    if (dbOwls.length === 0 && folderSpecs.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    // ─── LLM semantic routing ────────────────────────────────────
    if (this.classify) {
      const specialists = [
        ...dbOwls.map((o) => ({
          name: o.name,
          role: o.specialization,
          expertise: o.dna?.expertiseDomains ?? [],
        })),
        ...folderSpecs.map((s) => ({
          name: s.name,
          role: s.role,
          expertise: s.expertise,
        })),
      ];

      let chosenName: string | null = null;
      try {
        chosenName = await this.classify(message, specialists);
      } catch {
        // classify errors are swallowed — fall through to keyword matching / direct
      }

      if (chosenName) {
        const folderSpec = folderSpecs.find((s) => s.name === chosenName);
        if (folderSpec) {
          const syntheticOwl: SpecializedOwl = {
            id: `folder-${folderSpec.name}`,
            ownerId: userId,
            name: folderSpec.name,
            specialization: folderSpec.role,
            personalityPrompt: `You are ${folderSpec.name}, ${folderSpec.role}. Your expertise: ${folderSpec.expertise.join(", ") || "general"}.`,
            routingRules: folderSpec.routingRules.keywords,
            dna: {
              challengeLevel: 0.7,
              verbosity: 0.5,
              expertiseDomains: folderSpec.expertise,
              routingQuality: 0.7,
              evolutionSpeed: 0.5,
            },
            isMainOwl: false,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          const decision = {
            type: "specialist" as const,
            owl: syntheticOwl,
            isFolderSpec: true,
            reason: `LLM routed to folder specialist: ${chosenName}`,
          };
          log.engine.info(`[SecretaryRouter] LLM → folder specialist "${chosenName}"`);
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }

        const dbOwl = dbOwls.find((o) => o.name === chosenName);
        if (dbOwl) {
          const decision = {
            type: "specialist" as const,
            owl: dbOwl,
            reason: `LLM routed to specialist: ${chosenName}`,
          };
          log.engine.info(`[SecretaryRouter] LLM → db specialist "${chosenName}"`);
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }
      }

      // LLM returned null or unknown name — check parliament, then direct
      if (this.shouldConveneParliament(message)) {
        const decision = { type: "parliament" as const, reason: "Complex query detected - convening parliament" };
        this.logRoutingDecision(userId, message, decision, "success");
        return decision;
      }
      const decision = { type: "direct" as const, reason: "LLM classified as no specialist" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    // ─── Keyword fallback (no classify fn) ───────────────────────
    const messageLower = message.toLowerCase();
    const dbTargets = dbOwls.map((owl) => this.toRoutingTarget(owl));
    const folderTargets: RoutingTarget[] = folderSpecs.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
      isFolderSpec: true,
    }));

    const allTargets = [...dbTargets, ...folderTargets];
    const matchedTarget = this.findBestMatch(messageLower, allTargets);

    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(
          `[SecretaryRouter] Keyword → ${matchedTarget.name} (confidence: ${confidence.toFixed(2)})`,
        );

        if (matchedTarget.isFolderSpec) {
          const spec = this.folderRegistry?.get(matchedTarget.name);
          const syntheticOwl: SpecializedOwl = {
            id: `folder-${matchedTarget.name}`,
            ownerId: userId,
            name: matchedTarget.name,
            specialization: spec?.role ?? matchedTarget.name,
            personalityPrompt: `You are ${matchedTarget.name}, ${spec?.role ?? "a specialized assistant"}. Your expertise: ${(matchedTarget.expertiseDomains ?? []).join(", ") || "general"}.`,
            routingRules: matchedTarget.routingRules,
            dna: {
              challengeLevel: 0.7,
              verbosity: 0.5,
              expertiseDomains: matchedTarget.expertiseDomains ?? [],
              routingQuality: 0.7,
              evolutionSpeed: 0.5,
            },
            isMainOwl: false,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          const decision = {
            type: "specialist" as const,
            owl: syntheticOwl,
            isFolderSpec: true,
            reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
          };
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }

        const matchedDbOwl = dbOwls.find((o) => o.name === matchedTarget.name);
        if (!matchedDbOwl) {
          const fallback = { type: "direct" as const, reason: "Matched owl not found in DB" };
          this.logRoutingDecision(userId, message, fallback, "failure");
          return fallback;
        }
        const decision = {
          type: "specialist" as const,
          owl: matchedDbOwl,
          reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
        };
        this.logRoutingDecision(userId, message, decision, "success");
        return decision;
      }
    }

    if (this.shouldConveneParliament(message)) {
      const decision = { type: "parliament" as const, reason: "Complex query detected - convening parliament" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const decision = { type: "direct" as const, reason: "No specialist match found" };
    this.logRoutingDecision(userId, message, decision, "success");
    return decision;
  }

  private findBestMatch(message: string, targets: RoutingTarget[]): RoutingTarget | null {
    let bestMatch: RoutingTarget | null = null;
    let bestScore = 0;
    for (const target of targets) {
      const score = this.scoreMatch(message, target);
      if (score > bestScore) { bestScore = score; bestMatch = target; }
    }
    return bestScore >= MATCH_SCORE_THRESHOLD ? bestMatch : null;
  }

  private scoreMatch(message: string, target: RoutingTarget): number {
    const rules = target.routingRules.map((r) => r.toLowerCase());
    if (rules.length === 0) return 0;
    const messageLower = message.toLowerCase();
    let matches = 0;
    for (const rule of rules) {
      if (messageLower.includes(rule)) matches++;
    }
    return matches / rules.length;
  }

  private calculateConfidence(messageLower: string, target: RoutingTarget): number {
    const matchScore = this.scoreMatch(messageLower, target);
    const dnaScore = target.routingQuality ?? (target.isFolderSpec ? 0.7 : 0.5);
    return (matchScore * MATCH_WEIGHT) + (dnaScore * DNA_WEIGHT);
  }

  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;
    if (keywordCount >= 3) return true;
    if (keywordCount >= 2 && message.length > 200) return true;
    return false;
  }

  getMainOwl(userId: string): SpecializedOwl | null {
    return this.db.owls.getMainOwl(userId);
  }

  private logRoutingDecision(
    userId: string,
    message: string,
    decision: RoutingDecision,
    outcome: "success" | "failure",
  ): void {
    log.engine.info(
      `[SecretaryRouter] Routing decision: ${JSON.stringify({
        userId,
        message: message.slice(0, 100),
        decisionType: decision.type,
        targetOwl: decision.type === "specialist" ? decision.owl.name : null,
        reason: decision.reason,
        outcome,
        timestamp: new Date().toISOString(),
      })}`,
    );
  }
}
```

- [ ] **Step 4: Run secretary tests**

```bash
npx vitest run __tests__/routing/secretary.test.ts
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/routing/secretary.ts __tests__/routing/secretary.test.ts
git commit -m "feat: make SecretaryRouter async with LLM classify injection"
```

---

## Task 4: Wire classify fn into core.ts

**Files:**
- Modify: `src/gateway/core.ts` (two changes: import + constructor call + await)

- [ ] **Step 1: Add import for buildClassifyFn**

At the top of `src/gateway/core.ts`, after the existing `SecretaryRouter` import (line ~40):

```typescript
import { buildClassifyFn } from "../routing/llm-classifier.js";
```

- [ ] **Step 2: Pass classify fn when constructing SecretaryRouter**

Find this block in `core.ts` (around line 1681):

```typescript
if (!this.secretaryRouter) {
  this.secretaryRouter = new SecretaryRouter(this.ctx.db, this.ctx.specializedRegistry);
}
const routingDecision = this.secretaryRouter.route(text, message.userId);
```

Replace with:

```typescript
if (!this.secretaryRouter) {
  const classifyFn = buildClassifyFn(
    this.ctx.provider,
    this.ctx.config.defaultModel ?? "claude-haiku-4-5-20251001",
  );
  this.secretaryRouter = new SecretaryRouter(
    this.ctx.db,
    this.ctx.specializedRegistry,
    classifyFn,
  );
}
const routingDecision = await this.secretaryRouter.route(text, message.userId);
```

- [ ] **Step 3: Build to verify no TypeScript errors**

```bash
npm run build
```

Expected: exits 0.

- [ ] **Step 4: Run all tests**

```bash
npm test 2>&1 | grep -E "✓|✗|Tests " | tail -20
```

Expected: all tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat: wire LLM classify fn into gateway SecretaryRouter"
```

---
