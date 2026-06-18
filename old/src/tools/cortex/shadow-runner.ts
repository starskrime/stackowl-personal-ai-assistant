/**
 * StackOwl — Element 7 T14 — ShadowRunner
 *
 * Shadow execution layer for SET (Self-Evolving Tools). Runs a candidate
 * (rewritten) tool alongside the baseline on every invocation, comparing
 * success rates over a configurable call budget. Verdicts:
 *   - `promote`  — ≥ MIN_CALLS candidate calls AND ≥ +PROMOTE_DELTA success
 *   - `rollback` — ≥ MIN_CALLS candidate calls AND ≤ -ROLLBACK_DELTA
 *   - `continue` — otherwise (more samples needed, or within noise band)
 *
 * State is persisted in `tool_evolution_runs` (schema v24) so counters
 * survive restarts; any process holding the same DB can evaluate the run.
 */
import type { MemoryDatabase } from "../../memory/db.js";
import { log } from "../../logger.js";

export interface ShadowRunStartArgs {
  baselineTool: string;
  candidateTool: string;
  baselinePath: string;
  candidatePath: string;
}

export interface ShadowRecord {
  which: "baseline" | "candidate";
  success: boolean;
}

export type ShadowVerdict = "promote" | "rollback" | "continue";

const MIN_CALLS = 100;
const PROMOTE_DELTA = 0.05;
const ROLLBACK_DELTA = 0.05;

export class ShadowRunner {
  constructor(private readonly db: MemoryDatabase) {}

  start(args: ShadowRunStartArgs): number {
    log.tool.debug("shadow-runner.start: entry", {
      baselineTool: args.baselineTool,
      candidateTool: args.candidateTool,
    });

    const result = this.db.rawDb
      .prepare(
        `INSERT INTO tool_evolution_runs
            (baseline_tool, candidate_tool, baseline_path, candidate_path)
          VALUES (?, ?, ?, ?)`,
      )
      .run(
        args.baselineTool,
        args.candidateTool,
        args.baselinePath,
        args.candidatePath,
      );
    const runId = Number(result.lastInsertRowid);

    log.tool.debug("shadow-runner.start: exit", {
      runId,
      baselineTool: args.baselineTool,
      candidateTool: args.candidateTool,
      success: true,
    });

    return runId;
  }

  record(runId: number, obs: ShadowRecord): void {
    const successCol =
      obs.which === "baseline" ? "baseline_successes" : "candidate_successes";
    const totalCol =
      obs.which === "baseline" ? "baseline_total" : "candidate_total";

    log.tool.debug("shadow-runner.record: observation recorded", {
      runId,
      which: obs.which,
      success: obs.success,
    });

    this.db.rawDb
      .prepare(
        `UPDATE tool_evolution_runs
            SET ${totalCol} = ${totalCol} + 1,
                ${successCol} = ${successCol} + ?
          WHERE id = ?`,
      )
      .run(obs.success ? 1 : 0, runId);
  }

  evaluate(runId: number): ShadowVerdict {
    log.tool.debug("shadow-runner.evaluate: entry", { runId });

    const row = this.db.rawDb
      .prepare(
        `SELECT baseline_successes, baseline_total, candidate_successes, candidate_total
            FROM tool_evolution_runs WHERE id = ?`,
      )
      .get(runId) as
      | {
          baseline_successes: number;
          baseline_total: number;
          candidate_successes: number;
          candidate_total: number;
        }
      | undefined;
    if (!row) {
      log.tool.debug("shadow-runner.evaluate: run not found — returning continue", { runId });
      return "continue";
    }
    if (row.candidate_total < MIN_CALLS) {
      log.tool.debug("shadow-runner.evaluate: insufficient candidate calls — continuing", {
        runId,
        candidateTotal: row.candidate_total,
        minCalls: MIN_CALLS,
      });
      return "continue";
    }
    if (row.baseline_total === 0) {
      log.tool.debug("shadow-runner.evaluate: baseline has no calls — continuing", { runId });
      return "continue";
    }

    const baselineRate = row.baseline_successes / row.baseline_total;
    const candidateRate = row.candidate_successes / row.candidate_total;
    const delta = candidateRate - baselineRate;

    let verdict: ShadowVerdict;
    if (delta >= PROMOTE_DELTA) verdict = "promote";
    else if (delta <= -ROLLBACK_DELTA) verdict = "rollback";
    else verdict = "continue";

    log.tool.debug("shadow-runner.evaluate: verdict reached", {
      runId,
      verdict,
      baselineRate,
      candidateRate,
      delta,
      reason:
        verdict === "promote"
          ? `candidate improved by ${delta.toFixed(3)} >= ${PROMOTE_DELTA}`
          : verdict === "rollback"
            ? `candidate degraded by ${Math.abs(delta).toFixed(3)} >= ${ROLLBACK_DELTA}`
            : "delta within noise band",
    });

    return verdict;
  }

  finish(runId: number, status: "promoted" | "rolled_back"): void {
    log.tool.debug("shadow-runner.finish: shadow run closed", { runId, status });

    this.db.rawDb
      .prepare(
        `UPDATE tool_evolution_runs
            SET status = ?, finished_at = datetime('now')
          WHERE id = ?`,
      )
      .run(status, runId);
  }
}
