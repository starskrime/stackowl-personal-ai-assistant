/**
 * StackOwl — Background Worker (Phase 2)
 *
 * The core of the agentic loop. Runs every N minutes, picks the next
 * pending task from the goal queue, and executes it using the existing
 * OwlEngine — the same engine that handles user messages.
 *
 * Key design decisions:
 *   - Reuses OwlEngine directly — no new execution logic
 *   - Only executes low/medium-risk tasks that don't require approval
 *   - One task at a time — no concurrency headaches
 *   - Results stored as pellets + task.result
 *   - Queues a Telegram briefing when notable work is done
 *
 * Wired into ProactivePinger's existing timer infrastructure.
 */

import type { MemoryDatabase, AgentTask, AgentGoal } from "../memory/db.js";
import type { PelletStore } from "../pellets/store.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { EventBus } from "../events/bus.js";
import type { StackOwlConfig } from "../config/loader.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface WorkerContext {
  db: MemoryDatabase;
  pelletStore: PelletStore;
  provider: ModelProvider;
  owl: OwlInstance;
  toolRegistry: ToolRegistry;
  config: StackOwlConfig;
  eventBus?: EventBus;
  /** Channel + userId to send briefings to — set when Telegram is active */
  briefingTarget?: { channelId: string; userId: string };
}

// ─── BackgroundWorker ─────────────────────────────────────────────

export class BackgroundWorker {
  private busy = false;
  private completedSinceLastBriefing: Array<{ task: AgentTask; goal: AgentGoal }> = [];
  private lastBriefingAt = 0;

  /** Minimum time between briefings (30 minutes) */
  private static readonly BRIEFING_INTERVAL_MS = 30 * 60 * 1000;
  /** Max task execution time before we give up */
  private static readonly TASK_TIMEOUT_MS = 3 * 60 * 1000;
  /** Max attempts per task before marking as failed */
  private static readonly MAX_ATTEMPTS = 3;

  constructor(private ctx: WorkerContext) {}

  /**
   * Called by ProactivePinger on every timer tick.
   * Picks one pending task, executes it, stores the result.
   * Returns true if work was done.
   */
  async tick(): Promise<boolean> {
    if (this.busy) return false;
    this.busy = true;

    try {
      const task = this.ctx.db.agentTasks.nextPending();
      if (!task) return false;

      const goal = this.ctx.db.agentGoals.get(task.goalId);
      if (!goal) {
        this.ctx.db.agentTasks.markFailed(task.id, "Goal not found");
        return false;
      }

      // Abandon tasks with too many attempts
      if (task.attempts >= BackgroundWorker.MAX_ATTEMPTS) {
        this.ctx.db.agentTasks.markFailed(task.id, `Exceeded ${BackgroundWorker.MAX_ATTEMPTS} attempts`);
        return false;
      }

      log.engine.info(`[Worker] Executing task: "${task.description}" (goal: "${goal.title}")`);
      this.ctx.db.agentTasks.markRunning(task.id);
      this.ctx.db.agentGoals.updateStatus(goal.id, "active");

      const result = await this.executeTask(task, goal);

      this.ctx.db.agentTasks.markComplete(task.id, result);
      log.engine.info(`[Worker] Task complete: "${task.description}"`);

      // Update goal progress
      this.updateGoalProgress(goal);

      // Queue for briefing
      this.completedSinceLastBriefing.push({ task, goal });
      await this.maybeSendBriefing();

      return true;
    } catch (err) {
      log.engine.warn(`[Worker] Tick error: ${err instanceof Error ? err.message : err}`);
      return false;
    } finally {
      this.busy = false;
    }
  }

  /** Whether the worker is currently executing a task */
  get isBusy(): boolean {
    return this.busy;
  }

  // ─── Task Execution ───────────────────────────────────────────

  private async executeTask(task: AgentTask, goal: AgentGoal): Promise<string> {
    // Build the prompt that the engine will receive as the "user message"
    const enginePrompt = this.buildTaskPrompt(task, goal);

    // Import engine lazily to avoid circular deps
    const { OwlEngine } = await import("../engine/runtime.js");

    const engine = new OwlEngine();

    // Run with a timeout
    const engineRun = engine.run(enginePrompt, {
      provider: this.ctx.provider,
      owl: this.ctx.owl,
      config: this.ctx.config,
      pelletStore: this.ctx.pelletStore,
      toolRegistry: this.ctx.toolRegistry,
      sessionHistory: [],
      skipGapDetection: true,
      isolatedTask: true,
    });

    const timeoutPromise = new Promise<never>((_, reject) =>
      setTimeout(
        () => reject(new Error("Task execution timed out")),
        BackgroundWorker.TASK_TIMEOUT_MS,
      ),
    );

    const response = await Promise.race([engineRun, timeoutPromise]);
    return response.content.slice(0, 2000); // cap result size
  }

  private buildTaskPrompt(task: AgentTask, goal: AgentGoal): string {
    return [
      `[BACKGROUND TASK — no user is watching, work autonomously]`,
      ``,
      `Goal: ${goal.title}`,
      `Task: ${task.description}`,
      ``,
      `Execute this task completely using your available tools.`,
      `Store important findings as pellets using pellet_recall or by synthesizing knowledge.`,
      `When done, summarize what you found/did in 2-3 sentences.`,
      `Output ONLY the summary — no preamble, no "[DONE]" suffix.`,
    ].join("\n");
  }

  // ─── Goal Progress ────────────────────────────────────────────

  private updateGoalProgress(goal: AgentGoal): void {
    const tasks = this.ctx.db.agentTasks.forGoal(goal.id);
    const done = tasks.filter((t) => t.status === "complete").length;
    const total = tasks.length;
    const progress = total > 0 ? Math.round((done / total) * 100) : 0;

    if (done === total && total > 0) {
      this.ctx.db.agentGoals.updateStatus(goal.id, "complete", 100);
      log.engine.info(`[Worker] Goal complete: "${goal.title}"`);
    } else {
      this.ctx.db.agentGoals.updateProgress(goal.id, progress);
    }
  }

  // ─── Briefing ─────────────────────────────────────────────────

  private async maybeSendBriefing(): Promise<void> {
    if (this.completedSinceLastBriefing.length === 0) return;
    const now = Date.now();
    if (now - this.lastBriefingAt < BackgroundWorker.BRIEFING_INTERVAL_MS) return;
    if (!this.ctx.briefingTarget || !this.ctx.eventBus) return;

    const items = this.completedSinceLastBriefing.splice(0);
    this.lastBriefingAt = now;

    const lines = items.map(({ task, goal }) =>
      `• **${goal.title}**: ${task.result?.slice(0, 200) ?? "done"}`,
    );

    const briefing = [
      `🦉 *Background work completed:*`,
      ``,
      ...lines,
    ].join("\n");

    this.ctx.eventBus.emit("agent:ping_request", {
      prompt: briefing,
      type: "briefing",
    });

    log.engine.info(`[Worker] Briefing sent for ${items.length} completed task(s)`);
  }

  // ─── Status ───────────────────────────────────────────────────

  getStatus(): {
    pendingTasks: number;
    activeGoals: number;
    completedSinceBriefing: number;
  } {
    const goals = this.ctx.db.agentGoals.getActive();
    const next = this.ctx.db.agentTasks.nextPending();
    return {
      pendingTasks: next ? 1 : 0, // conservative — nextPending returns 1
      activeGoals: goals.length,
      completedSinceBriefing: this.completedSinceLastBriefing.length,
    };
  }
}
