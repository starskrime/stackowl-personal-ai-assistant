/**
 * StackOwl — Element 7 T8 — Cost-Weighted Tool Graph (single-hop replan)
 *
 * On a GAV BLOCKED verdict the registry asks the graph for a next-best tool
 * to retry the same capability tag. v1 is single-hop: pick the alternative
 * with the highest historical success rate from `tool_edges`, filtered by a
 * minimum-samples noise floor and any caller-supplied exclusions.
 *
 * Multi-hop extension point: replace the single SELECT with a Dijkstra over
 * (1 - success_rate) weights once chained recovery (A → B → C) is justified
 * by data. Not needed at current scale.
 */
import type { MemoryDatabase } from "../../memory/db.js";

export interface ReplanOptions {
  /** Tool names to skip in addition to the current/failing tool. */
  exclude?: string[];
}

export interface ToolGraphConfig {
  /** Minimum sample_count an edge needs before it's considered. Default: 3. */
  minSamples?: number;
}

export class ToolGraph {
  constructor(
    private readonly db: MemoryDatabase,
    private readonly config: ToolGraphConfig = {},
  ) {}

  replan(
    currentTool: string,
    capabilityTag: string,
    opts: ReplanOptions = {},
  ): string | null {
    const minSamples = this.config.minSamples ?? 3;
    const exclude = Array.from(
      new Set([currentTool, ...(opts.exclude ?? [])]),
    );
    const placeholders = exclude.map(() => "?").join(",");

    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
            WHERE capability_tag = ?
              AND sample_count >= ?
              AND to_tool NOT IN (${placeholders})
            ORDER BY success_rate DESC, sample_count DESC, avg_duration_ms ASC
            LIMIT 1`,
      )
      .get(capabilityTag, minSamples, ...exclude) as
      | { to_tool: string }
      | undefined;

    return row?.to_tool ?? null;
  }
}
