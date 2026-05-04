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

  async search(_query: string, _opts: MemorySearchOptions = {}): Promise<MemoryRecord[]> {
    void this.db;
    throw new Error("not implemented");
  }

  insertBatch(_records: MemoryInsert[]): void {
    throw new Error("not implemented");
  }

  invalidate(_id: string, _opts: InvalidateOptions): void {
    throw new Error("not implemented");
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
}
