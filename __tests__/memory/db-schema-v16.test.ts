import { describe, it, expect, afterEach } from "vitest";
import { StackOwlDB } from "../../src/memory/db.js";
import { tmpdir } from "os";
import { join } from "path";
import { randomBytes } from "crypto";
import { unlinkSync } from "fs";

function tmpDbPath(): string {
  return join(tmpdir(), `stackowl-test-${randomBytes(4).toString("hex")}.db`);
}

describe("Schema v16 migration", () => {
  const dbPaths: string[] = [];

  afterEach(() => {
    for (const p of dbPaths) {
      try { unlinkSync(p); } catch {}
      try { unlinkSync(p + "-shm"); } catch {}
      try { unlinkSync(p + "-wal"); } catch {}
    }
    dbPaths.length = 0;
  });

  it("trajectory_turns has verification_result column after v16 migration", () => {
    const path = tmpDbPath();
    dbPaths.push(path);
    const db = new StackOwlDB(path);
    const cols = (db as any).db.prepare("PRAGMA table_info(trajectory_turns)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toContain("verification_result");
  });

  it("trajectory_turns has verifier_reason column after v16 migration", () => {
    const path = tmpDbPath();
    dbPaths.push(path);
    const db = new StackOwlDB(path);
    const cols = (db as any).db.prepare("PRAGMA table_info(trajectory_turns)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toContain("verifier_reason");
  });

  it("trajectory_turns has subgoal_id column after v16 migration", () => {
    const path = tmpDbPath();
    dbPaths.push(path);
    const db = new StackOwlDB(path);
    const cols = (db as any).db.prepare("PRAGMA table_info(trajectory_turns)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toContain("subgoal_id");
  });

  it("workspace_tools table exists after v16 migration", () => {
    const path = tmpDbPath();
    dbPaths.push(path);
    const db = new StackOwlDB(path);
    const tables = (db as any).db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all() as Array<{ name: string }>;
    const names = tables.map(t => t.name);
    expect(names).toContain("workspace_tools");
  });

  it("workspace_tools has correct columns", () => {
    const path = tmpDbPath();
    dbPaths.push(path);
    const db = new StackOwlDB(path);
    const cols = (db as any).db.prepare("PRAGMA table_info(workspace_tools)").all() as Array<{ name: string }>;
    const names = cols.map(c => c.name);
    expect(names).toContain("tool_name");
    expect(names).toContain("state");
    expect(names).toContain("source_code");
    expect(names).toContain("created_at");
  });
});
