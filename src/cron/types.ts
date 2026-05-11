export type SafetyProfile = "low" | "medium" | "full";

export type JobStatus = "pending" | "running" | "completed" | "failed";

export interface CronJob {
  id: string;
  schedule: string;
  prompt: string;
  safetyProfile: SafetyProfile;
  deliver?: boolean;
  deliveryTarget?: { channel: string; userId: string };
  description?: string;
}

export interface CronJobState {
  id: string;
  status: JobStatus;
  lastRunAt: Date | null;
  nextRunAt: Date | null;
  lastResult?: string;
  failCount: number;
}

export interface CronRun {
  jobId: string;
  startedAt: number;
  completedAt?: number;
  status: JobStatus;
  result?: string;
  error?: string;
  traceId: string;
}
