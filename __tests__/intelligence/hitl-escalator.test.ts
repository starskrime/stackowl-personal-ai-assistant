import { describe, it, expect } from "vitest";
import { HITLEscalator } from "../../src/intelligence/hitl-escalator.js";

describe("HITLEscalator", () => {
  it("does not escalate below threshold", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404 not found", "search docs");
    e.onBlocked("web", "timeout", "search docs");
    expect(e.shouldEscalate(6)).toBe(false); // threshold = 3 at challengeLevel 6
  });

  it("escalates at threshold", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "timeout", "search");
    e.onBlocked("memory", "not found", "search");
    expect(e.shouldEscalate(6)).toBe(true);
  });

  it("escalates after 1 failure when challengeLevel is 2", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    expect(e.shouldEscalate(2)).toBe(true);
  });

  it("buildNarration includes attempt summaries", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404 not found", "find docs");
    e.onBlocked("memory", "no match", "find docs");
    e.onBlocked("web", "timeout", "find docs");
    const narration = e.buildNarration();
    expect(narration).toContain("3 approaches");
    expect(narration).toContain("web: 404 not found");
    expect(narration).toContain("genuinely stuck");
  });

  it("buildQuestion returns binary choice", () => {
    const e = new HITLEscalator();
    const q = e.buildQuestion(["try the API directly", "search for a cached version"]);
    expect(q).toContain("(A) try the API directly");
    expect(q).toContain("(B) search for a cached version");
  });

  it("reset clears state", () => {
    const e = new HITLEscalator();
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "404", "search");
    e.onBlocked("web", "404", "search");
    e.reset();
    expect(e.shouldEscalate(6)).toBe(false);
  });
});
