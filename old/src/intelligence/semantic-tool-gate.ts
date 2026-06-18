/**
 * StackOwl — SemanticToolGate
 *
 * Reduces LLM tool overload by selecting the top-K most relevant tools
 * for the current query using cosine similarity on tool description embeddings.
 *
 * Falls back gracefully when no embed function is provided (returns all tools
 * up to the requested limit in registration order).
 */

import type { ToolDefinition } from "../providers/base.js";

function cosineSimilarity(a: Float32Array, b: Float32Array): number {
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i]! * b[i]!;
    normA += a[i]! * a[i]!;
    normB += b[i]! * b[i]!;
  }
  const denom = Math.sqrt(normA) * Math.sqrt(normB);
  return denom === 0 ? 0 : dot / denom;
}

export class SemanticToolGate {
  private embeddings = new Map<string, Float32Array>();
  private tools: ToolDefinition[] = [];
  private embedFn?: (text: string) => Promise<number[]>;

  /**
   * Index a set of tools. Optionally provide an embed function to enable
   * semantic similarity ranking. Without embedFn the gate falls back to
   * returning tools in registration order.
   */
  async index(tools: ToolDefinition[], embedFn?: (text: string) => Promise<number[]>): Promise<void> {
    this.tools = tools;
    this.embedFn = embedFn;
    this.embeddings.clear();
    if (!embedFn) return; // No embedding function — fallback mode
    for (const tool of tools) {
      const vec = await embedFn(`${tool.name}: ${tool.description}`);
      this.embeddings.set(tool.name, new Float32Array(vec));
    }
  }

  /**
   * Return the top-`limit` most relevant tools for `query`.
   *
   * - With embedFn: ranks by cosine similarity of query vs tool embeddings.
   * - Without embedFn or empty query: returns first `limit` tools in order.
   */
  async getRelevant(query: string, limit: number): Promise<ToolDefinition[]> {
    if (!this.embedFn || this.embeddings.size === 0 || !query.trim()) {
      return this.tools.slice(0, limit);
    }
    const queryVec = new Float32Array(await this.embedFn(query));
    const scored = this.tools.map(tool => {
      const toolVec = this.embeddings.get(tool.name);
      const score = toolVec ? cosineSimilarity(queryVec, toolVec) : 0;
      return { tool, score };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, limit).map(s => s.tool);
  }
}
