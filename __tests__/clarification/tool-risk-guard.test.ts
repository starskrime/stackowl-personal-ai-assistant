import { describe, it, expect, vi } from 'vitest';
import { ToolRiskGuard } from '../../src/clarification/tool-risk-guard.js';
import type { PreActionQuestioner, RiskAssessment } from '../../src/clarification/pre-action-questioner.js';

function makeQuestioner(risk: Partial<RiskAssessment>): PreActionQuestioner {
  const full: RiskAssessment = {
    riskLevel: 'low',
    riskReasons: [],
    shouldConfirm: false,
    confirmationQuestion: null,
    ...risk,
  };
  return {
    assessRisk: vi.fn().mockResolvedValue(full),
    generateQuestion: vi.fn().mockReturnValue({
      id: 'q1', toolName: 'T', action: 'delete', question: 'Are you sure?',
      isReversible: false, riskLevel: 'high', timestamp: new Date().toISOString(),
    }),
    confirmAction: vi.fn().mockReturnValue(true),
    cancelAction: vi.fn().mockReturnValue(true),
    isConfirmed: vi.fn().mockReturnValue(false),
    getPendingQuestions: vi.fn().mockReturnValue([]),
    shouldQuestionAction: vi.fn(),
    hasPendingConfirmation: vi.fn().mockReturnValue(false),
    clearPending: vi.fn(),
    clearConfirmed: vi.fn(),
  } as any;
}

describe('ToolRiskGuard', () => {
  it('allows low-risk tools', async () => {
    const guard = new ToolRiskGuard(makeQuestioner({ riskLevel: 'low', shouldConfirm: false }));
    const result = await guard.check('ReadFile', { path: '/tmp/a.txt' }, {});
    expect(result.allowed).toBe(true);
  });

  it('suspends high-risk tools', async () => {
    const guard = new ToolRiskGuard(makeQuestioner({ riskLevel: 'high', shouldConfirm: true, confirmationQuestion: 'Delete?' }));
    const result = await guard.check('DeleteFile', { path: '/important.txt' }, {});
    expect(result.allowed).toBe(false);
    if (!result.allowed) {
      expect(result.confirmationId).toBeTruthy();
      expect(result.userFacingMessage).toContain('Are you sure?');
    }
  });

  it('resolveConfirmation confirms a pending action', async () => {
    const q = makeQuestioner({ riskLevel: 'critical', shouldConfirm: true, confirmationQuestion: 'Confirm?' });
    const guard = new ToolRiskGuard(q);
    const result = await guard.check('DropTable', {}, {});
    if (!result.allowed) {
      const outcome = guard.resolveConfirmation(result.confirmationId, 'yes');
      expect(outcome).toBe('confirmed');
    }
  });

  it('resolveConfirmation cancels a pending action', async () => {
    const q = makeQuestioner({ riskLevel: 'high', shouldConfirm: true, confirmationQuestion: 'Sure?' });
    const guard = new ToolRiskGuard(q);
    const result = await guard.check('DeleteFile', {}, {});
    if (!result.allowed) {
      const outcome = guard.resolveConfirmation(result.confirmationId, 'no');
      expect(outcome).toBe('cancelled');
    }
  });

  it('resolveConfirmation returns not_found for unknown id', () => {
    const guard = new ToolRiskGuard(makeQuestioner({}));
    expect(guard.resolveConfirmation('nonexistent', 'yes')).toBe('not_found');
  });
});
