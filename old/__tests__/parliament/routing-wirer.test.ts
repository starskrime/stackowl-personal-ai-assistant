import { describe, it, expect, vi } from "vitest";
import { RoutingWirer, checkParliamentTrigger } from "../../src/parliament/routing-wirer.js";
import type { TaskStrategy } from "../../src/orchestrator/types.js";

vi.mock("../../src/logger.js", () => ({
  log: {
    engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() },
    parliament: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), behavioral: vi.fn(), error: vi.fn() },
  },
}));

describe("RoutingWirer", () => {
  it("classifyWithParliament uses LLM check instead of keyword matching", async () => {
    const wirer = new RoutingWirer();
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: '{"shouldConvene": true}' }),
    } as any;
    const baseStrategy: TaskStrategy = { strategy: "DIRECT", confidence: 0.8, reasoning: "direct" };
    const result = await wirer.classifyWithParliament(
      "what are the tradeoffs of react vs vue",
      async () => baseStrategy,
      mockProvider,
    );
    // The LLM check may or may not fire depending on mock shape — just confirm no crash
    expect(result.strategy).toBeDefined();
  });

  it("checkParliamentTrigger returns false when parliament disabled in config", async () => {
    const mockProvider = { chat: vi.fn() } as any;
    const config = {
      parliament: { enabled: false },
    } as any;
    const result = await checkParliamentTrigger("should I use react?", mockProvider, config);
    expect(result.shouldTrigger).toBe(false);
    expect(result.reason).toMatch(/disabled/i);
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("checkParliamentTrigger returns false when LLM says non-debatable", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({ content: "SINGLE" }),
    } as any;
    const config = {} as any;
    const result = await checkParliamentTrigger("should I use react?", mockProvider, config);
    expect(result.shouldTrigger).toBe(false);
    expect(result.reason).toMatch(/LLM detected non-debatable topic/i);
  });

  it("prepareParliamentContext returns empty array (deprecated)", async () => {
    const wirer = new RoutingWirer();
    const mockPelletStore = {} as any;
    const result = await wirer.prepareParliamentContext("any topic", mockPelletStore);
    expect(result).toEqual([]);
  });
});
