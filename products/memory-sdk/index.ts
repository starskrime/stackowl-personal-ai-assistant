/**
 * Memory SDK
 *
 * Drop-in episodic + fact memory for any AI app.
 * Three calls give any LLM app persistent memory:
 *
 *   await sdk.store(userId, userMessage, assistantResponse)
 *   const memories = await sdk.recall(userId, query)
 *   const ctx = await sdk.context(userId)   // inject into system prompt
 *
 * Backed by StackOwl's production memory systems:
 *   - EpisodicMemory  — narrative conversation summaries (Park et al.)
 *   - FactStore       — structured extracted facts with conflict resolution
 *   - FactExtractor   — LLM-powered fact extraction from conversations
 *   - WorkingContext  — per-user in-session state
 *
 * Provider-agnostic: works with OpenAI, Anthropic, Ollama, or any fetch-based API.
 */

import { join } from "node:path";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { EpisodicMemory } from "../../src/memory/episodic.js";
import { FactStore } from "../../src/memory/fact-store.js";
import { FactExtractor } from "../../src/memory/fact-extractor.js";
import { WorkingContext } from "../../src/memory/working-context.js";
import type { ModelProvider, ChatMessage } from "../../src/providers/base.js";
import type {
  MemorySDKConfig,
  MemoryProvider,
  StoreResult,
  RecallResult,
  RecalledFact,
  RecalledEpisode,
  ContextResult,
  MemoryStats,
} from "./types.js";

// ─── Provider Bridge ──────────────────────────────────────────────────────
// Adapts the SDK's minimal MemoryProvider interface to StackOwl's ModelProvider

function bridgeProvider(provider: MemoryProvider): ModelProvider {
  return {
    async chat(messages: ChatMessage[], _model?: string, options?: { maxTokens?: number; temperature?: number }) {
      const adapted = messages.map((m) => ({
        role: m.role as "system" | "user" | "assistant",
        content: m.content,
      }));
      const result = await provider.chat(adapted, options);
      return {
        content: result.content,
        model: "memory-sdk",
        finishReason: "stop" as const,
      };
    },

    async embed(text: string) {
      if (!provider.embed) return { embedding: [], model: "none" };
      const result = await provider.embed(text);
      return { embedding: result.embedding, model: "memory-sdk" };
    },

    // Stubs for unused ModelProvider methods
    async stream() { throw new Error("streaming not used by MemorySDK"); },
    async listModels() { return []; },
    isAvailable() { return Promise.resolve(true); },
    getDefaultModel() { return "memory-sdk"; },
  } as unknown as ModelProvider;
}

// ─── Per-user state ────────────────────────────────────────────────────────

interface UserState {
  episodic: EpisodicMemory;
  factStore: FactStore;
  workingCtx: WorkingContext;
  turns: Array<{ userMessage: string; assistantResponse: string; timestamp: number }>;
  initialized: boolean;
}

// ─── SDK ──────────────────────────────────────────────────────────────────

export class MemorySDK {
  private config: Required<MemorySDKConfig>;
  private users: Map<string, UserState> = new Map();
  private extractor: FactExtractor | null = null;
  private bridgedProvider: ModelProvider | null = null;

  constructor(config: MemorySDKConfig) {
    this.config = {
      workspacePath: config.workspacePath,
      provider: config.provider,
      workingContextWindow: config.workingContextWindow ?? 10,
      maxFactsPerUser: config.maxFactsPerUser ?? 1000,
      factTtlDays: config.factTtlDays ?? 30,
    } as Required<MemorySDKConfig>;

    if (config.provider) {
      this.bridgedProvider = bridgeProvider(config.provider);
      this.extractor = new FactExtractor(this.bridgedProvider);
    }
  }

  /**
   * Store a user↔assistant exchange.
   * Extracts facts and (every N turns) creates an episode.
   *
   * @param userId    - User identifier
   * @param message   - What the user said
   * @param response  - What the assistant replied
   */
  async store(
    userId: string,
    message: string,
    response: string,
  ): Promise<StoreResult> {
    const state = await this.getOrCreateUser(userId);
    const turn = { userMessage: message, assistantResponse: response, timestamp: Date.now() };
    state.turns.push(turn);

    // Update working context
    state.workingCtx.setLastUserMessage(message);

    let factsExtracted = 0;
    let episodeCreated = false;

    // Extract facts if provider is available
    if (this.extractor && this.bridgedProvider) {
      try {
        const messages: ChatMessage[] = [
          { role: "user", content: message },
          { role: "assistant", content: response },
        ];
        const result = await this.extractor.extract(messages);

        if (result.facts.length > 0) {
          const ttlMs = this.config.factTtlDays * 24 * 60 * 60 * 1000;
          const expiresAt = new Date(Date.now() + ttlMs).toISOString();

          await state.factStore.addBatch(
            result.facts
              .filter((f) => f.confidence >= 0.4)
              .map((f) => ({
                userId,
                fact: f.fact,
                entity: f.entity,
                category: f.category,
                confidence: f.confidence,
                source: "inferred" as const,
                expiresAt,
              })),
          );
          factsExtracted = result.facts.length;
        }
      } catch {
        // Fact extraction is best-effort
      }
    }

    // Flush to episodic memory every N turns
    const windowSize = this.config.workingContextWindow;
    if (state.turns.length >= windowSize) {
      if (this.bridgedProvider) {
        try {
          const messages = state.turns.flatMap((t) => [
            { role: "user" as const, content: t.userMessage },
            { role: "assistant" as const, content: t.assistantResponse },
          ]);
          await state.episodic.extractFromMessages(
            messages,
            `session-${userId}-${Date.now()}`,
            userId,
            this.bridgedProvider,
          );
          episodeCreated = true;
        } catch {
          // Episode creation is best-effort
        }
      }
      // Reset turns after flush
      state.turns = [];
    }

    return {
      factsExtracted,
      episodeCreated,
      turnCount: state.turns.length,
    };
  }

  /**
   * Recall relevant memories for a query.
   * Uses Park et al. retrieval scoring (recency × importance × relevance).
   *
   * @param userId - User identifier
   * @param query  - What to search for
   */
  async recall(userId: string, query: string): Promise<RecallResult> {
    const state = await this.getOrCreateUser(userId);

    // Search facts
    const rawFacts = state.factStore.search(query, userId, 10);
    const facts: RecalledFact[] = rawFacts.map((f) => ({
      fact: f.fact,
      category: f.category,
      confidence: f.confidence,
      source: f.source,
      updatedAt: f.updatedAt,
    }));

    // Search episodes with Park et al. scoring
    const rawEpisodes = await state.episodic.searchWithScoring(
      query,
      5,
      this.bridgedProvider ?? undefined,
    );
    const episodes: RecalledEpisode[] = rawEpisodes.map((e) => ({
      date: e.date,
      summary: e.summary,
      keyFacts: e.keyFacts,
      topics: e.topics,
      retrievalScore: e.retrievalScore,
    }));

    return { facts, episodes, query, userId };
  }

  /**
   * Build an enriched context string to inject into a system prompt.
   * Combines recent facts, relevant episodes, and working context state.
   *
   * @param userId - User identifier
   * @param query  - Optional query to bias episodic recall (defaults to last user message)
   */
  async context(userId: string, query?: string): Promise<ContextResult> {
    const state = await this.getOrCreateUser(userId);
    const effectiveQuery =
      query ?? state.workingCtx.getLastUserMessage() ?? userId;

    const lines: string[] = [];
    let factCount = 0;
    let episodeCount = 0;

    // High-confidence facts
    const facts = state.factStore
      .getActiveForUser(userId)
      .filter((f) => f.confidence >= 0.6)
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, 12);

    if (facts.length > 0) {
      lines.push("<user_facts>");
      for (const f of facts) {
        lines.push(`  [${f.category}] ${f.fact}`);
      }
      lines.push("</user_facts>");
      factCount = facts.length;
    }

    // Relevant episodes
    const episodes = await state.episodic.searchWithScoring(
      effectiveQuery,
      3,
      this.bridgedProvider ?? undefined,
      0.3,
    );

    if (episodes.length > 0) {
      lines.push("<relevant_history>");
      for (const ep of episodes) {
        const date = new Date(ep.date).toLocaleDateString();
        lines.push(`  [${date}] ${ep.summary}`);
        if (ep.keyFacts.length > 0) {
          lines.push(`    → ${ep.keyFacts.slice(0, 2).join("; ")}`);
        }
      }
      lines.push("</relevant_history>");
      episodeCount = episodes.length;
    }

    // Current topic from working context
    const currentTopic = state.workingCtx.getCurrentTopic();
    const taskInProgress = state.workingCtx.getTaskInProgress();

    if (currentTopic || taskInProgress) {
      lines.push("<working_context>");
      if (currentTopic) lines.push(`  Current topic: ${currentTopic}`);
      if (taskInProgress) lines.push(`  Task in progress: ${taskInProgress}`);
      lines.push("</working_context>");
    }

    const workingContextKeys = state.workingCtx.getKeys
      ? state.workingCtx.getKeys()
      : [];

    return {
      contextString: lines.join("\n"),
      breakdown: { factCount, episodeCount, workingContextKeys },
    };
  }

  /**
   * Get memory statistics for a user.
   */
  async stats(userId: string): Promise<MemoryStats> {
    const state = await this.getOrCreateUser(userId);
    const factStats = state.factStore.getStats(userId);
    const episodeStats = state.episodic.getStats();

    return {
      userId,
      facts: {
        total: factStats.total,
        byCategory: factStats.byCategory,
      },
      episodes: {
        total: episodeStats.total,
        topics: episodeStats.topics,
      },
      workingContext: {
        turns: state.turns.length,
        currentTopic: state.workingCtx.getCurrentTopic(),
      },
    };
  }

  /**
   * Flush current turn buffer to episodic memory immediately.
   * Useful at session end.
   */
  async flush(userId: string): Promise<boolean> {
    const state = await this.getOrCreateUser(userId);
    if (state.turns.length === 0 || !this.bridgedProvider) return false;

    try {
      const messages = state.turns.flatMap((t) => [
        { role: "user" as const, content: t.userMessage },
        { role: "assistant" as const, content: t.assistantResponse },
      ]);
      await state.episodic.extractFromMessages(
        messages,
        `session-${userId}-${Date.now()}`,
        userId,
        this.bridgedProvider,
      );
      state.turns = [];
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Clear all memory for a user.
   */
  async clear(userId: string): Promise<void> {
    this.users.delete(userId);
    // Re-create fresh state
    await this.getOrCreateUser(userId);
  }

  // ─── Private ────────────────────────────────────────────────────────────

  private async getOrCreateUser(userId: string): Promise<UserState> {
    if (this.users.has(userId)) {
      return this.users.get(userId)!;
    }

    const userPath = join(this.config.workspacePath, userId);
    if (!existsSync(userPath)) {
      await mkdir(userPath, { recursive: true });
    }

    const episodic = new EpisodicMemory(userPath, this.bridgedProvider ?? undefined);
    const factStore = new FactStore(userPath, {
      maxFactsPerUser: this.config.maxFactsPerUser,
      defaultTtlDays: this.config.factTtlDays,
    });

    await episodic.load();
    await factStore.load();

    const state: UserState = {
      episodic,
      factStore,
      workingCtx: new WorkingContext(),
      turns: [],
      initialized: true,
    };

    this.users.set(userId, state);
    return state;
  }
}

// Re-export types and adapters for convenience
export type {
  MemorySDKConfig,
  MemoryProvider,
  StoreResult,
  RecallResult,
  RecalledFact,
  RecalledEpisode,
  ContextResult,
  MemoryStats,
} from "./types.js";
