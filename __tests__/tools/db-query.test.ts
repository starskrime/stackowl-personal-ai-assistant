// __tests__/tools/db-query.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { unlinkSync, existsSync, mkdirSync, symlinkSync } from "node:fs";
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

  it("rejects symlink escape attempts (symlink pointing outside sandbox)", async () => {
    // Create a temporary workspace directory (inside /tmp, allowed by sandbox)
    const testTmpDir = join(tmpdir(), "stackowl-symlink-test-" + Date.now());
    mkdirSync(testTmpDir, { recursive: true });

    // Create a separate external directory outside /tmp to hold the "secret" database
    // Use /var/tmp which is not in the /tmp/ prefix check
    const externalDir = "/var/tmp/stackowl-external-" + Date.now();
    mkdirSync(externalDir, { recursive: true });

    try {
      // Create a test database file in the external directory (outside the allowed sandbox)
      const externalDbPath = join(externalDir, "secret.db");
      const externalDb = new Database(externalDbPath);
      externalDb.exec(`
        CREATE TABLE IF NOT EXISTS secret (id INTEGER PRIMARY KEY, data TEXT);
        INSERT INTO secret VALUES (1, 'sensitive');
      `);
      externalDb.close();

      // Create a symlink inside the test workspace pointing to the external database
      const symlinkPath = join(testTmpDir, "evil.db");
      symlinkSync(externalDbPath, symlinkPath);

      // Attempt to query the symlink from within the workspace
      const mod = await import("../../src/tools/db-query.js");
      const result = await mod.DbQueryTool.execute(
        { dbPath: symlinkPath, sql: "SELECT * FROM secret" },
        { cwd: testTmpDir },
      );
      const parsed = JSON.parse(result);

      // Should be rejected because the real path (after realpathSync) is outside the sandbox
      expect(parsed.success).toBe(false);
      expect(parsed.error.code).toBe("ACCESS_DENIED");
      expect(parsed.error.message).toContain("Access denied");
    } finally {
      // Clean up the test directory
      try {
        const files = require("node:fs").readdirSync(testTmpDir);
        files.forEach((f: string) => unlinkSync(join(testTmpDir, f)));
        require("node:fs").rmdirSync(testTmpDir);
      } catch {
        // Ignore cleanup errors
      }

      // Clean up external directory
      try {
        const files = require("node:fs").readdirSync(externalDir);
        files.forEach((f: string) => unlinkSync(join(externalDir, f)));
        require("node:fs").rmdirSync(externalDir);
      } catch {
        // Ignore cleanup errors
      }
    }
  });

  it("allows database access from workspace root when cwd is the project root", async () => {
    // Use the actual project root as the workspace
    const projectRoot = "/ssd/projects/stackowl-personal-ai-assistant";
    const workspaceDbPath = join(projectRoot, ".stackowl-test-" + Date.now() + ".sqlite");

    try {
      // Create a test database in the project root
      const db = new Database(workspaceDbPath);
      db.exec(`
        CREATE TABLE IF NOT EXISTS test_data (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO test_data VALUES (1, 'workspace_test');
      `);
      db.close();

      // Execute query with cwd set to project root
      const mod = await import("../../src/tools/db-query.js");
      const result = await mod.DbQueryTool.execute(
        { dbPath: workspaceDbPath, sql: "SELECT * FROM test_data" },
        { cwd: projectRoot },
      );
      const parsed = JSON.parse(result);

      expect(parsed.success).toBe(true);
      expect(parsed.data.rows).toHaveLength(1);
      expect(parsed.data.rows[0].name).toBe("workspace_test");
    } finally {
      if (existsSync(workspaceDbPath)) {
        unlinkSync(workspaceDbPath);
      }
    }
  });
});
