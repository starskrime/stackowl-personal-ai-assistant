/**
 * StackOwl — MessageCompressor
 *
 * Every 20 messages, summarizes the oldest batch into a structured JSON summary
 * stored in the SQLite `summaries` table. Also extracts:
 *   - key_facts    → written to `facts` table (permanent, searchable)
 *   - skills       → written to `owl_learnings` (category: 'skill')
 *   - failures     → written to `owl_learnings` (category: 'failure')
 *
 * Context assembly then uses: latest summary (~300 tokens) + last 10 raw messages
 * (~2,000 tokens) instead of all 50 messages (~10,000 tokens).
 * Net saving: ~74% on history tokens for long conversations.
 *
 * Uses a fast/cheap model for summarization (Haiku or gpt-4o-mini).
 * Cost: ~700 tokens per batch of 20 = ~35 tokens/message amortized.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { MemoryDatabase, Summary } from "./db.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface CompressionResult {
  summary: Summary;
  factsWritten: number;
  learningsWritten: number;
  tokensSaved: number;
}

interface SummaryData {
  task: string;
  accomplished: string;
  keyFacts: string[];
  decisions: string[];
  failedApproaches: string[];
  openQuestions: string[];
}

// ─── Compressor ────────────────────────────────────────────────────

export class MessageCompressor {
  private static readonly BATCH_SIZE = 20;
  // Reserved for future sliding-window assembly of recent messages
  // private static readonly KEEP_RECENT = 10;
  // Avg tokens per message (conservative estimate for cost tracking)
  private static readonly AVG_TOKENS_PER_MSG = 200;

  constructor(
    private db: MemoryDatabase,
    private provider: ModelProvider,
  ) {}

  /**
   * Called from PostProcessor when session message count crosses a batch boundary.
   * Summarizes the oldest uncompressed batch and writes findings to DB.
   */
  async compress(
    sessionId: string,
    userId: string,
    owlName: string,
    messages: ChatMessage[],
  ): Promise<CompressionResult | null> {
    if (messages.length < MessageCompressor.BATCH_SIZE) return null;

    // Only compress messages not already covered by an existing summary
    const latestSummary = this.db.summaries.getLatest(sessionId);
    const compressedUpTo = latestSummary?.toSeq ?? -1;

    // Find messages after the last compressed point
    const uncompressed = messages.filter((_, i) => i > compressedUpTo);
    if (uncompressed.length < MessageCompressor.BATCH_SIZE) return null;

    const batch = uncompressed.slice(0, MessageCompressor.BATCH_SIZE);
    const fromSeq = compressedUpTo + 1;
    const toSeq = fromSeq + batch.length - 1;

    log.engine.info(
      `[Compressor] Compressing ${batch.length} messages for session ${sessionId} (seq ${fromSeq}–${toSeq})`,
    );

    try {
      const data = await this.summarize(batch);

      const tokensSaved = Math.max(0,
        batch.length * MessageCompressor.AVG_TOKENS_PER_MSG -
        this.estimateSummaryTokens(data),
      );

      const summary = this.db.summaries.add({
        sessionId,
        userId,
        owlName,
        fromSeq,
        toSeq,
        messageCount: batch.length,
        summaryText: this.toProseString(data),
        task: data.task,
        accomplished: data.accomplished,
        keyFacts: data.keyFacts,
        decisions: data.decisions,
        failedApproaches: data.failedApproaches,
        openQuestions: data.openQuestions,
        tokensSaved,
      });

      // Extract learnings → write to facts + owl_learnings
      let factsWritten = 0;
      let learningsWritten = 0;

      for (const fact of data.keyFacts) {
        if (!fact.trim()) continue;
        this.db.facts.add({
          userId,
          owlName,
          fact,
          category: "skill",
          confidence: 0.75,
          source: "inferred",
          expiresAt: new Date(Date.now() + 180 * 86400_000).toISOString(), // 6 months
        });
        factsWritten++;
      }

      for (const skill of data.keyFacts) {
        if (!skill.trim()) continue;
        this.db.owlLearnings.add(owlName, skill, "skill", sessionId, 0.7);
        learningsWritten++;
      }

      for (const failure of data.failedApproaches) {
        if (!failure.trim()) continue;
        this.db.owlLearnings.add(owlName, failure, "failure", sessionId, 0.8);
        learningsWritten++;
      }

      if (data.accomplished) {
        this.db.owlLearnings.add(owlName, data.accomplished, "insight", sessionId, 0.7);
        learningsWritten++;
      }

      log.engine.info(
        `[Compressor] Done — ${factsWritten} facts, ${learningsWritten} learnings, ~${tokensSaved} tokens saved`,
      );

      return { summary, factsWritten, learningsWritten, tokensSaved };
    } catch (err) {
      log.engine.warn(
        `[Compressor] Summarization failed: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
  }

  /**
   * Build context for the prompt: latest summary + last N raw messages.
   * This replaces injecting all 50 session messages.
   */
  buildContext(
    sessionId: string,
    _recentMessages: ChatMessage[],
  ): string {
    const summary = this.db.summaries.getLatest(sessionId);
    const parts: string[] = [];

    if (summary) {
      parts.push("<conversation_history_summary>");
      if (summary.task) parts.push(`  Task: ${summary.task}`);
      if (summary.accomplished) parts.push(`  Accomplished: ${summary.accomplished}`);
      if (summary.keyFacts.length > 0) {
        parts.push("  Key facts:");
        for (const f of summary.keyFacts) parts.push(`    - ${f}`);
      }
      if (summary.decisions.length > 0) {
        parts.push("  Decisions made:");
        for (const d of summary.decisions) parts.push(`    - ${d}`);
      }
      if (summary.failedApproaches.length > 0) {
        parts.push("  Already tried (failed):");
        for (const f of summary.failedApproaches) parts.push(`    - ${f}`);
      }
      if (summary.openQuestions.length > 0) {
        parts.push("  Still open:");
        for (const q of summary.openQuestions) parts.push(`    - ${q}`);
      }
      parts.push(`  (covers ${summary.messageCount} earlier messages)`);
      parts.push("</conversation_history_summary>");
    }

    return parts.join("\n");
  }

  // ── Private helpers ────────────────────────────────────────────

  private async summarize(messages: ChatMessage[]): Promise<SummaryData> {
    const transcript = messages
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => `${m.role.toUpperCase()}: ${(m.content ?? "").slice(0, 500)}`)
      .join("\n");

    const prompt = `Summarize this conversation segment. Return ONLY valid JSON, no markdown.

Conversation:
${transcript}

Return this exact JSON structure:
{
  "task": "what the user was trying to accomplish (1 sentence)",
  "accomplished": "what was actually resolved (1 sentence, empty string if nothing)",
  "keyFacts": ["specific facts discovered that are worth remembering permanently"],
  "decisions": ["choices made during this conversation"],
  "failedApproaches": ["what was tried but didn't work, with brief reason"],
  "openQuestions": ["things still unresolved or unclear"]
}

Be specific and actionable. Include tool names, commands, URLs if relevant.`;

    const response = await this.provider.chat([
      { role: "user", content: prompt },
    ], undefined, {
      maxTokens: 400,
      temperature: 0,
    });

    const raw = response.content.trim()
      .replace(/^```json\s*/i, "")
      .replace(/^```\s*/i, "")
      .replace(/\s*```$/, "");

    try {
      const parsed = JSON.parse(raw) as SummaryData;
      return {
        task: parsed.task ?? "",
        accomplished: parsed.accomplished ?? "",
        keyFacts: Array.isArray(parsed.keyFacts) ? parsed.keyFacts.slice(0, 8) : [],
        decisions: Array.isArray(parsed.decisions) ? parsed.decisions.slice(0, 5) : [],
        failedApproaches: Array.isArray(parsed.failedApproaches) ? parsed.failedApproaches.slice(0, 5) : [],
        openQuestions: Array.isArray(parsed.openQuestions) ? parsed.openQuestions.slice(0, 4) : [],
      };
    } catch {
      // Fallback: minimal summary from raw text
      return {
        task: "Conversation summary",
        accomplished: raw.slice(0, 200),
        keyFacts: [],
        decisions: [],
        failedApproaches: [],
        openQuestions: [],
      };
    }
  }

  private toProseString(data: SummaryData): string {
    const parts = [`Task: ${data.task}`];
    if (data.accomplished) parts.push(`Accomplished: ${data.accomplished}`);
    if (data.keyFacts.length > 0) parts.push(`Key facts: ${data.keyFacts.join("; ")}`);
    if (data.failedApproaches.length > 0) parts.push(`Failed: ${data.failedApproaches.join("; ")}`);
    return parts.join(". ");
  }

  private estimateSummaryTokens(data: SummaryData): number {
    const text = this.toProseString(data);
    return Math.ceil(text.length / 4); // rough chars-to-tokens estimate
  }
}
