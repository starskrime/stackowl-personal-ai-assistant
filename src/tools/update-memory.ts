import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";

const DEFAULT_MEMORY_PATH = join(
  homedir(),
  ".stackowl",
  "workspace",
  "MEMORY.md",
);
const MAX_LINES = 150;
const MAX_LINE_LENGTH = 200;

export interface UpdateMemoryInput {
  operation: "add" | "update" | "remove";
  section: string;
  content: string;
}

export class UpdateMemoryTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "update_memory",
    description:
      "Update MEMORY.md — the always-loaded Tier-0 memory. " +
      "Use to persist durable facts: user preferences, ongoing projects, key relationships. " +
      "Operations: add (append to section), update (replace matching line), remove (delete matching line).",
    parameters: {
      type: "object",
      properties: {
        operation: {
          type: "string",
          enum: ["add", "update", "remove"],
          description:
            "Operation to perform: add (append line to section), update (replace matching line), remove (delete matching line)",
        },
        section: {
          type: "string",
          description:
            'Section heading (without # prefix, e.g. "Preferences" for "# Preferences")',
        },
        content: {
          type: "string",
          description: "Content to add/update/remove (max 200 chars per line)",
        },
      },
      required: ["operation", "section", "content"],
    },
  };

  category = "filesystem" as const;
  source = "builtin";

  constructor(private memoryPath: string = DEFAULT_MEMORY_PATH) {}

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const input = args as unknown as UpdateMemoryInput;

    log.tool.debug("update_memory.execute: entry", {
      operation: input.operation,
      section: input.section,
    });

    if (input.content.length > MAX_LINE_LENGTH) {
      const err = new Error(
        `Line too long (${input.content.length} chars). Keep lines under ${MAX_LINE_LENGTH}.`,
      );
      log.tool.error("update_memory.execute: line too long", err, {
        contentLen: input.content.length,
      });
      throw err;
    }

    mkdirSync(dirname(this.memoryPath), { recursive: true });

    const raw = existsSync(this.memoryPath)
      ? readFileSync(this.memoryPath, "utf-8")
      : "";

    let lines = raw.split("\n");

    if (input.operation === "add") {
      if (lines.length + 1 > MAX_LINES) {
        const err = new Error(
          `MEMORY.md is at ${MAX_LINES} lines — remove stale entries before adding new ones.`,
        );
        log.tool.error("update_memory.execute: file too large", err, {
          currentLines: lines.length,
        });
        throw err;
      }

      const sectionHeader = `# ${input.section}`;
      const idx = lines.findIndex((l) => l.trim() === sectionHeader);

      if (idx === -1) {
        // Append new section at end
        lines = [
          ...lines.filter((l) => l !== ""),
          "",
          sectionHeader,
          input.content,
          "",
        ];
      } else {
        // Find end of section (next heading or EOF)
        let insertAt = idx + 1;
        while (insertAt < lines.length && !lines[insertAt].startsWith("#")) {
          insertAt++;
        }
        lines.splice(insertAt, 0, input.content);
      }
    } else if (input.operation === "remove") {
      const keyword = input.content.toLowerCase();
      lines = lines.filter((l) => !l.toLowerCase().includes(keyword));
    } else if (input.operation === "update") {
      const keyword = input.content.split(":")[0].toLowerCase().trim();
      const replaceIdx = lines.findIndex((l) =>
        l.toLowerCase().startsWith(keyword),
      );
      if (replaceIdx !== -1) {
        lines[replaceIdx] = input.content;
      } else {
        lines.push(input.content);
      }
    }

    writeFileSync(this.memoryPath, lines.join("\n"), "utf-8");
    log.tool.info("update_memory.execute: written", {
      operation: input.operation,
      lines: lines.length,
    });
    return `MEMORY.md updated (${input.operation} in "${input.section}").`;
  }
}
