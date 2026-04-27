/**
 * StackOwl — Proactive Knowledge Generator
 *
 * Runs scheduled knowledge generation to fill gaps in the knowledge base.
 * Generates pellets from knowledge council sessions, dream reflexion, and skill evolution.
 */

import type { PelletStore, Pellet } from "./store.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { PelletGenerator } from "./generator.js";
import { KnowledgeBase } from "./knowledge-base.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface ProactiveGenerationConfig {
  councilIntervalHours: number;
  dreamEnabled: boolean;
  evolveSkillsEnabled: boolean;
  minGapAgeDays: number;
}

export const DEFAULT_CONFIG: ProactiveGenerationConfig = {
  councilIntervalHours: 12,
  dreamEnabled: true,
  evolveSkillsEnabled: true,
  minGapAgeDays: 30,
};

// ─── Proactive Generator ─────────────────────────────────────────

export class ProactiveKnowledgeGenerator {
  private generator: PelletGenerator;
  private knowledgeBase: KnowledgeBase;
  private lastCouncilRun: string = "";
  private lastDreamRun: string = "";
  private lastEvolveRun: string = "";

  constructor(
    private pelletStore: PelletStore,
    private provider: ModelProvider,
    private owl: OwlInstance,
    private config: StackOwlConfig,
    private generationConfig: Partial<ProactiveGenerationConfig> = {},
  ) {
    this.generator = new PelletGenerator();
    this.knowledgeBase = new KnowledgeBase(pelletStore);
    this.generationConfig = { ...DEFAULT_CONFIG, ...generationConfig };
  }

  /**
   * Evaluate knowledge gaps and return topics needing coverage.
   */
  async evaluateKnowledgeGaps(): Promise<string[]> {
    const gaps = await this.knowledgeBase.findCoverageGaps();
    const stats = await this.knowledgeBase.getStats();

    const staleTopics = stats.stalePellets
      .flatMap((p) => p.tags)
      .filter((tag) => {
        const tagPellets = stats.recentPellets.filter((p) => p.tags.includes(tag));
        return tagPellets.length === 0;
      });

    const combined = [...new Set([...gaps, ...staleTopics])];
    log.engine.info(
      `[ProactiveGenerator] Found ${combined.length} knowledge gaps: ${combined.slice(0, 5).join(", ")}${combined.length > 5 ? "..." : ""}`,
    );

    return combined;
  }

  /**
   * Run knowledge council: generate pellets to fill identified gaps.
   */
  async runKnowledgeCouncil(): Promise<Pellet[]> {
    const now = new Date();
    const hoursSinceLastRun = this.lastCouncilRun
      ? (now.getTime() - new Date(this.lastCouncilRun).getTime()) / (1000 * 60 * 60)
      : Infinity;

    if (hoursSinceLastRun < (this.generationConfig.councilIntervalHours ?? DEFAULT_CONFIG.councilIntervalHours)) {
      log.engine.debug(
        `[ProactiveGenerator] Skipping council (ran ${Math.round(hoursSinceLastRun)}h ago, interval: ${this.generationConfig.councilIntervalHours}h)`,
      );
      return [];
    }

    this.lastCouncilRun = now.toISOString();
    log.engine.info("[ProactiveGenerator] Running knowledge council session...");

    const gaps = await this.evaluateKnowledgeGaps();
    if (gaps.length === 0) {
      log.engine.info("[ProactiveGenerator] No knowledge gaps found");
      return [];
    }

    const pellets: Pellet[] = [];
    for (const topic of gaps.slice(0, 3)) {
      try {
        const pellet = await this.generateGapFillingPellet(topic);
        if (pellet) {
          pellets.push(pellet);
          await this.pelletStore.save(pellet);
        }
      } catch (err) {
        log.engine.warn(
          `[ProactiveGenerator] Failed to generate pellet for "${topic}": ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    log.engine.info(
      `[ProactiveGenerator] Knowledge council complete — ${pellets.length} pellets created`,
    );

    return pellets;
  }

  /**
   * Run dream reflexion: generate insight pellets from recent lessons.
   */
  async runDream(): Promise<Pellet[]> {
    if (!this.generationConfig.dreamEnabled) {
      log.engine.debug("[ProactiveGenerator] Dream disabled");
      return [];
    }

    const now = new Date();
    const hoursSinceLastRun = this.lastDreamRun
      ? (now.getTime() - new Date(this.lastDreamRun).getTime()) / (1000 * 60 * 60)
      : Infinity;

    if (hoursSinceLastRun < 24) {
      log.engine.debug("[ProactiveGenerator] Skipping dream (ran recently)");
      return [];
    }

    this.lastDreamRun = now.toISOString();
    log.engine.info("[ProactiveGenerator] Running dream reflexion session...");

    try {
      const recentPellets = (await this.pelletStore.listAll())
        .filter((p) => p.tags.includes("lesson-learned"))
        .slice(0, 5);

      if (recentPellets.length === 0) {
        log.engine.info("[ProactiveGenerator] No recent lessons to reflect on");
        return [];
      }

      const lessonsSummary = recentPellets
        .map((p) => `- ${p.title}: ${p.content.slice(0, 200)}`)
        .join("\n");

      const prompt =
        `You are reflecting on recent lessons learned to generate insights.\n\n` +
        `Recent lessons:\n${lessonsSummary}\n\n` +
        `Generate ONE pellet that captures the overarching insight from these lessons. ` +
        `Focus on the pattern or principle that connects these cases. ` +
        `Format as JSON with: id, title, tags (including "dream_insight"), content.`;

      const pellet = await this.generator.generate(prompt, "dream_reflexion", {
        provider: this.provider,
        owl: this.owl,
        config: this.config,
      });

      pellet.tags = [...new Set([...pellet.tags, "dream_insight", "reflexion"])];

      await this.pelletStore.save(pellet);
      log.engine.info(`[ProactiveGenerator] Dream reflexion complete — pellet "${pellet.id}" created`);

      return [pellet];
    } catch (err) {
      log.engine.warn(
        `[ProactiveGenerator] Dream reflexion failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return [];
    }
  }

  /**
   * Run skill evolution: generate pellets capturing evolved knowledge.
   */
  async runEvolveSkills(): Promise<Pellet[]> {
    if (!this.generationConfig.evolveSkillsEnabled) {
      log.engine.debug("[ProactiveGenerator] Skill evolution disabled");
      return [];
    }

    const now = new Date();
    const hoursSinceLastRun = this.lastEvolveRun
      ? (now.getTime() - new Date(this.lastEvolveRun).getTime()) / (1000 * 60 * 60)
      : Infinity;

    if (hoursSinceLastRun < 24) {
      log.engine.debug("[ProactiveGenerator] Skipping skill evolution (ran recently)");
      return [];
    }

    this.lastEvolveRun = now.toISOString();
    log.engine.info("[ProactiveGenerator] Running skill evolution knowledge capture...");

    try {
      const recentPellets = (await this.pelletStore.listAll())
        .filter((p) => p.tags.includes("evolution-insight"))
        .slice(0, 3);

      if (recentPellets.length === 0) {
        log.engine.info("[ProactiveGenerator] No recent evolution insights to capture");
        return [];
      }

      const insightsSummary = recentPellets
        .map((p) => `- ${p.title}: ${p.content.slice(0, 200)}`)
        .join("\n");

      const prompt =
        `You are capturing evolved skill knowledge.\n\n` +
        `Recent evolution insights:\n${insightsSummary}\n\n` +
        `Generate ONE pellet that synthesizes the new capabilities and knowledge. ` +
        `Format as JSON with: id, title, tags (including "skill_evolution"), content.`;

      const pellet = await this.generator.generate(prompt, "skill_evolution", {
        provider: this.provider,
        owl: this.owl,
        config: this.config,
      });

      pellet.tags = [...new Set([...pellet.tags, "skill_evolution", "capability"])];

      await this.pelletStore.save(pellet);
      log.engine.info(`[ProactiveGenerator] Skill evolution complete — pellet "${pellet.id}" created`);

      return [pellet];
    } catch (err) {
      log.engine.warn(
        `[ProactiveGenerator] Skill evolution failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return [];
    }
  }

  private async generateGapFillingPellet(topic: string): Promise<Pellet | null> {
    const prompt =
      `You are filling a knowledge gap on the topic: "${topic}".\n\n` +
      `Generate a comprehensive pellet that covers:\n` +
      `1. Core concepts and definitions\n` +
      `2. Common patterns and best practices\n` +
      `3. Potential pitfalls and how to avoid them\n\n` +
      `Format as JSON with: id, title, tags (including the topic), content in markdown.`;

    try {
      return await this.generator.generate(prompt, `knowledge-gap:${topic}`, {
        provider: this.provider,
        owl: this.owl,
        config: this.config,
      });
    } catch (err) {
      log.engine.warn(
        `[ProactiveGenerator] Gap filling failed for "${topic}": ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}