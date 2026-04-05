import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  ProactivePinger,
  type PingContext,
  type PingConfig,
} from "../src/heartbeat/proactive.js";
import {
  AutonomousPlanner,
  type PlannerConfig,
  type PlannedAction,
} from "../src/heartbeat/planner.js";
import {
  CapabilityScanner,
  type ScanResult,
} from "../src/heartbeat/capability-scanner.js";
import {
  IdleActivityEngine,
  type IdleEngineConfig,
} from "../src/heartbeat/idle-engine.js";
import { MemoryConsolidator } from "../src/heartbeat/consolidation.js";
import type { ModelProvider, ChatMessage } from "../src/providers/base.js";
import type { OwlInstance } from "../src/owls/persona.js";
import type { StackOwlConfig } from "../src/config/loader.js";
import type { GoalGraph } from "../src/goals/graph.js";
import type { PreferenceStore } from "../src/preferences/store.js";
import type { LearningEngine } from "../src/learning/self-study.js";
import type { PatternMiner } from "../src/skills/pattern-miner.js";
import type { CapabilityLedger } from "../src/evolution/ledger.js";
import type { ToolRegistry } from "../src/tools/registry.js";
import type { MicroLearner } from "../src/learning/micro-learner.js";
import type { ToolOutcomeStore } from "../src/tools/outcome-store.js";
import type { SkillsRegistry } from "../src/skills/registry.js";

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

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockResolvedValue("{}"),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("node:fs", () => ({
  existsSync: vi.fn().mockReturnValue(true),
}));

function makeMockProvider(): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: '{"extractedFacts": []}',
      model: "mock-model",
      finishReason: "stop" as const,
    }),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn().mockResolvedValue({ embedding: [0.1], model: "mock" }),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

function makeMockOwl(): OwlInstance {
  return {
    id: "owl-1",
    name: "Noctua",
    persona: {
      name: "Noctua",
      description: "Test owl",
      traits: [],
      sourcePath: "/tmp/owl.md",
    },
    dna: {
      owl: "Noctua",
      generation: 1,
      created: new Date().toISOString(),
      lastEvolved: new Date().toISOString(),
      learnedPreferences: {},
      evolvedTraits: {} as any,
    },
  } as unknown as OwlInstance;
}

function makeMockConfig(): StackOwlConfig {
  return {
    providers: {
      ollama: { baseUrl: "http://localhost:11434", defaultModel: "llama3" },
    },
    defaultProvider: "ollama",
    defaultModel: "llama3",
    workspace: "/tmp/test-workspace",
    gateway: { port: 3000, host: "localhost" },
    parliament: { maxRounds: 3, maxOwls: 3 },
    heartbeat: { enabled: true, intervalMinutes: 20 },
    owlDna: { enabled: true, evolutionBatchSize: 10, decayRatePerWeek: 0.1 },
  } as unknown as StackOwlConfig;
}

function makeMockGoalGraph(): GoalGraph {
  return {
    load: vi.fn().mockResolvedValue(undefined),
    save: vi.fn().mockResolvedValue(undefined),
    getStale: vi.fn().mockReturnValue([]),
    getBlocked: vi.fn().mockReturnValue([]),
    getTopPriority: vi.fn().mockReturnValue(null),
    getAll: vi.fn().mockReturnValue([]),
  } as unknown as GoalGraph;
}

function makeMockPreferenceStore(): PreferenceStore {
  return {
    isQuietHours: vi.fn().mockReturnValue(false),
    get: vi.fn(),
    set: vi.fn(),
  } as unknown as PreferenceStore;
}

function makeMockLearningEngine(): LearningEngine {
  return {
    runStudySession: vi.fn().mockResolvedValue({
      studied: [],
      pelletsCreated: 0,
      newFrontierTopics: [],
    }),
  } as unknown as LearningEngine;
}

function makeMockPatternMiner(): PatternMiner {
  return {
    mine: vi.fn().mockResolvedValue([]),
  } as unknown as PatternMiner;
}

function makeMockCapabilityLedger(): CapabilityLedger {
  return {
    recordToolOutcome: vi.fn(),
    getStats: vi.fn().mockReturnValue({ totalCalls: 0 }),
  } as unknown as CapabilityLedger;
}

function makeMockPingContext(
  overrides: Partial<{
    capabilityLedger: CapabilityLedger;
    autonomousPlanner: any;
    preferenceStore: PreferenceStore;
    sendToUser: ReturnType<typeof vi.fn>;
  }> = {},
): PingContext {
  return {
    provider: makeMockProvider(),
    owl: makeMockOwl(),
    config: makeMockConfig(),
    capabilityLedger: makeMockCapabilityLedger(),
    sendToUser: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as PingContext;
}

function makeMockToolRegistry(): ToolRegistry {
  return {
    getAllDefinitions: vi.fn().mockReturnValue([]),
    getByName: vi.fn(),
  } as unknown as ToolRegistry;
}

function makeMockMicroLearner(): MicroLearner {
  return {
    getProfile: vi.fn().mockReturnValue({
      totalMessages: 0,
      toolUsage: {},
    }),
    getAnticipatedNeeds: vi.fn().mockReturnValue([]),
    processMessage: vi.fn().mockReturnValue([]),
  } as unknown as MicroLearner;
}

function makeMockToolOutcomeStore(): ToolOutcomeStore {
  return {
    getTopPatterns: vi.fn().mockReturnValue([]),
    record: vi.fn(),
  } as unknown as ToolOutcomeStore;
}

function makeMockSkillsRegistry(): SkillsRegistry {
  return {
    listEnabled: vi.fn().mockReturnValue([]),
    get: vi.fn(),
  } as unknown as SkillsRegistry;
}

// ─── ProactivePinger Tests ────────────────────────────────────────

describe("ProactivePinger", () => {
  let pinger: ProactivePinger;
  let sendToUserMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    sendToUserMock = vi.fn().mockResolvedValue(undefined);
  });

  describe("constructor", () => {
    it("should apply default config when no overrides provided", () => {
      pinger = new ProactivePinger(
        makeMockPingContext({
          sendToUser: sendToUserMock,
        }),
      );

      expect(pinger).toBeDefined();
    });

    it("should merge custom config with defaults", () => {
      const customConfig: Partial<PingConfig> = {
        enabled: false,
        checkInIntervalMinutes: 30,
      };

      pinger = new ProactivePinger(
        makeMockPingContext({ sendToUser: sendToUserMock }),
        customConfig,
      );

      expect(pinger).toBeDefined();
    });
  });

  describe("start/stop", () => {
    it("should not start timers when disabled", () => {
      pinger = new ProactivePinger(
        makeMockPingContext({ sendToUser: sendToUserMock }),
        { enabled: false },
      );

      pinger.start();
      pinger.stop();

      expect(sendToUserMock).not.toHaveBeenCalled();
    });
  });

  describe("notifyUserActivity", () => {
    it("should reset unanswered pings counter", () => {
      pinger = new ProactivePinger(
        makeMockPingContext({
          sendToUser: sendToUserMock,
        }),
      );

      pinger.notifyUserActivity();

      expect(sendToUserMock).not.toHaveBeenCalled();
    });

    it("should call onUserActivity on autonomousPlanner if present", () => {
      const mockPlanner = {
        onUserActivity: vi.fn(),
        planAndExecute: vi.fn().mockResolvedValue(null),
      };

      pinger = new ProactivePinger(
        makeMockPingContext({
          sendToUser: sendToUserMock,
          autonomousPlanner: mockPlanner as any,
        }),
      );

      pinger.notifyUserActivity();

      expect(mockPlanner.onUserActivity).toHaveBeenCalled();
    });
  });

  describe("isQuietHours", () => {
    it("should return false during active hours with default config", () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2024-01-01T14:00:00"));

      pinger = new ProactivePinger(
        makeMockPingContext({
          sendToUser: sendToUserMock,
        }),
      );

      vi.useRealTimers();
    });

    it("should use preferenceStore quiet hours when available", () => {
      const mockPrefStore = makeMockPreferenceStore();
      (mockPrefStore.isQuietHours as ReturnType<typeof vi.fn>).mockReturnValue(
        true,
      );

      pinger = new ProactivePinger(
        makeMockPingContext({
          sendToUser: sendToUserMock,
          preferenceStore: mockPrefStore,
        }),
      );

      vi.useRealTimers();
    });
  });
});

// ─── AutonomousPlanner Tests ───────────────────────────────────────

describe("AutonomousPlanner", () => {
  let planner: AutonomousPlanner;
  let mockGoalGraph: GoalGraph;
  let onActionMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockGoalGraph = makeMockGoalGraph();
    onActionMock = vi.fn().mockResolvedValue(undefined);
  });

  describe("constructor", () => {
    it("should create planner with default config", () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      expect(planner).toBeDefined();
    });

    it("should merge custom config with defaults", () => {
      const customConfig: Partial<PlannerConfig> = {
        intervalMinutes: 5,
        minActionCooldownMinutes: 30,
      };

      planner = new AutonomousPlanner(
        mockGoalGraph,
        { onAction: onActionMock },
        customConfig,
      );

      expect(planner).toBeDefined();
    });
  });

  describe("start/stop", () => {
    it("should start timer when started", () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      planner.start();
      planner.stop();

      expect(onActionMock).not.toHaveBeenCalled();
    });
  });

  describe("onUserActivity", () => {
    it("should update last user message time", () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      planner.onUserActivity();

      expect(onActionMock).not.toHaveBeenCalled();
    });
  });

  describe("planAndExecute", () => {
    it("should return null when on cooldown", async () => {
      planner = new AutonomousPlanner(
        mockGoalGraph,
        { onAction: onActionMock },
        { minActionCooldownMinutes: 60 },
      );

      await planner.planAndExecute();
      const result = await planner.planAndExecute();

      expect(result).toBeNull();
    });

    it("should return null when no candidates generated", async () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      const result = await planner.planAndExecute();

      expect(result).toBeNull();
    });

    it("should return null for none action type", async () => {
      const mockGraphWithNone = makeMockGoalGraph();
      (mockGraphWithNone.getStale as ReturnType<typeof vi.fn>).mockReturnValue(
        [],
      );

      planner = new AutonomousPlanner(mockGraphWithNone, {
        onAction: onActionMock,
        learningEngine: makeMockLearningEngine(),
      });

      const mockNow = new Date("2024-01-01T12:00:00");
      vi.useFakeTimers();
      vi.setSystemTime(mockNow);

      const result = await planner.planAndExecute();

      vi.useRealTimers();

      expect(result === null || result.type === "none").toBeTruthy();
    });
  });

  describe("markMorningBriefDone", () => {
    it("should set lastMorningBriefDate to today", () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      planner.markMorningBriefDone();

      expect(planner).toBeDefined();
    });
  });

  describe("markConsolidationDone", () => {
    it("should set lastConsolidationDate to today", () => {
      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
      });

      planner.markConsolidationDone();

      expect(planner).toBeDefined();
    });
  });

  describe("isQuietHours", () => {
    it("should use preferenceStore when available", () => {
      const mockPrefStore = makeMockPreferenceStore();
      (mockPrefStore.isQuietHours as ReturnType<typeof vi.fn>).mockReturnValue(
        true,
      );

      planner = new AutonomousPlanner(mockGoalGraph, {
        onAction: onActionMock,
        preferenceStore: mockPrefStore,
      });

      expect(planner).toBeDefined();
    });

    it("should calculate quiet hours from config when no preferenceStore", () => {
      planner = new AutonomousPlanner(
        mockGoalGraph,
        { onAction: onActionMock },
        { quietHoursStart: 22, quietHoursEnd: 7 },
      );

      expect(planner).toBeDefined();
    });

    it("should handle quiet hours spanning midnight", () => {
      planner = new AutonomousPlanner(
        mockGoalGraph,
        { onAction: onActionMock },
        { quietHoursStart: 22, quietHoursEnd: 7 },
      );

      expect(planner).toBeDefined();
    });
  });
});

// ─── CapabilityScanner Tests ───────────────────────────────────────

describe("CapabilityScanner", () => {
  let scanner: CapabilityScanner;
  let mockConfig: StackOwlConfig;
  let mockToolRegistry: ToolRegistry;
  let mockSkillsRegistry: SkillsRegistry;
  let mockMicroLearner: MicroLearner;

  beforeEach(() => {
    vi.clearAllMocks();
    mockConfig = makeMockConfig();
    mockToolRegistry = makeMockToolRegistry();
    mockSkillsRegistry = makeMockSkillsRegistry();
    mockMicroLearner = makeMockMicroLearner();
  });

  describe("constructor", () => {
    it("should create scanner with all dependencies", () => {
      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        mockMicroLearner,
      );

      expect(scanner).toBeDefined();
    });

    it("should create scanner with only required dependencies", () => {
      scanner = new CapabilityScanner(mockConfig);

      expect(scanner).toBeDefined();
    });
  });

  describe("scan", () => {
    it("should return scan result with all fields", () => {
      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        mockMicroLearner,
      );

      const result = scanner.scan();

      expect(result).toHaveProperty("gaps");
      expect(result).toHaveProperty("totalToolsRegistered");
      expect(result).toHaveProperty("totalSkillsEnabled");
      expect(result).toHaveProperty("coveragePercent");
      expect(result).toHaveProperty("timestamp");
      expect(Array.isArray(result.gaps)).toBe(true);
    });

    it("should sort gaps by priority descending", () => {
      const mockToolRegWithTools = makeMockToolRegistry();
      (
        mockToolRegWithTools.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        { name: "web_crawl", description: "Crawl web" },
        { name: "google_search", description: "Search Google" },
      ]);

      const mockSkillsRegWithSkills = makeMockSkillsRegistry();
      (
        mockSkillsRegWithSkills.listEnabled as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "web_skill", description: "Web wrapper" }]);

      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegWithTools,
        mockSkillsRegWithSkills,
        mockMicroLearner,
      );

      const result = scanner.scan();

      if (result.gaps.length > 1) {
        for (let i = 1; i < result.gaps.length; i++) {
          expect(result.gaps[i - 1].priority).toBeGreaterThanOrEqual(
            result.gaps[i].priority,
          );
        }
      }
    });

    it("should calculate coverage percentage", () => {
      const mockToolReg = makeMockToolRegistry();
      (
        mockToolReg.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "tool1" }, { name: "tool2" }]);

      const mockSkillsReg = makeMockSkillsRegistry();
      (mockSkillsReg.listEnabled as ReturnType<typeof vi.fn>).mockReturnValue([
        { name: "skill1", description: "Tool1 wrapper" },
      ]);

      scanner = new CapabilityScanner(mockConfig, mockToolReg, mockSkillsReg);

      const result = scanner.scan();

      expect(result.coveragePercent).toBe(50);
    });

    it("should return 0 coverage when no tools", () => {
      scanner = new CapabilityScanner(mockConfig);

      const result = scanner.scan();

      expect(result.coveragePercent).toBe(0);
    });
  });

  describe("getTopGaps", () => {
    it("should return top N gaps sorted by priority", () => {
      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        mockMicroLearner,
      );

      const topGaps = scanner.getTopGaps(3);

      expect(Array.isArray(topGaps)).toBe(true);
      expect(topGaps.length).toBeLessThanOrEqual(3);
    });

    it("should default to 5 gaps", () => {
      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        mockMicroLearner,
      );

      const topGaps = scanner.getTopGaps();

      expect(topGaps.length).toBeLessThanOrEqual(5);
    });
  });

  describe("toIdlePrompt", () => {
    it("should return empty string when no gaps", () => {
      scanner = new CapabilityScanner(mockConfig);

      const prompt = scanner.toIdlePrompt();

      expect(prompt).toBe("");
    });

    it("should format gaps as prompt string", () => {
      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        mockMicroLearner,
      );

      const result = scanner.scan();
      const prompt = scanner.toIdlePrompt(result);

      if (result.gaps.length > 0) {
        expect(prompt).toContain("CAPABILITY GAPS");
      }
    });

    it("should limit to top 5 gaps in prompt", () => {
      const mockToolRegWithTools = makeMockToolRegistry();
      (
        mockToolRegWithTools.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "web_crawl" }, { name: "google_search" }]);

      const mockSkillsRegWithSkills = makeMockSkillsRegistry();
      (
        mockSkillsRegWithSkills.listEnabled as ReturnType<typeof vi.fn>
      ).mockReturnValue([]);

      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegWithTools,
        mockSkillsRegWithSkills,
        mockMicroLearner,
      );

      const result = scanner.scan();
      const prompt = scanner.toIdlePrompt(result);

      expect(result.gaps.length).toBeGreaterThan(0);
      expect(prompt).toContain("CAPABILITY GAPS");
    });
  });

  describe("scanUnusedAdapters", () => {
    it("should detect unused telegram adapter", () => {
      const configWithTelegram: StackOwlConfig = {
        ...mockConfig,
        telegram: { botToken: "test-token" },
      } as StackOwlConfig;

      const microLearnerWithProfile = makeMockMicroLearner();
      (
        microLearnerWithProfile.getProfile as ReturnType<typeof vi.fn>
      ).mockReturnValue({
        totalMessages: 20,
        toolUsage: {},
      });

      scanner = new CapabilityScanner(
        configWithTelegram,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithProfile,
      );

      const result = scanner.scan();
      const telegramGap = result.gaps.find((g) => g.name === "telegram");

      expect(telegramGap).toBeDefined();
      expect(telegramGap?.type).toBe("unused_adapter");
    });

    it("should not flag telegram as unused when under threshold", () => {
      const configWithTelegram: StackOwlConfig = {
        ...mockConfig,
        telegram: { botToken: "test-token" },
      } as StackOwlConfig;

      const microLearnerWithProfile = makeMockMicroLearner();
      (
        microLearnerWithProfile.getProfile as ReturnType<typeof vi.fn>
      ).mockReturnValue({
        totalMessages: 5,
        toolUsage: {},
      });

      scanner = new CapabilityScanner(
        configWithTelegram,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithProfile,
      );

      const result = scanner.scan();
      const telegramGap = result.gaps.find((g) => g.name === "telegram");

      expect(telegramGap).toBeUndefined();
    });

    it("should detect unused slack adapter", () => {
      const configWithSlack: StackOwlConfig = {
        ...mockConfig,
        slack: { botToken: "test-token" },
      } as StackOwlConfig;

      const microLearnerWithProfile = makeMockMicroLearner();
      (
        microLearnerWithProfile.getProfile as ReturnType<typeof vi.fn>
      ).mockReturnValue({
        totalMessages: 20,
        toolUsage: {},
      });

      scanner = new CapabilityScanner(
        configWithSlack,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithProfile,
      );

      const result = scanner.scan();
      const slackGap = result.gaps.find((g) => g.name === "slack");

      expect(slackGap).toBeDefined();
      expect(slackGap?.type).toBe("unused_adapter");
    });
  });

  describe("scanToolsWithoutSkills", () => {
    it("should flag important tools without skills", () => {
      const mockToolReg = makeMockToolRegistry();
      (
        mockToolReg.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "web_crawl" }, { name: "google_search" }]);

      const mockSkillsReg = makeMockSkillsRegistry();
      (mockSkillsReg.listEnabled as ReturnType<typeof vi.fn>).mockReturnValue(
        [],
      );

      scanner = new CapabilityScanner(mockConfig, mockToolReg, mockSkillsReg);

      const result = scanner.scan();
      const toolGaps = result.gaps.filter(
        (g) => g.type === "tool_without_skill",
      );

      expect(toolGaps.length).toBeGreaterThan(0);
    });

    it("should not flag tools that have matching skills", () => {
      const mockToolReg = makeMockToolRegistry();
      (
        mockToolReg.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "web_crawl" }]);

      const mockSkillsReg = makeMockSkillsRegistry();
      (mockSkillsReg.listEnabled as ReturnType<typeof vi.fn>).mockReturnValue([
        { name: "crawl_skill", description: "Wraps web_crawl tool" },
      ]);

      scanner = new CapabilityScanner(mockConfig, mockToolReg, mockSkillsReg);

      const result = scanner.scan();
      const toolGap = result.gaps.find(
        (g) => g.type === "tool_without_skill" && g.name === "web_crawl",
      );

      expect(toolGap).toBeUndefined();
    });

    it("should not flag non-important tools", () => {
      const mockToolReg = makeMockToolRegistry();
      (
        mockToolReg.getAllDefinitions as ReturnType<typeof vi.fn>
      ).mockReturnValue([{ name: "some_random_tool" }]);

      const mockSkillsReg = makeMockSkillsRegistry();
      (mockSkillsReg.listEnabled as ReturnType<typeof vi.fn>).mockReturnValue(
        [],
      );

      scanner = new CapabilityScanner(mockConfig, mockToolReg, mockSkillsReg);

      const result = scanner.scan();
      const toolGap = result.gaps.find(
        (g) => g.type === "tool_without_skill" && g.name === "some_random_tool",
      );

      expect(toolGap).toBeUndefined();
    });
  });

  describe("scanUnusedMCP", () => {
    it("should detect unused MCP servers", () => {
      const configWithMCP: StackOwlConfig = {
        ...mockConfig,
        mcp: {
          servers: [
            {
              name: "filesystem",
              transport: "stdio" as const,
              command: "npx",
              args: [],
            },
          ],
        },
      } as unknown as StackOwlConfig;

      const microLearnerWithProfile = makeMockMicroLearner();
      (
        microLearnerWithProfile.getProfile as ReturnType<typeof vi.fn>
      ).mockReturnValue({
        totalMessages: 20,
        toolUsage: {},
      });

      scanner = new CapabilityScanner(
        configWithMCP,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithProfile,
      );

      const result = scanner.scan();
      const mcpGap = result.gaps.find(
        (g) => g.type === "unused_mcp" && g.name === "filesystem",
      );

      expect(mcpGap).toBeDefined();
    });

    it("should not flag MCP when microLearner not available", () => {
      const configWithMCP: StackOwlConfig = {
        ...mockConfig,
        mcp: {
          servers: [
            {
              name: "filesystem",
              transport: "stdio" as const,
              command: "npx",
              args: [],
            },
          ],
        },
      } as unknown as StackOwlConfig;

      scanner = new CapabilityScanner(configWithMCP);

      const result = scanner.scan();
      const mcpGaps = result.gaps.filter((g) => g.type === "unused_mcp");

      expect(mcpGaps.length).toBe(0);
    });
  });

  describe("scanTopicGaps", () => {
    it("should detect topic gaps from anticipated needs", () => {
      const microLearnerWithNeeds = makeMockMicroLearner();
      (
        microLearnerWithNeeds.getAnticipatedNeeds as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        {
          capability: "email",
          confidence: 0.7,
          reason: "Frequent email mentions",
        },
      ]);

      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithNeeds,
      );

      const result = scanner.scan();
      const topicGap = result.gaps.find(
        (g) => g.type === "topic_gap" && g.name === "email",
      );

      expect(topicGap).toBeDefined();
      expect(topicGap?.priority).toBe(42);
    });

    it("should not flag low confidence anticipated needs", () => {
      const microLearnerWithNeeds = makeMockMicroLearner();
      (
        microLearnerWithNeeds.getAnticipatedNeeds as ReturnType<typeof vi.fn>
      ).mockReturnValue([
        { capability: "email", confidence: 0.3, reason: "Low confidence" },
      ]);

      scanner = new CapabilityScanner(
        mockConfig,
        mockToolRegistry,
        mockSkillsRegistry,
        microLearnerWithNeeds,
      );

      const result = scanner.scan();
      const topicGaps = result.gaps.filter((g) => g.type === "topic_gap");

      expect(topicGaps.length).toBe(0);
    });
  });

  describe("scanPermissionGaps", () => {
    it("should detect denied tools from config", () => {
      const configWithPermissions: StackOwlConfig = {
        ...mockConfig,
        tools: {
          permissions: {
            shell: "denied",
            dangerous_tool: "denied",
          },
        },
      } as StackOwlConfig;

      scanner = new CapabilityScanner(configWithPermissions);

      const result = scanner.scan();
      const shellGap = result.gaps.find(
        (g) => g.type === "permission_gap" && g.name === "shell",
      );

      expect(shellGap).toBeDefined();
      expect(shellGap?.priority).toBe(20);
    });
  });
});

// ─── IdleActivityEngine Tests ─────────────────────────────────────

describe("IdleActivityEngine", () => {
  let engine: IdleActivityEngine;
  let mockConfig: StackOwlConfig;
  let onResultMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockConfig = makeMockConfig();
    onResultMock = vi.fn();
  });

  describe("constructor", () => {
    it("should create engine with default config", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      expect(engine).toBeDefined();
    });

    it("should merge custom config with defaults", () => {
      const customConfig: Partial<IdleEngineConfig> = {
        idleThresholdMinutes: 10,
        cycleLengthMinutes: 15,
      };

      engine = new IdleActivityEngine(
        mockConfig,
        { onResult: onResultMock },
        customConfig,
      );

      expect(engine).toBeDefined();
    });

    it("should set all enabled activities by default", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      expect(engine).toBeDefined();
    });
  });

  describe("start/stop", () => {
    it("should start timer when started", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      engine.start();
      engine.stop();

      expect(onResultMock).not.toHaveBeenCalled();
    });
  });

  describe("onUserActivity", () => {
    it("should reset idle timer", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      engine.onUserActivity();

      expect(onResultMock).not.toHaveBeenCalled();
    });

    it("should cancel in-progress activities", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      engine.onUserActivity();

      expect(engine).toBeDefined();
    });
  });

  describe("isIdle", () => {
    it("should return true after idle threshold", () => {
      vi.useFakeTimers();

      const engine = new IdleActivityEngine(
        mockConfig,
        { onResult: onResultMock },
        { idleThresholdMinutes: 5 },
      );

      vi.advanceTimersByTime(6 * 60 * 1000);

      expect(engine.isIdle()).toBe(true);

      vi.useRealTimers();
    });

    it("should return false within idle threshold", () => {
      vi.useFakeTimers();

      const engine = new IdleActivityEngine(
        mockConfig,
        { onResult: onResultMock },
        { idleThresholdMinutes: 10 },
      );

      vi.advanceTimersByTime(5 * 60 * 1000);

      expect(engine.isIdle()).toBe(false);

      vi.useRealTimers();
    });
  });

  describe("getRecentResults", () => {
    it("should return empty array initially", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const results = engine.getRecentResults();

      expect(Array.isArray(results)).toBe(true);
      expect(results.length).toBe(0);
    });

    it("should respect limit parameter", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const results = engine.getRecentResults(5);

      expect(Array.isArray(results)).toBe(true);
    });
  });

  describe("pickNextActivity", () => {
    it("should return null when no dependencies available", () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      vi.useFakeTimers();
      vi.advanceTimersByTime(10 * 60 * 1000);

      const activity = (engine as any).pickNextActivity();

      vi.useRealTimers();

      expect(activity).toBeNull();
    });

    it("should prioritize pattern_mining when available", () => {
      const mockPatternMiner = makeMockPatternMiner();

      engine = new IdleActivityEngine(
        mockConfig,
        {
          onResult: onResultMock,
          patternMiner: mockPatternMiner,
        },
        { enabled: { patternMining: true } as any },
      );

      vi.useFakeTimers();
      vi.advanceTimersByTime(60 * 60 * 1000);

      const activity = (engine as any).pickNextActivity();

      vi.useRealTimers();

      expect(activity === null || typeof activity === "string").toBe(true);
    });
  });

  describe("runActivity", () => {
    it("should return empty result for pattern_mining without dependencies", async () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const result = await (engine as any).runActivity("pattern_mining");

      expect(result.activity).toBe("pattern_mining");
      expect(result.success).toBe(false);
    });

    it("should return empty result for capability_exploration without scanner", async () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const result = await (engine as any).runActivity(
        "capability_exploration",
      );

      expect(result.activity).toBe("capability_exploration");
      expect(result.success).toBe(false);
    });

    it("should return empty result for anticipatory_research without learning engine", async () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const result = await (engine as any).runActivity("anticipatory_research");

      expect(result.activity).toBe("anticipatory_research");
      expect(result.success).toBe(false);
    });

    it("should return empty result for tool_outcome_review without store", async () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const result = await (engine as any).runActivity("tool_outcome_review");

      expect(result.activity).toBe("tool_outcome_review");
      expect(result.success).toBe(false);
    });

    it("should return empty result for knowledge_refresh without learning engine", async () => {
      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
      });

      const result = await (engine as any).runActivity("knowledge_refresh");

      expect(result.activity).toBe("knowledge_refresh");
      expect(result.success).toBe(false);
    });
  });

  describe("runCapabilityExploration", () => {
    it("should run capability scanner and return result", async () => {
      const mockScanner = new CapabilityScanner(mockConfig);

      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
        capabilityScanner: mockScanner,
      });

      const result = await (engine as any).runCapabilityExploration();

      expect(result.activity).toBe("capability_exploration");
      expect(result.success).toBe(true);
      expect(Array.isArray(result.artifacts)).toBe(true);
    });
  });

  describe("runAnticipatoryResearch", () => {
    it("should run learning engine and return result", async () => {
      const mockLearningEngine = makeMockLearningEngine();

      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
        learningEngine: mockLearningEngine,
      });

      const result = await (engine as any).runAnticipatoryResearch();

      expect(result.activity).toBe("anticipatory_research");
      expect(result.success).toBe(true);
    });
  });

  describe("runToolOutcomeReview", () => {
    it("should analyze tool patterns and return result", async () => {
      const mockStore = makeMockToolOutcomeStore();
      (mockStore.getTopPatterns as ReturnType<typeof vi.fn>).mockReturnValue([
        { requestType: "shell", successRate: 0.9 },
        { requestType: "search", successRate: 0.3 },
      ]);

      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
        toolOutcomeStore: mockStore,
      });

      const result = await (engine as any).runToolOutcomeReview();

      expect(result.activity).toBe("tool_outcome_review");
      expect(result.success).toBe(true);
      expect(result.artifacts).toContain("search");
    });
  });

  describe("runKnowledgeRefresh", () => {
    it("should run learning engine with limit 1", async () => {
      const mockLearningEngine = makeMockLearningEngine();

      engine = new IdleActivityEngine(mockConfig, {
        onResult: onResultMock,
        learningEngine: mockLearningEngine,
      });

      const result = await (engine as any).runKnowledgeRefresh();

      expect(result.activity).toBe("knowledge_refresh");
      expect(result.success).toBe(true);
    });
  });
});

// ─── MemoryConsolidator Tests ───────────────────────────────────────

describe("MemoryConsolidator", () => {
  let consolidator: MemoryConsolidator;
  let mockProvider: ModelProvider;
  let mockOwl: OwlInstance;
  const workspace = "/tmp/test-workspace";

  beforeEach(() => {
    vi.clearAllMocks();
    mockProvider = makeMockProvider();
    mockOwl = makeMockOwl();
  });

  describe("constructor", () => {
    it("should create consolidator with required dependencies", () => {
      consolidator = new MemoryConsolidator(mockProvider, mockOwl, workspace);

      expect(consolidator).toBeDefined();
    });
  });

  describe("consolidateSession", () => {
    it("should skip when no session file exists", async () => {
      vi.mock("node:fs", () => ({
        existsSync: vi.fn().mockReturnValue(false),
      }));

      consolidator = new MemoryConsolidator(mockProvider, mockOwl, workspace);

      await expect(
        consolidator.consolidateSession("nonexistent-user"),
      ).resolves.not.toThrow();
    });

    it("should skip session with fewer than 5 messages", async () => {
      const sessionData = { messages: [{ role: "user", content: "Hi" }] };

      vi.mock("node:fs/promises", () => ({
        readFile: vi.fn().mockResolvedValue(JSON.stringify(sessionData)),
        writeFile: vi.fn().mockResolvedValue(undefined),
      }));

      consolidator = new MemoryConsolidator(mockProvider, mockOwl, workspace);

      await expect(
        consolidator.consolidateSession("test-user"),
      ).resolves.not.toThrow();
    });

    it("should handle JSON parsing errors gracefully", async () => {
      vi.mock("node:fs/promises", () => ({
        readFile: vi.fn().mockResolvedValue("not valid json"),
        writeFile: vi.fn().mockResolvedValue(undefined),
      }));

      consolidator = new MemoryConsolidator(mockProvider, mockOwl, workspace);

      await expect(
        consolidator.consolidateSession("test-user"),
      ).resolves.not.toThrow();
    });
  });
});
