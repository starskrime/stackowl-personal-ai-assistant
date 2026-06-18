import { describe, it, expect, vi } from "vitest";
import { ParliamentLite } from "../../src/parliament/lite.js";
import type { OwlInstance } from "../../src/owls/persona.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
         parliament: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), behavioral: vi.fn(), error: vi.fn() } },
}));

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
    dna: {
      owl: name, generation: 0, created: "", lastEvolved: "",
      learnedPreferences: {}, evolvedTraits: {
        challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate",
        teachingStyle: "adaptive", delegationPreference: "collaborative",
      },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [],
    },
  };
}

describe("ParliamentLite router wiring", () => {
  it("uses router.resolve('classification').model when router is provided", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "VOTE: [PROCEED] — good" }),
    } as any;
    const mockRouter = {
      resolve: vi.fn().mockReturnValue({ provider: "test", model: "router-resolved-model", tier: "low" }),
    } as any;
    const config = { defaultProvider: "mock", providers: {} } as any;
    const lite = new ParliamentLite(mockProvider, config, undefined, mockRouter);
    await lite.deliberate({
      topic: "test topic",
      question: "should we proceed?",
      context: "test context",
      owls: [makeOwl("OwlA"), makeOwl("OwlB")],
    });
    // All provider.chat calls should use router-resolved-model, not haiku hardcoded
    for (const call of mockProvider.chat.mock.calls) {
      expect(call[1]).toBe("router-resolved-model");
    }
  });
});
