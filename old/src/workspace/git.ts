/**
 * WorkspaceGit — local-only git repo for the StackOwl workspace.
 *
 * Maintains a git history of all workspace state changes (owl DNA, memories,
 * skills, preferences, knowledge graph) so any point in time can be rolled back.
 *
 * Binary/large files are excluded via .gitignore. All commits are local only —
 * no remote is ever configured.
 */

import { execSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";

const GITIGNORE = `# Binary databases
memory/stackowl.db
memory/stackowl.db-wal
memory/stackowl.db-shm

# Vector and graph stores (binary, large)
.pellets_lance/
.pellets_kuzu/

# Logs (append-only, high churn, not rollback-worthy)
logs/

# Browser data
.browser-data/

# Lock files
*.lock
*.lock.tmp

# Transient CLI session scratch
cli-sessions/

# npm / node
node_modules/
`;

const GIT_OPTS = {
  encoding: "utf-8" as const,
  timeout: 15_000,
  stdio: ["ignore", "pipe", "pipe"] as ["ignore", "pipe", "pipe"],
};

function git(workspacePath: string, ...args: string[]): string {
  return (
    execSync(`git ${args.join(" ")}`, { ...GIT_OPTS, cwd: workspacePath }) as unknown as string
  ).trim();
}

function hasChanges(workspacePath: string): boolean {
  try {
    const status = git(workspacePath, "status", "--porcelain");
    return status.length > 0;
  } catch {
    return false;
  }
}

export class WorkspaceGit {
  private _ready = false;
  private _debounceTimer: NodeJS.Timeout | null = null;
  private _pendingMessage = "";

  constructor(private readonly workspacePath: string) {}

  /**
   * Initialize git repo and do first commit if needed.
   * Safe to call multiple times — idempotent.
   */
  async init(): Promise<void> {
    try {
      const gitDir = join(this.workspacePath, ".git");

      if (!existsSync(gitDir)) {
        git(this.workspacePath, "init", "-b", "main");
        git(this.workspacePath, "config", "user.name", '"StackOwl"');
        git(this.workspacePath, "config", "user.email", '"stackowl@local"');
        log.engine.info("[WorkspaceGit] Initialized local git repo in workspace");
      }

      // Write/overwrite .gitignore every time to keep it current
      const gitignorePath = join(this.workspacePath, ".gitignore");
      writeFileSync(gitignorePath, GITIGNORE, "utf-8");

      this._ready = true;

      // Stage and commit everything currently present
      await this.commit("init: workspace state snapshot");
    } catch (err) {
      log.engine.warn(
        `[WorkspaceGit] init failed — workspace git disabled: ${err instanceof Error ? err.message : err}`,
      );
      this._ready = false;
    }
  }

  /**
   * Commit all workspace changes with the given message.
   * No-ops if there is nothing to commit or repo is not ready.
   * Never throws.
   */
  async commit(message: string): Promise<boolean> {
    if (!this._ready) return false;
    try {
      if (!hasChanges(this.workspacePath)) return false;

      git(this.workspacePath, "add", "-A");

      // Summarize what changed for the log
      const diffStat = git(this.workspacePath, "diff", "--cached", "--stat");
      const lines = diffStat.split("\n").filter(Boolean);
      const summary = lines[lines.length - 1] ?? "";

      git(this.workspacePath, "commit", "-m", `"${message.replace(/"/g, "'")}"`);

      log.engine.info(`[WorkspaceGit] Committed — ${message} | ${summary}`);
      return true;
    } catch (err) {
      log.engine.warn(
        `[WorkspaceGit] commit failed: ${err instanceof Error ? err.message : err}`,
      );
      return false;
    }
  }

  /**
   * Debounced commit — collects rapid-fire events and commits once
   * after `debounceMs` of silence.
   */
  scheduleCommit(message: string, debounceMs = 30_000): void {
    this._pendingMessage = message;
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => {
      this._debounceTimer = null;
      this.commit(this._pendingMessage).catch(() => {});
    }, debounceMs);
  }

  /**
   * Subscribe to GatewayEventBus events and auto-commit on key state changes.
   */
  subscribe(bus: GatewayEventBus): void {
    // After each conversation ends — commit session artifacts (DNA, memories, graph)
    bus.on("session:ended", () => {
      this.commit("session: conversation ended — owl DNA, memories, knowledge graph").catch(() => {});
    });

    // After owl DNA evolves
    bus.on("evolution:done", (e) => {
      this.commit(`evolution: ${e.owlName} DNA updated`).catch(() => {});
    });

    // Memory writes are high-frequency — debounce into one commit per burst
    bus.on("memory:written", () => {
      this.scheduleCommit("memory: facts written", 30_000);
    });

    // After learning/synthesis completes
    bus.on("learning:complete", () => {
      this.scheduleCommit("learning: knowledge synthesis completed", 10_000);
    });
  }
}
