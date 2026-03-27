/**
 * StackOwl — Proactive Intention Loop
 *
 * Drives the proactive heartbeat from intent state, not just timers.
 * Evaluates what to proactively tell the user, prioritized by urgency.
 *
 * Priority order:
 *   1. Due commitments (owl promised to follow up)
 *   2. Stale intents (active tasks with no recent activity)
 *   3. Stale goals (goals not mentioned in 3+ days)
 *   4. Ambient opportunities (high-priority context signals)
 *   5. Time-based (morning brief, lunch reminder, etc.)
 */

import type { CommitmentTracker } from "../intent/commitment-tracker.js";
import type { IntentStateMachine } from "../intent/state-machine.js";
import type { GoalGraph } from "../goals/graph.js";
import type { ContextMesh } from "../ambient/mesh.js";
import { log } from "../logger.js";

export type ProactiveItemType =
  | "commitment"
  | "stale_intent"
  | "stale_goal"
  | "ambient_signal"
  | "time_based";

export interface ProactiveItem {
  type: ProactiveItemType;
  priority: number; // 0-100, higher = more urgent
  message: string;
  metadata?: Record<string, unknown>;
}

export class ProactiveIntentionLoop {
  constructor(
    private commitmentTracker: CommitmentTracker | undefined,
    private intentStateMachine: IntentStateMachine | undefined,
    private goalGraph: GoalGraph | undefined,
    private contextMesh: ContextMesh | undefined,
  ) {}

  /**
   * Evaluate all proactive signals and return the highest priority item to send.
   * Returns null if nothing is due.
   */
  evaluate(): ProactiveItem | null {
    const items: ProactiveItem[] = [];

    // 1. Due commitments (highest priority)
    if (this.commitmentTracker) {
      const dueCommitments = this.commitmentTracker.getDue();
      for (const c of dueCommitments) {
        items.push({
          type: "commitment",
          priority: 100,
          message: c.followUpMessage,
          metadata: { commitmentId: c.id, intentId: c.intentId },
        });
      }
    }

    // 2. Stale intents (active but no activity in 30+ min)
    if (this.intentStateMachine) {
      const staleIntents = this.intentStateMachine.getStale();
      for (const intent of staleIntents) {
        if (intent.status === "in_progress") {
          const msg = this.buildStaleIntentMessage(intent);
          items.push({
            type: "stale_intent",
            priority: 80,
            message: msg,
            metadata: { intentId: intent.id },
          });
        }
      }
    }

    // 3. Stale goals (not mentioned in 3+ days)
    if (this.goalGraph) {
      try {
        const staleGoals = this.goalGraph.getStale(3);
        for (const goal of staleGoals.slice(0, 2)) {
          const daysSinceActive = Math.round(
            (Date.now() - goal.lastActiveAt) / (1000 * 60 * 60 * 24),
          );
          items.push({
            type: "stale_goal",
            priority: 60,
            message: `Just checking in — you had a goal: "${goal.title}" (${goal.progress}% complete, last active ${daysSinceActive} days ago). Any progress?`,
            metadata: { goalId: goal.id },
          });
        }
      } catch (err) {
        log.engine.warn(
          `[ProactiveLoop] Stale goal check failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // 4. High-priority ambient signals
    if (this.contextMesh) {
      const signals = this.contextMesh.getState().signals;
      for (const signal of signals.slice(0, 3)) {
        if (signal.priority === "critical" || signal.priority === "high") {
          items.push({
            type: "ambient_signal",
            priority: 50,
            message: `I noticed: ${signal.title}. ${signal.content?.slice(0, 100) ?? ""}`,
            metadata: { signalId: signal.id, source: signal.source },
          });
        }
      }
    }

    if (items.length === 0) return null;

    // Sort by priority descending
    items.sort((a, b) => b.priority - a.priority);
    return items[0];
  }

  private buildStaleIntentMessage(
    intent: import("../intent/types.js").Intent,
  ): string {
    if (intent.status === "waiting_on_user") {
      return `Hey, you mentioned "${intent.description}" earlier — do you have the info I needed?`;
    }
    if (intent.status === "blocked") {
      return `The task "${intent.description}" seems blocked. Want to try a different approach?`;
    }
    const minutesSince = Math.round(
      (Date.now() - intent.lastActiveAt) / (1000 * 60),
    );
    if (minutesSince < 60) {
      return `Just checking — I'm still working on "${intent.description}". Anything else you need?`;
    }
    return `Hi! Just following up on "${intent.description}" — any updates?`;
  }

  /**
   * Returns a summary of all pending proactive items (for debugging/status).
   */
  getPendingSummary(): string {
    const parts: string[] = [];

    if (this.commitmentTracker) {
      const pending = this.commitmentTracker.getPending().length;
      if (pending > 0) parts.push(`${pending} pending commitment(s)`);
    }

    if (this.intentStateMachine) {
      const stale = this.intentStateMachine.getStale().length;
      if (stale > 0) parts.push(`${stale} stale intent(s)`);
    }

    if (this.goalGraph) {
      try {
        const staleGoals = this.goalGraph.getStale(3).length;
        if (staleGoals > 0) parts.push(`${staleGoals} stale goal(s)`);
      } catch {}
    }

    return parts.length > 0
      ? `Proactive: ${parts.join(", ")}`
      : "Proactive: nothing pending";
  }
}
