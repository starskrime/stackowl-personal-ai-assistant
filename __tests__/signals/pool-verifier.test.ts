import { describe, it, expect, vi, beforeEach } from "vitest";
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

function sig(): ContextSignal {
  return {
    id: "s1",
    source: "git",
    priority: "low",
    title: "t",
    content: "c",
    timestamp: Date.now(),
    ttlMs: 60_000,
  };
}

function makePool(opts: {
  verify: any;
  getTop?: () => Goal | undefined;
  emit?: any;
}) {
  return new SignalPool({
    bus: { emit: opts.emit ?? vi.fn(), on: vi.fn() } as any,
    classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
    verifier: { verify: opts.verify } as any,
    goalGraph: {
      getActive: () => (opts.getTop?.() ? [opts.getTop()!] : []),
      getTopPriority: opts.getTop ?? (() => undefined),
    } as any,
    config: { maxSignals: 32, consent: {} },
    workspacePath: "/tmp",
  });
}

describe("SignalPool stage 2 verifier", () => {
  beforeEach(() => vi.clearAllMocks());

  it("skips verifier when no active goal — userSurfaceable stays false", async () => {
    const verify = vi.fn();
    const pool = makePool({ verify });
    await pool.injectSignal(sig());
    expect(verify).not.toHaveBeenCalled();
    expect(pool.getState().signals[0].userSurfaceable).toBeFalsy();
  });

  it("marks userSurfaceable=true on ADVANCES verdict", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => ({
      verdict: "ADVANCES",
      reason: "edits in scope",
    }));
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    const s = pool.getState().signals[0];
    expect(s.userSurfaceable).toBe(true);
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:promoted" }),
    );
  });

  it("emits signal:suppressed on NEUTRAL verdict", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => ({
      verdict: "NEUTRAL",
      reason: "unrelated",
    }));
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    expect(pool.getState().signals[0].userSurfaceable).toBeFalsy();
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:suppressed" }),
    );
  });

  it("verifier throw → signal stays in pool, no userSurfaceable, no event", async () => {
    const emit = vi.fn();
    const verify = vi.fn(async () => {
      throw new Error("model down");
    });
    const pool = makePool({ verify, getTop: () => goal, emit });
    await pool.injectSignal(sig());
    const s = pool.getState().signals[0];
    expect(s).toBeDefined();
    expect(s.userSurfaceable).toBeFalsy();
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:emitted" }),
    );
    expect(emit).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:promoted" }),
    );
  });

  it("only triggers verifier on priority=high (medium signals admitted but not verified)", async () => {
    const verify = vi.fn();
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.8 }) },
      verifier: { verify } as any,
      goalGraph: {
        getActive: () => [goal],
        getTopPriority: () => goal,
      } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await pool.injectSignal(sig());
    expect(verify).not.toHaveBeenCalled();
    expect(pool.getState().signals[0].priority).toBe("medium");
  });
});
