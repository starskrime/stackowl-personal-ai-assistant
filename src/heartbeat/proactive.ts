/**
 * StackOwl — Proactive Pinger
 *
 * Makes Noctua feel alive — she proactively reaches out to the user
 * with reminders, morning briefs, ideas, and follow-ups.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { OwlEngine } from "../engine/runtime.js";
import { MemoryConsolidator } from "./consolidation.js";
import { ToolPruner } from "../evolution/pruner.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
// SkillEvolver and PatternMiner imports removed — proactive learning disabled
import type { SkillsRegistry } from "../skills/registry.js";
import type { SessionStore } from "../memory/store.js";
import type { AutonomousPlanner } from "./planner.js";

// ─── Types ───────────────────────────────────────────────────────

export interface PingConfig {
  /** Enable/disable proactive pinging */
  enabled: boolean;
  /** Interval in minutes between periodic check-ins */
  checkInIntervalMinutes: number;
  /** Enable morning brief */
  morningBrief: boolean;
  /** Morning brief hour (24h format) */
  morningBriefHour: number;
  /** Quiet hours — no pings during these hours */
  quietHoursStart: number;
  quietHoursEnd: number;
}

export interface PingContext {
  provider: ModelProvider;
  owl: OwlInstance;
  config: StackOwlConfig;
  capabilityLedger: CapabilityLedger;
  toolRegistry?: ToolRegistry;
  /** Callback to send a message to the user */
  sendToUser: (message: string) => Promise<void>;
  /** Get recent session history for context */
  getRecentHistory?: () => ChatMessage[];
  /** The user ID to run consolidation for */
  userId?: string;
  /** Learning engine for proactive self-study sessions */
  learningEngine?: LearningEngine;
  /** New unified learning orchestrator (TopicFusion + Synthesis + Reflexion) */
  learningOrchestrator?: LearningOrchestrator;
  /** User preference store — used to check dynamic quiet hours */
  preferenceStore?: PreferenceStore;
  /** Reflexion engine for extracting rules from past failures */
  reflexionEngine?: ReflexionEngine;
  /** Skills registry for evolution pass */
  skillsRegistry?: SkillsRegistry;
  /** Absolute path to skills directory (for PatternMiner crystallization) */
  skillsDir?: string;
  /** Session store used by PatternMiner to read conversation history */
  sessionStore?: SessionStore;
  /** Episodic memory — used to give proactive messages cross-session context */
  episodicMemory?: import("../memory/episodic.js").EpisodicMemory;
  /** Knowledge Council for automated group learning sessions */
  knowledgeCouncil?: import("../parliament/knowledge-council.js").KnowledgeCouncil;
  /** Owl registry for council sessions */
  owlRegistry?: import("../owls/registry.js").OwlRegistry;
  /** Goal graph for checking stale goals */
  goalGraph?: import("../goals/graph.js").GoalGraph;
  /** Proactive intention loop — returns prioritized proactive items */
  proactiveLoop?: import("../intent/proactive-loop.js").ProactiveIntentionLoop;
  /** Autonomous planner — priority-based action scheduler driven by GoalGraph */
  autonomousPlanner?: AutonomousPlanner;
}

export type PingType =
  | "morning_brief"
  | "check_in"
  | "reminder"
  | "idea"
  | "follow_up";

// ─── Default Config ──────────────────────────────────────────────

const DEFAULT_PING_CONFIG: PingConfig = {
  enabled: true,
  checkInIntervalMinutes: 20, // base interval — actual timing is randomized ±50%
  morningBrief: true,
  morningBriefHour: 9,
  quietHoursStart: 22,
  quietHoursEnd: 7,
};

// Minimum time between ANY two pings (prevents spam even with short intervals)
const MIN_PING_COOLDOWN_MS = 60 * 60 * 1000; // 1 hour

// Stop sending check-ins after this many unanswered pings.
// Resets when the user sends a message (via notifyUserActivity).
const MAX_UNANSWERED_PINGS = 1;

// ─── Proactive Pinger ────────────────────────────────────────────

export class ProactivePinger {
  private config: PingConfig;
  private context: PingContext;
  private engine: OwlEngine;
  private timers: NodeJS.Timeout[] = [];
  private lastPingTime: number = 0;
  private lastMorningBriefDate: string = "";
  private lastConsolidationDate: string = "";
  private lastSelfStudyDate: string = "";
  // lastDreamTime and lastSkillEvolutionDate removed — proactive learning disabled
  private unansweredPings: number = 0;

  constructor(context: PingContext, config?: Partial<PingConfig>) {
    this.config = { ...DEFAULT_PING_CONFIG, ...config };
    this.context = context;
    this.engine = new OwlEngine();
  }

  /**
   * Start the proactive pinging system.
   */
  start(): void {
    if (!this.config.enabled) return;

    // Clear any existing timers before starting new ones
    this.stop();

    console.log("[ProactivePinger] 🔔 Proactive pinging started");

    // Unified tick: try the AutonomousPlanner first (if available).
    // If the planner executes an action, skip manual check-in and morning brief
    // for this tick to avoid duplicate or redundant pings.
    const unifiedTimer = setInterval(() => {
      this.tickPlannerThenManual().catch((err) => {
        console.error("[ProactivePinger] Unified tick error:", err);
      });
    }, 60 * 1000);
    this.timers.push(unifiedTimer);

    // 🧠 Daily Memory Consolidation timer
    const consolidationTimer = setInterval(() => {
      this.maybeConsolidateMemory().catch((err) => {
        console.error("[ProactivePinger] Memory consolidation error:", err);
      });
    }, 60 * 1000);
    this.timers.push(consolidationTimer);

    // 🧹 Autonomous Tool Pruning timer
    const pruningTimer = setInterval(() => {
      this.maybePruneTools().catch((err) => {
        console.error("[ProactivePinger] Tool pruning error:", err);
      });
    }, 60 * 1000);
    this.timers.push(pruningTimer);

    // 🧠 Proactive Self-Study timer
    const selfStudyTimer = setInterval(() => {
      this.maybeSelfStudy().catch((err) => {
        console.error("[ProactivePinger] Self-study error:", err);
      });
    }, 60 * 1000);
    this.timers.push(selfStudyTimer);

    // 🏛️ Knowledge Council — automated group learning sessions
    const councilTimer = setInterval(() => {
      this.maybeKnowledgeCouncil().catch((err) => {
        console.error("[ProactivePinger] Knowledge Council error:", err);
      });
    }, 60 * 1000);
    this.timers.push(councilTimer);

    // 🧠 Idle-Time Dreaming (Reflexion) timer
    const dreamTimer = setInterval(() => {
      this.maybeDream().catch((err) => {
        console.error("[ProactivePinger] Dream error:", err);
      });
    }, 60 * 1000);
    this.timers.push(dreamTimer);

    // 🌱 Skill Evolution + Pattern Mining timer (5 AM)
    const skillEvoTimer = setInterval(() => {
      this.maybeEvolveSkills().catch((err) => {
        console.error("[ProactivePinger] Skill evolution error:", err);
      });
    }, 60 * 1000);
    this.timers.push(skillEvoTimer);

    // No startup greeting — avoids spamming "what's on your plate" on every restart
  }

  /**
   * Call when the user sends a message. Resets the unanswered ping counter
   * so check-ins resume after the user re-engages.
   */
  notifyUserActivity(): void {
    this.unansweredPings = 0;
    this.context.autonomousPlanner?.onUserActivity();
  }

  /**
   * Stop all proactive pinging.
   */
  stop(): void {
    for (const timer of this.timers) {
      clearInterval(timer);
    }
    this.timers = [];
    console.log("[ProactivePinger] 🔕 Proactive pinging stopped");
  }

  /**
   * Check if we're in quiet hours.
   * User-configured quiet hours (from PreferenceStore) take priority over defaults.
   */
  private isQuietHours(): boolean {
    // Dynamic: if the user has set quiet hours via conversation, honour those
    if (this.context.preferenceStore) {
      return this.context.preferenceStore.isQuietHours(
        this.config.quietHoursStart,
        this.config.quietHoursEnd,
      );
    }
    // Static fallback from PingConfig
    const hour = new Date().getHours();
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
   * Unified tick: run AutonomousPlanner first; if it executes an action,
   * skip manual check-in and morning brief for this tick.
   */
  private async tickPlannerThenManual(): Promise<void> {
    const planner = this.context.autonomousPlanner;
    if (planner) {
      try {
        const action = await planner.planAndExecute();
        if (action) {
          // Planner acted — sync dedup state so the manual methods don't repeat
          if (action.type === "morning_brief") {
            planner.markMorningBriefDone();
            this.lastMorningBriefDate = new Date().toISOString().split("T")[0];
          }
          if (action.type === "memory_consolidation") {
            planner.markConsolidationDone();
          }
          // Skip manual check-in and morning brief this tick
          return;
        }
      } catch (err) {
        console.error("[ProactivePinger] AutonomousPlanner error:", err);
      }
    }

    // Fallback: run the manual checks when planner is absent or chose no action
    await this.maybeCheckIn();
    await this.maybeMorningBrief();
  }

  /**
   * Maybe send a periodic check-in.
   */
  private async maybeCheckIn(): Promise<void> {
    if (this.isQuietHours()) return;

    // Suppress check-ins if user hasn't responded to recent pings
    if (this.unansweredPings >= MAX_UNANSWERED_PINGS) return;

    const now = Date.now();

    // Enforce minimum cooldown between any two pings
    if (now - this.lastPingTime < MIN_PING_COOLDOWN_MS) return;

    // Use ProactiveIntentionLoop if available
    const proactiveLoop = this.context.proactiveLoop;
    if (proactiveLoop) {
      const item = proactiveLoop.evaluate();
      if (item) {
        await this.generateAndSend(
          item.message,
          item.type === "commitment" ? "follow_up" : "check_in",
        );
        return;
      }
    } else {
      // Fallback: check for stale goals (legacy path when proactiveLoop not available)
      const goalGraph = this.context.goalGraph;
      if (goalGraph) {
        try {
          const staleGoals = goalGraph.getStale(3);
          if (staleGoals.length > 0) {
            const goal = staleGoals[0];
            const daysSinceActive = Math.round(
              (Date.now() - goal.lastActiveAt) / (1000 * 60 * 60 * 24),
            );
            const prompt =
              `Just checking in — you had a goal: "${goal.title}" ` +
              `(${goal.progress}% complete, last active ${daysSinceActive} days ago). ` +
              `Any progress on this?`;
            await this.generateAndSend(prompt, "follow_up");
            return;
          }
        } catch (err) {
          console.warn("[ProactivePinger] Stale goal check failed:", err);
        }
      }
    }

    // No real content to share — skip. Generic check-ins ("what's on your plate?")
    // are noise. Only ping when there's something worth saying (handled above via
    // ProactiveIntentionLoop or stale-goal follow-ups).
  }

  /**
   * Maybe send the morning brief.
   */
  private async maybeMorningBrief(): Promise<void> {
    if (!this.config.morningBrief) return;
    if (this.isQuietHours()) return;

    const now = new Date();
    const hour = now.getHours();
    const minute = now.getMinutes();
    const dateKey = now.toISOString().split("T")[0];

    // Only fire at the configured hour, minute 0, once per day
    if (hour !== this.config.morningBriefHour || minute !== 0) return;
    if (this.lastMorningBriefDate === dateKey) return;

    this.lastMorningBriefDate = dateKey;

    const dayOfWeek = [
      "Sunday",
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
    ][now.getDay()];

    // Gather real context: stale goals and recent history summary
    let goalContext = "";
    try {
      const staleGoals = this.context.goalGraph?.getStale(1) ?? [];
      if (staleGoals.length > 0) {
        goalContext = `Active goals: ${staleGoals.map((g) => `"${g.title}" (${g.progress}%)`).join(", ")}.`;
      }
    } catch { /* non-critical */ }

    const recentHistory = this.context.getRecentHistory?.() ?? [];
    const lastUserMessages = recentHistory
      .filter((m) => m.role === "user")
      .slice(-3)
      .map((m) => (typeof m.content === "string" ? m.content.slice(0, 100) : ""))
      .filter(Boolean);
    const historyContext = lastUserMessages.length > 0
      ? `Recent activity: ${lastUserMessages.join(" | ")}.`
      : "";

    // Skip morning brief if there's nothing real to say
    if (!goalContext && !historyContext) return;

    const prompt =
      `It's ${dayOfWeek} morning. Generate a concise morning brief (2-3 sentences max). ` +
      (goalContext ? `Context: ${goalContext} ` : "") +
      (historyContext ? `${historyContext} ` : "") +
      `Reference the actual context above — do not invent generic motivational filler. ` +
      `Sound like a real assistant who knows what the user is working on.`;

    await this.generateAndSend(prompt, "morning_brief");
  }

  /**
   * Maybe run the daily memory consolidation job.
   * Extracts persistent facts from the day's chat logs and saves them to owl_dna.json.
   */
  private async maybeConsolidateMemory(): Promise<void> {
    const now = new Date();
    const hour = now.getHours();
    const minute = now.getMinutes();
    const dateKey = now.toISOString().split("T")[0];

    // Run at 3 AM by default (when the user is asleep)
    // Hardcoded for now, but could be added to PingConfig
    if (hour !== 3 || minute !== 0) return;
    if (this.lastConsolidationDate === dateKey) return;

    this.lastConsolidationDate = dateKey;

    // Ensure we know who we are consolidating for
    const userId = this.context.userId;
    if (!userId) {
      console.log(
        "[ProactivePinger] Skipping consolidation: no userId in context",
      );
      return;
    }

    try {
      const consolidator = new MemoryConsolidator(
        this.context.provider,
        this.context.owl,
        this.context.config.workspace,
      );
      await consolidator.consolidateSession(userId);
    } catch (e) {
      console.error("[ProactivePinger] Memory consolidation failed:", e);
    }
  }

  /**
   * Maybe run the autonomous tool pruner.
   * Scans for failing tools and attempts to rewrite or archive them.
   */
  private async maybePruneTools(): Promise<void> {
    const now = new Date();
    const hour = now.getHours();

    // Run every 4 hours (e.g. 0, 4, 8, 12, 16, 20)
    if (hour % 4 !== 0 || now.getMinutes() !== 0) return;

    const dateKey = `${now.toISOString().split("T")[0]}_${hour}`;
    if (this.lastConsolidationDate === dateKey) return; // Reusing this key variable slightly hackily for MVP, would normally track separately
    this.lastConsolidationDate = dateKey;

    try {
      // Provide the configured global ledger
      const pruner = new ToolPruner(
        this.context.provider,
        this.context.owl,
        this.context.config.workspace,
        this.context.capabilityLedger,
      );
      await pruner.scanAndPrune();
    } catch (e) {
      console.error("[ProactivePinger] Tool pruning failed:", e);
    }
  }

  /**
   * Proactive self-study session — runs at 2 AM.
   * The owl quietly researches queued topics while the user is asleep.
   * No message is sent to the user; knowledge is stored as Pellets.
   */
  private async maybeSelfStudy(): Promise<void> {
    if (!this.context.learningEngine && !this.context.learningOrchestrator)
      return;

    const now = new Date();
    const hour = now.getHours();
    const minute = now.getMinutes();
    const dateKey = now.toISOString().split("T")[0];

    // Run at 2 AM, once per day
    if (hour !== 2 || minute !== 0) return;
    if (this.lastSelfStudyDate === dateKey) return;

    this.lastSelfStudyDate = dateKey;

    try {
      console.log(
        "[ProactivePinger] 🧠 Starting overnight self-study session...",
      );

      // Use new orchestrator if available
      if (this.context.learningOrchestrator) {
        const cycle =
          await this.context.learningOrchestrator.runProactiveSession();
        if (cycle.synthesisReport) {
          console.log(
            `[ProactivePinger] ✓ Self-study (orchestrator) done: ` +
              `${cycle.topicsPrioritized} topics, ` +
              `${cycle.synthesisReport.pelletsCreated} pellets created`,
          );
        }
      } else {
        const result = await this.context.learningEngine!.runStudySession(4);
        if (result.studied.length > 0) {
          console.log(
            `[ProactivePinger] ✓ Self-study done: studied [${result.studied.join(", ")}], ` +
              `${result.pelletsCreated} pellets created, ` +
              `${result.newFrontierTopics.length} new topics discovered`,
          );
        }
      }
    } catch (err) {
      console.error("[ProactivePinger] Self-study session failed:", err);
    }
  }

  /**
   * Knowledge Council — owls learn independently, then brainstorm and validate.
   * Runs weekly during quiet hours (3 AM on Sundays by default).
   */
  private async maybeKnowledgeCouncil(): Promise<void> {
    // DISABLED — Knowledge Council burned tokens proactively.
    // Learning now only happens reactively (on actual failures).
    return;
  }

  /**
   * Idle-Time Dreaming — reflects on past mistakes to adapt heuristics.
   */
  private async maybeDream(): Promise<void> {
    // DISABLED — Dreaming is handled by CognitiveLoop's reflexion_dream action.
    // No need to duplicate with a separate timer.
    return;
  }

  /**
   * Skill Evolution + Pattern Mining — runs at 5 AM, once per day.
   *
   * Two phases:
   *   1. SkillEvolver: critiques every registered skill and rewrites low-scoring ones
   *      (Self-Refine loop, max 2 iterations each).
   *   2. PatternMiner: scans recent session history for repeated successful tool
   *      sequences and crystallizes them as new SKILL.md files (LATS-inspired).
   *
   * Neither phase sends anything to the user — all work is silent.
   */
  private async maybeEvolveSkills(): Promise<void> {
    // DISABLED — Skill evolution and pattern mining burned tokens proactively.
    // Learning now only happens reactively (on actual failures).
    return;
  }

  /**
   * Generate a proactive message using the LLM and send it.
   * Injects recent episodic memory (if available) so the message references
   * real past context instead of sending vague generic follow-ups.
   */
  private async generateAndSend(
    prompt: string,
    _type: PingType,
  ): Promise<void> {
    try {
      // ── Episodic context injection ──────────────────────────────────
      // When the proactive engine runs, it doesn't go through the ContextBuilder,
      // so episodic memory is NOT automatically injected. We query it here and
      // prepend it to the prompt so the model knows what actually happened recently.
      let episodicPrefix = "";
      if (this.context.episodicMemory) {
        try {
          const episodes = await Promise.race([
            this.context.episodicMemory.searchWithScoring(
              prompt.slice(0, 150), // use the prompt itself as query
              3,
              this.context.provider,
              0.1, // low threshold — proactive messages need broad recall
            ),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error("timeout")), 2000),
            ),
          ]);
          if (episodes.length > 0) {
            episodicPrefix =
              "<past_sessions>\n" +
              episodes
                .map(
                  (ep) =>
                    `  <session date="${new Date(ep.date).toLocaleDateString()}">${ep.summary}</session>`,
                )
                .join("\n") +
              "\n</past_sessions>\n\n";
          }
        } catch {
          // Non-fatal — proceed without episodes
        }
      }

      const fullPrompt = episodicPrefix ? episodicPrefix + prompt : prompt;

      const response = await this.engine.run(fullPrompt, {
        provider: this.context.provider,
        owl: this.context.owl,
        sessionHistory: this.context.getRecentHistory?.() ?? [],
        config: this.context.config,
        toolRegistry: this.context.toolRegistry,
        cwd: this.context.config.workspace,
        skipGapDetection: true, // Proactive messages are pre-generated — never evolve on them
      });

      await this.context.sendToUser(response.content);
      this.lastPingTime = Date.now();
      this.unansweredPings++;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error(`[ProactivePinger] Failed to generate ping: ${msg}`);
    }
  }
}
