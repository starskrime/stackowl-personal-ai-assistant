// src/tools/db-query.ts
import type { ToolImplementation, ToolContext } from "./registry.js";

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

    if (!dbPath) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "dbPath is required" } });
    if (!sql)    return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "sql is required" } });

    let params: unknown[] = [];
    if (paramsRaw) {
      try {
        params = JSON.parse(paramsRaw);
      } catch {
        return JSON.stringify({ success: false, error: { code: "INVALID_PARAMS", message: "params must be a valid JSON array" } });
      }
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let DatabaseCtor: any;
    try {
      // better-sqlite3 uses `export =` style — the module itself is the constructor
      const mod = await import("better-sqlite3");
      DatabaseCtor = (mod as unknown as { default: unknown }).default ?? mod;
    } catch {
      return JSON.stringify({ success: false, error: { code: "MISSING_DEP", message: "better-sqlite3 is not installed" } });
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let db: any = null;
    try {
      db = new DatabaseCtor(dbPath, { readonly: true, fileMustExist: true });
      const stmt = db.prepare(sql);
      const rows = stmt.all(...params) as Record<string, unknown>[];
      const columns = rows.length > 0 ? Object.keys(rows[0]!) : [];
      return JSON.stringify({ success: true, data: { rows, rowCount: rows.length, columns } });
    } catch (err) {
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
