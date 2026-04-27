export type AmbiguityType =
  | 'vague_pronoun'
  | 'incomplete_reference'
  | 'conflicting_constraints'
  | 'missing_context'
  | 'underspecified_scope'
  | 'ambiguous_priority'
  | 'unclear_timeline';

export interface AmbiguitySignal {
  type: AmbiguityType;
  description: string;
  confidence: number;
  originalText: string;
  suggestedClarifications?: string[];
}

export interface ClarificationQuestion {
  id: string;
  ambiguitySignal: AmbiguitySignal;
  question: string;
  options?: string[];
  contextPreserved: string[];
  timestamp: string;
}

export interface ClarificationResponse {
  questionId: string;
  answer: string;
  timestamp: string;
  contextUpdated: string[];
}

export interface ClarificationState {
  pendingQuestions: ClarificationQuestion[];
  resolvedQuestions: ClarificationResponse[];
  lastClarificationAt?: string;
  clarificationCount: number;
}

export interface ExecutionCheckpoint {
  id: string;
  toolCalls: Array<{
    toolName: string;
    params: Record<string, unknown>;
    result?: unknown;
    completed: boolean;
  }>;
  contextSnapshot: string[];
  timestamp: string;
}

export interface MidExecutionState {
  isPaused: boolean;
  checkpoint: ExecutionCheckpoint | null;
  pauseReason: string;
  pendingQuestion: ClarificationQuestion | null;
}

export interface UnclarityItem {
  id: string;
  description: string;
  sourceMessage: string;
  detectedAt: string;
  addressed: boolean;
  addressedAt?: string;
}

export interface PreActionQuestion {
  id: string;
  toolName: string;
  action: string;
  question: string;
  isReversible: boolean;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  timestamp: string;
}

export interface PreExecutionConfirmation {
  id: string;
  summary: string;
  uncertaintyAreas: string[];
  confidence: number;
  isHighStakes: boolean;
  confirmed: boolean | null;
  timestamp: string;
}

export interface ClarificationResult {
  needsClarification: boolean;
  ambiguitySignals: AmbiguitySignal[];
  question?: ClarificationQuestion;
  autoResolved?: boolean;
}
