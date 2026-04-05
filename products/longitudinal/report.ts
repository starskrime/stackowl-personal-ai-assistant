/**
 * Longitudinal AI — Personality Report Generator
 *
 * Uses an LLM to generate a narrative "who you were then vs now" report.
 * Combines timeline data + drift analysis into a human-readable story.
 *
 * Two report types:
 *   - "mirror"  — reflective narrative, no judgement
 *   - "digest"  — weekly/monthly summary with highlights
 */

import type { Timeline } from "./timeline.js";
import type { DriftReport } from "./drift.js";
import type { MemoryProvider } from "../memory-sdk/types.js";

export type ReportType = "mirror" | "digest";

export interface PersonalityReportOptions {
  type?: ReportType;
  tone?: "warm" | "direct" | "analytical";
  focusWindow?: number; // number of most recent windows to focus on
}

export interface PersonalityReport {
  userId: string;
  generatedAt: number;
  type: ReportType;
  title: string;
  narrative: string;
  highlights: string[];
  commitmentFollowThrough?: number; // 0–1 if commitment data is provided
  timeSpan: { from: string; to: string } | null;
}

export class PersonalityReportGenerator {
  private provider: MemoryProvider;

  constructor(provider: MemoryProvider) {
    this.provider = provider;
  }

  /**
   * Generate a narrative report from timeline + drift data.
   */
  async generate(
    timeline: Timeline,
    drift: DriftReport,
    options: PersonalityReportOptions = {},
  ): Promise<PersonalityReport> {
    const type = options.type ?? "mirror";
    const tone = options.tone ?? "warm";

    if (timeline.totalEpisodes === 0) {
      return this.emptyReport(timeline.userId, type);
    }

    const context = this.buildContext(timeline, drift, options);
    const prompt = this.buildPrompt(context, type, tone);

    let narrative = "";
    let highlights: string[] = [];

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content:
              "You are a thoughtful AI that helps people understand how they've changed over time. " +
              "You write in the second person ('you'). You are never judgemental. " +
              "You surface patterns the user may not have noticed themselves. " +
              "You are specific — you reference actual topics and events, not vague platitudes.",
          },
          { role: "user", content: prompt },
        ],
        { maxTokens: 800, temperature: 0.7 },
      );

      const parsed = this.parseResponse(response.content);
      narrative = parsed.narrative;
      highlights = parsed.highlights;
    } catch {
      // Fallback: template-based report
      narrative = this.templateNarrative(timeline, drift, type);
      highlights = this.templateHighlights(drift);
    }

    const timeSpan = this.formatTimeSpan(timeline);
    const title = this.buildTitle(type, timeSpan, drift);

    return {
      userId: timeline.userId,
      generatedAt: Date.now(),
      type,
      title,
      narrative,
      highlights,
      timeSpan,
    };
  }

  private buildContext(
    timeline: Timeline,
    drift: DriftReport,
    options: PersonalityReportOptions,
  ): string {
    const focusN = options.focusWindow ?? Math.min(6, timeline.windows.length);
    const recentWindows = timeline.windows.slice(-focusN);

    const windowSummaries = recentWindows
      .map(
        (p) =>
          `${p.window.label} (${p.window.episodeCount} interactions): ` +
          `Top topics: ${p.window.topTopics.map((t) => t.topic).join(", ")}. ` +
          `Sentiment: ${Object.entries(p.window.sentimentBreakdown)
            .map(([k, v]) => `${k}(${v})`)
            .join(", ")}.` +
          (p.driftFromPrevious > 0.5 ? ` [HIGH DRIFT from previous]` : ""),
      )
      .join("\n");

    const priorityShifts =
      drift.priorityShifts.length > 0
        ? drift.priorityShifts.map((s) => s.description).join("\n")
        : "No major priority shifts detected.";

    return [
      `USER ID: ${timeline.userId}`,
      `ANALYSIS PERIOD: ${focusN} windows, ${timeline.totalEpisodes} total interactions`,
      ``,
      `RECENT ACTIVITY WINDOWS:`,
      windowSummaries,
      ``,
      `CURRENT FOCUS AREAS: ${drift.currentIdentity.join(", ") || "none identified"}`,
      `PAST FOCUS (faded): ${drift.pastIdentity.join(", ") || "none"}`,
      `OVERALL DRIFT LEVEL: ${drift.driftLevel}`,
      ``,
      `PRIORITY SHIFTS:`,
      priorityShifts,
      ``,
      `DRIFT SUMMARY: ${drift.summary}`,
    ].join("\n");
  }

  private buildPrompt(context: string, type: ReportType, tone: string): string {
    const toneInstructions =
      tone === "warm"
        ? "Be warm, empathetic, and encouraging."
        : tone === "analytical"
          ? "Be precise, data-driven, and objective."
          : "Be honest, direct, and concise.";

    if (type === "digest") {
      return (
        `Here is data about a user's recent activity patterns:\n\n${context}\n\n` +
        `Write a weekly/monthly digest for this user. ${toneInstructions}\n` +
        `Format your response as JSON with these fields:\n` +
        `- "narrative": 2-3 paragraph summary of recent activity and patterns\n` +
        `- "highlights": array of 3-5 bullet point strings (key observations)\n\n` +
        `Return ONLY valid JSON.`
      );
    }

    return (
      `Here is data about a user's activity patterns over time:\n\n${context}\n\n` +
      `Write a reflective "mirror" report — who was this person then vs who are they now? ` +
      `What has evolved? What has stayed constant? ${toneInstructions}\n\n` +
      `Format your response as JSON with these fields:\n` +
      `- "narrative": 3-4 paragraph reflective story written in second person ("you")\n` +
      `- "highlights": array of 3-5 specific insight strings\n\n` +
      `Return ONLY valid JSON.`
    );
  }

  private parseResponse(raw: string): { narrative: string; highlights: string[] } {
    let text = raw.trim();
    if (text.startsWith("```")) {
      text = text.replace(/^```json?/, "").replace(/```$/, "").trim();
    }
    // Strip trailing commas
    text = text.replace(/,\s*([}\]])/g, "$1");

    try {
      const parsed = JSON.parse(text) as { narrative?: string; highlights?: string[] };
      return {
        narrative: parsed.narrative ?? raw,
        highlights: parsed.highlights ?? [],
      };
    } catch {
      return { narrative: raw, highlights: [] };
    }
  }

  private templateNarrative(
    timeline: Timeline,
    drift: DriftReport,
    type: ReportType,
  ): string {
    const current = drift.currentIdentity.slice(0, 3).join(", ") || "various topics";
    const past = drift.pastIdentity.slice(0, 3).join(", ");

    if (type === "digest") {
      return (
        `Over the past period, your conversations have centred around ${current}. ` +
        (drift.driftLevel !== "minor"
          ? `There's been a notable shift in your focus — you're spending more time on new areas. `
          : `Your priorities have remained consistent. `) +
        `You've had ${timeline.totalEpisodes} recorded interactions across this period.`
      );
    }

    let narrative =
      `Looking back at your conversation history, your primary focus has been on ${current}. `;

    if (past) {
      narrative += `Earlier, you spent significant time on ${past}, but those topics have become less prominent. `;
    }

    if (drift.driftLevel === "significant") {
      narrative +=
        `There's been a significant evolution in what you're working on and thinking about. ` +
        `This kind of shift often reflects genuine growth or a major life change.`;
    } else if (drift.driftLevel === "moderate") {
      narrative += `You've been gradually shifting your focus — not a dramatic change, but a clear evolution over time.`;
    } else {
      narrative += `Your focus has been consistent and deliberate — you clearly know what you're working toward.`;
    }

    return narrative;
  }

  private templateHighlights(drift: DriftReport): string[] {
    const highlights: string[] = [];

    if (drift.currentIdentity.length > 0) {
      highlights.push(`Current focus: ${drift.currentIdentity.slice(0, 3).join(", ")}`);
    }
    if (drift.pastIdentity.length > 0) {
      highlights.push(`Faded topics: ${drift.pastIdentity.slice(0, 3).join(", ")}`);
    }
    if (drift.priorityShifts.length > 0) {
      highlights.push(drift.priorityShifts[0].description);
    }
    highlights.push(`Overall drift: ${drift.driftLevel}`);

    return highlights;
  }

  private formatTimeSpan(
    timeline: Timeline,
  ): { from: string; to: string } | null {
    if (!timeline.firstEpisodeDate || !timeline.latestEpisodeDate) return null;
    return {
      from: new Date(timeline.firstEpisodeDate).toLocaleDateString(),
      to: new Date(timeline.latestEpisodeDate).toLocaleDateString(),
    };
  }

  private buildTitle(
    type: ReportType,
    timeSpan: { from: string; to: string } | null,
    drift: DriftReport,
  ): string {
    const period = timeSpan ? `${timeSpan.from} – ${timeSpan.to}` : "All time";
    if (type === "digest") return `Weekly Digest (${period})`;

    const driftLabel =
      drift.driftLevel === "significant"
        ? "Major Evolution"
        : drift.driftLevel === "moderate"
          ? "Gradual Shift"
          : "Consistent Focus";

    return `Who You Were vs Who You Are — ${driftLabel} (${period})`;
  }

  private emptyReport(userId: string, type: ReportType): PersonalityReport {
    return {
      userId,
      generatedAt: Date.now(),
      type,
      title: "Not enough history yet",
      narrative:
        "You don't have enough conversation history yet to generate a report. " +
        "Keep using your AI assistant and check back after a few sessions.",
      highlights: [],
      timeSpan: null,
    };
  }
}
