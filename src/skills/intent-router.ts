/**
 * StackOwl — Intent Router
 *
 * Enterprise-grade skill matching that replaces primitive keyword scoring.
 * Three-tier routing:
 *
 *   Tier 1 — BM25 retrieval (sub-ms, offline)
 *     Uses TfIdfEngine with field boosting: name(3x) > description(2x) > instructions(1x)
 *     Returns top-10 candidates
 *
 *   Tier 2 — Usage-weighted re-ranking
 *     Boosts skills with higher success rates from SkillTracker
 *     Penalizes skills that frequently fail
 *
 *   Tier 3 — LLM disambiguation (optional, only when ambiguous)
 *     If top-2 BM25 scores are within 15% of each other AND both score > threshold,
 *     make a single LLM call: "Which skill best matches this request?"
 *     This avoids LLM calls for clear-cut matches while resolving ambiguity.
 *
 * Architecture:
 *   - TfIdfEngine is used in-memory (no disk persistence for skill index — rebuilt on startup)
 *   - LLM disambiguation is gated: only fires when ambiguous AND provider is available
 *   - Results are cached per message prefix (100 chars) with 200-entry LRU
 */

import type { ModelProvider } from '../providers/base.js';
import type { Skill } from './types.js';
import type { SkillsRegistry } from './registry.js';
import type { SkillTracker } from './tracker.js';
import { TfIdfEngine } from '../pellets/tfidf.js';
import { log } from '../logger.js';

export interface IntentMatch {
  skill: Skill;
  score: number;
  method: 'bm25' | 'bm25+usage' | 'llm';
}

export class IntentRouter {
  private registry: SkillsRegistry;
  private provider: ModelProvider | null;
  private tracker: SkillTracker | null;
  private tfidf: TfIdfEngine;
  private cache: Map<string, IntentMatch[]> = new Map();
  private static readonly CACHE_KEY_LEN = 100;
  private static readonly MAX_CACHE = 200;
  private static readonly AMBIGUITY_THRESHOLD = 0.15; // 15% score difference = ambiguous
  private static readonly MIN_SCORE = 0.10; // minimum BM25 score to consider

  constructor(
    registry: SkillsRegistry,
    provider?: ModelProvider,
    tracker?: SkillTracker,
  ) {
    this.registry = registry;
    this.provider = provider ?? null;
    this.tracker = tracker ?? null;
    // In-memory TF-IDF — no disk persistence (rebuilt on startup)
    this.tfidf = new TfIdfEngine('/dev/null');
    this.reindex();
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
      const spacedName = skill.name.replace(/_/g, ' ');

      this.tfidf.addDocument(skill.name, {
        title: spacedName,
        tags: skill.description,
        content: skill.instructions.slice(0, 500),
      });
    }

    log.engine.debug(`[IntentRouter] Indexed ${skills.length} skills for BM25 retrieval`);
  }

  /** Find top-N relevant skills for a user message */
  async route(userMessage: string, maxResults: number = 3): Promise<IntentMatch[]> {
    // 1. Check cache
    const cacheKey = userMessage.slice(0, IntentRouter.CACHE_KEY_LEN).toLowerCase();
    const cached = this.cache.get(cacheKey);
    if (cached) {
      log.engine.debug(`[IntentRouter] Cache hit for "${cacheKey.slice(0, 40)}..."`);
      return cached.slice(0, maxResults);
    }

    // 2. BM25 retrieval (top 10)
    const bm25Results = this.tfidf.search(userMessage, 10);

    if (bm25Results.length === 0) {
      log.engine.debug('[IntentRouter] No BM25 matches found');
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
        method: 'bm25',
      });
    }

    // 3. Usage-weighted re-ranking (boost by success rate)
    if (this.tracker) {
      for (const match of matches) {
        const successRate = this.tracker.getSuccessRate(match.skill.name);
        if (successRate !== undefined) {
          // Boost: success rate 0.0–1.0 maps to multiplier 0.7–1.3
          const multiplier = 0.7 + (successRate * 0.6);
          match.score *= multiplier;
          match.method = 'bm25+usage';
        }
      }

      // Re-sort after usage weighting
      matches.sort((a, b) => b.score - a.score);
    }

    // Filter below minimum score
    matches = matches.filter((m) => m.score >= IntentRouter.MIN_SCORE);

    if (matches.length === 0) {
      log.engine.debug('[IntentRouter] All matches below minimum score threshold');
      return [];
    }

    // 4. If ambiguous: LLM disambiguation
    if (
      this.provider &&
      matches.length >= 2 &&
      this.isAmbiguous(matches[0], matches[1])
    ) {
      log.engine.info(
        `[IntentRouter] Ambiguous match: "${matches[0].skill.name}" (${matches[0].score.toFixed(3)}) vs "${matches[1].skill.name}" (${matches[1].score.toFixed(3)}) — requesting LLM disambiguation`,
      );
      matches = await this.disambiguate(userMessage, matches);
    }

    const finalResults = matches.slice(0, maxResults);

    // 5. Cache the result (LRU eviction)
    if (this.cache.size >= IntentRouter.MAX_CACHE) {
      // Evict oldest entry (first key in insertion order)
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) {
        this.cache.delete(firstKey);
      }
    }
    this.cache.set(cacheKey, finalResults);

    log.engine.info(
      `[IntentRouter] Routed to: ${finalResults.map((m) => `${m.skill.name}(${m.score.toFixed(2)},${m.method})`).join(', ')}`,
    );

    return finalResults;
  }

  /** Check whether the top two matches are ambiguous */
  private isAmbiguous(first: IntentMatch, second: IntentMatch): boolean {
    if (first.score === 0) return false;
    const scoreDiff = (first.score - second.score) / first.score;
    return scoreDiff < IntentRouter.AMBIGUITY_THRESHOLD;
  }

  /** LLM disambiguation for ambiguous matches */
  private async disambiguate(
    userMessage: string,
    candidates: IntentMatch[],
  ): Promise<IntentMatch[]> {
    if (!this.provider) return candidates;

    // Build a concise prompt listing candidate skills
    const skillList = candidates
      .slice(0, 5) // Only send top 5 to keep the prompt small
      .map((m, i) => `${i + 1}. ${m.skill.name} — ${m.skill.description}`)
      .join('\n');

    const prompt = [
      `Given this user request: "${userMessage}"`,
      '',
      'Which of these skills best matches the request?',
      '',
      skillList,
      '',
      'Respond with ONLY the skill name that is the best match. Nothing else.',
    ].join('\n');

    try {
      const response = await this.provider.chat(
        [{ role: 'user', content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 128 },
      );

      const chosenName = response.content.trim().toLowerCase();

      // Find the chosen skill in candidates
      const chosenIndex = candidates.findIndex(
        (m) => m.skill.name.toLowerCase() === chosenName,
      );

      if (chosenIndex > 0) {
        // Move the LLM-chosen skill to the top
        const chosen = candidates[chosenIndex];
        chosen.method = 'llm';
        candidates.splice(chosenIndex, 1);
        candidates.unshift(chosen);
        log.engine.info(`[IntentRouter] LLM disambiguated to: ${chosen.skill.name}`);
      } else if (chosenIndex === 0) {
        // LLM agreed with BM25 ranking
        candidates[0].method = 'llm';
        log.engine.debug('[IntentRouter] LLM confirmed BM25 top result');
      } else {
        log.engine.debug(
          `[IntentRouter] LLM response "${chosenName}" did not match any candidate — keeping BM25 order`,
        );
      }
    } catch (err) {
      // Fall back to BM25 order on any LLM failure
      log.engine.debug(
        `[IntentRouter] LLM disambiguation failed, keeping BM25 order: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    return candidates;
  }

  clearCache(): void {
    this.cache.clear();
  }
}
