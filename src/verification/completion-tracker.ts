export type TimeWindow = 'daily' | 'weekly' | 'monthly';

export interface CompletionStats {
  totalTasks: number;
  completedTasks: number;
  failedTasks: number;
  pendingTasks: number;
  completionRate: number;
  failureRate: number;
  window?: TimeWindow;
  fromDate?: string;
  toDate?: string;
}

export interface TaskOutcome {
  taskId: string;
  success: boolean;
  recordedAt: string;
  taskType?: string;
}

export interface CompletionTrackerConfig {
  defaultWindow?: TimeWindow;
  retentionDays?: number;
}

const DEFAULT_CONFIG: CompletionTrackerConfig = {
  defaultWindow: 'daily',
  retentionDays: 30,
};

export class CompletionTracker {
  private config: CompletionTrackerConfig;
  private outcomes: TaskOutcome[] = [];
  private taskTypes: Map<string, number> = new Map();

  constructor(config: Partial<CompletionTrackerConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  recordOutcome(taskId: string, success: boolean, taskType?: string): void {
    const outcome: TaskOutcome = {
      taskId,
      success,
      recordedAt: new Date().toISOString(),
      taskType,
    };
    this.outcomes.push(outcome);

    if (taskType) {
      const count = this.taskTypes.get(taskType) ?? 0;
      this.taskTypes.set(taskType, count + 1);
    }

    this.logBehavioral('recorded', taskId);
  }

  getStats(window?: TimeWindow): CompletionStats {
    const effectiveWindow: TimeWindow = window ?? (this.config.defaultWindow ?? 'daily');
    const cutoff = this.getCutoffDate(effectiveWindow);

    const filteredOutcomes = this.outcomes.filter(o => {
      if (!o.recordedAt) return true;
      return new Date(o.recordedAt) >= cutoff;
    });

    const total = filteredOutcomes.length;
    const completed = filteredOutcomes.filter(o => o.success).length;
    const failed = filteredOutcomes.filter(o => !o.success).length;
    const pending = 0;

    return {
      totalTasks: total,
      completedTasks: completed,
      failedTasks: failed,
      pendingTasks: pending,
      completionRate: total > 0 ? completed / total : 0,
      failureRate: total > 0 ? failed / total : 0,
      window: effectiveWindow,
      fromDate: cutoff.toISOString(),
      toDate: new Date().toISOString(),
    };
  }

  getCompletionRate(window?: TimeWindow): number {
    const stats = this.getStats(window);
    return stats.completionRate;
  }

  toEvolutionInput(): {
    completionRate: number;
    failureRate: number;
    totalTasks: number;
    recentTrends: string[];
  } {
    const weeklyStats = this.getStats('weekly');
    const monthlyStats = this.getStats('monthly');

    const trends: string[] = [];
    if (weeklyStats.completionRate > 0.8) {
      trends.push('High weekly completion rate');
    } else if (weeklyStats.completionRate < 0.5) {
      trends.push('Low weekly completion rate - needs attention');
    }

    if (monthlyStats.failureRate > 0.3) {
      trends.push('High monthly failure rate');
    }

    return {
      completionRate: weeklyStats.completionRate,
      failureRate: weeklyStats.failureRate,
      totalTasks: weeklyStats.totalTasks,
      recentTrends: trends,
    };
  }

  private getCutoffDate(window: TimeWindow): Date {
    const now = new Date();
    const days = window === 'daily' ? 1 : window === 'weekly' ? 7 : 30;
    return new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
  }

  private logBehavioral(event: 'recorded' | 'rate_updated', taskId: string): void {
    const timestamp = new Date().toISOString();
    switch (event) {
      case 'recorded':
        console.log(
          `${timestamp} INFO [CompletionTracker] behavioral.completion.recorded taskId=${taskId}`,
        );
        break;
      case 'rate_updated':
        const rate = this.getCompletionRate();
        console.log(
          `${timestamp} INFO [CompletionTracker] behavioral.completion.rate_updated rate=${rate.toFixed(3)}`,
        );
        break;
    }
  }

  getTotalCount(): number {
    return this.outcomes.length;
  }

  getSuccessfulCount(): number {
    return this.outcomes.filter(o => o.success).length;
  }

  getFailedCount(): number {
    return this.outcomes.filter(o => !o.success).length;
  }

  getOutcomesByTaskType(taskType: string): TaskOutcome[] {
    return this.outcomes.filter(o => o.taskType === taskType);
  }

  clearOldEntries(): void {
    const cutoff = new Date(
      Date.now() - (this.config.retentionDays ?? 30) * 24 * 60 * 60 * 1000
    );
    this.outcomes = this.outcomes.filter(o => new Date(o.recordedAt) >= cutoff);
  }
}

export const completionTracker = new CompletionTracker();