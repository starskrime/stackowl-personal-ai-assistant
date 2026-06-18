import { describe, it, expect, beforeEach } from "vitest";
import { InstinctEngineV2 } from "../src/instincts/engine.js";
import type { InstinctSpec } from "../src/instincts/types.js";

const instincts: InstinctSpec[] = [
  { name: "no-finance", description: "Don't give financial advice", constraint: "Never give specific investment advice", owlName: "atlas", keywords: ["invest", "stock", "crypto", "portfolio"] },
  { name: "be-brief", description: "Keep responses concise", constraint: "Respond in 2-3 sentences", owlName: "atlas", keywords: ["brief", "short", "quick"] },
];

describe("InstinctEngineV2", () => {
  let engine: InstinctEngineV2;
  beforeEach(() => { engine = new InstinctEngineV2(); });

  it("matches keyword instinct instantly (no LLM call)", () => {
    const matched = engine.evaluateHeuristic(instincts, "Should I invest in Bitcoin stocks?");
    expect(matched.some(i => i.name === "no-finance")).toBe(true);
  });

  it("does not match unrelated message", () => {
    const matched = engine.evaluateHeuristic(instincts, "What is the weather today?");
    expect(matched.length).toBe(0);
  });

  it("caches results per session", () => {
    engine.evaluateHeuristic(instincts, "invest in crypto");
    const cached = engine.getCached("invest in crypto");
    expect(cached).not.toBeNull();
  });

  it("buildConstraintBlock returns constraint strings", () => {
    const matched = engine.evaluateHeuristic(instincts, "Should I buy stocks?");
    const block = engine.buildConstraintBlock(matched);
    expect(block).toContain("Never give specific investment advice");
  });
});
