/**
 * StackOwl — Pellet Deduplicator
 *
 * AI-driven deduplication layer for the pellet knowledge store.
 * Before saving a new pellet, evaluates similarity against existing
 * pellets using BM25 scoring and optional LLM merge decisions.
 *
 * Verdicts:
 *   CREATE    — new topic, save normally
 *   SKIP      — existing pellet already covers this
 *   MERGE     — combine new + existing into one improved pellet
 *   SUPERSEDE — new pellet replaces the old one entirely
 */

import type { ModelProvider } from "../providers/base.js";
import type { Pellet } from "./store.js";
import { log } from "../logger.js";

/** Semantic similarity search callback — provided by LancePelletStore */
export type SimilarFn = (
  pellet: Pellet,
  limit: number,
) => Promise<Array<{ pellet: Pellet; score: number }>>;

// ─── JSON Sanitizer ─────────────────────────────────────────────

/**
 * Walk a JSON string character-by-character. When inside a quoted string,
 * replace raw control characters (0x00–0x1f, 0x7f) with spaces so that
 * JSON.parse doesn't choke on them. Outside strings, these characters
 * are structural whitespace (\n, \t, \r) and are left alone.
 */
function sanitizeJsonStrings(raw: string): string {
  const out: string[] = [];
  let inString = false;
  let escaped = false;

  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    const code = raw.charCodeAt(i);

    if (escaped) {
      out.push(ch);
      escaped = false;
      continue;
    }

    if (ch === "\\" && inString) {
      out.push(ch);
      escaped = true;
      continue;
    }

    if (ch === '"') {
      inString = !inString;
      out.push(ch);
      continue;
    }

    // Inside a string: replace control chars with space
    if (inString && (code < 0x20 || code === 0x7f)) {
      out.push(" ");
      continue;
    }

    out.push(ch);
  }

  return out.join("");
}

// ─── Types ──────────────────────────────────────────────────────

export type DedupVerdict = "CREATE" | "MERGE" | "SUPERSEDE" | "SKIP";

export interface DedupResult {
  verdict: DedupVerdict;
  reasoning: string;
  /** The existing pellet that was matched (MERGE/SUPERSEDE/SKIP) */
  targetPelletId?: string;
  /** LLM-produced merged content (MERGE only) */
  mergedContent?: string;
  /** LLM-produced merged title (MERGE only) */
  mergedTitle?: string;
  /** Union of tags from both pellets (MERGE only) */
  mergedTags?: string[];
}

export interface DedupConfig {
  /** Enable deduplication. Default: true */
  enabled: boolean;
  /** Normalized BM25 similarity threshold to trigger LLM check. Default: 0.4 */
  similarityThreshold: number;
  /** Normalized similarity above which to auto-skip without LLM. Default: 0.8 */
  skipThreshold: number;
  /** Use LLM for merge/supersede decisions. Default: true */
  useLlm: boolean;
  /** Max candidates to consider from BM25. Default: 3 */
  maxCandidates: number;
}

export const DEFAULT_DEDUP_CONFIG: DedupConfig = {
  enabled: true,
  /** Cosine similarity threshold to trigger LLM check (was 0.4 BM25-normalized) */
  similarityThreshold: 0.65,
  /** Cosine similarity above which to auto-skip without LLM */
  skipThreshold: 0.85,
  useLlm: true,
  maxCandidates: 3,
};

// ─── Deduplicator ───────────────────────────────────────────────

export class PelletDeduplicator {
  private config: DedupConfig;

  constructor(
    /** Vector similarity search function — provided by LancePelletStore */
    private searchSimilar: SimilarFn,
    private provider?: ModelProvider,
    config?: Partial<DedupConfig>,
  ) {
    this.config = { ...DEFAULT_DEDUP_CONFIG, ...config };
  }

  /**
   * Evaluate an incoming pellet against existing ones.
   * Returns a verdict with instructions for PelletStore.
   */
  async evaluate(incoming: Pellet): Promise<DedupResult> {
    if (!this.config.enabled) {
      return { verdict: "CREATE", reasoning: "dedup disabled" };
    }

    try {
      return await this.doEvaluate(incoming);
    } catch (err) {
      log.engine.info(
        `[PelletDedup] Evaluation error, defaulting to CREATE: ${err instanceof Error ? err.message : String(err)}`,
      );
      return { verdict: "CREATE", reasoning: "dedup evaluation failed" };
    }
  }

  private async doEvaluate(incoming: Pellet): Promise<DedupResult> {
    if (!incoming.title && !incoming.content) {
      return { verdict: "CREATE", reasoning: "no indexable content" };
    }

    // Vector similarity search — scores are cosine similarity (0–1)
    const candidates = await this.searchSimilar(
      incoming,
      this.config.maxCandidates,
    );

    if (candidates.length === 0) {
      return { verdict: "CREATE", reasoning: "no similar pellets found" };
    }

    const best = candidates[0];
    const similarity = best.score;

    log.engine.debug(
      `[PelletDedup] Best match: "${best.pellet.id}" (cosine_sim=${similarity.toFixed(3)})`,
    );

    if (similarity < this.config.similarityThreshold) {
      return {
        verdict: "CREATE",
        reasoning: `cosine similarity ${similarity.toFixed(2)} below threshold ${this.config.similarityThreshold}`,
      };
    }

    const existing = best.pellet;

    // High similarity without LLM — auto-skip
    if (similarity >= this.config.skipThreshold && !this.config.useLlm) {
      return {
        verdict: "SKIP",
        reasoning: `cosine similarity ${similarity.toFixed(2)} above skip threshold; existing pellet covers this`,
        targetPelletId: existing.id,
      };
    }

    // Use LLM for nuanced decision
    if (this.config.useLlm && this.provider) {
      return await this.decideWithLlm(incoming, existing, similarity);
    }

    // Heuristic fallback (no LLM available)
    return this.decideHeuristic(incoming, existing, similarity);
  }

  /**
   * LLM-powered merge decision.
   * Falls back to heuristic if the LLM call fails.
   */
  private async decideWithLlm(
    incoming: Pellet,
    existing: Pellet,
    similarity: number,
  ): Promise<DedupResult> {
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
            content:
              "You are a knowledge base curator. Output only valid JSON. Be concise.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0, maxTokens: 1024 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```")) {
        jsonStr = jsonStr
          .replace(/^```json?/, "")
          .replace(/```$/, "")
          .trim();
      }

      // Strip JS-style comments that LLMs sometimes echo from the prompt
      jsonStr = jsonStr.replace(/\/\/[^\n]*/g, "");
      // Strip trailing commas before } or ]
      jsonStr = jsonStr.replace(/,\s*([}\]])/g, "$1");

      // Extract JSON object if embedded in other text
      const jsonMatch = jsonStr.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        jsonStr = jsonMatch[0];
      }

      // Sanitize control characters inside JSON string values.
      // Walk the string tracking whether we're inside quotes; replace
      // control chars (0x00-0x1f, 0x7f) with spaces when inside a string.
      jsonStr = sanitizeJsonStrings(jsonStr);

      const parsed = JSON.parse(jsonStr);
      const verdict = this.parseVerdict(parsed.verdict);

      log.engine.info(
        `[PelletDedup] LLM verdict: ${verdict} — "${parsed.reasoning}" (similarity=${similarity.toFixed(2)})`,
      );

      const result: DedupResult = {
        verdict,
        reasoning: parsed.reasoning || "LLM decision",
        targetPelletId: existing.id,
      };

      if (verdict === "MERGE") {
        result.mergedContent = parsed.merged_content || incoming.content;
        result.mergedTitle = parsed.merged_title || existing.title;
        result.mergedTags = Array.isArray(parsed.merged_tags)
          ? parsed.merged_tags
          : [...new Set([...existing.tags, ...incoming.tags])];
      }

      return result;
    } catch (err) {
      log.engine.info(
        `[PelletDedup] LLM decision failed, using heuristic: ${err instanceof Error ? err.message : String(err)}`,
      );
      return this.decideHeuristic(incoming, existing, similarity);
    }
  }

  /**
   * Fast heuristic fallback when LLM is unavailable.
   * Uses title word overlap and content length comparison.
   */
  private decideHeuristic(
    incoming: Pellet,
    existing: Pellet,
    similarity: number,
  ): DedupResult {
    // Compute title word overlap
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
    const titleOverlap =
      incomingWords.size > 0 ? overlap / incomingWords.size : 0;

    // Very high similarity + high title overlap → SKIP
    if (similarity >= this.config.skipThreshold && titleOverlap >= 0.6) {
      return {
        verdict: "SKIP",
        reasoning: `heuristic: similarity=${similarity.toFixed(2)}, titleOverlap=${titleOverlap.toFixed(2)} — existing covers this`,
        targetPelletId: existing.id,
      };
    }

    // High similarity + new content is longer → SUPERSEDE
    if (
      similarity >= 0.6 &&
      incoming.content.length > existing.content.length * 1.5
    ) {
      return {
        verdict: "SUPERSEDE",
        reasoning: `heuristic: new content is ${Math.round((incoming.content.length / existing.content.length) * 100)}% longer`,
        targetPelletId: existing.id,
      };
    }

    // Medium similarity → CREATE (let both exist)
    return {
      verdict: "CREATE",
      reasoning: `heuristic: similarity=${similarity.toFixed(2)} — different enough to keep both`,
    };
  }

  private parseVerdict(raw: string): DedupVerdict {
    const upper = (raw || "").toUpperCase().trim();
    if (upper === "MERGE" || upper === "SUPERSEDE" || upper === "SKIP") {
      return upper;
    }
    return "CREATE";
  }
}
