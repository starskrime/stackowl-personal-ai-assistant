/**
 * StackOwl — Opinion Injector
 *
 * Gives the owl a voice by surfacing its formed opinions when they're
 * relevant to what the user is asking. Prevents the assistant from
 * always giving bland "both are good" non-answers.
 *
 * Flow:
 *   1. Score existing opinions against current message (keyword overlap + topic match)
 *   2. If a high-confidence opinion (>= 0.75) is relevant (>= 0.5 score), return it
 *   3. After the response is generated, asynchronously form a new opinion on the topic
 *      and store it in OwlInnerLife for next time
 *
 * The injected opinion is a 1-sentence addition appended to the system prompt:
 *   "I have a view on this: [stance]. You don't have to agree — but you asked."
 *
 * Architecture: stateless injector. Reads from OwlInnerLife.getState().
 * Does NOT modify state synchronously — only queues async opinion formation.
 */

import type { OwlOpinion } from "./inner-life.js";
import type { OwlInnerLife } from "./inner-life.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface OpinionMatch {
  opinion: OwlOpinion;
  relevanceScore: number;  // 0–1
  injectionText: string;
}

// ─── OpinionInjector ──────────────────────────────────────────────

export class OpinionInjector {
  private readonly MIN_CONFIDENCE = 0.65;  // must be at least 65% confident
  private readonly MIN_RELEVANCE  = 0.45;  // must be at least 45% relevant

  /**
   * Find the most relevant, high-confidence opinion for this message.
   * Returns null if no opinion is relevant enough to surface.
   */
  findRelevant(
    message: string,
    opinions: OwlOpinion[],
  ): OpinionMatch | null {
    if (opinions.length === 0) return null;

    const msgWords = this.tokenize(message);
    let best: OpinionMatch | null = null;

    for (const opinion of opinions) {
      if (opinion.confidence < this.MIN_CONFIDENCE) continue;

      const relevance = this.scoreRelevance(msgWords, opinion.topic);
      if (relevance < this.MIN_RELEVANCE) continue;

      if (!best || relevance > best.relevanceScore) {
        best = {
          opinion,
          relevanceScore: relevance,
          injectionText: this.buildInjection(opinion),
        };
      }
    }

    if (best) {
      log.engine.debug(
        `[OpinionInjector] Surfacing opinion on "${best.opinion.topic}" ` +
        `(conf=${best.opinion.confidence.toFixed(2)}, relevance=${best.relevanceScore.toFixed(2)})`,
      );
    }

    return best;
  }

  /**
   * Format the opinion match as a system prompt addition.
   */
  formatForSystemPrompt(match: OpinionMatch): string {
    return (
      `\n[Your opinion — surface this naturally if it fits the conversation]\n` +
      `You have a view on "${match.opinion.topic}": ${match.opinion.stance}\n` +
      `Confidence: ${Math.round(match.opinion.confidence * 100)}%. ` +
      `You can mention this briefly if relevant — you don't need to lecture about it. ` +
      `One sentence is enough. If the user hasn't asked for your opinion directly, ` +
      `frame it as "for what it's worth..." or "my take is..."\n`
    );
  }

  /**
   * Asynchronously form a new opinion based on the topic + assistant response.
   * Fire-and-forget — never awaited on the response path.
   */
  async formOpinionAsync(
    message: string,
    innerLife: OwlInnerLife,
  ): Promise<void> {
    try {
      const topic = this.extractTopic(message);
      if (!topic) return;

      // formOpinion respects a 24h cooldown internally
      await innerLife.formOpinion(topic, message.slice(0, 300));
    } catch {
      // Non-fatal
    }
  }

  // ─── Private ─────────────────────────────────────────────────

  private scoreRelevance(msgWords: Set<string>, topic: string): number {
    const topicWords = this.tokenize(topic);
    if (topicWords.size === 0 || msgWords.size === 0) return 0;

    let overlap = 0;
    for (const word of topicWords) {
      if (msgWords.has(word)) overlap++;
    }

    // Jaccard similarity
    const union = new Set([...msgWords, ...topicWords]).size;
    return overlap / union;
  }

  private buildInjection(opinion: OwlOpinion): string {
    return opinion.stance;
  }

  private tokenize(text: string): Set<string> {
    const STOP = new Set([
      "a","an","the","is","are","was","be","do","it","in","on","at","to",
      "of","and","or","but","i","you","we","my","your","this","that","with",
      "for","not","can","what","how","why","when","should","would","could",
    ]);
    return new Set(
      text.toLowerCase()
        .replace(/[^a-z0-9 ]/g, " ")
        .split(/\s+/)
        .filter((w) => w.length >= 3 && !STOP.has(w)),
    );
  }

  private extractTopic(message: string): string | null {
    // Take the first 60 chars of the message, trimmed — good enough as topic
    const trimmed = message.trim().slice(0, 60).replace(/\?$/, "").trim();
    return trimmed.length >= 10 ? trimmed : null;
  }
}
