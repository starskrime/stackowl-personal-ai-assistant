export type SkippedReason =
  | "budget_exhausted"
  | "circuit_open"
  | "should_not_fire"
  | "dep_missing"
  | "cache_hit";

export interface ContextDependencies {
  userId: string;
  sessionId: string;
  channelId: string;
  message: string;
  intelligenceRouter: {
    resolve(taskType: string): { provider: string; model: string };
  };
  db: {
    prepare(sql: string): { get(...args: unknown[]): unknown; all(...args: unknown[]): unknown[]; run(...args: unknown[]): void };
    exec(sql: string): void;
  };
  eventBus: {
    emit(event: string, payload: unknown): void;
  };
  logger: {
    debug(msg: string, meta?: Record<string, unknown>): void;
    info(msg: string, meta?: Record<string, unknown>): void;
    warn(msg: string, meta?: Record<string, unknown>): void;
    error(msg: string, meta?: Record<string, unknown>): void;
  };
  [key: string]: unknown;
}

export interface TriageSignals {
  userId: string;
  sessionId: string;
  channelId: string;
  message: string;
  messageLength: number;
  isGreeting: boolean;
  isQuestion: boolean;
  isCommand: boolean;
  mentionedOwl: string | null;
  hasAttachment: boolean;
  sessionTurnCount: number;
  isNewSession: boolean;
}

export interface ContextRequest {
  triage: TriageSignals;
  deps: ContextDependencies;
  globalTokenBudget: number;
}

export type LayerResults = Map<string, string>;

export interface ContextBuildTraceEntry {
  layer: string;
  status: "hit" | "built" | "skipped" | "error";
  skippedReason?: SkippedReason;
  tokens: number;
  latencyMs: number;
  cacheHit: boolean;
  error?: string;
}

export interface ContextBuildTrace {
  entries: ContextBuildTraceEntry[];
  totalTokens: number;
  totalLatencyMs: number;
  qualityScore: number;
}

export interface ContextLayer {
  /** Unique identifier for this layer */
  name: string;
  /** Higher = included first when budget is tight (1–100) */
  priority: number;
  /** Max tokens this layer may consume */
  maxTokens: number;
  /** Keys this layer writes to LayerResults */
  produces: string[];
  /** Keys from LayerResults this layer reads (must be produced by earlier layers) */
  dependsOn: string[];
  /** If true, bypasses shouldFire() and circuit state (budget still applies) */
  alwaysInclude?: boolean;
  /** Cache TTL in milliseconds; undefined = no caching */
  cacheTtlMs?: number;

  /**
   * Returns false to skip this layer for the current request.
   * Ignored when alwaysInclude is true.
   */
  shouldFire(req: ContextRequest): boolean;

  /**
   * Build the context string(s) for this layer.
   * Returns a map of key → content for each key in produces[].
   * Missing deps write "" to results — handle gracefully.
   */
  build(req: ContextRequest, results: LayerResults): Promise<Partial<Record<string, string>>>;

  /**
   * Optional: return a cache key for this layer+request.
   * If absent, caching is disabled for this layer.
   */
  getCacheKey?(req: ContextRequest): string;
}
