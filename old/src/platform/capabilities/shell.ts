import { spawn } from "node:child_process";
import { platform as osPlatform } from "node:os";
import { log } from "../../logger.js";
import type { Shell, SpawnOptions, SpawnResult } from "../types.js";

export class ShellImpl implements Shell {
  async exec(command: string, opts: SpawnOptions = {}): Promise<SpawnResult> {
    const start = Date.now();
    const isWin = osPlatform() === "win32";

    const [bin, args] = isWin
      ? ["cmd.exe", ["/d", "/s", "/c", command]]
      : ["/bin/sh", ["-c", command]];

    log.tool.debug("shell.exec: entry", { bin, command: command.slice(0, 200), cwd: opts.cwd });

    return new Promise<SpawnResult>((resolveResult) => {
      const child = spawn(bin, args as string[], {
        cwd: opts.cwd,
        env: opts.env ?? process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      const stdoutChunks: Buffer[] = [];
      const stderrChunks: Buffer[] = [];
      child.stdout.on("data", (c) => stdoutChunks.push(c as Buffer));
      child.stderr.on("data", (c) => stderrChunks.push(c as Buffer));

      let timedOut = false;
      const timer = opts.timeoutMs
        ? setTimeout(() => {
            timedOut = true;
            child.kill("SIGTERM");
            setTimeout(() => {
              if (!child.killed) child.kill("SIGKILL");
            }, 100);
          }, opts.timeoutMs)
        : null;

      if (opts.inputStdin !== undefined) {
        child.stdin.write(opts.inputStdin);
      }
      child.stdin.end();

      child.on("close", (exitCode) => {
        if (timer) clearTimeout(timer);
        const stdout = Buffer.concat(stdoutChunks).toString("utf-8");
        const stderr = Buffer.concat(stderrChunks).toString("utf-8");
        const durationMs = Date.now() - start;
        log.tool.debug("shell.exec: exit", { exitCode, durationMs, timedOut });
        resolveResult({ exitCode, stdout, stderr, durationMs, timedOut });
      });

      child.on("error", (err) => {
        if (timer) clearTimeout(timer);
        log.tool.error("shell.exec: spawn failed", err);
        resolveResult({
          exitCode: null,
          stdout: Buffer.concat(stdoutChunks).toString("utf-8"),
          stderr: String(err),
          durationMs: Date.now() - start,
          timedOut,
        });
      });
    });
  }
}
