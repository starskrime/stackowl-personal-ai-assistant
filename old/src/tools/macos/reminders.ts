import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";
import { log } from "../../logger.js";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

export const AppleRemindersTool: ToolImplementation = {
  definition: {
    name: "apple_reminders",
    description:
      "Manage macOS Reminders — list tasks, add new reminders with due dates, or mark them complete.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["list", "add", "complete"],
          description:
            "Action to perform: list incomplete reminders, add a new reminder, or mark one complete.",
        },
        title: {
          type: "string",
          description: "Reminder title (required for 'add' and 'complete').",
        },
        due_date: {
          type: "string",
          description: "Optional due date in YYYY-MM-DD format (for 'add').",
        },
        list_name: {
          type: "string",
          description:
            "Optional reminders list name (for 'add'). Defaults to the default list.",
        },
      },
      required: ["action"],
    },
    capabilities: ["reminder_query", "reminder_create"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 1, retryDelayMs: 1_000 },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;
    log.tool.debug("apple_reminders.execute: entry", { action });

    try {
      switch (action) {
        case "list": {
          log.tool.debug("apple_reminders.execute: listing incomplete reminders via AppleScript");
          const script = `
tell application "Reminders"
    set output to ""
    repeat with rem in (every reminder whose completed is false)
        set remName to name of rem
        set remList to name of container of rem
        set duePart to ""
        try
            set dueDate to due date of rem
            set duePart to " | Due: " & (dueDate as string)
        end try
        set output to output & remList & " | " & remName & duePart & linefeed
    end repeat
    if output is "" then
        return "No incomplete reminders found."
    end if
    return output
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          const result = stdout.trim() || "No incomplete reminders found.";
          log.tool.debug("apple_reminders.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "add": {
          const title = args.title as string;
          if (!title) {
            return "Error: 'add' action requires a title parameter.";
          }

          const listName = args.list_name as string | undefined;
          const dueDate = args.due_date as string | undefined;

          log.tool.debug("apple_reminders.execute: creating reminder via AppleScript", { title, dueDate, listName });

          let dueDatePart = "";
          if (dueDate) {
            const [year, month, day] = dueDate.split("-");
            dueDatePart = `
    set dueD to current date
    set year of dueD to ${year}
    set month of dueD to ${month}
    set day of dueD to ${day}
    set hours of dueD to 9
    set minutes of dueD to 0
    set seconds of dueD to 0
    set due date of newReminder to dueD`;
          }

          const listTarget = listName
            ? `list "${escapeForShell(listName)}"`
            : "default list";

          const script = `
tell application "Reminders"
    set newReminder to make new reminder in ${listTarget} with properties {name:"${escapeForShell(title)}"}${dueDatePart}
    return "Reminder created: ${escapeForShell(title)}"
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          const result = stdout.trim() || `Reminder "${title}" created successfully.`;
          log.tool.debug("apple_reminders.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        case "complete": {
          const title = args.title as string;
          if (!title) {
            return "Error: 'complete' action requires a title parameter.";
          }

          log.tool.debug("apple_reminders.execute: marking reminder complete via AppleScript", { title });
          const script = `
tell application "Reminders"
    set matchedReminders to (every reminder whose name is "${escapeForShell(title)}" and completed is false)
    if (count of matchedReminders) is 0 then
        return "No incomplete reminder found with title: ${escapeForShell(title)}"
    end if
    set completed of item 1 of matchedReminders to true
    return "Marked as complete: ${escapeForShell(title)}"
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          const result = stdout.trim() || `Reminder "${title}" marked as complete.`;
          log.tool.debug("apple_reminders.execute: exit", { success: true, action, resultLen: result.length });
          return result;
        }

        default:
          return `Error: Unknown action "${action}". Use "list", "add", or "complete".`;
      }
    } catch (error) {
      log.tool.error("apple_reminders.execute: failed", error instanceof Error ? error : new Error(String(error)), { action });
      const msg = error instanceof Error ? error.message : String(error);
      return `Error interacting with Reminders: ${msg}`;
    }
  },
};
