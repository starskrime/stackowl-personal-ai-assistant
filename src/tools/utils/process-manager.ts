import { exec } from "node:child_process";
import type { ToolImplementation, ToolContext } from "../registry.js";

function execPromise(cmd: string, timeout = 15000): Promise<string> {
  return new Promise((resolve, reject) => {
    exec(cmd, { timeout }, (error, stdout, stderr) => {
      if (error) {
        reject(new Error(stderr || error.message));
      } else {
        resolve(stdout);
      }
    });
  });
}

export const ProcessManagerTool: ToolImplementation = {
  definition: {
    name: "process_manager",
    description:
      "Manage system processes — list top processes, find by name, or kill by PID.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            'Action to perform: "list" (top processes), "find" (by name), "kill" (by PID)',
        },
        name: {
          type: "string",
          description: "Process name to find (for find action)",
        },
        pid: {
          type: "number",
          description: "Process ID to kill (for kill action)",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const action = String(args.action);

      switch (action) {
        case "list": {
          // macOS uses -r for sort by CPU
          const output = await execPromise("ps aux -r | head -20");
          return `Top processes by CPU:\n${output}`;
        }

        case "find": {
          const name = args.name ? String(args.name) : "";
          if (!name) {
            return 'Error: "name" parameter is required for find action.';
          }
          const output = await execPromise(
            `ps aux | grep -i "${name.replace(/"/g, '\\"')}" | grep -v grep`,
          );
          if (!output.trim()) {
            return `No processes found matching "${name}"`;
          }
          return `Processes matching "${name}":\n${output}`;
        }

        case "kill": {
          const pid = args.pid ? Number(args.pid) : NaN;
          if (!isFinite(pid) || pid <= 0) {
            return 'Error: Valid "pid" parameter is required for kill action.';
          }
          await execPromise(`kill -15 ${Math.floor(pid)}`);
          return `Sent SIGTERM to process ${Math.floor(pid)}`;
        }

        default:
          return `Error: Unknown action "${action}". Use: list, find, or kill.`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error managing processes: ${msg}`;
    }
  },
};
