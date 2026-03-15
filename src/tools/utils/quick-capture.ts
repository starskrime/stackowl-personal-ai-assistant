import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

function getCapturePath(context: ToolContext): string {
  return join(context.cwd, "workspace", "captures.md");
}

export const QuickCaptureTool: ToolImplementation = {
  definition: {
    name: "quick_capture",
    description:
      "Quickly capture a thought, idea, or note. Saved with timestamp to a captures file for later review.",
    parameters: {
      type: "object",
      properties: {
        content: {
          type: "string",
          description: "The note, thought, or idea to capture",
        },
        tag: {
          type: "string",
          description: 'Optional tag/category, e.g. "idea", "todo", "bookmark"',
        },
      },
      required: ["content"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    try {
      const content = String(args.content);
      const tag = args.tag ? String(args.tag) : "";

      if (!content.trim()) {
        return "Error: Content cannot be empty.";
      }

      const capturePath = getCapturePath(context);
      const dir = dirname(capturePath);
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }

      const now = new Date();
      const timestamp = now.toISOString().replace("T", " ").slice(0, 19);
      const tagStr = tag ? ` [${tag}]` : "";

      // Add header if file doesn't exist
      let prefix = "";
      if (!existsSync(capturePath)) {
        prefix = "# Quick Captures\n\n";
      }

      const entry = `${prefix}- **${timestamp}**${tagStr}: ${content}\n`;
      appendFileSync(capturePath, entry, "utf-8");

      return `Captured${tagStr}: "${content.slice(0, 60)}${content.length > 60 ? "..." : ""}" at ${timestamp}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error capturing note: ${msg}`;
    }
  },
};
