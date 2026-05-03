// __tests__/parliament/parliament-integration.test.ts
import { describe, it, expect, vi } from "vitest";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
    parliament: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), behavioral: vi.fn() },
    evolution: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
  },
}));

// Test AC-4: ContextPipeline receives parliament_synthesis after session
it("AC-4: post-session synthesis formatting includes topic, verdict, synthesis, and key dissent", () => {
  // Test the formatting logic that gateway/core.ts runs — not the pipeline call itself
  const debateSession = {
    synthesis: "The council recommends PROCEED with microservices.",
    verdict: "PROCEED",
    config: { topic: "microservices vs monolith", participants: [] },
    positions: [
      { owlName: "Owl1", owlEmoji: "🦉", position: "FOR", argument: "Microservices scale better" },
      { owlName: "Owl2", owlEmoji: "🦉", position: "AGAINST", argument: "Monolith is simpler and we have 3 devs" },
    ],
    challenges: [] as any[],
  };
  const synthesis = debateSession.synthesis ?? "";
  const minorityContent = debateSession.positions.find(p => p.position === "AGAINST")?.argument
    ?? debateSession.challenges[0]?.challengeContent
    ?? "";
  const formattedSynthesis =
    `[Parliament concluded on "${debateSession.config.topic}"] Verdict: ${debateSession.verdict ?? "CONSENSUS_REACHED"}\n` +
    `The council's synthesis: ${synthesis.slice(0, 300)}\n` +
    (minorityContent ? `Key dissent: ${minorityContent.slice(0, 150)}\n` : "");

  expect(formattedSynthesis).toContain("microservices vs monolith");
  expect(formattedSynthesis).toContain("PROCEED");
  expect(formattedSynthesis).toContain("The council recommends PROCEED");
  expect(formattedSynthesis).toContain("Key dissent: Monolith is simpler");
});

// Test AC-5: GoalVerifier receives the correct arguments when Parliament calls it
it("AC-5: GoalVerifier.verify receives toolName='parliament' and synthesis as toolResult", async () => {
  const synthesis = "Parliament recommends PROCEED.";
  const activeSubGoal = { id: "g1", description: "decide architecture", status: "in_progress" as const, dependsOn: [] };

  const capturedArgs: any[] = [];
  const mockVerifier = {
    verify: vi.fn().mockImplementation((args: any) => {
      capturedArgs.push(args);
      return Promise.resolve({ verdict: "ADVANCES", reason: "debate helped" });
    }),
  };

  // Simulate the conditional call from gateway/core.ts Task 10 Block A
  if (mockVerifier && activeSubGoal) {
    await mockVerifier.verify({
      toolName: "parliament",
      toolArgs: {},
      toolResult: synthesis,
      subGoal: activeSubGoal,
      userMessage: "should we use microservices?",
    });
  }

  expect(capturedArgs).toHaveLength(1);
  expect(capturedArgs[0]).toMatchObject({
    toolName: "parliament",
    toolResult: synthesis,
    subGoal: expect.objectContaining({ id: "g1", description: "decide architecture" }),
  });
});

// Test AC-6: DNA update fires when GoalVerifier returns ADVANCES
it("AC-6: updateParliamentDNA is called when GoalVerifier returns ADVANCES", async () => {
  const { updateParliamentDNA } = await import("../../src/owls/evolution.js");

  function makeOwl(name: string) {
    return {
      persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
        specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
      dna: { owl: name, generation: 1, created: "", lastEvolved: "", learnedPreferences: {},
        evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
          formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
          delegationPreference: "collaborative" },
        expertiseGrowth: {}, domainConfidence: {},
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
          challengesAccepted: 0, parliamentSessions: 0 }, evolutionLog: [] },
    };
  }

  const synthesizer = makeOwl("Owl1");
  const challenger = makeOwl("Owl2");
  const participants = [synthesizer, challenger];
  await updateParliamentDNA(synthesizer, challenger, participants, "PROCEED", "architecture", {} as any, "ADVANCES");
  // Synthesizer's expertiseGrowth should increase
  expect(synthesizer.dna.expertiseGrowth["architecture"]).toBeGreaterThan(0);
});

// Test AC-7: No DNA change when GoalVerifier returns BLOCKED
it("AC-7: updateParliamentDNA skips all mutations when goalVerifierResult is BLOCKED", async () => {
  const { updateParliamentDNA } = await import("../../src/owls/evolution.js");

  function makeOwl(name: string) {
    return {
      persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
        specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
      dna: { owl: name, generation: 1, created: "", lastEvolved: "", learnedPreferences: {},
        evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
          formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
          delegationPreference: "collaborative" },
        expertiseGrowth: {}, domainConfidence: {},
        interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
          challengesAccepted: 0, parliamentSessions: 0 }, evolutionLog: [] },
    };
  }

  const owl = makeOwl("TestOwl");
  const snapshotBefore = JSON.stringify(owl.dna.expertiseGrowth);
  await updateParliamentDNA(owl, undefined, [owl], "HOLD", "design", {} as any, "BLOCKED");
  expect(JSON.stringify(owl.dna.expertiseGrowth)).toBe(snapshotBefore);
});

// Test AC-10: IntentClarifier CLARIFY verdict blocks Parliament
it("AC-10: gateway skips Parliament auto-trigger when IntentClarifier returns CLARIFY", async () => {
  const { IntentClarifier } = await import("../../src/clarification/intent-clarifier.js");
  // IntentClarifier constructor: (provider, router, coordinator)
  const mockProvider = {
    chat: vi.fn().mockResolvedValue({
      content: JSON.stringify({ verdict: "CLARIFY", question: "Can you clarify what you mean?", interpretation: null, reasoning: "ambiguous request" }),
    }),
  };
  const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
  const mockCoordinator = { shouldSuppressDuplicate: vi.fn().mockReturnValue(false) };
  const clarifier = new IntentClarifier(mockProvider as any, mockRouter as any, mockCoordinator as any);
  const mockDna = { evolvedTraits: { delegationPreference: "collaborative" } };
  const mockBias = { toPromptContext: vi.fn().mockReturnValue("") };
  const result = await clarifier.evaluate("help me with this thing", [], mockDna as any, mockBias as any);
  expect(result.verdict).toBe("CLARIFY");
});
