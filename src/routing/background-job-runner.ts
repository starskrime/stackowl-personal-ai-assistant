// src/routing/background-job-runner.ts
import type { MemoryDatabase, OwlTask, OwlJob } from "../memory/db.js";
import type { EventBus } from "../events/bus.js";
import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";

const POLL_INTERVAL_MS = 60_000;

export class BackgroundJobRunner {
  private interval: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private db: Pick<MemoryDatabase, "owlJobs" | "owlTasks">,
    private eventBus: EventBus | null,
  ) {}

  start(): void {
    if (this.interval) return;
    this.interval = setInterval(() => { this.tick().catch(() => {}); }, POLL_INTERVAL_MS);
    this.interval.unref();
    log.engine.info("[BackgroundJobRunner] Started — polling every 60s");
  }

  stop(): void {
    if (this.interval) { clearInterval(this.interval); this.interval = null; }
  }

  async tick(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      const job = this.db.owlJobs.dequeueNext();
      if (!job) return;
      log.engine.info(`[BackgroundJobRunner] Executing job ${job.id} (${job.type})`);
      try {
        const result = await this.executeJob(job);
        this.db.owlJobs.markDone(job.id, result);
        if (job.taskId) {
          this.db.owlTasks.updateStatus(job.taskId, "done", result);
        }
        this.eventBus?.emit("job:complete", { userId: job.userId, jobId: job.id, type: job.type, result });
        log.engine.info(`[BackgroundJobRunner] Job ${job.id} done`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.db.owlJobs.markFailed(job.id, msg);
        log.engine.warn(`[BackgroundJobRunner] Job ${job.id} failed: ${msg}`);
      }
    } finally {
      this.running = false;
    }
  }

  scheduleFollowup(task: OwlTask, delayMs: number): void {
    // Ensure the task row exists before inserting the FK-constrained job row.
    if (!this.db.owlTasks.get(task.id)) {
      try {
        this.db.owlTasks.create({
          id: task.id,
          userId: task.userId,
          owlName: task.owlName,
          title: task.title,
          description: task.description,
          status: task.status,
          priority: task.priority,
          sessionId: task.sessionId,
          dueAt: task.dueAt,
        });
      } catch {
        // Row may have been inserted concurrently — safe to ignore.
      }
    }
    const scheduledAt = new Date(Date.now() + delayMs).toISOString();
    this.db.owlJobs.enqueue({
      id: uuidv4(),
      taskId: task.id,
      userId: task.userId,
      owlName: task.owlName,
      type: "followup",
      payload: { taskTitle: task.title, taskId: task.id },
      scheduledAt,
    });
  }

  private async executeJob(job: OwlJob): Promise<string> {
    switch (job.type) {
      case "followup": {
        const title = (job.payload as any).taskTitle ?? "task";
        return `Follow-up on: ${title} — no update available yet.`;
      }
      case "proactive":
        return `Proactive check completed at ${new Date().toISOString()}.`;
      case "research":
        throw new Error("Research jobs require a provider — not wired yet");
      case "monitor":
        return `Monitor check completed at ${new Date().toISOString()}.`;
      default:
        throw new Error(`Unknown job type: ${job.type}`);
    }
  }
}
