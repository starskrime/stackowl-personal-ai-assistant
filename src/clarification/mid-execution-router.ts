import type {
  ClarificationQuestion,
  ExecutionCheckpoint,
  MidExecutionState,
} from './types.js';
import type { AmbiguityDetector } from './ambiguity-detector.js';

export class MidExecutionRouter {
  private state: MidExecutionState = {
    isPaused: false,
    checkpoint: null,
    pauseReason: '',
    pendingQuestion: null,
  };

  private toolCallHistory: Array<{
    toolName: string;
    params: Record<string, unknown>;
    result?: unknown;
    completed: boolean;
    timestamp: string;
  }> = [];

  constructor(private ambiguityDetector: AmbiguityDetector) {}

  async shouldPauseForClarification(
    toolResult: unknown,
    context: string[],
    errorMessage?: string
  ): Promise<boolean> {
    if (errorMessage) {
      const ambiguityResult = await this.ambiguityDetector.detectAmbiguity(errorMessage, context);
      if (ambiguityResult.needsClarification) {
        return true;
      }
    }

    if (this.isResultAmbiguous(toolResult)) {
      return true;
    }

    if (this.detectMissingPrerequisites(context)) {
      return true;
    }

    return false;
  }

  private isResultAmbiguous(result: unknown): boolean {
    if (result === null || result === undefined) return true;
    if (typeof result === 'string' && result.includes('[UNCERTAIN]')) return true;
    if (Array.isArray(result) && result.length === 0) return true;
    if (typeof result === 'object') {
      const obj = result as Record<string, unknown>;
      if (obj.uncertain === true || obj.confidence === 'low') return true;
    }
    return false;
  }

  private detectMissingPrerequisites(context: string[]): boolean {
    const prerequisitePatterns = [
      /need[s]?\s+(?:to\s+)?(?:know|have|get|find)/i,
      /before\s+(?:I|we)\s+can/i,
      /first\s+(?:I|we)\s+need/i,
      /missing\s+(?:info|information|data)/i,
    ];

    const contextText = context.join(' ');
    return prerequisitePatterns.some(pattern => pattern.test(contextText));
  }

  async pauseExecution(
    reason: string,
    context: string[]
  ): Promise<{ checkpoint: ExecutionCheckpoint; question: ClarificationQuestion } | null> {
    if (this.state.isPaused) {
      return null;
    }

    const checkpoint = this.createCheckpoint(context);

    const ambiguityResult = await this.ambiguityDetector.detectAmbiguity(reason, context);
    let question: ClarificationQuestion;

    if (ambiguityResult.question) {
      question = ambiguityResult.question;
    } else {
      question = {
        id: `mid_exec_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
        ambiguitySignal: {
          type: 'missing_context',
          description: reason,
          confidence: 0.8,
          originalText: reason,
        },
        question: `I'm unclear about something: ${reason}`,
        options: ['Here is the clarification', 'Cancel this operation'],
        contextPreserved: context,
        timestamp: new Date().toISOString(),
      };
    }

    this.state = {
      isPaused: true,
      checkpoint,
      pauseReason: reason,
      pendingQuestion: question,
    };

    this.ambiguityDetector.addPendingQuestion(question);

    return { checkpoint, question };
  }

  private createCheckpoint(context: string[]): ExecutionCheckpoint {
    return {
      id: `checkpoint_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      toolCalls: this.toolCallHistory.map(tc => ({
        toolName: tc.toolName,
        params: tc.params,
        result: tc.result,
        completed: tc.completed,
      })),
      contextSnapshot: context,
      timestamp: new Date().toISOString(),
    };
  }

  recordToolCall(
    toolName: string,
    params: Record<string, unknown>,
    completed: boolean,
    result?: unknown
  ): void {
    this.toolCallHistory.push({
      toolName,
      params,
      result,
      completed,
      timestamp: new Date().toISOString(),
    });
  }

  resumeExecution(clarification: string): {
    resumed: boolean;
    contextUpdates: string[];
    retainedResults: Array<{ toolName: string; result: unknown }>;
  } {
    if (!this.state.isPaused || !this.state.pendingQuestion) {
      return { resumed: false, contextUpdates: [], retainedResults: [] };
    }

    const contextUpdates = this.ambiguityDetector.resolveQuestion(
      this.state.pendingQuestion.id,
      clarification
    );

    const retainedResults = this.state.checkpoint?.toolCalls
      .filter(tc => tc.completed && tc.result !== undefined)
      .map(tc => ({ toolName: tc.toolName, result: tc.result })) ?? [];

    this.state = {
      isPaused: false,
      checkpoint: null,
      pauseReason: '',
      pendingQuestion: null,
    };

    return { resumed: true, contextUpdates, retainedResults };
  }

  cancelExecution(): void {
    this.state = {
      isPaused: false,
      checkpoint: null,
      pauseReason: '',
      pendingQuestion: null,
    };
    this.toolCallHistory = [];
  }

  getState(): MidExecutionState {
    return { ...this.state };
  }

  getToolHistory(): Array<{
    toolName: string;
    params: Record<string, unknown>;
    completed: boolean;
  }> {
    return this.toolCallHistory.map(tc => ({
      toolName: tc.toolName,
      params: tc.params,
      completed: tc.completed,
    }));
  }

  isPaused(): boolean {
    return this.state.isPaused;
  }

  getPendingQuestion(): ClarificationQuestion | null {
    return this.state.pendingQuestion;
  }
}
