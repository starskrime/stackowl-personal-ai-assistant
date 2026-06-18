import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ScheduleStore } from "../../src/schedule/store.js";
import type { ScheduledJob } from "../../src/schedule/types.js";

let dir: string;
let db: MemoryDatabase;
let store: ScheduleStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "stackowl-sched-store-"));
  db = new MemoryDatabase(dir);
  store = new ScheduleStore(db);
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

function makeJob(over: Partial<ScheduledJob> = {}): ScheduledJob {
  return {
    id: "j" + Math.random().toString(36).slice(2, 8),
    type: "remind",
    message: "test",
    scheduleAt: new Date(Date.now() + 60_000).toISOString(),
    nextFireAt: new Date(Date.now() + 60_000).toISOString(),
    createdAt: new Date().toISOString(),
    status: "active",
    metadata: {},
    ...over,
  };
}

describe("ScheduleStore", () => {
  it("add + list", () => {
    store.add(makeJob({ id: "a" }));
    const all = store.list();
    expect(all).toHaveLength(1);
    expect(all[0].id).toBe("a");
  });

  it("update patches fields", () => {
    store.add(makeJob({ id: "a" }));
    store.update("a", { status: "fired" });
    expect(store.list()[0].status).toBe("fired");
  });

  it("remove deletes", () => {
    store.add(makeJob({ id: "a" }));
    store.remove("a");
    expect(store.list()).toHaveLength(0);
  });

  it("list filter by status", () => {
    store.add(makeJob({ id: "a", status: "active" }));
    store.add(makeJob({ id: "b", status: "fired" }));
    expect(store.list({ status: "active" })).toHaveLength(1);
  });

  it("due() returns past-due active jobs", () => {
    store.add(makeJob({ id: "past", nextFireAt: new Date(Date.now() - 1000).toISOString(), status: "active" }));
    store.add(makeJob({ id: "future", nextFireAt: new Date(Date.now() + 60_000).toISOString(), status: "active" }));
    const due = store.due(new Date());
    expect(due).toHaveLength(1);
    expect(due[0].id).toBe("past");
  });

  it("survives database close/reopen", () => {
    store.add(makeJob({ id: "persist" }));
    const db2 = new MemoryDatabase(dir);
    const store2 = new ScheduleStore(db2);
    expect(store2.list().some((j) => j.id === "persist")).toBe(true);
  });
});
