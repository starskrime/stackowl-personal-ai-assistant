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
  /**
   * Optional direct callback. When present, CronService invokes this
   * instead of the LLM prompt — for deterministic operations that
   * don't need model reasoning (e.g., fact promotion, dedup).
   * Not persisted to disk (callbacks can't be serialized); jobs that
   * use this must be re-attached in code on startup.
   */
  handler?: (traceId: string) => Promise<string>;
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
