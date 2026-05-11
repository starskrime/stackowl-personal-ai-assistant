import { Cron } from "croner";
import { writeFileSync, readFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";
import type { CronJob, CronJobState, CronRun } from "./types.js";

const DEFAULT_PERSIST_PATH = join(homedir(), ".stackowl", "crons.json");

export interface CronServiceOptions {
  persist?: boolean;
  persistPath?: string;
  maxConcurrentRuns?: number;
  onJobFire?: (job: CronJob, traceId: string) => Promise<string>;
}

function validateCronExpression(schedule: string): boolean {
  try {
    const c = new Cron(schedule);
    c.stop();
    return true;
  } catch {
    return false;
  }
}

export class CronService {
  private jobs = new Map<string, CronJob>();
  private states = new Map<string, CronJobState>();
  private cronInstances = new Map<string, Cron>();
  private runningCount = 0;
  private options: Required<Omit<CronServiceOptions, "onJobFire">> & {
    onJobFire?: CronServiceOptions["onJobFire"];
  };

  constructor(options: CronServiceOptions = {}) {
    this.options = {
      persist: options.persist ?? true,
      persistPath: options.persistPath ?? DEFAULT_PERSIST_PATH,
      maxConcurrentRuns: options.maxConcurrentRuns ?? 3,
      onJobFire: options.onJobFire,
    };

    if (this.options.persist) {
      this.loadFromDisk();
    }
  }

  private canStartJob(): boolean {
    return this.runningCount < this.options.maxConcurrentRuns;
  }

  addJob(job: CronJob): void {
    if (this.jobs.has(job.id)) {
      throw new Error(`Job "${job.id}" is already registered.`);
    }
    if (!validateCronExpression(job.schedule)) {
      throw new Error(
        `Invalid schedule expression "${job.schedule}" for job "${job.id}".`,
      );
    }

    this.jobs.set(job.id, job);

    const cronInstance = new Cron(job.schedule, async () => {
      await this.fireJob(job);
    });

    this.cronInstances.set(job.id, cronInstance);

    const nextRun = cronInstance.nextRun();
    this.states.set(job.id, {
      id: job.id,
      status: "pending",
      lastRunAt: null,
      nextRunAt: nextRun ?? null,
      failCount: 0,
    });

    log.engine.info("[CronService] Job registered", {
      id: job.id,
      schedule: job.schedule,
    });

    if (this.options.persist) {
      this.saveToDisk();
    }
  }

  removeJob(id: string): void {
    const cron = this.cronInstances.get(id);
    cron?.stop();
    this.cronInstances.delete(id);
    this.jobs.delete(id);
    this.states.delete(id);
    if (this.options.persist) {
      this.saveToDisk();
    }
    log.engine.info("[CronService] Job removed", { id });
  }

  listJobs(): CronJob[] {
    return Array.from(this.jobs.values());
  }

  getJobState(id: string): CronJobState | undefined {
    return this.states.get(id);
  }

  private async fireJob(job: CronJob): Promise<void> {
    const state = this.states.get(job.id);
    if (!state) return;

    if (!this.canStartJob()) {
      log.engine.warn("[CronService] Max concurrent runs reached, skipping", {
        id: job.id,
      });
      return;
    }

    const traceId = crypto.randomUUID();
    this.runningCount++;
    state.status = "running";
    state.lastRunAt = new Date();

    try {
      if (this.options.onJobFire) {
        const result = await this.options.onJobFire(job, traceId);
        state.status = "completed";
        state.lastResult = result;
      } else {
        state.status = "completed";
      }
    } catch (err) {
      log.engine.error(
        "[CronService] Job failed",
        err as Error,
        { id: job.id, traceId },
      );
      state.status = "failed";
      state.failCount++;
    } finally {
      this.runningCount--;
      const nextRun = this.cronInstances.get(job.id)?.nextRun();
      state.nextRunAt = nextRun ?? null;
      if (this.options.persist) {
        this.saveToDisk();
      }
    }
  }

  stop(): void {
    for (const cron of this.cronInstances.values()) {
      cron.stop();
    }
    this.cronInstances.clear();
    log.engine.info("[CronService] Stopped all jobs");
  }

  private saveToDisk(): void {
    try {
      const dir = join(homedir(), ".stackowl");
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }
      const data = { jobs: Array.from(this.jobs.values()), updatedAt: Date.now() };
      writeFileSync(this.options.persistPath, JSON.stringify(data, null, 2), "utf-8");
    } catch (err) {
      log.engine.error(
        "[CronService] Failed to save to disk",
        err as Error,
      );
    }
  }

  private loadFromDisk(): void {
    if (!existsSync(this.options.persistPath)) return;
    try {
      const raw = readFileSync(this.options.persistPath, "utf-8");
      const data = JSON.parse(raw) as { jobs: CronJob[] };
      if (Array.isArray(data.jobs)) {
        for (const job of data.jobs) {
          try {
            this.addJob(job);
          } catch (err) {
            log.engine.warn("[CronService] Skipped job on load", {
              id: job.id,
              err: String(err),
            });
          }
        }
        log.engine.info(`[CronService] Loaded ${data.jobs.length} jobs from disk`);
      }
    } catch (err) {
      log.engine.error(
        "[CronService] Failed to load from disk",
        err as Error,
      );
    }
  }
}
