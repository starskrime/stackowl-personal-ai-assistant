import { describe, it, expect, vi, beforeEach } from "vitest";
import type { EngineContext, EngineResponse } from "../src/engine/runtime.js";
import type { ModelProvider, ChatMessage } from "../src/providers/base.js";
import type { OwlRegistry } from "../src/owls/registry.js";
import type { ToolRegistry } from "../src/tools/registry.js";
import type { PelletStore } from "../src/pellets/store.js";
import type { StackOwlConfig } from "../src/config/loader.js";
import type { OwlInstance, OwlDNA } from "../src/owls/persona.js";
import type { TaskStrategy } from "../src/orchestrator/types.js";
import type { ParliamentSession } from "../src/parliament/protocol.js";

// ─── Mock Logger ───────────────────────────────────────────────────────

vi.mock("../src/logger.js", () => ({
  log: {
    engine: {
      info: vi.fn(),
      warn: vi.fn(),
      debug: vi.fn(),
      error: vi.fn(),
    },
  },
}));

// ─── Mock ParliamentOrchestrator ───────────────────────────────────────

const mockParliamentSession: ParliamentSession = {
  id: "test-session-123",
  config: {
    topic: "test topic",
    participants: [],
    contextMessages: [],
  },
  phase: "complete",
  positions: [],
  challenges: [],
  startedAt: Date.now(),
  completedAt: Date.now(),
};

vi.mock("../src/parliament/orchestrator.js", () => ({
  ParliamentOrchestrator: vi.fn().mockImplementation(() => ({
    convene: vi.fn().mockResolvedValue(mockParliamentSession),
    formatSessionMarkdown: vi
      .fn()
      .mockReturnValue("# Parliament Result\n\nMock synthesis."),
  })),
}));

// ─── Mock SwarmBlackboard ─────────────────────────────────────────────

vi.mock("../src/swarm/blackboard.js", () => ({
  SwarmBlackboard: vi.fn().mockImplementation(() => ({
    write: vi.fn(),
    read: vi.fn(),
    has: vi.fn().mockReturnValue(false),
    waitFor: vi.fn().mockResolvedValue(undefined),
    getByAuthor: vi.fn().mockReturnValue([]),
    toSummary: vi.fn().mockReturnValue(""),
    clear: vi.fn(),
    get size() {
      return 0;
    },
  })),
}));

// ─── Mock TaskPlanner ─────────────────────────────────────────────────

vi.mock("../src/engine/planner.js", () => ({
  TaskPlanner: vi.fn().mockImplementation(() => ({
    createPlan: vi.fn().mockResolvedValue({
      steps: [
        {
          id: 1,
          description: "Mock step 1",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Mock step 2",
          dependsOn: [1],
          toolsNeeded: [],
        },
      ],
    }),
  })),
}));

// ─── Mock OwlEngine ────────────────────────────────────────────────────

const mockEngineResponse = (
  overrides: Partial<EngineResponse> = {},
): EngineResponse => ({
  content: "Mock response content",
  owlName: "Noctua",
  owlEmoji: "🦉",
  toolsUsed: [],
  challenged: false,
  modelUsed: "mock-model",
  newMessages: [],
  ...overrides,
});

vi.mock("../src/engine/runtime.js", () => ({
  OwlEngine: vi.fn().mockImplementation(() => ({
    run: vi.fn().mockResolvedValue(mockEngineResponse()),
  })),
}));

// ─── Factories ────────────────────────────────────────────────────────

function makeMockOwlInstance(
  overrides: Partial<OwlInstance> = {},
): OwlInstance {
  const base: OwlInstance = {
    persona: {
      name: "Noctua",
      type: "assistant",
      emoji: "🦉",
      challengeLevel: "medium",
      specialties: ["general", "planning"],
      traits: ["helpful", "wise"],
      systemPrompt: "You are Noctua, a helpful assistant.",
      sourcePath: "/test/noctua/OWL.md",
    },
    dna: makeMockDNA(),
  };

  if (!overrides.persona && !overrides.dna) {
    return base;
  }

  return {
    persona: {
      ...base.persona,
      ...overrides.persona,
    },
    dna: {
      ...base.dna,
      ...overrides.dna,
    },
  };
}

function makeMockDNA(overrides: Partial<OwlDNA["evolvedTraits"]> = {}): OwlDNA {
  return {
    owl: "Noctua",
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
      ...overrides,
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
  };
}

function makeMockProvider(responseContent = "Mock response"): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: responseContent,
      model: "mock-model",
      finishReason: "stop" as const,
    }),
    chatWithTools: vi.fn().mockResolvedValue({
      content: responseContent,
      model: "mock-model",
      finishReason: "stop" as const,
    }),
    chatStream: vi.fn(),
    embed: vi.fn().mockResolvedValue({ embedding: [0.1], model: "mock" }),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeMockConfig(): StackOwlConfig {
  return {
    providers: {
      ollama: { baseUrl: "http://localhost:11434", defaultModel: "llama3" },
      anthropic: {
        apiKey: "test-key",
        defaultModel: "claude-3-5-sonnet-latest",
      },
    },
    defaultProvider: "ollama",
    defaultModel: "llama3",
    workspace: "/tmp/test",
    gateway: { port: 3000, host: "localhost" },
    parliament: { maxRounds: 3, maxOwls: 3 },
    heartbeat: { enabled: false, intervalMinutes: 60 },
    owlDna: { enabled: true, evolutionBatchSize: 10, decayRatePerWeek: 0.1 },
    smartRouting: {
      enabled: true,
      fallbackProvider: "anthropic",
      fallbackModel: "claude-3-5-sonnet-latest",
      availableModels: [
        { name: "llama3:8b", description: "Fast" },
        { name: "claude-3-5-sonnet-latest", description: "Capable" },
      ],
    },
  };
}

function makeMockOwlRegistry(owls: OwlInstance[] = []): OwlRegistry {
  const registry = {
    get: vi.fn((name: string) => {
      const lower = name.toLowerCase();
      return owls.find((o) => o.persona.name.toLowerCase() === lower);
    }),
    listOwls: vi.fn(() => owls),
    getDefault: vi.fn(() => owls[0] ?? makeMockOwlInstance()),
  } as unknown as OwlRegistry;
  return registry;
}

function makeMockPelletStore(): PelletStore {
  return {
    save: vi.fn().mockResolvedValue(undefined),
    search: vi.fn().mockResolvedValue([]),
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn().mockResolvedValue(null),
    delete: vi.fn().mockResolvedValue(true),
  } as unknown as PelletStore;
}

function makeMockToolRegistry(): ToolRegistry {
  return {
    getAllDefinitions: vi.fn().mockReturnValue([]),
    execute: vi.fn().mockResolvedValue("mock result"),
    get: vi.fn().mockReturnValue(null),
  } as unknown as ToolRegistry;
}

function makeEngineContext(
  overrides: Partial<EngineContext> = {},
): EngineContext {
  const defaultOwl = makeMockOwlInstance();
  return {
    provider: makeMockProvider(),
    owl: defaultOwl,
    sessionHistory: [],
    config: makeMockConfig(),
    toolRegistry: makeMockToolRegistry(),
    owlRegistry: makeMockOwlRegistry([defaultOwl]),
    pelletStore: makeMockPelletStore(),
    cwd: "/tmp/test",
    ...overrides,
  };
}

// ─── Imports (after mocks) ─────────────────────────────────────────────

import { TaskOrchestrator } from "../src/orchestrator/orchestrator.js";
import { classifyStrategy } from "../src/orchestrator/classifier.js";
import { CrossAppPlanner } from "../src/orchestrator/cross-app.js";

// ══════════════════════════════════════════════════════════════════════════
// TASKORCHESTRATOR TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("TaskOrchestrator", () => {
  let orchestrator: TaskOrchestrator;
  let mockOwl: OwlInstance;
  let mockOwlRegistry: OwlRegistry;
  let mockProvider: ModelProvider;
  let mockConfig: StackOwlConfig;
  let mockPelletStore: PelletStore;

  beforeEach(() => {
    vi.clearAllMocks();

    mockOwl = makeMockOwlInstance({
      persona: { name: "Noctua", emoji: "🦉", type: "assistant" },
    });
    mockOwlRegistry = makeMockOwlRegistry([mockOwl]);
    mockProvider = makeMockProvider();
    mockConfig = makeMockConfig();
    mockPelletStore = makeMockPelletStore();

    orchestrator = new TaskOrchestrator(
      mockOwlRegistry,
      mockProvider,
      mockConfig,
      mockPelletStore,
    );
  });

  describe("executeWithFallback()", () => {
    it("returns result when strategy succeeds", async () => {
      const strategy: TaskStrategy = {
        strategy: "STANDARD",
        reasoning: "Default",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      };

      const result = await orchestrator.executeWithFallback(
        strategy,
        "hello",
        makeEngineContext({ owl: mockOwl }),
        {},
      );

      expect(result.strategy).toBe("STANDARD");
    });

    it("falls back to STANDARD when strategy throws", async () => {
      const { OwlEngine } = await import("../src/engine/runtime.js");
      vi.mocked(OwlEngine).mockImplementationOnce(
        vi.fn().mockImplementation(() => ({
          run: vi
            .fn()
            .mockRejectedValueOnce(new Error("Strategy failed"))
            .mockResolvedValueOnce(
              mockEngineResponse({ content: "Fallback response" }),
            ),
        })),
      );

      const strategy: TaskStrategy = {
        strategy: "PARLIAMENT",
        reasoning: "Test",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "test" },
        ],
      };

      const baseContext = makeEngineContext({ owl: mockOwl });
      const callbacks = { onProgress: vi.fn() };

      const result = await orchestrator.executeWithFallback(
        strategy,
        "hello",
        baseContext,
        callbacks,
      );

      expect(result.strategy).toBe("STANDARD");
      expect(callbacks.onProgress).toHaveBeenCalledWith(
        "Strategy failed, falling back to standard processing...",
      );
    });
  });

  describe("execute()", () => {
    it("routes DIRECT strategy to executeDirect", async () => {
      const { OwlEngine } = await import("../src/engine/runtime.js");
      const mockRun = vi
        .fn()
        .mockResolvedValue(
          mockEngineResponse({ content: "Direct response", owlName: "Noctua" }),
        );
      vi.mocked(OwlEngine).mockImplementation(
        vi.fn().mockImplementation(() => ({
          run: mockRun,
        })),
      );

      const strategy: TaskStrategy = {
        strategy: "DIRECT",
        reasoning: "Trivial",
        confidence: 1.0,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      };

      const result = await orchestrator.execute(
        strategy,
        "hi",
        makeEngineContext({ owl: mockOwl }),
        {},
      );

      expect(result.strategy).toBe("DIRECT");
      expect(mockRun).toHaveBeenCalledWith(
        "hi",
        expect.objectContaining({ skipGapDetection: true }),
      );
    });

    it("routes STANDARD strategy to executeStandard", async () => {
      const { OwlEngine } = await import("../src/engine/runtime.js");
      const mockRun = vi
        .fn()
        .mockResolvedValue(
          mockEngineResponse({ content: "Standard response" }),
        );
      vi.mocked(OwlEngine).mockImplementation(
        vi.fn().mockImplementation(() => ({
          run: mockRun,
        })),
      );

      const strategy: TaskStrategy = {
        strategy: "STANDARD",
        reasoning: "Default",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      };

      const result = await orchestrator.execute(
        strategy,
        "hello",
        makeEngineContext({ owl: mockOwl }),
        {},
      );

      expect(result.strategy).toBe("STANDARD");
    });

    it("routes SPECIALIST strategy to executeSpecialist", async () => {
      const specialistOwl = makeMockOwlInstance({
        persona: {
          name: "Archimedes",
          emoji: "🧠",
          type: "analyst",
          specialties: ["analysis"],
        },
      });
      const registryWithSpecialist = makeMockOwlRegistry([
        mockOwl,
        specialistOwl,
      ]);

      const { OwlEngine } = await import("../src/engine/runtime.js");
      const mockRun = vi.fn().mockResolvedValue(
        mockEngineResponse({
          content: "Specialist response",
          owlName: "Archimedes",
        }),
      );
      vi.mocked(OwlEngine).mockImplementation(
        vi.fn().mockImplementation(() => ({
          run: mockRun,
        })),
      );

      const specialistOrchestrator = new TaskOrchestrator(
        registryWithSpecialist,
        mockProvider,
        mockConfig,
        mockPelletStore,
      );

      const strategy: TaskStrategy = {
        strategy: "SPECIALIST",
        reasoning: "Analysis task",
        confidence: 0.8,
        owlAssignments: [
          { owlName: "Archimedes", role: "lead", reasoning: "domain match" },
        ],
      };

      const callbacks = { onProgress: vi.fn() };
      const result = await specialistOrchestrator.execute(
        strategy,
        "analyze this data",
        makeEngineContext({ owl: mockOwl }),
        callbacks,
      );

      expect(result.strategy).toBe("SPECIALIST");
      expect(callbacks.onProgress).toHaveBeenCalled();
    });

    it("routes PARLIAMENT strategy to executeParliament", async () => {
      const participant1 = makeMockOwlInstance({
        persona: { name: "Noctua", emoji: "🦉" },
      });
      const participant2 = makeMockOwlInstance({
        persona: { name: "Archimedes", emoji: "🧠" },
      });
      const registryWithParticipants = makeMockOwlRegistry([
        participant1,
        participant2,
      ]);

      const parliamentOrchestrator = new TaskOrchestrator(
        registryWithParticipants,
        mockProvider,
        mockConfig,
        mockPelletStore,
      );

      const strategy: TaskStrategy = {
        strategy: "PARLIAMENT",
        reasoning: "Decision needed",
        confidence: 0.7,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "primary" },
          { owlName: "Archimedes", role: "reviewer", reasoning: "analyst" },
        ],
      };

      const callbacks = { onProgress: vi.fn() };
      const result = await parliamentOrchestrator.execute(
        strategy,
        "should we use A or B?",
        makeEngineContext({ owl: participant1 }),
        callbacks,
      );

      expect(result.strategy).toBe("PARLIAMENT");
      expect(result.toolsUsed).toContain("summon_parliament");
    });

    it("routes SWARM strategy to executeSwarm", async () => {
      const participant1 = makeMockOwlInstance({
        persona: { name: "Noctua", emoji: "🦉" },
      });
      const participant2 = makeMockOwlInstance({
        persona: { name: "Archimedes", emoji: "🧠" },
      });
      const registryWithParticipants = makeMockOwlRegistry([
        participant1,
        participant2,
      ]);

      const { OwlEngine } = await import("../src/engine/runtime.js");
      const mockRun = vi
        .fn()
        .mockResolvedValue(mockEngineResponse({ content: "Swarm response" }));
      vi.mocked(OwlEngine).mockImplementation(
        vi.fn().mockImplementation(() => ({
          run: mockRun,
        })),
      );

      const swarmOrchestrator = new TaskOrchestrator(
        registryWithParticipants,
        mockProvider,
        mockConfig,
        mockPelletStore,
      );

      const strategy: TaskStrategy = {
        strategy: "SWARM",
        reasoning: "Multiple tasks",
        confidence: 0.8,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "primary" },
          { owlName: "Archimedes", role: "specialist", reasoning: "analysis" },
        ],
        subtasks: [
          {
            id: 1,
            description: "Task 1",
            assignedOwl: "Noctua",
            dependsOn: [],
            toolsNeeded: [],
          },
          {
            id: 2,
            description: "Task 2",
            assignedOwl: "Archimedes",
            dependsOn: [],
            toolsNeeded: [],
          },
        ],
      };

      const callbacks = { onProgress: vi.fn() };
      const result = await swarmOrchestrator.execute(
        strategy,
        "do multiple things",
        makeEngineContext({ owl: participant1 }),
        callbacks,
      );

      expect(result.strategy).toBe("SWARM");
      expect(callbacks.onProgress).toHaveBeenCalled();
    });

    it("routes PLANNED strategy to executePlanned", async () => {
      const { OwlEngine } = await import("../src/engine/runtime.js");
      const mockRun = vi
        .fn()
        .mockResolvedValue(mockEngineResponse({ content: "Planned response" }));
      vi.mocked(OwlEngine).mockImplementation(
        vi.fn().mockImplementation(() => ({
          run: mockRun,
        })),
      );

      const strategy: TaskStrategy = {
        strategy: "PLANNED",
        reasoning: "Multi-step",
        confidence: 0.8,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
        subtasks: [
          {
            id: 1,
            description: "Step 1",
            assignedOwl: "Noctua",
            dependsOn: [],
            toolsNeeded: [],
          },
          {
            id: 2,
            description: "Step 2",
            assignedOwl: "Noctua",
            dependsOn: [1],
            toolsNeeded: [],
          },
        ],
      };

      const callbacks = { onProgress: vi.fn() };
      const result = await orchestrator.execute(
        strategy,
        "do a multi-step task",
        makeEngineContext({ owl: mockOwl }),
        callbacks,
      );

      expect(result.strategy).toBe("PLANNED");
    });

    it("defaults to STANDARD for unknown strategy", async () => {
      const strategy = {
        strategy: "UNKNOWN" as any,
        reasoning: "Test",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      };

      const result = await orchestrator.execute(
        strategy,
        "hello",
        makeEngineContext({ owl: mockOwl }),
        {},
      );

      expect(result.strategy).toBe("STANDARD");
    });
  });

  describe("buildWaves()", () => {
    it("groups tasks by dependency level", () => {
      const tasks = [
        {
          id: 1,
          description: "Task 1",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Task 2",
          assignedOwl: "Noctua",
          dependsOn: [1],
          toolsNeeded: [],
        },
        {
          id: 3,
          description: "Task 3",
          assignedOwl: "Noctua",
          dependsOn: [2],
          toolsNeeded: [],
        },
      ];

      // Access private method via any
      const waves = (orchestrator as any).buildWaves(tasks);

      expect(waves).toHaveLength(3);
      expect(waves[0]).toHaveLength(1);
      expect(waves[1]).toHaveLength(1);
      expect(waves[2]).toHaveLength(1);
    });

    it("runs independent tasks in parallel (same wave)", () => {
      const tasks = [
        {
          id: 1,
          description: "Task 1",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Task 2",
          assignedOwl: "Archimedes",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 3,
          description: "Task 3",
          assignedOwl: "Noctua",
          dependsOn: [1, 2],
          toolsNeeded: [],
        },
      ];

      const waves = (orchestrator as any).buildWaves(tasks);

      expect(waves).toHaveLength(2);
      expect(waves[0]).toHaveLength(2); // Tasks 1 and 2 run in parallel
      expect(waves[1]).toHaveLength(1); // Task 3 depends on both
    });

    it("handles circular dependencies by forcing remaining tasks", () => {
      const tasks = [
        {
          id: 1,
          description: "Task 1",
          assignedOwl: "Noctua",
          dependsOn: [2],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Task 2",
          assignedOwl: "Noctua",
          dependsOn: [1],
          toolsNeeded: [],
        },
      ];

      const waves = (orchestrator as any).buildWaves(tasks);

      expect(waves).toHaveLength(2);
      expect(waves[1]).toHaveLength(2); // Both forced into final wave
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// CLASSIFIER TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("classifyStrategy()", () => {
  let mockProvider: ModelProvider;

  beforeEach(() => {
    vi.clearAllMocks();
    mockProvider = makeMockProvider();
  });

  it("returns DIRECT for trivial messages", async () => {
    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy("hi", owls, [], [], mockProvider);
    expect(result.strategy).toBe("DIRECT");
  });

  it("returns DIRECT for short thanks", async () => {
    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy("thanks", owls, [], [], mockProvider);
    expect(result.strategy).toBe("DIRECT");
  });

  it("returns DIRECT for short questions", async () => {
    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "What is 2+2?",
      owls,
      [],
      [],
      mockProvider,
    );
    expect(result.strategy).toBe("DIRECT");
  });

  it("parses LLM response into TaskStrategy", async () => {
    mockProvider = makeMockProvider(
      JSON.stringify({
        strategy: "STANDARD",
        reasoning: "General request",
        confidence: 0.8,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      }),
    );

    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "Help me with my code",
      owls,
      ["read_file", "write_file"],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("STANDARD");
    expect(result.confidence).toBe(0.8);
  });

  it("handles PARLIAMENT strategy from LLM", async () => {
    mockProvider = makeMockProvider(
      JSON.stringify({
        strategy: "PARLIAMENT",
        reasoning: "Decision needed",
        confidence: 0.9,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "primary" },
          { owlName: "Archimedes", role: "reviewer", reasoning: "analyst" },
        ],
        parliamentConfig: { topic: "A vs B", owlCount: 2 },
      }),
    );

    const owls = [
      makeMockOwlInstance({ persona: { name: "Noctua", emoji: "🦉" } }),
      makeMockOwlInstance({ persona: { name: "Archimedes", emoji: "🧠" } }),
    ];

    const result = await classifyStrategy(
      "Should I choose A or B?",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("PARLIAMENT");
    expect(result.parliamentConfig?.topic).toBe("A vs B");
  });

  it("handles SWARM strategy with subtasks from LLM", async () => {
    mockProvider = makeMockProvider(
      JSON.stringify({
        strategy: "SWARM",
        reasoning: "Independent tasks",
        confidence: 0.85,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "primary" },
          { owlName: "Archimedes", role: "specialist", reasoning: "analysis" },
        ],
        subtasks: [
          {
            id: 1,
            description: "Task 1",
            assignedOwl: "Noctua",
            dependsOn: [],
            toolsNeeded: [],
          },
          {
            id: 2,
            description: "Task 2",
            assignedOwl: "Archimedes",
            dependsOn: [],
            toolsNeeded: [],
          },
        ],
      }),
    );

    const owls = [
      makeMockOwlInstance({ persona: { name: "Noctua", emoji: "🦉" } }),
      makeMockOwlInstance({ persona: { name: "Archimedes", emoji: "🧠" } }),
    ];

    const result = await classifyStrategy(
      "Do multiple things in parallel",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("SWARM");
    expect(result.subtasks).toHaveLength(2);
  });

  it("defaults to STANDARD when JSON parsing fails", async () => {
    mockProvider = makeMockProvider("This is not JSON response");

    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "Help me",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("STANDARD");
  });

  it("defaults to STANDARD for invalid strategy type", async () => {
    mockProvider = makeMockProvider(
      JSON.stringify({
        strategy: "INVALID_STRATEGY",
        reasoning: "Test",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "Noctua", role: "lead", reasoning: "default" },
        ],
      }),
    );

    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "Help me",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("STANDARD");
  });

  it("falls back to Noctua or first owl when assignment owl not found", async () => {
    mockProvider = makeMockProvider(
      JSON.stringify({
        strategy: "STANDARD",
        reasoning: "Test",
        confidence: 0.5,
        owlAssignments: [
          { owlName: "NonExistentOwl", role: "lead", reasoning: "default" },
        ],
      }),
    );

    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "Help me",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.owlAssignments[0].owlName).toBe("Noctua");
  });

  it("strips thinking tags before parsing JSON", async () => {
    mockProvider = makeMockProvider(
      `<think> Some thinking here <think/> {"strategy": "SPECIALIST", "reasoning": "Test", "confidence": 0.9, "owlAssignments": [{"owlName": "Noctua", "role": "lead", "reasoning": "default"}]}`,
    );

    const owls = [makeMockOwlInstance()];
    const result = await classifyStrategy(
      "Analyze this",
      owls,
      [],
      [],
      mockProvider,
    );

    expect(result.strategy).toBe("SPECIALIST");
  });
});

// ══════════════════════════════════════════════════════════════════════════
// CROSSAPPCLASSIFIER TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("CrossAppPlanner", () => {
  let planner: CrossAppPlanner;
  let mockProvider: ModelProvider;

  beforeEach(() => {
    vi.clearAllMocks();
    mockProvider = makeMockProvider();
    planner = new CrossAppPlanner(mockProvider, undefined, "/tmp/test");
  });

  describe("plan()", () => {
    it("returns null when no tools or apps available", async () => {
      const result = await planner.plan("do something", [], []);
      expect(result).toBeNull();
    });

    it("returns null when LLM returns skip", async () => {
      mockProvider = makeMockProvider(JSON.stringify({ skip: true }));
      planner = new CrossAppPlanner(mockProvider, undefined, "/tmp/test");

      const result = await planner.plan("simple task", ["tool1"], []);
      expect(result).toBeNull();
    });

    it("returns null when no JSON in response", async () => {
      mockProvider = makeMockProvider("No JSON here");
      planner = new CrossAppPlanner(mockProvider, undefined, "/tmp/test");

      const result = await planner.plan("do something", ["tool1"], []);
      expect(result).toBeNull();
    });

    it("parses LLM response into ActionPlan", async () => {
      mockProvider = makeMockProvider(
        JSON.stringify({
          description: "Test plan",
          steps: [
            {
              id: "step-1",
              app: "github",
              action: "create_issue",
              args: { title: "Test" },
              dependsOn: [],
            },
          ],
          requiresConfirmation: true,
          estimatedDuration: "~1 min",
        }),
      );
      planner = new CrossAppPlanner(mockProvider, undefined, "/tmp/test");

      const result = await planner.plan(
        "create a github issue",
        ["github", "slack"],
        ["github"],
      );

      expect(result).not.toBeNull();
      expect(result!.description).toBe("Test plan");
      expect(result!.steps).toHaveLength(1);
      expect(result!.requiresConfirmation).toBe(true);
    });

    it("handles planning errors gracefully", async () => {
      mockProvider = makeMockProvider("invalid");
      Object.defineProperty(mockProvider, "chat", {
        value: vi.fn().mockRejectedValue(new Error("Provider error")),
      });
      planner = new CrossAppPlanner(mockProvider, undefined, "/tmp/test");

      const result = await planner.plan("do something", ["tool1"], []);
      expect(result).toBeNull();
    });
  });

  describe("buildWaves() (private)", () => {
    it("groups steps by dependency", () => {
      const steps = [
        {
          id: "step-1",
          app: "app1",
          action: "action1",
          args: {},
          dependsOn: [],
        },
        {
          id: "step-2",
          app: "app2",
          action: "action2",
          args: {},
          dependsOn: ["step-1"],
        },
        {
          id: "step-3",
          app: "app3",
          action: "action3",
          args: {},
          dependsOn: ["step-2"],
        },
      ];

      const waves = (planner as any).buildWaves(steps);

      expect(waves).toHaveLength(3);
    });

    it("runs independent steps in parallel", () => {
      const steps = [
        {
          id: "step-1",
          app: "app1",
          action: "action1",
          args: {},
          dependsOn: [],
        },
        {
          id: "step-2",
          app: "app2",
          action: "action2",
          args: {},
          dependsOn: [],
        },
        {
          id: "step-3",
          app: "app3",
          action: "action3",
          args: {},
          dependsOn: ["step-1", "step-2"],
        },
      ];

      const waves = (planner as any).buildWaves(steps);

      expect(waves).toHaveLength(2);
      expect(waves[0]).toHaveLength(2);
      expect(waves[1]).toHaveLength(1);
    });
  });

  describe("resolveArgs() (private)", () => {
    it("resolves template references from previous outputs", () => {
      const outputs = new Map<string, unknown>();
      outputs.set("step-1", { issueNumber: 42 });

      const args = { issueNumber: "{{ step-1.issueNumber }}" };

      const resolved = (planner as any).resolveArgs(args, outputs);

      expect(resolved.issueNumber).toBe(42);
    });

    it("keeps non-template values as-is", () => {
      const outputs = new Map<string, unknown>();
      const args = { title: "Static Title", count: 5 };

      const resolved = (planner as any).resolveArgs(args, outputs);

      expect(resolved.title).toBe("Static Title");
      expect(resolved.count).toBe(5);
    });

    it("falls back to original value when step output not found", () => {
      const outputs = new Map<string, unknown>();
      const args = { value: "{{ missing-step.field }}" };

      const resolved = (planner as any).resolveArgs(args, outputs);

      expect(resolved.value).toBe("{{ missing-step.field }}");
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// INTEGRATION-STYLE TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("Orchestrator integration scenarios", () => {
  let orchestrator: TaskOrchestrator;
  let mockOwl: OwlInstance;
  let mockOwlRegistry: OwlRegistry;
  let mockProvider: ModelProvider;
  let mockConfig: StackOwlConfig;
  let mockPelletStore: PelletStore;

  beforeEach(() => {
    vi.clearAllMocks();

    mockOwl = makeMockOwlInstance({ persona: { name: "Noctua", emoji: "🦉" } });
    mockOwlRegistry = makeMockOwlRegistry([mockOwl]);
    mockProvider = makeMockProvider();
    mockConfig = makeMockConfig();
    mockPelletStore = makeMockPelletStore();

    orchestrator = new TaskOrchestrator(
      mockOwlRegistry,
      mockProvider,
      mockConfig,
      mockPelletStore,
    );
  });

  it("handles PLANNED with no subtasks by using TaskPlanner", async () => {
    const { OwlEngine } = await import("../src/engine/runtime.js");
    const { TaskPlanner } = await import("../src/engine/planner.js");

    const mockRun = vi
      .fn()
      .mockResolvedValue(mockEngineResponse({ content: "Planned response" }));
    const mockCreatePlan = vi.fn().mockResolvedValue({
      steps: [
        {
          id: 1,
          description: "Planner step 1",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Planner step 2",
          dependsOn: [1],
          toolsNeeded: [],
        },
      ],
    });

    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({ run: mockRun })),
    );
    vi.mocked(TaskPlanner).mockImplementation(
      vi.fn().mockImplementation(() => ({ createPlan: mockCreatePlan })),
    );

    const strategy: TaskStrategy = {
      strategy: "PLANNED",
      reasoning: "Multi-step",
      confidence: 0.8,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "default" },
      ],
      subtasks: [], // Empty - should trigger TaskPlanner
    };

    const result = await orchestrator.execute(
      strategy,
      "do a complex multi-step task",
      makeEngineContext({ owl: mockOwl }),
      {},
    );

    expect(mockCreatePlan).toHaveBeenCalled();
    expect(result.strategy).toBe("PLANNED");
  });

  it("handles SWARM with single subtask by delegating to SPECIALIST", async () => {
    const { OwlEngine } = await import("../src/engine/runtime.js");
    const mockRun = vi
      .fn()
      .mockResolvedValue(
        mockEngineResponse({ content: "Single task response" }),
      );
    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({ run: mockRun })),
    );

    const strategy: TaskStrategy = {
      strategy: "SWARM",
      reasoning: "Single task",
      confidence: 0.8,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "default" },
      ],
      subtasks: [
        {
          id: 1,
          description: "Only task",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
      ],
    };

    const result = await orchestrator.execute(
      strategy,
      "do one thing",
      makeEngineContext({ owl: mockOwl }),
      {},
    );

    expect(result.strategy).toBe("SPECIALIST");
  });

  it("handles PARLIAMENT with fewer than 2 owls by using available owls", async () => {
    const onlyOwl = makeMockOwlInstance({
      persona: { name: "Noctua", emoji: "🦉" },
    });
    const registryWithOneOwl = makeMockOwlRegistry([onlyOwl]);

    const { OwlEngine } = await import("../src/engine/runtime.js");
    const mockRun = vi
      .fn()
      .mockResolvedValue(
        mockEngineResponse({ content: "Parliament fallback" }),
      );
    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({ run: mockRun })),
    );

    const singleOwlOrchestrator = new TaskOrchestrator(
      registryWithOneOwl,
      mockProvider,
      mockConfig,
      mockPelletStore,
    );

    const strategy: TaskStrategy = {
      strategy: "PARLIAMENT",
      reasoning: "Decision",
      confidence: 0.7,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "only one" },
      ],
    };

    // Should fall back to STANDARD since we only have 1 owl (need 2+ for parliament)
    const result = await singleOwlOrchestrator.execute(
      strategy,
      "decide something",
      makeEngineContext({ owl: onlyOwl }),
      {},
    );

    expect(result.strategy).toBe("STANDARD");
  });

  it("returns failure result when all PLANNED steps fail", async () => {
    const { OwlEngine } = await import("../src/engine/runtime.js");
    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({
        run: vi.fn().mockRejectedValue(new Error("Step failed")),
      })),
    );

    const strategy: TaskStrategy = {
      strategy: "PLANNED",
      reasoning: "Multi-step",
      confidence: 0.8,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "default" },
      ],
      subtasks: [
        {
          id: 1,
          description: "Failing step 1",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Failing step 2",
          assignedOwl: "Noctua",
          dependsOn: [1],
          toolsNeeded: [],
        },
      ],
    };

    const result = await orchestrator.execute(
      strategy,
      "do a multi-step task",
      makeEngineContext({ owl: mockOwl }),
      {},
    );

    expect(result.content).toContain("failed");
    expect(result.subtaskResults).toBeDefined();
    expect(result.subtaskResults!.every((r) => r.status === "failed")).toBe(
      true,
    );
  });

  it("returns failure result when all SWARM tasks fail", async () => {
    const { OwlEngine } = await import("../src/engine/runtime.js");
    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({
        run: vi.fn().mockRejectedValue(new Error("Task failed")),
      })),
    );

    const strategy: TaskStrategy = {
      strategy: "SWARM",
      reasoning: "Parallel tasks",
      confidence: 0.8,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "default" },
      ],
      subtasks: [
        {
          id: 1,
          description: "Task 1",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Task 2",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
      ],
    };

    const result = await orchestrator.execute(
      strategy,
      "do parallel tasks",
      makeEngineContext({ owl: mockOwl }),
      {},
    );

    expect(result.content).toContain("failed");
    expect(result.subtaskResults).toBeDefined();
    expect(result.subtaskResults!.every((r) => r.status === "failed")).toBe(
      true,
    );
  });

  it("synthesizes results from successful PLANNED steps", async () => {
    const { OwlEngine } = await import("../src/engine/runtime.js");
    let callCount = 0;
    vi.mocked(OwlEngine).mockImplementation(
      vi.fn().mockImplementation(() => ({
        run: vi.fn().mockImplementation(() => {
          callCount++;
          if (callCount <= 2) {
            // Subtask executions
            return Promise.resolve(
              mockEngineResponse({ content: `Step ${callCount} result` }),
            );
          }
          // Synthesis call
          return Promise.resolve(
            mockEngineResponse({ content: "Synthesized final answer" }),
          );
        }),
      })),
    );

    const strategy: TaskStrategy = {
      strategy: "PLANNED",
      reasoning: "Multi-step",
      confidence: 0.8,
      owlAssignments: [
        { owlName: "Noctua", role: "lead", reasoning: "default" },
      ],
      subtasks: [
        {
          id: 1,
          description: "Step 1",
          assignedOwl: "Noctua",
          dependsOn: [],
          toolsNeeded: [],
        },
        {
          id: 2,
          description: "Step 2",
          assignedOwl: "Noctua",
          dependsOn: [1],
          toolsNeeded: [],
        },
      ],
    };

    const result = await orchestrator.execute(
      strategy,
      "do a multi-step task",
      makeEngineContext({ owl: mockOwl }),
      {},
    );

    expect(result.content).toBe("Synthesized final answer");
    expect(result.usage).toBeDefined();
  });

  it("resolveOwl falls back to case-insensitive match", async () => {
    const archimedes = makeMockOwlInstance({
      persona: { name: "Archimedes", emoji: "🧠", type: "analyst" },
    });
    const registryWithArchimedes = makeMockOwlRegistry([mockOwl, archimedes]);

    const caseInsensitiveOrchestrator = new TaskOrchestrator(
      registryWithArchimedes,
      mockProvider,
      mockConfig,
      mockPelletStore,
    );

    // Access private method to test case-insensitive resolution
    const resolved = (caseInsensitiveOrchestrator as any).resolveOwl(
      "ARCHIMEDES",
    );

    expect(resolved).toBeDefined();
    expect(resolved!.persona.name).toBe("Archimedes");
  });
});
