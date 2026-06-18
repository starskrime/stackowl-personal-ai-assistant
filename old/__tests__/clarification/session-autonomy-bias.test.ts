import { describe, it, expect, beforeEach } from 'vitest';
import { SessionAutonomyBias } from '../../src/clarification/session-autonomy-bias.js';

describe('SessionAutonomyBias', () => {
  let bias: SessionAutonomyBias;

  beforeEach(() => { bias = new SessionAutonomyBias(); });

  it('starts at zero dismissals', () => {
    expect(bias.dismissCount).toBe(0);
  });

  it('increments on recordDismissal', () => {
    bias.recordDismissal();
    bias.recordDismissal();
    expect(bias.dismissCount).toBe(2);
  });

  it('toPromptContext returns empty string at zero', () => {
    expect(bias.toPromptContext()).toBe('');
  });

  it('toPromptContext mentions 1 dismissal', () => {
    bias.recordDismissal();
    expect(bias.toPromptContext()).toContain('1 clarification question');
  });

  it('toPromptContext prefers PROCEED at 2+ dismissals', () => {
    bias.recordDismissal();
    bias.recordDismissal();
    expect(bias.toPromptContext()).toContain('prefer PROCEED');
  });
});
