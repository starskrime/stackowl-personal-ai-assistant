import { describe, it, expect, vi } from "vitest";
import { SignalPool } from "../../src/signals/pool.js";
import type { Goal } from "../../src/goals/types.js";

vi.mock("../../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn() } },
}));

const goal: Goal = {
  id: "g",
  title: "T",
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

function sig() {
  return {
    id: "s1",
    source: "git" as const,
    priority: "low" as const,
    title: "t",
    content: "c",
    timestamp: Date.now(),
    ttlMs: 60_000,
  };
}

describe("memory promotion on ADVANCES", () => {
  it("calls memoryRepo.insertBatch on signal:promoted with kind=reflexive", async () => {
    const insertBatch = vi.fn();
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: {
        verify: async () => ({ verdict: "ADVANCES", reason: "yes" }),
      } as any,
      goalGraph: {
        getActive: () => [goal],
        getTopPriority: () => goal,
      } as any,
      config: { maxSignals: 32, consent: {} },
      memoryRepo: { insertBatch } as any,
      workspacePath: "/tmp",
    });
    await pool.injectSignal(sig());
    expect(insertBatch).toHaveBeenCalled();
    const records = insertBatch.mock.calls[0][0];
    expect(records).toHaveLength(1);
    expect(records[0].kind).toBe("reflexive");
    expect(records[0].goal_id).toBe(goal.id);
    expect(records[0].verdict).toBe("ADVANCES");
    expect(records[0].content).toContain("[git]");
  });

  it("memory store throw does not block bus emission (fail-open)", async () => {
    const emit = vi.fn();
    const insertBatch = vi.fn(() => {
      throw new Error("disk full");
    });
    const pool = new SignalPool({
      bus: { emit, on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: {
        verify: async () => ({ verdict: "ADVANCES", reason: "yes" }),
      } as any,
      goalGraph: {
        getActive: () => [goal],
        getTopPriority: () => goal,
      } as any,
      config: { maxSignals: 32, consent: {} },
      memoryRepo: { insertBatch } as any,
      workspacePath: "/tmp",
    });
    await pool.injectSignal(sig());
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "signal:promoted" }),
    );
  });

  it("works without memoryRepo (no-op)", async () => {
    const pool = new SignalPool({
      bus: { emit: vi.fn(), on: vi.fn() } as any,
      classifier: { classify: async () => ({ keep: true, confidence: 0.95 }) },
      verifier: {
        verify: async () => ({ verdict: "ADVANCES", reason: "yes" }),
      } as any,
      goalGraph: {
        getActive: () => [goal],
        getTopPriority: () => goal,
      } as any,
      config: { maxSignals: 32, consent: {} },
      workspacePath: "/tmp",
    });
    await expect(pool.injectSignal(sig())).resolves.toBeUndefined();
  });
});
