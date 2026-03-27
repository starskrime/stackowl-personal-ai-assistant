/**
 * StackOwl — File System Perch
 *
 * A Perch Point that passively watches the workspace for file changes.
 * Detects structural changes, edits, and diffs.
 *
 * Improvements over basic watcher:
 *   - Content hashing to detect actual changes (not just fs events)
 *   - Diff summary via simple line-count comparison
 *   - Batch reporting: groups rapid changes into a single event
 *   - Tracks new/deleted/modified files separately
 */

import { watch, existsSync, readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { join, extname } from "node:path";
import type { PerchPoint, PerchEvent } from "./manager.js";

interface FileSnapshot {
  hash: string;
  size: number;
  lineCount: number;
  lastSeen: number;
}

export class FilePerch implements PerchPoint {
  name = "FileSystem Watcher";
  private rootPath: string;
  private targetDir: string = "";
  private watcher: ReturnType<typeof watch> | null = null;
  private emitFn: ((event: PerchEvent) => void) | null = null;
  private debounceTimer: NodeJS.Timeout | null = null;
  private lastEventTime = 0;

  /** Content hash snapshots for diff detection */
  private snapshots = new Map<string, FileSnapshot>();
  /** Pending changes batched during debounce window */
  private pendingChanges = new Map<
    string,
    {
      type: "created" | "modified" | "deleted";
      linesAdded?: number;
      linesRemoved?: number;
    }
  >();

  constructor(rootPath: string) {
    this.rootPath = rootPath;
  }

  async start(emit: (event: PerchEvent) => void): Promise<void> {
    this.emitFn = emit;

    try {
      const srcDir = join(this.rootPath, "src");
      this.targetDir = existsSync(srcDir) ? srcDir : this.rootPath;

      if (this.targetDir === this.rootPath) {
        console.log(
          `[FilePerch] workspace/src not found — watching workspace root instead.`,
        );
      }

      this.watcher = watch(
        this.targetDir,
        { recursive: true },
        (eventType, filename) => {
          if (filename && this.shouldProcess(filename)) {
            this.handleFileChange(eventType, filename);
          }
        },
      );
    } catch (error) {
      console.warn(
        `[FilePerch] Could not start watcher: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  stop(): void {
    if (this.watcher) {
      this.watcher.close();
      this.watcher = null;
    }
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }
  }

  private shouldProcess(filename: string): boolean {
    if (
      filename.startsWith(".") ||
      filename.endsWith("~") ||
      filename.endsWith(".tmp")
    )
      return false;

    const ALLOWED_EXTS = new Set([
      ".ts",
      ".tsx",
      ".js",
      ".jsx",
      ".json",
      ".md",
      ".yaml",
      ".yml",
      ".toml",
      ".css",
      ".html",
      ".py",
      ".sh",
    ]);
    if (!ALLOWED_EXTS.has(extname(filename))) return false;

    if (filename.includes("sessions/") || filename.includes("sessions\\"))
      return false;
    if (filename.includes("pellets/")) return false;
    if (filename.includes("synthesized/_manifest")) return false;
    if (filename.includes("node_modules/")) return false;
    if (filename.includes("dist/")) return false;

    return true;
  }

  private handleFileChange(_eventType: string, filename: string) {
    const now = Date.now();
    if (now - this.lastEventTime < 30000) return;

    // Compute diff against snapshot
    const fullPath = join(this.targetDir, filename);
    const prevSnapshot = this.snapshots.get(filename);

    if (!existsSync(fullPath)) {
      // File was deleted
      if (prevSnapshot) {
        this.pendingChanges.set(filename, {
          type: "deleted",
          linesRemoved: prevSnapshot.lineCount,
        });
        this.snapshots.delete(filename);
      }
    } else {
      try {
        const content = readFileSync(fullPath, "utf-8");
        const hash = createHash("md5").update(content).digest("hex");

        // Skip if content hasn't actually changed (just a fs event with no real edit)
        if (prevSnapshot && prevSnapshot.hash === hash) return;

        const lineCount = content.split("\n").length;
        const size = statSync(fullPath).size;

        if (!prevSnapshot) {
          this.pendingChanges.set(filename, {
            type: "created",
            linesAdded: lineCount,
          });
        } else {
          const linesAdded = Math.max(0, lineCount - prevSnapshot.lineCount);
          const linesRemoved = Math.max(0, prevSnapshot.lineCount - lineCount);
          this.pendingChanges.set(filename, {
            type: "modified",
            linesAdded,
            linesRemoved,
          });
        }

        this.snapshots.set(filename, { hash, size, lineCount, lastSeen: now });
      } catch {
        // File might be locked or transient — skip
        return;
      }
    }

    if (this.debounceTimer) clearTimeout(this.debounceTimer);

    this.debounceTimer = setTimeout(() => {
      this.flushPendingChanges();
    }, 5000);
  }

  private flushPendingChanges(): void {
    if (this.pendingChanges.size === 0) return;

    this.lastEventTime = Date.now();

    const created: string[] = [];
    const modified: string[] = [];
    const deleted: string[] = [];
    let totalLinesAdded = 0;
    let totalLinesRemoved = 0;

    for (const [file, change] of this.pendingChanges) {
      if (change.type === "created") created.push(file);
      else if (change.type === "modified") modified.push(file);
      else if (change.type === "deleted") deleted.push(file);

      totalLinesAdded += change.linesAdded ?? 0;
      totalLinesRemoved += change.linesRemoved ?? 0;
    }

    // Build a rich detail string
    const parts: string[] = [];
    if (created.length > 0) parts.push(`Created: ${created.join(", ")}`);
    if (modified.length > 0) parts.push(`Modified: ${modified.join(", ")}`);
    if (deleted.length > 0) parts.push(`Deleted: ${deleted.join(", ")}`);

    const diffSummary =
      totalLinesAdded > 0 || totalLinesRemoved > 0
        ? ` (+${totalLinesAdded}/-${totalLinesRemoved} lines)`
        : "";

    const details = `${parts.join(". ")}${diffSummary}`;
    const totalFiles = this.pendingChanges.size;

    this.pendingChanges.clear();

    if (this.emitFn) {
      this.emitFn({
        type: "file_change",
        source:
          totalFiles === 1
            ? created[0] || modified[0] || deleted[0]
            : `${totalFiles} files`,
        details,
      });
    }
  }
}
