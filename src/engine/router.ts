/**
 * StackOwl — Dynamic Model Router
 *
 * Routes prompts to the most appropriate model using a fast heuristic
 * (token/complexity scoring) instead of an LLM call. Zero added latency.
 *
 * Routing tiers (in order of ascending capability):
 *   SIMPLE   — conversational, short, no code/math/tools implied
 *   STANDARD — default; most tasks land here
 *   HEAVY    — code generation, multi-step reasoning, long documents
 */

import type { StackOwlConfig } from "../config/loader.js";
import { log } from "../logger.js";

export interface RouteDecision {
  modelName: string;
  providerName?: string;
}

// ─── Heuristic Signals ───────────────────────────────────────────

const HEAVY_PATTERNS = [
  /\b(implement|refactor|architect|design|migrate|debug|optimize|write.*code|generate.*code)\b/i,
  /\b(algorithm|database|sql|typescript|javascript|python|rust|golang|kubernetes|docker)\b/i,
  /\b(compare|analyze|explain.*in.*detail|summarize.*document|research|plan)\b/i,
  /\b(parliament|orchestrate|multi.*step|complex)\b/i,
];

const SIMPLE_PATTERNS = [
  /^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|yep|nope|cool)[\s.!?]*$/i,
  /^.{0,40}$/, // Very short messages
];

function scoreComplexity(prompt: string): "simple" | "standard" | "heavy" {
  const trimmed = prompt.trim();

  // Short/conversational → simple
  if (SIMPLE_PATTERNS.some((p) => p.test(trimmed))) {
    return "simple";
  }

  // Code/analysis signals → heavy
  if (HEAVY_PATTERNS.some((p) => p.test(trimmed))) {
    return "heavy";
  }

  // Word count as secondary signal
  const wordCount = trimmed.split(/\s+/).length;
  if (wordCount > 60) return "heavy";
  if (wordCount < 12) return "simple";

  return "standard";
}

// ─── Router ──────────────────────────────────────────────────────

export class ModelRouter {
  /**
   * Determine the best model for the given prompt using fast heuristics.
   * No LLM calls — zero added latency per message.
   *
   * failureCount > 0 triggers escalation to the fallback (cloud) provider.
   */
  static route(
    prompt: string,
    config: StackOwlConfig,
    failureCount: number = 0,
  ): RouteDecision {
    // Repeated tool failures → force cross-provider fallback immediately
    if (failureCount >= 2 && config.smartRouting?.fallbackModel) {
      log.engine.warn(
        `[ModelRouter] Local model failed ${failureCount}x. ` +
          `Escalating to ${config.smartRouting.fallbackProvider} / ${config.smartRouting.fallbackModel}`,
      );
      return {
        modelName: config.smartRouting.fallbackModel,
        providerName: config.smartRouting.fallbackProvider,
      };
    }

    // Smart routing disabled or no roster → use default
    if (
      !config.smartRouting?.enabled ||
      !config.smartRouting.availableModels?.length
    ) {
      return { modelName: config.defaultModel };
    }

    const models = config.smartRouting.availableModels;

    // Single model in roster → no decision needed
    if (models.length === 1) {
      return { modelName: models[0].name };
    }

    // Score task complexity
    const tier = scoreComplexity(prompt);

    // Map tier to roster position by index (assume models ordered light → heavy)
    let targetIndex: number;
    if (tier === "simple") {
      targetIndex = 0;
    } else if (tier === "heavy") {
      targetIndex = models.length - 1;
    } else {
      targetIndex = Math.floor(models.length / 2);
    }

    const selected = models[targetIndex];
    log.engine.info(`[ModelRouter] Tier="${tier}" → ${selected.name}`);
    return { modelName: selected.name };
  }
}
