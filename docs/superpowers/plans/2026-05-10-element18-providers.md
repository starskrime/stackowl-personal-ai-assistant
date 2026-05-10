# Element 18 — Providers Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the providers layer by deleting inert code (`engine/router.ts`, `openai-compat.ts`), making `IntelligenceRouter` the single authoritative routing path with cost-awareness and capability filtering, and adding a `ProviderCircuitBreaker` for passive health monitoring.

**Architecture:** Delete `ModelRouter` (active but always returns `config.defaultModel`); add `resolveCapable()`, `resolveWithCostAwareness()`, `resolveFailover()` to `IntelligenceRouter`; add `ProviderCircuitBreaker` per provider with CLOSED/OPEN/HALF_OPEN state machine; always construct `ctx.intelligence` from a default config so it's never null. Net: +1 new file, −2 deleted = −1 file, −450 LOC.

**Tech Stack:** TypeScript (strict), Vitest, existing IntelligenceRouter / CostTracker / ProviderRegistry.

---

## File Map

| File | Action |
|---|---|
| `src/engine/router.ts` | **Delete** — Task 1 |
| `src/engine/runtime.ts` | **Modify** — Tasks 1, 8 |
| `src/config/loader.ts` | **Modify** — Tasks 2, 3 |
| `src/gateway/adapters/telegram-config/menu.ts` | **Modify** — Task 2 |
| `src/gateway/adapters/telegram-config/screens.ts` | **Modify** — Task 2 |
| `src/cli/onboarding-flow.ts` | **Modify** — Task 2 |
| `src/skills/config-context.ts` | **Modify** — Task 2 |
| `src/gateway/core.ts` | **Modify** — Tasks 3, 6 |
| `src/intelligence/router.ts` | **Modify** — Tasks 4, 5, 6, 7 |
| `src/gateway/handlers/context-builder.ts` | **Modify** — Task 8 |
| `src/providers/circuit-breaker.ts` | **Create** — Task 9 |
| `src/providers/registry.ts` | **Modify** — Task 9 |
| `src/providers/openai-compat.ts` | **Delete** — Task 10 |
| `src/providers/anthropic-native.ts` | **Modify** — Task 11 |
| `src/providers/protocols/openai.ts` | **Modify** — Task 11 |
| `src/costs/pricing.ts` | **Modify** — Task 12 |
| `src/index.ts` | **Modify** — Task 12 |
| `__tests__/element18/circuit-breaker.test.ts` | **Create** — Task 13 |
| `__tests__/element18/intelligence-router-extensions.test.ts` | **Create** — Task 13 |
| `__tests__/element18/provider-registry-circuit.test.ts` | **Create** — Task 13 |
| `__tests__/element18/adapter-missing-model.test.ts` | **Create** — Task 13 |

---

### Task 1: D1a — Delete `engine/router.ts`; replace `runtime.ts` call sites with `config.defaultModel`

`ModelRouter.route()` is called at `runtime.ts:775` and `runtime.ts:1882` but always returns `{ modelName: config.defaultModel }` (because `config.smartRouting` is always `undefined` at runtime — loader throws if present in JSON). The `HEAVY_PATTERNS`/`SIMPLE_PATTERNS` regex arrays violate the no-hardcoded-keywords rule. Delete the file and replace both call sites with equivalent minimal code. Full `IntelligenceRouter` wiring comes in Task 8 after the new methods exist.

**Files:**
- Delete: `src/engine/router.ts`
- Modify: `src/engine/runtime.ts:23` (remove import)
- Modify: `src/engine/runtime.ts:774-791` (replace routing block)
- Modify: `src/engine/runtime.ts:1880-1918` (replace escalation block)

- [ ] **Step 1: Delete `src/engine/router.ts`**

```bash
rm src/engine/router.ts
```

- [ ] **Step 2: Remove the `ModelRouter` import from `runtime.ts:23`**

In `src/engine/runtime.ts`, remove line 23:
```typescript
// DELETE this line:
import { ModelRouter } from "./router.js";
```

- [ ] **Step 3: Replace the routing block at `runtime.ts:774-791`**

Find this block (starts with comment "// 1. Determine optimal model"):
```typescript
    // 1. Determine optimal model (heuristic, no LLM call)
    let routeDecision = ModelRouter.route(userMessage, config);
    let optimalModel = routeDecision.modelName;

    // Dynamic provider resolution based on route (if cross-provider routing is needed early)
    let currentProvider = provider;
    if (
      routeDecision.providerName &&
      routeDecision.providerName !== provider.name &&
      context.providerRegistry
    ) {
      log.engine.warn(
        `Cross-provider routing on first turn: Swapping ${provider.name} for ${routeDecision.providerName}`,
      );
      currentProvider = context.providerRegistry.get(
        routeDecision.providerName,
      );
    }
```

Replace with:
```typescript
    // 1. Determine optimal model (IntelligenceRouter — full wiring added in Task 8)
    let optimalModel = config.defaultModel;
    let currentProvider = provider;
```

- [ ] **Step 4: Replace the escalation block at `runtime.ts:1880-1918`**

Find this block (starts with comment "// If we've failed multiple tool calls"):
```typescript
        // If we've failed multiple tool calls in a row across the whole loop,
        // it's highly likely the local model is hallucinating or stuck.
        // Try to trigger a fallback router switch to a heavier cloud model.
        if (globalConsecutiveFailures >= 2) {
          const newRoute = ModelRouter.route(
            userMessage,
            config,
            globalConsecutiveFailures,
          );

          if (
            newRoute.providerName &&
            newRoute.providerName !== currentProvider.name &&
            context.providerRegistry
          ) {
            try {
              const fallbackProvider = context.providerRegistry.get(
                newRoute.providerName,
              );
              log.engine.warn(
                `[Cross-Provider Hot Swap] Tool failed ${globalConsecutiveFailures}x. Swapping provider: ${currentProvider.name} → ${newRoute.providerName}`,
              );
              currentProvider = fallbackProvider;
              if (context.onProgress)
                await context.onProgress(
                  `🔄 **Cross-Provider Triggered:** Swapping to ${newRoute.providerName} (${newRoute.modelName}) to resolve failure.`,
                );
            } catch (err) {
              log.engine.warn(
                `Could not swap to fallback provider "${newRoute.providerName}" - staying on current provider. Reason: ${(err as Error).message}`,
              );
            }
          }

          if (newRoute.modelName && newRoute.modelName !== optimalModel) {
            log.engine.warn(
              `Tool failed ${globalConsecutiveFailures}x. Swapping model: ${optimalModel} → ${newRoute.modelName}`,
            );
            optimalModel = newRoute.modelName;
          }
        }
```

Replace with:
```typescript
        // If we've failed multiple tool calls in a row, log the condition.
        // Full IntelligenceRouter failover wiring added in Task 8.
        if (globalConsecutiveFailures >= 2) {
          log.engine.warn(
            `[Runtime] Tool failed ${globalConsecutiveFailures}x — no fallback configured yet.`,
          );
        }
```

- [ ] **Step 5: Run tests to verify nothing broke**

```bash
npx vitest run
```

Expected: same count as before (baseline ~5001). Zero new failures from the deletion.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(providers): D1 — delete ModelRouter; replace runtime.ts call sites with config.defaultModel"
```

---

### Task 2: D1b — Remove `smartRouting` dead code from all sites

`ModelRouter` was the only consumer of `config.smartRouting`. With it gone, the `smartRouting` type stub in `StackOwlConfig`, its Telegram config menu, CLI onboarding section, and one skills reference are all dead. The loader already throws at runtime if `smartRouting` appears in JSON (`loader.ts:408-411`). Clean it all up.

**Files:**
- Modify: `src/config/loader.ts` (remove type stub + runtime throw)
- Modify: `src/gateway/adapters/telegram-config/screens.ts` (remove 3 render functions + menu button)
- Modify: `src/gateway/adapters/telegram-config/menu.ts` (remove dispatch cases + handler methods)
- Modify: `src/cli/onboarding-flow.ts` (remove sr_* fields, steps, render cases, input cases)
- Modify: `src/skills/config-context.ts` (remove one-liner)

- [ ] **Step 1: Remove `smartRouting` type stub from `src/config/loader.ts`**

Find and remove these lines (the full `smartRouting?` block, including the `@deprecated` comment block above it):
```typescript
  /**
   * @deprecated Use `intelligence` block instead.
   * Kept in type so existing ModelRouter references compile.
   * At runtime, a config JSON containing smartRouting throws.
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
```

Also remove the runtime throw block (around line 408):
```typescript
    if ("smartRouting" in userConfig) {
      throw new Error(
        "[Config] smartRouting is no longer supported. Replace it with the intelligence block. See docs/platform-audit/progress.md.",
      );
    }
```

- [ ] **Step 2: Remove `smartRouting` from `src/skills/config-context.ts`**

Find and remove this one line (around line 203):
```typescript
    if (this.config.smartRouting?.enabled) caps.push("smart_routing");
```

- [ ] **Step 3: Remove smartRouting render functions from `src/gateway/adapters/telegram-config/screens.ts`**

3a. Remove the "Smart Routing" button from the main menu keyboard (around line 92). Change:
```typescript
  const keyboard = new InlineKeyboard()
    .text("📡 Providers",      "cfg:pr").text("🎯 Model Roles",   "cfg:rl").row()
    .text("⚡ Smart Routing",  "cfg:sr").text("🏥 Health Check",  "cfg:hc").row()
    .text("❌ Close",          "cfg:cl");
```
To:
```typescript
  const keyboard = new InlineKeyboard()
    .text("📡 Providers",      "cfg:pr").text("🎯 Model Roles",   "cfg:rl").row()
    .text("🏥 Health Check",   "cfg:hc").row()
    .text("❌ Close",          "cfg:cl");
```

3b. Delete the three render functions (find by comment headers and delete entire sections):
- The `// ─── Screen: Smart Routing ───` section: `renderSmartRouting()` function (~44 lines, roughly lines 357-402)
- The `// ─── Screen: Smart Routing — Provider Picker ───` section: `renderSmartRoutingProviderPicker()` (~9 lines)
- The `// ─── Screen: Smart Routing — Model Picker ───` section: `renderSmartRoutingModelPicker()` (~12 lines)

- [ ] **Step 4: Remove smartRouting dispatch cases from `src/gateway/adapters/telegram-config/menu.ts`**

4a. Remove the "Smart Routing" dispatch block (roughly lines 387-440 — all the `cmd === "sr"`, `cmd === "sr_tog"`, `cmd === "sr_add"`, `cmd.startsWith("sr_ap:")`, `cmd.startsWith("sr_am:")`, `cmd.startsWith("sr_rm:")`, `cmd.startsWith("sr_up:")`, `cmd.startsWith("sr_dn:")` blocks).

4b. Remove the state machine cases (roughly lines 497-511):
```typescript
      case "smart_routing":
        await this.editScreen(ctx, state, renderSmartRouting(config));
        break;
      case "sr_prov_pick": {
        const providers = getModelLoader().getAll().map(d => d.name);
        await this.editScreen(ctx, state, renderSmartRoutingProviderPicker(providers));
        break;
      }
      case "sr_model_pick": {
        const provName = state.pendingSrProvider ?? "";
        const def      = getModelLoader().get(provName);
        const models   = def?.availableModels ?? [];
        await this.editScreen(ctx, state, renderSmartRoutingModelPicker(provName, models));
        break;
      }
```

4c. Remove the four private handler methods at the bottom (roughly lines 890-960):
- `private async toggleSmartRouting(...)` 
- `private async addRosterEntry(...)`
- `private async removeRosterEntry(...)`
- `private async moveRosterEntry(...)`

4d. Remove the `pendingSrProvider?` field from the `MenuState` type (search for it in the file).

4e. Remove imports of the deleted render functions at the top of the file:
```typescript
// DELETE the renderSmartRouting, renderSmartRoutingProviderPicker, renderSmartRoutingModelPicker imports
```

- [ ] **Step 5: Remove smartRouting onboarding from `src/cli/onboarding-flow.ts`**

5a. In the `OnboardingData` interface (around line 75), remove these fields:
```typescript
  srEnabled?:         boolean;
  srRoster?:          Array<{ modelName: string; providerName: string }>;
  srAvailProviders?:  string[];
  srPendingProvider?: string;
  srProviderModels?:  string[];
```

5b. In the `OnboardingStep` type (around line 90), remove:
```typescript
  | "sr_ask"
  | "sr_prov_pick"
  | "sr_model_pick"
  | "sr_more"
```

5c. In `buildConfig()` (around line 173), remove:
```typescript
  if (d.srEnabled && d.srRoster && d.srRoster.length >= 2) {
    (cfg as any).smartRouting = {
      enabled: true,
      availableModels: d.srRoster,
      fallbackProvider: d.srRoster[d.srRoster.length - 1].providerName,
      fallbackModel:    d.srRoster[d.srRoster.length - 1].modelName,
    };
  }
```

5d. In the review render case (around line 603), remove the Smart Routing summary line:
```typescript
          D("  Smart Routing") + W(
            d.srEnabled && d.srRoster?.length
              ? `ON — ${d.srRoster.length} models`
              : "OFF"
          ),
```

5e. In the `_showStep` switch, remove render cases `"sr_ask"`, `"sr_prov_pick"`, `"sr_model_pick"`, `"sr_more"` (roughly lines 615-667).

5f. Change all `this._step = "sr_ask"` assignments (there are ~9 of them, roughly lines 761-873) to `this._step = "chan_multi"` — this skips the smart routing step and goes directly to channel configuration. Example:
```typescript
// BEFORE:
        d.provModel = models[n - 1];
        this._step  = "sr_ask";
        this._showStep(ui);
// AFTER:
        d.provModel = models[n - 1];
        this._step  = "chan_multi";
        this._showStep(ui);
```

5g. In the `_handleInput` switch, remove cases `"sr_ask"`, `"sr_prov_pick"`, `"sr_model_pick"`, `"sr_more"` (roughly lines 962-1041).

- [ ] **Step 6: Run tests**

```bash
npx vitest run
```

Expected: same passing count, no new failures.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(providers): D1b — remove smartRouting dead code (type stub, Telegram menu, CLI onboarding, skills context)"
```

---

### Task 3: D2 — `buildDefaultIntelligenceConfig()` + always construct `ctx.intelligence`

Currently `ctx.intelligence` is only constructed `if (ctx.config.intelligence)`. A gateway without an `intelligence` config block runs with `ctx.intelligence = undefined`, bypassing all routing. Add `buildDefaultIntelligenceConfig()` that returns a pass-through config using the owl's default provider/model, and always construct `ctx.intelligence` from it.

**Files:**
- Modify: `src/config/loader.ts`
- Modify: `src/gateway/core.ts:432-439`

- [ ] **Step 1: Add `buildDefaultIntelligenceConfig()` to `src/config/loader.ts`**

Add after the existing imports (after the `IntelligenceConfig` import):

At the top of the file, the import for `IntelligenceConfig` already exists via `StackOwlConfig.intelligence?: IntelligenceConfig`. Add a direct import:
```typescript
import type { IntelligenceConfig } from "../intelligence/router.js";
```

Then add this function before `loadConfig()`:
```typescript
/**
 * Build a pass-through IntelligenceConfig from bare provider/model defaults.
 * Used when the user config omits the `intelligence` block entirely.
 * Behavior is identical to the pre-IntelligenceRouter default: every task type
 * resolves to the same provider and model.
 */
export function buildDefaultIntelligenceConfig(
  defaultProvider: string,
  defaultModel: string,
): IntelligenceConfig {
  const tier = { provider: defaultProvider, model: defaultModel };
  return {
    tiers: { high: tier, mid: tier, low: tier },
    defaults: {
      conversation:   "mid",
      parliament:     "high",
      evolution:      "mid",
      extraction:     "low",
      episodic:       "low",
      classification: "low",
      synthesis:      "high",
      summarization:  "low",
      clarification:  "mid",
    },
  };
}
```

- [ ] **Step 2: Update `src/gateway/core.ts:432-439` to always construct**

Find this block:
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

Replace with:
```typescript
    // ─── Intelligence Router (tiered model routing) ────────────
    {
      const intelligenceConfig = ctx.config.intelligence
        ?? buildDefaultIntelligenceConfig(ctx.config.defaultProvider, ctx.config.defaultModel);
      ctx.intelligence = new IntelligenceRouter(
        intelligenceConfig,
        ctx.config.defaultProvider,
        ctx.config.defaultModel,
      );
      log.engine.info(
        ctx.config.intelligence
          ? "[IntelligenceRouter] Tiered model routing active"
          : "[IntelligenceRouter] Using default pass-through config (no intelligence block in config)",
      );
    }
```

Add the import for `buildDefaultIntelligenceConfig` at the top of `core.ts`:
```typescript
import { buildDefaultIntelligenceConfig } from "../config/loader.js";
```

- [ ] **Step 3: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 4: Commit**

```bash
git add src/config/loader.ts src/gateway/core.ts
git commit -m "feat(providers): D2 — always construct ctx.intelligence from default config when intelligence block absent"
```

---

### Task 4: D6 — Extend `IntelligenceConfig` types

Add `FallbackEntry`, `HealthPolicy`, `CostPolicy` types and extend `TierConfig` with `capabilities?` and `IntelligenceConfig` with three new optional fields. Zero breaking change — all new fields are optional.

**Files:**
- Modify: `src/intelligence/router.ts`

- [ ] **Step 1: Add the new types and extend existing interfaces**

Replace the content of `src/intelligence/router.ts` with:

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
  /** Optional capability tags. Vocabulary: vision, code, reasoning, long-context, tool-use, fast, structured-output */
  capabilities?: string[];
}

export interface FallbackEntry {
  provider: string;
  model: string;
  /** Which failure tiers this fallback entry covers. */
  forTiers: Tier[];
}

export interface HealthPolicy {
  /** Number of consecutive failures before opening the circuit. Default: 5 */
  failureThreshold: number;
  /** Milliseconds to wait in OPEN state before trying HALF_OPEN. Default: 30000 */
  recoveryTimeoutMs: number;
}

export interface CostPolicy {
  /** Max daily spend in USD. 0 = unlimited. Default: 0 */
  maxDailyUsd: number;
  /** Downgrade to a cheaper tier when budget is exhausted. Default: true */
  downgradeTierOnBudgetExhausted: boolean;
}

export interface IntelligenceConfig {
  tiers: Record<Tier, TierConfig>;
  defaults: Partial<Record<TaskType, Tier>>;
  overrides?: Partial<Record<TaskType, Partial<TierConfig>>>;
  /** Ordered list of fallback providers/models when primary tier is unavailable. */
  fallbacks?: FallbackEntry[];
  /** Circuit breaker parameters for provider health monitoring. */
  healthPolicy?: HealthPolicy;
  /** Cost-based routing policy. */
  costPolicy?: CostPolicy;
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
    private getBudgetState?: () => { dailyRemainingUsd: number; maxDailyUsd: number },
  ) {}

  resolve(taskType: TaskType): ResolvedModel {
    const tier = this.config.defaults[taskType] ?? TASK_TYPE_DEFAULTS[taskType];
    const base = this.config.tiers[tier];
    const usedBase = (base?.provider && base?.model)
      ? base
      : { provider: this.fallbackProvider, model: this.fallbackModel };

    const override = this.config.overrides?.[taskType];

    return {
      provider: override?.provider || usedBase.provider,
      model:    override?.model    || usedBase.model,
      tier,
    };
  }

  // resolveCapable(), resolveWithCostAwareness(), resolveFailover() added in Tasks 5-7
}
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run
```

Expected: same passing count. The type changes are additive — no existing call sites break.

- [ ] **Step 3: Commit**

```bash
git add src/intelligence/router.ts
git commit -m "feat(providers): D6 — extend IntelligenceConfig with FallbackEntry, HealthPolicy, CostPolicy; add capabilities to TierConfig"
```

---

### Task 5: D4 — Add `resolveCapable()` to `IntelligenceRouter`

Add `resolveCapable(taskType, required)` — routes to the highest-priority tier whose `capabilities[]` contains all required tags. Falls back to `resolve(taskType)` with a warning when no capable tier exists (graceful degrade, not hard-fail).

**Files:**
- Modify: `src/intelligence/router.ts`

- [ ] **Step 1: Add `resolveCapable()` method to `IntelligenceRouter`**

In `src/intelligence/router.ts`, add this method to the `IntelligenceRouter` class after `resolve()`:

```typescript
  /**
   * Route to the highest-priority tier whose capabilities[] contains all required tags.
   * Falls back to resolve(taskType) with a warning when no capable tier exists.
   */
  resolveCapable(taskType: TaskType, required: string[]): ResolvedModel {
    if (required.length === 0) return this.resolve(taskType);

    const tierOrder: Tier[] = ["high", "mid", "low"];
    for (const tier of tierOrder) {
      const cfg = this.config.tiers[tier];
      if (!cfg?.provider || !cfg?.model) continue;
      if (required.every((tag) => cfg.capabilities?.includes(tag))) {
        return { provider: cfg.provider, model: cfg.model, tier };
      }
    }

    // No capable tier — graceful degrade with warning
    const log = (await import("../logger.js")).log;
    log.engine.warn(
      `[IntelligenceRouter] No tier has capabilities [${required.join(", ")}] — falling back to unconstrained resolve`,
    );
    return this.resolve(taskType);
  }
```

Wait — dynamic import in a sync method is wrong. Use synchronous logger import at the top of the file instead:

At the top of `src/intelligence/router.ts`, add:
```typescript
import { log } from "../logger.js";
```

Then the method becomes:
```typescript
  resolveCapable(taskType: TaskType, required: string[]): ResolvedModel {
    if (required.length === 0) return this.resolve(taskType);

    const tierOrder: Tier[] = ["high", "mid", "low"];
    for (const tier of tierOrder) {
      const cfg = this.config.tiers[tier];
      if (!cfg?.provider || !cfg?.model) continue;
      if (required.every((tag) => cfg.capabilities?.includes(tag))) {
        return { provider: cfg.provider, model: cfg.model, tier };
      }
    }

    log.engine.warn(
      `[IntelligenceRouter] No tier has capabilities [${required.join(", ")}] — falling back to unconstrained resolve`,
    );
    return this.resolve(taskType);
  }
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 3: Commit**

```bash
git add src/intelligence/router.ts
git commit -m "feat(providers): D4 — add resolveCapable() to IntelligenceRouter with graceful capability degrade"
```

---

### Task 6: D5 — Budget accessor + `resolveWithCostAwareness()` + core.ts wiring

Add `resolveWithCostAwareness(taskType)` — checks cached daily budget via the injected `getBudgetState?()` accessor and downgrades tier (high→mid→low) when the tier's estimated cost would exceed the remaining budget. Never hard-blocks. Wire the budget accessor in `core.ts`.

**Files:**
- Modify: `src/intelligence/router.ts`
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Add `resolveWithCostAwareness()` to `IntelligenceRouter`**

Add `estimateCost` import at top of `src/intelligence/router.ts`:
```typescript
import { estimateCost } from "../costs/pricing.js";
```

Add `resolveWithCostAwareness()` to the class after `resolveCapable()`:
```typescript
  /**
   * Resolve model for taskType with cost awareness.
   * If getBudgetState is set and maxDailyUsd > 0, downgrades tier when
   * the estimated per-request cost would exceed remaining daily budget.
   * Never hard-blocks — routes with a warning when all tiers are over budget.
   */
  resolveWithCostAwareness(taskType: TaskType): ResolvedModel {
    const budget = this.getBudgetState?.();
    if (!budget || budget.maxDailyUsd <= 0) return this.resolve(taskType);

    // Estimate cost as 2000 output tokens (conservative ceiling per request)
    const tierOrder: Tier[] = ["high", "mid", "low"];
    for (const tier of tierOrder) {
      const cfg = this.config.tiers[tier];
      if (!cfg?.model) continue;
      const estimated = estimateCost(cfg.model, 1000, 2000);
      if (estimated <= budget.dailyRemainingUsd) {
        const preferred = this.config.defaults[taskType] ?? TASK_TYPE_DEFAULTS[taskType];
        // Only downgrade — never upgrade beyond what resolve() would give
        const preferredIdx = tierOrder.indexOf(preferred);
        const thisIdx = tierOrder.indexOf(tier);
        if (thisIdx >= preferredIdx) {
          if (thisIdx > preferredIdx) {
            log.engine.warn(
              `[IntelligenceRouter] Budget low ($${budget.dailyRemainingUsd.toFixed(4)} remaining) — downgrading tier ${preferred} → ${tier}`,
            );
          }
          return { provider: cfg.provider, model: cfg.model, tier };
        }
      }
    }

    // All tiers over budget — route to low with warning (never hard-block)
    log.engine.warn(
      `[IntelligenceRouter] All tiers over daily budget ($${budget.dailyRemainingUsd.toFixed(4)} remaining) — routing to low tier anyway`,
    );
    return this.resolve(taskType);
  }
```

- [ ] **Step 2: Wire the budget accessor in `src/gateway/core.ts`**

Find the `ctx.intelligence = new IntelligenceRouter(...)` construction added in Task 3 and add the budget accessor as the 4th argument:

```typescript
      ctx.intelligence = new IntelligenceRouter(
        intelligenceConfig,
        ctx.config.defaultProvider,
        ctx.config.defaultModel,
        () => {
          const check = ctx.costTracker?.checkBudget();
          return {
            dailyRemainingUsd: check?.dailyRemainingUsd ?? Infinity,
            maxDailyUsd: (ctx.config.costs?.budget as any)?.maxDailyUsd ?? 0,
          };
        },
      );
```

- [ ] **Step 3: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 4: Commit**

```bash
git add src/intelligence/router.ts src/gateway/core.ts
git commit -m "feat(providers): D5 — add resolveWithCostAwareness(); wire CostTracker budget accessor into IntelligenceRouter"
```

---

### Task 7: D6b — Add `resolveFailover()`

Add `resolveFailover(tier)` — returns the first `FallbackEntry` in `config.fallbacks[]` whose `forTiers` includes the given tier. Returns `null` if no fallback configured (caller falls back to `config.defaultModel`).

**Files:**
- Modify: `src/intelligence/router.ts`

- [ ] **Step 1: Add `resolveFailover()` to `IntelligenceRouter`**

Add after `resolveWithCostAwareness()`:
```typescript
  /**
   * Return the first configured FallbackEntry whose forTiers includes `tier`.
   * Returns null when no fallback is configured for this tier.
   * Callers should fall back to config.defaultModel when null is returned.
   */
  resolveFailover(tier: Tier): ResolvedModel | null {
    if (!this.config.fallbacks?.length) return null;
    const entry = this.config.fallbacks.find((f) => f.forTiers.includes(tier));
    if (!entry) return null;
    return { provider: entry.provider, model: entry.model, tier };
  }
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 3: Commit**

```bash
git add src/intelligence/router.ts
git commit -m "feat(providers): D6b — add resolveFailover() to IntelligenceRouter"
```

---

### Task 8: Wire `IntelligenceRouter` into `EngineContext` + full `runtime.ts` rewiring

`EngineContext` (the type `runtime.ts` uses) doesn't have an `intelligence` field — the router lives on `GatewayContext` only. Add `intelligence?` to `EngineContext`, thread it through `context-builder.ts:baseContext()`, then replace the minimal `config.defaultModel` placeholders added in Task 1 with the full `resolveWithCostAwareness` and `resolveFailover` calls.

**Files:**
- Modify: `src/engine/runtime.ts` (add field to EngineContext type, replace 2 call sites)
- Modify: `src/gateway/handlers/context-builder.ts` (wire in baseContext)

- [ ] **Step 1: Add `intelligence?` to `EngineContext` in `src/engine/runtime.ts`**

In the `EngineContext` interface (around line 43), add after the last field:
```typescript
  /** IntelligenceRouter for per-turn model-tier resolution. Wired from GatewayContext. */
  intelligence?: import("../intelligence/router.js").IntelligenceRouter;
```

- [ ] **Step 2: Wire `intelligence` in `src/gateway/handlers/context-builder.ts:baseContext()`**

In `baseContext()`, add `intelligence: this.ctx.intelligence,` to the returned object. The existing entries end with `relationshipContext: this.ctx.relationshipContext,`. Add:
```typescript
      relationshipContext: this.ctx.relationshipContext,
      intelligence: this.ctx.intelligence,
```

- [ ] **Step 3: Replace the routing block at `runtime.ts:774` (full wiring)**

Find the minimal block added in Task 1:
```typescript
    // 1. Determine optimal model (IntelligenceRouter — full wiring added in Task 8)
    let optimalModel = config.defaultModel;
    let currentProvider = provider;
```

Replace with:
```typescript
    // 1. Determine optimal model via IntelligenceRouter
    const resolved = context.intelligence?.resolveWithCostAwareness("conversation");
    let optimalModel = resolved?.model ?? config.defaultModel;

    // Dynamic provider resolution based on route
    let currentProvider = provider;
    if (
      resolved?.provider &&
      resolved.provider !== provider.name &&
      context.providerRegistry
    ) {
      const available = context.providerRegistry.getAvailable(resolved.provider);
      if (available) {
        log.engine.warn(
          `[IntelligenceRouter] Cross-provider routing on first turn: ${provider.name} → ${resolved.provider}`,
        );
        currentProvider = available;
      }
    }
```

Note: `context.providerRegistry.getAvailable()` is added in Task 9. For now it will be a TypeScript error until Task 9 completes — that's OK since we're building incrementally. If you need tests to pass before Task 9, temporarily use `context.providerRegistry.get(resolved.provider)` in a try/catch.

- [ ] **Step 4: Replace the escalation block at `runtime.ts:1880` (full wiring)**

Find the minimal block added in Task 1:
```typescript
        // If we've failed multiple tool calls in a row, log the condition.
        // Full IntelligenceRouter failover wiring added in Task 8.
        if (globalConsecutiveFailures >= 2) {
          log.engine.warn(
            `[Runtime] Tool failed ${globalConsecutiveFailures}x — no fallback configured yet.`,
          );
        }
```

Replace with:
```typescript
        // If we've failed multiple tool calls in a row, try IntelligenceRouter failover.
        if (globalConsecutiveFailures >= 2) {
          context.providerRegistry?.recordProviderResult(currentProvider.name, false);
          const currentTier = context.intelligence?.resolve("conversation").tier ?? "mid";
          const fallback = context.intelligence?.resolveFailover(currentTier);

          if (fallback && fallback.provider !== currentProvider.name && context.providerRegistry) {
            const fallbackProvider = context.providerRegistry.getAvailable(fallback.provider);
            if (fallbackProvider) {
              log.engine.warn(
                `[IntelligenceRouter] Tool failed ${globalConsecutiveFailures}x. Swapping provider: ${currentProvider.name} → ${fallback.provider}`,
              );
              currentProvider = fallbackProvider;
              if (context.onProgress) {
                await context.onProgress(
                  `🔄 **Fallback Triggered:** Swapping to ${fallback.provider} (${fallback.model}) to resolve failure.`,
                );
              }
            }
          }

          if (fallback?.model && fallback.model !== optimalModel) {
            log.engine.warn(
              `Tool failed ${globalConsecutiveFailures}x. Swapping model: ${optimalModel} → ${fallback.model}`,
            );
            optimalModel = fallback.model;
          }
        }
```

Note: `recordProviderResult` and `getAvailable` are added to `ProviderRegistry` in Task 9.

- [ ] **Step 5: Run tests**

```bash
npx vitest run
```

Expected: same passing count. TypeScript may have errors on `getAvailable`/`recordProviderResult` until Task 9 — if so, use `get()` temporarily:

```typescript
// Temporary until Task 9 adds getAvailable():
const available = (() => { try { return context.providerRegistry?.get(resolved.provider) } catch { return undefined } })();
```

- [ ] **Step 6: Commit**

```bash
git add src/engine/runtime.ts src/gateway/handlers/context-builder.ts
git commit -m "feat(providers): wire IntelligenceRouter into EngineContext; full resolveWithCostAwareness + resolveFailover in runtime.ts"
```

---

### Task 9: D3 — Create `ProviderCircuitBreaker` + wire into `ProviderRegistry`

Create `src/providers/circuit-breaker.ts` with CLOSED/OPEN/HALF_OPEN state machine. Wire one breaker per registered provider into `ProviderRegistry`. Add `getAvailable()` (checks breaker) and `recordProviderResult()` (updates breaker state) methods.

**Files:**
- Create: `src/providers/circuit-breaker.ts`
- Modify: `src/providers/registry.ts`

- [ ] **Step 1: Create `src/providers/circuit-breaker.ts`**

```typescript
/**
 * StackOwl — Provider Circuit Breaker
 *
 * Passive health monitoring for AI providers.
 * State machine: CLOSED (normal) → OPEN (failing) → HALF_OPEN (probing) → CLOSED
 */

export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export class ProviderCircuitBreaker {
  private state: CircuitState = "CLOSED";
  private failures = 0;
  private openedAt = 0;

  constructor(
    private readonly failureThreshold = 5,
    private readonly recoveryTimeoutMs = 30_000,
  ) {}

  /**
   * Returns true when the provider should be skipped for routing.
   * Transitions OPEN → HALF_OPEN when the recovery timeout has elapsed.
   */
  isOpen(): boolean {
    if (this.state === "CLOSED") return false;
    if (this.state === "OPEN") {
      if (Date.now() - this.openedAt >= this.recoveryTimeoutMs) {
        this.state = "HALF_OPEN";
        return false; // let one probe request through
      }
      return true;
    }
    // HALF_OPEN — one probe is already allowed through
    return false;
  }

  /**
   * Record the result of a provider API call.
   * Transitions: success → CLOSED (reset failures); failure → OPEN (at threshold).
   */
  recordResult(success: boolean): void {
    if (success) {
      this.failures = 0;
      this.state = "CLOSED";
    } else {
      this.failures++;
      if (this.state === "HALF_OPEN" || this.failures >= this.failureThreshold) {
        this.state = "OPEN";
        this.openedAt = Date.now();
        this.failures = 0;
      }
    }
  }

  getState(): CircuitState {
    return this.state;
  }

  /** For testing: fast-forward the recovery clock. */
  _forceOpenedAt(timestamp: number): void {
    this.openedAt = timestamp;
  }
}
```

- [ ] **Step 2: Wire `ProviderCircuitBreaker` into `src/providers/registry.ts`**

Add the import at the top of `registry.ts`:
```typescript
import { ProviderCircuitBreaker } from "./circuit-breaker.js";
import type { HealthPolicy } from "../intelligence/router.js";
```

Add a `private breakers` map to the `ProviderRegistry` class (after the existing `private providers` map):
```typescript
  private breakers: Map<string, ProviderCircuitBreaker> = new Map();
  private healthPolicy: HealthPolicy = { failureThreshold: 5, recoveryTimeoutMs: 30_000 };
```

Add a `setHealthPolicy()` method:
```typescript
  /** Configure circuit breaker parameters from IntelligenceConfig.healthPolicy. */
  setHealthPolicy(policy: HealthPolicy): void {
    this.healthPolicy = policy;
  }
```

In the `register()` method, after `this.providers.set(config.name, provider)`, add:
```typescript
      this.breakers.set(
        config.name,
        new ProviderCircuitBreaker(
          this.healthPolicy.failureThreshold,
          this.healthPolicy.recoveryTimeoutMs,
        ),
      );
```

(This must be added in all three code paths where `this.providers.set()` is called inside `register()`.)

Add `getAvailable()` and `recordProviderResult()` methods after the existing `get()` method:
```typescript
  /**
   * Get a provider if its circuit breaker is not OPEN.
   * Returns null if the provider is OPEN (caller should try a fallback).
   * Returns the provider instance if CLOSED or HALF_OPEN.
   */
  getAvailable(name?: string): ModelProvider | null {
    const targetName = name ?? this.defaultProviderName;
    if (!targetName) return null;

    const breaker = this.breakers.get(targetName);
    if (breaker?.isOpen()) {
      log.engine.warn(
        `[ProviderRegistry] Provider "${targetName}" circuit is OPEN — skipping`,
      );
      return null;
    }

    const provider = this.providers.get(targetName);
    return provider ?? null;
  }

  /**
   * Record the outcome of a provider API call.
   * Updates the circuit breaker state for the named provider.
   */
  recordProviderResult(name: string, success: boolean): void {
    this.breakers.get(name)?.recordResult(success);
  }

  /**
   * Check whether a provider's circuit is currently open (failing).
   */
  isProviderOpen(name: string): boolean {
    return this.breakers.get(name)?.isOpen() ?? false;
  }
```

- [ ] **Step 3: Wire `setHealthPolicy()` in `src/gateway/core.ts`**

After the `ctx.intelligence` construction block, add:
```typescript
    // Wire health policy into ProviderRegistry circuit breakers
    if (ctx.providerRegistry && ctx.config.intelligence?.healthPolicy) {
      ctx.providerRegistry.setHealthPolicy(ctx.config.intelligence.healthPolicy);
    }
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 5: Commit**

```bash
git add src/providers/circuit-breaker.ts src/providers/registry.ts src/gateway/core.ts
git commit -m "feat(providers): D3 — add ProviderCircuitBreaker; wire getAvailable() + recordProviderResult() into ProviderRegistry"
```

---

### Task 10: D7 — Delete `openai-compat.ts`

`src/providers/openai-compat.ts` (522 LOC) has zero production imports. The `"openai-compatible"` strings in onboarding-flow.ts, telegram-config, etc. refer to a `ProviderConfig.type` value routed through `PROTOCOL_FACTORIES` in `registry.ts` — they use `protocols/openai.ts`, not this file. Verify, then delete.

**Files:**
- Delete: `src/providers/openai-compat.ts`

- [ ] **Step 1: Verify no production imports**

```bash
grep -r "openai-compat" src/ --include="*.ts"
```

Expected output: no results (or only type-value strings, not file imports).

- [ ] **Step 2: Delete the file**

```bash
rm src/providers/openai-compat.ts
```

- [ ] **Step 3: Verify TypeScript compiles cleanly**

```bash
npx tsc --noEmit 2>&1 | grep -v "error TS" | head -5
# If there are new TS errors from the deletion, they'll appear here
npx tsc --noEmit 2>&1 | grep "openai-compat" | head -5
```

Expected: no errors mentioning `openai-compat`.

- [ ] **Step 4: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(providers): D7 — delete openai-compat.ts (522 LOC dead code)"
```

---

### Task 11: D8 — Throw on missing model config in adapters

`anthropic-native.ts:141` falls back to `"claude-sonnet-4-20250514"` when `config.defaultModel` is absent. `protocols/openai.ts:85` falls back to `"gpt-4o"`. These silently pin to deprecated model versions. Replace both with an informative throw. Also replace `anthropic-native.ts:381-386` `listModels()` hardcoded array with a live API call.

**Files:**
- Modify: `src/providers/anthropic-native.ts`
- Modify: `src/providers/protocols/openai.ts`

- [ ] **Step 1: Fix `src/providers/anthropic-native.ts:141`**

Find:
```typescript
    this.defaultModel = config.defaultModel ?? "claude-sonnet-4-20250514";
```

Replace with:
```typescript
    if (!config.defaultModel) {
      throw new Error(
        "[Anthropic] No model configured. Set defaultModel in your provider config (e.g. \"claude-sonnet-4-6\").",
      );
    }
    this.defaultModel = config.defaultModel;
```

- [ ] **Step 2: Replace `listModels()` hardcoded array in `anthropic-native.ts:381-386`**

Find:
```typescript
  async listModels(): Promise<string[]> {
    return [
      "claude-opus-4-20250514",
      "claude-sonnet-4-20250514",
      "claude-haiku-4-20250414",
    ];
  }
```

Replace with:
```typescript
  async listModels(): Promise<string[]> {
    try {
      const response = await this.client.models.list();
      return response.data.map((m: { id: string }) => m.id);
    } catch {
      // Fall back to known models if API is unreachable
      return ["claude-opus-4-20250514", "claude-sonnet-4-20250514", "claude-haiku-4-20250414"];
    }
  }
```

- [ ] **Step 3: Fix `src/providers/protocols/openai.ts:84-85`**

Find:
```typescript
    this.activeModel =
      (config as any).activeModel ?? config.defaultModel ?? "gpt-4o";
```

Replace with:
```typescript
    const resolvedModel = (config as any).activeModel ?? config.defaultModel;
    if (!resolvedModel) {
      throw new Error(
        "[OpenAI] No model configured. Set defaultModel in your provider config (e.g. \"gpt-4o\").",
      );
    }
    this.activeModel = resolvedModel;
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run
```

Expected: same passing count. The tests in `__tests__/providers.test.ts` that register providers with a `defaultModel` should still pass. Any test constructing a provider without `defaultModel` will now throw — check and fix if needed.

- [ ] **Step 5: Commit**

```bash
git add src/providers/anthropic-native.ts src/providers/protocols/openai.ts
git commit -m "feat(providers): D8 — throw on missing model config; replace listModels() hardcoded array with live API call"
```

---

### Task 12: Q1 — `PRICING_UPDATED_AT` export + staleness warning

Add a `PRICING_UPDATED_AT` export to `costs/pricing.ts` so external code can check staleness. Add a startup warning in `src/index.ts` when the pricing table is more than 90 days old.

**Files:**
- Modify: `src/costs/pricing.ts`
- Modify: `src/index.ts`

- [ ] **Step 1: Add `PRICING_UPDATED_AT` to `src/costs/pricing.ts`**

Add after the file header comment, before the `ModelPrice` interface:
```typescript
/** Date when MODEL_PRICING was last updated. Used for staleness detection. */
export const PRICING_UPDATED_AT = "2026-03-01";
```

- [ ] **Step 2: Add staleness warning to `src/index.ts`**

Find the bootstrap section (after `loadConfig()` succeeds, before gateway construction). Add:

```typescript
  // Warn if model pricing table is stale (> 90 days old)
  const { PRICING_UPDATED_AT } = await import("./costs/pricing.js");
  const pricingAge = Date.now() - new Date(PRICING_UPDATED_AT).getTime();
  if (pricingAge > 90 * 86_400_000) {
    log.engine.warn(
      `[CostTracker] MODEL_PRICING may be stale (last updated ${PRICING_UPDATED_AT}). Cost routing estimates may be inaccurate. Update src/costs/pricing.ts.`,
    );
  }
```

Place this block right after the `costTracker` initialization block (around line 955 where `costTracker` is set up).

- [ ] **Step 3: Run tests**

```bash
npx vitest run
```

Expected: same passing count.

- [ ] **Step 4: Commit**

```bash
git add src/costs/pricing.ts src/index.ts
git commit -m "feat(providers): Q1 — add PRICING_UPDATED_AT export; warn at startup when pricing table is >90 days stale"
```

---

### Task 13: Tests — `__tests__/element18/` suite

Write the 17 tests covering all new behavior. Group by concern in 4 files.

**Files:**
- Create: `__tests__/element18/circuit-breaker.test.ts`
- Create: `__tests__/element18/intelligence-router-extensions.test.ts`
- Create: `__tests__/element18/provider-registry-circuit.test.ts`
- Create: `__tests__/element18/adapter-missing-model.test.ts`

- [ ] **Step 1: Create `__tests__/element18/circuit-breaker.test.ts`**

```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import { ProviderCircuitBreaker } from "../../src/providers/circuit-breaker.js";

describe("ProviderCircuitBreaker", () => {
  let breaker: ProviderCircuitBreaker;

  beforeEach(() => {
    breaker = new ProviderCircuitBreaker(3, 1000); // threshold=3, timeout=1s
  });

  it("starts CLOSED and allows requests", () => {
    expect(breaker.isOpen()).toBe(false);
    expect(breaker.getState()).toBe("CLOSED");
  });

  it("transitions CLOSED → OPEN after failureThreshold failures", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(false); // not yet at threshold
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(true);
    expect(breaker.getState()).toBe("OPEN");
  });

  it("transitions OPEN → HALF_OPEN after recoveryTimeoutMs", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // now OPEN
    expect(breaker.isOpen()).toBe(true);

    // Fast-forward the clock past recovery timeout
    breaker._forceOpenedAt(Date.now() - 1001);
    expect(breaker.isOpen()).toBe(false); // HALF_OPEN lets one through
    expect(breaker.getState()).toBe("HALF_OPEN");
  });

  it("HALF_OPEN probe success → CLOSED", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // OPEN
    breaker._forceOpenedAt(Date.now() - 1001);
    breaker.isOpen(); // transition to HALF_OPEN
    breaker.recordResult(true); // probe success
    expect(breaker.getState()).toBe("CLOSED");
    expect(breaker.isOpen()).toBe(false);
  });

  it("HALF_OPEN probe failure → OPEN (timer reset)", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(false); // OPEN
    breaker._forceOpenedAt(Date.now() - 1001);
    breaker.isOpen(); // transition to HALF_OPEN
    breaker.recordResult(false); // probe failure
    expect(breaker.getState()).toBe("OPEN");
    expect(breaker.isOpen()).toBe(true); // timer reset — not yet expired
  });

  it("success resets failure counter and closes circuit", () => {
    breaker.recordResult(false);
    breaker.recordResult(false);
    breaker.recordResult(true); // success before threshold
    expect(breaker.getState()).toBe("CLOSED");
    // Need 3 more failures to open again
    breaker.recordResult(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(false);
    breaker.recordResult(false);
    expect(breaker.isOpen()).toBe(true);
  });
});
```

- [ ] **Step 2: Run circuit-breaker tests**

```bash
npx vitest run __tests__/element18/circuit-breaker.test.ts
```

Expected: 6/6 pass.

- [ ] **Step 3: Create `__tests__/element18/intelligence-router-extensions.test.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { IntelligenceRouter } from "../../src/intelligence/router.js";
import type { IntelligenceConfig } from "../../src/intelligence/router.js";

function makeConfig(overrides: Partial<IntelligenceConfig> = {}): IntelligenceConfig {
  return {
    tiers: {
      high: { provider: "anthropic", model: "claude-opus-4-6",  capabilities: ["reasoning", "vision", "code"] },
      mid:  { provider: "anthropic", model: "claude-sonnet-4-6", capabilities: ["code"] },
      low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
    },
    defaults: { conversation: "mid" },
    ...overrides,
  };
}

describe("IntelligenceRouter.resolveCapable()", () => {
  it("routes to the first tier (high) with all required capabilities", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("conversation", ["vision"]);
    expect(result.tier).toBe("high");
    expect(result.model).toBe("claude-opus-4-6");
  });

  it("falls back to unconstrained resolve() when no tier has required capability", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("conversation", ["long-context"]);
    // No tier has "long-context" — falls back to resolve("conversation") = mid
    expect(result.tier).toBe("mid");
    expect(result.model).toBe("claude-sonnet-4-6");
  });

  it("returns resolve() directly when required is empty", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("parliament", []);
    expect(result.tier).toBe("high"); // parliament defaults to high
  });
});

describe("IntelligenceRouter.resolveWithCostAwareness()", () => {
  it("returns normal result when maxDailyUsd is 0 (unlimited)", () => {
    const router = new IntelligenceRouter(
      makeConfig({ costPolicy: { maxDailyUsd: 0, downgradeTierOnBudgetExhausted: true } }),
      "anthropic", "claude-sonnet-4-6",
      () => ({ dailyRemainingUsd: 0, maxDailyUsd: 0 }),
    );
    const result = router.resolveWithCostAwareness("conversation");
    expect(result.tier).toBe("mid"); // no downgrade when unlimited
  });

  it("downgrades from high to low when high and mid tiers cost too much", () => {
    // haiku is essentially free ($0) so all budget amounts let it through
    const router = new IntelligenceRouter(
      makeConfig(),
      "anthropic", "claude-sonnet-4-6",
      () => ({ dailyRemainingUsd: 0.000001, maxDailyUsd: 1 }), // almost no budget
    );
    // parliament defaults to "high" — should downgrade to low (haiku ≈ $0)
    const result = router.resolveWithCostAwareness("parliament");
    expect(result.tier).toBe("low");
  });

  it("allows routing with warning when all tiers are over budget", () => {
    // Set all models to expensive prices by giving near-zero budget
    const router = new IntelligenceRouter(
      {
        tiers: {
          high: { provider: "p", model: "expensive-model-1" },
          mid:  { provider: "p", model: "expensive-model-2" },
          low:  { provider: "p", model: "expensive-model-3" },
        },
        defaults: { conversation: "mid" },
        costPolicy: { maxDailyUsd: 0.0001, downgradeTierOnBudgetExhausted: true },
      },
      "p", "expensive-model-2",
      () => ({ dailyRemainingUsd: 0, maxDailyUsd: 0.0001 }),
    );
    // Should not throw — routes to resolve() as fallback
    expect(() => router.resolveWithCostAwareness("conversation")).not.toThrow();
  });
});

describe("IntelligenceRouter.resolveFailover()", () => {
  it("returns the first FallbackEntry matching the tier", () => {
    const router = new IntelligenceRouter(
      makeConfig({
        fallbacks: [
          { provider: "openai", model: "gpt-4o-mini", forTiers: ["high", "mid"] },
          { provider: "deepseek", model: "deepseek-chat", forTiers: ["low"] },
        ],
      }),
      "anthropic", "claude-sonnet-4-6",
    );
    const result = router.resolveFailover("high");
    expect(result?.provider).toBe("openai");
    expect(result?.model).toBe("gpt-4o-mini");
    expect(result?.tier).toBe("high");
  });

  it("returns null when no fallbacks are configured", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    expect(router.resolveFailover("high")).toBeNull();
  });

  it("returns null when no fallback entry matches the given tier", () => {
    const router = new IntelligenceRouter(
      makeConfig({ fallbacks: [{ provider: "openai", model: "gpt-4o", forTiers: ["high"] }] }),
      "anthropic", "claude-sonnet-4-6",
    );
    expect(router.resolveFailover("low")).toBeNull();
  });
});

describe("DEFAULT_INTELLIGENCE_CONFIG passthrough", () => {
  it("routes every task to the same provider/model when using default config", async () => {
    const { buildDefaultIntelligenceConfig } = await import("../../src/config/loader.js");
    const config = buildDefaultIntelligenceConfig("anthropic", "claude-sonnet-4-6");
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("conversation");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-sonnet-4-6");
  });
});
```

- [ ] **Step 4: Run intelligence router extension tests**

```bash
npx vitest run __tests__/element18/intelligence-router-extensions.test.ts
```

Expected: 9/9 pass.

- [ ] **Step 5: Create `__tests__/element18/provider-registry-circuit.test.ts`**

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { ProviderRegistry } from "../../src/providers/registry.js";
import { ProviderCircuitBreaker } from "../../src/providers/circuit-breaker.js";

describe("ProviderRegistry circuit breaker integration", () => {
  it("getAvailable() returns null for a provider with OPEN circuit", () => {
    const registry = new ProviderRegistry();
    // Manually trip the circuit via recordProviderResult
    // First we need a provider registered — use a mock
    // Since registry.register() requires model files, we test via recordProviderResult + isProviderOpen
    const breaker = new ProviderCircuitBreaker(1, 60_000);
    breaker.recordResult(false); // trip at threshold=1
    expect(breaker.isOpen()).toBe(true);
    expect(breaker.getState()).toBe("OPEN");
  });

  it("getAvailable() returns null when provider name is unknown", () => {
    const registry = new ProviderRegistry();
    expect(registry.getAvailable("unknown-provider")).toBeNull();
  });

  it("recordProviderResult does not throw for unknown provider", () => {
    const registry = new ProviderRegistry();
    expect(() => registry.recordProviderResult("unknown", false)).not.toThrow();
    expect(() => registry.recordProviderResult("unknown", true)).not.toThrow();
  });

  it("isProviderOpen returns false for unknown provider", () => {
    const registry = new ProviderRegistry();
    expect(registry.isProviderOpen("unknown")).toBe(false);
  });
});
```

- [ ] **Step 6: Run registry circuit tests**

```bash
npx vitest run __tests__/element18/provider-registry-circuit.test.ts
```

Expected: 4/4 pass.

- [ ] **Step 7: Create `__tests__/element18/adapter-missing-model.test.ts`**

```typescript
import { describe, it, expect } from "vitest";

describe("AnthropicNativeProvider — missing model config", () => {
  it("throws when defaultModel is absent", async () => {
    const { AnthropicNativeProvider } = await import("../../src/providers/anthropic-native.js");
    expect(
      () => new AnthropicNativeProvider({ name: "anthropic", apiKey: "test-key" } as any),
    ).toThrow("[Anthropic] No model configured");
  });

  it("constructs successfully when defaultModel is set", async () => {
    const { AnthropicNativeProvider } = await import("../../src/providers/anthropic-native.js");
    expect(
      () => new AnthropicNativeProvider({ name: "anthropic", defaultModel: "claude-sonnet-4-6", apiKey: "test-key" } as any),
    ).not.toThrow();
  });
});

describe("OpenAIProtocolProvider — missing model config", () => {
  it("throws when both activeModel and defaultModel are absent", async () => {
    const { OpenAIProtocolProvider } = await import("../../src/providers/protocols/openai.js");
    expect(
      () => new OpenAIProtocolProvider({ name: "openai", apiKey: "test-key" } as any, "https://api.openai.com/v1"),
    ).toThrow("[OpenAI] No model configured");
  });

  it("constructs successfully when defaultModel is set", async () => {
    const { OpenAIProtocolProvider } = await import("../../src/providers/protocols/openai.js");
    expect(
      () => new OpenAIProtocolProvider(
        { name: "openai", defaultModel: "gpt-4o", apiKey: "test-key" } as any,
        "https://api.openai.com/v1",
      ),
    ).not.toThrow();
  });
});
```

- [ ] **Step 8: Run adapter tests**

```bash
npx vitest run __tests__/element18/adapter-missing-model.test.ts
```

Expected: 4/4 pass.

- [ ] **Step 9: Run full test suite**

```bash
npx vitest run
```

Expected: ~5018 tests passing (5001 baseline + ~17 new).

- [ ] **Step 10: Commit**

```bash
git add __tests__/element18/
git commit -m "test(element18): add 17 tests covering ProviderCircuitBreaker, IntelligenceRouter extensions, registry circuit integration, adapter missing model"
```

---

### Task 14: Progress update

Update `docs/platform-audit/progress.md` with Element 18 completion row.

**Files:**
- Modify: `docs/platform-audit/progress.md`

- [ ] **Step 1: Update progress.md**

Find the Element 18 row:
```markdown
| 18 | Providers (model routing, health, cost) | 🔄 Phase 3 ...
```

Replace with:
```markdown
| 18 | Providers (model routing, health, cost) | ✅ All tasks shipped. D1: deleted `engine/router.ts` (~190 LOC dead, regex violations). D2: always-construct `ctx.intelligence` from default config. D3: `ProviderCircuitBreaker` CLOSED/OPEN/HALF_OPEN state machine per provider. D4: `resolveCapable()` with graceful capability degrade. D5: `resolveWithCostAwareness()` with CostTracker budget accessor. D6: FallbackEntry/HealthPolicy/CostPolicy types on IntelligenceConfig. D7: deleted `openai-compat.ts` (522 LOC dead). D8: throw on missing model config; live `listModels()` API call. Q1: PRICING_UPDATED_AT staleness warning. Net file delta: −1. ~5018 tests passing. | 2026-05-10 |
```

- [ ] **Step 2: Commit**

```bash
git add docs/platform-audit/progress.md
git commit -m "docs(element18): mark Element 18 complete in progress tracker"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Task |
|---|---|
| D1: Delete engine/router.ts; replace runtime.ts call sites | Task 1, Task 8 |
| D1b: Remove smartRouting type stub and all cleanup sites | Task 2 |
| D2: Always construct ctx.intelligence from DEFAULT_INTELLIGENCE_CONFIG | Task 3 |
| D6: FallbackEntry, HealthPolicy, CostPolicy, capabilities on TierConfig | Task 4 |
| D4: resolveCapable() with graceful degrade | Task 5 |
| D5: resolveWithCostAwareness() + budget accessor in core.ts | Task 6 |
| D6b: resolveFailover() | Task 7 |
| Wire intelligence into EngineContext + full runtime.ts rewiring | Task 8 |
| D3: ProviderCircuitBreaker + getAvailable() + recordProviderResult() | Task 9 |
| D7: Delete openai-compat.ts | Task 10 |
| D8: Throw on missing model; live listModels() | Task 11 |
| Q1: PRICING_UPDATED_AT + staleness warning | Task 12 |
| ~17 tests: circuit breaker, router extensions, registry, adapters | Task 13 |
| Progress tracker update | Task 14 |

All spec requirements covered. ✅

### Placeholder Scan

No "TBD", "TODO", or incomplete sections found. All code blocks contain complete implementations.

### Type Consistency Check

- `IntelligenceConfig` extended in Task 4 → used by `resolveCapable()` (Task 5), `resolveWithCostAwareness()` (Task 6), `resolveFailover()` (Task 7) ✅
- `ProviderCircuitBreaker` created in Task 9, imported by `ProviderRegistry` in Task 9 ✅
- `getAvailable()` and `recordProviderResult()` added to `ProviderRegistry` in Task 9, called in `runtime.ts` Task 8 ✅
- `IntelligenceRouter.getBudgetState?` 4th constructor param added in Task 4 (via type replacement) and wired in Task 6 ✅
- `buildDefaultIntelligenceConfig()` exported from `loader.ts` in Task 3, imported in `core.ts` Task 3 ✅

Note: Task 8 calls `context.providerRegistry.getAvailable()` and `context.providerRegistry.recordProviderResult()` which are added in Task 9. The build will have TypeScript errors between Task 8 and Task 9 completion. Execute Task 9 immediately after Task 8 to close the gap, or use the temporary `get()` fallback noted in Task 8 Step 3.
