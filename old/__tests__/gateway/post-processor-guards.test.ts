// __tests__/gateway/post-processor-guards.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("PostProcessor synchronous call guards", () => {
  it("process() completes even when coordinator.processMessage throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const crashCoordinator = {
      processMessage: () => { throw new Error("coordinator crash"); },
      save: vi.fn(),
      getMicroLearnerProfile: vi.fn().mockReturnValue({}),
      gateEvolution: vi.fn().mockReturnValue(null),
      flushHighConfidencePrefs: vi.fn().mockReturnValue([]),
      recordMutationStart: vi.fn(),
      recordMutationEnd: vi.fn(),
    } as any;
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, crashCoordinator, null, null);
    // Must not throw
    expect(() =>
      pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" })
    ).not.toThrow();
  });

  it("process() completes even when sentimentProbe throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    // Corrupt sentimentProbe to throw
    (pp as any).sentimentProbe = { onNextMessage: () => { throw new Error("probe crash"); }, arm: () => {} };
    expect(() =>
      pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" })
    ).not.toThrow();
  });

  it("PostProcessor constructs without crash when ctx.db is null", async () => {
    // Verifies Decision 8: bare ! assertion removed from sentimentProbe callback
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
    } as any;
    expect(() => new PostProcessor(ctx, queue, null, null, null, null)).not.toThrow();
  });
});
