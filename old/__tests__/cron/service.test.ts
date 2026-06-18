import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { CronService } from "../../src/cron/service.js";
import type { CronJob } from "../../src/cron/types.js";

const SAMPLE_JOB: CronJob = {
  id: "test-job",
  schedule: "* * * * *",
  prompt: "Do something useful",
  safetyProfile: "low",
  deliver: false,
};

describe("CronService", () => {
  let service: CronService;

  beforeEach(() => {
    service = new CronService({ persist: false });
  });

  afterEach(() => {
    service.stop();
  });

  it("registers a job and lists it", () => {
    service.addJob(SAMPLE_JOB);
    const jobs = service.listJobs();
    expect(jobs).toHaveLength(1);
    expect(jobs[0].id).toBe("test-job");
  });

  it("removes a job by id", () => {
    service.addJob(SAMPLE_JOB);
    service.removeJob("test-job");
    expect(service.listJobs()).toHaveLength(0);
  });

  it("rejects duplicate job ids", () => {
    service.addJob(SAMPLE_JOB);
    expect(() => service.addJob(SAMPLE_JOB)).toThrow(/already registered/i);
  });

  it("rejects an invalid cron expression", () => {
    expect(() =>
      service.addJob({ ...SAMPLE_JOB, id: "bad", schedule: "not-a-cron" }),
    ).toThrow(/invalid.*schedule/i);
  });

  it("tracks job state as pending initially", () => {
    service.addJob(SAMPLE_JOB);
    const state = service.getJobState("test-job");
    expect(state?.status).toBe("pending");
    expect(state?.lastRunAt).toBeNull();
  });

  it("reports nextRunAt as a future date", () => {
    service.addJob(SAMPLE_JOB);
    const state = service.getJobState("test-job");
    expect(state?.nextRunAt).toBeInstanceOf(Date);
    expect(state!.nextRunAt!.getTime()).toBeGreaterThan(Date.now());
  });

  it("respects maxConcurrentRuns — does not start a 4th job when max is 3", () => {
    const service3 = new CronService({ persist: false, maxConcurrentRuns: 3 });
    (service3 as any).runningCount = 3;
    expect((service3 as any).canStartJob()).toBe(false);
    service3.stop();
  });
});
