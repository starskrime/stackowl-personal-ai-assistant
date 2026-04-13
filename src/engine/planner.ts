/**
 * StackOwl — Task Planner
 *
 * Decomposes complex multi-step tasks into discrete steps.
 * Each step is executed as a separate ReAct loop, with results
 * from completed steps injected as context for the next.
 */

import type { ModelProvider } from "../providers/base.js";
import type { ToolDefinition } from "../providers/base.js";
import { log } from "../logger.js";

export interface PlanStep {
  id: number;
  description: string;
  toolsNeeded: string[];
  dependsOn: number[];
  status: "pending" | "running" | "done" | "failed";
  result?: string;
}

export interface TaskPlan {
  goal: string;
  steps: PlanStep[];
  estimatedComplexity: "simple" | "moderate" | "complex";
}

// ─── Complexity Detection ────────────────────────────────────

const COMPLEX_PATTERNS = [
  /\bthen\b.*\b(then|after|next|finally)\b/i,
  /\bfirst\b.*\bthen\b/i,
  /\bstep\s*\d/i,
  /\b(plan|strategy|approach)\b.*\bfor\b/i,
  /\band\s+then\b/i,
  /\bafter\s+that\b/i,
  /\bmulti[-\s]?step/i,
];

const EXPLICIT_PLAN_TRIGGER = /^(plan:|\/plan\s)/i;

export function shouldUsePlanner(text: string): boolean {
  if (EXPLICIT_PLAN_TRIGGER.test(text.trim())) return true;

  // Count action verbs as a complexity signal
  const actionVerbs = text.match(
    /\b(create|build|deploy|test|fix|update|install|configure|migrate|refactor|analyze|fetch|download|write|read|run|execute)\b/gi,
  );
  if (actionVerbs && actionVerbs.length >= 3) return true;

  return COMPLEX_PATTERNS.some((p) => p.test(text));
}

// ─── Plan Generation ─────────────────────────────────────────

export class TaskPlanner {
  constructor(private provider: ModelProvider) {}

  /**
   * Create a research plan — decomposes a research topic into
   * fact-gathering → comparison → analysis → synthesis phases.
   */
  async createDeepResearchPlan(
    userMessage: string,
    subtopics: string[],
    availableTools: ToolDefinition[],
  ): Promise<TaskPlan> {
    const toolNames = availableTools.map((t) => `${t.name}: ${t.description}`);

    const subtopicList =
      subtopics.length > 0
        ? `Identified research subtopics:\n${subtopics
            .map((s, i) => `${i + 1}. ${s}`)
            .join("\n")}\n\n`
        : "";

    const prompt =
      `You are a research planner. Decompose a research request into phased steps.\n\n` +
      `${subtopicList}` +
      `USER REQUEST: ${userMessage}\n\n` +
      `AVAILABLE TOOLS:\n${toolNames.join("\n")}\n\n` +
      `Create a research plan with these phases:\n` +
      `Phase 1: Fact-Gathering — gather primary facts, definitions, overview\n` +
      `Phase 2: Deep-Dive — explore each subtopic with targeted searches\n` +
      `Phase 3: Comparison/Analysis — compare findings, identify contradictions\n` +
      `Phase 4: Synthesis — produce comprehensive, well-structured answer\n\n` +
      `Respond with ONLY valid JSON:\n` +
      `{\n` +
      `  "goal": "one-line summary of the research goal",\n` +
      `  "estimatedComplexity": "simple" | "moderate" | "complex",\n` +
      `  "steps": [\n` +
      `    {\n` +
      `      "id": 1,\n` +
      `      "description": "what to research in this step",\n` +
      `      "toolsNeeded": ["tool_name"],\n` +
      `      "dependsOn": []\n` +
      `    }\n` +
      `  ]\n` +
      `}\n\n` +
      `Maximum 8 steps. Output ONLY valid JSON.`;

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content: "You are a research planner. Output only valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.1 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```json"))
        jsonStr = jsonStr
          .replace(/^```json/, "")
          .replace(/```$/, "")
          .trim();
      else if (jsonStr.startsWith("```"))
        jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();

      const parsed = JSON.parse(jsonStr);
      return {
        goal: parsed.goal ?? userMessage.slice(0, 100),
        estimatedComplexity: parsed.estimatedComplexity ?? "complex",
        steps: (parsed.steps ?? []).map((s: any, i: number) => ({
          id: s.id ?? i + 1,
          description: s.description ?? "",
          toolsNeeded: s.toolsNeeded ?? [],
          dependsOn: s.dependsOn ?? [],
          status: "pending" as const,
        })),
      };
    } catch (err) {
      log.engine.warn(
        `[ResearchPlanner] Failed to parse plan: ${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        goal: userMessage.slice(0, 100),
        estimatedComplexity: "complex",
        steps: [
          {
            id: 1,
            description: "Research " + userMessage.slice(0, 80),
            toolsNeeded: [],
            dependsOn: [],
            status: "pending",
          },
        ],
      };
    }
  }

  /**
   * Generate a plan by asking the LLM to decompose the task.
   */
  async createPlan(
    userMessage: string,
    availableTools: ToolDefinition[],
    model?: string,
  ): Promise<TaskPlan> {
    const toolNames = availableTools.map((t) => `${t.name}: ${t.description}`);

    const prompt =
      `You are a task planner. Break down the following user request into discrete, ordered steps.\n\n` +
      `USER REQUEST:\n${userMessage}\n\n` +
      `AVAILABLE TOOLS:\n${toolNames.join("\n")}\n\n` +
      `Respond with a JSON object:\n` +
      `{\n` +
      `  "goal": "one-line summary of the overall goal",\n` +
      `  "estimatedComplexity": "simple" | "moderate" | "complex",\n` +
      `  "steps": [\n` +
      `    {\n` +
      `      "id": 1,\n` +
      `      "description": "what to do in this step",\n` +
      `      "toolsNeeded": ["tool_name"],\n` +
      `      "dependsOn": []\n` +
      `    }\n` +
      `  ]\n` +
      `}\n\n` +
      `Rules:\n` +
      `- Keep steps atomic — one action per step\n` +
      `- Use dependsOn to express ordering (step 2 depends on step 1)\n` +
      `- Only reference tools from the available list\n` +
      `- Maximum 8 steps\n` +
      `- Output ONLY valid JSON`;

    const response = await this.provider.chat(
      [
        {
          role: "system",
          content:
            "You are a task decomposition planner. Output only valid JSON.",
        },
        { role: "user", content: prompt },
      ],
      model,
      { temperature: 0.1 },
    );

    try {
      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```json"))
        jsonStr = jsonStr
          .replace(/^```json/, "")
          .replace(/```$/, "")
          .trim();
      else if (jsonStr.startsWith("```"))
        jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();

      const parsed = JSON.parse(jsonStr);

      return {
        goal: parsed.goal ?? userMessage.slice(0, 100),
        estimatedComplexity: parsed.estimatedComplexity ?? "moderate",
        steps: (parsed.steps ?? []).map((s: any, i: number) => ({
          id: s.id ?? i + 1,
          description: s.description ?? "",
          toolsNeeded: s.toolsNeeded ?? [],
          dependsOn: s.dependsOn ?? [],
          status: "pending" as const,
        })),
      };
    } catch {
      // Fallback: single-step plan
      log.engine.warn(
        "[Planner] Failed to parse plan — falling back to single step",
      );
      return {
        goal: userMessage.slice(0, 100),
        estimatedComplexity: "simple",
        steps: [
          {
            id: 1,
            description: userMessage,
            toolsNeeded: [],
            dependsOn: [],
            status: "pending",
          },
        ],
      };
    }
  }

  /**
   * Format plan + completed step results for injection into system prompt.
   */
  formatPlanContext(plan: TaskPlan): string {
    const lines = [`## Task Plan: ${plan.goal}\n`];
    for (const step of plan.steps) {
      const status =
        step.status === "done"
          ? "✅"
          : step.status === "failed"
            ? "❌"
            : step.status === "running"
              ? "⏳"
              : "⬜";
      lines.push(`${status} Step ${step.id}: ${step.description}`);
      if (step.result) {
        lines.push(`   Result: ${step.result.slice(0, 500)}`);
      }
    }
    return lines.join("\n");
  /**
   * Dynamically replan mid-flight based on a stuck step or new information.
   */
  async replan(
    currentPlan: TaskPlan,
    recentObservations: string,
    model?: string,
  ): Promise<TaskPlan> {
    const prompt =
      `You are a dynamic replanner. The current task is stuck or requires adaptation.\n\n` +
      `Current Plan Goal: ${currentPlan.goal}\n` +
      `Completed/Pending Steps Summary:\n${JSON.stringify(currentPlan.steps, null, 2)}\n\n` +
      `Recent Observations/Errors causing replan:\n${recentObservations}\n\n` +
      `Formulate a modified JSON plan array for the remaining steps. ` +
      `You must output ONLY a valid JSON object matching the original TaskPlan schema, integrating the new necessary steps.`;

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content: "You are a dynamic task replanner. Output ONLY valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        model,
        { temperature: 0.2 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith("```json")) jsonStr = jsonStr.replace(/^```json/, "").replace(/```$/, "").trim();
      else if (jsonStr.startsWith("```")) jsonStr = jsonStr.replace(/^```/, "").replace(/```$/, "").trim();
      
      const parsed = JSON.parse(jsonStr);
      return {
        goal: parsed.goal || currentPlan.goal,
        estimatedComplexity: parsed.estimatedComplexity || currentPlan.estimatedComplexity,
        steps: parsed.steps || currentPlan.steps,
      };
    } catch {
      log.engine.warn("[Planner] Replanning failed — returning original plan");
      return currentPlan;
    }
  }
}
