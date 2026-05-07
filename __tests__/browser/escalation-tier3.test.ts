import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, createPuppeteerTier } from "../../src/browser/smart-fetch.js";
import type { TierRunner } from "../../src/browser/smart-fetch.js";
import type { PuppeteerFetcher } from "../../src/browser/puppeteer-fetcher.js";
import type { GatewayEventBus } from "../../src/gateway/event-bus.js";

const noop_bus = { emit: () => {} } as unknown as GatewayEventBus;

function mockBlockedRunner(tier: number, name: string): TierRunner {
  return {
    tier,
    name: name as any,
    isAvailable: () => true,
    run: async () => ({
      attempt: { tier, name: name as any, durationMs: 1, outcome: "blocked" as const },
    }),
  };
}

describe("Tier 3 escalation", () => {
  it("createPuppeteerTier returns TierRunner with tier=3 name=puppeteer", async () => {
    const mockFetcher = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({ html: "<h1>ok</h1>", finalUrl: "https://example.com", status: 200 }),
    } as unknown as PuppeteerFetcher;
    const runner = createPuppeteerTier(mockFetcher);
    expect(runner.tier).toBe(3);
    expect(runner.name).toBe("puppeteer");
    expect(await runner.isAvailable()).toBe(true);
  });

  it("runEscalationChain returns puppeteer success after tier1+tier2 block", async () => {
    const mockFetcher = {
      probe: vi.fn().mockResolvedValue(true),
      fetch: vi.fn().mockResolvedValue({ html: "<h1>amazon</h1>", finalUrl: "https://amazon.com", status: 200 }),
    } as unknown as PuppeteerFetcher;
    const tiers: TierRunner[] = [
      mockBlockedRunner(1, "scrapling"),
      mockBlockedRunner(2, "camofox"),
      createPuppeteerTier(mockFetcher),
    ];
    const result = await runEscalationChain(tiers, "https://amazon.com", { bus: noop_bus });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.kind).toBe("page");
      expect((result.data as any).content).toBe("<h1>amazon</h1>");
    }
  });

  it("runEscalationChain records all 3 attempts when all blocked", async () => {
    const tiers: TierRunner[] = [
      mockBlockedRunner(1, "scrapling"),
      mockBlockedRunner(2, "camofox"),
      mockBlockedRunner(3, "puppeteer"),
    ];
    const blocked = await runEscalationChain(tiers, "https://amazon.com", { bus: noop_bus });
    expect(blocked.success).toBe(false);
    if (!blocked.success) {
      expect(blocked.error.attemptedTiers).toHaveLength(3);
      expect(blocked.error.attemptedTiers.map(t => t.outcome)).toEqual(["blocked", "blocked", "blocked"]);
    }
  });
});
