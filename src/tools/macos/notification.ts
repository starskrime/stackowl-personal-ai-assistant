import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";
import { log } from "../../logger.js";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

export const NotificationTool: ToolImplementation = {
  definition: {
    name: "send_notification",
    deprecated: true,
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
    log.tool.debug("send_notification.execute: entry", { title, messageLen: message?.length });

    if (!title || !message) {
      return "Error: Both title and message parameters are required.";
    }

    try {
      log.tool.debug("send_notification.execute: displaying notification via osascript", { title });
      const cmd = `osascript -e 'display notification "${escapeForShell(message)}" with title "${escapeForShell(title)}"'`;
      await execAsync(cmd, { timeout: 15000 });
      const result = `Notification sent: "${title}" — ${message}`;
      log.tool.debug("send_notification.execute: exit", { success: true, resultLen: result.length });
      return result;
    } catch (error) {
      log.tool.error("send_notification.execute: failed", error instanceof Error ? error : new Error(String(error)), { title });
      const msg = error instanceof Error ? error.message : String(error);
      return `Error sending notification: ${msg}`;
    }
  },
};
