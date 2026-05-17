/**
 * StackOwl — Proactive Pinger
 *
 * Makes Noctua feel alive — she proactively reaches out to the user
 * with reminders, morning briefs, ideas, and follow-ups.
 */

import { log } from "../logger.js";
import { runWithContext } from "../infra/observability/context.js";
import { makeEnvelope } from "../gateway/delivery-envelope.js";
import { platform } from "../platform/index.js";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import { ToolPruner } from "../evolution/pruner.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { PreferenceStore } from "../preferences/store.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
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
  sendToUser?: (message: string) => Promise<void>;
  /** Get recent session history for context */
  getRecentHistory?: () => ChatMessage[];
  /** The user ID to run consolidation for */
  userId?: string;
  /** Persistent job queue — replaces the 8 independent setInterval timers */
  jobQueue?: import("./job-queue.js").ProactiveJobQueue;
  /** Unified learning orchestrator (TopicFusion + Synthesis + Reflexion) */
  learningOrchestrator?: LearningOrchestrator;
  /** User preference store — used to check dynamic quiet hours */
  preferenceStore?: PreferenceStore;
  /** Reflexion engine for extracting rules from past failures */
  reflexionEngine?: ReflexionEngine;
  /** Skills registry for evolution pass */
  skillsRegistry?: SkillsRegistry;
  /** Absolute path to skills directory */
  skillsDir?: string;
  /** Session store used to read conversation history */
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
  /** Autonomous planner — priority-based action scheduler driven by GoalGraph */
  autonomousPlanner?: AutonomousPlanner;
  /** Global event bus */
  eventBus?: import("../events/bus.js").EventBus;
  /** GatewayEventBus — when set, proactive messages are routed through the delivery bus */
  gatewayEventBus?: import("../gateway/event-bus.js").GatewayEventBus;
  /**
   * Channel adapter reference — when set and `capabilities().tuiV2` is true,
   * proactive messages are emitted as `heartbeat.message` UiEvents so the
   * TUI v2 HeartbeatBanner card lane renders them distinctly.
   */
  channelAdapter?: import("../gateway/types.js").ChannelAdapter;
  /** MemoryDatabase — used to record delivery outcomes */
  db?: import("../memory/db.js").MemoryDatabase;
  /** Stable user identifier for delivery/engagement rows */
  // Note: userId is already declared above; this comment is for documentation only.
  /** DeliveryVerifier — gates outbound proactive sends */
  deliveryVerifier?: import("./delivery-verifier.js").DeliveryVerifier;
}

export type PingType =
  | "morning_brief"
  | "check_in"
  | "reminder"
  | "idea"
  | "follow_up"
  | "goal_progress_update";

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
  private timers: NodeJS.Timeout[] = [];
  private lastPingTime: number = 0;
  private lastMorningBriefDate: string = "";
  private lastConsolidationDate: string = "";
  private lastSelfStudyDate: string = "";
  // lastDreamTime and lastSkillEvolutionDate removed — proactive learning disabled
  private unansweredPings: number = 0;
  private lastDeliveryId: string = "";
  private currentJobId: string = "";
  private _backgroundWorker: import("../agent/background-worker.js").BackgroundWorker | null = null;

  constructor(context: PingContext, config?: Partial<PingConfig>) {
    this.config = { ...DEFAULT_PING_CONFIG, ...config };
    this.context = context;
  }

  /** Attach the background worker so the timer loop can drive it. */
  setBackgroundWorker(worker: import("../agent/background-worker.js").BackgroundWorker): void {
    this._backgroundWorker = worker;
  }

  /**
   * Start the proactive pinging system.
   *
   * Previously: 8 independent setInterval timers, each polling its own state,
   * losing jobs across quiet hours and process restarts.
   *
   * Now: a single 30-second worker tick polls the ProactiveJobQueue. Jobs are
   * persisted to SQLite, survive restarts, respect quiet hours by rescheduling
   * (not discarding), and execute in priority order.
   *
   * On first start, standard recurring jobs are seeded into the queue if they
   * don't already exist.
   */
  start(): void {
    if (!this.config.enabled) return;

    this.stop();
    log.heartbeat.info("[ProactivePinger] Proactive pinging started (queue-driven)");

    const userId = this.context.userId ?? "default";

    // ── Seed recurring jobs into queue if not already scheduled ──
    if (this.context.jobQueue) {
      this.seedRecurringJobs(userId);
    }

    // ── Single worker tick every 30 seconds ──────────────────────
    // Replaces 8 independent timers. Polls the queue, respects quiet hours
    // by rescheduling jobs rather than skipping them.
    const workerTimer = setInterval(() => {
      runWithContext({ channelId: "heartbeat", spanName: "heartbeat.worker-tick" }, () =>
        this.tickJobQueue(userId).catch((err) => {
          log.heartbeat.error("[ProactivePinger] Job queue tick error", err);
        }),
      );
    }, 30_000);
    this.timers.push(workerTimer);

    // ── Background worker (5-minute cycle, unchanged) ────────────
    // Executes pending AgentTask records (separate from the proactive queue).
    const bgTimer = setInterval(() => {
      runWithContext({ channelId: "heartbeat", spanName: "heartbeat.bg-worker-tick" }, () => {
        if (this._backgroundWorker && !this.isQuietHours()) {
          void this._backgroundWorker.tick().catch((err) => {
            log.heartbeat.error("[ProactivePinger] Background worker tick error", err);
          });
        }
      });
    }, 5 * 60 * 1000);
    this.timers.push(bgTimer);
  }

  /**
   * Seed standard recurring jobs into the queue on startup.
   * Skips if jobs of each type already exist as pending.
   */
  private seedRecurringJobs(userId: string): void {
    const queue = this.context.jobQueue!;

    // Morning brief — schedule for today at configured hour, or tomorrow if past
    if (this.config.morningBrief) {
      const now = new Date();
      const briefToday = new Date();
      briefToday.setHours(this.config.morningBriefHour, 0, 0, 0);
      const briefTime = briefToday > now ? briefToday : new Date(briefToday.getTime() + 86_400_000);
      queue.schedule({ type: "morning_brief", userId, scheduledAt: briefTime, priority: 8, deduplicate: true });
    }

    // Check-in — first one in checkInIntervalMinutes
    const checkInTime = new Date(Date.now() + this.config.checkInIntervalMinutes * 60_000);
    queue.schedule({ type: "check_in", userId, scheduledAt: checkInTime, priority: 5, deduplicate: true });

    // Memory consolidation — daily at 2 AM
    const consolidationTime = this.nextDailyAt(2, 0);
    queue.schedule({ type: "memory_consolidation", userId, scheduledAt: consolidationTime, priority: 3, deduplicate: true });

    // Tool pruning — daily at 3 AM
    const pruningTime = this.nextDailyAt(3, 0);
    queue.schedule({ type: "tool_pruning", userId, scheduledAt: pruningTime, priority: 2, deduplicate: true });

    // Self-study — every 6 hours
    const selfStudyTime = new Date(Date.now() + 6 * 3_600_000);
    queue.schedule({ type: "self_study", userId, scheduledAt: selfStudyTime, priority: 4, deduplicate: true });

    log.engine.info("[ProactivePinger] Seeded recurring jobs into queue");
  }

  /**
   * Single worker tick: poll due jobs, respect quiet hours, execute in priority order.
   * Quiet hours cause rescheduling (to end of quiet window), NOT job loss.
   */
  private async tickJobQueue(userId: string): Promise<void> {
    const queue = this.context.jobQueue;
    if (!queue) {
      // Fallback to legacy tick if queue not available
      await this.tickPlannerThenManual();
      return;
    }

    const dueJobs = queue.getDueJobs(5);
    if (dueJobs.length === 0) return;

    for (const job of dueJobs) {
      // Quiet hours: reschedule to end of quiet window instead of discarding
      if (this.isQuietHours()) {
        const wakeTime = this.nextWakeTime();
        queue.reschedule(job.id, wakeTime);
        log.engine.debug(
          `[ProactivePinger] Quiet hours — rescheduled ${job.type} to ${wakeTime.toISOString()}`,
        );
        continue;
      }

      queue.markRunning(job.id);

      try {
        const handled = await this.executeJob(job);
        if (!handled) {
          // executeJob did not finalize the job itself — caller closes it out
          queue.markDone(job.id);
          this.reenqueueRecurring(job, userId, queue);
        }

        log.engine.debug(`[ProactivePinger] Job ${job.type} completed`);
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        queue.markFailed(job.id, errMsg);
        log.engine.warn(`[ProactivePinger] Job ${job.type} failed: ${errMsg}`);
      }
    }
  }

  /**
   * Execute a single job from the queue.
   */
  private async executeJob(job: import("./job-queue.js").ProactiveJob): Promise<boolean> {
    this.currentJobId = job.id;
    const { deliveryVerifier, db, userId, jobQueue } = this.context;

    if (deliveryVerifier) {
      let payload: any = {};
      try { payload = job.payload ? JSON.parse(job.payload) : {}; } catch { /* keep {} */ }

      const verdict = await deliveryVerifier.verify({
        jobType: job.type,
        goalId: payload.goalId,
        messagePreview: payload.summary ?? "",
        activeGoals: payload.activeGoals ?? [],
        priority: job.priority,
        idleSeconds: payload.idleSeconds,
      });

      if (verdict.verdict === "NOISE") {
        db?.writeProactiveDelivery({
          id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          jobId: job.id,
          channel: "none",
          userId: userId ?? job.userId,
          verdict: "NOISE",
          messagePreview: payload.summary?.slice(0, 100),
          status: "discarded",
          deliveredAt: new Date().toISOString(),
        });
        jobQueue?.markDone(job.id);
        // NOISE: still re-enqueue the next recurring instance (skip THIS one only)
        this.reenqueueRecurring(job, userId ?? job.userId, jobQueue!);
        log.engine.debug(`[ProactivePinger] NOISE verdict — discarded job ${job.id}: ${verdict.reason}`);
        return true;
      }

      if (verdict.verdict === "NEUTRAL") {
        const suppressUntil =
          verdict.suppressUntil ?? new Date(Date.now() + 2 * 60 * 60_000);
        jobQueue?.reschedule(job.id, suppressUntil);
        db?.writeProactiveDelivery({
          id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          jobId: job.id,
          channel: "none",
          userId: userId ?? job.userId,
          verdict: "NEUTRAL",
          messagePreview: payload.summary?.slice(0, 100),
          status: "suppressed",
          deliveredAt: new Date().toISOString(),
        });
        log.engine.debug(`[ProactivePinger] NEUTRAL verdict — suppressed job ${job.id} until ${suppressUntil.toISOString()}`);
        return true;
      }
      // ADVANCES → fall through to existing switch
    }

    switch (job.type) {
      case "morning_brief":
        await this.sendMorningBrief();
        break;
      case "check_in":
        await this.sendCheckIn();
        break;
      case "memory_consolidation": {
        // Step 1: Episodic decay sweep — compress/archive old memories (like NREM sleep)
        if (this.context.episodicMemory) {
          const decay = this.context.episodicMemory.runDecay();
          log.engine.info(
            `[ProactivePinger] memory_consolidation: decay sweep — compressed ${decay.compressed}, archived ${decay.archived}`,
          );
        }

        // Step 2: Cross-session synthesis — feed top episodic themes into learning orchestrator
        // so they become durable semantic pellets (hippocampal → cortical transfer)
        if (this.context.learningOrchestrator && this.context.episodicMemory) {
          const threadStrings = this.context.episodicMemory.getThematicThreads(5);
          const themes = threadStrings
            .map((t) => {
              const m = t.match(/^\[Topic: ([^\]]+)\]/);
              return m ? m[1].toLowerCase() : "";
            })
            .filter(Boolean);

          if (themes.length > 0) {
            try {
              const cycle = await this.context.learningOrchestrator.runProactiveSession({
                upcomingPatterns: themes,
                maxTopics: 3,
              });
              log.engine.info(
                `[ProactivePinger] memory_consolidation: synthesis — ` +
                  `${cycle.topicsPrioritized} topics, ${cycle.synthesisReport?.pelletsCreated ?? 0} pellets`,
              );
            } catch (err) {
              log.engine.warn(
                `[ProactivePinger] memory_consolidation: synthesis failed: ${err instanceof Error ? err.message : String(err)}`,
              );
            }
          } else {
            log.engine.debug("[ProactivePinger] memory_consolidation: no thematic threads — skipping synthesis");
          }
        } else {
          log.engine.debug("[ProactivePinger] memory_consolidation: episodicMemory or learningOrchestrator unavailable — skipping");
        }
        break;
      }
      case "tool_pruning":
        await this.maybePruneTools();
        break;
      case "self_study":
        await this.maybeSelfStudy();
        break;
      case "knowledge_council":
        log.engine.debug("[ProactivePinger] knowledge_council handled by CognitiveLoop — skipping");
        break;
      case "dream_reflexion":
        log.engine.debug("[ProactivePinger] dream_reflexion handled by CognitiveLoop — skipping");
        break;
      case "goal_check":
        await this.maybeCheckGoals();
        break;
      case "goal_progress_update": {
        let payload: { goalId?: string; summary?: string } = {};
        try { payload = job.payload ? JSON.parse(job.payload) : {}; } catch { /* keep {} */ }

        if (!payload.goalId || !payload.summary) {
          log.engine.warn(`[ProactivePinger] goal_progress_update missing payload — marking done`);
          this.context.jobQueue?.markDone(job.id);
          break;
        }

        const goalContext = await this.assembleGoalContext();
        const prompt =
          `Inform the user about progress on a goal they're tracking. ` +
          `Be brief (1-2 sentences max), specific, and reference the actual progress.\n\n` +
          `Goal context:\n${goalContext || "(no active goals loaded)"}\n\n` +
          `Progress summary: ${payload.summary}`;

        await this.generateAndSend(prompt, "goal_progress_update");
        this.context.jobQueue?.markDone(job.id);
        break;
      }
      case "background_task":
        if (this._backgroundWorker) {
          await this._backgroundWorker.tick();
        }
        break;
      default:
        log.engine.warn(`[ProactivePinger] Unknown job type: ${(job as any).type}`);
    }
    return false;
  }

  /**
   * Re-enqueue a completed recurring job for its next scheduled occurrence.
   */
  private reenqueueRecurring(
    job: import("./job-queue.js").ProactiveJob,
    userId: string,
    queue: import("./job-queue.js").ProactiveJobQueue,
  ): void {
    const intervals: Partial<Record<import("./job-queue.js").JobType, number>> = {
      check_in: this.config.checkInIntervalMinutes * 60_000,
      self_study: 6 * 3_600_000,
      knowledge_council: 12 * 3_600_000,
      dream_reflexion: 24 * 3_600_000,
      background_task: 5 * 60_000,
    };
    const dailyAt: Partial<Record<import("./job-queue.js").JobType, [number, number]>> = {
      morning_brief: [this.config.morningBriefHour, 0],
      memory_consolidation: [2, 0],
      tool_pruning: [3, 0],
    };

    if (intervals[job.type]) {
      queue.schedule({
        type: job.type,
        userId,
        scheduledAt: new Date(Date.now() + intervals[job.type]!),
        priority: job.priority,
        deduplicate: true,
      });
    } else if (dailyAt[job.type]) {
      const [h, m] = dailyAt[job.type]!;
      queue.schedule({
        type: job.type,
        userId,
        scheduledAt: this.nextDailyAt(h, m),
        priority: job.priority,
        deduplicate: true,
      });
    }
  }

  // ─── Scheduling helpers ─────────────────────────────────────

  /** Returns the next occurrence of HH:MM (today if not yet passed, else tomorrow) */
  private nextDailyAt(hour: number, minute: number): Date {
    const t = new Date();
    t.setHours(hour, minute, 0, 0);
    if (t <= new Date()) {
      t.setDate(t.getDate() + 1);
    }
    return t;
  }

  /** Returns the time when quiet hours end (the next wake window start) */
  private nextWakeTime(): Date {
    const now = new Date();
    const wake = new Date();
    wake.setHours(this.config.quietHoursEnd, 0, 0, 0);
    if (wake <= now) {
      wake.setDate(wake.getDate() + 1);
    }
    return wake;
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
    log.heartbeat.info("[ProactivePinger] Proactive pinging stopped");
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
        log.heartbeat.error("[ProactivePinger] AutonomousPlanner error", err);
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
          log.heartbeat.warn(`[ProactivePinger] Stale goal check failed: ${err instanceof Error ? err.message : err}`);
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
    } catch (err) { log.heartbeat.warn(`[ProactivePinger] Goal context load failed: ${err instanceof Error ? err.message : err}`); }

    // Use episodic memory to fetch semantic thematic clustering instead of raw text
    let historyContext = "";
    if (this.context.episodicMemory) {
      const threads = this.context.episodicMemory.getThematicThreads(3);
      if (threads.length > 0) {
        historyContext = `Top ongoing topics:\n` + threads.map(t => `- ${t}`).join("\n");
      }
    }
    
    // Fallback: if no episodes available, use recent session raw history
    if (!historyContext) {
      const recentHistory = this.context.getRecentHistory?.() ?? [];
      const lastUserMessages = recentHistory
        .filter((m) => m.role === "user")
        .slice(-3)
        .map((m) => (typeof m.content === "string" ? m.content.slice(0, 100) : ""))
        .filter(Boolean);
      historyContext = lastUserMessages.length > 0
        ? `Recent activity: ${lastUserMessages.join(" | ")}.`
        : "";
    }

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
      log.heartbeat.error("[ProactivePinger] Tool pruning failed", e);
    }
  }

  /**
   * Proactive self-study session — runs at 2 AM.
   * The owl quietly researches queued topics while the user is asleep.
   * No message is sent to the user; knowledge is stored as Pellets.
   */
  private async maybeSelfStudy(): Promise<void> {
    if (!this.context.learningOrchestrator)
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
      log.heartbeat.info("[ProactivePinger] Starting overnight self-study session...");

      const cycle =
        await this.context.learningOrchestrator.runProactiveSession();
      if (cycle.synthesisReport) {
        log.heartbeat.info(
          `[ProactivePinger] Self-study (orchestrator) done: ` +
            `${cycle.topicsPrioritized} topics, ` +
            `${cycle.synthesisReport.pelletsCreated} pellets created`,
        );
      }
    } catch (err) {
      log.heartbeat.error("[ProactivePinger] Self-study session failed", err);
    }
  }

  // ── Queue-compatible wrappers (called by job executor) ────────

  /**
   * Assemble goal-aware context string for proactive message prompts.
   * Priority-ordered: active goals (high) > recent history (low).
   */
  private async assembleGoalContext(): Promise<string> {
    const parts: string[] = [];

    if (this.context.goalGraph) {
      try {
        await this.context.goalGraph.load();
        const activeGoals = this.context.goalGraph.getActive?.() ?? [];
        if (activeGoals.length > 0) {
          parts.push(
            `Active goals:\n${activeGoals.slice(0, 3).map((g: any) => `- ${g.title}`).join("\n")}`,
          );
        }
      } catch (err) {
        log.heartbeat.warn(`[ProactivePinger] assembleGoalContext failed: ${err instanceof Error ? err.message : err}`);
      }
    }

    if (this.context.getRecentHistory) {
      const history = this.context.getRecentHistory();
      if (history.length > 0) {
        const lastUserMessages = history
          .filter(m => m.role === "user")
          .slice(-3)
          .map(m => (typeof m.content === "string" ? m.content : "").slice(0, 80));
        if (lastUserMessages.length > 0) {
          parts.push(`Recent context: ${lastUserMessages.join(" | ")}`);
        }
      }
    }

    return parts.join("\n\n");
  }

  /** Job-queue path: goal-anchored morning brief (queue handles time gating). */
  private async sendMorningBrief(): Promise<void> {
    const dayOfWeek = new Date().toLocaleDateString("en-US", { weekday: "long" });
    const goalContext = await this.assembleGoalContext();

    if (!goalContext) return; // Skip if nothing real to say

    const prompt =
      `It's ${dayOfWeek} morning. Generate a concise morning brief (2-3 sentences max). ` +
      `${goalContext}\n\n` +
      `Reference the actual context above — do not invent generic motivational filler. ` +
      `Sound like a real assistant who knows what the user is working on.`;

    await this.generateAndSend(prompt, "morning_brief");
  }

  /** Thin wrapper so job executor can call by job type name */
  private async sendCheckIn(): Promise<void> {
    return this.maybeCheckIn();
  }

  /**
   * Check active goals and send a nudge if any are overdue or blocked.
   * Fires from the job queue on the "goal_check" job type.
   */
  private async maybeCheckGoals(): Promise<void> {
    const graph = this.context.goalGraph;
    if (!graph) return;
    try {
      await graph.load();
      const stale = graph.getStale(3); // goals not touched in 3 days
      if (stale.length === 0) return;
      const goalSummaries = stale
        .slice(0, 3)
        .map((g) => `• **${g.title}** — ${g.description.slice(0, 80)}`)
        .join("\n");
      await this.deliverProactive(
        `📌 **Goal check-in** — you have ${stale.length} goal(s) with no recent progress:\n` +
        goalSummaries +
        `\n\nWant me to pick one and start working on it?`,
        "goal_check",
        this.currentJobId,
      );
    } catch (err) {
      log.engine.warn(
        `[ProactivePinger] Goal check failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  /** Returns the delivery ID of the last successfully recorded proactive delivery. */
  getLastDeliveryId(): string {
    return this.lastDeliveryId;
  }

  private async deliverProactive(
    message: string,
    _jobType: string = "unknown",
    jobId: string = "",
    verdict: string = "skipped_check",
  ): Promise<void> {
    const { gatewayEventBus, channelAdapter, userId, db } = this.context;
    const deliveryId = `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const channel = gatewayEventBus ? "gateway" : "direct";

    // TUI v2 fast path: emit a heartbeat.message UiEvent so the HeartbeatBanner
    // card lane renders it distinctly from solicited replies.
    if (channelAdapter?.capabilities?.()?.tuiV2 && channelAdapter.emit) {
      channelAdapter.emit({
        kind: "heartbeat.message",
        owlId: this.context.owl.persona.name,
        owlName: this.context.owl.persona.name,
        owlEmoji: this.context.owl.persona.emoji ?? "🔔",
        text: message,
        timestamp: Date.now(),
      });
      db?.writeProactiveDelivery({
        id: deliveryId,
        jobId,
        channel: "tui",
        userId: userId ?? "local",
        messagePreview: message.slice(0, 100),
        verdict,
        deliveredAt: new Date().toISOString(),
        status: "delivered",
      });
      this.lastDeliveryId = deliveryId;
      this.lastPingTime = Date.now();
      return;
    }

    if (gatewayEventBus && userId) {
      gatewayEventBus.publish(makeEnvelope({
        userId,
        content: { text: message, streamable: false },
        urgency: "proactive",
        trigger: "proactive",
        ttlMs: 4 * 60 * 60 * 1000,
        deliveryId,
        jobType: _jobType,
      }));
      db?.writeProactiveDelivery({
        id: deliveryId,
        jobId,
        channel,
        userId,
        messagePreview: message.slice(0, 100),
        verdict,
        deliveredAt: new Date().toISOString(),
        status: "delivered",
      });
      this.lastDeliveryId = deliveryId;
      this.lastPingTime = Date.now();
    } else if (this.context.sendToUser) {
      try {
        await this.context.sendToUser(message);
        db?.writeProactiveDelivery({
          id: deliveryId,
          jobId,
          channel: "direct",
          userId: userId ?? "unknown",
          messagePreview: message.slice(0, 100),
          verdict,
          deliveredAt: new Date().toISOString(),
          status: "delivered",
        });
        this.lastDeliveryId = deliveryId;
        this.lastPingTime = Date.now();
      } catch (err) {
        log.engine.warn(`[ProactivePinger] sendToUser failed: ${err}`);
        db?.writeProactiveDelivery({
          id: deliveryId,
          jobId,
          channel: "direct",
          userId: userId ?? "unknown",
          verdict,
          status: "failed",
        });
      }
    } else {
      // No channel adapter, gatewayEventBus, or sendToUser — fall back to platform notifier
      // This ensures proactive messages reach the user via native notifications or stderr
      try {
        const result = await platform.notifier.notify({
          title: this.context.owl?.persona?.name ?? "Heartbeat",
          body: message,
          category: "heartbeat",
        });
        db?.writeProactiveDelivery({
          id: deliveryId,
          jobId,
          channel: result.via,
          userId: userId ?? "local",
          messagePreview: message.slice(0, 100),
          verdict,
          deliveredAt: new Date().toISOString(),
          status: result.delivered ? "delivered" : "failed",
        });
        if (result.delivered) {
          this.lastDeliveryId = deliveryId;
          this.lastPingTime = Date.now();
          log.heartbeat.debug(`[ProactivePinger] notifier fallback delivered via ${result.via}`);
        } else {
          log.heartbeat.warn(`[ProactivePinger] notifier fallback failed (${result.reason})`);
        }
      } catch (err) {
        log.heartbeat.error(
          "[ProactivePinger] notifier fallback error",
          err,
          { deliveryId, message: message.slice(0, 100) },
        );
        db?.writeProactiveDelivery({
          id: deliveryId,
          jobId,
          channel: "notifier-error",
          userId: userId ?? "local",
          verdict,
          status: "failed",
          deliveredAt: new Date().toISOString(),
        });
      }
    }
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
        } catch (err) {
          log.heartbeat.warn(`[ProactivePinger] Episodic memory search failed: ${err instanceof Error ? err.message : err}`);
        }
      }

      const fullPrompt = episodicPrefix ? episodicPrefix + prompt : prompt;

      if (this.context.eventBus) {
        // Dispatch to task queue instead of running synchronously
        this.context.eventBus.emit("agent:ping_request", {
          prompt: fullPrompt,
          type: _type,
        });
        this.lastPingTime = Date.now();
        this.unansweredPings++;
      } else if (this.context.gatewayEventBus || this.context.sendToUser) {
        await this.deliverProactive(fullPrompt, _type, this.currentJobId);
      } else {
        await this.handleUndeliverable(this.currentJobId, "no transport available (eventBus, gatewayEventBus, sendToUser all missing)");
      }
    } catch (error) {
      log.heartbeat.error(`[ProactivePinger] Failed to generate ping: ${error instanceof Error ? error.message : String(error)}`, error);
    }
  }

  private async handleUndeliverable(jobId: string, reason: string): Promise<void> {
    const { jobQueue, db, userId } = this.context;
    if (!jobQueue || !jobId) return;

    const retryCount = jobQueue.getRetryCount?.(jobId) ?? 0;

    if (retryCount < 3) {
      jobQueue.incrementRetry?.(jobId);
      const backoffMs = 60_000 * Math.pow(2, retryCount);
      jobQueue.reschedule?.(jobId, new Date(Date.now() + backoffMs));
      log.engine.warn(`[ProactivePinger] Job ${jobId} undeliverable (${reason}); retry ${retryCount + 1}/3 in ${backoffMs / 1000}s`);
      return;
    }

    jobQueue.markFailed?.(jobId, `undeliverable after 3 retries: ${reason}`);
    db?.writeProactiveDelivery({
      id: `del_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      jobId,
      channel: "none",
      userId: userId ?? "unknown",
      verdict: "ADVANCES",
      status: "failed",
      deliveredAt: new Date().toISOString(),
    });
    log.engine.error(`[ProactivePinger] Job ${jobId} failed after 3 retries: ${reason}`);
  }

  recordEngagement(
    deliveryId: string,
    jobType: string,
    replied: boolean,
    replyLatencySeconds?: number,
    goalId?: string,
  ): void {
    this.context.db?.writeProactiveEngagement({
      id: `eng_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      deliveryId,
      jobType,
      goalId,
      replied,
      replyLatencySeconds,
    });
  }
}
