import type { Skill } from "./types.js";

export interface SkillQuery {
  name?: string;
  sourceApp?: string;
  targetApp?: string;
  action?: string;
  minSuccessRate?: number;
  since?: number;
  limit?: number;
}

export class ProceduralStore {
  private skills: Map<string, Skill> = new Map();
  private nameIndex: Map<string, Set<string>> = new Map();
  private sourceAppIndex: Map<string, Set<string>> = new Map();
  private targetAppIndex: Map<string, Set<string>> = new Map();
  private actionIndex: Map<string, Set<string>> = new Map();

  async store(skill: Omit<Skill, "id" | "createdAt" | "updatedAt" | "usageCount">): Promise<Skill> {
    const id = `skill_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const now = Date.now();
    const full: Skill = {
      ...skill,
      id,
      createdAt: now,
      updatedAt: now,
      usageCount: 0,
    };

    this.skills.set(id, full);
    this.indexSkill(full);

    return full;
  }

  async update(id: string, updates: Partial<Skill>): Promise<Skill | null> {
    const existing = this.skills.get(id);
    if (!existing) return null;

    const updated: Skill = {
      ...existing,
      ...updates,
      id: existing.id,
      createdAt: existing.createdAt,
      updatedAt: Date.now(),
    };

    this.skills.set(id, updated);
    this.reindexSkill(updated);

    return updated;
  }

  async recordUsage(id: string): Promise<Skill | null> {
    const existing = this.skills.get(id);
    if (!existing) return null;

    return this.update(id, {
      usageCount: existing.usageCount + 1,
      lastUsed: Date.now(),
    });
  }

  private indexSkill(skill: Skill): void {
    const nameWords = skill.name.toLowerCase().split(/\s+/);
    for (const word of nameWords) {
      if (!this.nameIndex.has(word)) {
        this.nameIndex.set(word, new Set());
      }
      this.nameIndex.get(word)!.add(skill.id);
    }

    if (skill.sourceApp) {
      if (!this.sourceAppIndex.has(skill.sourceApp)) {
        this.sourceAppIndex.set(skill.sourceApp, new Set());
      }
      this.sourceAppIndex.get(skill.sourceApp)!.add(skill.id);
    }

    for (const app of skill.targetApps) {
      if (!this.targetAppIndex.has(app)) {
        this.targetAppIndex.set(app, new Set());
      }
      this.targetAppIndex.get(app)!.add(skill.id);
    }

    for (const step of skill.steps) {
      if (!this.actionIndex.has(step.action)) {
        this.actionIndex.set(step.action, new Set());
      }
      this.actionIndex.get(step.action)!.add(skill.id);
    }
  }

  private reindexSkill(skill: Skill): void {
    for (const ids of this.nameIndex.values()) {
      ids.delete(skill.id);
    }
    for (const ids of this.sourceAppIndex.values()) {
      ids.delete(skill.id);
    }
    for (const ids of this.targetAppIndex.values()) {
      ids.delete(skill.id);
    }
    for (const ids of this.actionIndex.values()) {
      ids.delete(skill.id);
    }
    this.indexSkill(skill);
  }

  async query(query: SkillQuery): Promise<Skill[]> {
    let candidateIds: Set<string> | null = null;

    if (query.name) {
      const words = query.name.toLowerCase().split(/\s+/);
      const nameMatches = new Set<string>();
      for (const word of words) {
        const ids = Array.from(this.nameIndex.get(word) || []);
        if (nameMatches.size === 0) {
          for (const id of ids) nameMatches.add(id);
        } else {
          for (const id of ids) {
            if (!nameMatches.has(id)) nameMatches.delete(id);
          }
        }
      }
      candidateIds = nameMatches;
    }

    if (query.sourceApp) {
      const sourceSet = this.sourceAppIndex.get(query.sourceApp) || new Set();
      candidateIds = candidateIds
        ? this.intersection(candidateIds, sourceSet)
        : sourceSet;
    }

    if (query.targetApp) {
      const targetSet = this.targetAppIndex.get(query.targetApp) || new Set();
      candidateIds = candidateIds
        ? this.intersection(candidateIds, targetSet)
        : targetSet;
    }

    if (query.action) {
      const actionSet = this.actionIndex.get(query.action) || new Set();
      candidateIds = candidateIds
        ? this.intersection(candidateIds, actionSet)
        : actionSet;
    }

    const allIds = candidateIds || new Set(this.skills.keys());
    const results = Array.from(allIds)
      .map((id) => this.skills.get(id))
      .filter((skill): skill is Skill => {
        if (!skill) return false;
        if (query.minSuccessRate && skill.successRate < query.minSuccessRate) return false;
        if (query.since && skill.updatedAt < query.since) return false;
        return true;
      })
      .sort((a, b) => b.usageCount - a.usageCount);

    return results.slice(0, query.limit || 100);
  }

  async findByName(name: string, limit?: number): Promise<Skill[]> {
    return this.query({ name, limit });
  }

  async findByTargetApp(app: string, limit?: number): Promise<Skill[]> {
    return this.query({ targetApp: app, limit });
  }

  async findBySourceApp(app: string, limit?: number): Promise<Skill[]> {
    return this.query({ sourceApp: app, limit });
  }

  async findMostUsed(limit: number = 10): Promise<Skill[]> {
    return this.query({ limit });
  }

  async findTopRated(limit: number = 10): Promise<Skill[]> {
    const all = Array.from(this.skills.values());
    return all
      .sort((a, b) => b.successRate - a.successRate)
      .slice(0, limit);
  }

  async getStats(): Promise<{
    total: number;
    byTargetApp: Record<string, number>;
    avgSuccessRate: number;
    totalUsage: number;
  }> {
    const stats = {
      total: this.skills.size,
      byTargetApp: {} as Record<string, number>,
      avgSuccessRate: 0,
      totalUsage: 0,
    };

    let totalRate = 0;
    for (const skill of this.skills.values()) {
      for (const app of skill.targetApps) {
        stats.byTargetApp[app] = (stats.byTargetApp[app] || 0) + 1;
      }
      totalRate += skill.successRate;
      stats.totalUsage += skill.usageCount;
    }

    if (this.skills.size > 0) {
      stats.avgSuccessRate = totalRate / this.skills.size;
    }

    return stats;
  }

  async delete(id: string): Promise<boolean> {
    const skill = this.skills.get(id);
    if (!skill) return false;

    this.skills.delete(id);
    this.reindexSkill(skill);

    return true;
  }

  async clear(): Promise<void> {
    this.skills.clear();
    this.nameIndex.clear();
    this.sourceAppIndex.clear();
    this.targetAppIndex.clear();
    this.actionIndex.clear();
  }

  private intersection<T>(a: Set<T>, b: Set<T>): Set<T> {
    const result = new Set<T>();
    for (const item of a) {
      if (b.has(item)) result.add(item);
    }
    return result;
  }
}

export const proceduralStore = new ProceduralStore();
