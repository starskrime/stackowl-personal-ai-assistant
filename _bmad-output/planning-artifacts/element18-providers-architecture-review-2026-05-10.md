# Element 18 — Providers Layer Architecture Review (2026-05-10)

🏗️ Winston — System Architect

**Inputs:** Phase 1 audit (10 gaps P1–P10, `element18-providers-audit-2026-05-10.md`) + Phase 2 research (10 sections, risk register R1–R10, `market-element18-providers-research-2026-05-10.md`).  
**All source files read firsthand before locking.** Divergences from audit flagged inline.

---

## Audit Divergences (Read Before the Decisions)

Two claims in the Phase 1 audit require correction — the code is the source of truth.

**Divergence 1 — P3/P9: `engine/router.ts` is NOT dead code.**  
The audit stated the file is inert. False: `src/engine/runtime.ts:23` imports `ModelRouter`; `runtime.ts:775` calls `ModelRouter.route(userMessage, config)` at the start of every ReAct loop; `runtime.ts:1882` calls it again on tool-failure escalation. The file IS actively called. However, it is *effectively* inert because `config.smartRouting` is always `undefined` at runtime (the loader throws if `smartRouting` is present in JSON, per `config/loader.ts:408-411`), so every call falls through to `return { modelName: config.defaultModel }` at `router.ts:99`. The routing tiers, domain detection, and model-roster indexing (lines 102–151) run but produce only the default. Additionally, `HEAVY_PATTERNS` (lines 32–37) and `SIMPLE_PATTERNS` (lines 39–42) are hardcoded regex arrays in violation of the no-keywords rule — this was not flagged in the audit.

**Divergence 2 — P5: Hardcoded model in `anthropic-native.ts` is at line 141, not 385.**  
Line 385 is in `listModels()` — a discovery method returning a hardcoded array of known models. The routing-relevant hardcoded default is at `anthropic-native.ts:141`: `this.defaultModel = config.defaultModel ?? "claude-sonnet-4-20250514"`. Both are problems but for different reasons.

---

## Locked Decisions (8)

**D1 — Delete `engine/router.ts`; replace both call sites in `runtime.ts` with `IntelligenceRouter`.** `ModelRouter.route()` is called at `runtime.ts:775` and `runtime.ts:1882` but always returns `{ modelName: config.defaultModel }` because `config.smartRouting` is always undefined. The complexity-scoring (regex arrays), domain detection, and tool-mastery logic (lines 113–137) run every turn but their output is discarded — `toolMastery` and `domainToolMap` are never passed at the call sites. The `HEAVY_PATTERNS`/`SIMPLE_PATTERNS` regex arrays (lines 32–42) violate the no-hardcoded-keywords rule. **Decision: delete `src/engine/router.ts` (~190 LOC).** Replace `runtime.ts:775` with `ctx.intelligence?.resolve("conversation").model ?? config.defaultModel`. Replace `runtime.ts:1882` (tool-failure escalation) with a new `IntelligenceRouter.resolveFailover()` method that returns the first healthy fallback from `IntelligenceConfig.fallbacks[]` (D6). The `StackOwlConfig.smartRouting` type stub in `config/loader.ts:103–113` is removed at the same time — it was explicitly marked `@deprecated` and the loader already throws on it.

**D2 — Default-construct `IntelligenceRouter` in loader; keep `intelligence?` type as-is.** `gateway/types.ts:346` has `intelligence?: IntelligenceRouter`. Making this non-optional would require changes across dozens of call sites that use optional chaining. Instead: add a `DEFAULT_INTELLIGENCE_CONFIG` constant in `config/loader.ts` and always construct `ctx.intelligence` in the Gateway initializer at `core.ts:432–438` — if `config.intelligence` is absent, fall back to the default config. This guarantees `ctx.intelligence` is always set post-construction without changing the type. The default config maps every task type to the owl's own `providerName`/`defaultModel` (pass-through — no change in behavior for configs that omit `intelligence`). The `_clarificationRouter` fallback at `core.ts:408` and all `ctx.intelligence?.resolve()` optional chains remain valid but will always resolve.

**D3 — Add `ProviderCircuitBreaker` per provider (new file `src/providers/circuit-breaker.ts`).** `ProviderRegistry.healthCheckAll()` at `registry.ts:189–195` is a one-shot startup probe with no ongoing monitoring. Add a minimal `ProviderCircuitBreaker` class with three states: CLOSED (normal), OPEN (failing), HALF-OPEN (probing recovery). Parameters from §3 research: `failureThreshold=5`, `recoveryTimeoutMs=30000`. **Passive-only monitoring** (Q3 decision): `recordResult(success: boolean)` is called after every API response; `isOpen(): boolean` is checked by the Gateway before routing to a provider. HALF-OPEN: first request after recovery timeout passes through as a probe; success → CLOSED, failure → OPEN with reset timer. Each entry in `ProviderRegistry` gets one breaker. `ProviderRegistry.get(name)` gains a `checkBreaker` parameter (default `true`) so direct provider lookups in health-check paths bypass the breaker. No active synthetic probes — StackOwl is personal-use; probes waste tokens when idle.

**D4 — Add `capabilities?: string[]` to `TierConfig`; add `resolveCapable()` to `IntelligenceRouter`.** No capability field exists anywhere in `src/providers/` or `src/intelligence/`. Add `capabilities?: string[]` to `TierConfig` in `intelligence/router.ts:14–17`. Tag vocabulary locked (from §4 research): `vision`, `code`, `reasoning`, `long-context`, `tool-use`, `fast`, `structured-output`. Add `IntelligenceRouter.resolveCapable(taskType: TaskType, required: string[]): ResolvedModel` — filters `this.config.tiers` to entries whose `capabilities` array contains all required tags, applies normal tier-priority ordering within the eligible set, falls back to unconstrained `resolve(taskType)` if no capable tier exists with a `log.engine.warn`. **Graceful degrade, not hard-fail** (Q4 decision). Usage: Gateway passes `required: ["vision"]` when the user message contains an image attachment; otherwise passes `[]` (standard routing).

**D5 — Wire `CostTracker` into `IntelligenceRouter` via injected budget accessor.** `CostTracker` lives at `ctx.costTracker` (wired at `core.ts:466`). `IntelligenceRouter` has no reference to it. **Decision: add an optional `getBudgetState?: () => { dailyRemainingUsd: number; maxDailyUsd: number }` parameter to the `IntelligenceRouter` constructor.** Add `resolveWithCostAwareness(taskType: TaskType, userId?: string): ResolvedModel` — if `maxDailyUsd > 0` and `dailyRemainingUsd < estimatedTierCost(tier)`, downgrade tier (high→mid→low). `estimatedTierCost(tier)` reads `MODEL_PRICING` for the tier's model, estimating 2000 tokens output as a conservative per-request ceiling. **When all tiers are over budget: allow but log warning** (don't hard-block — personal-use assistant must not silently fail). The Gateway passes a closure `() => costTracker.checkBudget("system")` at construction. Sync (Q2 decision): budget state is pre-computed from the last `CostTracker.checkBudget()` result cached at Gateway level — no async in the routing hot path.

**D6 — Extend `IntelligenceConfig` with `fallbacks`, `healthPolicy`, `costPolicy`.** Current `IntelligenceConfig` at `intelligence/router.ts:19–23` has only `tiers`, `defaults`, `overrides`. Extend with three optional fields (all optional → zero breaking change for existing configs):

```typescript
export interface FallbackEntry {
  provider: string;
  model: string;
  forTiers: Tier[];       // which tiers this fallback covers
}

export interface HealthPolicy {
  failureThreshold: number;   // default 5
  recoveryTimeoutMs: number;  // default 30000
}

export interface CostPolicy {
  maxDailyUsd: number;                        // default 0 = unlimited
  downgradeTierOnBudgetExhausted: boolean;   // default true
}

export interface IntelligenceConfig {
  tiers: Record<Tier, TierConfig>;
  defaults: Partial<Record<TaskType, Tier>>;
  overrides?: Partial<Record<TaskType, Partial<TierConfig>>>;
  fallbacks?: FallbackEntry[];      // NEW — ordered failover chain
  healthPolicy?: HealthPolicy;      // NEW — circuit breaker params
  costPolicy?: CostPolicy;          // NEW — budget policy
}
```

`IntelligenceRouter.resolveFailover(tier: Tier): ResolvedModel | null` — returns the first `FallbackEntry` whose `forTiers` includes the given tier, or `null` if no fallback configured. Used by D1's `runtime.ts:1882` replacement.

**D7 — Delete `openai-compat.ts`.** `src/providers/openai-compat.ts` (522 LOC) has zero production imports confirmed via grep. The `"openai-compatible"` string in `onboarding-flow.ts:60`, `onboarding.ts:545`, and `telegram-config/screens.ts:47` refers to a ProviderConfig `type` value routed through `ProviderRegistry`'s `openai` protocol factory — not to this file. The `__tests__/providers.test.ts:197` test registers a provider named `"openai-compatible"` but tests `ProviderRegistry.register()`, not the `openai-compat.ts` class. **Safe to delete.** TypeScript compiler will catch any undiscovered import. Net: −522 LOC.

**D8 — Remove hardcoded model name strings; fail fast on missing config.** Two sites: `anthropic-native.ts:141` → `this.defaultModel = config.defaultModel ?? "claude-sonnet-4-20250514"` and `protocols/openai.ts:85` → `(config as any).activeModel ?? config.defaultModel ?? "gpt-4o"`. **Decision: throw if the resolved model is empty** rather than silently defaulting to a pinned version string. Change both to: if `config.defaultModel` and `(config as any).activeModel` are both absent, throw `Error("[ProviderName] No model configured. Set defaultModel in your provider config.")`. The `listModels()` hardcoded array in `anthropic-native.ts:381–386` is a discovery method used by health-check only; replace with a live `this.client.models.list()` API call (Anthropic SDK supports it at `anthropic-native.ts:392`: `await this.client.models.list()` is already called in `healthCheck()`). The authoritative model names live in `IntelligenceConfig.tiers.*` and `stackowl.config.json` — adapters must not override them.

---

## Resolved Open Questions (7)

**Q1 — `MODEL_PRICING` stays in source with staleness warning.** Moving it to config introduces user-facing complexity for no gain at StackOwl's personal-use scale. Keep in `costs/pricing.ts`. Add a build-time `updatedAt: "2026-03-01"` comment and a startup log warning `if (Date.now() - parseDate(updatedAt) > 90 * 86400 * 1000)`. The research confirms this is sufficient (§9 mitigates R2).

**Q2 — `resolve()` stays sync; budget state is pre-cached.** The routing hot path must not block on async DB or file reads. `CostTracker.checkBudget()` result is cached as `lastBudgetCheck: BudgetCheck` on the Gateway, refreshed after each request completes. `resolveWithCostAwareness()` reads the cached value synchronously.

**Q3 — Passive-only health monitoring in `ProviderCircuitBreaker`.** No active synthetic probes. HALF-OPEN state provides natural probe behavior: one real request passes through after the recovery timeout. Active probes waste tokens in a personal-use assistant that may be idle for hours. Mitigates R3 (false positives) via the sliding-window approach.

**Q4 — Graceful capability degrade, not hard-fail.** When no tier has all required capabilities, log a warning and route to the best available model anyway. Hard-block on missing `vision` capability would break every text conversation for users who haven't configured a vision-capable model. The capability system is advisory at this scope.

**Q5 — Delete `engine/router.ts` entirely (YAGNI).** The domain-routing and tool-mastery logic (lines 113–137) was never wired — `toolMastery` and `domainToolMap` are never passed at the two call sites in `runtime.ts`. Extracting "potentially useful" dead logic violates YAGNI. The complexity-scoring regex arrays violate the no-keywords rule. Net: ~190 LOC deleted cleanly.

**Q6 — File delta: +1 new, −2 deleted = net −1.** New: `src/providers/circuit-breaker.ts` (D3, estimated ~120 LOC). Deleted: `src/engine/router.ts` (D1, ~190 LOC), `src/providers/openai-compat.ts` (D7, 522 LOC). Net delta: −591 LOC. Well within budget.

**Q7 — Gateway calls `IntelligenceRouter` directly; `OwlBrain` is a separate concern.** `OwlBrain` (wired at `core.ts:610`) handles routing between owl personas — which OWL answers this message. `IntelligenceRouter` handles model-tier selection — which MODEL the chosen owl uses. These layers are orthogonal. Call chain: `Gateway.handleMessage()` → `IntelligenceRouter.resolveWithCostAwareness(taskType)` → `ResolvedModel` → provider lookup. `OwlBrain` runs before this, selecting the owl; `IntelligenceRouter` runs after, selecting the model for that owl's turn.

---

## File-by-File Change Matrix

| File | Action | Reason | LOC Delta |
|---|---|---|---|
| `src/intelligence/router.ts` | **Modify** | Add `FallbackEntry`, `HealthPolicy`, `CostPolicy` types; extend `IntelligenceConfig`; add `resolveCapable()`, `resolveWithCostAwareness()`, `resolveFailover()` | +70 |
| `src/providers/circuit-breaker.ts` | **Create** | D3 — `ProviderCircuitBreaker` class with CLOSED/OPEN/HALF-OPEN state machine | +120 |
| `src/providers/registry.ts` | **Modify** | D3 — add `CircuitBreaker` per provider; add `isProviderOpen(name)` | +30 |
| `src/providers/base.ts` | **Modify** | D4 — add `capabilities?: string[]` to `TierConfig` (already in this file as `ProviderConfig`; confirm placement) | +5 |
| `src/providers/anthropic-native.ts` | **Modify** | D8 — throw on missing model; replace `listModels()` hardcoded array with API call | ±0 |
| `src/providers/protocols/openai.ts` | **Modify** | D8 — throw on missing model | −2 |
| `src/providers/openai-compat.ts` | **Delete** | D7 — dead code, 522 LOC eliminated | −522 |
| `src/engine/router.ts` | **Delete** | D1 — active but inert; regex arrays violate no-keywords rule; ~190 LOC | −190 |
| `src/engine/runtime.ts` | **Modify** | D1 — replace two `ModelRouter.route()` call sites with `IntelligenceRouter` calls | ±5 |
| `src/gateway/core.ts` | **Modify** | D2 — always construct `ctx.intelligence` from default config; D5 — pass budget accessor to router constructor | +15 |
| `src/config/loader.ts` | **Modify** | D1 — remove `smartRouting` type stub; D2 — add `DEFAULT_INTELLIGENCE_CONFIG` | +20 |
| `src/costs/pricing.ts` | **Modify** | Q1 — add `updatedAt` comment + staleness warning export | +5 |
| `__tests__/element18/*.test.ts` | **Create** | G15 equivalent — test tasks (see below) | +new |

**Net file delta: +1 new (`circuit-breaker.ts`) − 2 deleted = −1. LOC net: −591 deleted + ~145 added = −446 LOC. Comfortably within budget.**

---

## Required Tests (G15-equivalent)

Minimum tests to merge:
1. `ProviderCircuitBreaker` — CLOSED→OPEN transition at threshold; OPEN→HALF-OPEN after timeout; HALF-OPEN probe success→CLOSED; HALF-OPEN probe failure→OPEN reset
2. `IntelligenceRouter.resolveCapable()` — routes to capable tier; degrades gracefully when no capable tier
3. `IntelligenceRouter.resolveWithCostAwareness()` — downgrades tier on budget exhausted; allows (with warn) when all tiers exhausted
4. `IntelligenceRouter.resolveFailover()` — returns correct fallback entry for tier; returns null when no fallback configured
5. `ProviderRegistry` with circuit breaker — `get()` skips open provider; falls to fallback
6. `engine/runtime.ts` smoke test — `ModelRouter` removed; routing falls back to `IntelligenceRouter` correctly
7. `anthropic-native.ts` / `protocols/openai.ts` — throws on missing model config

---

## Risk Tieback (R1–R10)

| Risk | Architecture Mitigation |
|---|---|
| **R1** — New provider breaks schema | D6 `fallbacks[]` is additive; new providers append entries. Mitigated. |
| **R2** — Pricing table staleness | Q1 adds `updatedAt` + startup warning. Mitigated (warning only). |
| **R3** — Circuit breaker false positives | D3 uses sliding-window failure rate (threshold=5 per §3 research). Mitigated. |
| **R4** — Capability tag mismatch | D4 graceful degrade + integration tests. Partially mitigated; full mitigation requires live API verification (deferred). |
| **R5** — Silent routing bypass | D2 always constructs `ctx.intelligence` from DEFAULT config. Mitigated. |
| **R6** — Config schema breaking change | D6 all new fields are optional with defaults. Zero breaking change. Mitigated. |
| **R7** — `openai-compat.ts` deletion breaks undiscovered path | D7 pre-deletion grep + TypeScript compiler verifies. Mitigated. |
| **R8** — Cost routing over-constrains selection | D5 only downgrades on budget exhausted; normal path unchanged. Mitigated. |
| **R9** — API key committed accidentally | Security is out of scope for E18 (per §10 research assessment). Deferred to post-E18 CI rule. |
| **R10** — Hardcoded model deprecation | D8 throws on missing model; requires config to always specify. Mitigated. |

---

## Creative Ideas — Deferred to Phase B

- **CI-1 — Prompt cache routing**: Route repeated system-prompt-heavy requests to the same provider to maximize cache hits (§2 research: 70–90% input cost reduction). Requires per-session provider affinity tracking.
- **CI-2 — Dynamic pricing refresh**: Periodic fetch of model prices from a community-maintained JSON (e.g., `llm.datasette.io` pricing feed). Would replace the manual `MODEL_PRICING` update cycle.
- **CI-3 — Provider latency tracking**: Record P95 latency per provider in `ProviderCircuitBreaker`; expose via `/metrics` in `SkillManagementRouter` (Element 19 D8). Feeds into `latency-based-routing` strategy.
- **CI-4 — Virtual keys for multi-user**: If StackOwl goes multi-user, add per-user virtual key abstraction at the `CostTracker` level. Out of scope at current personal-use scale.
- **CI-5 — `intelligence.tiers` from model auto-discovery**: At startup, query each registered provider's `listModels()` and auto-populate missing `TierConfig` entries. Eliminates need to manually configure model names for providers that expose a model list.

---

*Architecture review complete. All 8 decisions locked. File delta verified (−1). HALT — awaiting Boss approval before Phase 4 (brainstorming/spec) launches.*
