import { randomUUID } from "node:crypto";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ScheduleRunner } from "../schedule/runner.js";
import type { ScheduleStore } from "../schedule/store.js";
import { log } from "../logger.js";

let runnerRef: ScheduleRunner | null = null;
let storeRef: ScheduleStore | null = null;

/** Called from src/index.ts after the runner is created. */
export function attachSchedule(runner: ScheduleRunner, store: ScheduleStore): void {
  runnerRef = runner;
  storeRef = store;
}

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
      "Durable: jobs survive process restarts (SQLite-backed). " +
      'Example: schedule(action: "remind", when: "in 30 minutes", message: "Check deployment")',
    parameters: {
      type: "object",
      properties: {
        action: { type: "string", enum: ["remind", "repeat", "cancel", "list"], description: "What to do" },
        when: { type: "string", description: "For remind: \"in N minutes/hours/days\" or ISO 8601. For repeat: interval in ms." },
        message: { type: "string", description: "Message to deliver when the job fires" },
        id: { type: "string", description: "Job ID for cancel" },
      },
      required: ["action"],
    },
    capabilities: ["schedule", "reminder"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!runnerRef || !storeRef) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Schedule runner not yet initialized" } });
    }
    const action = args["action"] as string;
    const when = args["when"] as string | undefined;
    const message = args["message"] as string | undefined;
    const id = args["id"] as string | undefined;
    log.tool.debug("schedule.execute: entry", { action });

    switch (action) {
      case "remind": {
        if (!when) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const fireAt = parseWhen(when);
        if (!fireAt) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: `Cannot parse: "${when}"` } });
        const jobId = randomUUID();
        runnerRef.scheduleJob({
          id: jobId, type: "remind", message,
          scheduleAt: fireAt.toISOString(), nextFireAt: fireAt.toISOString(),
          createdAt: new Date().toISOString(), status: "active", metadata: {},
        });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Reminder scheduled for ${fireAt.toISOString()}` } });
      }
      case "repeat": {
        if (!when) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "when (interval ms) is required" } });
        if (!message) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "message is required" } });
        const intervalMs = parseInt(when, 10);
        if (isNaN(intervalMs) || intervalMs <= 0) return JSON.stringify({ success: false, error: { code: "INVALID_TIME", message: "when for repeat must be positive ms" } });
        const jobId = randomUUID();
        runnerRef.scheduleJob({
          id: jobId, type: "repeat", intervalMs, message,
          nextFireAt: new Date(Date.now() + intervalMs).toISOString(),
          createdAt: new Date().toISOString(), status: "active", metadata: {},
        });
        return JSON.stringify({ success: true, data: { id: jobId, message: `Repeating every ${intervalMs}ms` } });
      }
      case "cancel": {
        if (!id) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
        const ok = runnerRef.cancelJob(id);
        return ok
          ? JSON.stringify({ success: true, data: { id, message: "cancelled" } })
          : JSON.stringify({ success: false, error: { code: "JOB_NOT_FOUND", message: `Job "${id}" not found` } });
      }
      case "list": {
        const jobs = storeRef.list({ status: "active" });
        return JSON.stringify({ success: true, data: { jobs, count: jobs.length } });
      }
      default:
        return JSON.stringify({ success: false, error: { code: "INVALID_ACTION", message: `Unknown action: "${action}"` } });
    }
  },
};
