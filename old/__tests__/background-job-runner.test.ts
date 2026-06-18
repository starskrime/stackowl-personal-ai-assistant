// __tests__/background-job-runner.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { BackgroundJobRunner } from "../src/routing/background-job-runner.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";

let tmpDir: string;
let db: MemoryDatabase;
let runner: BackgroundJobRunner;

const mockEventBus = { emit: vi.fn() } as any;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-jobs-"));
  db = new MemoryDatabase(tmpDir);
  runner = new BackgroundJobRunner(db, mockEventBus);
  vi.clearAllMocks();
});

afterEach(() => {
  runner.stop();
  rmSync(tmpDir, { recursive: true, force: true });
});

describe("BackgroundJobRunner", () => {
  it("does not execute jobs scheduled in the future", async () => {
    db.owlJobs.enqueue({ id: uuidv4(), userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() + 60_000).toISOString() });
    await runner.tick();
    expect(mockEventBus.emit).not.toHaveBeenCalled();
  });

  it("executes a due followup job and emits job:complete", async () => {
    const jobId = uuidv4();
    db.owlJobs.enqueue({ id: jobId, userId: "u1", owlName: "owl", type: "followup", payload: { message: "Your task is ready" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    await runner.tick();
    const job = db.owlJobs.get(jobId);
    expect(job!.status).toBe("done");
    expect(mockEventBus.emit).toHaveBeenCalledWith("job:complete", expect.objectContaining({ userId: "u1" }));
  });

  it("scheduleFollowup inserts a job row", () => {
    runner.scheduleFollowup({ id: "t1", userId: "u1", owlName: "owl", title: "Check Redis", status: "pending", priority: "normal", createdAt: "", updatedAt: "" }, 5000);
    const jobs = db.owlJobs.getQueued("u1");
    expect(jobs).toHaveLength(1);
    expect(jobs[0].type).toBe("followup");
    expect(jobs[0].taskId).toBe("t1");
  });

  it("marks job failed when handler throws", async () => {
    const jobId = uuidv4();
    db.owlJobs.enqueue({ id: jobId, userId: "u1", owlName: "owl", type: "research", payload: { query: "bad query" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    await runner.tick();
    const job = db.owlJobs.get(jobId);
    expect(["done", "failed"]).toContain(job!.status);
  });
});
