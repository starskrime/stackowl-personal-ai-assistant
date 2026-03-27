/**
 * StackOwl — Idle Activity Engine
 *
 * Fills the gap when the assistant sits idle. Instead of waiting for
 * quiet hours to learn, the engine detects idle periods (no user
 * messages for >5 minutes) and runs productive background activities:
 *
 *   1. Pattern Mining — crystallize new skills from conversation history
 *   2. Capability Exploration — research unused adapters/tools
 *   3. Anticipatory Research — pre-research topics the user is likely
 *      to ask about based on MicroLearner signals
 *   4. Tool Outcome Review — analyze which tools are failing and why
 *   5. Knowledge Refresh — re-study topics where the owl is uncertain
 *
 * Activities run at lower priority than user requests. If a user
 * message arrives, idle activities are paused immediately.
 */

import type { StackOwlConfig } from "../config/loader.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { PatternMiner } from "../skills/pattern-miner.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { MicroLearner } from "../learning/micro-learner.js";
import type { ToolOutcomeStore } from "../tools/outcome-store.js";
import { CapabilityScanner } from "./capability-scanner.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export type IdleActivity =
  | "pattern_mining"
  | "capability_exploration"
  | "anticipatory_research"
  | "tool_outcome_review"
  | "knowledge_refresh";

export interface IdleActivityResult {
  activity: IdleActivity;
  description: string;
  success: boolean;
  durationMs: number;
  artifacts: string[]; // e.g., new skill names, pellet titles
}

export interface IdleEngineConfig {
  /** Minutes of inactivity before starting idle work */
  idleThresholdMinutes: number;
  /** Minutes between idle activity cycles */
  cycleLengthMinutes: number;
  /** Maximum activities per cycle */
  maxActivitiesPerCycle: number;
  /** Enable/disable specific activities */
  enabled: {
    patternMining: boolean;
    capabilityExploration: boolean;
    anticipatoryResearch: boolean;
    toolOutcomeReview: boolean;
    knowledgeRefresh: boolean;
  };
}

const DEFAULT_IDLE_CONFIG: IdleEngineConfig = {
  idleThresholdMinutes: 5,
  cycleLengthMinutes: 10,
  maxActivitiesPerCycle: 2,
  enabled: {
    patternMining: true,
    capabilityExploration: true,
    anticipatoryResearch: true,
    toolOutcomeReview: true,
    knowledgeRefresh: true,
  },
};

// ─── Idle Engine ─────────────────────────────────────────────────

export class IdleActivityEngine {
  private config: IdleEngineConfig;
  private lastUserMessageAt: number = Date.now();
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private lastActivities: Map<IdleActivity, number> = new Map();
  private results: IdleActivityResult[] = [];

  constructor(
    private appConfig: StackOwlConfig,
    private deps: {
      learningEngine?: LearningEngine;
      patternMiner?: PatternMiner;
      skillsRegistry?: SkillsRegistry;
      microLearner?: MicroLearner;
      toolOutcomeStore?: ToolOutcomeStore;
      capabilityScanner?: CapabilityScanner;
      /** Callback when an idle activity produces something user-relevant */
      onResult?: (result: IdleActivityResult) => void;
    },
    config?: Partial<IdleEngineConfig>,
  ) {
    this.config = { ...DEFAULT_IDLE_CONFIG, ...config };
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  start(): void {
    log.engine.info(
      `[IdleEngine] Started — idle threshold: ${this.config.idleThresholdMinutes}min, ` +
        `cycle: ${this.config.cycleLengthMinutes}min`,
    );

    this.timer = setInterval(
      () =>
        this.tick().catch((err) => {
          log.engine.warn(
            `[IdleEngine] Tick failed: ${err instanceof Error ? err.message : err}`,
          );
        }),
      60_000, // Check every minute
    );
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.running = false;
    log.engine.info("[IdleEngine] Stopped");
  }

  /**
   * Call when a user message is received to reset the idle timer.
   */
  onUserActivity(): void {
    this.lastUserMessageAt = Date.now();
    this.running = false; // Cancel any in-progress idle work
  }

  /**
   * Is the user currently idle?
   */
  isIdle(): boolean {
    const idleMs = Date.now() - this.lastUserMessageAt;
    return idleMs > this.config.idleThresholdMinutes * 60_000;
  }

  /**
   * Get recent idle activity results.
   */
  getRecentResults(limit: number = 10): IdleActivityResult[] {
    return this.results.slice(-limit);
  }

  // ─── Core Tick ─────────────────────────────────────────────────

  private async tick(): Promise<void> {
    if (!this.isIdle()) return;
    if (this.running) return; // Already running an activity

    this.running = true;

    try {
      // Pick the highest priority activity we haven't run recently
      const activity = this.pickNextActivity();
      if (!activity) return;

      const startTime = Date.now();
      log.engine.info(`[IdleEngine] Starting idle activity: ${activity}`);

      const result = await this.runActivity(activity);
      result.durationMs = Date.now() - startTime;

      this.results.push(result);
      if (this.results.length > 50) {
        this.results = this.results.slice(-50);
      }

      this.lastActivities.set(activity, Date.now());

      if (result.success && result.artifacts.length > 0) {
        log.engine.info(
          `[IdleEngine] ✓ ${activity}: ${result.description} (${result.durationMs}ms)`,
        );
        if (this.deps.onResult) {
          this.deps.onResult(result);
        }
      }
    } finally {
      this.running = false;
    }
  }

  // ─── Activity Selection ────────────────────────────────────────

  private pickNextActivity(): IdleActivity | null {
    const now = Date.now();
    const cycleMs = this.config.cycleLengthMinutes * 60_000;

    const candidates: Array<{ activity: IdleActivity; score: number }> = [];

    // Pattern mining — highest value, creates new skills
    if (this.config.enabled.patternMining && this.deps.patternMiner) {
      const lastRun = this.lastActivities.get("pattern_mining") ?? 0;
      if (now - lastRun > cycleMs * 3) {
        // Run every 3 cycles
        candidates.push({ activity: "pattern_mining", score: 80 });
      }
    }

    // Capability exploration — find unused platform features
    if (
      this.config.enabled.capabilityExploration &&
      this.deps.capabilityScanner
    ) {
      const lastRun = this.lastActivities.get("capability_exploration") ?? 0;
      if (now - lastRun > cycleMs * 5) {
        // Run every 5 cycles
        candidates.push({ activity: "capability_exploration", score: 60 });
      }
    }

    // Anticipatory research — pre-study likely topics
    if (this.config.enabled.anticipatoryResearch && this.deps.learningEngine) {
      const lastRun = this.lastActivities.get("anticipatory_research") ?? 0;
      if (now - lastRun > cycleMs) {
        candidates.push({ activity: "anticipatory_research", score: 50 });
      }
    }

    // Tool outcome review
    if (this.config.enabled.toolOutcomeReview && this.deps.toolOutcomeStore) {
      const lastRun = this.lastActivities.get("tool_outcome_review") ?? 0;
      if (now - lastRun > cycleMs * 2) {
        candidates.push({ activity: "tool_outcome_review", score: 40 });
      }
    }

    // Knowledge refresh
    if (this.config.enabled.knowledgeRefresh && this.deps.learningEngine) {
      const lastRun = this.lastActivities.get("knowledge_refresh") ?? 0;
      if (now - lastRun > cycleMs * 2) {
        candidates.push({ activity: "knowledge_refresh", score: 30 });
      }
    }

    if (candidates.length === 0) return null;

    candidates.sort((a, b) => b.score - a.score);
    return candidates[0].activity;
  }

  // ─── Activity Runners ──────────────────────────────────────────

  private async runActivity(
    activity: IdleActivity,
  ): Promise<IdleActivityResult> {
    switch (activity) {
      case "pattern_mining":
        return await this.runPatternMining();
      case "capability_exploration":
        return await this.runCapabilityExploration();
      case "anticipatory_research":
        return await this.runAnticipatoryResearch();
      case "tool_outcome_review":
        return await this.runToolOutcomeReview();
      case "knowledge_refresh":
        return await this.runKnowledgeRefresh();
    }
  }

  private async runPatternMining(): Promise<IdleActivityResult> {
    if (!this.deps.patternMiner || !this.deps.skillsRegistry) {
      return this.emptyResult("pattern_mining", "Missing dependencies");
    }

    try {
      const skillsDirs = this.appConfig.skills?.directories ?? [];
      const skillsDir = skillsDirs[0] ?? "./workspace/skills";
      const newSkills = await this.deps.patternMiner.mine(
        this.deps.skillsRegistry,
        skillsDir,
      );

      return {
        activity: "pattern_mining",
        description:
          newSkills.length > 0
            ? `Crystallized ${newSkills.length} new skill(s): ${newSkills.join(", ")}`
            : "No new patterns found to crystallize",
        success: true,
        durationMs: 0,
        artifacts: newSkills,
      };
    } catch (err) {
      return this.emptyResult(
        "pattern_mining",
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private async runCapabilityExploration(): Promise<IdleActivityResult> {
    if (!this.deps.capabilityScanner) {
      return this.emptyResult("capability_exploration", "No scanner available");
    }

    try {
      const result = this.deps.capabilityScanner.scan();
      const topGaps = result.gaps.slice(0, 3);

      return {
        activity: "capability_exploration",
        description:
          topGaps.length > 0
            ? `Found ${result.gaps.length} capability gaps. Top: ${topGaps.map((g) => g.name).join(", ")}`
            : "No capability gaps detected — platform is well-utilized",
        success: true,
        durationMs: 0,
        artifacts: topGaps.map((g) => `${g.type}:${g.name}`),
      };
    } catch (err) {
      return this.emptyResult(
        "capability_exploration",
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private async runAnticipatoryResearch(): Promise<IdleActivityResult> {
    if (!this.deps.learningEngine) {
      return this.emptyResult("anticipatory_research", "No learning engine");
    }

    try {
      const result = await this.deps.learningEngine.runStudySession(2);
      return {
        activity: "anticipatory_research",
        description:
          result.studied.length > 0
            ? `Studied ${result.studied.length} topic(s): ${result.studied.join(", ")}. ${result.pelletsCreated} pellet(s) created.`
            : "Study queue empty — no topics to research",
        success: true,
        durationMs: 0,
        artifacts: result.studied,
      };
    } catch (err) {
      return this.emptyResult(
        "anticipatory_research",
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private async runToolOutcomeReview(): Promise<IdleActivityResult> {
    if (!this.deps.toolOutcomeStore) {
      return this.emptyResult("tool_outcome_review", "No outcome store");
    }

    try {
      const patterns = this.deps.toolOutcomeStore.getTopPatterns(5);
      const lowSuccess = patterns.filter((p) => p.successRate < 0.5);

      return {
        activity: "tool_outcome_review",
        description:
          lowSuccess.length > 0
            ? `Found ${lowSuccess.length} underperforming tool pattern(s): ${lowSuccess.map((p) => `${p.requestType} (${(p.successRate * 100).toFixed(0)}%)`).join(", ")}`
            : `All ${patterns.length} tool patterns performing well`,
        success: true,
        durationMs: 0,
        artifacts: lowSuccess.map((p) => p.requestType),
      };
    } catch (err) {
      return this.emptyResult(
        "tool_outcome_review",
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private async runKnowledgeRefresh(): Promise<IdleActivityResult> {
    if (!this.deps.learningEngine) {
      return this.emptyResult("knowledge_refresh", "No learning engine");
    }

    try {
      const result = await this.deps.learningEngine.runStudySession(1);
      return {
        activity: "knowledge_refresh",
        description:
          result.studied.length > 0
            ? `Refreshed knowledge on: ${result.studied.join(", ")}`
            : "Nothing to refresh",
        success: true,
        durationMs: 0,
        artifacts: result.studied,
      };
    } catch (err) {
      return this.emptyResult(
        "knowledge_refresh",
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  private emptyResult(
    activity: IdleActivity,
    description: string,
  ): IdleActivityResult {
    return {
      activity,
      description,
      success: false,
      durationMs: 0,
      artifacts: [],
    };
  }
}
