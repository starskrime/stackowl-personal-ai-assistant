import { describe, it, expect } from "vitest";
import { SpecializedOwlRegistry } from "../../src/owls/specialized-registry.js";
import { resolveOwl } from "../../src/cli/commands.js";

function makeRegistry(specs: Record<string, { name: string; role: string }>): SpecializedOwlRegistry {
  const registry = new SpecializedOwlRegistry();
  (registry as any).specs = new Map(
    Object.entries(specs).map(([key, spec]) => [
      key,
      {
        ...spec,
        emoji: "🦉",
        expertise: [],
        personality: { challengeLevel: "medium" as const, verbosity: "balanced" as const, tone: "neutral" },
        model: { provider: "", model: "" },
        permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
        routingRules: { keywords: [] },
        skills: { allowed: [] },
      },
    ]),
  );
  return registry;
}

describe("resolveOwl", () => {
  it("finds an owl by exact name", () => {
    const registry = makeRegistry({ tradingbot: { name: "TradingBot", role: "trading assistant" } });
    const spec = resolveOwl("tradingbot", registry);
    expect(spec).not.toBeNull();
    expect(spec!.name).toBe("TradingBot");
  });

  it("finds an owl case-insensitively", () => {
    const registry = makeRegistry({ calculus: { name: "Calculus", role: "math teacher" } });
    expect(resolveOwl("CALCULUS", registry)).not.toBeNull();
  });

  it("finds an owl by prefix", () => {
    const registry = makeRegistry({ calculus: { name: "Calculus", role: "math teacher" } });
    expect(resolveOwl("calc", registry)).not.toBeNull();
  });

  it("returns null when not found", () => {
    const registry = makeRegistry({});
    expect(resolveOwl("unknown", registry)).toBeNull();
  });

  it("returns null with undefined registry", () => {
    expect(resolveOwl("anything", undefined)).toBeNull();
  });
});
