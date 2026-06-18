// __tests__/integration/post-processor-element8.test.ts
import { describe, it, expect, vi } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";
import { TaskQueue } from "../../src/queue/task-queue.js";
import { PostProcessor } from "../../src/gateway/handlers/post-processor.js";
import { KnowledgeGraph } from "../../src/knowledge/graph.js";
import { KnowledgeGraphLayer } from "../../src/context/layers/knowledge.js";
import { PredictiveContextLayer } from "../../src/context/layers/predictive.js";

function makeDb() {
  const db = new Database(":memory:");
  applyAllMigrationsToRawDb(db);
  return db;
}

describe("Element 8 integration", () => {
  it("failed job writes error_code row to post_processor_job_runs", async () => {
    const rawDb = makeDb();
    const queue = new TaskQueue();
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: { rawDb },
      digestManager: {
        update: vi.fn().mockRejectedValue(new RangeError("digest failed")),
      },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hello" }], "sess-x", { userId: "u-x" });
    await queue.drain();

    const failures = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE success=0"
    ).all() as any[];
    expect(failures.length).toBeGreaterThanOrEqual(1);
    const digestFailure = failures.find((r: any) => r.job_name === "digest-update");
    expect(digestFailure).toBeDefined();
    expect(digestFailure.error_code).toBe("RangeError");
  });

  it("CRITICAL job (digest-update) is enqueued with 'high' priority", () => {
    const queue = new TaskQueue();
    const spy = vi.spyOn(queue, "enqueue");
    const ctx = {
      owl: { persona: { name: "x" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
      config: {},
      db: null,
      digestManager: { update: vi.fn().mockResolvedValue(undefined) },
    } as any;
    const pp = new PostProcessor(ctx, queue, null, null, null, null);
    pp.process([{ role: "user", content: "hi" }], "sess", { userId: "u" });
    const digestCall = spy.mock.calls.find(c => c[0] === "digest-update");
    expect(digestCall![2]).toBe("high");
  });

  it("KnowledgeGraphLayer reads from deps.knowledgeGraph (not session cast)", async () => {
    const kg = new KnowledgeGraph("/tmp");
    kg.addNode({ title: "Node A", content: "About topic A", domain: "d", type: "concept", confidence: 0.9, tags: [] });
    const layer = new KnowledgeGraphLayer();
    const req = {
      session: { messages: [] },
      callbacks: {},
      continuityResult: null,
      digest: null,
      deps: { sessionStore: {} as any, config: {} as any, knowledgeGraph: kg },
    } as any;
    const result = await layer.build(req, { userMessage: "topic A", isConversational: false } as any, new Map());
    expect(result).toContain("Node A");
    expect(result).toContain("<knowledge_graph>");
  });

  it("PredictiveContextLayer returns predicted tasks", async () => {
    const layer = new PredictiveContextLayer();
    const mockQueue = {
      getReadyTasks: () => [
        { action: "Check standup", confidence: 0.85, status: "ready" },
      ],
    };
    const req = {
      session: { messages: [] },
      callbacks: {},
      continuityResult: null,
      digest: null,
      deps: { sessionStore: {} as any, config: {} as any, predictiveQueue: mockQueue },
    } as any;
    const result = await layer.build(req, { userMessage: "what next", isConversational: false } as any, new Map());
    expect(result).toContain("<predicted_next>");
    expect(result).toContain("Check standup");
  });

  it("drainCritical() resolves after high-priority tasks complete", async () => {
    const queue = new TaskQueue({ concurrency: 1 });
    const completed: string[] = [];
    queue.enqueue("high-job", async () => { completed.push("high"); }, "high");
    queue.enqueue("normal-job", async () => { completed.push("normal"); }, "normal");
    await queue.drainCritical();
    expect(completed).toContain("high");
    expect(completed).not.toContain("normal");
  });
});
