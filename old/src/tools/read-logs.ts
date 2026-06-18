/**
 * StackOwl — read_logs tool
 *
 * Lets the AI investigate its own structured JSONL logs to diagnose failures,
 * trace slow operations, or review what happened in a specific session/trace.
 */

import { join } from "node:path";
import { readLogsArray } from "../infra/observability/reader.js";
import type { LogQuery } from "../infra/observability/reader.js";
import type { ToolImplementation, ToolContext } from "./registry.js";
import type { ToolDefinition } from "../providers/base.js";
import { log } from "../logger.js";

const HARD_CAP = 1000;
const DEFAULT_SINCE_MINUTES = 60;
const DEFAULT_LIMIT = 100;

export class ReadLogsTool implements ToolImplementation {
  definition: ToolDefinition = {
    name: "read_logs",
    description:
      "Read StackOwl's structured logs to investigate failures, repeated errors, slow operations, " +
      "or what happened in a specific session/trace. Use when the user asks 'why did that fail', " +
      "'what went wrong', or when investigating your own past behavior.",
    parameters: {
      type: "object",
      properties: {
        traceId: {
          type: "string",
          description: "Filter by W3C trace ID (32-hex). Matches a single request end-to-end.",
        },
        sessionId: {
          type: "string",
          description: "Filter by session ID in the format <channelId>:<userId>.",
        },
        userId: {
          type: "string",
          description: "Filter by user ID.",
        },
        module: {
          type: "string",
          description:
            "Filter by module name or prefix, e.g. \"engine\", \"gateway\", \"tool.read_logs\". " +
            "Prefix matching is supported — \"engine\" matches \"engine.runtime\".",
        },
        level: {
          type: "string",
          enum: ["debug", "info", "warn", "error", "fatal"],
          description: "Minimum log level to include. Records below this level are excluded.",
        },
        sinceMinutes: {
          type: "number",
          description:
            "Return records from the last N minutes (default 60). Ignored if traceId is provided " +
            "and no since/until window is needed.",
        },
        until: {
          type: "string",
          description: "ISO-8601 upper-bound timestamp (inclusive). E.g. '2026-05-10T12:00:00Z'.",
        },
        contains: {
          type: "string",
          description: "Case-insensitive substring match on the log message (msg field).",
        },
        errorOnly: {
          type: "boolean",
          description: "Convenience shortcut: return only error and fatal records.",
        },
        limit: {
          type: "number",
          description: `Maximum number of records to return (default ${DEFAULT_LIMIT}, hard cap ${HARD_CAP}).`,
        },
      },
      required: [],
    },
    capabilities: ["log_read", "observability"],
    executionPolicy: { timeoutMs: 15_000, maxRetries: 0 },
  };

  category = "cognitive" as const;
  source = "builtin";

  constructor(private readonly workspacePath: string) {}

  async execute(
    args: Record<string, unknown>,
    _ctx: ToolContext,
  ): Promise<string> {
    const logsDir = join(this.workspacePath, "logs");
    log.tool.debug("read-logs.execute: entry", {
      traceId: args["traceId"],
      sessionId: args["sessionId"],
      module: args["module"],
      level: args["level"],
      sinceMinutes: args["sinceMinutes"],
      errorOnly: args["errorOnly"],
      contains: args["contains"],
      limit: args["limit"],
    });

    // Build the time window — default to last sinceMinutes unless explicit bounds given
    const sinceMinutes =
      typeof args["sinceMinutes"] === "number"
        ? (args["sinceMinutes"] as number)
        : DEFAULT_SINCE_MINUTES;

    const sinceDate =
      args["traceId"] || args["sinceMinutes"] !== undefined || args["until"] !== undefined
        ? // If a traceId narrows scope or explicit bounds are set, only apply the default window
          // when sinceMinutes was explicitly passed.
          args["sinceMinutes"] !== undefined
            ? new Date(Date.now() - sinceMinutes * 60_000)
            : undefined
        : new Date(Date.now() - sinceMinutes * 60_000);

    const untilDate = args["until"]
      ? new Date(args["until"] as string)
      : undefined;

    const rawLimit =
      typeof args["limit"] === "number" ? (args["limit"] as number) : DEFAULT_LIMIT;
    const limit = Math.min(rawLimit, HARD_CAP);

    const query: LogQuery = {
      traceId:   args["traceId"]   as string | undefined,
      sessionId: args["sessionId"] as string | undefined,
      userId:    args["userId"]    as string | undefined,
      module:    args["module"]    as string | undefined,
      level:     args["level"]     as LogQuery["level"] | undefined,
      errorOnly: args["errorOnly"] as boolean | undefined,
      contains:  args["contains"]  as string | undefined,
      since:     sinceDate,
      until:     untilDate,
      limit,
    };

    let records;
    try {
      log.tool.debug("read-logs.execute: reading log files", { logsDir });
      records = await readLogsArray(logsDir, query);
      log.tool.debug("read-logs.execute: step files read", { recordCount: records.length, limit });
    } catch (err) {
      log.tool.error("read-logs.execute: failed", err, { logsDir });
      const msg = err instanceof Error ? err.message : String(err);
      return JSON.stringify({
        success: false,
        error: { code: "READ_FAILED", message: `Failed to read logs from ${logsDir}: ${msg}` },
      });
    }

    log.tool.debug("read-logs.execute: exit", { success: true, recordsReturned: records.length });
    return JSON.stringify({
      success: true,
      count: records.length,
      logsDir,
      query: {
        traceId:      query.traceId,
        sessionId:    query.sessionId,
        userId:       query.userId,
        module:       query.module,
        level:        query.level,
        errorOnly:    query.errorOnly,
        contains:     query.contains,
        since:        sinceDate?.toISOString(),
        until:        untilDate?.toISOString(),
        limit,
      },
      records,
    });
  }
}
