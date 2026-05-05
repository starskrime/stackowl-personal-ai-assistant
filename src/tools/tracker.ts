/**
 * StackOwl — Tool Execution Tracker (SQLite-backed)
 *
 * Records every tool call to the `tool_executions` table (schema v23) and
 * answers stats queries by aggregating that table. Captures error_code +
 * error_message on failure (previously dropped). Optional sessionId and
 * subgoalId let downstream layers (CWTG, PTR) trace tool usage to goal state.
 */

import type { MemoryDatabase } from "../memory/db.js";

export interface ToolUsageStats {
  selectionCount: number;
  successCount: number;
  failureCount: number;
  avgDurationMs: number;
  lastUsedAt: string | null;
  successRate: number;
}

export class ToolTracker {
  constructor(private readonly db: MemoryDatabase) {}

  recordSuccess(
    toolName: string,
    durationMs: number,
    ctx: { subgoalId?: string; sessionId?: string; attemptMetadata?: unknown[] } = {},
  ): void {
    this.db.recordToolExecution({
      toolName,
      success: true,
      durationMs,
      subgoalId: ctx.subgoalId,
      sessionId: ctx.sessionId,
      attemptMetadata: ctx.attemptMetadata ? JSON.stringify(ctx.attemptMetadata) : undefined,
    });
  }

  recordFailure(
    toolName: string,
    durationMs: number,
    ctx: {
      errorCode?: string;
      errorMessage?: string;
      subgoalId?: string;
      sessionId?: string;
      attemptMetadata?: unknown[];
    } = {},
  ): void {
    this.db.recordToolExecution({
      toolName,
      success: false,
      durationMs,
      errorCode: ctx.errorCode,
      errorMessage: ctx.errorMessage,
      subgoalId: ctx.subgoalId,
      sessionId: ctx.sessionId,
      attemptMetadata: ctx.attemptMetadata ? JSON.stringify(ctx.attemptMetadata) : undefined,
    });
  }

  getStats(toolName: string, days = 30): ToolUsageStats | null {
    const s = this.db.getToolStats(toolName, { days });
    if (!s) return null;
    const completed = s.successCount + s.failureCount;
    return {
      ...s,
      successRate: completed === 0 ? 0 : s.successCount / completed,
    };
  }

  getSuccessRate(toolName: string, days = 30): number | undefined {
    const s = this.db.getToolStats(toolName, { days });
    if (!s) return undefined;
    const completed = s.successCount + s.failureCount;
    if (completed === 0) return undefined;
    return s.successCount / completed;
  }

  /** Days since last use (Infinity if never used) */
  getDaysSinceLastUse(toolName: string, days = 30): number {
    const s = this.db.getToolStats(toolName, { days });
    if (!s || !s.lastUsedAt) return Infinity;
    const msPerDay = 24 * 60 * 60 * 1000;
    return (Date.now() - new Date(s.lastUsedAt).getTime()) / msPerDay;
  }

  /**
   * Recency-adjusted usage multiplier for re-ranking.
   * Range: 0.35 (never used) → 1.3 (100% success, used today).
   * Half-life: 90 days (recent successes matter ~2.7× more than stale ones).
   * Preserved verbatim from the JSON-backed tracker.
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

  /**
   * Top-N tools by selection count over the window.
   * Return shape preserves `{ name, stats }` for back-compat with
   * `CapabilityScanner` and any other callers that read `.name`.
   */
  getTopBySelectionCount(
    n: number,
    days = 30,
  ): Array<{ name: string; stats: ToolUsageStats }> {
    const rows = this.db.rawDb
      .prepare(
        `SELECT tool_name, COUNT(*) AS selection_count
           FROM tool_executions
           WHERE created_at > datetime('now', '-' || ? || ' days')
           GROUP BY tool_name
           ORDER BY selection_count DESC
           LIMIT ?`,
      )
      .all(days, n) as Array<{ tool_name: string; selection_count: number }>;

    return rows
      .map((r) => {
        const stats = this.getStats(r.tool_name, days);
        return stats ? { name: r.tool_name, stats } : null;
      })
      .filter((x): x is { name: string; stats: ToolUsageStats } => x !== null);
  }
}
