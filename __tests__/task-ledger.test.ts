import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { TaskLedgerStore } from "../src/engine/task-ledger.js";
import { MemoryDatabase } from "../src/memory/db.js";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

let dir: string, db: MemoryDatabase, store: TaskLedgerStore;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "owl-ledger-"));
  db = new MemoryDatabase(dir);
  store = new TaskLedgerStore(db);
});
afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

describe("TaskLedgerStore", () => {
  it("saves and retrieves a ledger", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "research EVs",
      subGoals: [{ id: "sg1", description: "search", status: "pending", dependsOn: [] }],
      expectedOutput: "comparison table",
      complexity: "medium",
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: [],
    });
    await store.save(ledger);
    const loaded = await store.load(ledger.id);
    expect(loaded?.goal).toBe("research EVs");
    expect(loaded?.subGoals.length).toBe(1);
  });

  it("updates sub-goal status", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "test", subGoals: [{ id: "sg1", description: "step", status: "pending", dependsOn: [] }],
      expectedOutput: "", complexity: "simple", estimatedTurns: 1,
      behavioralConstraints: [], approachPatterns: [], revisions: [],
    });
    await store.save(ledger);
    await store.updateSubGoal(ledger.id, "sg1", "done", "result text");
    const updated = await store.load(ledger.id);
    expect(updated?.subGoals[0].status).toBe("done");
    expect(updated?.subGoals[0].result).toBe("result text");
  });

  it("addRevision appends to revisions array", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "test", subGoals: [],
      expectedOutput: "", complexity: "simple", estimatedTurns: 1,
      behavioralConstraints: [], approachPatterns: [], revisions: [],
    });
    await store.save(ledger);
    await store.addRevision(ledger.id, "stall detected", "test");
    const updated = await store.load(ledger.id);
    expect(updated?.revisions.length).toBe(1);
    expect(updated?.revisions[0].reason).toBe("stall detected");
  });

  it("round-trips estimatedTurns through save/load", async () => {
    const ledger = store.create("s1", "u1", {
      goal: "test", subGoals: [], expectedOutput: "", complexity: "simple",
      estimatedTurns: 12, behavioralConstraints: ["be brief"],
      approachPatterns: [], revisions: [],
    });
    await store.save(ledger);
    const loaded = await store.load(ledger.id);
    expect(loaded?.estimatedTurns).toBe(12);
    expect(loaded?.behavioralConstraints).toEqual(["be brief"]);
  });
});
