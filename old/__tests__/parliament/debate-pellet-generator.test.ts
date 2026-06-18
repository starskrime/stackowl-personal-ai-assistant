import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ModelProvider } from "../../src/providers/base.js";
import type { StackOwlConfig } from "../../src/config/loader.js";
import type { OwlInstance } from "../../src/owls/persona.js";
import type { ParliamentSession } from "../../src/parliament/protocol.js";
import { DebatePelletGenerator, findRelatedDebatePellets, formatPastDebatesForContext } from "../../src/parliament/debate-pellet-generator.js";
import type { PelletStore, Pellet } from "../../src/pellets/store.js";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
    },
    parliament: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
      behavioral: vi.fn(),
    },
  },
}));

// Mock PelletGenerator
vi.mock("../../src/pellets/generator.js", () => ({
  PelletGenerator: vi.fn().mockImplementation(() => ({
    generate: vi.fn().mockResolvedValue({
      id: "test-pellet-123",
      title: "Parliament: Test Topic",
      generatedAt: new Date().toISOString(),
      source: "Parliament: Test Topic",
      owls: ["Noctua", "Archimedes"],
      tags: ["parliament", "debate", "multi-owl"],
      content: "Test content",
      version: 1,
    }),
  })),
  makeProviderRouter: vi.fn().mockReturnValue({ resolve: vi.fn() }),
}));

function makeMockOwlInstance(name: string): OwlInstance {
  return {
    persona: {
      name,
      type: "assistant",
      emoji: "🦉",
      challengeLevel: "medium",
      specialties: ["general"],
      traits: ["helpful"],
      systemPrompt: `You are ${name}.`,
      sourcePath: `/test/${name}/OWL.md`,
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
        totalConversations: 10,
        adviceAcceptedRate: 0.6,
        challengesGiven: 5,
        challengesAccepted: 3,
        parliamentSessions: 1,
      },
      evolutionLog: [],
    },
  };
}

function makeMockConfig(): StackOwlConfig {
  return {
    parliament: { maxRounds: 3, maxOwls: 3, enabled: true },
    defaultProvider: "mock",
    defaultModel: "mock-model",
  } as unknown as StackOwlConfig;
}

function makeMockProvider(): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: JSON.stringify({
        categories: ["technical", "code"],
        primaryCategory: "technical",
        reasoning: "TypeScript vs JavaScript is a code/technical topic",
      }),
      model: "mock",
      finishReason: "stop" as const,
    }),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeMockSession(): ParliamentSession {
  const owl1 = makeMockOwlInstance("Noctua");
  const owl2 = makeMockOwlInstance("Archimedes");

  return {
    id: "test-session-456",
    config: {
      topic: "Should we use TypeScript or JavaScript?",
      participants: [owl1, owl2],
      contextMessages: [],
      callbacks: {},
    },
    phase: "complete",
    positions: [
      {
        owlName: "Noctua",
        owlEmoji: "🦉",
        position: "FOR",
        argument: "TypeScript provides better type safety and tooling.",
      },
      {
        owlName: "Archimedes",
        owlEmoji: "🧠",
        position: "AGAINST",
        argument: "JavaScript is simpler and has faster iteration.",
      },
    ],
    challenges: [
      {
        owlName: "Archimedes",
        targetOwl: "Noctua",
        challengeContent: "TypeScript adds complexity without proportional benefit.",
      },
    ],
    synthesis: "After careful consideration, we recommend TypeScript for larger projects but JavaScript for quick prototypes.",
    verdict: "PROCEED",
    startedAt: Date.now(),
    completedAt: Date.now(),
  };
}

function makeMockPelletStore(): PelletStore {
  return {
    save: vi.fn().mockResolvedValue({ verdict: "CREATE", reasoning: "new" }),
    search: vi.fn().mockResolvedValue([]),
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn().mockResolvedValue(null),
    delete: vi.fn().mockResolvedValue(true),
    count: vi.fn().mockResolvedValue(0),
    init: vi.fn().mockResolvedValue(undefined),
  } as unknown as PelletStore;
}

describe("DebatePelletGenerator", () => {
  let generator: DebatePelletGenerator;
  let mockProvider: ModelProvider;
  let mockConfig: StackOwlConfig;

  beforeEach(() => {
    mockProvider = makeMockProvider();
    mockConfig = makeMockConfig();
    generator = new DebatePelletGenerator(mockProvider, mockConfig);
  });

  describe("generateFromSession()", () => {
    it("generates a pellet from a Parliament session", async () => {
      const session = makeMockSession();
      const pelletStore = makeMockPelletStore();

      const pellet = await generator.generateFromSession(session, pelletStore);

      expect(pellet).not.toBeNull();
      expect(pellet?.id).toBe("test-pellet-123");
      expect(pellet?.owls).toContain("Noctua");
      expect(pellet?.owls).toContain("Archimedes");
    });

    it("tags pellet with parliament and verdict", async () => {
      const session = makeMockSession();
      const pelletStore = makeMockPelletStore();

      const pellet = await generator.generateFromSession(session, pelletStore);

      expect(pellet?.tags).toContain("parliament");
      expect(pellet?.tags).toContain("debate");
      expect(pellet?.tags).toContain("multi-owl");
      expect(pellet?.tags).toContain("proceed");
    });

    it("saves pellet to store", async () => {
      const session = makeMockSession();
      const pelletStore = makeMockPelletStore();

      await generator.generateFromSession(session, pelletStore);

      expect(pelletStore.save).toHaveBeenCalled();
    });
  });

  describe("generateDebateSummary()", () => {
    it("formats session into markdown summary", () => {
      const session = makeMockSession();
      const summary = generator.generateDebateSummary(session);

      expect(summary).toContain("Parliament Debate: Should we use TypeScript or JavaScript?");
      expect(summary).toContain("Noctua");
      expect(summary).toContain("PROCEED");
      expect(summary).toContain("Positions");
      expect(summary).toContain("Cross-Examination");
    });

    it("includes verdict in summary", () => {
      const session = makeMockSession();
      const summary = generator.generateDebateSummary(session);

      expect(summary).toContain("## Verdict");
      expect(summary).toContain("**PROCEED**");
    });

    it("includes synthesis in summary", () => {
      const session = makeMockSession();
      const summary = generator.generateDebateSummary(session);

      expect(summary).toContain("## Synthesis");
      expect(summary).toContain("After careful consideration");
    });
  });

  describe("extractKeyInsights()", () => {
    it("extracts insights from positions", () => {
      const session = makeMockSession();
      const insights = generator.extractKeyInsights(session);

      expect(insights.length).toBeGreaterThan(0);
      expect(insights[0]).toContain("TypeScript");
    });

    it("limits insights to 10", () => {
      const session = makeMockSession();
      const insights = generator.extractKeyInsights(session);

      expect(insights.length).toBeLessThanOrEqual(10);
    });
  });

  describe("generateTags()", () => {
    it("includes verdict tag", async () => {
      const session = makeMockSession();
      const tags = await generator.generateTags(session);

      expect(tags).toContain("proceed");
    });

    it("adds technical tag for code/framework topics", async () => {
      const session = makeMockSession();
      const tags = await generator.generateTags(session);

      expect(tags).toContain("technical");
    });

    it("deduplicates tags", async () => {
      const session = makeMockSession();
      const tags = await generator.generateTags(session);

      const uniqueTags = [...new Set(tags)];
      expect(tags.length).toBe(uniqueTags.length);
    });
  });
});

describe("findRelatedDebatePellets()", () => {
  it("searches pellet store for related topics", async () => {
    const mockPellet: Pellet = {
      id: "past-debate-1",
      title: "Past: React vs Vue",
      generatedAt: new Date().toISOString(),
      source: "Parliament",
      owls: ["Noctua"],
      tags: ["parliament", "debate"],
      content: "Discussion about frontend frameworks",
      version: 1,
    };

    const pelletStore = {
      search: vi.fn().mockResolvedValue([mockPellet]),
    } as unknown as PelletStore;

    const pellets = await findRelatedDebatePellets(pelletStore, "framework decision", 5);

    expect(pellets).toHaveLength(1);
    expect(pellets[0].id).toBe("past-debate-1");
  });

  it("filters for parliament/debate tagged pellets", async () => {
    const nonParliamentPellet: Pellet = {
      id: "regular-pellet",
      title: "Regular knowledge",
      generatedAt: new Date().toISOString(),
      source: "General",
      owls: ["Noctua"],
      tags: ["knowledge"],
      content: "Some knowledge",
      version: 1,
    };

    const pelletStore = {
      search: vi.fn().mockResolvedValue([nonParliamentPellet]),
    } as unknown as PelletStore;

    const pellets = await findRelatedDebatePellets(pelletStore, "topic", 5);

    expect(pellets).toHaveLength(0);
  });

  it("handles errors gracefully", async () => {
    const pelletStore = {
      search: vi.fn().mockRejectedValue(new Error("Search failed")),
    } as unknown as PelletStore;

    const pellets = await findRelatedDebatePellets(pelletStore, "topic", 5);

    expect(pellets).toHaveLength(0);
  });
});

describe("formatPastDebatesForContext()", () => {
  it("formats pellets into context string", () => {
    const pellets: Pellet[] = [
      {
        id: "debate-1",
        title: "Past: Architecture decision",
        generatedAt: new Date().toISOString(),
        source: "Parliament",
        owls: ["Noctua"],
        tags: ["parliament"],
        content: "Recommended microservices",
        version: 1,
      },
    ];

    const context = formatPastDebatesForContext(pellets);

    expect(context).toContain("Past Parliament decisions");
    expect(context).toContain("Architecture decision");
    expect(context).toContain("microservices");
  });

  it("returns empty string for empty pellets", () => {
    const context = formatPastDebatesForContext([]);

    expect(context).toBe("");
  });
});