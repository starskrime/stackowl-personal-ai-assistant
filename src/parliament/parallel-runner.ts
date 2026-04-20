/**
 * StackOwl — Parallel Parliament Runner
 *
 * Replaces the sequential debate with true parallel execution.
 * All owl positions are gathered in a single Promise.all, then each owl
 * scores the others (again in parallel) to produce a convergence result.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface ParliamentPosition {
  owlName: string;
  stance: string;
  reasoning: string;
  confidence: number; // 0-1
  evidence: string[];
}

export interface ConvergenceResult {
  majorityView: string;
  minorityViews: string[];
  consensusScore: number; // 0-1, how much agreement
  finalSynthesis: string;
  positions: ParliamentPosition[];
  shouldSavePellet: boolean;
}

interface OwlScoring {
  owlName: string;
  scores: Record<string, number>; // owlName → score 0-1
  agreedWith: string[];
}

// ─── Runner ───────────────────────────────────────────────────────

export class ParallelParliamentRunner {
  private readonly maxOwls: number;
  private readonly timeoutMs: number;

  constructor(
    private provider: ModelProvider,
    config?: { maxOwls?: number; timeoutMs?: number },
  ) {
    this.maxOwls = config?.maxOwls ?? 5;
    this.timeoutMs = config?.timeoutMs ?? 30_000;
  }

  /**
   * Phase 1: Run all owl position prompts in true parallel via Promise.all.
   * If any single owl times out (> 30 s) it gets a neutral fallback position.
   */
  async runPositions(
    topic: string,
    owlPersonas: string[],
  ): Promise<ParliamentPosition[]> {
    const personas = owlPersonas.slice(0, this.maxOwls);

    log.engine.info(
      `[ParallelParliament] Gathering positions from ${personas.length} owls on: "${topic.slice(0, 80)}"`,
    );

    const positionPromises = personas.map((persona) =>
      this.fetchPosition(topic, persona),
    );

    const settled = await Promise.allSettled(positionPromises);

    const positions: ParliamentPosition[] = settled.map((result, idx) => {
      if (result.status === "fulfilled") {
        return result.value;
      }
      // Timed out or errored → neutral fallback
      log.engine.info(
        `[ParallelParliament] Owl "${personas[idx]}" failed/timed out — using neutral position`,
      );
      return {
        owlName: personas[idx],
        stance: "neutral",
        reasoning: "Unable to form a position in time.",
        confidence: 0.5,
        evidence: [],
      };
    });

    return positions;
  }

  /**
   * Phase 2: Each owl reads ALL positions and scores them.
   * Tallied scores identify the majority view.
   */
  async runConvergence(
    topic: string,
    positions: ParliamentPosition[],
  ): Promise<ConvergenceResult> {
    log.engine.info(
      `[ParallelParliament] Running convergence across ${positions.length} positions`,
    );

    // Each owl scores the others in parallel
    const scoringPromises = positions.map((pos) =>
      this.fetchScoring(topic, pos.owlName, positions),
    );

    const scoringSettled = await Promise.allSettled(scoringPromises);
    const scorings: OwlScoring[] = scoringSettled
      .map((result, idx) => {
        if (result.status === "fulfilled") return result.value;
        // Fallback: give everyone equal scores
        const equalScores: Record<string, number> = {};
        positions.forEach((p) => {
          equalScores[p.owlName] = 0.5;
        });
        return {
          owlName: positions[idx].owlName,
          scores: equalScores,
          agreedWith: [],
        };
      });

    // Tally: sum scores each owl received from all others
    const totalScores: Record<string, number> = {};
    positions.forEach((p) => (totalScores[p.owlName] = 0));

    for (const scoring of scorings) {
      for (const [name, score] of Object.entries(scoring.scores)) {
        if (name in totalScores) {
          totalScores[name] += score;
        }
      }
    }

    // Highest total score = majority view
    const ranked = Object.entries(totalScores).sort((a, b) => b[1] - a[1]);
    const majorityOwlName = ranked[0][0];
    const majorityPosition = positions.find(
      (p) => p.owlName === majorityOwlName,
    )!;
    const minorityPositions = positions.filter(
      (p) => p.owlName !== majorityOwlName,
    );

    // Consensus score: std-deviation proxy — how concentrated are the top scores?
    const scoreValues = ranked.map(([, s]) => s);
    const maxScore = scoreValues[0] ?? 1;
    const minScore = scoreValues[scoreValues.length - 1] ?? 0;
    const range = maxScore - minScore;
    // Low range → high consensus; high range → low consensus
    const consensusScore = Math.max(0, 1 - range / (maxScore || 1));

    // Final synthesis via a single LLM call
    const finalSynthesis = await this.synthesize(topic, positions, majorityPosition);

    const shouldSavePellet =
      consensusScore > 0.6 || positions.length >= 3;

    return {
      majorityView: majorityPosition.stance,
      minorityViews: minorityPositions.map((p) => p.stance),
      consensusScore,
      finalSynthesis,
      positions,
      shouldSavePellet,
    };
  }

  /**
   * Full parliament session: positions → convergence.
   */
  async run(topic: string, owlPersonas: string[]): Promise<ConvergenceResult> {
    const positions = await this.runPositions(topic, owlPersonas);
    return this.runConvergence(topic, positions);
  }

  /**
   * Auto-trigger check: should this topic go to parliament?
   * Returns true if confidence < 0.6 OR topic contains contested keywords.
   */
  static shouldTrigger(topic: string, owlConfidence?: number): boolean {
    if (owlConfidence !== undefined && owlConfidence < 0.6) return true;

    const contested = [
      "should",
      "best way",
      "which is better",
      "tradeoff",
      "trade-off",
      " vs ",
      " vs.",
      "compare",
      "versus",
      "pros and cons",
      "recommend",
      "alternative",
    ];

    const lower = topic.toLowerCase();
    return contested.some((kw) => lower.includes(kw));
  }

  // ─── Private helpers ──────────────────────────────────────────────

  private async fetchPosition(
    topic: string,
    persona: string,
  ): Promise<ParliamentPosition> {
    const messages: ChatMessage[] = [
      {
        role: "system",
        content: `You are ${persona}. Respond ONLY with valid JSON — no markdown fences, no extra text.`,
      },
      {
        role: "user",
        content: `On the topic: "${topic}"

State your position with evidence. Be specific and direct.
Output JSON exactly in this shape:
{
  "stance": "your clear position in 1-2 sentences",
  "reasoning": "your reasoning in 2-4 sentences",
  "confidence": 0.85,
  "evidence": ["point 1", "point 2", "point 3"]
}`,
      },
    ];

    const raceResult = await Promise.race([
      this.provider.chat(messages),
      new Promise<never>((_, reject) =>
        setTimeout(
          () => reject(new Error("Position fetch timed out")),
          this.timeoutMs,
        ),
      ),
    ]);

    const raw = raceResult.content.trim();
    const parsed = this.parseJson<{
      stance: string;
      reasoning: string;
      confidence: number;
      evidence: string[];
    }>(raw);

    return {
      owlName: persona,
      stance: parsed?.stance ?? raw.slice(0, 200),
      reasoning: parsed?.reasoning ?? "",
      confidence: typeof parsed?.confidence === "number"
        ? Math.min(1, Math.max(0, parsed.confidence))
        : 0.5,
      evidence: Array.isArray(parsed?.evidence) ? parsed.evidence : [],
    };
  }

  private async fetchScoring(
    topic: string,
    scorerPersona: string,
    positions: ParliamentPosition[],
  ): Promise<OwlScoring> {
    const positionsSummary = positions
      .map(
        (p) =>
          `${p.owlName}: "${p.stance}" (confidence: ${p.confidence.toFixed(2)})`,
      )
      .join("\n");

    const messages: ChatMessage[] = [
      {
        role: "system",
        content: `You are ${scorerPersona}. Respond ONLY with valid JSON — no markdown fences, no extra text.`,
      },
      {
        role: "user",
        content: `Topic: "${topic}"

Here are all positions from the parliament:
${positionsSummary}

Score each position 0.0–1.0 based on how well-reasoned and correct you believe it is.
Also list which owls you most agree with.
Output JSON exactly in this shape (include all owl names):
{
  "scores": {
    ${positions.map((p) => `"${p.owlName}": 0.75`).join(",\n    ")}
  },
  "agreedWith": ["OwlName1", "OwlName2"]
}`,
      },
    ];

    try {
      const raceResult = await Promise.race([
        this.provider.chat(messages),
        new Promise<never>((_, reject) =>
          setTimeout(
            () => reject(new Error("Scoring timed out")),
            this.timeoutMs,
          ),
        ),
      ]);

      const parsed = this.parseJson<{
        scores: Record<string, number>;
        agreedWith: string[];
      }>(raceResult.content.trim());

      return {
        owlName: scorerPersona,
        scores: parsed?.scores ?? {},
        agreedWith: parsed?.agreedWith ?? [],
      };
    } catch {
      const equalScores: Record<string, number> = {};
      positions.forEach((p) => (equalScores[p.owlName] = 0.5));
      return { owlName: scorerPersona, scores: equalScores, agreedWith: [] };
    }
  }

  private async synthesize(
    topic: string,
    positions: ParliamentPosition[],
    majority: ParliamentPosition,
  ): Promise<string> {
    const positionBlock = positions
      .map(
        (p) =>
          `**${p.owlName}** (confidence ${(p.confidence * 100).toFixed(0)}%): ${p.stance}`,
      )
      .join("\n");

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          "You are a neutral Parliament synthesizer. Produce a concise, balanced synthesis.",
      },
      {
        role: "user",
        content: `Topic: "${topic}"

Parliament positions:
${positionBlock}

The majority view (highest scored) is from ${majority.owlName}: "${majority.stance}"

Write a 2-4 sentence synthesis that integrates all viewpoints, acknowledges dissent, and gives a clear recommendation. Be direct.`,
      },
    ];

    try {
      const response = await this.provider.chat(messages);
      return response.content.trim();
    } catch {
      return `The parliament majority holds: ${majority.stance}`;
    }
  }

  private parseJson<T>(raw: string): T | null {
    try {
      // Strip markdown code fences if present
      const cleaned = raw
        .replace(/^```(?:json)?\s*/i, "")
        .replace(/\s*```$/, "")
        .trim();
      return JSON.parse(cleaned) as T;
    } catch {
      return null;
    }
  }
}
