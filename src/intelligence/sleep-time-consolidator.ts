import { randomUUID } from "node:crypto";
import type { Database as BetterSqlite3 } from "better-sqlite3";
import type { ModelProvider } from "../providers/base.js";

// ─── Types ────────────────────────────────────────────────────────

/** Minimal shape accepted from MemoryDatabase or a raw better-sqlite3 instance. */
interface DbWithRaw {
  rawDb: BetterSqlite3;
}

export interface PelletStore {
  store(pellet: {
    id: string;
    userId: string;
    content: string;
    tags: string[];
    source: string;
    confidence: number;
    createdAt: string;
  }): Promise<string>;
}

// ─── SleepTimeConsolidator ────────────────────────────────────────

/**
 * Consolidates insights from recent session summaries after a session ends.
 * Uses an LLM to infer cross-session patterns about the user, then stores
 * them as knowledge pellets for future retrieval.
 *
 * Non-blocking: designed to run in the PostProcessor's background task queue.
 * Debounced per-user to avoid hammering the LLM when multiple sessions end
 * in quick succession (e.g., tab switching or reconnects).
 */
export class SleepTimeConsolidator {
  private readonly raw: BetterSqlite3;
  private lastRunAt = new Map<string, number>();
  private readonly DEBOUNCE_MS = 60 * 60 * 1000; // 60 minutes

  constructor(
    db: DbWithRaw | BetterSqlite3,
    private readonly provider: ModelProvider,
    private readonly pelletStore: PelletStore,
  ) {
    // Accept either a MemoryDatabase wrapper (has .rawDb) or a raw db instance
    this.raw = (db as DbWithRaw).rawDb ?? (db as BetterSqlite3);
  }

  /**
   * Called when a session ends. Loads recent session summaries for this user,
   * asks the LLM to infer new patterns, and stores them as pellets.
   *
   * Skips silently when:
   *  - called again within DEBOUNCE_MS for the same user (per-user debounce)
   *  - no prior session summaries exist (nothing to consolidate)
   *  - the LLM call fails (non-blocking)
   */
  async onSessionEnded(userId: string, _sessionId: string): Promise<void> {
    // Per-user debounce: skip if we ran within the last 60 minutes
    const last = this.lastRunAt.get(userId) ?? 0;
    if (Date.now() - last < this.DEBOUNCE_MS) return;

    // Fetch up to 5 recent summaries for this user
    const recentSummaries = this.raw
      .prepare(
        `SELECT summary_text FROM summaries
         WHERE user_id = ? ORDER BY created_at DESC LIMIT 5`,
      )
      .all(userId) as { summary_text: string }[];

    // Nothing to consolidate if no prior sessions (don't consume debounce window)
    if (recentSummaries.length === 0) return;

    // Stamp debounce only after confirming there's real work to do
    this.lastRunAt.set(userId, Date.now());

    const context = recentSummaries
      .map((s, i) => `Session ${i + 1}: ${s.summary_text}`)
      .join("\n\n");

    const prompt =
      `Based on these recent sessions with this user:\n\n${context}\n\n` +
      `What 1-3 new patterns or insights about this user can you infer that aren't explicitly stated? ` +
      `Be specific and concise. Each insight on its own line.`;

    let insights: string;
    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { maxTokens: 200, temperature: 0.4 },
      );
      insights = response.content.trim();
    } catch {
      // Non-blocking — silently skip on LLM failure
      return;
    }

    // Parse lines, filter noise, cap at 3 pellets
    const lines = insights
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 10);

    for (const line of lines.slice(0, 3)) {
      await this.pelletStore.store({
        id: randomUUID(),
        userId,
        content: line,
        tags: ["sleep_consolidation", "pattern"],
        source: "sleep_consolidation",
        confidence: 0.7,
        createdAt: new Date().toISOString(),
      });
    }
  }
}
