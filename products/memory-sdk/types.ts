/**
 * Memory SDK — Public Types
 *
 * These are the types that SDK consumers interact with.
 * All internal StackOwl-specific types are kept behind the SDK surface.
 */

// ─── Provider Abstraction ──────────────────────────────────────────────────

/**
 * Minimal provider interface the SDK needs.
 * Implement this for any LLM (OpenAI, Anthropic, Ollama, etc.)
 */
export interface MemoryProvider {
  /**
   * Send a chat message and get a text response.
   */
  chat(
    messages: Array<{ role: "system" | "user" | "assistant"; content: string }>,
    options?: { maxTokens?: number; temperature?: number },
  ): Promise<{ content: string }>;

  /**
   * Generate an embedding vector for a text string.
   * Optional — if not provided, falls back to keyword-only search.
   */
  embed?(text: string): Promise<{ embedding: number[] }>;
}

// ─── SDK Config ────────────────────────────────────────────────────────────

export interface MemorySDKConfig {
  /** Directory where memory files are persisted */
  workspacePath: string;

  /** AI provider for fact extraction and embedding */
  provider?: MemoryProvider;

  /**
   * How many recent turns to track in working context before auto-flushing
   * to episodic memory. Default: 10
   */
  workingContextWindow?: number;

  /**
   * Maximum facts to store per user. Default: 1000
   */
  maxFactsPerUser?: number;

  /**
   * Days before facts expire. Default: 30
   */
  factTtlDays?: number;
}

// ─── Store Result ──────────────────────────────────────────────────────────

export interface StoreResult {
  factsExtracted: number;
  episodeCreated: boolean;
  turnCount: number;
}

// ─── Recall Result ─────────────────────────────────────────────────────────

export interface RecalledFact {
  fact: string;
  category: string;
  confidence: number;
  source: string;
  updatedAt: string;
}

export interface RecalledEpisode {
  date: number;
  summary: string;
  keyFacts: string[];
  topics: string[];
  retrievalScore: number;
}

export interface RecallResult {
  facts: RecalledFact[];
  episodes: RecalledEpisode[];
  query: string;
  userId: string;
}

// ─── Context Result ────────────────────────────────────────────────────────

export interface ContextResult {
  /** Ready-to-inject system prompt string */
  contextString: string;

  /** Structured breakdown of what's included */
  breakdown: {
    factCount: number;
    episodeCount: number;
    workingContextKeys: string[];
  };
}

// ─── Memory Stats ──────────────────────────────────────────────────────────

export interface MemoryStats {
  userId: string;
  facts: {
    total: number;
    byCategory: Record<string, number>;
  };
  episodes: {
    total: number;
    topics: Record<string, number>;
  };
  workingContext: {
    turns: number;
    currentTopic?: string;
  };
}
