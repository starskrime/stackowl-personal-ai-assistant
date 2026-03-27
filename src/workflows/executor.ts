/**
 * StackOwl — Workflow Executor
 *
 * Executes workflow chains with dependency-aware scheduling,
 * variable interpolation, and retry logic.
 */

import type {
  WorkflowDefinition,
  WorkflowStep,
  WorkflowRun,
  StepResult,
  ToolStepConfig,
  LlmStepConfig,
  AgentStepConfig,
  ConditionStepConfig,
  ParallelStepConfig,
  WaitStepConfig,
} from "./types.js";
import type { ToolRegistry, ToolContext } from "../tools/registry.js";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { StackOwlConfig } from "../config/loader.js";
import { OwlEngine } from "../engine/runtime.js";
import { log } from "../logger.js";

export class WorkflowExecutor {
  private engine: OwlEngine;

  constructor(
    private toolRegistry: ToolRegistry | undefined,
    private provider: ModelProvider,
    private defaultCwd: string,
    private owlRegistry?: OwlRegistry,
    private config?: StackOwlConfig,
  ) {
    this.engine = new OwlEngine();
  }

  /**
   * Execute a workflow definition with given parameters.
   */
  async execute(
    workflow: WorkflowDefinition,
    params: Record<string, unknown>,
    onProgress?: (stepId: string, status: string) => void,
  ): Promise<WorkflowRun> {
    const run: WorkflowRun = {
      id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      workflowId: workflow.id,
      status: "running",
      parameters: params,
      stepResults: [],
      startedAt: Date.now(),
    };

    const context = new ExecutionContext(params);

    try {
      // Build execution waves based on dependencies
      const waves = this.buildWaves(workflow.steps);

      for (const wave of waves) {
        const results = await Promise.allSettled(
          wave.map((step) => this.executeStep(step, context, onProgress)),
        );

        for (let i = 0; i < results.length; i++) {
          const result = results[i];
          const step = wave[i];
          if (result.status === "fulfilled") {
            run.stepResults.push(result.value);
            context.setResult(step.id, result.value.output);
          } else {
            const failed: StepResult = {
              stepId: step.id,
              status: "failed",
              error: result.reason?.message ?? String(result.reason),
              durationMs: 0,
              retryCount: 0,
            };
            run.stepResults.push(failed);
            // Fail the whole workflow on step failure
            run.status = "failed";
            run.error = `Step "${step.name}" failed: ${failed.error}`;
            run.completedAt = Date.now();
            return run;
          }
        }
      }

      run.status = "completed";
      run.completedAt = Date.now();

      // Update workflow stats
      workflow.lastRunAt = Date.now();
      workflow.runCount++;
    } catch (err) {
      run.status = "failed";
      run.error = err instanceof Error ? err.message : String(err);
      run.completedAt = Date.now();
    }

    return run;
  }

  private async executeStep(
    step: WorkflowStep,
    context: ExecutionContext,
    onProgress?: (stepId: string, status: string) => void,
  ): Promise<StepResult> {
    const start = Date.now();
    let retryCount = 0;
    const maxRetries = step.retries ?? 0;

    onProgress?.(step.id, `Starting: ${step.name}`);

    while (retryCount <= maxRetries) {
      try {
        // Resolve input variables
        if (step.inputs) {
          for (const [key, ref] of Object.entries(step.inputs)) {
            const value = context.resolve(ref);
            if (value !== undefined) {
              context.setLocal(step.id, key, value);
            }
          }
        }

        const output = await this.runStep(step, context);

        onProgress?.(step.id, `Completed: ${step.name}`);
        return {
          stepId: step.id,
          status: "completed",
          output,
          durationMs: Date.now() - start,
          retryCount,
        };
      } catch (err) {
        retryCount++;
        if (retryCount > maxRetries) {
          onProgress?.(step.id, `Failed: ${step.name}`);
          return {
            stepId: step.id,
            status: "failed",
            error: err instanceof Error ? err.message : String(err),
            durationMs: Date.now() - start,
            retryCount: retryCount - 1,
          };
        }
        log.engine.info(
          `[WorkflowExecutor] Retrying step "${step.name}" (${retryCount}/${maxRetries})`,
        );
      }
    }

    // Unreachable but TypeScript needs it
    return {
      stepId: step.id,
      status: "failed",
      error: "Max retries exceeded",
      durationMs: Date.now() - start,
      retryCount,
    };
  }

  private async runStep(
    step: WorkflowStep,
    context: ExecutionContext,
  ): Promise<unknown> {
    const timeoutMs = step.timeoutMs ?? 30_000;

    const promise = (async () => {
      switch (step.type) {
        case "tool":
          return this.runToolStep(step.config as ToolStepConfig, context);
        case "llm":
          return this.runLlmStep(step.config as LlmStepConfig, context);
        case "agent":
          return this.runAgentStep(step.config as AgentStepConfig, context);
        case "condition":
          return this.runConditionStep(
            step.config as ConditionStepConfig,
            context,
          );
        case "parallel":
          return this.runParallelStep(step.config as ParallelStepConfig);
        case "wait":
          return this.runWaitStep(step.config as WaitStepConfig);
        default:
          throw new Error(`Unknown step type: ${step.type}`);
      }
    })();

    return Promise.race([
      promise,
      new Promise((_, reject) =>
        setTimeout(
          () =>
            reject(
              new Error(`Step "${step.name}" timed out after ${timeoutMs}ms`),
            ),
          timeoutMs,
        ),
      ),
    ]);
  }

  private async runToolStep(
    config: ToolStepConfig,
    context: ExecutionContext,
  ): Promise<unknown> {
    if (!this.toolRegistry) {
      throw new Error("No tool registry available");
    }

    // Interpolate args with context values
    const resolvedArgs: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(config.args)) {
      if (
        typeof value === "string" &&
        value.startsWith("{{") &&
        value.endsWith("}}")
      ) {
        const ref = value.slice(2, -2).trim();
        resolvedArgs[key] = context.resolve(ref) ?? value;
      } else {
        resolvedArgs[key] = value;
      }
    }

    const toolCtx: ToolContext = { cwd: this.defaultCwd };
    const result = await this.toolRegistry.execute(
      config.toolName,
      resolvedArgs,
      toolCtx,
    );
    return result;
  }

  private async runLlmStep(
    config: LlmStepConfig,
    context: ExecutionContext,
  ): Promise<unknown> {
    // Interpolate prompt
    let prompt = config.prompt;
    const varPattern = /\{\{([^}]+)\}\}/g;
    let match: RegExpExecArray | null;
    while ((match = varPattern.exec(config.prompt)) !== null) {
      const ref = match[1].trim();
      const value = context.resolve(ref);
      if (value !== undefined) {
        prompt = prompt.replace(match[0], String(value));
      }
    }

    const messages: ChatMessage[] = [{ role: "user", content: prompt }];
    const response = await this.provider.chat(messages);
    const text = response.content;

    if (config.extractAs === "json") {
      try {
        return JSON.parse(text);
      } catch {
        return text;
      }
    }
    if (config.extractAs === "list") {
      return text.split("\n").filter((l: string) => l.trim().length > 0);
    }
    return text;
  }

  private async runAgentStep(
    config: AgentStepConfig,
    context: ExecutionContext,
  ): Promise<unknown> {
    if (!this.owlRegistry || !this.config) {
      throw new Error("OwlRegistry and Config are required for agent steps.");
    }

    let prompt =
      config.prompt ||
      (config as any).message ||
      (config as any).task ||
      "Follow your instructions.";
    const varPattern = /\{\{([^}]+)\}\}/g;
    let match: RegExpExecArray | null;
    if (config.prompt) {
      while ((match = varPattern.exec(config.prompt)) !== null) {
        const ref = match[1].trim();
        const value = context.resolve(ref);
        if (value !== undefined) {
          prompt = prompt.replace(match[0], String(value));
        }
      }
    }

    let owl = this.owlRegistry.getDefault();
    const desiredOwlName =
      config.owlName || (config as any).agent_id || (config as any).agent;
    const desiredOwlRole =
      config.owlRole || (config as any).role || desiredOwlName;

    if (desiredOwlName) {
      const found = this.owlRegistry.get(desiredOwlName);
      if (found) owl = found;
      else if (desiredOwlRole) {
        const roleStr = desiredOwlRole.toLowerCase();
        const allOwls = this.owlRegistry.listOwls();
        const foundRole = allOwls.find(
          (o) =>
            (o.persona.type &&
              o.persona.type.toLowerCase().includes(roleStr)) ||
            (o.persona.name && o.persona.name.toLowerCase() === roleStr),
        );
        if (foundRole) owl = foundRole;
      }
    } else if (desiredOwlRole) {
      const roleStr = desiredOwlRole.toLowerCase();
      const allOwls = this.owlRegistry.listOwls();
      const found = allOwls.find(
        (o) =>
          (o.persona.type && o.persona.type.toLowerCase().includes(roleStr)) ||
          (o.persona.name && o.persona.name.toLowerCase() === roleStr),
      );
      if (found) owl = found;
    }

    const response = await this.engine.run(prompt, {
      provider: this.provider,
      owl,
      config: this.config,
      toolRegistry: config.useTools ? this.toolRegistry : undefined,
      sessionHistory: [],
    });

    const text = response.content;
    if (config.extractAs === "json") {
      try {
        return JSON.parse(text);
      } catch {
        return text;
      }
    }
    if (config.extractAs === "list") {
      return text.split("\n").filter((l: string) => l.trim().length > 0);
    }
    return text;
  }

  private runConditionStep(
    config: ConditionStepConfig,
    context: ExecutionContext,
  ): string {
    // Simple expression evaluation: "stepId.output === 'value'"
    let expr = config.expression;
    const varPattern = /\{\{([^}]+)\}\}/g;
    let match: RegExpExecArray | null;
    while ((match = varPattern.exec(config.expression)) !== null) {
      const ref = match[1].trim();
      const value = context.resolve(ref);
      expr = expr.replace(match[0], JSON.stringify(value));
    }

    // Evaluate simple comparisons (no eval for safety)
    const result = this.evaluateSimpleExpression(expr);
    return result ? config.thenStep : (config.elseStep ?? "");
  }

  private evaluateSimpleExpression(expr: string): boolean {
    // Support: "value" === "value", "value" !== "value", truthy checks
    const eqMatch = expr.match(/^(.+?)\s*===\s*(.+)$/);
    if (eqMatch) {
      const left = eqMatch[1].trim().replace(/^["']|["']$/g, "");
      const right = eqMatch[2].trim().replace(/^["']|["']$/g, "");
      return left === right;
    }

    const neqMatch = expr.match(/^(.+?)\s*!==\s*(.+)$/);
    if (neqMatch) {
      const left = neqMatch[1].trim().replace(/^["']|["']$/g, "");
      const right = neqMatch[2].trim().replace(/^["']|["']$/g, "");
      return left !== right;
    }

    // Truthy check
    const val = expr.trim().replace(/^["']|["']$/g, "");
    return (
      val !== "" && val !== "false" && val !== "null" && val !== "undefined"
    );
  }

  private runParallelStep(_config: ParallelStepConfig): string {
    // Parallel steps are handled by the wave builder, not here
    return "parallel-group";
  }

  private runWaitStep(config: WaitStepConfig): Promise<string> {
    return new Promise((resolve) =>
      setTimeout(() => resolve("waited"), config.durationMs),
    );
  }

  /**
   * Build execution waves from step dependencies.
   * Each wave contains steps whose dependencies are all in previous waves.
   */
  private buildWaves(steps: WorkflowStep[]): WorkflowStep[][] {
    const waves: WorkflowStep[][] = [];
    const completed = new Set<string>();
    const remaining = [...steps];

    while (remaining.length > 0) {
      const wave = remaining.filter((step) => {
        const deps = step.dependsOn ?? [];
        return deps.every((d) => completed.has(d));
      });

      if (wave.length === 0) {
        // Circular dependency — fail securely instead of silently running unresolved steps
        log.engine.error(
          "[WorkflowExecutor] Circular dependency detected among remaining steps",
        );
        throw new Error(
          "Workflow resolution failed: Circular or missing dependencies detected",
        );
      }

      waves.push(wave);
      for (const step of wave) {
        completed.add(step.id);
        const idx = remaining.indexOf(step);
        if (idx >= 0) remaining.splice(idx, 1);
      }
    }

    return waves;
  }
}

/**
 * Tracks variable values across workflow execution.
 */
class ExecutionContext {
  private results = new Map<string, unknown>();
  private locals = new Map<string, Record<string, unknown>>();

  constructor(private params: Record<string, unknown>) {}

  setResult(stepId: string, value: unknown): void {
    this.results.set(stepId, value);
  }

  setLocal(stepId: string, key: string, value: unknown): void {
    const existing = this.locals.get(stepId) ?? {};
    existing[key] = value;
    this.locals.set(stepId, existing);
  }

  /**
   * Resolve a reference like "step1.output" or "params.name"
   */
  resolve(ref: string): unknown {
    const parts = ref.split(".");
    if (parts[0] === "params") {
      return this.params[parts.slice(1).join(".")];
    }
    const stepId = parts[0];
    if (parts.length === 1) {
      return this.results.get(stepId);
    }
    const result = this.results.get(stepId);
    if (result && typeof result === "object" && result !== null) {
      return (result as Record<string, unknown>)[parts.slice(1).join(".")];
    }
    return this.results.get(stepId);
  }
}
