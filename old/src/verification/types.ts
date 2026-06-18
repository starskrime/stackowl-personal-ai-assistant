export type VerificationStatus = 'pending' | 'passed' | 'failed';

export interface VerificationResult {
  status: VerificationStatus;
  confidence: number;
  matchDetails?: string;
  checkedAt: string;
}

export interface IntentMatchResult {
  isMatch: boolean;
  confidence: number;
  reasoning?: string;
}

export interface VerificationRecord {
  taskId: string;
  intent: string;
  result: string;
  verification: VerificationResult;
}