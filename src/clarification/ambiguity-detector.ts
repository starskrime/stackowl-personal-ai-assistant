import type { AmbiguitySignal, AmbiguityType, ClarificationQuestion, ClarificationResult, ClarificationState } from './types.js';
import type { ModelProvider } from '../providers/base.js';

const AMBIGUITY_THRESHOLD = 0.75;

const AMBIGUITY_ANALYSIS_PROMPT = `Evaluate whether this message genuinely needs clarification or whether you can proceed with reasonable interpretation.

Message: "{message}"

Only flag as ambiguous if:
- The core intent is genuinely unclear (not just brief or informal)
- Proceeding would likely do the wrong thing
- The ambiguity cannot be resolved from context

Respond with JSON:
{
  "isAmbiguous": boolean,
  "ambiguityTypes": string[],
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}`;

export class AmbiguityDetector {
  private modelProvider: ModelProvider;
  private state: ClarificationState = {
    pendingQuestions: [],
    resolvedQuestions: [],
    clarificationCount: 0,
  };

  constructor(modelProvider: ModelProvider) {
    this.modelProvider = modelProvider;
  }

  async detectAmbiguity(message: string, context: string[] = []): Promise<ClarificationResult> {
    if (!message.trim()) {
      return { needsClarification: false, ambiguitySignals: [] };
    }

    const recentlyResolved = this.state.resolvedQuestions.find(rq =>
      rq.contextUpdated.some(ctx => message.toLowerCase().includes(ctx.toLowerCase().slice(0, 50)))
    );
    if (recentlyResolved) {
      this.logBehavioralEvent('ambiguity_auto_resolved', { reason: 'previously_answered' });
      return { needsClarification: false, ambiguitySignals: [], autoResolved: true };
    }

    try {
      const response = await this.modelProvider.chat(
        [
          {
            role: 'user',
            content: AMBIGUITY_ANALYSIS_PROMPT.replace('{message}', message),
          },
        ],
        undefined,
        { temperature: 0.1 }
      );

      const parsed = this.parseLlmResponse(response.content);
      if (!parsed) {
        return { needsClarification: false, ambiguitySignals: [] };
      }

      const signals: AmbiguitySignal[] = parsed.ambiguityTypes.map((type) => ({
        type: type as AmbiguityType,
        description: this.getDescriptionForType(type),
        confidence: parsed.confidence,
        originalText: message,
      }));

      const needsClarification = parsed.isAmbiguous && parsed.confidence >= AMBIGUITY_THRESHOLD;

      if (needsClarification && signals.length > 0) {
        const primarySignal = signals.reduce((a, b) => (a.confidence > b.confidence ? a : b));
        const question = this.formClarificationQuestion(primarySignal, signals, context, null);

        this.logBehavioralEvent('ambiguity_detected', {
          type: primarySignal.type,
          confidence: parsed.confidence,
          questionId: question.id,
        });

        return {
          needsClarification: true,
          ambiguitySignals: signals,
          question,
        };
      }

      return {
        needsClarification: false,
        ambiguitySignals: signals,
      };
    } catch {
      return { needsClarification: false, ambiguitySignals: [] };
    }
  }

  private parseLlmResponse(content: string): { isAmbiguous: boolean; ambiguityTypes: string[]; confidence: number; reasoning: string } | null {
    try {
      const jsonMatch = content.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;
      return JSON.parse(jsonMatch[0]);
    } catch {
      return null;
    }
  }

  private getDescriptionForType(type: string): string {
    const descriptions: Record<string, string> = {
      vague_pronoun: 'Vague pronoun without clear referent',
      incomplete_reference: 'Incomplete reference lacks specificity',
      conflicting_constraints: 'Conflicting constraints detected',
      unspecified_scope: 'Underspecified scope does not define extent',
      ambiguous_priority: 'Ambiguous priority is unclear',
      unclear_timeline: 'Unclear timeline does not specify when',
    };
    return descriptions[type] || `${type} detected`;
  }

  private formClarificationQuestion(
    primarySignal: AmbiguitySignal,
    _allSignals: AmbiguitySignal[],
    context: string[],
    llmQuestion: string | null
  ): ClarificationQuestion {
    const baseQuestion = llmQuestion || primarySignal.suggestedClarifications?.[0] || 'Could you clarify?';
    const questionText = baseQuestion;

    return {
      id: `clarify_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      ambiguitySignal: primarySignal,
      question: questionText,
      options: primarySignal.suggestedClarifications?.length
        ? primarySignal.suggestedClarifications
        : ['Yes, please proceed', 'No, let me clarify'],
      contextPreserved: context,
      timestamp: new Date().toISOString(),
    };
  }

  private logBehavioralEvent(event: string, data: Record<string, unknown>): void {
    const timestamp = new Date().toISOString();
    console.log(`${timestamp} INFO [AmbiguityDetector] behavioral.clarification.${event} ${JSON.stringify(data)}`);
  }

  getState(): ClarificationState {
    return { ...this.state };
  }

  addPendingQuestion(question: ClarificationQuestion): void {
    this.state.pendingQuestions.push(question);
  }

  resolveQuestion(questionId: string, response: string): string[] {
    const question = this.state.pendingQuestions.find(q => q.id === questionId);
    if (!question) return [];

    const contextUpdated = [question.ambiguitySignal.originalText];

    this.state.resolvedQuestions.push({
      questionId,
      answer: response,
      timestamp: new Date().toISOString(),
      contextUpdated,
    });

    this.state.pendingQuestions = this.state.pendingQuestions.filter(q => q.id !== questionId);
    this.state.lastClarificationAt = new Date().toISOString();
    this.state.clarificationCount++;

    return contextUpdated;
  }

  clear(): void {
    this.state = {
      pendingQuestions: [],
      resolvedQuestions: [],
      clarificationCount: 0,
    };
  }
}