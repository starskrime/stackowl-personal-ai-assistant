import type { ToolImplementation } from "../registry.js";
import { log } from "../../logger.js";
import { platform } from "../../platform/index.js";

const TIMEOUT_MS = 15000;

async function gitCmd(
  cwd: string,
  args: string[],
  timeoutMs = TIMEOUT_MS
): Promise<{ stdout: string; stderr: string; exitCode: number | null }> {
  const quoted = args.map(a => (/[\s"'$`\\]/.test(a) ? JSON.stringify(a) : a)).join(" ");
  const result = await platform.shell.exec(`git ${quoted}`, { cwd, timeoutMs });
  return { stdout: result.stdout, stderr: result.stderr, exitCode: result.exitCode };
}

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
      let cmdArgs: string[];

      switch (action) {
        case "status":
          cmdArgs = ["status", "--short", "--branch"];
          break;
        case "log":
          cmdArgs = ["log", "--oneline", "--graph", "-n", String(count)];
          break;
        case "diff":
          // Compose a shell command that shows unstaged and staged diffs
          const { stdout: unstaged, stderr: unstagedErr } = await gitCmd(cwd, ["diff"]);
          const { stdout: staged, stderr: stagedErr } = await gitCmd(cwd, ["diff", "--staged"]);
          const output = (unstaged || "").trim();
          const stagedOutput = (staged || "").trim();
          const errors = [(unstagedErr || "").trim(), (stagedErr || "").trim()].filter(Boolean).join("\n");

          if (!output && !stagedOutput && !errors)
            return `git ${action}: no output (clean state).`;
          if (errors && !output && !stagedOutput) return errors;
          if (errors)
            return `${output}\n\n--- STAGED ---\n${stagedOutput}\n\n(stderr: ${errors})`;

          log.tool.debug("git_tool.execute: exit", { success: true, resultLen: output.length + stagedOutput.length });
          return `${output}\n\n--- STAGED ---\n${stagedOutput}`;
        case "branch":
          cmdArgs = [
            "branch",
            "-a",
            "--format=%(if)%(HEAD)%(then)* %(end)%(refname:short) %(upstream:short)",
          ];
          break;
        case "stash":
          switch (stashAction) {
            case "list":
              cmdArgs = ["stash", "list"];
              break;
            case "save":
              if (message) {
                cmdArgs = ["stash", "save", message];
              } else {
                cmdArgs = ["stash"];
              }
              break;
            case "pop":
              cmdArgs = ["stash", "pop"];
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
      log.tool.debug("git_tool.execute: command built", { action, isRemoteOp });

      // 3. STEP — subprocess spawned via platform.shell.exec
      const { stdout, stderr } = await gitCmd(cwd, cmdArgs, TIMEOUT_MS);
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
