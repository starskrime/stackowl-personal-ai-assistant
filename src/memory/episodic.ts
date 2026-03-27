/**
 * StackOwl — Episodic Memory
 *
 * Layer 2 of the memory hierarchy: episodic memory.
 * Extracts and stores facts from completed sessions so the owl
 * can reference past interactions naturally.
 *
 * "On March 15, user asked about booking a meeting room"
 * "User seemed frustrated about X last week"
 *
 * These facts are stored separately from the raw session history
 * and injected into context when relevant.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ModelProvider } from "../providers/base.js";
import type { Session } from "./store.js";
import { log } from "../logger.js";

export interface Episode {
  id: string;
  sessionId: string;
  owlName: string;
  date: number;
  summary: string;
  keyFacts: string[];
  topics: string[];
  sentiment?: "positive" | "neutral" | "frustrated" | "happy";
  userMessageCount: number;
  embedding?: number[];
}

export class EpisodicMemory {
  private episodes: Map<string, Episode> = new Map();
  private filePath: string;
  private loaded = false;
  private provider?: ModelProvider;

  constructor(workspacePath: string, provider?: ModelProvider) {
    this.filePath = join(workspacePath, "memory", "episodes.json");
    this.provider = provider;
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data) as Episode[];
        for (const ep of parsed) {
          this.episodes.set(ep.id, ep);
        }
        log.engine.info(
          `[EpisodicMemory] Loaded ${this.episodes.size} episodes`,
        );
      }
    } catch (err) {
      log.engine.warn(
        `[EpisodicMemory] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;
  }

  async save(): Promise<void> {
    const dir = join(this.filePath, "..");
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(
      this.filePath,
      JSON.stringify([...this.episodes.values()], null, 2),
      "utf-8",
    );
  }

  /**
   * Extract an episode from a completed session.
   * Called by endSession or session save.
   */
  async extractFromSession(
    session: Session,
    provider: ModelProvider,
  ): Promise<Episode | null> {
    if (session.messages.length < 2) return null;

    const userMessages = session.messages
      .filter((m) => m.role === "user")
      .map((m) => m.content);

    if (userMessages.length === 0) return null;

    const tryExtract = async (): Promise<Episode | null> => {
      try {
        const systemPrompt = `You are an episodic memory extractor for a personal AI assistant.
Given a conversation transcript, extract key facts about what the user wanted and what happened.
Return a JSON object with these fields:
- summary: 1-2 sentence summary of what the user was trying to do
- keyFacts: array of 2-5 specific facts extracted (e.g. "Meeting room booked for tomorrow 9am", "User prefers Chinese responses")
- topics: array of topic tags (e.g. "meeting", "booking", "spanish-learning")
- sentiment: one of "positive", "neutral", "frustrated", "happy" based on the overall tone

Return ONLY a valid JSON object, no markdown or explanation.`;

        const response = await provider.chat(
          [
            { role: "system", content: systemPrompt },
            {
              role: "user",
              content: `Conversation:\n${userMessages.join("\n---\n")}`,
            },
          ],
          undefined,
          { temperature: 0.3, maxTokens: 400 },
        );

        const text = response.content.trim();
        const match = text.match(/\{[\s\S]*\}/);
        if (!match) return null;

        const parsed = JSON.parse(match[0]) as {
          summary?: string;
          keyFacts?: string[];
          topics?: string[];
          sentiment?: string;
        };

        if (!parsed.summary) return null;

        const episode: Episode = {
          id: `ep_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          sessionId: session.id,
          owlName: session.metadata.owlName,
          date: session.metadata.lastUpdatedAt,
          summary: parsed.summary,
          keyFacts: parsed.keyFacts ?? [],
          topics: parsed.topics ?? [],
          sentiment: (parsed.sentiment as Episode["sentiment"]) ?? "neutral",
          userMessageCount: userMessages.length,
        };

        try {
          const embedResp = await provider.embed(parsed.summary);
          if (embedResp.embedding?.length) {
            episode.embedding = embedResp.embedding;
          }
        } catch {
          // Embedding is optional — episode still gets stored
        }

        this.episodes.set(episode.id, episode);
        await this.save();
        log.engine.info(
          `[EpisodicMemory] Extracted episode: "${episode.summary.slice(0, 50)}"`,
        );
        return episode;
      } catch (err) {
        log.engine.warn(
          `[EpisodicMemory] LLM extraction failed: ${err instanceof Error ? err.message : err}`,
        );
        return null;
      }
    };

    return tryExtract();
  }

  /**
   * Search episodes by query using hybrid semantic + keyword search.
   * Uses embeddings when available, falls back to pure keyword matching.
   */
  async search(
    query: string,
    limit = 5,
    provider?: ModelProvider,
  ): Promise<Episode[]> {
    const episodes = [...this.episodes.values()];
    if (episodes.length === 0) return [];

    const lower = query.toLowerCase();

    const keywordMatches = episodes.filter(
      (ep) =>
        ep.summary.toLowerCase().includes(lower) ||
        ep.topics.some((t) => t.toLowerCase().includes(lower)) ||
        ep.keyFacts.some((f) => f.toLowerCase().includes(lower)),
    );

    if (!provider) {
      return keywordMatches.slice(0, limit);
    }

    const episodesWithEmbed = episodes.filter((ep) => ep.embedding?.length);
    if (episodesWithEmbed.length === 0) {
      return keywordMatches.slice(0, limit);
    }

    let queryEmbedding: number[] = [];
    try {
      const resp = await provider.embed(query);
      queryEmbedding = resp.embedding ?? [];
    } catch {
      return keywordMatches.slice(0, limit);
    }

    if (queryEmbedding.length === 0) {
      return keywordMatches.slice(0, limit);
    }

    const scored = episodes.map((ep) => {
      if (ep.embedding?.length) {
        const sim = this.cosineSimilarity(queryEmbedding, ep.embedding);
        const kwBoost = keywordMatches.includes(ep) ? 0.15 : 0;
        return { episode: ep, score: sim + kwBoost };
      }
      const kwScore = keywordMatches.includes(ep) ? 0.3 : 0;
      return { episode: ep, score: kwScore };
    });

    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map((r) => r.episode);
  }

  private cosineSimilarity(a: number[], b: number[]): number {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    const denom = Math.sqrt(normA) * Math.sqrt(normB);
    return denom === 0 ? 0 : dot / denom;
  }

  /**
   * Get recent episodes.
   */
  getRecent(limit = 10): Episode[] {
    return [...this.episodes.values()]
      .sort((a, b) => b.date - a.date)
      .slice(0, limit);
  }

  /**
   * Get episodes by topic.
   */
  getByTopic(topic: string): Episode[] {
    const lower = topic.toLowerCase();
    return [...this.episodes.values()]
      .filter((ep) => ep.topics.some((t) => t.toLowerCase().includes(lower)))
      .sort((a, b) => b.date - a.date);
  }

  /**
   * Format relevant episodes as context string for system prompt.
   */
  async toContextString(query: string, maxEpisodes = 3): Promise<string> {
    const relevant = await this.search(query, maxEpisodes, this.provider);
    if (relevant.length === 0) return "";

    const lines = relevant.map(
      (ep) =>
        `[${new Date(ep.date).toLocaleDateString()}] ${ep.summary}` +
        (ep.keyFacts.length > 0
          ? ` | Facts: ${ep.keyFacts.slice(0, 2).join("; ")}`
          : ""),
    );
    return `<episodic_memory>\n  ${lines.join("\n  ")}\n</episodic_memory>`;
  }

  getStats(): { total: number; topics: Record<string, number> } {
    const topics: Record<string, number> = {};
    for (const ep of this.episodes.values()) {
      for (const t of ep.topics) {
        topics[t] = (topics[t] ?? 0) + 1;
      }
    }
    return { total: this.episodes.size, topics };
  }
}
