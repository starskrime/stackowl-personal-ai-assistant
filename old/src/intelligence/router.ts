import { log } from "../logger.js";
import { estimateCost } from "../costs/pricing.js";

export const TIER_ORDER: Tier[] = ["low", "mid", "high"];

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
  conversation:   "low",
  parliament:     "low",
  evolution:      "low",
  extraction:     "low",
  episodic:       "low",
  classification: "mid",
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

    log.engine.warn(
      `[IntelligenceRouter] No tier has capabilities [${required.join(", ")}] — falling back to unconstrained resolve`,
    );
    return this.resolve(taskType);
  }

  /**
   * Resolve model for taskType with cost awareness.
   * If getBudgetState is set and maxDailyUsd > 0, downgrades tier when
   * the estimated per-request cost would exceed remaining daily budget.
   * Never hard-blocks — routes with a warning when all tiers are over budget.
   */
  resolveWithCostAwareness(taskType: TaskType): ResolvedModel {
    const budget = this.getBudgetState?.();
    if (!budget || budget.maxDailyUsd <= 0) return this.resolve(taskType);

    // Estimate cost as 1000 input + 2000 output tokens (conservative ceiling per request)
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

  /**
   * Resolve model for taskType, but treat `floor` as the minimum tier.
   * If the normal resolution would produce a lower tier, it is clamped up to floor.
   * Uses cost-aware resolution as the base so budget downgrade still applies within the floor.
   */
  resolveWithFloor(taskType: TaskType, floor: Tier): ResolvedModel {
    const base = this.resolveWithCostAwareness(taskType);
    const baseIdx = TIER_ORDER.indexOf(base.tier);
    const floorIdx = TIER_ORDER.indexOf(floor);
    if (baseIdx >= floorIdx) return base; // already at or above floor

    // Clamp up to floor tier
    const cfg = this.config.tiers[floor];
    if (!cfg?.provider || !cfg?.model) {
      log.engine.warn(
        `[IntelligenceRouter] Floor tier "${floor}" is not configured — using base tier "${base.tier}"`,
      );
      return base;
    }
    return { provider: cfg.provider, model: cfg.model, tier: floor };
  }

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
}
