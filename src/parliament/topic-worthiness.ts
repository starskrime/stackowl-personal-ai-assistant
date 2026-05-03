/**
 * StackOwl — Topic Worthiness Evaluator
 *
 * Determines whether a topic warrants multi-owl deliberation using LLM-based
 * evaluation instead of hardcoded language patterns.
 */

import type { ModelProvider } from "../providers/base.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import { log } from "../logger.js";

// ─── Types ─────────────────────────────────────────────────────

export type WorthinessCategory =
  | "tradeoff"
  | "dilemma"
  | "architectural"
  | "factual"
  | "other";

export interface TopicWorthinessResult {
  isWorthy: boolean;
  score: number;
  confidence: number;
  reasoning: string;
  indicators: string[];
  category: WorthinessCategory;
}

const EVALUATION_PROMPT = `Evaluate if this topic warrants multi-owl deliberation (Parliament).

Topics worth debating:
- Tradeoffs and dilemmas (cost vs quality, speed vs security)
- Architectural decisions
- Multi-perspective problems
- Complex decisions with competing values

Topics NOT worth debating:
- Simple factual questions
- Greetings
- Trivial tasks
- Commands with clear correct answers

Topic: "{topic}"

Respond with JSON:
{
  "isWorthy": boolean,
  "confidence": 0.0-1.0,
  "reasons": ["reason1", "reason2"],
  "category": "tradeoff|architectural|dilemma|factual|other"
}`;

// ─── TopicWorthinessEvaluator ────────────────────────────────────

export class TopicWorthinessEvaluator {
  constructor(
    private provider: ModelProvider,
    private router?: IntelligenceRouter,
  ) {}

  /**
   * Evaluate whether a topic warrants multi-owl deliberation.
   *
   * @returns TopicWorthinessResult with score, confidence, and reasoning
   *
   * Decision logic:
   * - Trust LLM isWorthy directly — no secondary threshold gate
   */
  async evaluate(topic: string): Promise<TopicWorthinessResult> {
    log.parliament.debug(`[TopicWorthiness] Evaluating: "${topic.slice(0, 60)}..."`);

    const prompt = EVALUATION_PROMPT.replace("{topic}", topic);
    const model = this.router?.resolve("classification").model;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        model,
        { temperature: 0, maxTokens: 200 },
      );

      const content = response.content.trim();
      const jsonMatch = content.match(/\{[\s\S]*\}/);

      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        const confidence = Math.min(1.0, Math.max(0.0, parsed.confidence ?? 0.5));
        const isWorthy = parsed.isWorthy === true;
        const score = isWorthy ? Math.max(0.6, confidence) : Math.min(0.5, confidence);

        const result: TopicWorthinessResult = {
          isWorthy,
          score,
          confidence,
          reasoning: (parsed.reasons ?? []).join("; "),
          indicators: parsed.reasons ?? [],
          category: (parsed.category ?? "other") as WorthinessCategory,
        };

        log.parliament.behavioral("behavioral.parliament.topic_evaluated", {
          topic: topic.slice(0, 100),
          isWorthy: result.isWorthy,
          score: result.score,
          confidence: result.confidence,
          category: result.category,
        });

        log.parliament.info(
          `[TopicWorthiness] → isWorthy=${result.isWorthy} (score=${result.score.toFixed(2)}, conf=${result.confidence.toFixed(2)})`,
        );

        return result;
      }
    } catch (err) {
      log.parliament.debug(
        `[TopicWorthiness] LLM evaluation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    const fallback: TopicWorthinessResult = {
      isWorthy: false,
      score: 0.0,
      confidence: 0.0,
      reasoning: "LLM evaluation unavailable",
      indicators: [],
      category: "other",
    };

    log.parliament.behavioral("behavioral.parliament.topic_evaluated", {
      topic: topic.slice(0, 100),
      isWorthy: fallback.isWorthy,
      score: fallback.score,
      confidence: fallback.confidence,
      category: fallback.category,
    });

    return fallback;
  }
}

/**
 * Convenience function to quickly check if a topic is worthy.
 */
export async function isTopicWorthy(
  topic: string,
  provider: ModelProvider,
): Promise<boolean> {
  const evaluator = new TopicWorthinessEvaluator(provider);
  const result = await evaluator.evaluate(topic);
  return result.isWorthy;
}
