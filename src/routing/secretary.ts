/**
 * StackOwl — Secretary Owl Router
 *
 * Routes user messages to the right specialist owl using LLM semantic
 * classification. Falls back to keyword matching if no classify fn provided.
 * Skips the LLM call entirely when no specialists are configured.
 */

import type { SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import type { ClassifyFn } from "./llm-classifier.js";
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
  | { type: "specialist"; owl: SpecializedOwl; reason: string; isFolderSpec?: boolean }
  | { type: "parliament"; reason: string };

interface RoutingTarget {
  name: string;
  routingRules: string[];
  expertiseDomains?: string[];
  routingQuality?: number;
  isFolderSpec?: boolean;
}

export class SecretaryRouter {
  private folderRegistry?: SpecializedOwlRegistry;
  private classify?: ClassifyFn;

  constructor(
    folderRegistry?: SpecializedOwlRegistry,
    classify?: ClassifyFn,
  ) {
    this.folderRegistry = folderRegistry;
    this.classify = classify;
  }

  private specToSyntheticOwl(spec: ReturnType<SpecializedOwlRegistry["listAll"]>[number], userId: string): SpecializedOwl {
    return {
      id: `folder-${spec.name}`,
      ownerId: userId,
      name: spec.name,
      specialization: spec.role,
      personalityPrompt: `You are ${spec.name}, ${spec.role}. Your expertise: ${spec.expertise.join(", ") || "general"}.`,
      routingRules: spec.routingRules.keywords,
      dna: { challengeLevel: 0.7, verbosity: 0.5, expertiseDomains: spec.expertise, routingQuality: 0.7, evolutionSpeed: 0.5 },
      isMainOwl: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  }

  async route(message: string, userId: string): Promise<RoutingDecision> {
    const folderSpecs = this.folderRegistry?.listAll() ?? [];

    if (folderSpecs.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    // ─── LLM semantic routing ────────────────────────────────────
    if (this.classify) {
      const specialists = folderSpecs.map((s) => ({ name: s.name, role: s.role, expertise: s.expertise }));
      let chosenName: string | null = null;
      try {
        chosenName = await this.classify(message, specialists);
      } catch {
        // fall through to keyword matching
      }

      if (chosenName) {
        const spec = folderSpecs.find((s) => s.name === chosenName);
        if (spec) {
          const decision = { type: "specialist" as const, owl: this.specToSyntheticOwl(spec, userId), isFolderSpec: true, reason: `LLM routed to: ${chosenName}` };
          log.engine.info(`[SecretaryRouter] LLM → "${chosenName}"`);
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }
        log.engine.warn(`[SecretaryRouter] LLM returned unrecognized specialist "${chosenName}" — falling through`);
      }

      if (this.shouldConveneParliament(message)) {
        const decision = { type: "parliament" as const, reason: "Complex query detected - convening parliament" };
        this.logRoutingDecision(userId, message, decision, "success");
        return decision;
      }
      const decision = { type: "direct" as const, reason: "LLM classified as no specialist" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    // ─── Keyword fallback ─────────────────────────────────────────
    const messageLower = message.toLowerCase();
    const targets: RoutingTarget[] = folderSpecs.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
      isFolderSpec: true,
    }));

    const matchedTarget = this.findBestMatch(messageLower, targets);
    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(`[SecretaryRouter] Keyword → ${matchedTarget.name} (confidence: ${confidence.toFixed(2)})`);
        const spec = this.folderRegistry?.get(matchedTarget.name);
        if (spec) {
          const decision = {
            type: "specialist" as const,
            owl: this.specToSyntheticOwl(spec, userId),
            isFolderSpec: true,
            reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
          };
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }
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
  private findBestMatch(message: string, targets: RoutingTarget[]): RoutingTarget | null {
    let bestMatch: RoutingTarget | null = null;
    let bestScore = 0;

    for (const target of targets) {
      const score = this.scoreMatch(message, target);
      if (score > bestScore) {
        bestScore = score;
        bestMatch = target;
      }
    }

    return bestScore >= MATCH_SCORE_THRESHOLD ? bestMatch : null;
  }

  /**
   * Score how well a message matches an owl's routing rules.
   */
  private scoreMatch(message: string, target: RoutingTarget): number {
    const rules = target.routingRules.map((r) => r.toLowerCase());
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
  private calculateConfidence(messageLower: string, target: RoutingTarget): number {
    const matchScore = this.scoreMatch(messageLower, target);
    const dnaScore = target.routingQuality ?? (target.isFolderSpec ? 0.7 : 0.5);
    return (matchScore * MATCH_WEIGHT) + (dnaScore * DNA_WEIGHT);
  }

  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;
    if (keywordCount >= 3) return true;
    if (keywordCount >= 2 && message.length > 200) return true;
    return false;
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
    log.engine.info(
      `[SecretaryRouter] Routing decision: ${JSON.stringify({
        userId,
        message: message.slice(0, 100),
        decisionType: decision.type,
        targetOwl: decision.type === "specialist" ? decision.owl.name : null,
        reason: decision.reason,
        outcome,
        timestamp: new Date().toISOString(),
      })}`,
    );
  }
}
