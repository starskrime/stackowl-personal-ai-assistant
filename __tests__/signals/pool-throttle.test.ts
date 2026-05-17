import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import { DEFAULT_CONSENT } from "../../src/ambient/types.js";
import type { ContextSignal } from "../../src/ambient/types.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

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
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it("does not re-verify a signal within the cooldown window", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    // Mark as verified 1 second ago (within cooldown)
    (pool as any)._lastVerifiedAt = new Map([["s1", Date.now() - 1_000]]);

    await (pool as any).heartbeatTick();

    expect(verifier.verify).not.toHaveBeenCalled();
  });

  it("re-verifies a signal after the cooldown has elapsed", async () => {
    const verifier = { verify: vi.fn().mockResolvedValue({ verdict: "NEUTRAL" }) };
    const pool = makePool(verifier);

    const signal = makeSignal("s1", "medium");
    (pool as any).signals.set("s1", signal);
    // Last verified 11 minutes ago — past the 10-minute cooldown
    (pool as any)._lastVerifiedAt = new Map([["s1", Date.now() - 11 * 60_000]]);

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
    expiredSignal.timestamp = Date.now() - 700_000; // TTL is 600_000 ms
    (pool as any).signals.set("s_expired", expiredSignal);
    (pool as any)._lastVerifiedAt = new Map([["s_expired", Date.now() - 5_000]]);

    await (pool as any).heartbeatTick();

    // Signal expired — verifier should not be called, map entry cleaned up
    expect(verifier.verify).not.toHaveBeenCalled();
    expect((pool as any)._lastVerifiedAt.has("s_expired")).toBe(false);
  });
});
