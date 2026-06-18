import { describe, it, expect } from "vitest";
import { QualityEvaluator } from "../src/engine/quality-evaluator.js";

const ev = new QualityEvaluator();

describe("QualityEvaluator", () => {
  it("starts near 1.0 for clean response", () => {
    const s = ev.evaluateSync({ content: "Here is the comparison table.", loopExhausted: false, toolCallCount: 3, toolFailureCount: 0, taskComplexity: "medium", hasStructuredOutput: true });
    expect(s).toBeGreaterThan(0.9);
  });
  it("penalizes loop exhaustion", () => {
    const s = ev.evaluateSync({ content: "I tried.", loopExhausted: true, toolCallCount: 5, toolFailureCount: 3, taskComplexity: "medium", hasStructuredOutput: false });
    expect(s).toBeLessThan(0.75);
  });
  it("penalizes raw error patterns", () => {
    const s = ev.evaluateSync({ content: "Error: HTTP 429 Too Many Requests", loopExhausted: false, toolCallCount: 1, toolFailureCount: 1, taskComplexity: "simple", hasStructuredOutput: false });
    expect(s).toBeLessThan(0.75);
  });
  it("strips EXHAUSTION_MARKER", () => {
    const { cleanContent } = ev.evaluateAndStrip({ content: "I tried. __STACKOWL_EXHAUSTED__", loopExhausted: true, toolCallCount: 0, toolFailureCount: 0, taskComplexity: "medium", hasStructuredOutput: false });
    expect(cleanContent).not.toContain("__STACKOWL_EXHAUSTED__");
  });
  it("strips HTTP error jargon", () => {
    expect(ev.stripJargon("Got HTTP 429 error")).not.toContain("HTTP 429");
  });
});
