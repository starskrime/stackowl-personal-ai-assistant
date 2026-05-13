import { describe, it, expect } from "vitest";
import { buildBmadParticipant } from "../../src/tools/parliament.js";
import type { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

describe("buildBmadParticipant", () => {
  it("converts a SpecializedOwlSpec into a valid OwlInstance-shaped object", () => {
    const spec: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Business Analyst",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: ["business analysis"],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: ["business"] },
      skills: { allowed: [] },
      additionalPrompt: "Identity: Channels Michael Porter.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    const instance = buildBmadParticipant(spec);
    expect(instance.persona.name).toBe("Mary");
    expect(instance.persona.emoji).toBe("📊");
    expect(instance.persona.specialties).toContain("business analysis");
    expect(instance.dna.owl).toBe("Mary");
    expect(instance.specialistPrompt).toContain("Michael Porter");
  });
});
