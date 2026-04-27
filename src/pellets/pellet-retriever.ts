/**
 * StackOwl — Pellet Retriever
 *
 * Retrieves relevant pellets using semantic similarity and injects them
 * into the owl's context to enhance responses with prior knowledge.
 */

import type { PelletStore, Pellet } from "./store.js";
import { log } from "../logger.js";

// ─── Configuration ───────────────────────────────────────────────

export interface RetrievalConfig {
  topK: number;
  threshold: number;
  maxTokensPerPellet: number;
}

export const DEFAULT_RETRIEVAL_CONFIG: RetrievalConfig = {
  topK: 5,
  threshold: 0.4,
  maxTokensPerPellet: 300,
};

// ─── Pellet Retriever ────────────────────────────────────────────

export class PelletRetriever {
  private config: RetrievalConfig;

  constructor(
    private pelletStore: PelletStore,
    config: Partial<RetrievalConfig> = {},
  ) {
    this.config = { ...DEFAULT_RETRIEVAL_CONFIG, ...config };
  }

  /**
   * Retrieve pellets relevant to a query using semantic similarity.
   */
  async retrieveRelevant(query: string): Promise<Pellet[]> {
    try {
      const pellets = await this.pelletStore.search(
        query,
        this.config.topK,
        1 - this.config.threshold,
      );

      if (pellets.length > 0) {
        log.engine.info(
          `[PelletRetriever] Retrieved ${pellets.length}/${await this.pelletStore.count()} pellets ` +
            `(threshold: ${this.config.threshold})`,
        );
      }

      return pellets;
    } catch (err) {
      log.engine.warn(
        `[PelletRetriever] Retrieval failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return [];
    }
  }

  /**
   * Retrieve pellets using a structured query with context.
   */
  async retrieveWithContext(
    query: string,
    context: { sessionId?: string; owlName?: string; tags?: string[] },
  ): Promise<Pellet[]> {
    let pellets = await this.retrieveRelevant(query);

    if (context.tags && context.tags.length > 0) {
      pellets = pellets.filter((p) =>
        context.tags!.some((tag) => p.tags.includes(tag)),
      );
    }

    if (context.owlName) {
      pellets = pellets.filter(
        (p) => p.owls.includes(context.owlName!) || p.owls.length === 0,
      );
    }

    return pellets.slice(0, this.config.topK);
  }

  /**
   * Format retrieved pellets for injection into the system prompt.
   * Produces a <relevant_knowledge> block with domain-tagged excerpts.
   */
  formatForInjection(pellets: Pellet[]): string {
    if (pellets.length === 0) return "";

    const lines = ["<relevant_knowledge>"];
    for (const pellet of pellets) {
      const domain = pellet.tags[0] ?? "general";
      const excerpt = this.truncateContent(pellet.content, this.config.maxTokensPerPellet);
      lines.push(`[${domain}] ${pellet.title}`);
      lines.push(excerpt);
      lines.push("");
    }
    lines.push("</relevant_knowledge>");

    return lines.join("\n");
  }

  /**
   * Retrieve and format in one step.
   */
  async retrieveAndFormat(query: string): Promise<string> {
    const pellets = await this.retrieveRelevant(query);
    return this.formatForInjection(pellets);
  }

  /**
   * Truncate content to max tokens (rough estimate: ~4 chars per token).
   */
  private truncateContent(content: string, maxTokens: number): string {
    const maxChars = maxTokens * 4;
    if (content.length <= maxChars) return content;
    return content.slice(0, maxChars - 3) + "...";
  }
}