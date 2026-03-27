/**
 * StackOwl — Self-Learning Coordinator
 *
 * Central hub that wires together the fragmented learning subsystems:
 *   - SignalBus (pub/sub for micro-learner signals)
 *   - MutationTracker (DNA mutation rollback via satisfaction feedback)
 *   - UserPreferenceModel (behavioral preference inference)
 *
 * This coordinator closes the feedback loops that were implemented but never wired:
 *
 *   MicroLearner.processMessage() → signals → SignalBus → subscribers
 *                                                        ├── MutationTracker.recordSatisfaction()
 *                                                        └── UserPreferenceModel.recordSignal()
 *
 *   PostProcessor.process() → coordinator.processMessage()
 *                           → coordinator.recordToolUse()
 *
 * Without this coordinator, signals from MicroLearner are captured but discarded.
 */

import type { MicroLearner, MicroSignal } from "./micro-learner.js";
import type { UserPreferenceModel } from "../preferences/model.js";
import type { MutationTracker } from "../owls/mutation-tracker.js";
import { SignalBus, SignalFilters } from "./signal-bus.js";
import { log } from "../logger.js";

export class SelfLearningCoordinator {
  public readonly signalBus: SignalBus;

  constructor(
    private microLearner: MicroLearner,
    private mutationTracker: MutationTracker | null,
    private preferenceModel: UserPreferenceModel | null,
    private owlName: string,
  ) {
    this.signalBus = new SignalBus();
    this.wire();
  }

  // ─── Evolution Gating & Mutation Recording ───────────────────

  /**
   * Check if evolution should proceed, freeze, or rollback for this owl.
   * Call BEFORE running OwlEvolutionEngine.evolve() to gate the mutation.
   */
  gateEvolution(): MutationTracker["analyze"] extends (name: string) => infer R
    ? R
    : never {
    if (!this.mutationTracker) {
      return {
        totalMutations: 0,
        avgSatisfaction: 0.5,
        oscillations: {
          isOscillating: false,
          oscillatingTraits: [],
          recommendation: "",
        },
        bestMutationType: null,
        worstMutationType: null,
        recommendedAction: "proceed" as const,
      };
    }
    return this.mutationTracker.analyze(this.owlName);
  }

  /**
   * Record the pre-mutation DNA state. Call BEFORE OwlEvolutionEngine.evolve().
   * Returns a record ID that must be passed to recordMutationEnd() after.
   * @param dna The owl's full current DNA (from owlRegistry.get(owlName).dna)
   */
  recordMutationStart(dna: import("../owls/persona.js").OwlDNA): string | null {
    if (!this.mutationTracker) return null;
    return this.mutationTracker.recordBeforeMutation(this.owlName, dna);
  }

  /**
   * Confirm the mutation and record what changed. Call AFTER OwlEvolutionEngine.evolve().
   * @param recordId From recordMutationStart()
   * @param dna The owl's current DNA (after evolution applied)
   * @param mutations List of human-readable mutation descriptions
   */
  async recordMutationEnd(
    recordId: string,
    dna: import("../owls/persona.js").OwlDNA,
    mutations: string[],
  ): Promise<void> {
    if (!this.mutationTracker || !recordId) return;
    try {
      await this.mutationTracker.confirmMutation(recordId, dna, mutations);
    } catch (err) {
      log.evolution.warn(
        `[SelfLearningCoordinator] confirmMutation failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Subscribe all consumers to the SignalBus.
   * Call once during construction.
   */
  private wire(): void {
    // ── MutationTracker: sentiment → satisfaction ────────────────
    // Map MicroLearner's positive/negative sentiment signals to
    // MutationTracker's 0-1 satisfaction scale.
    if (this.mutationTracker) {
      this.signalBus.subscribe(
        "mutation-tracker-satisfaction",
        SignalFilters.sentiment,
        async (signals) => {
          // Compute satisfaction from sentiment batch
          // positive=1, negative=0, mixed=average
          let posCount = 0;
          let negCount = 0;
          for (const s of signals) {
            if (s.type === "sentiment") {
              if (s.key === "positive") posCount++;
              else if (s.key === "negative") negCount++;
            }
          }
          const total = posCount + negCount;
          if (total === 0) return;

          // Satisfaction: ratio of positive to total, scaled to [0.3, 0.9]
          // Never give 0 or 1 — always room to improve or rollback
          const raw = posCount / total;
          const satisfaction = 0.3 + raw * 0.6;
          const tracker = this.mutationTracker!;

          try {
            const { shouldRollback } = await tracker.recordSatisfaction(
              this.owlName,
              satisfaction,
            );
            if (shouldRollback) {
              log.evolution.warn(
                `[SelfLearningCoordinator] Satisfaction dropped — triggering rollback check for ${this.owlName}`,
              );
              const analysis = tracker.analyze(this.owlName);
              if (analysis.recommendedAction === "rollback") {
                const recent = [...tracker["records"]]
                  .reverse()
                  .find((r) => r.owlName === this.owlName && !r.rolledBack);
                if (recent) {
                  await tracker.rollback(this.owlName, recent.id);
                }
              }
            }
          } catch (err) {
            log.evolution.warn(
              `[SelfLearningCoordinator] MutationTracker.recordSatisfaction failed: ${err instanceof Error ? err.message : String(err)}`,
            );
          }
        },
        { batchSize: 5, flushIntervalMs: 5000 },
      );
      log.engine.debug(
        "[SelfLearningCoordinator] MutationTracker subscribed to SignalBus (sentiment → satisfaction)",
      );
    }

    // ── UserPreferenceModel: style/temporal signals → inference ──
    if (this.preferenceModel) {
      this.signalBus.subscribe(
        "preference-model-signals",
        (s: MicroSignal) =>
          s.type === "style" || s.type === "temporal" || s.type === "topic",
        (signals) => {
          const prefModel = this.preferenceModel;
          if (!prefModel) return;
          for (const signal of signals) {
            if (
              signal.type === "style" ||
              signal.type === "temporal" ||
              signal.type === "topic"
            ) {
              try {
                prefModel.recordSignal(signal.type, signal.value);
              } catch (err) {
                log.engine.warn(
                  `[SelfLearningCoordinator] PreferenceModel.recordSignal failed: ${err instanceof Error ? err.message : String(err)}`,
                );
              }
            }
          }
        },
        { batchSize: 10, flushIntervalMs: 10000 },
      );
      log.engine.debug(
        "[SelfLearningCoordinator] UserPreferenceModel subscribed to SignalBus (style/temporal/topic → inference)",
      );
    }
  }

  /**
   * Process a user message end-to-end:
   *   1. Run MicroLearner signal extraction
   *   2. Publish signals to SignalBus (triggers all subscribers)
   *   3. Run UserPreferenceModel behavioral inference
   *
   * Call this from PostProcessor after every user message.
   */
  processMessage(
    userMessage: string,
    usedTools?: string[],
    channelId?: string,
  ): MicroSignal[] {
    // Step 1: Extract micro signals
    const signals = this.microLearner.processMessage(userMessage, usedTools);

    // Step 2: Publish to SignalBus for all subscribers
    this.signalBus.publishBatch(signals);

    // Step 3: Run behavioral inference (separate from SignalBus — direct method call)
    if (this.preferenceModel && channelId) {
      try {
        this.preferenceModel.analyzeMessage(userMessage, channelId);
      } catch (err) {
        log.engine.warn(
          `[SelfLearningCoordinator] PreferenceModel.analyzeMessage failed: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return signals;
  }

  /**
   * Record that a tool was used. Publishes a tool_use signal to the bus.
   * Call this from PostProcessor after tool execution.
   */
  recordToolUse(toolName: string): void {
    try {
      this.microLearner.recordToolUse(toolName);
      const signal: MicroSignal = {
        timestamp: new Date().toISOString(),
        type: "tool_use",
        key: toolName,
        value: 1,
      };
      this.signalBus.publish(signal);
    } catch (err) {
      log.engine.warn(
        `[SelfLearningCoordinator] recordToolUse failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Flush high-confidence inferred preferences from UserPreferenceModel.
   * Returns preferences with confidence >= threshold so the caller can
   * apply them to DNA learnedPreferences.
   */
  flushHighConfidencePrefs(
    threshold = 0.7,
  ): { key: string; value: unknown; confidence: number }[] {
    if (!this.preferenceModel) return [];
    return this.preferenceModel
      .getAll()
      .filter((p) => p.confidence >= threshold)
      .map((p) => ({ key: p.key, value: p.value, confidence: p.confidence }));
  }

  /**
   * Persist all learning state (MicroLearner profile, UserPreferenceModel prefs).
   * Call periodically or during shutdown.
   */
  async save(): Promise<void> {
    try {
      await this.microLearner.save();
    } catch (err) {
      log.engine.warn(
        `[SelfLearningCoordinator] microLearner.save() failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
    try {
      if (this.preferenceModel) {
        await this.preferenceModel.save();
      }
    } catch (err) {
      log.engine.warn(
        `[SelfLearningCoordinator] preferenceModel.save() failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Flush all pending signals and unsubscribe all subscribers.
   * Call during graceful shutdown.
   */
  shutdown(): void {
    this.signalBus.flushAll();
    this.signalBus.destroy();
    log.engine.debug("[SelfLearningCoordinator] Shutdown complete");
  }

  /**
   * Get the MicroLearner user profile (for pattern analyzer enrichment).
   */
  getMicroLearnerProfile() {
    return this.microLearner.getProfile();
  }

  /**
   * Get SignalBus statistics for monitoring.
   */
  getStats() {
    return this.signalBus.getStats();
  }
}
