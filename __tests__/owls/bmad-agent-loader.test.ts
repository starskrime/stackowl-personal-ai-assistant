import { describe, it, expect } from "vitest";
import type { SpecializedOwlSpec } from "../../src/owls/specialized-types.js";

describe("SpecializedOwlSpec source fields", () => {
  it("accepts source and bmadSkillName optional fields", () => {
    const spec: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Business Analyst",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: ["business analysis", "requirements"],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: ["business", "requirements", "stakeholder"] },
      skills: { allowed: [] },
      additionalPrompt: "You are Mary.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    expect(spec.source).toBe("bmad");
    expect(spec.bmadSkillName).toBe("bmad-agent-analyst");
  });

  it("source and bmadSkillName are optional (undefined by default)", () => {
    const spec: SpecializedOwlSpec = {
      name: "Custom",
      type: "specialist",
      role: "Custom role",
      emoji: "🦉",
      personality: { challengeLevel: "low", verbosity: "concise", tone: "casual" },
      expertise: [],
      model: { provider: "ollama", model: "llama3" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [] },
      skills: { allowed: [] },
      additionalPrompt: "",
    };
    expect(spec.source).toBeUndefined();
    expect(spec.bmadSkillName).toBeUndefined();
  });
});

import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";

describe("SpecializedOwlRegistry.registerSpec", () => {
  it("registers a spec that is then retrievable by name", () => {
    const registry = new SpecializedOwlRegistry();
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
      additionalPrompt: "You are Mary.",
      source: "bmad",
      bmadSkillName: "bmad-agent-analyst",
    };
    registry.registerSpec(spec);
    const retrieved = registry.get("Mary");
    expect(retrieved).toBeDefined();
    expect(retrieved!.name).toBe("Mary");
    expect(retrieved!.source).toBe("bmad");
  });

  it("registerSpec overwrites a spec with the same name", () => {
    const registry = new SpecializedOwlRegistry();
    const spec1: SpecializedOwlSpec = {
      name: "Mary",
      type: "specialist",
      role: "Role v1",
      emoji: "📊",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "professional" },
      expertise: [],
      model: { provider: "anthropic", model: "claude-sonnet-4-6" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [] },
      skills: { allowed: [] },
      additionalPrompt: "",
    };
    const spec2 = { ...spec1, role: "Role v2" };
    registry.registerSpec(spec1);
    registry.registerSpec(spec2);
    expect(registry.get("Mary")!.role).toBe("Role v2");
  });
});
