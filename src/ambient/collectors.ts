import { execSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type {
  ContextSignal,
  SignalCollector,
  SignalPriority,
  SignalSource,
} from "./types.js";

const log = new Logger("AMBIENT");

function makeSignal(
  source: SignalSource,
  priority: SignalPriority,
  title: string,
  content: string,
  ttlMs: number,
  metadata?: Record<string, unknown>,
): ContextSignal {
  return {
    id: randomUUID(),
    source,
    priority,
    title,
    content,
    timestamp: Date.now(),
    ttlMs,
    metadata,
  };
}

export class GitStatusCollector implements SignalCollector {
  readonly source: SignalSource = "git";
  readonly intervalMs = 60_000;

  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    try {
      const statusRaw = execSync("git status --porcelain", {
        cwd: this.workspacePath,
        encoding: "utf-8",
        timeout: 10_000,
      }).trim();

      const logRaw = execSync("git log --oneline -3", {
        cwd: this.workspacePath,
        encoding: "utf-8",
        timeout: 10_000,
      }).trim();

      const signals: ContextSignal[] = [];
      const changedFiles = statusRaw
        ? statusRaw.split("\n").filter(Boolean)
        : [];
      const fileCount = changedFiles.length;

      if (fileCount > 0) {
        const priority: SignalPriority = fileCount > 5 ? "medium" : "low";
        const summary = changedFiles.slice(0, 10).join("\n");
        signals.push(
          makeSignal(
            "git",
            priority,
            `${fileCount} uncommitted file${fileCount === 1 ? "" : "s"}`,
            summary,
            90_000,
            { fileCount, files: changedFiles.slice(0, 20) },
          ),
        );
      }

      if (logRaw) {
        signals.push(
          makeSignal("git", "low", "Recent commits", logRaw, 90_000),
        );
      }

      return signals;
    } catch (err) {
      log.warn(`GitStatusCollector failed: ${(err as Error).message}`);
      return [];
    }
  }
}

export class TimeContextCollector implements SignalCollector {
  readonly source: SignalSource = "time_of_day";
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const now = new Date();
      const hour = now.getHours();
      const day = now.getDay();
      const isWeekend = day === 0 || day === 6;

      let period: string;
      if (hour >= 5 && hour < 12) period = "morning";
      else if (hour >= 12 && hour < 17) period = "afternoon";
      else if (hour >= 17 && hour < 21) period = "evening";
      else period = "night";

      const dayNames = [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
      ];
      const dayName = dayNames[day];
      const timeStr = now.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      });
      const dayType = isWeekend ? "weekend" : "weekday";

      const title = `${dayName} ${period}, ${timeStr}`;
      const content = `${dayName} ${period} (${dayType}), ${timeStr}. Hour ${hour}.`;

      return [
        makeSignal("time_of_day", "low", title, content, 360_000, {
          hour,
          period,
          dayName,
          isWeekend,
        }),
      ];
    } catch (err) {
      log.warn(`TimeContextCollector failed: ${(err as Error).message}`);
      return [];
    }
  }
}

export class SystemCollector implements SignalCollector {
  readonly source: SignalSource = "system";
  readonly intervalMs = 300_000;

  async collect(): Promise<ContextSignal[]> {
    try {
      const signals: ContextSignal[] = [];

      const uptimeRaw = execSync("uptime", {
        encoding: "utf-8",
        timeout: 5_000,
      }).trim();
      signals.push(
        makeSignal("system", "low", "System uptime", uptimeRaw, 360_000),
      );

      const dfRaw = execSync("df -h /", {
        encoding: "utf-8",
        timeout: 5_000,
      }).trim();
      const dfLines = dfRaw.split("\n");
      if (dfLines.length >= 2) {
        const parts = dfLines[1].split(/\s+/);
        const usageStr = parts.find((p) => p.endsWith("%"));
        const usagePercent = usageStr
          ? parseInt(usageStr.replace("%", ""), 10)
          : 0;
        const priority: SignalPriority = usagePercent > 90 ? "high" : "low";

        signals.push(
          makeSignal(
            "system",
            priority,
            `Disk usage: ${usageStr || "unknown"}`,
            dfLines[1],
            360_000,
            {
              usagePercent,
            },
          ),
        );
      }

      return signals;
    } catch (err) {
      log.warn(`SystemCollector failed: ${(err as Error).message}`);
      return [];
    }
  }
}

export class ActiveFileCollector implements SignalCollector {
  readonly source: SignalSource = "active_file";
  readonly intervalMs = 30_000;

  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    try {
      const fiveMinAgo = Date.now() - 5 * 60 * 1000;
      const recentFiles = this.findRecentFiles(
        this.workspacePath,
        fiveMinAgo,
        3,
      );

      if (recentFiles.length === 0) return [];

      const fileList = recentFiles.map((f) => f.path).join("\n");
      return [
        makeSignal(
          "active_file",
          "low",
          `${recentFiles.length} recently modified file${recentFiles.length === 1 ? "" : "s"}`,
          fileList,
          45_000,
          { files: recentFiles },
        ),
      ];
    } catch (err) {
      log.warn(`ActiveFileCollector failed: ${(err as Error).message}`);
      return [];
    }
  }

  private findRecentFiles(
    dir: string,
    since: number,
    maxDepth: number,
    depth = 0,
  ): Array<{ path: string; mtime: number }> {
    if (depth > maxDepth) return [];

    const results: Array<{ path: string; mtime: number }> = [];

    try {
      const entries = readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (
          entry.name.startsWith(".") ||
          entry.name === "node_modules" ||
          entry.name === "dist"
        ) {
          continue;
        }

        const fullPath = join(dir, entry.name);

        try {
          if (entry.isFile()) {
            const stat = statSync(fullPath);
            if (stat.mtimeMs >= since) {
              results.push({ path: fullPath, mtime: stat.mtimeMs });
            }
          } else if (entry.isDirectory()) {
            results.push(
              ...this.findRecentFiles(fullPath, since, maxDepth, depth + 1),
            );
          }
        } catch {
          // Skip inaccessible files
        }
      }
    } catch {
      // Skip inaccessible directories
    }

    return results.sort((a, b) => b.mtime - a.mtime).slice(0, 20);
  }
}

export class ClipboardCollector implements SignalCollector {
  readonly source: SignalSource = "clipboard";
  readonly intervalMs = 10_000;
  private lastContent = "";

  async collect(): Promise<ContextSignal[]> {
    try {
      if (process.platform !== "darwin") return [];

      const content = execSync("pbpaste", {
        encoding: "utf-8",
        timeout: 3_000,
      });
      const trimmed = content.trim();

      if (!trimmed || trimmed === this.lastContent) return [];

      this.lastContent = trimmed;
      const preview =
        trimmed.length > 200 ? trimmed.slice(0, 200) + "..." : trimmed;

      return [
        makeSignal("clipboard", "low", "Clipboard updated", preview, 30_000, {
          length: trimmed.length,
        }),
      ];
    } catch (err) {
      log.warn(`ClipboardCollector failed: ${(err as Error).message}`);
      return [];
    }
  }
}
