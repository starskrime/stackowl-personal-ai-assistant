import type { PreActionQuestioner } from './pre-action-questioner.js';

export type RiskGateResult =
  | { allowed: true }
  | { allowed: false; confirmationId: string; userFacingMessage: string };

export class ToolRiskGuard {
  private pendingConfirmations: Map<string, { questionId: string; answer: 'pending' | 'confirmed' | 'cancelled' }> = new Map();

  constructor(private questioner: PreActionQuestioner) {}

  async check(
    toolName: string,
    args: Record<string, unknown>,
    _toolPolicy: Record<string, unknown>,
  ): Promise<RiskGateResult> {
    const risk = await this.questioner.assessRisk(toolName, args);

    if (!risk.shouldConfirm) {
      return { allowed: true };
    }

    const question = this.questioner.generateQuestion(toolName, args, risk.riskLevel);
    const confirmationId = `risk_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    this.pendingConfirmations.set(confirmationId, { questionId: question.id, answer: 'pending' });

    return {
      allowed: false,
      confirmationId,
      userFacingMessage: question.question,
    };
  }

  resolveConfirmation(confirmationId: string, userAnswer: string): 'confirmed' | 'cancelled' | 'not_found' {
    const pending = this.pendingConfirmations.get(confirmationId);
    if (!pending) return 'not_found';

    const isAffirmative = /^(yes|y|confirm|ok|sure|proceed|do it)\b/i.test(userAnswer.trim());
    const answer = isAffirmative ? 'confirmed' : 'cancelled';

    pending.answer = answer;
    if (answer === 'confirmed') {
      this.questioner.confirmAction(pending.questionId);
    } else {
      this.questioner.cancelAction(pending.questionId);
    }
    this.pendingConfirmations.delete(confirmationId);
    return answer;
  }
}
