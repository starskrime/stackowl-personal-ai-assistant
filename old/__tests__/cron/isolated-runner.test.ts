import { describe, it, expect, vi } from "vitest";
import { IsolatedRunner } from "../../src/cron/isolated-runner.js";
import type { CronJob } from "../../src/cron/types.js";

const LOW_JOB: CronJob = {
  id: "memory-consolidation",
  schedule: "0 * * * *",
  prompt: "Consolidate recent episodic memories",
  safetyProfile: "low",
  deliver: false,
};

describe("IsolatedRunner", () => {
  it("creates a runner without crashing", () => {
    const fakeProvider = { chat: vi.fn().mockResolvedValue({ content: "done" }) } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider });
    expect(runner).toBeTruthy();
  });

  it("run() calls provider.chat with job prompt and returns string", async () => {
    const fakeProvider = {
      chat: vi.fn().mockResolvedValue({ content: "Memory consolidated: 5 episodes." }),
    } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider });

    const result = await runner.run(LOW_JOB, "trace-abc");

    expect(fakeProvider.chat).toHaveBeenCalledOnce();
    expect(typeof result).toBe("string");
    expect(result).toContain("Memory consolidated");
  });

  it("returns error message string on provider failure — does not throw", async () => {
    const fakeProvider = {
      chat: vi.fn().mockRejectedValue(new Error("rate limit")),
    } as any;
    const runner = new IsolatedRunner({ provider: fakeProvider });

    const result = await runner.run(LOW_JOB, "trace-fail");
    expect(result).toMatch(/error|failed|rate limit/i);
  });
});
