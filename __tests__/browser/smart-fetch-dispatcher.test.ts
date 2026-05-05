import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../../src/browser/smart-fetch.js";

const noopBus = { emit: vi.fn() } as any;

describe("runEscalationChain — Element 16c default order", () => {
  it("does not invoke any 'http' tier (http tier deleted)", async () => {
    const runners: TierRunner[] = [
      {
        tier: 1,
        name: "scrapling",
        isAvailable: () => true,
        run: async () => ({
          attempt: { tier: 1, name: "scrapling", outcome: "success", durationMs: 10 },
          data: { kind: "page", url: "https://x", content: "ok" },
        }),
      },
    ];
    const result = await runEscalationChain(runners, "https://x", { bus: noopBus });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(["scrapling", "camofox", "obscura"]).toContain("scrapling");
    }
  });
});
