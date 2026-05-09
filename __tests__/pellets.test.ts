import { describe, it, expect, beforeEach, vi } from "vitest";
import { PelletStore, type Pellet } from "../src/pellets/store.js";
import { PelletGenerator } from "../src/pellets/generator.js";
import { PelletDeduplicator, type SimilarFn } from "../src/pellets/dedup.js";
import type { ModelProvider, ChatResponse } from "../src/providers/base.js";
import { rm, mkdir } from "node:fs/promises";
import { join } from "node:path";

// ─── Native-store mocks (avoids LanceDB/KuzuDB native crashes in tests) ──────

vi.mock("../src/pellets/embedder.js", () => ({
  embed: vi.fn().mockResolvedValue(null),
  pelletToEmbedText: vi.fn().mockReturnValue(""),
}));

// Shared per-path state so "cold start migration" tests can create a second store
// instance pointing to the same workspace and see previously-saved data.
const mockLanceState = vi.hoisted(() => new Map<string, Map<string, any>>());

vi.mock("../src/pellets/lance-store.js", () => ({
  LancePelletStore: vi.fn().mockImplementation((workspacePath: string) => {
    if (!mockLanceState.has(workspacePath)) {
      mockLanceState.set(workspacePath, new Map<string, any>());
    }
    const pellets = mockLanceState.get(workspacePath)!;
    return {
      init: vi.fn().mockResolvedValue(undefined),
      count: vi.fn().mockImplementation(async () => pellets.size),
      upsert: vi.fn().mockImplementation(async (pellet: any) => {
        pellets.set(pellet.id, { ...pellet });
      }),
      get: vi.fn().mockImplementation(async (id: string) => pellets.get(id) ?? null),
      listAll: vi.fn().mockImplementation(async () =>
        [...pellets.values()].sort(
          (a, b) => new Date(b.generatedAt).getTime() - new Date(a.generatedAt).getTime(),
        ),
      ),
      delete: vi.fn().mockImplementation(async (id: string) => {
        pellets.delete(id);
      }),
      findSimilarTo: vi.fn().mockImplementation(async (pellet: any, limit: number = 3) =>
        [...pellets.values()]
          .filter((p) => p.id !== pellet.id)
          .slice(0, limit)
          .map((p) => ({ pellet: p, score: 0.9 })),
      ),
      searchSimilar: vi.fn().mockImplementation(async (_vec: any, limit: number = 5) =>
        [...pellets.values()].slice(0, limit).map((p) => ({ pellet: p, score: 0.1 })),
      ),
      getByIds: vi.fn().mockImplementation(async (ids: string[]) =>
        ids.map((id) => pellets.get(id)).filter((p): p is any => p !== undefined),
      ),
      updateCounters: vi.fn().mockResolvedValue(undefined),
      migrate: vi.fn().mockImplementation(async (items: any[]) => {
        for (const p of items) pellets.set(p.id, p);
      }),
    };
  }),
}));

vi.mock("../src/pellets/kuzu-graph.js", () => ({
  KuzuPelletGraph: vi.fn().mockImplementation(() => {
    let _isBuilt = false;
    return {
      get isBuilt() { return _isBuilt; },
      init: vi.fn().mockResolvedValue(undefined),
      buildFromPellets: vi.fn().mockImplementation(async () => { _isBuilt = true; }),
      addNode: vi.fn().mockResolvedValue(undefined),
      addEdge: vi.fn().mockResolvedValue(undefined),
      removeNode: vi.fn().mockResolvedValue(undefined),
      getNeighbors: vi.fn().mockResolvedValue([]),
    };
  }),
}));

vi.mock("../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

const createMockProvider = (
  overrides?: Partial<ModelProvider>,
): ModelProvider => ({
  name: "mock",
  chat: vi.fn().mockResolvedValue({ content: "{}" } as ChatResponse),
  chatWithTools: vi.fn(),
  chatStream: vi.fn(),
  embed: vi.fn(),
  listModels: vi.fn(),
  healthCheck: vi.fn(),
  ...overrides,
});

const testSpace = join(__dirname, ".pellets_test_workspace");

describe("PelletDeduplicator", () => {
  let mockProvider: ModelProvider;

  const samplePellet: Pellet = {
    id: "pellet-1",
    title: "Test Pellet",
    generatedAt: new Date().toISOString(),
    source: "test",
    owls: ["Noctua"],
    tags: ["test"],
    content: "This is a test pellet content that has substantial information.",
    version: 1,
  };

  beforeEach(() => {
    mockProvider = createMockProvider();
  });

  it("should return CREATE when dedup is disabled", async () => {
    const searchSimilar: SimilarFn = vi.fn().mockResolvedValue([]);
    const deduplicator = new PelletDeduplicator(
      searchSimilar,
      mockProvider,
      { enabled: false },
    );

    const result = await deduplicator.evaluate(samplePellet);
    expect(result.verdict).toBe("CREATE");
  });

  it("should return CREATE when no similar pellets found", async () => {
    const searchSimilar: SimilarFn = vi.fn().mockResolvedValue([]);
    const deduplicator = new PelletDeduplicator(searchSimilar, mockProvider);

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Completely Different Topic",
      content: "Nothing similar to existing pellets about cooking and gardening.",
    });

    expect(result.verdict).toBe("CREATE");
  });

  it("should use heuristic decision when no provider available", async () => {
    const searchSimilar: SimilarFn = vi.fn().mockResolvedValue([
      { pellet: samplePellet, score: 0.75 },
    ]);
    const deduplicator = new PelletDeduplicator(searchSimilar);

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Test Pellet Similar",
      content: "This is a similar test pellet content with more details about testing.",
    });

    expect(["CREATE", "SUPERSEDE", "SKIP"]).toContain(result.verdict);
  });

  it("should call LLM when provider is available", async () => {
    const searchSimilar: SimilarFn = vi.fn().mockResolvedValue([
      { pellet: samplePellet, score: 0.75 },
    ]);
    mockProvider = createMockProvider({
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify({
          verdict: "MERGE",
          reasoning: "Both pellets have valuable information",
          merged_title: "Merged Pellet",
          merged_content: "Combined content",
          merged_tags: ["test", "merged"],
        }),
      } as ChatResponse),
    });

    const deduplicator = new PelletDeduplicator(searchSimilar, mockProvider, { useLlm: true });

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Test Pellet Similar",
      content: "This is a similar test pellet content with additional information.",
    });

    expect(result.verdict).toBe("MERGE");
    expect(mockProvider.chat).toHaveBeenCalled();
  });

  it("should fall back to CREATE on LLM error", async () => {
    const searchSimilar: SimilarFn = vi.fn().mockResolvedValue([
      { pellet: samplePellet, score: 0.75 },
    ]);
    mockProvider = createMockProvider({
      chat: vi.fn().mockRejectedValue(new Error("LLM error")),
    });

    const deduplicator = new PelletDeduplicator(searchSimilar, mockProvider, { useLlm: true });

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Test Pellet Similar",
      content: "This is a similar test pellet content with more information.",
    });

    expect(result.verdict).toBe("CREATE");
  });
});

describe("PelletStore", () => {
  let store: PelletStore;

  beforeEach(async () => {
    mockLanceState.delete(testSpace); // reset shared mock state for this test workspace
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
    await mkdir(testSpace, { recursive: true });
    store = new PelletStore(testSpace);
    await store.init();
  });

  describe("save and get", () => {
    it("should save and retrieve a pellet", async () => {
      const pellet: Pellet = {
        id: "test-pellet",
        title: "Test Title",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: ["Noctua"],
        tags: ["testing"],
        content: "Test content",
        version: 1,
      };

      await store.save(pellet, { skipDedup: true });
      const retrieved = await store.get("test-pellet");

      expect(retrieved).not.toBeNull();
      expect(retrieved!.id).toBe("test-pellet");
      expect(retrieved!.title).toBe("Test Title");
      expect(retrieved!.tags).toEqual(["testing"]);
    });

    it("should return null for non-existent pellet", async () => {
      const result = await store.get("non-existent");
      expect(result).toBeNull();
    });

    it("should save pellet with correct metadata", async () => {
      const pellet: Pellet = {
        id: "frontmatter-test",
        title: "Frontmatter Test",
        generatedAt: "2024-01-01T00:00:00.000Z",
        source: "test-source",
        owls: ["Owl1", "Owl2"],
        tags: ["tag1", "tag2"],
        content: "Frontmatter content",
        version: 2,
      };

      await store.save(pellet, { skipDedup: true });
      const retrieved = await store.get("frontmatter-test");

      expect(retrieved).not.toBeNull();
      expect(retrieved!.title).toBe("Frontmatter Test");
      expect(retrieved!.source).toBe("test-source");
      expect(retrieved!.owls).toEqual(["Owl1", "Owl2"]);
      expect(retrieved!.tags).toContain("tag1");
      expect(retrieved!.tags).toContain("tag2");
    });
  });

  describe("listAll", () => {
    it("should list all saved pellets sorted by date", async () => {
      const older: Pellet = {
        id: "older",
        title: "Older",
        generatedAt: "2024-01-01T00:00:00.000Z",
        source: "test",
        owls: [],
        tags: [],
        content: "Older content",
        version: 1,
      };

      const newer: Pellet = {
        id: "newer",
        title: "Newer",
        generatedAt: "2024-06-01T00:00:00.000Z",
        source: "test",
        owls: [],
        tags: [],
        content: "Newer content",
        version: 1,
      };

      await store.save(older, { skipDedup: true });
      await store.save(newer, { skipDedup: true });

      const list = await store.listAll();
      expect(list[0].id).toBe("newer");
      expect(list[1].id).toBe("older");
    });

    it("should return empty array when no pellets", async () => {
      const list = await store.listAll();
      expect(list).toHaveLength(0);
    });

    it("should return consistent results when called twice", async () => {
      const pellet: Pellet = {
        id: "cache-test",
        title: "Cache Test",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: [],
        content: "Content",
        version: 1,
      };

      await store.save(pellet, { skipDedup: true });
      const list1 = await store.listAll();
      const list2 = await store.listAll();

      expect(list1.map((p) => p.id)).toEqual(list2.map((p) => p.id));
    });
  });

  describe("search", () => {
    it("should find pellets by content", async () => {
      await store.save(
        {
          id: "search-test",
          title: "Search Test",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: [],
          content: "JavaScript is a programming language",
          version: 1,
        },
        { skipDedup: true },
      );

      const results = await store.search("JavaScript");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].id).toBe("search-test");
    });

    it("should return all pellets for empty query", async () => {
      await store.save(
        {
          id: "empty-query-test",
          title: "Test",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: [],
          content: "Content",
          version: 1,
        },
        { skipDedup: true },
      );

      const results = await store.search("");
      expect(results.length).toBeGreaterThan(0);
    });
  });

  describe("delete", () => {
    it("should delete a pellet", async () => {
      await store.save(
        {
          id: "delete-me",
          title: "Delete Me",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: [],
          content: "Content",
          version: 1,
        },
        { skipDedup: true },
      );

      await store.delete("delete-me");

      const retrieved = await store.get("delete-me");
      expect(retrieved).toBeNull();
    });

    it("should not throw when deleting non-existent pellet", async () => {
      await expect(store.delete("non-existent")).resolves.not.toThrow();
    });
  });

  describe("deduplication", () => {
    it("should skip duplicate pellet when LLM returns SKIP", async () => {
      const mockChat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          verdict: "SKIP",
          reasoning: "Existing pellet covers this",
        }),
      } as ChatResponse);

      const provider = createMockProvider({ chat: mockChat });
      const storeWithDedup = new PelletStore(testSpace, provider);

      const originalContent =
        "Original pellet content that is fairly long and detailed with specific information about this topic.";

      await storeWithDedup.save(
        {
          id: "original",
          title: "Original Title",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: ["test"],
          content: originalContent,
          version: 1,
        },
        { skipDedup: false },
      );

      const result = await storeWithDedup.save(
        {
          id: "duplicate",
          title: "Original Duplicate Title",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: ["test"],
          content: originalContent,
          version: 1,
        },
        { skipDedup: false },
      );

      expect(result.verdict).toBe("SKIP");
    });

    it("should merge pellets when LLM returns MERGE", async () => {
      const mockChat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          verdict: "MERGE",
          reasoning: "Both have valuable info",
          merged_title: "Merged Title",
          merged_content: "Merged content here",
          merged_tags: ["test", "merged"],
        }),
      } as ChatResponse);

      const provider = createMockProvider({ chat: mockChat });
      const storeWithDedup = new PelletStore(testSpace, provider);

      const commonContent =
        "Content about programming that is fairly long and detailed with specific information.";

      await storeWithDedup.save(
        {
          id: "first",
          title: "Programming Guide Part 1",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: ["Owl1"],
          tags: ["test", "programming"],
          content:
            commonContent +
            " This is additional unique content for the first pellet.",
          version: 1,
        },
        { skipDedup: false },
      );

      const result = await storeWithDedup.save(
        {
          id: "second",
          title: "Programming Guide Part 2",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: ["Owl2"],
          tags: ["test", "programming"],
          content:
            commonContent +
            " This is additional unique content for the second pellet.",
          version: 1,
        },
        { skipDedup: false },
      );

      expect(result.verdict).toBe("MERGE");
    });

    it("should supersede when LLM returns SUPERSEDE", async () => {
      const mockChat = vi.fn().mockResolvedValue({
        content: JSON.stringify({
          verdict: "SUPERSEDE",
          reasoning: "New is better",
        }),
      } as ChatResponse);

      const provider = createMockProvider({ chat: mockChat });
      const storeWithDedup = new PelletStore(testSpace, provider);

      const commonContent =
        "Content about programming that is fairly long and detailed with specific information about various topics.";

      await storeWithDedup.save(
        {
          id: "old-pellet",
          title: "Old Programming Guide",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: ["test", "programming"],
          content:
            commonContent +
            " This is the old pellet content that is somewhat limited.",
          version: 1,
        },
        { skipDedup: false },
      );

      const result = await storeWithDedup.save(
        {
          id: "new-pellet",
          title: "New Improved Programming Guide",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: ["test", "programming"],
          content:
            commonContent +
            " This is the new pellet content that is much more comprehensive and detailed.",
          version: 1,
        },
        { skipDedup: false },
      );

      expect(result.verdict).toBe("SUPERSEDE");
    });
  });

  describe("cold start migration", () => {
    it("should rebuild index from existing md files", async () => {
      await store.save(
        {
          id: "migration-test",
          title: "Migration Test",
          generatedAt: new Date().toISOString(),
          source: "test",
          owls: [],
          tags: ["migration"],
          content: "Content for migration testing.",
          version: 1,
        },
        { skipDedup: true },
      );

      const freshStore = new PelletStore(testSpace);
      await freshStore.init();

      const results = await freshStore.search("migration");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].id).toBe("migration-test");
    });
  });
});

describe("PelletGenerator", () => {
  it("should generate pellet from source material", async () => {
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "generated-pellet",
          title: "Generated Title",
          tags: ["generated", "test"],
          owlsInvolved: ["TestOwl"],
          content: "## Key Insight\nGenerated content here.",
        }),
      ),
    };

    const generator = new PelletGenerator(mockRouter);
    const result = await generator.generate("Raw source material content", "test-source");

    expect(result).not.toBeNull();
    expect(result!.id).toBe("generated-pellet");
    expect(result!.title).toBe("Generated Title");
    expect(result!.tags).toEqual(["generated", "test"]);
    expect(result!.owls).toEqual(["TestOwl"]);
    expect(result!.source).toBe("test-source");
    expect(result!.version).toBe(1);
  });

  it("should handle JSON wrapped in code blocks", async () => {
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        "```json\n" +
        JSON.stringify({
          slug: "code-block-pellet",
          title: "Code Block Title",
          tags: ["test"],
          owlsInvolved: ["Owl"],
          content: "Content",
        }) +
        "\n```",
      ),
    };

    const generator = new PelletGenerator(mockRouter);
    const result = await generator.generate("source", "source");

    expect(result).not.toBeNull();
    expect(result!.id).toBe("code-block-pellet");
  });

  it("should return null on parse error", async () => {
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue("This is not valid JSON at all"),
    };

    const generator = new PelletGenerator(mockRouter);
    const result = await generator.generate("source", "source");

    expect(result).toBeNull();
  });

  it("should handle missing fields in LLM response", async () => {
    const mockRouter = {
      resolve: vi.fn().mockResolvedValue(
        JSON.stringify({
          slug: "partial-pellet",
          content: "Only content provided",
        }),
      ),
    };

    const generator = new PelletGenerator(mockRouter);
    const result = await generator.generate("source", "source");

    expect(result).not.toBeNull();
    expect(result!.id).toBe("partial-pellet");
    expect(result!.content).toBe("Only content provided");
    expect(result!.tags).toEqual([]);
  });
});

describe("PelletStore with graph", () => {
  let store: PelletStore;

  beforeEach(async () => {
    mockLanceState.delete(testSpace); // reset shared mock state
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
    await mkdir(testSpace, { recursive: true });
    store = new PelletStore(testSpace);
    await store.init();
  });

  it("should build knowledge graph and mark it as built", async () => {
    await store.save(
      {
        id: "graph-pellet-1",
        title: "Graph Pellet One",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["graph"],
        content: "Content for graph testing",
        version: 1,
      },
      { skipDedup: true },
    );

    await store.save(
      {
        id: "graph-pellet-2",
        title: "Graph Pellet Two",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["graph"],
        content: "More content for graph testing",
        version: 1,
      },
      { skipDedup: true },
    );

    await store.buildGraph();
    const graph = await store.getGraph();
    expect(graph).toBeDefined();
    expect(graph.isBuilt).toBe(true);
  });

  it("should return the same graph instance from getGraph()", async () => {
    await store.save(
      {
        id: "graph-test",
        title: "Graph Test",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: [],
        content: "Content",
        version: 1,
      },
      { skipDedup: true },
    );

    const graph1 = await store.getGraph();
    const graph2 = await store.getGraph();
    expect(graph1).toBe(graph2);
  });

  it("should find related pellets via findRelated", async () => {
    await store.save(
      {
        id: "related-1",
        title: "Related One",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["related"],
        content: "First related pellet content",
        version: 1,
      },
      { skipDedup: true },
    );

    await store.save(
      {
        id: "related-2",
        title: "Related Two",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["related"],
        content: "Second related pellet content",
        version: 1,
      },
      { skipDedup: true },
    );

    const related = await store.findRelated("related-1", 5);
    expect(related.length).toBeGreaterThan(0);
  });

  it("should search with graph enhancement", async () => {
    await store.save(
      {
        id: "graph-search-1",
        title: "Graph Search Test",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["search"],
        content: "Graph search content",
        version: 1,
      },
      { skipDedup: true },
    );

    const results = await store.searchWithGraph("graph search");
    expect(results.length).toBeGreaterThan(0);
  });

  it("should fall back to regular search when graph not built", async () => {
    await store.save(
      {
        id: "fallback-test",
        title: "Fallback Test",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: [],
        tags: ["fallback"],
        content: "Fallback test content",
        version: 1,
      },
      { skipDedup: true },
    );

    const results = await store.searchWithGraph("fallback");
    expect(results.length).toBeGreaterThan(0);
  });
});
