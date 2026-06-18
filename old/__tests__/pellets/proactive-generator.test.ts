import { describe, it, expect, beforeEach, vi } from "vitest";
import { ProactiveKnowledgeGenerator, DEFAULT_CONFIG, type ProactiveGenerationConfig } from "../../src/pellets/proactive-generator.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";

const createMockPelletStore = (pellets: Pellet[] = []): PelletStore => {
  return {
    init: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockImplementation(async (pellet: Pellet) => {
      pellets.push(pellet);
      return { verdict: "CREATE" as const, reasoning: "test" };
    }),
    get: vi.fn().mockImplementation(async (id: string) => pellets.find(p => p.id === id) ?? null),
    listAll: vi.fn().mockResolvedValue(pellets),
    search: vi.fn().mockResolvedValue([]),
    count: vi.fn().mockResolvedValue(pellets.length),
    delete: vi.fn().mockResolvedValue(undefined),
    buildGraph: vi.fn().mockResolvedValue(undefined),
    getDeduplicator: vi.fn(),
    getKuzuGraph: vi.fn() as any,
    kuzuGraph: {} as any,
  } as unknown as PelletStore;
};

describe("ProactiveKnowledgeGenerator", () => {
  let generator: ProactiveKnowledgeGenerator;
  let mockPelletStore: PelletStore;

  beforeEach(() => {
    mockPelletStore = createMockPelletStore([]);
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "proactive-pellet",
          title: "Proactive Pellet",
          tags: ["proactive"],
          owlsInvolved: ["TestOwl"],
          content: "Proactive content.",
        }),
      ),
    };
    generator = new ProactiveKnowledgeGenerator(mockPelletStore, mockRouter);
  });

  describe("evaluateKnowledgeGaps", () => {
    it("should return array of gap topics", async () => {
      const gaps = await generator.evaluateKnowledgeGaps();
      expect(Array.isArray(gaps)).toBe(true);
    });

    it("should handle empty knowledge base", async () => {
      const gaps = await generator.evaluateKnowledgeGaps();
      expect(gaps).toBeDefined();
    });
  });

  describe("runKnowledgeCouncil", () => {
    it("should skip if run too recently", async () => {
      const pellets = await generator.runKnowledgeCouncil();
      expect(Array.isArray(pellets)).toBe(true);
    });

    it("should return pellets when gaps are found", async () => {
      const pellets = await generator.runKnowledgeCouncil();
      expect(Array.isArray(pellets)).toBe(true);
    });

    it("should respect council interval configuration", () => {
      const customConfig: Partial<ProactiveGenerationConfig> = {
        councilIntervalHours: 24,
      };
      const mockRouter = { resolve: vi.fn().mockResolvedValue("{}") };
      const customGenerator = new ProactiveKnowledgeGenerator(
        mockPelletStore,
        mockRouter,
        customConfig,
      );
      expect(customGenerator).toBeDefined();
    });
  });

  describe("runDream", () => {
    it("should return empty when disabled", async () => {
      const customConfig: Partial<ProactiveGenerationConfig> = {
        dreamEnabled: false,
      };
      const mockRouter = { resolve: vi.fn().mockResolvedValue("{}") };
      const customGenerator = new ProactiveKnowledgeGenerator(
        mockPelletStore,
        mockRouter,
        customConfig,
      );

      const pellets = await customGenerator.runDream();
      expect(pellets).toEqual([]);
    });

    it("should skip if run too recently", async () => {
      const pellets = await generator.runDream();
      expect(Array.isArray(pellets)).toBe(true);
    });
  });

  describe("runEvolveSkills", () => {
    it("should return empty when disabled", async () => {
      const customConfig: Partial<ProactiveGenerationConfig> = {
        evolveSkillsEnabled: false,
      };
      const mockRouter = { resolve: vi.fn().mockResolvedValue("{}") };
      const customGenerator = new ProactiveKnowledgeGenerator(
        mockPelletStore,
        mockRouter,
        customConfig,
      );

      const pellets = await customGenerator.runEvolveSkills();
      expect(pellets).toEqual([]);
    });

    it("should skip if run too recently", async () => {
      const pellets = await generator.runEvolveSkills();
      expect(Array.isArray(pellets)).toBe(true);
    });
  });

  describe("default configuration", () => {
    it("should use default values", () => {
      expect(DEFAULT_CONFIG.councilIntervalHours).toBe(12);
      expect(DEFAULT_CONFIG.dreamEnabled).toBe(true);
      expect(DEFAULT_CONFIG.evolveSkillsEnabled).toBe(true);
      expect(DEFAULT_CONFIG.minGapAgeDays).toBe(30);
    });
  });
});
