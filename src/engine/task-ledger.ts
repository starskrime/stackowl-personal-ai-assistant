import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type {
  TaskLedger,
  SubGoal,
  SubGoalStatus,
  TaskComplexity,
  TaskLedgerRevision,
} from "./types.js";

interface LedgerWithMeta extends TaskLedger {
  sessionId: string;
  userId: string;
}

export class TaskLedgerStore {
  constructor(private readonly db: MemoryDatabase) {}

  create(sessionId: string, userId: string, input: Omit<TaskLedger, "id" | "createdAt">): LedgerWithMeta {
    return {
      id: uuidv4(),
      createdAt: Date.now(),
      sessionId,
      userId,
      ...input,
    };
  }

  async save(ledger: TaskLedger): Promise<void> {
    const now = new Date().toISOString();
    const meta = ledger as LedgerWithMeta;
    (this.db as any).db.prepare(`
      INSERT OR REPLACE INTO task_ledgers
        (id, session_id, user_id, goal, sub_goals, expected_output,
         complexity, status, revisions, created_at, updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      ledger.id,
      meta.sessionId ?? "unknown",
      meta.userId ?? "default",
      ledger.goal,
      JSON.stringify(ledger.subGoals),
      ledger.expectedOutput,
      ledger.complexity,
      "active",
      JSON.stringify(ledger.revisions),
      new Date(ledger.createdAt).toISOString(),
      now,
    );
  }

  async load(id: string): Promise<LedgerWithMeta | null> {
    const row = (this.db as any).db.prepare(
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

  private _parse(row: any): LedgerWithMeta {
    return {
      id: row.id,
      sessionId: row.session_id ?? "unknown",
      userId: row.user_id ?? "default",
      goal: row.goal,
      subGoals: JSON.parse(row.sub_goals ?? "[]") as SubGoal[],
      expectedOutput: row.expected_output ?? "",
      complexity: (row.complexity ?? "medium") as TaskComplexity,
      estimatedTurns: 5,
      behavioralConstraints: [],
      approachPatterns: [],
      revisions: JSON.parse(row.revisions ?? "[]") as TaskLedgerRevision[],
      createdAt: new Date(row.created_at).getTime(),
    };
  }
}
