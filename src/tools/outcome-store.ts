/**
 * StackOwl — Tool Outcome Store (Fix 9)
 *
 * Records which tool combinations succeed for which request types.
 * Learns from every tool execution — builds a pattern database that
 * injects "preferred approaches" into the system prompt.
 *
 * This solves the problem where every request starts from zero —
 * the owl should know which tools worked well in the past.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface ToolOutcome {
  /** Classification of the request type */
  requestType: string;
  /** Tools used (in order) */
  toolsUsed: string[];
  /** Did the overall task succeed? */
  success: boolean;
  /** User reaction (positive/negative/neutral) */
  userReaction: "positive" | "negative" | "neutral";
  /** Timestamp */
  timestamp: string;
}

export interface ToolPattern {
  /** Common request type */
  requestType: string;
  /** Best tool combination for this type */
  bestTools: string[];
  /** Success rate (0-1) */
  successRate: number;
  /** How many times this pattern has been observed */
  observations: number;
  /** User satisfaction rate */
  satisfactionRate: number;
}

export interface ToolOutcomeIndex {
  outcomes: ToolOutcome[];
  patterns: Record<string, ToolPattern>;
  lastUpdated: string;
}

// ─── Request Type Classification ─────────────────────────────────

const REQUEST_TYPE_PATTERNS: Array<[RegExp, string]> = [
  [/\b(?:search|find|look\s*up|google)\b/i, "web_search"],
  [/\b(?:send|message|telegram|email|notify)\b/i, "communication"],
  [/\b(?:file|read|write|save|create|open)\b/i, "file_ops"],
  [/\b(?:run|execute|command|shell|terminal)\b/i, "shell_ops"],
  [/\b(?:image|picture|photo|generate|draw)\b/i, "image_gen"],
  [/\b(?:web|crawl|scrape|fetch|url|page)\b/i, "web_crawl"],
  [/\b(?:code|program|debug|fix|implement)\b/i, "coding"],
  [/\b(?:analyze|research|compare|review)\b/i, "analysis"],
  [/\b(?:summarize|tldr|brief|overview)\b/i, "summarization"],
  [/\b(?:schedule|remind|calendar|timer)\b/i, "scheduling"],
  [/\b(?:translate|language|convert)\b/i, "translation"],
  [/\b(?:weather|forecast)\b/i, "weather"],
];

// ─── Outcome Store ───────────────────────────────────────────────

export class ToolOutcomeStore {
  private index: ToolOutcomeIndex;
  private filePath: string;
  private dirty = false;

  private static readonly MAX_OUTCOMES = 200;
  private static readonly MIN_OBSERVATIONS_FOR_PATTERN = 3;

  constructor(workspacePath: string) {
    const dir = join(workspacePath, "tools");
    this.filePath = join(dir, "outcome-index.json");
    this.index = {
      outcomes: [],
      patterns: {},
      lastUpdated: new Date().toISOString(),
    };
  }

  async init(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) {
      await mkdir(dir, { recursive: true });
    }

    if (existsSync(this.filePath)) {
      try {
        const raw = await readFile(this.filePath, "utf-8");
        this.index = JSON.parse(raw);
      } catch (err) {
        log.engine.warn(`[ToolOutcomeStore] Failed to load: ${err}`);
      }
    }
  }

  /**
   * Record a tool outcome after execution.
   */
  async record(
    userMessage: string,
    toolsUsed: string[],
    success: boolean,
    userReaction: "positive" | "negative" | "neutral" = "neutral",
  ): Promise<void> {
    const requestType = this.classifyRequest(userMessage);

    const outcome: ToolOutcome = {
      requestType,
      toolsUsed,
      success,
      userReaction,
      timestamp: new Date().toISOString(),
    };

    this.index.outcomes.push(outcome);

    // Cap outcomes
    if (this.index.outcomes.length > ToolOutcomeStore.MAX_OUTCOMES) {
      this.index.outcomes = this.index.outcomes.slice(
        -ToolOutcomeStore.MAX_OUTCOMES,
      );
    }

    // Rebuild patterns for this request type
    this.rebuildPattern(requestType);

    this.index.lastUpdated = new Date().toISOString();
    this.dirty = true;
    await this.save();
  }

  /**
   * Get the best tool pattern for a given user message.
   * Returns null if no strong pattern exists yet.
   */
  getBestPattern(userMessage: string): ToolPattern | null {
    const requestType = this.classifyRequest(userMessage);
    const pattern = this.index.patterns[requestType];

    if (!pattern) return null;
    if (pattern.observations < ToolOutcomeStore.MIN_OBSERVATIONS_FOR_PATTERN)
      return null;
    if (pattern.successRate < 0.5) return null;

    return pattern;
  }

  /**
   * Get all patterns sorted by confidence (observations × success rate).
   */
  getTopPatterns(limit: number = 5): ToolPattern[] {
    return Object.values(this.index.patterns)
      .filter(
        (p) => p.observations >= ToolOutcomeStore.MIN_OBSERVATIONS_FOR_PATTERN,
      )
      .sort((a, b) => {
        const scoreA = a.successRate * Math.log2(a.observations + 1);
        const scoreB = b.successRate * Math.log2(b.observations + 1);
        return scoreB - scoreA;
      })
      .slice(0, limit);
  }

  /**
   * Generate system prompt context about preferred approaches.
   */
  toSystemPrompt(userMessage: string, maxChars: number = 500): string {
    const pattern = this.getBestPattern(userMessage);
    if (!pattern) return "";

    const lines: string[] = [
      "## Learned Tool Preferences",
      `For "${pattern.requestType}" requests, your best approach is:`,
      `- Tools: ${pattern.bestTools.join(" → ")}`,
      `- Success rate: ${(pattern.successRate * 100).toFixed(0)}% (${pattern.observations} observations)`,
      `- User satisfaction: ${(pattern.satisfactionRate * 100).toFixed(0)}%`,
      "",
      "Consider using this proven approach unless the context demands something different.",
    ];

    const result = lines.join("\n");
    return result.length > maxChars
      ? result.slice(0, maxChars) + "..."
      : result;
  }

  // ─── Private ───────────────────────────────────────────────────

  private classifyRequest(message: string): string {
    for (const [pattern, type] of REQUEST_TYPE_PATTERNS) {
      if (pattern.test(message)) return type;
    }
    return "general";
  }

  private rebuildPattern(requestType: string): void {
    const relevant = this.index.outcomes.filter(
      (o) => o.requestType === requestType,
    );
    if (relevant.length === 0) return;

    // Find the most successful tool combination
    const comboCounts = new Map<
      string,
      { success: number; total: number; satisfaction: number }
    >();

    for (const outcome of relevant) {
      const key = outcome.toolsUsed.sort().join("+");
      const existing = comboCounts.get(key) ?? {
        success: 0,
        total: 0,
        satisfaction: 0,
      };
      existing.total++;
      if (outcome.success) existing.success++;
      if (outcome.userReaction === "positive") existing.satisfaction++;
      comboCounts.set(key, existing);
    }

    // Find best combo
    let bestKey = "";
    let bestScore = -1;

    for (const [key, stats] of comboCounts) {
      const score = (stats.success / stats.total) * Math.log2(stats.total + 1);
      if (score > bestScore) {
        bestScore = score;
        bestKey = key;
      }
    }

    if (!bestKey) return;

    const bestStats = comboCounts.get(bestKey)!;
    this.index.patterns[requestType] = {
      requestType,
      bestTools: bestKey.split("+"),
      successRate: bestStats.success / bestStats.total,
      observations: bestStats.total,
      satisfactionRate: bestStats.satisfaction / bestStats.total,
    };
  }

  private async save(): Promise<void> {
    if (!this.dirty) return;
    try {
      await writeFile(
        this.filePath,
        JSON.stringify(this.index, null, 2),
        "utf-8",
      );
      this.dirty = false;
    } catch (err) {
      log.engine.warn(`[ToolOutcomeStore] Save failed: ${err}`);
    }
  }
}
