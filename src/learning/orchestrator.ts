/**
 * StackOwl — Learning Orchestrator
 * Unified learning system combining TopicFusion, Synthesis, and Memory.
 */

import { join } from "node:path";
import { log } from "../logger.js";
import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { PelletStore } from "../pellets/store.js";
import type { ProviderRegistry } from "../providers/registry.js";

import { ConversationExtractor } from "./extractor.js";
import { KnowledgeGraphManager } from "./knowledge-graph.js";
import { SelfHealer } from "./self-healer.js";
import { TopicFusionEngine } from "./topic-fusion.js";
import type { FusedTopic } from "./topic-fusion.js";
import { KnowledgeSynthesizer } from "./synthesizer.js";
import type { SynthesisReport } from "./synthesizer.js";
import { MemoryReflexionEngine } from "../memory/reflexion.js";
import type {
  ReflexionResult,
  ConsolidationResult,
} from "../memory/reflexion.js";

export interface LearningCycle {
  id: string;
  trigger: "reactive" | "scheduled" | "manual";
  startedAt: string;
  completedAt?: string;
  insightsExtracted: number;
  topicsPrioritized: number;
  criticalTopics: number;
  synthesisReport?: SynthesisReport;
  memoryResult?: ConsolidationResult;
  reflexionResult?: ReflexionResult;
  durationMs: number;
  success: boolean;
  error?: string;
}

export interface LearningStats {
  totalCycles: number;
  reactiveCycles: number;
  proactiveCycles: number;
  totalTopicsStudied: number;
  totalPelletsCreated: number;
  memoryEntries: number;
  knowledgeDomains: number;
  lastCycle: string;
}

type ErrorClass = "timeout" | "rate_limit" | "parse" | "network" | "unknown";

function classifyError(err: unknown): ErrorClass {
  const msg =
    err instanceof Error
      ? err.message.toLowerCase()
      : String(err).toLowerCase();
  if (msg.includes("timeout")) return "timeout";
  if (msg.includes("rate") || msg.includes("429")) return "rate_limit";
  if (msg.includes("json") || msg.includes("parse")) return "parse";
  if (msg.includes("network") || msg.includes("fetch failed")) return "network";
  return "unknown";
}

export class LearningOrchestrator {
  private extractor: ConversationExtractor;
  private fusionEngine: TopicFusionEngine;
  private graphManager: KnowledgeGraphManager;
  private synthesizer?: KnowledgeSynthesizer;
  private reflexionEngine?: MemoryReflexionEngine;
  private selfHealer?: SelfHealer;

  /**
   * Callback for notifying external systems (e.g., CognitiveLoop) about
   * capability gaps discovered during conversation analysis. This bridges
   * the knowledge system (pellets) with the skill system (SKILL.md synthesis).
   */
  private onCapabilityGap?: (gap: string, description: string) => void;

  private cycles: LearningCycle[] = [];
  private stats: LearningStats = {
    totalCycles: 0,
    reactiveCycles: 0,
    proactiveCycles: 0,
    totalTopicsStudied: 0,
    totalPelletsCreated: 0,
    memoryEntries: 0,
    knowledgeDomains: 0,
    lastCycle: "",
  };

  constructor(
    provider: ModelProvider,
    owl: OwlInstance,
    config: StackOwlConfig,
    pelletStore: PelletStore,
    workspacePath: string,
    providerRegistry?: ProviderRegistry,
  ) {
    this.extractor = new ConversationExtractor(provider);
    this.fusionEngine = new TopicFusionEngine();
    this.graphManager = new KnowledgeGraphManager(workspacePath);
    this.synthesizer = new KnowledgeSynthesizer(
      provider,
      owl,
      config,
      pelletStore,
      workspacePath,
    );
    this.reflexionEngine = new MemoryReflexionEngine(
      workspacePath,
      provider,
      owl,
    );
    if (providerRegistry) {
      this.selfHealer = new SelfHealer(
        providerRegistry,
        join(workspacePath, ".."),
        workspacePath,
      );
    }
  }

  /**
   * Register a callback for capability gaps discovered from conversations.
   * The CognitiveLoop uses this to feed gaps into its synthesis queue.
   */
  setCapabilityGapCallback(
    cb: (gap: string, description: string) => void,
  ): void {
    this.onCapabilityGap = cb;
  }

  async processConversation(messages: ChatMessage[]): Promise<LearningCycle> {
    const cycleId = `reactive_${Date.now()}`;
    const startTime = Date.now();

    const cycle: LearningCycle = {
      id: cycleId,
      trigger: "reactive",
      startedAt: new Date().toISOString(),
      insightsExtracted: 0,
      topicsPrioritized: 0,
      criticalTopics: 0,
      durationMs: 0,
      success: false,
    };

    try {
      const insights = await this.extractor.extract(messages);
      insights.timestamp = new Date().toISOString();
      cycle.insightsExtracted =
        insights.topics.length + insights.knowledgeGaps.length;

      if (cycle.insightsExtracted === 0) {
        cycle.success = true;
        cycle.durationMs = Date.now() - startTime;
        return this.recordCycle(cycle);
      }

      // Notify CognitiveLoop about capability gaps for skill synthesis.
      // Knowledge gaps like "couldn't send email" or "didn't know how to
      // automate file backup" become synthesis targets.
      if (this.onCapabilityGap && insights.knowledgeGaps.length > 0) {
        for (const gap of insights.knowledgeGaps) {
          this.onCapabilityGap(
            gap,
            `Conversation analysis: assistant couldn't do "${gap}"`,
          );
        }
      }

      await this.graphManager.load();

      const graph = this.graphManager.getGraph();
      const fusion = await this.fusionEngine.fuse([insights], graph);

      cycle.topicsPrioritized = fusion.fusedTopics.length;
      cycle.criticalTopics = fusion.stats.criticalCount;

      await this.reflexionEngine?.consolidate(messages, cycleId);

      // Two-tier synthesis:
      //   Tier 1 (urgency >= 25 or priority override): Full synthesis into pellets
      //     HARD CAP: max 2 topics per cycle to prevent token burn (300-500 calls)
      //   Tier 2 (urgency < 25): Register in knowledge graph for future study
      // This is reactive — only runs after actual user conversations, not proactively.
      const MAX_SYNTHESIZE_TOPICS = 2;
      const synthesizableTopics = fusion.fusedTopics
        .filter(
          (t: FusedTopic) => t.urgency >= 25 || t.priorityOverride === "critical" || t.priorityOverride === "high",
        )
        .sort((a: FusedTopic, b: FusedTopic) => b.urgency - a.urgency)
        .slice(0, MAX_SYNTHESIZE_TOPICS);
      const touchOnlyTopics = fusion.fusedTopics.filter(
        (t: FusedTopic) =>
          !synthesizableTopics.includes(t),
      );

      if (synthesizableTopics.length > 0 && this.synthesizer) {
        const report = await this.synthesizer.synthesize(synthesizableTopics);
        cycle.synthesisReport = report;
        this.stats.totalTopicsStudied += report.successful;
        this.stats.totalPelletsCreated += report.pelletsCreated;
      }

      // Register remaining topics in the knowledge graph for future reference
      if (touchOnlyTopics.length > 0) {
        for (const topic of touchOnlyTopics) {
          this.graphManager.touchDomain(topic.normalizedName, "conversation");
        }
        await this.graphManager.save();
      }

      cycle.success = true;
    } catch (err) {
      const errClass = classifyError(err);
      log.evolution.error(
        `[Orchestrator] Reactive learning FAILED (${errClass}):\n` +
          `${err instanceof Error ? `${err.message}\n${err.stack}` : err}`,
      );
      if (this.selfHealer && errClass !== "parse") {
        await this.selfHealer.heal({
          subsystem: "learning",
          operation: "reactive",
          error: err instanceof Error ? err : new Error(String(err)),
        });
      }
      cycle.error = String(err);
    }

    cycle.completedAt = new Date().toISOString();
    cycle.durationMs = Date.now() - startTime;
    return this.recordCycle(cycle);
  }

  /**
   * Proactive learning session — DISABLED.
   * Previously deep-researched random knowledge graph topics, burning tokens.
   * Learning now only happens reactively (on failure via synthesis queue).
   * Kept as no-op for backward compatibility.
   */
  async runProactiveSession(): Promise<LearningCycle> {
    const now = new Date().toISOString();
    return this.recordCycle({
      id: `proactive_${Date.now()}`,
      trigger: "scheduled",
      startedAt: now,
      completedAt: now,
      insightsExtracted: 0,
      topicsPrioritized: 0,
      criticalTopics: 0,
      durationMs: 0,
      success: true,
    });
  }

  async learnTopic(
    topic: string,
    deepResearch: boolean = true,
  ): Promise<LearningCycle> {
    const cycleId = `manual_${Date.now()}`;
    const startTime = Date.now();

    const cycle: LearningCycle = {
      id: cycleId,
      trigger: "manual",
      startedAt: new Date().toISOString(),
      insightsExtracted: 0,
      topicsPrioritized: 1,
      criticalTopics: deepResearch ? 1 : 0,
      durationMs: 0,
      success: false,
    };

    try {
      await this.graphManager.load();

      const fusedTopic: FusedTopic = {
        id: topic,
        normalizedName: topic.toLowerCase().replace(/\s+/g, "-"),
        displayName: topic,
        urgency: deepResearch ? 80 : 50,
        sourceSignals: ["question"],
        originalSignals: [topic],
        lastSeen: new Date().toISOString(),
        failureCount: 0,
        relatedDomains: [],
        synthesisStrategy: deepResearch ? "deep_research" : "q_and_a",
        priorityOverride: "high",
        sourceInsights: [],
      };

      if (this.synthesizer) {
        const report = await this.synthesizer.synthesize([fusedTopic]);
        cycle.synthesisReport = report;
        this.stats.totalTopicsStudied += report.successful;
        this.stats.totalPelletsCreated += report.pelletsCreated;
      }

      this.graphManager.touchDomain(fusedTopic.normalizedName, "self-study");
      await this.graphManager.save();

      cycle.success = true;
    } catch (err) {
      log.evolution.error(
        `[Orchestrator] Manual learning FAILED for "${topic}":\n` +
          `${err instanceof Error ? `${err.message}\n${err.stack}` : err}`,
      );
      cycle.error = String(err);
    }

    cycle.completedAt = new Date().toISOString();
    cycle.durationMs = Date.now() - startTime;
    return this.recordCycle(cycle);
  }

  getStats(): LearningStats {
    const graphStats = this.graphManager.getStats();
    return {
      ...this.stats,
      memoryEntries: this.reflexionEngine?.getStats().total ?? 0,
      knowledgeDomains: graphStats.totalDomains,
      lastCycle: this.cycles[this.cycles.length - 1]?.completedAt ?? "",
    };
  }

  async getFullReport(): Promise<string> {
    const stats = this.getStats();
    const graphReport = this.graphManager.getFullReport();
    const memoryStats = this.reflexionEngine?.getStats();
    const recentCycles = this.cycles.slice(-5);

    const lines = [
      "## Learning Orchestrator Report",
      "",
      "### Stats",
      `- Total cycles: ${stats.totalCycles} (${stats.reactiveCycles} reactive, ${stats.proactiveCycles} proactive)`,
      `- Topics studied: ${stats.totalTopicsStudied}`,
      `- Pellets created: ${stats.totalPelletsCreated}`,
      `- Memory entries: ${stats.memoryEntries}`,
      `- Knowledge domains: ${stats.knowledgeDomains}`,
      `- Last cycle: ${stats.lastCycle || "never"}`,
      "",
      "### Knowledge Graph",
      graphReport,
      "",
      "### Memory",
      `- Total entries: ${memoryStats?.total ?? 0}`,
      `- Health: ${memoryStats?.health ?? "N/A"}%`,
      `- Last reflexion: ${memoryStats?.lastReflex ?? "never"}`,
      "",
      "### Recent Cycles",
    ];

    for (const c of recentCycles) {
      const status = c.success ? "✓" : "✗";
      const duration = Math.round(c.durationMs / 1000) + "s";
      lines.push(
        `${status} [${c.trigger}] ${c.completedAt?.slice(0, 19)} - ` +
          `${c.topicsPrioritized} topics, ${c.synthesisReport?.pelletsCreated ?? 0} pellets (${duration})`,
      );
    }

    return lines.join("\n");
  }

  private recordCycle(cycle: LearningCycle): LearningCycle {
    this.cycles.push(cycle);
    this.stats.totalCycles++;
    if (cycle.trigger === "reactive") this.stats.reactiveCycles++;
    else if (cycle.trigger === "scheduled") this.stats.proactiveCycles++;
    this.stats.lastCycle = cycle.completedAt ?? "";
    if (this.cycles.length > 100) this.cycles = this.cycles.slice(-100);
    return cycle;
  }

}
