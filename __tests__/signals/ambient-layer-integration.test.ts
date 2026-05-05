import { describe, it, expect, vi } from "vitest";
import { AmbientContextLayer } from "../../src/context/layers/ambient.js";
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

function makePool() {
  return new SignalPool({
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
}

describe("AmbientContextLayer integration with SignalPool", () => {
  it("priority is 145 and maxTokens is 400", () => {
    const pool = makePool();
    const layer = new AmbientContextLayer(pool);
    expect(layer.priority).toBe(145);
    expect(layer.maxTokens).toBe(400);
  });

  it("shouldFire is false when conversational", () => {
    const layer = new AmbientContextLayer(makePool());
    expect(layer.shouldFire({ isConversational: true } as any)).toBe(false);
  });

  it("shouldFire is false when no high-priority surfaceable signals", () => {
    const layer = new AmbientContextLayer(makePool());
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(false);
  });

  it("shouldFire is true when SignalPool has high-priority surfaceable signal", async () => {
    const pool = makePool();
    await pool.injectSignal({
      id: "s",
      source: "git",
      priority: "low",
      title: "t",
      content: "c",
      timestamp: Date.now(),
      ttlMs: 60_000,
    });
    const layer = new AmbientContextLayer(pool);
    expect(layer.shouldFire({ isConversational: false } as any)).toBe(true);
  });

  it("build returns <ambient_context> wrapper with surfaceable signals", async () => {
    const pool = makePool();
    await pool.injectSignal({
      id: "s",
      source: "git",
      priority: "low",
      title: "12 uncommitted files",
      content: "c",
      timestamp: Date.now(),
      ttlMs: 60_000,
    });
    const layer = new AmbientContextLayer(pool);
    const out = await layer.build({} as any, {} as any, {} as any);
    expect(out).toContain("<ambient_context");
    expect(out).toContain("12 uncommitted files");
  });
});
