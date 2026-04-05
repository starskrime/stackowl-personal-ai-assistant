/**
 * Longitudinal AI — Timeline Engine
 *
 * Ordered view of a user's episodic memory with drift scoring.
 * Groups episodes into time windows and computes shift metrics
 * (topic distribution, sentiment, priority patterns) between windows.
 */

import { join } from "node:path";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import type { Episode } from "../../src/memory/episodic.js";

export type WindowSize = "week" | "month" | "quarter";

export interface TimeWindow {
  label: string;
  startMs: number;
  endMs: number;
  episodes: Episode[];
  topTopics: Array<{ topic: string; count: number }>;
  sentimentBreakdown: Record<string, number>;
  avgImportance: number;
  episodeCount: number;
}

export interface TimelinePoint {
  window: TimeWindow;
  driftFromPrevious: number; // 0–1, how different from last window
  topicShifts: Array<{ topic: string; direction: "emerged" | "faded" | "stable" }>;
  sentimentShift: number; // -1 to +1 (negative = more negative, positive = more positive)
}

export interface Timeline {
  userId: string;
  windows: TimelinePoint[];
  overallDrift: number;
  dominantTopicsAllTime: Array<{ topic: string; count: number }>;
  totalEpisodes: number;
  firstEpisodeDate: number | null;
  latestEpisodeDate: number | null;
}

export class TimelineEngine {
  /**
   * Build a timeline from a user's episodic memory workspace.
   *
   * @param workspacePath  Root path (e.g. ./memory-store/userId)
   * @param windowSize     Granularity: week | month | quarter
   * @param maxWindows     Max number of windows to return (most recent first)
   */
  async build(
    workspacePath: string,
    windowSize: WindowSize = "month",
    maxWindows = 12,
  ): Promise<Timeline> {
    const episodes = await this.loadEpisodes(workspacePath);
    const userId = workspacePath.split("/").pop() ?? "unknown";

    if (episodes.length === 0) {
      return this.emptyTimeline(userId);
    }

    const sorted = [...episodes].sort((a, b) => a.date - b.date);
    const windows = this.groupIntoWindows(sorted, windowSize);
    const points = this.computeDrift(windows);

    // Keep most recent N windows
    const trimmed = points.slice(-maxWindows);

    const allTopics = this.aggregateTopics(episodes);

    return {
      userId,
      windows: trimmed,
      overallDrift: this.computeOverallDrift(trimmed),
      dominantTopicsAllTime: allTopics.slice(0, 10),
      totalEpisodes: episodes.length,
      firstEpisodeDate: sorted[0]?.date ?? null,
      latestEpisodeDate: sorted[sorted.length - 1]?.date ?? null,
    };
  }

  private async loadEpisodes(workspacePath: string): Promise<Episode[]> {
    const filePath = join(workspacePath, "memory", "episodes.json");
    if (!existsSync(filePath)) return [];
    try {
      const raw = await readFile(filePath, "utf-8");
      return JSON.parse(raw) as Episode[];
    } catch {
      return [];
    }
  }

  private groupIntoWindows(episodes: Episode[], size: WindowSize): TimeWindow[] {
    if (episodes.length === 0) return [];

    const windowMs =
      size === "week" ? 7 * 24 * 60 * 60 * 1000
      : size === "month" ? 30 * 24 * 60 * 60 * 1000
      : 90 * 24 * 60 * 60 * 1000;

    const first = episodes[0].date;
    const last = episodes[episodes.length - 1].date;
    const windows: TimeWindow[] = [];

    let cursor = this.alignToWindowStart(first, size);
    while (cursor <= last) {
      const start = cursor;
      const end = cursor + windowMs;
      const bucket = episodes.filter((e) => e.date >= start && e.date < end);

      if (bucket.length > 0) {
        windows.push(this.buildWindow(bucket, start, end, size));
      }

      cursor = end;
    }

    return windows;
  }

  private buildWindow(
    episodes: Episode[],
    startMs: number,
    endMs: number,
    size: WindowSize,
  ): TimeWindow {
    const topicCounts: Record<string, number> = {};
    const sentimentCounts: Record<string, number> = {};
    let totalImportance = 0;

    for (const ep of episodes) {
      for (const t of ep.topics) {
        topicCounts[t] = (topicCounts[t] ?? 0) + 1;
      }
      const s = ep.sentiment ?? "neutral";
      sentimentCounts[s] = (sentimentCounts[s] ?? 0) + 1;
      totalImportance += ep.importance ?? 0.5;
    }

    const topTopics = Object.entries(topicCounts)
      .map(([topic, count]) => ({ topic, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 5);

    const label = this.formatWindowLabel(startMs, size);

    return {
      label,
      startMs,
      endMs,
      episodes,
      topTopics,
      sentimentBreakdown: sentimentCounts,
      avgImportance: episodes.length > 0 ? totalImportance / episodes.length : 0,
      episodeCount: episodes.length,
    };
  }

  private computeDrift(windows: TimeWindow[]): TimelinePoint[] {
    return windows.map((window, i) => {
      const prev = i > 0 ? windows[i - 1] : null;

      const topicShifts = prev
        ? this.computeTopicShifts(prev, window)
        : window.topTopics.map((t) => ({ topic: t.topic, direction: "emerged" as const }));

      const driftFromPrevious = prev ? this.jaccardDrift(prev, window) : 0;
      const sentimentShift = prev ? this.sentimentShift(prev, window) : 0;

      return { window, driftFromPrevious, topicShifts, sentimentShift };
    });
  }

  private computeTopicShifts(
    prev: TimeWindow,
    curr: TimeWindow,
  ): Array<{ topic: string; direction: "emerged" | "faded" | "stable" }> {
    const prevTopics = new Set(prev.topTopics.map((t) => t.topic));
    const currTopics = new Set(curr.topTopics.map((t) => t.topic));
    const result: Array<{ topic: string; direction: "emerged" | "faded" | "stable" }> = [];

    for (const t of currTopics) {
      result.push({ topic: t, direction: prevTopics.has(t) ? "stable" : "emerged" });
    }
    for (const t of prevTopics) {
      if (!currTopics.has(t)) {
        result.push({ topic: t, direction: "faded" });
      }
    }

    return result.slice(0, 8);
  }

  private jaccardDrift(prev: TimeWindow, curr: TimeWindow): number {
    const prevTopics = new Set(prev.topTopics.map((t) => t.topic));
    const currTopics = new Set(curr.topTopics.map((t) => t.topic));
    if (prevTopics.size === 0 && currTopics.size === 0) return 0;

    let intersection = 0;
    for (const t of currTopics) {
      if (prevTopics.has(t)) intersection++;
    }
    const union = prevTopics.size + currTopics.size - intersection;
    return union === 0 ? 0 : 1 - intersection / union;
  }

  private sentimentShift(prev: TimeWindow, curr: TimeWindow): number {
    const score = (w: TimeWindow) => {
      const pos = (w.sentimentBreakdown["positive"] ?? 0) + (w.sentimentBreakdown["happy"] ?? 0);
      const neg = (w.sentimentBreakdown["frustrated"] ?? 0);
      const total = w.episodeCount;
      return total === 0 ? 0 : (pos - neg) / total;
    };
    return score(curr) - score(prev);
  }

  private computeOverallDrift(points: TimelinePoint[]): number {
    if (points.length < 2) return 0;
    const drifts = points.slice(1).map((p) => p.driftFromPrevious);
    return drifts.reduce((sum, d) => sum + d, 0) / drifts.length;
  }

  private aggregateTopics(episodes: Episode[]): Array<{ topic: string; count: number }> {
    const counts: Record<string, number> = {};
    for (const ep of episodes) {
      for (const t of ep.topics) {
        counts[t] = (counts[t] ?? 0) + 1;
      }
    }
    return Object.entries(counts)
      .map(([topic, count]) => ({ topic, count }))
      .sort((a, b) => b.count - a.count);
  }

  private alignToWindowStart(ts: number, size: WindowSize): number {
    const d = new Date(ts);
    if (size === "week") {
      const day = d.getUTCDay();
      d.setUTCDate(d.getUTCDate() - day);
    } else if (size === "month") {
      d.setUTCDate(1);
    } else {
      const month = d.getUTCMonth();
      d.setUTCMonth(month - (month % 3));
      d.setUTCDate(1);
    }
    d.setUTCHours(0, 0, 0, 0);
    return d.getTime();
  }

  private formatWindowLabel(ts: number, size: WindowSize): string {
    const d = new Date(ts);
    if (size === "week") {
      return `Week of ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;
    }
    if (size === "month") {
      return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
    }
    const quarters = ["Q1", "Q2", "Q3", "Q4"];
    return `${quarters[Math.floor(d.getUTCMonth() / 3)]} ${d.getUTCFullYear()}`;
  }

  private emptyTimeline(userId: string): Timeline {
    return {
      userId,
      windows: [],
      overallDrift: 0,
      dominantTopicsAllTime: [],
      totalEpisodes: 0,
      firstEpisodeDate: null,
      latestEpisodeDate: null,
    };
  }
}
