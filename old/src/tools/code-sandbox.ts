// src/tools/code-sandbox.ts
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { platform } from "../platform/index.js";
import { SANDBOX_IMAGES } from "../platform/capabilities/system-info.js";

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

export async function runOnHost(opts: SandboxRunOptions): Promise<SandboxRunResult> {
  if (opts.workspaceAccess === "rw") {
    return {
      exitCode: null, stdout: "", stderr: "",
      durationMs: 0, via: "host", timedOut: false,
      warning: "E_UNSAFE_HOST: workspace_access:'rw' rejected without Docker isolation",
    };
  }
  const interpreter =
    opts.language === "python" ? "python3"
    : opts.language === "typescript" ? "tsx"
    : "node";
  const cmd = `${interpreter} -`;
  log.tool.debug("code-sandbox.runOnHost: spawning", { interpreter, timeoutMs: opts.timeoutMs });
  const r = await platform.shell.exec(cmd, { timeoutMs: opts.timeoutMs, inputStdin: opts.code });
  return {
    exitCode: r.exitCode,
    stdout: r.stdout,
    stderr: r.stderr,
    durationMs: r.durationMs,
    via: "host",
    warning: "Docker unavailable — code ran on host without isolation",
    timedOut: r.timedOut,
  };
}

export const CodeSandboxTool: ToolImplementation = {
  definition: {
    name: "sandbox",
    description:
      "Run Python/JavaScript/TypeScript code in an isolated sandbox. Uses Docker container with no-network/non-root/resource-limited defaults when available; " +
      "falls back to host execution with a degradation warning when Docker isn't installed. " +
      'Example: sandbox(language: "python", code: "print(\\"hi\\")")',
    parameters: {
      type: "object",
      properties: {
        language: {
          type: "string",
          enum: ["python", "javascript", "typescript"],
          description: "Code language",
        },
        code: {
          type: "string",
          description: "Source code to execute",
        },
        timeoutMs: {
          type: "number",
          description: "Timeout in ms (default 30000, max 300000)",
        },
        allow_network: {
          type: "boolean",
          description: "Allow network access (default false)",
        },
        workspace_access: {
          type: "string",
          enum: ["none", "ro", "rw"],
          description: "Workspace mount mode (default ro; rw requires Docker)",
        },
        packages: {
          type: "string",
          description: "Comma-separated package names to install (requires allow_network)",
        },
      },
      required: ["language", "code"],
    },
    capabilities: ["code_execution", "sandbox"],
    executionPolicy: { timeoutMs: 30_000, maxRetries: 0 },
  },

  category: "shell",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const language = args["language"] as SandboxRunOptions["language"];
    const code = args["code"] as string;
    const timeoutMs = Math.min((args["timeoutMs"] as number | undefined) ?? 30_000, 300_000);
    const allowNetwork = args["allow_network"] === true;
    const workspaceAccess = (args["workspace_access"] as SandboxRunOptions["workspaceAccess"] | undefined) ?? "ro";
    const packagesRaw = args["packages"];
    const packages = Array.isArray(packagesRaw)
      ? (packagesRaw as string[])
      : typeof packagesRaw === "string"
        ? packagesRaw.split(",").map(s => s.trim()).filter(Boolean)
        : [];
    const cwd = context.cwd || process.cwd();

    if (!language || !["python", "javascript", "typescript"].includes(language)) {
      return JSON.stringify({ success: false, error: { code: "INVALID_ARG", message: "language must be python|javascript|typescript" } });
    }
    if (!code) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "code is required" } });
    }
    if (packages.length > 0 && !allowNetwork) {
      return JSON.stringify({ success: false, error: { code: "E_NETWORK_REQUIRED", message: "packages installs require allow_network:true" } });
    }

    const hasDocker = platform.systemInfo.current().capabilities.hasDocker;
    log.tool.debug("code-sandbox.execute: entry", { language, hasDocker, allowNetwork, workspaceAccess, packageCount: packages.length });

    const runOpts: SandboxRunOptions = { language, code, timeoutMs, allowNetwork, workspaceAccess, packages, cwd };

    let result: SandboxRunResult;
    try {
      if (hasDocker) {
        result = await runInDocker(runOpts);
        if (result.warning?.startsWith("E_IMAGE_NOT_PULLED")) {
          return JSON.stringify({ success: false, error: { code: "E_IMAGE_NOT_PULLED", message: result.warning } });
        }
      } else {
        result = await runOnHost(runOpts);
        if (result.warning?.startsWith("E_UNSAFE_HOST")) {
          return JSON.stringify({ success: false, error: { code: "E_UNSAFE_HOST", message: result.warning } });
        }
      }
    } catch (err) {
      log.tool.error("code-sandbox.execute: dispatch failed", err as Error);
      return JSON.stringify({ success: false, error: { code: "SANDBOX_ERROR", message: String(err) } });
    }

    log.tool.debug("code-sandbox.execute: exit", { via: result.via, exitCode: result.exitCode, durationMs: result.durationMs, timedOut: result.timedOut });
    return JSON.stringify({ success: true, data: result });
  },
};
