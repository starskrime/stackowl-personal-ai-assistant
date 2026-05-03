/**
 * StackOwl — Element 7 T9 — EdgeAccumulator
 *
 * Writer side of the cost-weighted tool graph. Each `observe()` call updates
 * the (from_tool, to_tool, capability_tag) row in `tool_edges` with running
 * averages: success_rate is the proportion of successful observations,
 * avg_duration_ms is the running mean. Single SQL UPDATE per observation —
 * no per-call query cost beyond a primary-key probe.
 */
import type { MemoryDatabase } from "../../memory/db.js";

export interface EdgeObservation {
  fromTool: string;
  toTool: string;
  capabilityTag: string;
  success: boolean;
  durationMs: number;
}

export class EdgeAccumulator {
  constructor(private readonly db: MemoryDatabase) {}

  observe(obs: EdgeObservation): void {
    const existing = this.db.rawDb
      .prepare(
        "SELECT success_rate, avg_duration_ms, sample_count FROM tool_edges WHERE from_tool = ? AND to_tool = ? AND capability_tag = ?",
      )
      .get(obs.fromTool, obs.toTool, obs.capabilityTag) as
      | { success_rate: number; avg_duration_ms: number; sample_count: number }
      | undefined;

    if (!existing) {
      this.db.rawDb
        .prepare(
          "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        )
        .run(
          obs.fromTool,
          obs.toTool,
          obs.capabilityTag,
          obs.success ? 1 : 0,
          obs.durationMs,
          1,
        );
      return;
    }

    const newCount = existing.sample_count + 1;
    const newRate =
      (existing.success_rate * existing.sample_count + (obs.success ? 1 : 0)) /
      newCount;
    const newAvg = Math.round(
      (existing.avg_duration_ms * existing.sample_count + obs.durationMs) /
        newCount,
    );
    this.db.rawDb
      .prepare(
        "UPDATE tool_edges SET success_rate = ?, avg_duration_ms = ?, sample_count = ?, updated_at = datetime('now') WHERE from_tool = ? AND to_tool = ? AND capability_tag = ?",
      )
      .run(
        newRate,
        newAvg,
        newCount,
        obs.fromTool,
        obs.toTool,
        obs.capabilityTag,
      );
  }
}
