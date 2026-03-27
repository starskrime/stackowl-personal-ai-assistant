/**
 * StackOwl — Post-Processor
 *
 * Extracted from gateway/core.ts. Runs background tasks after every response:
 * learning, evolution, micro-learning, anticipation, knowledge extraction,
 * pattern analysis, trust chain persistence.
 *
 * Now uses TaskQueue instead of fire-and-forget promises.
 */

import type { ChatMessage } from "../../providers/base.js";
import type { GatewayContext } from "../types.js";
import type { TaskQueue } from "../../queue/task-queue.js";
import type { EventBus } from "../../events/bus.js";
import type { CostTracker } from "../../costs/tracker.js";
import type { SelfLearningCoordinator } from "../../learning/coordinator.js";
import type { ProactiveAnticipator } from "../../learning/anticipator.js";
import type { InnerLifeDNABridge } from "../../owls/inner-bridge.js";
import { log } from "../../logger.js";

export class PostProcessor {
  private messageCount = 0;

  constructor(
    private ctx: GatewayContext,
    private taskQueue: TaskQueue,
    private eventBus: EventBus | null,
    private coordinator: SelfLearningCoordinator | null,
    private anticipator: ProactiveAnticipator | null,
    private costTracker: CostTracker | null,
    private innerLifeBridge: InnerLifeDNABridge | null = null,
  ) {}

  /**
   * Run all post-processing tasks after a response.
   * Tasks are enqueued into the TaskQueue for bounded parallel execution.
   */
  process(
    messages: ChatMessage[],
    sessionId?: string,
    metadata?: {
      channelId?: string;
      userId?: string;
      owlName?: string;
      toolsUsed?: string[];
      usage?: { promptTokens: number; completionTokens: number };
      model?: string;
      provider?: string;
    },
  ): void {
    this.messageCount++;

    // Emit event
    if (this.eventBus && sessionId && metadata) {
      this.eventBus.emit("message:responded", {
        sessionId,
        channelId: metadata.channelId ?? "",
        userId: metadata.userId ?? "",
        content: messages[messages.length - 1]?.content ?? "",
        owlName: metadata.owlName ?? "",
        toolsUsed: metadata.toolsUsed ?? [],
        usage: metadata.usage
          ? {
              ...metadata.usage,
              totalTokens:
                metadata.usage.promptTokens + metadata.usage.completionTokens,
            }
          : undefined,
        messages: messages.map((m) => ({ role: m.role, content: m.content })),
      });
    }

    // Track costs
    if (
      this.costTracker &&
      metadata?.usage &&
      metadata?.provider &&
      metadata?.model &&
      sessionId
    ) {
      this.costTracker.record(
        metadata.provider,
        metadata.model,
        metadata.usage.promptTokens,
        metadata.usage.completionTokens,
        sessionId,
        metadata.userId ?? "unknown",
      );
    }

    // Learning — prefer new orchestrator (TopicFusion + Synthesis), fallback to legacy
    if (this.ctx.learningOrchestrator) {
      this.taskQueue.enqueue("learning-orchestrator", async () => {
        const cycle =
          await this.ctx.learningOrchestrator!.processConversation(messages);
        if (cycle.error) {
          log.engine.warn(
            `[PostProcessor:learning] Orchestrator error: ${cycle.error}`,
          );
        } else if (cycle.synthesisReport) {
          const r = cycle.synthesisReport;
          log.engine.info(
            `[PostProcessor:learning] ${r.pelletsCreated} pellets from ${r.successful}/${r.totalTopics} topics ` +
              `(${r.failed} failed) in ${r.durationMs}ms`,
          );
        } else {
          log.engine.info(
            `[PostProcessor:learning] Completed — ${cycle.topicsPrioritized} topics prioritized, no synthesis needed`,
          );
        }
      });
    } else if (this.ctx.learningEngine) {
      this.taskQueue.enqueue("learning", async () => {
        await this.ctx.learningEngine!.processConversation(messages);
        log.engine.info("[PostProcessor:learning] Legacy engine completed");
      });
    }

    // DNA evolution (every N messages) — now gated by MutationTracker
    const evolutionInterval = this.ctx.config.owlDna?.evolutionBatchSize ?? 10;
    if (
      this.messageCount % evolutionInterval === 0 &&
      this.ctx.evolutionEngine
    ) {
      this.taskQueue.enqueue(
        `dna-evolve(${this.ctx.owl.persona.name})`,
        async () => {
          // Gate: check MutationTracker analysis before mutating
          const analysis = this.coordinator?.gateEvolution();
          if (analysis?.recommendedAction === "freeze") {
            log.evolution.info(
              `[PostProcessor] Evolution frozen for ${this.ctx.owl.persona.name}: ${analysis.oscillations.recommendation}`,
            );
            return;
          }
          if (analysis?.recommendedAction === "rollback") {
            log.evolution.warn(
              `[PostProcessor] Pre-evolution rollback triggered for ${this.ctx.owl.persona.name}`,
            );
            // Rollback handled by coordinator's signal bus subscriber automatically
          }

          // Record pre-mutation DNA state
          const recordId = this.coordinator?.recordMutationStart(
            this.ctx.owl.dna,
          );

          // Run evolution
          const mutated = await this.ctx.evolutionEngine!.evolve(
            this.ctx.owl.persona.name,
          );

          // Record post-mutation confirmation with mutations that occurred
          if (recordId && mutated) {
            const updatedOwl = this.ctx.owlRegistry?.get(
              this.ctx.owl.persona.name,
            );
            const mutations: string[] = [];
            if (updatedOwl) {
              // Extract mutations from evolution log
              const log = updatedOwl.dna.evolutionLog;
              const lastEntry = log[log.length - 1];
              if (lastEntry) {
                mutations.push(...lastEntry.mutations);
              }
            }
            await this.coordinator?.recordMutationEnd(
              recordId,
              updatedOwl?.dna ?? this.ctx.owl.dna,
              mutations,
            );
          }
        },
      );
    }

    // Inner Life → DNA Bridge (every 5 messages)
    // Syncs opinions, desires, and mood into DNA mutations so inner life
    // actually influences future behavior instead of being decorative.
    if (this.innerLifeBridge && this.ctx.innerLife && this.messageCount % 5 === 0) {
      this.taskQueue.enqueue("inner-life-dna-sync", async () => {
        const innerState = this.ctx.innerLife!.getState();
        if (!innerState) return; // State not loaded yet
        const feedback = await this.innerLifeBridge!.sync(
          this.ctx.owl.persona.name,
          innerState,
        );
        if (
          feedback.preferencesUpdated.length > 0 ||
          feedback.expertiseSignals.length > 0 ||
          feedback.traitAdjustments.length > 0
        ) {
          log.evolution.info(
            `[PostProcessor:innerLife→DNA] ${feedback.preferencesUpdated.length} prefs, ` +
              `${feedback.expertiseSignals.length} expertise, ` +
              `${feedback.traitAdjustments.length} traits synced`,
          );
        }
      });
    }

    // Micro-learning (every message, zero LLM cost)
    // SelfLearningCoordinator wires: MicroLearner → SignalBus → MutationTracker + UserPreferenceModel
    if (this.coordinator) {
      const lastUserMsg = [...messages]
        .reverse()
        .find((m) => m.role === "user");
      if (lastUserMsg) {
        const lastAssistantMsg = [...messages]
          .reverse()
          .find((m) => m.role === "assistant");
        const toolsUsed: string[] = [];
        if (lastAssistantMsg?.content) {
          const toolMatches = lastAssistantMsg.content.match(
            /\btool[_\s]?(?:call|use|execute)[:\s]+["']?(\w+)/gi,
          );
          if (toolMatches) {
            for (const match of toolMatches) {
              const name = match.replace(/.*?["']?(\w+)["']?$/, "$1");
              if (name) toolsUsed.push(name);
            }
          }
        }
        this.coordinator.processMessage(
          lastUserMsg.content,
          toolsUsed.length > 0 ? toolsUsed : undefined,
          metadata?.channelId,
        );
      }

      if (this.messageCount % 5 === 0) {
        this.taskQueue.enqueue("coordinator-save", () =>
          this.coordinator!.save(),
        );
      }
    }

    // UserPreferenceModel → DNA learnedPreferences feedback loop
    // When UserPreferenceModel infers a high-confidence preference, reflect it in DNA
    if (this.coordinator && this.messageCount % 20 === 0) {
      this.taskQueue.enqueue("dna-preference-feedback", async () => {
        const highConf = this.coordinator!.flushHighConfidencePrefs(0.7);
        if (highConf.length === 0) return;

        const owl = this.ctx.owlRegistry?.get(this.ctx.owl.persona.name);
        if (!owl) return;

        let changed = false;
        for (const pref of highConf) {
          // Only migrate behavioral preferences (not system fields)
          if (
            [
              "msg_length_avg",
              "language",
              "uses_emoji",
              "preferred_response_length",
              "time_of_day_pattern",
              "message_type",
            ].includes(pref.key)
          ) {
            // Map to learnedPreferences with confidence as value
            const dnaKey = `inferred_${pref.key}`;
            const prev = owl.dna.learnedPreferences[dnaKey] ?? 0.5;
            // Weighted update: blend previous DNA value with new inference
            owl.dna.learnedPreferences[dnaKey] =
              prev * 0.7 + pref.confidence * 0.3;
            changed = true;
          }
        }

        if (changed) {
          await this.ctx.owlRegistry?.saveDNA(this.ctx.owl.persona.name);
          log.evolution.info(
            `[PostProcessor] Applied ${highConf.length} high-confidence preference(s) to DNA for ${this.ctx.owl.persona.name}`,
          );
        }
      });
    }

    // Proactive anticipation → learning pipeline (every 20 messages)
    // Anticipations with high confidence that aren't covered by existing skills
    // are fed into the learning orchestrator as high-priority topics so the
    // assistant proactively learns about them before the user explicitly asks.
    if (this.anticipator && this.messageCount % 20 === 0) {
      const existingSkills =
        this.ctx.skillsLoader?.getRegistry()?.listEnabled() ?? [];
      this.taskQueue.enqueue("anticipation", async () => {
        const anticipations =
          await this.anticipator!.anticipate(existingSkills);
        if (anticipations.length > 0) {
          log.engine.info(
            `[Anticipator] ${anticipations.length} anticipations: ` +
              anticipations
                .map(
                  (a) =>
                    `${a.capability} (${(a.confidence * 100).toFixed(0)}%)`,
                )
                .join(", "),
          );

          // Feed high-confidence anticipations into learning orchestrator
          // so the owl proactively studies capabilities before they're needed.
          if (this.ctx.learningOrchestrator) {
            const highConfidence = anticipations.filter((a) => a.confidence >= 0.7);
            for (const a of highConfidence.slice(0, 3)) {
              await this.ctx.learningOrchestrator.learnTopic(
                a.capability,
                false, // quick study, not deep research
              ).catch((err) => {
                log.engine.warn(
                  `[PostProcessor] Proactive learning failed for "${a.capability}": ${err}`,
                );
              });
            }
          }
        }
      });
    }

    // Timeline auto-snapshot (every 10 messages)
    if (this.ctx.timelineManager && sessionId) {
      const snapshot = this.ctx.timelineManager.autoSnapshot(
        sessionId,
        messages,
        this.ctx.owl.persona.name,
      );
      if (snapshot) {
        this.taskQueue.enqueue("timeline-snapshot", () =>
          this.ctx.timelineManager!.save(),
        );
      }
    }

    // Knowledge extraction (every 5 messages)
    if (
      this.ctx.knowledgeReasoner &&
      messages.length > 0 &&
      this.messageCount % 5 === 0
    ) {
      this.taskQueue.enqueue("knowledge-extract", async () => {
        await this.ctx.knowledgeReasoner!.extractFromConversation(messages);
        await this.ctx.knowledgeGraph?.save();
      });
    }

    // Fact extraction from conversations (Mem0-inspired memory layer)
    // Extract structured facts every 10 messages when extractor is available
    if (
      this.ctx.factExtractor &&
      this.ctx.factStore &&
      messages.length > 0 &&
      this.messageCount % 10 === 0
    ) {
      this.taskQueue.enqueue("fact-extract", async () => {
        const userId = metadata?.userId ?? "default";
        const extracted = await this.ctx.factExtractor!.extract(
          messages,
          userId,
        );
        if (extracted.length > 0) {
          await this.ctx.factStore!.addBatch(extracted);
          log.memory.info(
            `[PostProcessor] Extracted ${extracted.length} facts from conversation`,
          );
        }
      });
    }

    // Memory feedback decay (every 50 messages)
    if (this.ctx.memoryFeedback && this.messageCount % 50 === 0) {
      this.taskQueue.enqueue("memory-decay", async () => {
        const result = await this.ctx.memoryFeedback!.decayConfidence();
        if (result.decayed > 0 || result.removed > 0) {
          log.memory.info(
            `[PostProcessor] Memory decay: ${result.decayed} adjusted, ${result.removed} removed`,
          );
        }
      });
    }

    // Pattern recording
    if (this.ctx.patternAnalyzer) {
      const lastUserMsg = [...messages]
        .reverse()
        .find((m) => m.role === "user");
      if (lastUserMsg) {
        this.ctx.patternAnalyzer.recordAction(
          lastUserMsg.content.slice(0, 100),
          [],
        );
      }

      if (this.coordinator && this.messageCount % 15 === 0) {
        const profile = this.coordinator.getMicroLearnerProfile();
        this.ctx.patternAnalyzer?.enrichFromProfile(profile);
      }
    }

    // Periodic persistence (every 10 messages)
    if (this.messageCount % 10 === 0) {
      if (this.ctx.patternAnalyzer) {
        this.taskQueue.enqueue("pattern-save", () =>
          this.ctx.patternAnalyzer!.save(),
        );
      }
      if (this.ctx.trustChain) {
        this.taskQueue.enqueue("trust-save", () => this.ctx.trustChain!.save());
      }
      if (this.ctx.predictiveQueue) {
        this.taskQueue.enqueue("predictive-prep", async () => {
          const newTasks =
            await this.ctx.predictiveQueue!.generatePredictions();
          for (const task of newTasks) {
            await this.ctx.predictiveQueue!.prepareTask(task.id);
          }
        });
      }
    }
  }

  getMessageCount(): number {
    return this.messageCount;
  }
}
