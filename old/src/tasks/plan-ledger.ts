/**
 * StackOwl — Plan Ledger
 *
 * Persists PLANNED and SWARM execution state across sessions.
 *
 * Problem solved: PLANNED/SWARM strategies decompose tasks into subtasks and
 * run them in waves, but the state is held entirely in memory. If the user
 * disconnects, closes the app, or a new session starts before execution
 * finishes, all progress is lost and the next conversation starts from zero.
 *
 * The PlanLedger writes a plan record at task-start and updates each step
 * as it completes. On session start, the gateway checks for incomplete plans
 * for the current user and offers to resume rather than re-plan.
 *
 * Storage: JSON files in {workspace}/plans/ — one file per plan.
 * Simple, durable, no extra dependencies (same approach as TaskStore).
 */

import { mkdir, readFile, writeFile, readdir, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";
import type { SubTask } from "../orchestrator/types.js";

// ─── Types ───────────────────────────────────────────────────────

export type PlanStatus = "running" | "complete" | "failed" | "abandoned";

export interface PlanStepRecord {
  stepId: number;
  description: string;
  assignedOwl: string;
  status: "pending" | "running" | "done" | "failed";
  /** First 400 chars of the step result (for resume context) */
  resultPreview?: string;
  startedAt?: number;
  completedAt?: number;
}

export interface PlanRecord {
  /** Unique plan ID */
  planId: string;
  /** User this plan belongs to */
  userId: string;
  /** Session that created this plan */
  sessionId: string;
  /** Original user request */
  goal: string;
  /** Which strategy produced this plan */
  strategy: "PLANNED" | "SWARM";
  /** Number of execution waves */
  totalWaves: number;
  /** Which wave we were on when last written */
  currentWave: number;
  /** All step records */
  steps: PlanStepRecord[];
  /** Overall plan status */
  status: PlanStatus;
  /** Final synthesis result (populated on complete) */
  synthesisPreview?: string;
  createdAt: number;
  updatedAt: number;
}

// ─── Plan Ledger ─────────────────────────────────────────────────

const RETENTION_MS = 14 * 24 * 60 * 60 * 1000; // 14 days

export class PlanLedger {
  private plansDir: string;
  private cache: Map<string, PlanRecord> = new Map();
  private initialized = false;

  constructor(workspacePath: string) {
    this.plansDir = join(workspacePath, "plans");
  }

  // ─── Lifecycle ───────────────────────────────────────────────

  async init(): Promise<void> {
    if (this.initialized) return;
    if (!existsSync(this.plansDir)) {
      await mkdir(this.plansDir, { recursive: true });
    }
    await this.loadAll();
    this.initialized = true;
  }

  // ─── Write operations ────────────────────────────────────────

  /**
   * Create a new plan record at the start of a PLANNED/SWARM execution.
   * Returns the planId to pass through to subsequent updates.
   */
  async createPlan(params: {
    userId: string;
    sessionId: string;
    goal: string;
    strategy: "PLANNED" | "SWARM";
    subtasks: SubTask[];
    totalWaves: number;
  }): Promise<string> {
    const planId = `plan_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const now = Date.now();

    const record: PlanRecord = {
      planId,
      userId: params.userId,
      sessionId: params.sessionId,
      goal: params.goal,
      strategy: params.strategy,
      totalWaves: params.totalWaves,
      currentWave: 0,
      steps: params.subtasks.map((t) => ({
        stepId: t.id,
        description: t.description,
        assignedOwl: t.assignedOwl,
        status: "pending",
      })),
      status: "running",
      createdAt: now,
      updatedAt: now,
    };

    this.cache.set(planId, record);
    await this.persist(record);

    log.engine.info(
      `[PlanLedger] Created plan ${planId} for user ${params.userId}: ` +
      `"${params.goal.slice(0, 60)}" (${params.subtasks.length} steps, ${params.totalWaves} waves)`,
    );

    return planId;
  }

  /**
   * Mark a single step as done with its result.
   */
  async markStepDone(
    planId: string,
    stepId: number,
    result: string,
  ): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    const step = plan.steps.find((s) => s.stepId === stepId);
    if (step) {
      step.status = "done";
      step.resultPreview = result.slice(0, 400);
      step.completedAt = Date.now();
    }

    plan.updatedAt = Date.now();
    await this.persist(plan);
  }

  /**
   * Mark a step as failed.
   */
  async markStepFailed(planId: string, stepId: number): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    const step = plan.steps.find((s) => s.stepId === stepId);
    if (step) {
      step.status = "failed";
      step.completedAt = Date.now();
    }

    plan.updatedAt = Date.now();
    await this.persist(plan);
  }

  /**
   * Advance to the next wave.
   */
  async advanceWave(planId: string, waveIndex: number): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    plan.currentWave = waveIndex;
    plan.updatedAt = Date.now();
    await this.persist(plan);
  }

  /**
   * Mark the plan as complete with a synthesis preview.
   */
  async completePlan(planId: string, synthesisPreview: string): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    plan.status = "complete";
    plan.synthesisPreview = synthesisPreview.slice(0, 500);
    plan.updatedAt = Date.now();
    await this.persist(plan);

    log.engine.info(`[PlanLedger] Plan ${planId} completed.`);
  }

  /**
   * Mark the plan as failed.
   */
  async failPlan(planId: string): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    plan.status = "failed";
    plan.updatedAt = Date.now();
    await this.persist(plan);
  }

  /**
   * User chose not to resume — mark as abandoned.
   */
  async abandonPlan(planId: string): Promise<void> {
    const plan = this.cache.get(planId);
    if (!plan) return;

    plan.status = "abandoned";
    plan.updatedAt = Date.now();
    await this.persist(plan);
    log.engine.info(`[PlanLedger] Plan ${planId} abandoned by user.`);
  }

  // ─── Read operations ─────────────────────────────────────────

  /**
   * Get all running plans for a user (across all sessions).
   * These are candidates for resume.
   */
  getRunningPlans(userId: string): PlanRecord[] {
    return [...this.cache.values()]
      .filter((p) => p.userId === userId && p.status === "running")
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }

  /**
   * Get a single plan by ID.
   */
  get(planId: string): PlanRecord | undefined {
    return this.cache.get(planId);
  }

  /**
   * Build a human-readable resume summary for a plan.
   * Used to tell the user "hey, you had this in progress..."
   */
  buildResumeSummary(plan: PlanRecord): string {
    const done = plan.steps.filter((s) => s.status === "done").length;
    const total = plan.steps.length;
    const pct = Math.round((done / total) * 100);
    const pendingSteps = plan.steps
      .filter((s) => s.status === "pending")
      .slice(0, 3)
      .map((s) => `• ${s.description.slice(0, 70)}`)
      .join("\n");

    const age = Date.now() - plan.createdAt;
    const ageStr =
      age < 60_000
        ? "just now"
        : age < 3_600_000
        ? `${Math.round(age / 60_000)}m ago`
        : age < 86_400_000
        ? `${Math.round(age / 3_600_000)}h ago`
        : `${Math.round(age / 86_400_000)}d ago`;

    return (
      `📋 **Interrupted task** (${ageStr}, ${pct}% done — ${done}/${total} steps)\n` +
      `Goal: _${plan.goal.slice(0, 100)}_\n` +
      (pendingSteps ? `\nRemaining:\n${pendingSteps}` : "")
    );
  }

  /**
   * Build the prior results context for resuming — passes done-step results
   * into the wave executor so it doesn't redo work.
   */
  buildResumeContext(plan: PlanRecord): Map<number, string> {
    const ctx = new Map<number, string>();
    for (const step of plan.steps) {
      if (step.status === "done" && step.resultPreview) {
        ctx.set(step.stepId, step.resultPreview);
      }
    }
    return ctx;
  }

  // ─── Cleanup ─────────────────────────────────────────────────

  async cleanup(): Promise<number> {
    const cutoff = Date.now() - RETENTION_MS;
    let removed = 0;

    for (const [id, plan] of this.cache) {
      if (
        (plan.status === "complete" ||
          plan.status === "failed" ||
          plan.status === "abandoned") &&
        plan.updatedAt < cutoff
      ) {
        this.cache.delete(id);
        await unlink(join(this.plansDir, `${id}.json`)).catch(() => {});
        removed++;
      }
    }

    return removed;
  }

  // ─── Persistence ─────────────────────────────────────────────

  private async persist(plan: PlanRecord): Promise<void> {
    try {
      await writeFile(
        join(this.plansDir, `${plan.planId}.json`),
        JSON.stringify(plan, null, 2),
        "utf-8",
      );
    } catch (err) {
      log.engine.warn(
        `[PlanLedger] Failed to persist plan ${plan.planId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  private async loadAll(): Promise<void> {
    if (!existsSync(this.plansDir)) return;

    try {
      const files = await readdir(this.plansDir);
      for (const file of files) {
        if (!file.endsWith(".json")) continue;
        try {
          const data = await readFile(join(this.plansDir, file), "utf-8");
          const plan = JSON.parse(data) as PlanRecord;
          // Rehydrate "running" plans that are stale (process died mid-execution).
          // Plans that were "running" at load time must have been abandoned by a crash.
          // Keep them as "running" so resume detection works — don't auto-fail them.
          this.cache.set(plan.planId, plan);
        } catch {
          // Corrupted file — skip silently
        }
      }
    } catch {
      // Directory read failed — non-fatal
    }
  }
}
