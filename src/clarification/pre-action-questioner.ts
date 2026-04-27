import type { ModelProvider } from '../providers/base.js';

export interface RiskAssessment {
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  riskReasons: string[];
  shouldConfirm: boolean;
  confirmationQuestion: string | null;
}

const RISK_ASSESSMENT_PROMPT = `Assess the risk level of this action.

Action: tool="{toolName}" parameters={JSON.stringify(params)}

Risk levels:
- "low": Read-only operations, no side effects
- "medium": Creates or modifies files/data but can be undone
- "high": Irreversible operations (delete, drop, truncate, shutdown)
- "critical": Operations that destroy data or systems permanently

Respond with JSON:
{
  "riskLevel": "low|medium|high|critical",
  "riskReasons": ["reason1"],
  "shouldConfirm": boolean,
  "confirmationQuestion": "User-facing question to confirm" or null
}`;

export class PreActionQuestioner {
  private modelProvider: ModelProvider;
  private pendingQuestions: import('./types.js').PreActionQuestion[] = [];
  private confirmedActions: Set<string> = new Set();

  constructor(modelProvider: ModelProvider) {
    this.modelProvider = modelProvider;
  }

  async assessRisk(toolName: string, params: Record<string, unknown>): Promise<RiskAssessment> {
    try {
      const response = await this.modelProvider.chat(
        [
          {
            role: 'user',
            content: RISK_ASSESSMENT_PROMPT
              .replace('{toolName}', toolName)
              .replace('{JSON.stringify(params)}', JSON.stringify(params)),
          },
        ],
        undefined,
        { temperature: 0.1 }
      );

      const parsed = this.parseLlmResponse(response.content);
      if (!parsed) {
        return {
          riskLevel: 'medium',
          riskReasons: ['Failed to parse LLM response'],
          shouldConfirm: false,
          confirmationQuestion: null,
        };
      }

      return {
        riskLevel: parsed.riskLevel as RiskAssessment['riskLevel'],
        riskReasons: parsed.riskReasons || [],
        shouldConfirm: parsed.shouldConfirm ?? true,
        confirmationQuestion: parsed.confirmationQuestion ?? null,
      };
    } catch {
      return {
        riskLevel: 'low',
        riskReasons: ['Risk assessment unavailable'],
        shouldConfirm: false,
        confirmationQuestion: null,
      };
    }
  }

  async shouldQuestionAction(toolName: string, params: Record<string, unknown>): Promise<boolean> {
    const assessment = await this.assessRisk(toolName, params);

    if (assessment.shouldConfirm && (assessment.riskLevel === 'high' || assessment.riskLevel === 'critical')) {
      this.logBehavioralEvent('irreversible_action_detected', {
        toolName,
        riskLevel: assessment.riskLevel,
        reasons: assessment.riskReasons,
      });
    }

    return assessment.shouldConfirm;
  }

  generateQuestion(
    toolName: string,
    params: Record<string, unknown>,
    riskLevel: RiskAssessment['riskLevel']
  ): import('./types.js').PreActionQuestion {
    const questionId = `pre_action_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    const action = this.describeAction(toolName, params);

    let question: string;
    switch (riskLevel) {
      case 'critical':
        question = `⚠️ This action is potentially destructive: ${action}. Are you sure you want to proceed? This cannot be undone.`;
        break;
      case 'high':
        question = `⚡ High-risk action: ${action}. Please confirm this is intended.`;
        break;
      case 'medium':
        question = `This action will: ${action}. Do you want to proceed?`;
        break;
      default:
        question = `About to: ${action}. Continue?`;
    }

    const preActionQuestion: import('./types.js').PreActionQuestion = {
      id: questionId,
      toolName,
      action,
      question,
      isReversible: riskLevel === 'low' || riskLevel === 'medium',
      riskLevel,
      timestamp: new Date().toISOString(),
    };

    this.pendingQuestions.push(preActionQuestion);
    return preActionQuestion;
  }

  private parseLlmResponse(content: string): { riskLevel: string; riskReasons: string[]; shouldConfirm: boolean; confirmationQuestion: string | null } | null {
    try {
      const jsonMatch = content.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;
      return JSON.parse(jsonMatch[0]);
    } catch {
      return null;
    }
  }

  private describeAction(toolName: string, params: Record<string, unknown>): string {
    const parts: string[] = [toolName];
    if (params.path) parts.push(`on "${params.path}"`);
    if (params.force) parts.push('with force');
    if (params.recursive) parts.push('recursively');
    if (params.mode) parts.push(`with permissions ${params.mode}`);
    return parts.join(' ');
  }

  confirmAction(questionId: string): boolean {
    const index = this.pendingQuestions.findIndex(q => q.id === questionId);
    if (index === -1) return false;
    this.confirmedActions.add(questionId);
    this.pendingQuestions.splice(index, 1);
    return true;
  }

  cancelAction(questionId: string): boolean {
    const index = this.pendingQuestions.findIndex(q => q.id === questionId);
    if (index === -1) return false;
    this.pendingQuestions.splice(index, 1);
    return true;
  }

  isConfirmed(questionId: string): boolean {
    return this.confirmedActions.has(questionId);
  }

  getPendingQuestions(): import('./types.js').PreActionQuestion[] {
    return [...this.pendingQuestions];
  }

  hasPendingConfirmation(toolName: string): boolean {
    return this.pendingQuestions.some(q => q.toolName === toolName);
  }

  clearPending(): void {
    this.pendingQuestions = [];
  }

  clearConfirmed(): void {
    this.confirmedActions.clear();
  }

  private logBehavioralEvent(event: string, data: Record<string, unknown>): void {
    const timestamp = new Date().toISOString();
    console.log(`${timestamp} INFO [PreActionQuestioner] behavioral.clarification.${event} ${JSON.stringify(data)}`);
  }
}
