import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rmSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { MemoryDatabase } from "../src/memory/db.js";

describe("Schema v23 — tool_executions + tool_edges", () => {
  let dir: string;
  let db: MemoryDatabase;

  beforeEach(() => {
    dir = join(tmpdir(), `db-v23-${Date.now()}-${Math.random()}`);
    db = new MemoryDatabase(dir);
  });
  afterEach(() => {
    db.close();
    if (existsSync(dir)) rmSync(dir, { recursive: true, force: true });
  });

  it("schema version is at least 23", () => {
    const v = db.rawDb.pragma("user_version", { simple: true }) as number;
    expect(v).toBeGreaterThanOrEqual(23);
  });

  it("creates tool_executions table with required columns", () => {
    const cols = db.rawDb.prepare("PRAGMA table_info(tool_executions)").all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(
      expect.arrayContaining(["id", "tool_name", "success", "duration_ms", "error_code", "error_message", "subgoal_id", "session_id", "created_at"]),
    );
  });

  it("creates tool_edges table with capability_tag index", () => {
    const cols = db.rawDb.prepare("PRAGMA table_info(tool_edges)").all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(
      expect.arrayContaining(["from_tool", "to_tool", "capability_tag", "success_rate", "avg_duration_ms", "sample_count", "updated_at"]),
    );
    const indexes = db.rawDb.prepare("PRAGMA index_list(tool_edges)").all() as Array<{ name: string }>;
    expect(indexes.some((i) => i.name.includes("capability"))).toBe(true);
  });

  it("recordToolExecution writes a row", () => {
    db.recordToolExecution({
      toolName: "web",
      success: true,
      durationMs: 123,
      sessionId: "sess-1",
    });
    const row = db.rawDb.prepare("SELECT * FROM tool_executions WHERE tool_name = ?").get("web") as Record<string, unknown>;
    expect(row.success).toBe(1);
    expect(row.duration_ms).toBe(123);
  });

  it("getToolStats aggregates selection/success/failure", () => {
    db.recordToolExecution({ toolName: "web", success: true, durationMs: 100 });
    db.recordToolExecution({ toolName: "web", success: false, durationMs: 200, errorCode: "TIMEOUT" });
    db.recordToolExecution({ toolName: "web", success: true, durationMs: 150 });
    const stats = db.getToolStats("web");
    expect(stats?.selectionCount).toBe(3);
    expect(stats?.successCount).toBe(2);
    expect(stats?.failureCount).toBe(1);
    expect(stats?.avgDurationMs).toBeCloseTo(150, 0);
  });
});
