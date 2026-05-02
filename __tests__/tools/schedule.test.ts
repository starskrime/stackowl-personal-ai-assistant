// __tests__/tools/schedule.test.ts
import { describe, it, expect, afterEach, vi } from "vitest";

describe("ScheduleTool", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("tool name is 'schedule'", async () => {
    const mod = await import("../../src/tools/schedule.js");
    expect(mod.ScheduleTool.definition.name).toBe("schedule");
  });

  it("action enum includes remind, repeat, cancel, list", async () => {
    const mod = await import("../../src/tools/schedule.js");
    const actionEnum = mod.ScheduleTool.definition.parameters.properties.action.enum;
    expect(actionEnum).toContain("remind");
    expect(actionEnum).toContain("repeat");
    expect(actionEnum).toContain("cancel");
    expect(actionEnum).toContain("list");
  });

  it("remind action schedules a job and returns an id", async () => {
    vi.useFakeTimers();
    const mod = await import("../../src/tools/schedule.js");
    const result = await mod.ScheduleTool.execute(
      { action: "remind", when: "in 5 minutes", message: "Check the build" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(typeof parsed.data.id).toBe("string");
    vi.useRealTimers();
  });

  it("list returns scheduled jobs", async () => {
    vi.useFakeTimers();
    const mod = await import("../../src/tools/schedule.js");
    await mod.ScheduleTool.execute(
      { action: "remind", when: "in 10 minutes", message: "Test reminder" },
      { cwd: process.cwd() },
    );
    const result = await mod.ScheduleTool.execute(
      { action: "list" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(Array.isArray(parsed.data.jobs)).toBe(true);
    vi.useRealTimers();
  });

  it("cancel removes a job", async () => {
    vi.useFakeTimers();
    const mod = await import("../../src/tools/schedule.js");
    const scheduleResult = await mod.ScheduleTool.execute(
      { action: "remind", when: "in 15 minutes", message: "To cancel" },
      { cwd: process.cwd() },
    );
    const { id } = JSON.parse(scheduleResult).data;
    const cancelResult = await mod.ScheduleTool.execute(
      { action: "cancel", id },
      { cwd: process.cwd() },
    );
    expect(JSON.parse(cancelResult).success).toBe(true);
    vi.useRealTimers();
  });

  it("invalid when expression returns structured error", async () => {
    const mod = await import("../../src/tools/schedule.js");
    const result = await mod.ScheduleTool.execute(
      { action: "remind", when: "not-a-time", message: "Bad time" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_TIME");
  });
});
