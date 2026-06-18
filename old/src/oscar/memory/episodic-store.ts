import type { Episode } from "./types.js";

export interface EpisodicQuery {
  app?: string;
  outcome?: Episode["outcome"];
  since?: number;
  until?: number;
  limit?: number;
  offset?: number;
}

export class EpisodicStore {
  private episodes: Map<string, Episode> = new Map();
  private appIndex: Map<string, Set<string>> = new Map();
  private outcomeIndex: Map<Episode["outcome"], Set<string>> = new Map();
  private timestampIndex: string[] = [];
  private maxEpisodes = 10000;
  private pruneThreshold = 0.9;

  async record(episode: Omit<Episode, "id" | "timestamp">): Promise<Episode> {
    const id = `ep_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const fullEpisode: Episode = {
      ...episode,
      id,
      timestamp: Date.now(),
    };

    this.episodes.set(id, fullEpisode);
    this.indexEpisode(fullEpisode);

    if (this.episodes.size > this.maxEpisodes * this.pruneThreshold) {
      this.prune();
    }

    return fullEpisode;
  }

  private indexEpisode(episode: Episode): void {
    if (episode.app) {
      if (!this.appIndex.has(episode.app)) {
        this.appIndex.set(episode.app, new Set());
      }
      this.appIndex.get(episode.app)!.add(episode.id);
    }

    if (!this.outcomeIndex.has(episode.outcome)) {
      this.outcomeIndex.set(episode.outcome, new Set());
    }
    this.outcomeIndex.get(episode.outcome)!.add(episode.id);

    const insertPos = this.timestampIndex.findIndex(
      (tid) => (this.episodes.get(tid)?.timestamp || 0) > episode.timestamp
    );
    if (insertPos === -1) {
      this.timestampIndex.push(episode.id);
    } else {
      this.timestampIndex.splice(insertPos, 0, episode.id);
    }
  }

  async query(query: EpisodicQuery): Promise<Episode[]> {
    let candidateIds: Set<string> | null = null;

    if (query.app) {
      candidateIds = this.appIndex.get(query.app) || new Set();
    }

    if (query.outcome) {
      const outcomeSet = this.outcomeIndex.get(query.outcome) || new Set();
      if (candidateIds) {
        candidateIds = this.intersection(candidateIds, outcomeSet);
      } else {
        candidateIds = outcomeSet;
      }
    }

    const allIds = candidateIds || new Set(this.episodes.keys());
    const ids = Array.from(allIds);

    const episodes = ids
      .map((id) => this.episodes.get(id))
      .filter((ep): ep is Episode => {
        if (!ep) return false;
        if (query.since && ep.timestamp < query.since) return false;
        if (query.until && ep.timestamp > query.until) return false;
        return true;
      })
      .sort((a, b) => b.timestamp - a.timestamp);

    const offset = query.offset || 0;
    const limit = query.limit || 100;
    return episodes.slice(offset, offset + limit);
  }

  async get(id: string): Promise<Episode | null> {
    return this.episodes.get(id) || null;
  }

  async getRecent(count: number = 10): Promise<Episode[]> {
    return this.query({ limit: count });
  }

  async getByApp(app: string, limit?: number): Promise<Episode[]> {
    return this.query({ app, limit });
  }

  async getByOutcome(outcome: Episode["outcome"], limit?: number): Promise<Episode[]> {
    return this.query({ outcome, limit });
  }

  async getFailed(limit?: number): Promise<Episode[]> {
    return this.query({ outcome: "failed", limit });
  }

  async getCorrected(limit?: number): Promise<Episode[]> {
    const all = await this.query({ limit: 1000 });
    return all.filter((ep) => ep.userFeedback === "corrected").slice(0, limit);
  }

  async getStats(): Promise<{
    total: number;
    byOutcome: Record<string, number>;
    byApp: Record<string, number>;
    avgDuration?: number;
  }> {
    const stats = {
      total: this.episodes.size,
      byOutcome: {} as Record<string, number>,
      byApp: {} as Record<string, number>,
    };

    for (const episode of this.episodes.values()) {
      stats.byOutcome[episode.outcome] = (stats.byOutcome[episode.outcome] || 0) + 1;
      if (episode.app) {
        stats.byApp[episode.app] = (stats.byApp[episode.app] || 0) + 1;
      }
    }

    return stats;
  }

  async updateFeedback(
    episodeId: string,
    feedback: Episode["userFeedback"]
  ): Promise<Episode | null> {
    const episode = this.episodes.get(episodeId);
    if (!episode) return null;

    episode.userFeedback = feedback;
    return episode;
  }

  async delete(id: string): Promise<boolean> {
    const episode = this.episodes.get(id);
    if (!episode) return false;

    this.episodes.delete(id);

    if (episode.app) {
      this.appIndex.get(episode.app)?.delete(id);
    }
    this.outcomeIndex.get(episode.outcome)?.delete(id);

    const tsIdx = this.timestampIndex.indexOf(id);
    if (tsIdx !== -1) {
      this.timestampIndex.splice(tsIdx, 1);
    }

    return true;
  }

  async clear(): Promise<void> {
    this.episodes.clear();
    this.appIndex.clear();
    this.outcomeIndex.clear();
    this.timestampIndex = [];
  }

  private prune(): void {
    const toRemove = Math.floor(this.maxEpisodes * 0.1);
    const oldestIds = this.timestampIndex.slice(0, toRemove);

    for (const id of oldestIds) {
      const episode = this.episodes.get(id);
      if (episode) {
        if (episode.app) {
          this.appIndex.get(episode.app)?.delete(id);
        }
        this.outcomeIndex.get(episode.outcome)?.delete(id);
        this.episodes.delete(id);
      }
    }

    this.timestampIndex = this.timestampIndex.slice(toRemove);
    console.log(`[EpisodicStore] Pruned ${toRemove} old episodes`);
  }

  private intersection<T>(a: Set<T>, b: Set<T>): Set<T> {
    const result = new Set<T>();
    for (const item of a) {
      if (b.has(item)) result.add(item);
    }
    return result;
  }
}

export const episodicStore = new EpisodicStore();
