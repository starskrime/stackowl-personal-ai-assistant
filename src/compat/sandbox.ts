/**
 * StackOwl — Docker Sandbox
 *
 * Provides isolated container execution for tools, similar to OpenCLAW.
 * Protects the host from malicious commands.
 */

import { exec, spawn } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";

const execAsync = promisify(exec);

interface SandboxConfig {
  enabled?: boolean;
  image?: string;
  networkAccess?: boolean;
  maxMemory?: string;
  maxCpu?: number;
  workspacePath?: string;
}

interface ExecutionResult {
  stdout: string;
  stderr: string;
  exitCode: number;
  duration: number;
}

const DEFAULT_IMAGE = "node:22-alpine";
const CONTAINER_PREFIX = "stackowl-sandbox-";

export class DockerSandbox {
  private config: Required<SandboxConfig>;

  constructor(config: SandboxConfig) {
    this.config = {
      enabled: config.enabled ?? false,
      image: config.image || DEFAULT_IMAGE,
      networkAccess: config.networkAccess ?? false,
      maxMemory: config.maxMemory || "512m",
      maxCpu: config.maxCpu || 1,
      workspacePath: config.workspacePath || process.cwd(),
    };
  }

  /**
   * Check if Docker is available.
   */
  async isAvailable(): Promise<boolean> {
    try {
      await execAsync("docker info", { timeout: 5000 });
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Execute a command in the sandbox.
   */
  async execute(
    command: string,
    options: {
      cwd?: string;
      env?: Record<string, string>;
      timeout?: number;
      networkAccess?: boolean;
    } = {},
  ): Promise<ExecutionResult> {
    const {
      cwd = "/workspace",
      env = {},
      timeout = 30000,
      networkAccess = this.config.networkAccess ?? false,
    } = options;

    const executionId = randomUUID();
    const containerName = `${CONTAINER_PREFIX}${executionId.slice(0, 8)}`;

    const startTime = Date.now();

    // Build docker run command
    const dockerArgs = [
      "run",
      "--rm",
      "--name",
      containerName,
      "--memory",
      this.config.maxMemory || "512m",
      "--cpus",
      String(this.config.maxCpu || 1),
      "--workdir",
      cwd,
      "-v",
      `${this.config.workspacePath || process.cwd()}:/workspace:ro`,
    ];

    // Network access
    if (!networkAccess) {
      dockerArgs.push("--network", "none");
    }

    // Environment variables
    for (const [key, value] of Object.entries(env)) {
      dockerArgs.push("-e", `${key}=${value}`);
    }

    // Image
    dockerArgs.push(this.config.image || DEFAULT_IMAGE);

    // Command
    dockerArgs.push("sh", "-c", command);

    try {
      // Run docker
      const { stdout, stderr } = await execAsync(
        `docker ${dockerArgs.join(" ")}`,
        { timeout, maxBuffer: 10 * 1024 * 1024 },
      );

      return {
        stdout: stdout || "",
        stderr: stderr || "",
        exitCode: 0,
        duration: Date.now() - startTime,
      };
    } catch (error) {
      // Check if it's a timeout
      if (error instanceof Error && error.message.includes("timeout")) {
        // Try to kill the container
        try {
          await execAsync(`docker kill ${containerName}`, { timeout: 5000 });
        } catch {
          /* ignore */
        }

        return {
          stdout: "",
          stderr: "Command timed out",
          exitCode: 124,
          duration: timeout,
        };
      }

      // Parse exit code from error message
      const exitCodeMatch = String(error).match(/exit code: (\d+)/);
      const exitCode = exitCodeMatch ? parseInt(exitCodeMatch[1], 10) : 1;

      // Get any output we can
      let stdout = "";
      let stderr = String(error);

      return {
        stdout,
        stderr,
        exitCode,
        duration: Date.now() - startTime,
      };
    }
  }

  /**
   * Execute interactively with streaming output.
   */
  async executeInteractive(
    command: string,
    options: {
      cwd?: string;
      env?: Record<string, string>;
      onOutput?: (data: string) => void;
      onError?: (data: string) => void;
    } = {},
  ): Promise<ExecutionResult> {
    const { cwd = "/workspace", env = {}, onOutput, onError } = options;

    const executionId = randomUUID();
    const containerName = `${CONTAINER_PREFIX}${executionId.slice(0, 8)}`;

    const dockerArgs = [
      "run",
      "--rm",
      "--name",
      containerName,
      "--memory",
      this.config.maxMemory || "512m",
      "--cpus",
      String(this.config.maxCpu || 1),
      "--workdir",
      cwd,
      "-i",
      "-v",
      `${this.config.workspacePath || process.cwd()}:/workspace:ro`,
    ];

    if (!(this.config.networkAccess ?? false)) {
      dockerArgs.push("--network", "none");
    }

    for (const [key, value] of Object.entries(env)) {
      dockerArgs.push("-e", `${key}=${value}`);
    }

    dockerArgs.push(this.config.image || DEFAULT_IMAGE);
    dockerArgs.push("sh", "-c", command);

    return new Promise((resolve) => {
      let stdout = "";
      let stderr = "";
      const startTime = Date.now();

      const proc = spawn("docker", dockerArgs, {
        cwd,
        stdio: ["pipe", "pipe", "pipe"],
      });

      proc.stdout?.on("data", (data) => {
        const str = data.toString();
        stdout += str;
        onOutput?.(str);
      });

      proc.stderr?.on("data", (data) => {
        const str = data.toString();
        stderr += str;
        onError?.(str);
      });

      proc.on("close", (code) => {
        resolve({
          stdout,
          stderr,
          exitCode: code ?? 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on("error", (err) => {
        stderr += err.message;
        resolve({
          stdout,
          stderr,
          exitCode: 1,
          duration: Date.now() - startTime,
        });
      });
    });
  }

  /**
   * Run a Node.js script in sandbox.
   */
  async runNodeScript(
    script: string,
    timeout = 30000,
  ): Promise<ExecutionResult> {
    const wrappedScript = `
            const fs = require('fs');
            const scriptFile = '/tmp/script_${Date.now()}.js';
            fs.writeFileSync(scriptFile, \`${script.replace(/`/g, "\\`")}\`);
            try {
                require(scriptFile);
            } catch(e) {
                console.error(e.message);
                process.exit(1);
            }
        `;

    return this.execute(`node -e "${wrappedScript}"`, { timeout });
  }

  /**
   * Clean up any leftover containers.
   */
  async cleanup(): Promise<void> {
    try {
      await execAsync(
        `docker ps -aq --filter "name=${CONTAINER_PREFIX}" | xargs -r docker kill`,
        { timeout: 10000 },
      );
      await execAsync(
        `docker ps -aq --filter "name=${CONTAINER_PREFIX}" | xargs -r docker rm`,
        { timeout: 10000 },
      );
    } catch {
      /* ignore cleanup errors */
    }
  }

  /**
   * Get sandbox status.
   */
  getStatus(): { available: boolean; config: SandboxConfig } {
    return {
      available: this.config.enabled,
      config: this.config,
    };
  }
}

/**
 * Execute a shell command with optional sandboxing.
 */
export async function executeWithSandbox(
  command: string,
  sandbox: DockerSandbox | undefined,
  options: {
    useSandbox?: boolean;
    cwd?: string;
    env?: Record<string, string>;
    timeout?: number;
  } = {},
): Promise<ExecutionResult> {
  const { useSandbox = false, cwd, env, timeout } = options;

  if (useSandbox && sandbox) {
    const isAvailable = await sandbox.isAvailable();
    if (isAvailable) {
      return sandbox.execute(command, { cwd, env, timeout });
    }
    console.warn("[Sandbox] Docker not available, executing on host");
  }

  // Execute directly on host
  const startTime = Date.now();

  try {
    const { stdout, stderr } = await execAsync(command, {
      cwd,
      env: { ...process.env, ...env },
      timeout: timeout || 30000,
      maxBuffer: 10 * 1024 * 1024,
    });

    return {
      stdout: stdout || "",
      stderr: stderr || "",
      exitCode: 0,
      duration: Date.now() - startTime,
    };
  } catch (error) {
    const exitCodeMatch = String(error).match(/exit code: (\d+)/);
    const exitCode = exitCodeMatch ? parseInt(exitCodeMatch[1], 10) : 1;

    return {
      stdout: "",
      stderr: String(error),
      exitCode,
      duration: Date.now() - startTime,
    };
  }
}
