import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ModelProvider, ChatMessage } from "../../src/providers/base.js";
import type { StackOwlConfig } from "../../src/config/loader.js";
import type { OwlInstance, OwlDNA } from "../../src/owls/persona.js";
import { MultiRoundDebateManager } from "../../src/parliament/multi-round-debate.js";
import type { ParliamentSession, ParliamentCallbacks } from "../../src/parliament/protocol.js";
import type { PerspectiveOverlay } from "../../src/parliament/perspectives.js";

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

// Mock OwlEngine
vi.mock("../../src/engine/runtime.js", () => ({
  OwlEngine: vi.fn().mockImplementation(() => ({
    run: vi.fn().mockResolvedValue({
      content: "Mock response with [FOR] position and some argument text",
      owlName: "TestOwl",
      owlEmoji: "🦉",
      toolsUsed: [],
      challenged: false,
      modelUsed: "mock-model",
      newMessages: [],
    }),
  })),
}));

function makeMockOwlInstance(name: string, type: string = "assistant"): OwlInstance {
  return {
    persona: {
      name,
      type,
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
      content: "SYNTHESIS: This is the final synthesis content.",
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

function makeMockSession(
  participants: OwlInstance[],
  topic: string = "Test topic",
): ParliamentSession {
  const perspectives = new Map<string, PerspectiveOverlay>();
  for (const p of participants) {
    perspectives.set(p.persona.name, {
      role: "mentor",
      label: "The Mentor",
      emoji: "🧙",
      systemPromptPrefix: "You are a mentor.",
    });
  }

  return {
    id: "test-session-123",
    config: {
      topic,
      participants,
      contextMessages: [],
      perspectives: perspectives as unknown as ParliamentCallbacks["perspectives"],
      callbacks: {
        onRoundStart: vi.fn().mockResolvedValue(undefined),
        onPositionReady: vi.fn().mockResolvedValue(undefined),
        onChallengeReady: vi.fn().mockResolvedValue(undefined),
        onSynthesisReady: vi.fn().mockResolvedValue(undefined),
      },
    },
    phase: "setup",
    positions: [],
    challenges: [],
    startedAt: Date.now(),
  };
}

describe("MultiRoundDebateManager", () => {
  let manager: MultiRoundDebateManager;
  let mockProvider: ModelProvider;
  let mockConfig: StackOwlConfig;

  beforeEach(() => {
    mockProvider = makeMockProvider();
    mockConfig = makeMockConfig();
    manager = new MultiRoundDebateManager(mockProvider, mockConfig);
  });

  describe("runDebate()", () => {
    it("runs all 3 rounds when called", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const owl2 = makeMockOwlInstance("Archimedes");
      const session = makeMockSession([owl1, owl2], "Should we use A or B?");

      await manager.runDebate(session);

      // All phases should have been visited
      expect(session.phase).toBe("round3_synthesis");
      expect(session.positions).toHaveLength(2);
    });

    it("extracts position tags from responses", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const owl2 = makeMockOwlInstance("Archimedes");
      const session = makeMockSession([owl1, owl2], "Which is better?");

      await manager.runDebate(session);

      // Positions should have valid position values
      for (const pos of session.positions) {
        expect(["FOR", "AGAINST", "CONDITIONAL", "NEUTRAL", "ANALYSIS"]).toContain(pos.position);
      }
    });
  });

  describe("runRound1()", () => {
    it("collects positions from all participants", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const owl2 = makeMockOwlInstance("Archimedes");
      const session = makeMockSession([owl1, owl2], "Should we refactor?");

      await manager.runRound1(session, new Map());

      expect(session.positions).toHaveLength(2);
      expect(session.phase).toBe("round1_position");
    });

    it("calls onPositionReady callback for each position", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const session = makeMockSession([owl1], "Simple question");

      const onPositionReady = vi.fn().mockResolvedValue(undefined);
      session.config.callbacks!.onPositionReady = onPositionReady;

      await manager.runRound1(session, new Map());

      expect(onPositionReady).toHaveBeenCalled();
    });
  });

  describe("runRound2()", () => {
    it("creates a challenge after round 1", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const owl2 = makeMockOwlInstance("Archimedes");
      const session = makeMockSession([owl1, owl2], "Decision time");

      // First run round 1 to populate positions
      await manager.runRound1(session, new Map());
      await manager.runRound2(session, new Map());

      expect(session.challenges).toHaveLength(1);
      expect(session.phase).toBe("round2_challenge");
    });
  });

  describe("runRound3()", () => {
    it("produces synthesis and verdict", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const session = makeMockSession([owl1], "Final decision");

      await manager.runRound3(session, new Map());

      expect(session.synthesis).toBeDefined();
      expect(session.verdict).toBeDefined();
      expect(session.phase).toBe("round3_synthesis");
    });

    it("extracts verdict from synthesis content", async () => {
      const owl1 = makeMockOwlInstance("Noctua");
      const session = makeMockSession([owl1], "What should we do?");

      await manager.runRound3(session, new Map());

      // Verdict should be one of the valid values or CONSENSUS_REACHED
      const validVerdicts = ["PROCEED", "HOLD", "ABORT", "REVISE", "APPROVE", "REJECT", "CONSENSUS_REACHED"];
      expect(validVerdicts).toContain(session.verdict);
    });
  });
});