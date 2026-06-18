import { describe, it, expect, beforeEach, vi } from "vitest";
import { KnowledgeBase, type KnowledgeBaseStats } from "../../src/pellets/knowledge-base.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";

const createMockPellet = (id: string, title: string, tags: string[], content: string, daysAgo: number): Pellet => ({
  id,
  title,
  generatedAt: new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000).toISOString(),
  source: "test",
  owls: ["TestOwl"],
  tags,
  content,
  version: 1,
});

const createMockPelletStore = (pellets: Pellet[] = []): PelletStore => {
  return {
    init: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockResolvedValue({ verdict: "CREATE" as const, reasoning: "test" }),
    get: vi.fn().mockImplementation(async (id: string) => pellets.find(p => p.id === id) ?? null),
    listAll: vi.fn().mockResolvedValue(pellets),
    search: vi.fn().mockResolvedValue(pellets),
    count: vi.fn().mockResolvedValue(pellets.length),
    delete: vi.fn().mockResolvedValue(undefined),
    buildGraph: vi.fn().mockResolvedValue(undefined),
    getDeduplicator: vi.fn(),
    getKuzuGraph: vi.fn() as any,
    kuzuGraph: {} as any,
  } as unknown as PelletStore;
};

describe("KnowledgeBase", () => {
  let knowledgeBase: KnowledgeBase;
  let mockPelletStore: PelletStore;

  describe("getStats", () => {
    it("should return empty stats for empty store", async () => {
      mockPelletStore = createMockPelletStore([]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const stats = await knowledgeBase.getStats();

      expect(stats.totalPellets).toBe(0);
      expect(stats.topicsCovered).toEqual([]);
      expect(stats.domainsCovered).toEqual([]);
      expect(stats.growthRate).toBe(0);
    });

    it("should count pellets correctly", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Pellet 1", ["typescript"], "content", 1),
        createMockPellet("p2", "Pellet 2", ["javascript"], "content", 2),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const stats = await knowledgeBase.getStats();

      expect(stats.totalPellets).toBe(2);
    });

    it("should extract domains from tags", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "TypeScript Guide", ["typescript", "guide"], "content", 1),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const stats = await knowledgeBase.getStats();

      expect(stats.domainsCovered).toContain("typescript");
      expect(stats.domainsCovered).toContain("guide");
    });

    it("should identify recent pellets", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Recent Pellet", ["test"], "content", 3),
        createMockPellet("p2", "Old Pellet", ["test"], "content", 10),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const stats = await knowledgeBase.getStats();

      expect(stats.recentPellets.length).toBeGreaterThan(0);
    });

    it("should identify stale pellets", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Recent Pellet", ["test"], "content", 5),
        createMockPellet("p2", "Stale Pellet", ["test"], "content", 45),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const stats = await knowledgeBase.getStats();

      expect(stats.stalePellets.length).toBeGreaterThan(0);
    });
  });

  describe("getGrowthMetrics", () => {
    it("should return zero for empty store", async () => {
      mockPelletStore = createMockPelletStore([]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const metrics = await knowledgeBase.getGrowthMetrics();

      expect(metrics.pelletsThisWeek).toBe(0);
      expect(metrics.pelletsThisMonth).toBe(0);
    });

    it("should count pellets in time periods", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Recent", ["test"], "content", 2),
        createMockPellet("p2", "MonthOld", ["test"], "content", 20),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const metrics = await knowledgeBase.getGrowthMetrics();

      expect(metrics.pelletsThisWeek).toBeGreaterThanOrEqual(1);
      expect(metrics.pelletsThisMonth).toBeGreaterThanOrEqual(1);
    });
  });

  describe("findCoverageGaps", () => {
    it("should return common topics not covered", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "TypeScript Only", ["typescript"], "content", 1),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const gaps = await knowledgeBase.findCoverageGaps();

      expect(Array.isArray(gaps)).toBe(true);
      expect(gaps.length).toBeLessThanOrEqual(10);
    });
  });

  describe("findOrphanedPellets", () => {
    it("should return pellets older than threshold", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Recent", ["test"], "content", 10),
        createMockPellet("p2", "Orphaned", ["test"], "content", 90),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const orphaned = await knowledgeBase.findOrphanedPellets();

      expect(orphaned.length).toBeGreaterThan(0);
    });
  });

  describe("getTopicCoverage", () => {
    it("should return coverage map", async () => {
      mockPelletStore = createMockPelletStore([
        createMockPellet("p1", "Pellet 1", ["typescript"], "content", 1),
        createMockPellet("p2", "Pellet 2", ["typescript", "guide"], "content", 2),
      ]);
      knowledgeBase = new KnowledgeBase(mockPelletStore);

      const coverage = await knowledgeBase.getTopicCoverage();

      expect(coverage.get("typescript")).toBeDefined();
      expect(coverage.get("typescript")?.count).toBe(2);
    });
  });
});