import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ModelProvider } from "../../src/providers/base.js";
import type { StackOwlConfig } from "../../src/config/loader.js";
import { ParliamentAutoTrigger } from "../../src/parliament/auto-trigger.js";

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

function makeMockProvider(shouldConvene: boolean = false): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: shouldConvene ? "DEBATE" : "SINGLE",
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

function makeMockConfig(overrides: Partial<StackOwlConfig["parliament"]> = {}): StackOwlConfig {
  return {
    parliament: {
      maxRounds: 3,
      maxOwls: 3,
      enabled: true,
      ...overrides,
    },
  } as unknown as StackOwlConfig;
}

describe("ParliamentAutoTrigger", () => {
  let autoTrigger: ParliamentAutoTrigger;
  let mockProvider: ModelProvider;
  let mockConfig: StackOwlConfig;

  beforeEach(() => {
    mockProvider = makeMockProvider(false);
    mockConfig = makeMockConfig();
    autoTrigger = new ParliamentAutoTrigger(mockConfig);
  });

  describe("check()", () => {
    it("returns bypassed=true for short messages (< 25 chars)", async () => {
      const result = await autoTrigger.check("hi", mockProvider);

      expect(result.shouldTrigger).toBe(false);
      expect(result.bypassed).toBe(true);
      expect(result.reason).toContain("Trivial");
    });

    it("returns bypassed=true for greetings", async () => {
      const greetings = ["hello", "hi", "hey", "good morning", "thanks"];

      for (const greeting of greetings) {
        const result = await autoTrigger.check(greeting, mockProvider);
        expect(result.shouldTrigger).toBe(false);
        expect(result.bypassed).toBe(true);
      }
    });

    it("returns bypassed=true for trivial OK/okay messages", async () => {
      const result = await autoTrigger.check("okay", mockProvider);
      expect(result.shouldTrigger).toBe(false);
      expect(result.bypassed).toBe(true);
    });

    it("returns shouldTrigger=false when parliament is disabled in config", async () => {
      const config = makeMockConfig({ enabled: false });
      autoTrigger = new ParliamentAutoTrigger(config);

      const result = await autoTrigger.check("Should I use React or Vue for my project?", mockProvider);

      expect(result.shouldTrigger).toBe(false);
      expect(result.bypassed).toBe(false);
      expect(result.reason).toContain("disabled");
    });

    it("returns shouldTrigger=false when autoTriggerThreshold > 1.0", async () => {
      const config = makeMockConfig({ autoTriggerThreshold: 1.5 });
      autoTrigger = new ParliamentAutoTrigger(config);

      const result = await autoTrigger.check("Should I use React or Vue?", mockProvider);

      expect(result.shouldTrigger).toBe(false);
      expect(result.reason).toContain("autoTriggerThreshold");
    });

    it("returns shouldTrigger=true when LLM detects DEBATE", async () => {
      const debateProvider = makeMockProvider(true);
      autoTrigger = new ParliamentAutoTrigger(mockConfig);

      const result = await autoTrigger.check(
        "Should I switch careers or stay where I am?",
        debateProvider,
      );

      expect(result.shouldTrigger).toBe(true);
      expect(result.bypassed).toBe(false);
      expect(result.reason).toContain("debate-worthy");
    });

    it("returns shouldTrigger=false when LLM detects SINGLE", async () => {
      const singleProvider = makeMockProvider(false);
      autoTrigger = new ParliamentAutoTrigger(mockConfig);

      const result = await autoTrigger.check(
        "What is the weather today?",
        singleProvider,
      );

      expect(result.shouldTrigger).toBe(false);
      expect(result.bypassed).toBe(false);
      expect(result.reason).toContain("non-debatable");
    });

    it("handles provider errors gracefully", async () => {
      const errorProvider = {
        name: "error",
        chat: vi.fn().mockRejectedValue(new Error("Provider error")),
        chatWithTools: vi.fn(),
        chatStream: vi.fn(),
        embed: vi.fn().mockResolvedValue({ embedding: [] }),
        listModels: vi.fn().mockResolvedValue([]),
        healthCheck: vi.fn().mockResolvedValue(true),
      } as unknown as ModelProvider;

      const result = await autoTrigger.check(
        "Which framework should I use for my new project - A or B?",
        errorProvider,
      );

      expect(result.shouldTrigger).toBe(false);
      // When provider errors, detector catches and returns false (non-debatable)
      // So reason reflects that, not a detection error
      expect(result.reason).toContain("non-debatable");
    });

    it("passes through message to LLM for complex questions", async () => {
      const debateProvider = makeMockProvider(true);
      autoTrigger = new ParliamentAutoTrigger(mockConfig);

      const complexQuestion = "Should we use microservices or a monolith for our new project? This is a major architectural decision.";

      await autoTrigger.check(complexQuestion, debateProvider);

      expect(debateProvider.chat).toHaveBeenCalled();
      const callArgs = vi.mocked(debateProvider.chat).mock.calls[0];
      const messageContent = callArgs[0][0].content;
      expect(messageContent).toContain(complexQuestion);
    });
  });
});

describe("isTrivialMessage (via check)", () => {
  it("short questions under 25 chars are trivial", async () => {
    const config = makeMockConfig();
    const trigger = new ParliamentAutoTrigger(config);

    const result = await trigger.check("hi", {} as ModelProvider);
    expect(result.bypassed).toBe(true);
  });

  it("longer questions are not trivial", async () => {
    const config = makeMockConfig();
    const trigger = new ParliamentAutoTrigger(config);

    const result = await trigger.check("Should I learn TypeScript or Python?", {} as ModelProvider);
    expect(result.bypassed).toBe(false);
  });
});