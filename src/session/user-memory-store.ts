import { MemoryDatabase } from "../memory/db.js";
import type { FactCategory } from "../memory/db.js";
import { embed, isEmbedderReady } from "../pellets/embedder.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";

export type { FactCategory };

const DEDUP_THRESHOLD = 0.88;

export class UserMemoryStore {
  constructor(
    private db: MemoryDatabase,
    private eventBus?: GatewayEventBus,
  ) {}

  async retrieve(userId: string, query: string, limit = 3): Promise<string[]> {
    if (isEmbedderReady()) {
      const queryEmbedding = await embed(query);
      if (queryEmbedding) {
        const results = this.db.facts.semanticSearch(queryEmbedding, userId, limit);
        return results.map((r) => r.fact);
      }
    }
    // FTS fallback
    return this.ftsFallback(userId, query, limit);
  }

  async add(
    userId: string,
    fact: string,
    category: FactCategory,
    owlName: string,
  ): Promise<void> {
    if (isEmbedderReady()) {
      const newEmbedding = await embed(fact);
      if (newEmbedding) {
        // dedup check — semanticSearch returns top-1 nearest fact (already sorted by score);
        // semanticSearch strips scores so we recompute similarity for just this one result
        const topMatches = this.db.facts.semanticSearch(newEmbedding, userId, 1);
        if (topMatches.length > 0 && topMatches[0].embedding) {
          const sim = cosineSimilarity(newEmbedding, topMatches[0].embedding);
          if (sim >= DEDUP_THRESHOLD) return; // duplicate — skip
        }
        const stored = this.db.facts.add({
          userId,
          owlName,
          fact,
          category,
          confidence: 0.8,
          source: "inferred",
          embedding: newEmbedding,
        });
        this.eventBus?.emit({ type: "fact:extracted", userId, factText: fact, factId: stored.id });
        return;
      }
    }
    // no embedder: store without embedding (no dedup)
    const stored = this.db.facts.add({
      userId,
      owlName,
      fact,
      category,
      confidence: 0.8,
      source: "inferred",
      embedding: undefined,
    });
    this.eventBus?.emit({ type: "fact:extracted", userId, factText: fact, factId: stored.id });
  }

  private ftsFallback(userId: string, query: string, limit: number): string[] {
    return this.db.facts.search(query, userId, limit).map((r) => r.fact);
  }
}

function cosineSimilarity(a: number[], b: number[]): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0, magA = 0, magB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    magA += a[i] * a[i];
    magB += b[i] * b[i];
  }
  const denom = Math.sqrt(magA) * Math.sqrt(magB);
  return denom === 0 ? 0 : dot / denom;
}
