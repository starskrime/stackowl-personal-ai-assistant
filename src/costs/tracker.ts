/**
 * StackOwl — Cost Tracker
 *
 * Accumulates token usage and estimated costs per provider/session/user.
 * Enforces configurable budgets with warnings and hard limits.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { estimateCost } from "./pricing.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface CostEntry {
  timestamp: number;
  provider: string;
  model: string;
  sessionId: string;
  userId: string;
  promptTokens: number;
  completionTokens: number;
  estimatedCostUsd: number;
}

export interface CostBudget {
  /** Max daily spend in USD. 0 = unlimited. */
  maxDailyUsd: number;
  /** Max monthly spend in USD. 0 = unlimited. */
  maxMonthlyUsd: number;
  /** Max tokens per single request. 0 = unlimited. */
  maxPerRequestTokens: number;
  /** Emit warning at this % of daily budget. Default: 80 */
  warnAtPercent: number;
}

export interface BudgetCheck {
  allowed: boolean;
  reason?: string;
  dailyUsedUsd: number;
  dailyRemainingUsd: number;
  monthlyUsedUsd: number;
}

export interface CostSummary {
  totalPromptTokens: number;
  totalCompletionTokens: number;
  totalTokens: number;
  estimatedCostUsd: number;
  entries: number;
  byProvider: Record<string, { tokens: number; costUsd: number }>;
  byModel: Record<string, { tokens: number; costUsd: number }>;
}

const DEFAULT_BUDGET: CostBudget = {
  maxDailyUsd: 0,
  maxMonthlyUsd: 0,
  maxPerRequestTokens: 0,
  warnAtPercent: 80,
};

// ─── Implementation ─────────────────────────────────────────────

export class CostTracker {
  private budget: CostBudget;
  private entries: CostEntry[] = [];
  private persistPath: string | null;
  private dirty = false;
  private saveTimer: NodeJS.Timeout | null = null;

  constructor(budget?: Partial<CostBudget>, persistPath?: string) {
    this.budget = { ...DEFAULT_BUDGET, ...budget };
    this.persistPath = persistPath ?? null;
  }

  /**
   * Record a usage event. Returns the computed cost entry.
   */
  record(
    provider: string,
    model: string,
    promptTokens: number,
    completionTokens: number,
    sessionId: string,
    userId: string,
  ): CostEntry {
    const entry: CostEntry = {
      timestamp: Date.now(),
      provider,
      model,
      sessionId,
      userId,
      promptTokens,
      completionTokens,
      estimatedCostUsd: estimateCost(model, promptTokens, completionTokens),
    };

    this.entries.push(entry);
    this.dirty = true;
    this.debounceSave();

    // Check budget warnings
    if (this.budget.maxDailyUsd > 0) {
      const daily = this.getDailySpend();
      const pct = (daily / this.budget.maxDailyUsd) * 100;
      if (pct >= this.budget.warnAtPercent && pct < 100) {
        log.engine.warn(
          `[CostTracker] Daily budget ${pct.toFixed(0)}% used ($${daily.toFixed(4)} / $${this.budget.maxDailyUsd})`,
        );
      }
    }

    return entry;
  }

  /**
   * Check whether a request is allowed under the current budget.
   */
  checkBudget(): BudgetCheck {
    const dailyUsed = this.getDailySpend();
    const monthlyUsed = this.getMonthlySpend();

    if (this.budget.maxDailyUsd > 0 && dailyUsed >= this.budget.maxDailyUsd) {
      return {
        allowed: false,
        reason: `Daily budget exceeded ($${dailyUsed.toFixed(4)} / $${this.budget.maxDailyUsd})`,
        dailyUsedUsd: dailyUsed,
        dailyRemainingUsd: 0,
        monthlyUsedUsd: monthlyUsed,
      };
    }

    if (
      this.budget.maxMonthlyUsd > 0 &&
      monthlyUsed >= this.budget.maxMonthlyUsd
    ) {
      return {
        allowed: false,
        reason: `Monthly budget exceeded ($${monthlyUsed.toFixed(4)} / $${this.budget.maxMonthlyUsd})`,
        dailyUsedUsd: dailyUsed,
        dailyRemainingUsd: Math.max(0, this.budget.maxDailyUsd - dailyUsed),
        monthlyUsedUsd: monthlyUsed,
      };
    }

    return {
      allowed: true,
      dailyUsedUsd: dailyUsed,
      dailyRemainingUsd:
        this.budget.maxDailyUsd > 0
          ? Math.max(0, this.budget.maxDailyUsd - dailyUsed)
          : Infinity,
      monthlyUsedUsd: monthlyUsed,
    };
  }

  /**
   * Get a summary of costs, optionally filtered by time range.
   */
  getSummary(since?: number): CostSummary {
    const filtered = since
      ? this.entries.filter((e) => e.timestamp >= since)
      : this.entries;

    const byProvider: Record<string, { tokens: number; costUsd: number }> = {};
    const byModel: Record<string, { tokens: number; costUsd: number }> = {};
    let totalPrompt = 0;
    let totalCompletion = 0;
    let totalCost = 0;

    for (const e of filtered) {
      const tokens = e.promptTokens + e.completionTokens;
      totalPrompt += e.promptTokens;
      totalCompletion += e.completionTokens;
      totalCost += e.estimatedCostUsd;

      if (!byProvider[e.provider])
        byProvider[e.provider] = { tokens: 0, costUsd: 0 };
      byProvider[e.provider].tokens += tokens;
      byProvider[e.provider].costUsd += e.estimatedCostUsd;

      if (!byModel[e.model]) byModel[e.model] = { tokens: 0, costUsd: 0 };
      byModel[e.model].tokens += tokens;
      byModel[e.model].costUsd += e.estimatedCostUsd;
    }

    return {
      totalPromptTokens: totalPrompt,
      totalCompletionTokens: totalCompletion,
      totalTokens: totalPrompt + totalCompletion,
      estimatedCostUsd: totalCost,
      entries: filtered.length,
      byProvider,
      byModel,
    };
  }

  // ─── Persistence ────────────────────────────────────────────

  async load(): Promise<void> {
    if (!this.persistPath || !existsSync(this.persistPath)) return;
    try {
      const raw = await readFile(this.persistPath, "utf-8");
      const data = JSON.parse(raw);
      if (Array.isArray(data.entries)) {
        this.entries = data.entries;
      }
    } catch {
      // Start fresh on corrupt data
    }
  }

  async save(): Promise<void> {
    if (!this.persistPath || !this.dirty) return;
    try {
      // Only persist last 30 days of entries to limit file growth
      const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000;
      const recent = this.entries.filter((e) => e.timestamp >= cutoff);
      await writeFile(
        this.persistPath,
        JSON.stringify({ entries: recent }, null, 2),
        "utf-8",
      );
      this.dirty = false;
    } catch (err) {
      log.engine.warn(
        `[CostTracker] Save failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  // ─── Helpers ────────────────────────────────────────────────

  private getDailySpend(): number {
    const dayStart = new Date();
    dayStart.setHours(0, 0, 0, 0);
    const cutoff = dayStart.getTime();
    return this.entries
      .filter((e) => e.timestamp >= cutoff)
      .reduce((sum, e) => sum + e.estimatedCostUsd, 0);
  }

  private getMonthlySpend(): number {
    const monthStart = new Date();
    monthStart.setDate(1);
    monthStart.setHours(0, 0, 0, 0);
    const cutoff = monthStart.getTime();
    return this.entries
      .filter((e) => e.timestamp >= cutoff)
      .reduce((sum, e) => sum + e.estimatedCostUsd, 0);
  }

  private debounceSave(): void {
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      this.save().catch(() => {});
    }, 5000);
    this.saveTimer.unref?.();
  }
}
