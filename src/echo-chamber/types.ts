/**
 * StackOwl — Echo Chamber Detector Types
 */

export type CognitiveBias =
  | 'confirmation_bias'
  | 'sunk_cost'
  | 'recency_bias'
  | 'anchoring'
  | 'status_quo'
  | 'availability_heuristic'
  | 'bandwagon'
  | 'optimism_bias'
  | 'dunning_kruger';

export type ChallengeIntensity = 'gentle' | 'balanced' | 'relentless';

export interface BiasDetection {
  bias: CognitiveBias;
  evidence: string;
  confidence: number;
  suggestedChallenge: string;
  sessionIds: string[];
}

export interface EchoChamberAnalysis {
  detections: BiasDetection[];
  overallAssessment: string;
  analyzedAt: string;
  sessionCount: number;
}

export interface EchoChamberConfig {
  enabled: boolean;
  intensity: ChallengeIntensity;
  proactiveAnalysis: boolean;
  analysisIntervalHours: number;
  minSessionsForAnalysis: number;
}
