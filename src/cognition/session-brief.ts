/**
 * StackOwl — Session Opening Brief
 *
 * When the user returns after an absence (FRESH_START detected by
 * ContinuityEngine), the owl generates a personalised opening brief
 * so the user doesn't have to re-orient themselves or re-explain context.
 *
 * The brief is:
 *   1. What you were working on last time (from episodic memory)
 *   2. Active projects / goals (from ground state + facts)
 *   3. Things on your radar — high-intensity desires + open questions
 *
 * Output is injected BEFORE the owl answers the user's first message,
 * so they arrive to context rather than to a blank slate.
 *
 * Architecture: pure generator — no side effects, no storage.
 * Called once per FRESH_START, result prepended to the first response.
 */

import type { ModelProvider, ChatMessage } from "../providers/base.js";
import type { EpisodicMemory } from "../memory/episodic.js";
import type { GroundStateView } from "./ground-state.js";
import type { OwlInnerLife } from "../owls/inner-life.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface SessionBrief {
  lastSessionSummary: string | null;
  activeProjects: string[];
  radarItems: string[];
  /** Ready-to-prepend formatted string */
  formatted: string;
}

// ─── SessionBriefGenerator ────────────────────────────────────────

export class SessionBriefGenerator {
  private readonly TIMEOUT_MS = 8_000;

  constructor(private provider: ModelProvider) {}

  /**
   * Generate a brief for a returning user.
   * Returns null if there isn't enough context to say anything useful.
   */
  async generate(opts: {
    owlName: string;
    episodicMemory?: EpisodicMemory;
    groundState?: GroundStateView;
    innerLife?: OwlInnerLife;
    userId?: string;
  }): Promise<SessionBrief | null> {
    const { owlName, episodicMemory, groundState, innerLife, userId: _userId } = opts;

    // ── Gather raw materials (parallel, non-blocking) ──────────
    const [lastEpisode, groundContext, innerState] = await Promise.all([
      this.fetchLastEpisode(episodicMemory),
      Promise.resolve(groundState && _userId ? groundState.getState(_userId) : null),
      Promise.resolve(innerLife?.getState() ?? null),
    ]);

    // Nothing to say — skip entirely
    if (!lastEpisode && !groundContext && !innerState) return null;

    // ── Build raw content blocks ───────────────────────────────
    const lastSessionSummary = lastEpisode?.summary ?? null;

    const activeProjects: string[] = [];
    if (groundContext) {
      for (const g of groundContext.activeGoals ?? []) {
        if (g.fact) activeProjects.push(g.fact);
      }
      for (const d of groundContext.decisions ?? []) {
        if (d.fact) activeProjects.push(d.fact);
      }
    }

    const radarItems: string[] = [];
    if (innerState) {
      // Top-intensity desires
      const topDesires = [...(innerState.desires ?? [])]
        .sort((a, b) => b.intensity - a.intensity)
        .slice(0, 2)
        .map((d) => d.description);
      radarItems.push(...topDesires);

      // Open questions from inner state
      for (const q of (innerState.currentThoughts ?? []).slice(0, 2)) {
        radarItems.push(q);
      }
    }

    if (!lastSessionSummary && activeProjects.length === 0 && radarItems.length === 0) {
      return null;
    }

    // ── Generate natural-language brief via LLM ────────────────
    const formatted = await this.format({
      owlName,
      lastSessionSummary,
      activeProjects: activeProjects.slice(0, 3),
      radarItems: radarItems.slice(0, 3),
    });

    if (!formatted) return null;

    log.engine.info(`[SessionBrief] Generated for ${owlName}`);

    return {
      lastSessionSummary,
      activeProjects,
      radarItems,
      formatted,
    };
  }

  // ─── Private ─────────────────────────────────────────────────

  private async fetchLastEpisode(
    episodicMemory?: EpisodicMemory,
  ) {
    if (!episodicMemory) return null;
    try {
      // Search with a broad query to get the most recent episode
      const episodes = await episodicMemory.searchWithScoring(
        "recent session work",
        1,
        undefined,
        0.0, // no threshold — just get the most recent
      );
      return episodes[0] ?? null;
    } catch {
      return null;
    }
  }

  private async format(data: {
    owlName: string;
    lastSessionSummary: string | null;
    activeProjects: string[];
    radarItems: string[];
  }): Promise<string | null> {
    const { owlName, lastSessionSummary, activeProjects, radarItems } = data;

    const contextLines: string[] = [];
    if (lastSessionSummary) {
      contextLines.push(`Last session: ${lastSessionSummary}`);
    }
    if (activeProjects.length > 0) {
      contextLines.push(`Active projects/goals:\n${activeProjects.map((p) => `- ${p}`).join("\n")}`);
    }
    if (radarItems.length > 0) {
      contextLines.push(`On your radar:\n${radarItems.map((r) => `- ${r}`).join("\n")}`);
    }

    const messages: ChatMessage[] = [
      {
        role: "system",
        content:
          `You are ${owlName}. The user is returning after an absence. ` +
          `Write a SHORT, warm opening brief — 2-4 sentences max. ` +
          `Sound like you actually remembered them, not like you're reading a database. ` +
          `Be specific about what they were working on. No filler phrases like "Welcome back!". ` +
          `Do not add bullet points or headers. Plain prose only.`,
      },
      {
        role: "user",
        content: `Context about the user:\n\n${contextLines.join("\n\n")}\n\nWrite the opening brief.`,
      },
    ];

    try {
      const result = await Promise.race([
        this.provider.chat(messages),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error("brief timeout")), this.TIMEOUT_MS),
        ),
      ]);
      const text = result.content.trim();
      return text.length > 20 ? text : null;
    } catch {
      // Fallback: simple structured brief without LLM polish
      if (!lastSessionSummary) return null;
      return `Picking up where we left off — ${lastSessionSummary.slice(0, 120)}.`;
    }
  }
}
