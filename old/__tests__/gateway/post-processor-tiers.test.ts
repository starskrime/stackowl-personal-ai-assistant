// __tests__/gateway/post-processor-tiers.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("PostProcessor job tier assignments", () => {
  it("digest-update enqueued with 'high' priority", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
      digestManager: { update: vi.fn().mockResolvedValue(undefined) },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess-1", { userId: "u1" });
    const digestCall = spy.mock.calls.find(c => c[0] === "digest-update");
    expect(digestCall).toBeDefined();
    expect(digestCall![2]).toBe("high");
  });

  it("dna-evolve enqueued with 'low' priority when interval fires", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const mockEvolutionEngine = { evolve: vi.fn().mockResolvedValue(true) };
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: { owlDna: { evolutionBatchSize: 1 } }, // fires on message 1
      db: null,
      evolutionEngine: mockEvolutionEngine,
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess-1", { userId: "u1" });
    const evolveCall = spy.mock.calls.find(c => c[0].startsWith("dna-evolve"));
    expect(evolveCall).toBeDefined();
    expect(evolveCall![2]).toBe("low");
  });
});
