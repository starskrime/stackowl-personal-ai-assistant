import { describe, it, expect, vi, beforeEach } from "vitest";
import { DeliveryVerifier } from "../src/heartbeat/delivery-verifier.js";
import type { ModelProvider } from "../src/providers/base.js";
import type { IntelligenceRouter } from "../src/intelligence/router.js";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function makeMockProvider(responseJson: string): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: responseJson, model: "mock", finishReason: "stop" }),
    chatWithTools: vi.fn(),
    chatStream: vi.fn(),
    embed: vi.fn(),
    listModels: vi.fn(),
    healthCheck: vi.fn(),
  } as unknown as ModelProvider;
}

function makeMockRouter(model = "cheap-model"): IntelligenceRouter {
  return {
    resolve: vi.fn().mockReturnValue({ provider: "mock", model, tier: "low" }),
  } as unknown as IntelligenceRouter;
}

describe("DeliveryVerifier", () => {
  describe("verify()", () => {
    it("returns ADVANCES when LLM says ADVANCES", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "ADVANCES", reason: "helps goal" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "I learned about TypeScript generics today",
        activeGoals: ["master TypeScript"],
      });
      expect(result.verdict).toBe("ADVANCES");
    });

    it("returns NEUTRAL with suppressUntil when LLM says NEUTRAL", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NEUTRAL", reason: "tangential" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "Did you know cats sleep 16 hours?",
        activeGoals: ["master TypeScript"],
      });
      expect(result.verdict).toBe("NEUTRAL");
      expect(result.suppressUntil).toBeInstanceOf(Date);
    });

    it("returns NOISE when LLM says NOISE", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NOISE", reason: "irrelevant" }));
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "check_in",
        messagePreview: "Just saying hi",
        activeGoals: [],
      });
      expect(result.verdict).toBe("NOISE");
    });

    it("skip rule 1: returns ADVANCES without LLM call when goalId present", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "goal_progress_update",
        messagePreview: "Goal update",
        activeGoals: ["master TypeScript"],
        goalId: "goal_123",
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("skip rule 2: morning_brief always gets ADVANCES without LLM call", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "morning_brief",
        messagePreview: "Good morning brief",
        activeGoals: [],
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("skip rule 3: high-priority idle message always gets ADVANCES", async () => {
      const provider = makeMockProvider("{}");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "follow_up_stale_goal",
        messagePreview: "You haven't worked on X in 5 days",
        activeGoals: ["ship feature X"],
        idleSeconds: 5 * 3600,
        priority: 80,
      });
      expect(result.verdict).toBe("ADVANCES");
      expect(provider.chat).not.toHaveBeenCalled();
    });

    it("falls back to ADVANCES on invalid LLM response", async () => {
      const provider = makeMockProvider("not valid json");
      const verifier = new DeliveryVerifier(provider, makeMockRouter());
      const result = await verifier.verify({
        jobType: "check_in",
        messagePreview: "Quick check-in",
        activeGoals: ["ship feature X"],
      });
      expect(result.verdict).toBe("ADVANCES");
    });

    it("works without router (uses provider default model)", async () => {
      const provider = makeMockProvider(JSON.stringify({ verdict: "NEUTRAL", reason: "ok" }));
      const verifier = new DeliveryVerifier(provider);
      const result = await verifier.verify({
        jobType: "self_study",
        messagePreview: "Some study result",
        activeGoals: ["learn something"],
      });
      expect(result.verdict).toBe("NEUTRAL");
    });
  });
});
