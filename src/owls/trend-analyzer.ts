/**
 * StackOwl — Evolution Trend Analyzer (Fix 10)
 *
 * Analyzes the evolution log to detect:
 *   1. Oscillation — same trait flipping back and forth
 *   2. Regression — satisfaction dropping after mutations
 *   3. Stagnation — no meaningful mutations for extended periods
 *   4. Effective mutation types — which kinds of changes correlate with improvement
 *
 * Acts as a GATE before the EvolutionEngine applies mutations.
 * If the analyzer detects a problem, it can freeze specific traits
 * or block the entire evolution pass.
 */

import type { OwlDNA, EvolutionEntry } from "./persona.js";
// logger reserved for future use

// ─── Types ───────────────────────────────────────────────────────

export interface TrendAnalysis {
  /** Should evolution proceed? */
  shouldEvolve: boolean;
  /** Traits that should be frozen (not mutated) */
  frozenTraits: string[];
  /** Mutation types to avoid (based on poor historical performance) */
  avoidMutationTypes: string[];
  /** Mutation types to prefer (based on good historical performance) */
  preferMutationTypes: string[];
  /** Human-readable explanation */
  reasoning: string;
  /** Overall DNA health score (0-1) */
  healthScore: number;
}

export interface TraitHistory {
  trait: string;
  values: string[];
  timestamps: string[];
  isOscillating: boolean;
}

// ─── Analyzer ────────────────────────────────────────────────────

export class EvolutionTrendAnalyzer {
  /** Minimum entries needed for analysis */
  private static readonly MIN_ENTRIES = 4;
  /** Oscillation detection window */
  private static readonly OSCILLATION_WINDOW = 6;
  /** Effectiveness threshold: below this, mutations are questionable */
  private static readonly EFFECTIVENESS_FLOOR = 0.3;
  /** Stagnation: if no mutations for this many weeks, flag it */
  private static readonly STAGNATION_WEEKS = 4;

  /**
   * Analyze the evolution log and recommend whether to proceed.
   * Call this BEFORE running the evolution engine.
   */
  analyze(dna: OwlDNA): TrendAnalysis {
    const entries = dna.evolutionLog;

    if (entries.length < EvolutionTrendAnalyzer.MIN_ENTRIES) {
      return {
        shouldEvolve: true,
        frozenTraits: [],
        avoidMutationTypes: [],
        preferMutationTypes: [],
        reasoning: `Only ${entries.length} evolution entries — not enough data for trend analysis. Proceeding.`,
        healthScore: 0.5,
      };
    }

    const frozenTraits: string[] = [];
    const avoidTypes: string[] = [];
    const preferTypes: string[] = [];
    const reasons: string[] = [];
    let healthScore = 0.7; // Start optimistic

    // 1. Detect oscillation
    const oscillations = this.detectOscillation(entries);
    for (const osc of oscillations) {
      if (osc.isOscillating) {
        frozenTraits.push(osc.trait);
        reasons.push(
          `"${osc.trait}" is oscillating (values: ${osc.values.slice(-4).join(" → ")})`,
        );
        healthScore -= 0.1;
      }
    }

    // 2. Detect regression (declining effectiveness)
    const regression = this.detectRegression(entries);
    if (regression.isRegressing) {
      healthScore -= 0.2;
      reasons.push(`Effectiveness declining: ${regression.trend}`);
    }

    // 3. Detect stagnation
    const stagnation = this.detectStagnation(dna);
    if (stagnation.isStagnating) {
      reasons.push(
        `No evolution for ${stagnation.weeksSinceLastEvolution} weeks — consider triggering manual review`,
      );
      healthScore -= 0.05; // Mild concern
    }

    // 4. Classify mutation types by effectiveness
    const typeAnalysis = this.analyzeMutationTypes(entries);
    for (const [type, avg] of typeAnalysis) {
      if (avg < EvolutionTrendAnalyzer.EFFECTIVENESS_FLOOR) {
        avoidTypes.push(type);
        reasons.push(
          `"${type}" mutations average ${(avg * 100).toFixed(0)}% effectiveness — avoid`,
        );
      } else if (avg > 0.7) {
        preferTypes.push(type);
      }
    }

    // 5. Decision: should we evolve?
    const shouldEvolve = healthScore > 0.3 && frozenTraits.length < 3;
    if (!shouldEvolve) {
      reasons.push(
        "Evolution PAUSED — too many issues detected. Allow stabilization.",
      );
    }

    return {
      shouldEvolve,
      frozenTraits,
      avoidMutationTypes: avoidTypes,
      preferMutationTypes: preferTypes,
      reasoning: reasons.join("; "),
      healthScore: Math.max(0, Math.min(1, healthScore)),
    };
  }

  /**
   * Generate a guard prompt for the evolution LLM.
   * Injects frozen traits and avoid/prefer directives.
   */
  toGuardPrompt(analysis: TrendAnalysis): string {
    const lines: string[] = [];

    if (analysis.frozenTraits.length > 0) {
      lines.push(
        `FROZEN TRAITS (DO NOT MUTATE): ${analysis.frozenTraits.join(", ")}. ` +
          `These traits have been oscillating — leave them unchanged.`,
      );
    }

    if (analysis.avoidMutationTypes.length > 0) {
      lines.push(
        `AVOID these mutation types (historically ineffective): ${analysis.avoidMutationTypes.join(", ")}`,
      );
    }

    if (analysis.preferMutationTypes.length > 0) {
      lines.push(
        `PREFER these mutation types (historically effective): ${analysis.preferMutationTypes.join(", ")}`,
      );
    }

    return lines.join("\n");
  }

  // ─── Oscillation Detection ─────────────────────────────────────

  private detectOscillation(entries: EvolutionEntry[]): TraitHistory[] {
    const window = entries.slice(-EvolutionTrendAnalyzer.OSCILLATION_WINDOW);
    const traitTimeline = new Map<
      string,
      { values: string[]; timestamps: string[] }
    >();

    for (const entry of window) {
      for (const mutation of entry.mutations) {
        const parsed = this.parseMutation(mutation);
        if (!parsed) continue;

        const existing = traitTimeline.get(parsed.trait) ?? {
          values: [],
          timestamps: [],
        };
        existing.values.push(parsed.newValue);
        existing.timestamps.push(entry.timestamp);
        traitTimeline.set(parsed.trait, existing);
      }
    }

    const results: TraitHistory[] = [];
    for (const [trait, timeline] of traitTimeline) {
      const isOscillating = this.isOscillatingSequence(timeline.values);
      results.push({
        trait,
        values: timeline.values,
        timestamps: timeline.timestamps,
        isOscillating,
      });
    }

    return results;
  }

  private isOscillatingSequence(values: string[]): boolean {
    if (values.length < 3) return false;

    let flips = 0;
    for (let i = 2; i < values.length; i++) {
      // A→B→A pattern (value returns to previous state)
      if (values[i] === values[i - 2] && values[i] !== values[i - 1]) {
        flips++;
      }
    }

    return flips >= 1;
  }

  // ─── Regression Detection ──────────────────────────────────────

  private detectRegression(entries: EvolutionEntry[]): {
    isRegressing: boolean;
    trend: string;
  } {
    const withEffectiveness = entries.filter(
      (e) => e.effectiveness !== undefined,
    );
    if (withEffectiveness.length < 4) {
      return { isRegressing: false, trend: "insufficient data" };
    }

    const recent = withEffectiveness.slice(-5);
    const earlier = withEffectiveness.slice(-10, -5);

    if (earlier.length === 0) {
      return { isRegressing: false, trend: "insufficient history" };
    }

    const recentAvg =
      recent.reduce((s, e) => s + (e.effectiveness ?? 0), 0) / recent.length;
    const earlierAvg =
      earlier.reduce((s, e) => s + (e.effectiveness ?? 0), 0) / earlier.length;
    const delta = recentAvg - earlierAvg;

    if (delta < -0.1) {
      return {
        isRegressing: true,
        trend: `${(earlierAvg * 100).toFixed(0)}% → ${(recentAvg * 100).toFixed(0)}% (Δ${(delta * 100).toFixed(0)}%)`,
      };
    }

    return {
      isRegressing: false,
      trend: `stable at ${(recentAvg * 100).toFixed(0)}%`,
    };
  }

  // ─── Stagnation Detection ──────────────────────────────────────

  private detectStagnation(dna: OwlDNA): {
    isStagnating: boolean;
    weeksSinceLastEvolution: number;
  } {
    if (!dna.lastEvolved) {
      return { isStagnating: true, weeksSinceLastEvolution: 999 };
    }

    const daysSince =
      (Date.now() - new Date(dna.lastEvolved).getTime()) /
      (1000 * 60 * 60 * 24);
    const weeks = Math.floor(daysSince / 7);

    return {
      isStagnating: weeks >= EvolutionTrendAnalyzer.STAGNATION_WEEKS,
      weeksSinceLastEvolution: weeks,
    };
  }

  // ─── Mutation Type Analysis ────────────────────────────────────

  private analyzeMutationTypes(entries: EvolutionEntry[]): Map<string, number> {
    const typeScores = new Map<string, number[]>();

    for (const entry of entries) {
      if (entry.effectiveness === undefined) continue;

      for (const mutation of entry.mutations) {
        const type = this.classifyMutationType(mutation);
        const existing = typeScores.get(type) ?? [];
        existing.push(entry.effectiveness);
        typeScores.set(type, existing);
      }
    }

    const averages = new Map<string, number>();
    for (const [type, scores] of typeScores) {
      if (scores.length < 2) continue; // Need at least 2 observations
      const avg = scores.reduce((s, v) => s + v, 0) / scores.length;
      averages.set(type, avg);
    }

    return averages;
  }

  // ─── Helpers ───────────────────────────────────────────────────

  private parseMutation(
    mutation: string,
  ): { trait: string; newValue: string } | null {
    // "Verbosity changed: balanced -> concise"
    const changeMatch = mutation.match(/(.+?) changed: .+ -> (.+)/i);
    if (changeMatch) {
      return {
        trait: changeMatch[1].trim().toLowerCase(),
        newValue: changeMatch[2].trim(),
      };
    }

    // "Learned preference: prefers_rust = 0.9"
    const prefMatch = mutation.match(/Learned preference: (.+?) = (.+)/i);
    if (prefMatch) {
      return {
        trait: `pref:${prefMatch[1].trim()}`,
        newValue: prefMatch[2].trim(),
      };
    }

    // "Grew expertise in rust_macros (+0.1)"
    const expertiseMatch = mutation.match(/Grew expertise in (.+?) \(/i);
    if (expertiseMatch) {
      return {
        trait: `expertise:${expertiseMatch[1].trim()}`,
        newValue: "grew",
      };
    }

    return null;
  }

  private classifyMutationType(mutation: string): string {
    const lower = mutation.toLowerCase();
    if (lower.includes("verbosity")) return "verbosity";
    if (lower.includes("challenge")) return "challenge";
    if (lower.includes("preference")) return "preference";
    if (lower.includes("expertise")) return "expertise";
    if (lower.includes("humor")) return "humor";
    if (lower.includes("formality")) return "formality";
    return "other";
  }
}
