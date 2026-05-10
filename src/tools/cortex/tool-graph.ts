/**
 * StackOwl — Element 7 T8 — Cost-Weighted Tool Graph (single-hop replan)
 *
 * On a GAV BLOCKED verdict the registry asks the graph for a next-best tool
 * to retry the same capability tag. v1 is single-hop: pick the alternative
 * with the highest historical success rate from `tool_edges`, filtered by a
 * minimum-samples noise floor and any caller-supplied exclusions.
 *
 * Host-aware extension (Element 16d T7): when a `hostRoot` is provided in
 * ReplanOptions, the graph first checks for a host-specific edge
 * (host_root = hostRoot) before falling back to the global pool
 * (host_root = ''). Without hostRoot, only global rows are considered,
 * preventing per-host rows from polluting global queries.
 *
 * Multi-hop extension point: replace the single SELECT with a Dijkstra over
 * (1 - success_rate) weights once chained recovery (A → B → C) is justified
 * by data. Not needed at current scale.
 */
import type { MemoryDatabase } from "../../memory/db.js";
import { log } from "../../logger.js";

export interface ReplanOptions {
  /** Tool names to skip in addition to the current/failing tool. */
  exclude?: string[];
  /** Hostname (e.g. "amazon.com") to prefer host-specific edges first. */
  hostRoot?: string;
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

    log.tool.debug("tool-graph.replan: entry", {
      currentTool,
      capabilityTag,
      exclude,
      minSamples,
      hostRoot: opts.hostRoot,
    });

    // 1. Try host-specific edge first when hostRoot is provided
    if (opts.hostRoot) {
      const hostRow = this.db.rawDb
        .prepare(
          `SELECT to_tool FROM tool_edges
              WHERE capability_tag = ? AND host_root = ?
                AND sample_count >= ?
                AND to_tool NOT IN (${placeholders})
              ORDER BY success_rate DESC, sample_count DESC LIMIT 1`,
        )
        .get(capabilityTag, opts.hostRoot, minSamples, ...exclude) as
        | { to_tool: string }
        | undefined;
      if (hostRow) {
        log.tool.debug("tool-graph.replan: host-specific alternative selected", {
          chosen: hostRow.to_tool,
          reason: "host-specific edge matched",
          hostRoot: opts.hostRoot,
          capabilityTag,
        });
        return hostRow.to_tool;
      }
      log.tool.debug("tool-graph.replan: no host-specific edge found, falling back to global", {
        hostRoot: opts.hostRoot,
        capabilityTag,
      });
    }

    // 2. Global fallback — filter to host_root = '' to prevent per-host rows
    //    from appearing as global fallbacks when no hostRoot was provided
    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
            WHERE capability_tag = ? AND host_root = ''
              AND sample_count >= ?
              AND to_tool NOT IN (${placeholders})
            ORDER BY success_rate DESC, sample_count DESC, avg_duration_ms ASC
            LIMIT 1`,
      )
      .get(capabilityTag, minSamples, ...exclude) as
      | { to_tool: string }
      | undefined;

    if (row) {
      log.tool.debug("tool-graph.replan: global alternative selected", {
        chosen: row.to_tool,
        reason: "global edge matched",
        capabilityTag,
      });
    } else {
      log.tool.debug("tool-graph.replan: no alternative found", {
        reason: "no qualifying edge in graph",
        capabilityTag,
        exclude,
        minSamples,
      });
    }

    log.tool.debug("tool-graph.replan: exit", {
      result: row?.to_tool ?? null,
      success: true,
    });

    return row?.to_tool ?? null;
  }
}
