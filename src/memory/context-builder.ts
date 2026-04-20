/**
 * StackOwl — MemoryFirstContextBuilder
 *
 * Replaces the old 9-subsystem context injection chaos with a single,
 * priority-ordered builder that respects a hard 1200-token budget.
 *
 * Budget allocation (all 3 sections must fit within 1200 tokens total):
 *   Priority 1 — 400 tokens : User preferences snapshot
 *   Priority 2 — 400 tokens : Relevant episodic / fact memory
 *   Priority 3 — 400 tokens : Relevant pellets (knowledge artifacts)
 *
 * Each section is individually capped; if a section is empty or its
 * subsystem throws, the budget is NOT redistributed — the section is
 * simply omitted and the remaining sections still get their own cap.
 */

import { log } from "../logger.js";
import type { PelletStore } from "../pellets/store.js";
import type { UserPreferenceModel } from "../preferences/model.js";
import type { MemoryDatabase } from "./db.js";

// ─── Public types ────────────────────────────────────────────────────────────

export interface MemoryContext {
  /** Formatted preference block, or empty string if nothing was injected. */
  preferences: string;
  /** Formatted memory/fact block, or empty string. */
  memory: string;
  /** Formatted pellet block, or empty string. */
  pellets: string;
  /** Total estimated tokens across all three sections. */
  totalTokens: number;
}

export interface MemoryFirstContextBuilderOptions {
  /**
   * PelletStore instance.
   * Pass undefined to skip the pellet section gracefully.
   */
  pelletStore?: PelletStore;

  /**
   * MemoryDatabase instance used to search facts and episodic memory.
   * Pass undefined to skip the memory section gracefully.
   */
  factStore?: MemoryDatabase;

  /**
   * UserPreferenceModel instance (inferred behavioral preferences).
   * Pass undefined to skip the preference section gracefully.
   */
  preferenceModel?: UserPreferenceModel;

  /** The current user message — used as the search query for memory + pellets. */
  userMessage: string;

  /**
   * Optional userId to scope fact / episode searches.
   * Falls back to no-filter when omitted.
   */
  userId?: string;
}

// ─── Token estimator ─────────────────────────────────────────────────────────

/**
 * Lightweight token estimator — no external tokenizer required.
 * Approximates GPT-style tokenization: ~3.8 chars per token on average.
 */
function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.8);
}

/**
 * Trim `text` so that estimateTokens(result) <= maxTokens.
 * Trims at sentence boundary when possible to keep the output readable.
 */
function trimToTokenBudget(text: string, maxTokens: number): string {
  if (estimateTokens(text) <= maxTokens) return text;

  const maxChars = Math.floor(maxTokens * 3.8);

  // Try to cut at the last sentence boundary within the budget
  const truncated = text.slice(0, maxChars);
  const lastPeriod = Math.max(
    truncated.lastIndexOf(". "),
    truncated.lastIndexOf(".\n"),
  );

  if (lastPeriod > maxChars * 0.6) {
    // Found a reasonable sentence boundary — keep it clean
    return truncated.slice(0, lastPeriod + 1).trim();
  }

  // No good boundary found — hard-cut with ellipsis
  return truncated.trimEnd() + "…";
}

// ─── MemoryFirstContextBuilder ───────────────────────────────────────────────

export class MemoryFirstContextBuilder {
  private readonly BUDGET_PREFERENCES = 400; // tokens
  private readonly BUDGET_MEMORY = 400;       // tokens
  private readonly BUDGET_PELLETS = 400;       // tokens

  /**
   * Build the full context string to inject into the system prompt.
   *
   * @returns A markdown-formatted string combining all sections that had data,
   *          or an empty string if nothing was available.
   */
  async build(opts: MemoryFirstContextBuilderOptions): Promise<string> {
    const { userMessage, userId, pelletStore, factStore, preferenceModel } = opts;

    const context = await this.buildContext({
      userMessage,
      userId,
      pelletStore,
      factStore,
      preferenceModel,
    });

    const parts: string[] = [];
    if (context.preferences) parts.push(context.preferences);
    if (context.memory) parts.push(context.memory);
    if (context.pellets) parts.push(context.pellets);

    log.engine.info(
      `[MemoryFirstContextBuilder] Injected context — ` +
      `preferences: ${estimateTokens(context.preferences)} tok, ` +
      `memory: ${estimateTokens(context.memory)} tok, ` +
      `pellets: ${estimateTokens(context.pellets)} tok, ` +
      `total: ${context.totalTokens} tok`,
    );

    return parts.join("\n\n");
  }

  /**
   * Build and return the structured MemoryContext object.
   * Useful for callers that want per-section data rather than the flat string.
   */
  async buildContext(opts: MemoryFirstContextBuilderOptions): Promise<MemoryContext> {
    const { userMessage, userId, pelletStore, factStore, preferenceModel } = opts;

    // Run all three sections in parallel — each is independently defensive
    const [preferences, memory, pellets] = await Promise.all([
      this.buildPreferences(preferenceModel),
      this.buildMemory(factStore, userMessage, userId),
      this.buildPellets(pelletStore, userMessage),
    ]);

    const totalTokens =
      estimateTokens(preferences) +
      estimateTokens(memory) +
      estimateTokens(pellets);

    return { preferences, memory, pellets, totalTokens };
  }

  // ─── Priority 1: User Preferences (400 tokens) ────────────────────────────

  private async buildPreferences(
    preferenceModel?: UserPreferenceModel,
  ): Promise<string> {
    if (!preferenceModel) return "";

    try {
      // Gather active preferences with confidence > 0.5
      const allPrefs = preferenceModel.getAll();
      const active = allPrefs.filter((p) => p.confidence > 0.5);

      if (active.length === 0) {
        // No preferences exist yet — do NOT inject anything
        return "";
      }

      const commStyle = preferenceModel.getCommunicationStyle();

      const prefLines = active
        .sort((a, b) => b.confidence - a.confidence) // highest confidence first
        .map((p) => {
          const conf = Math.round(p.confidence * 100);
          return `- ${p.key}: ${String(p.value)} (${conf}% confidence)`;
        })
        .join("\n");

      const block =
        `## User Preferences\n` +
        `Communication style: ${commStyle}\n\n` +
        `Active inferred preferences:\n${prefLines}`;

      return trimToTokenBudget(block, this.BUDGET_PREFERENCES);
    } catch (err) {
      log.engine.warn(
        `[MemoryFirstContextBuilder] Preference section failed — skipping. ` +
        `Error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return "";
    }
  }

  // ─── Priority 2: Episodic / Fact Memory (400 tokens) ─────────────────────

  private async buildMemory(
    factStore?: MemoryDatabase,
    userMessage?: string,
    userId?: string,
  ): Promise<string> {
    if (!factStore || !userMessage) return "";

    try {
      // Extract meaningful keywords from the user message for better FTS results
      const query = this.extractSearchKeywords(userMessage);

      // Search facts (FTS5 + LIKE fallback built into FactsRepo)
      const facts = factStore.facts.search(query, userId, 5);

      // Search episodic memory
      const episodes = factStore.episodes.search(query, userId, 3);

      if (facts.length === 0 && episodes.length === 0) return "";

      const parts: string[] = ["## Memory\nYou previously learned:"];

      if (facts.length > 0) {
        const factLines = facts
          .slice(0, 5)
          .map((f) => `- ${f.fact}`)
          .join("\n");
        parts.push(`**Facts:**\n${factLines}`);
      }

      if (episodes.length > 0) {
        const episodeLines = episodes
          .slice(0, 3)
          .map((e) => {
            const keyFacts =
              e.keyFacts.length > 0 ? ` Key facts: ${e.keyFacts.slice(0, 2).join("; ")}.` : "";
            return `- ${e.summary}${keyFacts}`;
          })
          .join("\n");
        parts.push(`**Past sessions:**\n${episodeLines}`);
      }

      const block = parts.join("\n\n");
      return trimToTokenBudget(block, this.BUDGET_MEMORY);
    } catch (err) {
      log.engine.warn(
        `[MemoryFirstContextBuilder] Memory section failed — skipping. ` +
        `Error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return "";
    }
  }

  /**
   * Extract keywords from a user message for FTS querying.
   * Strips common stop words and very short tokens to improve FTS5 accuracy.
   */
  private extractSearchKeywords(message: string): string {
    const STOP_WORDS = new Set([
      "a", "an", "the", "is", "are", "was", "were", "be", "been",
      "have", "has", "had", "do", "does", "did", "will", "would",
      "could", "should", "may", "might", "shall", "can", "need",
      "i", "me", "my", "we", "our", "you", "your", "it", "its",
      "this", "that", "these", "those", "and", "or", "but", "in",
      "on", "at", "to", "for", "of", "with", "by", "from", "up",
      "about", "into", "through", "during", "before", "after",
      "what", "which", "who", "how", "when", "where", "why",
    ]);

    const words = message
      .toLowerCase()
      .replace(/[^\w\s]/g, " ")  // strip punctuation
      .split(/\s+/)
      .filter((w) => w.length >= 3 && !STOP_WORDS.has(w));

    // Return top 5 keywords to keep the FTS query focused
    return words.slice(0, 5).join(" ") || message.slice(0, 50);
  }

  // ─── Priority 3: Relevant Pellets (400 tokens) ────────────────────────────

  private async buildPellets(
    pelletStore?: PelletStore,
    userMessage?: string,
  ): Promise<string> {
    if (!pelletStore || !userMessage) return "";

    try {
      // Always search pellets — not only in "deep mode"
      const pellets = await pelletStore.search(userMessage, 3, 0.35);

      if (pellets.length === 0) return "";

      const pelletLines = pellets
        .map((p) => {
          // Brief title + first 200 chars of content as excerpt
          const excerpt = p.content.slice(0, 200).trim().replace(/\n+/g, " ");
          const ellipsis = p.content.length > 200 ? "…" : "";
          return `**${p.title}**\n${excerpt}${ellipsis}`;
        })
        .join("\n\n");

      const block = `## Knowledge Base\n${pelletLines}`;
      return trimToTokenBudget(block, this.BUDGET_PELLETS);
    } catch (err) {
      log.engine.warn(
        `[MemoryFirstContextBuilder] Pellet section failed — skipping. ` +
        `Error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return "";
    }
  }
}
