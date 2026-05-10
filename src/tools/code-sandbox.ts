// src/tools/code-sandbox.ts
import { spawn } from "node:child_process";
import { writeFile, rm, mkdtemp } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

const DEFAULT_TIMEOUT_MS = 30_000;

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

    const dir  = await mkdtemp(join(tmpdir(), "stackowl-sandbox-"));
    const ext  = language === "python" ? "py" : "js";
    const file = join(dir, `script.${ext}`);
    await writeFile(file, code, "utf-8");

    const cmd     = language === "python" ? "python3" : "node";
    const cmdArgs = [file];

    return new Promise<string>((resolve) => {
      let stdout = "";
      let stderr = "";
      let timedOut = false;

      const child = spawn(cmd, cmdArgs, { cwd: dir });

      const timer = setTimeout(() => {
        timedOut = true;
        child.kill("SIGKILL");
      }, timeout);

      child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
      child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

      child.on("close", (exitCode) => {
        clearTimeout(timer);
        rm(dir, { recursive: true }).catch((err) => { log.tool.warn("sandbox temp-dir cleanup failed", err); });

        if (timedOut) {
          resolve(JSON.stringify({
            success: false,
            error: { code: "TIMEOUT", message: `Execution exceeded ${timeout}ms` },
          }));
          return;
        }

        resolve(JSON.stringify({
          success: true,
          data: { stdout, stderr, exitCode: exitCode ?? 0 },
        }));
      });

      child.on("error", (err) => {
        clearTimeout(timer);
        rm(dir, { recursive: true }).catch((err) => { log.tool.warn("sandbox temp-dir cleanup failed", err); });
        resolve(JSON.stringify({
          success: false,
          error: { code: "SPAWN_ERROR", message: (err as Error).message },
        }));
      });
    });
  },
};
