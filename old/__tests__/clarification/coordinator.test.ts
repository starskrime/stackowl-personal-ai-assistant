import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ClarificationCoordinator } from '../../src/clarification/coordinator.js';

describe('ClarificationCoordinator', () => {
  let coordinator: ClarificationCoordinator;

  beforeEach(() => { coordinator = new ClarificationCoordinator(); });

  it('allows the first question for a reasoning hash', () => {
    expect(coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session1')).toBe(false);
  });

  it('suppresses the same reasoning within 5 minutes for same session', () => {
    coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session1');
    expect(coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session1')).toBe(true);
  });

  it('does NOT suppress for a different session', () => {
    coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session1');
    expect(coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session2')).toBe(false);
  });

  it('does NOT suppress different reasoning for same session', () => {
    coordinator.shouldSuppressDuplicate('user wants X but is vague', 'session1');
    expect(coordinator.shouldSuppressDuplicate('completely different reasoning here', 'session1')).toBe(false);
  });

  it('allows question after window expires', () => {
    vi.useFakeTimers();
    coordinator.shouldSuppressDuplicate('user wants X', 'session1');
    vi.advanceTimersByTime(6 * 60 * 1000); // 6 minutes
    expect(coordinator.shouldSuppressDuplicate('user wants X', 'session1')).toBe(false);
    vi.useRealTimers();
  });

  it('clear() resets all entries', () => {
    coordinator.shouldSuppressDuplicate('user wants X', 'session1');
    coordinator.clear();
    expect(coordinator.shouldSuppressDuplicate('user wants X', 'session1')).toBe(false);
  });
});
