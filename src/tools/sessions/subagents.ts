import type { ToolImplementation, ToolContext } from "../registry.js";
import { log } from "../../logger.js";
import { getRunner, isAttached } from "./attach.js";

export const SubagentsTool: ToolImplementation = {
  definition: {
    name: "subagents",
    description:
      "Spawn N background subagent sessions. Returns immediately with session IDs; sessions outlive this conversation. " +
      "Use this for fire-and-forget research / long-running work. For sync map-reduce, use orchestrate_tasks instead. " +
      'Example: subagents(tasks: ["research X", "draft Y"], shared_context: "project Foo")',
    parameters: {
      type: "object",
      properties: {
        tasks: {
          type: "array",
          description: "Array of prompts; one session per prompt",
        } as any,
        shared_context: { type: "string", description: "Common preamble prepended to every task" },
        metadata: {
          type: "object",
          description: "owl/model override per spawn",
        } as any,
      },
      required: ["tasks"],
    },
    capabilities: ["session_lifecycle"],
    executionPolicy: { timeoutMs: 10_000, maxRetries: 0 },
  },
  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    if (!isAttached()) {
      return JSON.stringify({ success: false, error: { code: "NOT_READY", message: "Session runner not yet initialized" } });
    }
    const tasks = args["tasks"] as string[] | undefined;
    const sharedContext = (args["shared_context"] as string | undefined) ?? "";
    const metadata = (args["metadata"] as Record<string, string> | undefined) ?? {};

    if (!Array.isArray(tasks) || tasks.length === 0) {
      return JSON.stringify({ success: false, error: { code: "MISSING_ARG", message: "tasks must be a non-empty array" } });
    }

    log.tool.debug("subagents.execute: entry", { count: tasks.length });

    const runner = getRunner();
    const parentId = context.engineContext?.sessionId ?? null;
    const sessions = [];
    for (const task of tasks) {
      const prompt = sharedContext ? `${sharedContext}\n\n${task}` : task;
      const s = await runner.spawn({
        prompt,
        parentId: parentId ?? undefined,
        metadata,
      });
      sessions.push({ id: s.id, prompt: s.prompt, status: s.status });
    }

    log.tool.debug("subagents.execute: exit", { spawned: sessions.length });
    return JSON.stringify({
      success: true,
      data: { spawned: sessions.length, sessions },
    });
  },
};
