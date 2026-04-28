/**
 * StackOwl — Secretary Owl Router
 *
 * The Secretary Owl acts as a mandatory facade for all user messages.
 * It decides whether to:
 * - Answer directly as a generalist
 * - Route to a specialized owl
 * - Convene parliament for complex queries
 */

import type { MemoryDatabase, SpecializedOwl } from "../memory/db.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
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
  private db: MemoryDatabase;
  private folderRegistry?: SpecializedOwlRegistry;

  constructor(db: MemoryDatabase, folderRegistry?: SpecializedOwlRegistry) {
    this.db = db;
    this.folderRegistry = folderRegistry;
  }

  private toRoutingTarget(owl: SpecializedOwl): RoutingTarget {
    return {
      name: owl.name,
      routingRules: owl.routingRules,
      expertiseDomains: owl.dna?.expertiseDomains,
      routingQuality: owl.dna?.routingQuality,
    };
  }

  /**
   * Decide how to route the incoming message.
   * All messages go through the Secretary Owl first.
   */
  route(
    message: string,
    userId: string,
  ): RoutingDecision {
    const dbOwls = this.db.owls.getByOwner(userId);
    const folderSpecs = this.folderRegistry?.listAll() ?? [];

    if (dbOwls.length === 0 && folderSpecs.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    const messageLower = message.toLowerCase();

    const dbTargets = dbOwls.map((owl) => this.toRoutingTarget(owl));
    const folderTargets: RoutingTarget[] = folderSpecs.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
      isFolderSpec: true,
    }));

    const allTargets = [...dbTargets, ...folderTargets];
    const matchedTarget = this.findBestMatch(messageLower, allTargets);

    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(
          `[SecretaryRouter] Routing to ${matchedTarget.name} (confidence: ${confidence.toFixed(2)}, folder=${matchedTarget.isFolderSpec ?? false})`,
        );

        if (matchedTarget.isFolderSpec) {
          const spec = this.folderRegistry?.get(matchedTarget.name);
          const syntheticOwl: SpecializedOwl = {
            id: `folder-${matchedTarget.name}`,
            ownerId: userId,
            name: matchedTarget.name,
            specialization: spec?.role ?? matchedTarget.name,
            personalityPrompt: `You are ${matchedTarget.name}, ${spec?.role ?? "a specialized assistant"}. Your expertise: ${(matchedTarget.expertiseDomains ?? []).join(", ") || "general"}.`,
            routingRules: matchedTarget.routingRules,
            dna: {
              challengeLevel: 0.7,
              verbosity: 0.5,
              expertiseDomains: matchedTarget.expertiseDomains ?? [],
              routingQuality: 0.7,
              evolutionSpeed: 0.5,
            },
            isMainOwl: false,
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          };
          const decision = {
            type: "specialist" as const,
            owl: syntheticOwl,
            isFolderSpec: true,
            reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
          };
          this.logRoutingDecision(userId, message, decision, "success");
          return decision;
        }

        const matchedDbOwl = dbOwls.find((o) => o.name === matchedTarget.name);
        if (!matchedDbOwl) {
          log.engine.warn(`[SecretaryRouter] Matched target "${matchedTarget.name}" not found in dbOwls — falling back to direct`);
          const fallback = { type: "direct" as const, reason: "Matched owl not found in DB" };
          this.logRoutingDecision(userId, message, fallback, "failure");
          return fallback;
        }
        const decision = {
          type: "specialist" as const,
          owl: matchedDbOwl,
          reason: `Matched routing rules: ${matchedTarget.routingRules.slice(0, 3).join(", ")}`,
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

  /**
   * Check if parliament should be convened.
   * Uses keyword-based heuristic for fast path.
   */
  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;

    if (keywordCount >= 3) {
      return true;
    }

    if (keywordCount >= 2 && message.length > 200) {
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