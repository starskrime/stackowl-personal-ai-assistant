import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";
import { TaskLedgerStore } from "../../src/engine/task-ledger.js";

// TaskLedgerStore expects a MemoryDatabase (with a rawDb getter).
// Wrap the raw better-sqlite3 instance to satisfy the interface.
function makeMemoryDb(raw: InstanceType<typeof Database>) {
  return { rawDb: raw } as any;
}

describe("TaskLedgerStore persistence", () => {
  let db: InstanceType<typeof Database>;
  let store: TaskLedgerStore;

  beforeEach(() => {
    db = new Database(":memory:");
    applyMigrations(db);
    store = new TaskLedgerStore(makeMemoryDb(db));
  });

  afterEach(() => db.close());

  it("persistSubgoal writes row to owl_task_ledger", async () => {
    await store.persistSubgoal({
      id: "ledger-1",
      sessionId: "s1",
      userId: "u1",
      taskId: "task-1",
      subgoalIndex: 0,
      subgoalText: "Search for TypeScript docs",
      stateJson: JSON.stringify({ tools: [] }),
      status: "in_progress",
      attemptCount: 1,
    });
    const row = db.prepare("SELECT * FROM owl_task_ledger WHERE id = 'ledger-1'").get() as any;
    expect(row).toBeDefined();
    expect(row.subgoal_text).toBe("Search for TypeScript docs");
  });

  it("loadIncomplete returns in_progress tasks for user", async () => {
    await store.persistSubgoal({
      id: "ledger-2",
      sessionId: "s2",
      userId: "u2",
      taskId: "task-2",
      subgoalIndex: 1,
      subgoalText: "Fetch results",
      stateJson: "{}",
      status: "in_progress",
      attemptCount: 1,
    });
    const result = await store.loadIncomplete("u2");
    expect(result).not.toBeNull();
    expect(result!.subgoalText).toBe("Fetch results");
  });

  it("loadIncomplete returns null when no incomplete tasks", async () => {
    const result = await store.loadIncomplete("no-such-user");
    expect(result).toBeNull();
  });
});
