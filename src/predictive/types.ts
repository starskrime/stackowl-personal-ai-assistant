export type DayOfWeek = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday';
export type TimeSlot = 'early_morning' | 'morning' | 'afternoon' | 'evening' | 'night';

export interface UserPattern {
  id: string;
  action: string;
  frequency: number;
  dayPreference: DayOfWeek[];
  timePreference: TimeSlot[];
  lastOccurred: string;
  avgIntervalHours: number;
  confidence: number;
  relatedSkills: string[];
}

export interface PredictedTask {
  id: string;
  action: string;
  predictedTime: string;
  confidence: number;
  source: string;
  status: 'queued' | 'preparing' | 'ready' | 'presented' | 'accepted' | 'dismissed';
  preparedContent?: string;
  relatedSkills: string[];
}

export interface PredictiveConfig {
  minPatternFrequency: number;
  predictionHorizonHours: number;
  maxQueuedTasks: number;
  minConfidence: number;
}
