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
import type { LearningEngine } from "../learning/self-study.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { TaskStore } from "../tasks/store.js";
import type { PatternMiner } from "../skills/pattern-miner.js";
import type { CapabilityScanner } from "./capability-scanner.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type ActionType =
  | "follow_up_stale_goal"
  | "advance_blocked_goal"
  | "self_study"
  | "skill_evolution"
  | "memory_consolidation"
  | "check_in"
  | "morning_brief"
  | "mine_patterns"
  | "explore_capabilities"
  | "anticipatory_research"
  | "review_tool_outcomes"
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

export class AutonomousPlanner {
  private config: PlannerConfig;
  private timer: NodeJS.Timeout | null = null;
  private lastActionTime: number = 0;
  private lastMorningBriefDate: string = "";
  private lastConsolidationDate: string = "";

  /** Track last user message time for idle detection */
  private lastUserMessageAt: number = Date.now();

  constructor(
    private goalGraph: GoalGraph,
    private deps: {
      learningEngine?: LearningEngine;
      preferenceStore?: PreferenceStore;
      skillsRegistry?: SkillsRegistry;
      taskStore?: TaskStore;
      patternMiner?: PatternMiner;
      capabilityScanner?: CapabilityScanner;
      skillsDir?: string;
      onAction: (action: PlannedAction) => Promise<void>;
    },
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
        candidates.push({
          type: "follow_up_stale_goal",
          priority: 80 - staleGoals.indexOf(goal) * 10,
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
          priority: 70,
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
        priority: 90, // High priority during morning window
        description: "Deliver morning brief with goals status + agenda",
      });
      // Will be marked done after execution to prevent re-trigger
    }

    // ── 4. Self-study (any idle period, not just quiet hours) ──
    if (this.deps.learningEngine && this.idleMinutes > 10) {
      candidates.push({
        type: "self_study",
        priority: isQuiet ? 50 : 40, // Higher priority during quiet hours
        description: "Proactive learning session — study queued topics",
      });
    }

    // ── 5. Skill evolution (during quiet hours, low priority) ──
    if (isQuiet && this.deps.skillsRegistry) {
      candidates.push({
        type: "skill_evolution",
        priority: 30,
        description: "Evolve and improve existing skills",
      });
    }

    // ── 6. Memory consolidation (during quiet hours) ──
    if (isQuiet && this.lastConsolidationDate !== dateKey) {
      candidates.push({
        type: "memory_consolidation",
        priority: 50,
        description: "Consolidate daily memories and extract persistent facts",
      });
    }

    // ── 7. General check-in (low priority during active hours) ──
    if (!isQuiet) {
      const topGoal = this.goalGraph.getTopPriority();
      candidates.push({
        type: "check_in",
        priority: 20,
        description: topGoal
          ? `Check in — top goal: "${topGoal.title}" (${topGoal.progress}%)`
          : "General check-in",
        goalId: topGoal?.id,
      });
    }

    // ── 8. Pattern mining → crystallize new skills ──
    if (
      this.deps.patternMiner &&
      this.deps.skillsRegistry &&
      this.idleMinutes > 10
    ) {
      candidates.push({
        type: "mine_patterns",
        priority: 60,
        description:
          "Mine conversation patterns and crystallize into new skills",
      });
    }

    // ── 9. Capability exploration → find unused platform features ──
    if (this.deps.capabilityScanner && this.idleMinutes > 15) {
      candidates.push({
        type: "explore_capabilities",
        priority: 45,
        description:
          "Scan platform config for unused adapters, tools, and MCP servers",
      });
    }

    // ── 10. Anticipatory research → pre-study likely topics ──
    if (this.deps.learningEngine && this.idleMinutes > 5) {
      candidates.push({
        type: "anticipatory_research",
        priority: 35,
        description: "Pre-research topics the user is likely to ask about next",
      });
    }

    // ── 11. Tool outcome review → identify failing tool patterns ──
    if (this.idleMinutes > 20) {
      candidates.push({
        type: "review_tool_outcomes",
        priority: 25,
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
