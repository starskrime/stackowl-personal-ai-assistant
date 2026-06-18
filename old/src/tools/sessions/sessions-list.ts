import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getStore, isAttached } from "./attach.js";
import type { SessionStatus } from "../../sessions/types.js";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

export const SessionsListTool: ToolImplementation = {
  definition: {
    name: "sessions_list",
    description:
      "Enumerate sessions, optionally filtered by status or parent. " +
      `Default limit ${DEFAULT_LIMIT}, max ${MAX_LIMIT}. ` +
      'Example: sessions_list(status: "running") — what subagents are still working?',
    parameters: {
      type: "object",
      properties: {
        status: {
          type: "string",
          enum: ["pending", "running", "awaiting_input", "completed", "terminated", "failed"],
          description: "Filter by status",
        },
        parent_id: { type: "string", description: "Filter to sessions spawned from this parent" },
        limit: { type: "number", description: `Cap (default ${DEFAULT_LIMIT}, max ${MAX_LIMIT})` },
      },
    },
    capabilities: ["session_query"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const status = args["status"] as SessionStatus | undefined;
    const parentId = args["parent_id"] as string | undefined;
    const rawLimit = (args["limit"] as number | undefined) ?? DEFAULT_LIMIT;
    const limit = Math.min(rawLimit, MAX_LIMIT);

    log.tool.debug("sessions_list.execute: entry", { status, parentId, limit });

    const store = getStore();
    const sessions = store.list({ status, parentId, limit });

    log.tool.debug("sessions_list.execute: exit", { count: sessions.length });
    return JSON.stringify({
      success: true,
      data: { sessions, total: sessions.length },
    });
  },
};
