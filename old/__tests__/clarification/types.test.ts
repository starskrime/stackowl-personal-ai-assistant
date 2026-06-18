import { describe, it, expectTypeOf } from 'vitest';
import type { IntentVerdict, IntentClassification } from '../../src/clarification/types.js';

describe('clarification types', () => {
  it('IntentVerdict has four values', () => {
    const v: IntentVerdict = 'PROCEED';
    expectTypeOf(v).toMatchTypeOf<'PROCEED' | 'NARRATE' | 'CLARIFY' | 'USER_CONFUSED'>();
  });

  it('IntentClassification has required fields', () => {
    const c: IntentClassification = {
      verdict: 'PROCEED',
      question: null,
      interpretation: null,
      reasoning: 'clear request',
    };
    expectTypeOf(c.verdict).toEqualTypeOf<IntentVerdict>();
  });
});
