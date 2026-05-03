/**
 * CognitiveLoop — Task 10
 * goal_progress_update enqueue helper
 */

import { describe, it, expect, vi } from "vitest";

vi.mock("../src/logger.js", () => ({
  log: { engine: { info: vi.fn(), warn: vi.fn(), debug: vi.fn(), error: vi.fn() } },
}));

describe("CognitiveLoop goal_progress_update enqueue", () => {
  it("enqueues goal_progress_update via maybeEnqueueGoalUpdate helper", async () => {
    const scheduleGoalUpdate = vi.fn();
    const mockJobQueue = { schedule: scheduleGoalUpdate };

    const { CognitiveLoop } = await import("../src/cognition/loop.js");

    const loop = new CognitiveLoop(
      {
        provider: {
          name: "mock",
          chat: vi.fn().mockResolvedValue({ content: "", model: "m", finishReason: "stop" }),
          chatWithTools: vi.fn(),
          chatStream: vi.fn(),
          embed: vi.fn(),
          listModels: vi.fn(),
          healthCheck: vi.fn(),
        } as any,
        owl: { name: "test", dna: {} } as any,
        config: { workspace: "/tmp/test", cognition: {} } as any,
        jobQueue: mockJobQueue as any,
      } as any,
      {},
    );

    await (loop as any).maybeEnqueueGoalUpdate("g_123", "study session complete: TypeScript generics");
    expect(scheduleGoalUpdate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "goal_progress_update",
        payload: expect.objectContaining({ goalId: "g_123" }),
      }),
    );
  });

  it("is a no-op when jobQueue is not in deps", async () => {
    const { CognitiveLoop } = await import("../src/cognition/loop.js");

    const loop = new CognitiveLoop(
      {
        provider: {
          name: "mock",
          chat: vi.fn(),
          chatWithTools: vi.fn(),
          chatStream: vi.fn(),
          embed: vi.fn(),
          listModels: vi.fn(),
          healthCheck: vi.fn(),
        } as any,
        owl: { name: "test", dna: {} } as any,
        config: { workspace: "/tmp/test", cognition: {} } as any,
      } as any,
      {},
    );

    // Must not throw when jobQueue is missing
    await expect(
      (loop as any).maybeEnqueueGoalUpdate("g_456", "summary"),
    ).resolves.toBeUndefined();
  });
});
