import { exec } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation } from "../registry.js";

const execAsync = promisify(exec);
const TIMEOUT_MS = 15000;

export const CronJobTool: ToolImplementation = {
  definition: {
    name: "system_cron",
    description:
      "Manage system cron jobs — list, add, or remove scheduled tasks from the system crontab.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["list", "add", "remove"],
          description: "Cron action: list, add, or remove.",
        },
        schedule: {
          type: "string",
          description:
            'Cron schedule expression (e.g. "0 * * * *" for hourly). Required for "add".',
        },
        command: {
          type: "string",
          description: 'Command to schedule. Required for "add".',
        },
        line_number: {
          type: "number",
          description:
            'Line number to remove (1-based, from "list" output). Required for "remove".',
        },
      },
      required: ["action"],
    },
  },

  async execute(args, _context) {
    const action = args.action as string;
    const schedule = args.schedule as string | undefined;
    const command = args.command as string | undefined;
    const lineNumber = args.line_number as number | undefined;

    try {
      switch (action) {
        case "list": {
          try {
            const { stdout } = await execAsync("crontab -l", { timeout: TIMEOUT_MS });
            const lines = stdout.trim();
            if (!lines) return "Crontab is empty.";
            const numbered = lines
              .split("\n")
              .map((line, i) => `${i + 1}: ${line}`)
              .join("\n");
            return `Current crontab:\n${numbered}`;
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            if (msg.includes("no crontab")) return "No crontab exists for the current user.";
            return `Error listing crontab: ${msg}`;
          }
        }

        case "add": {
          if (!schedule) return "Error: schedule is required for the add action.";
          if (!command) return "Error: command is required for the add action.";

          const newEntry = `${schedule} ${command}`;

          // Get existing crontab (may be empty)
          let existing = "";
          try {
            const { stdout } = await execAsync("crontab -l", { timeout: TIMEOUT_MS });
            existing = stdout.trimEnd();
          } catch {
            // No existing crontab, that's fine
          }

          const updated = existing ? `${existing}\n${newEntry}` : newEntry;

          // Use printf to pipe into crontab
          await execAsync(`printf '%s\\n' '${updated.replace(/'/g, "'\\''")}' | crontab -`, {
            timeout: TIMEOUT_MS,
          });

          return `Added cron job: ${newEntry}`;
        }

        case "remove": {
          if (lineNumber === undefined) return "Error: line_number is required for the remove action.";

          let existing: string;
          try {
            const { stdout } = await execAsync("crontab -l", { timeout: TIMEOUT_MS });
            existing = stdout.trimEnd();
          } catch {
            return "No crontab exists — nothing to remove.";
          }

          const lines = existing.split("\n");
          if (lineNumber < 1 || lineNumber > lines.length) {
            return `Error: line_number ${lineNumber} is out of range (1-${lines.length}).`;
          }

          const removed = lines[lineNumber - 1];
          lines.splice(lineNumber - 1, 1);

          if (lines.length === 0) {
            await execAsync("crontab -r", { timeout: TIMEOUT_MS });
            return `Removed "${removed}" — crontab is now empty.`;
          }

          const updated = lines.join("\n");
          await execAsync(`printf '%s\\n' '${updated.replace(/'/g, "'\\''")}' | crontab -`, {
            timeout: TIMEOUT_MS,
          });

          return `Removed line ${lineNumber}: "${removed}"`;
        }

        default:
          return `Unknown action: ${action}. Use list, add, or remove.`;
      }
    } catch (e) {
      return `system_cron error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
