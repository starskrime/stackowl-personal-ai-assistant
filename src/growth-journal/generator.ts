/**
 * StackOwl — Growth Journal Generator
 *
 * Auto-generates periodic growth journals from pellets, sessions,
 * DNA evolution, and micro-learner signals.
 */

import type { ModelProvider } from "../providers/base.js";
import type { PelletStore } from "../pellets/store.js";
import type { SessionStore } from "../memory/store.js";
import type { JournalEntry, GrowthMetrics } from "./types.js";
import { join } from "node:path";
import { readFile, writeFile, readdir, mkdir } from "node:fs/promises";
import { existsSync, mkdirSync } from "node:fs";
import { log } from "../logger.js";

export class JournalGenerator {
  private pelletStore: PelletStore;
  private sessionStore: SessionStore;
  private provider: ModelProvider;
  private journalDir: string;

  constructor(
    pelletStore: PelletStore,
    sessionStore: SessionStore,
    provider: ModelProvider,
    workspacePath: string,
  ) {
    this.pelletStore = pelletStore;
    this.sessionStore = sessionStore;
    this.provider = provider;
    this.journalDir = join(workspacePath, "journal");
    if (!existsSync(this.journalDir))
      mkdirSync(this.journalDir, { recursive: true });
  }

  /**
   * Generate a journal entry for a given period.
   */
  async generate(period: "weekly" | "monthly"): Promise<JournalEntry> {
    const now = new Date();
    const startDate = new Date(now);

    if (period === "weekly") {
      startDate.setDate(now.getDate() - 7);
    } else {
      startDate.setMonth(now.getMonth() - 1);
    }

    // Gather data
    const metrics = await this.getMetrics(startDate, now);
    const pellets = await this.getPelletsInRange(startDate, now);
    const sessions = await this.getSessionsInRange(startDate, now);

    // Build LLM prompt with gathered data
    const dataContext = this.buildDataContext(metrics, pellets, sessions);

    const narrative = await this.generateNarrative(
      period,
      startDate,
      now,
      dataContext,
    );

    const entry: JournalEntry = {
      id: `journal_${period}_${now.toISOString().slice(0, 10)}`,
      period,
      startDate: startDate.toISOString(),
      endDate: now.toISOString(),
      sections: {
        skillsAcquired: metrics.toolsLearned,
        beliefsChanged: [],
        patternsRecognized: [],
        highlights: [],
        metrics,
      },
      narrative,
      generatedAt: now.toISOString(),
    };

    await this.save(entry);
    return entry;
  }

  /**
   * List all journal entries.
   */
  async list(): Promise<JournalEntry[]> {
    if (!existsSync(this.journalDir)) return [];
    const files = await readdir(this.journalDir);
    const entries: JournalEntry[] = [];

    for (const file of files) {
      if (!file.endsWith(".json")) continue;
      try {
        const data = await readFile(join(this.journalDir, file), "utf-8");
        entries.push(JSON.parse(data));
      } catch {
        /* skip corrupt files */
      }
    }

    return entries.sort(
      (a, b) =>
        new Date(b.generatedAt).getTime() - new Date(a.generatedAt).getTime(),
    );
  }

  /**
   * Get a specific journal entry.
   */
  async get(id: string): Promise<JournalEntry | null> {
    const path = join(this.journalDir, `${id}.json`);
    if (!existsSync(path)) return null;
    try {
      const data = await readFile(path, "utf-8");
      return JSON.parse(data);
    } catch {
      return null;
    }
  }

  /**
   * Search journal entries by keyword.
   */
  async search(query: string): Promise<JournalEntry[]> {
    const all = await this.list();
    const q = query.toLowerCase();
    return all.filter(
      (e) =>
        e.narrative.toLowerCase().includes(q) ||
        e.sections.skillsAcquired.some((s) => s.toLowerCase().includes(q)) ||
        e.sections.highlights.some((h) => h.toLowerCase().includes(q)),
    );
  }

  // ─── Private ─────────────────────────────────────────────

  private async getMetrics(start: Date, end: Date): Promise<GrowthMetrics> {
    const allPellets = await this.pelletStore.listAll();
    const pelletsInRange = allPellets.filter((p) => {
      const d = new Date(p.generatedAt);
      return d >= start && d <= end;
    });

    const sessions = await this.sessionStore.listSessions();
    const sessionsInRange = sessions.filter(
      (s) =>
        s.metadata.startedAt >= start.getTime() &&
        s.metadata.startedAt <= end.getTime(),
    );

    const topics = new Set<string>();
    const tools = new Set<string>();

    for (const p of pelletsInRange) {
      p.tags.forEach((t) => topics.add(t));
    }

    for (const s of sessionsInRange) {
      for (const m of s.messages) {
        if (m.role === "assistant" && m.content.includes("Running:")) {
          const toolMatch = m.content.match(/Running:\s+(\w+)/);
          if (toolMatch) tools.add(toolMatch[1]);
        }
      }
    }

    const parliamentCount = pelletsInRange.filter(
      (p) =>
        p.source?.includes("Parliament") || p.source?.includes("parliament"),
    ).length;

    const avgLength =
      sessionsInRange.length > 0
        ? sessionsInRange.reduce((sum, s) => sum + s.messages.length, 0) /
          sessionsInRange.length
        : 0;

    return {
      pelletsCreated: pelletsInRange.length,
      sessionsCount: sessionsInRange.length,
      topicsExplored: [...topics].slice(0, 10),
      toolsLearned: [...tools].slice(0, 10),
      parliamentSessions: parliamentCount,
      averageSessionLength: Math.round(avgLength),
    };
  }

  private async getPelletsInRange(
    start: Date,
    end: Date,
  ): Promise<Array<{ title: string; tags: string[] }>> {
    const all = await this.pelletStore.listAll();
    return all
      .filter((p) => {
        const d = new Date(p.generatedAt);
        return d >= start && d <= end;
      })
      .map((p) => ({ title: p.title, tags: p.tags }));
  }

  private async getSessionsInRange(
    start: Date,
    end: Date,
  ): Promise<Array<{ summary: string; date: string }>> {
    const sessions = await this.sessionStore.listSessions();
    return sessions
      .filter(
        (s) =>
          s.metadata.startedAt >= start.getTime() &&
          s.metadata.startedAt <= end.getTime(),
      )
      .slice(0, 10)
      .map((s) => {
        const userMsgs = s.messages
          .filter((m) => m.role === "user")
          .map((m) => m.content.slice(0, 100));
        return {
          summary: userMsgs.join(" | ").slice(0, 300),
          date: new Date(s.metadata.startedAt).toLocaleDateString(),
        };
      });
  }

  private buildDataContext(
    metrics: GrowthMetrics,
    pellets: Array<{ title: string; tags: string[] }>,
    sessions: Array<{ summary: string; date: string }>,
  ): string {
    const parts: string[] = [];

    parts.push(
      `Metrics: ${metrics.pelletsCreated} pellets, ${metrics.sessionsCount} sessions, ${metrics.parliamentSessions} parliament debates`,
    );
    parts.push(
      `Topics explored: ${metrics.topicsExplored.join(", ") || "none"}`,
    );
    parts.push(`Tools used: ${metrics.toolsLearned.join(", ") || "none"}`);

    if (pellets.length > 0) {
      parts.push(
        `\nKnowledge created:\n${pellets.map((p) => `- ${p.title} [${p.tags.join(", ")}]`).join("\n")}`,
      );
    }

    if (sessions.length > 0) {
      parts.push(
        `\nConversation highlights:\n${sessions.map((s) => `- ${s.date}: ${s.summary}`).join("\n")}`,
      );
    }

    return parts.join("\n");
  }

  private async generateNarrative(
    period: string,
    start: Date,
    end: Date,
    dataContext: string,
  ): Promise<string> {
    try {
      const response = await this.provider.chat(
        [
          {
            role: "user",
            content:
              `Write a ${period} growth journal entry for the period ${start.toLocaleDateString()} to ${end.toLocaleDateString()}.\n\n` +
              `Data:\n${dataContext}\n\n` +
              `Write in second person ("You did...", "You learned..."). Include:\n` +
              `1. A brief overview of the period\n` +
              `2. Key skills acquired or deepened\n` +
              `3. Patterns noticed (what they spent time on, how they grew)\n` +
              `4. Highlights or breakthroughs\n` +
              `5. Suggested focus for next period\n\n` +
              `Keep it warm, encouraging, and honest. 200-400 words. Use markdown formatting.`,
          },
        ],
        undefined,
        { temperature: 0.4, maxTokens: 600 },
      );

      return response.content.trim();
    } catch (err) {
      log.engine.debug(`[GrowthJournal] Narrative generation failed: ${err}`);
      return `Journal generation encountered an error. Here are the raw metrics:\n${dataContext}`;
    }
  }

  private async save(entry: JournalEntry): Promise<void> {
    if (!existsSync(this.journalDir))
      await mkdir(this.journalDir, { recursive: true });
    await writeFile(
      join(this.journalDir, `${entry.id}.json`),
      JSON.stringify(entry, null, 2),
    );
    log.engine.info(`[GrowthJournal] Saved: ${entry.id}`);
  }
}
