// src/tools/schedule.ts
import { randomUUID } from "node:crypto";
import type { ToolImplementation, ToolContext } from "./registry.js";
import { log } from "../logger.js";

interface ScheduledJob {
  id: string;
  type: "remind" | "repeat";
  message: string;
  schedule: string;
  createdAt: string;
}

// TODO: jobs are not durable across restarts (JOB_STORE is a Map).
//       Migrate to SQLite-backed cron store when scheduling becomes a primary UX.
const JOB_STORE = new Map<string, { job: ScheduledJob; timer: ReturnType<typeof setTimeout> | ReturnType<typeof setInterval> }>();

function parseWhen(when: string): Date | null {
  const now = Date.now();
  const relMatch = when.match(/^in\s+(\d+(?:\.\d+)?)\s*(second|minute|hour|day)s?$/i);
  if (relMatch) {
    const n = parseFloat(relMatch[1]!);
    const unit = relMatch[2]!.toLowerCase();
    const multipliers: Record<string, number> = {
      second: 1_000, minute: 60_000, hour: 3_600_000, day: 86_400_000,
    };
    return new Date(now + n * multipliers[unit]!);
  }
  const d = new Date(when);
  if (!isNaN(d.getTime()) && d.getTime() > now) return d;
  return null;
}

export const ScheduleTool: ToolImplementation = {
  definition: {
    name: "schedule",
    description:
      "Schedule reminders and recurring tasks. Natural language times: \"in 5 minutes\", \"in 2 hours\". " +
      "NOTE: In-process job store only — jobs do not survive restarts and fire no external notification. " +
      'Example: schedule(action: "remind", when: "in 30 minutes", message: "Check deployment")',
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["remind", "repeat", "cancel", "list"],
          description: "What to do.",
        },
        when: {
          type: "string",
          description: "When to fire. For remind: \"in N minutes/hours/days\" or ISO 8601. For repeat: interval in ms.",
        },
        message: {
          type: "string",
          description: "Message to deliver when the job fires.",
        },
        id: {
          type: "string",
          description: "Job ID for cancel action.",
        },
      },
      required: ["action"],
    },
    capabilities: ["schedule", "reminder"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },

  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const action  = args["action"]  as string;
    const when    = args["when"]    as string | undefined;
    const message = args["message"] as string | undefined;
    const id      = args["id"]      as string | undefined;
    const onProgress = context.engineContext?.onProgress;
    log.tool.debug("schedule.execute: entry", { action, when, hasMessage: !!message, id });

    switch (action) {
      case "remind": {
        if (!when)    return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const fireAt = parseWhen(when);
        if (!fireAt) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: `Cannot parse: "${when}"` } });
        const delayMs = fireAt.getTime() - Date.now();
        const jobId   = randomUUID();
        const job: ScheduledJob = { id: jobId, type: "remind", message, schedule: fireAt.toISOString(), createdAt: new Date().toISOString() };
        const timer = setTimeout(async () => {
          JOB_STORE.delete(jobId);
          try {
            await onProgress?.(message);
          } catch (err) {
            log.tool.error("schedule.remind: delivery failed", err as Error, { jobId });
          }
        }, delayMs);
        JOB_STORE.set(jobId, { job, timer });
        if (!onProgress) {
          log.tool.warn("schedule.remind: no onProgress available — reminder will fire but cannot be delivered to user", { jobId });
        }
        log.tool.debug("schedule.execute: exit", { success: true, action, jobId, fireAt: fireAt.toISOString(), delayMs });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Reminder scheduled for ${fireAt.toISOString()}` } });
      }

      case "repeat": {
        if (!when)    return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when (interval ms) is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const intervalMs = parseInt(when, 10);
        if (isNaN(intervalMs) || intervalMs <= 0) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: "when for repeat must be positive ms" } });
        const jobId = randomUUID();
        const job: ScheduledJob = { id: jobId, type: "repeat", message, schedule: `every ${intervalMs}ms`, createdAt: new Date().toISOString() };
        const timer = setInterval(async () => {
          try {
            await onProgress?.(message);
          } catch (err) {
            log.tool.error("schedule.repeat: delivery failed", err as Error, { jobId });
          }
        }, intervalMs);
        JOB_STORE.set(jobId, { job, timer });
        log.tool.warn("schedule.repeat: registered (NOT durable across restarts)", { jobId, intervalMs, hasOnProgress: !!onProgress });
        log.tool.debug("schedule.execute: exit", { success: true, action, jobId, intervalMs });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Repeating job every ${intervalMs}ms` } });
      }

      case "cancel": {
        if (!id) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
        const entry = JOB_STORE.get(id);
        if (!entry) return JSON.stringify({ success: false, error: { code: "JOB_NOT_FOUND", message: `Job "${id}" not found` } });
        clearTimeout(entry.timer as ReturnType<typeof setTimeout>);
        clearInterval(entry.timer as ReturnType<typeof setInterval>);
        JOB_STORE.delete(id);
        log.tool.debug("schedule.execute: exit", { success: true, action, id });
        return JSON.stringify({ success: true, data: { id, message: "Job cancelled" } });
      }

      case "list": {
        const jobs = Array.from(JOB_STORE.values()).map(({ job }) => job);
        log.tool.debug("schedule.execute: exit", { success: true, action, jobCount: jobs.length });
        return JSON.stringify({ success: true, data: { jobs, count: jobs.length } });
      }

      default:
        return JSON.stringify({ success: false, error: { code: "INVALID_ACTION", message: `Unknown action: "${action}"` } });
    }
  },
};
