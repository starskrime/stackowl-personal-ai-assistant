# Element 18 — Providers Layer Design Spec

> **Approved:** 2026-05-10. Architecture review: `_bmad-output/planning-artifacts/element18-providers-architecture-review-2026-05-10.md`

**Goal:** Repair the providers layer by deleting inert code, wiring cost and health signals into routing, and making `IntelligenceRouter` the single authoritative path for all model selection.

**Architecture:** Delete `engine/router.ts` (inert + keyword-regex violations) and `openai-compat.ts` (dead code). Add `ProviderCircuitBreaker` per provider. Extend `IntelligenceRouter` with cost-aware routing, capability filtering, and fallback resolution. Always construct `ctx.intelligence` from a default config — never null.

**Tech Stack:** TypeScript (strict), Vitest, existing `IntelligenceRouter`/`CostTracker`/`ProviderRegistry` primitives.

---

## 1. Architecture Overview

### Current state (the problems)

- `ModelRouter` (`engine/router.ts`) is called every turn from `runtime.ts:775` and `runtime.ts:1882` but always returns `config.defaultModel`. The complexity-scoring regex arrays (`HEAVY_PATTERNS`, `SIMPLE_PATTERNS`) run but their output is discarded. They violate the no-hardcoded-keywords rule.
- `IntelligenceRouter` exists but `gateway/types.ts:346` has `intelligence?: IntelligenceRouter` — optional. A gateway constructed without `config.intelligence` bypasses all routing logic silently.
- `ProviderRegistry.healthCheckAll()` at `registry.ts:189–195` fires once at startup and never again. A provider that fails post-startup is not detected until a live request fails.
- `CostTracker` accumulates spend at `gateway/core.ts:466` but nothing reads it for routing decisions.
- `openai-compat.ts` (522 LOC) has zero production imports.

### Target state

- `engine/router.ts` deleted. `IntelligenceRouter` takes over both call sites in `runtime.ts`.
- `ctx.intelligence` always constructed from `DEFAULT_INTELLIGENCE_CONFIG` if the user config omits `intelligence` — same behavior as today, but never null.
- New `ProviderCircuitBreaker` per registered provider. Passive monitoring (no synthetic probes). CLOSED/OPEN/HALF-OPEN state machine.
- `resolveWithCostAwareness()` checks cached daily budget; downgrades tier if needed.
- `resolveCapable()` routes to capability-tagged models (vision, code, etc.) when the request requires it.
- `openai-compat.ts` and `engine/router.ts` deleted → net −1 file, −446 LOC.

### Scope boundary

Model-tier selection and provider health only. Out of scope: channel routing (`OwlBrain`), conversation-level owl selection, ReAct loop structure, API key storage/rotation. Provider-aware skill cost guards are deferred to post-E18.

---

## 2. Components

### 2a. `ProviderCircuitBreaker` — new file `src/providers/circuit-breaker.ts`

Three states:
- **CLOSED** — normal; all requests pass through
- **OPEN** — `failureThreshold` consecutive failures; `isOpen()` returns `true` until `recoveryTimeoutMs` elapses
- **HALF_OPEN** — timeout elapsed; next single request is a probe: success → CLOSED, failure → OPEN with timer reset

```typescript
export type CircuitState = 'CLOSED' | 'OPEN' | 'HALF_OPEN'

export class ProviderCircuitBreaker {
  private state: CircuitState = 'CLOSED'
  private failures = 0
  private openedAt = 0

  constructor(
    private readonly failureThreshold = 5,
    private readonly recoveryTimeoutMs = 30_000,
  ) {}

  /** Returns true when the provider should be skipped for routing. */
  isOpen(): boolean {
    if (this.state === 'CLOSED') return false
    if (this.state === 'OPEN') {
      if (Date.now() - this.openedAt >= this.recoveryTimeoutMs) {
        this.state = 'HALF_OPEN'
        return false  // let one probe through
      }
      return true
    }
    // HALF_OPEN: one probe already allowed
    return false
  }

  /** Call after every API response. */
  recordResult(success: boolean): void {
    if (success) {
      this.failures = 0
      this.state = 'CLOSED'
    } else {
      this.failures++
      if (this.state === 'HALF_OPEN' || this.failures >= this.failureThreshold) {
        this.state = 'OPEN'
        this.openedAt = Date.now()
        this.failures = 0
      }
    }
  }

  getState(): CircuitState { return this.state }
}
```

`ProviderRegistry` creates one `ProviderCircuitBreaker` per registered provider (using `healthPolicy` from `IntelligenceConfig` if present, else defaults). `ProviderRegistry.getAvailable(name)` returns `null` if `isOpen()` is true. `recordResult()` is called by the Gateway after every provider API response.

### 2b. `IntelligenceRouter` extensions — modify `src/intelligence/router.ts`

Three new methods added to the existing class:

```typescript
/**
 * Routes to the highest-priority tier whose capabilities[] contains ALL required tags.
 * Falls back to resolve(taskType) with a warning if no capable tier exists.
 */
resolveCapable(taskType: TaskType, required: string[]): ResolvedModel

/**
 * Checks cached budget state; downgrades tier if estimated cost exceeds daily limit.
 * Reads from getBudgetState?() injected at construction. Allows routing with warn
 * when all tiers are over budget (never hard-blocks).
 */
resolveWithCostAwareness(taskType: TaskType): ResolvedModel

/**
 * Returns the first FallbackEntry in config.fallbacks[] whose forTiers includes `tier`.
 * Returns null if no fallback is configured — caller falls back to config.defaultModel.
 */
resolveFailover(tier: Tier): ResolvedModel | null
```

Constructor gains an optional second parameter:

```typescript
constructor(
  private config: IntelligenceConfig,
  private getBudgetState?: () => { dailyRemainingUsd: number; maxDailyUsd: number },
) {}
```

### 2c. `IntelligenceConfig` type extensions — in `src/intelligence/router.ts`

All new fields are optional — zero breaking change for existing configs.

```typescript
export interface FallbackEntry {
  provider: string
  model: string
  forTiers: Tier[]  // which failure tiers this fallback covers
}

export interface HealthPolicy {
  failureThreshold: number   // default 5
  recoveryTimeoutMs: number  // default 30_000
}

export interface CostPolicy {
  maxDailyUsd: number                       // default 0 = unlimited
  downgradeTierOnBudgetExhausted: boolean   // default true
}

export interface TierConfig {
  provider: string
  model: string
  capabilities?: string[]  // NEW: e.g. ["vision", "code", "reasoning"]
}

export interface IntelligenceConfig {
  tiers: Record<Tier, TierConfig>
  defaults: Partial<Record<TaskType, Tier>>
  overrides?: Partial<Record<TaskType, Partial<TierConfig>>>
  fallbacks?: FallbackEntry[]    // NEW
  healthPolicy?: HealthPolicy    // NEW
  costPolicy?: CostPolicy        // NEW
}
```

**Capability tag vocabulary** (locked, not extensible via config at this scope):
`vision`, `code`, `reasoning`, `long-context`, `tool-use`, `fast`, `structured-output`

### 2d. `DEFAULT_INTELLIGENCE_CONFIG` — in `src/config/loader.ts`

```typescript
export const DEFAULT_INTELLIGENCE_CONFIG: IntelligenceConfig = {
  tiers: {
    high:  { provider: '', model: '' },   // filled at runtime from config.defaultProvider/defaultModel
    mid:   { provider: '', model: '' },
    low:   { provider: '', model: '' },
  },
  defaults: {
    conversation: 'mid',
    synthesis:    'high',
    extraction:   'low',
    classification: 'low',
    summarization: 'low',
    parliament:   'high',
    evolution:    'mid',
    episodic:     'low',
    clarification: 'low',
  },
}
```

At Gateway init (`core.ts:432–438`), if `config.intelligence` is absent, `DEFAULT_INTELLIGENCE_CONFIG` tiers are populated with the owl's `providerName`/`defaultModel` — identical routing behavior to today.

### 2e. Deletions

| File | LOC | Reason |
|---|---|---|
| `src/engine/router.ts` | ~190 | Active but inert; `HEAVY_PATTERNS`/`SIMPLE_PATTERNS` violate no-keywords rule |
| `src/providers/openai-compat.ts` | 522 | Dead code — zero production imports confirmed by grep |
| `StackOwlConfig.smartRouting` type stub in `config/loader.ts:103–113` | ~11 | `@deprecated`; loader already throws on it at runtime |

---

## 3. Data Flow

### Per-request routing (happy path)

```
Gateway.handleMessage()
  → OwlBrain.selectOwl()                          [unchanged — which owl answers]
  → ctx.intelligence.resolveWithCostAwareness("conversation")
       reads lastBudgetCheck (cached from previous turn, sync — Q2)
       if maxDailyUsd=0 (unlimited): resolve(taskType) directly
       if budget OK: resolve(taskType) → {provider, model, tier}
       if budget low: downgrade tier (high→mid→low) → {provider, model, tier}
  → ProviderRegistry.getAvailable(resolvedModel.provider)
       CircuitBreaker.isOpen()?
       if OPEN: resolveFailover(tier) → alternative {provider, model}
       if no fallback: warn + use config.defaultModel
  → Provider.chat(messages, resolvedModel.model)
  → CircuitBreaker.recordResult(success|failure)
  → CostTracker.record(usage)                     [existing, unchanged]
  → Gateway.lastBudgetCheck = costTracker.checkBudget("system")  [new cache refresh]
```

### Vision/capability routing

```
Gateway detects image attachment in message
  → ctx.intelligence.resolveCapable("conversation", ["vision"])
       filter tiers by capabilities.includes("vision")
       if match: route to vision-capable tier
       if no match: log warn + resolve("conversation") unconstrained
```

### Tool-failure escalation (replaces `runtime.ts:1882` ModelRouter call)

```
ReAct loop detects consecutive tool failures
  → ctx.intelligence.resolveFailover(currentTier)
       if FallbackEntry found: switch to {provider, model}
       if null: keep current model (existing behavior)
```

### Gateway initialization

```
loadConfig() in config/loader.ts
  if config.intelligence present: use as-is
  if absent: DEFAULT_INTELLIGENCE_CONFIG with owl's provider+model filled in
  
Gateway constructor:
  ctx.intelligence = new IntelligenceRouter(
    resolvedIntelligenceConfig,
    () => ctx.costTracker?.checkBudget("system") ?? { dailyRemainingUsd: Infinity, maxDailyUsd: 0 }
  )
  // ctx.intelligence is now always defined — D2
```

---

## 4. Error Handling

**Missing model config (D8):**
- `anthropic-native.ts:141` — if `config.defaultModel` is absent: `throw new Error("[Anthropic] No model configured. Set defaultModel in your provider config.")`
- `protocols/openai.ts:85` — if both `(config as any).activeModel` and `config.defaultModel` are absent: `throw new Error("[OpenAI] No model configured. Set defaultModel in your provider config.")`
- Both throw at first use (construction or first request), surfacing misconfiguration before the first real turn.

**Provider circuit OPEN:**
- `ProviderRegistry.getAvailable(name)` returns `null`
- Gateway logs `log.engine.warn("Provider %s circuit open (state: OPEN), trying fallback", name)`
- Calls `ctx.intelligence.resolveFailover(tier)`
- If fallback found: routes to fallback provider
- If no fallback: logs `log.engine.warn("No fallback configured for tier %s, using default provider", tier)` and routes to `config.defaultModel`

**Budget exhausted:**
- `resolveWithCostAwareness()` downgrades tier (high→mid→low)
- If all tiers exceed estimated cost: logs `log.engine.warn("All tiers over daily budget, routing to low tier anyway")` and routes to `low` — never hard-blocks

**`resolveCapable()` no match:**
- Logs `log.engine.warn("No tier has capabilities %j, falling back to unconstrained resolve", required)`
- Returns `resolve(taskType)` result

**`resolveFailover()` returns null:**
- Not an error — caller falls through to `config.defaultModel`
- No exception thrown

**`MODEL_PRICING` staleness:**
- `costs/pricing.ts` exports `export const PRICING_UPDATED_AT = "2026-03-01"`
- In `src/index.ts` bootstrap: if `Date.now() - new Date(PRICING_UPDATED_AT).getTime() > 90 * 86_400_000`, log `log.engine.warn("MODEL_PRICING may be stale (last updated %s). Cost routing estimates may be inaccurate.", PRICING_UPDATED_AT)`

---

## 5. Testing

All tests in `__tests__/element18/`. Runner: `npx vitest run __tests__/element18/`.

### `circuit-breaker.test.ts` (4 tests)
1. CLOSED→OPEN: `failureThreshold` failures trip the breaker; `isOpen()` returns true
2. OPEN→HALF_OPEN: after `recoveryTimeoutMs`, `isOpen()` returns false (allows one probe)
3. HALF_OPEN probe success: `recordResult(true)` → state = CLOSED, `isOpen()` = false
4. HALF_OPEN probe failure: `recordResult(false)` → state = OPEN, timer reset

### `intelligence-router-extensions.test.ts` (8 tests)
5. `resolveCapable()` routes to tier whose `capabilities` includes required tag
6. `resolveCapable()` falls back to unconstrained `resolve()` when no capable tier
7. `resolveWithCostAwareness()` returns normal result when budget unlimited (`maxDailyUsd=0`)
8. `resolveWithCostAwareness()` downgrades from `high` to `mid` when high-tier cost exceeds remaining budget
9. `resolveWithCostAwareness()` routes to `low` with warn when all tiers exceed budget
10. `resolveFailover()` returns first `FallbackEntry` matching the tier
11. `resolveFailover()` returns null when `config.fallbacks` is empty
12. DEFAULT_INTELLIGENCE_CONFIG passthrough: when no `intelligence` in config, routing returns `config.defaultModel`

### `provider-registry-circuit.test.ts` (2 tests)
13. `getAvailable()` returns null for provider with OPEN circuit
14. `getAvailable()` returns provider instance for CLOSED/HALF_OPEN circuit

### `runtime-no-model-router.test.ts` (1 test)
15. `ModelRouter` import removed; Gateway routes a conversation turn via `IntelligenceRouter` correctly

### `adapter-missing-model.test.ts` (2 tests)
16. `AnthropicNativeProvider` throws on construction/first call when `defaultModel` absent
17. `OpenAIProtocol` throws on construction/first call when both `activeModel` and `defaultModel` absent

**Total: ~17 new tests.** Baseline is 5001 (head `eb9833e`).

---

## 6. Migration Plan

### Files deleted
```
src/engine/router.ts              (~190 LOC)
src/providers/openai-compat.ts    (522 LOC)
```

Verify before deleting `openai-compat.ts`:
```bash
grep -r "openai-compat" src/ --include="*.ts"
# Expected: zero results
```
TypeScript compiler (`tsc --noEmit`) is the safety net — any undiscovered import surfaces as a compile error.

### Type stub removed
`config/loader.ts:103–113` — remove the `smartRouting?: { ... }` block from `StackOwlConfig`. The `@deprecated` JSDoc confirms intent; the runtime throw at `loader.ts:408–411` already enforces it. Remove both the type and the throw (the throw is now unnecessary because the type is gone).

### Call sites rewritten in `src/engine/runtime.ts`

**Line 23 — remove import:**
```typescript
// DELETE:
import { ModelRouter } from "./router.js"
```

**Line 775 — routing at ReAct loop start:**
```typescript
// BEFORE:
let routeDecision = ModelRouter.route(userMessage, config)
const modelName = routeDecision.modelName

// AFTER:
const resolved = ctx.intelligence?.resolveWithCostAwareness("conversation")
const modelName = resolved?.model ?? config.defaultModel
```

**Line 1882 — tool-failure escalation:**
```typescript
// BEFORE:
const newRoute = ModelRouter.route(userMessage, config, globalConsecutiveFailures)
const escalatedModel = newRoute.modelName

// AFTER:
const currentTier = ctx.intelligence?.resolve("conversation").tier ?? 'mid'
const fallback = ctx.intelligence?.resolveFailover(currentTier)
const escalatedModel = fallback?.model ?? config.defaultModel
```

### `src/gateway/core.ts` — always construct `ctx.intelligence`

At `core.ts:432–438`, replace the conditional construction:
```typescript
// BEFORE (conditional):
if (ctx.config.intelligence) {
  ctx.intelligence = new IntelligenceRouter(ctx.config.intelligence)
}

// AFTER (always):
const intelligenceConfig = ctx.config.intelligence
  ?? buildDefaultIntelligenceConfig(ctx.config)
ctx.intelligence = new IntelligenceRouter(
  intelligenceConfig,
  () => ctx.costTracker?.checkBudget("system") ?? { dailyRemainingUsd: Infinity, maxDailyUsd: 0 }
)
```

Where `buildDefaultIntelligenceConfig(config)` in `config/loader.ts` fills `DEFAULT_INTELLIGENCE_CONFIG.tiers` from `config.defaultProvider`/`config.defaultModel`.

### Backwards compatibility

- Existing configs omitting `intelligence` → `DEFAULT_INTELLIGENCE_CONFIG` passthrough → identical routing behavior
- Existing configs with `smartRouting` → loader already throws with migration message; no change needed

---

## 7. File Delta Summary

| File | Action | LOC Delta |
|---|---|---|
| `src/providers/circuit-breaker.ts` | **Create** | +120 |
| `src/intelligence/router.ts` | **Modify** | +75 |
| `src/providers/registry.ts` | **Modify** | +25 |
| `src/providers/anthropic-native.ts` | **Modify** | ±0 |
| `src/providers/protocols/openai.ts` | **Modify** | −2 |
| `src/providers/openai-compat.ts` | **Delete** | −522 |
| `src/engine/router.ts` | **Delete** | −190 |
| `src/engine/runtime.ts` | **Modify** | −10 |
| `src/gateway/core.ts` | **Modify** | +15 |
| `src/config/loader.ts` | **Modify** | +15 |
| `src/costs/pricing.ts` | **Modify** | +3 |
| `__tests__/element18/*.test.ts` | **Create** | +~350 |
| `src/index.ts` | **Modify** (staleness warn) | +5 |

**Net source file delta: +1 new − 2 deleted = −1. Net LOC: −591 deleted + ~141 added = −450 LOC.**

---

## 8. Boss-Locked Rules Compliance

| Rule | Status |
|---|---|
| No hardcoded keyword arrays/regex | ✅ `HEAVY_PATTERNS`/`SIMPLE_PATTERNS` deleted with `engine/router.ts` |
| Channel parity | ✅ Provider routing is below channel layer — all channels benefit identically |
| Max 4–6 new files; net delta ≤ 0 | ✅ +1 new file, net −1 |
| Compose existing primitives | ✅ `IntelligenceRouter`, `CostTracker`, `ProviderRegistry`, `GatewayEventBus` all reused |
| Skills Engine (E19) compatibility | ✅ `SkillContextInjector` and `IntentRouter` are unchanged; they call `IntelligenceRouter` which gains new methods but keeps `resolve()` signature |
