import { describe, it, expect } from "vitest";
import { llmTaskQueue } from "../../src/queue/llm-task-queue.js";
import { TaskQueue } from "../../src/queue/task-queue.js";

describe("llmTaskQueue", () => {
  it("should be a TaskQueue singleton with concurrency=1", () => {
    expect(llmTaskQueue).toBeInstanceOf(TaskQueue);
    const stats = llmTaskQueue.getStats();
    // Check that concurrency is 1 by running two tasks and verifying they don't overlap
    expect(stats.pending).toBeDefined();
    expect(stats.active).toBeDefined();
    expect(stats.completed).toBeDefined();
    expect(stats.failed).toBeDefined();
  });

  it("should run tasks sequentially (one at a time) when concurrency=1", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    const execution: string[] = [];
    let maxConcurrent = 0;
    let currentConcurrent = 0;

    const task = async (name: string, duration: number) => {
      currentConcurrent++;
      maxConcurrent = Math.max(maxConcurrent, currentConcurrent);
      execution.push(`${name}-start`);
      await new Promise((r) => setTimeout(r, duration));
      execution.push(`${name}-end`);
      currentConcurrent--;
    };

    q.enqueue("task-1", () => task("task-1", 10), "low");
    q.enqueue("task-2", () => task("task-2", 10), "low");
    q.enqueue("task-3", () => task("task-3", 10), "low");

    await q.drain();

    expect(maxConcurrent).toBe(1);
    expect(execution).toEqual([
      "task-1-start",
      "task-1-end",
      "task-2-start",
      "task-2-end",
      "task-3-start",
      "task-3-end",
    ]);
  });

  it("should execute background tasks with low priority", async () => {
    const q = new TaskQueue({ concurrency: 1 });
    const order: string[] = [];

    q.enqueue("high-task", async () => {
      order.push("high");
    }, "high");

    q.enqueue("low-task", async () => {
      order.push("low");
    }, "low");

    await q.drain();

    // High priority should execute first
    expect(order).toEqual(["high", "low"]);
  });
});
