import { spawn } from "node:child_process";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";
import { resolve } from "node:path";

const SANDBOX_IMAGE = "node:22-alpine";
const DEFAULT_TIMEOUT_MS = 60_000 * 5; // 5 minutes for long-horizon tests

export const SandboxTool: ToolImplementation = {
  definition: {
    name: "run_sandbox_command",
    description:
      "Execute a command inside a secure, long-horizon Docker sandbox. " +
      "Use this to run testing suites, build scripts, or linters during autonomous programming. " +
      "This tool guarantees up to 5 minutes of execution time and tracks the exit code accurately.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "The testing, building, or linting command to run.",
        },
      },
      required: ["command"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const cmd = args["command"] as string;
    if (!cmd) throw new Error("Command argument missing");

    const workspaceDir = resolve(context.cwd);

    // 1. ENTRY
    log.tool.debug("run_sandbox_command.execute: entry", { cmd, workspaceDir, image: SANDBOX_IMAGE });
    log.tool.info(`[SandboxTool] Executing long-horizon command: ${cmd}`);

    return new Promise((resolvePromise) => {
      const dockerArgs = [
        "run",
        "--rm",
        "--volume",
        `${workspaceDir}:/workspace`,
        "--workdir",
        "/workspace",
        "--env",
        "NODE_ENV=development",
        SANDBOX_IMAGE,
        "sh",
        "-c",
        cmd,
      ];

      // 2. DECISION — Docker container execution (not bare host)
      log.tool.debug("run_sandbox_command.execute: docker args built", { image: SANDBOX_IMAGE, workspaceDir });

      // 3. STEP — Docker subprocess spawned
      log.tool.debug("run_sandbox_command.execute: spawning docker container", { cmd });
      const proc = spawn("docker", dockerArgs);

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (chunk: Buffer) => {
        stdout += chunk.toString();
        // Optional: emit to progress stream for real-time UI viewing
        if (context.engineContext?.onProgress) {
          context.engineContext.onProgress(chunk.toString()).catch((err: unknown) => { log.tool.warn("sandbox: onProgress emit failed", err); });
        }
      });

      proc.stderr.on("data", (chunk: Buffer) => {
        stderr += chunk.toString();
        if (context.engineContext?.onProgress) {
          context.engineContext.onProgress(chunk.toString()).catch((err: unknown) => { log.tool.warn("sandbox: onProgress emit failed", err); });
        }
      });

      const timer = setTimeout(() => {
        proc.kill("SIGKILL");
        log.tool.debug("run_sandbox_command.execute: timeout triggered", { timeoutMs: DEFAULT_TIMEOUT_MS });
        resolvePromise(
          `[SYSTEM DIAGNOSTIC HINT]\nCommand timed out after ${DEFAULT_TIMEOUT_MS / 1000}s. ` +
          `Partial STDOUT:\n${stdout.slice(-2000)}\nPartial STDERR:\n${stderr.slice(-2000)}`
        );
      }, DEFAULT_TIMEOUT_MS);

      proc.on("close", (code) => {
        clearTimeout(timer);

        let output = `EXIT_CODE: ${code ?? 0}\n\nSTDOUT:\n${stdout.trim() || "(none)"}\n\nSTDERR:\n${stderr.trim() || "(none)"}`;

        if (output.length > 15000) {
           output = output.slice(0, 15000) + "\n...[truncated]";
        }

        if (code !== 0) {
           output += `\n\n[SYSTEM DIAGNOSTIC HINT] The command failed (exit code ${code}). Read the STDERR closely to figure out what code to fix, then try running the command again until it passes.`;
        } else {
           output += `\n\n[SYSTEM DIAGNOSTIC HINT] Success! Verification passed.`;
        }

        // 4. EXIT
        log.tool.debug("run_sandbox_command.execute: exit", { success: code === 0, exitCode: code ?? 0, stdoutLen: stdout.length, stderrLen: stderr.length, outputLen: output.length });
        resolvePromise(output);
      });

      proc.on("error", (err) => {
        clearTimeout(timer);
        log.tool.error("run_sandbox_command.execute: docker spawn failed", err, { cmd });
        resolvePromise(`[SYSTEM DIAGNOSTIC HINT] Failed to spawn Docker process. Is Docker running? Error: ${err.message}`);
      });
    });
  },
};
