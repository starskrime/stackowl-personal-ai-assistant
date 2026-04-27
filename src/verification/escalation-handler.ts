import type { ModelProvider } from '../providers/base.js';
import type { VerificationResult } from './types.js';

export interface ConfirmationIntent {
  intent: 'confirm' | 'reject' | 'unclear';
  confidence: number;
  reasoning: string;
}

export interface EscalationMessage {
  message: string;
  context: {
    taskId: string;
    intent: string;
    result: string;
  };
  verificationResult: VerificationResult;
  timestamp: string;
}

export interface EscalationRecord {
  taskId: string;
  escalation: EscalationMessage;
  userResponse?: string;
  userConfirmed?: boolean;
  respondedAt?: string;
}

export interface EscalationHandlerConfig {
  minConfidenceForEscalation?: number;
}

const DEFAULT_CONFIG: EscalationHandlerConfig = {
  minConfidenceForEscalation: 0.5,
};

export class EscalationHandler {
  private config: EscalationHandlerConfig;
  private escalationHistory: Map<string, EscalationRecord> = new Map();
  private pendingEscalations: Map<string, EscalationMessage> = new Map();
  private modelProvider: ModelProvider;

  constructor(modelProvider: ModelProvider, config: Partial<EscalationHandlerConfig> = {}) {
    this.modelProvider = modelProvider;
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  createEscalationMessage(
    taskId: string,
    intent: string,
    result: string,
    verificationResult: VerificationResult,
  ): EscalationMessage {
    const escalation: EscalationMessage = {
      message: this.buildEscalationText(verificationResult),
      context: { taskId, intent, result },
      verificationResult,
      timestamp: new Date().toISOString(),
    };

    this.pendingEscalations.set(taskId, escalation);
    this.escalationHistory.set(taskId, {
      taskId,
      escalation,
    });

    this.logBehavioral('behavioral.escalation.triggered', taskId);
    return escalation;
  }

  private buildEscalationText(verificationResult: VerificationResult): string {
    if (verificationResult.status === 'failed') {
      return `I wasn't able to fully verify that the result achieved what you needed. ${verificationResult.matchDetails ?? ''}\n\nDid this accomplish what you were looking for?`;
    }
    return `I completed this task but want to make sure it meets your needs. Did this achieve what you needed?`;
  }

  shouldEscalate(confidence: number): boolean {
    return confidence < (this.config.minConfidenceForEscalation ?? 0.5);
  }

  triggerEscalation(taskId: string): EscalationMessage | undefined {
    return this.pendingEscalations.get(taskId);
  }

  async handleUserResponse(taskId: string, response: string, context?: string): Promise<void> {
    const record = this.escalationHistory.get(taskId);
    if (!record) return;

    const parsed = await this.parseUserConfirmation(response, context);
    record.userResponse = response;
    record.userConfirmed = parsed.intent === 'confirm';
    record.respondedAt = new Date().toISOString();

    if (parsed.intent === 'confirm') {
      this.logBehavioral('behavioral.escalation.user_confirmed', taskId);
    } else if (parsed.intent === 'reject') {
      this.logBehavioral('behavioral.escalation.user_rejected', taskId);
    } else {
      this.logBehavioral('behavioral.escalation.user_unclear', taskId);
    }

    this.pendingEscalations.delete(taskId);
  }

  async parseUserConfirmation(response: string, context?: string): Promise<ConfirmationIntent> {
    const prompt = `Parse this user response to a confirmation question.

User said: "${response}"
Confirmation question was: "${context ?? 'Did this accomplish what you were looking for?'}"

Determine if the user:
1. Said yes/confirmed (agreed to proceed)
2. Said no/rejected (did not agree)
3. Is unclear/neutral

Respond with JSON:
{
  "intent": "confirm|reject|unclear",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}`;

    const result = await this.modelProvider.chat([
      { role: 'user', content: prompt }
    ]);

    const content = result.content;
    try {
      const parsed = JSON.parse(content) as ConfirmationIntent;
      if (!['confirm', 'reject', 'unclear'].includes(parsed.intent)) {
        return { intent: 'unclear', confidence: 0, reasoning: 'Invalid intent from LLM' };
      }
      return parsed;
    } catch {
      return { intent: 'unclear', confidence: 0, reasoning: 'Failed to parse LLM response' };
    }
  }

  getEscalationRecord(taskId: string): EscalationRecord | undefined {
    return this.escalationHistory.get(taskId);
  }

  getPendingEscalation(taskId: string): EscalationMessage | undefined {
    return this.pendingEscalations.get(taskId);
  }

  getAllEscalations(): Map<string, EscalationRecord> {
    return new Map(this.escalationHistory);
  }

  private logBehavioral(event: 'behavioral.escalation.triggered' | 'behavioral.escalation.user_confirmed' | 'behavioral.escalation.user_rejected' | 'behavioral.escalation.user_unclear', taskId: string): void {
    console.log(
      `${new Date().toISOString()} INFO [EscalationHandler] ${event} taskId=${taskId}`,
    );
  }
}

export const escalationHandler: EscalationHandler | null = null;