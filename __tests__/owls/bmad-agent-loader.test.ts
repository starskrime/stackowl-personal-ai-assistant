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

import { BmadAgentLoader } from "../../src/owls/bmad-agent-loader.js";

describe("BmadAgentLoader", () => {
  it("loadAll returns at least 6 BMAD agents (bmad-method is installed)", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    expect(specs.length).toBeGreaterThanOrEqual(6);
  });

  it("all returned specs have required SpecializedOwlSpec fields", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    for (const spec of specs) {
      expect(typeof spec.name).toBe("string");
      expect(spec.name.length).toBeGreaterThan(0);
      expect(typeof spec.role).toBe("string");
      expect(typeof spec.emoji).toBe("string");
      expect(spec.source).toBe("bmad");
      expect(typeof spec.bmadSkillName).toBe("string");
      expect(spec.type).toBe("specialist");
      expect(Array.isArray(spec.expertise)).toBe(true);
      expect(Array.isArray(spec.routingRules.keywords)).toBe(true);
    }
  });

  it("Mary (Business Analyst) is loaded from bmad-agent-analyst", async () => {
    const loader = new BmadAgentLoader();
    const specs = await loader.loadAll();
    const mary = specs.find((s) => s.name === "Mary");
    expect(mary).toBeDefined();
    expect(mary!.emoji).toBe("📊");
    expect(mary!.bmadSkillName).toBe("bmad-agent-analyst");
    expect(mary!.source).toBe("bmad");
  });

  it("loadAll returns empty array when bmad-method package name is wrong", async () => {
    const loader = new BmadAgentLoader({ packageName: "bmad-method-nonexistent-xyz" });
    const specs = await loader.loadAll();
    expect(specs).toEqual([]);
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
