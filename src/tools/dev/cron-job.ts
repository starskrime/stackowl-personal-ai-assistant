import { exec } from "node:child_process";
import { log } from "../../logger.js";
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

    // 1. ENTRY
    log.tool.debug("system_cron.execute: entry", { action, schedule, lineNumber });

    try {
      switch (action) {
        case "list": {
          // 3. STEP
          log.tool.debug("system_cron.execute: listing crontab");
          try {
            const { stdout } = await execAsync("crontab -l", {
              timeout: TIMEOUT_MS,
            });
            const lines = stdout.trim();
            if (!lines) return "Crontab is empty.";
            const numbered = lines
              .split("\n")
              .map((line, i) => `${i + 1}: ${line}`)
              .join("\n");
            const result = `Current crontab:\n${numbered}`;
            // 4. EXIT
            log.tool.debug("system_cron.execute: exit", { success: true, resultLen: result.length });
            return result;
          } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            if (msg.includes("no crontab"))
              return "No crontab exists for the current user.";
            log.tool.error("system_cron.execute: list failed", e instanceof Error ? e : new Error(msg), { action });
            return `Error listing crontab: ${msg}`;
          }
        }

        case "add": {
          if (!schedule)
            return "Error: schedule is required for the add action.";
          if (!command) return "Error: command is required for the add action.";

          const newEntry = `${schedule} ${command}`;

          // 2. DECISION — building new vs appending to existing crontab
          let existing = "";
          try {
            const { stdout } = await execAsync("crontab -l", {
              timeout: TIMEOUT_MS,
            });
            existing = stdout.trimEnd();
          } catch (err) {
            log.tool.warn("system_cron.execute: no existing crontab, creating fresh", err);
            // No existing crontab, that's fine
          }

          const isFirstEntry = !existing;
          log.tool.debug("system_cron.execute: adding entry", { schedule, isFirstEntry, newEntry });

          const updated = existing ? `${existing}\n${newEntry}` : newEntry;

          // 3. STEP — write updated crontab
          await execAsync(
            `printf '%s\\n' '${updated.replace(/'/g, "'\\''")}' | crontab -`,
            {
              timeout: TIMEOUT_MS,
            },
          );

          const result = `Added cron job: ${newEntry}`;
          // 4. EXIT
          log.tool.debug("system_cron.execute: exit", { success: true, resultLen: result.length });
          return result;
        }

        case "remove": {
          if (lineNumber === undefined)
            return "Error: line_number is required for the remove action.";

          // 3. STEP — read existing crontab
          log.tool.debug("system_cron.execute: reading crontab for removal", { lineNumber });
          let existing: string;
          try {
            const { stdout } = await execAsync("crontab -l", {
              timeout: TIMEOUT_MS,
            });
            existing = stdout.trimEnd();
          } catch (err) {
            log.tool.warn("system_cron.execute: crontab read failed during remove", err);
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
            const result = `Removed "${removed}" — crontab is now empty.`;
            log.tool.debug("system_cron.execute: exit", { success: true, resultLen: result.length });
            return result;
          }

          const updated = lines.join("\n");
          await execAsync(
            `printf '%s\\n' '${updated.replace(/'/g, "'\\''")}' | crontab -`,
            {
              timeout: TIMEOUT_MS,
            },
          );

          const result = `Removed line ${lineNumber}: "${removed}"`;
          // 4. EXIT
          log.tool.debug("system_cron.execute: exit", { success: true, resultLen: result.length });
          return result;
        }

        default:
          return `Unknown action: ${action}. Use list, add, or remove.`;
      }
    } catch (e) {
      log.tool.error("system_cron.execute: operation failed", e instanceof Error ? e : new Error(String(e)), { action });
      return `system_cron error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
