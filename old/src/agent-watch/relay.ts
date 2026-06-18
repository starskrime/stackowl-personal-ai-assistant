/**
 * StackOwl — Agent Watch: Relay Engine
 *
 * The brain of agent supervision.
 * For each incoming question it decides:
 *   1. Auto-approve silently (low risk, or on session allowlist)
 *   2. Auto-deny silently (on session denylist)
 *   3. Notify user via Telegram and wait for reply (medium/high risk)
 *
 * Notification is fire-and-forget; the actual waiting is handled by
 * QuestionQueue which returns a Promise that resolves when the user replies
 * or the timeout fires.
 */

import type { AgentQuestion, Decision } from "./adapters/base.js";
import type { SessionRegistry } from "./session-registry.js";
import type { QuestionQueue } from "./question-queue.js";
import type { RiskClassifier } from "./risk-classifier.js";
import {
  formatQuestion,
  formatAutoDecision,
} from "./formatters/telegram.js";
import { log } from "../logger.js";

// ─── Notify Callback ─────────────────────────────────────────────

/** Called to send a message to the user's Telegram */
export type NotifyFn = (userId: string, channelId: string, html: string) => Promise<void>;

// ─── Relay ────────────────────────────────────────────────────────

export class Relay {
  constructor(
    private registry: SessionRegistry,
    private queue: QuestionQueue,
    private classifier: RiskClassifier,
    private notify: NotifyFn,
  ) {}

  /**
   * Main entry point: process an incoming question and return a decision.
   * Awaits until the user replies or a timeout fires.
   */
  async process(question: AgentQuestion): Promise<Decision> {
    const session = this.registry.get(question.sessionId);
    if (!session) {
      // Unknown session — auto-deny for safety
      log.engine.warn(
        `[Relay] Unknown session ${question.sessionId} — denying`,
      );
      return "deny";
    }

    // ── 1. Check session-level lists (from "yes all" / "no all" replies) ──
    if (session.sessionAllowlist.has(question.toolName)) {
      this.registry.recordDecision(question.sessionId, "autoApproved");
      log.engine.info(
        `[Relay] Auto-allow (session allowlist): ${question.toolName}`,
      );
      return "allow";
    }
    if (session.sessionDenylist.has(question.toolName)) {
      this.registry.recordDecision(question.sessionId, "autoDenied");
      log.engine.info(
        `[Relay] Auto-deny (session denylist): ${question.toolName}`,
      );
      return "deny";
    }

    // ── 2. Low risk → auto-approve silently ───────────────────────
    if (question.risk === "low") {
      this.registry.recordDecision(question.sessionId, "autoApproved");
      log.engine.info(
        `[Relay] Auto-approve (low risk): ${question.toolName}`,
      );
      return "allow";
    }

    // ── 3. Medium / High → notify user and queue ──────────────────
    const { reason } = this.classifier.classify(
      question.toolName,
      question.toolInput,
    );

    // Send Telegram notification (non-blocking)
    const telegramMsg = formatQuestion(question, reason);
    this.notify(session.userId, session.channelId, telegramMsg).catch((err) =>
      log.engine.warn(
        `[Relay] Failed to notify user: ${err instanceof Error ? err.message : err}`,
      ),
    );

    log.engine.info(
      `[Relay] Queued question ${question.id} (${question.risk}) for ${session.userId}`,
    );

    // Await decision (long-poll — held by QuestionQueue until user replies or timeout)
    const decision = await this.queue.enqueue(question);

    // Record stats
    const statKey = decision === "allow" ? "approved" : "denied";
    this.registry.recordDecision(question.sessionId, statKey);

    return decision;
  }

  /**
   * Called when the user sends "yes all Bash" or "no all Write" from Telegram.
   * Adds the tool to the session's persistent list and resolves any pending question.
   */
  applySessionRule(
    sessionId: string,
    toolName: string,
    decision: Decision,
  ): void {
    if (decision === "allow") {
      this.registry.addToAllowlist(sessionId, toolName);
    } else {
      this.registry.addToDenylist(sessionId, toolName);
    }

    // Resolve any currently pending question for this tool
    const pending = this.queue.getForSession(sessionId);
    for (const q of pending) {
      if (q.toolName === toolName) {
        const resolved = this.queue.answer(q.id, decision);
        if (resolved) {
          const session = this.registry.get(sessionId);
          if (session) {
            const autoMsg = formatAutoDecision(
              q,
              decision,
              `Session rule applied — all ${toolName} calls will be ${decision === "allow" ? "allowed" : "denied"}`,
            );
            this.notify(session.userId, session.channelId, autoMsg).catch(() => {});
          }
        }
      }
    }
  }
}
