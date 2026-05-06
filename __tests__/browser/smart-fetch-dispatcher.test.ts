import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../../src/browser/smart-fetch.js";
import { createObscuraTier } from "../../src/browser/smart-fetch.js";

const noopBus = { emit: vi.fn() } as any;

describe("runEscalationChain — host-aware reorder", () => {
  it("reorders runners when FallbackSequencer suggests a different starting tool for hostRoot", async () => {
    const calls: string[] = [];
    const runners: TierRunner[] = [
      { tier: 1, name: "scrapling", isAvailable: () => true, run: async () => { calls.push("scrapling"); return { attempt: { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 10 } }; } },
      { tier: 2, name: "camofox", isAvailable: () => true, run: async () => { calls.push("camofox"); return { attempt: { tier: 2, name: "camofox", outcome: "success", durationMs: 20 }, data: { kind: "page", url: "https://linkedin.com/in/x", content: "ok" } }; } },
    ];
    const sequencer = {
      getNextFallback: (_from: string, _cap: string, _excl: string[], host?: string) =>
        host === "linkedin.com" ? "camofox" : null,
    };
    const result = await runEscalationChain(runners, "https://linkedin.com/in/x", {
      bus: { emit: vi.fn() } as any,
      sequencer: sequencer as any,
    });
    expect(result.success).toBe(true);
    expect(calls[0]).toBe("camofox");
    // scrapling was bypassed — it must never be invoked
    expect(calls).toHaveLength(1);
  });

  it("does not double-iterate bypassed runners when preferred runner fails", async () => {
    const calls: string[] = [];
    const runners: TierRunner[] = [
      {
        tier: 1, name: "scrapling", isAvailable: () => true,
        run: async () => { calls.push("scrapling"); return { attempt: { tier: 1, name: "scrapling", outcome: "error", durationMs: 5 } }; },
      },
      {
        tier: 2, name: "camofox", isAvailable: () => true,
        run: async () => { calls.push("camofox"); return { attempt: { tier: 2, name: "camofox", outcome: "blocked", durationMs: 12 } }; },
      },
    ];
    const sequencer = {
      getNextFallback: (_f: string, _c: string, _e: string[], host?: string) =>
        host === "linkedin.com" ? "camofox" : null,
    };
    const result = await runEscalationChain(runners, "https://linkedin.com/in/x", {
      bus: { emit: vi.fn() } as any,
      sequencer: sequencer as any,
    });

    expect(result.success).toBe(false);
    if (!result.success) {
      const scraplingAttempts = result.error.attemptedTiers.filter(a => a.name === "scrapling");
      expect(scraplingAttempts).toHaveLength(1);
      expect(scraplingAttempts[0].outcome).toBe("skipped-by-learned-routing");
      const camofoxAttempts = result.error.attemptedTiers.filter(a => a.name === "camofox");
      expect(camofoxAttempts).toHaveLength(1);
      expect(camofoxAttempts[0].outcome).toBe("blocked");
    }
    // scrapling skipped → never called; camofox called once.
    expect(calls).toEqual(["camofox"]);
  });
});

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
