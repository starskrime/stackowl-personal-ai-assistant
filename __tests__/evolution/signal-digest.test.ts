import { describe, it, expect, vi } from "vitest";
import { OwlEvolutionEngine } from "../../src/owls/evolution.js";
import type { ModelProvider } from "../../src/providers/base.js";

// Minimal valid JSON that evolve() can parse without throwing
const VALID_MUTATION_JSON = JSON.stringify({
  newPreferences: { prefers_brevity: 0.8 },
  traitAdjustments: { verbosity: "concise" },
  expertiseGrowth: {},
  statsUpdate: { adviceAccepted: true, challengesGiven: 0 },
  promptRules: [],
  evolutionReasoning: "Test mutation.",
});

function makeMockProvider(responseContent: string): ModelProvider {
  return {
    chat: vi.fn().mockResolvedValue({
      content: responseContent,
      model: "test",
      usage: undefined,
    }),
    embed: vi.fn().mockResolvedValue({ embedding: [] }),
  } as unknown as ModelProvider;
}

function makeMinimalOwlDNA() {
  return {
    owl: "TestOwl",
    generation: 1,
    lastEvolved: null,
    evolvedTraits: { verbosity: "balanced", challengeLevel: "medium", delegationPreference: "collaborative" },
    learnedPreferences: {},
    expertiseGrowth: {},
    interactionStats: {
      totalConversations: 5,
      adviceAcceptedRate: 0.5,
      challengesGiven: 0,
      challengesAccepted: 0,
    },
    evolutionLog: [],
    promptSections: [],
  };
}

function makeOwlRegistry(owlName: string) {
  const dna = makeMinimalOwlDNA();
  return {
    get: vi.fn().mockReturnValue({
      persona: { name: owlName, emoji: "🦉" },
      dna,
    }),
    saveDNA: vi.fn().mockResolvedValue(undefined),
    listOwls: vi.fn().mockReturnValue([]),
  } as any;
}

function makeSessionStore(owlName: string) {
  // Provide sessions that are long enough (≥ 4 messages) so evolve() proceeds
  const messages = [
    { role: "user", content: "Hello" },
    { role: "assistant", content: "Hi there!" },
    { role: "user", content: "How are you?" },
    { role: "assistant", content: "Doing great!" },
  ];
  return {
    listSessions: vi.fn().mockResolvedValue([
      {
        metadata: { owlName },
        messages,
      },
    ]),
  } as any;
}

function makeConfig() {
  return {
    owlDna: { decayRatePerWeek: 0 },
  } as any;
}

describe("evolve() signal digest — learningsSection (D5)", () => {
  it("injects RECENT LEARNINGS header and all learning strings into the LLM prompt when learnings exist", async () => {
    const owlName = "TestOwl";
    const provider = makeMockProvider(VALID_MUTATION_JSON);
    const config = makeConfig();
    const sessionStore = makeSessionStore(owlName);
    const owlRegistry = makeOwlRegistry(owlName);

    const learnings = [
      "Always use absolute paths when calling shell tools.",
      "User prefers code-first answers without preamble.",
      "yt-dlp is preferred over youtube-dl for media downloads.",
    ];

    const mockDb = {
      owlLearnings: {
        getForOwlSorted: vi.fn().mockReturnValue(learnings),
      },
      owlPerf: {
        getSummary: vi.fn().mockReturnValue({ totalInteractions: 0 }),
      },
      trajectories: {
        getRecent: vi.fn().mockReturnValue([]),
        getLowReward: vi.fn().mockReturnValue([]),
        getRecentWithClarification: vi.fn().mockReturnValue([]),
      },
      rawDb: {
        prepare: vi.fn().mockReturnValue({
          get: vi.fn().mockReturnValue(undefined),
        }),
      },
    } as any;

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      mockDb,
    );

    await engine.evolve(owlName);

    expect(provider.chat).toHaveBeenCalledOnce();
    const callArgs = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0];
    // chat(messages, model, options) — messages is first arg
    const messages = callArgs[0] as Array<{ role: string; content: string }>;
    const userMsg = messages.find((m) => m.role === "user");
    expect(userMsg).toBeDefined();

    const prompt = userMsg!.content;
    expect(prompt).toContain("RECENT LEARNINGS");
    expect(prompt).toContain("Always use absolute paths when calling shell tools.");
    expect(prompt).toContain("User prefers code-first answers without preamble.");
    expect(prompt).toContain("yt-dlp is preferred over youtube-dl for media downloads.");
  });

  it("resolves without throwing when getForOwlSorted returns empty array", async () => {
    const owlName = "TestOwl";
    const provider = makeMockProvider(VALID_MUTATION_JSON);
    const config = makeConfig();
    const sessionStore = makeSessionStore(owlName);
    const owlRegistry = makeOwlRegistry(owlName);

    const mockDb = {
      owlLearnings: {
        getForOwlSorted: vi.fn().mockReturnValue([]),
      },
      owlPerf: {
        getSummary: vi.fn().mockReturnValue({ totalInteractions: 0 }),
      },
      trajectories: {
        getRecent: vi.fn().mockReturnValue([]),
        getLowReward: vi.fn().mockReturnValue([]),
        getRecentWithClarification: vi.fn().mockReturnValue([]),
      },
      rawDb: {
        prepare: vi.fn().mockReturnValue({
          get: vi.fn().mockReturnValue(undefined),
        }),
      },
    } as any;

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      mockDb,
    );

    await expect(engine.evolve(owlName)).resolves.not.toThrow();
    // When learnings are empty, the section must be absent from the prompt
    const chatCalls = (provider.chat as ReturnType<typeof vi.fn>).mock.calls;
    if (chatCalls.length > 0) {
      const allText = chatCalls[0][0].map((m: any) => m.content ?? "").join("\n");
      expect(allText).not.toContain("RECENT LEARNINGS");
    }
  });
});
