/**
 * StackOwl — Element 15 — MemoryRepository
 *
 * Typed read/write surface over the v25 memory tables. Sole consumer of
 * `memories` / `memory_invalidations` / `memory_contradictions` /
 * `memory_access_log`. Other code paths must never `prepare(...)` against
 * those tables — they go through this surface.
 *
 * Task 1 ships the skeleton + types. Behavior comes in Tasks 2, 3, 8, 9, 10.
 */

import type Database from "better-sqlite3";

export type MemoryKind = "semantic" | "episodic" | "working" | "procedural" | "reflexive";

export type MemoryVerdict = "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";

export interface MemoryRecord {
  id: string;
  kind: MemoryKind;
  content: string;
  embedding: Float32Array | null;
  importance: number;
  goal_id: string | null;
  subgoal_id: string | null;
  verdict: MemoryVerdict | null;
  source_turn_id: string | null;
  source_channel: string | null;
  valid_at: string;
  invalid_at: string | null;
  created_at: string;
  updated_at: string;
  access_count: number;
  last_accessed_at: string | null;
}

export interface MemorySearchOptions {
  kinds?: MemoryKind[];
  topK?: number;
  minImportance?: number;
  goalId?: string;
  includeInvalid?: boolean;
}

export interface MemoryInsert {
  id: string;
  kind: MemoryKind;
  content: string;
  embedding?: Float32Array;
  importance: number;
  goal_id?: string;
  subgoal_id?: string;
  verdict?: MemoryVerdict;
  source_turn_id?: string;
  source_channel?: string;
}

export interface InvalidateOptions {
  reason: string;
  invalidatedBy: string;
  contradicts?: string[];
}

export interface MemoryStats {
  total: number;
  byKind: Record<MemoryKind, number>;
  invalidated: number;
  avgImportance: number;
}

export class MemoryRepository {
  constructor(private readonly db: Database.Database) {}

  async search(query: string, opts: MemorySearchOptions = {}): Promise<MemoryRecord[]> {
    const { kinds, topK = 50, minImportance, includeInvalid = false, goalId } = opts;
    const where: string[] = [];
    const params: Record<string, unknown> = {};

    if (!includeInvalid) where.push("invalid_at IS NULL");
    if (kinds && kinds.length > 0) {
      where.push(`kind IN (${kinds.map((_, i) => `@k${i}`).join(",")})`);
      kinds.forEach((k, i) => (params[`k${i}`] = k));
    }
    if (typeof minImportance === "number") {
      where.push("importance >= @minImportance");
      params.minImportance = minImportance;
    }
    if (goalId) {
      where.push("goal_id = @goalId");
      params.goalId = goalId;
    }

    const sql = `SELECT * FROM memories ${where.length ? "WHERE " + where.join(" AND ") : ""}`;
    const rows = this.db.prepare(sql).all(params) as Array<Record<string, unknown>>;

    const queryEmbedding = this.embedQuery(query);
    const now = Date.now();

    const scored = rows.map((row) => {
      const record = this.rowToRecord(row);
      const recencyMs = now - new Date(record.valid_at).getTime();
      const recency = Math.exp(-recencyMs / (1000 * 60 * 60 * 24 * 7));
      const relevance =
        queryEmbedding && record.embedding ? this.cosine(queryEmbedding, record.embedding) : 0;
      const score = 0.3 * recency + 0.3 * record.importance + 0.4 * relevance;
      return { record, score };
    });

    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, topK).map((s) => s.record);
  }

  insertBatch(records: MemoryInsert[]): void {
    if (records.length === 0) return;
    const stmt = this.db.prepare(`
      INSERT INTO memories
        (id, kind, content, embedding, importance, goal_id, subgoal_id, verdict,
         source_turn_id, source_channel, valid_at, created_at, updated_at)
      VALUES
        (@id, @kind, @content, @embedding, @importance, @goal_id, @subgoal_id, @verdict,
         @source_turn_id, @source_channel, @now, @now, @now)
    `);
    const insertMany = this.db.transaction((rows: MemoryInsert[]) => {
      const now = new Date().toISOString();
      for (const r of rows) {
        stmt.run({
          id: r.id,
          kind: r.kind,
          content: r.content,
          embedding: r.embedding ? Buffer.from(r.embedding.buffer) : null,
          importance: r.importance,
          goal_id: r.goal_id ?? null,
          subgoal_id: r.subgoal_id ?? null,
          verdict: r.verdict ?? null,
          source_turn_id: r.source_turn_id ?? null,
          source_channel: r.source_channel ?? null,
          now,
        });
      }
    });
    insertMany(records);
  }

  invalidate(id: string, opts: InvalidateOptions): void {
    const now = new Date().toISOString();
    const tx = this.db.transaction(() => {
      this.db
        .prepare(`UPDATE memories SET invalid_at = ?, updated_at = ? WHERE id = ?`)
        .run(now, now, id);
      this.db
        .prepare(
          `INSERT INTO memory_invalidations (id, memory_id, reason, invalidated_by, invalidated_at)
           VALUES (?, ?, ?, ?, ?)`,
        )
        .run(`inv_${id}_${Date.now()}`, id, opts.reason, opts.invalidatedBy, now);
      if (opts.contradicts) {
        const cstmt = this.db.prepare(
          `INSERT INTO memory_contradictions (id, memory_id, contradicts_id, detected_at)
           VALUES (?, ?, ?, ?)`,
        );
        for (const cId of opts.contradicts) {
          cstmt.run(`con_${id}_${cId}`, id, cId, now);
        }
      }
    });
    tx();
  }

  getById(_id: string): MemoryRecord | null {
    throw new Error("not implemented");
  }

  history(_id: string): {
    record: MemoryRecord | null;
    invalidations: unknown[];
    contradictions: unknown[];
  } {
    throw new Error("not implemented");
  }

  recordAccess(_id: string): void {
    throw new Error("not implemented");
  }

  stats(): MemoryStats {
    throw new Error("not implemented");
  }

  private cosine(a: Float32Array, b: Float32Array): number {
    let dot = 0;
    let na = 0;
    let nb = 0;
    const len = Math.min(a.length, b.length);
    for (let i = 0; i < len; i++) {
      dot += a[i] * b[i];
      na += a[i] * a[i];
      nb += b[i] * b[i];
    }
    if (na === 0 || nb === 0) return 0;
    return dot / (Math.sqrt(na) * Math.sqrt(nb));
  }

  private embedQuery(_query: string): Float32Array | null {
    return null;
  }

  private rowToRecord(row: Record<string, unknown>): MemoryRecord {
    return {
      id: row.id as string,
      kind: row.kind as MemoryKind,
      content: row.content as string,
      embedding: row.embedding ? new Float32Array((row.embedding as Buffer).buffer) : null,
      importance: row.importance as number,
      goal_id: (row.goal_id as string) ?? null,
      subgoal_id: (row.subgoal_id as string) ?? null,
      verdict: (row.verdict as MemoryRecord["verdict"]) ?? null,
      source_turn_id: (row.source_turn_id as string) ?? null,
      source_channel: (row.source_channel as string) ?? null,
      valid_at: row.valid_at as string,
      invalid_at: (row.invalid_at as string) ?? null,
      created_at: row.created_at as string,
      updated_at: row.updated_at as string,
      access_count: (row.access_count as number) ?? 0,
      last_accessed_at: (row.last_accessed_at as string) ?? null,
    };
  }
}
