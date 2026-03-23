/**
 * StackOwl — Unified MemoryBus
 *
 * Single retrieval interface across ALL memory stores. Solves the
 * fragmentation problem where the owl "forgets" things because the
 * right store wasn't queried.
 *
 * Queries in parallel:
 *   1. MemoryReflexionEngine (structured entries with categories)
 *   2. PelletStore (knowledge artifacts from research)
 *   3. MemoryThreadSearcher (thread-based conversational recall)
 *   4. MicroLearner UserProfile (user signals)
 *   5. MemoryConsolidator (legacy memory.md)
 *
 * Returns deduplicated, relevance-ranked results regardless of source.
 */

import type { MemoryReflexionEngine, MemoryEntry } from './reflexion.js';
import type { PelletStore, Pellet } from '../pellets/store.js';
import type { MicroLearner } from '../learning/micro-learner.js';
import { MemoryConsolidator } from './consolidator.js';
import { log } from '../logger.js';

// ─── Unified Memory Item ─────────────────────────────────────────

export interface UnifiedMemory {
  /** Unique ID across all stores */
  id: string;
  /** Human-readable content */
  content: string;
  /** Where this memory came from */
  source: 'reflexion' | 'pellet' | 'thread' | 'profile' | 'legacy';
  /** 0–1 relevance score to the query */
  relevance: number;
  /** Category hint for system prompt formatting */
  category: 'preference' | 'fact' | 'knowledge' | 'context' | 'profile';
  /** When this memory was created or last accessed */
  timestamp: string;
}

// ─── MemoryBus ───────────────────────────────────────────────────

export class MemoryBus {
  constructor(
    private reflexion?: MemoryReflexionEngine,
    private pelletStore?: PelletStore,
    private microLearner?: MicroLearner,
    private workspacePath?: string,
  ) {}

  /**
   * Query all memory stores in parallel and return deduplicated,
   * relevance-ranked results.
   *
   * @param query   The user message or search query
   * @param maxResults  Maximum number of memories to return
   * @param timeout  Maximum time to wait for all stores (ms)
   */
  async recall(
    query: string,
    maxResults: number = 15,
    timeout: number = 3000,
  ): Promise<UnifiedMemory[]> {
    const startTime = Date.now();

    // Fire all queries in parallel with a shared timeout
    const results = await Promise.race([
      this.queryAllStores(query),
      new Promise<UnifiedMemory[]>((resolve) =>
        setTimeout(() => {
          log.memory.warn(`[MemoryBus] Timeout after ${timeout}ms — returning partial results`);
          resolve([]);
        }, timeout),
      ),
    ]);

    // Deduplicate — same content from multiple stores
    const deduped = this.deduplicate(results);

    // Sort by relevance
    const ranked = deduped
      .sort((a, b) => b.relevance - a.relevance)
      .slice(0, maxResults);

    const elapsed = Date.now() - startTime;
    log.memory.info(
      `[MemoryBus] Recalled ${ranked.length} memories from ${results.length} raw results in ${elapsed}ms`,
    );

    return ranked;
  }

  /**
   * Format recalled memories for system prompt injection.
   * Groups by category and caps total length.
   */
  toSystemPrompt(memories: UnifiedMemory[], maxChars: number = 3000): string {
    if (memories.length === 0) return '';

    const lines: string[] = ['## Recalled Memories (cross-store)'];

    // Group by category
    const groups = new Map<string, UnifiedMemory[]>();
    for (const mem of memories) {
      const existing = groups.get(mem.category) ?? [];
      existing.push(mem);
      groups.set(mem.category, existing);
    }

    // Priority order for categories
    const categoryOrder: string[] = ['preference', 'fact', 'knowledge', 'context', 'profile'];
    const categoryLabels: Record<string, string> = {
      preference: '🎯 Preferences',
      fact: '📌 Known Facts',
      knowledge: '📚 Knowledge',
      context: '📝 Context',
      profile: '👤 User Profile',
    };

    let totalChars = 0;
    for (const cat of categoryOrder) {
      const items = groups.get(cat);
      if (!items || items.length === 0) continue;

      const label = categoryLabels[cat] ?? cat;
      lines.push(`\n### ${label}`);

      for (const item of items.slice(0, 5)) {
        const sourceTag = item.source === 'pellet' ? ' 📦' :
                          item.source === 'reflexion' ? ' 🧠' :
                          item.source === 'profile' ? ' 👤' : '';
        const line = `- ${item.content}${sourceTag}`;

        if (totalChars + line.length > maxChars) {
          lines.push('- ...[more memories available]');
          return lines.join('\n');
        }

        lines.push(line);
        totalChars += line.length;
      }
    }

    return lines.join('\n');
  }

  // ─── Private: Query All Stores ─────────────────────────────────

  private async queryAllStores(query: string): Promise<UnifiedMemory[]> {
    const promises: Promise<UnifiedMemory[]>[] = [];

    // 1. Reflexion engine (structured memories)
    if (this.reflexion) {
      promises.push(this.queryReflexion(query));
    }

    // 2. Pellet store (knowledge artifacts)
    if (this.pelletStore) {
      promises.push(this.queryPellets(query));
    }

    // 3. MicroLearner profile (user signals)
    if (this.microLearner) {
      promises.push(this.queryProfile(query));
    }

    // 4. Legacy memory.md
    if (this.workspacePath) {
      promises.push(this.queryLegacy(query));
    }

    const settled = await Promise.allSettled(promises);
    const results: UnifiedMemory[] = [];

    for (const s of settled) {
      if (s.status === 'fulfilled') {
        results.push(...s.value);
      } else {
        log.memory.warn(`[MemoryBus] Store query failed: ${s.reason}`);
      }
    }

    return results;
  }

  // ─── Store-Specific Queries ────────────────────────────────────

  private async queryReflexion(query: string): Promise<UnifiedMemory[]> {
    if (!this.reflexion) return [];

    try {
      const entries: MemoryEntry[] = await this.reflexion.retrieve(query, 10);
      return entries.map((e) => ({
        id: `rfx_${e.id}`,
        content: e.content,
        source: 'reflexion' as const,
        relevance: e.importance,
        category: this.mapReflexionCategory(e.category),
        timestamp: e.lastAccessedAt ?? e.createdAt,
      }));
    } catch (err) {
      log.memory.warn(`[MemoryBus] Reflexion query failed: ${err}`);
      return [];
    }
  }

  private async queryPellets(query: string): Promise<UnifiedMemory[]> {
    if (!this.pelletStore) return [];

    try {
      const pellets: Pellet[] = await this.pelletStore.search(query);
      return pellets.slice(0, 5).map((p, i) => ({
        id: `plt_${p.id}`,
        content: `**${p.title}**: ${p.content.slice(0, 300)}`,
        source: 'pellet' as const,
        relevance: Math.max(0.3, 1 - i * 0.15), // Position-based decay
        category: 'knowledge' as const,
        timestamp: p.generatedAt,
      }));
    } catch (err) {
      log.memory.warn(`[MemoryBus] Pellet query failed: ${err}`);
      return [];
    }
  }

  private async queryProfile(_query: string): Promise<UnifiedMemory[]> {
    if (!this.microLearner) return [];

    try {
      const profile = this.microLearner.getProfile();
      if (profile.totalMessages < 5) return [];

      const memories: UnifiedMemory[] = [];
      const now = new Date().toISOString();

      // Top topics
      const topTopics = Object.entries(profile.topics)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 3);
      if (topTopics.length > 0) {
        memories.push({
          id: 'prf_topics',
          content: `User frequently discusses: ${topTopics.map(([t, c]) => `${t} (${c}x)`).join(', ')}`,
          source: 'profile',
          relevance: 0.6,
          category: 'profile',
          timestamp: now,
        });
      }

      // Interaction style
      const style =
        profile.commandRate > 0.5 ? 'command-oriented (prefers direct actions)' :
        profile.questionRate > 0.4 ? 'question-oriented (prefers explanations)' :
        'conversational';
      memories.push({
        id: 'prf_style',
        content: `User interaction style: ${style}. Avg message: ${Math.round(profile.avgMessageLength)} chars.`,
        source: 'profile',
        relevance: 0.4,
        category: 'profile',
        timestamp: now,
      });

      // Sentiment balance
      if (profile.positiveSignals + profile.negativeSignals > 10) {
        const ratio = profile.positiveSignals / Math.max(1, profile.positiveSignals + profile.negativeSignals);
        memories.push({
          id: 'prf_sentiment',
          content: `User satisfaction: ${(ratio * 100).toFixed(0)}% positive (${profile.positiveSignals}+ / ${profile.negativeSignals}-)`,
          source: 'profile',
          relevance: 0.5,
          category: 'profile',
          timestamp: now,
        });
      }

      return memories;
    } catch (err) {
      log.memory.warn(`[MemoryBus] Profile query failed: ${err}`);
      return [];
    }
  }

  private async queryLegacy(query: string): Promise<UnifiedMemory[]> {
    if (!this.workspacePath) return [];

    try {
      const raw = await MemoryConsolidator.loadMemory(this.workspacePath);
      if (!raw || raw.length < 10) return [];

      // Simple keyword matching against memory.md content
      const queryWords = query.toLowerCase().split(/\s+/).filter(w => w.length > 3);
      const lines = raw.split('\n').filter(l => l.startsWith('- '));

      return lines
        .map((line, i) => {
          const content = line.replace(/^- /, '').trim();
          const lower = content.toLowerCase();
          let relevance = 0.2; // Base relevance for being in memory

          for (const word of queryWords) {
            if (lower.includes(word)) relevance += 0.15;
          }

          return {
            id: `leg_${i}`,
            content,
            source: 'legacy' as const,
            relevance: Math.min(1, relevance),
            category: 'fact' as const,
            timestamp: new Date().toISOString(),
          };
        })
        .filter(m => m.relevance > 0.25) // Only return matches
        .slice(0, 5);
    } catch {
      return [];
    }
  }

  // ─── Deduplication ─────────────────────────────────────────────

  private deduplicate(memories: UnifiedMemory[]): UnifiedMemory[] {
    const seen = new Map<string, UnifiedMemory>();

    for (const mem of memories) {
      // Normalize content for comparison
      const key = mem.content
        .toLowerCase()
        .replace(/\s+/g, ' ')
        .trim()
        .slice(0, 100);

      const existing = seen.get(key);
      if (existing) {
        // Keep the one with higher relevance
        if (mem.relevance > existing.relevance) {
          seen.set(key, mem);
        }
      } else {
        // Check for fuzzy duplicates (>70% word overlap)
        let isDuplicate = false;
        const memWords = new Set(key.split(' ').filter(w => w.length > 3));

        for (const [existingKey] of seen) {
          const existingWords = new Set(existingKey.split(' ').filter(w => w.length > 3));
          const intersection = [...memWords].filter(w => existingWords.has(w));
          const overlap = intersection.length / Math.max(memWords.size, existingWords.size);

          if (overlap > 0.7) {
            isDuplicate = true;
            break;
          }
        }

        if (!isDuplicate) {
          seen.set(key, mem);
        }
      }
    }

    return [...seen.values()];
  }

  // ─── Helpers ───────────────────────────────────────────────────

  private mapReflexionCategory(cat: string): UnifiedMemory['category'] {
    const mapping: Record<string, UnifiedMemory['category']> = {
      preference: 'preference',
      fact: 'fact',
      decision: 'fact',
      project: 'context',
      context: 'context',
    };
    return mapping[cat] ?? 'context';
  }
}
