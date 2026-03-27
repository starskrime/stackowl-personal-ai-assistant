/**
 * StackOwl — Pellet Search (RAG)
 *
 * Replaces brute-force pellet injection with relevance-based retrieval.
 * Uses TF-IDF cosine similarity (zero external dependencies) to rank
 * pellets by relevance to the current query.
 *
 * WHY NOT a vector DB:
 *   - Zero dependency footprint — runs anywhere Node runs
 *   - Pellet corpus is small (100s, not millions)
 *   - TF-IDF is fast, deterministic, and surprisingly effective at this scale
 *
 * Upgrade path: swap the similarity function for embeddings + hnswlib
 * when the pellet corpus exceeds ~1000 items.
 */

import type { PelletStore } from "./store.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

interface ScoredPellet {
  content: string;
  domain: string;
  score: number;
}

interface PelletAttribution {
  pelletId: string;
  usedInSession: string;
  userRating?: "positive" | "negative" | "neutral";
  timestamp: number;
}

// ─── TF-IDF Helpers ──────────────────────────────────────────────

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 2);
}

function termFrequency(tokens: string[]): Map<string, number> {
  const tf = new Map<string, number>();
  for (const token of tokens) {
    tf.set(token, (tf.get(token) ?? 0) + 1);
  }
  // Normalize by document length
  const len = tokens.length || 1;
  for (const [k, v] of tf) {
    tf.set(k, v / len);
  }
  return tf;
}

function cosineSimilarity(
  a: Map<string, number>,
  b: Map<string, number>,
): number {
  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (const [term, weight] of a) {
    const bWeight = b.get(term) ?? 0;
    dotProduct += weight * bWeight;
    normA += weight * weight;
  }
  for (const [, weight] of b) {
    normB += weight * weight;
  }

  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dotProduct / denom;
}

// ─── Pellet Search ───────────────────────────────────────────────

export class PelletSearch {
  /** Attribution log for feedback loop */
  private attributions: PelletAttribution[] = [];

  constructor(private pelletStore: PelletStore) {}

  /**
   * Retrieve the top-K most relevant pellets for a given query.
   * Replaces the brute-force "dump all pellets into system prompt" approach.
   *
   * @param query - The user's message or topic
   * @param topK - Maximum number of pellets to return (default: 5)
   * @param minScore - Minimum relevance score to include (default: 0.05)
   */
  async search(
    query: string,
    topK: number = 5,
    minScore: number = 0.05,
  ): Promise<ScoredPellet[]> {
    const allPellets = await this.loadAllPellets();
    if (allPellets.length === 0) return [];

    const queryTokens = tokenize(query);
    if (queryTokens.length === 0) return [];

    const queryTf = termFrequency(queryTokens);

    // Build IDF from corpus
    const docCount = allPellets.length;
    const docFreq = new Map<string, number>();
    const pelletTfs: Map<string, number>[] = [];

    for (const pellet of allPellets) {
      const tokens = tokenize(pellet.content);
      const tf = termFrequency(tokens);
      pelletTfs.push(tf);

      const uniqueTerms = new Set(tokens);
      for (const term of uniqueTerms) {
        docFreq.set(term, (docFreq.get(term) ?? 0) + 1);
      }
    }

    // Apply IDF weighting to both query and document TF vectors
    const idf = (term: string): number => {
      const df = docFreq.get(term) ?? 0;
      return df > 0 ? Math.log(docCount / df) : 0;
    };

    const queryTfIdf = new Map<string, number>();
    for (const [term, tf] of queryTf) {
      queryTfIdf.set(term, tf * idf(term));
    }

    // Score each pellet
    const scored: ScoredPellet[] = allPellets.map((pellet, i) => {
      const pelletTfIdf = new Map<string, number>();
      for (const [term, tf] of pelletTfs[i]) {
        pelletTfIdf.set(term, tf * idf(term));
      }

      return {
        content: pellet.content,
        domain: pellet.domain,
        score: cosineSimilarity(queryTfIdf, pelletTfIdf),
      };
    });

    // Filter and sort
    const results = scored
      .filter((p) => p.score >= minScore)
      .sort((a, b) => b.score - a.score)
      .slice(0, topK);

    if (results.length > 0) {
      log.engine.info(
        `[PelletSearch] Retrieved ${results.length}/${allPellets.length} pellets ` +
          `(top score: ${results[0].score.toFixed(3)}, threshold: ${minScore})`,
      );
    }

    return results;
  }

  /**
   * Format retrieved pellets for system prompt injection.
   */
  async getRelevantContext(query: string, topK: number = 5): Promise<string> {
    const results = await this.search(query, topK);
    if (results.length === 0) return "";

    const lines = ["<relevant_knowledge>"];
    for (const pellet of results) {
      lines.push(`[${pellet.domain}] ${pellet.content}`);
    }
    lines.push("</relevant_knowledge>");
    return lines.join("\n");
  }

  // ─── Attribution / Feedback Loop ───────────────────────────────

  /**
   * Record which pellets were used in a session for the feedback loop.
   */
  recordAttribution(pelletId: string, sessionId: string): void {
    this.attributions.push({
      pelletId,
      usedInSession: sessionId,
      timestamp: Date.now(),
    });
  }

  /**
   * Record user feedback on a session (used to learn which pellets help).
   */
  recordFeedback(
    sessionId: string,
    rating: "positive" | "negative" | "neutral",
  ): void {
    for (const attr of this.attributions) {
      if (attr.usedInSession === sessionId) {
        attr.userRating = rating;
      }
    }
  }

  /**
   * Get pellets that were in context during positively-rated sessions.
   * These are the knowledge items that the user found helpful.
   */
  getEffectivePellets(): string[] {
    return this.attributions
      .filter((a) => a.userRating === "positive")
      .map((a) => a.pelletId);
  }

  // ─── Private ───────────────────────────────────────────────────

  private async loadAllPellets(): Promise<
    Array<{ content: string; domain: string }>
  > {
    try {
      const pellets = await this.pelletStore.listAll();
      return pellets.map((p) => ({
        content: p.content ?? "",
        domain: p.tags?.[0] ?? "general",
      }));
    } catch {
      return [];
    }
  }
}
