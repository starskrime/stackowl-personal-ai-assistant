// src/tools/db-query.ts
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

export const DbQueryTool: ToolImplementation = {
  definition: {
    name: "db_query",
    description:
      "Execute a SQL query against a SQLite database file and return results as rows. " +
      'Example: db_query(dbPath: "/home/user/data.sqlite", sql: "SELECT * FROM users LIMIT 10")',
    parameters: {
      type: "object",
      properties: {
        dbPath: {
          type: "string",
          description: "Absolute path to the SQLite database file.",
        },
        sql: {
          type: "string",
          description: "SQL query to execute.",
        },
        params: {
          type: "string",
          description: "Optional JSON array of query parameters.",
        },
      },
      required: ["dbPath", "sql"],
    },
    capabilities: ["db_query", "data_read"],
    executionPolicy: { timeoutMs: 15_000, maxRetries: 0 },
  },

  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const dbPath    = args["dbPath"]  as string;
    const sql       = args["sql"]     as string;
    const paramsRaw = args["params"]  as string | undefined;
    log.tool.debug("db-query.execute: entry", { dbPath, sqlLen: sql?.length ?? 0 });

    if (!dbPath) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "dbPath is required" } });
    if (!sql)    return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "sql is required" } });

    let params: unknown[] = [];
    if (paramsRaw) {
      try {
        params = JSON.parse(paramsRaw);
      } catch (err) {
        log.tool.warn("db-query: params JSON parse failed", err);
        return JSON.stringify({ success: false, error: { code: "INVALID_PARAMS", message: "params must be a valid JSON array" } });
      }
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let DatabaseCtor: any;
    try {
      // better-sqlite3 uses `export =` style — the module itself is the constructor
      const mod = await import("better-sqlite3");
      DatabaseCtor = (mod as unknown as { default: unknown }).default ?? mod;
    } catch (err) {
      log.tool.warn("db-query: better-sqlite3 not available", err);
      return JSON.stringify({ success: false, error: { code: "MISSING_DEP", message: "better-sqlite3 is not installed" } });
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let db: any = null;
    try {
      log.tool.debug("db-query.execute: opening database", { dbPath });
      db = new DatabaseCtor(dbPath, { readonly: true, fileMustExist: true });
      log.tool.debug("db-query.execute: executing query", { sql: sql.slice(0, 200) });
      const stmt = db.prepare(sql);
      const rows = stmt.all(...params) as Record<string, unknown>[];
      const columns = rows.length > 0 ? Object.keys(rows[0]!) : [];
      log.tool.debug("db-query.execute: exit", { success: true, rowCount: rows.length, columns });
      return JSON.stringify({ success: true, data: { rows, rowCount: rows.length, columns } });
    } catch (err) {
      log.tool.error("db-query.execute: failed", err, { dbPath, sql: sql.slice(0, 200) });
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("ENOENT") || msg.includes("no such file")) {
        return JSON.stringify({ success: false, error: { code: "FILE_NOT_FOUND", message: msg } });
      }
      return JSON.stringify({ success: false, error: { code: "QUERY_ERROR", message: msg } });
    } finally {
      db?.close();
    }
  },
};
