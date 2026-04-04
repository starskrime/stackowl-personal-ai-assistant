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
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { OwlInnerLife } from "../owls/inner-life.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { LearningOrchestrator } from "../learning/orchestrator.js";
import type { LearningEngine } from "../learning/self-study.js";
import type { ReflexionEngine } from "../evolution/reflexion.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { SessionStore } from "../memory/store.js";
import type { PelletStore } from "../pellets/store.js";
import type { CapabilityLedger } from "../evolution/ledger.js";
import type { MicroLearner } from "../learning/micro-learner.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { OwlEvolutionEngine } from "../owls/evolution.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { SkillsLoader } from "../skills/loader.js";
import { CapabilityScanner } from "../heartbeat/capability-scanner.js";
import { SkillEvolver } from "../skills/evolver.js";
import { PatternMiner } from "../skills/pattern-miner.js";
import { KnowledgeGraphManager } from "../learning/knowledge-graph.js";

// ─── Types ───────────────────────────────────────────────────────

export type CognitiveAction =
  | "desire_driven_study"        // Study a topic from inner desires
  | "gap_driven_study"           // Study a topic from capability gaps
  | "autonomous_skill_synthesis" // Proactively create skills for anticipated needs
  | "self_reflection"            // Review failures & generate new desires/goals
  | "pattern_mining"             // Crystallize skills from conversation patterns
  | "skill_evolution"            // Improve existing skills
  | "reflexion_dream"            // Learn from past mistakes
  | "capability_scan"            // Discover unused platform features
  | "frontier_exploration"       // Explore adjacent domains to deepen knowledge
  | "memory_consolidation"       // Consolidate conversation memories
  | "tool_pruning"               // Archive/fix failing synthesized tools
  | "dna_evolution"              // Evolve owl DNA from accumulated interactions
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
  learningOrchestrator?: LearningOrchestrator;
  learningEngine?: LearningEngine;
  reflexionEngine?: ReflexionEngine;
  skillsRegistry?: SkillsRegistry;
  sessionStore?: SessionStore;
  pelletStore?: PelletStore;
  capabilityLedger?: CapabilityLedger;
  microLearner?: MicroLearner;
  toolRegistry?: ToolRegistry;
  skillsDir?: string;
  owlRegistry?: OwlRegistry;
  evolutionEngine?: OwlEvolutionEngine;
  providerRegistry?: ProviderRegistry;
  skillsLoader?: SkillsLoader;
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
  tickIntervalMinutes: 15,
  minIdleMinutes: 5,
  maxActionsPerDay: 20,
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
  private lastPatternMineTime = 0;
  // @ts-expect-error TS6133
  private lastSkillEvolveTime = 0;
  // @ts-expect-error TS6133
  private lastSelfReflectionTime = 0;
  // @ts-expect-error TS6133
  private lastAutonomousSynthesisTime = 0;
  private studySessionsSinceDnaSync: number = 0;
  private skillsCreatedToday: number = 0;
  private history: CognitiveTickResult[] = [];
  private capabilityScanner: CapabilityScanner | null = null;

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
    source: "conversation" | "self_reflection" | "capability_scan" | "skill_stats";
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
        deps.microLearner,
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
    check("learningOrchestrator", deps.learningOrchestrator);
    check("reflexionEngine", deps.reflexionEngine);
    check("skillsRegistry", deps.skillsRegistry);
    check("sessionStore", deps.sessionStore);
    check("pelletStore", deps.pelletStore);
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

    // Wire the learning orchestrator's capability gap callback so that
    // knowledge gaps discovered from conversation analysis (e.g., "couldn't send email")
    // automatically enter the synthesis queue for autonomous skill creation.
    if (this.deps.learningOrchestrator) {
      this.deps.learningOrchestrator.setCapabilityGapCallback(
        (gap, description) => this.enqueueSynthesisTarget(gap, description, "conversation"),
      );
    }

    // NOTE: Desire seeding removed — the loop only learns reactively now.
    // Learning is triggered by actual failures (synthesis queue from conversation
    // gaps), not proactive exploration of random topics.

    log.engine.info(
      `[CognitiveLoop] Started — ticking every ${this.config.tickIntervalMinutes} min`,
    );

    // Run first tick after a short warmup delay (30s) instead of waiting
    // a full interval. This ensures the loop does something on startup.
    const warmupTimeout = setTimeout(() => {
      this.tick().catch((err) => {
        log.engine.error(
          `[CognitiveLoop] Initial tick error: ${err instanceof Error ? err.message : err}`,
        );
      });
    }, 30_000);
    warmupTimeout.unref();

    this.timer = setInterval(
      () => this.tick().catch((err) => {
        log.engine.error(
          `[CognitiveLoop] Tick error: ${err instanceof Error ? err.message : err}`,
        );
      }),
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
    source: "conversation" | "self_reflection" | "capability_scan" | "skill_stats" = "conversation",
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
      result = {
        action: decision.action,
        success: true,
        detail,
        durationMs: Date.now() - startTime,
      };
      this.actionsToday++;
    } catch (err) {
      result = {
        action: decision.action,
        success: false,
        detail: err instanceof Error ? err.message : String(err),
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

    // ─── REMOVED (proactive token burners) ──────────────────────
    // desire_driven_study    — studied random desires
    // gap_driven_study       — proactive capability gap exploration
    // self_reflection        — generated new desires (fed more random study)
    // pattern_mining         — mined patterns into skills proactively
    // skill_evolution        — critiqued/rewrote skills proactively
    // capability_scan        — discovered unused features proactively
    // frontier_exploration   — deep-researched random knowledge graph topics

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

      case "pattern_mining":
        return this.executePatternMining();

      case "skill_evolution":
        return this.executeSkillEvolution();

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

      default:
        return "No action taken";
    }
  }

  private async executeDesireStudy(topic: string): Promise<string> {
    if (!this.deps.learningOrchestrator) return "No orchestrator available";

    const cycle = await this.deps.learningOrchestrator.learnTopic(topic, true);
    this.studySessionsSinceDnaSync++;

    // Evolve desires: reduce intensity of studied topic, add related desires
    if (this.deps.innerLife && cycle.success) {
      await this.deps.innerLife.fulfillDesire(topic);
    }

    if (cycle.synthesisReport) {
      return `Studied "${topic}": ${cycle.synthesisReport.pelletsCreated} pellets created`;
    }
    return `Studied "${topic}": no pellets (${cycle.error ?? "empty synthesis"})`;
  }

  private async executeGapStudy(topic: string): Promise<string> {
    if (!this.deps.learningOrchestrator) return "No orchestrator available";

    const cycle = await this.deps.learningOrchestrator.learnTopic(topic, false);
    this.studySessionsSinceDnaSync++;

    return `Gap study "${topic}": ${cycle.synthesisReport?.pelletsCreated ?? 0} pellets`;
  }

  private async executeReflexion(): Promise<string> {
    if (!this.deps.reflexionEngine) return "No reflexion engine";

    await this.deps.reflexionEngine.dream();
    this.lastReflexionTime = Date.now();
    return "Reflexion dream completed — behavioral patches extracted";
  }

  private async executePatternMining(): Promise<string> {
    if (!this.deps.sessionStore || !this.deps.skillsRegistry || !this.deps.skillsDir) {
      return "Missing deps for pattern mining";
    }

    const miner = new PatternMiner(
      this.deps.provider,
      this.deps.sessionStore,
      this.deps.config,
    );
    const newSkills = await miner.mine(
      this.deps.skillsRegistry,
      this.deps.skillsDir,
    );
    this.lastPatternMineTime = Date.now();

    if (newSkills.length > 0) {
      return `Mined ${newSkills.length} new skill(s): [${newSkills.join(", ")}]`;
    }
    return "No new patterns found";
  }

  private async executeSkillEvolution(): Promise<string> {
    if (!this.deps.skillsRegistry) return "No skills registry";

    const evolver = new SkillEvolver(this.deps.provider, this.deps.config);
    const report = await evolver.evolveAll(this.deps.skillsRegistry);
    this.lastSkillEvolveTime = Date.now();

    return `Skills evolved: ${report.improved}/${report.evaluated} improved, ${report.failed} failed`;
  }

  private async executeCapabilityScan(): Promise<string> {
    if (!this.capabilityScanner) return "No scanner available";

    const result = this.capabilityScanner.scan();
    this.lastCapScanTime = Date.now();

    // Feed high-priority gaps into learning pipeline
    const actionableGaps = result.gaps.filter((g) => g.priority >= 3);
    if (actionableGaps.length > 0 && this.deps.learningOrchestrator) {
      const topGap = actionableGaps[0];
      await this.deps.learningOrchestrator
        .learnTopic(topGap.description, false)
        .catch(() => {});
    }

    return `Scanned: ${result.totalToolsRegistered} tools, ${result.totalSkillsEnabled} skills, ${result.gaps.length} gaps (${result.coveragePercent}% coverage)`;
  }

  private async executeFrontierExploration(): Promise<string> {
    if (!this.deps.learningOrchestrator) return "No orchestrator";

    const cycle = await this.deps.learningOrchestrator.runProactiveSession();
    this.studySessionsSinceDnaSync++;

    if (cycle.synthesisReport) {
      return `Frontier: ${cycle.topicsPrioritized} topics, ${cycle.synthesisReport.pelletsCreated} pellets`;
    }
    return `Frontier: ${cycle.topicsPrioritized} topics queued`;
  }

  /**
   * Memory consolidation — extracts persistent facts and behavioral rules
   * from recent conversations. Replaces ProactivePinger's 3 AM job since
   * heartbeat is disabled. Uses MemoryConsolidator.extractAndAppend() which
   * saves facts to workspace/memory.md for injection into future prompts.
   */
  private async executeMemoryConsolidation(): Promise<string> {
    if (!this.deps.sessionStore) return "No session store";

    const { MemoryConsolidator } = await import("../memory/consolidator.js");
    const workspacePath = this.deps.config.workspace || "./workspace";

    const consolidator = new MemoryConsolidator(
      this.deps.provider,
      this.deps.owl,
      workspacePath,
    );

    // Load recent sessions and consolidate their messages
    const sessions = await this.deps.sessionStore.listSessions();
    const recentSessions = sessions
      .filter((s) => s.messages.length >= 4) // Only sessions with real conversation
      .slice(0, 5);

    let consolidated = 0;
    for (const session of recentSessions) {
      try {
        await consolidator.extractAndAppend(session.messages);
        consolidated++;
      } catch {
        // Non-fatal — continue with next session
      }
    }

    this.lastMemoryConsolidationTime = Date.now();
    return `Consolidated ${consolidated}/${recentSessions.length} recent sessions`;
  }

  /**
   * Tool pruning — scans the capability ledger for failing or unused
   * synthesized tools and attempts to fix or archive them. Replaces
   * ProactivePinger's 4-hourly job.
   */
  private async executeToolPruning(): Promise<string> {
    if (!this.deps.capabilityLedger) return "No capability ledger";

    const { ToolPruner } = await import("../evolution/pruner.js");
    const workspacePath = this.deps.config.workspace || "./workspace";

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

    // Resolve synthesis provider (prefer Anthropic for quality)
    const synthesisConfig = this.deps.config.synthesis;
    const providerName = synthesisConfig?.provider ?? "anthropic";
    const synthesisModel = synthesisConfig?.model ?? this.deps.config.defaultModel;
    let synthesisProvider = this.deps.provider;

    if (this.deps.providerRegistry) {
      try {
        synthesisProvider = this.deps.providerRegistry.get(providerName);
      } catch {
        // Fall back to default provider
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
      const workspacePath = this.deps.config.workspace || "./workspace";
      const tracker = new SkillTracker(workspacePath);
      await tracker.load();
      const failingSkills = tracker.getFailingSkills(3, 0.3);
      for (const { name, stats } of failingSkills.slice(0, 2)) {
        gapTargets.push({
          userRequest: name.replace(/_/g, " "),
          description: `Skill "${name}" selected ${stats.selectionCount}x but success rate is ${(stats.successRate * 100).toFixed(0)}% — needs re-synthesis`,
        });
      }
    } catch {
      // Non-fatal — skill tracker may not exist yet
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
      const workspacePath = this.deps.config.workspace || "./workspace";
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
    } catch {
      // Non-fatal — user profile may not exist
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
          } catch {
            // Non-fatal — skill exists on disk for next restart
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
      } catch {
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
    if (!this.deps.innerLife || !this.deps.config) return;

    const state = this.deps.innerLife.getState();
    if (!state || state.desires.length === 0) return;

    const workspacePath = this.deps.config.workspace || "./workspace";
    const graph = new KnowledgeGraphManager(workspacePath);
    await graph.load();

    let seeded = 0;
    for (const desire of state.desires) {
      const topic = this.extractTopicFromDesire(desire.description);
      if (topic) {
        graph.touchDomain(topic, "self-study");
        seeded++;
      }
    }

    // Also seed from personalGoals and currentThoughts
    for (const goal of state.personalGoals ?? []) {
      const topic = this.extractTopicFromDesire(goal);
      if (topic) {
        graph.touchDomain(topic, "self-study");
        seeded++;
      }
    }

    // ── Desire hygiene: decay abstract desires, boost capability desires ──
    // Abstract desires ("Build a relationship", "Anticipate user needs") can't
    // produce study topics or synthesis targets, yet they dominate the desire list
    // at high intensity. Decay them to make room for actionable desires.
    if (this.deps.innerLife) {
      const state = this.deps.innerLife.getState();
      if (state) {
        let decayed = 0;
        for (const desire of state.desires) {
          const topic = this.extractTopicFromDesire(desire.description);
          const isCap = isCapabilityDesire(desire.description);
          // If this desire can't produce a study topic AND isn't a capability target,
          // it's dead weight — decay it
          if (!topic && !isCap && desire.intensity > 0.3) {
            desire.intensity = Math.max(0.2, desire.intensity - 0.15);
            decayed++;
          }
        }
        if (decayed > 0) {
          await this.deps.innerLife.save();
          log.engine.info(
            `[CognitiveLoop] Decayed ${decayed} abstract/unactionable desires`,
          );
        }
      }

      // Inject capability-oriented desires if none exist.
      // These drive autonomous skill synthesis — without them, the owl
      // only learns knowledge (pellets) but never builds capabilities (skills).
      const freshState = this.deps.innerLife.getState();
      const hasCapabilityDesires = freshState?.desires.some((d) =>
        isCapabilityDesire(d.description),
      );

      if (!hasCapabilityDesires) {
        const bootstrapDesires = [
          "Build ability to automate recurring tasks the user asks for",
          "Develop skills for file and document management",
          "Create tools for information retrieval and summarization",
        ];
        for (const desc of bootstrapDesires) {
          await this.deps.innerLife.addDesire(desc, 0.5);
        }
        log.engine.info(
          `[CognitiveLoop] Injected ${bootstrapDesires.length} bootstrap capability desires`,
        );
      }
    }

    if (seeded > 0) {
      await graph.save();
      log.engine.info(
        `[CognitiveLoop] Seeded knowledge graph with ${seeded} topics from inner life desires`,
      );
    }
  }

  /**
   * Extract a learnable topic from a desire description.
   * Desires like "I want to understand TypeScript generics better"
   * become "TypeScript generics".
   *
   * Filters out abstract behavioral goals (relationship, trust, proactive)
   * that can't be meaningfully studied via web research or Q&A.
   */
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
