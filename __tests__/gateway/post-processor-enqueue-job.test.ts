// __tests__/gateway/post-processor-enqueue-job.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyAllMigrationsToRawDb } from "../../src/memory/db.js";
import { TaskQueue } from "../../src/queue/task-queue.js";

// Minimal GatewayContext stub for PostProcessor
function makeCtx(db?: InstanceType<typeof Database>) {
  return {
    owl: { persona: { name: "test-owl" }, dna: { evolvedTraits: {}, learnedPreferences: {}, evolutionLog: [] } },
    config: {},
    db: db ? { rawDb: db } : null,
  } as any;
}

describe("PostProcessor.enqueueJob()", () => {
  let rawDb: InstanceType<typeof Database>;

  beforeEach(() => {
    rawDb = new Database(":memory:");
    applyAllMigrationsToRawDb(rawDb);
  });

  it("records success row with correct tier in post_processor_job_runs", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = makeCtx(rawDb);
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    // Access private method via any cast for testing
    (pp as any)._lastProcessUserId = "user-1";
    (pp as any)._lastSessionId = "sess-1";
    await (pp as any).enqueueJobForTest("test-job", "critical", async () => {});
    await queue.drain();

    const rows = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE job_name='test-job'"
    ).all() as any[];
    expect(rows).toHaveLength(1);
    expect(rows[0].success).toBe(1);
    expect(rows[0].tier).toBe("critical");
    expect(rows[0].user_id).toBe("user-1");
  });

  it("records failure row with error_code when job throws", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const ctx = makeCtx(rawDb);
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    (pp as any)._lastProcessUserId = "user-2";
    (pp as any)._lastSessionId = "sess-2";
    await (pp as any).enqueueJobForTest("fail-job", "standard", async () => {
      throw new TypeError("oops");
    });
    await queue.drain();

    const rows = rawDb.prepare(
      "SELECT * FROM post_processor_job_runs WHERE job_name='fail-job'"
    ).all() as any[];
    expect(rows[0].success).toBe(0);
    expect(rows[0].error_code).toBe("TypeError");
  });

  it("maps tier 'critical' to TaskPriority 'high'", async () => {
    const { PostProcessor } = await import("../../src/gateway/handlers/post-processor.js");
    const queue = new TaskQueue();
    const enqueueSpy = vi.spyOn(queue, "enqueue");
    const ctx = makeCtx();
    const pp = new PostProcessor(ctx, queue, null, null, null, null);

    (pp as any).enqueueJobForTest("x", "critical", async () => {});
    expect(enqueueSpy).toHaveBeenCalledWith("x", expect.any(Function), "high");
  });
});
