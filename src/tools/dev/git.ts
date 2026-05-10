import { exec } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";

const execAsync = promisify(exec);
const TIMEOUT_MS = 15000;

export const GitTool: ToolImplementation = {
  definition: {
    name: "git_tool",
    description:
      "Git operations — status, log, diff, branch info, and stash management. Works in the current workspace.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["status", "log", "diff", "branch", "stash"],
          description: "Git action to perform.",
        },
        count: {
          type: "number",
          description:
            "Number of log entries to show (default 10). Used with log.",
        },
        stash_action: {
          type: "string",
          enum: ["list", "save", "pop"],
          description: 'Stash sub-action (default "list"). Used with stash.',
        },
        message: {
          type: "string",
          description: "Stash save message. Used with stash save.",
        },
      },
      required: ["action"],
    },
  },

  async execute(args, context) {
    const action = args.action as string;
    const count = (args.count as number) ?? 10;
    const stashAction = (args.stash_action as string) ?? "list";
    const message = args.message as string | undefined;
    const cwd = context.cwd;

    // 1. ENTRY
    log.tool.debug("git_tool.execute: entry", { action, stashAction, cwd });

    try {
      let cmd: string;

      switch (action) {
        case "status":
          cmd = "git status --short --branch";
          break;
        case "log":
          cmd = `git log --oneline --graph -n ${count}`;
          break;
        case "diff":
          cmd = "git diff && echo '\\n--- STAGED ---\\n' && git diff --staged";
          break;
        case "branch":
          cmd =
            "git branch -a --format='%(if)%(HEAD)%(then)* %(end)%(refname:short) %(upstream:short)'";
          break;
        case "stash":
          switch (stashAction) {
            case "list":
              cmd = "git stash list";
              break;
            case "save":
              cmd = message ? `git stash save "${message}"` : "git stash";
              break;
            case "pop":
              cmd = "git stash pop";
              break;
            default:
              return `Unknown stash action: ${stashAction}. Use list, save, or pop.`;
          }
          break;
        default:
          return `Unknown action: ${action}. Use status, log, diff, branch, or stash.`;
      }

      // 2. DECISION — local vs remote operation
      const isRemoteOp = action === "log" || (action === "stash" && stashAction === "pop");
      log.tool.debug("git_tool.execute: command built", { cmd, isRemoteOp });

      // 3. STEP — subprocess spawned
      const { stdout, stderr } = await execAsync(cmd, {
        timeout: TIMEOUT_MS,
        cwd,
      });
      const output = (stdout || "").trim();
      const errors = (stderr || "").trim();

      log.tool.debug("git_tool.execute: command complete", { outputLen: output.length, hasStderr: !!errors });

      if (!output && !errors) return `git ${action}: no output (clean state).`;
      if (errors && !output) return errors;
      if (errors) return `${output}\n\n(stderr: ${errors})`;

      // 4. EXIT
      log.tool.debug("git_tool.execute: exit", { success: true, resultLen: output.length });
      return output;
    } catch (e) {
      log.tool.error("git_tool.execute: command failed", e instanceof Error ? e : new Error(String(e)), { action });
      return `git_tool error: ${e instanceof Error ? e.message : String(e)}`;
    }
  },
};
