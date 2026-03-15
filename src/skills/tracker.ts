/**
 * StackOwl — Skill Execution Tracker
 *
 * Tracks skill usage across sessions: selection count, success/failure rates,
 * average duration. Persists to workspace/skills-stats.json.
 *
 * Used by:
 *   - SkillContextInjector: records skill selections
 *   - Gateway: records success/failure after engine response
 *   - IntentRouter: boosts frequently-successful skills in ranking
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";
import type { SkillUsageStats } from "./types.js";

export class SkillTracker {
  private stats: Map<string, SkillUsageStats> = new Map();
  private filePath: string;
  private dirty = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly SAVE_DEBOUNCE_MS = 5000;

  constructor(workspacePath: string) {
    this.filePath = join(workspacePath, "skills-stats.json");
  }

  async load(): Promise<void> {
    if (!existsSync(this.filePath)) {
      log.engine.info("No existing skill stats file, starting fresh");
      return;
    }

    try {
      const raw = await readFile(this.filePath, "utf-8");
      const parsed: Record<string, SkillUsageStats> = JSON.parse(raw);

      for (const [name, entry] of Object.entries(parsed)) {
        this.stats.set(name, entry);
      }

      log.engine.info(
        `Loaded skill stats for ${this.stats.size} skill(s) from ${this.filePath}`
      );
    } catch (err) {
      log.engine.warn(
        `Failed to load skill stats from ${this.filePath}: ${err}`
      );
    }
  }

  recordSelection(skillName: string): void {
    const s = this.ensureStats(skillName);
    s.selectionCount += 1;
    s.lastUsedAt = new Date().toISOString();
    this.dirty = true;
    this.scheduleSave();
  }

  recordSuccess(skillName: string, durationMs: number): void {
    const s = this.ensureStats(skillName);
    s.successCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  recordFailure(skillName: string, durationMs: number): void {
    const s = this.ensureStats(skillName);
    s.failureCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  getStats(skillName: string): SkillUsageStats | undefined {
    return this.stats.get(skillName);
  }

  /** Get top N skills sorted by selection count */
  getTopSkills(n: number): { name: string; stats: SkillUsageStats }[] {
    return [...this.stats.entries()]
      .sort(([, a], [, b]) => b.selectionCount - a.selectionCount)
      .slice(0, n)
      .map(([name, stats]) => ({ name, stats }));
  }

  /** Get success rate for a skill (0-1), or undefined if no data */
  getSuccessRate(skillName: string): number | undefined {
    const s = this.stats.get(skillName);
    if (!s || s.successCount + s.failureCount === 0) return undefined;
    return s.successRate;
  }

  private scheduleSave(): void {
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(async () => {
      this.saveTimer = null;
      await this.persist();
    }, SkillTracker.SAVE_DEBOUNCE_MS);
  }

  async persist(): Promise<void> {
    if (!this.dirty) return;

    const obj: Record<string, SkillUsageStats> = {};
    for (const [name, entry] of this.stats) {
      obj[name] = entry;
    }

    try {
      await writeFile(this.filePath, JSON.stringify(obj, null, 2), "utf-8");
      this.dirty = false;
      log.engine.info(
        `Persisted skill stats for ${this.stats.size} skill(s) to ${this.filePath}`
      );
    } catch (err) {
      log.engine.warn(`Failed to persist skill stats: ${err}`);
    }
  }

  private ensureStats(skillName: string): SkillUsageStats {
    let s = this.stats.get(skillName);
    if (!s) {
      s = {
        selectionCount: 0,
        successCount: 0,
        failureCount: 0,
        avgDurationMs: 0,
        lastUsedAt: null,
        successRate: 0,
      };
      this.stats.set(skillName, s);
    }
    return s;
  }
}
