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

    if (!row) return null;
    return {
      toolName: row.tool_name,
      successRate: row.successes / row.total,
      failureCount: row.total - row.successes,
    };
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
    const active = this.deps.db.rawDb
      .prepare(
        "SELECT COUNT(*) AS n FROM tool_evolution_runs WHERE status = 'running'",
      )
      .get() as { n: number };
    if (active.n > 0) return null;

    const candidate = await this.findCandidate(opts);
    if (!candidate) return null;

    const baselinePath = this.deps.resolveToolPath?.(candidate.toolName) ?? null;
    if (!baselinePath) return null;

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

    const proposal =
      `SET proposes rewriting tool "${candidate.toolName}" — ` +
      `success rate ${(candidate.successRate * 100).toFixed(1)}% over ` +
      `${candidate.failureCount} failures. Approve rewrite?`;
    const verdict = await this.deps.hitlChannel.propose(proposal);
    if (!verdict || !verdict.approved) return null;

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

    return {
      runId,
      toolName: candidate.toolName,
      baselinePath,
      candidatePath,
    };
  }
}
