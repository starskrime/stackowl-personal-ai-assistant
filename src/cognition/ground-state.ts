/**
 * StackOwl — Conversational Ground State
 *
 * A session-scoped view over FactStore that tracks:
 *   - What we've established (shared facts, decisions)
 *   - What's still open (questions, blockers)
 *   - What the user wants (active goal, sub-goals)
 *   - A rolling summary of where we are
 *
 * Refreshed every N turns via a lightweight LLM call.
 * Archived on topic switch (TTL set on session-scoped facts).
 *
 * Based on Clark & Brennan (1991) — conversational grounding theory:
 *   Common ground accumulates through presentation + acceptance.
 */

import type { ModelProvider } from "../providers/base.js";
import type { ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

interface GroundStateFact {
  id: string;
  fact: string;
  category: string;
  confidence: number;
}

interface GroundState {
  sharedFacts: GroundStateFact[];
  decisions: GroundStateFact[];
  openQuestions: GroundStateFact[];
  activeGoals: GroundStateFact[];
  subGoals: GroundStateFact[];
  lastSummary: string | null;
}

// ─── Ground State View ──────────────────────────────────────────

export class GroundStateView {
  private lastSummary: string | null = null;
  private turnsSinceRefresh = 0;
  private sessionId: string | null = null;
  private refreshInProgress = false;

  constructor(
    private provider: ModelProvider,
    private refreshInterval = 5,
  ) {}

  /**
   * Set the active session. Resets turn counter on session change.
   */
  setSession(sessionId: string): void {
    if (this.sessionId !== sessionId) {
      this.sessionId = sessionId;
      this.turnsSinceRefresh = 0;
      this.lastSummary = null;
    }
  }

  /**
   * Get the current ground state for a session.
   * Returns empty state — FactStore removed; ground state rebuilt via MemoryManager.
   */
  getState(_userId: string): GroundState {
    return {
      sharedFacts: [],
      decisions: [],
      openQuestions: [],
      activeGoals: [],
      subGoals: [],
      lastSummary: this.lastSummary,
    };
  }

  /**
   * Record a turn. Returns true if refresh should be triggered.
   */
  recordTurn(): boolean {
    this.turnsSinceRefresh++;
    return this.turnsSinceRefresh >= this.refreshInterval;
  }

  /**
   * Refresh ground state from recent messages via lightweight LLM call.
   * Extracts facts, decisions, open questions, and goals into FactStore.
   *
   * Uses Haiku-class model for speed (~100 input tokens).
   */
  async refresh(
    recentMessages: ChatMessage[],
    _userId: string,
    sessionId: string,
  ): Promise<void> {
    if (this.refreshInProgress) return;
    if (recentMessages.length < 2) return;

    this.refreshInProgress = true;
    this.setSession(sessionId);

    try {
      // Take last messages since last refresh (max 10)
      const msgs = recentMessages
        .slice(-10)
        .map((m) => `${m.role}: ${m.content.slice(0, 200)}`)
        .join("\n");

      const prompt = `Given these recent messages from a conversation, extract the conversational ground state.

Messages:
${msgs}

Return a JSON object:
{
  "facts": ["fact1", "fact2"],
  "decisions": ["decision1"],
  "open_questions": ["question1"],
  "goal": "what the user is trying to accomplish" or null,
  "summary": "1-2 sentence summary of where we are right now"
}

Only include items that were explicitly established in the messages. Return ONLY valid JSON.`;

      const response = await Promise.race([
        this.provider.chat(
          [{ role: "user", content: prompt }],
          undefined,
          { temperature: 0, maxTokens: 300 },
        ),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("timeout")), 3000),
        ),
      ]);

      const match = response.content.trim().match(/\{[\s\S]*\}/);
      if (!match) return;

      const parsed = JSON.parse(match[0]) as {
        facts?: string[];
        decisions?: string[];
        open_questions?: string[];
        goal?: string | null;
        summary?: string;
      };

      this.lastSummary = parsed.summary ?? null;
      this.turnsSinceRefresh = 0;

      const factCount =
        (parsed.facts?.length ?? 0) +
        (parsed.decisions?.length ?? 0) +
        (parsed.open_questions?.length ?? 0) +
        (parsed.goal ? 1 : 0);

      log.engine.info(
        `[GroundState] Refreshed: ${factCount} facts (in-memory only), summary="${(this.lastSummary ?? "").slice(0, 60)}"`,
      );
    } catch (err) {
      log.engine.warn(
        `[GroundState] Refresh failed: ${err instanceof Error ? err.message : err}`,
      );
      // Keep stale summary — better than nothing
    } finally {
      this.refreshInProgress = false;
    }
  }

  /**
   * Archive the current ground state on topic switch.
   */
  async archive(_userId: string): Promise<void> {
    this.lastSummary = null;
    this.turnsSinceRefresh = 0;
    log.engine.info(`[GroundState] Archived`);
  }

  /**
   * Format ground state for system prompt injection.
   * Returns empty string if nothing meaningful to inject.
   */
  toContextString(userId: string): string {
    const state = this.getState(userId);

    const hasContent =
      state.activeGoals.length > 0 ||
      state.decisions.length > 0 ||
      state.openQuestions.length > 0 ||
      state.lastSummary;

    if (!hasContent) return "";

    const lines: string[] = ["<conversational_ground>"];

    if (state.activeGoals.length > 0) {
      lines.push(
        `  Working on: ${state.activeGoals.map((g) => g.fact).join("; ")}`,
      );
    }

    if (state.decisions.length > 0) {
      lines.push("  Decided:");
      for (const d of state.decisions.slice(0, 5)) {
        lines.push(`    - ${d.fact}`);
      }
    }

    if (state.openQuestions.length > 0) {
      lines.push("  Still open:");
      for (const q of state.openQuestions.slice(0, 3)) {
        lines.push(`    - ${q.fact}`);
      }
    }

    if (state.subGoals.length > 0) {
      const done = state.subGoals.filter(
        (g) => g.confidence >= 0.9,
      ).length;
      lines.push(
        `  Progress: ${done}/${state.subGoals.length} sub-goals done`,
      );
    }

    if (state.lastSummary) {
      lines.push(`  Where we are: ${state.lastSummary}`);
    }

    lines.push("</conversational_ground>");
    return lines.join("\n");
  }
}
