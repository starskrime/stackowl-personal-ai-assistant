import { describe, it, expect, beforeEach, vi } from "vitest";
import { PelletStore, type Pellet } from "../src/pellets/store.js";
import { PelletSearch } from "../src/pellets/search.js";
import { PelletGenerator } from "../src/pellets/generator.js";
import { TfIdfEngine } from "../src/pellets/tfidf.js";
import { PelletDeduplicator } from "../src/pellets/dedup.js";
import type { ModelProvider, ChatResponse } from "../src/providers/base.js";
import type { OwlInstance } from "../src/owls/persona.js";
import { rm, mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";

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

const createMockOwl = (name = "TestOwl"): OwlInstance => ({
  persona: {
    name,
    type: "test",
    emoji: "🦉",
    challengeLevel: "medium",
    specialties: ["testing"],
    traits: ["analytical"],
    systemPrompt: "You are a test owl.",
    sourcePath: "/test/owl.md",
  },
  dna: {
    owl: name,
    generation: 0,
    created: new Date().toISOString(),
    lastEvolved: new Date().toISOString(),
    learnedPreferences: {},
    evolvedTraits: {
      challengeLevel: "medium",
      verbosity: "balanced",
      humor: 0.3,
      formality: 0.5,
      proactivity: 0.5,
      riskTolerance: "moderate",
      teachingStyle: "adaptive",
      delegationPreference: "collaborative",
    },
    expertiseGrowth: {},
    domainConfidence: {},
    interactionStats: {
      totalConversations: 0,
      adviceAcceptedRate: 0,
      challengesGiven: 0,
      challengesAccepted: 0,
      parliamentSessions: 0,
    },
    evolutionLog: [],
  },
});

const testSpace = join(__dirname, ".pellets_test_workspace");

describe("TfIdfEngine", () => {
  let engine: TfIdfEngine;

  beforeEach(() => {
    engine = new TfIdfEngine(join(testSpace, "tfidf_test.json"));
  });

  it("should add and search documents", () => {
    engine.addDocument("doc1", {
      title: "JavaScript Programming",
      tags: "javascript web",
      content: "JavaScript is a programming language for the web.",
    });

    const results = engine.search("javascript");
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].id).toBe("doc1");
  });

  it("should return empty for no matches", () => {
    engine.addDocument("doc1", {
      title: "Python Guide",
      tags: "python",
      content: "Python is a great language.",
    });

    const results = engine.search("javascript");
    expect(results).toHaveLength(0);
  });

  it("should remove documents", () => {
    engine.addDocument("doc1", {
      title: "Test",
      tags: "test",
      content: "Test content",
    });

    engine.removeDocument("doc1");
    const results = engine.search("test");
    expect(results).toHaveLength(0);
  });

  it("should handle empty index", () => {
    expect(engine.isEmpty()).toBe(true);
    expect(engine.search("test")).toHaveLength(0);
  });
});

describe("PelletDeduplicator", () => {
  let engine: TfIdfEngine;
  let getPellet: (id: string) => Promise<Pellet | null>;
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

  beforeEach(async () => {
    engine = new TfIdfEngine(join(testSpace, "dedup_test.json"));
    await rm(join(testSpace, "dedup_test.json"), { force: true }).catch(
      () => {},
    );

    const pelletStore: Record<string, Pellet> = {
      "pellet-1": samplePellet,
    };

    getPellet = vi.fn().mockImplementation((id: string) => {
      return Promise.resolve(pelletStore[id] || null);
    });

    mockProvider = createMockProvider();
  });

  it("should return CREATE when dedup is disabled", async () => {
    const deduplicator = new PelletDeduplicator(
      engine,
      getPellet,
      mockProvider,
      {
        enabled: false,
      },
    );

    const result = await deduplicator.evaluate(samplePellet);
    expect(result.verdict).toBe("CREATE");
  });

  it("should return CREATE when no similar pellets found", async () => {
    const deduplicator = new PelletDeduplicator(
      engine,
      getPellet,
      mockProvider,
    );

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Completely Different Topic",
      content:
        "Nothing similar to existing pellets about cooking and gardening.",
    });

    expect(result.verdict).toBe("CREATE");
  });

  it("should use heuristic decision when no provider available", async () => {
    engine.addDocument("pellet-1", {
      title: "Test Pellet",
      tags: "test",
      content:
        "This is a test pellet content that has substantial information.",
    });

    const deduplicator = new PelletDeduplicator(engine, getPellet, undefined);

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Test Pellet Similar",
      content:
        "This is a similar test pellet content with more details about testing.",
    });

    expect(["CREATE", "SUPERSEDE", "SKIP"]).toContain(result.verdict);
  });

  it("should call LLM when provider is available", async () => {
    engine.addDocument("pellet-1", {
      title: "Test Pellet",
      tags: "test",
      content:
        "This is a test pellet content that has substantial information.",
    });

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

    const deduplicator = new PelletDeduplicator(
      engine,
      getPellet,
      mockProvider,
      {
        useLlm: true,
      },
    );

    const result = await deduplicator.evaluate({
      ...samplePellet,
      id: "new-pellet",
      title: "Test Pellet Similar",
      content:
        "This is a similar test pellet content with additional information.",
    });

    expect(result.verdict).toBe("MERGE");
    expect(mockProvider.chat).toHaveBeenCalled();
  });

  it("should fall back to CREATE on LLM error", async () => {
    engine.addDocument("pellet-1", {
      title: "Test Pellet",
      tags: "test",
      content:
        "This is a test pellet content that has substantial information.",
    });

    mockProvider = createMockProvider({
      chat: vi.fn().mockRejectedValue(new Error("LLM error")),
    });

    const deduplicator = new PelletDeduplicator(
      engine,
      getPellet,
      mockProvider,
      {
        useLlm: true,
      },
    );

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

    it("should write pellet with correct frontmatter", async () => {
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

      const mdPath = join(testSpace, "pellets", "frontmatter-test.md");
      const raw = await readFile(mdPath, "utf-8");

      expect(raw).toContain("title: Frontmatter Test");
      expect(raw).toContain("source: test-source");
      expect(raw).toContain("owls:");
      expect(raw).toContain("tag1");
      expect(raw).toContain("tag2");
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

    it("should cache results", async () => {
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

      expect(list1).toBe(list2);
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

      const deleted = await store.delete("delete-me");
      expect(deleted).toBe(true);

      const retrieved = await store.get("delete-me");
      expect(retrieved).toBeNull();
    });

    it("should return false for non-existent pellet", async () => {
      const deleted = await store.delete("non-existent");
      expect(deleted).toBe(false);
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

describe("PelletSearch", () => {
  let store: PelletStore;
  let search: PelletSearch;

  beforeEach(async () => {
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
    await mkdir(testSpace, { recursive: true });
    store = new PelletStore(testSpace);
    await store.init();

    await store.save(
      {
        id: "pellet-javascript",
        title: "JavaScript Guide",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: ["Noctua"],
        tags: ["programming", "web"],
        content: "JavaScript is a programming language for web development.",
        version: 1,
      },
      { skipDedup: true },
    );

    await store.save(
      {
        id: "pellet-python",
        title: "Python Guide",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: ["Archimedes"],
        tags: ["programming"],
        content: "Python is a versatile programming language.",
        version: 1,
      },
      { skipDedup: true },
    );

    await store.save(
      {
        id: "pellet-cooking",
        title: "Cooking Recipes",
        generatedAt: new Date().toISOString(),
        source: "test",
        owls: ["Noctua"],
        tags: ["food"],
        content: "Cooking tips and recipes for delicious meals.",
        version: 1,
      },
      { skipDedup: true },
    );

    search = new PelletSearch(store);
  });

  describe("search", () => {
    it("should find relevant pellets by query", async () => {
      const results = await search.search("JavaScript programming");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0].content).toContain("JavaScript");
    });

    it("should respect topK parameter", async () => {
      const results = await search.search("programming", 1);
      expect(results.length).toBeLessThanOrEqual(1);
    });

    it("should filter by minScore", async () => {
      const results = await search.search("programming", 5, 0.5);
      for (const r of results) {
        expect(r.score).toBeGreaterThanOrEqual(0.5);
      }
    });

    it("should return empty for no matches", async () => {
      const results = await search.search("xyz123nonexistent");
      expect(results).toHaveLength(0);
    });

    it("should return empty for empty query", async () => {
      const results = await search.search("");
      expect(results).toHaveLength(0);
    });
  });

  describe("getRelevantContext", () => {
    it("should format results for system prompt", async () => {
      const context = await search.getRelevantContext("JavaScript", 2);
      expect(context).toContain("<relevant_knowledge>");
      expect(context).toContain("</relevant_knowledge>");
    });

    it("should return empty string for no results", async () => {
      const context = await search.getRelevantContext("nonexistentterm123");
      expect(context).toBe("");
    });
  });

  describe("attribution tracking", () => {
    it("should record attribution", () => {
      search.recordAttribution("pellet-1", "session-123");
      expect(search.getEffectivePellets()).toHaveLength(0);
    });

    it("should record feedback", () => {
      search.recordAttribution("pellet-1", "session-123");
      search.recordFeedback("session-123", "positive");
      expect(search.getEffectivePellets()).toContain("pellet-1");
    });

    it("should not include negative feedback pellets", () => {
      search.recordAttribution("pellet-1", "session-123");
      search.recordFeedback("session-123", "negative");
      expect(search.getEffectivePellets()).not.toContain("pellet-1");
    });

    it("should handle multiple sessions", () => {
      search.recordAttribution("pellet-1", "session-1");
      search.recordAttribution("pellet-2", "session-2");
      search.recordFeedback("session-1", "positive");
      search.recordFeedback("session-2", "neutral");

      const effective = search.getEffectivePellets();
      expect(effective).toContain("pellet-1");
      expect(effective).not.toContain("pellet-2");
    });
  });
});

describe("PelletGenerator", () => {
  it("should generate pellet from source material", async () => {
    const mockChat = vi.fn().mockResolvedValue({
      content: JSON.stringify({
        slug: "generated-pellet",
        title: "Generated Title",
        tags: ["generated", "test"],
        owlsInvolved: ["TestOwl"],
        content: "## Key Insight\nGenerated content here.",
      }),
    } as ChatResponse);

    const provider = createMockProvider({ chat: mockChat });

    const generator = new PelletGenerator();
    const result = await generator.generate(
      "Raw source material content",
      "test-source",
      {
        provider,
        owl: createMockOwl("TestOwl"),
        config: {
          providers: {},
          parliament: {},
          heartbeat: {},
          owlDna: {},
          telegram: {},
        } as any,
      },
    );

    expect(result.id).toBe("generated-pellet");
    expect(result.title).toBe("Generated Title");
    expect(result.tags).toEqual(["generated", "test"]);
    expect(result.owls).toEqual(["TestOwl"]);
    expect(result.source).toBe("test-source");
    expect(result.version).toBe(1);
  });

  it("should handle JSON wrapped in code blocks", async () => {
    const mockChat = vi.fn().mockResolvedValue({
      content:
        "```json\n" +
        JSON.stringify({
          slug: "code-block-pellet",
          title: "Code Block Title",
          tags: ["test"],
          owlsInvolved: ["Owl"],
          content: "Content",
        }) +
        "\n```",
    } as ChatResponse);

    const provider = createMockProvider({ chat: mockChat });

    const generator = new PelletGenerator();
    const result = await generator.generate("source", "source", {
      provider,
      owl: createMockOwl("Owl"),
      config: {
        providers: {},
        parliament: {},
        heartbeat: {},
        owlDna: {},
        telegram: {},
      } as any,
    });

    expect(result.id).toBe("code-block-pellet");
  });

  it("should fall back to auto-generated pellet on parse error", async () => {
    const mockChat = vi.fn().mockResolvedValue({
      content: "This is not valid JSON at all",
    } as ChatResponse);

    const provider = createMockProvider({ chat: mockChat });

    const generator = new PelletGenerator();
    const result = await generator.generate("source", "source", {
      provider,
      owl: createMockOwl("Owl"),
      config: {
        providers: {},
        parliament: {},
        heartbeat: {},
        owlDna: {},
        telegram: {},
      } as any,
    });

    expect(result.title).toBe("Auto-generated Pellet");
    expect(result.tags).toEqual(["auto-generated"]);
    expect(result.content).toBe("This is not valid JSON at all");
  });

  it("should handle missing fields in LLM response", async () => {
    const mockChat = vi.fn().mockResolvedValue({
      content: JSON.stringify({
        slug: "partial-pellet",
        content: "Only content provided",
      }),
    } as ChatResponse);

    const provider = createMockProvider({ chat: mockChat });

    const generator = new PelletGenerator();
    const result = await generator.generate("source", "source", {
      provider,
      owl: createMockOwl("Owl"),
      config: {
        providers: {},
        parliament: {},
        heartbeat: {},
        owlDna: {},
        telegram: {},
      } as any,
    });

    expect(result.id).toBe("partial-pellet");
    expect(result.content).toBe("Only content provided");
    expect(result.tags).toEqual([]);
  });
});

describe("PelletStore with graph", () => {
  let store: PelletStore;

  beforeEach(async () => {
    await rm(testSpace, { recursive: true, force: true }).catch(() => {});
    await mkdir(testSpace, { recursive: true });
    store = new PelletStore(testSpace);
    await store.init();
  });

  it("should build and return knowledge graph", async () => {
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

    const graph = await store.buildGraph();
    expect(graph).toBeDefined();
    expect(graph.isBuilt()).toBe(true);
  });

  it("should get existing graph without rebuilding", async () => {
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

    const graph1 = await store.buildGraph();
    const graph2 = await store.getGraph();

    expect(graph1).toBe(graph2);
  });

  it("should find related pellets via graph", async () => {
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
