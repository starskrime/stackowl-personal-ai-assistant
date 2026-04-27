/**
 * StackOwl — Debate Pellet Generator
 *
 * Converts Parliament debate sessions into structured knowledge pellets
 * for future reference and recall.
 */

import type { ModelProvider } from "../providers/base.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ParliamentSession } from "./protocol.js";
import { PelletGenerator } from "../pellets/generator.js";
import type { Pellet, PelletStore } from "../pellets/store.js";
import { log } from "../logger.js";

// ─── DebatePelletGenerator ──────────────────────────────────────

export class DebatePelletGenerator {
  private pelletGenerator: PelletGenerator;

  constructor(
    private provider: ModelProvider,
    private config: StackOwlConfig,
  ) {
    this.pelletGenerator = new PelletGenerator();
  }

  /**
   * Generate a knowledge pellet from a completed Parliament session.
   */
  async generateFromSession(
    session: ParliamentSession,
    pelletStore: PelletStore,
  ): Promise<Pellet | null> {
    log.engine.info(
      `[DebatePelletGenerator] Generating pellet for Parliament session: ${session.id}`,
    );

    try {
      const pellet = await this.pelletGenerator.generate(
        this.generateDebateSummary(session),
        `Parliament: ${session.config.topic}`,
        {
          provider: this.provider,
          owl: session.config.participants[0],
          config: this.config,
        },
      );

      // Enhance pellet with debate-specific metadata
      pellet.owls = session.config.participants.map((p) => p.persona.name);
      pellet.tags = await this.generateTags(session);

      await pelletStore.save(pellet);
      log.engine.info(
        `[DebatePelletGenerator] Saved debate pellet: ${pellet.id}.md`,
      );

      return pellet;
    } catch (err) {
      log.engine.error(
        `[DebatePelletGenerator] Failed to generate pellet: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }

  /**
   * Generate a formatted debate summary for pellet content.
   */
  generateDebateSummary(session: ParliamentSession): string {
    const lines: string[] = [];

    // Title
    lines.push(`# Parliament Debate: ${session.config.topic}`);
    lines.push("");
    lines.push(`*Session: ${session.id} | ${session.config.participants.length} owls | Verdict: ${session.verdict ?? "PENDING"}*`);
    lines.push("");

    // Topic
    lines.push("## Topic");
    lines.push(session.config.topic);
    lines.push("");

    // Positions
    lines.push("## Positions");
    for (const position of session.positions) {
      lines.push(`- **${position.owlName}** [${position.position}]: ${position.argument}`);
    }
    lines.push("");

    // Cross-Examination
    if (session.challenges.length > 0) {
      lines.push("## Cross-Examination");
      for (const challenge of session.challenges) {
        lines.push(`- **${challenge.owlName}** challenged ${challenge.targetOwl}: ${challenge.challengeContent}`);
      }
      lines.push("");
    }

    // Verdict
    if (session.verdict) {
      lines.push("## Verdict");
      lines.push(`**${session.verdict}**`);
      lines.push("");
    }

    // Synthesis
    if (session.synthesis) {
      lines.push("## Synthesis");
      lines.push(session.synthesis);
      lines.push("");
    }

    // Key Insights
    const insights = this.extractKeyInsights(session);
    if (insights.length > 0) {
      lines.push("## Key Insights");
      for (const insight of insights) {
        lines.push(`- ${insight}`);
      }
      lines.push("");
    }

    // Participants
    lines.push("## Participants");
    for (const owl of session.config.participants) {
      lines.push(`- ${owl.persona.emoji} ${owl.persona.name} (${owl.persona.type})`);
    }

    return lines.join("\n");
  }

  /**
   * Extract key insights from the debate session.
   */
  extractKeyInsights(session: ParliamentSession): string[] {
    const insights: string[] = [];

    // Extract from positions
    for (const position of session.positions) {
      if (position.argument.length > 20) {
        insights.push(`${position.owlName} (${position.position}): ${position.argument.slice(0, 100)}`);
      }
    }

    // Extract from synthesis if available
    if (session.synthesis) {
      const sentences = session.synthesis.split(/[.!?]+/).filter((s) => s.trim().length > 20);
      for (const sentence of sentences.slice(0, 3)) {
        insights.push(`Synthesis: ${sentence.trim().slice(0, 150)}`);
      }
    }

    return insights.slice(0, 10);
  }

  /**
   * Generate tags for the debate pellet using LLM classification.
   */
  async generateTags(session: ParliamentSession): Promise<string[]> {
    const tags = ["parliament", "debate", "multi-owl"];

    if (session.verdict) {
      tags.push(session.verdict.toLowerCase());
    }

    const topic = session.config.topic;

    try {
      const classification = await this.classifyTopic(topic);

      for (const category of classification.categories) {
        tags.push(category);
      }

      log.parliament.behavioral("behavioral.parliament.pellet_generated", {
        sessionId: session.id,
        topic: topic.slice(0, 100),
        primaryCategory: classification.primaryCategory,
        categories: classification.categories,
        reasoning: classification.reasoning,
      });
    } catch (err) {
      log.parliament.debug(
        `[DebatePelletGenerator] Topic classification failed, using fallback tags: ${err instanceof Error ? err.message : String(err)}`,
      );
      tags.push("other");
    }

    return [...new Set(tags)];
  }

  /**
   * Classify a topic into categories using LLM.
   */
  private async classifyTopic(
    topic: string,
  ): Promise<{ categories: string[]; primaryCategory: string; reasoning: string }> {
    const messages: import("../providers/base.js").ChatMessage[] = [
      {
        role: "user",
        content: `Classify this topic into one or more categories.

Topic: "${topic}"

Categories:
- "architecture": System design, infrastructure, patterns
- "career": Job, work, professional development
- "code": Programming, implementation, frameworks
- "database": Data storage, queries, schemas
- "business": Strategy, products, customer
- "personal": Non-work topics
- "other": Doesn't fit above categories

Respond with JSON:
{
  "categories": ["architecture", "career"],
  "primaryCategory": "architecture",
  "reasoning": "brief explanation"
}`,
      },
    ];

    const response = await this.provider.chat(messages);

    try {
      const parsed = JSON.parse(response.content);
      return {
        categories: Array.isArray(parsed.categories) ? parsed.categories : [],
        primaryCategory: parsed.primaryCategory ?? "other",
        reasoning: parsed.reasoning ?? "",
      };
    } catch {
      log.parliament.debug(`[DebatePelletGenerator] Failed to parse classification response`);
      return { categories: ["other"], primaryCategory: "other", reasoning: "Parse failed" };
    }
  }
}

/**
 * Find past debate pellets related to a topic for recall.
 */
export async function findRelatedDebatePellets(
  pelletStore: PelletStore,
  topic: string,
  limit: number = 5,
): Promise<Pellet[]> {
  try {
    const pellets = await pelletStore.search(topic, limit, 0.5);
    return pellets.filter(
      (p) => p.tags?.includes("parliament") || p.tags?.includes("debate"),
    );
  } catch (err) {
    log.engine.warn(
      `[DebatePelletGenerator] Failed to find related pellets: ${err instanceof Error ? err.message : String(err)}`,
    );
    return [];
  }
}

/**
 * Format past debate pellets for injection into Parliament context.
 */
export function formatPastDebatesForContext(pellets: Pellet[]): string {
  if (pellets.length === 0) return "";

  const lines = ["\n[Past Parliament decisions on similar topics]:\n"];
  for (const pellet of pellets) {
    lines.push(
      `  • "${pellet.title.slice(0, 80)}" → ${pellet.content?.slice(0, 100) ?? "n/a"}...`,
    );
  }
  return lines.join("\n") + "\n";
}