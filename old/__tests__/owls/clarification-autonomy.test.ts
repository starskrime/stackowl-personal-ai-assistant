import { describe, it, expect, vi } from 'vitest';
import { updateClarificationAutonomy } from '../../src/owls/evolution.js';
import type { OwlDNA } from '../../src/owls/persona.js';

function makeDna(score?: number): OwlDNA {
  return {
    generation: 1,
    learnedPreferences: score !== undefined ? { clarification_autonomy_score: score } : {},
    evolvedTraits: {
      verbosity: 'balanced', challengeLevel: 'medium', humor: 0.3, formality: 0.5,
      delegationPreference: 'collaborative', proactivity: 0.5, riskTolerance: 'moderate',
    },
    expertiseGrowth: {},
    interactionStats: { totalConversations: 0, avgSessionLength: 0, adviceAcceptedRate: 0 },
  } as any;
}

function makeDb(rows: Array<{ reward: number; clarification_asked: number }>): any {
  return {
    trajectories: {
      getRecentWithClarification: vi.fn().mockReturnValue(
        rows.map((r, i) => ({ id: String(i), reward: r.reward, clarification_asked: r.clarification_asked }))
      ),
    },
  };
}

describe('updateClarificationAutonomy', () => {
  it('does nothing with fewer than 5 trajectories', async () => {
    const dna = makeDna(0.5);
    await updateClarificationAutonomy('owl1', makeDb([
      { reward: 0.9, clarification_asked: 0 },
    ]), dna);
    expect(dna.learnedPreferences['clarification_autonomy_score']).toBeUndefined();
  });

  it('increases score when proceeding gets better rewards', async () => {
    const dna = makeDna(0.5);
    const rows = [
      ...Array(8).fill({ reward: 0.9, clarification_asked: 0 }),
      ...Array(7).fill({ reward: 0.2, clarification_asked: 1 }),
    ];
    await updateClarificationAutonomy('owl1', makeDb(rows), dna);
    const score = dna.learnedPreferences['clarification_autonomy_score'] as number;
    expect(score).toBeGreaterThan(0.5);
  });

  it('decreases score when asking gets better rewards', async () => {
    const dna = makeDna(0.5);
    const rows = [
      ...Array(8).fill({ reward: 0.9, clarification_asked: 1 }),
      ...Array(7).fill({ reward: 0.1, clarification_asked: 0 }),
    ];
    await updateClarificationAutonomy('owl1', makeDb(rows), dna);
    const score = dna.learnedPreferences['clarification_autonomy_score'] as number;
    expect(score).toBeLessThan(0.5);
  });

  it('clamps score between 0.1 and 0.9', async () => {
    const dna = makeDna(0.9);
    const rows = Array(10).fill({ reward: 1.0, clarification_asked: 0 });
    await updateClarificationAutonomy('owl1', makeDb(rows), dna);
    const score = dna.learnedPreferences['clarification_autonomy_score'] as number;
    expect(score).toBeLessThanOrEqual(0.9);
    expect(score).toBeGreaterThanOrEqual(0.1);
  });

  it('uses proportional delta not Math.sign', async () => {
    const dna = makeDna(0.5);
    const rows = [
      ...Array(8).fill({ reward: 0.51, clarification_asked: 0 }),
      ...Array(7).fill({ reward: 0.50, clarification_asked: 1 }),
    ];
    await updateClarificationAutonomy('owl1', makeDb(rows), dna);
    const score = dna.learnedPreferences['clarification_autonomy_score'] as number;
    expect(Math.abs(score - 0.5)).toBeLessThan(0.01);
  });
});
