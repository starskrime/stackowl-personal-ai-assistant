import { MemoryDatabase } from "../memory/db.js";
import { embed, isEmbedderReady } from "../pellets/embedder.js";

export type FactCategory =
  | "skill" | "preference" | "project_detail" | "personal" | "context"
  | "goal" | "habit" | "relationship" | "decision" | "open_question"
  | "active_goal" | "sub_goal";

const DEDUP_THRESHOLD = 0.88;

export class UserMemoryStore {
  constructor(private db: MemoryDatabase) {}

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
        // dedup check — getAllForUser returns Fact[] with embedding: number[] | undefined
        const existing = this.db.facts.getAllForUser(userId);
        for (const row of existing) {
          if (row.embedding) {
            const sim = cosineSimilarity(newEmbedding, row.embedding);
            if (sim >= DEDUP_THRESHOLD) return; // duplicate — skip
          }
        }
        this.db.facts.add({
          userId,
          owlName,
          fact,
          category,
          confidence: 0.8,
          source: "inferred",
          embedding: newEmbedding,
        });
        return;
      }
    }
    // no embedder: store without embedding (no dedup)
    this.db.facts.add({
      userId,
      owlName,
      fact,
      category,
      confidence: 0.8,
      source: "inferred",
      embedding: undefined,
    });
  }

  private ftsFallback(userId: string, query: string, limit: number): string[] {
    try {
      const rows = (this.db.rawDb as any)
        .prepare("SELECT fact FROM facts_fts WHERE facts_fts MATCH ? AND user_id = ? LIMIT ?")
        .all(query, userId, limit) as Array<{ fact: string }>;
      return rows.map((r) => r.fact);
    } catch {
      return [];
    }
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
