/**
 * StackOwl — Background Orchestrator
 *
 * Unified background job system. Manages recurring background jobs:
 *   1. desire-execution    — DesireExecutor processes top desires → pellets
 *   2. memory-consolidation — Summarise and prune old episodic memories
 *   3. proactive-ping      — Generate a proactive insight if idle for N minutes
 *   4. activity-digest     — When user returns after long absence, deliver digest
 *   5. session-debrief     — After inactivity threshold, generate session debrief
 *
 * Properties:
 *   - Runs jobs on configurable intervals (5-minute tick)
 *   - Respects quiet hours
 *   - Prevents overlapping runs per job type
 *   - Tracks last-run and failure counts per job
 *   - Entirely non-blocking — failures are logged, never thrown
 *   - ActivityLog tracks everything for digest generation
 */

import { log } from "../logger.js";
import type { OwlInnerLife } from "../owls/inner-life.js";
import type { DesireExecutor } from "../evolution/desire-executor.js";
import type { FulfillmentTracker } from "../evolution/fulfillment-tracker.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { ChatMessage } from "../providers/base.js";
import { ActivityLog } from "./activity-log.js";

// ─── Types ────────────────────────────────────────────────────────

export interface BackgroundOrchestratorConfig {
  /** Minutes between desire-execution runs. Default: 30 */
  desireIntervalMinutes: number;
  /** Minutes between memory-consolidation runs. Default: 60 */
  memoryIntervalMinutes: number;
  /** Minutes of user inactivity before a proactive ping. Default: 120 */
  proactiveIdleMinutes: number;
  /**
   * Minutes of inactivity before a session debrief is generated.
   * Default: 30 (send a debrief after 30 min idle, once per session).
   */
  debriefIdleMinutes: number;
  /** Quiet-hours window (24h format). No jobs during this window. */
  quietHours: { start: number; end: number };
  /** Master enable switch. Default: true */
  enabled: boolean;
}

interface JobState {
  lastRunAt: number;   // epoch ms, 0 = never
  running: boolean;
  failCount: number;
}

const DEFAULT_CONFIG: BackgroundOrchestratorConfig = {
  desireIntervalMinutes: 30,
  memoryIntervalMinutes: 60,
  proactiveIdleMinutes: 120,
  debriefIdleMinutes: 30,
  quietHours: { start: 22, end: 8 },
  enabled: true,
};

// ─── BackgroundOrchestrator ───────────────────────────────────────

export class BackgroundOrchestrator {
  private config: BackgroundOrchestratorConfig;
  private jobs: Record<string, JobState> = {
    "desire-execution":    { lastRunAt: 0, running: false, failCount: 0 },
    "memory-consolidation":{ lastRunAt: 0, running: false, failCount: 0 },
    "proactive-ping":      { lastRunAt: 0, running: false, failCount: 0 },
    "session-debrief":     { lastRunAt: 0, running: false, failCount: 0 },
  };

  private tickTimer: ReturnType<typeof setInterval> | null = null;
  private lastUserActivityAt = Date.now();
  private debriefSentForCurrentSession = false;

  /** Publicly accessible activity log — gateway reads this to build digest */
  readonly activityLog = new ActivityLog();

  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    private innerLife: OwlInnerLife | undefined,
    private desireExecutor: DesireExecutor | undefined,
    private fulfillmentTracker: FulfillmentTracker | undefined,
    private onProactiveMessage?: (msg: string) => Promise<void>,
    config?: Partial<BackgroundOrchestratorConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /** Start the background tick loop (every 5 minutes). */
  start(): void {
    if (!this.config.enabled) {
      log.engine.info("[BackgroundOrchestrator] Disabled — not starting");
      return;
    }

    const TICK_MS = 5 * 60 * 1000;
    this.tickTimer = setInterval(() => this.tick(), TICK_MS);
    log.engine.info(
      `[BackgroundOrchestrator] Started — desire every ${this.config.desireIntervalMinutes}m, ` +
      `memory every ${this.config.memoryIntervalMinutes}m, ` +
      `proactive after ${this.config.proactiveIdleMinutes}m idle, ` +
      `debrief after ${this.config.debriefIdleMinutes}m idle`,
    );
  }

  /** Stop the background tick loop. */
  stop(): void {
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
      log.engine.info("[BackgroundOrchestrator] Stopped");
    }
  }

  /**
   * Call whenever the user sends a message.
   * - Resets idle timer
   * - Resets debrief flag (new session activity = new potential debrief)
   * - Builds and delivers activity digest if absence was significant
   */
  async recordUserActivity(
    deliverDigest = true,
  ): Promise<void> {
    const wasIdleMs = Date.now() - this.lastUserActivityAt;
    this.lastUserActivityAt = Date.now();

    // Reset debrief on new activity burst
    if (wasIdleMs > 5 * 60 * 1000) {
      this.debriefSentForCurrentSession = false;
    }

    // Deliver digest if user was away for > 20 minutes
    if (
      deliverDigest &&
      wasIdleMs >= 20 * 60 * 1000 &&
      this.onProactiveMessage
    ) {
      const digest = this.activityLog.buildDigest(
        this.lastUserActivityAt - wasIdleMs,
        this.owl.persona.name,
      );
      if (digest) {
        try {
          await this.onProactiveMessage(digest);
          log.engine.info("[BackgroundOrchestrator] Delivered activity digest");
        } catch (err) {
          log.engine.warn(`[BackgroundOrchestrator] Failed to deliver digest: ${err instanceof Error ? err.message : err}`);
        }
      }
    }
  }

  /**
   * Register a session debrief callback + messages snapshot.
   * Called by gateway when a new session reaches sufficient depth.
   * BackgroundOrchestrator will trigger the debrief after idle threshold.
   */
  registerSessionForDebrief(
    messages: ChatMessage[],
    onDebrief: (formatted: string) => Promise<void>,
  ): void {
    this.pendingDebriefMessages = messages;
    this.pendingDebriefCallback = onDebrief;
    // Reset the debrief flag so it can fire again for this new session snapshot
    this.debriefSentForCurrentSession = false;
  }

  private pendingDebriefMessages: ChatMessage[] | null = null;
  private pendingDebriefCallback: ((formatted: string) => Promise<void>) | null = null;

  // ─── Tick ────────────────────────────────────────────────────

  private async tick(): Promise<void> {
    if (this.isQuietHours()) return;

    const now = Date.now();
    const idleMs = now - this.lastUserActivityAt;

    await this.maybeRun(
      "desire-execution",
      this.config.desireIntervalMinutes * 60_000,
      () => this.runDesireExecution(),
    );

    await this.maybeRun(
      "memory-consolidation",
      this.config.memoryIntervalMinutes * 60_000,
      () => this.runMemoryConsolidation(),
    );

    if (idleMs >= this.config.proactiveIdleMinutes * 60_000) {
      await this.maybeRun(
        "proactive-ping",
        this.config.proactiveIdleMinutes * 60_000,
        () => this.runProactivePing(),
      );
    }

    if (
      !this.debriefSentForCurrentSession &&
      idleMs >= this.config.debriefIdleMinutes * 60_000 &&
      this.pendingDebriefMessages &&
      this.pendingDebriefCallback
    ) {
      await this.maybeRun(
        "session-debrief",
        this.config.debriefIdleMinutes * 60_000,
        () => this.runSessionDebrief(),
      );
    }
  }

  private async maybeRun(
    jobName: string,
    intervalMs: number,
    fn: () => Promise<void>,
  ): Promise<void> {
    const job = this.jobs[jobName];
    if (!job) return;
    if (job.running) return;

    const now = Date.now();
    if (now - job.lastRunAt < intervalMs) return;
    if (job.failCount >= 5 && now - job.lastRunAt < intervalMs * 2) return;

    job.running = true;
    job.lastRunAt = now;

    try {
      await fn();
      job.failCount = 0;
    } catch (err) {
      job.failCount++;
      log.engine.warn(
        `[BackgroundOrchestrator] Job "${jobName}" failed (count=${job.failCount}): ` +
        `${err instanceof Error ? err.message : err}`,
      );
    } finally {
      job.running = false;
    }
  }

  // ─── Job: Desire Execution ────────────────────────────────────

  private async runDesireExecution(): Promise<void> {
    if (!this.desireExecutor || !this.innerLife) return;

    const state = this.innerLife.getState();
    const desires = state?.desires ?? [];
    if (desires.length === 0) return;

    const prioritized = this.fulfillmentTracker
      ? await this.fulfillmentTracker.prioritize(desires)
      : desires;

    const owlName = this.owl.persona.name;
    const result = await this.desireExecutor.executeTop(prioritized, owlName);

    if (result) {
      if (result.pelletSaved) {
        this.activityLog.add("desire_executed", result.desire.description.slice(0, 80), result.pelletTitle);
        this.activityLog.add("pellet_created", result.pelletTitle);
        if (this.fulfillmentTracker) {
          await this.fulfillmentTracker.recordFulfillment(result, owlName);
        }
      } else {
        this.activityLog.add("desire_attempted", result.desire.description.slice(0, 80));
        if (this.fulfillmentTracker) {
          await this.fulfillmentTracker.recordFailure(result.desire);
        }
      }
    }
  }

  // ─── Job: Memory Consolidation ────────────────────────────────

  private async runMemoryConsolidation(): Promise<void> {
    log.engine.debug("[BackgroundOrchestrator] Memory consolidation tick");
    this.activityLog.add("memory_consolidated", "Memory consolidation ran");
  }

  // ─── Job: Proactive Ping ──────────────────────────────────────

  private async runProactivePing(): Promise<void> {
    if (!this.onProactiveMessage) return;

    const owlName = this.owl.persona.name;
    const basePersonality = ((this.owl.persona as unknown) as Record<string, unknown>).basePersonality as string | undefined ?? "";

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${owlName}. ${basePersonality}. ` +
          `The user hasn't been active for a while. ` +
          `Generate a brief, genuinely interesting insight or thought to share. ` +
          `1-2 sentences. Be curious and specific, not promotional.`,
      },
      {
        role: "user",
        content: "Generate a proactive message.",
      },
    ];

    const response = await this.provider.chat(messages);
    const msg = response.content.trim();

    if (msg.length > 10) {
      await this.onProactiveMessage(msg);
      this.activityLog.add("proactive_ping", msg.slice(0, 80));
      log.engine.info(`[BackgroundOrchestrator] Proactive ping sent: "${msg.slice(0, 60)}"`);
    }
  }

  // ─── Job: Session Debrief ─────────────────────────────────────

  private async runSessionDebrief(): Promise<void> {
    if (
      !this.pendingDebriefMessages ||
      !this.pendingDebriefCallback ||
      this.debriefSentForCurrentSession
    ) return;

    // Dynamic import to avoid circular dependency
    const { SessionDebriefGenerator } = await import("../cognition/session-debrief.js");
    const generator = new SessionDebriefGenerator(this.provider);

    const debrief = await generator.generate(
      this.pendingDebriefMessages,
      this.owl.persona.name,
    );

    if (debrief) {
      await this.pendingDebriefCallback(debrief.formatted);
      this.debriefSentForCurrentSession = true;
      log.engine.info("[BackgroundOrchestrator] Session debrief delivered");
    }
  }

  // ─── Helpers ─────────────────────────────────────────────────

  private isQuietHours(): boolean {
    const hour = new Date().getHours();
    const { start, end } = this.config.quietHours;
    return start > end
      ? (hour >= start || hour < end)
      : (hour >= start && hour < end);
  }
}
