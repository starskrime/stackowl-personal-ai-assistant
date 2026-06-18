import { describe, it, expect, vi } from "vitest";
import { OwlEvolutionEngine } from "../../src/owls/evolution.js";
import type { ModelProvider } from "../../src/providers/base.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function makeMinimalOwlDNA(overrides: {
  learnedPreferences?: Record<string, number>;
  expertiseGrowth?: Record<string, number>;
  lastEvolved?: string | null;
} = {}) {
  return {
    owl: "test-owl",
    generation: 1,
    lastEvolved: overrides.lastEvolved ?? null,
    evolvedTraits: {
      verbosity: "balanced",
      challengeLevel: "medium",
      delegationPreference: "collaborative",
    },
    learnedPreferences: overrides.learnedPreferences ?? { verbosity_preference: 0.4 },
    expertiseGrowth: overrides.expertiseGrowth ?? { typescript: 0.5 },
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

function makeOwlRegistry(
  owlName: string,
  dnaOverrides: Parameters<typeof makeMinimalOwlDNA>[0] = {},
) {
  const dna = makeMinimalOwlDNA(dnaOverrides);
  const owlInstance = {
    persona: { name: owlName, emoji: "🦉" },
    dna,
  };
  return {
    get: vi.fn().mockReturnValue(owlInstance),
    saveDNA: vi.fn().mockResolvedValue(undefined),
    listOwls: vi.fn().mockReturnValue([]),
    _owlInstance: owlInstance, // expose for assertions
  } as any;
}

function makeSessionStore(owlName: string) {
  const messages = [
    { role: "user", content: "Hello" },
    { role: "assistant", content: "Hi there!" },
    { role: "user", content: "How are you?" },
    { role: "assistant", content: "Doing great!" },
  ];
  return {
    listSessions: vi.fn().mockResolvedValue([
      { metadata: { owlName }, messages },
    ]),
  } as any;
}

function makeMinimalDb() {
  return {
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
}

// ---------------------------------------------------------------------------
// EMA blending tests — learnedPreferences
// ---------------------------------------------------------------------------

describe("EMA blending — learnedPreferences (D6)", () => {
  it("EMA test 1: proposed=1.0, current=0.4 → stored ≈ 0.82 (β=0.7)", async () => {
    const owlName = "test-owl";
    const mutationJson = JSON.stringify({
      newPreferences: { verbosity_preference: 1.0 },
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: null,
      promptRules: [],
      evolutionReasoning: "EMA test",
    });

    const provider = makeMockProvider(mutationJson);
    // config has decayRatePerWeek=0 so decay never fires
    const config = { owlDna: { decayRatePerWeek: 0 } } as any;
    const sessionStore = makeSessionStore(owlName);
    const owlRegistry = makeOwlRegistry(owlName, {
      learnedPreferences: { verbosity_preference: 0.4 },
    });
    const db = makeMinimalDb();

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      db,
    );

    await engine.evolve(owlName);

    const stored = owlRegistry._owlInstance.dna.learnedPreferences["verbosity_preference"];
    // EMA: 0.7 * 1.0 + 0.3 * 0.4 = 0.82
    // After clamping to [0.05, 0.95]: still 0.82
    expect(stored).toBeCloseTo(0.82, 5);
  });

  it("EMA test 2: expertiseGrowth proposed=min(1.0,0.5+0.2)=0.7, current=0.5 → stored ≈ 0.64 (β=0.7)", async () => {
    const owlName = "test-owl";
    const mutationJson = JSON.stringify({
      newPreferences: {},
      traitAdjustments: {},
      expertiseGrowth: { typescript: 0.2 },
      statsUpdate: null,
      promptRules: [],
      evolutionReasoning: "EMA expertise test",
    });

    const provider = makeMockProvider(mutationJson);
    const config = { owlDna: { decayRatePerWeek: 0 } } as any;
    const sessionStore = makeSessionStore(owlName);
    const owlRegistry = makeOwlRegistry(owlName, {
      expertiseGrowth: { typescript: 0.5 },
    });
    const db = makeMinimalDb();

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      db,
    );

    await engine.evolve(owlName);

    const stored = owlRegistry._owlInstance.dna.expertiseGrowth["typescript"];
    // proposed = min(1.0, 0.5 + 0.2) = 0.7
    // EMA: 0.7 * 0.7 + 0.3 * 0.5 = 0.49 + 0.15 = 0.64
    expect(stored).toBeCloseTo(0.64, 5);
  });

  it("EMA test 3: proposed=1.0 from base=0.4 → stored is > 0.4 and < 1.0 (no extreme jump)", async () => {
    const owlName = "test-owl";
    const mutationJson = JSON.stringify({
      newPreferences: { verbosity_preference: 1.0 },
      traitAdjustments: {},
      expertiseGrowth: {},
      statsUpdate: null,
      promptRules: [],
      evolutionReasoning: "Extreme jump prevention test",
    });

    const provider = makeMockProvider(mutationJson);
    const config = { owlDna: { decayRatePerWeek: 0 } } as any;
    const sessionStore = makeSessionStore(owlName);
    const owlRegistry = makeOwlRegistry(owlName, {
      learnedPreferences: { verbosity_preference: 0.4 },
    });
    const db = makeMinimalDb();

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      db,
    );

    await engine.evolve(owlName);

    const stored = owlRegistry._owlInstance.dna.learnedPreferences["verbosity_preference"];
    // Must not jump all the way to 1.0 (capped at 0.95 by clamping, but also won't be < 0.4)
    expect(stored).toBeGreaterThan(0.4);
    expect(stored).toBeLessThan(1.0);
  });
});

// ---------------------------------------------------------------------------
// Decay rate default test
// ---------------------------------------------------------------------------

describe("applyDecayIfNeeded — decay rate default (D6)", () => {
  it("decay test: 14 days elapsed, test_pref=1.0, no config owlDna field → decays significantly (≥0.01/week rate)", async () => {
    const owlName = "test-owl";

    // 14 days ago
    const fourteenDaysAgo = new Date(
      Date.now() - 14 * 24 * 60 * 60 * 1000,
    ).toISOString();

    // config with NO owlDna field at all — forces the fallback default
    const config = {} as any;

    const owlRegistry = makeOwlRegistry(owlName, {
      learnedPreferences: { test_pref: 1.0 },
      lastEvolved: fourteenDaysAgo,
    });

    const provider = makeMockProvider("{}");
    const sessionStore = makeSessionStore(owlName);

    const engine = new OwlEvolutionEngine(
      provider,
      config,
      sessionStore,
      owlRegistry,
      undefined,
      undefined,
      undefined,
    );

    await engine.applyDecayIfNeeded(owlName);

    const stored = owlRegistry._owlInstance.dna.learnedPreferences["test_pref"];

    // With default rate=0.1 and 2 weeks elapsed:
    // factor = 0.1 * 2 = 0.2
    // decayed = 1.0 + (0.5 - 1.0) * 0.2 = 1.0 - 0.1 = 0.9
    // So stored ≈ 0.9. Must be < 0.95 to prove it's NOT the old 0.01 rate.
    // With old rate=0.01: factor=0.02, decayed = 1.0 - 0.01 = 0.99 (>> 0.95)
    expect(stored).toBeLessThan(0.95);

    // Also must have moved from 1.0 toward 0.5 (not gone past 0.5)
    expect(stored).toBeGreaterThan(0.5);
    expect(stored).toBeCloseTo(0.9, 5);
  });
});
