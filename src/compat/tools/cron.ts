/**
 * StackOwl — Cron Tool
 *
 * Provides scheduled task management similar to OpenCLAW.
 */

import type { ToolImplementation, ToolContext } from "../../tools/registry.js";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

interface CronJob {
  id: string;
  schedule: string;
  task: string;
  enabled: boolean;
  lastRun?: number;
  nextRun?: number;
}

const CRON_FILE = "cron-jobs.json";

export class CronTool implements ToolImplementation {
  private workspacePath: string;
  private jobs: Map<string, CronJob> = new Map();

  constructor(workspacePath: string) {
    this.workspacePath = workspacePath;
    this.loadJobs();
  }

  definition = {
    name: "cron",
    description: `Manage scheduled tasks and cron jobs.

Examples:
- cron action="list": List all scheduled jobs
- cron action="add" schedule="0 9 * * *" task="daily summary": Add a job
- cron action="remove" jobId="123": Remove a job
- cron action="run" jobId="123": Run a job immediately`,
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          description: "Action: list, add, remove, run, status",
        },
        schedule: {
          type: "string",
          description: 'Cron schedule (e.g., "0 9 * * *" for daily at 9am)',
        },
        task: {
          type: "string",
          description: "Task description or command",
        },
        jobId: {
          type: "string",
          description: "Job ID for remove/run actions",
        },
      },
      required: ["action"],
    },
  };

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = args["action"] as string;

    try {
      switch (action) {
        case "list":
          return this.listJobs();
        case "add":
          return this.addJob(
            args["schedule"] as string,
            args["task"] as string,
          );
        case "remove":
          return this.removeJob(args["jobId"] as string);
        case "run":
          return this.runJob(args["jobId"] as string);
        case "status":
          return JSON.stringify({ status: "active", jobs: this.jobs.size });
        default:
          return `ERROR: Unknown action "${action}". Use: list, add, remove, run, status`;
      }
    } catch (error) {
      return JSON.stringify({ error: String(error) });
    }
  }

  private async loadJobs(): Promise<void> {
    const cronPath = join(this.workspacePath, CRON_FILE);
    if (existsSync(cronPath)) {
      try {
        const content = await readFile(cronPath, "utf-8");
        const jobs = JSON.parse(content) as CronJob[];
        for (const job of jobs) {
          this.jobs.set(job.id, job);
        }
      } catch {
        // Start with empty jobs
      }
    }
  }

  private async saveJobs(): Promise<void> {
    const cronPath = join(this.workspacePath, CRON_FILE);
    const jobs = Array.from(this.jobs.values());
    await writeFile(cronPath, JSON.stringify(jobs, null, 2), "utf-8");
  }

  private listJobs(): string {
    const jobs = Array.from(this.jobs.values());
    if (jobs.length === 0) {
      return JSON.stringify({ jobs: [], message: "No cron jobs configured" });
    }
    return JSON.stringify({ jobs }, null, 2);
  }

  private addJob(schedule?: string, task?: string): string {
    if (!schedule || !task) {
      return "ERROR: schedule and task are required for add action";
    }

    const id = `job_${Date.now()}`;
    const job: CronJob = {
      id,
      schedule,
      task,
      enabled: true,
      lastRun: 0,
    };

    this.jobs.set(id, job);
    this.saveJobs().catch(console.error);

    return JSON.stringify({
      status: "added",
      job,
      message: `Job ${id} added with schedule: ${schedule}`,
    });
  }

  private removeJob(jobId?: string): string {
    if (!jobId) {
      return "ERROR: jobId is required for remove action";
    }

    if (!this.jobs.has(jobId)) {
      return JSON.stringify({ error: `Job ${jobId} not found` });
    }

    this.jobs.delete(jobId);
    this.saveJobs().catch(console.error);

    return JSON.stringify({ status: "removed", jobId });
  }

  private runJob(jobId?: string): string {
    if (!jobId) {
      return "ERROR: jobId is required for run action";
    }

    const job = this.jobs.get(jobId);
    if (!job) {
      return JSON.stringify({ error: `Job ${jobId} not found` });
    }

    job.lastRun = Date.now();
    this.saveJobs().catch(console.error);

    return JSON.stringify({
      status: "triggered",
      jobId,
      task: job.task,
      message: "Job triggered (execution would happen here)",
    });
  }
}
