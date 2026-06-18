import { describe, it, expect, vi } from "vitest";
import { TopicWorthinessEvaluator } from "../../src/parliament/topic-worthiness.js";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
    parliament: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), behavioral: vi.fn(), error: vi.fn() },
  },
}));

describe("TopicWorthinessEvaluator", () => {
  it("trusts LLM isWorthy directly without THRESHOLD gate", async () => {
    // LLM returns isWorthy=true, confidence=0.3 (below old 0.4 confidence gate)
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: '{"isWorthy": true, "confidence": 0.3, "reasons": ["test"], "category": "tradeoff"}',
      }),
    } as any;
    const evaluator = new TopicWorthinessEvaluator(mockProvider);
    const result = await evaluator.evaluate("should I use react or vue?");
    // With THRESHOLD deleted, isWorthy=true from LLM → result.isWorthy should be true
    expect(result.isWorthy).toBe(true);
  });

  it("THRESHOLD export is removed", async () => {
    const mod = await import("../../src/parliament/topic-worthiness.js");
    expect((mod as Record<string, unknown>).THRESHOLD).toBeUndefined();
  });

  it("uses router.resolve('classification') model when router is provided", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: '{"isWorthy": true, "confidence": 0.8, "reasons": [], "category": "tradeoff"}',
      }),
    } as any;
    const mockRouter = {
      resolve: vi.fn().mockReturnValue({ provider: "mock", model: "router-model", tier: "low" as const }),
    } as any;
    const evaluator = new TopicWorthinessEvaluator(mockProvider, mockRouter);
    await evaluator.evaluate("tradeoffs of microservices");
    // Second arg of chat() should be "router-model"
    expect(mockProvider.chat).toHaveBeenCalledWith(
      expect.any(Array),
      "router-model",
      expect.any(Object),
    );
  });
});
