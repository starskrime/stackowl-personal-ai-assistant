/**
 * StackOwl — Element 7 T13 — SelfEvolver scaffolding
 *
 * Orchestrator for the SET (Self-Evolving Tools) loop. v1 ships with the
 * candidate-selection step only — `findCandidate()` returns the
 * worst-performing non-critical tool over a recent window. Subsequent tasks
 * (T14 ShadowRunner, T15 ImprovementScheduler integration) layer the rewrite
 * proposal, shadow execution, and rollback on top of this base.
 *
 * Critical-tool exclusion is enforced AT THE QUERY, not as a post-filter.
 * Rewriting `remember`, `shell`, or `write_file` could destroy user data —
 * these are infrastructure invariants, not classification heuristics.
 */
import type { MemoryDatabase } from "../../memory/db.js";
import type { ShadowRunner } from "./shadow-runner.js";
import { log } from "../../logger.js";

/**
 * Tools whose code or behavior must never be auto-rewritten by SET.
 * Anything touching durable user state, secrets, or shell access goes here.
 *
 * Names are matched against `tool_executions.tool_name` exactly — keep this
 * list synchronized with registered tool names if you rename anything.
 */
export const CRITICAL_TOOLS: ReadonlySet<string> = new Set([
  "remember",
  "recall",
  "pellet_recall",
  "memory",
  "write_file",
  "edit_file",
  "shell",
  "run_shell_command",
  "patch_tool",
  "credentials",
]);

export interface SelfEvolverDeps {
  db: MemoryDatabase;
  patchTool: {
    execute(args: {
      toolPath: string;
      instruction: string;
      failureTraces: string[];
    }): Promise<string>;
  };
  hitlChannel: {
    propose(msg: string): Promise<{ approved: boolean } | null>;
  };
  /**
   * Resolve a tool name to its source-file path. Returns null when the tool
   * has no rewriteable source on disk (e.g. MCP-supplied tools, dynamically
   * registered tools). SET skips any candidate without a resolvable path.
   */
  resolveToolPath?: (toolName: string) => string | null;
}

export interface FindCandidateOptions {
  /** How far back to look. Default: 7 days. */
  days?: number;
  /** Minimum executions in the window before a tool is considered. Default: 20. */
  minExecutions?: number;
}

export interface EvolutionCandidate {
  toolName: string;
  successRate: number;
  failureCount: number;
}

export interface RunOnceResult {
  runId: number;
  toolName: string;
  baselinePath: string;
  candidatePath: string;
}

/** Max failure traces to feed into the rewrite prompt (cost ceiling). */
const MAX_FAILURE_TRACES = 50;

export class SelfEvolver {
  constructor(private readonly deps: SelfEvolverDeps) {}

  async findCandidate(
    opts: FindCandidateOptions = {},
  ): Promise<EvolutionCandidate | null> {
    const days = opts.days ?? 7;
    const minExec = opts.minExecutions ?? 20;
    const criticalList = [...CRITICAL_TOOLS];
    const placeholders = criticalList.map(() => "?").join(",");

    log.tool.debug("self-evolver.findCandidate: entry", {
      days,
      minExecutions: minExec,
      excludedCriticalTools: criticalList.length,
    });

    const row = this.deps.db.rawDb
      .prepare(
        `SELECT tool_name,
                COUNT(*)         AS total,
                SUM(success)     AS successes
            FROM tool_executions
            WHERE created_at > datetime('now', '-' || ? || ' days')
              AND tool_name NOT IN (${placeholders})
            GROUP BY tool_name
            HAVING total >= ?
            ORDER BY (CAST(SUM(success) AS REAL) / COUNT(*)) ASC, total DESC
            LIMIT 1`,
      )
      .get(days, ...criticalList, minExec) as
      | { tool_name: string; total: number; successes: number }
      | undefined;

    if (!row) {
      log.tool.debug("self-evolver.findCandidate: no candidate found", {
        reason: "no non-critical tool meets minExecutions threshold",
        days,
        minExecutions: minExec,
      });
      return null;
    }

    const candidate: EvolutionCandidate = {
      toolName: row.tool_name,
      successRate: row.successes / row.total,
      failureCount: row.total - row.successes,
    };

    log.tool.debug("self-evolver.findCandidate: candidate identified", {
      toolName: candidate.toolName,
      successRate: candidate.successRate,
      failureCount: candidate.failureCount,
      reason: "lowest success rate among qualifying non-critical tools",
    });

    return candidate;
  }

  /**
   * Drives one full SET cycle. Called weekly by ImprovementScheduler.
   *
   * Steps:
   *   1. Concurrency lock — abort if any `tool_evolution_runs.status='running'`.
   *      Hard safety constraint: at most one rewrite in flight at a time.
   *   2. Pick worst non-critical candidate.
   *   3. Resolve source path (skip if unresolvable — MCP-supplied tools etc.).
   *   4. Pull recent failure traces (capped at MAX_FAILURE_TRACES).
   *   5. Propose to user via HITL. Decline (or null) ⇒ abort.
   *   6. Dispatch PatchTool to produce a rewrite at a sibling path.
   *   7. Start a ShadowRunner run so subsequent invocations can record into it.
   *
   * Returns the new run's metadata, or null if any gate aborted the cycle.
   */
  async runOnce(
    shadowRunner: ShadowRunner,
    opts: FindCandidateOptions = {},
  ): Promise<RunOnceResult | null> {
    log.tool.debug("self-evolver.runOnce: entry", { opts });

    const active = this.deps.db.rawDb
      .prepare(
        "SELECT COUNT(*) AS n FROM tool_evolution_runs WHERE status = 'running'",
      )
      .get() as { n: number };
    if (active.n > 0) {
      log.tool.debug("self-evolver.runOnce: aborted — concurrency lock held", {
        reason: "another evolution run is already in progress",
        activeRuns: active.n,
      });
      return null;
    }

    const candidate = await this.findCandidate(opts);
    if (!candidate) {
      log.tool.debug("self-evolver.runOnce: aborted — no candidate found");
      return null;
    }

    const baselinePath = this.deps.resolveToolPath?.(candidate.toolName) ?? null;
    if (!baselinePath) {
      log.tool.debug("self-evolver.runOnce: aborted — tool path unresolvable", {
        reason: "MCP-supplied or dynamically registered tool, no source on disk",
        toolName: candidate.toolName,
      });
      return null;
    }

    log.tool.debug("self-evolver.runOnce: candidate resolved", {
      toolName: candidate.toolName,
      baselinePath,
      successRate: candidate.successRate,
      failureCount: candidate.failureCount,
    });

    const failureRows = this.deps.db.rawDb
      .prepare(
        `SELECT error_message FROM tool_executions
            WHERE tool_name = ? AND success = 0 AND error_message IS NOT NULL
            ORDER BY created_at DESC LIMIT ?`,
      )
      .all(candidate.toolName, MAX_FAILURE_TRACES) as Array<{
      error_message: string;
    }>;
    const failureTraces = failureRows.map((r) => r.error_message);

    log.tool.debug("self-evolver.runOnce: failure traces collected", {
      toolName: candidate.toolName,
      traceCount: failureTraces.length,
      maxTraces: MAX_FAILURE_TRACES,
    });

    const proposal =
      `SET proposes rewriting tool "${candidate.toolName}" — ` +
      `success rate ${(candidate.successRate * 100).toFixed(1)}% over ` +
      `${candidate.failureCount} failures. Approve rewrite?`;
    const verdict = await this.deps.hitlChannel.propose(proposal);
    if (!verdict || !verdict.approved) {
      log.tool.debug("self-evolver.runOnce: aborted — HITL proposal declined", {
        reason: verdict === null ? "no response from HITL channel" : "user declined rewrite",
        toolName: candidate.toolName,
      });
      return null;
    }

    log.tool.debug("self-evolver.runOnce: HITL approved — dispatching PatchTool", {
      toolName: candidate.toolName,
    });

    const candidatePath = await this.deps.patchTool.execute({
      toolPath: baselinePath,
      instruction:
        `Rewrite this tool to handle the failure modes shown in the traces. ` +
        `Preserve the public interface exactly.`,
      failureTraces,
    });

    const runId = shadowRunner.start({
      baselineTool: candidate.toolName,
      candidateTool: `${candidate.toolName}__v2`,
      baselinePath,
      candidatePath,
    });

    log.tool.debug("self-evolver.runOnce: exit", {
      success: true,
      runId,
      toolName: candidate.toolName,
      baselinePath,
      candidatePath,
    });

    return {
      runId,
      toolName: candidate.toolName,
      baselinePath,
      candidatePath,
    };
  }
}
