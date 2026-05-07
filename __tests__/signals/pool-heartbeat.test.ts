import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g1",
  title: "Ship 16b",
  description: "",
  status: "active",
  priority: "high",
  subGoalIds: [],
  dependsOn: [],
  progress: 0,
  milestones: [],
  mentionedInSessions: [],
  lastActiveAt: 0,
  createdAt: 0,
  updatedAt: 0,
  tags: [],
};

function makePool(opts: {
  verify?: any;
  getTop?: () => Goal | undefined;
  emit?: any;
}) {
  return new SignalPool({
    bus: { emit: opts.emit ?? vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.8 }) },
    verifier: { verify: opts.verify ?? vi.fn() } as any,
    goalGraph: {
      getActive: () => (opts.getTop?.() ? [opts.getTop()!] : []),
      getTopPriority: opts.getTop ?? (() => undefined),
    } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

function admit(pool: SignalPool, overrides: Partial<ContextSignal> = {}): ContextSignal {
  const s: ContextSignal = {
    id: overrides.id ?? Math.random().toString(36),
    source: "git",
    priority: "medium",
    title: "t",
    content: "c",
    timestamp: Date.now(),
    ttlMs: 60_000,
    userSurfaceable: false,
    ...overrides,
  };
  // Insert directly via the internal map by using a shim that mirrors injectSignal's admit step.
  (pool as any).signals.set(s.id, s);
  return s;
}

describe("SignalPool.heartbeatTick", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it("evicts TTL-expired signals and emits signal:expired with reason=ttl", async () => {
    const emit = vi.fn();
    const pool = makePool({ emit });
    admit(pool, { id: "old", timestamp: Date.now() - 120_000, ttlMs: 60_000 });
    admit(pool, { id: "fresh", timestamp: Date.now(), ttlMs: 60_000 });

    await pool.heartbeatTick();

    const ids = pool.getState().signals.map((s) => s.id);
    expect(ids).not.toContain("old");
    expect(ids).toContain("fresh");
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:expired", reason: "ttl" }),
    );
  });

  it("re-verifies up to 5 medium/high non-surfaceable candidates against active goal", async () => {
    const verify = vi.fn(async () => ({ verdict: "NEUTRAL", reason: "" }));
    const pool = makePool({ verify, getTop: () => goal });
    for (let i = 0; i < 8; i++) admit(pool, { id: `s${i}`, priority: "medium" });
    // low-priority should be skipped entirely
    admit(pool, { id: "low", priority: "low" });

    await pool.heartbeatTick();

    expect(verify).toHaveBeenCalledTimes(5);
  });

  it("skips re-verify when no top-priority goal", async () => {
    const verify = vi.fn();
    const pool = makePool({ verify });
    admit(pool, { id: "s1", priority: "high" });
    await pool.heartbeatTick();
    expect(verify).not.toHaveBeenCalled();
  });

  it("ADVANCES on heartbeat sets userSurfaceable=true and emits signal:promoted", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => ({ verdict: "ADVANCES", reason: "fits goal" }));
    const pool = makePool({ verify, getTop: () => goal, emit });
    admit(pool, { id: "s1", priority: "high" });

    await pool.heartbeatTick();

    expect(pool.getState().signals[0].userSurfaceable).toBe(true);
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:promoted" }),
    );
  });

  it("verifier throw during heartbeat is logged and does not break the loop", async () => {
    let n = 0;
    const verify = vi.fn(async () => {
      if (n++ === 0) throw new Error("model down");
      return { verdict: "NEUTRAL", reason: "" };
    });
    const pool = makePool({ verify, getTop: () => goal });
    admit(pool, { id: "s1", priority: "high" });
    admit(pool, { id: "s2", priority: "high" });

    await expect(pool.heartbeatTick()).resolves.toBeUndefined();
    expect(verify).toHaveBeenCalledTimes(2);
  });

  it("schedules heartbeatTick every 60s via setInterval", async () => {
    vi.useFakeTimers();
    const pool = makePool({});
    const heartbeatSpy = vi
      .spyOn(pool as any, "heartbeatTick")
      .mockResolvedValue(undefined);

    pool.start();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(heartbeatSpy).toHaveBeenCalledTimes(1);

    pool.stop();
  });
});
