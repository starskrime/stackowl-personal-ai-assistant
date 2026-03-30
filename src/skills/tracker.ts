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
    this.load();
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
        `Loaded skill stats for ${this.stats.size} skill(s) from ${this.filePath}`,
      );
    } catch (err) {
      log.engine.warn(
        `Failed to load skill stats from ${this.filePath}: ${err}`,
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

  /** Days since last use (0 if never used) */
  getDaysSinceLastUse(skillName: string): number {
    const s = this.stats.get(skillName);
    if (!s || !s.lastUsedAt) return Infinity;
    const msPerDay = 24 * 60 * 60 * 1000;
    return (Date.now() - new Date(s.lastUsedAt).getTime()) / msPerDay;
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

  /** Get all tracked skills and their stats */
  listAll(): { name: string; stats: SkillUsageStats }[] {
    return [...this.stats.entries()].map(([name, stats]) => ({ name, stats }));
  }

  /**
   * Get skills with high selection counts but poor success rates.
   * These are prime candidates for re-synthesis or improvement.
   */
  getFailingSkills(
    minSelections: number = 3,
    maxSuccessRate: number = 0.3,
  ): { name: string; stats: SkillUsageStats }[] {
    return [...this.stats.entries()]
      .filter(
        ([, s]) =>
          s.selectionCount >= minSelections &&
          (s.successCount + s.failureCount === 0 ||
            s.successRate <= maxSuccessRate),
      )
      .sort(([, a], [, b]) => b.selectionCount - a.selectionCount)
      .map(([name, stats]) => ({ name, stats }));
  }

  /** Get success rate for a skill (0-1), or undefined if no data */
  getSuccessRate(skillName: string): number | undefined {
    const s = this.stats.get(skillName);
    if (!s || s.successCount + s.failureCount === 0) return undefined;
    return s.successRate;
  }

  /**
   * Recency-adjusted usage multiplier for re-ranking.
   * Range: 0.7 (never used or 0% success) → 1.3 (100% success, used today)
   * Half-life: 90 days (recent successes matter ~2.7× more than stale ones)
   */
  getUsageMultiplier(skillName: string): number {
    const successRate = this.getSuccessRate(skillName);
    const daysSinceLastUse = this.getDaysSinceLastUse(skillName);

    // Baseline multiplier from success rate: 0.7–1.3
    let baseMultiplier = 0.7;
    if (successRate !== undefined) {
      baseMultiplier = 0.7 + successRate * 0.6;
    }

    // Recency factor: e^(-days/90), ~1.0 (today) to ~0.5 (180+ days)
    const recencyFactor = Math.exp(-daysSinceLastUse / 90);

    // Blend: multiplier × (0.5 + 0.5 × recencyFactor)
    // Recent perfect score: 1.3 × 1.0 = 1.3
    // Stale perfect score: 1.3 × 0.75 = 0.975
    // Zero success: 0.7 regardless of recency
    return baseMultiplier * (0.5 + 0.5 * recencyFactor);
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
        `Persisted skill stats for ${this.stats.size} skill(s) to ${this.filePath}`,
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
