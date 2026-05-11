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
import type { ModelProvider } from "../providers/base.js";

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
    private readonly provider?: ModelProvider,
  ) {}

  async remember(input: RememberInput): Promise<MemoryId> {
    const id = `mem_${randomUUID().replace(/-/g, "").slice(0, 16)}`;
    const now = new Date().toISOString();

    const rawLen = input.content.length;
    log.memory.debug("unified.remember: entry", {
      id,
      kind: input.kind,
      domain: input.domain,
      scope: input.scope ?? "user",
      source: input.source ?? "inferred",
      contentLen: rawLen,
    });

    const content = await this.compressContent(input.content, input.kind, input.domain);

    this.repo.insertBatch([
      {
        id,
        kind: input.kind,
        content,
        importance: input.importance ?? input.confidence ?? 0.8,
        goal_id: input.goal_id,
        valid_at: now,
      },
    ]);

    // Populate v30 columns (domain, scope, source, confidence, evidence_ids)
    // that MemoryInsert doesn't cover yet.
    if (this.db) {
      try {
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
      } catch (err) {
        log.memory.error("unified.remember: v30 UPDATE failed — partial write, metadata not persisted", err, { id });
      }
    }

    log.memory.debug("unified.remember: exit", {
      id,
      kind: input.kind,
      domain: input.domain,
      rawLen,
      storedLen: content.length,
      compressed: content !== input.content,
    });
    return id;
  }

  private async compressContent(
    raw: string,
    kind: MemoryKind,
    domain: MemoryDomain,
  ): Promise<string> {
    if (!this.provider || raw.length < 80) {
      // Skip compression for very short content — already concise
      return raw;
    }

    const prompt =
      `You are a memory compressor. Convert the following ${kind} memory (domain: ${domain}) ` +
      `into the shortest possible form that preserves all meaning, intent, and actionable detail. ` +
      `Use telegraphic style. No filler words. No "the user" framing — write the fact directly. ` +
      `Return ONLY the compressed memory, nothing else.\n\n${raw}`;

    try {
      const response = await this.provider.chat(
        [{ role: "user", content: prompt }],
        undefined,
        { maxTokens: 120, temperature: 0 },
      );
      const compressed = response.content.trim();
      if (!compressed || compressed.length >= raw.length) {
        // Compression made it longer or empty — keep raw
        return raw;
      }
      log.memory.debug("unified.compressContent: compressed", {
        rawLen: raw.length,
        compressedLen: compressed.length,
        ratio: (compressed.length / raw.length).toFixed(2),
      });
      return compressed;
    } catch (err) {
      log.memory.warn("unified.compressContent: LLM call failed — using raw content", err);
      return raw;
    }
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

    // Empty query → relevance scores 0, ranking falls back to recency + importance
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
    const result = this.repoRecordToMemoryRecord(record);
    log.memory.debug("unified.get: exit", { id });
    return result;
  }

  forget(id: MemoryId, reason = "user-requested"): void {
    log.memory.debug("unified.forget: entry", { id, reason });
    this.repo.invalidate(id, { reason, invalidatedBy: "unified-memory" });
    log.memory.debug("unified.forget: exit", { id });
  }

  reinforce(id: MemoryId): void {
    log.memory.debug("unified.reinforce: entry", { id });
    try {
      this.repo.recordAccess(id);
    } catch (err) {
      log.memory.warn("unified.reinforce: recordAccess failed (id may not exist)", err, { id });
      return;
    }
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
    log.memory.debug("unified.stats: entry");
    const s = this.repo.stats();
    const result: UnifiedMemoryStats = {
      total: s.total,
      byKind: s.byKind,
      byDomain: {}, // requires v30 domain column — placeholder until Phase 2 migration
      invalidated: s.invalidated,
      pinned: 0, // requires v30 pinned column — placeholder until Phase 2 migration
      avgImportance: s.avgImportance,
    };
    log.memory.debug("unified.stats: exit", { total: s.total });
    return result;
  }

  why(id: MemoryId): MemoryProvenance | null {
    log.memory.debug("unified.why: entry", { id });
    const record = this.repo.getById(id);
    if (!record) {
      log.memory.debug("unified.why: not found", { id });
      return null;
    }
    const provenance: MemoryProvenance = {
      id,
      source: null, // populated from v30 source column in later migration phase
      evidence_ids: [], // populated from v30 evidence_ids column in later migration phase
      created_at: record.created_at,
    };
    log.memory.debug("unified.why: exit", { id });
    return provenance;
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
