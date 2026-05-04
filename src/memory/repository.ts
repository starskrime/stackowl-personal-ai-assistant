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

import { randomUUID } from "node:crypto";
import type Database from "better-sqlite3";
import type { GatewayEventBus } from "../gateway/event-bus.js";

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
  /** Optional override for bitemporal valid_at. Defaults to now. Used by the legacy merge to preserve original timestamps. */
  valid_at?: string;
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

export interface MemoryInvalidation {
  id: string;
  memory_id: string;
  reason: string;
  invalidated_by: string;
  invalidated_at: string;
}

export interface MemoryContradiction {
  id: string;
  memory_id: string;
  contradicts_id: string;
  detected_at: string;
}

export type EmbedderFn = (query: string) => Float32Array | null;

export class MemoryRepository {
  constructor(
    private readonly db: Database.Database,
    private readonly bus?: GatewayEventBus,
    private readonly embedder?: EmbedderFn,
  ) {}

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
    for (const r of records) {
      if (r.importance < 0 || r.importance > 1) {
        throw new Error(`importance must be in [0,1], got ${r.importance} for id=${r.id}`);
      }
    }
    const stmt = this.db.prepare(`
      INSERT INTO memories
        (id, kind, content, embedding, importance, goal_id, subgoal_id, verdict,
         source_turn_id, source_channel, valid_at, created_at, updated_at)
      VALUES
        (@id, @kind, @content, @embedding, @importance, @goal_id, @subgoal_id, @verdict,
         @source_turn_id, @source_channel, @valid_at, @now, @now)
      ON CONFLICT(id) DO UPDATE SET
        content = excluded.content,
        embedding = excluded.embedding,
        importance = excluded.importance,
        verdict = excluded.verdict,
        updated_at = excluded.updated_at
    `);
    const insertMany = this.db.transaction((rows: MemoryInsert[]) => {
      const now = new Date().toISOString();
      for (const r of rows) {
        stmt.run({
          id: r.id,
          kind: r.kind,
          content: r.content,
          embedding: r.embedding ? this.embeddingToBuffer(r.embedding) : null,
          importance: r.importance,
          goal_id: r.goal_id ?? null,
          subgoal_id: r.subgoal_id ?? null,
          verdict: r.verdict ?? null,
          source_turn_id: r.source_turn_id ?? null,
          source_channel: r.source_channel ?? null,
          valid_at: r.valid_at ?? now,
          now,
        });
      }
    });
    insertMany(records);

    if (this.bus) {
      for (const r of records) {
        this.bus.emit({
          type: "memory:written",
          id: r.id,
          kind: r.kind,
          goal_id: r.goal_id ?? null,
          importance: r.importance,
        });
      }
    }
  }

  async searchSemanticByEmbedding(
    queryEmbedding: Float32Array,
    opts: MemorySearchOptions = {},
  ): Promise<MemoryRecord[]> {
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
    const now = Date.now();

    const scored = rows.map((row) => {
      const record = this.rowToRecord(row);
      const recencyMs = now - new Date(record.valid_at).getTime();
      const recency = Math.exp(-recencyMs / (1000 * 60 * 60 * 24 * 7));
      const relevance = record.embedding ? this.cosine(queryEmbedding, record.embedding) : 0;
      const score = 0.3 * recency + 0.3 * record.importance + 0.4 * relevance;
      return { record, score };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, topK).map((s) => s.record);
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
        .run(`inv_${randomUUID()}`, id, opts.reason, opts.invalidatedBy, now);
      if (opts.contradicts) {
        const cstmt = this.db.prepare(
          `INSERT INTO memory_contradictions (id, memory_id, contradicts_id, detected_at)
           VALUES (?, ?, ?, ?)`,
        );
        for (const cId of opts.contradicts) {
          cstmt.run(`con_${randomUUID()}`, id, cId, now);
        }
      }
    });
    tx();

    this.bus?.emit({
      type: "memory:invalidated",
      id,
      reason: opts.reason,
      invalidated_by: opts.invalidatedBy,
    });
  }

  getById(id: string): MemoryRecord | null {
    const row = this.db.prepare(`SELECT * FROM memories WHERE id = ?`).get(id) as
      | Record<string, unknown>
      | undefined;
    return row ? this.rowToRecord(row) : null;
  }

  history(id: string): {
    record: MemoryRecord | null;
    invalidations: MemoryInvalidation[];
    contradictions: MemoryContradiction[];
  } {
    const record = this.getById(id);
    const invalidations = this.db
      .prepare(
        `SELECT * FROM memory_invalidations WHERE memory_id = ? ORDER BY invalidated_at DESC`,
      )
      .all(id) as MemoryInvalidation[];
    const contradictions = this.db
      .prepare(
        `SELECT * FROM memory_contradictions WHERE memory_id = ? OR contradicts_id = ? ORDER BY detected_at DESC`,
      )
      .all(id, id) as MemoryContradiction[];
    return { record, invalidations, contradictions };
  }

  recordAccess(id: string): void {
    const exists = this.db.prepare(`SELECT 1 FROM memories WHERE id = ?`).get(id);
    if (!exists) {
      throw new Error(`recordAccess: memory id=${id} does not exist`);
    }
    const now = new Date().toISOString();
    const tx = this.db.transaction(() => {
      this.db
        .prepare(
          `UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?`,
        )
        .run(now, id);
      this.db
        .prepare(`INSERT INTO memory_access_log (id, memory_id, accessed_at) VALUES (?, ?, ?)`)
        .run(`acc_${randomUUID()}`, id, now);
    });
    tx();
  }

  stats(): MemoryStats {
    const row = this.db
      .prepare(
        `SELECT
           COUNT(*) AS total,
           SUM(CASE WHEN invalid_at IS NOT NULL THEN 1 ELSE 0 END) AS invalidated,
           AVG(importance) AS avg_importance
         FROM memories`,
      )
      .get() as { total: number; invalidated: number | null; avg_importance: number | null };

    const kindRows = this.db
      .prepare(`SELECT kind, COUNT(*) AS c FROM memories GROUP BY kind`)
      .all() as Array<{ kind: MemoryKind; c: number }>;
    const byKind: Record<MemoryKind, number> = {
      semantic: 0,
      episodic: 0,
      working: 0,
      procedural: 0,
      reflexive: 0,
    };
    for (const r of kindRows) byKind[r.kind] = r.c;

    return {
      total: row.total ?? 0,
      byKind,
      invalidated: row.invalidated ?? 0,
      avgImportance: row.avg_importance ?? 0,
    };
  }

  private cosine(a: Float32Array, b: Float32Array): number {
    if (a.length !== b.length) {
      throw new Error(`cosine: dim mismatch (a=${a.length}, b=${b.length})`);
    }
    let dot = 0;
    let na = 0;
    let nb = 0;
    for (let i = 0; i < a.length; i++) {
      dot += a[i] * b[i];
      na += a[i] * a[i];
      nb += b[i] * b[i];
    }
    if (na === 0 || nb === 0) return 0;
    return dot / (Math.sqrt(na) * Math.sqrt(nb));
  }

  private embedQuery(query: string): Float32Array | null {
    if (!query) return null;
    if (!this.embedder) return null;
    return this.embedder(query);
  }

  /**
   * Float32Array → Buffer with explicit byteOffset/byteLength.
   * Critical: Node Buffer pools small typed-array allocations, so `.buffer` may
   * be a shared 8KB pool. Without offset/length we'd persist garbage.
   */
  private embeddingToBuffer(embedding: Float32Array): Buffer {
    return Buffer.from(embedding.buffer, embedding.byteOffset, embedding.byteLength);
  }

  /**
   * Buffer → Float32Array with explicit byteOffset/byteLength. Same reason as above.
   */
  private bufferToEmbedding(buf: Buffer): Float32Array {
    return new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
  }

  private rowToRecord(row: Record<string, unknown>): MemoryRecord {
    return {
      id: row.id as string,
      kind: row.kind as MemoryKind,
      content: row.content as string,
      embedding: row.embedding ? this.bufferToEmbedding(row.embedding as Buffer) : null,
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
