// __tests__/parliament/multi-round-debate-sparse.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MultiRoundDebateManager } from "../../src/parliament/multi-round-debate.js";
import type { ParliamentSession } from "../../src/parliament/protocol.js";
import type { OwlInstance } from "../../src/owls/persona.js";

vi.mock("../../src/logger.js", () => ({
  log: { parliament: { info: vi.fn(), debug: vi.fn(), warn: vi.fn(), behavioral: vi.fn() },
         engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } },
}));

// callTimestamps is reset in beforeEach to avoid cross-test contamination
let callTimestamps: number[] = [];
beforeEach(() => { callTimestamps = []; });

vi.mock("../../src/engine/runtime.js", () => ({
  OwlEngine: vi.fn().mockImplementation(() => ({
    run: vi.fn().mockImplementation(async () => {
      callTimestamps.push(Date.now());
      await new Promise(r => setTimeout(r, 10));
      return { content: "[FOR] I support this position fully.", owlName: "test", owlEmoji: "🦉", toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
    }),
  })),
}));

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: `You are ${name}.`, sourcePath: "" },
    dna: { owl: name, generation: 0, created: "", lastEvolved: "", learnedPreferences: {},
      evolvedTraits: { challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate", teachingStyle: "adaptive",
        delegationPreference: "collaborative" },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [] },
  };
}

function makeSession(owls: OwlInstance[]): ParliamentSession {
  return { id: "test-session", config: { topic: "test topic", participants: owls,
    contextMessages: [] }, phase: "setup", positions: [], challenges: [],
    synthesis: "", verdict: undefined, startedAt: Date.now() };
}

describe("MultiRoundDebateManager — sparse debate", () => {
  it("AC-2: Round 1 fires all owl calls in parallel (start time delta < 100ms)", async () => {
    const owls = [makeOwl("Owl1"), makeOwl("Owl2"), makeOwl("Owl3")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    const perspectives = new Map();
    await (manager as any).runRound1(session, perspectives);
    // All 3 owl calls should overlap: max - min < 100ms
    expect(callTimestamps.length).toBe(3);
    const delta = Math.max(...callTimestamps) - Math.min(...callTimestamps);
    expect(delta).toBeLessThan(100);
  });

  it("AC-3: Round 2 prompt contains only the two diverging owls, not the others", async () => {
    const owls = [makeOwl("OwlA"), makeOwl("OwlB"), makeOwl("OwlC"), makeOwl("OwlD")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    const perspectives = new Map();

    // Manually set positions and diversePair (simulating post-Round1 state)
    session.positions = [
      { owlName: "OwlA", owlEmoji: "🦉", position: "FOR", argument: "Position A" },
      { owlName: "OwlB", owlEmoji: "🦉", position: "AGAINST", argument: "Position B" },
      { owlName: "OwlC", owlEmoji: "🦉", position: "NEUTRAL", argument: "Position C" },
      { owlName: "OwlD", owlEmoji: "🦉", position: "FOR", argument: "Position D" },
    ];
    // DiversityFilter chose OwlB and OwlD as most diverging
    session.diversePair = [session.positions[1], session.positions[3]];

    const capturedPrompts: string[] = [];
    const engineMock = {
      run: vi.fn().mockImplementation(async (prompt: string) => {
        capturedPrompts.push(prompt);
        return { content: "I challenge OwlB on their reasoning.", owlName: "OwlA", owlEmoji: "🦉",
          toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
      }),
    };
    (manager as any).engine = engineMock;

    await (manager as any).runRound2(session, perspectives);

    // The challenger prompt should mention OwlB and OwlD but NOT OwlA or OwlC
    const challengerPrompt = capturedPrompts[0] ?? "";
    expect(challengerPrompt).toContain("Position B");
    expect(challengerPrompt).toContain("Position D");
    expect(challengerPrompt).not.toContain("Position A");
    expect(challengerPrompt).not.toContain("Position C");
  });

  it("Round 3 prompt includes diversityReasoning when present", async () => {
    const owls = [makeOwl("Mentor"), makeOwl("OwlB")];
    const manager = new MultiRoundDebateManager({} as any, {} as any);
    const session = makeSession(owls);
    session.positions = [
      { owlName: "Mentor", owlEmoji: "🦉", position: "FOR", argument: "arg1" },
      { owlName: "OwlB", owlEmoji: "🦉", position: "AGAINST", argument: "arg2" },
    ];
    session.challenges = [{ owlName: "OwlB", targetOwl: "Mentor", challengeContent: "challenge text" }];
    session.diversePair = [session.positions[0], session.positions[1]];
    session.diversityReasoning = "They disagree on fundamentals";

    const capturedPrompts: string[] = [];
    const engineMock = {
      run: vi.fn().mockImplementation(async (prompt: string) => {
        capturedPrompts.push(prompt);
        return { content: "PROCEED — synthesis", owlName: "Mentor", owlEmoji: "🦉",
          toolsUsed: [], challenged: false, modelUsed: "m", newMessages: [] };
      }),
    };
    (manager as any).engine = engineMock;
    const perspectives = new Map();
    await (manager as any).runRound3(session, perspectives);
    expect(capturedPrompts[0]).toContain("They disagree on fundamentals");
  });
});
