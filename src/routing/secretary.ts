/**
 * StackOwl — Secretary Owl Router
 *
 * Routes user messages to the right specialist owl using keyword matching.
 * Convenes Parliament for complex multi-faceted queries.
 * Skips routing entirely when no specialists are configured.
 */

import type { SpecializedOwlSpec } from "../owls/specialized-types.js";
import type { SpecializedOwlRegistry } from "../owls/specialized-registry.js";
import { log } from "../logger.js";

const MIN_MESSAGE_LENGTH = 10;
const ROUTING_CONFIDENCE_THRESHOLD = 0.4;
const MATCH_SCORE_THRESHOLD = 0.25;
const MATCH_WEIGHT = 0.7;
const DNA_WEIGHT = 0.3;
const DOMAIN_SIGNAL_BOOST = 0.15;
const FACT_SIGNAL_BOOST   = 0.25;
// Must be >= MATCH_SCORE_THRESHOLD so a single explicit fact mention routes even when keywords score 0

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
  | { type: "specialist"; owl: SpecializedOwlSpec; reason: string }
  | { type: "parliament"; reason: string };

interface RoutingTarget {
  name: string;
  routingRules: string[];
  expertiseDomains?: string[];
  routingQuality?: number;
}

export class SecretaryRouter {
  private folderRegistry?: SpecializedOwlRegistry;

  constructor(
    folderRegistry?: SpecializedOwlRegistry,
  ) {
    this.folderRegistry = folderRegistry;
  }

  async route(message: string, userId: string): Promise<RoutingDecision> {
    const specialists = this.folderRegistry?.listSpecialists() ?? [];

    if (specialists.length === 0) {
      const decision = { type: "direct" as const, reason: "No specialized owls configured" };
      this.logRoutingDecision(userId, message, decision, "success");
      return decision;
    }

    // ─── Keyword fallback ─────────────────────────────────────────
    const messageLower = message.toLowerCase();
    const targets: RoutingTarget[] = specialists.map((spec) => ({
      name: spec.name,
      routingRules: spec.routingRules.keywords,
      expertiseDomains: spec.expertise,
    }));

    const matchedTarget = this.findBestMatch(messageLower, targets);
    if (matchedTarget && message.length >= MIN_MESSAGE_LENGTH) {
      const confidence = this.calculateConfidence(messageLower, matchedTarget);
      if (confidence >= ROUTING_CONFIDENCE_THRESHOLD) {
        log.engine.info(`[SecretaryRouter] Keyword → ${matchedTarget.name} (confidence: ${confidence.toFixed(2)})`);
        const spec = specialists.find((s) => s.name === matchedTarget.name);
        if (spec) {
          const decision = {
            type: "specialist" as const,
            owl: spec,
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

  async routeWithSignals(
    message: string,
    _userId: string,
    signals: import("./user-profile-service.js").RoutingSignals,
  ): Promise<RoutingDecision> {
    const specialists = this.folderRegistry?.listSpecialists() ?? [];
    if (specialists.length === 0) {
      return { type: "direct", reason: "No specialized owls configured" };
    }
    if (message.length < MIN_MESSAGE_LENGTH) {
      return { type: "direct", reason: "Message too short to classify" };
    }

    const cappedDomains = signals.domainStack.slice(0, 10);
    const cappedFacts = signals.relevantFacts.slice(0, 10);

    // Score each specialist with signal boosts
    const scored = specialists.map((spec) => {
      let score = this.computeKeywordScore(message, spec);

      // Domain signal boost: active goals overlapping with owl's expertise
      for (const domain of cappedDomains) {
        const domainLower = domain.toLowerCase();
        const domainTokens = domainLower.split(/\s+/).filter(t => t.length > 2);
        if (domainTokens.length > 0 && spec.expertise.some((e) =>
          domainTokens.some((token) => e.toLowerCase().includes(token)) ||
          domainLower.includes(e.toLowerCase())
        )) {
          score += DOMAIN_SIGNAL_BOOST;
        }
      }

      // Fact signal boost: facts that mention this owl by name
      for (const fact of cappedFacts) {
        if (fact.toLowerCase().includes(spec.name.toLowerCase())) {
          score += FACT_SIGNAL_BOOST;
        }
      }

      return { spec, score };
    });

    scored.sort((a, b) => b.score - a.score);
    const best = scored[0];

    if (best.score >= MATCH_SCORE_THRESHOLD) {
      log.engine.info(`[SecretaryRouter] routeWithSignals → "${best.spec.name}" (score=${best.score.toFixed(2)})`);
      return { type: "specialist", owl: best.spec, reason: `score=${best.score.toFixed(2)}` };
    }

    // Parliament detection (same logic as route())
    if (this.shouldConveneParliament(message)) {
      return { type: "parliament", reason: "parliament keyword matched" };
    }

    return { type: "direct", reason: `max score ${best.score.toFixed(2)} below threshold` };
  }

  private computeKeywordScore(message: string, spec: SpecializedOwlSpec): number {
    const lowerMsg = message.toLowerCase();
    const keywords = spec.routingRules?.keywords ?? [];
    const expertise = spec.expertise ?? [];
    const allKeywords = [...keywords, ...expertise];
    if (allKeywords.length === 0) return 0;
    const matchCount = allKeywords.filter((kw) => lowerMsg.includes(kw.toLowerCase())).length;
    const matchRatio = matchCount / allKeywords.length;
    return matchRatio * MATCH_WEIGHT;
  }

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

  private calculateConfidence(messageLower: string, target: RoutingTarget): number {
    const matchScore = this.scoreMatch(messageLower, target);
    const dnaScore = target.routingQuality ?? 0.7;
    return (matchScore * MATCH_WEIGHT) + (dnaScore * DNA_WEIGHT);
  }

  private shouldConveneParliament(message: string): boolean {
    const lower = message.toLowerCase();
    const keywordCount = PARLIAMENT_KEYWORDS.filter((kw) => lower.includes(kw)).length;
    if (keywordCount >= 3) return true;
    if (keywordCount >= 2 && message.length > 200) return true;
    return false;
  }

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
