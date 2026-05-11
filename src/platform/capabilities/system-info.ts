import { platform as osPlatform, arch as osArch, release as osRelease } from "node:os";
import { existsSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import type {
  SystemInfo,
  SystemInfoAPI,
  PlatformName,
  SystemCapabilities,
} from "../types.js";

function detectInContainer(): boolean {
  if (process.env.IN_DOCKER === "true") return true;
  if (existsSync("/.dockerenv")) return true;
  return false;
}

function detectInWSL(): boolean {
  if (osPlatform() !== "linux") return false;
  try {
    const v = readFileSync("/proc/version", "utf-8").toLowerCase();
    return v.includes("microsoft") || v.includes("wsl");
  } catch {
    return false;
  }
}

async function commandAvailable(cmd: string): Promise<boolean> {
  return new Promise((resolveResult) => {
    const checker = osPlatform() === "win32" ? "where" : "which";
    const child = spawn(checker, [cmd], { stdio: "ignore" });
    child.on("error", () => resolveResult(false));
    child.on("close", (code) => resolveResult(code === 0));
  });
}

async function probeCapabilities(): Promise<SystemCapabilities> {
  const [hasOpener, hasDocker, hasGit, hasPython] = await Promise.all([
    osPlatform() === "win32"
      ? Promise.resolve(true)
      : osPlatform() === "darwin"
        ? commandAvailable("open")
        : commandAvailable("xdg-open"),
    commandAvailable("docker"),
    commandAvailable("git"),
    commandAvailable("python3").then((found) => found || commandAvailable("python")),
  ]);
  return {
    hasNotifier: true,
    hasOpener,
    hasDocker,
    hasGit,
    hasPython,
    hasNode: true,
  };
}

export class SystemInfoImpl implements SystemInfoAPI {
  private cached: SystemInfo;

  constructor() {
    this.cached = {
      platform: osPlatform() as PlatformName,
      arch: osArch(),
      release: osRelease(),
      locale: Intl.DateTimeFormat().resolvedOptions().locale,
      inContainer: detectInContainer(),
      inWSL: detectInWSL(),
      capabilities: {
        hasNotifier: true,
        hasOpener: false,
        hasDocker: false,
        hasGit: false,
        hasPython: false,
        hasNode: true,
      },
    };
  }

  current(): SystemInfo {
    return this.cached;
  }

  async refresh(): Promise<SystemInfo> {
    const capabilities = await probeCapabilities();
    this.cached = {
      ...this.cached,
      release: osRelease(),
      inContainer: detectInContainer(),
      inWSL: detectInWSL(),
      capabilities,
    };
    return this.cached;
  }
}
