import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

export const SessionsTerminateTool: ToolImplementation = {
  definition: {
    name: "sessions_terminate",
    description:
      "Kill a running subagent session. Idempotent on terminal sessions (returns terminated=true with previous_status). " +
      'Example: sessions_terminate(id: "ses_abc")',
    parameters: {
      type: "object",
      properties: {
        id: { type: "string", description: "Session id to terminate" },
      },
      required: ["id"],
    },
    capabilities: ["session_lifecycle"],
    executionPolicy: { timeoutMs: 5_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const id = args["id"] as string;
    if (!id) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "id is required" } });
    }

    log.tool.debug("sessions_terminate.execute: entry", { id });

    const runner = getRunner();
    const result = runner.terminate(id);

    log.tool.debug("sessions_terminate.execute: exit", { id, terminated: result.terminated });
    return JSON.stringify({
      success: true,
      data: {
        terminated: result.terminated,
        previous_status: result.previousStatus,
      },
    });
  },
};
