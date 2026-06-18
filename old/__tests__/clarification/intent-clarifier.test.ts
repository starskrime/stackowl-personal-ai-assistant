import { describe, it, expect, vi, beforeEach } from 'vitest';
import { IntentClarifier } from '../../src/clarification/intent-clarifier.js';
import { ClarificationCoordinator } from '../../src/clarification/coordinator.js';
import { SessionAutonomyBias } from '../../src/clarification/session-autonomy-bias.js';
import type { IntelligenceRouter } from '../../src/intelligence/router.js';
import type { ModelProvider } from '../../src/providers/base.js';

function makeProvider(verdictJson: string): ModelProvider {
  return { chat: vi.fn().mockResolvedValue({ content: verdictJson }) } as any;
}

function makeRouter(): IntelligenceRouter {
  return { resolve: vi.fn().mockReturnValue({ provider: 'test', model: 'test-model', tier: 'mid' }) } as any;
}

function makeDna(delegation: 'autonomous' | 'collaborative' | 'confirmatory' = 'collaborative'): any {
  return { evolvedTraits: { delegationPreference: delegation }, learnedPreferences: {} };
}

describe('IntentClarifier', () => {
  let coordinator: ClarificationCoordinator;
  let bias: SessionAutonomyBias;

  beforeEach(() => {
    coordinator = new ClarificationCoordinator();
    bias = new SessionAutonomyBias();
  });

  it('returns PROCEED for the ZimaBoard research request', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'PROCEED', question: null, interpretation: null,
      reasoning: 'clear research request'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate(
      'can you do research about zimaboard 2, tell me where i can use?',
      [], makeDna(), bias
    );
    expect(result.verdict).toBe('PROCEED');
    expect(result.question).toBeNull();
  });

  it('returns CLARIFY with question for genuinely ambiguous request', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'CLARIFY',
      question: 'Which file did you mean — config.yaml or package.json?',
      interpretation: null,
      reasoning: 'multiple files match "that file"'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate('edit it', [], makeDna(), bias);
    expect(result.verdict).toBe('CLARIFY');
    expect(result.question).toBe('Which file did you mean — config.yaml or package.json?');
  });

  it('suppresses duplicate via coordinator and returns PROCEED', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'CLARIFY',
      question: 'Which file?',
      interpretation: null,
      reasoning: 'multiple files match'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    // First call — allowed
    await clarifier.evaluate('edit it', [], makeDna(), bias, 'session1');
    // Second call same reasoning — suppressed, returns PROCEED
    const result = await clarifier.evaluate('edit it', [], makeDna(), bias, 'session1');
    expect(result.verdict).toBe('PROCEED');
  });

  it('returns NARRATE with interpretation', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'NARRATE',
      question: null,
      interpretation: 'update the package.json version field',
      reasoning: 'slightly ambiguous but proceeding is safe'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate('bump it', [], makeDna(), bias);
    expect(result.verdict).toBe('NARRATE');
    expect(result.interpretation).toBe('update the package.json version field');
  });

  it('returns USER_CONFUSED for expressed confusion', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'USER_CONFUSED', question: null, interpretation: null,
      reasoning: 'user says they are not sure which approach'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate(
      "I'm not sure which approach is better", [], makeDna(), bias
    );
    expect(result.verdict).toBe('USER_CONFUSED');
  });

  it('fails open to PROCEED on LLM parse error', async () => {
    const provider = makeProvider('not valid json at all');
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate('do the thing', [], makeDna(), bias);
    expect(result.verdict).toBe('PROCEED');
  });

  it('fails open to PROCEED on LLM exception', async () => {
    const provider = { chat: vi.fn().mockRejectedValue(new Error('network error')) } as any;
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    const result = await clarifier.evaluate('do the thing', [], makeDna(), bias);
    expect(result.verdict).toBe('PROCEED');
  });

  it('includes delegationPreference in the LLM prompt', async () => {
    const provider = makeProvider(JSON.stringify({
      verdict: 'PROCEED', question: null, interpretation: null, reasoning: 'clear'
    }));
    const clarifier = new IntentClarifier(provider, makeRouter(), coordinator);
    await clarifier.evaluate('do something', [], makeDna('autonomous'), bias);
    const callArg = (provider.chat as any).mock.calls[0][0][0].content as string;
    expect(callArg).toContain('autonomous');
  });
});
