import { describe, it, expect, beforeEach } from "vitest";
import { SpecializedOwlRegistry } from "../../../src/owls/specialized-registry.js";
import { dispatchOwlCommand } from "../../../src/gateway/commands/owl-command.js";
import type { SpecializedOwlSpec } from "../../../src/owls/specialized-types.js";

function makeSpec(overrides: Partial<SpecializedOwlSpec> = {}): SpecializedOwlSpec {
  return {
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
    additionalPrompt: "Identity: Strategic analyst.",
    source: "bmad",
    bmadSkillName: "bmad-agent-analyst",
    ...overrides,
  };
}

describe("dispatchOwlCommand", () => {
  let registry: SpecializedOwlRegistry;

  beforeEach(() => {
    registry = new SpecializedOwlRegistry();
    registry.registerSpec(makeSpec({ name: "Mary", source: "bmad" }));
    registry.registerSpec(makeSpec({ name: "CustomOwl", source: "custom", emoji: "🦉" }));
  });

  const ctx = () => ({
    registry,
    userId: "test-user",
    workspacePath: "/tmp/test-workspace",
  });

  it("list returns all owls with emoji and source", async () => {
    const result = await dispatchOwlCommand("list", [], ctx());
    expect(result).toContain("Mary");
    expect(result).toContain("📊");
    expect(result).toContain("bmad");
    expect(result).toContain("CustomOwl");
  });

  it("show returns detailed spec for a known owl", async () => {
    const result = await dispatchOwlCommand("show", ["mary"], ctx());
    expect(result).toContain("Mary");
    expect(result).toContain("Business Analyst");
    expect(result).toContain("business analysis");
  });

  it("show returns error for unknown owl", async () => {
    const result = await dispatchOwlCommand("show", ["nobody"], ctx());
    expect(result).toMatch(/not found/i);
  });

  it("delete rejects bmad-sourced owls", async () => {
    const result = await dispatchOwlCommand("delete", ["mary"], ctx());
    expect(result).toMatch(/cannot delete.*bmad/i);
  });

  it("unknown verb returns help text", async () => {
    const result = await dispatchOwlCommand("frobnicate", [], ctx());
    expect(result).toMatch(/unknown.*command|usage|verb/i);
  });
});
