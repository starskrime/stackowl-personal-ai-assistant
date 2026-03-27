/**
 * StackOwl — Constellation Miner
 *
 * Discovers patterns across the pellet corpus: themes, contradictions,
 * knowledge gaps, and topic evolution.
 */

import type { ModelProvider } from "../providers/base.js";
import type { PelletStore } from "../pellets/store.js";
import type { Constellation, ConstellationType } from "./types.js";
import { join } from "node:path";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { existsSync, mkdirSync } from "node:fs";
import { log } from "../logger.js";

export class ConstellationMiner {
  private provider: ModelProvider;
  private pelletStore: PelletStore;
  private constellationDir: string;

  constructor(
    provider: ModelProvider,
    pelletStore: PelletStore,
    workspacePath: string,
  ) {
    this.provider = provider;
    this.pelletStore = pelletStore;
    this.constellationDir = join(workspacePath, "constellations");
    if (!existsSync(this.constellationDir))
      mkdirSync(this.constellationDir, { recursive: true });
  }

  /**
   * Run a full mining pass across all pellets.
   * Returns newly discovered constellations.
   */
  async mine(): Promise<Constellation[]> {
    const allPellets = await this.pelletStore.listAll();
    if (allPellets.length < 3) return []; // Need enough data

    // Build a condensed corpus for LLM analysis
    const corpus = allPellets.slice(0, 50).map((p) => ({
      id: p.id,
      title: p.title,
      tags: p.tags,
      excerpt: p.content.slice(0, 200),
    }));

    const corpusText = corpus
      .map(
        (p) =>
          `[${p.id}] "${p.title}" (tags: ${p.tags.join(", ")}): ${p.excerpt}`,
      )
      .join("\n\n");

    const existingConstellations = await this.list();
    const existingTitles = existingConstellations.map((c) => c.title);

    try {
      const resp = await this.provider.chat(
        [
          {
            role: "user",
            content:
              `Analyze this knowledge base for cross-cutting patterns:\n\n${corpusText}\n\n` +
              `Find patterns of these types:\n` +
              `- "theme": Topics that span multiple pellets in unexpected ways\n` +
              `- "contradiction": Pellets that contain conflicting viewpoints\n` +
              `- "gap": Important topics referenced but never fully explored\n` +
              `- "evolution": Topics where the user's understanding has changed over time\n\n` +
              (existingTitles.length > 0
                ? `Already discovered: ${existingTitles.join(", ")}. Find NEW patterns only.\n\n`
                : "") +
              `Respond with JSON:\n` +
              `[{"type":"theme|contradiction|gap|evolution","title":"...","description":"...","insight":"...","linkedPelletIds":["id1","id2"]}]\n\n` +
              `Find 1-3 patterns. Be specific and reference actual pellet IDs.`,
          },
        ],
        undefined,
        { temperature: 0.4, maxTokens: 800 },
      );

      const text = resp.content.trim();
      const jsonMatch = text.match(/\[[\s\S]*\]/);
      if (!jsonMatch) return [];

      const parsed = JSON.parse(jsonMatch[0]);
      const newConstellations: Constellation[] = [];

      for (const raw of parsed) {
        // Skip duplicates
        if (existingTitles.includes(raw.title)) continue;

        const links = (raw.linkedPelletIds || [])
          .map((id: string) => {
            const pellet = corpus.find((p) => p.id === id);
            return pellet
              ? {
                  pelletId: pellet.id,
                  pelletTitle: pellet.title,
                  relevance: 1.0,
                  excerpt: pellet.excerpt.slice(0, 100),
                }
              : null;
          })
          .filter(Boolean);

        const constellation: Constellation = {
          id: `constellation_${Date.now()}_${newConstellations.length}`,
          type: (raw.type as ConstellationType) || "theme",
          title: raw.title || "Untitled Pattern",
          description: raw.description || "",
          links,
          insight: raw.insight || "",
          discoveredAt: new Date().toISOString(),
          notified: false,
        };

        newConstellations.push(constellation);
        await this.save(constellation);
      }

      return newConstellations;
    } catch (err) {
      log.engine.debug(`[ConstellationMiner] Mining failed: ${err}`);
      return [];
    }
  }

  /**
   * List all discovered constellations.
   */
  async list(): Promise<Constellation[]> {
    if (!existsSync(this.constellationDir)) return [];
    const files = await readdir(this.constellationDir);
    const constellations: Constellation[] = [];

    for (const file of files) {
      if (!file.endsWith(".json")) continue;
      try {
        const data = await readFile(join(this.constellationDir, file), "utf-8");
        constellations.push(JSON.parse(data));
      } catch {
        /* skip */
      }
    }

    return constellations.sort(
      (a, b) =>
        new Date(b.discoveredAt).getTime() - new Date(a.discoveredAt).getTime(),
    );
  }

  /**
   * Get unnotified constellations (for proactive delivery).
   */
  async getUnnotified(): Promise<Constellation[]> {
    const all = await this.list();
    return all.filter((c) => !c.notified);
  }

  /**
   * Mark a constellation as notified.
   */
  async markNotified(id: string): Promise<void> {
    const path = join(this.constellationDir, `${id}.json`);
    if (!existsSync(path)) return;
    try {
      const data = await readFile(path, "utf-8");
      const constellation: Constellation = JSON.parse(data);
      constellation.notified = true;
      await this.save(constellation);
    } catch {
      /* skip */
    }
  }

  /**
   * Format constellation for user display.
   */
  format(c: Constellation): string {
    const typeEmoji: Record<ConstellationType, string> = {
      theme: "\u2728",
      contradiction: "\u26A1",
      gap: "\uD83D\uDD73\uFE0F",
      evolution: "\uD83C\uDF31",
    };

    const lines: string[] = [
      `${typeEmoji[c.type]} **${c.title}** (${c.type})`,
      "",
      c.description,
      "",
      `**Insight:** ${c.insight}`,
    ];

    if (c.links.length > 0) {
      lines.push("", "**Related pellets:**");
      for (const link of c.links) {
        lines.push(`  - ${link.pelletTitle}`);
      }
    }

    return lines.join("\n");
  }

  private async save(constellation: Constellation): Promise<void> {
    await writeFile(
      join(this.constellationDir, `${constellation.id}.json`),
      JSON.stringify(constellation, null, 2),
    );
    log.engine.info(`[ConstellationMiner] Saved: ${constellation.id}`);
  }
}
