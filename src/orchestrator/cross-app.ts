/**
 * StackOwl — Cross-App Action Planner
 *
 * Plans and executes multi-app workflows when the user asks
 * for operations that span multiple connected systems.
 *
 * Example: "Check the failing tests in GitHub, find the related error
 * in Sentry, and post a summary to Slack."
 */

import type { ModelProvider } from "../providers/base.js";
import type { ToolRegistry, ToolContext } from "../tools/registry.js";
import type { ActionPlan, ActionStep, ActionResult } from "./types.js";
import { log } from "../logger.js";

export class CrossAppPlanner {
  constructor(
    private provider: ModelProvider,
    private toolRegistry: ToolRegistry | undefined,
    private cwd: string,
  ) {}

  /**
   * Given a user request, plan a multi-app action sequence.
   * Uses one LLM call to decompose the request into steps.
   */
  async plan(
    userRequest: string,
    availableTools: string[],
    connectedApps: string[],
  ): Promise<ActionPlan | null> {
    if (availableTools.length === 0 && connectedApps.length === 0) {
      return null;
    }

    const prompt = `You are an action planner. The user wants to perform an operation that may span multiple apps/tools.

## Available Tools
${availableTools.join(", ")}

## Connected Apps
${connectedApps.join(", ") || "none"}

## User Request
${userRequest}

## Output Format
Return ONLY valid JSON:
{
  "description": "what this plan does",
  "steps": [
    {
      "id": "step-1",
      "app": "tool or app name",
      "action": "what to do",
      "args": {},
      "dependsOn": [],
      "extractFields": ["field_to_pass_downstream"]
    }
  ],
  "requiresConfirmation": true,
  "estimatedDuration": "~30 seconds"
}

If the request does NOT need cross-app coordination, return {"skip": true}.
Keep steps minimal — only what's needed.`;

    try {
      const chatResponse = await this.provider.chat([
        { role: "user", content: prompt },
      ]);
      const response = chatResponse.content;

      const jsonMatch = response.match(/\{[\s\S]*\}/);
      if (!jsonMatch) return null;

      const parsed = JSON.parse(jsonMatch[0]);
      if (parsed.skip) return null;

      const plan: ActionPlan = {
        id: `plan-${Date.now()}`,
        description: parsed.description ?? userRequest,
        steps: (parsed.steps ?? []).map((s: Record<string, unknown>) => ({
          id: s.id ?? `step-${Math.random().toString(36).slice(2, 6)}`,
          app: String(s.app ?? ""),
          action: String(s.action ?? ""),
          args: (s.args as Record<string, unknown>) ?? {},
          dependsOn: (s.dependsOn as string[]) ?? [],
          extractFields: (s.extractFields as string[]) ?? [],
        })),
        requiresConfirmation: parsed.requiresConfirmation ?? true,
        estimatedDuration: parsed.estimatedDuration,
      };

      log.engine.info(
        `[CrossApp] Planned ${plan.steps.length} step(s): ${plan.description}`,
      );
      return plan;
    } catch (err) {
      log.engine.warn(`[CrossApp] Planning failed: ${err}`);
      return null;
    }
  }

  /**
   * Execute an action plan, step by step with dependency resolution.
   */
  async execute(
    plan: ActionPlan,
    onProgress?: (stepId: string, status: string) => void,
  ): Promise<ActionResult> {
    const result: ActionResult = {
      planId: plan.id,
      status: "completed",
      stepResults: [],
    };

    const outputs = new Map<string, unknown>();

    // Build dependency waves
    const waves = this.buildWaves(plan.steps);

    for (const wave of waves) {
      const waveResults = await Promise.allSettled(
        wave.map(async (step) => {
          onProgress?.(step.id, `Running: ${step.action} on ${step.app}`);

          try {
            // Resolve args with outputs from previous steps
            const resolvedArgs = this.resolveArgs(step.args, outputs);
            const output = await this.executeStep(step, resolvedArgs);

            outputs.set(step.id, output);
            onProgress?.(step.id, `Done: ${step.action}`);

            return {
              stepId: step.id,
              app: step.app,
              status: "done" as const,
              output:
                typeof output === "string" ? output : JSON.stringify(output),
            };
          } catch (err) {
            const error = err instanceof Error ? err.message : String(err);
            onProgress?.(step.id, `Failed: ${step.action} — ${error}`);
            return {
              stepId: step.id,
              app: step.app,
              status: "failed" as const,
              error,
            };
          }
        }),
      );

      for (const wr of waveResults) {
        if (wr.status === "fulfilled") {
          result.stepResults.push(wr.value);
          if (wr.value.status === "failed") {
            result.status = "partial";
          }
        } else {
          result.stepResults.push({
            stepId: "unknown",
            app: "unknown",
            status: "failed",
            error: wr.reason?.message ?? String(wr.reason),
          });
          result.status = "partial";
        }
      }
    }

    // Check if all failed
    if (result.stepResults.every((r) => r.status === "failed")) {
      result.status = "failed";
    }

    return result;
  }

  private async executeStep(
    step: ActionStep,
    args: Record<string, unknown>,
  ): Promise<unknown> {
    // Try to find a matching tool
    if (this.toolRegistry) {
      try {
        const toolCtx: ToolContext = { cwd: this.cwd };
        const result = await this.toolRegistry.execute(step.app, args, toolCtx);
        return result;
      } catch {
        // Tool not found, try action as tool name
        try {
          const toolCtx: ToolContext = { cwd: this.cwd };
          const result = await this.toolRegistry.execute(
            step.action,
            args,
            toolCtx,
          );
          return result;
        } catch {
          // Neither found
        }
      }
    }

    throw new Error(
      `No handler found for app "${step.app}" action "${step.action}"`,
    );
  }

  private resolveArgs(
    args: Record<string, unknown>,
    outputs: Map<string, unknown>,
  ): Record<string, unknown> {
    const resolved: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(args)) {
      if (
        typeof value === "string" &&
        value.startsWith("{{") &&
        value.endsWith("}}")
      ) {
        const ref = value.slice(2, -2).trim();
        const [stepId, ...fieldParts] = ref.split(".");
        const stepOutput = outputs.get(stepId);
        if (
          stepOutput &&
          typeof stepOutput === "object" &&
          stepOutput !== null
        ) {
          resolved[key] =
            (stepOutput as Record<string, unknown>)[fieldParts.join(".")] ??
            value;
        } else {
          resolved[key] = stepOutput ?? value;
        }
      } else {
        resolved[key] = value;
      }
    }
    return resolved;
  }

  private buildWaves(steps: ActionStep[]): ActionStep[][] {
    const waves: ActionStep[][] = [];
    const completed = new Set<string>();
    const remaining = [...steps];

    while (remaining.length > 0) {
      const wave = remaining.filter((step) => {
        const deps = step.dependsOn ?? [];
        return deps.every((d) => completed.has(d));
      });

      if (wave.length === 0) {
        waves.push(remaining.splice(0));
        break;
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
