import { execSync, type ExecSyncOptions } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";
import type {
  ContextSignal,
  SignalCollector,
  SignalSource,
} from "../ambient/types.js";

function makeSignal(
  source: SignalSource,
  title: string,
  content: string,
  ttlMs: number,
  metadata?: Record<string, unknown>,
): ContextSignal {
  return {
    id: randomUUID(),
    source,
    priority: "low",
    title,
    content,
    timestamp: Date.now(),
    ttlMs,
    metadata,
  };
}

export class GitStatusCollector implements SignalCollector {
  readonly source: SignalSource = "git";
  readonly mode = "poll" as const;
  readonly intervalMs = 60_000;
  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    const opts: ExecSyncOptions = {
      cwd: this.workspacePath,
      encoding: "utf-8",
      timeout: 10_000,
    };
    try {
      const status = (
        execSync("git status --porcelain", opts) as unknown as string
      ).trim();
      const logRaw = (
        execSync("git log --oneline -3", opts) as unknown as string
      ).trim();
      const out: ContextSignal[] = [];
      if (status) {
        const files = status.split("\n").filter(Boolean);
        out.push(
          makeSignal(
            "git",
            `${files.length} uncommitted file${files.length === 1 ? "" : "s"}`,
            files.slice(0, 10).join("\n"),
            90_000,
            { fileCount: files.length, files: files.slice(0, 20) },
          ),
        );
      }
      if (logRaw) {
        out.push(makeSignal("git", "Recent commits", logRaw, 90_000));
      }
      return out;
    } catch (err) {
      log.engine.warn(`[GitStatusCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class TimeContextCollector implements SignalCollector {
  readonly source: SignalSource = "time_of_day";
  readonly mode = "poll" as const;
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const now = new Date();
      const hour = now.getHours();
      const day = now.getDay();
      const isWeekend = day === 0 || day === 6;
      const period =
        hour >= 5 && hour < 12
          ? "morning"
          : hour >= 12 && hour < 17
            ? "afternoon"
            : hour >= 17 && hour < 21
              ? "evening"
              : "night";
      const dayName = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
      ][day];
      const timeStr = now.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      });
      const dayType = isWeekend ? "weekend" : "weekday";
      return [
        makeSignal(
          "time_of_day",
          `${dayName} ${period}, ${timeStr}`,
          `${dayName} ${period} (${dayType}), ${timeStr}. Hour ${hour}.`,
          360_000,
          { hour, period, dayName, isWeekend },
        ),
      ];
    } catch (err) {
      log.engine.warn(`[TimeContextCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class SystemCollector implements SignalCollector {
  readonly source: SignalSource = "system";
  readonly mode = "poll" as const;
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const out: ContextSignal[] = [];
      const uptime = (
        execSync("uptime", { encoding: "utf-8", timeout: 5_000 }) as unknown as
          string
      ).trim();
      out.push(makeSignal("system", "System uptime", uptime, 360_000));
      const dfRaw = (
        execSync("df -h /", { encoding: "utf-8", timeout: 5_000 }) as unknown as
          string
      ).trim();
      const dfLines = dfRaw.split("\n");
      if (dfLines.length >= 2) {
        const parts = dfLines[1].split(/\s+/);
        const usageStr = parts.find((p) => p.endsWith("%"));
        const usagePercent = usageStr
          ? parseInt(usageStr.replace("%", ""), 10)
          : 0;
        out.push(
          makeSignal(
            "system",
            `Disk usage: ${usageStr ?? "unknown"}`,
            dfLines[1],
            360_000,
            { usagePercent },
          ),
        );
      }
      return out;
    } catch (err) {
      log.engine.warn(`[SystemCollector] ${(err as Error).message}`);
      return [];
    }
  }
}

export class ActiveFileCollector implements SignalCollector {
  readonly source: SignalSource = "active_file";
  readonly mode = "poll" as const;
  readonly intervalMs = 30_000;
  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    try {
      const since = Date.now() - 5 * 60_000;
      const recent = this.findRecent(this.workspacePath, since, 3, 0);
      if (recent.length === 0) return [];
      return [
        makeSignal(
          "active_file",
          `${recent.length} recently modified file${recent.length === 1 ? "" : "s"}`,
          recent.map((f) => f.path).join("\n"),
          45_000,
          { files: recent },
        ),
      ];
    } catch (err) {
      log.engine.warn(`[ActiveFileCollector] ${(err as Error).message}`);
      return [];
    }
  }

  private findRecent(
    dir: string,
    since: number,
    maxDepth: number,
    depth: number,
  ): Array<{ path: string; mtime: number }> {
    if (depth > maxDepth) return [];
    const out: Array<{ path: string; mtime: number }> = [];
    try {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (
          entry.name.startsWith(".") ||
          entry.name === "node_modules" ||
          entry.name === "dist"
        )
          continue;
        const full = join(dir, entry.name);
        try {
          if (entry.isFile()) {
            const stat = statSync(full);
            if (stat.mtimeMs >= since)
              out.push({ path: full, mtime: stat.mtimeMs });
          } else if (entry.isDirectory()) {
            out.push(...this.findRecent(full, since, maxDepth, depth + 1));
          }
        } catch {}
      }
    } catch {}
    return out.sort((a, b) => b.mtime - a.mtime).slice(0, 20);
  }
}

export class ClipboardCollector implements SignalCollector {
  readonly source: SignalSource = "clipboard";
  readonly mode = "poll" as const;
  readonly intervalMs = 10_000;
  private lastContent = "";

  async collect(): Promise<ContextSignal[]> {
    if (process.platform !== "darwin") return [];
    try {
      const raw = execSync("pbpaste", {
        encoding: "utf-8",
        timeout: 3_000,
      }) as unknown as string;
      const trimmed = raw.trim();
      if (!trimmed || trimmed === this.lastContent) return [];
      this.lastContent = trimmed;
      const preview =
        trimmed.length > 200 ? trimmed.slice(0, 200) + "..." : trimmed;
      return [
        makeSignal("clipboard", "Clipboard updated", preview, 30_000, {
          length: trimmed.length,
        }),
      ];
    } catch (err) {
      log.engine.warn(`[ClipboardCollector] ${(err as Error).message}`);
      return [];
    }
  }
}
