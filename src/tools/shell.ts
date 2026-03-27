/**
 * StackOwl — Shell Tool (Zero-Trust Sandboxed)
 *
 * Allows owls to execute terminal commands safely inside ephemeral Docker containers.
 *
 * Output capture: uses child_process.spawn (not dockerode stream API) so stdout/stderr
 * are always returned to the LLM, not just printed to the terminal.
 */

import { spawn, exec } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { resolve } from "node:path";

const execAsync = promisify(exec);

const SANDBOX_IMAGE = "node:22-alpine";
const EXEC_TIMEOUT_MS = 30_000;
const IMAGE_PULL_TIMEOUT_MS = 120_000;

// ─── Pre-flight: Network Command Detection ────────────────────────────────────
// Network commands are now ALLOWED by default. This detection was removed to allow full internet access.

// ─── Docker Spawn (proper stdout/stderr capture) ──────────────────────────────

interface DockerResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

function runInDocker(
  cmd: string,
  workspaceDir: string,
  allowNetwork: boolean = true,
): Promise<DockerResult> {
  return new Promise((resolvePromise, reject) => {
    const dockerArgs = [
      "run",
      "--rm",
      "--volume",
      `${workspaceDir}:/workspace`,
      "--workdir",
      "/workspace",
      "--env",
      "NODE_ENV=development",
    ];

    // Full network access by default - user can set allowNetwork=false to disable
    if (!allowNetwork) {
      dockerArgs.push("--network", "none");
    }

    dockerArgs.push(SANDBOX_IMAGE, "sh", "-c", cmd);

    const proc = spawn("docker", dockerArgs);

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    proc.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      reject(new Error(`Sandbox timed out after ${EXEC_TIMEOUT_MS / 1000}s`));
    }, EXEC_TIMEOUT_MS);

    proc.on("close", (code) => {
      clearTimeout(timer);
      resolvePromise({ exitCode: code ?? 0, stdout, stderr });
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

// ─── Tier 1 Auto-Heal: Pull missing image ────────────────────────────────────

async function ensureImage(): Promise<void> {
  log.tool.warn(
    `[ShellTool] Image '${SANDBOX_IMAGE}' not found. Auto-pulling (Tier 1 heal)...`,
  );
  await execAsync(`docker pull ${SANDBOX_IMAGE}`, {
    timeout: IMAGE_PULL_TIMEOUT_MS,
  });
  log.tool.info(`[ShellTool] Image '${SANDBOX_IMAGE}' pulled successfully.`);
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function cap(s: string, max: number): string {
  return s.length > max
    ? s.slice(0, max) + `\n...[truncated — ${s.length - max} chars omitted]`
    : s;
}

function formatResult(
  exitCode: number,
  stdout: string,
  stderr: string,
  diagnosticHint = "",
): string {
  return [
    `EXIT_CODE: ${exitCode}`,
    `STDOUT:\n${cap(stdout, 6000) || "(none)"}`,
    `STDERR:\n${cap(stderr, 2000) || "(none)"}${diagnosticHint ? "\n\n" + diagnosticHint : ""}`,
  ].join("\n\n");
}

function buildDiagnosticHint(
  exitCode: number,
  stdout: string,
  stderr: string,
): string {
  const combined = stdout + stderr;

  if (
    exitCode === 127 ||
    combined.includes("not found") ||
    combined.includes("No such file or directory")
  ) {
    return (
      `[SYSTEM DIAGNOSTIC HINT: A command was not found in the Alpine Linux sandbox. ` +
      `Alpine only includes busybox utilities by default. ` +
      `To install a missing package: chain 'apk add <pkg> && <your command>' in one command. ` +
      `Note: Network commands (curl, wget) ARE now supported - you can fetch URLs directly.]`
    );
  }
  if (exitCode === 126) {
    return `[SYSTEM DIAGNOSTIC HINT: Permission denied. Check file permissions (chmod +x) before executing.]`;
  }
  if (
    combined.toLowerCase().includes("out of memory") ||
    combined.includes("Killed")
  ) {
    return `[SYSTEM DIAGNOSTIC HINT: Process was killed (OOM or timeout). Try a smaller input or break the task into steps.]`;
  }
  return `[SYSTEM DIAGNOSTIC HINT: Non-zero exit code ${exitCode}. Check the stderr above for the root cause.]`;
}

// ─── Raw (unsandboxed) fallback ───────────────────────────────────────────────

const DANGEROUS_PATTERNS = [
  {
    pattern: /rm\s+-rf\s+[\/~]/,
    label: "Recursive force delete of root/home directory",
  },
  { pattern: /dd\s+.*of=\//, label: "Disk dd to raw device (/)" },
  { pattern: /mkfs\./, label: "Filesystem format command" },
  { pattern: /cryptsetup\s+luksFormat/, label: "LUKS encryption format" },
  { pattern: /\|\s*nc\s+.*-e\s*$/, label: "Netcat reverse shell pattern" },
  { pattern: />\s*\/etc\/passwd/, label: "Writing to /etc/passwd" },
  {
    pattern: /chmod\s+-R\s+777\s+\//,
    label: " chmod -R 777 / (world-writable root)",
  },
  {
    pattern: /curl.*\|.*sh$/,
    label: "Pipe curl to shell (one-liner install pattern, often malicious)",
  },
  {
    pattern: /eval\s+\$\(curl/,
    label: "eval curl output directly (remote code execution)",
  },
  {
    pattern: /wget.*\|.*sh$/,
    label: "Pipe wget to shell (one-liner install pattern, often malicious)",
  },
  {
    pattern: /ssh\s+.*--\s*.*@.*\s+['"];?\s*rm/,
    label: "SSH followed by rm (potentially destructive)",
  },
  {
    pattern: /shutdown|reboot|init\s+0|init\s+6/,
    label: "System shutdown/reboot command",
  },
  {
    pattern: /curl\s+.*(-o|--output)\s+\S+.*\|.*sh/,
    label: "curl download then pipe to shell",
  },
  {
    pattern: /curl\s+.*(-o|--output)\s+\S+.*;\s*sh/,
    label: "curl download then execute with sh",
  },
  {
    pattern: /wget\s+.*(-O|--output-document)\s+\S+.*\|.*sh/,
    label: "wget download then pipe to shell",
  },
  {
    pattern: /wget\s+.*(-O|--output-document)\s+\S+.*;\s*sh/,
    label: "wget download then execute with sh",
  },
  {
    pattern: /curl\s+.*\.(sh|bash)\s*\|.*sh/,
    label: "curl downloading script piped to shell",
  },
  {
    pattern: /wget\s+.*\.(sh|bash)\s*\|.*sh/,
    label: "wget downloading script piped to shell",
  },
  {
    pattern: /base64\s+-d\s+.*\|.*sh/,
    label: "base64 decode then pipe to shell",
  },
  {
    pattern: /python.*-c.*\|.*sh/,
    label: "Python one-liner piped to shell",
  },
  {
    pattern: /ruby.*-e.*\|.*sh/,
    label: "Ruby one-liner piped to shell",
  },
  {
    pattern: /perl.*\|.*sh/,
    label: "Perl one-liner piped to shell",
  },
];

function detectDangerousCommand(cmd: string): string | null {
  for (const { pattern, label } of DANGEROUS_PATTERNS) {
    if (pattern.test(cmd)) {
      return label;
    }
  }
  return null;
}

async function executeRawCommand(cmd: string, cwd: string): Promise<string> {
  const MAX_COMMAND_LENGTH = 50000;
  if (cmd.length > MAX_COMMAND_LENGTH) {
    return formatResult(
      1,
      "",
      `Command exceeds maximum length of ${MAX_COMMAND_LENGTH} characters.`,
    );
  }
  const dangerousReason = detectDangerousCommand(cmd);
  if (dangerousReason) {
    log.tool.error(
      `[ShellTool] ⚠️ DANGEROUS COMMAND DETECTED in local mode: ${dangerousReason}`,
    );
    log.tool.error(`[ShellTool] Command: ${cmd.slice(0, 200)}`);
    return formatResult(
      1,
      "",
      `Command blocked: ${dangerousReason}.\nThis command was rejected because it matches a known dangerous pattern.\nIf you genuinely need to run this command, use sandbox mode or disable the dangerous command guard in config.`,
    );
  }
  log.tool.warn(
    `[ShellTool] ⚠️ Executing on HOST (local mode): ${cmd.slice(0, 200)}`,
  );
  try {
    const { stdout, stderr } = await execAsync(cmd, {
      cwd,
      timeout: EXEC_TIMEOUT_MS,
    });
    return formatResult(0, stdout, stderr);
  } catch (error: any) {
    const isTimeout =
      error.message?.includes("maxBuffer") ||
      error.message?.includes("Callback called more than");
    return formatResult(
      isTimeout ? 124 : (error.code ?? 1),
      error.stdout ?? "",
      isTimeout
        ? `Command timed out after ${EXEC_TIMEOUT_MS / 1000}s`
        : (error.stderr ?? ""),
      isTimeout
        ? `[SYSTEM DIAGNOSTIC HINT: Command timed out after ${EXEC_TIMEOUT_MS / 1000}s. Try a simpler command or break the task into smaller steps.]`
        : "",
    );
  }
}

// ─── Tool ────────────────────────────────────────────────────────────────────

export const ShellTool: ToolImplementation = {
  definition: {
    name: "run_shell_command",
    description:
      "Execute a shell command on the host machine. Use for: running code/scripts, " +
      "processing files, system tasks (screenshots via 'screencapture', clipboard, notifications), " +
      "installing packages, or any OS-level operation. " +
      "Has full internet access — curl, wget, node, python all available. " +
      "30-second timeout. For reading/writing files, prefer read_file/write_file tools instead.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "The shell command to execute.",
        },
        mode: {
          type: "string",
          enum: ["local", "sandbox"],
          description:
            "Execution mode. 'local': Runs directly on host machine (full access). 'sandbox': Runs in isolated Alpine Docker container (safe). The Assistant should prioritize 'sandbox' unless you specifically require local host dependencies to achieve your goal.",
        },
      },
      required: ["command", "mode"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const cmd = args["command"] as string;
    const mode = args["mode"] as string;
    if (!cmd) throw new Error("Command argument missing");
    if (!mode) throw new Error("Mode argument missing");

    const execConfig = context.engineContext?.config?.execution ?? {
      hostMode: true,
      sandboxMode: true,
    };
    const workspaceDir = resolve(context.cwd);

    if (mode === "local") {
      if (!execConfig.hostMode) {
        return formatResult(
          1,
          "",
          `Host execution is disabled in stackowl.config.json. Please adjust 'execution.hostMode' to use local mode, or switch to 'sandbox' mode.`,
        );
      }
      log.tool.warn(`[ShellTool] WARNING: Executing on host: ${cmd}`);
      return executeRawCommand(cmd, workspaceDir);
    }

    // mode === "sandbox"
    if (!execConfig.sandboxMode) {
      return formatResult(
        1,
        "",
        `Sandbox execution is disabled in stackowl.config.json. Please adjust 'execution.sandboxMode' to use the sandbox, or switch to 'local' mode.`,
      );
    }

    // Full network access is enabled - curl/wget can be used directly

    log.tool.info(`[ShellTool] Executing in sandbox: ${cmd}`);

    try {
      const result = await runInDocker(cmd, workspaceDir);

      const combined = result.stdout + result.stderr;
      if (
        result.exitCode !== 0 &&
        (combined.includes("Cannot connect to the Docker daemon") ||
          combined.includes("failed to connect to the docker API") ||
          combined.includes("docker.sock") ||
          combined.includes("Is the docker daemon running"))
      ) {
        log.tool.error(
          `[ShellTool] Docker daemon unavailable (detected from output). Sandbox execution blocked.`,
        );
        return formatResult(
          1,
          "",
          combined,
          "[SYSTEM DIAGNOSTIC HINT: Cannot execute sandbox command because Docker is not running. Sandbox mode requires Docker. Start Docker to continue, or switch to 'local' mode if you absolutely trust the command.]",
        );
      }

      if (result.exitCode !== 0) {
        const hint = buildDiagnosticHint(
          result.exitCode,
          result.stdout,
          result.stderr,
        );
        log.tool.warn(
          `[ShellTool] Command exited with code ${result.exitCode}`,
        );
        return formatResult(
          result.exitCode,
          result.stdout,
          result.stderr,
          hint,
        );
      }

      return formatResult(0, result.stdout, result.stderr);
    } catch (spawnError: any) {
      const msg: string = spawnError.message ?? String(spawnError);

      // ── Tier 1 Auto-Heal: missing Docker image ──
      if (
        msg.includes("Unable to find image") ||
        msg.includes("No such image") ||
        msg.includes("pull access denied")
      ) {
        try {
          await ensureImage();
          const retryResult = await runInDocker(cmd, workspaceDir);
          if (retryResult.exitCode !== 0) {
            const hint = buildDiagnosticHint(
              retryResult.exitCode,
              retryResult.stdout,
              retryResult.stderr,
            );
            return formatResult(
              retryResult.exitCode,
              retryResult.stdout,
              retryResult.stderr,
              hint,
            );
          }
          return formatResult(0, retryResult.stdout, retryResult.stderr);
        } catch (pullErr: any) {
          log.tool.error(`[ShellTool] Auto-heal pull failed:`, pullErr);
          return formatResult(
            1,
            "",
            String(pullErr.message ?? pullErr),
            `[SYSTEM DIAGNOSTIC HINT: Docker image pull failed. Docker daemon may be unavailable or the image registry is unreachable. Consider disabling sandboxing in config.]`,
          );
        }
      }

      // ── Docker daemon not running → Block Sandbox Escape ──
      if (
        msg.includes("Cannot connect to the Docker daemon") ||
        msg.includes("connect ENOENT") ||
        msg.includes("ENOENT")
      ) {
        log.tool.error(
          `[ShellTool] Docker daemon unavailable. Sandbox execution blocked.`,
        );
        return formatResult(
          1,
          "",
          msg,
          "[SYSTEM DIAGNOSTIC HINT: Cannot execute sandbox command because Docker is not running. Sandbox mode requires Docker. Start Docker to continue, or switch to 'local' mode if you absolutely trust the command.]",
        );
      }

      // ── Timeout ──
      if (msg.includes("timed out")) {
        return formatResult(
          124,
          "",
          msg,
          `[SYSTEM DIAGNOSTIC HINT: Command timed out after ${EXEC_TIMEOUT_MS / 1000}s. Break long-running tasks into smaller steps or increase the timeout in config.]`,
        );
      }

      log.tool.error(`[ShellTool] Unexpected spawn error:`, spawnError);
      return formatResult(
        1,
        "",
        msg,
        `[SYSTEM DIAGNOSTIC HINT: Unexpected sandbox error. Check Docker is running on this machine.]`,
      );
    }
  },
};
