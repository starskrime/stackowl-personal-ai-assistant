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
          enum: [
            "status", "log", "diff", "branch", "stash",
            "add", "commit", "fetch", "push", "pull",
            "checkout", "merge", "rebase", "reset",
            "branch_create", "branch_delete", "tag"
          ],
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
          description: "Commit/tag message. Used with commit and tag actions.",
        },
        paths: {
          type: "string",
          description: 'Comma-separated paths for action:add (use "." for all)',
        },
        amend: {
          type: "boolean",
          description: "Amend the previous commit (action:commit)",
        },
        target: {
          type: "string",
          description: "Branch/commit/file target (action:checkout/reset)",
        },
        create_branch: {
          type: "boolean",
          description: "Create branch on checkout (-b)",
        },
        remote: {
          type: "string",
          description: "Remote name (default origin)",
        },
        branch: {
          type: "string",
          description: "Branch name for action:push/pull/branch_create",
        },
        from: {
          type: "string",
          description: "Source ref for action:branch_create",
        },
        name: {
          type: "string",
          description: "Branch/tag name",
        },
        force: {
          type: "boolean",
          description:
            "Force flag (push/branch_delete). Destructive — requires i_understand_destructive.",
        },
        mode: {
          type: "string",
          enum: ["soft", "mixed", "hard"],
          description: "Reset mode: soft|mixed|hard (default mixed)",
        },
        rebase: {
          type: "boolean",
          description: "Pull --rebase",
        },
        no_ff: {
          type: "boolean",
          description: "Merge --no-ff",
        },
        abort: {
          type: "boolean",
          description: "Abort in-progress merge/rebase",
        },
        continue: {
          type: "boolean",
          description: "Continue in-progress rebase",
        },
        onto: {
          type: "string",
          description: "Rebase --onto target",
        },
        delete: {
          type: "boolean",
          description: "Delete tag (action:tag)",
        },
        i_understand_destructive: {
          type: "boolean",
          description:
            "Required for destructive actions (force push, hard reset, force branch delete)",
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

    // Destructive action gate — must come BEFORE switch statement
    const isDestructive =
      (action === "push" && args.force === true) ||
      (action === "reset" && (args.mode as string) === "hard") ||
      (action === "branch_delete" && args.force === true);

    if (isDestructive && args.i_understand_destructive !== true) {
      log.tool.warn("git_tool: destructive action blocked", { action, force: args.force, mode: args.mode });
      return JSON.stringify({
        success: false,
        error: {
          code: "DESTRUCTIVE_ACTION_BLOCKED",
          message: `${action}${args.force ? " --force" : ""}${(args.mode as string) === "hard" ? " --hard" : ""} is destructive. Pass i_understand_destructive: true to proceed.`,
          hint: "This action can permanently destroy work. Confirm with the user before retrying.",
        },
      });
    }

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
        case "add": {
          const pathsArg = args["paths"] as string | string[] | undefined;
          const paths = Array.isArray(pathsArg) ? pathsArg : (pathsArg ? pathsArg.split(",").map(s => s.trim()) : ["."]);
          const r = await gitCmd(cwd, ["add", ...paths]);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { staged: paths } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr || `exit ${r.exitCode}` } });
        }

        case "commit": {
          const message = args["message"] as string;
          const amend = args["amend"] === true;
          if (!message && !amend) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
          const cmdArgs = ["commit"];
          if (amend) cmdArgs.push("--amend");
          if (message) cmdArgs.push("-m", message);
          const r = await gitCmd(cwd, cmdArgs);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
        }

        case "fetch": {
          const remote = (args["remote"] as string) ?? "origin";
          const r = await gitCmd(cwd, ["fetch", remote]);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || `fetched ${remote}` } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
        }

        case "push": {
          const remote = (args["remote"] as string) ?? "origin";
          const branch = args["branch"] as string | undefined;
          const force = args["force"] === true;
          const cmdArgs = ["push"];
          if (force) cmdArgs.push("--force-with-lease");
          cmdArgs.push(remote);
          if (branch) cmdArgs.push(branch);
          log.tool.warn("git_tool.push: destructive action proceeding (audit)", { remote, branch, force, cmd: cmdArgs });
          const r = await gitCmd(cwd, cmdArgs, 60_000);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || "push complete" } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
        }

        case "pull": {
          const remote = (args["remote"] as string) ?? "origin";
          const branch = args["branch"] as string | undefined;
          const rebase = args["rebase"] === true;
          const cmdArgs = ["pull"];
          if (rebase) cmdArgs.push("--rebase");
          cmdArgs.push(remote);
          if (branch) cmdArgs.push(branch);
          const r = await gitCmd(cwd, cmdArgs, 60_000);
          return r.exitCode === 0
            ? JSON.stringify({ success: true, data: { stdout: r.stdout.trim() || "pull complete" } })
            : JSON.stringify({ success: false, error: { code: "GIT_ERROR", message: r.stderr.trim() || `exit ${r.exitCode}` } });
        }

        default:
          return `Unknown action: ${action}. Use status, log, diff, branch, stash, add, commit, fetch, push, or pull.`;
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
