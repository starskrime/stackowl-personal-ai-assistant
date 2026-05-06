// __tests__/post-processor-critique.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { MemoryDatabase } from "../src/memory/db.js";
import { PostProcessor } from "../src/gateway/handlers/post-processor.js";
import type { GatewayContext } from "../src/gateway/types.js";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-critique-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function makeMockCtx(
  provider: any,
  trajectoryGetterOverride?: () => any[],
): Partial<GatewayContext> {
  return {
    db: {
      ...db,
      trajectories: {
        ...db.trajectories,
        getSessionFailures: trajectoryGetterOverride
          ? trajectoryGetterOverride
          : () => [],
      },
      owlLearnings: {
        ...db.owlLearnings,
        admitIfWorthy: vi.fn().mockReturnValue({ id: "test_id" }),
      },
    } as any,
    provider: provider,
    owl: { persona: { name: "test-owl" }, dna: {} } as any,
    config: {} as any,
  };
}

function makeTaskQueue() {
  // post-processor calls: this.taskQueue.enqueue(name, fn, priority)
  const jobs: Array<() => Promise<void>> = [];
  return {
    enqueue: (_name: string, fn: () => Promise<void>, _priority: any) => {
      jobs.push(fn);
    },
    flush: async () => {
      for (const fn of jobs) await fn();
      jobs.length = 0;
    },
  };
}

describe("PostProcessor failure critique job", () => {
  it("does not call provider when no BLOCKED/PARTIAL turns", async () => {
    const mockProvider = { chat: vi.fn() };
    const ctx = makeMockCtx(mockProvider);
    const queue = makeTaskQueue();
    // Constructor: (ctx, taskQueue, eventBus, coordinator, anticipator, costTracker, innerLifeBridge?)
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess1", { owlName: "test-owl" });
    await queue.flush();
    expect(mockProvider.chat).not.toHaveBeenCalled();
  });

  it("calls provider once when one BLOCKED turn exists", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Sentence 1. Sentence 2.",
        model: "m",
        finishReason: "stop",
      }),
    };
    const turns = [
      { tool_name: "web_fetch", verification_result: "BLOCKED", verifier_reason: "bot detection" },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess2", { owlName: "test-owl" });
    await queue.flush();
    expect(mockProvider.chat).toHaveBeenCalledOnce();
    expect((ctx.db as any).owlLearnings.admitIfWorthy).toHaveBeenCalledOnce();
  });

  it("handles admitIfWorthy returning null without error", async () => {
    const mockProvider = {
      chat: vi.fn().mockResolvedValue({
        content: "Critique text.",
        model: "m",
        finishReason: "stop",
      }),
    };
    const turns = [
      { tool_name: "web_fetch", verification_result: "BLOCKED", verifier_reason: null },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    (ctx.db as any).owlLearnings.admitIfWorthy = vi.fn().mockReturnValue(null);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess3", { owlName: "test-owl" });
    await expect(queue.flush()).resolves.not.toThrow();
  });

  it("catches provider throw and completes job without rethrowing", async () => {
    const mockProvider = {
      chat: vi.fn().mockRejectedValue(new Error("model unavailable")),
    };
    const turns = [
      { tool_name: "shell", verification_result: "PARTIAL", verifier_reason: "exit code 1" },
    ];
    const ctx = makeMockCtx(mockProvider, () => turns);
    const queue = makeTaskQueue();
    const pp = new PostProcessor(
      ctx as any,
      queue as any,
      null,
      null,
      null,
      null,
    );
    pp.process([], "sess4", { owlName: "test-owl" });
    await expect(queue.flush()).resolves.not.toThrow();
  });
});
