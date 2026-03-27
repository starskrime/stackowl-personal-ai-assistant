/**
 * StackOwl — Tool Intent Router
 *
 * Five-tier routing for intelligent per-turn tool selection:
 *
 *   Tier 1 — BM25 retrieval (sub-ms, offline)
 *     Indexes: tool.name (5x boost) + description (2x)
 *     Returns top-25 candidates from the full tool registry
 *
 *   Tier 2 — Recency-weighted usage re-ranking
 *     Boost tools with higher recency-adjusted success rates from ToolTracker
 *     90-day half-life: recent successes matter ~2.7× more than stale ones
 *
 *   Tier 3 — Semantic re-ranking (optional, embedding-powered)
 *     Cosine similarity: embed(userMessage) vs embed(tool.name + description)
 *     Hybrid: BM25×0.4 + cosine×0.6; fires only if provider returns embeddings
 *
 *   Tier 4 — Overlap deduplication
 *     Jaccard on parameter names; 0.5 threshold
 *     Keeps higher-ranked when two tools have similar signatures
 *
 *   Tier 5 — LLM disambiguation (optional, only when ambiguous)
 *     Fire when top-2 scores within 20%; pass top-8 candidates
 *     Fuzzy name match as fallback for LLM output
 *
 * Architecture:
 *   - TfIdfEngine in-memory (rebuilt on reindex())
 *   - LLM disambiguation gated: only fires when ambiguous AND provider available
 *   - Semantic re-ranking gated: only fires if provider returns non-empty embeddings
 *   - Results cached per SHA256(message) with 200-entry LRU
 */

import { createHash } from "node:crypto";
import type { ToolDefinition } from "../providers/base.js";
import type { ModelProvider } from "../providers/base.js";
import type { ToolTracker } from "./tracker.js";
import { TfIdfEngine } from "../pellets/tfidf.js";
import { log } from "../logger.js";

export type ToolMatchMethod =
  | "bm25"
  | "bm25+usage"
  | "bm25+usage+semantic"
  | "bm25+usage+dedup"
  | "bm25+usage+semantic+dedup"
  | "llm";

export interface ToolMatch {
  definition: ToolDefinition;
  score: number;
  method: ToolMatchMethod;
}

export class ToolIntentRouter {
  private definitions: ToolDefinition[] = [];
  private provider: ModelProvider | null;
  private tracker: ToolTracker | null;
  private tfidf: TfIdfEngine;
  private cache: Map<string, ToolMatch[]> = new Map();
  private static readonly MAX_CACHE = 200;
  private static readonly AMBIGUITY_THRESHOLD = 0.2;
  private static readonly MIN_SCORE = 0.05;
  private static readonly BM25_RETRIEVAL_LIMIT = 25;

  constructor(provider?: ModelProvider, tracker?: ToolTracker) {
    this.provider = provider ?? null;
    this.tracker = tracker ?? null;
    this.tfidf = new TfIdfEngine("/dev/null");
  }

  /** Rebuild the BM25 index from current tool definitions */
  reindex(definitions: ToolDefinition[]): void {
    this.definitions = definitions;

    for (const def of definitions) {
      this.tfidf.removeDocument(def.name);
    }

    for (const def of definitions) {
      const paramSchema = def.parameters?.properties ?? {};
      const paramSummary = Object.entries(paramSchema)
        .map(([k, v]) => `${k}: ${v.description}`)
        .join(" ");

      this.tfidf.addDocument(def.name, {
        title: def.name,
        tags: def.description,
        content: paramSummary,
      });
    }

    this.cache.clear();
    log.engine.debug(
      `[ToolIntentRouter] Indexed ${definitions.length} tools for BM25 retrieval`,
    );
  }

  /**
   * Route a user message to the top-N relevant tools.
   * Returns tool definitions sorted by relevance score.
   */
  async route(userMessage: string, maxTools: number = 8): Promise<ToolMatch[]> {
    if (this.definitions.length === 0) return [];

    const cacheKey = createHash("sha256").update(userMessage).digest("hex");
    const cached = this.cache.get(cacheKey);
    if (cached) {
      log.engine.debug(`[ToolIntentRouter] Cache hit`);
      return cached.slice(0, maxTools);
    }

    const bm25Results = this.tfidf.search(
      userMessage,
      ToolIntentRouter.BM25_RETRIEVAL_LIMIT,
    );

    if (bm25Results.length === 0) {
      log.engine.debug("[ToolIntentRouter] No BM25 matches found");
      return [];
    }

    let matches: ToolMatch[] = [];
    for (const result of bm25Results) {
      const def = this.definitions.find((d) => d.name === result.id);
      if (!def) continue;

      matches.push({
        definition: def,
        score: result.score,
        method: "bm25",
      });
    }

    // Tier 2: Usage-weighted re-ranking
    if (this.tracker) {
      for (const match of matches) {
        const multiplier = this.tracker.getUsageMultiplier(
          match.definition.name,
        );
        match.score *= multiplier;
        match.method = "bm25+usage";
      }

      matches.sort((a, b) => b.score - a.score);
    }

    matches = matches.filter((m) => m.score >= ToolIntentRouter.MIN_SCORE);

    if (matches.length === 0) {
      return [];
    }

    // Tier 3: Semantic re-ranking (optional)
    if (matches.length > 1) {
      const semanticMatches = await this.rerankWithSemantics(
        userMessage,
        matches.slice(0, 10),
      );
      if (semanticMatches) {
        matches = semanticMatches;
      }
    }

    // Tier 4: Overlap dedup on parameter signatures
    const beforeDedup = matches.length;
    matches = this.deduplicateOverlapping(matches);
    if (matches.length < beforeDedup) {
      log.engine.debug(
        `[ToolIntentRouter] Dedup removed ${beforeDedup - matches.length} overlapping tool(s)`,
      );
    }

    // Tier 5: LLM disambiguation (only if still ambiguous)
    if (
      this.provider &&
      matches.length >= 2 &&
      this.isAmbiguous(matches[0], matches[1])
    ) {
      log.engine.info(
        `[ToolIntentRouter] Ambiguous match: "${matches[0].definition.name}" (${matches[0].score.toFixed(3)}) vs "${matches[1].definition.name}" (${matches[1].score.toFixed(3)}) — LLM disambiguation`,
      );
      matches = await this.disambiguate(userMessage, matches);
    }

    const finalResults = matches.slice(0, maxTools);

    if (this.cache.size >= ToolIntentRouter.MAX_CACHE) {
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) {
        this.cache.delete(firstKey);
      }
    }
    this.cache.set(cacheKey, finalResults);

    log.engine.info(
      `[ToolIntentRouter] Routed to: ${finalResults.map((m) => `${m.definition.name}(${m.score.toFixed(2)},${m.method})`).join(", ")}`,
    );

    return finalResults;
  }

  clearCache(): void {
    this.cache.clear();
  }

  setTracker(tracker: ToolTracker): void {
    this.tracker = tracker;
  }

  setProvider(provider: ModelProvider): void {
    this.provider = provider;
  }

  private isAmbiguous(first: ToolMatch, second: ToolMatch): boolean {
    if (first.score === 0) return false;
    const scoreDiff = (first.score - second.score) / first.score;
    return scoreDiff < ToolIntentRouter.AMBIGUITY_THRESHOLD;
  }

  private cosineSimilarity(a: number[], b: number[]): number {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    const denom = Math.sqrt(normA) * Math.sqrt(normB);
    return denom === 0 ? 0 : dot / denom;
  }

  private async rerankWithSemantics(
    userMessage: string,
    candidates: ToolMatch[],
  ): Promise<ToolMatch[] | null> {
    if (!this.provider) return null;

    try {
      const msgEmbed = await this.provider.embed(userMessage);
      if (!msgEmbed.embedding || msgEmbed.embedding.length === 0) {
        return null;
      }

      const scores = await Promise.all(
        candidates.map(async (m) => {
          try {
            const toolEmbed = await this.provider!.embed(
              `${m.definition.name} ${m.definition.description}`,
            );
            if (!toolEmbed.embedding || toolEmbed.embedding.length === 0) {
              return 0;
            }
            return this.cosineSimilarity(
              msgEmbed.embedding,
              toolEmbed.embedding,
            );
          } catch {
            return 0;
          }
        }),
      );

      const maxBm25 = Math.max(...candidates.map((m) => m.score), 0.001);
      for (let i = 0; i < candidates.length; i++) {
        const normBm25 = candidates[i].score / maxBm25;
        const hybridScore = normBm25 * 0.4 + scores[i] * 0.6;
        candidates[i].score = hybridScore;
        candidates[i].method = "bm25+usage+semantic";
      }

      candidates.sort((a, b) => b.score - a.score);
      return candidates;
    } catch {
      return null;
    }
  }

  private deduplicateOverlapping(matches: ToolMatch[]): ToolMatch[] {
    if (matches.length <= 1) return matches;

    const getParamSet = (def: ToolDefinition): Set<string> =>
      new Set(Object.keys(def.parameters?.properties ?? {}));

    const jaccard = (a: Set<string>, b: Set<string>): number => {
      let intersection = 0;
      for (const x of a) {
        if (b.has(x)) intersection++;
      }
      const union = a.size + b.size - intersection;
      return union === 0 ? 0 : intersection / union;
    };

    const result: ToolMatch[] = [];
    const used = new Set<string>();

    for (const match of matches) {
      if (used.has(match.definition.name)) continue;

      const paramsA = getParamSet(match.definition);
      let hasOverlap = false;

      for (const kept of result) {
        const paramsB = getParamSet(kept.definition);
        if (jaccard(paramsA, paramsB) > 0.5) {
          hasOverlap = true;
          break;
        }
      }

      if (!hasOverlap) {
        result.push(match);
        used.add(match.definition.name);
      }
    }

    result.sort((a, b) => b.score - a.score);

    if (result.length < matches.length) {
      for (const m of result) {
        if (m.method === "bm25+usage+semantic") {
          m.method = "bm25+usage+semantic+dedup";
        } else if (m.method.startsWith("bm25+usage")) {
          m.method = "bm25+usage+dedup";
        }
      }
    }

    return result;
  }

  private async disambiguate(
    userMessage: string,
    candidates: ToolMatch[],
  ): Promise<ToolMatch[]> {
    if (!this.provider) return candidates;

    const skillList = candidates
      .slice(0, 8)
      .map((m, i) => {
        const params = Object.entries(m.definition.parameters?.properties ?? {})
          .map(([k, v]) => `${k}: ${v.description}`)
          .join(", ");
        return `${i + 1}. ${m.definition.name} — ${m.definition.description} [params: ${params || "none"}]`;
      })
      .join("\n");

    const prompt = [
      `Given this user request: "${userMessage}"`,
      "",
      "Which tool(s) best match this request? You may select one or more tools if the task requires multiple capabilities.",
      "",
      skillList,
      "",
      "Respond with ONLY the number(s) of the matching tool(s), separated by commas. Nothing else.",
    ].join("\n");

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 128 },
      );

      const content = response.content.trim();
      const chosenIndices: number[] = [];

      for (const token of content.split(/[,\s]+/)) {
        const idx = parseInt(token.trim(), 10);
        if (!isNaN(idx) && idx >= 1 && idx <= candidates.length) {
          chosenIndices.push(idx - 1);
        }
      }

      if (chosenIndices.length === 0) {
        log.engine.debug(
          `[ToolIntentRouter] LLM response "${content}" did not match any candidate`,
        );
        return candidates;
      }

      // Reorder: chosen first (in original order), then rest
      const chosen = chosenIndices.map((i) => candidates[i]).filter(Boolean);
      const rest = candidates.filter((_, i) => !chosenIndices.includes(i));
      const reordered = [...chosen, ...rest];

      // Update methods
      for (const c of chosen) {
        c.method = "llm";
      }

      // Re-score: chosen get 2x score boost, rest get 0.9x
      for (const r of reordered) {
        if (r.method === "llm") {
          r.score *= 2.0;
        } else {
          r.score *= 0.9;
        }
      }

      reordered.sort((a, b) => b.score - a.score);
      log.engine.info(
        `[ToolIntentRouter] LLM disambiguated to: ${chosen.map((c) => c.definition.name).join(", ")}`,
      );
      return reordered;
    } catch (err) {
      log.engine.debug(
        `[ToolIntentRouter] LLM disambiguation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return candidates;
    }
  }
}
