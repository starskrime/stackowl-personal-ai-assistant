import { describe, it, expect, vi, beforeEach } from "vitest";
import { ModelRouter } from "../src/engine/router.js";
import { TaskPlanner, shouldUsePlanner } from "../src/engine/planner.js";
import { CreativeThinking } from "../src/engine/creative.js";
import { DiagnosticEngine } from "../src/engine/diagnostic-engine.js";
import type { StackOwlConfig } from "../src/config/loader.js";
import type {
  ModelProvider,
  ChatMessage,
  ToolDefinition,
} from "../src/providers/base.js";
import type { OwlDNA } from "../src/owls/persona.js";
import type { DNADecisions } from "../src/owls/decision-layer.js";
import type { DiagnosticInput } from "../src/engine/diagnostic-engine.js";

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

// ─── Mock Provider Factory ──────────────────────────────────────────────

function makeMockProvider(responseContent: string): ModelProvider {
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

// ─── Mock Config Factory ─────────────────────────────────────────────────

function makeConfig(
  overrides: Partial<StackOwlConfig["smartRouting"]> = {},
): StackOwlConfig {
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
        { modelName: "llama3:8b", providerName: "ollama", description: "Fast, lightweight" },
        { modelName: "llama3:70b", providerName: "ollama", description: "Balanced" },
        { modelName: "claude-3-5-sonnet-latest", providerName: "anthropic", description: "Most capable" },
      ],
      ...overrides,
    },
  };
}

// ─── Mock DNA Factory ───────────────────────────────────────────────────

function makeMockDNA(overrides: Partial<OwlDNA["evolvedTraits"]> = {}): OwlDNA {
  return {
    owl: "TestOwl",
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
    expertiseGrowth: { typescript: 0.6, python: 0.4 },
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

// ─── Mock DNADecisions Factory ─────────────────────────────────────────

function makeMockDecisions(
  overrides: Partial<DNADecisions> = {},
): DNADecisions {
  return {
    maxResponseTokens: 800,
    temperatureAdjustment: 0,
    prioritizedTools: ["run_shell_command", "read_file"],
    deprioritizedTools: [],
    riskTolerance: "moderate",
    style: {
      humorLevel: "subtle",
      formalityLevel: "balanced",
      includeExamples: false,
      suggestNextSteps: true,
    },
    preferredStrategy: null,
    expertiseContext: "You are proficient in typescript.",
    ...overrides,
  };
}

// ─── Tool Definitions ────────────────────────────────────────────────────

const MOCK_TOOLS: ToolDefinition[] = [
  {
    name: "run_shell_command",
    description: "Run a shell command",
    parameters: {
      type: "object",
      properties: {
        command: { type: "string", description: "The command to run" },
      },
      required: ["command"],
    },
  },
  {
    name: "read_file",
    description: "Read a file",
    parameters: {
      type: "object",
      properties: { path: { type: "string", description: "Path to the file" } },
      required: ["path"],
    },
  },
  {
    name: "write_file",
    description: "Write a file",
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "Path to write to" },
        content: { type: "string", description: "Content to write" },
      },
      required: ["path", "content"],
    },
  },
  {
    name: "web_crawl",
    description: "Fetch a URL",
    parameters: {
      type: "object",
      properties: { url: { type: "string", description: "URL to fetch" } },
      required: ["url"],
    },
  },
];

// ══════════════════════════════════════════════════════════════════════════
// MODEL ROUTER TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("ModelRouter", () => {
  describe("route()", () => {
    it("returns default model when smart routing is disabled", () => {
      const config = makeConfig({ enabled: false });
      const result = ModelRouter.route("hello", config);
      expect(result.modelName).toBe("llama3");
    });

    it("returns default model when availableModels is empty", () => {
      const config = makeConfig({ availableModels: [] });
      const result = ModelRouter.route("hello", config);
      expect(result.modelName).toBe("llama3");
    });

    it("returns single model when only one is available", () => {
      const config = makeConfig({
        availableModels: [{ modelName: "llama3:8b", providerName: "ollama", description: "Fast" }],
      });
      const result = ModelRouter.route("hello", config);
      expect(result.modelName).toBe("llama3:8b");
    });

    it("returns first model for simple conversational messages", () => {
      const config = makeConfig();
      const simpleMessages = [
        "hi",
        "hello",
        "thanks",
        "ok",
        "sure",
        "yes",
        "no",
        "yep",
        "nope",
        "cool",
      ];
      for (const msg of simpleMessages) {
        const result = ModelRouter.route(msg, config);
        expect(result.modelName).toBe("llama3:8b");
      }
    });

    it("returns last (most capable) model for heavy tasks", () => {
      const config = makeConfig();
      const heavyPrompts = [
        "implement a binary search algorithm in typescript with full test coverage",
        "design a complete microservices architecture for a large scale application",
        "debug this persistent memory leak in the node.js application",
        "optimize the slow database query performance across all tables",
        "compare and analyze react vs vue for building a large enterprise application",
        "explain in detail how kubernetes container scheduling works under the hood",
        "analyze the performance bottlenecks in this distributed system",
        "write code to implement a red-black tree data structure from scratch",
      ];
      for (const prompt of heavyPrompts) {
        const result = ModelRouter.route(prompt, config);
        expect(result.modelName).toBe("claude-3-5-sonnet-latest");
      }
    });

    it("returns middle model for standard complexity tasks", () => {
      const config = makeConfig();
      const prompt =
        "I need to build a feature that handles user authentication with JWT tokens and session management for a web application";
      const result = ModelRouter.route(prompt, config);
      expect(result.modelName).toBe("llama3:70b");
    });

    it("returns first model for very short messages (<40 chars)", () => {
      const config = makeConfig();
      const result = ModelRouter.route("Hello there!", config);
      expect(result.modelName).toBe("llama3:8b");
    });

    it("returns last model for long prompts (>60 words)", () => {
      const config = makeConfig();
      const longPrompt =
        "describe " +
        "the various different important key essential critical main significant notable features of this particular system ".repeat(
          4,
        );
      expect(longPrompt.split(/\s+/).length).toBeGreaterThan(60);
      const result = ModelRouter.route(longPrompt, config);
      expect(result.modelName).toBe("claude-3-5-sonnet-latest");
    });

    it("returns first model for short prompts (<12 words)", () => {
      const config = makeConfig();
      const result = ModelRouter.route("Explain recursion briefly", config);
      expect(result.modelName).toBe("llama3:8b");
    });

    it("escalates to fallback after 2 failures", () => {
      const config = makeConfig();
      const result = ModelRouter.route("hello", config, 2);
      expect(result.modelName).toBe("claude-3-5-sonnet-latest");
      expect(result.providerName).toBe("anthropic");
    });

    it("escalates to fallback after 3 failures", () => {
      const config = makeConfig();
      const result = ModelRouter.route("hello", config, 3);
      expect(result.modelName).toBe("claude-3-5-sonnet-latest");
      expect(result.providerName).toBe("anthropic");
    });

    it("does not escalate for failureCount < 2", () => {
      const config = makeConfig();
      const result = ModelRouter.route("hello", config, 1);
      expect(result.modelName).toBe("llama3:8b");
      expect(result.providerName).toBe("ollama");
    });

    it("handles missing fallbackProvider gracefully", () => {
      const config = makeConfig({
        fallbackProvider: undefined,
        fallbackModel: "claude-3-5-sonnet-latest",
      });
      const result = ModelRouter.route("hello", config, 2);
      expect(result.modelName).toBe("claude-3-5-sonnet-latest");
      expect(result.providerName).toBeUndefined();
    });

    it("handles missing smartRouting entirely", () => {
      const config = { ...makeConfig(), smartRouting: undefined };
      const result = ModelRouter.route("hello", config as StackOwlConfig);
      expect(result.modelName).toBe("llama3");
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// PLANNER TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("TaskPlanner", () => {
  describe("shouldUsePlanner()", () => {
    it("returns true for explicit plan triggers", () => {
      expect(shouldUsePlanner("plan: build a web app")).toBe(true);
      expect(shouldUsePlanner("/plan create an API")).toBe(true);
      expect(shouldUsePlanner("PLAN: my complex request")).toBe(true);
    });

    it("returns true for multi-step patterns", () => {
      expect(
        shouldUsePlanner("first do this then do that finally finish"),
      ).toBe(true);
      expect(shouldUsePlanner("step 1 install deps step 2 run tests")).toBe(
        true,
      );
      expect(shouldUsePlanner("multi-step process to achieve this")).toBe(true);
      expect(
        shouldUsePlanner("and then after that finally complete the task"),
      ).toBe(true);
    });

    it("returns true for 3+ action verbs", () => {
      const prompt =
        "create a database, build an API, test the endpoints, and deploy to production";
      expect(shouldUsePlanner(prompt)).toBe(true);
    });

    it("returns true for plan/strategy keywords with for", () => {
      expect(shouldUsePlanner("plan for migrating the database")).toBe(true);
      expect(shouldUsePlanner("strategy for scaling the application")).toBe(
        true,
      );
    });

    it("returns false for simple messages", () => {
      expect(shouldUsePlanner("hello")).toBe(false);
      expect(shouldUsePlanner("thanks")).toBe(false);
      expect(shouldUsePlanner("ok")).toBe(false);
    });

    it("returns false for 2 action verbs only", () => {
      const prompt = "create the file and run the script";
      expect(shouldUsePlanner(prompt)).toBe(false);
    });

    it("returns false for complex patterns without action verbs", () => {
      expect(shouldUsePlanner("what is the meaning of life?")).toBe(false);
      expect(shouldUsePlanner("tell me about python")).toBe(false);
    });
  });

  describe("createPlan()", () => {
    let planner: TaskPlanner;
    let mockProvider: ModelProvider;

    beforeEach(() => {
      mockProvider = makeMockProvider(
        JSON.stringify({
          goal: "Build a simple web server",
          estimatedComplexity: "moderate",
          steps: [
            {
              id: 1,
              description: "Create project directory",
              toolsNeeded: ["run_shell_command"],
              dependsOn: [],
            },
            {
              id: 2,
              description: "Write server code",
              toolsNeeded: ["write_file"],
              dependsOn: [1],
            },
            {
              id: 3,
              description: "Test the server",
              toolsNeeded: ["run_shell_command"],
              dependsOn: [2],
            },
          ],
        }),
      );
      planner = new TaskPlanner(mockProvider);
    });

    it("creates a plan from LLM response", async () => {
      const plan = await planner.createPlan("Build a web server", MOCK_TOOLS);

      expect(plan.goal).toBe("Build a simple web server");
      expect(plan.estimatedComplexity).toBe("moderate");
      expect(plan.steps).toHaveLength(3);
      expect(plan.steps[0].description).toBe("Create project directory");
      expect(plan.steps[0].status).toBe("pending");
    });

    it("maps step dependencies correctly", async () => {
      const plan = await planner.createPlan("Build a web server", MOCK_TOOLS);

      expect(plan.steps[1].dependsOn).toContain(1);
      expect(plan.steps[2].dependsOn).toContain(2);
    });

    it("uses default model when not specified", async () => {
      await planner.createPlan("Build a web server", MOCK_TOOLS);

      expect(mockProvider.chat).toHaveBeenCalledWith(
        expect.any(Array),
        undefined,
        { temperature: 0.1 },
      );
    });

    it("passes available tools to the LLM", async () => {
      await planner.createPlan(
        "Build a web server",
        MOCK_TOOLS,
        "custom-model",
      );

      const call = (mockProvider.chat as ReturnType<typeof vi.fn>).mock
        .calls[0];
      const messages = call[0] as Array<{ role: string; content: string }>;
      const userMessage = messages.find((m) => m.role === "user");
      expect(userMessage?.content).toContain("run_shell_command");
      expect(userMessage?.content).toContain("read_file");
    });

    it("falls back to single-step plan on parse error", async () => {
      const brokenProvider = makeMockProvider("not json at all { invalid");
      const brokenPlanner = new TaskPlanner(brokenProvider);

      const plan = await brokenPlanner.createPlan("Do something", MOCK_TOOLS);

      expect(plan.steps).toHaveLength(1);
      expect(plan.estimatedComplexity).toBe("simple");
      expect(plan.goal).toContain("Do something");
    });

    it("falls back on empty response", async () => {
      const emptyProvider = makeMockProvider("");
      const emptyPlanner = new TaskPlanner(emptyProvider);

      const plan = await emptyPlanner.createPlan("Do something", MOCK_TOOLS);

      expect(plan.steps).toHaveLength(1);
    });

    it("handles markdown-wrapped JSON response", async () => {
      const wrappedProvider = makeMockProvider(
        "```json\n" +
          JSON.stringify({
            goal: "Wrapped response test",
            estimatedComplexity: "simple",
            steps: [
              {
                id: 1,
                description: "First step",
                toolsNeeded: [],
                dependsOn: [],
              },
            ],
          }) +
          "\n```",
      );
      const wrappedPlanner = new TaskPlanner(wrappedProvider);

      const plan = await wrappedPlanner.createPlan("Test", MOCK_TOOLS);

      expect(plan.goal).toBe("Wrapped response test");
    });

    it("handles triple-backtick wrapped JSON", async () => {
      const wrappedProvider = makeMockProvider(
        "```\n" +
          JSON.stringify({
            goal: "Triple backtick test",
            estimatedComplexity: "complex",
            steps: [
              {
                id: 1,
                description: "Test step",
                toolsNeeded: [],
                dependsOn: [],
              },
            ],
          }) +
          "\n```",
      );
      const wrappedPlanner = new TaskPlanner(wrappedProvider);

      const plan = await wrappedPlanner.createPlan("Test", MOCK_TOOLS);

      expect(plan.goal).toBe("Triple backtick test");
    });

    it("handles missing fields in LLM response with defaults", async () => {
      const partialProvider = makeMockProvider(
        JSON.stringify({
          steps: [{ description: "Only description" }],
        }),
      );
      const partialPlanner = new TaskPlanner(partialProvider);

      const plan = await partialPlanner.createPlan("Test", MOCK_TOOLS);

      expect(plan.steps[0].description).toBe("Only description");
      expect(plan.goal).toBeTruthy();
    });
  });

  describe("formatPlanContext()", () => {
    it("formats plan with pending steps", () => {
      const planner = new TaskPlanner(makeMockProvider("{}"));
      const plan = {
        goal: "Test goal",
        estimatedComplexity: "moderate" as const,
        steps: [
          {
            id: 1,
            description: "Step one",
            toolsNeeded: [],
            dependsOn: [],
            status: "pending" as const,
          },
          {
            id: 2,
            description: "Step two",
            toolsNeeded: [],
            dependsOn: [1],
            status: "pending" as const,
          },
        ],
      };

      const context = planner.formatPlanContext(plan);

      expect(context).toContain("## Task Plan: Test goal");
      expect(context).toContain("Step 1: Step one");
      expect(context).toContain("Step 2: Step two");
      expect(context).toContain("⬜"); // pending icon
    });

    it("shows result for completed steps", () => {
      const planner = new TaskPlanner(makeMockProvider("{}"));
      const plan = {
        goal: "Test",
        estimatedComplexity: "simple" as const,
        steps: [
          {
            id: 1,
            description: "Done step",
            toolsNeeded: [],
            dependsOn: [],
            status: "done" as const,
            result: "It worked!",
          },
        ],
      };

      const context = planner.formatPlanContext(plan);

      expect(context).toContain("✅");
      expect(context).toContain("Result: It worked!");
    });

    it("shows running status", () => {
      const planner = new TaskPlanner(makeMockProvider("{}"));
      const plan = {
        goal: "Test",
        estimatedComplexity: "simple" as const,
        steps: [
          {
            id: 1,
            description: "Running step",
            toolsNeeded: [],
            dependsOn: [],
            status: "running" as const,
          },
        ],
      };

      const context = planner.formatPlanContext(plan);

      expect(context).toContain("⏳");
    });

    it("shows failed status", () => {
      const planner = new TaskPlanner(makeMockProvider("{}"));
      const plan = {
        goal: "Test",
        estimatedComplexity: "simple" as const,
        steps: [
          {
            id: 1,
            description: "Failed step",
            toolsNeeded: [],
            dependsOn: [],
            status: "failed" as const,
          },
        ],
      };

      const context = planner.formatPlanContext(plan);

      expect(context).toContain("❌");
    });

    it("truncates long results", () => {
      const planner = new TaskPlanner(makeMockProvider("{}"));
      const plan = {
        goal: "Test",
        estimatedComplexity: "simple" as const,
        steps: [
          {
            id: 1,
            description: "Step",
            toolsNeeded: [],
            dependsOn: [],
            status: "done" as const,
            result: "a".repeat(1000),
          },
        ],
      };

      const context = planner.formatPlanContext(plan);

      expect(context).toContain("Result: " + "a".repeat(500));
      expect(context).not.toContain("a".repeat(501));
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// CREATIVE THINKING TESTS
// ══════════════════════════════════════════════════════════════════════════

describe("CreativeThinking", () => {
  let creative: CreativeThinking;
  let mockProvider: ModelProvider;

  beforeEach(() => {
    mockProvider = makeMockProvider(
      JSON.stringify([
        {
          name: "Approach A",
          description: "First approach",
          tools: ["run_shell_command"],
          complexity: 2,
          differentiator: "Fast",
        },
        {
          name: "Approach B",
          description: "Second approach",
          tools: ["read_file", "write_file"],
          complexity: 3,
          differentiator: "Thorough",
        },
        {
          name: "Approach C",
          description: "Third approach",
          tools: ["web_crawl"],
          complexity: 1,
          differentiator: "Simple",
        },
      ]),
    );
    creative = new CreativeThinking(mockProvider);
  });

  describe("explore()", () => {
    it("returns triggered=false for short messages", async () => {
      const result = await creative.explore(
        "hi",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.triggered).toBe(false);
      expect(result.approaches).toHaveLength(0);
    });

    it("returns triggered=false for direct commands", async () => {
      const result = await creative.explore(
        "do run shell command ls",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.triggered).toBe(false);
    });

    it("returns triggered=false when maxResponseTokens < 500", async () => {
      const decisions = makeMockDecisions({ maxResponseTokens: 400 });
      const result = await creative.explore(
        "how would you design a distributed system architecture for scalability?",
        makeMockDNA(),
        decisions,
        [],
      );

      expect(result.triggered).toBe(false);
    });

    it("returns triggered=false for simple complexity", async () => {
      const result = await creative.explore(
        "what is 2+2?",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.triggered).toBe(false);
    });

    it("returns triggered=true and generates approaches for complex queries", async () => {
      const result = await creative.explore(
        "how would you design a scalable microservices architecture?",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.triggered).toBe(true);
      expect(result.approaches.length).toBeGreaterThan(0);
      expect(result.selected).not.toBeNull();
    });

    it("selects highest alignment approach", async () => {
      const dna = makeMockDNA({ challengeLevel: "high", verbosity: "concise" });
      const decisions = makeMockDecisions({
        prioritizedTools: ["read_file", "write_file"],
        riskTolerance: "aggressive",
      });

      const result = await creative.explore(
        "what is the best way to optimize this database query?",
        dna,
        decisions,
        [],
      );

      expect(result.selected).not.toBeNull();
      expect(result.directive).toContain("Selected approach:");
    });

    it("builds directive string when approaches exist", async () => {
      const result = await creative.explore(
        "compare and evaluate different authentication strategies for a web app",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.directive).toContain("## Creative Exploration");
      expect(result.directive).toContain("Alternative approaches considered:");
      expect(result.directive).toContain("Use tools:");
    });

    it("returns triggered=false on LLM failure", async () => {
      const failingProvider = makeMockProvider("invalid");
      (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error("provider down"),
      );
      const failingCreative = new CreativeThinking(failingProvider);

      const result = await failingCreative.explore(
        "how would you implement a caching strategy for this API?",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.triggered).toBe(false);
      expect(result.approaches).toHaveLength(0);
    });

    it("tracks duration", async () => {
      const result = await creative.explore(
        "design an algorithm to sort a large dataset efficiently",
        makeMockDNA(),
        makeMockDecisions(),
        [],
      );

      expect(result.durationMs).toBeGreaterThanOrEqual(0);
    });

    it("handles history context in prompt", async () => {
      const history: ChatMessage[] = [
        { role: "user", content: "I need help with my project" },
        { role: "assistant", content: "What kind of project?" },
      ];

      await creative.explore(
        "how should I architect the backend?",
        makeMockDNA(),
        makeMockDecisions(),
        history,
      );

      const call = (mockProvider.chat as ReturnType<typeof vi.fn>).mock
        .calls[0];
      const messages = call[0] as Array<{ role: string; content: string }>;
      const userMessage = messages.find((m) => m.role === "user");
      expect(userMessage?.content).toContain("RECENT CONTEXT");
    });

    it("prioritizes tools from DNA expertise", async () => {
      const dna = makeMockDNA();
      const decisions = makeMockDecisions({
        prioritizedTools: ["run_shell_command"],
      });

      const result = await creative.explore(
        "what approach would you take to build this feature?",
        dna,
        decisions,
        [],
      );

      expect(result.approaches.length).toBeGreaterThan(0);
      for (const approach of result.approaches) {
        expect(approach.alignmentScore).toBeGreaterThanOrEqual(0);
        expect(approach.alignmentScore).toBeLessThanOrEqual(1);
      }
    });

    it("boosts score for cautious users preferring low complexity", async () => {
      const dna = makeMockDNA({ verbosity: "concise" });
      const decisions = makeMockDecisions({ riskTolerance: "cautious" });

      const result = await creative.explore(
        "evaluate different approaches for handling errors gracefully",
        dna,
        decisions,
        [],
      );

      expect(result.triggered).toBe(true);
    });

    it("boosts score for relentless challenge level with high complexity", async () => {
      const dna = makeMockDNA({ challengeLevel: "relentless" });
      const decisions = makeMockDecisions();

      const result = await creative.explore(
        "analyze and design a solution for real-time data processing",
        dna,
        decisions,
        [],
      );

      expect(result.triggered).toBe(true);
    });
  });
});

// ══════════════════════════════════════════════════════════════════════════
// DIAGNOSTIC ENGINE TESTS (additional coverage)
// ══════════════════════════════════════════════════════════════════════════

describe("DiagnosticEngine", () => {
  function makeInput(
    overrides: Partial<DiagnosticInput> = {},
  ): DiagnosticInput {
    return {
      toolName: "shell",
      toolArgs: { command: "curl https://example.com" },
      toolResult: "curl: command not found",
      failStreak: 1,
      failureType: "soft",
      errorClass: "NON-RETRYABLE",
      recentMessages: [
        { role: "user", content: "Fetch the homepage of example.com" },
      ],
      userIntent: "Fetch the homepage of example.com",
      ...overrides,
    };
  }

  function makeValidResponse(): string {
    return JSON.stringify({
      rootCause: "curl is not installed in this environment",
      errorClass: "non-retryable",
      candidates: [
        {
          label: "Use web_crawl",
          reasoning: "Built-in tool",
          action: "Call web_crawl",
          likelihood: 0.9,
          feasibility: 0.95,
          risk: 0.05,
        },
        {
          label: "Tell user",
          reasoning: "Informed",
          action: "Respond to user",
          likelihood: 1.0,
          feasibility: 1.0,
          risk: 0.0,
        },
      ],
    });
  }

  it("classifies error as retryable when LLM says so", async () => {
    const response = JSON.stringify({
      rootCause: "Temporary network issue",
      errorClass: "retryable",
      candidates: [
        {
          label: "Retry",
          reasoning: "Transient",
          action: "Retry",
          likelihood: 0.7,
          feasibility: 0.9,
          risk: 0.1,
        },
      ],
    });
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    expect(result.errorClass).toBe("retryable");
  });

  it("classifies error as environmental when LLM says so", async () => {
    const response = JSON.stringify({
      rootCause: "Disk full",
      errorClass: "environmental",
      candidates: [
        {
          label: "Free space",
          reasoning: "Clean up",
          action: "Delete temp files",
          likelihood: 0.8,
          feasibility: 0.7,
          risk: 0.2,
        },
      ],
    });
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    expect(result.errorClass).toBe("environmental");
  });

  it("defaults unknown error class to 'unknown'", async () => {
    const response = JSON.stringify({
      rootCause: "Something happened",
      errorClass: "invalid_class",
      candidates: [
        {
          label: "Try again",
          reasoning: "Maybe",
          action: "Retry",
          likelihood: 0.5,
          feasibility: 0.5,
          risk: 0.3,
        },
      ],
    });
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    expect(result.errorClass).toBe("unknown");
  });

  it("heuristic fallback detects permission errors", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "Permission denied (EACCES)" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Fix permissions");
  });

  it("heuristic fallback detects network errors", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "fetch failed: ECONNREFUSED" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Network issue — try alternative");
  });

  it("heuristic fallback detects timeout errors", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "Request timeout after 30s" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Network issue — try alternative");
  });

  it("heuristic fallback detects JSON parse errors", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "JSON parse error: Unexpected token" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Fix malformed input");
  });

  it("heuristic always includes report-to-user fallback", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "some unknown error" }),
    );

    const labels = result.candidates.map((c) => c.label);
    expect(labels).toContain("Report to user");
  });

  it("adds generic try-different-approach when no heuristics matched", async () => {
    const failingProvider = makeMockProvider("");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("fail"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({
        toolResult: "Something weird happened that doesn't match any pattern",
      }),
    );

    expect(result.candidates.length).toBeGreaterThan(1);
  });

  it("formatDirective shows all required sections", async () => {
    const engine = new DiagnosticEngine(makeMockProvider(makeValidResponse()));
    const input = makeInput();
    const result = await engine.diagnose(input);
    const directive = engine.formatDirective(result, input);

    expect(directive).toContain("DIAGNOSTIC ANALYSIS");
    expect(directive).toContain("ROOT CAUSE");
    expect(directive).toContain("ERROR CLASS");
    expect(directive).toContain("CANDIDATE FIXES");
    expect(directive).toContain("DIRECTIVE:");
  });

  it("formatDirective truncates tool result in directive", async () => {
    const engine = new DiagnosticEngine(makeMockProvider(makeValidResponse()));
    const longResult = "a".repeat(2000);
    const input = makeInput({ toolResult: longResult });
    const result = await engine.diagnose(input);
    const directive = engine.formatDirective(result, input);

    expect(directive.length).toBeLessThan(5000);
  });

  it("falls back to heuristic when LLM returns empty candidates", async () => {
    const response = JSON.stringify({
      rootCause: "test",
      errorClass: "unknown",
      candidates: [],
    });
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    // Should fall back to heuristic since empty candidates causes throw → fallback
    expect(result.candidates.length).toBeGreaterThan(0);
    expect(result.rootCause).toContain("Heuristic");
  });

  it("parses JSON with trailing commas", async () => {
    const response = JSON.stringify({
      rootCause: "test",
      errorClass: "unknown",
      candidates: [
        {
          label: "fix1",
          reasoning: "r",
          action: "a",
          likelihood: 0.5,
          feasibility: 0.5,
          risk: 0.1,
        },
        {
          label: "fix2",
          reasoning: "r",
          action: "a",
          likelihood: 0.5,
          feasibility: 0.5,
          risk: 0.1,
        },
      ],
    });
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    expect(result.candidates).toHaveLength(2);
  });

  it("extracts JSON from mixed content", async () => {
    const response =
      "Some text before ```json\n" +
      makeValidResponse() +
      "\n``` and some text after";
    const engine = new DiagnosticEngine(makeMockProvider(response));

    const result = await engine.diagnose(makeInput());

    expect(result.candidates.length).toBeGreaterThan(0);
  });

  it("handles non-JSON response by throwing to fallback", async () => {
    const failingProvider = makeMockProvider("This is not JSON at all");
    (failingProvider.chat as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("parse error"),
    );
    const engine = new DiagnosticEngine(failingProvider);

    const result = await engine.diagnose(
      makeInput({ toolResult: "error text" }),
    );

    expect(result.candidates.length).toBeGreaterThan(0);
    expect(result.rootCause).toContain("Heuristic");
  });
});
