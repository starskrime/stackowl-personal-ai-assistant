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
  private lastCapScanTime: number = 0;
  private lastPatternMineTime: number = 0;
  private lastSkillEvolveTime: number = 0;
  private lastMemoryConsolidationTime: number = 0;
  private lastToolPruneTime: number = 0;
  private lastDnaEvolutionTime: number = 0;
  private lastSelfReflectionTime: number = 0;
  private lastAutonomousSynthesisTime: number = 0;
  private studySessionsSinceDnaSync: number = 0;
  private skillsCreatedToday: number = 0;
  private history: CognitiveTickResult[] = [];
  private capabilityScanner: CapabilityScanner | null = null;

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

    // Seed knowledge graph from inner life desires so the study queue
    // isn't empty on fresh start. Desires like "learn about X" become
    // initial study topics, giving the learning pipeline material to work with.
    this.seedKnowledgeGraphFromDesires().catch((err) => {
      log.engine.warn(
        `[CognitiveLoop] Desire seeding failed: ${err instanceof Error ? err.message : err}`,
      );
    });

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

    // 1. Desire-driven study — highest priority
    // The owl's inner desires represent genuine curiosity that should drive learning.
    if (this.deps.innerLife && this.deps.learningOrchestrator) {
      const state = this.deps.innerLife.getState();
      if (state) {
        const strongDesires = state.desires
          .filter((d) => d.intensity >= 0.5)
          .sort((a, b) => b.intensity - a.intensity);

        for (const desire of strongDesires.slice(0, 2)) {
          const topic = this.extractTopicFromDesire(desire.description);
          if (topic) {
            candidates.push({
              action: "desire_driven_study",
              reason: `Inner desire: "${desire.description.slice(0, 60)}"`,
              priority: 70 + Math.round(desire.intensity * 20), // 70-90
              topic,
            });
          }
        }
      }
    }

    // 2. Capability gap study — react to known weaknesses
    if (this.capabilityScanner && now - this.lastCapScanTime > 2 * HOUR_MS) {
      const scanResult = this.capabilityScanner.scan();
      const topGaps = scanResult.gaps.slice(0, 3);
      for (const gap of topGaps) {
        if (gap.suggestion) {
          candidates.push({
            action: "gap_driven_study",
            reason: `Capability gap: ${gap.description.slice(0, 60)}`,
            priority: 60 + gap.priority * 5,
            topic: gap.description,
          });
        }
      }
    }

    // 3. Autonomous skill synthesis — proactively create skills for known gaps
    // This is the KEY missing piece: the owl doesn't just study, it BUILDS capabilities.
    // Analyzes the capability ledger for recurring failures, inner life for capability desires,
    // and proactively synthesizes skills WITHOUT waiting for a user to hit the gap.
    if (
      this.deps.skillsDir &&
      this.deps.capabilityLedger &&
      this.skillsCreatedToday < 3 && // Max 3 skills per day to prevent spam
      now - this.lastAutonomousSynthesisTime > 3 * HOUR_MS
    ) {
      candidates.push({
        action: "autonomous_skill_synthesis",
        reason: "Proactively build skills for known capability gaps",
        priority: 75, // Higher than study — building > reading
      });
    }

    // 4. Self-reflection — review past failures, generate new desires and goals
    // The owl thinks: "What went wrong? What should I learn? What tools do I need?"
    // This feeds the desire system, which drives future study and synthesis.
    if (
      this.deps.sessionStore &&
      this.deps.innerLife &&
      now - this.lastSelfReflectionTime > 6 * HOUR_MS
    ) {
      candidates.push({
        action: "self_reflection",
        reason: "Reflect on past interactions to identify growth areas",
        priority: 65,
      });
    }

    // 5. DNA evolution — sync accumulated learning into personality
    // Trigger after every 5 study sessions to compound growth
    if (
      this.deps.evolutionEngine &&
      this.studySessionsSinceDnaSync >= 5 &&
      now - this.lastDnaEvolutionTime > 2 * HOUR_MS
    ) {
      candidates.push({
        action: "dna_evolution",
        reason: `${this.studySessionsSinceDnaSync} study sessions since last DNA sync`,
        priority: 58,
      });
    }

    // 6. Reflexion (dream) — learn from mistakes
    // Run at most once every 4 hours
    if (this.deps.reflexionEngine && now - this.lastReflexionTime > 4 * HOUR_MS) {
      candidates.push({
        action: "reflexion_dream",
        reason: "Reflect on past mistakes to extract behavioral rules",
        priority: 55,
      });
    }

    // 5. Pattern mining — crystallize skills from repeated tool sequences
    // Run at most once every 6 hours
    if (
      this.deps.sessionStore &&
      this.deps.skillsRegistry &&
      this.deps.skillsDir &&
      now - this.lastPatternMineTime > 6 * HOUR_MS
    ) {
      candidates.push({
        action: "pattern_mining",
        reason: "Mine successful tool patterns into reusable skills",
        priority: 50,
      });
    }

    // 6. Memory consolidation — extract persistent facts from sessions
    // Run at most once every 8 hours (replaces ProactivePinger's 3 AM job)
    if (
      this.deps.sessionStore &&
      now - this.lastMemoryConsolidationTime > 8 * HOUR_MS
    ) {
      candidates.push({
        action: "memory_consolidation",
        reason: "Consolidate conversation memories into persistent storage",
        priority: 45,
      });
    }

    // 7. Skill evolution — improve existing low-quality skills
    // Run at most once every 12 hours
    if (
      this.deps.skillsRegistry &&
      now - this.lastSkillEvolveTime > 12 * HOUR_MS
    ) {
      candidates.push({
        action: "skill_evolution",
        reason: "Critique and improve existing skills",
        priority: 40,
      });
    }

    // 8. Capability scan — discover unused features
    if (this.capabilityScanner && now - this.lastCapScanTime > 4 * HOUR_MS) {
      candidates.push({
        action: "capability_scan",
        reason: "Discover unused platform capabilities",
        priority: 35,
      });
    }

    // 9. Tool pruning — fix or archive failing synthesized tools
    // Run at most once every 8 hours (replaces ProactivePinger's 4h job)
    if (
      this.deps.capabilityLedger &&
      now - this.lastToolPruneTime > 8 * HOUR_MS
    ) {
      candidates.push({
        action: "tool_pruning",
        reason: "Scan synthesized tools for failures and prune/fix them",
        priority: 32,
      });
    }

    // 10. Frontier exploration — deepen knowledge in weak areas
    if (this.deps.learningOrchestrator) {
      candidates.push({
        action: "frontier_exploration",
        reason: "Explore and deepen knowledge in weakest domains",
        priority: 30,
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
    if (!this.deps.skillsDir || !this.deps.capabilityLedger) {
      return "Missing skillsDir or capability ledger";
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

    // ── Source 1: Capability ledger failures → skill synthesis targets ──
    const gapTargets: Array<{ userRequest: string; description: string }> = [];

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

    // ── Source 2: Inner life desires that look like capabilities ──
    if (this.deps.innerLife) {
      const state = this.deps.innerLife.getState();
      if (state) {
        const capabilityDesires = state.desires.filter(
          (d) =>
            d.intensity >= 0.5 &&
            /\b(ability|tool|skill|capability|automate|create|build|send|fetch|control|access)\b/i.test(
              d.description,
            ),
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

    if (gapTargets.length === 0) {
      this.lastAutonomousSynthesisTime = Date.now();
      return "No capability gaps found to synthesize";
    }

    // ── Dedup against existing skills ──
    const existingSkills = this.deps.skillsRegistry
      ? this.deps.skillsRegistry.listEnabled().map((s) => s.name.toLowerCase())
      : [];

    let created = 0;
    for (const target of gapTargets) {
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

        // Reload the skill into the registry so it's immediately available
        if (this.deps.skillsLoader) {
          try {
            await this.deps.skillsLoader.load({
              directories: [this.deps.skillsDir],
              watch: false,
            });
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

      // Inject capability gaps as desires (high intensity — these drive skill synthesis)
      for (const gap of (analysis.capabilityGaps ?? []).slice(0, 3)) {
        await this.deps.innerLife.addDesire(
          `Build ability to ${gap}`,
          0.7,
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
