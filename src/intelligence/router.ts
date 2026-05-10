import { log } from "../logger.js";

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

  // resolveWithCostAwareness(), resolveFailover() added in Tasks 6-7
}
