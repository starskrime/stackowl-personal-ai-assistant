export type JobStatus = "active" | "fired" | "cancelled" | "expired";

export interface ScheduledJob {
  id: string;
  type: "remind" | "repeat";
  message: string;
  scheduleAt?: string;
  intervalMs?: number;
  nextFireAt: string;
  createdAt: string;
  status: JobStatus;
  metadata: {
    urgency?: "low" | "normal" | "critical";
    category?: string;
    channel?: string;
    userId?: string;
  };
}
