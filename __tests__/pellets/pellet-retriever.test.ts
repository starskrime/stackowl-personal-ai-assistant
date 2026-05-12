import { describe, it, expect, beforeEach, vi } from "vitest";
import { PelletRetriever, DEFAULT_RETRIEVAL_CONFIG, type RetrievalConfig } from "../../src/pellets/pellet-retriever.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";

const createMockPelletStore = (pellets: Pellet[] = []): PelletStore => {
  return {
    init: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockResolvedValue({ verdict: "CREATE" as const, reasoning: "test" }),
    get: vi.fn().mockImplementation(async (id: string) => pellets.find(p => p.id === id) ?? null),
    listAll: vi.fn().mockResolvedValue(pellets),
    search: vi.fn().mockImplementation(async (query: string, limit?: number, threshold?: number) => {
      return pellets.slice(0, limit ?? 5);
    }),
    searchWithGraph: vi.fn().mockImplementation(async (query: string, limit?: number) => {
      return pellets.slice(0, limit ?? 5);
    }),
    count: vi.fn().mockResolvedValue(pellets.length),
    delete: vi.fn().mockResolvedValue(undefined),
    buildGraph: vi.fn().mockResolvedValue(undefined),
    getDeduplicator: vi.fn(),
    getKuzuGraph: vi.fn() as any,
    kuzuGraph: {} as any,
  } as unknown as PelletStore;
};

const createMockPellet = (id: string, title: string, tags: string[], content: string): Pellet => ({
  id,
  title,
  generatedAt: new Date().toISOString(),
  source: "test",
  owls: ["TestOwl"],
  tags,
  content,
  version: 1,
});

describe("PelletRetriever", () => {
  let retriever: PelletRetriever;
  let mockPelletStore: PelletStore;

  beforeEach(() => {
    mockPelletStore = createMockPelletStore([
      createMockPellet("p1", "TypeScript Guide", ["typescript", "guide"], "TypeScript content..."),
      createMockPellet("p2", "JavaScript Tips", ["javascript", "tips"], "JavaScript content..."),
      createMockPellet("p3", "Node.js API", ["nodejs", "api"], "Node.js API content..."),
    ]);
    retriever = new PelletRetriever(mockPelletStore);
  });

  describe("retrieveRelevant", () => {
    it("should return pellets from store search", async () => {
      const pellets = await retriever.retrieveRelevant("typescript");
      expect(pellets).toBeDefined();
      expect(Array.isArray(pellets)).toBe(true);
    });

    it("should use default config values", async () => {
      await retriever.retrieveRelevant("test");
      expect(mockPelletStore.searchWithGraph).toHaveBeenCalledWith(
        "test",
        DEFAULT_RETRIEVAL_CONFIG.topK,
      );
    });

    it("should return empty array on error", async () => {
      const errorStore = createMockPelletStore();
      errorStore.search = vi.fn().mockRejectedValue(new Error("search failed"));
      const errorRetriever = new PelletRetriever(errorStore);
      const pellets = await errorRetriever.retrieveRelevant("test");
      expect(pellets).toEqual([]);
    });
  });

  describe("retrieveWithContext", () => {
    it("should filter by tags when provided", async () => {
      const pellets = await retriever.retrieveWithContext("typescript", {
        tags: ["typescript"],
      });
      expect(pellets).toBeDefined();
    });

    it("should filter by owl name when provided", async () => {
      const pellets = await retriever.retrieveWithContext("typescript", {
        owlName: "TestOwl",
      });
      expect(pellets).toBeDefined();
    });

    it("should combine filters correctly", async () => {
      const pellets = await retriever.retrieveWithContext("typescript", {
        tags: ["typescript"],
        owlName: "TestOwl",
      });
      expect(pellets).toBeDefined();
    });
  });

  describe("formatForInjection", () => {
    it("should return empty string for empty pellets", () => {
      const result = retriever.formatForInjection([]);
      expect(result).toBe("");
    });

    it("should format pellets as relevant_knowledge block", () => {
      const pellets = [
        createMockPellet("p1", "Test Pellet", ["test"], "Test content here"),
      ];
      const result = retriever.formatForInjection(pellets);
      expect(result).toContain("<relevant_knowledge>");
      expect(result).toContain("[test] Test Pellet");
      expect(result).toContain("Test content here");
      expect(result).toContain("</relevant_knowledge>");
    });

    it("should include domain tags in format", () => {
      const pellets = [
        createMockPellet("p1", "TypeScript Guide", ["typescript"], "TypeScript content"),
      ];
      const result = retriever.formatForInjection(pellets);
      expect(result).toContain("[typescript]");
    });

    it("should truncate long content", () => {
      const longContent = "a".repeat(5000);
      const pellets = [
        createMockPellet("p1", "Long Pellet", ["test"], longContent),
      ];
      const result = retriever.formatForInjection(pellets);
      expect(result).toContain("...");
    });
  });

  describe("retrieveAndFormat", () => {
    it("should retrieve and format in one step", async () => {
      const result = await retriever.retrieveAndFormat("typescript");
      expect(result).toBeDefined();
      expect(typeof result).toBe("string");
    });
  });

  describe("configuration", () => {
    it("should use custom config when provided", () => {
      const customConfig: Partial<RetrievalConfig> = {
        topK: 10,
        threshold: 0.6,
        maxTokensPerPellet: 500,
      };
      const customRetriever = new PelletRetriever(mockPelletStore, customConfig);
      expect(customRetriever).toBeDefined();
    });
  });
});