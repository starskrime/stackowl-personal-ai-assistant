import { describe, it, expect, vi } from "vitest";
import { OwlEngine } from "../src/engine/runtime.js";
import type { TurnRequest } from "../src/engine/types.js";

const mockProvider = {
  name: "mock",
  chat: vi.fn().mockResolvedValue({
    content: "I can help with that. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
  chatWithTools: vi.fn().mockResolvedValue({
    content: "I can help with that. [DONE]",
    toolCalls: [],
    usage: { promptTokens: 50, completionTokens: 30 },
    model: "mock",
    finishReason: "stop",
  }),
};

describe("OwlEngine.runTurn()", () => {
  it("returns TurnResult with doneSignal and no [DONE] text in content", async () => {
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "hello" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
    };
    const result = await engine.runTurn(request, mockProvider as any);
    expect(result.doneSignal).toBe(true);
    expect(result.content).not.toContain("[DONE]");
    expect(result.budgetExhausted).toBe(false);
    expect(result.tokensUsed).toBeGreaterThan(0);
  });

  it("never returns EXHAUSTION_MARKER in content", async () => {
    const exhaustedResponse = {
      content: "I tried many things. __STACKOWL_EXHAUSTED__",
      toolCalls: [],
      usage: { promptTokens: 100, completionTokens: 50 },
      model: "mock",
      finishReason: "stop",
    };
    const exhaustedProvider = {
      ...mockProvider,
      chat: vi.fn().mockResolvedValue(exhaustedResponse),
      chatWithTools: vi.fn().mockResolvedValue(exhaustedResponse),
    };
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "do something hard" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
    };
    const result = await engine.runTurn(request, exhaustedProvider as any);
    expect(result.content).not.toContain("__STACKOWL_EXHAUSTED__");
    expect(result.budgetExhausted).toBe(true);
  });
});
