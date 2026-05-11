// __tests__/tools/db-query.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { unlinkSync, existsSync } from "node:fs";
import Database from "better-sqlite3";
import { join } from "node:path";
import { tmpdir } from "node:os";

const TEST_DB = join(tmpdir(), "stackowl-dbquery-test.sqlite");

function createTestDb() {
  const db = new Database(TEST_DB);
  db.exec(`
    CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT, value INTEGER);
    INSERT OR REPLACE INTO items VALUES (1, 'alpha', 10);
    INSERT OR REPLACE INTO items VALUES (2, 'beta', 20);
  `);
  db.close();
}

describe("DbQueryTool", () => {
  afterEach(() => {
    if (existsSync(TEST_DB)) unlinkSync(TEST_DB);
  });

  it("tool name is 'db_query'", async () => {
    const mod = await import("../../src/tools/db-query.js");
    expect(mod.DbQueryTool.definition.name).toBe("db_query");
  });

  it("executes a SELECT and returns rows", async () => {
    createTestDb();
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { dbPath: TEST_DB, sql: "SELECT * FROM items ORDER BY id" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.rows).toHaveLength(2);
    expect(parsed.data.rows[0].name).toBe("alpha");
  });

  it("returns structured error for invalid SQL", async () => {
    createTestDb();
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { dbPath: TEST_DB, sql: "SELECT * FROM nonexistent_table" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("QUERY_ERROR");
  });

  it("returns structured error when db file not found", async () => {
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { dbPath: "/tmp/nonexistent-stackowl-xyz.sqlite", sql: "SELECT 1" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(["FILE_NOT_FOUND", "QUERY_ERROR"]).toContain(parsed.error.code);
  });

  it("rejects paths outside workspace and /tmp (path traversal attempt)", async () => {
    const mod = await import("../../src/tools/db-query.js");
    // Attempt to query a database outside the workspace (e.g., home directory)
    const result = await mod.DbQueryTool.execute(
      { dbPath: "/etc/hostname.db", sql: "SELECT 1" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("ACCESS_DENIED");
    expect(parsed.error.message).toContain("Access denied");
  });

  it("rejects non-.db and non-.sqlite file extensions", async () => {
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { dbPath: "/tmp/data.txt", sql: "SELECT 1" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_PATH");
    expect(parsed.error.message).toContain("Only .db and .sqlite");
  });

  it("allows queries to files in /tmp", async () => {
    createTestDb();
    const mod = await import("../../src/tools/db-query.js");
    const result = await mod.DbQueryTool.execute(
      { dbPath: TEST_DB, sql: "SELECT * FROM items ORDER BY id" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.rows).toHaveLength(2);
  });

  it("allows relative paths within the workspace", async () => {
    createTestDb();
    const mod = await import("../../src/tools/db-query.js");
    // Pass a relative path that resolves within /tmp (the cwd)
    const result = await mod.DbQueryTool.execute(
      { dbPath: "stackowl-dbquery-test.sqlite", sql: "SELECT * FROM items ORDER BY id" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.rows).toHaveLength(2);
  });
});
