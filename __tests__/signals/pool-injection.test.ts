import { describe, it, expect, vi, beforeEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { ContextSignal } from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function sig(source: any = "git", priority: any = "low"): ContextSignal {
  return {
    id: Math.random().toString(36),
    source,
    priority,
    title: "t",
    content: "c",
    timestamp: Date.now(),
    ttlMs: 60_000,
  };
}

function makePool(opts: { classify: any; consent?: any; enabledSources?: any }) {
  return new SignalPool({
    bus: { emit: vi.fn(), on: vi.fn() } as any,
    classifier: { classify: opts.classify },
    verifier: { verify: vi.fn() } as any,
    goalGraph: {
      getActive: () => [],
      getTopPriority: () => undefined,
    } as any,
    config: {
      maxSignals: 32,
      consent: opts.consent ?? {},
      enabledSources: opts.enabledSources,
    },
    workspacePath: "/tmp",
  });
}

describe("SignalPool.injectSignal — gates", () => {
  beforeEach(() => vi.clearAllMocks());

  it("drops when consent[source]===false (no classifier call)", async () => {
    const classify = vi.fn();
    const pool = makePool({ classify, consent: { clipboard: false } });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
    expect(pool.getState().signals).toEqual([]);
  });

  it("drops when source not in enabledSources (no classifier call)", async () => {
    const classify = vi.fn();
    const pool = makePool({ classify, enabledSources: ["git"] });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
  });

  it("admits at low when classifier confidence < 0.7", async () => {
    const pool = makePool({
      classify: async () => ({ keep: true, confidence: 0.5 }),
    });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("low");
  });

  it("admits at medium when confidence in [0.7, 0.9)", async () => {
    const pool = makePool({
      classify: async () => ({ keep: true, confidence: 0.8 }),
    });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("medium");
  });

  it("admits at high when confidence >= 0.9", async () => {
    const pool = makePool({
      classify: async () => ({ keep: true, confidence: 0.95 }),
    });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals[0].priority).toBe("high");
  });

  it("drops when classifier keep=false (no admission)", async () => {
    const pool = makePool({
      classify: async () => ({ keep: false, confidence: 0.9 }),
    });
    await pool.injectSignal(sig("git"));
    expect(pool.getState().signals).toEqual([]);
  });

  it("falls back to DEFAULT_CONSENT when consent map is empty", async () => {
    // clipboard default-OFF
    const classify = vi.fn();
    const pool = makePool({ classify, consent: {} });
    await pool.injectSignal(sig("clipboard"));
    expect(classify).not.toHaveBeenCalled();
    // git default-ON
    const classify2 = vi.fn(async () => ({ keep: true, confidence: 0.5 }));
    const pool2 = makePool({ classify: classify2, consent: {} });
    await pool2.injectSignal(sig("git"));
    expect(classify2).toHaveBeenCalled();
  });
});
