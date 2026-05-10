/**
 * StackOwl — Autonomous Planner (Goal-Driven Scheduler)
 *
 * Replaces the hardcoded cron-style scheduler in ProactivePinger with
 * a priority-based decision loop driven by the GoalGraph.
 *
 * Instead of "run self-study at 2AM, consolidation at 3AM", the planner asks:
 *   - What is the highest-priority unfinished goal?
 *   - What can I do right now to advance it?
 *   - Is the user available to review?
 *   - Should I proactively learn something for tomorrow?
 *
 * The planner runs every N minutes and picks the single highest-impact
 * action from a priority queue of candidates.
 */

import type { GoalGraph } from "../goals/graph.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { TaskStore } from "../tasks/store.js";
import type { CapabilityScanner } from "./capability-scanner.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type ActionType =
  | "follow_up_stale_goal"
  | "advance_blocked_goal"
  | "self_study"
  | "memory_consolidation"
  | "check_in"
  | "morning_brief"
  | "explore_capabilities"
  | "anticipatory_research"
  | "review_tool_outcomes"
  | "goal_progress_update"
  | "none";

export interface PlannedAction {
  type: ActionType;
  priority: number; // 0-100, higher = more urgent
  description: string;
  /** Goal this action relates to (if any) */
  goalId?: string;
  /** Topic to study (for self_study) */
  topic?: string;
}

export interface PlannerConfig {
  /** Interval in minutes between planning cycles */
  intervalMinutes: number;
  /** Quiet hours start (24h) */
  quietHoursStart: number;
  /** Quiet hours end (24h) */
  quietHoursEnd: number;
  /** Minimum minutes between user-facing actions */
  minActionCooldownMinutes: number;
}

const DEFAULT_CONFIG: PlannerConfig = {
  intervalMinutes: 10,
  quietHoursStart: 22,
  quietHoursEnd: 7,
  minActionCooldownMinutes: 15,
};

// ─── Planner ─────────────────────────────────────────────────────

export interface PlannerDeps {
  goalGraph: GoalGraph;
  learningOrchestrator?: import("../learning/orchestrator.js").LearningOrchestrator;
  preferenceStore?: PreferenceStore;
  skillsRegistry?: SkillsRegistry;
  taskStore?: TaskStore;
  capabilityScanner?: CapabilityScanner;
  skillsDir?: string;
  db?: import("../memory/db.js").MemoryDatabase;
  onAction: (action: PlannedAction) => Promise<void>;
}

export class AutonomousPlanner {
  private config: PlannerConfig;
  private timer: NodeJS.Timeout | null = null;
  private lastActionTime: number = 0;
  private lastMorningBriefDate: string = "";
  private lastConsolidationDate: string = "";

  /** Track last user message time for idle detection */
  private lastUserMessageAt: number = Date.now();

  private get goalGraph(): GoalGraph {
    return this.deps.goalGraph;
  }

  constructor(
    private deps: PlannerDeps,
    config?: Partial<PlannerConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Call when a user message arrives to reset idle timer.
   */
  onUserActivity(): void {
    this.lastUserMessageAt = Date.now();
  }

  /**
   * Minutes since last user message.
   */
  private get idleMinutes(): number {
    return (Date.now() - this.lastUserMessageAt) / 60_000;
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  start(): void {
    log.engine.info(
      `[AutonomousPlanner] Started — checking every ${this.config.intervalMinutes} min`,
    );

    this.timer = setInterval(
      () =>
        this.planAndExecute().catch((err) => {
          log.engine.error(
            `[AutonomousPlanner] Error: ${err instanceof Error ? err.message : err}`,
          );
        }),
      this.config.intervalMinutes * 60 * 1000,
    );
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    log.engine.info(`[AutonomousPlanner] Stopped`);
  }

  // ─── Core ──────────────────────────────────────────────────────

  /**
   * Run one planning cycle: generate candidates, rank, execute the best.
   */
  async planAndExecute(): Promise<PlannedAction | null> {
    const now = Date.now();

    // Enforce cooldown
    const cooldownMs = this.config.minActionCooldownMinutes * 60 * 1000;
    if (now - this.lastActionTime < cooldownMs) return null;

    // Generate candidate actions
    const candidates = await this.generateCandidates();

    if (candidates.length === 0) return null;

    // Sort by priority (highest first)
    candidates.sort((a, b) => b.priority - a.priority);

    const best = candidates[0];
    if (best.type === "none") return null;

    log.engine.info(
      `[AutonomousPlanner] Selected action: ${best.type} (priority: ${best.priority}) — "${best.description}"`,
    );

    // Execute
    try {
      await this.deps.onAction(best);
      this.lastActionTime = now;
      return best;
    } catch (err) {
      log.engine.warn(
        `[AutonomousPlanner] Action failed: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
  }

  // ─── Candidate Generation ──────────────────────────────────────

  /**
   * Returns a data-driven priority score for a given action type.
   * Uses the reply rate from proactive_engagement (last 30 days, min 20 samples).
   * Falls back to basePriority on cold start.
   * Score is clamped to [basePriority - 20, basePriority + 20].
   */
  private async learnedPriority(type: ActionType, basePriority: number): Promise<number> {
    if (!this.deps.db) return basePriority;
    const stats = this.deps.db.getEngagementStats(type, { days: 30, minSamples: 20 });
    if (!stats) return basePriority;
    const learned = Math.round(stats.replyRate * 100);
    return Math.max(basePriority - 20, Math.min(basePriority + 20, learned));
  }

  private async generateCandidates(): Promise<PlannedAction[]> {
    const candidates: PlannedAction[] = [];
    const now = new Date();
    const hour = now.getHours();
    const isQuiet = this.isQuietHours(hour);
    const dateKey = now.toISOString().split("T")[0];

    await this.goalGraph.load();

    // ── 1. Stale goal follow-ups (highest priority during active hours) ──
    if (!isQuiet) {
      const staleGoals = this.goalGraph.getStale(5); // 5 days without mention
      for (const goal of staleGoals.slice(0, 2)) {
        const base = 80 - staleGoals.indexOf(goal) * 10;
        candidates.push({
          type: "follow_up_stale_goal",
          priority: await this.learnedPriority("follow_up_stale_goal", base),
          description: `Follow up on "${goal.title}" — not mentioned in ${Math.round((Date.now() - goal.lastActiveAt) / (1000 * 60 * 60 * 24))} days`,
          goalId: goal.id,
        });
      }
    }

    // ── 2. Blocked goals (can the owl help unblock?) ──
    if (!isQuiet) {
      const blocked = this.goalGraph.getBlocked();
      for (const goal of blocked.slice(0, 2)) {
        candidates.push({
          type: "advance_blocked_goal",
          priority: await this.learnedPriority("advance_blocked_goal", 70),
          description: `Help unblock "${goal.title}" — reason: ${goal.blockedReason ?? "unknown"}`,
          goalId: goal.id,
        });
      }
    }

    // ── 3. Morning brief (once per day, during morning window) ──
    if (
      !isQuiet &&
      hour >= 8 &&
      hour <= 10 &&
      this.lastMorningBriefDate !== dateKey
    ) {
      candidates.push({
        type: "morning_brief",
        priority: await this.learnedPriority("morning_brief", 90),
        description: "Deliver morning brief with goals status + agenda",
      });
      // Will be marked done after execution to prevent re-trigger
    }

    // ── 4. Self-study (any idle period, not just quiet hours) ──
    if (this.deps.learningOrchestrator && this.idleMinutes > 10) {
      candidates.push({
        type: "self_study",
        priority: await this.learnedPriority("self_study", isQuiet ? 50 : 40),
        description: "Proactive learning session — study queued topics",
      });
    }

    // ── 5. Memory consolidation (during quiet hours) ──
    if (isQuiet && this.lastConsolidationDate !== dateKey) {
      candidates.push({
        type: "memory_consolidation",
        priority: await this.learnedPriority("memory_consolidation", 50),
        description: "Consolidate daily memories and extract persistent facts",
      });
    }

    // ── 7. Goal-driven check-in (only when there's a specific stale goal) ──
    // Generic "what's on your plate?" check-ins are removed — they're noise.
    // Only check in when there's a concrete goal that has gone stale.
    if (!isQuiet) {
      const topGoal = this.goalGraph.getTopPriority();
      if (topGoal && topGoal.progress < 100) {
        const hoursSinceActive =
          (Date.now() - topGoal.lastActiveAt) / (1000 * 60 * 60);
        // Only check in if the goal has been idle for > 4 hours
        if (hoursSinceActive > 4) {
          candidates.push({
            type: "check_in",
            priority: await this.learnedPriority("check_in", 20),
            description: `Check in — stale goal: "${topGoal.title}" (${topGoal.progress}%, idle ${Math.round(hoursSinceActive)}h)`,
            goalId: topGoal.id,
          });
        }
      }
    }

    // ── 8. Capability exploration → find unused platform features ──
    if (this.deps.capabilityScanner && this.idleMinutes > 15) {
      candidates.push({
        type: "explore_capabilities",
        priority: await this.learnedPriority("explore_capabilities", 45),
        description:
          "Scan platform config for unused adapters, tools, and MCP servers",
      });
    }

    // ── 10. Anticipatory research → pre-study likely topics ──
    if (this.deps.learningOrchestrator && this.idleMinutes > 5) {
      candidates.push({
        type: "anticipatory_research",
        priority: await this.learnedPriority("anticipatory_research", 35),
        description: "Pre-research topics the user is likely to ask about next",
      });
    }

    // ── 11. Tool outcome review → identify failing tool patterns ──
    if (this.idleMinutes > 20) {
      candidates.push({
        type: "review_tool_outcomes",
        priority: await this.learnedPriority("review_tool_outcomes", 25),
        description:
          "Analyze tool success/failure patterns and identify improvements",
      });
    }

    return candidates;
  }

  // ─── Helpers ───────────────────────────────────────────────────

  private isQuietHours(hour: number): boolean {
    if (this.deps.preferenceStore) {
      return this.deps.preferenceStore.isQuietHours(
        this.config.quietHoursStart,
        this.config.quietHoursEnd,
      );
    }
    if (this.config.quietHoursStart > this.config.quietHoursEnd) {
      return (
        hour >= this.config.quietHoursStart || hour < this.config.quietHoursEnd
      );
    }
    return (
      hour >= this.config.quietHoursStart && hour < this.config.quietHoursEnd
    );
  }

  /**
   * Notify the planner that a morning brief was sent.
   */
  markMorningBriefDone(): void {
    this.lastMorningBriefDate = new Date().toISOString().split("T")[0];
  }

  /**
   * Notify the planner that memory consolidation ran.
   */
  markConsolidationDone(): void {
    this.lastConsolidationDate = new Date().toISOString().split("T")[0];
  }
}
