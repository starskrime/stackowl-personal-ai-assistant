import { describe, it, expect, vi, beforeEach } from "vitest";
import { PostProcessor } from "../../src/gateway/handlers/post-processor.js";
import type { GatewayContext } from "../../src/gateway/types.js";
import type { TaskQueue } from "../../src/queue/task-queue.js";

function makeTaskQueue() {
  const enqueuedNames: string[] = [];
  return {
    enqueue: vi.fn((name: string, _fn: () => Promise<unknown>, _priority?: string) => {
      enqueuedNames.push(name);
      return `task-${Date.now()}`;
    }),
    enqueuedNames,
  };
}

function makeCtx(avgReward: number, lastEvolvedHoursAgo: number): GatewayContext {
  const lastEvolved = new Date(Date.now() - lastEvolvedHoursAgo * 60 * 60 * 1000).toISOString();
  const trajectories = Array.from({ length: 5 }, () => ({ reward: avgReward }));
  return {
    owl: {
      persona: { name: "test-owl" },
      dna: { lastEvolved, evolutionLog: [], learnedPreferences: {} },
    },
    config: {
      owlDna: { evolutionBatchSize: 10 },
    },
    db: {
      trajectories: {
        getRecent: vi.fn().mockReturnValue(trajectories),
        getSessionFailures: vi.fn().mockReturnValue([]),
      },
      owlPerf: {
        record: vi.fn(),
      },
      rawDb: {
        prepare: vi.fn().mockReturnValue({ run: vi.fn() }),
      },
    },
    evolutionEngine: {
      evolve: vi.fn().mockResolvedValue(true),
    },
    provider: {
      chat: vi.fn().mockResolvedValue({ content: "ok", model: "test", usage: undefined }),
    },
  } as unknown as GatewayContext;
}

const messages = [{ role: "user" as const, content: "test message" }];

describe("PostProcessor — mid-session evolution trigger (D4)", () => {
  it("enqueues mid-session-evolution after 5 messages when avg reward < -0.2 and evolved > 2h ago", () => {
    const ctx = makeCtx(-0.5, 3);
    const tq = makeTaskQueue();
    const processor = new PostProcessor(
      ctx,
      tq as unknown as TaskQueue,
      null,  // eventBus
      null,  // coordinator
      null,  // anticipator
      null,  // costTracker
    );

    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(tq.enqueuedNames).toContain("mid-session-evolution");
  });

  it("does NOT enqueue mid-session-evolution when avgReward >= -0.2 (even if evolved > 2h ago)", () => {
    const ctx = makeCtx(0.1, 3);  // good reward
    const tq = makeTaskQueue();
    const processor = new PostProcessor(
      ctx,
      tq as unknown as TaskQueue,
      null,
      null,
      null,
      null,
    );

    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(tq.enqueuedNames).not.toContain("mid-session-evolution");
  });

  it("does NOT enqueue mid-session-evolution when lastEvolved < 2h ago (even if reward is bad)", () => {
    const ctx = makeCtx(-0.5, 1);  // only 1 hour ago
    const tq = makeTaskQueue();
    const processor = new PostProcessor(
      ctx,
      tq as unknown as TaskQueue,
      null,
      null,
      null,
      null,
    );

    for (let i = 0; i < 5; i++) {
      processor.process(messages, "sess-001", { owlName: "test-owl" });
    }

    expect(tq.enqueuedNames).not.toContain("mid-session-evolution");
  });
});
