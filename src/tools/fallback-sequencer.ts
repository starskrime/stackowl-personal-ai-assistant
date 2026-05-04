/**
 * StackOwl — Fallback Sequencer (DB-backed)
 *
 * Reads learned fallback edges from `tool_edges` (schema v23). Edges are
 * populated by the EdgeAccumulator (Element 7 T9) as a side-effect of
 * successful recovery sequences observed in production.
 *
 * Replaces the in-memory `learnedSequences` map of the previous incarnation —
 * learning now persists across restarts.
 */

import type { MemoryDatabase } from "../memory/db.js";

export class FallbackSequencer {
  constructor(private readonly db: MemoryDatabase) {}

  /**
   * Returns the highest-success-rate alternative tool for the given
   * (fromTool, capabilityTag) edge, excluding any tools already tried.
   *
   * Edges with fewer than 3 samples are ignored — too noisy to trust.
   */
  getNextFallback(
    fromTool: string,
    capabilityTag: string,
    exclude: string[] = [],
  ): string | null {
    const placeholders = exclude.map(() => "?").join(",") || "''";
    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
           WHERE from_tool = ? AND capability_tag = ?
             AND sample_count >= 3
             AND to_tool NOT IN (${placeholders})
           ORDER BY success_rate DESC, sample_count DESC
           LIMIT 1`,
      )
      .get(fromTool, capabilityTag, ...exclude) as
      | { to_tool: string }
      | undefined;
    return row?.to_tool ?? null;
  }
}
