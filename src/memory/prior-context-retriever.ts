/**
 * StackOwl — Prior Context Retriever
 *
 * Retrieves relevant context from past conversations when users
 * reference prior discussions ("as I mentioned earlier", "last time").
 */

import type { EpisodicMemory } from "./episodic.js";
import type { Episode } from "./episodic.js";
import type { ModelProvider } from "../providers/base.js";
import { log } from "../logger.js";

export interface PriorContextQuery {
  currentMessage: string;
  sessionId?: string;
  owlName?: string;
  limit?: number;
}

export interface PriorContextResult {
  context: string;
  episodes: ScoredEpisode[];
  hasRelevantContext: boolean;
  matchedReferences: string[];
}

interface ScoredEpisode extends Episode {
  relevanceScore: number;
}

const TEMPORAL_KEYWORDS = [
  "earlier",
  "before",
  "previously",
  "last time",
  "earlier",
  "before that",
  "mentioned",
  "discussed",
  "talked about",
  "brought up",
  "as i said",
  "as i mentioned",
  "remember",
];

const DEFAULT_LIMIT = 3;
const MIN_RELEVANCE_SCORE = 0.2;

export class PriorContextRetriever {
  constructor(
    private episodicMemory: EpisodicMemory,
    private provider?: ModelProvider,
  ) {}

  /**
   * Check if a message contains temporal references to prior context
   */
  hasTemporalReference(message: string): boolean {
    const lower = message.toLowerCase();
    return TEMPORAL_KEYWORDS.some((kw) => lower.includes(kw));
  }

  /**
   * Extract temporal reference keywords from message
   */
  extractTemporalReferences(message: string): string[] {
    const lower = message.toLowerCase();
    return TEMPORAL_KEYWORDS.filter((kw) => lower.includes(kw));
  }

  /**
   * Retrieve prior context based on current message
   */
  async retrieve(query: PriorContextQuery): Promise<PriorContextResult> {
    const { currentMessage, sessionId, owlName, limit = DEFAULT_LIMIT } = query;

    const matchedRefs = this.extractTemporalReferences(currentMessage);

    let episodes: ScoredEpisode[] = [];

    if (this.provider) {
      episodes = await this.searchEpisodes(currentMessage, sessionId, owlName, limit);
    } else {
      const raw = await this.episodicMemory.search(currentMessage, limit, this.provider);
      episodes = raw.map((ep) => ({ ...ep, relevanceScore: 0.5 }));
    }

    const relevantEpisodes = episodes.filter((ep) => ep.relevanceScore >= MIN_RELEVANCE_SCORE);

    if (relevantEpisodes.length === 0) {
      return {
        context: "",
        episodes: [],
        hasRelevantContext: false,
        matchedReferences: matchedRefs,
      };
    }

    const context = this.formatContext(relevantEpisodes);

    log.engine.debug(
      `[PriorContextRetriever] Found ${relevantEpisodes.length} relevant episodes for message: "${currentMessage.slice(0, 50)}"`,
    );

    return {
      context,
      episodes: relevantEpisodes,
      hasRelevantContext: true,
      matchedReferences: matchedRefs,
    };
  }

  /**
   * Search episodes using semantic similarity
   */
  private async searchEpisodes(
    query: string,
    sessionId?: string,
    owlName?: string,
    limit?: number,
  ): Promise<ScoredEpisode[]> {
    const scored = await this.episodicMemory.searchWithScoring(
      query,
      limit ?? DEFAULT_LIMIT,
      this.provider,
      MIN_RELEVANCE_SCORE,
    );

    let filtered: ScoredEpisode[] = scored as unknown as ScoredEpisode[];

    if (sessionId) {
      filtered = filtered.filter((ep) => ep.sessionId !== sessionId);
    }

    if (owlName) {
      filtered = filtered.filter((ep) => ep.owlName === owlName);
    }

    return filtered;
  }

  /**
   * Format episodes as a readable context string
   */
  private formatContext(episodes: ScoredEpisode[]): string {
    if (episodes.length === 0) return "";

    const sections: string[] = ["## Previous Discussion\n"];

    for (const ep of episodes) {
      const date = new Date(ep.date).toLocaleDateString();
      const summary = ep.summary;

      sections.push(`**${date}**: ${summary}`);

      if (ep.keyFacts.length > 0) {
        const facts = ep.keyFacts.slice(0, 3).map((f) => `- ${f}`).join("\n");
        sections.push(`  Key points:\n${facts}`);
      }
    }

    return sections.join("\n");
  }

  /**
   * Build a context string for system prompt injection
   */
  async buildContextPrompt(query: PriorContextQuery): Promise<string> {
    const result = await this.retrieve(query);

    if (!result.hasRelevantContext) {
      return "";
    }

    const warning = result.matchedReferences.length > 0
      ? `\n\nNote: User referenced prior conversation using: "${result.matchedReferences.join(", ")}"\n`
      : "";

    return `${result.context}${warning}`;
  }
}
