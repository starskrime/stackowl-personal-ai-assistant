// __tests__/gateway/post-processor-zombie-removal.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

function makeCtx(overrides: Record<string, unknown> = {}) {
  return {
    owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
    config: {},
    db: null,
    ...overrides,
  } as any;
}

describe("Zombie job removal", () => {
  it("timeline-snapshot is never enqueued", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx({ timelineManager: { autoSnapshot: vi.fn().mockReturnValue(true), save: vi.fn() } });
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      // Run 10 messages to cover interval conditions
      for (let i = 0; i < 10; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const calls = spy.mock.calls.map(c => c[0]);
      expect(calls).not.toContain("timeline-snapshot");
    });
  });

  it("goal-extraction is never enqueued", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx();
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      for (let i = 0; i < 5; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const calls = spy.mock.calls.map(c => c[0]);
      expect(calls).not.toContain("goal-extraction");
    });
  });

  it("setGoalExtractor method does not exist", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const pp = new PostProcessor(makeCtx(), queue, null, null, null, null);
    expect((pp as any).setGoalExtractor).toBeUndefined();
  });

  it("knowledge-extract fires at message 10 with 'low' priority", () => {
    vi.resetModules();
    return import("../../src/gateway/handlers/post-processor.js").then(({ PostProcessor }) => {
      const queue = new TaskQueue();
      const spy = vi.spyOn(queue, "enqueue");
      const ctx = makeCtx({
        knowledgeReasoner: { extractFromConversation: vi.fn().mockResolvedValue(undefined) },
        knowledgeGraph: { save: vi.fn().mockResolvedValue(undefined) },
      });
      const pp = new PostProcessor(ctx, queue, null, null, null, null);
      for (let i = 0; i < 10; i++) {
        pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
      }
      const kgCalls = spy.mock.calls.filter(c => c[0] === "knowledge-extract");
      expect(kgCalls.length).toBe(1);
      expect(kgCalls[0][2]).toBe("low");
    });
  });
});
