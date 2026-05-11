/**
 * StackOwl — UnifiedMemory Facade
 *
 * Single interface over all memory stores. Phase 1: thin wrapper over
 * MemoryRepository that adds domain/scope/source metadata via v30 columns.
 * Future phases will consolidate FactStore, EpisodicMemory, etc. into this facade.
 *
 * Callers write once here; the facade handles routing. No dual-writes.
 */

import { randomUUID } from "node:crypto";
import type Database from "better-sqlite3";
import { log } from "../logger.js";
import type { MemoryRepository, MemoryRecord as RepoRecord } from "./repository.js";

// ---------------------------------------------------------------------------
// Exported types
// ---------------------------------------------------------------------------

export type MemoryKind = "semantic" | "episodic" | "procedural" | "working" | "reflexive";

export type MemoryDomain =
  | "fact"
  | "preference"
  | "learning"
  | "skill"
  | "habit"
  | "project_detail"
  | "personal"
  | "context"
  | "goal"
  | "relationship"
  | "decision"
  | "anti_pattern";

export type MemoryScope = "global" | "user" | "owl";

export type MemorySource = "user_direct" | "inferred" | "tool_output" | "reflection";

export type MemoryId = string;

export interface RememberInput {
  content: string;
  kind: MemoryKind;
  domain: MemoryDomain;
  scope?: MemoryScope;    // default: 'user'
  source?: MemorySource;  // default: 'inferred'
  confidence?: number;    // 0..1, default: 0.8
  importance?: number;    // 0..1, default: 0.8
  evidence?: MemoryId[];
  ttlDays?: number;
  goal_id?: string;
  userId?: string;
  owlName?: string;
}

export interface RecallQuery {
  query: string;
  kinds?: MemoryKind[];
  domains?: MemoryDomain[];
  topK?: number;
  includeInvalid?: boolean;
}

export interface ListFilter {
  kinds?: MemoryKind[];
  domains?: MemoryDomain[];
  topK?: number;
  includeInvalid?: boolean;
}

export interface MemoryHit {
  id: MemoryId;
  kind: MemoryKind;
  domain: MemoryDomain | null;
  content: string;
  importance: number;
  score: number;
  source: string | null;
  created_at: string;
  valid_at: string;
  invalid_at: string | null;
}

export interface MemoryRecord extends MemoryHit {
  embedding: Float32Array | null;
  goal_id: string | null;
  access_count: number;
  pinned: boolean;
  suppressed: boolean;
  superseded_by: MemoryId | null;
  evidence_ids: MemoryId[];
}

export interface MemoryProvenance {
  id: MemoryId;
  source: string | null;
  origin_session?: string;
  evidence_ids: MemoryId[];
  created_at: string;
}

export interface UnifiedMemoryStats {
  total: number;
  byKind: Record<MemoryKind, number>;
  byDomain: Record<string, number>;
  invalidated: number;
  pinned: number;
  avgImportance: number;
}

// ---------------------------------------------------------------------------
// UnifiedMemory facade
// ---------------------------------------------------------------------------

export class UnifiedMemory {
  constructor(
    private readonly repo: MemoryRepository,
    private readonly db?: Database.Database,
  ) {}

  async remember(input: RememberInput): Promise<MemoryId> {
    const id = `mem_${randomUUID().replace(/-/g, "").slice(0, 16)}`;
    const now = new Date().toISOString();

    log.memory.debug("unified.remember: entry", {
      id,
      kind: input.kind,
      domain: input.domain,
      scope: input.scope ?? "user",
      source: input.source ?? "inferred",
      contentLen: input.content.length,
    });

    this.repo.insertBatch([
      {
        id,
        kind: input.kind,
        content: input.content,
        importance: input.importance ?? input.confidence ?? 0.8,
        goal_id: input.goal_id,
        valid_at: now,
      },
    ]);

    // Populate v30 columns (domain, scope, source, confidence, evidence_ids)
    // that MemoryInsert doesn't cover yet.
    if (this.db) {
      this.db
        .prepare(
          `UPDATE memories SET domain = ?, scope = ?, source = ?, confidence = ?, evidence_ids = ? WHERE id = ?`,
        )
        .run(
          input.domain,
          input.scope ?? "user",
          input.source ?? "inferred",
          input.confidence ?? 0.8,
          JSON.stringify(input.evidence ?? []),
          id,
        );
    }

    log.memory.debug("unified.remember: exit", { id, kind: input.kind, domain: input.domain });
    return id;
  }

  async recall(query: RecallQuery): Promise<MemoryHit[]> {
    log.memory.debug("unified.recall: entry", {
      queryLen: query.query.length,
      kinds: query.kinds,
      topK: query.topK ?? 10,
    });

    const records = await this.repo.search(query.query, {
      kinds: query.kinds,
      topK: query.topK ?? 10,
      includeInvalid: query.includeInvalid,
    });

    const hits = records.map((r) => this.repoRecordToHit(r));

    log.memory.debug("unified.recall: exit", { count: hits.length });
    return hits;
  }

  async list(filter: ListFilter = {}): Promise<MemoryHit[]> {
    log.memory.debug("unified.list: entry", {
      kinds: filter.kinds,
      topK: filter.topK ?? 50,
    });

    const records = await this.repo.search("", {
      kinds: filter.kinds,
      topK: filter.topK ?? 50,
      includeInvalid: filter.includeInvalid,
    });

    const hits = records.map((r) => this.repoRecordToHit(r));

    log.memory.debug("unified.list: exit", { count: hits.length });
    return hits;
  }

  get(id: MemoryId): MemoryRecord | null {
    log.memory.debug("unified.get: entry", { id });
    const record = this.repo.getById(id);
    if (!record) {
      log.memory.debug("unified.get: not found", { id });
      return null;
    }
    return this.repoRecordToMemoryRecord(record);
  }

  forget(id: MemoryId, reason = "user-requested"): void {
    log.memory.debug("unified.forget: entry", { id, reason });
    this.repo.invalidate(id, { reason, invalidatedBy: "unified-memory" });
    log.memory.debug("unified.forget: exit", { id });
  }

  reinforce(id: MemoryId): void {
    log.memory.debug("unified.reinforce: entry", { id });
    this.repo.recordAccess(id);
    log.memory.debug("unified.reinforce: exit", { id });
  }

  pin(id: MemoryId): void {
    if (!this.db) return;
    log.memory.debug("unified.pin: entry", { id });
    this.db
      .prepare(`UPDATE memories SET pinned = 1, updated_at = ? WHERE id = ?`)
      .run(new Date().toISOString(), id);
    log.memory.debug("unified.pin: exit", { id });
  }

  stats(): UnifiedMemoryStats {
    const s = this.repo.stats();
    // byDomain requires v30 domain column — return {} until that migration lands
    return {
      total: s.total,
      byKind: s.byKind as Record<MemoryKind, number>,
      byDomain: {},
      invalidated: s.invalidated,
      pinned: 0,
      avgImportance: s.avgImportance,
    };
  }

  why(id: MemoryId): MemoryProvenance | null {
    log.memory.debug("unified.why: entry", { id });
    const record = this.repo.getById(id);
    if (!record) {
      log.memory.debug("unified.why: not found", { id });
      return null;
    }
    return {
      id,
      source: null, // populated from domain column in later migration phase
      evidence_ids: [],
      created_at: record.created_at,
    };
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  private repoRecordToMemoryRecord(r: RepoRecord): MemoryRecord {
    return {
      id: r.id,
      kind: r.kind as MemoryKind,
      domain: null, // populated from v30 columns in later phase
      content: r.content,
      importance: r.importance,
      score: r.importance,
      source: null,
      created_at: r.created_at,
      valid_at: r.valid_at,
      invalid_at: r.invalid_at,
      embedding: r.embedding,
      goal_id: r.goal_id,
      access_count: r.access_count,
      pinned: false,
      suppressed: false,
      superseded_by: null,
      evidence_ids: [],
    };
  }

  private repoRecordToHit(r: RepoRecord): MemoryHit {
    return {
      id: r.id,
      kind: r.kind as MemoryKind,
      domain: null,
      content: r.content,
      importance: r.importance,
      score: r.importance,
      source: null,
      created_at: r.created_at,
      valid_at: r.valid_at,
      invalid_at: r.invalid_at,
    };
  }
}
