import { log } from "../logger.js";
import type { ScheduleStore } from "./store.js";
import type { ScheduledJob } from "./types.js";
import type { Notifier } from "../platform/index.js";

const EXPIRED_THRESHOLD_MS = 5 * 60 * 1000;

export class ScheduleRunner {
  private timers = new Map<string, NodeJS.Timeout>();

  constructor(
    private readonly store: ScheduleStore,
    private readonly notifier: Notifier,
  ) {}

  async start(): Promise<void> {
    log.engine.info("[ScheduleRunner] starting — hydrating active jobs");
    const active = this.store.list({ status: "active" });
    const now = Date.now();
    let hydrated = 0, expired = 0;

    for (const job of active) {
      const fireAt = Date.parse(job.nextFireAt);
      if (Number.isNaN(fireAt)) {
        this.store.update(job.id, { status: "expired" });
        continue;
      }
      if (fireAt < now - EXPIRED_THRESHOLD_MS) {
        await this.fireMissed(job);
        expired++;
      } else {
        this.scheduleJob(job);
        hydrated++;
      }
    }
    log.engine.info("[ScheduleRunner] start complete", { hydrated, expired });
  }

  stop(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
    log.engine.info("[ScheduleRunner] stopped");
  }

  scheduleJob(job: ScheduledJob): void {
    this.store.add(job);
    const fireAt = Date.parse(job.nextFireAt);
    const delay = Math.max(0, fireAt - Date.now());
    const timer = setTimeout(() => this.fire(job), delay);
    this.timers.set(job.id, timer);
    log.engine.debug("[ScheduleRunner] job scheduled", { id: job.id, delay });
  }

  cancelJob(id: string): boolean {
    const timer = this.timers.get(id);
    if (timer) clearTimeout(timer);
    this.timers.delete(id);
    const existed = !!this.store.findOne(id);
    if (existed) this.store.update(id, { status: "cancelled" });
    return existed;
  }

  private async fire(job: ScheduledJob): Promise<void> {
    this.timers.delete(job.id);
    try {
      const result = await this.notifier.notify({
        title: job.metadata.category ?? "Reminder",
        body: job.message,
        urgency: job.metadata.urgency ?? "normal",
        category: job.metadata.category ?? "schedule",
      });
      log.engine.info("[ScheduleRunner] fired", { id: job.id, via: result.via });
    } catch (err) {
      log.engine.error("[ScheduleRunner] notify failed", err as Error, { id: job.id });
    }

    if (job.type === "repeat" && job.intervalMs) {
      const nextFireAt = new Date(Date.now() + job.intervalMs).toISOString();
      const updated: ScheduledJob = { ...job, nextFireAt };
      this.store.update(job.id, { nextFireAt });
      const timer = setTimeout(() => this.fire(updated), job.intervalMs);
      this.timers.set(job.id, timer);
    } else {
      this.store.update(job.id, { status: "fired" });
    }
  }

  private async fireMissed(job: ScheduledJob): Promise<void> {
    try {
      await this.notifier.notify({
        title: job.metadata.category ?? "Missed Reminder",
        body: `Missed: ${job.message}`,
        urgency: "normal",
        category: "schedule-missed",
      });
    } catch (err) {
      log.engine.error("[ScheduleRunner] missed-notify failed", err as Error, { id: job.id });
    }
    this.store.update(job.id, { status: "expired" });
  }
}
