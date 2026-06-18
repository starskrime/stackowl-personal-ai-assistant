import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type {
  TaskLedger,
  SubGoal,
  SubGoalStatus,
  TaskComplexity,
  TaskLedgerRevision,
} from "./types.js";

export interface PersistSubgoalArgs {
  id: string;
  sessionId: string;
  userId: string;
  taskId: string;
  subgoalIndex: number;
  subgoalText: string;
  stateJson: string;
  status: string;
  attemptCount: number;
}

export interface LedgerWithMeta extends TaskLedger {
  sessionId: string;
  userId: string;
  status: string;
}

export class TaskLedgerStore {
  constructor(private readonly db: MemoryDatabase) {}

  create(sessionId: string, userId: string, input: Omit<TaskLedger, "id" | "createdAt">): LedgerWithMeta {
    return {
      id: uuidv4(),
      createdAt: Date.now(),
      sessionId,
      userId,
      status: "active",
      ...input,
    };
  }

  async save(ledger: LedgerWithMeta): Promise<void> {
    const now = new Date().toISOString();
    const extras = JSON.stringify({
      estimatedTurns: ledger.estimatedTurns,
      behavioralConstraints: ledger.behavioralConstraints,
      approachPatterns: ledger.approachPatterns,
      parliamentContext: ledger.parliamentContext,
      reflexionContext: ledger.reflexionContext,
    });
    this.db.rawDb.prepare(`
      INSERT OR REPLACE INTO task_ledgers
        (id, session_id, user_id, goal, sub_goals, expected_output,
         complexity, status, revisions, created_at, updated_at, extras)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      ledger.id,
      ledger.sessionId ?? "unknown",
      ledger.userId ?? "default",
      ledger.goal,
      JSON.stringify(ledger.subGoals),
      ledger.expectedOutput,
      ledger.complexity,
      ledger.status ?? "active",
      JSON.stringify(ledger.revisions),
      new Date(ledger.createdAt).toISOString(),
      now,
      extras,
    );
  }

  async load(id: string): Promise<LedgerWithMeta | null> {
    const row = this.db.rawDb.prepare(
      "SELECT * FROM task_ledgers WHERE id = ?"
    ).get(id) as any;
    if (!row) return null;
    return this._parse(row);
  }

  async updateSubGoal(
    ledgerId: string,
    subGoalId: string,
    status: SubGoalStatus,
    result?: string,
  ): Promise<void> {
    const ledger = await this.load(ledgerId);
    if (!ledger) return;
    ledger.subGoals = ledger.subGoals.map((sg) =>
      sg.id === subGoalId ? { ...sg, status, result: result ?? sg.result } : sg
    );
    await this.save(ledger);
  }

  async addRevision(ledgerId: string, reason: string, previousGoal: string): Promise<void> {
    const ledger = await this.load(ledgerId);
    if (!ledger) return;
    const revision: TaskLedgerRevision = { at: Date.now(), reason, previousGoal };
    ledger.revisions = [...ledger.revisions, revision];
    await this.save(ledger);
  }

  async persistSubgoal(args: PersistSubgoalArgs): Promise<void> {
    this.db.rawDb.prepare(`
      INSERT INTO owl_task_ledger
        (id, session_id, user_id, task_id, subgoal_index, subgoal_text, state_json, status, attempt_count, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        subgoal_index = excluded.subgoal_index,
        subgoal_text  = excluded.subgoal_text,
        state_json    = excluded.state_json,
        status        = excluded.status,
        attempt_count = excluded.attempt_count
    `).run(
      args.id, args.sessionId, args.userId, args.taskId,
      args.subgoalIndex, args.subgoalText, args.stateJson,
      args.status, args.attemptCount, new Date().toISOString(),
    );
  }

  async loadIncomplete(userId: string): Promise<PersistSubgoalArgs | null> {
    const row = this.db.rawDb.prepare(`
      SELECT * FROM owl_task_ledger
      WHERE user_id = ? AND status = 'in_progress'
      ORDER BY created_at DESC
      LIMIT 1
    `).get(userId) as any;
    if (!row) return null;
    return {
      id: row.id,
      sessionId: row.session_id,
      userId: row.user_id,
      taskId: row.task_id,
      subgoalIndex: row.subgoal_index,
      subgoalText: row.subgoal_text,
      stateJson: row.state_json,
      status: row.status,
      attemptCount: row.attempt_count,
    };
  }

  async markComplete(id: string): Promise<void> {
    this.db.rawDb.prepare(
      "UPDATE owl_task_ledger SET status = 'complete', resumed_at = ? WHERE id = ?",
    ).run(new Date().toISOString(), id);
  }

  private _parse(row: any): LedgerWithMeta {
    const extras = JSON.parse(row.extras ?? "{}");
    return {
      id: row.id,
      sessionId: row.session_id ?? "unknown",
      userId: row.user_id ?? "default",
      status: row.status ?? "active",
      goal: row.goal,
      subGoals: JSON.parse(row.sub_goals ?? "[]") as SubGoal[],
      expectedOutput: row.expected_output ?? "",
      complexity: (row.complexity ?? "medium") as TaskComplexity,
      estimatedTurns: extras.estimatedTurns ?? 5,
      behavioralConstraints: extras.behavioralConstraints ?? [],
      approachPatterns: extras.approachPatterns ?? [],
      parliamentContext: extras.parliamentContext,
      reflexionContext: extras.reflexionContext,
      revisions: JSON.parse(row.revisions ?? "[]") as TaskLedgerRevision[],
      createdAt: new Date(row.created_at).getTime(),
    };
  }
}
