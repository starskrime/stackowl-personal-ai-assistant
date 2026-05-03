// __tests__/parliament/diversity-filter.test.ts
import { describe, it, expect, vi } from "vitest";
import { DiversityFilter } from "../../src/parliament/diversity-filter.js";
import type { OwlPosition } from "../../src/parliament/protocol.js";

function makePos(owlName: string, argument: string): OwlPosition {
  return { owlName, owlEmoji: "🦉", position: "FOR", argument };
}

describe("DiversityFilter", () => {
  it("returns the two most diverging positions from LLM response", async () => {
    const positions = [
      makePos("Owl1", "We should use microservices"),
      makePos("Owl2", "We should use a monolith"),
      makePos("Owl3", "We should use serverless"),
    ];
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"indices": [0, 1]}' }) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const mockProviders = new Map([["test", mockProvider]]);
    const filter = new DiversityFilter(mockRouter as any, mockProviders as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("Owl1");
    expect(b.owlName).toBe("Owl2");
  });

  it("falls back to [positions[0], positions[last]] when router throws", async () => {
    const positions = [
      makePos("Owl1", "arg1"),
      makePos("Owl2", "arg2"),
      makePos("Owl3", "arg3"),
    ];
    const mockProvider = { chat: vi.fn().mockRejectedValue(new Error("network error")) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const filter = new DiversityFilter(mockRouter as any, new Map([["test", mockProvider]]) as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("Owl1");
    expect(b.owlName).toBe("Owl3");
  });

  it("returns both positions when exactly 2 positions are provided", async () => {
    const positions = [makePos("OwlA", "for"), makePos("OwlB", "against")];
    const mockProvider = { chat: vi.fn().mockResolvedValue({ content: '{"indices": [0, 1]}' }) };
    const mockRouter = { resolve: vi.fn().mockReturnValue({ provider: "test", model: "m", tier: "low" as const }) };
    const filter = new DiversityFilter(mockRouter as any, new Map([["test", mockProvider]]) as any);
    const [a, b] = await filter.selectDivergingPair(positions);
    expect(a.owlName).toBe("OwlA");
    expect(b.owlName).toBe("OwlB");
  });
});
