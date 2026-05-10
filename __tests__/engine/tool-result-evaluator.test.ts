import { describe, it, expect, beforeEach, vi } from "vitest";
import { ToolResultEvaluator } from "../../src/engine/tool-result-evaluator.js";

function mockProvider(responseJson: object) {
  return {
    chat: vi.fn().mockResolvedValue({
      content: JSON.stringify(responseJson),
      model: "test-model",
      finishReason: "stop",
    }),
  } as any;
}

describe("ToolResultEvaluator", () => {
  it("returns satisfied: true when provider returns a satisfied verdict", async () => {
    const provider = mockProvider({ satisfied: true, confidence: 0.9, reason: "found prices" });
    const evaluator = new ToolResultEvaluator(provider);

    const verdict = await evaluator.evaluate("web_search", { q: "price" }, "Price: $42", "find prices");

    expect(verdict.satisfied).toBe(true);
    expect(verdict.confidence).toBe(0.9);
    expect(verdict.reason).toBe("found prices");
    expect(provider.chat).toHaveBeenCalledTimes(1);
  });

  it("returns satisfied: false with reason and suggestedAlternative", async () => {
    const provider = mockProvider({
      satisfied: false,
      confidence: 0.8,
      reason: "no prices found",
      suggestedAlternative: "web_fetch",
    });
    const evaluator = new ToolResultEvaluator(provider);

    const verdict = await evaluator.evaluate("web_search", { q: "price" }, "No results.", "find prices");

    expect(verdict.satisfied).toBe(false);
    expect(verdict.confidence).toBe(0.8);
    expect(verdict.reason).toBe("no prices found");
    expect(verdict.suggestedAlternative).toBe("web_fetch");
  });

  it("caches results — calling evaluate twice with same args only calls provider once", async () => {
    const provider = mockProvider({ satisfied: true, confidence: 0.9, reason: "found prices" });
    const evaluator = new ToolResultEvaluator(provider);

    await evaluator.evaluate("web_search", { q: "price" }, "Price: $42", "find prices");
    await evaluator.evaluate("web_search", { q: "price" }, "Price: $42", "find prices");

    expect(provider.chat).toHaveBeenCalledTimes(1);
  });

  it("returns satisfied: true when provider throws (safe default)", async () => {
    const provider = {
      chat: vi.fn().mockRejectedValue(new Error("network failure")),
    } as any;
    const evaluator = new ToolResultEvaluator(provider);

    const verdict = await evaluator.evaluate("web_search", {}, "some result", "user intent");

    expect(verdict.satisfied).toBe(true);
    expect(verdict.confidence).toBe(0);
    expect(verdict.reason).toContain("evaluator failed");
  });

  it("returns satisfied: true when provider returns malformed JSON (safe default)", async () => {
    const provider = {
      chat: vi.fn().mockResolvedValue({
        content: "not valid json at all",
        model: "test-model",
        finishReason: "stop",
      }),
    } as any;
    const evaluator = new ToolResultEvaluator(provider);

    const verdict = await evaluator.evaluate("web_search", {}, "some result", "user intent");

    expect(verdict.satisfied).toBe(true);
    expect(verdict.confidence).toBe(0);
    expect(verdict.reason).toContain("evaluator failed");
  });

  it("_clearCache() invalidates cache so next call re-queries the provider", async () => {
    const provider = mockProvider({ satisfied: true, confidence: 0.9, reason: "found prices" });
    const evaluator = new ToolResultEvaluator(provider);

    await evaluator.evaluate("web_search", { q: "price" }, "Price: $42", "find prices");
    expect(provider.chat).toHaveBeenCalledTimes(1);

    evaluator._clearCache();

    await evaluator.evaluate("web_search", { q: "price" }, "Price: $42", "find prices");
    expect(provider.chat).toHaveBeenCalledTimes(2);
  });
});
