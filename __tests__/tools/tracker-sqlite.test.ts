import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../../src/memory/db.js";
import { ToolTracker } from "../../src/tools/tracker.js";

describe("ToolTracker — SQLite-backed", () => {
  let dir: string;
  let db: MemoryDatabase;
  let tracker: ToolTracker;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "tracker-sqlite-"));
    db = new MemoryDatabase(dir);
    tracker = new ToolTracker(db);
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("records success and queries via getStats", () => {
    tracker.recordSuccess("web", 120);
    const stats = tracker.getStats("web");
    expect(stats?.selectionCount).toBe(1);
    expect(stats?.successCount).toBe(1);
    expect(stats?.failureCount).toBe(0);
    expect(stats?.successRate).toBe(1);
  });

  it("records failure with error reason persisted to tool_executions", () => {
    tracker.recordFailure("web", 200, {
      errorCode: "TIMEOUT",
      errorMessage: "504 Gateway Timeout",
    });
    const stats = tracker.getStats("web");
    expect(stats?.failureCount).toBe(1);
    expect(stats?.successRate).toBe(0);

    const row = db.rawDb
      .prepare("SELECT error_code, error_message FROM tool_executions WHERE tool_name = ?")
      .get("web") as { error_code: string; error_message: string };
    expect(row.error_code).toBe("TIMEOUT");
    expect(row.error_message).toBe("504 Gateway Timeout");
  });

  it("getTopBySelectionCount returns ordered top-N with name field", () => {
    for (let i = 0; i < 5; i++) tracker.recordSuccess("web", 10);
    for (let i = 0; i < 3; i++) tracker.recordSuccess("memory", 10);
    tracker.recordSuccess("schedule", 10);
    const top = tracker.getTopBySelectionCount(2);
    expect(top.map((t) => t.name)).toEqual(["web", "memory"]);
    expect(top[0].stats.selectionCount).toBe(5);
  });

  it("getStats returns null for unknown tool", () => {
    expect(tracker.getStats("nonexistent")).toBeNull();
  });

  it("getUsageMultiplier preserves 90-day half-life formula", () => {
    // Untracked tool → base 0.7, recency factor exp(0) = 1 against Infinity → 0.5
    // Actual: never-used returns 0.7 * (0.5 + 0.5 * exp(-Inf/90)) = 0.7 * 0.5 = 0.35
    const noData = tracker.getUsageMultiplier("never-used");
    expect(noData).toBeCloseTo(0.35, 2);

    // 100% success rate, used today → 1.3 * 1.0 = 1.3
    for (let i = 0; i < 5; i++) tracker.recordSuccess("hot", 10);
    const hot = tracker.getUsageMultiplier("hot");
    expect(hot).toBeCloseTo(1.3, 2);

    // 0% success rate, used today → 0.7 * 1.0 = 0.7
    for (let i = 0; i < 5; i++) tracker.recordFailure("cold", 10);
    const cold = tracker.getUsageMultiplier("cold");
    expect(cold).toBeCloseTo(0.7, 2);
  });

  it("captures sessionId and subgoalId when provided", () => {
    tracker.recordSuccess("web", 50, { sessionId: "sess-1", subgoalId: "sg-9" });
    const row = db.rawDb
      .prepare("SELECT session_id, subgoal_id FROM tool_executions WHERE tool_name = ?")
      .get("web") as { session_id: string; subgoal_id: string };
    expect(row.session_id).toBe("sess-1");
    expect(row.subgoal_id).toBe("sg-9");
  });
});
