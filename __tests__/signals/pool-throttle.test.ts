import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import { DEFAULT_CONSENT } from "../../src/ambient/types.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const PINNED_TIME = new Date("2025-01-01T00:00:00Z").getTime();

const goal: Goal = {
  id: "g1",
  title: "Ship it",
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

function makeSignal(id: string, priority: "medium" | "high" = "medium"): ContextSignal {
  return {
    id,
    source: "git",
    title: "Test signal",
    content: "some meaningful content that is long enough",
    priority,
    timestamp: Date.now(),
    ttlMs: 600_000,
    userSurfaceable: false,
  };
}

function makePool(verifier: { verify: ReturnType<typeof vi.fn> }) {
  const bus = { emit: vi.fn(), on: vi.fn() } as any;
  const classifier = {
    classify: vi.fn().mockResolvedValue({ keep: true, confidence: 0.8 }),
  };
  const goalGraph = {
    getActive: () => [goal],
    getTopPriority: vi.fn().mockReturnValue(goal),
  } as any;
  return new SignalPool({
    bus,
    classifier,
    verifier,
    goalGraph,
    config: { maxSignals: 50, consent: DEFAULT_CONSENT },
    workspacePath: "/tmp/test",
  });
}

describe("SignalPool heartbeat verifier throttle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-01-01T00:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("does not re-verify a signal within the cooldown window", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    // Mark as verified 1 second ago (within cooldown)
    (pool as any)._lastVerifiedAt = new Map([["s1", PINNED_TIME - 1_000]]);

    await (pool as any).heartbeatTick();

    expect(verifier.verify).not.toHaveBeenCalled();
  });

  it("re-verifies a signal after the cooldown has elapsed", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    // Last verified 11 minutes ago — past the 10-minute cooldown
    (pool as any)._lastVerifiedAt = new Map([["s1", PINNED_TIME - 11 * 60_000]]);

    await (pool as any).heartbeatTick();

    expect(verifier.verify).toHaveBeenCalledTimes(1);
  });

  it("verifies a signal with no prior verification record", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    (pool as any)._lastVerifiedAt = new Map(); // no record

    await (pool as any).heartbeatTick();

    expect(verifier.verify).toHaveBeenCalledTimes(1);
  });

  it("cleans up _lastVerifiedAt when a signal expires via TTL", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    // Signal that is already expired
    const expiredSignal = makeSignal("s_expired", "medium");
    expiredSignal.timestamp = PINNED_TIME - 700_000; // TTL is 600_000 ms
    (pool as any).signals.set("s_expired", expiredSignal);
    (pool as any)._lastVerifiedAt = new Map([["s_expired", PINNED_TIME - 5_000]]);

    await (pool as any).heartbeatTick();

    // Signal expired — verifier should not be called, map entry cleaned up
    expect(verifier.verify).not.toHaveBeenCalled();
    expect((pool as any)._lastVerifiedAt.has("s_expired")).toBe(false);
  });

  it("cleans up _lastVerifiedAt when a signal is evicted via enforceLimit", () => {
    const verifier = { verify: vi.fn() };
    // maxSignals = 2 so we can trigger eviction easily
    const bus = { emit: vi.fn(), on: vi.fn() } as any;
    const classifier = {
      classify: vi.fn().mockResolvedValue({ keep: true, confidence: 0.8 }),
    };
    const goalGraph = {
      getActive: () => [goal],
      getTopPriority: vi.fn().mockReturnValue(goal),
    } as any;
    const pool = new SignalPool({
      bus,
      classifier,
      verifier,
      goalGraph,
      config: { maxSignals: 2, consent: DEFAULT_CONSENT },
      workspacePath: "/tmp/test",
    });

    // Add 2 signals and record them in _lastVerifiedAt
    const s1 = makeSignal("evict1", "low");
    const s2 = makeSignal("evict2", "low");
    (pool as any).signals.set("evict1", s1);
    (pool as any).signals.set("evict2", s2);
    (pool as any)._lastVerifiedAt.set("evict1", PINNED_TIME - 1_000);
    (pool as any)._lastVerifiedAt.set("evict2", PINNED_TIME - 2_000);

    // Adding a third signal crosses maxSignals=2 and should evict the lowest-priority oldest one
    const s3 = makeSignal("evict3", "high");
    (pool as any).signals.set("evict3", s3);
    (pool as any).enforceLimit();

    // One signal should have been evicted; its _lastVerifiedAt entry must be gone
    const remaining = [...(pool as any).signals.keys()];
    const evictedIds = ["evict1", "evict2", "evict3"].filter(
      (id) => !remaining.includes(id),
    );
    expect(evictedIds).toHaveLength(1);
    expect((pool as any)._lastVerifiedAt.has(evictedIds[0])).toBe(false);
  });
});
