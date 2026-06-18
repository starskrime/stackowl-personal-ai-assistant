import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, getStore, isAttached } from "./attach.js";

const TERMINAL = new Set(["completed", "terminated", "failed"]);

export const SessionsSendTool: ToolImplementation = {
  definition: {
    name: "sessions_send",
    description:
      "Send a message to a running subagent session. Non-blocking; the message is queued for the session to consume. " +
      "Use sessions_yield to wait for a response. " +
      'Example: sessions_send(id: "ses_abc", content: "what have you found so far?")',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Target session id" },
        content: { type: "string", description: "Message text to deliver to the subagent" },
      },
      required: ["id", "content"],
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
    const content = args["content"] as string;

    if (!id) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    if (!content) return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "content is required" } });

    log.tool.debug("sessions_send.execute: entry", { id, contentLen: content.length });

    const store = getStore();
    const session = store.findOne(id);
    if (!session) {
      return JSON.stringify({ success: false, error: { code: "NOT_FOUND", message: `Session "${id}" not found` } });
    }

    if (TERMINAL.has(session.status)) {
      return JSON.stringify({
        success: true,
        data: { accepted: false, queued_message_id: 0, current_status: session.status },
      });
    }

    const runner = getRunner();
    const msg = runner.enqueueMessage(id, content);
    log.tool.debug("sessions_send.execute: exit", { id, messageId: msg.id });
    return JSON.stringify({
      success: true,
      data: { accepted: true, queued_message_id: msg.id, current_status: session.status },
    });
  },
};
