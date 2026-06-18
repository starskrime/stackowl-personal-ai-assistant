// __tests__/idle-engine-orchestrator.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  IdleActivityEngine,
  type IdleEngineCallbacks,
} from "../src/heartbeat/idle-engine.js";
import type { MemoryDatabase } from "../src/memory/db.js";
import type { LearningOrchestrator } from "../src/learning/orchestrator.js";

function makeMockOrchestrator(): LearningOrchestrator {
  return {
    runProactiveSession: vi.fn().mockResolvedValue({
      topicsPrioritized: 2,
      topicsStudied: 2,
      trigger: "scheduled",
      startedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
    }),
  } as any;
}

function makeMockDb(topics: string[] = []): MemoryDatabase {
  return {
    trajectories: {
      getFailureDensityTopics: vi.fn().mockReturnValue(topics),
    },
  } as any;
}

describe("IdleActivityEngine — runAnticipatoryResearch", () => {
  it("calls getFailureDensityTopics(7, 2) then runProactiveSession with those topics", async () => {
    const orch = makeMockOrchestrator();
    const db = makeMockDb(["web_fetch"]);
    const results: any[] = [];
    const callbacks: IdleEngineCallbacks = {
      onResult: (r) => results.push(r),
      learningOrchestrator: orch,
      db,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    // Force isIdle = true by setting lastUserActivity to long ago
    (engine as any).lastUserActivity = Date.now() - 999_999_999;

    await (engine as any).runAnticipatoryResearch();

    expect(db.trajectories.getFailureDensityTopics).toHaveBeenCalledWith(7, 2);
    expect(orch.runProactiveSession).toHaveBeenCalledWith({
      failureDensityTopics: ["web_fetch"],
      maxTopics: 3,
    });
  });

  it("passes empty failureDensityTopics when db is missing", async () => {
    const orch = makeMockOrchestrator();
    const callbacks: IdleEngineCallbacks = {
      onResult: () => {},
      learningOrchestrator: orch,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    await (engine as any).runAnticipatoryResearch();
    expect(orch.runProactiveSession).toHaveBeenCalledWith({
      failureDensityTopics: [],
      maxTopics: 3,
    });
  });

  it("returns success:false when learningOrchestrator is missing", async () => {
    const callbacks: IdleEngineCallbacks = { onResult: () => {} };
    const engine = new IdleActivityEngine({} as any, callbacks);
    const result = await (engine as any).runAnticipatoryResearch();
    expect(result.success).toBe(false);
  });
});

describe("IdleActivityEngine — runKnowledgeRefresh", () => {
  it("calls runProactiveSession with maxTopics:1 and no DB query", async () => {
    const orch = makeMockOrchestrator();
    const db = makeMockDb();
    const callbacks: IdleEngineCallbacks = {
      onResult: () => {},
      learningOrchestrator: orch,
      db,
    };
    const engine = new IdleActivityEngine({} as any, callbacks);
    await (engine as any).runKnowledgeRefresh();
    expect(orch.runProactiveSession).toHaveBeenCalledWith({ maxTopics: 1 });
    expect(db.trajectories.getFailureDensityTopics).not.toHaveBeenCalled();
  });

  it("returns success:false when learningOrchestrator is missing", async () => {
    const callbacks: IdleEngineCallbacks = { onResult: () => {} };
    const engine = new IdleActivityEngine({} as any, callbacks);
    const result = await (engine as any).runKnowledgeRefresh();
    expect(result.success).toBe(false);
  });
});
