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
}
