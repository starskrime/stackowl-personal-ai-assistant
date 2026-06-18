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

  it("sets budgetExhausted via token count without EXHAUSTION_MARKER", async () => {
    const tinyBudgetProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: "A short response",
        toolCalls: [],
        usage: { promptTokens: 100, completionTokens: 50 },
        model: "mock",
        finishReason: "stop",
      }),
      chatWithTools: vi.fn(),
    };
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "hello" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 100, used: 80, remaining: 20 }, // 80+150 >= 100
    };
    const result = await engine.runTurn(request, tinyBudgetProvider as any);
    expect(result.budgetExhausted).toBe(true);
  });

  it("populates failedTools when tool execution throws", async () => {
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "use tool" }],
      tools: [{ name: "my_tool", description: "a tool", parameters: { type: "object", properties: {} } }],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
      toolRegistry: {
        execute: async () => { throw new Error("tool exploded"); },
      },
    };
    const providerWithToolCalls = {
      name: "mock",
      chat: vi.fn(),
      chatWithTools: vi.fn().mockResolvedValue({
        content: "calling tool",
        toolCalls: [{ id: "tc1", name: "my_tool", arguments: {} }],
        usage: { promptTokens: 50, completionTokens: 30 },
        model: "mock",
        finishReason: "tool_calls",
      }),
    };
    const result = await engine.runTurn(request, providerWithToolCalls as any);
    expect(result.failedTools.length).toBe(1);
    expect(result.failedTools[0].name).toBe("my_tool");
    expect(result.failedTools[0].reason).toContain("tool exploded");
  });

  it("strips all internal markers from content", async () => {
    const markerProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({
        content: "Hello [CAPABILITY_GAP:needs_shell] [SYSTEM:internal] [DONE] [DEEPER] world",
        toolCalls: [],
        usage: { promptTokens: 20, completionTokens: 10 },
        model: "mock",
        finishReason: "stop",
      }),
      chatWithTools: vi.fn(),
    };
    const engine = new OwlEngine();
    const request: TurnRequest = {
      messages: [{ role: "user", content: "hello" }],
      tools: [],
      modelName: "mock",
      providerName: "mock",
      sessionId: "s1",
      turnBudget: { total: 8000, used: 0, remaining: 8000 },
    };
    const result = await engine.runTurn(request, markerProvider as any);
    expect(result.content).not.toContain("[CAPABILITY_GAP:");
    expect(result.content).not.toContain("[SYSTEM:");
    expect(result.content).not.toContain("[DONE]");
    expect(result.content).not.toContain("[DEEPER]");
    expect(result.content).toContain("Hello");
    expect(result.content).toContain("world");
    expect(result.pendingCapabilityGap).toBe("needs_shell");
  });
});
