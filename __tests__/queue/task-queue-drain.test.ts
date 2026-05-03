// __tests__/queue/task-queue-drain.test.ts
import { describe, it, expect, vi } from "vitest";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("TaskQueue.drainCritical()", () => {
  it("resolves immediately when queue is empty", async () => {
    const q = new TaskQueue();
    await expect(q.drainCritical()).resolves.toBeUndefined();
  });

  it("resolves after all high-priority tasks complete", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    const order: string[] = [];
    q.enqueue("high-1", async () => { order.push("high-1"); }, "high");
    q.enqueue("high-2", async () => { order.push("high-2"); }, "high");
    await q.drainCritical();
    expect(order).toContain("high-1");
    expect(order).toContain("high-2");
  });

  it("does not wait for normal-priority tasks", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    let normalDone = false;
    q.enqueue("high-1", async () => {}, "high");
    q.enqueue("normal-1", async () => { normalDone = true; }, "normal");
    await q.drainCritical();
    // drainCritical must return as soon as high tasks are done,
    // not waiting for normal tasks
    expect(normalDone).toBe(false);
  });
});
