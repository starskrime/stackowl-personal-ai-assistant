import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../../src/browser/smart-fetch.js";
import { createObscuraTier } from "../../src/browser/smart-fetch.js";

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

describe("createObscuraTier (Element 16c stub)", () => {
  it("emits skipped-disabled when webFetch.obscura.enabled = false", async () => {
    const tier = createObscuraTier({ enabled: false });
    expect(await tier.isAvailable()).toBe(true);
    const out = await tier.run("https://x", { bus: { emit: vi.fn() } as any });
    expect(out.attempt.name).toBe("obscura");
    expect(out.attempt.outcome).toBe("skipped-disabled");
    expect(out.data).toBeUndefined();
  });

  it("emits skipped-disabled even when enabled = true (no runtime client this round)", async () => {
    const tier = createObscuraTier({ enabled: true });
    const out = await tier.run("https://x", { bus: { emit: vi.fn() } as any });
    expect(out.attempt.name).toBe("obscura");
    expect(out.attempt.outcome).toBe("skipped-disabled");
  });
});
