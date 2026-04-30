import type { Session } from "../memory/store.js";
import type { GatewayCallbacks } from "../gateway/types.js";
import type { ConversationDigest } from "../memory/conversation-digest.js";
import type { ContinuityResult } from "../cognition/continuity-engine.js";
import type { IntelligenceRouter } from "../intelligence/router.js";
import type { PelletStore } from "../pellets/store.js";
import type { MemoryBus } from "../memory/bus.js";
import type { SessionStore } from "../memory/store.js";
import type { EventBus } from "../events/bus.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { ContinuityClass } from "../cognition/continuity-engine.js";

export type { ContinuityClass };

export interface ContextDependencies {
  intelligenceRouter: IntelligenceRouter;
  pelletStore: PelletStore;
  memoryBus: MemoryBus;
  sessionStore: SessionStore;
  eventBus: EventBus;
  config: StackOwlConfig;
}

export interface TriageSignals {
  userMessage: string;
  isConversational: boolean;
  hasFrustration: boolean;
  isOpinionRequest: boolean;
  hasTemporalTrigger: boolean;
  isReturningUser: boolean;
  sessionDepth: number;
  hasActiveItems: boolean;
  effectiveUserId: string;
  continuityClass: ContinuityClass | null;
}

export interface ContextRequest {
  readonly session: Session;
  readonly callbacks: GatewayCallbacks;
  readonly channelId?: string;
  readonly userId?: string;
  readonly continuityResult: ContinuityResult | null;
  readonly digest: ConversationDigest | null;
  readonly deps: ContextDependencies;
}

export type LayerResults = ReadonlyMap<string, string>;

export type SkippedReason =
  | "shouldFire=false"
  | "circuit_open"
  | "budget_exhausted"
  | "pipeline_timeout"
  | `error: ${string}`;

export interface ContextBuildTraceEntry {
  layerName: string;
  priority: number;
  batchIndex: number;
  fired: boolean;
  cacheHit: boolean;
  tokensUsed: number;
  durationMs: number;
  skippedReason?: SkippedReason;
}

export type ContextBuildTrace = ContextBuildTraceEntry[];

export interface ContextLayer {
  name: string;
  priority: number;
  maxTokens: number;
  produces: string[];
  dependsOn: string[];
  alwaysInclude?: boolean;
  /** Cache TTL in milliseconds; undefined = use pipeline default (300_000 ms) */
  cacheTtlMs?: number;
  shouldFire(triage: TriageSignals): boolean;
  build(req: ContextRequest, triage: TriageSignals, deps: LayerResults): Promise<string>;
  getCacheKey?(req: ContextRequest, triage: TriageSignals): string | null;
}
