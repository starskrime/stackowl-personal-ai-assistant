import psList from "ps-list";
import { platform as osPlatform } from "node:os";
import { spawn } from "node:child_process";
import { log } from "../../logger.js";
import type { ProcessAPI, ProcessInfo } from "../types.js";

export class ProcessImpl implements ProcessAPI {
  async list(filter?: { name?: string; pid?: number }): Promise<ProcessInfo[]> {
    const all = await psList();
    return all
      .filter((p) => (filter?.pid !== undefined ? p.pid === filter.pid : true))
      .filter((p) => (filter?.name ? p.name.toLowerCase().includes(filter.name.toLowerCase()) : true))
      .map((p) => ({
        pid: p.pid,
        ppid: p.ppid,
        name: p.name,
        cmd: p.cmd,
        cpu: p.cpu,
        memory: p.memory,
      }));
  }

  async kill(pid: number, signal: NodeJS.Signals = "SIGTERM"): Promise<boolean> {
    if (osPlatform() === "win32" && signal === "SIGKILL") {
      return new Promise<boolean>((resolveResult) => {
        const child = spawn("taskkill", ["/F", "/PID", String(pid)], { stdio: "ignore" });
        child.on("error", () => resolveResult(false));
        child.on("close", (code) => resolveResult(code === 0));
      });
    }

    try {
      process.kill(pid, signal);
      return true;
    } catch (err) {
      log.tool.debug("process.kill failed", { pid, signal, err: String(err) });
      return false;
    }
  }

  isAlive(pid: number): boolean {
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  }

  currentInfo(): ProcessInfo {
    return {
      pid: process.pid,
      ppid: process.ppid,
      name: process.title,
      cmd: process.argv.join(" "),
    };
  }
}
