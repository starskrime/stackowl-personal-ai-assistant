import { execSync, type ExecSyncOptions } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import {
  readdirSync,
  statSync,
  existsSync,
  readFileSync,
} from "node:fs";
import { join, isAbsolute, basename } from "node:path";
import { watch as chokidarWatch, type FSWatcher } from "chokidar";
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
  private _isGitRepo: boolean | null = null;
  constructor(private workspacePath: string) {}

  async collect(): Promise<ContextSignal[]> {
    // Check once whether this path is inside a git repo; skip silently if not
    if (this._isGitRepo === null) {
      try {
        execSync("git rev-parse --git-dir", {
          cwd: this.workspacePath,
          encoding: "utf-8",
          timeout: 5_000,
          stdio: ["ignore", "pipe", "pipe"],
        });
        this._isGitRepo = true;
      } catch {
        this._isGitRepo = false;
      }
    }
    if (!this._isGitRepo) return [];

    const opts: ExecSyncOptions = {
      cwd: this.workspacePath,
      encoding: "utf-8",
      timeout: 10_000,
      stdio: ["ignore", "pipe", "pipe"],
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
        } catch (err) {
          log.engine.warn("ActiveFileCollector: stat/readdir entry failed", err);
        }
      }
    } catch (err) {
      log.engine.warn("ActiveFileCollector: readdirSync failed", err);
    }
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

interface FileSnapshot {
  hash: string;
  size: number;
  lineCount: number;
}

export class FileSystemCollector implements SignalCollector {
  readonly source: SignalSource = "perch";
  readonly mode = "push" as const;

  private watcher: FSWatcher | null = null;
  private debounceTimer: NodeJS.Timeout | null = null;
  private snapshots = new Map<string, FileSnapshot>();
  private pendingChanges = new Map<
    string,
    {
      type: "created" | "modified" | "deleted";
      linesAdded?: number;
      linesRemoved?: number;
    }
  >();
  private targetDir = "";
  private emitFn: ((s: ContextSignal) => void) | null = null;

  constructor(
    private rootPath: string,
    private configuredPaths?: string[],
    private debounceMs?: number,
  ) {}

  start(emit: (s: ContextSignal) => void): void {
    this.emitFn = emit;
    const dirsToWatch: string[] =
      this.configuredPaths && this.configuredPaths.length > 0
        ? this.configuredPaths
        : (() => {
            const srcDir = join(this.rootPath, "src");
            return [existsSync(srcDir) ? srcDir : this.rootPath];
          })();
    this.targetDir = dirsToWatch[0];

    try {
      this.watcher = chokidarWatch(dirsToWatch, {
        persistent: false,
        ignoreInitial: true,
        usePolling: false,
      });
      this.watcher.on("add", (p) =>
        this.handleFileChange("rename", p),
      );
      this.watcher.on("change", (p) =>
        this.handleFileChange("change", p),
      );
      this.watcher.on("unlink", (p) =>
        this.handleFileChange("rename", p),
      );
      this.watcher.on("error", (err) =>
        log.engine.warn(`[FileSystemCollector] ${(err as Error).message}`),
      );
    } catch (err) {
      log.engine.warn(
        `[FileSystemCollector] start failed: ${(err as Error).message}`,
      );
    }
  }

  stop(): void {
    if (this.watcher) {
      void this.watcher.close();
      this.watcher = null;
    }
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
  }

  /**
   * Coarse perf prefilter — relevance is the classifier's job.
   */
  private shouldProcess(filename: string): boolean {
    if (basename(filename).startsWith(".")) return false;
    if (filename.endsWith(".tmp") || filename.endsWith("~")) return false;
    if (
      filename.includes("node_modules/") ||
      filename.includes("node_modules\\")
    )
      return false;
    if (filename.includes("dist/") || filename.includes("dist\\")) return false;
    if (filename.includes(".git/") || filename.includes(".git\\")) return false;
    if (filename.includes("sessions/") || filename.includes("pellets/"))
      return false;
    return true;
  }

  private handleFileChange(_eventType: string, filename: string): void {
    if (!this.shouldProcess(filename)) return;
    const fullPath = isAbsolute(filename) ? filename : join(this.targetDir, filename);
    const prev = this.snapshots.get(filename);

    if (!existsSync(fullPath)) {
      if (prev) {
        this.pendingChanges.set(filename, {
          type: "deleted",
          linesRemoved: prev.lineCount,
        });
        this.snapshots.delete(filename);
      }
    } else {
      try {
        const content = readFileSync(fullPath, "utf-8");
        const hash = createHash("md5").update(content).digest("hex");
        if (prev && prev.hash === hash) return;
        const lineCount = content.split("\n").length;
        const size = statSync(fullPath).size;
        if (!prev) {
          this.pendingChanges.set(filename, {
            type: "created",
            linesAdded: lineCount,
          });
        } else {
          this.pendingChanges.set(filename, {
            type: "modified",
            linesAdded: Math.max(0, lineCount - prev.lineCount),
            linesRemoved: Math.max(0, prev.lineCount - lineCount),
          });
        }
        this.snapshots.set(filename, { hash, size, lineCount });
      } catch (err) {
        log.engine.warn("FileSystemCollector: file read/hash failed", err);
        return;
      }
    }

    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => this.flush(), this.debounceMs ?? 5000);
  }

  private flush(): void {
    if (this.pendingChanges.size === 0 || !this.emitFn) return;
    const created: string[] = [];
    const modified: string[] = [];
    const deleted: string[] = [];
    let added = 0;
    let removed = 0;
    for (const [file, change] of this.pendingChanges) {
      if (change.type === "created") created.push(file);
      else if (change.type === "modified") modified.push(file);
      else deleted.push(file);
      added += change.linesAdded ?? 0;
      removed += change.linesRemoved ?? 0;
    }
    const parts: string[] = [];
    if (created.length) parts.push(`Created: ${created.join(", ")}`);
    if (modified.length) parts.push(`Modified: ${modified.join(", ")}`);
    if (deleted.length) parts.push(`Deleted: ${deleted.join(", ")}`);
    const totalFiles = this.pendingChanges.size;
    const title =
      totalFiles === 1
        ? created[0] || modified[0] || deleted[0]
        : `${totalFiles} files changed (+${added}/-${removed})`;
    const content =
      parts.join(". ") + (added || removed ? ` (+${added}/-${removed} lines)` : "");
    this.pendingChanges.clear();
    this.emitFn(makeSignal("perch", title, content, 60_000, { added, removed }));
  }
}
