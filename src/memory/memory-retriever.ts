/**
 * StackOwl — Memory Retriever
 *
 * Unified retrieval pipeline that queries all memory systems in parallel
 * and merges results into a single context string for injection into
 * the owl's system prompt.
 *
 * Architecture:
 *   Query → [EpisodicMemory] ← semantic episodes with embeddings
 *         → [FactStore] ← structured facts with categories
 *         → [KnowledgeGraph] ← entity nodes + relationships
 *         → [PreferenceStore] ← explicit user preferences
 *         → [PelletStore] ← knowledge pellets
 *   Merge & Rank → deduplicate → context string
 *
 * Inspired by mem0's retrieval layer but adapted for StackOwl's
 * domain (CLI workflows, code projects, owl personas).
 */

import type { ModelProvider } from "../providers/base.js";
import type { Episode, EpisodicMemory } from "./episodic.js";
import type { FactStore, StoredFact, FactCategory } from "./fact-store.js";
import type { KnowledgeNode } from "../knowledge/types.js";
import { KnowledgeGraph } from "../knowledge/graph.js";
import type { UserPreferenceModel } from "../preferences/model.js";
import type { PelletStore, Pellet } from "../pellets/store.js";
import { log } from "../logger.js";

export interface MemoryRetrievalQuery {
  query: string;
  userId?: string;
  limit?: number;
  categories?: FactCategory[];
  domains?: string[];
  includeEpisodes?: boolean;
  includeFacts?: boolean;
  includeGraph?: boolean;
  includePreferences?: boolean;
  includePellets?: boolean;
}

export interface MemoryRetrievalResult {
  episodes: Episode[];
  facts: StoredFact[];
  graphNodes: KnowledgeNode[];
  preferences: UserPreferenceModel | null;
  pellets: Pellet[];
  query: string;
  retrievedAt: string;
}

const DEFAULT_LIMIT = 10;

export class MemoryRetriever {
  constructor(
    private episodicMemory: EpisodicMemory,
    private factStore: FactStore,
    private knowledgeGraph: KnowledgeGraph,
    private preferenceModel: UserPreferenceModel,
    private pelletStore: PelletStore,
    private provider?: ModelProvider,
  ) {}

  async retrieve(query: MemoryRetrievalQuery): Promise<MemoryRetrievalResult> {
    const limit = query.limit ?? DEFAULT_LIMIT;
    const queryText = query.query.trim();

    const allResults = await Promise.allSettled([
      query.includeEpisodes !== false
        ? this.retrieveEpisodes(queryText, limit, query.categories)
        : Promise.resolve([]),
      query.includeFacts !== false
        ? this.retrieveFacts(queryText, query.userId, limit, query.categories)
        : Promise.resolve([]),
      query.includeGraph !== false
        ? this.retrieveGraph(queryText, limit, query.domains)
        : Promise.resolve([]),
      query.includePreferences !== false
        ? this.retrievePreferences(query.userId)
        : Promise.resolve(null),
      query.includePellets !== false
        ? this.retrievePellets(queryText, limit)
        : Promise.resolve([]),
    ]);

    const episodes = this.extractResult(allResults[0], 0);
    const facts = this.extractResult(allResults[1], 1);
    const graphNodes = this.extractResult(allResults[2], 2);
    const preferences = this.extractResult(allResults[3], 3);
    const pellets = this.extractResult(allResults[4], 4);

    log.memory.debug(
      `[MemoryRetriever] query="${queryText.slice(0, 50)}" → ` +
        `episodes=${episodes.length} facts=${facts.length} ` +
        `graph=${graphNodes.length} prefs=${preferences ? 1 : 0} pellets=${pellets.length}`,
    );

    return {
      episodes,
      facts,
      graphNodes,
      preferences,
      pellets,
      query: queryText,
      retrievedAt: new Date().toISOString(),
    };
  }

  async toContextString(
    results: MemoryRetrievalResult,
    options?: { maxEpisodes?: number; maxFacts?: number; maxPellets?: number },
  ): Promise<string> {
    const sections: string[] = [];
    const maxEp = options?.maxEpisodes ?? 3;
    const maxFacts = options?.maxFacts ?? 5;
    const maxPellets = options?.maxPellets ?? 3;

    if (results.episodes.length > 0) {
      const lines = results.episodes
        .slice(0, maxEp)
        .map(
          (ep) =>
            `[${new Date(ep.date).toLocaleDateString()}] ${ep.summary}` +
            (ep.keyFacts.length > 0
              ? ` | Key facts: ${ep.keyFacts.slice(0, 2).join("; ")}`
              : ""),
        );
      sections.push(
        `<episodic_memory>\n  ${lines.join("\n  ")}\n</episodic_memory>`,
      );
    }

    if (results.facts.length > 0) {
      const lines = results.facts.slice(0, maxFacts).map((f) => {
        const entity = f.entity ? `[${f.entity}] ` : "";
        const conf = f.confidence >= 0.7 ? "✓" : "~";
        return `${conf} ${entity}${f.fact}`;
      });
      sections.push(`<user_facts>\n  ${lines.join("\n  ")}\n</user_facts>`);
    }

    if (results.graphNodes.length > 0) {
      const lines = results.graphNodes.slice(0, maxFacts).map((n) => {
        const domain = n.domain ? `[${n.domain}] ` : "";
        return `- ${domain}${n.title}: ${n.content.slice(0, 100)}`;
      });
      sections.push(
        `<knowledge_graph>\n  ${lines.join("\n  ")}\n</knowledge_graph>`,
      );
    }

    if (results.preferences) {
      const prefStr = results.preferences.toContextString();
      const commStyle = results.preferences.getCommunicationStyle();
      if (prefStr || commStyle) {
        const lines: string[] = [];
        if (commStyle) lines.push(commStyle);
        if (prefStr) lines.push(prefStr);
        sections.push(
          `<user_preferences>\n  ${lines.join("\n  ")}\n</user_preferences>`,
        );
      }
    }

    if (results.pellets.length > 0) {
      const lines = results.pellets
        .slice(0, maxPellets)
        .map((p) => `# ${p.title}\n${p.content.slice(0, 150)}...`);
      sections.push(
        `<knowledge_pellets>\n  ${lines.join("\n\n  ")}\n</knowledge_pellets>`,
      );
    }

    return sections.join("\n\n");
  }

  private async retrieveEpisodes(
    query: string,
    limit: number,
    _categories?: FactCategory[],
  ): Promise<Episode[]> {
    return this.episodicMemory.search(query, limit, this.provider);
  }

  private async retrieveFacts(
    query: string,
    userId?: string,
    limit?: number,
    categories?: FactCategory[],
  ): Promise<StoredFact[]> {
    const all = this.factStore.search(query, userId);
    let filtered = all;
    if (categories && categories.length > 0) {
      filtered = filtered.filter((f) => categories.includes(f.category));
    }
    return filtered.slice(0, limit);
  }

  private async retrieveGraph(
    query: string,
    limit: number,
    domains?: string[],
  ): Promise<KnowledgeNode[]> {
    if (this.provider) {
      const nodes = await this.knowledgeGraph.semanticSearch(
        query,
        limit,
        async (text) => {
          const resp = await this.provider!.embed(text);
          return resp.embedding ?? [];
        },
      );
      if (domains && domains.length > 0) {
        return nodes.filter((n) => domains.includes(n.domain));
      }
      return nodes;
    }
    const nodes = this.knowledgeGraph.search(query, limit);
    if (domains && domains.length > 0) {
      return nodes.filter((n) => domains.includes(n.domain));
    }
    return nodes;
  }

  private async retrievePreferences(
    _userId?: string,
  ): Promise<UserPreferenceModel | null> {
    return this.preferenceModel ?? null;
  }

  private async retrievePellets(
    query: string,
    limit: number,
  ): Promise<Pellet[]> {
    return this.pelletStore
      .search(query)
      .then((results) => results.slice(0, limit));
  }

  private extractResult<T>(result: PromiseSettledResult<T>, _index: number): T {
    if (result.status === "fulfilled") {
      return result.value;
    }
    log.memory.warn(
      `[MemoryRetriever] Retrieval stage ${_index} failed: ${result.reason}`,
    );
    if (_index === 3) return null as T;
    return [] as unknown as T;
  }
}
