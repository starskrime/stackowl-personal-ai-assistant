import { describe, it, expect, vi } from "vitest";
import { GoalVerifier } from "../../src/tools/goal-verifier.js";
import type { IntelligenceRouter } from "../../src/intelligence/router.js";
import type { SubGoal } from "../../src/engine/types.js";

function makeRouter(responseText: string): IntelligenceRouter {
  return {
    resolve: vi.fn().mockResolvedValue({
      chat: vi.fn().mockResolvedValue({
        content: responseText,
        model: "mock",
        finishReason: "stop",
      }),
    }),
  } as unknown as IntelligenceRouter;
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
      toolName: "duckduckgo_search",
      toolArgs: { query: "typescript version" },
      toolResult: "TypeScript 5.4 released last year",
      subGoal,
      userMessage: "What is the LATEST TypeScript version?",
    });
    expect(result.verdict).toBe("PARTIAL");
  });
});
