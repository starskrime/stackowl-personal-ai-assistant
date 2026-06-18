import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getStore, isAttached } from "./attach.js";

export const SessionsStatusTool: ToolImplementation = {
  definition: {
    name: "sessions_status",
    description:
      "Get the current status of a spawned session by id, optionally including pending messages from the subagent. " +
      'Example: sessions_status(id: "ses_abc", include_messages: true)',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id returned by subagents" },
        include_messages: { type: "boolean", description: "If true, include pending from_session messages in result" },
        since_message_id: { type: "number", description: "Return only messages with id > this value" },
      },
      required: ["id"],
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
    const id = args["id"] as string;
    const includeMessages = args["include_messages"] === true;
    const sinceMessageId = args["since_message_id"] as number | undefined;

    log.tool.debug("sessions_status.execute: entry", { id, includeMessages });

    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    const store = getStore();
    const session = store.findOne(id);
    if (!session) {
      return JSON.stringify({ success: false, error: { code: "NOT_FOUND", message: `Session "${id}" not found` } });
    }

    const data: Record<string, unknown> = { session };
    if (includeMessages) {
      let messages = store.pendingMessages(id, "from_session");
      if (typeof sinceMessageId === "number") {
        messages = messages.filter(m => m.id > sinceMessageId);
      }
      data.messages = messages;
      data.message_cursor = messages.length > 0 ? messages[messages.length - 1].id : sinceMessageId ?? 0;
    }

    log.tool.debug("sessions_status.execute: exit", { id, status: session.status });
    return JSON.stringify({ success: true, data });
  },
};
