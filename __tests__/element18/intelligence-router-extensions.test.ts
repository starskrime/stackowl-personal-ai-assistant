import { describe, it, expect } from "vitest";
import { IntelligenceRouter } from "../../src/intelligence/router.js";
import type { IntelligenceConfig } from "../../src/intelligence/router.js";

function makeConfig(overrides: Partial<IntelligenceConfig> = {}): IntelligenceConfig {
  return {
    tiers: {
      high: { provider: "anthropic", model: "claude-opus-4-6",  capabilities: ["reasoning", "vision", "code"] },
      mid:  { provider: "anthropic", model: "claude-sonnet-4-6", capabilities: ["code"] },
      low:  { provider: "anthropic", model: "claude-haiku-4-5-20251001" },
    },
    defaults: { conversation: "mid" },
    ...overrides,
  };
}

describe("IntelligenceRouter.resolveCapable()", () => {
  it("routes to the first tier (high) with all required capabilities", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("conversation", ["vision"]);
    expect(result.tier).toBe("high");
    expect(result.model).toBe("claude-opus-4-6");
  });

  it("falls back to unconstrained resolve() when no tier has required capability", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("conversation", ["long-context"]);
    // No tier has "long-context" — falls back to resolve("conversation") = mid
    expect(result.tier).toBe("mid");
    expect(result.model).toBe("claude-sonnet-4-6");
  });

  it("returns resolve() directly when required is empty", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    const result = router.resolveCapable("parliament", []);
    expect(result.tier).toBe("high"); // parliament defaults to high
  });
});

describe("IntelligenceRouter.resolveWithCostAwareness()", () => {
  it("returns normal result when maxDailyUsd is 0 (unlimited)", () => {
    const router = new IntelligenceRouter(
      makeConfig({ costPolicy: { maxDailyUsd: 0, downgradeTierOnBudgetExhausted: true } }),
      "anthropic", "claude-sonnet-4-6",
      () => ({ dailyRemainingUsd: 0, maxDailyUsd: 0 }),
    );
    const result = router.resolveWithCostAwareness("conversation");
    expect(result.tier).toBe("mid"); // no downgrade when unlimited
  });

  it("downgrades from high to low when high and mid tiers cost too much", () => {
    // haiku costs $0.0088 per request (1000 in + 2000 out tokens)
    // sonnet costs $0.033, opus costs $0.165
    // Budget of 0.009 is > haiku (0.0088) but < sonnet (0.033) → expect downgrade to low
    const router = new IntelligenceRouter(
      makeConfig(),
      "anthropic", "claude-sonnet-4-6",
      () => ({ dailyRemainingUsd: 0.009, maxDailyUsd: 1 }),
    );
    // parliament defaults to "high" — should downgrade to low (haiku fits in budget)
    const result = router.resolveWithCostAwareness("parliament");
    expect(result.tier).toBe("low");
  });

  it("allows routing with warning when all tiers are over budget", () => {
    const router = new IntelligenceRouter(
      {
        tiers: {
          high: { provider: "p", model: "expensive-model-1" },
          mid:  { provider: "p", model: "expensive-model-2" },
          low:  { provider: "p", model: "expensive-model-3" },
        },
        defaults: { conversation: "mid" },
        costPolicy: { maxDailyUsd: 0.0001, downgradeTierOnBudgetExhausted: true },
      },
      "p", "expensive-model-2",
      () => ({ dailyRemainingUsd: 0, maxDailyUsd: 0.0001 }),
    );
    // Should not throw — routes to resolve() as fallback
    expect(() => router.resolveWithCostAwareness("conversation")).not.toThrow();
  });
});

describe("IntelligenceRouter.resolveFailover()", () => {
  it("returns the first FallbackEntry matching the tier", () => {
    const router = new IntelligenceRouter(
      makeConfig({
        fallbacks: [
          { provider: "openai", model: "gpt-4o-mini", forTiers: ["high", "mid"] },
          { provider: "deepseek", model: "deepseek-chat", forTiers: ["low"] },
        ],
      }),
      "anthropic", "claude-sonnet-4-6",
    );
    const result = router.resolveFailover("high");
    expect(result?.provider).toBe("openai");
    expect(result?.model).toBe("gpt-4o-mini");
    expect(result?.tier).toBe("high");
  });

  it("returns null when no fallbacks are configured", () => {
    const router = new IntelligenceRouter(makeConfig(), "anthropic", "claude-sonnet-4-6");
    expect(router.resolveFailover("high")).toBeNull();
  });

  it("returns null when no fallback entry matches the given tier", () => {
    const router = new IntelligenceRouter(
      makeConfig({ fallbacks: [{ provider: "openai", model: "gpt-4o", forTiers: ["high"] }] }),
      "anthropic", "claude-sonnet-4-6",
    );
    expect(router.resolveFailover("low")).toBeNull();
  });
});

describe("DEFAULT_INTELLIGENCE_CONFIG passthrough", () => {
  it("routes every task to the same provider/model when using default config", async () => {
    const { buildDefaultIntelligenceConfig } = await import("../../src/config/loader.js");
    const config = buildDefaultIntelligenceConfig("anthropic", "claude-sonnet-4-6");
    const router = new IntelligenceRouter(config, "anthropic", "claude-sonnet-4-6");
    const result = router.resolve("conversation");
    expect(result.provider).toBe("anthropic");
    expect(result.model).toBe("claude-sonnet-4-6");
  });
});
