import { v4 as uuidv4 } from "uuid";
import type { MemoryDatabase } from "../memory/db.js";
import type { DegradationTier } from "./types.js";

interface JournalEntry {
  sessionId: string;
  owlName: string;
  userId: string;
  userMessage: string;
  totalTurns: number;
  toolsUsed: string[];
  outcome: "success" | "failure" | "partial";
  reward: number;
  qualityScore: number;
  qualityFlags: string[];
  taskCategory: string;
  taskComplexity: string;
  degradationTier: DegradationTier;
  recoveryActions: string[];
  followUpSentiment?: "positive" | "correction" | "neutral";
}

interface StoredEntry extends JournalEntry {
  id: string;
  createdAt: string;
}

export class OutcomeJournal {
  constructor(private readonly db: MemoryDatabase) {}

  async record(entry: JournalEntry): Promise<string> {
    const id = uuidv4();
    const now = new Date().toISOString();
    (this.db as any).db.prepare(`
      INSERT INTO trajectories (
        id, session_id, owl_name, user_id, user_message,
        total_turns, tools_used, outcome, reward,
        quality_score, quality_flags, task_category, task_complexity,
        degradation_tier, recovery_actions, created_at, completed_at
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    `).run(
      id, entry.sessionId, entry.owlName, entry.userId, entry.userMessage,
      entry.totalTurns, JSON.stringify(entry.toolsUsed), entry.outcome, entry.reward,
      entry.qualityScore, JSON.stringify(entry.qualityFlags), entry.taskCategory,
      entry.taskComplexity, entry.degradationTier, JSON.stringify(entry.recoveryActions),
      now, now,
    );
    return id;
  }

  async updateSentiment(id: string, sentiment: "positive" | "correction" | "neutral"): Promise<void> {
    (this.db as any).db.prepare(`
      UPDATE trajectories SET follow_up_sentiment = ?, follow_up_updated_at = ? WHERE id = ?
    `).run(sentiment, new Date().toISOString(), id);
  }

  async getRecent(limit: number): Promise<StoredEntry[]> {
    const rows = (this.db as any).db.prepare(`
      SELECT * FROM trajectories WHERE quality_score IS NOT NULL ORDER BY created_at DESC LIMIT ?
    `).all(limit) as any[];
    return rows.map(this._parse);
  }

  async getFailures({ minEntries }: { minEntries: number }): Promise<StoredEntry[]> {
    const rows = (this.db as any).db.prepare(`
      SELECT * FROM trajectories WHERE quality_score IS NOT NULL AND quality_score < 0.5 ORDER BY created_at DESC LIMIT 50
    `).all() as any[];
    if (rows.length < minEntries) return [];
    return rows.map(this._parse);
  }

  private _parse(row: any): StoredEntry {
    return {
      id: row.id, sessionId: row.session_id, owlName: row.owl_name,
      userId: row.user_id ?? "default", userMessage: row.user_message,
      totalTurns: row.total_turns, toolsUsed: JSON.parse(row.tools_used ?? "[]"),
      outcome: row.outcome, reward: row.reward, qualityScore: row.quality_score,
      qualityFlags: JSON.parse(row.quality_flags ?? "[]"),
      taskCategory: row.task_category ?? "general",
      taskComplexity: row.task_complexity ?? "medium",
      degradationTier: (row.degradation_tier ?? 1) as DegradationTier,
      recoveryActions: JSON.parse(row.recovery_actions ?? "[]"),
      followUpSentiment: row.follow_up_sentiment ?? undefined,
      createdAt: row.created_at,
    };
  }
}
