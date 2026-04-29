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
}
