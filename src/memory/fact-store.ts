/**
 * StackOwl — Fact Store
 *
 * Layer 3 of the memory hierarchy: structured fact memory.
 * Mem0-inspired fact storage with LLM extraction, conflict resolution,
 * and time-to-live expiration.
 *
 * Facts are extracted from conversations after each session (or every N messages)
 * and stored as structured records with:
 *   - Category (preference, project_detail, personal, skill, goal)
 *   - Confidence score (from LLM extraction or user confirmation)
 *   - Source (explicit: stated by user, inferred: LLM-extracted, confirmed: user confirmed)
 *   - TTL for automatic expiration
 *   - Optional vector embedding for semantic search
 *
 * Conflict resolution:
 *   - Same entity + same fact → latest wins, confidence boosted
 *   - Contradicting facts → both stored, graph edge created
 *   - User correction → invalidate old fact, store correction
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { log } from "../logger.js";

// ─── Types ─────────────────────────────────────────────────────

export type FactCategory =
  | "preference"
  | "project_detail"
  | "personal"
  | "skill"
  | "goal"
  | "relationship"
  | "habit"
  | "context"
  // Phase 4: Conversational Ground State categories
  | "decision"
  | "open_question"
  | "active_goal"
  | "sub_goal";

export type FactSource = "explicit" | "inferred" | "confirmed";

export interface StoredFact {
  id: string;
  userId: string;
  fact: string;
  entity?: string;
  category: FactCategory;
  confidence: number;
  source: FactSource;
  createdAt: string;
  updatedAt: string;
  expiresAt?: string;
  accessCount: number;
  confirmedBy?: string;
  contradictedBy?: string[];
}

export interface FactConflictResult {
  action: "keep" | "update" | "retire";
  existingFact: StoredFact;
  newConfidence: number;
  reasoning: string;
}

export interface FactStoreConfig {
  defaultTtlDays: number;
  maxFactsPerUser: number;
  confidenceThreshold: number;
  enableConflictResolution: boolean;
}

interface FactStoreData {
  facts: StoredFact[];
  version: number;
}

// ─── Constants ────────────────────────────────────────────────

const DEFAULT_CONFIG: FactStoreConfig = {
  defaultTtlDays: 30,
  maxFactsPerUser: 1000,
  confidenceThreshold: 0.3,
  enableConflictResolution: true,
};

const STORE_VERSION = 1;

// ─── Store ────────────────────────────────────────────────────

export class FactStore {
  private facts: Map<string, StoredFact> = new Map();
  private filePath: string;
  private loaded = false;
  private config: FactStoreConfig;

  constructor(workspacePath: string, config: Partial<FactStoreConfig> = {}) {
    this.filePath = join(workspacePath, "memory", "facts.json");
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ─── Lifecycle ─────────────────────────────────────────────

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const raw = await readFile(this.filePath, "utf-8");
        const data = JSON.parse(raw) as FactStoreData;
        for (const fact of data.facts) {
          this.facts.set(fact.id, fact);
        }
        log.engine.info(
          `[FactStore] Loaded ${this.facts.size} facts (config: defaultTtlDays=${this.config.defaultTtlDays})`,
        );
      }
    } catch (err) {
      log.engine.warn(
        `[FactStore] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });

    const data: FactStoreData = {
      facts: [...this.facts.values()],
      version: STORE_VERSION,
    };

    await writeFile(this.filePath, JSON.stringify(data, null, 2), "utf-8");
    log.engine.debug(`[FactStore] Saved ${this.facts.size} facts`);
  }

  // ─── Add / Update ─────────────────────────────────────────

  /**
   * Add a single fact, with conflict resolution against existing facts.
   * Returns the stored fact (or updated existing fact if conflict resolved).
   */
  async add(
    fact: Omit<StoredFact, "id" | "createdAt" | "updatedAt" | "accessCount">,
  ): Promise<StoredFact> {
    await this.load();

    const now = new Date().toISOString();
    const id = `fact_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

    if (this.config.enableConflictResolution) {
      const conflict = this.detectConflict(fact);
      if (conflict) {
        return this.resolveConflict(conflict, fact);
      }
    }

    const stored: StoredFact = {
      ...fact,
      id,
      createdAt: now,
      updatedAt: now,
      accessCount: 0,
    };

    this.facts.set(id, stored);
    await this.save();

    log.engine.debug(
      `[FactStore] Added fact: "${fact.fact.slice(0, 50)}" (confidence=${fact.confidence.toFixed(2)})`,
    );
    return stored;
  }

  /**
   * Add multiple facts in batch. Checks conflicts within the batch too.
   * Returns all stored facts (after conflict resolution).
   */
  async addBatch(
    facts: Omit<StoredFact, "id" | "createdAt" | "updatedAt" | "accessCount">[],
  ): Promise<StoredFact[]> {
    await this.load();
    const results: StoredFact[] = [];

    for (const fact of facts) {
      const stored = await this.add(fact);
      results.push(stored);
    }

    return results;
  }

  /**
   * Update an existing fact by ID.
   */
  async update(
    id: string,
    updates: Partial<Omit<StoredFact, "id" | "createdAt">>,
  ): Promise<StoredFact | null> {
    await this.load();
    const existing = this.facts.get(id);
    if (!existing) return null;

    const updated: StoredFact = {
      ...existing,
      ...updates,
      id: existing.id,
      createdAt: existing.createdAt,
      updatedAt: new Date().toISOString(),
    };

    this.facts.set(id, updated);
    await this.save();
    return updated;
  }

  /**
   * Retire a fact (soft delete — marks as expired).
   * Used when user corrects or contradicts a fact.
   */
  async retire(id: string, reason?: string): Promise<boolean> {
    await this.load();
    const fact = this.facts.get(id);
    if (!fact) return false;

    fact.updatedAt = new Date().toISOString();
    fact.confidence = 0;
    if (reason) {
      fact.contradictedBy = [...(fact.contradictedBy ?? []), reason];
    }

    this.facts.set(id, fact);
    await this.save();
    log.engine.debug(
      `[FactStore] Retired fact ${id}: ${reason ?? "no reason"}`,
    );
    return true;
  }

  /**
   * Confirm a fact (user explicitly confirms an inferred fact).
   * Boosts confidence and marks as confirmed.
   */
  async confirm(id: string, userId: string): Promise<StoredFact | null> {
    return this.update(id, {
      source: "confirmed",
      confidence: Math.min(1, 0.95),
      confirmedBy: userId,
    });
  }

  // ─── Retrieve ────────────────────────────────────────────

  /**
   * Get a fact by ID.
   */
  get(id: string): StoredFact | undefined {
    return this.facts.get(id);
  }

  /**
   * Get all facts (across all users).
   */
  getAll(): StoredFact[] {
    return [...this.facts.values()];
  }

  /**
   * Get all facts for a user.
   */
  getForUser(userId: string): StoredFact[] {
    return [...this.facts.values()].filter((f) => f.userId === userId);
  }

  /**
   * Get all active (non-retired, non-expired) facts for a user.
   */
  getActiveForUser(userId: string): StoredFact[] {
    const now = new Date();
    return this.getForUser(userId).filter((f) => {
      if (f.confidence <= 0) return false;
      if (f.expiresAt && new Date(f.expiresAt) < now) return false;
      return true;
    });
  }

  /**
   * Search facts by text match on fact + entity.
   * For semantic search, use MemoryRetriever which combines with embeddings.
   */
  search(query: string, userId?: string, limit = 20): StoredFact[] {
    const lower = query.toLowerCase();
    const terms = lower.split(/\s+/).filter(Boolean);

    let facts = userId
      ? this.getActiveForUser(userId)
      : [...this.facts.values()].filter((f) => f.confidence > 0);

    const scored: Array<{ fact: StoredFact; score: number }> = [];
    for (const fact of facts) {
      let score = 0;
      const haystack = `${fact.fact} ${fact.entity ?? ""}`.toLowerCase();
      for (const term of terms) {
        if (haystack.includes(term)) score += 1;
      }
      if (fact.entity?.toLowerCase().includes(lower)) score += 2;
      if (fact.category === lower) score += 1;
      if (score > 0) {
        score = score / terms.length;
        scored.push({ fact, score });
      }
    }

    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map((r) => {
        r.fact.accessCount++;
        return r.fact;
      });
  }

  /**
   * Get facts by category for a user.
   */
  getByCategory(userId: string, category: FactCategory): StoredFact[] {
    return this.getActiveForUser(userId).filter((f) => f.category === category);
  }

  /**
   * Get facts that mention a specific entity.
   */
  getByEntity(entity: string): StoredFact[] {
    const lower = entity.toLowerCase();
    return [...this.facts.values()].filter(
      (f) => f.entity?.toLowerCase().includes(lower) && f.confidence > 0,
    );
  }

  /**
   * Get all facts related to a given fact (same entity or category).
   */
  getRelated(factId: string): StoredFact[] {
    const fact = this.facts.get(factId);
    if (!fact) return [];

    return [...this.facts.values()].filter((f) => {
      if (f.id === factId) return false;
      if (f.confidence <= 0) return false;
      if (f.entity && fact.entity && f.entity === fact.entity) return true;
      if (f.category === fact.category) return true;
      return false;
    });
  }

  /**
   * Get statistics about stored facts.
   */
  getStats(userId?: string): {
    total: number;
    byCategory: Record<string, number>;
    bySource: Record<string, number>;
    avgConfidence: number;
    expired: number;
  } {
    const facts = userId ? this.getForUser(userId) : [...this.facts.values()];
    const now = new Date();

    const byCategory: Record<string, number> = {};
    const bySource: Record<string, number> = {};
    let totalConfidence = 0;
    let expired = 0;

    for (const f of facts) {
      if (f.confidence <= 0) continue;
      if (f.expiresAt && new Date(f.expiresAt) < now) expired++;

      byCategory[f.category] = (byCategory[f.category] ?? 0) + 1;
      bySource[f.source] = (bySource[f.source] ?? 0) + 1;
      totalConfidence += f.confidence;
    }

    const active = facts.filter((f) => f.confidence > 0);
    return {
      total: active.length,
      byCategory,
      bySource,
      avgConfidence: active.length > 0 ? totalConfidence / active.length : 0,
      expired,
    };
  }

  // ─── Expiration ───────────────────────────────────────────

  /**
   * Remove all expired facts from storage.
   * Returns the count of removed facts.
   */
  async purgeExpired(): Promise<number> {
    await this.load();
    const now = new Date();
    let removed = 0;

    for (const [id, fact] of this.facts) {
      if (fact.confidence <= 0) continue;
      if (fact.expiresAt && new Date(fact.expiresAt) < now) {
        this.facts.delete(id);
        removed++;
      }
    }

    if (removed > 0) {
      await this.save();
      log.engine.info(`[FactStore] Purged ${removed} expired facts`);
    }

    return removed;
  }

  /**
   * Apply default TTL to facts that don't have an expiration date.
   * Called during cleanup passes.
   */
  async applyDefaultTtl(): Promise<number> {
    await this.load();
    const ttlMs = this.config.defaultTtlDays * 24 * 60 * 60 * 1000;
    const expiresAt = new Date(Date.now() + ttlMs).toISOString();
    let updated = 0;

    for (const fact of this.facts.values()) {
      if (fact.confidence > 0 && !fact.expiresAt) {
        fact.expiresAt = expiresAt;
        updated++;
      }
    }

    if (updated > 0) await this.save();
    return updated;
  }

  // ─── Private Helpers ───────────────────────────────────────

  /**
   * Detect if a new fact conflicts with an existing one.
   * Returns the existing fact if conflict detected.
   */
  private detectConflict(
    newFact: Omit<StoredFact, "id" | "createdAt" | "updatedAt" | "accessCount">,
  ): StoredFact | null {
    const candidates = this.getActiveForUser(newFact.userId);

    for (const existing of candidates) {
      if (
        existing.entity &&
        newFact.entity &&
        existing.entity !== newFact.entity
      )
        continue;
      if (existing.category !== newFact.category) continue;

      const sameFact = this.areSameFact(existing.fact, newFact.fact);
      const negated = this.areNegated(existing.fact, newFact.fact);

      if (sameFact || negated) return existing;
    }

    return null;
  }

  /**
   * Heuristic: are two fact strings essentially the same?
   */
  private areSameFact(a: string, b: string): boolean {
    const normA = a.toLowerCase().replace(/[^a-z0-9]/g, "");
    const normB = b.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (normA === normB) return true;

    const wordsA = new Set(normA.split(/\s+/).filter((w) => w.length > 3));
    const wordsB = new Set(normB.split(/\s+/).filter((w) => w.length > 3));
    const intersection = [...wordsA].filter((w) => wordsB.has(w)).length;
    const union = new Set([...wordsA, ...wordsB]).size;

    return union > 0 && intersection / union > 0.7;
  }

  /**
   * Heuristic: does fact B contradict fact A?
   * Looks for negation patterns.
   */
  private areNegated(a: string, b: string): boolean {
    const negations = [
      ["not ", "doesn't ", "don't ", "never ", "no longer "],
      ["actually ", "in fact ", "but ", "however "],
    ];
    const lowerA = a.toLowerCase();
    const lowerB = b.toLowerCase();

    for (const negationSet of negations) {
      for (const n of negationSet) {
        if (lowerA.includes(n) !== lowerB.includes(n)) return true;
      }
    }

    const contradictions: [string, string][] = [
      ["likes", "hates"],
      ["prefers", "dislikes"],
      ["uses", "doesn't use"],
      ["works with", "doesn't work with"],
      ["loves", "hates"],
    ];

    for (const [pos, neg] of contradictions) {
      const hasPos = lowerA.includes(pos) || lowerB.includes(pos);
      const hasNeg = lowerA.includes(neg) || lowerB.includes(neg);
      if (hasPos && hasNeg) return true;
    }

    return false;
  }

  /**
   * Resolve a conflict between existing and new fact.
   * Strategy:
   *   - Same fact, new is more confident → update existing
   *   - Same fact, old is more confident → keep existing
   *   - Contradicting → retire old, store new
   */
  private async resolveConflict(
    existing: StoredFact,
    newFact: Omit<StoredFact, "id" | "createdAt" | "updatedAt" | "accessCount">,
  ): Promise<StoredFact> {
    const areSame = this.areSameFact(existing.fact, newFact.fact);
    const areNegated = !areSame && this.areNegated(existing.fact, newFact.fact);

    if (areNegated) {
      await this.retire(existing.id, `Contradicted by: "${newFact.fact}"`);
      return this.add({ ...newFact, contradictedBy: [existing.id] });
    }

    if (newFact.confidence >= existing.confidence) {
      const updated = await this.update(existing.id, {
        fact: newFact.fact,
        confidence: (existing.confidence + newFact.confidence) / 2,
        source: existing.source === "confirmed" ? "confirmed" : newFact.source,
      });
      return updated ?? existing;
    }

    return existing;
  }
}
