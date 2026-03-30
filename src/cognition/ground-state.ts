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

import type { FactStore, StoredFact, FactCategory } from "../memory/fact-store.js";
import type { ModelProvider } from "../providers/base.js";
import type { ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

interface GroundState {
  sharedFacts: StoredFact[];
  decisions: StoredFact[];
  openQuestions: StoredFact[];
  activeGoals: StoredFact[];
  subGoals: StoredFact[];
  lastSummary: string | null;
}

// Ground state categories map to FactStore categories
const GROUND_CATEGORIES: FactCategory[] = [
  "decision",
  "open_question",
  "active_goal",
  "sub_goal",
];

// ─── Ground State View ──────────────────────────────────────────

export class GroundStateView {
  private lastSummary: string | null = null;
  private turnsSinceRefresh = 0;
  private sessionId: string | null = null;
  private refreshInProgress = false;

  constructor(
    private factStore: FactStore,
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
   * Queries FactStore filtered by session-scoped ground categories.
   */
  getState(userId: string): GroundState {
    const allFacts = this.factStore.getActiveForUser(userId);

    // Filter to ground state categories, scoped to current session
    const sessionFacts = allFacts.filter(
      (f) =>
        GROUND_CATEGORIES.includes(f.category) &&
        (!this.sessionId || f.entity === this.sessionId || !f.entity),
    );

    return {
      sharedFacts: allFacts.filter(
        (f) => f.category === "project_detail" || f.category === "context",
      ).slice(0, 5),
      decisions: sessionFacts.filter((f) => f.category === "decision"),
      openQuestions: sessionFacts.filter((f) => f.category === "open_question"),
      activeGoals: sessionFacts.filter((f) => f.category === "active_goal"),
      subGoals: sessionFacts.filter((f) => f.category === "sub_goal"),
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
    userId: string,
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

      // Store extracted ground state as facts
      const now = new Date();
      const ttl24h = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();

      const factsToAdd: Array<{
        fact: string;
        category: FactCategory;
        expiresAt?: string;
      }> = [];

      for (const fact of parsed.facts ?? []) {
        factsToAdd.push({ fact, category: "context" });
      }
      for (const decision of parsed.decisions ?? []) {
        factsToAdd.push({ fact: decision, category: "decision" });
      }
      for (const question of parsed.open_questions ?? []) {
        factsToAdd.push({
          fact: question,
          category: "open_question",
          expiresAt: ttl24h,
        });
      }
      if (parsed.goal) {
        factsToAdd.push({ fact: parsed.goal, category: "active_goal" });
      }

      for (const item of factsToAdd) {
        await this.factStore.add({
          userId,
          fact: item.fact,
          entity: sessionId,
          category: item.category,
          confidence: 0.8,
          source: "inferred",
          expiresAt: item.expiresAt,
        });
      }

      this.lastSummary = parsed.summary ?? null;
      this.turnsSinceRefresh = 0;

      log.engine.info(
        `[GroundState] Refreshed: ${factsToAdd.length} facts, summary="${(this.lastSummary ?? "").slice(0, 60)}"`,
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
   * Sets TTL on session-scoped facts so they expire.
   */
  async archive(userId: string): Promise<void> {
    const state = this.getState(userId);
    const ttl1h = new Date(Date.now() + 60 * 60 * 1000).toISOString();

    // Set short TTL on open questions (they're no longer relevant)
    for (const fact of state.openQuestions) {
      await this.factStore.update(fact.id, { expiresAt: ttl1h });
    }

    // Keep decisions and goals longer (they're still useful context)
    this.lastSummary = null;
    this.turnsSinceRefresh = 0;

    log.engine.info(
      `[GroundState] Archived: ${state.openQuestions.length} questions expired`,
    );
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
