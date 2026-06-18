import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";

let tmpDir: string;
let db: MemoryDatabase;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "owl-db-"));
  db = new MemoryDatabase(tmpDir);
});

afterEach(() => { rmSync(tmpDir, { recursive: true, force: true }); });

describe("UserProfilesRepo", () => {
  it("getPin returns null for unknown user", () => {
    expect(db.userProfiles.getPin("u1")).toBeNull();
  });

  it("setPin and getPin round-trip", () => {
    db.userProfiles.setPin("u1", "typescript-owl");
    expect(db.userProfiles.getPin("u1")).toBe("typescript-owl");
  });

  it("setPin(null) clears pin", () => {
    db.userProfiles.setPin("u1", "typescript-owl");
    db.userProfiles.setPin("u1", null);
    expect(db.userProfiles.getPin("u1")).toBeNull();
  });

  it("appendRoutingHistory keeps last 10 entries", () => {
    for (let i = 0; i < 12; i++) {
      db.userProfiles.appendRoutingHistory("u1", { ts: new Date().toISOString(), owl: `owl${i}`, reason: "test" });
    }
    const history = db.userProfiles.getRoutingHistory("u1");
    expect(history).toHaveLength(10);
    expect(history[0].owl).toBe("owl2");
    expect(history[9].owl).toBe("owl11");
  });
});

describe("TasksRepo", () => {
  it("creates and retrieves a task", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "Do X", status: "pending", priority: "normal" });
    const t = db.owlTasks.get("t1");
    expect(t).not.toBeNull();
    expect(t!.title).toBe("Do X");
  });

  it("getActive returns only pending/active/blocked tasks", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "A", status: "pending", priority: "normal" });
    db.owlTasks.create({ id: "t2", userId: "u1", owlName: "owl", title: "B", status: "done", priority: "normal" });
    db.owlTasks.create({ id: "t3", userId: "u1", owlName: "owl", title: "C", status: "active", priority: "high" });
    const active = db.owlTasks.getActive("u1");
    expect(active.map(t => t.id).sort()).toEqual(["t1", "t3"]);
  });

  it("updateStatus changes task status", () => {
    db.owlTasks.create({ id: "t1", userId: "u1", owlName: "owl", title: "A", status: "pending", priority: "normal" });
    db.owlTasks.updateStatus("t1", "done", "result text");
    expect(db.owlTasks.get("t1")!.status).toBe("done");
    expect(db.owlTasks.get("t1")!.result).toBe("result text");
  });
});

describe("JobsRepo", () => {
  it("enqueue and dequeueNext round-trip", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: { msg: "check" }, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    const job = db.owlJobs.dequeueNext();
    expect(job).not.toBeNull();
    expect(job!.id).toBe("j1");
    expect(job!.status).toBe("running");
  });

  it("dequeueNext returns null when no jobs are due", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() + 60_000).toISOString() });
    expect(db.owlJobs.dequeueNext()).toBeNull();
  });

  it("markDone updates status and result", () => {
    db.owlJobs.enqueue({ id: "j1", userId: "u1", owlName: "owl", type: "followup", payload: {}, scheduledAt: new Date(Date.now() - 1000).toISOString() });
    db.owlJobs.dequeueNext();
    db.owlJobs.markDone("j1", "done result");
    const row = db.owlJobs.get("j1");
    expect(row!.status).toBe("done");
    expect(row!.result).toBe("done result");
  });
});
