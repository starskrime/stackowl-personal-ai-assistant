/**
 * StackOwl — Goal Extractor
 *
 * After each conversation turn, checks if the user expressed a persistent
 * goal (something the agent should work on autonomously, not just respond to).
 *
 * Runs in the PostProcessor background queue — never blocks the user response.
 *
 * Examples of what triggers goal creation:
 *   "Keep me updated on competitor pricing"   → recurring research goal
 *   "Research ML papers this week"            → time-bound research goal
 *   "Monitor my repo for security issues"     → recurring watch goal
 *
 * Examples of what does NOT trigger goal creation:
 *   "What's the weather?"                     → one-shot question
 *   "Summarize this text"                     → immediate task, done inline
 */

import type { ChatMessage } from "../providers/base.js";
import type { ModelProvider } from "../providers/base.js";
import type { MemoryDatabase } from "../memory/db.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

interface ExtractedGoal {
  title: string;
  description: string;
  priority: number;         // 1-10
  deadline?: string;        // ISO date or null
  isRecurring: boolean;
}

// ─── GoalExtractor ────────────────────────────────────────────────

export class GoalExtractor {
  constructor(
    private provider: ModelProvider,
    private db: MemoryDatabase,
  ) {}

  /**
   * Extract persistent goals from the last few messages of a conversation.
   * Called by PostProcessor every 3 messages (lightweight check).
   * Returns the number of new goals created.
   */
  async extractFromConversation(
    messages: ChatMessage[],
    sessionId: string,
    userId = "default",
  ): Promise<number> {
    // Only look at the last 6 messages (3 turns) — no need to re-scan old history
    const recent = messages.slice(-6);
    const userMessages = recent
      .filter((m) => m.role === "user")
      .map((m) => (typeof m.content === "string" ? m.content : ""))
      .filter(Boolean);

    if (userMessages.length === 0) return 0;

    // Quick heuristic check before making LLM call — avoids 99% of cases
    if (!this.looksLikeGoal(userMessages.join(" "))) return 0;

    try {
      const goals = await this.callExtractor(userMessages, sessionId);
      if (goals.length === 0) return 0;

      for (const g of goals) {
        // Deduplicate: skip if a very similar goal title already exists
        const existing = this.db.agentGoals.getActive(userId);
        const duplicate = existing.some(
          (e) => this.similarity(e.title, g.title) > 0.7,
        );
        if (duplicate) continue;

        this.db.agentGoals.create(g.title, g.description, {
          userId,
          priority: g.priority,
          createdBy: "user",
          deadline: g.deadline,
          sourceSessionId: sessionId,
        });
        log.engine.info(`[GoalExtractor] New goal: "${g.title}" (priority ${g.priority})`);
      }

      return goals.length;
    } catch (err) {
      log.engine.warn(`[GoalExtractor] Failed: ${err instanceof Error ? err.message : err}`);
      return 0;
    }
  }

  // ─── Private ──────────────────────────────────────────────────

  /**
   * Cheap keyword check before making an LLM call.
   * Catches ~90% of goal-like messages without API cost.
   */
  private looksLikeGoal(text: string): boolean {
    const lower = text.toLowerCase();
    const triggers = [
      "keep me updated", "monitor", "watch", "track", "follow",
      "remind me", "every day", "weekly", "daily", "regularly",
      "research", "find out", "look into", "investigate",
      "make sure", "whenever", "if.*then", "alert me",
      "stay on top", "keep an eye",
    ];
    return triggers.some((t) => new RegExp(t).test(lower));
  }

  private async callExtractor(
    userMessages: string[],
    _sessionId: string,
  ): Promise<ExtractedGoal[]> {
    const today = new Date().toISOString().split("T")[0];
    const prompt = `You are analyzing a conversation to find PERSISTENT GOALS — things the user wants the AI assistant to work on autonomously over time.

Today is ${today}.

Recent user messages:
${userMessages.map((m, i) => `${i + 1}. "${m}"`).join("\n")}

Extract ONLY goals that require ONGOING or FUTURE autonomous work by the assistant (research, monitoring, recurring tasks). Do NOT extract:
- Questions that were already answered
- Simple one-shot requests
- Requests already completed in this conversation

Respond with a JSON array. If no persistent goals found, respond with [].

Format:
[
  {
    "title": "short goal title (max 60 chars)",
    "description": "what the assistant should do autonomously",
    "priority": 7,
    "deadline": "2026-04-20" or null,
    "isRecurring": true
  }
]

Only raw JSON, no markdown.`;

    const response = await this.provider.chat(
      [{ role: "user", content: prompt }],
      undefined,
      { maxTokens: 500, temperature: 0.1 },
    );

    const text = response.content.trim();
    // Strip markdown code blocks if present
    const json = text.replace(/^```json?\n?/, "").replace(/\n?```$/, "").trim();

    try {
      const parsed = JSON.parse(json);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (g) =>
          typeof g.title === "string" &&
          typeof g.description === "string" &&
          typeof g.priority === "number",
      ) as ExtractedGoal[];
    } catch {
      return [];
    }
  }

  /** Simple word-overlap similarity (0-1) for deduplication. */
  private similarity(a: string, b: string): number {
    const wa = new Set(a.toLowerCase().split(/\W+/).filter((w) => w.length > 3));
    const wb = new Set(b.toLowerCase().split(/\W+/).filter((w) => w.length > 3));
    if (wa.size === 0 || wb.size === 0) return 0;
    let shared = 0;
    for (const w of wa) if (wb.has(w)) shared++;
    return shared / Math.max(wa.size, wb.size);
  }
}
