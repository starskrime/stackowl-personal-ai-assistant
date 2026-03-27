import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

export const NotificationTool: ToolImplementation = {
  definition: {
    name: "send_notification",
    description:
      "Show a macOS notification to the user. Use for alerts, reminders, or when you need to get the user's attention.",
    parameters: {
      type: "object",
      properties: {
        title: {
          type: "string",
          description: "Notification title.",
        },
        message: {
          type: "string",
          description: "Notification message body.",
        },
      },
      required: ["title", "message"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const title = args.title as string;
    const message = args.message as string;

    if (!title || !message) {
      return "Error: Both title and message parameters are required.";
    }

    try {
      const cmd = `osascript -e 'display notification "${escapeForShell(message)}" with title "${escapeForShell(title)}"'`;
      await execAsync(cmd, { timeout: 15000 });
      return `Notification sent: "${title}" — ${message}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error sending notification: ${msg}`;
    }
  },
};
