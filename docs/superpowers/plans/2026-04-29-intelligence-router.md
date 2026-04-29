# IntelligenceRouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement tiered model routing so every platform component (Parliament, Evolution, extraction, etc.) can route to the right provider/model instead of always using the default.

**Architecture:** A new `IntelligenceRouter` class in `src/intelligence/router.ts` is instantiated in the `OwlGateway` constructor and stored on `GatewayContext`. Platform components call `ctx.intelligence?.resolve("parliament")` to get `{ provider, model, tier }`. The old `smartRouting` key in JSON config triggers a hard throw at startup to force migration.

**Tech Stack:** TypeScript (strict), Vitest, existing `src/config/loader.ts` validation pattern, existing `GatewayContext` DI container in `src/gateway/types.ts`.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/intelligence/router.ts` | **Create** | `IntelligenceRouter` class, `TaskType`, `Tier`, `ResolvedModel` types, `TASK_TYPE_DEFAULTS` map |
| `__tests__/intelligence-router.test.ts` | **Create** | Unit tests for `IntelligenceRouter.resolve()`, fallback chain, override merging |
| `src/config/loader.ts` | **Modify** | Add `IntelligenceConfig` interface to `StackOwlConfig`; remove `smartRouting` from `DEFAULT_CONFIG`; hard break + intelligence validation in `loadConfig()`; deep merge |
| `__tests__/config-validation.test.ts` | **Modify** | Add tests for hard break + intelligence tier validation |
| `src/gateway/types.ts` | **Modify** | Add `intelligence?: IntelligenceRouter` to `GatewayContext` interface |
| `src/gateway/core.ts` | **Modify** | Instantiate `IntelligenceRouter` from `ctx.config.intelligence` in constructor, assign to `ctx.intelligence` |

---

## Task 1: IntelligenceRouter class

**Files:**
- Create: `src/intelligence/router.ts`
- Create: `__tests__/intelligence-router.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/intelligence-router.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import {
  IntelligenceRouter,
  TASK_TYPE_DEFAULTS,
  type IntelligenceConfig,
} from "../src/intelligence/router.js";

function makeConfig(overrides?: Partial<IntelligenceConfig>): IntelligenceConfig {
  return {
    tiers: {
      high: { provider: "anthropic", model: "claude-opus-4-7" },
      mid:  { provider: "anthropic", model: "claude-sonnet-4-6" },
      low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
    },
    defaults: {
      parliament:   "high",
      extraction:   "low",
      conversation: "mid",
    },
    ...overrides,
  };
}

describe("IntelligenceRouter", () => {
  it("resolves parliament to high tier", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-opus-4-7");
    expect(result.tier).toBe("high");
  });

  it("resolves extraction to low tier", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("extraction");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-haiku-4-5-20251001");
    expect(result.tier).toBe("low");
  });

  it("falls back to mid tier when task type not in defaults", () => {
    const router = new IntelligenceRouter(
      makeConfig({ defaults: {} }),
      "anthropic",
      "claude-sonnet-4-6",
    );
    const result = router.resolve("synthesis");
    expect(result.tier).toBe("mid");
    expect(result.model).toBe("claude-sonnet-4-6");
  });

  it("applies provider override", () => {
    const config = makeConfig({
      overrides: { parliament: { provider: "openai", model: "gpt-4o" } },
    });
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("openai");
    expect(result.model).toBe("gpt-4o");
    expect(result.tier).toBe("high");
  });

  it("applies partial model-only override", () => {
    const config = makeConfig({
      overrides: { parliament: { model: "claude-opus-4-7-custom" } },
    });
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("parliament");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-opus-4-7-custom");
  });

  it("falls back to fallback provider/model when mid tier not configured", () => {
    const config: IntelligenceConfig = {
      tiers: {
        high: { provider: "anthropic", model: "claude-opus-4-7" },
        mid:  { provider: "", model: "" },
        low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
      },
      defaults: {},
    };
    const router = new IntelligenceRouter(config, "ollama", "llama3.2");
    const result = router.resolve("synthesis");
    expect(result.provider).toBe("ollama");
    expect(result.model).toBe("llama3.2");
  });

  it("TASK_TYPE_DEFAULTS covers all 9 task types", () => {
    const types = [
      "conversation", "parliament", "evolution", "extraction",
      "episodic", "classification", "synthesis", "summarization", "clarification",
    ];
    for (const t of types) {
      expect(TASK_TYPE_DEFAULTS).toHaveProperty(t);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/intelligence-router.test.ts
```

Expected: FAIL — `Cannot find module '../src/intelligence/router.js'`

- [ ] **Step 3: Create the router implementation**

Create `src/intelligence/router.ts`:

```typescript
export type Tier = "high" | "mid" | "low";

export type TaskType =
  | "conversation"
  | "parliament"
  | "evolution"
  | "extraction"
  | "episodic"
  | "classification"
  | "synthesis"
  | "summarization"
  | "clarification";

export interface TierConfig {
  provider: string;
  model: string;
}

export interface IntelligenceConfig {
  tiers: Record<Tier, TierConfig>;
  defaults: Partial<Record<TaskType, Tier>>;
  overrides?: Partial<Record<TaskType, Partial<TierConfig>>>;
}

export interface ResolvedModel {
  provider: string;
  model: string;
  tier: Tier;
}

export const TASK_TYPE_DEFAULTS: Record<TaskType, Tier> = {
  conversation:   "mid",
  parliament:     "high",
  evolution:      "mid",
  extraction:     "low",
  episodic:       "low",
  classification: "low",
  synthesis:      "high",
  summarization:  "low",
  clarification:  "mid",
};

export class IntelligenceRouter {
  constructor(
    private config: IntelligenceConfig,
    private fallbackProvider: string,
    private fallbackModel: string,
  ) {}

  resolve(taskType: TaskType): ResolvedModel {
    const tier = (this.config.defaults[taskType] ?? TASK_TYPE_DEFAULTS[taskType] ?? "mid") as Tier;
    const base = this.config.tiers[tier];
    const usedBase = (base?.provider && base?.model)
      ? base
      : { provider: this.fallbackProvider, model: this.fallbackModel };

    const override = this.config.overrides?.[taskType];

    return {
      provider: override?.provider ?? usedBase.provider,
      model:    override?.model    ?? usedBase.model,
      tier,
    };
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
npx vitest run __tests__/intelligence-router.test.ts
```

Expected: PASS — 7 tests passing

- [ ] **Step 5: Commit**

```bash
git add src/intelligence/router.ts __tests__/intelligence-router.test.ts
git commit -m "feat(intelligence): add IntelligenceRouter class with tiered model routing"
```

---

## Task 2: Config loader — types, validation, defaults

**Files:**
- Modify: `src/config/loader.ts`
- Modify: `__tests__/config-validation.test.ts`

- [ ] **Step 1: Write the failing config tests**

Append to `__tests__/config-validation.test.ts` (inside the `describe('Config Validation', ...)` block, after the last `it()` block before the closing `}`):

```typescript
  it('throws on smartRouting key in user config', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        smartRouting: { enabled: false, availableModels: [] },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('smartRouting is no longer supported');
  });

  it('throws when intelligence.tiers is missing', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: { defaults: {}, tiers: {} },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('intelligence.tiers.mid is required');
  });

  it('throws when intelligence.tiers.mid is missing', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: {
          tiers: { high: { provider: 'anthropic', model: 'opus' }, low: { provider: 'anthropic', model: 'haiku' } },
          defaults: {},
        },
      }),
      'utf-8',
    );
    await expect(loadConfig(testDir)).rejects.toThrow('intelligence.tiers.mid is required');
  });

  it('loads valid intelligence block without error', async () => {
    await writeFile(
      join(testDir, 'stackowl.config.json'),
      JSON.stringify({
        defaultProvider: 'ollama',
        defaultModel: 'llama3.2',
        providers: { ollama: { baseUrl: 'http://localhost:11434' } },
        intelligence: {
          tiers: {
            high: { provider: 'anthropic', model: 'claude-opus-4-7' },
            mid:  { provider: 'anthropic', model: 'claude-sonnet-4-6' },
            low:  { provider: 'anthropic', model: 'claude-haiku-4-5-20251001' },
          },
          defaults: { parliament: 'high', extraction: 'low' },
        },
      }),
      'utf-8',
    );
    const config = await loadConfig(testDir);
    expect(config.intelligence?.tiers.high.model).toBe('claude-opus-4-7');
    expect(config.intelligence?.tiers.mid.model).toBe('claude-sonnet-4-6');
  });
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run __tests__/config-validation.test.ts
```

Expected: FAIL — 4 new tests fail (smartRouting throws test fails because loadConfig currently doesn't throw; intelligence tests fail because the field doesn't exist)

- [ ] **Step 3: Add `IntelligenceConfig` type + `intelligence?` field to `StackOwlConfig`**

In `src/config/loader.ts`, import `IntelligenceConfig` from the router (add after the existing imports at line 10):

```typescript
import type { IntelligenceConfig } from "../intelligence/router.js";
```

In `src/config/loader.ts`, find the `smartRouting?` block at lines 95-104:

```typescript
  smartRouting?: {
    enabled: boolean;
    fallbackProvider?: string;
    fallbackModel?: string;
    availableModels: {
      modelName: string;
      providerName: string;
      description?: string;
    }[];
  };
```

Replace it with (keeps `smartRouting` as `@deprecated` so `ModelRouter` + `router.test.ts` still compile; adds `intelligence`):

```typescript
  /**
   * @deprecated Use `intelligence` block instead.
   * Kept in type so existing ModelRouter references compile.
   * At runtime, loading a config JSON that contains smartRouting throws.
   */
  smartRouting?: {
    enabled: boolean;
    fallbackProvider?: string;
    fallbackModel?: string;
    availableModels: {
      modelName: string;
      providerName: string;
      description?: string;
    }[];
  };
  /** Tiered model routing for all platform components. */
  intelligence?: IntelligenceConfig;
```

- [ ] **Step 4: Update `DEFAULT_CONFIG` — remove `smartRouting`, leave `intelligence` absent**

In `src/config/loader.ts`, find the `smartRouting` block in `DEFAULT_CONFIG` (lines 316-321):

```typescript
  smartRouting: {
    enabled: false,
    fallbackProvider: "anthropic",
    fallbackModel: "claude-3-5-sonnet-latest",
    availableModels: [],
  },
```

Delete those 6 lines entirely. (`intelligence` is optional so no default entry needed.)

- [ ] **Step 5: Remove `smartRouting` from deep merge; add `intelligence` pass-through**

In `src/config/loader.ts`, find the deep-merge block for `smartRouting` (lines 403-406):

```typescript
      smartRouting: {
        ...DEFAULT_CONFIG.smartRouting,
        ...(userConfig.smartRouting || {}),
      } as NonNullable<StackOwlConfig["smartRouting"]>,
```

Replace with (no deep merge for smartRouting; pass intelligence through):

```typescript
      intelligence: userConfig.intelligence,
```

- [ ] **Step 6: Add hard break + intelligence validation in `loadConfig()`**

In `src/config/loader.ts`, find this line (around line 377):

```typescript
    const userConfig = JSON.parse(raw) as Partial<StackOwlConfig>;
```

After that line, add the hard break:

```typescript
    if ("smartRouting" in userConfig) {
      throw new Error(
        "[Config] smartRouting is no longer supported. Replace it with the intelligence block. See docs/platform-audit/progress.md.",
      );
    }
```

Then, after the final merge closes (`};`) and before the `// Derive defaultModel` comment, add intelligence validation:

```typescript
    // Intelligence block validation
    if (config.intelligence) {
      const tiers = config.intelligence.tiers;
      if (!tiers || !tiers.mid?.provider || !tiers.mid?.model) {
        throw new Error(
          "[Config] intelligence.tiers.mid is required (used as fallback for unspecified task types).",
        );
      }
    }
```

- [ ] **Step 7: Run all config tests**

```bash
npx vitest run __tests__/config-validation.test.ts
```

Expected: PASS — all tests passing (including the 4 new ones)

- [ ] **Step 8: Run full test suite to check for regressions**

```bash
npx vitest run
```

Expected: No new failures. If `router.test.ts` fails because it sets `smartRouting` in the object literal (not loading from JSON file, so no throw), it will still pass — `loadConfig()` only throws when the key appears in the actual JSON file on disk, not when constructing a config object in tests.

- [ ] **Step 9: Commit**

```bash
git add src/config/loader.ts __tests__/config-validation.test.ts
git commit -m "feat(config): add IntelligenceConfig type, hard break on smartRouting, intelligence validation"
```

---

## Task 3: Wire GatewayContext and OwlGateway constructor

**Files:**
- Modify: `src/gateway/types.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Add `intelligence` field to `GatewayContext`**

In `src/gateway/types.ts`, find the Epic 7 block near line 344:

```typescript
  // ─── Epic 7: Knowledge Building Modules ─────────────────
  pelletRetriever?: import("../pellets/pellet-retriever.js").PelletRetriever;
  knowledgeBase?: import("../pellets/knowledge-base.js").KnowledgeBase;
  proactiveGenerator?: import("../pellets/proactive-generator.js").ProactiveKnowledgeGenerator;
  eventBasedGenerator?: import("../pellets/event-based-generator.js").EventBasedPelletGenerator;
  semanticDedup?: import("../pellets/semantic-dedup.js").SemanticDeduplicator;
}
```

Replace the closing `}` with:

```typescript
  // ─── Epic 7: Knowledge Building Modules ─────────────────
  pelletRetriever?: import("../pellets/pellet-retriever.js").PelletRetriever;
  knowledgeBase?: import("../pellets/knowledge-base.js").KnowledgeBase;
  proactiveGenerator?: import("../pellets/proactive-generator.js").ProactiveKnowledgeGenerator;
  eventBasedGenerator?: import("../pellets/event-based-generator.js").EventBasedPelletGenerator;
  semanticDedup?: import("../pellets/semantic-dedup.js").SemanticDeduplicator;

  // ─── Intelligence Router (tiered model routing) ───────────
  intelligence?: import("../intelligence/router.js").IntelligenceRouter;
}
```

- [ ] **Step 2: Instantiate IntelligenceRouter in OwlGateway constructor**

In `src/gateway/core.ts`, find the Epic 4 initialization block (lines 330-341):

```typescript
    // ─── Epic 4: Initialize Tool Mastery Modules ──────────────
    this.toolMastery = new ToolMastery();
    this.fallbackSequencer = new FallbackSequencer();
    this.fallbackDiscoverer = new FallbackDiscoverer();
    this.domainToolMap = new DomainToolMap();
    this.delegationDecider = new DelegationDecider();
    if (ctx.provider) {
      this.taskDecomposer = new TaskDecomposer(ctx.provider);
      this.resultSynthesizer = new ResultSynthesizer(ctx.provider);
    }

    log.engine.info("[Epic 3&4] Clarification and Tool Mastery modules initialized");
```

After `log.engine.info("[Epic 3&4] ...")`, add:

```typescript
    // ─── Intelligence Router (tiered model routing) ────────────
    if (ctx.config.intelligence) {
      const { IntelligenceRouter } = await import("../intelligence/router.js");
      ctx.intelligence = new IntelligenceRouter(
        ctx.config.intelligence,
        ctx.config.defaultProvider,
        ctx.config.defaultModel,
      );
      log.engine.info("[IntelligenceRouter] Tiered model routing active");
    }
```

Wait — the constructor is not `async`. Check if the constructor uses any `await`. Looking at core.ts constructor: it does NOT use `await` — it's synchronous. So we cannot use a dynamic `await import()` inside it.

Instead, use a static import at the top of `core.ts`. Find the imports section at the top of `src/gateway/core.ts` and add:

```typescript
import { IntelligenceRouter } from "../intelligence/router.js";
```

Then add the initialization block in the constructor (synchronous):

```typescript
    // ─── Intelligence Router (tiered model routing) ────────────
    if (ctx.config.intelligence) {
      ctx.intelligence = new IntelligenceRouter(
        ctx.config.intelligence,
        ctx.config.defaultProvider,
        ctx.config.defaultModel,
      );
      log.engine.info("[IntelligenceRouter] Tiered model routing active");
    }
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
npm run build
```

Expected: Compilation succeeds with no errors.

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run
```

Expected: All tests passing, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/types.ts src/gateway/core.ts
git commit -m "feat(gateway): wire IntelligenceRouter into GatewayContext via OwlGateway constructor"
```

---

## Task 4: Update stackowl.config.json

> This task updates the live config file that is gitignored. It is a manual one-time step.

- [ ] **Step 1: Open `stackowl.config.json` in the project root**

The file currently has no `smartRouting` key (confirmed during research). No hard break will fire. However, to enable tiered routing you must add the `intelligence` block. If you do not add it, platform components will continue using the default provider (no error — `ctx.intelligence` is optional).

Add the following block to `stackowl.config.json` (after any top-level key, e.g., after `"defaultProvider"`):

```json
"intelligence": {
  "tiers": {
    "high": { "provider": "anthropic", "model": "claude-opus-4-7" },
    "mid":  { "provider": "anthropic", "model": "claude-sonnet-4-6" },
    "low":  { "provider": "anthropic", "model": "claude-haiku-4-5-20251001" }
  },
  "defaults": {
    "conversation":   "mid",
    "parliament":     "high",
    "evolution":      "mid",
    "extraction":     "low",
    "episodic":       "low",
    "classification": "low",
    "synthesis":      "high",
    "summarization":  "low",
    "clarification":  "mid"
  }
}
```

Replace `"anthropic"` / model names with whatever providers are configured in your `providers` block.

- [ ] **Step 2: Verify the app starts without error**

```bash
npm run dev
```

Expected: App starts, logs `[IntelligenceRouter] Tiered model routing active` in the engine output.

---

## Usage Pattern for Platform Components

After this plan ships, any platform component that has access to `ctx` uses the router like this:

```typescript
// Parliament orchestrator
const { provider, model } = ctx.intelligence?.resolve("parliament")
  ?? { provider: ctx.config.defaultProvider, model: ctx.config.defaultModel };
```

The `??` fallback ensures backward compatibility for components running in tests or before `intelligence` is configured.
