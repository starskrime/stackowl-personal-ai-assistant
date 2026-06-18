import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

const MAX_TIMEOUT_MS = 600_000;
const DEFAULT_TIMEOUT_MS = 30_000;

export const SessionsYieldTool: ToolImplementation = {
  definition: {
    name: "sessions_yield",
    description:
      "Block until the session emits a new from_session message OR transitions to a terminal state OR the timeout fires. " +
      `Max timeout ${MAX_TIMEOUT_MS}ms (10 min). Use after sessions_send to wait for a response. ` +
      'Example: sessions_yield(id: "ses_abc", timeout_ms: 60000)',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id to wait on" },
        timeout_ms: { type: "number", description: `Max wait in ms (default ${DEFAULT_TIMEOUT_MS}, max ${MAX_TIMEOUT_MS})` },
      },
      required: ["id"],
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: MAX_TIMEOUT_MS + 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;
    const rawTimeout = (args["timeout_ms"] as number | undefined) ?? DEFAULT_TIMEOUT_MS;
    const timeoutMs = Math.min(Math.max(rawTimeout, 100), MAX_TIMEOUT_MS);

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    log.tool.debug("sessions_yield.execute: entry", { id, timeoutMs });

    const runner = getRunner();
    const event = await runner.awaitNextEvent(id, timeoutMs);

    log.tool.debug("sessions_yield.execute: exit", { id, ready: event.ready, status: event.status });
    return JSON.stringify({
      success: true,
      data: {
        ready: event.ready,
        status: event.status,
        new_messages: event.newMessages,
      },
    });
  },
};
