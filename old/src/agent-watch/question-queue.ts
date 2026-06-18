/**
 * StackOwl — Agent Watch: Question Queue
 *
 * Holds pending questions waiting for human decisions.
 * Each question is a Promise that resolves when:
 *   - User replies via Telegram
 *   - Auto-timeout fires (medium risk → allow, high risk → deny)
 *   - Session ends
 */

import type { Decision, AgentQuestion } from "./adapters/base.js";
import { log } from "../logger.js";

// ─── Timeouts ─────────────────────────────────────────────────────

const TIMEOUT_MS: Record<string, number> = {
  medium: 5 * 60 * 1000,   // 5 min — auto-allow if no reply
  high: 10 * 60 * 1000,    // 10 min — auto-deny if no reply
  low: 0,                  // never queued (auto-approved by relay)
};

const TIMEOUT_DECISION: Record<string, Decision> = {
  medium: "allow",  // safe to proceed if user doesn't respond
  high: "deny",     // block by default on sensitive ops
};

// ─── Pending Entry ────────────────────────────────────────────────

interface PendingEntry {
  question: AgentQuestion;
  resolve: (d: Decision) => void;
  timer: ReturnType<typeof setTimeout>;
}

// ─── QuestionQueue ────────────────────────────────────────────────

export class QuestionQueue {
  /** questionId → pending entry */
  private pending = new Map<string, PendingEntry>();

  /**
   * Add a question and return a Promise that resolves with the decision.
   * Caller awaits this — the HTTP hook connection stays open until resolved.
   */
  enqueue(question: AgentQuestion): Promise<Decision> {
    return new Promise<Decision>((resolve) => {
      const timeoutMs = TIMEOUT_MS[question.risk] ?? TIMEOUT_MS.medium;
      const timeoutDecision = TIMEOUT_DECISION[question.risk] ?? "deny";

      const timer = setTimeout(() => {
        if (!this.pending.has(question.id)) return;
        this.pending.delete(question.id);
        log.engine.info(
          `[AgentWatch] Timeout for question ${question.id} (${question.risk}) → ${timeoutDecision}`,
        );
        resolve(timeoutDecision);
      }, timeoutMs);

      this.pending.set(question.id, { question, resolve, timer });
    });
  }

  /**
   * Resolve a pending question with a user-supplied decision.
   * Returns false if the question ID wasn't found (already timed out or answered).
   */
  answer(questionId: string, decision: Decision): boolean {
    const entry = this.pending.get(questionId);
    if (!entry) return false;

    clearTimeout(entry.timer);
    this.pending.delete(questionId);
    entry.resolve(decision);
    log.engine.info(
      `[AgentWatch] Question ${questionId} answered: ${decision}`,
    );
    return true;
  }

  /**
   * Get all pending questions for a user session.
   * Used to show the user what's waiting if they ask.
   */
  getForSession(sessionId: string): AgentQuestion[] {
    return [...this.pending.values()]
      .filter((e) => e.question.sessionId === sessionId)
      .map((e) => e.question);
  }

  /** Returns the single pending question for a session, if exactly one exists. */
  getSinglePending(sessionId: string): AgentQuestion | null {
    const list = this.getForSession(sessionId);
    return list.length === 1 ? list[0] : null;
  }

  /** Cancel all pending questions for a session (session ended). */
  cancelSession(sessionId: string, decision: Decision = "deny"): void {
    for (const [id, entry] of this.pending) {
      if (entry.question.sessionId === sessionId) {
        clearTimeout(entry.timer);
        this.pending.delete(id);
        entry.resolve(decision);
      }
    }
  }

  get size(): number {
    return this.pending.size;
  }
}
