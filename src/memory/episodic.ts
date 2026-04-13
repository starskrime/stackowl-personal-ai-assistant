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
import type { MemoryDatabase } from "./db.js";

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
  /** Park et al. importance score (0-1). Higher = more significant interaction. */
  importance?: number;
  /** Whether this episode has been compressed (old episodes lose keyFacts/embedding) */
  compressed?: boolean;
  /** Whether this episode is archived (excluded from active search) */
  archived?: boolean;
}

/**
 * Compute importance score for an episode (0-1).
 * Based on Park et al. heuristics for memory significance.
 */
function computeImportance(
  userMessageCount: number,
  hasDecision: boolean,
  hasCommitment: boolean,
  sentiment: string,
  totalMessageCount: number,
): number {
  let score = 0.3; // Baseline

  // Decisions and commitments are high-importance
  if (hasDecision) score += 0.2;
  if (hasCommitment) score += 0.2;

  // Multi-turn deep discussion
  if (totalMessageCount >= 10) score += 0.15;
  else if (totalMessageCount >= 5) score += 0.1;

  // Strong sentiment (frustrated or happy) is more memorable
  if (sentiment === "frustrated" || sentiment === "happy") score += 0.1;

  // Very short interactions are less important
  if (userMessageCount <= 1) score -= 0.1;

  return Math.max(0.1, Math.min(1.0, score));
}

export class EpisodicMemory {
  private episodes: Map<string, Episode> = new Map();
  private filePath: string;
  private loaded = false;
  private provider?: ModelProvider;
  private db?: MemoryDatabase;

  constructor(workspacePath: string, provider?: ModelProvider, db?: MemoryDatabase) {
    this.filePath = join(workspacePath, "memory", "episodes.json");
    this.provider = provider;
    this.db = db;
  }

  async load(): Promise<void> {
    if (this.loaded) return;
    try {
      if (this.db) {
        // Load from SQLite DB
        const dbEpisodes = this.db.episodes.getAll();
        for (const ep of dbEpisodes) {
          // Map db.Episode → local Episode (compatible shapes, extra fields default)
          this.episodes.set(ep.id, {
            id: ep.id,
            sessionId: ep.sessionId,
            owlName: ep.owlName,
            date: new Date(ep.createdAt).getTime(),
            summary: ep.summary,
            keyFacts: ep.keyFacts,
            topics: ep.topics,
            sentiment: ep.sentiment as Episode["sentiment"],
            userMessageCount: 0,
            embedding: ep.embedding,
            importance: ep.importance,
          });
        }
        log.engine.info(`[EpisodicMemory] Loaded ${this.episodes.size} episodes from SQLite`);
      } else if (existsSync(this.filePath)) {
        const data = await readFile(this.filePath, "utf-8");
        const parsed = JSON.parse(data) as Episode[];
        for (const ep of parsed) {
          this.episodes.set(ep.id, ep);
        }
        log.engine.info(`[EpisodicMemory] Loaded ${this.episodes.size} episodes from JSON`);
      }
    } catch (err) {
      log.engine.warn(
        `[EpisodicMemory] Failed to load: ${err instanceof Error ? err.message : err}`,
      );
    }
    this.loaded = true;

    // Run decay on load to compress/archive old episodes
    if (this.episodes.size > 0) {
      this.runDecay();
    }
  }

  async save(): Promise<void> {
    if (this.db) {
      // Write to SQLite (upsert all in-memory episodes)
      for (const ep of this.episodes.values()) {
        this.db.episodes.upsert({
          id: ep.id,
          sessionId: ep.sessionId ?? "unknown",
          userId: "default",
          owlName: ep.owlName,
          summary: ep.summary,
          keyFacts: ep.keyFacts,
          topics: ep.topics,
          sentiment: ep.sentiment ?? "neutral",
          importance: ep.importance ?? 0.5,
          embedding: ep.embedding,
          createdAt: ep.date ? new Date(ep.date).toISOString() : new Date().toISOString(),
        });
      }
      log.engine.debug(`[EpisodicMemory] Synced ${this.episodes.size} episodes to SQLite`);
      return;
    }

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

  // ─── Phase 3: Segment-Based Extraction ──────────────────────────

  /**
   * Extract an episode from a message slice (segment).
   * Unlike extractFromSession(), this takes raw messages + metadata
   * so it can be called for mid-session segments.
   */
  async extractFromMessages(
    messages: Array<{ role: string; content: string }>,
    sessionId: string,
    owlName: string,
    provider: ModelProvider,
  ): Promise<Episode | null> {
    if (messages.length < 2) return null;

    const userMessages = messages
      .filter((m) => m.role === "user")
      .map((m) => m.content);

    if (userMessages.length === 0) return null;

    try {
      const systemPrompt = `You are an episodic memory extractor for a personal AI assistant.
Given a conversation transcript, extract a narrative episode that captures:
- What the user was trying to accomplish (not just "what they asked")
- Whether commitments were made
- The emotional tone of the interaction
- Whether this seems like an ongoing project or a one-off question

Return a JSON object:
- summary: 1-3 sentence narrative summary (write as if describing to a colleague what happened)
- keyFacts: array of 2-5 specific facts (decisions made, information shared, tools used)
- topics: array of topic tags
- sentiment: "positive" | "neutral" | "frustrated" | "happy"
- hasDecision: boolean (was something decided?)
- hasCommitment: boolean (did the assistant promise to do something?)

Return ONLY a valid JSON object, no markdown.`;

      const transcript = messages
        .map((m) => `${m.role}: ${m.content.slice(0, 300)}`)
        .slice(-20) // Cap at last 20 messages
        .join("\n---\n");

      const response = await provider.chat(
        [
          { role: "system", content: systemPrompt },
          { role: "user", content: `Conversation:\n${transcript}` },
        ],
        undefined,
        { temperature: 0.3, maxTokens: 500 },
      );

      const text = response.content.trim();
      const match = text.match(/\{[\s\S]*\}/);
      if (!match) return null;

      const parsed = JSON.parse(match[0]) as {
        summary?: string;
        keyFacts?: string[];
        topics?: string[];
        sentiment?: string;
        hasDecision?: boolean;
        hasCommitment?: boolean;
      };

      if (!parsed.summary) return null;

      // Compute importance score
      const importance = computeImportance(
        userMessages.length,
        parsed.hasDecision ?? false,
        parsed.hasCommitment ?? false,
        parsed.sentiment ?? "neutral",
        messages.length,
      );

      const episode: Episode = {
        id: `ep_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
        sessionId,
        owlName,
        date: Date.now(),
        summary: parsed.summary,
        keyFacts: parsed.keyFacts ?? [],
        topics: parsed.topics ?? [],
        sentiment: (parsed.sentiment as Episode["sentiment"]) ?? "neutral",
        userMessageCount: userMessages.length,
        importance,
      };

      // Try to compute embedding
      try {
        const embedResp = await provider.embed(parsed.summary);
        if (embedResp.embedding?.length) {
          episode.embedding = embedResp.embedding;
        }
      } catch {
        // Embedding is optional
      }

      this.episodes.set(episode.id, episode);
      await this.save();
      log.engine.info(
        `[EpisodicMemory] Extracted segment episode (importance=${importance.toFixed(2)}): "${episode.summary.slice(0, 60)}"`,
      );
      return episode;
    } catch (err) {
      log.engine.warn(
        `[EpisodicMemory] Segment extraction failed: ${err instanceof Error ? err.message : err}`,
      );
      // Save a minimal episode with metadata only (no LLM)
      return this.saveMinimalEpisode(messages, sessionId, owlName);
    }
  }

  /**
   * Fallback: save a minimal episode when LLM extraction fails.
   * Uses TF-IDF-style keyword extraction instead.
   */
  private saveMinimalEpisode(
    messages: Array<{ role: string; content: string }>,
    sessionId: string,
    owlName: string,
  ): Episode {
    const userMessages = messages
      .filter((m) => m.role === "user")
      .map((m) => m.content);

    // Simple topic extraction from frequent words
    const text = userMessages.join(" ").toLowerCase();
    const words = text.match(/\b[a-z]{4,}\b/g) ?? [];
    const STOPWORDS = new Set([
      "this", "that", "with", "from", "have", "been", "they", "were",
      "will", "would", "could", "should", "about", "your", "what",
      "when", "where", "which", "there", "their", "some", "just",
      "like", "more", "also", "than", "them", "very", "into",
      "want", "need", "help", "please", "know", "think", "make",
    ]);
    const freq = new Map<string, number>();
    for (const w of words) {
      if (!STOPWORDS.has(w)) freq.set(w, (freq.get(w) ?? 0) + 1);
    }
    const topics = [...freq.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([w]) => w);

    const episode: Episode = {
      id: `ep_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      sessionId,
      owlName,
      date: Date.now(),
      summary: `User discussed: ${topics.join(", ") || "various topics"} (${userMessages.length} messages)`,
      keyFacts: [],
      topics,
      sentiment: "neutral",
      userMessageCount: userMessages.length,
      importance: 0.3, // Low importance for minimal episodes
    };

    this.episodes.set(episode.id, episode);
    this.save().catch(() => {});
    log.engine.info(
      `[EpisodicMemory] Saved minimal episode (LLM unavailable): "${episode.summary.slice(0, 60)}"`,
    );
    return episode;
  }

  // ─── Phase 3: Park et al. Retrieval Scoring ─────────────────────

  /**
   * Search episodes using Park et al. retrieval scoring:
   *   score = recency_decay × importance × relevance
   *
   * Falls back to keyword search when no embeddings available.
   */
  async searchWithScoring(
    query: string,
    limit = 5,
    provider?: ModelProvider,
    threshold = 0.3,
  ): Promise<Array<Episode & { retrievalScore: number }>> {
    const now = Date.now();
    const episodes = [...this.episodes.values()].filter(
      (ep) => !ep.archived,
    );
    if (episodes.length === 0) return [];

    const lower = query.toLowerCase();

    // Compute keyword relevance for all episodes
    const keywordScores = new Map<string, number>();
    for (const ep of episodes) {
      let score = 0;
      if (ep.summary.toLowerCase().includes(lower)) score += 0.5;
      if (ep.topics.some((t) => t.toLowerCase().includes(lower))) score += 0.3;
      if (ep.keyFacts.some((f) => f.toLowerCase().includes(lower))) score += 0.2;
      // Partial word matching
      const queryWords = lower.split(/\s+/).filter((w) => w.length >= 3);
      for (const word of queryWords) {
        if (ep.summary.toLowerCase().includes(word)) score += 0.1;
        if (ep.topics.some((t) => t.toLowerCase().includes(word))) score += 0.1;
      }
      keywordScores.set(ep.id, Math.min(score, 1.0));
    }

    // Try to get semantic scores via embedding
    let queryEmbedding: number[] = [];
    if (provider) {
      try {
        const resp = await provider.embed(query);
        queryEmbedding = resp.embedding ?? [];
      } catch {
        // Fall through to keyword-only
      }
    }

    // Score each episode
    const scored = episodes.map((ep) => {
      const hoursSince = (now - ep.date) / (1000 * 60 * 60);
      const recency = Math.pow(0.99, hoursSince);
      const importance = ep.importance ?? 0.5;

      let relevance = keywordScores.get(ep.id) ?? 0;
      if (queryEmbedding.length > 0 && ep.embedding?.length) {
        const cosSim = this.cosineSimilarity(queryEmbedding, ep.embedding);
        relevance = Math.max(relevance, cosSim);
      }

      const retrievalScore = recency + importance + relevance;
      return { ...ep, retrievalScore };
    });

    return scored
      .filter((ep) => ep.retrievalScore >= threshold)
      .sort((a, b) => b.retrievalScore - a.retrievalScore)
      .slice(0, limit);
  }

  // ─── Semantic Clustering ────────────────────────────────────────

  /**
   * Fetch the top thematic threads (clusters) from active memory.
   * Clusters recent episodes by topic importance to provide an overarching narrative.
   */
  getThematicThreads(limit = 3): string[] {
    const recent = this.getRecent(50).filter(ep => !ep.archived);
    if (!recent.length) return [];
    
    // Cluster by topic, weighted by episode importance and recency
    const topicScores = new Map<string, number>();
    const now = Date.now();
    for (const ep of recent) {
      const daysOld = (now - ep.date) / (1000 * 60 * 60 * 24);
      const recencyWeight = Math.max(0.1, 1 - (daysOld * 0.05)); // Decays slowly over 20 days
      const score = (ep.importance ?? 0.5) * recencyWeight;
      
      for (const t of ep.topics) {
        topicScores.set(t, (topicScores.get(t) ?? 0) + score);
      }
    }

    const topTopics = [...topicScores.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit)
      .map(([t]) => t);
      
    // Find representative summaries for the top topics
    const threads: string[] = [];
    for (const t of topTopics) {
      const epsForTopic = recent.filter(ep => ep.topics.includes(t));
      // Sort to get the most important/recent episode for the topic
      epsForTopic.sort((a, b) => (b.importance ?? 0) - (a.importance ?? 0));
      if (epsForTopic[0]) {
        threads.push(`[Topic: ${t.toUpperCase()}] ${epsForTopic[0].summary}`);
      }
    }
    
    return threads;
  }

  // ─── Phase 3: Episode Decay ─────────────────────────────────────

  /**
   * Run decay on old episodes:
   *   - 30+ days old with importance < 0.5: compress (drop keyFacts + embedding)
   *   - 90+ days old with importance < 0.3: archive (excluded from search)
   *
   * Call this on load() or periodically.
   */
  runDecay(): { compressed: number; archived: number } {
    const now = Date.now();
    const DAY_MS = 24 * 60 * 60 * 1000;
    let compressed = 0;
    let archived = 0;

    for (const ep of this.episodes.values()) {
      const ageDays = (now - ep.date) / DAY_MS;

      if (ageDays > 90 && (ep.importance ?? 0.5) < 0.3 && !ep.archived) {
        ep.archived = true;
        ep.compressed = true;
        ep.keyFacts = [];
        ep.embedding = undefined;
        archived++;
      } else if (ageDays > 30 && (ep.importance ?? 0.5) < 0.5 && !ep.compressed) {
        ep.compressed = true;
        ep.keyFacts = [];
        ep.embedding = undefined;
        compressed++;
      }
    }

    if (compressed > 0 || archived > 0) {
      log.engine.info(
        `[EpisodicMemory] Decay: ${compressed} compressed, ${archived} archived`,
      );
      this.save().catch(() => {});
    }

    return { compressed, archived };
  }
}
