# Element 5: ContextPipeline — Design Specification

**Date:** 2026-04-30
**Status:** Architect-approved — ready for implementation plan
**Audit element:** 5 — ContextBuilder (memory + pellets + skills)
**Replaces:** `src/gateway/handlers/context-builder.ts` (762 lines, 28 inline signals)

---

## 1. Problem Statement

The current `ContextBuilder.build()` is a 762-line god-method with:

- **28 inline signal blocks** — no abstraction, no boundaries, adding a signal means editing the method
- **Sequential execution** — 28 layers × ~150ms avg = ~4,200ms wall time per request
- **Triple memory duplication** — `factContext` + `memoryBusContext` + `memoryFirstContext` all query the same stores; same fact can appear 3× in one prompt
- **No token budget** — context can silently exceed the LLM window with no visibility
- **InnerMonologue never injected** — owl generates thoughts every turn, they are discarded; voice is stateless
- **No user persona** — owl knows fragments (individual facts) but not WHO the user is as a person
- **O(n) behavioral patch scan** — `pelletStore.listAll()` + filter in-memory every turn
- **No resilience** — a failing store silently degrades context with no alerting or recovery
- **Zero test coverage** — no unit tests for any context assembly logic

---

## 2. Solution Overview

Replace `ContextBuilder.build()` with a **ContextPipeline** — an ordered registry of typed `ContextLayer` instances executed via a **Directed Acyclic Graph (DAG)** with three enterprise subsystems:

1. **DAGPlanner** — layers declare `produces[]` / `dependsOn[]`; independent layers run in parallel batches via `Promise.all()`
2. **LayerCircuitBreaker + HealthMonitor** — failing layers trip to OPEN state, auto-recover; `ContextQualityScore` gives operational visibility
3. **ContextCache** — per-layer LRU cache with TTL and event-driven invalidation; cache hits skip `build()` entirely

Two net-new capabilities:
- **InnerMonologueLayer** — owl's last-turn thoughts + response intent injected as framing context; voice carries across turns
- **UserPersonaSynthesizer** — LLM synthesises facts + episodes + preferences into a `UserPersona` character card; injected every request so the owl responds to a *person*, not a stream of messages

**Performance targets:**

| Scenario | Before | After |
|----------|--------|-------|
| Cold first message | ~4,200ms | ~400ms |
| CONTINUATION warm | ~4,200ms | ~80ms |
| Context overflow | silent | trimmed at sentence boundary, trace logged |
| Layer failure | silent empty | circuit trips, quality score drops, logged |

---

## 3. Architecture

### 3.1 Module Boundary

New module: `src/context/` — self-contained, no circular dependencies with existing modules.

```
src/context/
  layer.ts                    ← ContextLayer interface, all supporting types
  budget-controller.ts        ← BudgetController, estimateTokens
  dag-planner.ts              ← DAGPlanner, cycle detection, batch builder
  cache.ts                    ← ContextCache, LRUMap, CacheEntry
  circuit-breaker.ts          ← LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore
  pipeline.ts                 ← ContextPipeline runner (DAG + cache + CB aware)
  triage.ts                   ← computeTriage() → TriageSignals
  utils.ts                    ← resolveUserId(), hash()
  user-persona-synthesizer.ts ← UserPersonaSynthesizer, UserPersona type
  unified-memory-retriever.ts ← UnifiedMemoryRetriever
  index.ts                    ← barrel export
  layers/
    identity.ts               ← SynthesisIdentityLayer
    inner-monologue.ts        ← InnerMonologueLayer
    working-memory.ts         ← Digest, Continuity, Compression layers
    user-memory.ts            ← CrossSessionFacts, OpenTasks, Relationship
    user-persona.ts           ← UserPersonaLayer
    behavioral.ts             ← BehavioralPatch, ActiveIntents, OwlLearnings
    memory-retrieval.ts       ← MemoryRetrievalLayer
    knowledge.ts              ← KnowledgeGraph, RelevantPellets
    profile.ts                ← UserBehaviorProfile, InferredPreferences, PredictedNeeds
    ambient.ts                ← Collab, Ambient
    infrastructure.ts         ← Temporal, ChannelFormat, ModeDirective, Socratic
    calibration.ts            ← Depth, Opinion, MentalModel, EchoChamber, GroundState
```

### 3.2 Core Types (`src/context/layer.ts`)

```typescript
export interface ContextLayer {
  /** Unique name — used as cache key prefix and trace label */
  name: string;
  /** Lower = earlier in batch; used for ordering within a parallel batch */
  priority: number;
  /** Hard token cap for this layer's output */
  maxTokens: number;
  /** Semantic outputs this layer produces — consumed by dependsOn of other layers */
  produces: string[];
  /** Named outputs this layer requires before build() is called */
  dependsOn: string[];
  /**
   * If true: fires regardless of shouldFire() result AND circuit state.
   * Use for mandatory structural layers (e.g. SynthesisIdentityLayer).
   * Budget still applies — alwaysInclude does not bypass token ceiling.
   */
  alwaysInclude?: boolean;
  /** Whether this layer should fire for this request */
  shouldFire(triage: TriageSignals): boolean;
  /**
   * Build the context string for this layer.
   * dep entries are "" when the producing layer skipped or failed —
   * each layer must handle empty deps gracefully (treat as "no context available").
   */
  build(req: ContextRequest, triage: TriageSignals, deps: LayerResults): Promise<string>;
  /** Return a stable cache key for this request, or null if not cacheable */
  getCacheKey?(req: ContextRequest, triage: TriageSignals): string | null;
}

export type LayerResults = ReadonlyMap<string, string>;

export interface TriageSignals {
  userMessage: string;
  isConversational: boolean;       // < 80 chars, no action keywords
  hasFrustration: boolean;         // "still", "again", "not working" etc.
  isOpinionRequest: boolean;       // "what do you think", "agree" etc.
  hasTemporalTrigger: boolean;     // "last time", "yesterday" etc.
  isReturningUser: boolean;        // FRESH_START or TOPIC_SWITCH
  sessionDepth: number;            // count of user messages in session
  hasActiveItems: boolean;         // active intents OR commitments OR stale goals
  effectiveUserId: string;         // resolved via resolveUserId()
  continuityClass: ContinuityClass | null;
}

/**
 * Narrow dependency surface for src/context/ — never import GatewayContext directly.
 * GatewayContext satisfies this interface via structural typing (no explicit implements needed).
 * This keeps src/context/ free of circular dependencies on src/gateway/.
 */
export interface ContextDependencies {
  intelligenceRouter: IntelligenceRouter;
  pelletStore: PelletStore;
  memoryBus: MemoryBus;
  sessionStore: SessionStore;
  userPersonaSynthesizer: UserPersonaSynthesizer;
  eventBus: EventBus;
  config: StackOwlConfig;
}

export interface ContextRequest {
  readonly session: Session;
  readonly callbacks: GatewayCallbacks;
  readonly channelId?: string;
  readonly userId?: string;
  readonly continuityResult: ContinuityResult | null;
  readonly digest: ConversationDigest | null;
  readonly deps: ContextDependencies;  // replaces ctx: GatewayContext — no gateway import
}

export interface ContextBuildTraceEntry {
  layerName: string;
  priority: number;
  batchIndex: number;
  fired: boolean;
  cacheHit: boolean;
  tokensUsed: number;
  durationMs: number;
  skippedReason?: "shouldFire=false" | "circuit_open" | "budget_exhausted" | "pipeline_timeout" | `error: ${string}`;
}

export type ContextBuildTrace = ContextBuildTraceEntry[];
```

### 3.3 BudgetController (`src/context/budget-controller.ts`)

```typescript
export class BudgetController {
  constructor(private globalCeiling: number = 8_000) {}

  apply(layerName: string, text: string, maxTokens: number): string
  // Returns text trimmed to min(maxTokens, remainingGlobal) tokens.
  // Trims at sentence boundary where possible; appends "…[trimmed]" on cut.
  // Updates internal consumed counter.
  // Returns "" and records "budget_exhausted" when ceiling reached.

  get remaining(): number
  get consumed(): number
  reset(): void
}

function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.8);
}
```

Global ceiling is **configurable** via `stackowl.config.json`:
```json
{ "context": { "globalTokenCeiling": 8000 } }
```

Provider-aware ceiling resolution: if `provider.maxContextTokens` is known (e.g. 200,000 for Claude), ceiling scales proportionally. Default ceiling is conservative (8,000) to leave room for system prompt + response.

### 3.4 DAGPlanner (`src/context/dag-planner.ts`)

```typescript
export class DAGPlanner {
  /** Build execution batches from layer declarations.
   *  Throws CircularDependencyError at construction time if a cycle is detected.
   *  Returns batches in execution order; layers within a batch are independent. */
  buildBatches(layers: ContextLayer[]): ContextLayer[][]
}

export class CircularDependencyError extends Error {
  constructor(cycle: string[])  // ["LayerA", "LayerB", "LayerA"]
}
```

**Algorithm:**
1. Build adjacency map from `dependsOn` → `produces` relationships
2. Kahn's algorithm (BFS topological sort) — detects cycles when remaining nodes > 0 after traversal
3. Within each topological level, sort by `priority` ascending
4. Return `ContextLayer[][]` — outer = batch index, inner = parallel layers

**Construction-time validation** means zero runtime surprises. Bad layer configuration fails loudly at startup, not silently at request time.

**Dependency-missing contract:** When a layer skips (shouldFire=false, circuit OPEN) or throws, the pipeline writes `""` into the results map for every entry in `layer.produces`. Downstream layers receive `""` for that dep and are responsible for handling it gracefully — typically by treating it as "no context available" and returning their own output unchanged or empty.

### 3.5 ContextCache (`src/context/cache.ts`)

```typescript
interface CacheEntry {
  output: string;
  tokensUsed: number;
  cachedAt: number;
  ttlMs: number;
}

export class ContextCache {
  // Primary store: `${layerName}:${cacheKey}` → CacheEntry
  // userIndex:    userId → Set<fullKey>  — maintained on set(), cleared on invalidateUser()
  // This makes invalidateUser() O(1) lookup + O(k) deletes where k = user's cached entries,
  // instead of O(n) full scan over all 200 entries.
  constructor(private maxEntries: number = 200) {}

  get(layerName: string, cacheKey: string): string | null
  set(layerName: string, cacheKey: string, output: string, ttlMs: number, userId?: string): void
  invalidate(layerName: string): void
  invalidateUser(userId: string): void  // O(1) via userIndex reverse map
  stats(): { size: number; hitRate: number; evictions: number }
}
```

**Per-layer cache key + TTL table:**

| Layer | Cache Key | TTL |
|-------|-----------|-----|
| `SynthesisIdentityLayer` | `hash(owlName + synthesisVersion)` | 10 min |
| `UserPersonaLayer` | `hash(userId + persona.updatedAt)` | 30 min |
| `BehavioralPatchLayer` | `hash(pelletTable.lastWriteAt)` | 5 min |
| `TemporalAwarenessLayer` | `hash(userId + hourBucket)` | 1 hour |
| `ModeDirectiveLayer` | `hash(userId + hasActiveItems)` | 2 min |
| `UserBehaviorProfileLayer` | `hash(userId + microLearner.version)` | 5 min |
| `OwlLearningsLayer` | `hash(userId + owlLearnings.lastWriteAt)` | 5 min |
| `KnowledgeGraphLayer` | `hash(queryFingerprint + graphVersion)` | 10 min |
| `InnerMonologueLayer` | `null` (not cacheable) | — |
| `WorkingMemoryDigestLayer` | `null` (not cacheable) | — |
| `ContinuityPriorResponseLayer` | `null` (not cacheable) | — |
| `UnifiedMemoryRetrievalLayer` | `null` (not cacheable) | — |

**Event-driven invalidation** wires into existing `EventBus`:

```typescript
eventBus.on("pellet:written",    () => cache.invalidate("BehavioralPatchLayer"));
eventBus.on("synthesis:created", () => cache.invalidate("SynthesisIdentityLayer"));
eventBus.on("persona:refreshed", () => cache.invalidate("UserPersonaLayer"));
eventBus.on("learning:recorded", () => cache.invalidate("OwlLearningsLayer"));
eventBus.on("session:end",       (e) => cache.invalidateUser(e.userId));
```

### 3.6 Circuit Breaker + Health Monitor (`src/context/circuit-breaker.ts`)

```typescript
type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export class LayerCircuitBreaker {
  // Rolling window: last 20 calls
  // Trips OPEN when: error rate > 40% OR p95 latency > 1,800ms
  // Resets to HALF_OPEN after 60s
  // HALF_OPEN: one probe call; success → CLOSED, failure → OPEN

  recordSuccess(latencyMs: number): void
  recordFailure(): void
  get state(): CircuitState
  shouldBypass(): boolean   // true when OPEN (not during probe)
}

export class LayerHealthMonitor {
  getBreaker(layerName: string): LayerCircuitBreaker
  shouldBypass(layerName: string): boolean
  getReport(): Record<string, { state: CircuitState; errorRate: number; p95Ms: number }>
}

export class ContextQualityScore {
  /** 0.0–1.0 composite score. Below 0.6 = degraded. */
  compute(trace: ContextBuildTrace, totalLayers: number): number
  // Signals:
  //   signalRatio:     layersFired / totalLayers              (weight 0.4)
  //   tokenEfficiency: min(consumed, ceiling) / ceiling       (weight 0.3)
  //   dupScore:        1 - (duplicateTokens / totalTokens)    (weight 0.3)
  //
  // When score < 0.6, emits eventBus.emit("context:quality_degraded", { score, trace })
  // so operators can alert or auto-debug without polling logs.
}
```

### 3.7 ContextPipeline Runner (`src/context/pipeline.ts`)

```typescript
export class ContextPipeline {
  private readonly batches: ContextLayer[][];

  constructor(
    private layers: ContextLayer[],
    private cache: ContextCache,
    private healthMonitor: LayerHealthMonitor,
    dagPlanner: DAGPlanner,
  ) {
    // Throws CircularDependencyError at construction time — fail loud, fail early.
    // batches is immutable after construction; safe to share across concurrent run() calls.
    this.batches = dagPlanner.buildBatches(layers);
  }

  // BudgetController is created fresh inside run() — never shared between concurrent requests.
  // Node.js is single-threaded but ContextPipeline is a singleton; per-run instantiation
  // guarantees no consumed-counter bleed between simultaneous web/Telegram requests.
  async run(
    request: ContextRequest,
    triage: TriageSignals,
    options?: { timeoutMs?: number; globalTokenCeiling?: number }
  ): Promise<{ output: string; trace: ContextBuildTrace }>
}
```

**Execution loop:**

```
budget = new BudgetController(options.globalTokenCeiling ?? config.context.globalTokenCeiling)

for each batch in this.batches:
  await Promise.all(batch.map(layer => executeLayer(layer, request, triage, results, budget)))

  // Post-batch priority trim: if budget is near ceiling, trim lowest-priority outputs first.
  // Sort completed batch results by layer.priority descending (lowest priority = highest number).
  // Remove from results map starting at lowest priority until budget.remaining > 0.
  // This ensures high-priority layers (low priority number) keep their tokens if ceiling is hit
  // by fast-resolving lower-priority layers in the same batch.
  trimBatchToFloor(batch, results, budget)

executeLayer(layer, request, triage, results, budget):
  1. Check layer.alwaysInclude || shouldFire(triage) → skip if neither
  2. Check !layer.alwaysInclude && healthMonitor.shouldBypass → skip if circuit OPEN
     (alwaysInclude layers bypass circuit state — they must never fail silently)
  3. Check cache.get(cacheKey)        → return cached if hit
  4. Start timer
  5. await Promise.race([layer.build(...), timeout(2000)])
  6. Record success/failure on circuit breaker
  7. Apply budget cap via budget.apply()
  8. cache.set(output) if cacheable
  9. Append to results map (for dependsOn consumers)
  10. Record trace entry

On skip or throw:
  → Write "" for every key in layer.produces into results map
  → Record trace entry with fired=false, skippedReason set
```

**Pipeline-level timeout** (default 5,000ms): remaining layers after timeout marked `pipeline_timeout` in trace.

**Trace emission policy:**
- `debug` log level: full `ContextBuildTrace` (28 entries) emitted on every run — only visible when `LOG_LEVEL=debug`
- `info` log level: one-line summary always emitted (fires, cache hits, tokens, batches, ms, quality)
- `EventBus`: `"context:quality_degraded"` emitted with `{ score, trace }` when quality < 0.6
- No SQLite persistence of traces — retrospective analysis is PostProcessor's concern, not ContextPipeline's

```
[ContextPipeline] 12 fired (8 cache hits, 4 computed), 3 skipped, 2 circuit_open
  — 3,240/8,000 tokens, 4 batches, 180ms, quality: 0.87
```

---

## 4. Net-New Capabilities

### 4.1 InnerMonologue Layer

**Problem:** The owl generates an `InnerMonologue` (thoughts, mood shift, response intent) on every turn via `OwlInnerLife.think()`. These are discarded after generation. The owl has no persistent voice.

**Solution:**

1. `ConversationDigest` extended with `lastInnerMonologue?: StoredMonologue`
2. `PostProcessor` stores the monologue after each response via `digestManager.setLastMonologue()`
3. `InnerMonologueLayer` reads from digest, injects:

```xml
<owl_inner_voice>
My approach this turn: {responseIntent}
My current disposition: {mood}
</owl_inner_voice>
```

**Priority:** 15 (between SynthesisIdentity:10 and WorkingMemoryDigest:20)
**maxTokens:** 300
**Not cacheable** — must reflect latest thoughts
**Staleness guard:** if monologue > 10 min old, `shouldFire()` returns false (stale session gap)

### 4.2 UserPersona Synthesizer

**Problem:** The owl knows individual facts about the user but has no holistic model of WHO they are. "User prefers Python" and "User is building a trading bot" are stored as separate facts — never synthesised into a character.

**Solution:** `UserPersonaSynthesizer` periodically calls the LLM (using `IntelligenceRouter` `extraction` tier — mid-tier model) with the top 10 facts by confidence + top 3 episodes by importance + `preferenceModel.toContextString()`, producing a structured `UserPersona`:

```typescript
interface UserPersona {
  communicationStyle: "concise" | "verbose" | "technical" | "casual";
  expertiseLevel: "novice" | "intermediate" | "expert";
  currentProjects: string[];      // ["trading bot in Python", "home lab k8s cluster"]
  recurringPatterns: string[];    // ["prefers code over explanation", "iterates fast"]
  emotionalTendencies: string;    // "direct, gets frustrated with ambiguity"
  emotionalTrajectory: string[];  // ordered arc of recent emotional signals: ["frustrated→resolved (2026-04-28)", "excited (2026-04-30)"]
  preferredApproach: string;      // "show working code first, explain after"
  lastUpdated: string;
}
```

**Cache strategy:** 30-min TTL in `user_personas` SQLite table.

**Synthesis trigger:**
- On **cache miss**: `UserPersonaSynthesizer.synthesize()` is called synchronously inside `UserPersonaLayer.build()` — blocks the layer until synthesis completes. This is acceptable for a new user's first request; subsequent requests hit the cache.
- On **cache stale** (expired but exists): stale persona returned immediately from SQLite; background revalidation enqueued via `setImmediate(() => synthesizer.synthesize())` **after** `build()` returns. The current request gets the old persona; the next request gets the fresh one. This is the stale-while-revalidate invariant.
- **PostProcessor invariant:** synthesis can only be triggered after at least 3 turns (no persona for first-time users with < 3 facts). `shouldFire()` returns false when fact count < 3.
- First-time users with < 3 facts: layer skips gracefully (no `<user_persona>` block injected).

**Injected as:**
```xml
<user_persona>
Communication: technical, concise
Expertise: expert
Current focus: trading bot in Python, home lab k8s
Patterns: prefers code first, iterates fast
Approach: show working code first, explain after
</user_persona>
```

**Priority:** 50 | **maxTokens:** 400 | **Cache key:** `hash(userId + persona.updatedAt)`

### 4.3 Unified Memory Retrieval

**Problem:** `factContext`, `episodicContext`, `memoryBusContext`, and `memoryFirstContext` all query overlapping stores. Same fact can appear 3× in one prompt.

**Solution:** `UnifiedMemoryRetriever` extends `MemoryBus.recall()` with `FactStore` + `EpisodicMemory` sources. Single parallel query across all stores. Deduplication: `cosineSimilarity(a, b) > 0.9` → keep higher-relevance entry. Returns top 10 ranked results formatted as labeled XML tiers so the LLM knows the provenance and confidence of each memory:

```xml
<memory>
  <facts tier="long_term" confidence="high">
    User prefers Python over TypeScript for data pipelines.
    User is building a trading bot with live Binance feed.
  </facts>
  <episodes tier="episodic" recency="recent">
    2026-04-28: Debugged a race condition in the order book ingestion loop.
  </episodes>
  <bus tier="semantic" relevance="0.91">
    Trading bots should handle partial fills and network timeouts gracefully.
  </bus>
</memory>
```

Tier labels allow the owl to weight long-term facts more heavily than bus hits when they conflict, and to acknowledge recency vs. permanence in its response.

`MemoryFirstContextBuilder` (`src/memory/context-builder.ts`) is **deleted** — zero callers after migration.

---

## 5. Complete Layer Registry

| Priority | Layer | maxTokens | Trigger | Cacheable | produces | dependsOn |
|----------|-------|-----------|---------|-----------|----------|-----------|
| 10 | SynthesisIdentityLayer | 500 | **alwaysInclude** | ✓ 10m | `["identity"]` | `[]` |
| 15 | InnerMonologueLayer | 300 | digest.monologue < 10min | ✗ | `["inner_voice"]` | `[]` |
| 20 | WorkingMemoryDigestLayer | 600 | sessionDepth > 0 + has content | ✗ | `["digest"]` | `[]` |
| 25 | ContinuityPriorResponseLayer | 2000 | continuityResult + lastResponse | ✗ | `["continuity"]` | `["digest"]` |
| 30 | CompressionSummaryLayer | 800 | summary exists | ✗ | `["compression"]` | `[]` |
| 35 | CrossSessionFactsLayer | 400 | always | ✗ | `["cross_session_facts"]` | `[]` |
| 40 | OpenTasksLayer | 300 | tasks exist | ✗ | `["open_tasks"]` | `["digest"]` |
| 45 | RelationshipContextLayer | 300 | always | ✗ | `["relationship"]` | `[]` |
| 50 | UserPersonaLayer | 400 | persona cached | ✓ 30m | `["user_persona"]` | `[]` |
| 60 | TemporalAwarenessLayer | 200 | always | ✓ 1h | `["temporal"]` | `[]` |
| 65 | ChannelFormatHintLayer | 100 | channelId=telegram | ✓ 1h | `["channel_hint"]` | `[]` |
| 70 | ModeDirectiveLayer | 200 | always | ✓ 2m | `["mode"]` | `[]` |
| 75 | SocraticModeLayer | 200 | socratic active | ✗ | `["socratic"]` | `[]` |
| 80 | BehavioralPatchLayer | 500 | always | ✓ 5m | `["behavioral_rules"]` | `[]` |
| 90 | ActiveIntentsLayer | 300 | intents exist | ✗ | `["intents"]` | `[]` |
| 95 | OwlLearningsLayer | 400 | !conversational\|returning | ✓ 5m | `["learnings"]` | `[]` |
| 100 | UnifiedMemoryRetrievalLayer | 800 | !conversational\|returning | ✗ | `["memory"]` | `["user_persona"]` |
| 110 | KnowledgeGraphLayer | 300 | !conversational | ✓ 10m | `["knowledge"]` | `[]` |
| 115 | RelevantPelletsLayer | 500 | !conversational | ✗ | `["pellets"]` | `[]` |
| 120 | UserBehaviorProfileLayer | 300 | !conversational | ✓ 5m | `["user_profile"]` | `[]` |
| 125 | InferredPreferencesLayer | 300 | !conversational | ✗ | `["preferences"]` | `[]` |
| 130 | PredictedNeedsLayer | 300 | confidence ≥ 0.7 | ✗ | `["predictions"]` | `[]` |
| 140 | CollabContextLayer | 300 | collab sessions exist | ✗ | `["collab"]` | `[]` |
| 145 | AmbientContextLayer | 300 | !conversational | ✗ | `["ambient"]` | `[]` |
| 150 | DepthDirectiveLayer | 150 | always | ✗ | `["depth"]` | `[]` |
| 155 | OpinionInjectionLayer | 200 | relevant opinion found | ✗ | `["opinion"]` | `[]` |
| 160 | UserMentalModelLayer | 200 | frustration + calibrated | ✗ | `["mental_model"]` | `[]` |
| 165 | EchoChamberGuardLayer | 150 | isOpinionRequest | ✗ | `["echo_guard"]` | `[]` |
| 170 | GroundStateLayer | 500 | sessionDepth ≥ 5 | ✗ | `["ground_state"]` | `[]` |

**28 layers total. Global token ceiling: 8,000 (configurable).**

**Projected batch execution:**

```
Batch 1 (12 layers, no deps):   SynthesisIdentity | InnerMonologue | WorkingMemoryDigest |
                                  CompressionSummary | CrossSessionFacts | RelationshipContext |
                                  UserPersona | TemporalAwareness | ChannelFormatHint |
                                  ModeDirective | BehavioralPatch | OwlLearnings

Batch 2 (depends on Batch 1):   ContinuityPriorResponse (needs digest)
                                  OpenTasks (needs digest)
                                  UnifiedMemoryRetrieval (needs user_persona)
                                  KnowledgeGraph | RelevantPellets | UserBehaviorProfile |
                                  InferredPreferences | PredictedNeeds | Collab | Ambient

Batch 3 (always last):          DepthDirective | OpinionInjection | UserMentalModel |
                                  EchoChamberGuard | GroundState | SocraticMode | ActiveIntents
```

---

## 6. Schema v13

```sql
-- User persona cache (UserPersonaSynthesizer)
CREATE TABLE IF NOT EXISTS user_personas (
  user_id      TEXT PRIMARY KEY,
  persona_json TEXT NOT NULL,
  synthesized_at TEXT NOT NULL,
  expires_at   TEXT NOT NULL
);

-- Pellet tag index (BehavioralPatchLayer O(1) lookup)
CREATE INDEX IF NOT EXISTS idx_pellets_tag ON pellets(tag);
```

---

## 7. Modified Files

| File | Change |
|------|--------|
| `src/gateway/handlers/context-builder.ts` | Thin adapter ≤120 lines; delegates to `ContextPipeline.run()` |
| `src/gateway/handlers/post-processor.ts` | Store `InnerMonologue` in digest after each response |
| `src/gateway/types.ts` | Add `userPersonaSynthesizer?`, `contextCache?`, `contextPipeline?`; `GatewayContext` satisfies `ContextDependencies` interface structurally (no explicit implements needed) |
| `src/gateway/core.ts` | Instantiate all context subsystems; wire into `GatewayContext` |
| `src/memory/db.ts` | Schema v13 migration; `UserPersonasRepo`; `PelletsRepo.getByTag()` |
| `src/memory/bus.ts` | Add `FactStore` + `EpisodicMemory` as named query sources |
| `src/memory/conversation-digest.ts` | Add `lastInnerMonologue?: StoredMonologue`; add `setLastMonologue()` |
| `src/events/bus.ts` | Add `"pellet:written"`, `"persona:refreshed"`, `"learning:recorded"`, `"context:quality_degraded"` event types |

## 8. Deleted Files

| File | Reason |
|------|--------|
| `src/memory/context-builder.ts` | Superseded by `UnifiedMemoryRetrievalLayer` |

---

## 9. Testing Strategy

| Test File | Coverage |
|-----------|---------|
| `__tests__/context/budget-controller.test.ts` | Per-layer cap, global ceiling, sentence-boundary trim |
| `__tests__/context/dag-planner.test.ts` | Cycle detection, batch ordering, priority within batch |
| `__tests__/context/cache.test.ts` | LRU eviction, TTL, event invalidation, cacheHit in trace |
| `__tests__/context/circuit-breaker.test.ts` | CLOSED→OPEN→HALF_OPEN→CLOSED, probe logic, quality score |
| `__tests__/context/pipeline.test.ts` | DAG execution, error isolation, pipeline timeout, trace shape |
| `__tests__/context/triage.test.ts` | All 7 triage boolean conditions + resolveUserId() |
| `__tests__/context/inner-monologue.test.ts` | Store→retrieve→inject→stale-guard lifecycle |
| `__tests__/context/user-persona.test.ts` | Synthesis, cache hit/miss, stale-while-revalidate, format |
| `__tests__/context/unified-memory-retriever.test.ts` | Dedup, ranking, store failure isolation, empty result |
| `__tests__/context/layers/*.test.ts` | shouldFire(), build(), token cap per layer group |
| `__tests__/context/pipeline-integration.test.ts` | Full run: ordering, budget, cache hits, trace output |

**Target:** ≥65 new tests, all 418 existing tests continue to pass.

---

## 10. Non-Goals (Out of Scope)

- Streaming context assembly (layers stream to LLM before all complete) — future element
- Multi-tenancy / per-organisation layer scoping — future element
- Layer A/B testing framework — future element
- Speculative pre-assembly (background context build before user sends) — future element

---

## 11. Open Questions (resolved)

| Question | Decision |
|----------|----------|
| Sequential vs parallel? | **DAG parallel batches** |
| Single budget or per-layer? | **Both** — per-layer cap + global ceiling |
| Cache invalidation strategy? | **Event-driven + TTL** |
| Circuit breaker persistence? | **In-memory only** — resets on restart, 60s windows make persistence unnecessary |
| `MemoryFirstContextBuilder` fate? | **Deleted** — superseded by `UnifiedMemoryRetrievalLayer` |
| GroundState threshold? | **Lowered 10 → 5** turns |
