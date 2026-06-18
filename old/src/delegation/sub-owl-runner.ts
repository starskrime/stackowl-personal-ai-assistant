/**
 * StackOwl — Sub-Owl Runner
 *
 * Executes a DecompositionPlan by spawning lightweight "sub-owl" ReAct
 * loops for each subtask, respecting dependency ordering via parallelGroups.
 *
 * Each sub-owl:
 *   - Gets its own bounded ReAct loop (maxIterations=8, 45s timeout)
 *   - Operates on a minimal tool subset derived from the subtask's tool list
 *   - Produces a string result that feeds into synthesis
 *
 * After all subtasks complete, a final synthesis call produces the
 * user-facing response.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { DecompositionPlan, SubTask } from "./decomposer.js";
import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface SubOwlResult {
  taskId: string;
  description: string;
  output: string;
  success: boolean;
  iterations: number;
  durationMs: number;
}

export interface DelegationResult {
  synthesis: string;
  subtaskResults: SubOwlResult[];
  totalDurationMs: number;
  successRate: number;
}

// ─── SubOwlRunner ─────────────────────────────────────────────────

export class SubOwlRunner {
  private readonly MAX_ITERATIONS = 8;
  private readonly SUBTASK_TIMEOUT_MS = 45_000;
  private readonly SYNTHESIS_TIMEOUT_MS = 20_000;

  constructor(
    private provider: ModelProvider,
    private toolRegistry: Map<string, ToolImplementation | { execute: (args: Record<string, unknown>, ctx: any) => Promise<string>; name: string }> = new Map(),
    private owlPersonality: string = "a capable AI assistant",
    private workspacePath: string = process.cwd(),
    private maxIterations: number = 8,
  ) {}

  /**
   * Execute all subtasks in the plan, respecting parallel groups.
   * Returns a fully synthesized response.
   */
  async runAll(plan: DecompositionPlan): Promise<DelegationResult> {
    const startTime = Date.now();
    const results = new Map<string, SubOwlResult>();

    log.engine.info(
      `[SubOwlRunner] Executing plan: ${plan.subtasks.length} subtasks, ` +
      `${plan.parallelGroups.length} parallel groups`,
    );

    for (const [groupIdx, groupIds] of plan.parallelGroups.entries()) {
      log.engine.info(`[SubOwlRunner] Group ${groupIdx + 1}/${plan.parallelGroups.length}: [${groupIds.join(", ")}]`);

      // Build context from already-completed tasks
      const priorContext = this.buildPriorContext(results);

      const groupTasks = groupIds
        .map((id) => plan.subtasks.find((t) => t.id === id))
        .filter(Boolean) as SubTask[];

      // Run group tasks in parallel
      const groupResults = await Promise.all(
        groupTasks.map((task) => this.runSubtask(task, priorContext)),
      );

      for (const result of groupResults) {
        results.set(result.taskId, result);
      }
    }

    const allResults = [...results.values()];
    const synthesis = await this.synthesize(plan.originalTask, allResults);
    const successCount = allResults.filter((r) => r.success).length;

    return {
      synthesis,
      subtaskResults: allResults,
      totalDurationMs: Date.now() - startTime,
      successRate: allResults.length > 0 ? successCount / allResults.length : 0,
    };
  }

  // ─── Single subtask execution ─────────────────────────────────

  private async runSubtask(
    task: SubTask,
    priorContext: string,
  ): Promise<SubOwlResult> {
    const startTime = Date.now();
    log.engine.debug(`[SubOwlRunner] Starting subtask ${task.id}: "${task.description.slice(0, 60)}"`);

    try {
      const result = await Promise.race([
        this.reactLoop(task, priorContext),
        new Promise<never>((_, reject) =>
          setTimeout(
            () => reject(new Error(`Subtask ${task.id} timed out after ${this.SUBTASK_TIMEOUT_MS}ms`)),
            this.SUBTASK_TIMEOUT_MS,
          ),
        ),
      ]);

      log.engine.info(
        `[SubOwlRunner] Subtask ${task.id} ✓ in ${Date.now() - startTime}ms`,
      );

      return {
        taskId: task.id,
        description: task.description,
        output: result.output,
        success: true,
        iterations: result.iterations,
        durationMs: Date.now() - startTime,
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.engine.warn(`[SubOwlRunner] Subtask ${task.id} failed: ${msg}`);

      return {
        taskId: task.id,
        description: task.description,
        output: `[Failed: ${msg}]`,
        success: false,
        iterations: 0,
        durationMs: Date.now() - startTime,
      };
    }
  }

  // ─── Tool call parser ─────────────────────────────────────────

  private parseToolCall(response: string): { toolName: string; toolArgs: Record<string, unknown> } | null {
    try {
      const parsed = JSON.parse(response.trim());
      if (typeof parsed.tool === "string") {
        return { toolName: parsed.tool, toolArgs: parsed.args ?? {} };
      }
    } catch { /* not a JSON tool call */ }
    return null;
  }

  // ─── Bounded ReAct loop ───────────────────────────────────────

  private async reactLoop(
    task: SubTask,
    priorContext: string,
  ): Promise<{ output: string; iterations: number }> {
    const history: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${this.owlPersonality}. You are executing one subtask as part of a larger plan.\n\n` +
          `Available tools for this subtask: ${task.tools.join(", ")}.\n` +
          `Expected output: ${task.expectedOutput}\n\n` +
          (priorContext ? `Context from prior subtasks:\n${priorContext}\n\n` : "") +
          `Complete the subtask directly. Do not ask clarifying questions. ` +
          `When done, provide your complete result.`,
      },
      {
        role: "user",
        content: `Subtask to complete:\n\n${task.description}`,
      },
    ];

    const maxIter = this.maxIterations ?? this.MAX_ITERATIONS;
    let iterations = 0;
    let lastResponse = "";

    const toolContext: ToolContext = { cwd: this.workspacePath };

    while (iterations < maxIter) {
      iterations++;

      const response = await this.provider.chat(history);
      lastResponse = response.content.trim();

      history.push({ role: "assistant", content: lastResponse });

      const toolCall = this.parseToolCall(lastResponse);

      if (!toolCall) {
        // No tool call — this is the final answer
        break;
      }

      // Dispatch to tool registry
      const tool = this.toolRegistry.get(toolCall.toolName);
      const toolResult = tool
        ? await tool.execute(toolCall.toolArgs, toolContext).catch(
            (e: Error) => `[Tool error: ${e.message}]`,
          )
        : `[Tool "${toolCall.toolName}" not found in registry]`;

      log.engine.debug(
        `[SubOwlRunner] Tool ${toolCall.toolName} result: ${toolResult.slice(0, 120)}`,
      );

      history.push({ role: "user", content: `Tool result: ${toolResult}` });
    }

    return { output: lastResponse, iterations };
  }

  // ─── Simple run() entry-point (takes subtask array) ──────────

  /**
   * Execute an array of subtasks sequentially and return their combined output.
   * This is a simplified entry-point for callers that already have a flat list
   * of SubTask objects (e.g. SubOwlRunner unit tests, inline delegation).
   */
  async run(subtasks: SubTask[]): Promise<string> {
    const results: string[] = [];
    const completed = new Map<string, SubOwlResult>();

    for (const task of subtasks) {
      const priorContext = this.buildPriorContext(completed);
      const startTime = Date.now();

      try {
        const loopResult = await Promise.race([
          this.reactLoop(task, priorContext),
          new Promise<never>((_, reject) =>
            setTimeout(
              () => reject(new Error(`Subtask ${task.id} timed out after ${this.SUBTASK_TIMEOUT_MS}ms`)),
              this.SUBTASK_TIMEOUT_MS,
            ),
          ),
        ]);

        const owlResult: SubOwlResult = {
          taskId: task.id,
          description: task.description,
          output: loopResult.output,
          success: true,
          iterations: loopResult.iterations,
          durationMs: Date.now() - startTime,
        };
        completed.set(task.id, owlResult);
        results.push(loopResult.output);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const owlResult: SubOwlResult = {
          taskId: task.id,
          description: task.description,
          output: `[Failed: ${msg}]`,
          success: false,
          iterations: 0,
          durationMs: Date.now() - startTime,
        };
        completed.set(task.id, owlResult);
        results.push(`[Failed: ${msg}]`);
      }
    }

    return results.join("\n\n");
  }

  // ─── Synthesis ────────────────────────────────────────────────

  async synthesize(
    originalTask: string,
    results: SubOwlResult[],
  ): Promise<string> {
    const resultBlock = results
      .map((r) => `**${r.taskId}** (${r.success ? "✓" : "✗"}): ${r.description}\n${r.output}`)
      .join("\n\n---\n\n");

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${this.owlPersonality}. You have completed multiple subtasks. ` +
          `Synthesize their results into a single, coherent, user-facing response. ` +
          `Be direct. Do not repeat the subtask descriptions — just deliver the result.`,
      },
      {
        role: "user",
        content:
          `Original task: "${originalTask}"\n\n` +
          `Subtask results:\n\n${resultBlock}\n\n` +
          `Synthesize into a final answer for the user.`,
      },
    ];

    try {
      const response = await Promise.race([
        this.provider.chat(messages),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("synthesis timeout")), this.SYNTHESIS_TIMEOUT_MS),
        ),
      ]);
      return response.content.trim();
    } catch (err) {
      // Fallback: concatenate successful results
      const successResults = results.filter((r) => r.success);
      if (successResults.length > 0) {
        return successResults.map((r) => r.output).join("\n\n");
      }
      return `Task partially completed. ${results.filter((r) => !r.success).length} subtasks failed.`;
    }
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private buildPriorContext(results: Map<string, SubOwlResult>): string {
    if (results.size === 0) return "";

    return [...results.values()]
      .filter((r) => r.success)
      .map((r) => `[${r.taskId}] ${r.description}:\n${r.output.slice(0, 400)}`)
      .join("\n\n");
  }
}
