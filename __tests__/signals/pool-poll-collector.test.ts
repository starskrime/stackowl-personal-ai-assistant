import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { SignalCollector } from "../../src/ambient/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

function makePool() {
  return new SignalPool({
    bus: { emit: vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.5 }) },
    verifier: { verify: vi.fn() } as any,
    goalGraph: { getActive: () => [], getTopPriority: () => undefined } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

describe("SignalPool poll collector wrapper", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("invokes collect and admits returned signals", async () => {
    const collect = vi.fn(async () => [
      {
        id: "s",
        source: "git" as const,
        priority: "low" as const,
        title: "t",
        content: "c",
        timestamp: Date.now(),
        ttlMs: 60_000,
      },
    ]);
    const c: SignalCollector = {
      source: "git",
      mode: "poll",
      intervalMs: 1000,
      collect,
    };
    const pool = makePool();
    pool.addCollector(c);
    pool.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(collect).toHaveBeenCalled();
    pool.stop();
  });

  it("deregisters collector after 3 consecutive failures", async () => {
    const collect = vi.fn(async () => {
      throw new Error("boom");
    });
    const c: SignalCollector = {
      source: "git",
      mode: "poll",
      intervalMs: 100,
      collect,
    };
    const pool = makePool();
    pool.addCollector(c);
    pool.start();
    // Initial tick + 4 interval ticks
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(100);
    }
    expect(collect.mock.calls.length).toBeLessThanOrEqual(3);
    pool.stop();
  });
});
