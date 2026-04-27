export type OutcomeStatus = 'success' | 'failure' | 'partial';

export interface OutcomeRecord {
  toolName: string;
  taskType: string;
  timestamp: string;
  status: OutcomeStatus;
  errorMessage?: string;
  metadata?: Record<string, unknown>;
}

export interface BatchState {
  counter: number;
  records: OutcomeRecord[];
  lastEvolutionTimestamp?: string;
  errorCount: number;
  successCount: number;
}

export interface EvolutionTrigger {
  type: 'batch_size' | 'error_threshold';
  batchState: BatchState;
  timestamp: string;
}

export interface BehavioralEvent {
  system: string;
  action: string;
  timestamp: string;
  details?: Record<string, unknown>;
}

export interface TrendAnalysis {
  patterns: string[];
  errorPatterns: string[];
  recommendations: string[];
  rawAnalysis?: string;
}
