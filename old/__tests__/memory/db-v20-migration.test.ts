// __tests__/memory/db-v20-migration.test.ts
import { describe, it, expect, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyMigrations } from "../../src/memory/db.js";

describe("Schema v20 migration", () => {
  let db: Database.Database;
  afterEach(() => { try { db.close(); } catch {} });

  it("adds parliament_session_id column to trajectory_turns on fresh DB", () => {
    db = new Database(":memory:");
    applyMigrations(db);
    const cols = (db.prepare("PRAGMA table_info(trajectory_turns)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain("parliament_session_id");
  });

  it("is idempotent — calling applyMigrations twice does not throw", () => {
    db = new Database(":memory:");
    expect(() => { applyMigrations(db); applyMigrations(db); }).not.toThrow();
  });

  it("adds parliament_session_id column when trajectory_turns already exists without it", () => {
    db = new Database(":memory:");
    // Create trajectory_turns without the new column (simulate pre-v20 DB)
    db.exec(`CREATE TABLE trajectory_turns (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)`);
    db.pragma(`user_version = 19`);
    applyMigrations(db);
    const cols = (db.prepare("PRAGMA table_info(trajectory_turns)").all() as { name: string }[]).map(c => c.name);
    expect(cols).toContain("parliament_session_id");
  });
});
