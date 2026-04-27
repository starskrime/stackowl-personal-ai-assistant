/**
 * StackOwl — Domain Tool Map
 *
 * Dynamic DOMAIN_TOOL_MAP that updates based on accumulated success/failure
 * outcomes. Provides learned tool rankings per domain.
 */

import { log } from "../logger.js";

export interface DomainToolRanking {
  toolName: string;
  successRate: number;
  totalAttempts: number;
  lastUsed: string;
}

export class DomainToolMap {
  private domainToolMap: Record<string, string[]> = {
    research: ["web_fetch", "web_search", "recall", "pellet_recall"],
    coding: ["read_file", "write_file", "shell"],
    memory: ["recall", "remember", "pellet_recall"],
    filesystem: ["read_file", "write_file", "shell"],
    web: ["web_fetch", "web_search"],
    analysis: ["recall", "pellet_recall", "read_file"],
    communication: ["send_file"],
  };

  private toolStats: Map<string, Map<string, DomainToolRanking>> = new Map();

  recordOutcome(
    domain: string,
    toolName: string,
    success: boolean,
  ): void {
    if (!this.toolStats.has(domain)) {
      this.toolStats.set(domain, new Map());
    }

    const domainStats = this.toolStats.get(domain)!;
    const stats = domainStats.get(toolName) ?? {
      toolName,
      successRate: 0,
      totalAttempts: 0,
      lastUsed: "",
    };

    stats.totalAttempts++;
    stats.successRate =
      (stats.successRate * (stats.totalAttempts - 1) +
        (success ? 1 : 0)) /
      stats.totalAttempts;
    stats.lastUsed = new Date().toISOString();

    domainStats.set(toolName, stats);

    log.engine.debug(
      `[DomainToolMap] Updated ${domain}/${toolName}: rate=${stats.successRate.toFixed(2)}, attempts=${stats.totalAttempts}`,
    );
  }

  getToolsForDomain(domain: string): string[] {
    const baseTools = this.domainToolMap[domain] ?? ["recall"];
    const domainStats = this.toolStats.get(domain);

    if (!domainStats) return baseTools;

    const ranked: Array<{ tool: string; rate: number }> = [];

    for (const tool of baseTools) {
      const stats = domainStats.get(tool);
      ranked.push({
        tool,
        rate: stats?.successRate ?? 0.5,
      });
    }

    ranked.sort((a, b) => b.rate - a.rate);
    return ranked.map((r) => r.tool);
  }

  getToolStats(domain: string, toolName: string): DomainToolRanking | null {
    return this.toolStats.get(domain)?.get(toolName) ?? null;
  }

  getDomainStats(domain: string): Map<string, DomainToolRanking> | null {
    return this.toolStats.get(domain) ?? null;
  }

  getAllDomains(): string[] {
    return Object.keys(this.domainToolMap);
  }

  addDomain(domain: string, tools: string[]): void {
    this.domainToolMap[domain] = tools;
  }

  addToolToDomain(domain: string, toolName: string): void {
    const tools = this.domainToolMap[domain] ?? [];
    if (!tools.includes(toolName)) {
      this.domainToolMap[domain] = [...tools, toolName];
    }
  }
}
