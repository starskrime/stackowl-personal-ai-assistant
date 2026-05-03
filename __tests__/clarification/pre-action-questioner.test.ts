import { describe, it, expect, vi } from 'vitest';
import { PreActionQuestioner } from '../../src/clarification/pre-action-questioner.js';
import type { IntelligenceRouter } from '../../src/intelligence/router.js';

function makeProvider(json: string) {
  return { chat: vi.fn().mockResolvedValue({ content: json }) } as any;
}

function makeRouter(): IntelligenceRouter {
  return { resolve: vi.fn().mockReturnValue({ provider: 'test', model: 'test-model', tier: 'mid' }) } as any;
}

describe('PreActionQuestioner', () => {
  it('uses router model for risk assessment', async () => {
    const provider = makeProvider(JSON.stringify({
      riskLevel: 'low', riskReasons: [], shouldConfirm: false, confirmationQuestion: null
    }));
    const router = makeRouter();
    const questioner = new PreActionQuestioner(provider, router);
    await questioner.assessRisk('ReadFile', { path: '/tmp/test.txt' });
    expect(router.resolve).toHaveBeenCalledWith('clarification');
    expect(provider.chat).toHaveBeenCalledWith(
      expect.any(Array), 'test-model', expect.any(Object)
    );
  });

  it('fails open to low/no-confirm on parse error (not medium)', async () => {
    const provider = makeProvider('not json');
    const questioner = new PreActionQuestioner(provider, makeRouter());
    const result = await questioner.assessRisk('SomeTool', {});
    expect(result.riskLevel).toBe('low');
    expect(result.shouldConfirm).toBe(false);
  });
});
