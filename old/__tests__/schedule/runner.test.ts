import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ScheduleStore } from "../../src/schedule/store.js";
import { ScheduleRunner } from "../../src/schedule/runner.js";
import type { Notifier } from "../../src/platform/index.js";

let dir: string;
let db: MemoryDatabase;
let store: ScheduleStore;
let notified: any[];
let notifier: Notifier;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-sched-runner-"));
  db = new MemoryDatabase(dir);
  store = new ScheduleStore(db);
  notified = [];
  notifier = {
    notify: async (opts) => { notified.push(opts); return { delivered: true, via: "system" }; },
    capabilities: () => ({ native: false, system: true }),
  };
});

afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("ScheduleRunner", () => {
  it("scheduleJob fires after delay", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "soon", type: "remind", message: "ping",
      nextFireAt: new Date(Date.now() + 50).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    await new Promise(r => setTimeout(r, 150));
    expect(notified.length).toBe(1);
    expect(notified[0].body).toBe("ping");
    runner.stop();
  });

  it("cancelJob clears the timer", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "to-cancel", type: "remind", message: "should-not-fire",
      nextFireAt: new Date(Date.now() + 100).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    const ok = runner.cancelJob("to-cancel");
    expect(ok).toBe(true);
    await new Promise(r => setTimeout(r, 200));
    expect(notified.length).toBe(0);
    runner.stop();
  });

  it("start() hydrates expired jobs (fires once with Missed indicator)", async () => {
    store.add({
      id: "expired", type: "remind", message: "old",
      scheduleAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      nextFireAt: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
      createdAt: new Date(Date.now() - 11 * 60 * 1000).toISOString(),
      status: "active", metadata: {},
    });
    const runner = new ScheduleRunner(store, notifier);
    await runner.start();
    expect(notified.length).toBe(1);
    expect(notified[0].body).toContain("Missed");
    expect(store.findOne("expired")?.status).toBe("expired");
    runner.stop();
  });

  it("start() schedules future jobs without firing", async () => {
    store.add({
      id: "future", type: "remind", message: "later",
      scheduleAt: new Date(Date.now() + 60_000).toISOString(),
      nextFireAt: new Date(Date.now() + 60_000).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    const runner = new ScheduleRunner(store, notifier);
    await runner.start();
    await new Promise(r => setTimeout(r, 100));
    expect(notified.length).toBe(0);
    runner.stop();
  });

  it("repeat jobs re-fire after intervalMs", async () => {
    const runner = new ScheduleRunner(store, notifier);
    runner.scheduleJob({
      id: "rep", type: "repeat", intervalMs: 50, message: "tick",
      nextFireAt: new Date(Date.now() + 50).toISOString(),
      createdAt: new Date().toISOString(), status: "active", metadata: {},
    });
    await new Promise(r => setTimeout(r, 200));
    expect(notified.length).toBeGreaterThanOrEqual(2);
    runner.stop();
  });
});
