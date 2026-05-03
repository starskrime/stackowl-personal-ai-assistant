// __tests__/owls/evolution-parliament-dna.test.ts
import { describe, it, expect, vi } from "vitest";
import { updateParliamentDNA } from "../../src/owls/evolution.js";
import type { OwlInstance } from "../../src/owls/persona.js";

function makeOwl(name: string): OwlInstance {
  return {
    persona: { name, type: "assistant", emoji: "🦉", challengeLevel: "medium",
      specialties: [], traits: [], systemPrompt: "", sourcePath: "" },
    dna: { owl: name, generation: 1, created: "", lastEvolved: "",
      learnedPreferences: {}, evolvedTraits: {
        challengeLevel: "medium", verbosity: "balanced", humor: 0.3,
        formality: 0.5, proactivity: 0.5, riskTolerance: "moderate",
        teachingStyle: "adaptive", delegationPreference: "collaborative",
      },
      expertiseGrowth: {}, domainConfidence: {},
      interactionStats: { totalConversations: 0, adviceAcceptedRate: 0, challengesGiven: 0,
        challengesAccepted: 0, parliamentSessions: 0 },
      evolutionLog: [] },
  };
}

describe("updateParliamentDNA", () => {
  it("ADVANCES: increases synthesizer expertiseGrowth for topic category", async () => {
    const synthesizer = makeOwl("Synthesizer");
    const challenger = makeOwl("Challenger");
    const db = {} as any;
    await updateParliamentDNA(synthesizer, challenger, [synthesizer, challenger], "PROCEED", "architecture", db, "ADVANCES");
    expect(synthesizer.dna.expertiseGrowth["architecture"]).toBeGreaterThan(0.5);
  });

  it("ADVANCES: increases challenger expertiseGrowth for critical_thinking at half rate", async () => {
    const synthesizer = makeOwl("S");
    const challenger = makeOwl("C");
    const db = {} as any;
    await updateParliamentDNA(synthesizer, challenger, [synthesizer, challenger], "PROCEED", "typescript", db, "ADVANCES");
    const synthGrowth = synthesizer.dna.expertiseGrowth["typescript"] ?? 0;
    const challGrowth = challenger.dna.expertiseGrowth["critical_thinking"] ?? 0;
    expect(challGrowth).toBeCloseTo(synthGrowth / 2, 1);
  });

  it("BLOCKED: makes no DNA changes", async () => {
    const owl = makeOwl("Owl");
    const before = JSON.stringify(owl.dna);
    const db = {} as any;
    await updateParliamentDNA(owl, undefined, [owl], "HOLD", "design", db, "BLOCKED");
    expect(JSON.stringify(owl.dna)).toBe(before);
  });

  it("PARTIAL: makes no DNA changes (same as BLOCKED)", async () => {
    const owl = makeOwl("Owl");
    const before = JSON.stringify(owl.dna);
    const db = {} as any;
    await updateParliamentDNA(owl, undefined, [owl], "PARTIAL", "design", db, "PARTIAL");
    expect(JSON.stringify(owl.dna)).toBe(before);
  });

  it("is non-fatal — resolves even when expertiseGrowth mutation would throw", async () => {
    const owl = makeOwl("Owl");
    // Freeze expertiseGrowth to cause assignment throw
    Object.freeze(owl.dna.expertiseGrowth);
    const db = {} as any;
    // Should not propagate the TypeError
    await expect(
      updateParliamentDNA(owl, undefined, [owl], "PROCEED", "design", db, "ADVANCES")
    ).resolves.not.toThrow();
  });
});
