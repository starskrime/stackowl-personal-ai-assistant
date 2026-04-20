/**
 * StackOwl — Task Decomposer
 *
 * Breaks a complex user request into an ordered plan of subtasks
 * that can be executed by SubOwlRunner instances.
 *
 * Uses an LLM call to produce the decomposition, then validates
 * and normalises the result. Subtasks that have no dependencies on
 * each other are grouped into `parallelGroups` for concurrent execution.
 *
 * Domain → Tool-set mapping guides which tools each subtask needs,
 * so SubOwlRunner can configure the right minimal tool registry.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import { EnvironmentScanner, type EnvSnapshot } from "./env-scanner.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface SubTask {
  /** Unique id within this plan, e.g. "t1", "t2" */
  id: string;
  /** Human-readable description of what to do */
  description: string;
  /** Tools this subtask likely needs */
  tools: string[];
  /** IDs of subtasks that must complete before this one starts */
  dependsOn: string[];
  /** Expected output description (used for synthesis context) */
  expectedOutput: string;
}

export interface DecompositionPlan {
  /** Original user task */
  originalTask: string;
  /** All subtasks, topologically sorted */
  subtasks: SubTask[];
  /**
   * Groups of subtask IDs that can run concurrently.
   * Earlier groups must finish before later groups start.
   * E.g. [["t1","t2"], ["t3"], ["t4","t5"]]
   */
  parallelGroups: string[][];
  /** Estimated total steps (for progress UI) */
  totalSteps: number;
}

// ─── Domain → default tools mapping ──────────────────────────────

const DOMAIN_TOOLS: Record<string, string[]> = {
  research:      ["web_fetch", "web_search", "recall", "pellet_recall"],
  coding:        ["read_file", "write_file", "shell"],
  memory:        ["recall", "remember", "pellet_recall"],
  filesystem:    ["read_file", "write_file", "shell"],
  web:           ["web_fetch", "web_search"],
  analysis:      ["recall", "pellet_recall", "read_file"],
  communication: ["send_file"],
};

// ─── TaskDecomposer ───────────────────────────────────────────────

export class TaskDecomposer {
  private readonly timeoutMs = 20_000;
  private readonly maxSubtasks = 12;
  private readonly envScanner = new EnvironmentScanner();

  constructor(private provider: ModelProvider) {}

  /**
   * Decompose `task` into a structured `DecompositionPlan`.
   * Runs an environment scan first so the LLM knows the actual project context.
   * Falls back to a single-task plan if the LLM call fails.
   */
  async decompose(task: string, cwd?: string): Promise<DecompositionPlan> {
    log.engine.info(`[TaskDecomposer] Decomposing task: "${task.slice(0, 100)}"`);

    // Scan environment in parallel with no await blocking — fast best-effort
    let envSnapshot: EnvSnapshot | null = null;
    try {
      envSnapshot = await Promise.race([
        this.envScanner.scan(cwd),
        new Promise<null>((resolve) => setTimeout(() => resolve(null), 600)),
      ]);
    } catch {
      // Non-fatal — decompose without env context
    }

    try {
      const raw = await this.callLLM(task, envSnapshot?.summary);
      const subtasks = this.validate(raw, task);
      const parallelGroups = this.buildParallelGroups(subtasks);

      return {
        originalTask: task,
        subtasks,
        parallelGroups,
        totalSteps: subtasks.length,
      };
    } catch (err) {
      log.engine.warn(`[TaskDecomposer] Decomposition failed — single-task fallback: ${err instanceof Error ? err.message : err}`);
      return this.singleTaskFallback(task);
    }
  }

  // ─── LLM call ────────────────────────────────────────────────

  private async callLLM(task: string, envSummary?: string): Promise<SubTask[]> {
    const envBlock = envSummary
      ? `\nProject environment context:\n${envSummary}\n\nUse this to make subtask tools and commands specific to this environment.\n`
      : "";

    const messages: ChatMessage[] = [
      {
        role: "system",
        content: `You are a task planner. Break a complex task into ordered subtasks.
Each subtask must have a unique id (t1, t2, ...), description, tools list, dependsOn list, and expectedOutput.
${envBlock}
Available tools: web_fetch, web_search, read_file, write_file, shell, recall, remember, pellet_recall, send_file.

Output ONLY valid JSON array — no markdown fences, no extra text.
Max ${this.maxSubtasks} subtasks. If the task is simple, return just 1-2.`,
      },
      {
        role: "user",
        content: `Decompose this task into subtasks:

"${task.slice(0, 800)}"

Output JSON array of objects:
[
  {
    "id": "t1",
    "description": "...",
    "tools": ["web_search"],
    "dependsOn": [],
    "expectedOutput": "..."
  },
  ...
]`,
      },
    ];

    const result = await Promise.race([
      this.provider.chat(messages),
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("decompose timeout")), this.timeoutMs),
      ),
    ]);

    const cleaned = result.content.trim()
      .replace(/^```(?:json)?\s*/i, "")
      .replace(/\s*```$/, "");

    return JSON.parse(cleaned) as SubTask[];
  }

  // ─── Validation ──────────────────────────────────────────────

  private validate(raw: SubTask[], task: string): SubTask[] {
    if (!Array.isArray(raw) || raw.length === 0) {
      throw new Error("LLM returned empty or non-array decomposition");
    }

    const knownIds = new Set<string>();
    const validated: SubTask[] = [];

    for (const item of raw.slice(0, this.maxSubtasks)) {
      const id = String(item.id ?? `t${validated.length + 1}`).trim();
      knownIds.add(id);

      validated.push({
        id,
        description: String(item.description ?? task).trim(),
        tools: this.normalizeTools(item.tools),
        dependsOn: [],  // filled after all ids are known
        expectedOutput: String(item.expectedOutput ?? "task result").trim(),
      });
    }

    // Second pass: wire dependsOn (only allow known ids)
    for (let i = 0; i < validated.length; i++) {
      const rawItem = raw[i];
      if (Array.isArray(rawItem?.dependsOn)) {
        validated[i].dependsOn = rawItem.dependsOn
          .map(String)
          .filter((dep) => knownIds.has(dep) && dep !== validated[i].id);
      }
    }

    return validated;
  }

  private normalizeTools(rawTools: unknown): string[] {
    if (!Array.isArray(rawTools)) return ["recall"];
    const validTools = new Set(Object.values(DOMAIN_TOOLS).flat());
    return rawTools
      .map(String)
      .filter((t) => validTools.has(t))
      .slice(0, 5);
  }

  // ─── Parallel group builder ───────────────────────────────────

  /**
   * Topological sort → produce parallel groups.
   * Group 0 = tasks with no deps. Group 1 = tasks whose deps are all in group 0. Etc.
   */
  private buildParallelGroups(subtasks: SubTask[]): string[][] {
    const groups: string[][] = [];
    const assigned = new Map<string, number>(); // id → group index

    let remaining = [...subtasks];
    let iter = 0;

    while (remaining.length > 0 && iter < 20) {
      iter++;
      const ready = remaining.filter((t) =>
        t.dependsOn.every((dep) => assigned.has(dep)),
      );

      if (ready.length === 0) {
        // Circular dependency or error — push everything remaining as a single group
        groups.push(remaining.map((t) => t.id));
        break;
      }

      const groupIdx = groups.length;
      const groupIds = ready.map((t) => t.id);
      groups.push(groupIds);
      groupIds.forEach((id) => assigned.set(id, groupIdx));
      remaining = remaining.filter((t) => !assigned.has(t.id));
    }

    return groups;
  }

  // ─── Fallback ─────────────────────────────────────────────────

  private singleTaskFallback(task: string): DecompositionPlan {
    const subtask: SubTask = {
      id: "t1",
      description: task,
      tools: ["recall", "web_search", "read_file"],
      dependsOn: [],
      expectedOutput: "Task completed",
    };
    return {
      originalTask: task,
      subtasks: [subtask],
      parallelGroups: [["t1"]],
      totalSteps: 1,
    };
  }
}
