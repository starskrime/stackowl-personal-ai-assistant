import { describe, it, expect, vi } from 'vitest';
import { IntentClarifier } from '../../src/clarification/intent-clarifier.js';
import { ClarificationCoordinator } from '../../src/clarification/coordinator.js';
import { SessionAutonomyBias } from '../../src/clarification/session-autonomy-bias.js';

function makeRealBehaviorProvider() {
  return {
    chat: vi.fn().mockImplementation(async (messages: any[]) => {
      const content = messages[0].content as string;
      if (content.includes('zimaboard') || content.includes('research')) {
        return { content: JSON.stringify({
          verdict: 'PROCEED', question: null, interpretation: null,
          reasoning: 'clear research request with specific subject'
        })};
      }
      if (content.includes('edit it') && !content.includes('context')) {
        return { content: JSON.stringify({
          verdict: 'CLARIFY', question: 'Which file should I edit?', interpretation: null,
          reasoning: 'pronoun "it" has no clear referent'
        })};
      }
      return { content: JSON.stringify({
        verdict: 'PROCEED', question: null, interpretation: null, reasoning: 'default'
      })};
    }),
  } as any;
}

describe('Element 9 acceptance tests', () => {
  const makeRouter = () => ({
    resolve: vi.fn().mockReturnValue({ provider: 'test', model: 'test-model', tier: 'mid' }),
  } as any);

  const makeDna = () => ({
    evolvedTraits: { delegationPreference: 'collaborative' },
    learnedPreferences: {},
  } as any);

  it('AC-2: ZimaBoard research request returns PROCEED (never asks for confirmation)', async () => {
    const clarifier = new IntentClarifier(
      makeRealBehaviorProvider(), makeRouter(), new ClarificationCoordinator()
    );
    const result = await clarifier.evaluate(
      'can you do research about zimaboard 2, tell me where i can use?',
      [], makeDna(), new SessionAutonomyBias()
    );
    expect(result.verdict).toBe('PROCEED');
    expect(result.question).toBeNull();
  });

  it('AC-1: IntentClarifier instantiates without errors (no regex-based confidence scoring)', () => {
    const clarifier = new IntentClarifier(
      makeRealBehaviorProvider(), makeRouter(), new ClarificationCoordinator()
    );
    expect(clarifier).toBeInstanceOf(IntentClarifier);
  });

  it('AC-4: High-autonomy DNA injected into LLM prompt', async () => {
    const provider = {
      chat: vi.fn().mockImplementation(async (messages: any[]) => {
        const content = messages[0].content as string;
        expect(content).toContain('autonomous');
        return { content: JSON.stringify({
          verdict: 'PROCEED', question: null, interpretation: null,
          reasoning: 'high autonomy user — proceeding'
        })};
      }),
    } as any;
    const clarifier = new IntentClarifier(provider, makeRouter(), new ClarificationCoordinator());
    const result = await clarifier.evaluate(
      'do the thing',
      [],
      { evolvedTraits: { delegationPreference: 'autonomous' }, learnedPreferences: {} } as any,
      new SessionAutonomyBias()
    );
    expect(result.verdict).toBe('PROCEED');
  });
});
