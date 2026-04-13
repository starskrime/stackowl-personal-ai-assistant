/**
 * StackOwl — Creative Thinking Module
 *
 * Adds structured creativity to the ReAct loop. Instead of
 * committing to the first approach the LLM suggests, this module:
 *
 *   1. Generates 2-3 alternative approaches for complex requests
 *   2. Evaluates each against the user's DNA style preferences
 *   3. Picks the most aligned one OR presents options
 *
 * This prevents the "one-track mind" problem where the owl always
 * takes the most obvious path without considering alternatives.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlDNA } from "../owls/persona.js";
import type { DNADecisions } from "../owls/decision-layer.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface CreativeApproach {
  /** Short name for this approach */
  name: string;
  /** How the approach works */
  description: string;
  /** Which tools it would use */
  tools: string[];
  /** Estimated complexity (1-5) */
  complexity: number;
  /** How well it aligns with user's DNA preferences (computed) */
  alignmentScore: number;
  /** Why this approach might be better than the obvious one */
  differentiator: string;
}

export interface CreativeResult {
  /** Was creative exploration triggered? */
  triggered: boolean;
  /** The generated approaches */
  approaches: CreativeApproach[];
  /** The selected best approach */
  selected: CreativeApproach | null;
  /** Directive to inject into the system prompt */
  directive: string;
  /** Time spent on creative thinking (ms) */
  durationMs: number;
}

// ─── Complexity Heuristics ───────────────────────────────────────

/** Patterns that suggest a request is complex enough for creative thinking */
const COMPLEXITY_PATTERNS: RegExp[] = [
  /\b(?:how (?:can|should|would|do) (?:i|we|you))\b/i,
  /\b(?:best (?:way|approach|method|practice))\b/i,
  /\b(?:design|architect|plan|strategy|approach)\b/i,
  /\b(?:build|create|implement|develop) .{20,}/i,
  /\b(?:compare|evaluate|analyze|assess)\b/i,
  /\b(?:improve|optimize|refactor|redesign)\b/i,
  /\b(?:solve|fix|resolve|debug) .{30,}/i,
];

// ─── Creative Engine ─────────────────────────────────────────────

export class CreativeThinking {
  constructor(private provider: ModelProvider) {}

  /**
   * Evaluate whether a request deserves creative exploration,
   * and if so, generate alternative approaches.
   *
   * @param userMessage The user's request
   * @param dna The owl's evolved DNA
   * @param decisions The DNA decision layer's output
   * @param recentHistory Recent conversation for context
   */
  async explore(
    userMessage: string,
    dna: OwlDNA,
    decisions: DNADecisions,
    recentHistory: ChatMessage[],
  ): Promise<CreativeResult> {
    const startTime = Date.now();

    // Should we even try creative thinking?
    if (!this.shouldExplore(userMessage, decisions)) {
      return {
        triggered: false,
        approaches: [],
        selected: null,
        directive: "",
        durationMs: Date.now() - startTime,
      };
    }

    try {
      const approaches = await this.generateApproaches(
        userMessage,
        dna,
        recentHistory,
      );

      // Score each approach against the user's DNA preferences
      const scored = approaches.map((a) => ({
        ...a,
        alignmentScore: this.scoreAlignment(a, dna, decisions),
      }));

      // Sort by alignment score
      scored.sort((a, b) => b.alignmentScore - a.alignmentScore);

      const selected = scored[0] ?? null;
      const directive = this.buildDirective(scored, selected);

      log.engine.info(
        `[Creative] Generated ${scored.length} approaches for "${userMessage.slice(0, 50)}..." — selected: "${selected?.name ?? "none"}"`,
      );

      return {
        triggered: true,
        approaches: scored,
        selected,
        directive,
        durationMs: Date.now() - startTime,
      };
    } catch (err) {
      log.engine.warn(
        `[Creative] Exploration failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        triggered: false,
        approaches: [],
        selected: null,
        directive: "",
        durationMs: Date.now() - startTime,
      };
    }
  }

  // ─── Should Explore ────────────────────────────────────────────

  private shouldExplore(userMessage: string, decisions: DNADecisions): boolean {
    // Skip for very short or simple messages
    if (userMessage.length < 30) return false;

    // Skip for direct commands
    if (/^(?:do|run|send|open|get|set|show)\s/i.test(userMessage.trim())) {
      return false;
    }

    // Skip when the user wants concise responses
    if (decisions.maxResponseTokens < 500) return false;

    // Check complexity patterns
    const matches = COMPLEXITY_PATTERNS.filter((p) => p.test(userMessage));
    return matches.length >= 1;
  }

  // ─── Generate Approaches ───────────────────────────────────────

  private async generateApproaches(
    userMessage: string,
    dna: OwlDNA,
    recentHistory: ChatMessage[],
  ): Promise<CreativeApproach[]> {
    const context = recentHistory
      .slice(-4)
      .map((m) => `${m.role}: ${(m.content ?? "").slice(0, 200)}`)
      .join("\n");

    const expertise = Object.entries(dna.expertiseGrowth)
      .filter(([, s]) => s > 0.3)
      .map(([d]) => d)
      .join(", ");

    const prompt = `Given this user request, generate exactly 3 different APPROACHES to solve it.
Each approach should be meaningfully different — not just variations of the same idea.

USER REQUEST: "${userMessage}"

RECENT CONTEXT:
${context}

YOUR EXPERTISE: ${expertise || "general"}

For each approach, provide:
1. A short name (2-4 words)
2. A brief description (1-2 sentences)
3. Which tools it would use (from: run_shell_command, web_crawl, duckduckgo_search, read_file, write_file, generate_image, send_telegram_message, send_file)
4. Complexity rating (1-5)
5. What makes this approach different/better than the obvious one

Return ONLY valid JSON array:
[
  { "name": "...", "description": "...", "tools": ["..."], "complexity": 3, "differentiator": "..." }
]`;

    const response = await this.provider.chat(
      [
        {
          role: "system",
          content:
            "You are a creative problem-solving module. Output only valid JSON.",
        },
        { role: "user", content: prompt },
      ],
      undefined,
      { temperature: 0.9, maxTokens: 600 },
    );

    let jsonStr = response.content.trim();
    if (jsonStr.startsWith("```")) {
      jsonStr = jsonStr
        .replace(/^```json?\s*/i, "")
        .replace(/\s*```$/i, "")
        .trim();
    }

    const parsed = JSON.parse(jsonStr);
    if (!Array.isArray(parsed)) return [];

    return parsed.slice(0, 3).map((item: Record<string, unknown>) => ({
      name: String(item.name ?? "").slice(0, 50),
      description: String(item.description ?? "").slice(0, 200),
      tools: Array.isArray(item.tools) ? item.tools.map(String) : [],
      complexity: Math.max(1, Math.min(5, Number(item.complexity ?? 3))),
      alignmentScore: 0,
      differentiator: String(item.differentiator ?? "").slice(0, 150),
    }));
  }

  // ─── Score Alignment ───────────────────────────────────────────

  private scoreAlignment(
    approach: CreativeApproach,
    dna: OwlDNA,
    decisions: DNADecisions,
  ): number {
    let score = 0.5; // Base score

    // Prefer approaches using prioritized tools
    const prioritizedSet = new Set(decisions.prioritizedTools);
    const deprioritizedSet = new Set(decisions.deprioritizedTools);

    for (const tool of approach.tools) {
      if (prioritizedSet.has(tool)) score += 0.1;
      if (deprioritizedSet.has(tool)) score -= 0.1;
    }

    // Match complexity to risk tolerance
    if (decisions.riskTolerance === "cautious" && approach.complexity <= 2)
      score += 0.1;
    if (decisions.riskTolerance === "aggressive" && approach.complexity >= 3)
      score += 0.1;
    if (decisions.riskTolerance === "moderate") score += 0.05; // Neutral bonus

    // Concise users prefer simpler approaches
    if (dna.evolvedTraits.verbosity === "concise" && approach.complexity <= 2)
      score += 0.1;

    // High challenge users prefer thorough approaches
    if (
      (dna.evolvedTraits.challengeLevel === "high" ||
        dna.evolvedTraits.challengeLevel === "relentless") &&
      approach.complexity >= 3
    ) {
      score += 0.1;
    }

    return Math.max(0, Math.min(1, score));
  }

  // ─── Build Directive ───────────────────────────────────────────

  private buildDirective(
    approaches: CreativeApproach[],
    selected: CreativeApproach | null,
  ): string {
    if (!selected || approaches.length === 0) return "";

    const lines: string[] = [
      "## Creative Exploration (your thinking module explored alternatives)",
      "",
    ];

    if (approaches.length > 1) {
      lines.push("Alternative approaches considered:");
      for (const a of approaches) {
        const marker = a.name === selected.name ? "→" : "  ";
        lines.push(
          `${marker} **${a.name}** (alignment: ${(a.alignmentScore * 100).toFixed(0)}%): ${a.description}`,
        );
      }
      lines.push("");
    }

    lines.push(`**Selected approach:** ${selected.name}`);
    lines.push(`Reason: ${selected.differentiator}`);
    lines.push(`Use tools: ${selected.tools.join(", ")}`);
    lines.push("");
    lines.push(
      "Follow this approach, but stay flexible. If the selected approach hits a wall, " +
        "pivot to one of the alternatives above.",
    );

    return lines.join("\n");
  }
}
