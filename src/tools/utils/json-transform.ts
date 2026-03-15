import type { ToolImplementation, ToolContext } from "../registry.js";

export const JSONTransformTool: ToolImplementation = {
  definition: {
    name: "json_transform",
    description:
      "Transform JSON data — format, minify, extract values by path, list keys, or convert to CSV.",
    parameters: {
      type: "object",
      properties: {
        data: {
          type: "string",
          description: "JSON string input",
        },
        operation: {
          type: "string",
          description:
            'Operation to perform: "format" (pretty print), "minify", "extract" (with jpath), "keys" (list all keys), "csv" (array of objects to CSV)',
        },
        jpath: {
          type: "string",
          description:
            'Path for extract operation, e.g. "data.users[0].name"',
        },
      },
      required: ["data", "operation"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const dataStr = String(args.data);
      const operation = String(args.operation);

      let parsed: unknown;
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        return "Error: Invalid JSON input.";
      }

      switch (operation) {
        case "format":
          return JSON.stringify(parsed, null, 2);

        case "minify":
          return JSON.stringify(parsed);

        case "extract": {
          const jpath = String(args.jpath ?? "");
          if (!jpath) {
            return 'Error: "jpath" parameter is required for extract operation.';
          }
          const value = extractPath(parsed, jpath);
          if (value === undefined) {
            return `No value found at path: ${jpath}`;
          }
          return typeof value === "object"
            ? JSON.stringify(value, null, 2)
            : String(value);
        }

        case "keys": {
          const keys = collectKeys(parsed);
          return `Keys found (${keys.length}):\n${keys.join("\n")}`;
        }

        case "csv": {
          if (!Array.isArray(parsed)) {
            return "Error: CSV conversion requires an array of objects.";
          }
          if (parsed.length === 0) {
            return "Error: Empty array, nothing to convert.";
          }
          const first = parsed[0] as Record<string, unknown>;
          if (typeof first !== "object" || first === null) {
            return "Error: CSV conversion requires an array of objects.";
          }

          const headers = Object.keys(first);
          const rows = [headers.join(",")];
          for (const item of parsed) {
            const obj = item as Record<string, unknown>;
            const row = headers.map((h) => {
              const val = obj[h];
              const str = val === null || val === undefined ? "" : String(val);
              // Escape CSV values containing commas or quotes
              if (str.includes(",") || str.includes('"') || str.includes("\n")) {
                return `"${str.replace(/"/g, '""')}"`;
              }
              return str;
            });
            rows.push(row.join(","));
          }
          return rows.join("\n");
        }

        default:
          return `Error: Unknown operation "${operation}". Use: format, minify, extract, keys, or csv.`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error transforming JSON: ${msg}`;
    }
  },
};

function extractPath(obj: unknown, path: string): unknown {
  const parts = path.replace(/\[(\d+)\]/g, ".$1").split(".");
  let current: unknown = obj;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;
    if (typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function collectKeys(obj: unknown, prefix = ""): string[] {
  const keys: string[] = [];
  if (obj === null || typeof obj !== "object") return keys;
  if (Array.isArray(obj)) {
    if (obj.length > 0) {
      keys.push(...collectKeys(obj[0], prefix + "[0]"));
    }
    return keys;
  }
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    keys.push(fullKey);
    if (value && typeof value === "object") {
      keys.push(...collectKeys(value, fullKey));
    }
  }
  return keys;
}
