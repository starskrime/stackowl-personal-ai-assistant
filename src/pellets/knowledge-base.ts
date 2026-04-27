/**
 * StackOwl — Knowledge Base
 *
 * Manages knowledge base growth, health monitoring, and statistics.
 * Provides insights into what the knowledge base covers and where gaps exist.
 */

import type { PelletStore, Pellet } from "./store.js";
import { extractConcepts } from "./concepts.js";

// ─── Types ───────────────────────────────────────────────────────

export interface KnowledgeBaseStats {
  totalPellets: number;
  topicsCovered: string[];
  domainsCovered: string[];
  growthRate: number;
  lastUpdated: string;
  avgPelletAge: number;
  coverageGaps: string[];
  recentPellets: Pellet[];
  stalePellets: Pellet[];
}

export interface GrowthMetrics {
  pelletsThisWeek: number;
  pelletsThisMonth: number;
  weeklyAverage: number;
  trend: "growing" | "stable" | "declining";
}

// ─── Knowledge Base ───────────────────────────────────────────────

export class KnowledgeBase {
  constructor(private pelletStore: PelletStore) {}

  /**
   * Get comprehensive statistics about the knowledge base.
   */
  async getStats(): Promise<KnowledgeBaseStats> {
    const allPellets = await this.pelletStore.listAll();
    const now = Date.now();

    const topics = new Set<string>();
    const domains = new Set<string>();
    let totalAge = 0;
    const recentPellets: Pellet[] = [];
    const stalePellets: Pellet[] = [];
    const sevenDaysAgo = now - 7 * 24 * 60 * 60 * 1000;

    for (const pellet of allPellets) {
      for (const tag of pellet.tags) {
        domains.add(tag);
      }

      const concepts = extractConcepts(pellet.title, pellet.content, pellet.tags);
      concepts.forEach((c) => topics.add(c));

      const age = now - new Date(pellet.generatedAt).getTime();
      totalAge += age;

      if (new Date(pellet.generatedAt).getTime() > sevenDaysAgo) {
        recentPellets.push(pellet);
      }

      if (age > 30 * 24 * 60 * 60 * 1000) {
        stalePellets.push(pellet);
      }
    }

    const avgPelletAgeDays = allPellets.length > 0 ? totalAge / allPellets.length / (24 * 60 * 60 * 1000) : 0;
    const coverageGaps = this.computeCoverageGaps(topics);

    const growthRate = this.computeGrowthRate(allPellets);
    const lastUpdated = allPellets.length > 0
      ? allPellets.reduce((latest, p) =>
          new Date(p.generatedAt) > new Date(latest.generatedAt) ? p : latest,
        ).generatedAt
      : new Date().toISOString();

    return {
      totalPellets: allPellets.length,
      topicsCovered: [...topics].slice(0, 50),
      domainsCovered: [...domains],
      growthRate,
      lastUpdated,
      avgPelletAge: Math.round(avgPelletAgeDays),
      coverageGaps,
      recentPellets: recentPellets.slice(0, 10),
      stalePellets: stalePellets.slice(0, 10),
    };
  }

  /**
   * Get growth metrics over time.
   */
  async getGrowthMetrics(): Promise<GrowthMetrics> {
    const allPellets = await this.pelletStore.listAll();
    const now = Date.now();
    const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
    const monthAgo = now - 30 * 24 * 60 * 60 * 1000;

    const pelletsThisWeek = allPellets.filter(
      (p) => new Date(p.generatedAt).getTime() > weekAgo,
    ).length;

    const pelletsThisMonth = allPellets.filter(
      (p) => new Date(p.generatedAt).getTime() > monthAgo,
    ).length;

    const weeksWithData = new Set(
      allPellets.map((p) => {
        const d = new Date(p.generatedAt);
        const weekNum = Math.floor(d.getTime() / (7 * 24 * 60 * 60 * 1000));
        return `${d.getFullYear()}-${weekNum}`;
      }),
    ).size;

    const weeklyAverage = weeksWithData > 0 ? pelletsThisMonth / Math.min(weeksWithData, 4) : 0;

    const recentTrend = pelletsThisWeek >= weeklyAverage ? "growing" : "declining";

    return {
      pelletsThisWeek,
      pelletsThisMonth,
      weeklyAverage: Math.round(weeklyAverage * 10) / 10,
      trend: recentTrend,
    };
  }

  /**
   * Find topics with no or few pellets (knowledge gaps).
   */
  async findCoverageGaps(): Promise<string[]> {
    const stats = await this.getStats();
    return stats.coverageGaps;
  }

  /**
   * Identify pellets that haven't been referenced recently (candidates for review).
   */
  async findOrphanedPellets(): Promise<Pellet[]> {
    const allPellets = await this.pelletStore.listAll();
    const threshold = 60 * 24 * 60 * 60 * 1000;

    return allPellets.filter((p) => {
      const age = Date.now() - new Date(p.generatedAt).getTime();
      return age > threshold;
    });
  }

  /**
   * Get topic coverage summary (what % of topics have recent pellets).
   */
  async getTopicCoverage(): Promise<Map<string, { count: number; latest: string }>> {
    const allPellets = await this.pelletStore.listAll();
    const coverage = new Map<string, { count: number; latest: string }>();

    for (const pellet of allPellets) {
      for (const tag of pellet.tags) {
        const existing = coverage.get(tag);
        if (existing) {
          existing.count++;
          if (new Date(pellet.generatedAt) > new Date(existing.latest)) {
            existing.latest = pellet.generatedAt;
          }
        } else {
          coverage.set(tag, { count: 1, latest: pellet.generatedAt });
        }
      }
    }

    return coverage;
  }

  // ─── Private Helpers ─────────────────────────────────────────

  private computeGrowthRate(pellets: Pellet[]): number {
    if (pellets.length === 0) return 0;

    const now = Date.now();
    const oneWeekAgo = now - 7 * 24 * 60 * 60 * 1000;
    const twoWeeksAgo = now - 14 * 24 * 60 * 60 * 1000;

    const thisWeek = pellets.filter(
      (p) => new Date(p.generatedAt).getTime() > oneWeekAgo,
    ).length;

    const lastWeek = pellets.filter((p) => {
      const t = new Date(p.generatedAt).getTime();
      return t > twoWeeksAgo && t <= oneWeekAgo;
    }).length;

    if (lastWeek === 0) return thisWeek > 0 ? 100 : 0;
    return Math.round(((thisWeek - lastWeek) / lastWeek) * 100);
  }

  private computeCoverageGaps(topics: Set<string>): string[] {
    const commonTopics = [
      "typescript", "javascript", "node.js", "api", "database",
      "architecture", "testing", "debugging", "performance",
      "security", "deployment", "configuration", "error-handling",
    ];

    return commonTopics.filter((topic) => !topics.has(topic)).slice(0, 10);
  }
}