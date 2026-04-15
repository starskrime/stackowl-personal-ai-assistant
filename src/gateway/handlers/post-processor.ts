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
      /** Quality signals from EngineResponse — used to record failures */
      loopExhausted?: boolean;
      toolFailureCount?: number;
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

    // ── Owl performance recording (Phase 4 — data-driven DNA evolution) ──────
    // Records tool outcomes and loop quality to owl_performance table.
    // Used by Phase 5 to drive DNA evolution from real metrics, not LLM guesses.
    if (this.ctx.db && sessionId) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      const userId = metadata?.userId ?? "default";
      const topic = (() => {
        const m = [...messages].reverse().find((msg) => msg.role === "user");
        return typeof m?.content === "string" ? m.content.slice(0, 80) : undefined;
      })();

      if (metadata?.loopExhausted) {
        this.ctx.db.owlPerf.record(owlName, sessionId, userId, "loop_exhausted", topic);
      } else if ((metadata?.toolsUsed?.length ?? 0) > 0) {
        // Response used tools and didn't exhaust — task completed
        this.ctx.db.owlPerf.record(owlName, sessionId, userId, "task_completed", topic);
      }

      // Record per-tool failures from this response
      const failCount = metadata?.toolFailureCount ?? 0;
      if (failCount > 0) {
        this.ctx.db.owlPerf.record(owlName, sessionId, userId, "tool_failure", topic, failCount);
      } else if ((metadata?.toolsUsed?.length ?? 0) > 0) {
        this.ctx.db.owlPerf.record(owlName, sessionId, userId, "tool_success", topic);
      }
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

    // ── Message compression — every 20 messages, summarize batch ─
    // Triggered when session crosses a batch boundary (20, 40, 60...).
    // Writes structured summary → SQLite summaries table.
    // key_facts → facts + owl_learnings. Saves ~74% on history tokens.
    if (this.ctx.compressor && this.ctx.db && sessionId && metadata?.userId) {
      const msgCount = this.ctx.db.messages.countSession(sessionId);
      if (msgCount > 0 && msgCount % 20 === 0) {
        this.taskQueue.enqueue("compress", async () => {
          try {
            const result = await this.ctx.compressor!.compress(
              sessionId,
              metadata.userId!,
              metadata.owlName ?? this.ctx.owl.persona.name,
              messages,
            );
            if (result) {
              log.engine.info(
                `[PostProcessor:compress] Batch compressed — ${result.factsWritten} facts, ` +
                `${result.learningsWritten} learnings, ~${result.tokensSaved} tokens saved`,
              );
            }
          } catch (err) {
            log.engine.warn(
              `[PostProcessor:compress] Failed: ${err instanceof Error ? err.message : err}`,
            );
          }
        });
      }
    }

    // ── L1 Digest update — runs every turn, zero LLM cost ────────
    // Updates the ConversationDigest with URLs found, files written,
    // commands run, decisions made, and failures from this response.
    // The digest is injected at the TOP of the next prompt so the model
    // knows what it just produced without re-parsing raw tool results.
    if (this.ctx.digestManager && sessionId) {
      this.taskQueue.enqueue("digest-update", async () => {
        try {
          await this.ctx.digestManager!.update(sessionId, messages);
        } catch (err) {
          log.engine.warn(
            `[PostProcessor:digest] Update failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      });
    }

    // ── Option A: Victory lap — record successful tool use ─────
    // When tools were used and the response looks successful, write a fact
    // to factStore describing what worked. Next session, when a similar request
    // arrives, the context builder's factStore search retrieves this and the
    // assistant knows it has done this before and how.
    if (
      this.ctx.factStore &&
      (metadata?.toolsUsed?.length ?? 0) > 0 &&
      !metadata?.loopExhausted &&
      (metadata?.toolFailureCount ?? 0) < 2
    ) {
      const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
      const lastAssistantMsg = [...messages].reverse().find((m) => m.role === "assistant");
      if (lastUserMsg && lastAssistantMsg) {
        const userRequest =
          typeof lastUserMsg.content === "string"
            ? lastUserMsg.content.slice(0, 150)
            : "";
        const summary =
          typeof lastAssistantMsg.content === "string"
            ? lastAssistantMsg.content.slice(0, 200)
            : "";
        const tools = metadata!.toolsUsed!.join(", ");

        this.taskQueue.enqueue("success-recipe", async () => {
          try {
            await this.ctx.factStore!.add({
              userId: metadata?.userId ?? "default",
              fact: `I successfully handled "${userRequest}" using [${tools}]. Result: ${summary}`,
              entity: metadata!.toolsUsed![0],
              category: "skill",
              confidence: 0.85,
              source: "inferred",
              expiresAt: new Date(
                Date.now() + 90 * 24 * 60 * 60 * 1000, // 90 days
              ).toISOString(),
            });
            await this.ctx.factStore!.save();
            log.engine.info(
              `[PostProcessor:success-recipe] Recorded success using [${tools}]`,
            );
          } catch (err) {
            log.engine.warn(
              `[PostProcessor:success-recipe] Failed: ${err instanceof Error ? err.message : err}`,
            );
          }
        });
      }
    }

    // ── Response Quality Signal ────────────────────────────────
    // When the engine got stuck (loop exhausted or repeated tool failures),
    // record it in ReflexionEngine so it generates a behavioral patch.
    // This feeds the learning loop with quality feedback, not just "what was said".
    if (
      this.ctx.reflexionEngine &&
      (metadata?.loopExhausted || (metadata?.toolFailureCount ?? 0) >= 3)
    ) {
      const lastUserMsg = [...messages]
        .reverse()
        .find((m) => m.role === "user");
      if (lastUserMsg) {
        const toolsAttempted = metadata?.toolsUsed?.join(", ") ?? "unknown";
        const reason = metadata?.loopExhausted
          ? "loop_exhausted"
          : `tool_failures_${metadata?.toolFailureCount}`;

        this.taskQueue.enqueue("quality-reflexion", async () => {
          try {
            await this.ctx.reflexionEngine!.reflectOnFailure({
              userMessage: typeof lastUserMsg.content === "string"
                ? lastUserMsg.content.slice(0, 200)
                : "",
              toolsAttempted,
              reason,
              sessionId: sessionId ?? "unknown",
            });
            log.engine.info(
              `[PostProcessor:quality] Recorded failure for reflexion: ${reason} (tools: ${toolsAttempted})`,
            );
          } catch (err) {
            log.engine.warn(
              `[PostProcessor:quality] Reflexion recording failed: ${err instanceof Error ? err.message : err}`,
            );
          }
        });
      }
    }

    // Goal extraction (every 3 messages) — detect persistent goals from conversation
    this.maybeExtractGoals(
      messages,
      sessionId ?? "unknown",
      metadata?.userId ?? "default",
    );

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

  /**
   * Attach a GoalExtractor so PostProcessor can detect persistent goals.
   * Called during gateway setup when db + provider are available.
   */
  setGoalExtractor(extractor: import("../../agent/goal-extractor.js").GoalExtractor): void {
    this._goalExtractor = extractor;
  }

  private _goalExtractor: import("../../agent/goal-extractor.js").GoalExtractor | null = null;

  /** Extract goals from conversation every 3 messages. */
  private maybeExtractGoals(messages: ChatMessage[], sessionId: string, userId: string): void {
    if (!this._goalExtractor) return;
    if (this.messageCount % 3 !== 0) return;
    const extractor = this._goalExtractor;
    this.taskQueue.enqueue("goal-extraction", async () => {
      await extractor.extractFromConversation(messages, sessionId, userId);
    });
  }

  // ─── Gap Learning Feedback ────────────────────────────────────

  /** Pellet ID from the most recent gap learning — used to absorb user corrections. */
  private _lastGapPelletId: string | null = null;

  /**
   * Register a pellet ID from a gap learning event.
   * The next user message will be checked for corrections.
   */
  setLastGapPelletId(pelletId: string): void {
    this._lastGapPelletId = pelletId;
  }

  /**
   * After a gap-learning response, check if the user is giving feedback.
   * Saves corrections and confirmations as pellets so the learning improves.
   */
  absorbGapFeedback(messages: ChatMessage[], _sessionId: string): void {
    if (!this._lastGapPelletId || !this.ctx.pelletStore) return;

    // Find the latest user message
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser || typeof lastUser.content !== "string") return;

    const userText = lastUser.content.trim().toLowerCase();
    const pelletId = this._lastGapPelletId;

    // Detect feedback signals
    const isCorrectionSignal = /\b(actually|no[,.]|that'?s wrong|incorrect|not quite|wrong|you should|let me correct|correction|not right|mistake)\b/.test(userText);
    const isPositiveSignal = /\b(correct|right|good|great|perfect|yes|exactly|spot on|well done|good job|learned well|keep that)\b/.test(userText);

    if (!isCorrectionSignal && !isPositiveSignal) {
      this._lastGapPelletId = null;
      return;
    }

    const pelletStore = this.ctx.pelletStore;
    const owlName = this.ctx.owl.persona.name;

    this.taskQueue.enqueue("gap-feedback", async () => {
      if (isCorrectionSignal) {
        // Save the correction as a new pellet linking to the original
        const correctionPellet = {
          id: `gap-correction-${Date.now()}`,
          title: `Gap Learning Correction (ref: ${pelletId.slice(0, 8)})`,
          generatedAt: new Date().toISOString(),
          source: "user-correction",
          owls: [owlName],
          tags: ["gap_correction", "user_feedback", "auto_learned"],
          version: 1,
          content: [
            `## User Correction`,
            lastUser.content,
            ``,
            `## References`,
            `Corrects gap-learning pellet: ${pelletId}`,
          ].join("\n"),
        };
        await pelletStore.save(correctionPellet, { skipDedup: true });
        log.engine.info(`[PostProcessor] Gap correction saved for pellet ${pelletId}`);
      } else if (isPositiveSignal) {
        // Confirmation — just log it (dedup will handle reinforcement naturally)
        log.engine.info(`[PostProcessor] Gap learning confirmed by user for pellet ${pelletId}`);
      }
    });

    this._lastGapPelletId = null;
  }
}
