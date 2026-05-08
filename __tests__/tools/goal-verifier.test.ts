import { describe, it, expect, vi } from "vitest";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import type { IntelligenceRouter } from "../../src/intelligence/router.js";
import type { ModelProvider } from "../../src/providers/base.js";
import type { SubGoal } from "../../src/engine/types.js";

// Build a ClassificationRouter mock where resolve() returns a provider whose
// chat() calls the supplied chatFn. Exposing chatFn separately lets tests assert
// on how chat() was called (args, temperature, etc.).
function makeRouterWithChatFn(
  chatFn: (...args: unknown[]) => Promise<{ content: string; model: string; finishReason: string }>,
) {
  return {
    resolve: vi.fn().mockResolvedValue({
      chat: chatFn,
    }),
  };
}

function makeRouter(responseText: string) {
  const chatFn = vi.fn().mockResolvedValue({
    content: responseText,
    model: "mock",
    finishReason: "stop",
  });
  return makeRouterWithChatFn(chatFn);
}

const subGoal: SubGoal = {
  id: "sg-1",
  description: "Find the current TypeScript version",
  status: "in_progress",
  dependsOn: [],
};

describe("GoalVerifier", () => {
  it("returns ADVANCES when model responds with ADVANCES", async () => {
    const router = makeRouter('{"verdict":"ADVANCES","reason":"Tool found the TypeScript release page"}');
    const verifier = new GoalVerifier(router);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: { url: "https://typescriptlang.org" },
      toolResult: "TypeScript 5.5 is out",
      subGoal,
      userMessage: "What is the latest TypeScript version?",
    });
    expect(result.verdict).toBe("ADVANCES");
  });

  it("returns BLOCKED when model responds with BLOCKED", async () => {
    const router = makeRouter('{"verdict":"BLOCKED","reason":"Paywall, no content","suggestion":"try a different URL"}');
    const verifier = new GoalVerifier(router);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: { url: "https://paywalled.com" },
      toolResult: "Subscribe to read",
      subGoal,
      userMessage: "What is the latest TypeScript version?",
    });
    expect(result.verdict).toBe("BLOCKED");
    expect(result.suggestion).toBe("try a different URL");
  });

  it("returns NEUTRAL when model responds with NEUTRAL", async () => {
    const router = makeRouter('{"verdict":"NEUTRAL","reason":"Tool ran but result is unrelated"}');
    const verifier = new GoalVerifier(router);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "cat photos",
      subGoal,
      userMessage: "What is the latest TypeScript version?",
    });
    expect(result.verdict).toBe("NEUTRAL");
  });

  it("returns NEUTRAL when model returns unparseable response (fail-open)", async () => {
    const router = makeRouter("I cannot determine this");
    const verifier = new GoalVerifier(router);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "some result",
      subGoal,
      userMessage: "test",
    });
    expect(result.verdict).toBe("NEUTRAL");
  });

  it("returns PARTIAL when model responds with PARTIAL", async () => {
    const router = makeRouter('{"verdict":"PARTIAL","reason":"Found some but not all info"}');
    const verifier = new GoalVerifier(router);
    const result = await verifier.verify({
      toolName: "web_search",
      toolArgs: { query: "typescript version" },
      toolResult: "TypeScript 5.4 released last year",
      subGoal,
      userMessage: "What is the LATEST TypeScript version?",
    });
    expect(result.verdict).toBe("PARTIAL");
  });

  it("calls provider.chat with temperature 0 for deterministic classification", async () => {
    const chatFn = vi.fn().mockResolvedValue({
      content: '{"verdict":"ADVANCES","reason":"test"}',
      model: "mock",
      finishReason: "stop",
    });
    const router = makeRouterWithChatFn(chatFn);
    const verifier = new GoalVerifier(router);
    await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "test result",
      subGoal: { id: "sg-1", description: "test goal", status: "in_progress", dependsOn: [] },
      userMessage: "test",
    });
    expect(chatFn).toHaveBeenCalledWith(
      expect.any(Array),
      undefined, // model — passed as undefined; resolved separately via create()
      expect.objectContaining({ temperature: 0 }),
    );
  });

  it("GoalVerifier.create() wires IntelligenceRouter + Map<string, ModelProvider> into the classification tier", async () => {
    const chatMock = vi.fn().mockResolvedValue({
      content: '{"verdict":"ADVANCES","reason":"Factory wired correctly"}',
      model: "cheap-model",
      finishReason: "stop",
    });

    const realRouter = {
      resolve: vi.fn().mockReturnValue({
        provider: "anthropic",
        model: "claude-haiku-3",
        tier: "low",
      }),
    } as unknown as IntelligenceRouter;

    const providers = new Map<string, ModelProvider>([
      ["anthropic", { chat: chatMock } as unknown as ModelProvider],
    ]);

    const verifier = GoalVerifier.create(realRouter, providers);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: { url: "https://typescriptlang.org" },
      toolResult: "TypeScript 5.5 is out",
      subGoal,
      userMessage: "What is the latest TypeScript version?",
    });

    expect(result.verdict).toBe("ADVANCES");
    // router.resolve must always be called with "classification"
    expect(realRouter.resolve).toHaveBeenCalledWith("classification");
    // provider.chat must be called with the resolved model and temperature: 0
    expect(chatMock).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ role: "system" })]),
      "claude-haiku-3",
      expect.objectContaining({ temperature: 0 }),
    );
  });

  it("GoalVerifier.create() fails open (NEUTRAL) when provider is not in the map", async () => {
    const realRouter = {
      resolve: vi.fn().mockReturnValue({
        provider: "missing-provider",
        model: "some-model",
        tier: "low",
      }),
    } as unknown as IntelligenceRouter;

    const providers = new Map<string, ModelProvider>(); // empty — provider not found

    const verifier = GoalVerifier.create(realRouter, providers);
    const result = await verifier.verify({
      toolName: "web_crawl",
      toolArgs: {},
      toolResult: "some result",
      subGoal,
      userMessage: "test",
    });

    expect(result.verdict).toBe("NEUTRAL");
    expect(result.reason).toBe("provider not found");
  });
});
