/**
 * StackOwl — Parliament Routing Wirer
 *
 * Wires Parliament detection into the routing path so that
 * shouldConveneParliament() is checked during strategy selection.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { TaskStrategy } from "../orchestrator/types.js";
import { shouldConveneParliament } from "./detector.js";
import { TopicWorthinessEvaluator } from "./topic-worthiness.js";
import { log } from "../logger.js";

// ─── RoutingWirer ──────────────────────────────────────────────

export class RoutingWirer {
  private topicEvaluator?: TopicWorthinessEvaluator;

  constructor() {}

  /**
   * LLM-based check for whether Parliament should be convened.
   * More accurate but requires an LLM call.
   */
  async shouldConvene(
    message: string,
    provider: ModelProvider,
  ): Promise<boolean> {
    // Quick check using the existing detector
    return shouldConveneParliament(message, provider);
  }

  /**
   * Get a detailed worthiness evaluation for the message.
   */
  async evaluateWorthiness(
    message: string,
    provider: ModelProvider,
  ): Promise<{ isWorthy: boolean; score: number; confidence: number }> {
    if (!this.topicEvaluator) {
      this.topicEvaluator = new TopicWorthinessEvaluator(provider);
    }

    const result = await this.topicEvaluator.evaluate(message);
    return {
      isWorthy: result.isWorthy,
      score: result.score,
      confidence: result.confidence,
    };
  }

  /**
   * Classify strategy with Parliament consideration injected.
   *
   * This wraps the base classifyStrategy to add a Parliament check
   * using shouldConveneParliament() for LLM-based detection.
   */
  async classifyWithParliament(
    message: string,
    baseClassifyFn: () => Promise<TaskStrategy>,
    provider: ModelProvider,
    options?: {
      useParallelRunner?: boolean;
      useLLMCheck?: boolean;
    },
  ): Promise<TaskStrategy> {
    const opts = {
      useParallelRunner: true,
      useLLMCheck: true,
      ...options,
    };

    if (opts.useParallelRunner && opts.useLLMCheck) {
      const shouldConvene = await shouldConveneParliament(message, provider).catch(() => false);
      if (shouldConvene) {
        const strategy = await baseClassifyFn();
        if (strategy.strategy === "PARLIAMENT") return strategy;
        return {
          ...strategy,
          strategy: "PARLIAMENT",
          reasoning: `LLM detected debate-worthy topic → escalated to PARLIAMENT`,
          parliamentConfig: {
            topic: message.slice(0, 200),
            owlCount: Math.min(3, strategy.owlAssignments?.length ?? 2),
          },
        };
      }
    }

    return baseClassifyFn();
  }

  /**
   * Create a parliament-ready context by injecting related past debates.
   * @deprecated Parliament context injection is now handled inline by the orchestrator.
   */
  async prepareParliamentContext(
    _message: string,
    _pelletStore: import("../pellets/store.js").PelletStore,
  ): Promise<ChatMessage[]> {
    // Deprecated: Parliament context injection is now handled inline by the orchestrator.
    return [];
  }
}

/**
 * Check if Parliament should be triggered based on message and config.
 */
export async function checkParliamentTrigger(
  message: string,
  provider: ModelProvider,
  config: StackOwlConfig,
): Promise<{ shouldTrigger: boolean; reason: string }> {
  const parliamentEnabled = config.parliament && (config.parliament as Record<string, unknown>).enabled;
  if (parliamentEnabled === false) {
    return { shouldTrigger: false, reason: "Parliament disabled in config" };
  }

  try {
    const shouldConvene = await shouldConveneParliament(message, provider);
    return {
      shouldTrigger: shouldConvene,
      reason: shouldConvene ? "LLM detected debate-worthy topic" : "LLM detected non-debatable topic",
    };
  } catch (err) {
    return {
      shouldTrigger: false,
      reason: `LLM check failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

// Suppress unused import warning
void log;
