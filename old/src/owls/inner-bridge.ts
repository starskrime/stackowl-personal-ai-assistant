/**
 * StackOwl — Inner Life → DNA Bridge
 *
 * Connects the owl's inner life (opinions, desires, moods) to
 * concrete DNA mutations. Without this, inner life is decorative —
 * the owl forms opinions but they never influence future behavior.
 *
 * Feedback loops:
 *   1. Opinions → dna.learnedPreferences (opinion confidence → preference strength)
 *   2. Desires → dna.expertiseGrowth (desires about topics → expertise signals)
 *   3. Mood patterns → dna.evolvedTraits (sustained frustration → lower challenge)
 *   4. Unspoken observations → proactive planner hints
 */

import type { OwlInnerState } from "./inner-life.js";
import type { OwlDNA } from "./persona.js";
import type { OwlRegistry } from "./registry.js";
import { log } from "../logger.js";

// ─── Feedback Result ─────────────────────────────────────────────

export interface InnerLifeFeedback {
  preferencesUpdated: string[];
  expertiseSignals: string[];
  traitAdjustments: string[];
  proactiveHints: string[];
}

// ─── Bridge ──────────────────────────────────────────────────────

export class InnerLifeDNABridge {
  constructor(private owlRegistry: OwlRegistry) {}

  /**
   * Sync inner life state into DNA. Call this after each conversation
   * or during periodic reflection.
   *
   * This is the critical missing link — without it, inner life
   * accumulates state that never affects the owl's actual behavior.
   */
  async sync(
    owlName: string,
    innerState: OwlInnerState,
  ): Promise<InnerLifeFeedback> {
    const owl = this.owlRegistry.get(owlName);
    if (!owl) {
      log.evolution.warn(`[InnerLifeBridge] Owl "${owlName}" not found`);
      return {
        preferencesUpdated: [],
        expertiseSignals: [],
        traitAdjustments: [],
        proactiveHints: [],
      };
    }

    const feedback: InnerLifeFeedback = {
      preferencesUpdated: [],
      expertiseSignals: [],
      traitAdjustments: [],
      proactiveHints: [],
    };

    // 1. Opinions → Learned Preferences
    this.syncOpinions(owl.dna, innerState, feedback);

    // 2. Desires → Expertise Growth signals
    this.syncDesires(owl.dna, innerState, feedback);

    // 3. Mood Patterns → Trait Adjustments
    this.syncMood(owl.dna, innerState, feedback);

    // 4. Unspoken Observations → Proactive Hints
    this.extractProactiveHints(innerState, feedback);

    // Persist if anything changed
    if (
      feedback.preferencesUpdated.length > 0 ||
      feedback.expertiseSignals.length > 0 ||
      feedback.traitAdjustments.length > 0
    ) {
      await this.owlRegistry.saveDNA(owlName);
      log.evolution.info(
        `[InnerLifeBridge] Synced ${owlName}: ` +
          `${feedback.preferencesUpdated.length} prefs, ` +
          `${feedback.expertiseSignals.length} expertise, ` +
          `${feedback.traitAdjustments.length} traits`,
      );
    }

    return feedback;
  }

  // ─── Opinion → Preference Sync ─────────────────────────────────

  private syncOpinions(
    dna: OwlDNA,
    state: OwlInnerState,
    feedback: InnerLifeFeedback,
  ): void {
    for (const opinion of state.opinions) {
      if (opinion.confidence < 0.5) continue; // Only strong opinions matter

      // Convert opinion to a preference key
      const prefKey = this.opinionToPreferenceKey(
        opinion.topic,
        opinion.stance,
      );
      if (!prefKey) continue;

      const currentPref = dna.learnedPreferences[prefKey] ?? 0.5;

      // Positive stance → increase preference, negative → decrease
      const isPositive = this.isPositiveStance(opinion.stance);
      const delta = opinion.confidence * 0.1 * (isPositive ? 1 : -1);
      const newValue = Math.max(0.05, Math.min(0.95, currentPref + delta));

      if (Math.abs(newValue - currentPref) > 0.02) {
        dna.learnedPreferences[prefKey] = newValue;
        feedback.preferencesUpdated.push(
          `${prefKey}: ${currentPref.toFixed(2)} → ${newValue.toFixed(2)} (from opinion: "${opinion.stance.slice(0, 50)}")`,
        );
      }
    }
  }

  // ─── Desire → Expertise Sync ───────────────────────────────────

  private syncDesires(
    dna: OwlDNA,
    state: OwlInnerState,
    feedback: InnerLifeFeedback,
  ): void {
    for (const desire of state.desires) {
      if (desire.intensity < 0.4) continue;

      // Extract domain keywords from desire description
      const domains = this.extractDomains(desire.description);

      for (const domain of domains) {
        const current = dna.expertiseGrowth[domain] ?? 0;
        // Desires signal interest, not mastery — small boost
        const boost = desire.intensity * 0.03;
        const newValue = Math.min(0.95, current + boost);

        if (newValue > current + 0.01) {
          dna.expertiseGrowth[domain] = newValue;
          feedback.expertiseSignals.push(
            `${domain}: +${boost.toFixed(3)} (desire: "${desire.description.slice(0, 40)}")`,
          );
        }
      }
    }
  }

  // ─── Mood → Trait Sync ─────────────────────────────────────────

  private syncMood(
    dna: OwlDNA,
    state: OwlInnerState,
    feedback: InnerLifeFeedback,
  ): void {
    const mood = state.mood;
    if (mood.intensity < 0.6) return; // Only strong moods affect traits

    // Sustained frustration → reduce challenge level
    if (mood.current === "frustrated" && mood.intensity > 0.7) {
      const challengeLevels: Array<OwlDNA["evolvedTraits"]["challengeLevel"]> =
        ["low", "medium", "high", "relentless"];
      const currentIdx = challengeLevels.indexOf(
        dna.evolvedTraits.challengeLevel,
      );
      if (currentIdx > 0) {
        const newLevel = challengeLevels[currentIdx - 1];
        feedback.traitAdjustments.push(
          `challengeLevel: ${dna.evolvedTraits.challengeLevel} → ${newLevel} (sustained frustration)`,
        );
        dna.evolvedTraits.challengeLevel = newLevel;
      }
    }

    // Sustained excitement about a topic → slight verbosity increase
    if (mood.current === "excited" && mood.intensity > 0.7) {
      if (dna.evolvedTraits.verbosity === "concise") {
        // Don't override — user explicitly wants concise
      } else {
        // Boost humor slightly when excited
        dna.evolvedTraits.humor = Math.min(
          0.95,
          dna.evolvedTraits.humor + 0.05,
        );
        feedback.traitAdjustments.push(`humor: +0.05 (excited mood)`);
      }
    }

    // Contemplative mood → increase formality slightly
    if (mood.current === "contemplative" && mood.intensity > 0.6) {
      dna.evolvedTraits.formality = Math.min(
        0.95,
        dna.evolvedTraits.formality + 0.03,
      );
      feedback.traitAdjustments.push(`formality: +0.03 (contemplative mood)`);
    }

    // Playful mood → boost humor
    if (mood.current === "playful" && mood.intensity > 0.5) {
      dna.evolvedTraits.humor = Math.min(0.95, dna.evolvedTraits.humor + 0.05);
      feedback.traitAdjustments.push(`humor: +0.05 (playful mood)`);
    }
  }

  // ─── Observations → Proactive Hints ────────────────────────────

  private extractProactiveHints(
    state: OwlInnerState,
    feedback: InnerLifeFeedback,
  ): void {
    // Recent observations are potential proactive action triggers
    for (const obs of state.unspokenObservations.slice(-3)) {
      if (obs.length > 20) {
        feedback.proactiveHints.push(obs);
      }
    }
  }

  // ─── Helpers ───────────────────────────────────────────────────

  private opinionToPreferenceKey(
    topic: string,
    _stance: string,
  ): string | null {
    // Normalize topic to a preference key
    const normalized = topic
      .toLowerCase()
      .replace(/[^a-z0-9_\s]/g, "")
      .trim()
      .replace(/\s+/g, "_")
      .slice(0, 40);

    return normalized.length > 2 ? normalized : null;
  }

  private isPositiveStance(stance: string): boolean {
    const lower = stance.toLowerCase();
    const positivePatterns = [
      "good",
      "great",
      "prefer",
      "like",
      "love",
      "best",
      "excellent",
      "recommend",
      "should",
      "better",
      "effective",
      "useful",
      "powerful",
    ];
    const negativePatterns = [
      "bad",
      "avoid",
      "dislike",
      "hate",
      "worst",
      "poor",
      "terrible",
      "shouldn't",
      "overrated",
      "waste",
      "unnecessary",
      "harmful",
    ];

    const positiveHits = positivePatterns.filter((p) =>
      lower.includes(p),
    ).length;
    const negativeHits = negativePatterns.filter((p) =>
      lower.includes(p),
    ).length;

    return positiveHits >= negativeHits;
  }

  private extractDomains(text: string): string[] {
    const domainPatterns: Array<[RegExp, string]> = [
      [/\b(?:typescript|ts)\b/i, "typescript"],
      [/\b(?:javascript|js|node)\b/i, "javascript"],
      [/\b(?:python|py)\b/i, "python"],
      [/\b(?:rust)\b/i, "rust"],
      [/\b(?:devops|deploy|ci\/?cd)\b/i, "devops"],
      [/\b(?:docker|container)\b/i, "docker"],
      [/\b(?:kubernetes|k8s)\b/i, "kubernetes"],
      [/\b(?:machine learning|ml|ai|model)\b/i, "machine_learning"],
      [/\b(?:data|analytics|dashboard)\b/i, "data_analysis"],
      [/\b(?:finance|market|trading|crypto)\b/i, "finance"],
      [/\b(?:security|auth|crypto)\b/i, "security"],
      [/\b(?:design|ui|ux|frontend)\b/i, "design"],
      [/\b(?:database|sql|postgres|mongo)\b/i, "database"],
      [/\b(?:api|rest|graphql)\b/i, "api_design"],
      [/\b(?:anticipat|predict|proactive)\b/i, "anticipation"],
      [/\b(?:understand|learn|knowledge)\b/i, "learning"],
      [/\b(?:communicat|trust|relationship)\b/i, "communication"],
    ];

    const found: string[] = [];
    for (const [pattern, domain] of domainPatterns) {
      if (pattern.test(text)) found.push(domain);
    }

    return found;
  }
}
