import type { Affordance, VisualSignature, MemoryQuery } from "./types.js";
import { visualSignatureExtractor } from "./types.js";

export class SemanticStore {
  private affordances: Map<string, Affordance> = new Map();
  private roleIndex: Map<string, Set<string>> = new Map();
  private actionIndex: Map<string, Set<string>> = new Map();
  private appIndex: Map<string, Set<string>> = new Map();
  private visualIndex: Affordance[] = [];

  async store(affordance: Omit<Affordance, "id" | "createdAt" | "updatedAt">): Promise<Affordance> {
    const id = `aff_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const now = Date.now();
    const full: Affordance = {
      ...affordance,
      id,
      createdAt: now,
      updatedAt: now,
    };

    this.affordances.set(id, full);
    this.indexAffordance(full);
    this.rebuildVisualIndex();

    return full;
  }

  async update(id: string, updates: Partial<Affordance>): Promise<Affordance | null> {
    const existing = this.affordances.get(id);
    if (!existing) return null;

    const updated: Affordance = {
      ...existing,
      ...updates,
      id: existing.id,
      createdAt: existing.createdAt,
      updatedAt: Date.now(),
    };

    this.affordances.set(id, updated);
    this.reindexAffordance(updated);
    this.rebuildVisualIndex();

    return updated;
  }

  async recordAttempt(id: string, success: boolean): Promise<Affordance | null> {
    const existing = this.affordances.get(id);
    if (!existing) return null;

    const attempts = existing.attempts + 1;
    const successes = success ? existing.successRate * existing.attempts + 1 : existing.successRate * existing.attempts;
    const successRate = successes / attempts;

    return this.update(id, {
      attempts,
      successRate,
      lastAttempt: Date.now(),
    });
  }

  private indexAffordance(affordance: Affordance): void {
    if (!this.roleIndex.has(affordance.targetRole)) {
      this.roleIndex.set(affordance.targetRole, new Set());
    }
    this.roleIndex.get(affordance.targetRole)!.add(affordance.id);

    if (!this.actionIndex.has(affordance.action)) {
      this.actionIndex.set(affordance.action, new Set());
    }
    this.actionIndex.get(affordance.action)!.add(affordance.id);

    if (!this.appIndex.has(affordance.app)) {
      this.appIndex.set(affordance.app, new Set());
    }
    this.appIndex.get(affordance.app)!.add(affordance.id);
  }

  private reindexAffordance(affordance: Affordance): void {
    for (const index of [this.roleIndex, this.actionIndex, this.appIndex]) {
      for (const ids of index.values()) {
        ids.delete(affordance.id);
      }
    }
    this.indexAffordance(affordance);
  }

  private rebuildVisualIndex(): void {
    this.visualIndex = Array.from(this.affordances.values());
  }

  async query(query: MemoryQuery): Promise<Affordance[]> {
    let candidateIds: Set<string> | null = null;

    if (query.app) {
      candidateIds = this.appIndex.get(query.app) || new Set();
    }

    if (query.action) {
      const actionSet = this.actionIndex.get(query.action) || new Set();
      candidateIds = candidateIds
        ? this.intersection(candidateIds, actionSet)
        : actionSet;
    }

    if (query.role) {
      const roleSet = this.roleIndex.get(query.role) || new Set();
      candidateIds = candidateIds
        ? this.intersection(candidateIds, roleSet)
        : roleSet;
    }

    const allIds = candidateIds || new Set(this.affordances.keys());
    const results = Array.from(allIds)
      .map((id) => this.affordances.get(id))
      .filter((aff): aff is Affordance => {
        if (!aff) return false;
        if (query.minSuccessRate && aff.successRate < query.minSuccessRate) return false;
        if (query.since && aff.updatedAt < query.since) return false;
        return true;
      })
      .sort((a, b) => b.successRate - a.successRate);

    return results.slice(0, query.limit || 100);
  }

  async findSimilar(
    signature: VisualSignature,
    options?: { role?: string; action?: string; app?: string; limit?: number }
  ): Promise<Affordance[]> {
    let candidates = this.visualIndex;

    if (options?.role) {
      candidates = candidates.filter((a) => a.targetRole === options.role);
    }
    if (options?.action) {
      candidates = candidates.filter((a) => a.action === options.action);
    }
    if (options?.app) {
      candidates = candidates.filter((a) => a.app === options.app);
    }

    const withDistances = candidates.map((aff) => ({
      affordance: aff,
      distance: visualSignatureExtractor.distance(signature, aff.visualSignature),
    }));

    withDistances.sort((a, b) => a.distance - b.distance);

    const limit = options?.limit || 10;
    return withDistances.slice(0, limit).map((w) => w.affordance);
  }

  async findByRole(role: string, limit?: number): Promise<Affordance[]> {
    return this.query({ role, limit });
  }

  async findByAction(action: string, limit?: number): Promise<Affordance[]> {
    return this.query({ action, limit });
  }

  async findByApp(app: string, limit?: number): Promise<Affordance[]> {
    return this.query({ app, limit });
  }

  async getTopByApp(app: string, limit: number = 10): Promise<Affordance[]> {
    const all = await this.findByApp(app);
    return all.slice(0, limit);
  }

  async getStats(): Promise<{
    total: number;
    byApp: Record<string, number>;
    byRole: Record<string, number>;
    byAction: Record<string, number>;
    avgSuccessRate: number;
  }> {
    const stats = {
      total: this.affordances.size,
      byApp: {} as Record<string, number>,
      byRole: {} as Record<string, number>,
      byAction: {} as Record<string, number>,
      avgSuccessRate: 0,
    };

    let totalRate = 0;
    for (const aff of this.affordances.values()) {
      stats.byApp[aff.app] = (stats.byApp[aff.app] || 0) + 1;
      stats.byRole[aff.targetRole] = (stats.byRole[aff.targetRole] || 0) + 1;
      stats.byAction[aff.action] = (stats.byAction[aff.action] || 0) + 1;
      totalRate += aff.successRate;
    }

    if (this.affordances.size > 0) {
      stats.avgSuccessRate = totalRate / this.affordances.size;
    }

    return stats;
  }

  async delete(id: string): Promise<boolean> {
    const aff = this.affordances.get(id);
    if (!aff) return false;

    this.affordances.delete(id);
    this.reindexAffordance(aff);
    this.rebuildVisualIndex();

    return true;
  }

  async clear(): Promise<void> {
    this.affordances.clear();
    this.roleIndex.clear();
    this.actionIndex.clear();
    this.appIndex.clear();
    this.visualIndex = [];
  }

  private intersection<T>(a: Set<T>, b: Set<T>): Set<T> {
    const result = new Set<T>();
    for (const item of a) {
      if (b.has(item)) result.add(item);
    }
    return result;
  }
}

export const semanticStore = new SemanticStore();
