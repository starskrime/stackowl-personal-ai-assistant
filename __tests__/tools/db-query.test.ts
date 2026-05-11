// __tests__/tools/db-query.test.ts
import { describe, it, expect, afterEach } from "vitest";
import { unlinkSync, existsSync, mkdirSync, symlinkSync, rmSync } from "node:fs";
import Database from "better-sqlite3";
import { join, resolve } from "node:path";
import { tmpdir, homedir } from "node:os";

const TEST_DB = join(tmpdir(), "stackowl-dbquery-test.sqlite");

// Cross-platform "outside the temp tree" directory: live under homedir.
// realpathSync(tmpdir()) is never a prefix of homedir on Linux, macOS, or Windows.
const EXTERNAL_ROOT = join(homedir(), ".stackowl-test-external");

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
      { dbPath: join(tmpdir(), "nonexistent-stackowl-xyz.sqlite"), sql: "SELECT 1" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(["FILE_NOT_FOUND", "QUERY_ERROR"]).toContain(parsed.error.code);
  });

  it("rejects paths outside workspace and OS tempdir (path traversal attempt)", async () => {
    const mod = await import("../../src/tools/db-query.js");
    // homedir is guaranteed to be outside both the workspace (tmpdir) and tmpdir itself
    const result = await mod.DbQueryTool.execute(
      { dbPath: join(homedir(), "stackowl-traversal-target.db"), sql: "SELECT 1" },
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
      { dbPath: join(tmpdir(), "data.txt"), sql: "SELECT 1" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(false);
    expect(parsed.error.code).toBe("INVALID_PATH");
    expect(parsed.error.message).toContain("Only .db and .sqlite");
  });

  it("allows queries to files in OS tempdir", async () => {
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
    // Relative path resolves against cwd (tmpdir)
    const result = await mod.DbQueryTool.execute(
      { dbPath: "stackowl-dbquery-test.sqlite", sql: "SELECT * FROM items ORDER BY id" },
      { cwd: tmpdir() },
    );
    const parsed = JSON.parse(result);
    expect(parsed.success).toBe(true);
    expect(parsed.data.rows).toHaveLength(2);
  });

  it("rejects symlink escape attempts (symlink pointing outside sandbox)", async () => {
    const testWorkspace = join(tmpdir(), "stackowl-symlink-test-" + Date.now());
    mkdirSync(testWorkspace, { recursive: true });

    const externalDir = join(EXTERNAL_ROOT, "symlink-target-" + Date.now());
    mkdirSync(externalDir, { recursive: true });

    try {
      const externalDbPath = join(externalDir, "secret.db");
      const externalDb = new Database(externalDbPath);
      externalDb.exec(`
        CREATE TABLE IF NOT EXISTS secret (id INTEGER PRIMARY KEY, data TEXT);
        INSERT INTO secret VALUES (1, 'sensitive');
      `);
      externalDb.close();

      const symlinkPath = join(testWorkspace, "evil.db");
      try {
        symlinkSync(externalDbPath, symlinkPath);
      } catch (err) {
        // Windows requires admin or Developer Mode for symlinks. If we can't
        // create one we can't run this test path — skip rather than fail.
        if ((err as NodeJS.ErrnoException).code === "EPERM") {
          return;
        }
        throw err;
      }

      const mod = await import("../../src/tools/db-query.js");
      const result = await mod.DbQueryTool.execute(
        { dbPath: symlinkPath, sql: "SELECT * FROM secret" },
        { cwd: testWorkspace },
      );
      const parsed = JSON.parse(result);

      expect(parsed.success).toBe(false);
      expect(parsed.error.code).toBe("ACCESS_DENIED");
      expect(parsed.error.message).toContain("Access denied");
    } finally {
      rmSync(testWorkspace, { recursive: true, force: true });
      rmSync(externalDir, { recursive: true, force: true });
    }
  });

  it("allows database access from workspace root when cwd is the project root", async () => {
    // Use the resolved cwd as workspace — works regardless of OS
    const projectRoot = resolve(process.cwd());
    const workspaceDbPath = join(projectRoot, ".stackowl-test-" + Date.now() + ".sqlite");

    try {
      const db = new Database(workspaceDbPath);
      db.exec(`
        CREATE TABLE IF NOT EXISTS test_data (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO test_data VALUES (1, 'workspace_test');
      `);
      db.close();

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
