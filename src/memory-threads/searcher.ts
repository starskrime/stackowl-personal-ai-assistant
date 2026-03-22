/**
 * StackOwl — Memory Searcher
 *
 * Searches across pellets, sessions, and memory for conversational recall.
 * Reconstructs narrative threads from scattered knowledge.
 */

import type { ModelProvider } from '../providers/base.js';
import type { PelletStore } from '../pellets/store.js';
import type { SessionStore } from '../memory/store.js';
import type { MemoryThread, ThreadEntry, SessionIndex } from './types.js';
import { join } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { log } from '../logger.js';

export class MemorySearcher {
  private pelletStore: PelletStore;
  private sessionStore: SessionStore;
  private workspacePath: string;
  private provider: ModelProvider;
  private sessionIndex: SessionIndex | null = null;

  constructor(
    pelletStore: PelletStore,
    sessionStore: SessionStore,
    workspacePath: string,
    provider: ModelProvider,
  ) {
    this.pelletStore = pelletStore;
    this.sessionStore = sessionStore;
    this.workspacePath = workspacePath;
    this.provider = provider;
  }

  /**
   * Main recall entry point. Searches all sources and reconstructs a narrative thread.
   */
  async recall(query: string, scope: 'all' | 'pellets' | 'sessions' = 'all'): Promise<MemoryThread> {
    const entries: ThreadEntry[] = [];
    const relatedPellets: string[] = [];
    const relatedSessions: string[] = [];

    // 1. Search pellets via BM25
    if (scope === 'all' || scope === 'pellets') {
      try {
        const pelletResults = await this.pelletStore.search(query);
        for (const pellet of pelletResults.slice(0, 5)) {
          relatedPellets.push(pellet.id);
          entries.push({
            timestamp: pellet.generatedAt || new Date().toISOString(),
            source: 'pellet',
            sourceId: pellet.id,
            excerpt: `**${pellet.title}** (tags: ${pellet.tags.join(', ')})\n${pellet.content.slice(0, 300)}`,
            relevanceScore: 1.0,
          });
        }
      } catch (err) {
        log.engine.debug(`[MemorySearcher] Pellet search error: ${err}`);
      }
    }

    // 2. Search sessions via keyword scan
    if (scope === 'all' || scope === 'sessions') {
      try {
        const sessionMatches = await this.searchSessions(query, 5);
        for (const match of sessionMatches) {
          relatedSessions.push(match.sourceId);
          entries.push(match);
        }
      } catch (err) {
        log.engine.debug(`[MemorySearcher] Session search error: ${err}`);
      }
    }

    // 3. Search persistent memory
    if (scope === 'all') {
      try {
        const memoryMatches = await this.searchMemory(query);
        entries.push(...memoryMatches);
      } catch (err) {
        log.engine.debug(`[MemorySearcher] Memory search error: ${err}`);
      }
    }

    // Sort by timestamp (oldest first for narrative flow)
    entries.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

    // Generate narrative
    let narrative = '';
    if (entries.length > 0) {
      narrative = await this.generateNarrative(query, entries);
    } else {
      narrative = `I searched through pellets, sessions, and memory but couldn't find anything matching "${query}".`;
    }

    return {
      id: `thread_${Date.now()}`,
      query,
      timeline: entries,
      relatedPellets,
      relatedSessions,
      narrative,
      reconstructedAt: new Date().toISOString(),
    };
  }

  /**
   * Find cross-references: sessions/pellets where both topics appear.
   */
  async findCrossReferences(topicA: string, topicB: string): Promise<ThreadEntry[]> {
    const threadA = await this.recall(topicA, 'all');
    const threadB = await this.recall(topicB, 'all');

    // Find source IDs that appear in both
    const sourceIdsA = new Set(threadA.timeline.map(e => e.sourceId));
    const crossRefs = threadB.timeline.filter(e => sourceIdsA.has(e.sourceId));

    return crossRefs;
  }

  /**
   * Search session content for matching messages.
   */
  private async searchSessions(query: string, limit: number): Promise<ThreadEntry[]> {
    const results: ThreadEntry[] = [];
    const queryLower = query.toLowerCase();
    const queryWords = queryLower.split(/\s+/).filter(w => w.length > 2);

    // Use index if available, otherwise scan sessions directly
    const sessions = await this.sessionStore.listSessions();

    // Only scan recent sessions (last 50) for performance
    const recentSessions = sessions.slice(0, 50);

    for (const session of recentSessions) {
      let bestScore = 0;
      let bestExcerpt = '';

      for (const msg of session.messages) {
        const contentLower = msg.content.toLowerCase();
        let score = 0;

        // Score by word matches
        for (const word of queryWords) {
          if (contentLower.includes(word)) {
            score += 1;
          }
        }

        // Exact phrase match bonus
        if (contentLower.includes(queryLower)) {
          score += queryWords.length;
        }

        if (score > bestScore) {
          bestScore = score;
          const start = Math.max(0, contentLower.indexOf(queryWords[0] || queryLower) - 50);
          bestExcerpt = `[${msg.role}]: ${msg.content.slice(start, start + 300)}`;
        }
      }

      if (bestScore > 0) {
        results.push({
          timestamp: new Date(session.metadata.startedAt).toISOString(),
          source: 'session',
          sourceId: session.id,
          excerpt: bestExcerpt || `Session with ${session.messages.length} messages`,
          relevanceScore: bestScore / queryWords.length,
        });
      }
    }

    // Sort by relevance, return top N
    results.sort((a, b) => b.relevanceScore - a.relevanceScore);
    return results.slice(0, limit);
  }

  /**
   * Search persistent memory (memory.md).
   */
  private async searchMemory(query: string): Promise<ThreadEntry[]> {
    const memoryPath = join(this.workspacePath, 'memory.md');
    if (!existsSync(memoryPath)) return [];

    try {
      const content = await readFile(memoryPath, 'utf-8');
      const lines = content.split('\n').filter(l => l.trim());
      const queryLower = query.toLowerCase();
      const queryWords = queryLower.split(/\s+/).filter(w => w.length > 2);

      const results: ThreadEntry[] = [];

      for (const line of lines) {
        const lineLower = line.toLowerCase();
        let score = 0;
        for (const word of queryWords) {
          if (lineLower.includes(word)) score++;
        }
        if (score > 0) {
          results.push({
            timestamp: new Date().toISOString(), // memory.md has no timestamps per-line
            source: 'memory',
            sourceId: 'persistent_memory',
            excerpt: line.trim(),
            relevanceScore: score / queryWords.length,
          });
        }
      }

      return results.sort((a, b) => b.relevanceScore - a.relevanceScore).slice(0, 3);
    } catch {
      return [];
    }
  }

  /**
   * Generate a narrative summary from thread entries via LLM.
   */
  private async generateNarrative(query: string, entries: ThreadEntry[]): Promise<string> {
    // Build a condensed context for the LLM
    const entrySummaries = entries.slice(0, 8).map((e, i) => {
      const date = new Date(e.timestamp).toLocaleDateString('en-US', {
        weekday: 'short', month: 'short', day: 'numeric',
      });
      return `[${i + 1}] ${date} (${e.source}): ${e.excerpt.slice(0, 200)}`;
    }).join('\n\n');

    try {
      const response = await this.provider.chat(
        [
          {
            role: 'user',
            content:
              `The user asked: "${query}"\n\n` +
              `Here are the related memories and knowledge I found:\n\n${entrySummaries}\n\n` +
              `Write a concise narrative (3-5 sentences) that tells the story of this topic — ` +
              `when it was first discussed, key points, how it evolved, and any related discoveries. ` +
              `Use a warm, personal tone as if recalling a shared history. ` +
              `Start with "I remember..." or "We discussed..." or similar.`,
          },
        ],
        undefined,
        { temperature: 0.3, maxTokens: 300 },
      );

      return response.content.trim();
    } catch (err) {
      log.engine.debug(`[MemorySearcher] Narrative generation failed: ${err}`);
      // Fallback: return raw entries
      return entries
        .slice(0, 5)
        .map(e => `- ${e.excerpt.slice(0, 150)}`)
        .join('\n');
    }
  }

  /**
   * Build/update the session index for faster searching.
   */
  async rebuildIndex(): Promise<void> {
    const sessions = await this.sessionStore.listSessions();
    const entries = sessions.slice(0, 100).map(s => {
      const userMessages = s.messages.filter(m => m.role === 'user');
      const allText = userMessages.map(m => m.content).join(' ').toLowerCase();

      // Extract topic words (most frequent non-stop words)
      const words = allText.split(/\s+/).filter(w => w.length > 3);
      const freq = new Map<string, number>();
      for (const w of words) {
        freq.set(w, (freq.get(w) || 0) + 1);
      }
      const topics = [...freq.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([w]) => w);

      return {
        sessionId: s.id,
        topics,
        timestamp: s.metadata.startedAt,
        messageCount: s.messages.length,
        firstMessage: userMessages[0]?.content.slice(0, 100) || '',
      };
    });

    this.sessionIndex = { entries, lastUpdated: new Date().toISOString() };

    const indexPath = join(this.workspacePath, 'session-index.json');
    await writeFile(indexPath, JSON.stringify(this.sessionIndex, null, 2));
    log.engine.debug(`[MemorySearcher] Rebuilt session index: ${entries.length} sessions`);
  }

  /**
   * Load session index from disk.
   */
  async loadIndex(): Promise<void> {
    const indexPath = join(this.workspacePath, 'session-index.json');
    if (!existsSync(indexPath)) return;
    try {
      const data = await readFile(indexPath, 'utf-8');
      this.sessionIndex = JSON.parse(data);
    } catch {
      // Ignore
    }
  }
}
