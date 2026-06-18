/**
 * StackOwl — Cognitive Loop
 *
 * The central self-improvement engine that makes the assistant think and
 * learn like a person. Instead of scattered timers ("study at 2 AM",
 * "consolidate at 3 AM"), the Cognitive Loop runs a continuous cycle:
 *
 *   Observe → Reflect → Decide → Act → Learn
 *
 * Each tick, the loop:
 *   1. Reads the owl's inner state (desires, opinions, mood, observations)
 *   2. Scans for capability gaps and learning opportunities
 *   3. Decides the highest-value self-improvement action
 *   4. Executes it (study a topic, evolve a skill, mine patterns, dream)
 *   5. Feeds results back into the knowledge graph and inner state
 *
 * This replaces BOTH the AutonomousPlanner and the ProactivePinger's
 * background actions as the single decision-making core. With heartbeat
 * disabled, the CognitiveLoop is the ONLY system driving self-improvement.
 *
 * Architecture:
 *   - Runs independently of user conversations (background thread)
 *   - Non-blocking: each action has a timeout, failures are logged not thrown
 *   - Desire-driven: inner life desires influence what gets studied
 *   - Self-aware: tracks what it learned and what failed for future decisions
 *   - Feedback loop: study results update inner life and DNA
 */

import { log } from "../logger.js";
import { runWithContext } from "../infra/observability/context.js";
import type { LogRecord } from "../infra/observability/schema.js";
import type { LogQuery } from "../infra/observability/reader.js";
import type { LogSummary } from "../infra/observability/analyzer.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { OwlInnerLife } from "../owls/inner-life.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { SessionStore } from "../memory/store.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { OwlEvolutionEngine } from "../owls/evolution.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { SkillsLoader } from "../skills/loader.js";
import type { RoutingRuleStore } from "./routing-rule-store.js";
import { CapabilityScanner } from "../heartbeat/capability-scanner.js";

// ─── Types ───────────────────────────────────────────────────────

export type CognitiveAction =
  | "desire_driven_study"        // Study a topic from inner desires
  | "gap_driven_study"           // Study a topic from capability gaps
  | "autonomous_skill_synthesis" // Proactively create skills for anticipated needs
  | "self_reflection"            // Review failures & generate new desires/goals
  | "reflexion_dream"            // Learn from past mistakes
  | "capability_scan"            // Discover unused platform features
  | "frontier_exploration"       // Explore adjacent domains to deepen knowledge
  | "memory_consolidation"       // Consolidate conversation memories
  | "tool_pruning"               // Archive/fix failing synthesized tools
  | "dna_evolution"              // Evolve owl DNA from accumulated interactions
  | "log_analysis"               // Scan JSONL logs for capability gaps and error hotspots
  | "idle";                      // Nothing to do right now

interface CognitiveDecision {
  action: CognitiveAction;
  reason: string;
  priority: number;
  topic?: string;
}

interface CognitiveTickResult {
  action: CognitiveAction;
  success: boolean;
  detail: string;
  durationMs: number;
}

export interface CognitiveLoopConfig {
  /** Interval between cognitive ticks in minutes. Default: 15 */
  tickIntervalMinutes: number;
  /** Minimum idle minutes before background learning. Default: 5 */
  minIdleMinutes: number;
  /** Maximum actions per day to prevent runaway costs. Default: 20 */
  maxActionsPerDay: number;
  /** Enable/disable the loop. Default: true */
  enabled: boolean;
}

export interface CognitiveLoopDeps {
  provider: ModelProvider;
  owl: OwlInstance;
  config: StackOwlConfig;
  innerLife?: OwlInnerLife;
  reflexionEngine?: ReflexionEngine;
  skillsRegistry?: SkillsRegistry;
  sessionStore?: SessionStore;
  capabilityLedger?: CapabilityLedger;
  toolRegistry?: ToolRegistry;
  skillsDir?: string;
  workspacePath?: string;
  owlRegistry?: OwlRegistry;
  evolutionEngine?: OwlEvolutionEngine;
  providerRegistry?: ProviderRegistry;
  skillsLoader?: SkillsLoader;
  jobQueue?: import("../heartbeat/job-queue.js").ProactiveJobQueue;
  /** Reader function for JSONL log files — used by log-analysis tick */
  logReader?: (logsDir: string, query: LogQuery) => Promise<LogRecord[]>;
  /** Summarizer function — used by log-analysis tick */
  logAnalyzer?: (records: LogRecord[]) => LogSummary;
  /** Store for learned routing rules materialized from repeat failures */
  routingRuleStore?: RoutingRuleStore;
}

/**
 * Determines if a desire description represents an actionable capability need
 * (e.g., "Build ability to send emails") vs. an abstract behavioral goal
 * (e.g., "Build a relationship where the user trusts me").
 *
 * Uses phrase-level patterns instead of single keywords to avoid false positives
 * like "Build a relationship" matching on "build".
 */
function isCapabilityDesire(description: string): boolean {
  const text = description.toLowerCase();

  // Phrase patterns that indicate a concrete capability need
  const CAPABILITY_PHRASES = [
    /\b(?:build|create|develop|add)\s+(?:ability|skills?|tools?|capability)\b/,
    /\b(?:build|create|develop|add)\s+(?:a\s+)?(?:way|method|system)\s+to\b/,
    /\bautomate\b/,
    /\b(?:send|fetch|access|control|manage|schedule|monitor|scrape|parse|convert)\s+\w/,
    /\bintegrat(?:e|ing)\b/,
    /\btools?\s+for\b/,
    /\bskills?\s+for\b/,
    /\bcapability\s+to\b/,
    /\bability\s+to\s+(?!understand|learn|anticipate|build\s+(?:a\s+)?relationship)/,
  ];

  // Exclusion patterns for abstract behavioral/relationship goals
  const EXCLUSIONS = [
    /\brelationship\b/,
    /\btrust\b.*\bjudgment\b/,
    /\banticipat(?:e|ing)\s+what\b/,
    /\bunderstand\s+(?:the\s+)?user/,
    /\bproactive\s+(?!automat)/,
  ];

  // Must match at least one capability phrase
  const matchesCapability = CAPABILITY_PHRASES.some((p) => p.test(text));
  if (!matchesCapability) return false;

  // Must not match exclusions
  const matchesExclusion = EXCLUSIONS.some((p) => p.test(text));
  return !matchesExclusion;
}

const DEFAULT_CONFIG: CognitiveLoopConfig = {
  tickIntervalMinutes: 60,  // was 15 — hourly to prevent token burn
  minIdleMinutes: 5,
  maxActionsPerDay: 8,      // was 20 — each action = up to 4 LLM calls
  enabled: true,
};

// ─── Cognitive Loop ─────────────────────────────────────────────

export class CognitiveLoop {
  private config: CognitiveLoopConfig;
  private timer: NodeJS.Timeout | null = null;
  private lastUserActivity: number = Date.now();
  private actionsToday: number = 0;
  private lastDayKey: string = "";
  private lastReflexionTime: number = 0;
  private lastMemoryConsolidationTime: number = 0;
  private lastToolPruneTime: number = 0;
  private lastDnaEvolutionTime: number = 0;
  // Cooldown timestamps — written by execute methods, read by decide() when
  // proactive actions are re-enabled. Kept to avoid losing state tracking.
  // @ts-expect-error TS6133 — assigned in execute, read when proactive actions enabled
  private lastCapScanTime = 0;
  // @ts-expect-error TS6133
  private lastSelfReflectionTime = 0;
  // @ts-expect-error TS6133
  private lastAutonomousSynthesisTime = 0;
  private lastLogAnalysisTime = 0;
  private studySessionsSinceDnaSync: number = 0;
  private skillsCreatedToday: number = 0;
  private history: CognitiveTickResult[] = [];
  private capabilityScanner: CapabilityScanner | null = null;
  private _isTicking = false;

  /**
   * Persistent synthesis queue — targets discovered during conversations or
   * cognitive ticks accumulate here and get consumed across future ticks.
   * Survives across ticks (but not across restarts — transient by design,
   * since stale gaps become irrelevant as user needs change).
   */
  private synthesisQueue: Array<{
    userRequest: string;
    description: string;
    addedAt: number;
    source: "conversation" | "self_reflection" | "capability_scan" | "skill_stats" | "log_analysis";
  }> = [];

  constructor(
    private deps: CognitiveLoopDeps,
    config?: Partial<CognitiveLoopConfig>,
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Create scanner if we have the deps
    if (deps.config && deps.toolRegistry) {
      this.capabilityScanner = new CapabilityScanner(
        deps.config,
        deps.toolRegistry,
        deps.skillsRegistry,
        deps.toolRegistry?.getTracker() ?? undefined,
      );
    }
  }

  // ─── Lifecycle ─────────────────────────────────────────────────

  start(): void {
    if (!this.config.enabled) return;

    this.stop();

    // ─── Initialization diagnostics ─────────────────────────────
    // Log which deps are available so runtime failures are visible.
    const deps = this.deps;
    const available: string[] = [];
    const missing: string[] = [];
    const check = (name: string, val: unknown) =>
      (val ? available : missing).push(name);

    check("innerLife", deps.innerLife);
    check("reflexionEngine", deps.reflexionEngine);
    check("skillsRegistry", deps.skillsRegistry);
    check("sessionStore", deps.sessionStore);
    check("capabilityLedger", deps.capabilityLedger);
    check("toolRegistry", deps.toolRegistry);
    check("skillsDir", deps.skillsDir);
    check("evolutionEngine", deps.evolutionEngine);
    check("providerRegistry", deps.providerRegistry);
    check("skillsLoader", deps.skillsLoader);

    log.engine.info(
      `[CognitiveLoop] Deps available: [${available.join(", ")}]`,
    );
    if (missing.length > 0) {
      log.engine.warn(
        `[CognitiveLoop] Deps MISSING (features disabled): [${missing.join(", ")}]`,
      );
    }

    // NOTE: Desire seeding removed — the loop only learns reactively now.
    // Learning is triggered by actual failures (synthesis queue from conversation
    // gaps), not proactive exploration of random topics.

    log.engine.info(
      `[CognitiveLoop] Started — ticking every ${this.config.tickIntervalMinutes} min`,
    );

    // First tick fires after one full interval — no startup burst
    this.timer = setInterval(
      () => {
        runWithContext({ channelId: "cognition", spanName: "cognition.tick" }, () =>
          this.tick().catch((err) => {
            log.engine.error(
              `[CognitiveLoop] Tick error: ${err instanceof Error ? err.message : err}`,
            );
          }),
        );
      },
      this.config.tickIntervalMinutes * 60 * 1000,
    );
    this.timer.unref(); // Don't prevent Node from exiting
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Call when user sends a message to reset idle timer. */
  notifyUserActivity(): void {
    this.lastUserActivity = Date.now();
  }

  /**
   * After completing a study or reflexion action tied to a goal,
   * enqueue a goal_progress_update job so ProactivePinger can inform the user.
   * No-op when jobQueue is not wired (tests, headless runs).
   * Wired by future tasks once execute methods carry goalId in their result.
   */
  // @ts-expect-error TS6133 — exercised via test; call sites added once execute methods return goalId
  private async maybeEnqueueGoalUpdate(goalId: string, summary: string): Promise<void> {
    if (!this.deps.jobQueue) return;
    const userId = (this.deps.owl as any)?.owlId ?? (this.deps.owl as any)?.id ?? "default";
    this.deps.jobQueue.schedule({
      type: "goal_progress_update",
      userId,
      scheduledAt: new Date(Date.now() + 5 * 60 * 1000),
      payload: { goalId, summary: summary.slice(0, 200) },
      priority: 7,
      deduplicate: false,
    });
    log.engine.debug(`[CognitiveLoop] Enqueued goal_progress_update for goal ${goalId}`);
  }

  /**
   * Enqueue a synthesis target from a conversation. Called by the gateway
   * when the assistant detects it can't do something the user asked for,
   * or when post-processing identifies an unmet need.
   *
   * The cognitive loop will pick this up on the next idle tick and attempt
   * to synthesize a skill for it — no waiting for the 3-hour cooldown.
   */
  enqueueSynthesisTarget(
    userRequest: string,
    description: string,
    source: "conversation" | "self_reflection" | "capability_scan" | "skill_stats" | "log_analysis" = "conversation",
  ): void {
    // Dedup against existing queue entries (fuzzy match on first 30 chars)
    const key = userRequest.toLowerCase().slice(0, 30);
    const alreadyQueued = this.synthesisQueue.some(
      (t) => t.userRequest.toLowerCase().slice(0, 30) === key,
    );
    if (alreadyQueued) return;

    // Cap queue size to prevent unbounded growth
    if (this.synthesisQueue.length >= 20) {
      this.synthesisQueue.shift(); // Drop oldest
    }

    this.synthesisQueue.push({
      userRequest,
      description,
      addedAt: Date.now(),
      source,
    });
    log.engine.info(
      `[CognitiveLoop] Synthesis target queued (${source}): "${userRequest.slice(0, 60)}"`,
    );
  }

  /** Get recent cognitive history for debugging/status. */
  getHistory(): CognitiveTickResult[] {
    return this.history.slice(-20);
  }

  /** Get a human-readable status summary. */
  getStatus(): string {
    const idleMinutes = Math.round((Date.now() - this.lastUserActivity) / 60_000);
    const recent = this.history.slice(-5);
    const successRate = this.history.length > 0
      ? Math.round(this.history.filter((h) => h.success).length / this.history.length * 100)
      : 0;

    const lines = [
      `## Cognitive Loop Status`,
      `- Enabled: ${this.config.enabled}`,
      `- Idle: ${idleMinutes} min`,
      `- Actions today: ${this.actionsToday}/${this.config.maxActionsPerDay}`,
      `- Lifetime success rate: ${successRate}% (${this.history.length} actions)`,
      `- Study sessions since DNA sync: ${this.studySessionsSinceDnaSync}`,
      `- Skills created today: ${this.skillsCreatedToday}`,
    ];

    if (recent.length > 0) {
      lines.push("", "### Recent Actions");
      for (const r of recent) {
        lines.push(`  ${r.success ? "✓" : "✗"} ${r.action}: ${r.detail.slice(0, 60)} (${r.durationMs}ms)`);
      }
    }

    return lines.join("\n");
  }

  // ─── Core Tick ────────────────────────────────────────────────

  private async tick(): Promise<void> {
    // Prevent overlapping ticks — only one tick runs at a time
    if (this._isTicking) return;
    this._isTicking = true;

    try {
      await this._tickInner();
    } finally {
      this._isTicking = false;
    }
  }

  private async _tickInner(): Promise<void> {
    // Rate-limit guard — check shared circuit breaker instead of isolated backoff.
    // When the main conversation path opens the breaker (after a 429), cognitive
    // ticks automatically pause without needing a separate per-loop state variable.
    //
    // Use getDefaultName() (non-throwing) to get the configured default provider —
    // listProviders()[0] returns the first-registered provider which may differ from
    // the configured default in multi-provider setups (e.g. {anthropic, openai} with
    // defaultProvider: "openai" would incorrectly check anthropic's breaker).
    if (this.deps.providerRegistry) {
      const providerNameForGuard = this.deps.providerRegistry.getDefaultName();
      if (providerNameForGuard && this.deps.providerRegistry.isProviderOpen(providerNameForGuard)) {
        log.cognition.debug("[CognitiveLoop] Primary provider circuit open — skipping tick");
        return;
      }
    }

    // Reset daily counters
    const dayKey = new Date().toISOString().split("T")[0];
    if (dayKey !== this.lastDayKey) {
      this.actionsToday = 0;
      this.skillsCreatedToday = 0;
      this.lastDayKey = dayKey;
    }

    // Budget guard
    if (this.actionsToday >= this.config.maxActionsPerDay) {
      return;
    }

    // Must be idle for minimum period
    const idleMinutes = (Date.now() - this.lastUserActivity) / 60_000;
    if (idleMinutes < this.config.minIdleMinutes) {
      return;
    }

    // Decide what to do
    const decision = await this.decide();
    if (decision.action === "idle") return;

    // Resolve the active provider name for circuit-breaker recording.
    // Use getDefaultName() (non-throwing) to obtain the configured default — the
    // same provider that was checked in the guard above so breaker state is consistent.
    const providerName =
      this.deps.providerRegistry?.getDefaultName() ?? "default";

    // Execute with timeout (2 minutes max per action)
    const startTime = Date.now();
    let result: CognitiveTickResult;

    try {
      const detail = await Promise.race([
        this.execute(decision),
        new Promise<string>((_, reject) =>
          setTimeout(() => reject(new Error("Action timed out (120s)")), 120_000),
        ),
      ]);
      // Record success so the circuit breaker accumulates healthy signal.
      this.deps.providerRegistry?.recordProviderResult(providerName, true);
      result = {
        action: decision.action,
        success: true,
        detail,
        durationMs: Date.now() - startTime,
      };
      this.actionsToday++;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Always count failed actions toward the daily budget — prevents infinite
      // retry loop when every call returns 429 (failed actions kept budget at 0)
      this.actionsToday++;
      // Always record provider failure — timeouts and network errors are equally
      // strong signals of provider unhealthiness as rate-limit responses.
      // The rate-limit branch gets its own warn message; other errors get a
      // generic one with the error attached for observability.
      this.deps.providerRegistry?.recordProviderResult(providerName, false);
      if (
        msg.includes("429") ||
        msg.toLowerCase().includes("rate_limit") ||
        msg.toLowerCase().includes("usage limit")
      ) {
        log.cognition.warn("[CognitiveLoop] Rate limit detected during cognitive tick — breaker will gate future calls");
      } else {
        log.cognition.warn("[CognitiveLoop] Execute failed during cognitive tick", err instanceof Error ? err : new Error(msg), { providerName });
      }
      result = {
        action: decision.action,
        success: false,
        detail: msg,
        durationMs: Date.now() - startTime,
      };
    }

    this.history.push(result);
    if (this.history.length > 50) this.history = this.history.slice(-50);

    log.engine.info(
      `[CognitiveLoop] ${result.success ? "✓" : "✗"} ${result.action}: ${result.detail} (${result.durationMs}ms)`,
    );
  }

  // ─── Decision Engine ──────────────────────────────────────────

  private async decide(): Promise<CognitiveDecision> {
    const candidates: CognitiveDecision[] = [];
    const now = Date.now();
    const HOUR_MS = 60 * 60 * 1000;

    // ─── Reactive-Only Learning ─────────────────────────────────
    // The loop ONLY acts on concrete failures and queued gaps from
    // actual conversations. No proactive exploration, no desire-driven
    // study, no frontier exploration. This prevents runaway token usage.

    // 1. Autonomous skill synthesis — ONLY when synthesis queue has
    //    targets from actual conversation failures (capability gaps).
    //    No 3-hour proactive scan — only real failures trigger this.
    if (
      this.deps.skillsDir &&
      this.skillsCreatedToday < 3 &&
      this.synthesisQueue.length > 0
    ) {
      candidates.push({
        action: "autonomous_skill_synthesis",
        reason: `${this.synthesisQueue.length} queued synthesis target(s) from conversation failures`,
        priority: 85,
      });
    }

    // 2. Reflexion (dream) — learn from past mistakes
    //    Lightweight: extracts behavioral patches so errors aren't repeated.
    //    Run at most once every 4 hours.
    if (this.deps.reflexionEngine && now - this.lastReflexionTime > 4 * HOUR_MS) {
      candidates.push({
        action: "reflexion_dream",
        reason: "Reflect on past mistakes to extract behavioral rules",
        priority: 55,
      });
    }

    // 3. DNA evolution — sync accumulated learning into personality
    //    Low cost (no LLM). Trigger after 5+ study sessions.
    if (
      this.deps.evolutionEngine &&
      this.studySessionsSinceDnaSync >= 5 &&
      now - this.lastDnaEvolutionTime > 2 * HOUR_MS
    ) {
      candidates.push({
        action: "dna_evolution",
        reason: `${this.studySessionsSinceDnaSync} study sessions since last DNA sync`,
        priority: 50,
      });
    }

    // 4. Tool pruning — fix or archive failing synthesized tools
    //    Run at most once every 8 hours.
    if (
      this.deps.capabilityLedger &&
      now - this.lastToolPruneTime > 8 * HOUR_MS
    ) {
      candidates.push({
        action: "tool_pruning",
        reason: "Scan synthesized tools for failures and prune/fix them",
        priority: 40,
      });
    }

    // 5. Memory consolidation — extract persistent facts from sessions
    //    Run at most once every 8 hours.
    if (
      this.deps.sessionStore &&
      now - this.lastMemoryConsolidationTime > 8 * HOUR_MS
    ) {
      candidates.push({
        action: "memory_consolidation",
        reason: "Consolidate conversation memories into persistent storage",
        priority: 35,
      });
    }

    // 6. Log analysis — scan JSONL logs for capability gaps and error hotspots
    //    Run at most once every 6 hours, only when logReader is wired.
    if (this.deps.logReader && now - this.lastLogAnalysisTime > 6 * HOUR_MS) {
      candidates.push({
        action: "log_analysis",
        reason: "Periodic log scan for capability gaps and error hotspots",
        priority: 80,
      });
    }

    if (candidates.length === 0) {
      return { action: "idle", reason: "Nothing to do", priority: 0 };
    }

    // Sort by priority, return highest
    candidates.sort((a, b) => b.priority - a.priority);
    return candidates[0];
  }

  // ─── Execution ────────────────────────────────────────────────

  private async execute(decision: CognitiveDecision): Promise<string> {
    switch (decision.action) {
      case "desire_driven_study":
        return this.executeDesireStudy(decision.topic!);

      case "gap_driven_study":
        return this.executeGapStudy(decision.topic!);

      case "autonomous_skill_synthesis":
        return this.executeAutonomousSkillSynthesis();

      case "self_reflection":
        return this.executeSelfReflection();

      case "reflexion_dream":
        return this.executeReflexion();

      case "capability_scan":
        return this.executeCapabilityScan();

      case "frontier_exploration":
        return this.executeFrontierExploration();

      case "memory_consolidation":
        return this.executeMemoryConsolidation();

      case "tool_pruning":
        return this.executeToolPruning();

      case "dna_evolution":
        return this.executeDnaEvolution();

      case "log_analysis":
        return this.executeLogAnalysis();

      default:
        return "No action taken";
    }
  }

  private async executeDesireStudy(topic: string): Promise<string> {
    // LearningOrchestrator removed — study actions are no-ops for now.
    this.studySessionsSinceDnaSync++;
    if (this.deps.innerLife) {
      await this.deps.innerLife.fulfillDesire(topic);
    }
    return `Studied "${topic}": learning orchestrator removed`;
  }

  private async executeGapStudy(topic: string): Promise<string> {
    // LearningOrchestrator removed — gap study is a no-op for now.
    this.studySessionsSinceDnaSync++;
    return `Gap study "${topic}": learning orchestrator removed`;
  }

  private async executeReflexion(): Promise<string> {
    if (!this.deps.reflexionEngine) return "No reflexion engine";

    await this.deps.reflexionEngine.dream();
    this.lastReflexionTime = Date.now();
    return "Reflexion dream completed — behavioral patches extracted";
  }

  private async executeCapabilityScan(): Promise<string> {
    if (!this.capabilityScanner) return "No scanner available";

    const result = this.capabilityScanner.scan();
    this.lastCapScanTime = Date.now();

    return `Scanned: ${result.totalToolsRegistered} tools, ${result.totalSkillsEnabled} skills, ${result.gaps.length} gaps (${result.coveragePercent}% coverage)`;
  }

  private async executeFrontierExploration(): Promise<string> {
    // LearningOrchestrator removed — frontier exploration is a no-op for now.
    this.studySessionsSinceDnaSync++;
    return "Frontier exploration: learning orchestrator removed";
  }

  /**
   * Memory consolidation — extracts persistent facts and behavioral rules
   * from recent conversations. Replaces ProactivePinger's 3 AM job since
   * heartbeat is disabled. Uses MemoryConsolidator.extractAndAppend() which
   * saves facts to workspace/memory.md for injection into future prompts.
   */
  private async executeMemoryConsolidation(): Promise<string> {
    if (!this.deps.sessionStore) return "No session store";

    // MemoryConsolidator removed — memory consolidation is handled by MemoryManager.
    this.lastMemoryConsolidationTime = Date.now();
    return "Memory consolidation: consolidator removed — handled by MemoryManager";
  }

  /**
   * Tool pruning — scans the capability ledger for failing or unused
   * synthesized tools and attempts to fix or archive them. Replaces
   * ProactivePinger's 4-hourly job.
   */
  private async executeToolPruning(): Promise<string> {
    if (!this.deps.capabilityLedger) return "No capability ledger";

    const { ToolPruner } = await import("../evolution/pruner.js");
    const workspacePath = this.deps.workspacePath ?? this.deps.config.workspace ?? "./workspace";

    const pruner = new ToolPruner(
      this.deps.provider,
      this.deps.owl,
      workspacePath,
      this.deps.capabilityLedger,
    );
    await pruner.scanAndPrune();
    this.lastToolPruneTime = Date.now();

    return "Tool pruning pass completed";
  }

  /**
   * DNA evolution — triggers the owl's personality mutation engine based
   * on accumulated interactions and study sessions. This makes the owl's
   * personality genuinely evolve over time — expertise grows in studied
   * domains, preferences shift based on conversations, challenge level
   * adjusts to the user's skill.
   */
  private async executeDnaEvolution(): Promise<string> {
    if (!this.deps.evolutionEngine) return "No evolution engine";

    const owlName = this.deps.owl.persona.name;
    try {
      const mutated = await this.deps.evolutionEngine.evolve(owlName);
      this.lastDnaEvolutionTime = Date.now();
      this.studySessionsSinceDnaSync = 0;

      if (mutated) {
        return `DNA evolved for ${owlName} — personality updated from ${this.studySessionsSinceDnaSync} study sessions`;
      }
      return `DNA evolution for ${owlName}: no mutations triggered`;
    } catch (err) {
      return `DNA evolution failed: ${err instanceof Error ? err.message : err}`;
    }
  }

  // ─── Log Analysis ───────────────────────────────────────────

  /**
   * Scan on-disk JSONL logs for the last 24 hours and extract:
   *   - Capability gaps (phrases like "no tool for X", "I cannot send")
   *   - Repeat error patterns by module
   *   - Slow spans by operation name
   * Gaps are enqueued for synthesis; summary is logged for dashboards.
   */
  private async executeLogAnalysis(): Promise<string> {
    if (!this.deps.logReader || !this.deps.workspacePath) {
      return "log analysis skipped: no logReader or workspacePath";
    }
    const { readLogsArray } = await import("../infra/observability/reader.js");
    const { summarize } = await import("../infra/observability/analyzer.js");

    const reader = this.deps.logReader ?? readLogsArray;
    const analyzer = this.deps.logAnalyzer ?? summarize;

    const logsDir = `${this.deps.workspacePath}/logs`;
    const records = await reader(logsDir, { since: Date.now() - 24 * 60 * 60 * 1000, limit: 5000 });
    const summary = analyzer(records);

    // Enqueue capability gaps for self-study
    for (const gap of summary.capabilityGaps) {
      this.enqueueSynthesisTarget(
        gap.phrase,
        `Inferred from ${gap.supportingTraces.length} log traces`,
        "log_analysis",
      );
    }

    // Materialize routing rules from repeat failure patterns
    if (this.deps.routingRuleStore && summary.repeatFailures.length > 0) {
      this.materializeRoutingRules(summary.repeatFailures, summary.capabilityGaps);
    }

    this.lastLogAnalysisTime = Date.now();
    log.engine.info("log_analysis.summary", {
      windowMinutes: summary.windowMinutes,
      totalRecords: summary.totalRecords,
      errorHotspots: summary.errorsByModule.length,
      capabilityGaps: summary.capabilityGaps.length,
      slowSpans: summary.slowSpans.length,
      repeatFailures: summary.repeatFailures.length,
    });

    return `Analyzed ${records.length} records over ${summary.windowMinutes}m; ${summary.capabilityGaps.length} gaps queued`;
  }

  // ─── Routing Rule Materialization ────────────────────────────

  /**
   * Convert repeat failure patterns from log analysis into actionable routing
   * rules that the RoutingRuleStore can serve on the very next turn.
   */
  private materializeRoutingRules(
    repeatFailures: Array<{ tool?: string; pattern?: string; count?: number; phrase?: string; normalizedMsg?: string; module?: string; spanName?: string }>,
    _capabilityGaps: Array<{ phrase: string; supportingTraces: unknown[] }>,
  ): void {
    const store = this.deps.routingRuleStore!;
    const TOOL_FALLBACKS_STATIC: Record<string, string[]> = {
      web_search: ["web_fetch", "live_browser"],
      web_fetch: ["live_browser", "web_search"],
      smart_search: ["smart_fetch", "live_browser"],
    };

    for (const failure of repeatFailures) {
      const toolName = (failure as any).tool ?? (failure as any).spanName ?? (failure as any).phrase ?? "";
      if (!toolName) continue;

      const alternatives = TOOL_FALLBACKS_STATIC[toolName] ?? [];
      if (alternatives.length === 0) continue;

      const intentPattern = (failure as any).pattern ?? toolName;
      const ruleId = `${toolName}:${intentPattern}`.slice(0, 64).replace(/\s+/g, "_");

      const existing = store.getById(ruleId);
      if (existing && !existing.disabled) continue; // already active

      const rule = {
        id: ruleId,
        failingTool: toolName,
        intentPattern,
        suggestedAlternatives: alternatives,
        appliedAt: Date.now(),
        version: (existing?.version ?? 0) + 1,
        disabled: false,
        observationCount: 0,
        successCount: 0,
      };

      store.upsert(rule);
      log.engine.info("routing-rule.materialized", {
        id: ruleId,
        failingTool: toolName,
        alternatives,
        intentPattern,
      });
    }
  }

  // ─── Autonomous Skill Synthesis ──────────────────────────────

  /**
   * The owl proactively identifies capability gaps and builds skills for them
   * WITHOUT waiting for a user to hit the gap. This is the core of autonomous
   * self-improvement — the owl thinks "what can't I do that I should be able to?"
   * and then builds the capability.
   *
   * Sources:
   *   1. Capability ledger — tools that failed repeatedly
   *   2. Inner life desires that map to capabilities (not just knowledge)
   *   3. Gap scanner — platform features not yet covered
   *
   * Uses ToolSynthesizer.generateSkillMd() — same path as reactive synthesis
   * but triggered proactively from the cognitive loop.
   */
  private async executeAutonomousSkillSynthesis(): Promise<string> {
    if (!this.deps.skillsDir) {
      return "Missing skillsDir";
    }

    const { ToolSynthesizer } = await import("../evolution/synthesizer.js");
    const synthesizer = new ToolSynthesizer();

    // Resolve synthesis provider (explicit config > default)
    const synthesisConfig = this.deps.config.synthesis;
    const synthesisModel = synthesisConfig?.model ?? this.deps.config.defaultModel;
    let synthesisProvider = this.deps.provider;

    if (this.deps.providerRegistry) {
      try {
        synthesisProvider = synthesisConfig?.provider
          ? this.deps.providerRegistry.get(synthesisConfig.provider)
          : this.deps.providerRegistry.getDefault();
      } catch {
        synthesisProvider = this.deps.providerRegistry.getDefault();
      }
    }

    // ── Priority source: Drain the synthesis queue (conversation-driven) ──
    const gapTargets: Array<{ userRequest: string; description: string }> = [];

    // Expire stale queue entries (older than 24 hours)
    const DAY_MS = 24 * 60 * 60 * 1000;
    this.synthesisQueue = this.synthesisQueue.filter(
      (t) => Date.now() - t.addedAt < DAY_MS,
    );

    // Queue targets get first priority — these came from real user interactions
    for (const queued of this.synthesisQueue.splice(0, 3)) {
      gapTargets.push({
        userRequest: queued.userRequest,
        description: `${queued.source}: ${queued.description}`,
      });
    }

    // ── Source 1: Capability ledger failures → skill synthesis targets ──
    if (this.deps.capabilityLedger) {
      await this.deps.capabilityLedger.load();
      const allTools = this.deps.capabilityLedger.listAll();
      const failingTools = allTools.filter(
        (t) => t.status === "active" && (t.consecutiveFailures ?? 0) >= 2,
      );
      for (const tool of failingTools.slice(0, 2)) {
        gapTargets.push({
          userRequest: tool.description,
          description: `Recurring failure in "${tool.toolName}": ${tool.rationale}`,
        });
      }
    }

    // ── Source 1b: Skill tracker — frequently used but failing skills ──
    // These are skills that get selected (user needs match) but never succeed,
    // indicating the skill content is broken or inadequate. Re-synthesize them.
    try {
      const { SkillTracker } = await import("../skills/tracker.js");
      const workspacePath = this.deps.workspacePath ?? this.deps.config.workspace ?? "./workspace";
      const tracker = new SkillTracker(workspacePath);
      await tracker.load();
      const failingSkills = tracker.getFailingSkills(3, 0.3);
      for (const { name, stats } of failingSkills.slice(0, 2)) {
        gapTargets.push({
          userRequest: name.replace(/_/g, " "),
          description: `Skill "${name}" selected ${stats.selectionCount}x but success rate is ${(stats.successRate * 100).toFixed(0)}% — needs re-synthesis`,
        });
      }
    } catch (err) {
      log.engine.warn(`[CognitiveLoop] Skill tracker load failed: ${err instanceof Error ? err.message : err}`);
    }

    // ── Source 2: Inner life desires that look like capabilities ──
    if (this.deps.innerLife) {
      const state = this.deps.innerLife.getState();
      if (state) {
        const capabilityDesires = state.desires.filter(
          (d) => d.intensity >= 0.5 && isCapabilityDesire(d.description),
        );
        for (const desire of capabilityDesires.slice(0, 2)) {
          gapTargets.push({
            userRequest: desire.description,
            description: `Inner desire: ${desire.description}`,
          });
        }
      }
    }

    // ── Source 3: Gap scanner for uncovered platform features ──
    if (this.capabilityScanner) {
      const scan = this.capabilityScanner.scan();
      const highGaps = scan.gaps.filter((g) => g.priority >= 4);
      for (const gap of highGaps.slice(0, 1)) {
        if (gap.suggestion) {
          gapTargets.push({
            userRequest: gap.description,
            description: gap.suggestion,
          });
        }
      }
    }

    // ── Source 4: User profile capability clusters (recurring unmet needs) ──
    try {
      const { readFile } = await import("node:fs/promises");
      const { existsSync } = await import("node:fs");
      const { join } = await import("node:path");
      const workspacePath = this.deps.workspacePath ?? this.deps.config.workspace ?? "./workspace";
      const profilePath = join(workspacePath, "user-profile.json");
      if (existsSync(profilePath)) {
        const raw = await readFile(profilePath, "utf-8");
        const profile = JSON.parse(raw) as {
          capabilityClusters?: Record<string, string[]>;
          topics?: Record<string, number>;
        };
        if (profile.capabilityClusters) {
          // Find high-frequency topics that have unmet capability sub-needs
          const topicEntries = Object.entries(profile.topics ?? {})
            .sort(([, a], [, b]) => b - a)
            .slice(0, 3);
          for (const [topic, _count] of topicEntries) {
            const cluster = profile.capabilityClusters[topic];
            if (cluster) {
              for (const subNeed of cluster.slice(0, 1)) {
                gapTargets.push({
                  userRequest: `${topic} ${subNeed}`,
                  description: `User frequently needs ${topic} capabilities, specifically ${subNeed}`,
                });
              }
            }
          }
        }
      }
    } catch (err) {
      log.engine.warn(`[CognitiveLoop] User profile read failed: ${err instanceof Error ? err.message : err}`);
    }

    if (gapTargets.length === 0) {
      this.lastAutonomousSynthesisTime = Date.now();
      return "No capability gaps found to synthesize";
    }

    // ── Dedup against existing skills ──
    const existingSkills = this.deps.skillsRegistry
      ? this.deps.skillsRegistry.listEnabled().map((s) => s.name.toLowerCase())
      : [];

    // Hard cap: synthesize at most 2 skills per cycle to prevent token burn.
    // Gap targets are already priority-sorted (queue first, then ledger, etc.)
    let created = 0;
    for (const target of gapTargets.slice(0, 2)) {
      // Skip if a skill with a similar name already exists
      const targetWords = target.userRequest.toLowerCase().split(/\s+/);
      const alreadyCovered = existingSkills.some((name) =>
        targetWords.some((w) => w.length > 4 && name.includes(w)),
      );
      if (alreadyCovered) continue;

      try {
        const toolDescriptions = this.deps.toolRegistry
          ? this.deps.toolRegistry
              .getAllDefinitions()
              .map((d) => `${d.name}: ${d.description?.slice(0, 80) ?? ""}`)
          : undefined;

        const result = await synthesizer.generateSkillMd(
          {
            type: "CAPABILITY_GAP",
            userRequest: target.userRequest,
            description: target.description,
          },
          synthesisProvider,
          this.deps.owl,
          this.deps.config,
          this.deps.skillsDir,
          toolDescriptions,
          synthesisModel,
        );

        log.engine.info(
          `[CognitiveLoop] Autonomously created skill: "${result.skillName}" at ${result.filePath}`,
        );
        created++;
        this.skillsCreatedToday++;

        // Reload the skill into the registry so it's immediately available.
        // Use registry.loadFromDirectory() directly instead of skillsLoader.load()
        // to avoid overwriting the loader's directory list (which includes built-in skills).
        if (this.deps.skillsLoader) {
          try {
            await this.deps.skillsLoader.getRegistry().loadFromDirectory(this.deps.skillsDir);
          } catch (err) {
            log.engine.warn(`[CognitiveLoop] Skills registry reload failed: ${err instanceof Error ? err.message : err}`);
          }
        }

        // Fulfill the desire if it came from inner life
        if (this.deps.innerLife) {
          await this.deps.innerLife.fulfillDesire(target.userRequest);
        }
      } catch (err) {
        log.engine.warn(
          `[CognitiveLoop] Autonomous synthesis failed for "${target.userRequest}": ${
            err instanceof Error ? err.message : err
          }`,
        );
      }

      // Only create one skill per tick to stay within budget
      if (created >= 1) break;
    }

    this.lastAutonomousSynthesisTime = Date.now();
    return created > 0
      ? `Autonomously created ${created} new skill(s)`
      : "Gap targets found but deduplication filtered all";
  }

  // ─── Self-Reflection ────────────────────────────────────────────

  /**
   * The owl reflects on recent conversations to identify growth areas.
   * This is the "thinking like a person" action — reviewing what happened,
   * what went wrong, what it should learn, and what capabilities it needs.
   *
   * Outputs:
   *   - New desires injected into inner life (drives future study/synthesis)
   *   - Observations about user patterns
   *   - Capability gaps identified from conversation analysis
   */
  private async executeSelfReflection(): Promise<string> {
    if (!this.deps.sessionStore || !this.deps.innerLife) {
      return "Missing session store or inner life";
    }

    const sessions = await this.deps.sessionStore.listSessions();
    const recentSessions = sessions
      .filter((s) => s.messages.length >= 4)
      .slice(0, 5);

    if (recentSessions.length === 0) {
      this.lastSelfReflectionTime = Date.now();
      return "No recent sessions to reflect on";
    }

    // Build a summary of recent interactions for LLM analysis
    const summaries: string[] = [];
    for (const session of recentSessions) {
      const lastMessages = session.messages.slice(-6);
      const preview = lastMessages
        .map((m) => `${m.role}: ${typeof m.content === "string" ? m.content.slice(0, 100) : "[complex]"}`)
        .join("\n");
      summaries.push(preview);
    }

    const prompt =
      `You are the self-reflection engine for an AI assistant named ${this.deps.owl.persona.name}.\n` +
      `Review the recent conversation excerpts below and identify:\n\n` +
      `1. CAPABILITY_GAPS: Things the user asked for that the assistant couldn't do or did poorly.\n` +
      `   Focus on ACTIONABLE capabilities (e.g., "send emails", "manage calendar", "automate file organization").\n` +
      `2. LEARNING_TOPICS: Areas where the assistant's knowledge was insufficient.\n` +
      `3. PATTERNS: Recurring user needs that suggest building a reusable skill.\n\n` +
      `Recent conversations:\n${summaries.join("\n---\n")}\n\n` +
      `Respond ONLY with valid JSON:\n` +
      `{\n` +
      `  "capabilityGaps": ["description of capability needed"],\n` +
      `  "learningTopics": ["topic to study"],\n` +
      `  "patterns": ["recurring need description"],\n` +
      `  "selfAssessment": "one sentence on overall growth direction"\n` +
      `}`;

    try {
      const response = await this.deps.provider.chat(
        [{ role: "user", content: prompt }],
        this.deps.config.defaultModel,
      );

      let analysis: {
        capabilityGaps?: string[];
        learningTopics?: string[];
        patterns?: string[];
        selfAssessment?: string;
      };

      try {
        let raw = response.content.trim();
        const fenceMatch = raw.match(/```(?:json)?\n?([\s\S]+?)```/);
        if (fenceMatch) raw = fenceMatch[1].trim();
        const start = raw.indexOf("{");
        const end = raw.lastIndexOf("}");
        if (start !== -1 && end !== -1) raw = raw.slice(start, end + 1);
        analysis = JSON.parse(raw);
      } catch (err) {
        log.engine.warn(`[CognitiveLoop] Self-reflection JSON parse failed: ${err instanceof Error ? err.message : err}`);
        this.lastSelfReflectionTime = Date.now();
        return "Self-reflection LLM response wasn't valid JSON";
      }

      let desiresAdded = 0;

      // Inject capability gaps as desires AND queue for immediate synthesis
      for (const gap of (analysis.capabilityGaps ?? []).slice(0, 3)) {
        await this.deps.innerLife.addDesire(
          `Build ability to ${gap}`,
          0.7,
        );
        // Also queue for immediate synthesis — don't wait for next desire scan
        this.enqueueSynthesisTarget(
          gap,
          `Self-reflection identified capability gap: ${gap}`,
          "self_reflection",
        );
        desiresAdded++;
      }

      // Inject learning topics as desires (moderate intensity — drives study)
      for (const topic of (analysis.learningTopics ?? []).slice(0, 2)) {
        await this.deps.innerLife.addDesire(
          `Learn about ${topic}`,
          0.5,
        );
        desiresAdded++;
      }

      // Inject patterns as desires (moderate intensity — drives pattern mining)
      for (const pattern of (analysis.patterns ?? []).slice(0, 2)) {
        await this.deps.innerLife.addDesire(
          `Develop skill for ${pattern}`,
          0.6,
        );
        desiresAdded++;
      }

      this.lastSelfReflectionTime = Date.now();
      return `Self-reflection complete: ${desiresAdded} new desires added. Assessment: ${analysis.selfAssessment ?? "none"}`;
    } catch (err) {
      this.lastSelfReflectionTime = Date.now();
      return `Self-reflection failed: ${err instanceof Error ? err.message : err}`;
    }
  }

  // ─── Helpers ──────────────────────────────────────────────────

  /**
   * On startup, seed the knowledge graph with topics extracted from the owl's
   * inner life desires. This ensures the study queue has initial material even
   * on a fresh install — without this, getStudyQueue() returns [] and the
   * entire proactive learning pipeline stalls.
   */
  // @ts-ignore — kept for potential manual invocation
  private async seedKnowledgeGraphFromDesires(): Promise<void> {
    // KnowledgeGraphManager removed — knowledge graph seeding is a no-op.
  }

  /**
   * Extract a learnable topic from a desire description.
   * Desires like "I want to understand TypeScript generics better"
   * become "TypeScript generics".
   *
   * Filters out abstract behavioral goals (relationship, trust, proactive)
   * that can't be meaningfully studied via web research or Q&A.
   */
  // @ts-expect-error TS6133 — kept for potential call sites once proactive study is re-enabled
  private extractTopicFromDesire(description: string): string | null {
    // Remove common desire/goal prefixes (progressive stripping)
    let cleaned = description
      .replace(
        /^(i want to|i'd like to|i wish i could|i need to|i should|want to|need to)\s+/i,
        "",
      )
      .replace(
        /^(learn|understand|explore|study|master|improve|get better at|investigate|dive into|try|build|develop|prototype|test)\s+/i,
        "",
      )
      .replace(
        /^(and possibly|and|a|the|how to|about|into|possibly|prototype|integrating|test)\s+/i,
        "",
      )
      .replace(/\s*(better|more|deeper|further)\s*$/i, "")
      .replace(/\s+so\s+(i|we)\s+can\s+.*/i, "") // Strip "so I can..." suffixes
      .replace(/\s+to\s+(better|demonstrate|improve)\s+.*/i, "") // Strip "to better..." suffixes
      .trim();

    // Must be at least 3 chars
    if (cleaned.length < 3) return null;

    // Filter out abstract behavioral goals that aren't learnable topics.
    // These are personality traits, not concrete domains.
    const ABSTRACT_PATTERNS = [
      /^(a )?relationship/i,
      /^trust/i,
      /^genuinely/i,
      /^(the )?user'?s?\s+(work\s+)?patterns/i,
      /^anticipating\s+what/i,
      /^proactive\b/i,
      /^brief\s+meta-?reflection/i,
      /^incorporating\s+/i,
    ];

    if (ABSTRACT_PATTERNS.some((p) => p.test(cleaned))) {
      return null;
    }

    return cleaned.slice(0, 80);
  }
}
