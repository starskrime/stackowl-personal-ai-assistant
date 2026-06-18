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
import type { TaskQueue, TaskPriority } from "../../queue/task-queue.js";
import type { EventBus } from "../../events/bus.js";
import type { CostTracker } from "../../costs/tracker.js";
import type { InnerLifeDNABridge } from "../../owls/inner-bridge.js";
import type { ReflexionEngine as IntelligenceReflexionEngine } from "../../intelligence/reflexion-engine.js";
import type { SleepTimeConsolidator } from "../../intelligence/sleep-time-consolidator.js";
import { SentimentProbe } from "../../intelligence/sentiment-probe.js";
import { log } from "../../logger.js";
import { currentTrace, runWithContext } from "../../infra/observability/context.js";
import { w3cTraceparent, parseTraceparent } from "../../infra/observability/ids.js";

const TIER_PRIORITY: Record<"critical" | "standard" | "background", TaskPriority> = {
  critical: "high",
  standard: "normal",
  background: "low",
};

// Reward proxy constants for owl quality EWMA updates
const REWARD_LOOP_EXHAUSTED = 0.1;
const REWARD_TOOL_FAILURES = 0.3;
const REWARD_TOOL_SUCCESS = 0.85;
const REWARD_NEUTRAL = 0.7;

const CRITIQUE_PROMPT_TEMPLATE =
  `You are a learning assistant. In exactly two sentences:\n` +
  `Sentence 1: What went wrong when the assistant called "{tool_name}" and received a "{verdict}" result? (Context: "{verifier_reason}")\n` +
  `Sentence 2: In one concrete action, how should the assistant approach this differently next time?\n` +
  `Write only the two sentences. No headers, no explanation.`;

export class PostProcessor {
  private messageCount = 0;
  private intelligenceReflexion: IntelligenceReflexionEngine | null = null;
  private sleepConsolidator: SleepTimeConsolidator | null = null;
  private sentimentProbe: SentimentProbe | null = null;
  private _lastProcessUserId = "";
  private _lastSessionId: string | null = null;
  private _midSessionEvolving = false;

  constructor(
    private ctx: GatewayContext,
    private taskQueue: TaskQueue,
    private eventBus: EventBus | null,
    private coordinator: { gateEvolution?(): any; recordMutationStart?(dna: any): any; recordMutationEnd?(id: any, dna: any, mutations: any): Promise<void> } | null,
    _anticipator: null,
    private costTracker: CostTracker | null,
    private innerLifeBridge: InnerLifeDNABridge | null = null,
    intelligenceReflexion?: IntelligenceReflexionEngine,
    sleepConsolidator?: SleepTimeConsolidator,
  ) {
    this.intelligenceReflexion = intelligenceReflexion ?? null;
    this.sleepConsolidator = sleepConsolidator ?? null;

    // SentimentProbe — detects user corrections and increments challenge_instances
    // so DNA evolution can increase challengeLevel (reducing sycophancy) over time.
    this.sentimentProbe = new SentimentProbe((_sentiment, incrementChallenge) => {
      if (incrementChallenge && this._lastProcessUserId) {
        this.enqueueJob("sentiment-challenge-update", "critical", async () => {
          this.ctx.db?.rawDb?.prepare(
            `UPDATE outcome_journal
               SET challenge_instances = challenge_instances + 1
               WHERE id = (
                 SELECT id FROM outcome_journal
                 WHERE user_id = ?
                 ORDER BY created_at DESC
                 LIMIT 1
               )`,
          )?.run(this._lastProcessUserId);
          log.engine.info(
            `[PostProcessor:sentiment] Correction detected — incremented challenge_instances for user=${this._lastProcessUserId}`,
          );
        });
      }
    });
  }

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

    // Capture userId at the start of process() for SentimentProbe callback
    this._lastProcessUserId = metadata?.userId ?? "";
    this._lastSessionId = sessionId ?? null;

    // ── SentimentProbe — fire on current user message, then re-arm ──────────
    // The probe was armed after the PREVIOUS assistant response. Now the user
    // has replied — classify their sentiment before doing anything else.
    // After classification we re-arm so the NEXT user message is also checked.
    if (this.sentimentProbe) {
      try {
        const lastUserContent =
          messages.findLast?.((m) => m.role === "user")?.content ??
          [...messages].reverse().find((m) => m.role === "user")?.content ??
          "";
        const textToClassify =
          typeof lastUserContent === "string" ? lastUserContent : "";
        this.sentimentProbe.onNextMessage(textToClassify);
        // Re-arm for the next incoming message
        this.sentimentProbe.arm(metadata?.userId ?? "default");
      } catch (err) {
        log.engine.warn(
          `[PostProcessor:sentimentProbe] Failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

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

      // ── Update owl quality EWMA after each turn ─────────────────
      // Derives a [0,1] reward proxy from session metadata signals and writes
      // it to owl_quality_metrics so SecretaryRouter.calculateConfidence()
      // can use real performance data instead of static routingQuality DNA.
      try {
        const reward = metadata?.loopExhausted
          ? REWARD_LOOP_EXHAUSTED
          : (metadata?.toolFailureCount ?? 0) >= 3
            ? REWARD_TOOL_FAILURES
            : (metadata?.toolsUsed?.length ?? 0) > 0
              ? REWARD_TOOL_SUCCESS
              : REWARD_NEUTRAL; // no tools used — neutral pass
        this.ctx.db.owlQualityMetrics.update(owlName, userId, reward);
      } catch (err) {
        log.engine.warn("owlQualityMetrics update failed", err);
      }
    }

    // DNA evolution (every N messages) — now gated by MutationTracker
    const evolutionInterval = this.ctx.config.owlDna?.evolutionBatchSize ?? 5;
    if (
      this.messageCount % evolutionInterval === 0 &&
      this.ctx.evolutionEngine &&
      !this._midSessionEvolving
    ) {
      this.enqueueJob(
        `dna-evolve(${this.ctx.owl.persona.name})`,
        "background",
        async () => {
          // Gate: check MutationTracker analysis before mutating
          const analysis = this.coordinator?.gateEvolution?.();
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
          const recordId = this.coordinator?.recordMutationStart?.(
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
            await this.coordinator?.recordMutationEnd?.(
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
      this.enqueueJob("inner-life-dna-sync", "background", async () => {
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

    // Knowledge extraction (every 10 messages, BACKGROUND — feeds KnowledgeGraphLayer)
    if (
      this.ctx.knowledgeReasoner &&
      messages.length > 0 &&
      this.messageCount % 10 === 0
    ) {
      this.enqueueJob("knowledge-extract", "background", async () => {
        await this.ctx.knowledgeReasoner!.extractFromConversation(messages);
        await this.ctx.knowledgeGraph?.save();
      });
    }

    // Pattern recording
    if (this.ctx.patternAnalyzer) {
      const lastMsg = [...messages].reverse().find((m) => m.role === "user");
      if (lastMsg) {
        try {
          this.ctx.patternAnalyzer.recordAction(
            lastMsg.content.slice(0, 100),
            [],
          );
        } catch (err) {
          log.engine.warn(
            `[PostProcessor:patternAnalyzer.recordAction] Failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      }

    }

    // ── Intelligence Reflexion — write self-critiques to SQLite ──
    // When the loop exhausted, feed structured failure context into
    // IntelligenceReflexionEngine so CritiqueRetriever can surface these
    // lessons before similar future tasks (Reflexion loop closure).
    if (this.intelligenceReflexion && metadata?.loopExhausted) {
      const toolsUsed = metadata.toolsUsed ?? [];
      this.enqueueJob("reflexion-write", "standard", async () => {
        await this.intelligenceReflexion!.onTaskFailed({
          userId: metadata.userId ?? "",
          taskDescription: messages[0]?.content?.slice(0, 200) ?? "",
          toolSequence: toolsUsed,
          errorSummary: "Task loop exhausted without completion",
          category: "general",
          complexityTier: "medium",
        });
      });
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

        this.enqueueJob("quality-reflexion", "standard", async () => {
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
        });
      }
    }

    // Periodic persistence (every 10 messages)
    if (this.messageCount % 10 === 0) {
      if (this.ctx.patternAnalyzer) {
        this.enqueueJob("pattern-save", "background", () =>
          this.ctx.patternAnalyzer!.save(),
        );
      }
      if (this.ctx.trustChain) {
        this.enqueueJob("trust-save", "background", () => this.ctx.trustChain!.save());
      }
      if (this.ctx.predictiveQueue) {
        this.enqueueJob("predictive-prep", "background", async () => {
          const newTasks =
            await this.ctx.predictiveQueue!.generatePredictions();
          for (const task of newTasks) {
            await this.ctx.predictiveQueue!.prepareTask(task.id);
          }
        });
      }
    }

    // SleepTimeConsolidator — surface cross-session patterns after session ends
    if (this.sleepConsolidator && metadata?.userId && sessionId) {
      this.enqueueJob("sleep-consolidation", "standard", async () => {
        await this.sleepConsolidator!.onSessionEnded(metadata.userId!, sessionId);
      });
    }

    // ── Failure critique: BLOCKED/PARTIAL → owl_learnings ──────────
    if (this.ctx.db && sessionId) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      this.enqueueJob("learning-failure-critique", "background", async () => {
        const failedTurns =
          this.ctx.db!.trajectories.getSessionFailures(sessionId!) ?? [];
        if (failedTurns.length === 0) return;

        for (const turn of failedTurns.slice(0, 3)) {
          const prompt = CRITIQUE_PROMPT_TEMPLATE
            .replace("{tool_name}", turn.tool_name ?? "unknown")
            .replace("{verdict}", turn.verification_result)
            .replace("{verifier_reason}", turn.verifier_reason ?? "");

          try {
            const response = await this.ctx.provider.chat([
              { role: "user", content: prompt },
            ]);
            const critique = response.content.trim();
            if (critique) {
              this.ctx.db!.owlLearnings.admitIfWorthy(
                owlName,
                critique,
                "failure",
                0.6,
              );
            }
          } catch (err) {
            log.evolution.warn(
              `[PostProcessor:critique] Failed to generate critique: ${err instanceof Error ? err.message : err}`,
            );
          }
        }
      });
    }

    // ── Mid-session evolution trigger (D4) ──────────────────────
    // Every 5 messages: if avg trajectory reward < -0.2 AND last evolution
    // was > 2h ago, enqueue a background evolution job so the owl can adapt
    // mid-session instead of waiting until endSession().
    if (this.ctx.evolutionEngine && this.ctx.db && this.messageCount % 5 === 0) {
      const owlName = metadata?.owlName ?? this.ctx.owl.persona.name;
      const recent = this.ctx.db.trajectories.getRecent(owlName, 10);
      if (recent.length >= 5) {
        const avgReward =
          recent.reduce((s: number, t: { reward: number }) => s + t.reward, 0) /
          recent.length;
        const lastEvolved = this.ctx.owl.dna.lastEvolved
          ? new Date(this.ctx.owl.dna.lastEvolved).getTime()
          : 0;
        const hoursSinceEvolved = (Date.now() - lastEvolved) / (1000 * 60 * 60);

        if (avgReward < -0.2 && hoursSinceEvolved > 2 && !this._midSessionEvolving) {
          this._midSessionEvolving = true;
          this.enqueueJob("mid-session-evolution", "background", async () => {
            try {
              await this.ctx.evolutionEngine!.evolve(owlName);
              log.engine.info(
                `[PostProcessor:mid-session-evolution] avg_reward=${avgReward.toFixed(2)} triggered evolution for ${owlName}`,
              );
            } finally {
              this._midSessionEvolving = false;
            }
          });
        }
      }
    }
  }

  getMessageCount(): number {
    return this.messageCount;
  }

  // NOTE: setGoalExtractor() and _goalExtractor field were removed — goal-extraction
  // was a zombie job (no context layer reads the output). GoalExtractor code preserved.
  // If a context layer is added, re-wire via a different mechanism.

  // ─── Job Enqueueing Infrastructure ───────────────────────────

  private enqueueJob(
    name: string,
    tier: "critical" | "standard" | "background",
    fn: () => Promise<void>,
  ): void {
    const priority = TIER_PRIORITY[tier];
    // Capture the current trace context at enqueue time so it can be restored
    // when the job runs in the TaskQueue worker (which has no AsyncLocalStorage).
    const ctx = currentTrace();
    const traceparent = ctx ? w3cTraceparent(ctx.traceId, ctx.spanId) : undefined;

    this.taskQueue.enqueue(name, async () => {
      const start = Date.now();
      const tp = parseTraceparent(traceparent ?? "");
      const seed = tp
        ? { traceId: tp.traceId, parentSpanId: tp.spanId, channelId: "post-processor" }
        : { channelId: "post-processor" };
      try {
        await runWithContext(seed, fn);
        this.recordJobRun(name, tier, true, Date.now() - start);
      } catch (err) {
        const code = err instanceof Error ? err.constructor.name : "unknown";
        log.engine.warn(
          `[PostProcessor:${name}] Failed: ${err instanceof Error ? err.message : err}`,
        );
        this.recordJobRun(name, tier, false, Date.now() - start, code);
      }
    }, priority);
  }

  private recordJobRun(
    name: string,
    tier: string,
    success: boolean,
    durationMs: number,
    errorCode?: string,
  ): void {
    if (!this.ctx.db) return;
    try {
      this.ctx.db.rawDb.prepare(
        `INSERT INTO post_processor_job_runs
         (job_name, tier, success, error_code, duration_ms, user_id, session_id)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        name,
        tier,
        success ? 1 : 0,
        errorCode ?? null,
        durationMs,
        this._lastProcessUserId || null,
        this._lastSessionId || null,
      );
    } catch (err) {
      log.engine.warn("tool telemetry record failed", err);
    }
  }

  // Test-only alias — exposes enqueueJob for unit testing
  // @ts-expect-error TS6133: needed for test seam access
  private enqueueJobForTest = this.enqueueJob.bind(this);

  // ─── Gap Learning Feedback ────────────────────────────────────

}
