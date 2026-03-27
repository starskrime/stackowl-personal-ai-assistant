/**
 * StackOwl — Structured Skill Executor
 *
 * Drives structured skill execution as a DAG of steps.
 * Each step is either a direct tool call or an LLM interpretation.
 * The engine controls execution — not the LLM.
 *
 * Key differences from prompt-injected skills:
 *   - Steps execute deterministically (not "hope the LLM follows")
 *   - Per-step timeout, retry, and failure handling
 *   - Progress callbacks for real-time UI updates
 *   - Step outputs pipe into subsequent steps via template interpolation
 *   - LLM only consulted for analysis/synthesis steps (type: 'llm')
 */

import { log } from "../logger.js";
import type { ModelProvider } from "../providers/base.js";
import type { ToolRegistry } from "../tools/registry.js";
import type {
  Skill,
  SkillStep,
  SkillStepStatus,
  SkillStepResult,
  SkillExecutionResult,
} from "./types.js";

// ─── Types ───────────────────────────────────────────────────────

interface StepState {
  step: SkillStep;
  status: SkillStepStatus;
  output?: string;
  error?: string;
  startedAt?: number;
  finishedAt?: number;
}

type ProgressCallback = (
  stepId: string,
  status: SkillStepStatus,
  detail?: string,
) => Promise<void>;

const DEFAULT_STEP_TIMEOUT = 30000;

// ─── Implementation ─────────────────────────────────────────────

export class SkillExecutor {
  constructor(
    private toolRegistry: ToolRegistry,
    private provider: ModelProvider,
    private cwd: string,
  ) {}

  /**
   * Execute a structured skill with extracted parameters.
   * Returns a detailed result with per-step outcomes.
   */
  async execute(
    skill: Skill,
    parameters: Record<string, unknown>,
    onProgress?: ProgressCallback,
  ): Promise<SkillExecutionResult> {
    const startTime = Date.now();
    const steps = skill.steps ?? [];

    if (steps.length === 0) {
      return {
        skillName: skill.name,
        status: "failed",
        stepResults: [],
        finalOutput: "Skill has no execution steps defined.",
        totalDurationMs: 0,
        parameters,
      };
    }

    // Initialize step states
    const states = new Map<string, StepState>();
    for (const step of steps) {
      states.set(step.id, { step, status: "pending" });
    }

    // Step outputs for template interpolation
    const outputs = new Map<string, string>();

    // Build execution waves (topological sort)
    const waves = this.buildWaves(steps);

    log.engine.info(
      `[SkillExecutor] Starting "${skill.name}" — ` +
        `${steps.length} steps in ${waves.length} wave(s)`,
    );

    let failed = false;

    for (const wave of waves) {
      if (failed) break;

      // Execute all steps in this wave in parallel
      await Promise.allSettled(
        wave.map(async (stepId) => {
          const state = states.get(stepId)!;

          // Skip if already resolved (e.g., on_failure target)
          if (state.status !== "pending") return;

          await this.executeStep(
            state,
            parameters,
            outputs,
            states,
            onProgress,
          );
        }),
      );

      // Check for failures that should stop execution
      for (const stepId of wave) {
        const state = states.get(stepId)!;
        if (state.status === "failed" && !state.step.optional) {
          // Check if on_failure was triggered and succeeded
          if (state.step.on_failure) {
            const fallback = states.get(state.step.on_failure);
            if (fallback && fallback.status === "success") continue;
          }
          failed = true;
          break;
        }
      }
    }

    // Collect results
    const stepResults: SkillStepResult[] = [];
    for (const [id, state] of states) {
      stepResults.push({
        stepId: id,
        status: state.status,
        output: state.output,
        error: state.error,
        durationMs:
          state.startedAt && state.finishedAt
            ? state.finishedAt - state.startedAt
            : 0,
      });
    }

    // Build final output from the last successful step
    // Prefer the last LLM step's output (it's usually the analysis/summary)
    let finalOutput = "";
    const completedSteps = [...states.values()].filter(
      (s) => s.status === "success",
    );
    const lastLlmStep = completedSteps.findLast(
      (s) => s.step.type === "llm",
    );
    if (lastLlmStep?.output) {
      finalOutput = lastLlmStep.output;
    } else if (completedSteps.length > 0) {
      // Concatenate all tool outputs
      finalOutput = completedSteps
        .filter((s) => s.output)
        .map((s) => `**${s.step.id}:**\n${s.output}`)
        .join("\n\n");
    }

    if (failed && !finalOutput) {
      const failedSteps = [...states.values()].filter(
        (s) => s.status === "failed",
      );
      finalOutput =
        `Skill "${skill.name}" failed.\n\n` +
        failedSteps
          .map(
            (s) => `Step "${s.step.id}" failed: ${s.error || "unknown error"}`,
          )
          .join("\n");
    }

    const totalDurationMs = Date.now() - startTime;

    log.engine.info(
      `[SkillExecutor] "${skill.name}" ${failed ? "FAILED" : "completed"} — ` +
        `${completedSteps.length}/${steps.length} steps succeeded in ${totalDurationMs}ms`,
    );

    return {
      skillName: skill.name,
      status: failed ? "failed" : "success",
      stepResults,
      finalOutput,
      totalDurationMs,
      parameters,
    };
  }

  // ─── Step Execution ───────────────────────────────────────────

  private async executeStep(
    state: StepState,
    parameters: Record<string, unknown>,
    outputs: Map<string, string>,
    allStates: Map<string, StepState>,
    onProgress?: ProgressCallback,
  ): Promise<void> {
    const { step } = state;
    state.status = "running";
    state.startedAt = Date.now();

    await onProgress?.(step.id, "running", step.tool || step.type || "step");

    try {
      const timeout = step.timeout_ms ?? DEFAULT_STEP_TIMEOUT;
      let output: string;

      if (step.type === "llm") {
        output = await this.withTimeout(
          () => this.executeLlmStep(step, parameters, outputs),
          timeout,
          step.id,
        );
      } else {
        output = await this.withTimeout(
          () => this.executeToolStep(step, parameters, outputs),
          timeout,
          step.id,
        );
      }

      state.status = "success";
      state.output = output;
      state.finishedAt = Date.now();
      outputs.set(step.id, output);
      await onProgress?.(step.id, "success");
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      state.status = "failed";
      state.error = errorMsg;
      state.finishedAt = Date.now();

      log.engine.warn(`[SkillExecutor] Step "${step.id}" failed: ${errorMsg}`);

      await onProgress?.(step.id, "failed", errorMsg);

      // Handle on_failure redirect
      if (step.on_failure) {
        const fallbackState = allStates.get(step.on_failure);
        if (fallbackState && fallbackState.status === "pending") {
          log.engine.info(
            `[SkillExecutor] Step "${step.id}" failed, running fallback "${step.on_failure}"`,
          );
          await this.executeStep(
            fallbackState,
            parameters,
            outputs,
            allStates,
            onProgress,
          );
        }
      } else if (step.optional) {
        state.status = "skipped";
        await onProgress?.(step.id, "skipped", "optional step failed");
      }
    }
  }

  private async executeToolStep(
    step: SkillStep,
    parameters: Record<string, unknown>,
    outputs: Map<string, string>,
  ): Promise<string> {
    if (!step.tool) throw new Error(`Step "${step.id}" has no tool specified`);

    // Interpolate args
    const resolvedArgs: Record<string, unknown> = {};
    if (step.args) {
      for (const [key, val] of Object.entries(step.args)) {
        resolvedArgs[key] =
          typeof val === "string"
            ? this.interpolate(val, parameters, outputs)
            : val;
      }
    }

    log.tool.info(
      `[SkillExecutor] Calling tool "${step.tool}" for step "${step.id}"`,
    );
    return this.toolRegistry.execute(step.tool, resolvedArgs, {
      cwd: this.cwd,
    });
  }

  private async executeLlmStep(
    step: SkillStep,
    parameters: Record<string, unknown>,
    outputs: Map<string, string>,
  ): Promise<string> {
    if (!step.prompt) throw new Error(`LLM step "${step.id}" has no prompt`);

    // Interpolate prompt
    let prompt = this.interpolate(step.prompt, parameters, outputs);

    // Collect inputs from referenced steps
    if (step.inputs && step.inputs.length > 0) {
      const inputSections: string[] = [];
      for (const ref of step.inputs) {
        const match = ref.match(/^(\w+)\.output$/);
        if (match) {
          const stepOutput = outputs.get(match[1]);
          if (stepOutput) {
            inputSections.push(`### ${match[1]} output:\n${stepOutput}`);
          }
        }
      }
      if (inputSections.length > 0) {
        prompt += "\n\n## Step Results:\n" + inputSections.join("\n\n");
      }
    }

    const response = await this.provider.chat([
      {
        role: "system",
        content:
          "You are a helpful assistant analyzing results. Be concise and actionable.",
      },
      { role: "user", content: prompt },
    ]);

    return response.content;
  }

  // ─── DAG Resolution ───────────────────────────────────────────

  /**
   * Build execution waves via topological sort.
   * Steps without dependencies go in wave 0.
   * Steps whose dependencies are all in earlier waves go in the next wave.
   */
  private buildWaves(steps: SkillStep[]): string[][] {
    const stepIds = new Set(steps.map((s) => s.id));
    const deps = new Map<string, Set<string>>();

    for (const step of steps) {
      const d = new Set<string>();
      if (step.depends_on) {
        for (const dep of step.depends_on) {
          if (stepIds.has(dep)) d.add(dep);
        }
      }
      deps.set(step.id, d);
    }

    const waves: string[][] = [];
    const placed = new Set<string>();
    let remaining = new Set(stepIds);

    while (remaining.size > 0) {
      const wave: string[] = [];

      for (const id of remaining) {
        const d = deps.get(id)!;
        // All dependencies satisfied?
        if ([...d].every((dep) => placed.has(dep))) {
          wave.push(id);
        }
      }

      if (wave.length === 0) {
        // Cycle detected — cannot resolve dependencies
        log.engine.error(
          `[SkillExecutor] Circular dependency detected in steps: ${[...remaining].join(", ")}`,
        );
        throw new Error(
          `Circular dependency detected in steps: ${[...remaining].join(", ")}`,
        );
      }

      waves.push(wave);
      for (const id of wave) {
        placed.add(id);
        remaining.delete(id);
      }
    }

    return waves;
  }

  // ─── Helpers ──────────────────────────────────────────────────

  /**
   * Interpolate {{param}} and {{stepId.output}} templates in a string.
   */
  private interpolate(
    template: string,
    parameters: Record<string, unknown>,
    outputs: Map<string, string>,
  ): string {
    return template.replace(
      /\{\{(\w+(?:\.\w+)?)\}\}/g,
      (match, key: string) => {
        // Check if it's a step output reference: stepId.output
        if (key.includes(".")) {
          const [stepId, field] = key.split(".");
          if (field === "output") {
            return outputs.get(stepId) ?? match;
          }
        }
        // Otherwise it's a parameter reference
        const value = parameters[key];
        return value !== undefined ? String(value) : match;
      },
    );
  }

  /**
   * Run an async function with a timeout.
   */
  private async withTimeout<T>(
    fn: () => Promise<T>,
    timeoutMs: number,
    stepId: string,
  ): Promise<T> {
    let timer: ReturnType<typeof setTimeout>;
    return Promise.race([
      fn().then((result) => {
        clearTimeout(timer);
        return result;
      }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Error(`Step "${stepId}" timed out after ${timeoutMs}ms`),
            ),
          timeoutMs,
        );
      }),
    ]);
  }
}
