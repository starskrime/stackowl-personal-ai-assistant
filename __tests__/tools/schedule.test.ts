import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ScheduleStore } from "../../src/schedule/store.js";
import { ScheduleRunner } from "../../src/schedule/runner.js";
import { ScheduleTool, attachSchedule } from "../../src/tools/schedule.js";
import type { Notifier } from "../../src/platform/index.js";

let dir: string;
let runner: ScheduleRunner;
let store: ScheduleStore;

const stubNotifier: Notifier = {
  notify: async () => ({ delivered: true, via: "system" }),
  capabilities: () => ({ native: false, system: true }),
};

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-schedule-tool-"));
  const db = new MemoryDatabase(dir);
  store = new ScheduleStore(db);
  runner = new ScheduleRunner(store, stubNotifier);
  attachSchedule(runner, store);
});

afterEach(() => {
  runner.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("ScheduleTool", () => {
  it("tool name is 'schedule'", async () => {
    expect(ScheduleTool.definition.name).toBe("schedule");
  });

  it("action enum includes remind, repeat, cancel, list", async () => {
    const actionEnum = ScheduleTool.definition.parameters.properties.action.enum;
    expect(actionEnum).toContain("remind");
    expect(actionEnum).toContain("repeat");
    expect(actionEnum).toContain("cancel");
    expect(actionEnum).toContain("list");
  });

  it("remind action schedules a job and returns an id", async () => {
    const result = await ScheduleTool.execute(
      { action: "remind", when: "in 5 minutes", message: "Check the build" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(typeof parsed.data.id).toBe("string");
  });

  it("list returns scheduled jobs", async () => {
    await ScheduleTool.execute(
      { action: "remind", when: "in 10 minutes", message: "Test reminder" },
      { cwd: process.cwd() },
    );
    const result = await ScheduleTool.execute(
      { action: "list" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(Array.isArray(parsed.data.jobs)).toBe(true);
    expect(parsed.data.jobs.length).toBeGreaterThanOrEqual(1);
  });

  it("cancel removes a job", async () => {
    const scheduleResult = await ScheduleTool.execute(
      { action: "remind", when: "in 15 minutes", message: "To cancel" },
      { cwd: process.cwd() },
    );
    const { id } = JSON.parse(scheduleResult).data;
    const cancelResult = await ScheduleTool.execute(
      { action: "cancel", id },
      { cwd: process.cwd() },
    );
    expect(JSON.parse(cancelResult).success).toBe(true);
  });

  it("invalid when expression returns structured error", async () => {
    const result = await ScheduleTool.execute(
      { action: "remind", when: "not-a-time", message: "Bad time" },
      { cwd: process.cwd() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_TIME");
  });
});
