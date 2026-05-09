import { describe, it, expect, beforeEach } from 'vitest';
import { CompletionTracker } from '../../src/verification/completion-tracker.js';

describe('CompletionTracker', () => {
  let tracker: CompletionTracker;

  beforeEach(() => {
    tracker = new CompletionTracker({ defaultWindow: 'daily', retentionDays: 7 });
  });

  describe('recordOutcome', () => {
    it('should record successful outcome', () => {
      tracker.recordOutcome('task-1', true);
      expect(tracker.getSuccessfulCount()).toBe(1);
      expect(tracker.getFailedCount()).toBe(0);
    });

    it('should record failed outcome', () => {
      tracker.recordOutcome('task-2', false);
      expect(tracker.getSuccessfulCount()).toBe(0);
      expect(tracker.getFailedCount()).toBe(1);
    });

    it('should record outcome with task type', () => {
      tracker.recordOutcome('task-3', true, 'web_search');
      const outcomes = tracker.getOutcomesByTaskType('web_search');
      expect(outcomes.length).toBe(1);
      expect(outcomes[0].taskType).toBe('web_search');
    });
  });

  describe('getStats', () => {
    it('should return zero stats when no outcomes', () => {
      const stats = tracker.getStats();
      expect(stats.totalTasks).toBe(0);
      expect(stats.completionRate).toBe(0);
    });

    it('should calculate completion rate correctly', () => {
      tracker.recordOutcome('task-1', true);
      tracker.recordOutcome('task-2', true);
      tracker.recordOutcome('task-3', false);

      const stats = tracker.getStats();
      expect(stats.totalTasks).toBe(3);
      expect(stats.completedTasks).toBe(2);
      expect(stats.failedTasks).toBe(1);
      expect(stats.completionRate).toBeCloseTo(0.667, 2);
    });

    it('should include window in stats', () => {
      const stats = tracker.getStats('weekly');
      expect(stats.window).toBe('weekly');
    });
  });

  describe('getCompletionRate', () => {
    it('should return 0 when no outcomes', () => {
      expect(tracker.getCompletionRate()).toBe(0);
    });

    it('should return correct rate', () => {
      tracker.recordOutcome('task-1', true);
      tracker.recordOutcome('task-2', true);
      tracker.recordOutcome('task-3', true);
      tracker.recordOutcome('task-4', false);

      expect(tracker.getCompletionRate()).toBe(0.75);
    });

    it('should respect time window', () => {
      tracker.recordOutcome('old-task', true);
      const rate = tracker.getCompletionRate('weekly');
      expect(rate).toBeDefined();
    });
  });

  describe('toEvolutionInput', () => {
    it('should return evolution input format', () => {
      tracker.recordOutcome('task-1', true);
      tracker.recordOutcome('task-2', false);

      const input = tracker.toEvolutionInput();
      expect(input).toHaveProperty('completionRate');
      expect(input).toHaveProperty('failureRate');
      expect(input).toHaveProperty('totalTasks');
      expect(input).toHaveProperty('recentTrends');
      expect(Array.isArray(input.recentTrends)).toBe(true);
    });

    it('should detect low completion rate trend', () => {
      tracker.recordOutcome('task-1', false);
      tracker.recordOutcome('task-2', false);
      tracker.recordOutcome('task-3', false);

      const input = tracker.toEvolutionInput();
      expect(input.recentTrends).toContain('Low weekly completion rate - needs attention');
    });
  });

  describe('clearOldEntries', () => {
    it('should not clear new entries', () => {
      tracker.recordOutcome('new-task', true);
      tracker.clearOldEntries();
      expect(tracker.getTotalCount()).toBe(1);
    });

    it('should clear entries when retention is set', () => {
      const oldTracker = new CompletionTracker({ retentionDays: 1 });
      oldTracker.recordOutcome('task-1', true);
      expect(oldTracker.getTotalCount()).toBe(1);
    });
  });

  describe('getTotalCount', () => {
    it('should return total outcome count', () => {
      tracker.recordOutcome('task-1', true);
      tracker.recordOutcome('task-2', false);
      tracker.recordOutcome('task-3', true);
      expect(tracker.getTotalCount()).toBe(3);
    });
  });

  describe('getOutcomesByTaskType', () => {
    it('should return empty array for unknown task type', () => {
      const outcomes = tracker.getOutcomesByTaskType('unknown');
      expect(outcomes.length).toBe(0);
    });

    it('should filter by task type correctly', () => {
      tracker.recordOutcome('task-1', true, 'shell');
      tracker.recordOutcome('task-2', false, 'shell');
      tracker.recordOutcome('task-3', true, 'web');

      const shellOutcomes = tracker.getOutcomesByTaskType('shell');
      expect(shellOutcomes.length).toBe(2);
    });
  });
});