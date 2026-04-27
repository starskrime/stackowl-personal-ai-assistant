/**
 * StackOwl — SubOwl Executor
 *
 * Executes tools within sub-owl contexts. Provides consistent error handling
 * and partial success handling for delegated subtasks.
 */

import type { ToolImplementation, ToolContext } from "../tools/registry.js";
import type { SubTask } from "./decomposer.js";
import { log } from "../logger.js";

export interface SubOwlExecutionResult {
  taskId: string;
  success: boolean;
  output: string;
  toolsUsed: string[];
  error?: string;
}

export class SubOwlExecutor {
  constructor(private toolRegistry: Map<string, ToolImplementation>) {}

  async executeSubtask(
    task: SubTask,
    context: ToolContext,
  ): Promise<SubOwlExecutionResult> {
    const toolsUsed: string[] = [];
    let lastOutput = "";
    const errors: string[] = [];

    for (const toolName of task.tools) {
      const tool = this.toolRegistry.get(toolName);
      if (!tool) {
        const msg = `Tool ${toolName} not found in registry`;
        log.engine.warn(`[SubOwlExecutor] ${msg}`);
        errors.push(msg);
        continue;
      }

      try {
        const result = await tool.execute({}, context);
        lastOutput = result;
        toolsUsed.push(toolName);

        if (result.includes("[Failed") || result.toLowerCase().includes("error")) {
          log.engine.debug(
            `[SubOwlExecutor] Tool ${toolName} returned failure indicator`,
          );
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log.engine.warn(`[SubOwlExecutor] Tool ${toolName} threw: ${msg}`);
        errors.push(`${toolName}: ${msg}`);
      }
    }

    return {
      taskId: task.id,
      success: toolsUsed.length > 0,
      output: lastOutput || `[No output from tools: ${task.tools.join(", ")}]`,
      toolsUsed,
      error: errors.length > 0 ? errors.join("; ") : undefined,
    };
  }

  setToolRegistry(registry: Map<string, ToolImplementation>): void {
    this.toolRegistry = registry;
  }
}
