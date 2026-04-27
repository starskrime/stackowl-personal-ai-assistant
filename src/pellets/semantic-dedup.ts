/**
 * StackOwl — Pellet Deduplicator Enhancement
 *
 * Enhanced deduplication using semantic similarity > 0.85 threshold for auto-skip.
 * Implements the full FR36 requirement for intelligent deduplication.
 *
 * Verdicts:
 *   CREATE    — new topic, save normally
 *   SKIP      — existing pellet already covers this (similarity >= 0.85)
 *   MERGE     — combine new + existing into one improved pellet (0.65-0.85)
 *   SUPERSEDE — new pellet replaces the old one entirely
 */

import type { ModelProvider } from "../providers/base.js";
import type { Pellet } from "./store.js";
import { log } from "../logger.js";

// ─── Semantic Dedup Configuration ────────────────────────────────

export interface SemanticDedupConfig {
  /** Enable semantic deduplication. Default: true */
  enabled: boolean;
  /** Cosine similarity threshold to trigger LLM check. Default: 0.65 */
  similarityThreshold: number;
  /** Cosine similarity above which to auto-skip without LLM. Default: 0.85 */
  autoSkipThreshold: number;
  /** Use LLM for merge/supersede decisions. Default: true */
  useLlm: boolean;
  /** Max candidates to consider. Default: 3 */
  maxCandidates: number;
}

export const DEFAULT_SEMANTIC_DEDUP_CONFIG: SemanticDedupConfig = {
  enabled: true,
  similarityThreshold: 0.65,
  autoSkipThreshold: 0.85,
  useLlm: true,
  maxCandidates: 3,
};

// ─── Semantic Dedup Result ───────────────────────────────────────

export interface SemanticDedupResult {
  verdict: "CREATE" | "MERGE" | "SUPERSEDE" | "SKIP";
  reasoning: string;
  targetPelletId?: string;
  mergedContent?: string;
  mergedTitle?: string;
  mergedTags?: string[];
}

// ─── Semantic Deduplicator ───────────────────────────────────────

export class SemanticDeduplicator {
  private config: SemanticDedupConfig;

  constructor(
    private searchSimilar: (
      pellet: Pellet,
      limit: number,
    ) => Promise<Array<{ pellet: Pellet; score: number }>>,
    private provider?: ModelProvider,
    config?: Partial<SemanticDedupConfig>,
  ) {
    this.config = { ...DEFAULT_SEMANTIC_DEDUP_CONFIG, ...config };
  }

  /**
   * Evaluate an incoming pellet for deduplication against existing ones.
   */
  async evaluate(incoming: Pellet): Promise<SemanticDedupResult> {
    if (!this.config.enabled) {
      return { verdict: "CREATE", reasoning: "dedup disabled" };
    }

    if (!incoming.title && !incoming.content) {
      return { verdict: "CREATE", reasoning: "no indexable content" };
    }

    try {
      return await this.doEvaluate(incoming);
    } catch (err) {
      log.engine.info(
        `[SemanticDedup] Evaluation error, defaulting to CREATE: ${err instanceof Error ? err.message : String(err)}`,
      );
      return { verdict: "CREATE", reasoning: "dedup evaluation failed" };
    }
  }

  private async doEvaluate(incoming: Pellet): Promise<SemanticDedupResult> {
    const candidates = await this.searchSimilar(incoming, this.config.maxCandidates);

    if (candidates.length === 0) {
      return { verdict: "CREATE", reasoning: "no similar pellets found" };
    }

    const best = candidates[0];
    const similarity = best.score;

    log.engine.debug(
      `[SemanticDedup] Best match: "${best.pellet.id}" (cosine_sim=${similarity.toFixed(3)})`,
    );

    // Auto-skip high similarity (no LLM needed)
    if (similarity >= this.config.autoSkipThreshold) {
      return {
        verdict: "SKIP",
        reasoning: `cosine similarity ${similarity.toFixed(2)} >= auto-skip threshold ${this.config.autoSkipThreshold}; existing pellet covers this`,
        targetPelletId: best.pellet.id,
      };
    }

    // Below LLM threshold — create without dedup
    if (similarity < this.config.similarityThreshold) {
      return {
        verdict: "CREATE",
        reasoning: `cosine similarity ${similarity.toFixed(2)} below LLM threshold ${this.config.similarityThreshold}`,
      };
    }

    // Medium similarity — use LLM for nuanced decision
    if (this.config.useLlm && this.provider) {
      return await this.decideWithLlm(incoming, best.pellet, similarity);
    }

    return this.decideHeuristic(incoming, best.pellet, similarity);
  }

  private async decideWithLlm(
    incoming: Pellet,
    existing: Pellet,
    similarity: number,
  ): Promise<SemanticDedupResult> {
    const prompt =
      `You are a knowledge base curator. Two knowledge pellets overlap in topic.\n` +
      `Decide how to handle the overlap.\n\n` +
      `EXISTING PELLET:\n` +
      `  Title: "${existing.title}"\n` +
      `  Tags: [${existing.tags.join(", ")}]\n` +
      `  Source: ${existing.source}\n` +
      `  Content:\n${existing.content.slice(0, 800)}\n\n` +
      `NEW PELLET:\n` +
      `  Title: "${incoming.title}"\n` +
      `  Tags: [${incoming.tags.join(", ")}]\n` +
      `  Source: ${incoming.source}\n` +
      `  Content:\n${incoming.content.slice(0, 800)}\n\n` +
      `Similarity: ${(similarity * 100).toFixed(0)}%\n\n` +
      `Decide ONE of:\n` +
      `  MERGE     — Combine both into one improved pellet (both have valuable unique info)\n` +
      `  SUPERSEDE — Replace old with new (new is strictly better or more current)\n` +
      `  CREATE    — Keep both (they cover genuinely different aspects despite word overlap)\n` +
      `  SKIP      — Discard the new pellet (existing already covers this fully)\n\n` +
      `Return ONLY valid JSON (no comments, no trailing commas):\n` +
      `{\n` +
      `  "verdict": "MERGE or SUPERSEDE or CREATE or SKIP",\n` +
      `  "reasoning": "one sentence explanation",\n` +
      `  "merged_title": "title for combined pellet (only if MERGE)",\n` +
      `  "merged_content": "merged markdown content combining best of both, max 300 words (only if MERGE)",\n` +
      `  "merged_tags": ["union", "of", "tags"]\n` +
      `}\n\n` +
      `For non-MERGE verdicts, omit merged_title, merged_content, and merged_tags.`;

    try {
      const response = await this.provider!.chat(
        [
          {
            role: "system",
            content: "You are a knowledge base curator. Output only valid JSON. Be concise.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0, maxTokens: 2048 },
      );

      const result = this.parseDecisionResponse(response.content, existing, incoming, similarity);
      if (result) return result;

      throw new Error("Invalid JSON from LLM");
    } catch (err) {
      log.engine.info(
        `[SemanticDedup] LLM decision failed, using heuristic: ${err instanceof Error ? err.message : String(err)}`,
      );
      return this.decideHeuristic(incoming, existing, similarity);
    }
  }

  private parseDecisionResponse(
    raw: string,
    existing: Pellet,
    incoming: Pellet,
    similarity: number,
  ): SemanticDedupResult | null {
    try {
      let jsonStr = raw.trim();

      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr.replace(/^```json?/, "").replace(/```$/, "").trim();
      }

      jsonStr = jsonStr.replace(/\/\/[^\n]*/g, "");
      jsonStr = jsonStr.replace(/,\s*([}\]])/g, "$1");

      const jsonMatch = jsonStr.match(/\{[\s\S]*\}/);
      if (jsonMatch) jsonStr = jsonMatch[0]!;

      const parsed = JSON.parse(jsonStr);
      const verdict = this.parseVerdict(parsed["verdict"] as string);

      log.engine.info(
        `[SemanticDedup] LLM verdict: ${verdict} — "${parsed["reasoning"]}" (similarity=${similarity.toFixed(2)})`,
      );

      const result: SemanticDedupResult = {
        verdict,
        reasoning: (parsed["reasoning"] as string) || "LLM decision",
        targetPelletId: existing.id,
      };

      if (verdict === "MERGE") {
        result.mergedContent = (parsed["merged_content"] as string) || incoming.content;
        result.mergedTitle = (parsed["merged_title"] as string) || existing.title;
        result.mergedTags = Array.isArray(parsed["merged_tags"])
          ? (parsed["merged_tags"] as string[])
          : [...new Set([...existing.tags, ...incoming.tags])];
      }

      return result;
    } catch {
      return null;
    }
  }

  private decideHeuristic(
    incoming: Pellet,
    existing: Pellet,
    similarity: number,
  ): SemanticDedupResult {
    const incomingWords = new Set(
      incoming.title
        .toLowerCase()
        .split(/\W+/)
        .filter((w) => w.length > 2),
    );
    const existingWords = new Set(
      existing.title
        .toLowerCase()
        .split(/\W+/)
        .filter((w) => w.length > 2),
    );

    let overlap = 0;
    for (const w of incomingWords) {
      if (existingWords.has(w)) overlap++;
    }
    const titleOverlap = incomingWords.size > 0 ? overlap / incomingWords.size : 0;

    if (similarity >= this.config.autoSkipThreshold && titleOverlap >= 0.6) {
      return {
        verdict: "SKIP",
        reasoning: `heuristic: similarity=${similarity.toFixed(2)}, titleOverlap=${titleOverlap.toFixed(2)} — existing covers this`,
        targetPelletId: existing.id,
      };
    }

    if (similarity >= 0.6 && incoming.content.length > existing.content.length * 1.5) {
      return {
        verdict: "SUPERSEDE",
        reasoning: `heuristic: new content is ${Math.round((incoming.content.length / existing.content.length) * 100)}% longer`,
        targetPelletId: existing.id,
      };
    }

    return {
      verdict: "CREATE",
      reasoning: `heuristic: similarity=${similarity.toFixed(2)} — different enough to keep both`,
    };
  }

  private parseVerdict(raw: string): SemanticDedupResult["verdict"] {
    const upper = (raw || "").toUpperCase().trim();
    if (upper === "MERGE" || upper === "SUPERSEDE" || upper === "SKIP") {
      return upper;
    }
    return "CREATE";
  }
}