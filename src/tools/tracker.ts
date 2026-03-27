/**
 * StackOwl — Tool Execution Tracker
 *
 * Tracks tool usage across sessions: selection count, success/failure rates,
 * average duration. Persists to workspace/tools-stats.json.
 *
 * Mirrors SkillTracker but for tools. Used by ToolIntentRouter to re-rank
 * tools based on recency-adjusted success rates.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

export interface ToolUsageStats {
  selectionCount: number;
  successCount: number;
  failureCount: number;
  avgDurationMs: number;
  lastUsedAt: string | null;
  successRate: number;
}

export class ToolTracker {
  private stats: Map<string, ToolUsageStats> = new Map();
  private filePath: string;
  private dirty = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly SAVE_DEBOUNCE_MS = 5000;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "tools-stats.json");
    this.load();
  }

  async load(): Promise<void> {
    if (!existsSync(this.filePath)) {
      log.engine.info("No existing tool stats file, starting fresh");
      return;
    }

    try {
      const raw = await readFile(this.filePath, "utf-8");
      const parsed: Record<string, ToolUsageStats> = JSON.parse(raw);

      for (const [name, entry] of Object.entries(parsed)) {
        this.stats.set(name, entry);
      }

      log.engine.info(
        `Loaded tool stats for ${this.stats.size} tool(s) from ${this.filePath}`,
      );
    } catch (err) {
      log.engine.warn(
        `Failed to load tool stats from ${this.filePath}: ${err}`,
      );
    }
  }

  recordSelection(toolName: string): void {
    const s = this.ensureStats(toolName);
    s.selectionCount += 1;
    s.lastUsedAt = new Date().toISOString();
    this.dirty = true;
    this.scheduleSave();
  }

  /** Days since last use (Infinity if never used) */
  getDaysSinceLastUse(toolName: string): number {
    const s = this.stats.get(toolName);
    if (!s || !s.lastUsedAt) return Infinity;
    const msPerDay = 24 * 60 * 60 * 1000;
    return (Date.now() - new Date(s.lastUsedAt).getTime()) / msPerDay;
  }

  recordSuccess(toolName: string, durationMs: number): void {
    const s = this.ensureStats(toolName);
    s.successCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  recordFailure(toolName: string, durationMs: number): void {
    const s = this.ensureStats(toolName);
    s.failureCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  getStats(toolName: string): ToolUsageStats | undefined {
    return this.stats.get(toolName);
  }

  /** Get success rate for a tool (0-1), or undefined if no data */
  getSuccessRate(toolName: string): number | undefined {
    const s = this.stats.get(toolName);
    if (!s || s.successCount + s.failureCount === 0) return undefined;
    return s.successRate;
  }

  /**
   * Recency-adjusted usage multiplier for re-ranking.
   * Range: 0.7 (never used or 0% success) → 1.3 (100% success, used today)
   * Half-life: 90 days (recent successes matter ~2.7× more than stale ones)
   */
  getUsageMultiplier(toolName: string): number {
    const successRate = this.getSuccessRate(toolName);
    const daysSinceLastUse = this.getDaysSinceLastUse(toolName);

    let baseMultiplier = 0.7;
    if (successRate !== undefined) {
      baseMultiplier = 0.7 + successRate * 0.6;
    }

    const recencyFactor = Math.exp(-daysSinceLastUse / 90);
    return baseMultiplier * (0.5 + 0.5 * recencyFactor);
  }

  private scheduleSave(): void {
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(async () => {
      this.saveTimer = null;
      await this.persist();
    }, ToolTracker.SAVE_DEBOUNCE_MS);
  }

  async persist(): Promise<void> {
    if (!this.dirty) return;

    const obj: Record<string, ToolUsageStats> = {};
    for (const [name, entry] of this.stats) {
      obj[name] = entry;
    }

    try {
      await writeFile(this.filePath, JSON.stringify(obj, null, 2), "utf-8");
      this.dirty = false;
      log.engine.info(
        `Persisted tool stats for ${this.stats.size} tool(s) to ${this.filePath}`,
      );
    } catch (err) {
      log.engine.warn(`Failed to persist tool stats: ${err}`);
    }
  }

  private ensureStats(toolName: string): ToolUsageStats {
    let s = this.stats.get(toolName);
    if (!s) {
      s = {
        selectionCount: 0,
        successCount: 0,
        failureCount: 0,
        avgDurationMs: 0,
        lastUsedAt: null,
        successRate: 0,
      };
      this.stats.set(toolName, s);
    }
    return s;
  }
}
