export type AmbiguityType =
  | 'vague_pronoun'
  | 'incomplete_reference'
  | 'conflicting_constraints'
  | 'missing_context'
  | 'unspecified_scope'
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

// ─── Element 9: Intent Classification ───────────────────────────────

export type IntentVerdict =
  | 'PROCEED'        // clear actionable request — execute immediately
  | 'NARRATE'        // execute, but begin response with interpretation
  | 'CLARIFY'        // genuinely multi-path — ask one focused question first
  | 'USER_CONFUSED'; // user expressing their own uncertainty — help them

export interface IntentClassification {
  verdict: IntentVerdict;
  /** Populated only when verdict === 'CLARIFY'. Null otherwise. */
  question: string | null;
  /** Populated only when verdict === 'NARRATE'. Null otherwise. */
  interpretation: string | null;
  /** Always present — used for coordinator hash dedup */
  reasoning: string;
}

export interface ClarificationResult {
  needsClarification: boolean;
  ambiguitySignals: AmbiguitySignal[];
  question?: ClarificationQuestion;
  autoResolved?: boolean;
}
