/**
 * StackOwl — Spreadsheet Tool
 *
 * Create and manage simple spreadsheets stored as JSON files.
 * Supports creating, adding rows, reading, querying, and CSV export.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

interface SpreadsheetData {
  headers: string[];
  rows: string[][];
}

async function getSpreadsheetDir(cwd: string): Promise<string> {
  const dir = resolve(cwd, "workspace", "spreadsheets");
  await mkdir(dir, { recursive: true });
  return dir;
}

function sanitizeName(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]/g, "_");
}

async function loadSpreadsheet(
  dir: string,
  name: string,
): Promise<SpreadsheetData | null> {
  try {
    const content = await readFile(resolve(dir, `${name}.json`), "utf-8");
    return JSON.parse(content) as SpreadsheetData;
  } catch {
    return null;
  }
}

async function saveSpreadsheet(
  dir: string,
  name: string,
  data: SpreadsheetData,
): Promise<void> {
  await writeFile(resolve(dir, `${name}.json`), JSON.stringify(data, null, 2), "utf-8");
}

export const SpreadsheetTool: ToolImplementation = {
  definition: {
    name: "spreadsheet",
    description:
      "Create and manage simple spreadsheets — add rows, query data, and export to CSV. Data stored as JSON.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            'Action to perform: "create", "add_row", "read", "query", or "export_csv".',
        },
        name: {
          type: "string",
          description: "Spreadsheet name (used as filename).",
        },
        headers: {
          type: "string",
          description:
            'JSON array of header names (for "create" action). Example: \'["Name", "Age", "City"]\'',
        },
        values: {
          type: "string",
          description:
            'JSON array of row values (for "add_row" action). Example: \'["Alice", "30", "NYC"]\'',
        },
        row_start: {
          type: "number",
          description: 'Start row index for "read" action (0-based, optional).',
        },
        row_end: {
          type: "number",
          description: 'End row index for "read" action (exclusive, optional).',
        },
        column: {
          type: "string",
          description: 'Column name for "query" action.',
        },
        operator: {
          type: "string",
          description:
            'Comparison operator for "query": "eq", "neq", "contains", "gt", "lt".',
        },
        value: {
          type: "string",
          description: 'Value to compare against for "query" action.',
        },
      },
      required: ["action", "name"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const action = args["action"] as string;
      const rawName = args["name"] as string;
      if (!action) return "Error: 'action' parameter is required.";
      if (!rawName) return "Error: 'name' parameter is required.";

      const name = sanitizeName(rawName);
      const dir = await getSpreadsheetDir(_context.cwd);

      switch (action) {
        case "create": {
          const headersStr = args["headers"] as string;
          if (!headersStr) return "Error: 'headers' parameter is required for create action.";
          let headers: string[];
          try {
            headers = JSON.parse(headersStr);
          } catch {
            return "Error: 'headers' must be a valid JSON array string.";
          }
          if (!Array.isArray(headers) || headers.length === 0) {
            return "Error: 'headers' must be a non-empty array.";
          }
          const data: SpreadsheetData = { headers, rows: [] };
          await saveSpreadsheet(dir, name, data);
          const filePath = resolve(dir, `${name}.json`);
          return `Spreadsheet '${name}' created with columns: ${headers.join(", ")}\nFile: ${filePath}`;
        }

        case "add_row": {
          const valuesStr = args["values"] as string;
          if (!valuesStr) return "Error: 'values' parameter is required for add_row action.";
          let values: string[];
          try {
            values = JSON.parse(valuesStr);
          } catch {
            return "Error: 'values' must be a valid JSON array string.";
          }
          const sheet = await loadSpreadsheet(dir, name);
          if (!sheet) return `Error: Spreadsheet '${name}' not found.`;
          if (values.length !== sheet.headers.length) {
            return `Error: Expected ${sheet.headers.length} values (${sheet.headers.join(", ")}), got ${values.length}.`;
          }
          sheet.rows.push(values);
          await saveSpreadsheet(dir, name, sheet);
          return `Row added to '${name}' (total rows: ${sheet.rows.length}).`;
        }

        case "read": {
          const sheet = await loadSpreadsheet(dir, name);
          if (!sheet) return `Error: Spreadsheet '${name}' not found.`;
          const start = (args["row_start"] as number) ?? 0;
          const end = (args["row_end"] as number) ?? sheet.rows.length;
          const rows = sheet.rows.slice(start, end);
          const header = sheet.headers.join(" | ");
          const separator = sheet.headers.map(() => "---").join(" | ");
          const rowLines = rows.map((r) => r.join(" | "));
          return `Spreadsheet '${name}' (rows ${start}-${Math.min(end, sheet.rows.length)} of ${sheet.rows.length}):\n\n${header}\n${separator}\n${rowLines.join("\n")}`;
        }

        case "query": {
          const column = args["column"] as string;
          const operator = args["operator"] as string;
          const value = args["value"] as string;
          if (!column || !operator || value === undefined) {
            return "Error: 'column', 'operator', and 'value' are required for query action.";
          }
          const sheet = await loadSpreadsheet(dir, name);
          if (!sheet) return `Error: Spreadsheet '${name}' not found.`;
          const colIdx = sheet.headers.indexOf(column);
          if (colIdx === -1) {
            return `Error: Column '${column}' not found. Available: ${sheet.headers.join(", ")}`;
          }
          const matches = sheet.rows.filter((row) => {
            const cell = row[colIdx] ?? "";
            switch (operator) {
              case "eq":
                return cell === value;
              case "neq":
                return cell !== value;
              case "contains":
                return cell.toLowerCase().includes(value.toLowerCase());
              case "gt":
                return parseFloat(cell) > parseFloat(value);
              case "lt":
                return parseFloat(cell) < parseFloat(value);
              default:
                return false;
            }
          });
          if (matches.length === 0) return `No rows matched query: ${column} ${operator} ${value}`;
          const header = sheet.headers.join(" | ");
          const separator = sheet.headers.map(() => "---").join(" | ");
          const rowLines = matches.map((r) => r.join(" | "));
          return `Query results (${matches.length} matches for ${column} ${operator} ${value}):\n\n${header}\n${separator}\n${rowLines.join("\n")}`;
        }

        case "export_csv": {
          const sheet = await loadSpreadsheet(dir, name);
          if (!sheet) return `Error: Spreadsheet '${name}' not found.`;
          const escapeCsv = (s: string) => {
            if (s.includes(",") || s.includes('"') || s.includes("\n")) {
              return `"${s.replace(/"/g, '""')}"`;
            }
            return s;
          };
          const lines = [
            sheet.headers.map(escapeCsv).join(","),
            ...sheet.rows.map((row) => row.map(escapeCsv).join(",")),
          ];
          const csvContent = lines.join("\n");
          const csvPath = resolve(dir, `${name}.csv`);
          await writeFile(csvPath, csvContent, "utf-8");
          return `CSV exported to: ${csvPath}`;
        }

        default:
          return `Error: Unknown action '${action}'. Valid actions: create, add_row, read, query, export_csv`;
      }
    } catch (error: any) {
      return `Error in spreadsheet operation: ${error.message ?? String(error)}`;
    }
  },
};
