/**
 * StackOwl — Intent Router
 *
 * Enterprise-grade skill matching that replaces primitive keyword scoring.
 * Five-tier routing:
 *
 *   Tier 1 — BM25 retrieval (sub-ms, offline)
 *     Uses TfIdfEngine with field boosting: name(3x) > description(2x) > instructions(1x)
 *     Returns top-25 candidates (up from 10)
 *
 *   Tier 2 — Usage-weighted re-ranking
 *     Boosts skills with higher recency-adjusted success rates from SkillTracker
 *     90-day half-life: recent successes matter ~2.7× more than stale ones
 *
 *   Tier 3 — Semantic re-ranking (optional, embedding-powered)
 *     Cosine similarity on embeddings (user message vs skill descriptions)
 *     Hybrid: BM25×0.4 + cosine×0.6; only fires if embeddings are available
 *
 *   Tier 4 — Overlap deduplication
 *     Jaccard similarity (tokenized instructions); 0.5 threshold
 *     Keeps higher-ranked skill when two skills overlap too much
 *
 *   Tier 5 — LLM disambiguation (optional, only when ambiguous)
 *     If top-2 scores are within 20% AND both score > threshold,
 *     make a single LLM call with top-8 candidates
 *     Fuzzy name matching: "code interpreter" → `code_interpreter`
 *
 * Architecture:
 *   - TfIdfEngine is used in-memory (no disk persistence for skill index — rebuilt on startup)
 *   - LLM disambiguation is gated: only fires when ambiguous AND provider is available
 *   - Semantic re-ranking is gated: only fires if provider returns non-empty embeddings
 *   - Results are cached per SHA256(message) with 200-entry LRU
 */

import { createHash } from "node:crypto";
import type { ModelProvider } from "../providers/base.js";
import type { Skill } from "./types.js";
import type { SkillsRegistry } from "./registry.js";
import type { SkillTracker } from "./tracker.js";
import { TfIdfEngine } from "../pellets/tfidf.js";
import { log } from "../logger.js";

export type MatchMethod =
  | "bm25"
  | "bm25+usage"
  | "bm25+usage+semantic"
  | "bm25+usage+dedup"
  | "bm25+usage+semantic+dedup"
  | "llm";

export interface IntentMatch {
  skill: Skill;
  score: number;
  method: MatchMethod;
}

export class IntentRouter {
  private registry: SkillsRegistry;
  private provider: ModelProvider | null;
  private tracker: SkillTracker | null;
  private tfidf: TfIdfEngine;
  private cache: Map<string, IntentMatch[]> = new Map();
  /** Skill description embedding cache — computed once at startup, reused for every semantic re-rank */
  private skillEmbeddingCache: Map<string, number[]> = new Map();
  private static readonly MAX_CACHE = 200;
  private static readonly MIN_SCORE = 0.45; // minimum BM25 score to consider — raised to prevent weak keyword false-positives

  constructor(
    registry: SkillsRegistry,
    provider?: ModelProvider,
    tracker?: SkillTracker,
  ) {
    this.registry = registry;
    this.provider = provider ?? null;
    this.tracker = tracker ?? null;
    // In-memory TF-IDF — no disk persistence (rebuilt on startup)
    this.tfidf = new TfIdfEngine("/dev/null");
    this.reindex();
  }

  /**
   * Precompute and cache embeddings for all skill descriptions.
   * Call once at startup (after provider is ready) to avoid re-computing on every
   * semantic re-rank. Skill descriptions are static — no need to recompute.
   */
  async precomputeEmbeddings(): Promise<void> {
    if (!this.provider) return;
    const skills = this.registry.listEnabled();
    let computed = 0;
    for (const skill of skills) {
      const key = `${skill.name}:::${skill.description}`;
      if (this.skillEmbeddingCache.has(key)) continue;
      try {
        const result = await this.provider.embed(`${skill.name} ${skill.description}`);
        if (result.embedding && result.embedding.length > 0) {
          this.skillEmbeddingCache.set(key, result.embedding);
          computed++;
        }
      } catch {
        // Non-fatal — embedding may not be supported
      }
    }
    if (computed > 0) {
      log.engine.debug(`[IntentRouter] Precomputed ${computed} skill embeddings`);
    }
  }

  /** Rebuild the BM25 index from current registry contents */
  reindex(): void {
    const skills = this.registry.listEnabled();

    // Remove all existing documents first
    for (const skill of this.registry.listAll()) {
      this.tfidf.removeDocument(skill.name);
    }

    for (const skill of skills) {
      // Convert snake_case to space-separated for better BM25 matching
      const spacedName = skill.name.replace(/_/g, " ");

      this.tfidf.addDocument(skill.name, {
        title: spacedName,
        tags: skill.description,
        content: skill.instructions,
      });
    }

    log.engine.debug(
      `[IntentRouter] Indexed ${skills.length} skills for BM25 retrieval`,
    );
  }

  /** Find top-N relevant skills for a user message */
  async route(
    userMessage: string,
    maxResults: number = 3,
  ): Promise<IntentMatch[]> {
    // 1. Check cache (SHA256 of full message — no collision)
    const cacheKey = createHash("sha256").update(userMessage).digest("hex");
    const cached = this.cache.get(cacheKey);
    if (cached) {
      log.engine.debug(`[IntentRouter] Cache hit`);
      return cached.slice(0, maxResults);
    }

    // 2. BM25 retrieval (top 25 — semantic re-ranking will narrow further)
    const bm25Results = this.tfidf.search(userMessage, 25);

    if (bm25Results.length === 0) {
      log.engine.debug("[IntentRouter] No BM25 matches found");
      return [];
    }

    // Map BM25 results to IntentMatch
    let matches: IntentMatch[] = [];
    for (const result of bm25Results) {
      const skill = this.registry.get(result.id);
      if (!skill) continue;

      matches.push({
        skill,
        score: result.score,
        method: "bm25",
      });
    }

    // 3. Usage-weighted re-ranking (boost by recency-adjusted success rate)
    if (this.tracker) {
      for (const match of matches) {
        const multiplier = this.tracker.getUsageMultiplier(match.skill.name);
        match.score *= multiplier;
        match.method = "bm25+usage";
      }

      matches.sort((a, b) => b.score - a.score);
    }

    // Filter below minimum score
    matches = matches.filter((m) => m.score >= IntentRouter.MIN_SCORE);

    if (matches.length === 0) {
      log.engine.debug(
        "[IntentRouter] All matches below minimum score threshold",
      );
      return [];
    }

    // 4. Semantic re-ranking (Tier 3) — optional, only if embeddings are available
    if (matches.length > 1) {
      const semanticMatches = await this.rerankWithSemantics(
        userMessage,
        matches.slice(0, 10),
      );
      if (semanticMatches) {
        matches = semanticMatches;
        log.engine.debug(
          `[IntentRouter] Semantic re-ranking applied, top: ${matches[0]?.skill.name}`,
        );
      }
    }

    // 5. Overlap deduplication (Tier 4)
    const beforeDedup = matches.length;
    matches = this.deduplicateOverlapping(matches);
    if (matches.length < beforeDedup) {
      log.engine.debug(
        `[IntentRouter] Deduplication removed ${beforeDedup - matches.length} overlapping skill(s)`,
      );
    }

    // 6. LLM semantic validation (Tier 5) — always validates top result.
    // BM25 scores are unnormalized integers (5-15+), not confidence values,
    // so high scores do NOT mean "good match". A word like "find" in a user
    // message easily scores a skill named "duplicate_finder" near the top.
    // The LLM gate is the only reliable way to catch these false positives.
    if (this.provider && matches.length > 0) {
      matches = await this.disambiguate(userMessage, matches);
    }

    const finalResults = matches.slice(0, maxResults);

    // 7. Cache the result (LRU eviction)
    if (this.cache.size >= IntentRouter.MAX_CACHE) {
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) {
        this.cache.delete(firstKey);
      }
    }
    this.cache.set(cacheKey, finalResults);

    log.engine.info(
      `[IntentRouter] Routed to: ${finalResults.map((m) => `${m.skill.name}(${m.score.toFixed(2)},${m.method})`).join(", ")}`,
    );

    return finalResults;
  }

  /** LLM semantic validation: confirms top result is relevant, supports "none" answer */
  private async disambiguate(
    userMessage: string,
    candidates: IntentMatch[],
  ): Promise<IntentMatch[]> {
    if (!this.provider) return candidates;

    // Build a concise prompt listing candidate skills
    const skillList = candidates
      .slice(0, 8)
      .map((m, i) => `${i + 1}. ${m.skill.name} — ${m.skill.description}`)
      .join("\n");

    const prompt = [
      `User request: "${userMessage}"`,
      "",
      "Which of these skills is genuinely useful for this specific request?",
      "If NONE of them match, respond with exactly: none",
      "",
      skillList,
      "",
      "Respond with ONLY the skill name that matches, or 'none' if no skill is relevant.",
    ].join("\n");

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 64 },
      );

      const chosenName = response.content.trim().toLowerCase();

      // LLM said none of the skills match — return empty list
      if (chosenName === "none" || chosenName.startsWith("none")) {
        log.engine.info(
          `[IntentRouter] LLM rejected all BM25 matches for: "${userMessage.slice(0, 60)}"`,
        );
        return [];
      }

      // First try exact match (case-insensitive)
      let chosenIndex = candidates.findIndex(
        (m) => m.skill.name.toLowerCase() === chosenName,
      );

      // Fall back to fuzzy matching if exact match fails
      if (chosenIndex === -1) {
        chosenIndex = this.fuzzyMatch(chosenName, candidates);
        if (chosenIndex >= 0) {
          log.engine.debug(
            `[IntentRouter] Fuzzy matched "${chosenName}" to "${candidates[chosenIndex].skill.name}"`,
          );
        }
      }

      if (chosenIndex >= 0) {
        // LLM selected a specific skill — return ONLY that one.
        // BM25 ranked skills that were not chosen are irrelevant and should not be injected.
        const chosen = candidates[chosenIndex];
        chosen.method = "llm";
        log.engine.info(`[IntentRouter] LLM validated: ${chosen.skill.name}`);
        return [chosen];
      } else {
        // LLM returned something unrecognizable — treat as no match
        log.engine.info(
          `[IntentRouter] LLM response "${chosenName}" unrecognized — skipping skill injection`,
        );
        return [];
      }
    } catch (err) {
      // Fall back to BM25 top result only on LLM failure (don't inject noise from all candidates)
      log.engine.debug(
        `[IntentRouter] LLM validation failed, using BM25 top result only: ${err instanceof Error ? err.message : String(err)}`,
      );
      return candidates.slice(0, 1);
    }
  }

  /** Fuzzy name match: finds best skill name match using normalized Levenshtein distance */
  private fuzzyMatch(chosenName: string, candidates: IntentMatch[]): number {
    const normalized = (s: string) =>
      s.toLowerCase().replace(/_/g, " ").replace(/-/g, " ").trim();

    let bestIndex = -1;
    let bestScore = Infinity;

    for (let i = 0; i < candidates.length; i++) {
      const name = candidates[i].skill.name;
      const dist = this.levenshteinDistance(
        normalized(chosenName),
        normalized(name),
      );
      // Normalize by max length to get a 0-1 score
      const maxLen = Math.max(
        normalized(chosenName).length,
        normalized(name).length,
      );
      const score = maxLen === 0 ? 0 : dist / maxLen;
      if (score < bestScore) {
        bestScore = score;
        bestIndex = i;
      }
    }

    // Threshold: fuzzy match if normalized distance < 0.3 (70% similar)
    return bestScore < 0.3 ? bestIndex : -1;
  }

  /** Levenshtein distance between two strings */
  private levenshteinDistance(a: string, b: string): number {
    const m = a.length;
    const n = b.length;
    if (m === 0) return n;
    if (n === 0) return m;
    const dp: number[][] = Array.from({ length: m + 1 }, (_, i) =>
      Array.from({ length: n + 1 }, (_, j) => (i === 0 ? j : j === 0 ? i : 0)),
    );
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        dp[i][j] =
          a[i - 1] === b[j - 1]
            ? dp[i - 1][j - 1]
            : 1 + Math.min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1]);
      }
    }
    return dp[m][n];
  }

  /** Cosine similarity between two embedding vectors */
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

  /** Tier 3: Semantic re-ranking using embeddings. Returns null if embeddings unavailable. */
  private async rerankWithSemantics(
    userMessage: string,
    candidates: IntentMatch[],
  ): Promise<IntentMatch[] | null> {
    if (!this.provider) return null;

    try {
      const msgEmbed = await this.provider.embed(userMessage);
      if (!msgEmbed.embedding || msgEmbed.embedding.length === 0) {
        return null; // Provider doesn't support embeddings
      }

      const scores = await Promise.all(
        candidates.map(async (m) => {
          try {
            // Use cached embedding if available — avoids an LLM call per skill
            const cacheKey = `${m.skill.name}:::${m.skill.description}`;
            let skillEmbedding = this.skillEmbeddingCache.get(cacheKey);
            if (!skillEmbedding) {
              const skillEmbed = await this.provider!.embed(
                `${m.skill.name} ${m.skill.description}`,
              );
              if (!skillEmbed.embedding || skillEmbed.embedding.length === 0) {
                return 0;
              }
              skillEmbedding = skillEmbed.embedding;
              this.skillEmbeddingCache.set(cacheKey, skillEmbedding);
            }
            return this.cosineSimilarity(msgEmbed.embedding, skillEmbedding);
          } catch {
            return 0;
          }
        }),
      );

      // Normalize BM25 scores to 0-1 range using max
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
      return null; // Semantic re-ranking failed — fall back to BM25+usage
    }
  }

  /** Tier 4: Remove overlapping skills using Jaccard similarity on tokenized instructions */
  private deduplicateOverlapping(matches: IntentMatch[]): IntentMatch[] {
    if (matches.length <= 1) return matches;

    const tokenize = (text: string): Set<string> =>
      new Set(
        text
          .toLowerCase()
          .split(/[^a-z0-9]+/)
          .filter((w) => w.length >= 3),
      );

    const jaccard = (a: Set<string>, b: Set<string>): number => {
      let intersection = 0;
      for (const x of a) {
        if (b.has(x)) intersection++;
      }
      const union = a.size + b.size - intersection;
      return union === 0 ? 0 : intersection / union;
    };

    const result: IntentMatch[] = [];
    const used = new Set<string>();

    for (const match of matches) {
      if (used.has(match.skill.name)) continue;

      const tokensA = tokenize(match.skill.instructions);
      let hasOverlap = false;

      for (const kept of result) {
        const tokensB = tokenize(kept.skill.instructions);
        if (jaccard(tokensA, tokensB) > 0.5) {
          hasOverlap = true;
          break;
        }
      }

      if (!hasOverlap) {
        result.push(match);
        used.add(match.skill.name);
      }
    }

    // Re-sort by score (dedup may have disturbed order)
    result.sort((a, b) => b.score - a.score);

    // Update method flag if any dedup happened
    if (result.length < matches.length) {
      for (const m of result) {
        if (m.method.startsWith("bm25+usage")) {
          m.method =
            m.method === "bm25+usage+semantic"
              ? "bm25+usage+semantic+dedup"
              : "bm25+usage+dedup";
        }
      }
    }

    return result;
  }

  clearCache(): void {
    this.cache.clear();
  }
}
