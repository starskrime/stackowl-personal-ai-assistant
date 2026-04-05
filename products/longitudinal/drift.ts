/**
 * Longitudinal AI — Drift Detector
 *
 * Detects significant shifts in a user's topics, priorities, sentiment,
 * and communication style across time windows.
 *
 * Output is used by PersonalityReport and the dashboard to surface
 * meaningful changes — not noise.
 */

import type { Timeline, TimelinePoint } from "./timeline.js";

export type DriftSignificance = "minor" | "moderate" | "significant";

export interface TopicDriftEvent {
  topic: string;
  type: "emerged" | "faded" | "intensified" | "diminished";
  windowLabel: string;
  significance: DriftSignificance;
  percentChange?: number;
}

export interface SentimentDriftEvent {
  windowLabel: string;
  shift: number; // -1 to +1
  direction: "more_positive" | "more_negative" | "stable";
  significance: DriftSignificance;
}

export interface PriorityShift {
  from: string[];
  to: string[];
  windowLabel: string;
  description: string;
}

export interface DriftReport {
  userId: string;
  analysedAt: number;
  overallDrift: number;
  driftLevel: DriftSignificance;

  topicDrifts: TopicDriftEvent[];
  sentimentDrifts: SentimentDriftEvent[];
  priorityShifts: PriorityShift[];

  /** Summary sentence suitable for report introduction */
  summary: string;

  /** Headline topics that define the user today */
  currentIdentity: string[];

  /** Topics that used to define the user but are now gone */
  pastIdentity: string[];
}

export class DriftDetector {
  /**
   * Analyse a timeline and produce a structured drift report.
   */
  detect(timeline: Timeline): DriftReport {
    const topicDrifts = this.detectTopicDrifts(timeline);
    const sentimentDrifts = this.detectSentimentDrifts(timeline);
    const priorityShifts = this.detectPriorityShifts(timeline);

    const driftLevel = this.classifyDrift(timeline.overallDrift);

    const currentIdentity = this.deriveCurrentIdentity(timeline);
    const pastIdentity = this.derivePastIdentity(timeline, currentIdentity);

    const summary = this.buildSummary(
      timeline,
      driftLevel,
      currentIdentity,
      pastIdentity,
      topicDrifts,
    );

    return {
      userId: timeline.userId,
      analysedAt: Date.now(),
      overallDrift: timeline.overallDrift,
      driftLevel,
      topicDrifts,
      sentimentDrifts,
      priorityShifts,
      summary,
      currentIdentity,
      pastIdentity,
    };
  }

  private detectTopicDrifts(timeline: Timeline): TopicDriftEvent[] {
    const events: TopicDriftEvent[] = [];

    for (const point of timeline.windows) {
      for (const shift of point.topicShifts) {
        if (shift.direction === "stable") continue;

        const significance =
          point.driftFromPrevious > 0.7
            ? "significant"
            : point.driftFromPrevious > 0.4
              ? "moderate"
              : "minor";

        if (significance === "minor") continue; // filter noise

        events.push({
          topic: shift.topic,
          type: shift.direction,
          windowLabel: point.window.label,
          significance,
        });
      }
    }

    return events.slice(0, 20);
  }

  private detectSentimentDrifts(timeline: Timeline): SentimentDriftEvent[] {
    const events: SentimentDriftEvent[] = [];

    for (const point of timeline.windows) {
      const shift = point.sentimentShift;
      const absShift = Math.abs(shift);

      if (absShift < 0.15) continue; // ignore tiny fluctuations

      const direction: SentimentDriftEvent["direction"] =
        shift > 0.15 ? "more_positive" : shift < -0.15 ? "more_negative" : "stable";

      const significance: DriftSignificance =
        absShift > 0.5 ? "significant" : absShift > 0.25 ? "moderate" : "minor";

      events.push({
        windowLabel: point.window.label,
        shift,
        direction,
        significance,
      });
    }

    return events;
  }

  private detectPriorityShifts(timeline: Timeline): PriorityShift[] {
    const shifts: PriorityShift[] = [];
    const windows = timeline.windows;

    // Compare every 2 windows apart
    for (let i = 1; i < windows.length; i++) {
      const prev = windows[i - 1];
      const curr = windows[i];

      const prevTop3 = prev.window.topTopics.slice(0, 3).map((t) => t.topic);
      const currTop3 = curr.window.topTopics.slice(0, 3).map((t) => t.topic);

      const overlap = prevTop3.filter((t) => currTop3.includes(t)).length;

      if (overlap === 0 && prevTop3.length > 0 && currTop3.length > 0) {
        shifts.push({
          from: prevTop3,
          to: currTop3,
          windowLabel: curr.window.label,
          description: `Complete priority reset: moved from [${prevTop3.join(", ")}] to [${currTop3.join(", ")}]`,
        });
      } else if (overlap <= 1 && prevTop3.length >= 2) {
        const gained = currTop3.filter((t) => !prevTop3.includes(t));
        const lost = prevTop3.filter((t) => !currTop3.includes(t));
        if (gained.length > 0 && lost.length > 0) {
          shifts.push({
            from: lost,
            to: gained,
            windowLabel: curr.window.label,
            description: `Priority shift in ${curr.window.label}: dropped [${lost.join(", ")}], picked up [${gained.join(", ")}]`,
          });
        }
      }
    }

    return shifts.slice(0, 10);
  }

  private deriveCurrentIdentity(timeline: Timeline): string[] {
    const recent = timeline.windows.slice(-2);
    if (recent.length === 0) return [];
    const counts: Record<string, number> = {};
    for (const point of recent) {
      for (const t of point.window.topTopics) {
        counts[t.topic] = (counts[t.topic] ?? 0) + t.count;
      }
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([t]) => t);
  }

  private derivePastIdentity(timeline: Timeline, current: string[]): string[] {
    const old = timeline.windows.slice(0, Math.max(1, timeline.windows.length - 2));
    if (old.length === 0) return [];
    const counts: Record<string, number> = {};
    for (const point of old) {
      for (const t of point.window.topTopics) {
        counts[t.topic] = (counts[t.topic] ?? 0) + t.count;
      }
    }
    const currentSet = new Set(current);
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .filter(([t]) => !currentSet.has(t))
      .slice(0, 5)
      .map(([t]) => t);
  }

  private classifyDrift(drift: number): DriftSignificance {
    if (drift > 0.6) return "significant";
    if (drift > 0.3) return "moderate";
    return "minor";
  }

  private buildSummary(
    timeline: Timeline,
    level: DriftSignificance,
    current: string[],
    past: string[],
    topicDrifts: TopicDriftEvent[],
  ): string {
    const totalMonths = timeline.windows.length;
    const driftCount = topicDrifts.filter((d) => d.significance !== "minor").length;

    if (totalMonths === 0) return "Not enough history to detect drift.";

    if (level === "minor" && driftCount === 0) {
      return `Your focus has been remarkably consistent over the past ${totalMonths} period(s). Core themes: ${current.slice(0, 3).join(", ")}.`;
    }

    const parts: string[] = [];

    if (current.length > 0) {
      parts.push(`Currently focused on: ${current.slice(0, 3).join(", ")}.`);
    }

    if (past.length > 0 && level !== "minor") {
      parts.push(`These topics have faded: ${past.slice(0, 3).join(", ")}.`);
    }

    if (level === "significant") {
      parts.push(`Significant identity shift detected across ${totalMonths} time periods.`);
    } else if (level === "moderate") {
      parts.push(`Moderate evolution in priorities over ${totalMonths} periods.`);
    }

    return parts.join(" ");
  }
}
