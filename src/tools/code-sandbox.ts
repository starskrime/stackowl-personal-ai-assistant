// src/tools/code-sandbox.ts
import { spawn } from "node:child_process";
import { writeFile, rm, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { platform } from "../platform/index.js";
import { SANDBOX_IMAGES } from "../platform/capabilities/system-info.js";

const DEFAULT_TIMEOUT_MS = 30_000;

export interface SandboxRunOptions {
  language: "python" | "javascript" | "typescript";
  code: string;
  timeoutMs: number;
  allowNetwork: boolean;
  workspaceAccess: "none" | "ro" | "rw";
  packages: string[];
  cwd: string;
}

export interface SandboxRunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  via: "docker" | "host";
  warning?: string;
  timedOut: boolean;
  oomKilled?: boolean;
}

function imageForLanguage(lang: SandboxRunOptions["language"]): string {
  return lang === "python" ? SANDBOX_IMAGES.python : SANDBOX_IMAGES.node;
}

function interpreterForLanguage(lang: SandboxRunOptions["language"]): string[] {
  if (lang === "python") return ["python", "-"];
  if (lang === "typescript") return ["sh", "-c", "tsx -"];
  return ["node", "-"];
}

export async function runInDocker(opts: SandboxRunOptions): Promise<SandboxRunResult> {
  const caps = platform.systemInfo.current().capabilities;
  const image = imageForLanguage(opts.language);
  const imageKey = opts.language === "python" ? "python" : "node";
  if (!caps.hasDockerImagesPulled[imageKey]) {
    return {
      exitCode: null, stdout: "", stderr: "",
      durationMs: 0, via: "docker", timedOut: false,
      warning: `E_IMAGE_NOT_PULLED: Sandbox image '${image}' not present. Run: docker pull ${image}`,
    };
  }

  const dockerArgs = [
    "run", "--rm", "-i",
    "--network", opts.allowNetwork ? "bridge" : "none",
    "--memory=512m", "--memory-swap=512m",
    "--cpus=1", "--pids-limit=100",
    "--read-only",
    "--tmpfs", "/tmp:size=64m,exec",
    "--tmpfs", "/work-out:size=16m",
    "--user", "65534:65534",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
  ];
  if (opts.workspaceAccess !== "none") {
    dockerArgs.push("-v", `${opts.cwd}:/work:${opts.workspaceAccess}`);
    dockerArgs.push("-w", "/work");
  }
  dockerArgs.push("-e", "PYTHONDONTWRITEBYTECODE=1");
  dockerArgs.push("-e", "NODE_OPTIONS=--no-warnings");
  dockerArgs.push(image);
  dockerArgs.push(...interpreterForLanguage(opts.language));

  const cmd = `docker ${dockerArgs.map(a => /["\s]/.test(a) ? JSON.stringify(a) : a).join(" ")}`;
  log.tool.debug("code-sandbox.runInDocker: spawning", { image, allowNetwork: opts.allowNetwork, workspaceAccess: opts.workspaceAccess });

  const r = await platform.shell.exec(cmd, { timeoutMs: opts.timeoutMs, inputStdin: opts.code });
  return {
    exitCode: r.exitCode,
    stdout: r.stdout,
    stderr: r.stderr,
    durationMs: r.durationMs,
    via: "docker",
    timedOut: r.timedOut,
    oomKilled: r.exitCode === 137,
  };
}

export const CodeSandboxTool: ToolImplementation = {
  definition: {
    name: "sandbox",
    description:
      "Execute a Python or JavaScript code snippet in a temporary subprocess. " +
      "Returns stdout, stderr, and exit code. Timeout defaults to 30s. " +
      'Example: sandbox(language: "javascript", code: "console.log(2+2)")',
    parameters: {
      type: "object",
      properties: {
        language: {
          type: "string",
          enum: ["python", "javascript"],
          description: "Programming language to execute.",
        },
        code: {
          type: "string",
          description: "Code snippet to run.",
        },
        timeout: {
          type: "number",
          description: "Timeout in milliseconds. Default: 30000.",
        },
      },
      required: ["language", "code"],
    },
    capabilities: ["code_execution", "sandbox"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  category: "shell",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    const language = args["language"] as string;
    const code     = args["code"]     as string;
    const timeout  = typeof args["timeout"] === "number"
      ? (args["timeout"] as number)
      : DEFAULT_TIMEOUT_MS;

    if (!language) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "language is required" } });
    if (!code)     return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "code is required" } });

    // 1. ENTRY
    log.tool.debug("sandbox.execute: entry", { language, codeLen: code.length, timeout });

    const dir  = await mkdtemp(join(tmpdir(), "stackowl-sandbox-"));
    const ext  = language === "python" ? "py" : "js";
    const file = join(dir, `script.${ext}`);
    await writeFile(file, code, "utf-8");

    const cmd     = language === "python" ? "python3" : "node";
    const cmdArgs = [file];

    // 2. DECISION — language runtime selection
    log.tool.debug("sandbox.execute: runtime selected", { cmd, file });

    return new Promise<string>((resolve) => {
      let stdout = "";
      let stderr = "";
      let timedOut = false;

      // 3. STEP — subprocess spawned
      log.tool.debug("sandbox.execute: spawning subprocess", { cmd, cwd: dir });
      const child = spawn(cmd, cmdArgs, { cwd: dir });

      const timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGKILL");
        log.tool.debug("sandbox.execute: timeout triggered", { timeout });
      }, timeout);

      child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
      child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

      child.on("close", (exitCode) => {
        clearTimeout(timer);
        rm(dir, { recursive: true }).catch((err) => { log.tool.warn("sandbox temp-dir cleanup failed", err); });

        if (timedOut) {
          const result = JSON.stringify({
            success: false,
            error: { code: "TIMEOUT", message: `Execution exceeded ${timeout}ms` },
          });
          log.tool.debug("sandbox.execute: exit", { success: false, reason: "TIMEOUT", timeout });
          resolve(result);
          return;
        }

        // 4. EXIT
        log.tool.debug("sandbox.execute: exit", { success: true, exitCode: exitCode ?? 0, stdoutLen: stdout.length, stderrLen: stderr.length });
        resolve(JSON.stringify({
          success: true,
          data: { stdout, stderr, exitCode: exitCode ?? 0 },
        }));
      });

      child.on("error", (err) => {
        clearTimeout(timer);
        rm(dir, { recursive: true }).catch((err) => { log.tool.warn("sandbox temp-dir cleanup failed", err); });
        log.tool.error("sandbox.execute: spawn failed", err as Error, { cmd, language });
        resolve(JSON.stringify({
          success: false,
          error: { code: "SPAWN_ERROR", message: (err as Error).message },
        }));
      });
    });
  },
};
