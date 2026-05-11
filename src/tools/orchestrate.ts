import type { ToolImplementation, ToolContext } from "./registry.js";
import { OwlEngine } from "../engine/runtime.js";
import { log } from "../logger.js";

export const OrchestrateTasksTool: ToolImplementation = {
  definition: {
    name: "orchestrate_tasks",
    description:
      "Spawn asynchronous background sub-owls to execute multiple unrelated complex tasks in parallel. Use this when the user asks you to do several slow or complex things at once, so you do not block the main thread.",
    parameters: {
      type: "object",
      properties: {
        tasks: {
          type: "array",
          description:
            "Array of specific, detailed instructions for each background agent to execute.",
        } as any, // Cast to any because base ToolDefinition is too strict for array types
      },
      required: ["tasks"],
    },
    capabilities: ["task_orchestration", "parallel_exec"],
    executionPolicy: { timeoutMs: 600_000, maxRetries: 0 },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const tasks = args["tasks"] as string[];
    if (!Array.isArray(tasks) || tasks.length === 0) {
      throw new Error("Must provide an array of at least one task string.");
    }

    const eCtx = context.engineContext;
    if (!eCtx) {
      throw new Error("EngineContext is required to spawn sub-owls.");
    }

    log.engine.info(
      `Spawning ${tasks.length} background sub-owls for parallel execution.`,
    );

    type LaneResult =
      | { laneId: string; status: "ok"; taskText: string; content: string }
      | { laneId: string; status: "error"; taskText: string; error: string };

    // Collect lane results — primary owl returns immediately, but the aggregated
    // summary is emitted via onProgress when all lanes complete so nothing is lost.
    const orchestrationPromise = Promise.allSettled<LaneResult>(
      tasks.map(async (taskText, index) => {
        const laneId = `Lane-${index + 1}`;
        try {
          if (eCtx.onProgress) {
            await eCtx.onProgress(`🚀 ${laneId} ← ${taskText}`);
          }

          const engine = new OwlEngine();
          const subContext = {
            ...eCtx,
            sessionHistory: [],
          };

          const backgroundPrompt = `[SYSTEM DIRECTIVE: You are an asynchronous background Sub-Owl spawned by the Primary Owl to execute a specific lane task. Do NOT ask clarifying questions. Execute this task to completion using your tools. Your final output will be shown directly to the user.]\n\nYOUR TASK: ${taskText}`;

          const result = await engine.run(backgroundPrompt, subContext);

          if (eCtx.onProgress) {
            await eCtx.onProgress(`✅ ${laneId} → ${result.content}`);
          }
          return { laneId, status: "ok" as const, taskText, content: result.content };
        } catch (error) {
          const msg = error instanceof Error ? error.message : String(error);
          log.engine.error(`${laneId} failed:`, error);
          if (eCtx.onProgress) {
            await eCtx.onProgress(`❌ ${laneId} → ${msg}`);
          }
          return { laneId, status: "error" as const, taskText, error: msg };
        }
      }),
    );

    // Aggregated summary fires when all lanes complete (background, doesn't block primary)
    orchestrationPromise
      .then(async (settled) => {
        const lanes: LaneResult[] = settled.map((s) =>
          s.status === "fulfilled"
            ? s.value
            : { laneId: "?", status: "error" as const, taskText: "", error: String(s.reason) },
        );
        const successCount = lanes.filter((l) => l.status === "ok").length;
        const errorCount = lanes.length - successCount;
        log.engine.info(
          `Orchestration complete: ${successCount}/${lanes.length} succeeded, ${errorCount} failed`,
        );
        if (eCtx.onProgress) {
          const summary = { total: lanes.length, success: successCount, error: errorCount, lanes };
          await eCtx.onProgress(`📊 ${JSON.stringify(summary)}`);
        }
      })
      .catch((err) => {
        log.engine.error("orchestrate_tasks: aggregation failed", err);
      });

    return JSON.stringify({
      spawned: tasks.length,
      status: "running",
      laneIds: tasks.map((_, i) => `Lane-${i + 1}`),
    });
  },
};
