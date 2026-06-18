# IntelligenceRouter Design Spec

**Date:** 2026-04-29  
**Status:** Approved — all 5 sections  
**Context:** Discovered during Element 3 (SessionManager) audit — every platform component currently uses the default provider with no ability to route cheap tasks to cheap models or critical tasks to powerful ones.

---

## Problem

Every platform component — Parliament, Evolution, session extraction, episodic memory, classification, synthesis, summarization — calls `defaultProvider` unconditionally. There is no mechanism to say "use haiku-class for background extraction, use opus-class for Parliament synthesis."

The existing `smartRouting` block in config handles conversation-level SIMPLE/STANDARD/HEAVY heuristics only. It does not address non-conversation task types, and `ModelRouter` is intentionally untouched.

---

## Section 1: Architecture

**New file:** `src/intelligence/router.ts`  
**Injected via:** `GatewayContext` (already passed to all platform components)

```
stackowl.config.json
  └── intelligence block
        ├── tiers: { high, mid, low } → { provider, model }
        ├── defaults: { taskType → tier }
        └── overrides: { taskType → { provider?, model? } }

IntelligenceRouter (src/intelligence/router.ts)
  └── resolve(taskType: TaskType) → { provider: string, model: string, tier: Tier }

GatewayContext
  └── intelligence: IntelligenceRouter   ← injected at startup in core.ts

Platform components (Parliament, Evolution, SessionService, etc.)
  └── ctx.intelligence.resolve("parliament")  → { provider, model, tier }
```

`ModelRouter` stays untouched — it handles conversation SIMPLE/STANDARD/HEAVY heuristics only.

---

## Section 2: Config Structure

The `smartRouting` block is **removed**. A hard break at startup throws if it is present.

New `intelligence` block in `stackowl.config.json`:

```json
"intelligence": {
  "tiers": {
    "high": { "provider": "anthropic", "model": "claude-opus-4-7" },
    "mid":  { "provider": "anthropic", "model": "claude-sonnet-4-6" },
    "low":  { "provider": "anthropic", "model": "claude-haiku-4-5-20251001" }
  },
  "defaults": {
    "conversation":    "mid",
    "parliament":      "high",
    "evolution":       "mid",
    "extraction":      "low",
    "episodic":        "low",
    "classification":  "low",
    "synthesis":       "high",
    "summarization":   "low",
    "clarification":   "mid"
  },
  "overrides": {
    "parliament": { "model": "claude-opus-4-7" }
  }
}
```

**Resolution order:**
1. `overrides[taskType]` — apply any fields present (partial override)
2. `defaults[taskType]` → tier → `tiers[tier]`
3. If tier missing in `defaults` → fall back to `mid` tier
4. If `mid` tier not configured → fall back to `defaultProvider` / `defaultModel`

---

## Section 3: IntelligenceRouter Class

```typescript
// src/intelligence/router.ts

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

export interface ResolvedModel {
  provider: string;
  model: string;
  tier: Tier;
}

export interface IntelligenceConfig {
  tiers: Record<Tier, { provider: string; model: string }>;
  defaults: Partial<Record<TaskType, Tier>>;
  overrides?: Partial<Record<TaskType, { provider?: string; model?: string }>>;
}

export class IntelligenceRouter {
  constructor(
    private config: IntelligenceConfig,
    private fallbackProvider: string,
    private fallbackModel: string,
  ) {}

  resolve(taskType: TaskType): ResolvedModel {
    const override = this.config.overrides?.[taskType];
    const tier = (this.config.defaults[taskType] ?? "mid") as Tier;
    const base = this.config.tiers[tier] ?? {
      provider: this.fallbackProvider,
      model: this.fallbackModel,
    };

    return {
      provider: override?.provider ?? base.provider,
      model: override?.model ?? base.model,
      tier,
    };
  }
}
```

The method is synchronous — O(1) lookup, no async needed.

---

## Section 4: Built-in Defaults + Validation

**TASK_TYPE_DEFAULTS** — built-in fallback map used when config `defaults` does not cover a task type:

```typescript
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
```

**Config validation** — runs at startup in `src/config/loader.ts`:

1. If `smartRouting` key is present → **throw** with clear migration message: `"smartRouting is no longer supported. Replace with intelligence block. See docs/platform-audit/progress.md."`
2. If `intelligence` block is present but `tiers` is missing or empty → **throw**: `"intelligence.tiers is required"`
3. If `intelligence.tiers.mid` is missing → **throw**: `"intelligence.tiers.mid is required (used as fallback)"`
4. Any task type in `defaults` referencing a tier not defined in `tiers` → **log warning** at startup (not throw, because defaults can reference valid tiers only)

---

## Section 5: GatewayContext Injection + Hard Break

**GatewayContext** (`src/gateway/types.ts`) gets a new optional field:

```typescript
export interface GatewayContext {
  // ... existing fields ...
  intelligence?: IntelligenceRouter;
}
```

**Instantiation** in `src/gateway/core.ts` constructor:

```typescript
// After config validation (which throws on smartRouting / missing tiers):
if (config.intelligence) {
  this.ctx.intelligence = new IntelligenceRouter(
    config.intelligence,
    config.defaultProvider,
    config.defaultModel,
  );
}
```

**Usage pattern** in any platform component:

```typescript
const { provider, model } = ctx.intelligence?.resolve("extraction")
  ?? { provider: ctx.defaultProvider, model: ctx.defaultModel };
```

The `??` fallback ensures backward compatibility for components that run before `intelligence` is wired in tests or legacy callers.

**Hard break on `smartRouting`:** Config loader throws at process start — no migration path, no shim. Users must update their `stackowl.config.json` manually. The error message points to the progress doc for the new schema.

---

## Onboarding Flow Update (Backlog)

The `start.sh` interactive setup currently asks about provider/model for the default provider only. After IntelligenceRouter is implemented, `start.sh` should:

1. Ask if the user wants to configure per-task intelligence tiers or use a single model for everything
2. If tiers: ask provider + model for `high`, `mid`, `low` (with sensible defaults pre-filled)
3. Write the resulting `intelligence` block into `stackowl.config.json`
4. Remove any `smartRouting` block that may be present

This is a follow-on task — implement after IntelligenceRouter core is shipped.

---

## Files Touched

| File | Change |
|------|--------|
| `src/intelligence/router.ts` | **Create** — IntelligenceRouter class, TaskType, Tier, ResolvedModel, TASK_TYPE_DEFAULTS |
| `src/gateway/types.ts` | **Modify** — add `intelligence?: IntelligenceRouter` to GatewayContext |
| `src/gateway/core.ts` | **Modify** — instantiate IntelligenceRouter in constructor |
| `src/config/loader.ts` | **Modify** — hard break on smartRouting, validate tiers |
| `stackowl.config.json` (template / start.sh) | **Modify** — replace smartRouting with intelligence block |
| `__tests__/intelligence-router.test.ts` | **Create** — unit tests for resolve(), validation, fallback chain |
