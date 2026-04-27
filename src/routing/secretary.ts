/**
 * StackOwl — Secretary Owl Router
 *
 * The Secretary Owl acts as a mandatory facade for all user messages.
 * It decides whether to:
 * - Answer directly as a generalist
 * - Route to a specialized owl
 * - Convene parliament for complex queries
 */

import type { MemoryDatabase } from "../memory/db.js";
import type { SpecializedOwl } from "../memory/db.js";
import { log } from "../logger.js";

const MIN_MESSAGE_LENGTH = 10;
const ROUTING_CONFIDENCE_THRESHOLD = 0.4;
const MATCH_SCORE_THRESHOLD = 0.25;
const MATCH_WEIGHT = 0.7;
const DNA_WEIGHT = 0.3;

const PARLIAMENT_KEYWORDS = [
  "compare", "versus", "vs", "difference between",
  "pros and cons", "advantages and disadvantages",
  "should we", "should i", "decision", "choose between",
  "analyze", "analysis", "evaluate", "assessment",
  "strategy", "strategic", "planning", "plan",
  "architecture", "design", "system design",
] as const;

export type RoutingDecision =
  | { type: "direct"; reason: string }
  | { type: "specialist"; owl: SpecializedOwl; reason: string }
  | { type: "parliament"; reason: string };

export class SecretaryRouter {
  private db: MemoryDatabase;

  constructor(db: MemoryDatabase) {
    this.db = db;
  }

  /**
   * Decide how to route the incoming message.
   * All messages go through the Secretary Owl first.
   */
  route(
    message: string,
    userId: string,
  ): RoutingDecision {
    const owls = this.db.owls.getByOwner(userId);

    if (owls.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const messageLower = message.toLowerCase();
    const matchedOwl = this.findBestMatch(messageLower, owls);

    if (matchedOwl && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedOwl);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(
          `[SecretaryRouter] Routing to ${matchedOwl.name} (confidence: ${confidence.toFixed(2)})`,
        );
        const decision = {
          type: "specialist" as const,
          owl: matchedOwl,
          reason: `Matched routing rules: ${matchedOwl.routingRules.slice(0, 3).join(", ")}`,
        };
        this.logRoutingDecision(userId, message, decision, "success");
        return decision;
      }
    }

    if (this.shouldConveneParliament(message)) {
      const decision = { type: "parliament" as const, reason: "Complex query detected - convening parliament" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const decision = { type: "direct" as const, reason: "No specialist match found" };
    this.logRoutingDecision(userId, message, decision, "success");
    return decision;
  }

  /**
   * Find the best matching owl based on routing rules.
   */
  private findBestMatch(message: string, owls: SpecializedOwl[]): SpecializedOwl | null {
    let bestMatch: SpecializedOwl | null = null;
    let bestScore = 0;

    for (const owl of owls) {
      const score = this.scoreMatch(message, owl);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = owl;
      }
    }

    return bestScore >= MATCH_SCORE_THRESHOLD ? bestMatch : null;
  }

  /**
   * Score how well a message matches an owl's routing rules.
   */
  private scoreMatch(message: string, owl: SpecializedOwl): number {
    const rules = owl.routingRules.map((r) => r.toLowerCase());
    if (rules.length === 0) return 0;

    const messageLower = message.toLowerCase();
    let matches = 0;
    for (const rule of rules) {
      if (messageLower.includes(rule)) {
        matches++;
      }
    }

    return matches / rules.length;
  }

  /**
   * Calculate confidence in the routing decision.
   */
  private calculateConfidence(messageLower: string, owl: SpecializedOwl): number {
    const matchScore = this.scoreMatch(messageLower, owl);
    const dnaScore = owl.dna?.routingQuality ?? 0.5;
    return (matchScore * MATCH_WEIGHT) + (dnaScore * DNA_WEIGHT);
  }

  /**
   * Check if parliament should be convened.
   * Uses keyword-based heuristic for fast path.
   */
  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;

    if (keywordCount >= 2) {
      return true;
    }

    if (keywordCount === 1 && message.length > 200) {
      return true;
    }

    return false;
  }

  /**
   * Get the main owl for a user (Secretary Owl).
   */
  getMainOwl(userId: string): SpecializedOwl | null {
    return this.db.owls.getMainOwl(userId);
  }

  /**
   * Log a routing decision for evolution feedback.
   */
  private logRoutingDecision(
    userId: string,
    message: string,
    decision: RoutingDecision,
    outcome: "success" | "failure",
  ): void {
    const logEntry = {
      userId,
      message: message.slice(0, 100),
      decisionType: decision.type,
      targetOwl: decision.type === "specialist" ? decision.owl.name : null,
      reason: decision.reason,
      outcome,
      timestamp: new Date().toISOString(),
    };

    log.engine.info(
      `[SecretaryRouter] Routing decision: ${JSON.stringify(logEntry)}`,
    );
  }
}