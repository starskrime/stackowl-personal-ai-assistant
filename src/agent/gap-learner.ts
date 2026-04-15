/**
 * StackOwl — Gap Learner
 *
 * When the assistant detects a knowledge or capability gap, this module:
 *   1. Proactively researches the topic using available tools (search, web)
 *   2. Saves what was learned as a pellet tagged "gap_learning"
 *   3. Returns a transparent "here's what I just learned" summary
 *   4. Enriches the retry context so the answer benefits from the learning
 *
 * This turns "I can't do that" → "I didn't know that, I just learned it, here's the answer."
 */

import { v4 as uuidv4 } from "uuid";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { PelletStore } from "../pellets/store.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { PendingCapabilityGap } from "../engine/runtime.js";
import { OwlEngine } from "../engine/runtime.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface GapLearningResult {
  /** Whether meaningful knowledge was actually gathered */
  learned: boolean;
  /** 1-3 sentence summary of what was learned */
  summary: string;
  /** Pellet ID if knowledge was saved to the store */
  pelletId?: string;
  /**
   * Additional context block to inject into the retry's memoryContext.
   * Formatted as a readable block the LLM can use directly.
   */
  enrichedContext?: string;
  /** Human-readable banner to prepend to the final response */
  userFacingNote: string;
}

// ─── GapLearner ───────────────────────────────────────────────────

export class GapLearner {
  private engine = new OwlEngine();

  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    private config: StackOwlConfig,
    private toolRegistry: ToolRegistry,
    private pelletStore: PelletStore,
  ) {}

  /**
   * Research the gap topic, save findings, and return what was learned.
   */
  async learn(
    gap: PendingCapabilityGap,
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<GapLearningResult> {
    const topic = this.extractTopic(gap);
    log.engine.info(`[GapLearner] Researching gap: "${topic}"`);

    await onProgress?.(
      `🔍 I notice a knowledge gap about "${topic}" — let me research this now...`,
    );

    try {
      const researchPrompt = this.buildResearchPrompt(gap, topic);

      const response = await this.engine.run(researchPrompt, {
        provider: this.provider,
        owl: this.owl,
        config: this.config,
        toolRegistry: this.toolRegistry,
        sessionHistory: [],
        skipGapDetection: true,
        isolatedTask: true,
        pelletStore: this.pelletStore,
      });

      const rawSummary = response.content.trim();

      // Treat very short or empty responses as "didn't learn much"
      if (!rawSummary || rawSummary.length < 50) {
        return this.noLearning(topic);
      }

      // Save as a pellet so this knowledge persists
      const pelletId = await this.saveLearningPellet(topic, gap, rawSummary);

      const enrichedContext = this.buildEnrichedContext(topic, rawSummary);

      log.engine.info(
        `[GapLearner] Learned about "${topic}", pellet: ${pelletId ?? "not saved"}`,
      );

      return {
        learned: true,
        summary: rawSummary,
        pelletId,
        enrichedContext,
        userFacingNote: this.buildUserNote(topic, rawSummary),
      };
    } catch (err) {
      log.engine.warn(
        `[GapLearner] Research failed: ${err instanceof Error ? err.message : err}`,
      );
      return this.noLearning(topic);
    }
  }

  // ─── Private helpers ──────────────────────────────────────────

  private extractTopic(gap: PendingCapabilityGap): string {
    // Use the user's original request as the topic — it's the most
    // human-readable signal of what they actually needed.
    const base = gap.userRequest || gap.description;
    return base.slice(0, 120).replace(/\n/g, " ").trim();
  }

  private buildResearchPrompt(gap: PendingCapabilityGap, topic: string): string {
    return [
      `[GAP LEARNING — research this topic using your available tools]`,
      ``,
      `I was asked: "${topic}"`,
      ``,
      `I detected a knowledge gap: ${gap.description}`,
      ``,
      `Your job:`,
      `1. Use web_search or web_fetch to find reliable information on this topic.`,
      `2. If no search tools are available, use what you know to synthesize a concise answer.`,
      `3. Summarize the key findings in 3-6 clear sentences.`,
      `4. Focus on: what it is, how it works, and any practical steps or considerations.`,
      ``,
      `Output ONLY the summary — no preamble, no "[DONE]" suffix.`,
    ].join("\n");
  }

  private async saveLearningPellet(
    topic: string,
    gap: PendingCapabilityGap,
    content: string,
  ): Promise<string | undefined> {
    try {
      const pellet = {
        id: uuidv4(),
        title: `Gap Learning: ${topic.slice(0, 80)}`,
        generatedAt: new Date().toISOString(),
        source: "gap-learner",
        owls: [this.owl.persona.name],
        tags: ["gap_learning", "auto_learned"],
        version: 1,
        content: [
          `## What Was Asked`,
          gap.userRequest,
          ``,
          `## Gap Detected`,
          gap.description,
          ``,
          `## What I Learned`,
          content,
        ].join("\n"),
      };

      await this.pelletStore.save(pellet, { skipDedup: true });
      return pellet.id;
    } catch (err) {
      log.engine.warn(
        `[GapLearner] Failed to save pellet: ${err instanceof Error ? err.message : err}`,
      );
      return undefined;
    }
  }

  private buildEnrichedContext(topic: string, summary: string): string {
    return [
      `[KNOWLEDGE JUST LEARNED — use this in your response]`,
      `Topic: ${topic}`,
      ``,
      summary,
      `[END LEARNED KNOWLEDGE]`,
    ].join("\n");
  }

  private buildUserNote(topic: string, summary: string): string {
    const shortSummary = summary.length > 300 ? summary.slice(0, 300) + "..." : summary;
    return [
      `🧠 *I noticed a knowledge gap about "${topic}" and just researched it.*`,
      ``,
      `Here's what I learned:`,
      shortSummary,
      ``,
      `I've saved this to my memory. Was this learning accurate? Feel free to correct me or share more context — I'll update what I know.`,
    ].join("\n");
  }

  private noLearning(topic: string): GapLearningResult {
    return {
      learned: false,
      summary: "",
      userFacingNote: `🧠 *I noticed a gap about "${topic}" but couldn't find enough information to fill it right now.*`,
    };
  }
}
