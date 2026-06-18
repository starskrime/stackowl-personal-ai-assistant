import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyV27HostRootMigration } from "../../src/memory/db.js";

describe("v27 host_root migration", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.exec(`
      CREATE TABLE IF NOT EXISTS tool_edges (
        from_tool       TEXT NOT NULL,
        to_tool         TEXT NOT NULL,
        capability_tag  TEXT NOT NULL,
        success_rate    REAL NOT NULL DEFAULT 0,
        avg_duration_ms INTEGER NOT NULL DEFAULT 0,
        sample_count    INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (from_tool, to_tool, capability_tag)
      );
    `);
  });

  afterEach(() => db.close());

  it("adds host_root column with default empty string", () => {
    applyV27HostRootMigration(db);
    const cols = db
      .prepare(`PRAGMA table_info(tool_edges)`)
      .all() as Array<{ name: string; dflt_value: string | null }>;
    const hostRoot = cols.find((c) => c.name === "host_root");
    expect(hostRoot).toBeDefined();
    // SQLite renders the '' default as the literal string "''"
    expect(hostRoot?.dflt_value).toMatch(/''/);
  });

  it("preserves pre-existing rows with host_root = ''", () => {
    db.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count)
       VALUES (?, ?, ?, ?, ?)`,
    ).run("a", "b", "cap", 0.9, 5);
    applyV27HostRootMigration(db);
    const row = db
      .prepare(`SELECT host_root, success_rate, sample_count FROM tool_edges WHERE from_tool = 'a'`)
      .get() as { host_root: string; success_rate: number; sample_count: number };
    expect(row.host_root).toBe("");
    expect(row.success_rate).toBe(0.9);
    expect(row.sample_count).toBe(5);
  });

  it("creates idx_tool_edges_host_capability", () => {
    applyV27HostRootMigration(db);
    const idx = db
      .prepare(
        `SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tool_edges_host_capability'`,
      )
      .get();
    expect(idx).toBeDefined();
  });

  it("is idempotent — running twice does not throw", () => {
    applyV27HostRootMigration(db);
    expect(() => applyV27HostRootMigration(db)).not.toThrow();
  });

  it("extends primary key to (from_tool, to_tool, capability_tag, host_root)", () => {
    applyV27HostRootMigration(db);
    db.prepare(
      `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).run("a", "b", "cap", "", 0.5, 3);
    // Adding a host-scoped row with the same other-cols MUST succeed (different PK)
    expect(() =>
      db.prepare(
        `INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count)
         VALUES (?, ?, ?, ?, ?, ?)`,
      ).run("a", "b", "cap", "example.com", 0.9, 3),
    ).not.toThrow();
    const rows = db.prepare(`SELECT host_root FROM tool_edges ORDER BY host_root`).all() as Array<{ host_root: string }>;
    expect(rows.map((r) => r.host_root)).toEqual(["", "example.com"]);
  });
});
