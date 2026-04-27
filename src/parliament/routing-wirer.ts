/**
 * StackOwl — Parliament Routing Wirer
 *
 * Wires Parliament detection into the routing path so that
 * shouldConveneParliament() and ParallelRunner.shouldTrigger() are
 * checked during strategy selection.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { TaskStrategy } from "../orchestrator/types.js";
import { ParallelParliamentRunner } from "./parallel-runner.js";
import { shouldConveneParliament } from "./detector.js";
import { TopicWorthinessEvaluator } from "./topic-worthiness.js";
import { log } from "../logger.js";

// ─── RoutingWirer ──────────────────────────────────────────────

export class RoutingWirer {
  private topicEvaluator?: TopicWorthinessEvaluator;

  constructor() {}

  /**
   * Fast pre-filter check using ParallelRunner's heuristic.
   * No LLM call needed — uses keyword matching and confidence threshold.
   */
  static shouldTrigger(topic: string, owlConfidence?: number): boolean {
    return ParallelParliamentRunner.shouldTrigger(topic, owlConfidence);
  }

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
   * This wraps the base classifyStrategy to add a Parliament pre-check
   * using the fast shouldTrigger heuristic, and a post-check using
   * shouldConvene for confirmation.
   */
  async classifyWithParliament(
    message: string,
    baseClassifyFn: () => Promise<TaskStrategy>,
    provider: ModelProvider,
    options?: {
      useParallelRunner?: boolean;
      useLLMCheck?: boolean;
      confidenceThreshold?: number;
    },
  ): Promise<TaskStrategy> {
    const opts = {
      useParallelRunner: true,
      useLLMCheck: true,
      confidenceThreshold: 0.6,
      ...options,
    };

    // Step 1: Fast pre-filter using ParallelRunner heuristic
    if (opts.useParallelRunner && ParallelParliamentRunner.shouldTrigger(message)) {
      log.engine.info(
        `[RoutingWirer] ParallelRunner.shouldTrigger matched for: "${message.slice(0, 60)}..."`,
      );

      // Run LLM check for confirmation (if enabled)
      if (opts.useLLMCheck) {
        const shouldConvene = await shouldConveneParliament(message, provider).catch(() => false);

        if (!shouldConvene) {
          log.engine.info(
            `[RoutingWirer] ParallelRunner triggered but LLM check said skip — using base classification`,
          );
          return baseClassifyFn();
        }
      }

      // Trigger Parliament bias via base classification
      const strategy = await baseClassifyFn();

      // If strategy is already PARLIAMENT, we're good
      if (strategy.strategy === "PARLIAMENT") {
        return strategy;
      }

      // Bias toward PARLIAMENT if confidence is low and topic is triggered
      if (strategy.confidence < (opts.confidenceThreshold ?? 0.6)) {
        log.engine.info(
          `[RoutingWirer] Biasing toward PARLIAMENT (original strategy: ${strategy.strategy}, confidence: ${strategy.confidence})`,
        );

        return {
          ...strategy,
          strategy: "PARLIAMENT",
          reasoning: `Parallel-runner trigger + low confidence (${strategy.confidence}) → escalated to PARLIAMENT`,
          parliamentConfig: {
            topic: message.slice(0, 200),
            owlCount: Math.min(3, strategy.owlAssignments?.length ?? 2),
          },
        };
      }

      return strategy;
    }

    // Step 2: Normal classification path
    return baseClassifyFn();
  }

  /**
   * Create a parliament-ready context by injecting related past debates.
   */
  async prepareParliamentContext(
    message: string,
    _pelletStore: import("../pellets/store.js").PelletStore,
  ): Promise<ChatMessage[]> {
    const context: ChatMessage[] = [];

    // This would integrate with pellet store to find related debates
    // For now, return empty context (deferred to orchestrator)
    log.engine.debug(
      `[RoutingWirer] Preparing Parliament context for: "${message.slice(0, 60)}..."`,
    );

    void _pelletStore; // Will be used in future implementation
    return context;
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
  // Fast path: check if parliament is enabled (via extension field)
  const parliamentEnabled = config.parliament && (config.parliament as Record<string, unknown>).enabled;
  if (parliamentEnabled === false) {
    return { shouldTrigger: false, reason: "Parliament disabled in config" };
  }

  // Fast pre-filter using ParallelRunner
  if (ParallelParliamentRunner.shouldTrigger(message)) {
    return { shouldTrigger: true, reason: "ParallelRunner heuristic triggered" };
  }

  // LLM-based check
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