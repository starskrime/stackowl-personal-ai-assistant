/**
 * StackOwl — Constellation Miner
 *
 * Discovers patterns across the pellet corpus: themes, contradictions,
 * knowledge gaps, and topic evolution.
 */

import type { ModelProvider } from "../providers/base.js";
import type { Constellation, ConstellationType } from "./types.js";
import { join } from "node:path";
import { readFile, writeFile, readdir } from "node:fs/promises";
import { existsSync, mkdirSync } from "node:fs";
import { log } from "../logger.js";

export class ConstellationMiner {
  private constellationDir: string;

  constructor(
    _provider: ModelProvider,
    _pelletStore: unknown,
    workspacePath: string,
  ) {
    this.constellationDir = join(workspacePath, "constellations");
    if (!existsSync(this.constellationDir))
      mkdirSync(this.constellationDir, { recursive: true });
  }

  /**
   * Run a full mining pass across all pellets.
   * Returns newly discovered constellations.
   */
  async mine(): Promise<Constellation[]> {
    // No pellet store available — constellation mining is disabled in this configuration
    return [];
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
