/**
 * StackOwl — Loop Detector
 *
 * Detects when a user is asking semantically similar questions repeatedly
 * without getting a satisfying answer — a strong signal that the assistant
 * has been giving partial or surface-level responses to a deeper need.
 *
 * Algorithm:
 *   1. Tokenize current message → keyword set (strip stop words)
 *   2. Search episodic memory for past episodes whose summary overlaps
 *      with the current keywords (score ≥ 0.35)
 *   3. Count semantically similar past questions within 30 days
 *   4. If ≥ 3 found → loop detected. Build a LoopCluster with topic summary.
 *   5. Return a LoopDetectionResult that causes the gateway to force
 *      DELEGATE routing with a special root-cause-finding prompt.
 *
 * Architecture: read-only query — no writes, no LLM calls, no side effects.
 * Called on every message; must be fast (<100ms typical).
 */

import type { EpisodicMemory } from "../memory/episodic.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface LoopCluster {
  /** Inferred topic the user keeps asking about */
  topic: string;
  /** How many similar past questions were found */
  questionCount: number;
  /** ISO date of earliest similar question */
  firstSeen: string;
  /** ISO date of most recent similar question */
  lastSeen: string;
  /** Up to 3 verbatim past question summaries */
  sampleQuestions: string[];
}

export interface LoopDetectionResult {
  isLoop: boolean;
  cluster?: LoopCluster;
  /** Prompt addition to inject into the system prompt when loop detected */
  systemPromptHint?: string;
}

// ─── LoopDetector ─────────────────────────────────────────────────

export class LoopDetector {
  /** Minimum similar episodes before flagging a loop */
  private readonly LOOP_THRESHOLD = 3;
  /** Episodes older than this are excluded (milliseconds) */
  private readonly MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
  /** Minimum relevance score to count an episode as "similar" */
  private readonly MIN_SCORE = 0.30;

  /**
   * Detect if the current message is part of a recurring question loop.
   *
   * @param currentMessage  The user's current message text
   * @param episodicMemory  The episodic memory store to search
   * @param userId          Optional user ID for scoped search
   */
  async detect(
    currentMessage: string,
    episodicMemory: EpisodicMemory | undefined,
    _userId?: string,
  ): Promise<LoopDetectionResult> {
    if (!episodicMemory) return { isLoop: false };

    // Extract keywords for search
    const keywords = this.extractKeywords(currentMessage);
    if (keywords.length < 2) return { isLoop: false }; // too short to be meaningful

    try {
      // Search episodic memory for semantically similar past conversations
      const episodes = await episodicMemory.searchWithScoring(
        keywords.join(" "),
        10,     // retrieve up to 10
        undefined,
        this.MIN_SCORE,
      );

      if (episodes.length === 0) return { isLoop: false };

      // Filter to recent episodes (within MAX_AGE_MS)
      const cutoff = Date.now() - this.MAX_AGE_MS;
      const recent = episodes.filter((ep) => {
        const ts = typeof ep.date === "string"
          ? new Date(ep.date).getTime()
          : (ep.date as number);
        return ts > cutoff;
      });

      if (recent.length < this.LOOP_THRESHOLD) return { isLoop: false };

      // Build cluster
      const sorted = [...recent].sort((a, b) => {
        const aTs = new Date(a.date).getTime();
        const bTs = new Date(b.date).getTime();
        return aTs - bTs;
      });

      const topic = this.inferTopic(keywords, recent.map((e) => e.summary));
      const cluster: LoopCluster = {
        topic,
        questionCount: recent.length,
        firstSeen: String(sorted[0].date),
        lastSeen: String(sorted[sorted.length - 1].date),
        sampleQuestions: sorted.slice(-3).map((e) => e.summary.slice(0, 100)),
      };

      log.engine.info(
        `[LoopDetector] Loop detected — topic="${topic}", ` +
        `count=${recent.length}, firstSeen=${cluster.firstSeen.slice(0, 10)}`,
      );

      return {
        isLoop: true,
        cluster,
        systemPromptHint: this.buildHint(cluster),
      };
    } catch (err) {
      log.engine.debug(`[LoopDetector] Search failed: ${err instanceof Error ? err.message : err}`);
      return { isLoop: false };
    }
  }

  // ─── Private ─────────────────────────────────────────────────

  private extractKeywords(message: string): string[] {
    const STOP = new Set([
      "a","an","the","is","are","was","be","do","it","in","on","at","to",
      "of","and","or","but","i","you","we","my","your","this","that","with",
      "for","not","can","what","how","why","when","should","could","would",
      "please","help","me","tell","know","want","need","think","get","make",
    ]);
    return message.toLowerCase()
      .replace(/[^a-z0-9 ]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length >= 4 && !STOP.has(w))
      .slice(0, 8);
  }

  private inferTopic(keywords: string[], summaries: string[]): string {
    // Use the first two most meaningful keywords as the topic label
    const topicWords = keywords.slice(0, 3).join(" ");
    // If it's too short, fall back to a fragment of the first summary
    if (topicWords.length < 8 && summaries.length > 0) {
      return summaries[0].slice(0, 60);
    }
    return topicWords;
  }

  private buildHint(cluster: LoopCluster): string {
    return (
      `\n[Loop Pattern Detected]\n` +
      `The user has asked about "${cluster.topic}" ${cluster.questionCount} times ` +
      `in the last 30 days without finding a complete answer.\n` +
      `Past questions on this topic:\n` +
      cluster.sampleQuestions.map((q) => `  - ${q}`).join("\n") + "\n" +
      `Do NOT give another partial answer. This time:\n` +
      `1. Acknowledge the pattern directly: "I've noticed you've asked about this several times."\n` +
      `2. Identify WHY previous answers were insufficient (ask one direct question or make an assumption).\n` +
      `3. Solve the ROOT CAUSE, not just the surface question.\n` +
      `Be direct. The user is frustrated. One thorough answer is worth more than three partial ones.\n`
    );
  }
}
