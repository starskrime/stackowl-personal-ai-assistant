import { describe, it, expect, vi } from "vitest";
import { buildClassifyFn, type SpecialistSummary } from "../../src/routing/llm-classifier.js";
import type { ModelProvider, ChatResponse } from "../../src/providers/base.js";

function mockProvider(responseText: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({
      content: responseText,
      model: "mock",
      finishReason: "stop",
    } satisfies ChatResponse),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    chatWithToolsStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn().mockResolvedValue([]),
    healthCheck: vi.fn().mockResolvedValue(true),
  } as unknown as ModelProvider;
}

const specialists: SpecialistSummary[] = [
  { name: "Calculus", role: "math teacher", expertise: ["mathematics", "arithmetic"] },
  { name: "HistoryOwl", role: "history teacher", expertise: ["world history", "historical events"] },
];

describe("buildClassifyFn", () => {
  it("returns the matched specialist name when LLM responds with a valid name", async () => {
    const provider = mockProvider("Calculus");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBe("Calculus");
  });

  it("is case-insensitive when matching specialist name", async () => {
    const provider = mockProvider("calculus");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBe("Calculus");
  });

  it("returns null when LLM responds with 'none'", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("tell me a joke", specialists);

    expect(result).toBeNull();
  });

  it("returns null when LLM responds with an unknown name", async () => {
    const provider = mockProvider("UnknownOwl");
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("something", specialists);

    expect(result).toBeNull();
  });

  it("returns null and does not throw when provider throws", async () => {
    const provider = {
      name: "mock",
      chat: vi.fn().mockRejectedValue(new Error("network error")),
    } as unknown as ModelProvider;
    const classify = buildClassifyFn(provider, "test-model");

    const result = await classify("what is 5+5?", specialists);

    expect(result).toBeNull();
  });

  it("includes all specialists in the prompt sent to the provider", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    await classify("test message", specialists);

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0];
    const messages = chatCall[0] as Array<{ role: string; content: string }>;
    const prompt = messages[0].content;

    expect(prompt).toContain("Calculus");
    expect(prompt).toContain("HistoryOwl");
    expect(prompt).toContain("test message");
  });

  it("calls provider with maxTokens: 30", async () => {
    const provider = mockProvider("none");
    const classify = buildClassifyFn(provider, "test-model");

    await classify("test", specialists);

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0];
    const options = chatCall[2];
    expect(options?.maxTokens).toBe(30);
  });
});
