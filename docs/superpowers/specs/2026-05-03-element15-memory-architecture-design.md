# Element 15 — Memory Architecture (Design Spec, v1)

**Date:** 2026-05-03
**Status:** Design approved by Boss; awaiting written-spec review before writing-plans (Phase 4).
**Inputs:**
- Phase 1 audit: `_bmad-output/planning-artifacts/element15-memory-architecture-audit-2026-05-03.md`
- Phase 2 research: `_bmad-output/planning-artifacts/research/market-stackowl-element15-memory-db-research-2026-05-03.md`

---

## 1. Goal

Replace the 3,803-line `src/memory/db.ts` god-class and its 41-table sprawl with a focused, taxonomy-aware memory module that the assistant can use to remember facts efficiently and indefinitely, the LLM can search and invalidate but never blindly write, and that composes — not duplicates — StackOwl's existing primitives (`IntelligenceRouter`, `ContextPipeline`, `GoalVerifier`, `PelletStore`, `GatewayEventBus`, `TaskLedger`).

**v1 ships three of the five "creative-gap" architectural moves** identified in Phase 2 research:
- **Move #1 — Goal-conditioned writes.** Every memory row carries the `GoalVerifier` verdict that produced it.
- **Move #4 — Event-driven cross-channel invalidation.** Contradictions and goal lifecycle events drive bitemporal invalidation through `GatewayEventBus`.
- **Move #5 — TTL-layered context rendering.** Memory plugs into `ContextPipeline` as four kind-specific layers with per-kind budgets and lifetimes.

**v2 (out of scope here)** ships moves #2 (parliament-debated retention) and #3 (DNA-coupled retrieval). v1 allocates the `memory_links` table (used by both v2 moves) but does not populate or read it.

## 2. Non-goals

- Not adding a vector store. SQLite + existing `PelletStore` cosine helpers stay.
- Not adding FTS5, BM25, or an entity index in v1. Hybrid retrieval (Mem0 pattern) deferred to v2 if cosine recall proves insufficient.
- Not changing channel adapters. The gateway-uniform `/memory` command reuses the same `*CommandRouter` pattern as Element 7d's `/mcp`.
- Not building parliament retention, DNA coupling, A-MEM Zettelkasten link traversal, or Ebbinghaus strength-based decay. Schema accommodates them; code is v2.
- Not adding multi-process concurrency. Single-process SQLite assumed.

## 3. Architecture

### 3.1 Three new files, no more

```
src/memory/
├── repository.ts       NEW   typed read/write surface over the 12 surviving tables
├── writer.ts           NEW   goal-gated ingestion pipeline (move #1 + #4)
├── layer.ts            NEW   ContextPipeline integration (move #5)
├── db.ts               EXISTING — shrunk to schema-owner + migration runner only
├── consolidator.ts     DELETED (per Phase 1 audit)
├── context-manager.ts  DELETED (per audit)
├── prior-context-retriever.ts  DELETED (per audit)
├── preference-recognizer.ts \
├── preference-enforcer.ts    } MERGED into writer.ts
└── fact-extractor.ts        /
```

### 3.2 Boundaries

- **`repository.ts`** — only thing that touches the 12 memory tables. Owns prepared statements. Replaces all 9 `rawDb` consumers (the encapsulation breach the audit flagged at `db.ts:404`). No LLM calls. No event subscription. No event emission. Layer calls `repository.recordAccess(ids, sourceLayer)` directly for synchronous correctness, then separately emits `memory:accessed` for telemetry observers — Repository is unaware of the event.
- **`writer.ts`** — only place that calls `IntelligenceRouter` for kind-classification + contradiction-detection. Only place that consults `GoalVerifier` verdicts. Subscribes to `turn:completed`, `goal:completed`, `goal:abandoned`, `tool:goal_blocked`. Calls Repository to persist. Emits `memory:written`, `memory:invalidated`, `memory:contradiction_detected`, plus the failure events in §7.
- **`layer.ts`** — exports four `ContextPipeline.Layer` instances: `SemanticMemoryLayer`, `EpisodicMemoryLayer`, `WorkingMemoryLayer`, `ProceduralMemoryLayer`. Each pulls from Repository (read-only). Emits `memory:accessed(ids)` when memories are included in a rendered prompt. **No `ReflexiveMemoryLayer` exists** — refusing to expose reflexive memories to the LLM is a correctness invariant, not a config knob.

### 3.3 Composition with existing primitives

| Primitive | How v1 uses it |
|---|---|
| `IntelligenceRouter.resolve("classification")` | Writer's classify and contradiction-check calls only. ~25% of turns: 1 call. ~5%: 2 calls. ~70%: 0 calls (short-circuits). |
| `GoalVerifier` verdict | Read from `engineContext.activeSubGoal.verdict`, already computed by Element 7 GAV. Writer reads, never re-calls. |
| `PelletStore` embeddings | Repository reuses the existing embedding column + cosine helper. |
| `ContextPipeline` | Four `MemoryLayer` instances registered at engine boot. |
| `GatewayEventBus` | Writer subscribes + emits. Topic prefix: `memory:*`. |
| `TaskLedger` / `OutcomeJournal` | Writer reads `subGoals[i].verdict` to tag verdicts on writes. |
| `ParliamentOrchestrator` | Not consumed in v1. Reserved for v2 move #2. |
| `DNA` | Not consumed in v1. Reserved for v2 move #3. v1 does not subscribe to `dna:mutated`; v2 adds the subscription and any persistence schema it needs at that time. |

### 3.4 Data flow per turn

```
Turn completes:
  OwlOrchestrator.emit("turn:completed", turn)
    └─ Writer.ingest(turn)
        ├─ trivial-turn guard            → 0 LLM calls, return
        ├─ classify (IntelligenceRouter) → candidates[]
        ├─ empty-extraction guard         → 0 inserts, return
        ├─ verdict tag (read-only)
        ├─ contradiction check (semantic only, IntelligenceRouter)
        ├─ Repository.insertBatch
        ├─ Repository.appendInvalidations (auto-resolved)
        ├─ Repository.appendContradictions (deferred)
        └─ emit memory:written / memory:invalidated / memory:contradiction_detected

Next turn begins:
  ContextPipeline.render():
    SemanticMemoryLayer.render()    ┐
    EpisodicMemoryLayer.render()    │ each:
    WorkingMemoryLayer.render()     │   → Repository.search → score → packToBudget
    ProceduralMemoryLayer.render()  ┘   → emit memory:accessed(ids)
                                          → Repository.recordAccess(ids)
```

## 4. Schema

### 4.1 Table inventory — 41 → 12

**Five memory tables, one per kind:**

| Table | Kind | Notes |
|---|---|---|
| `semantic_memories` | semantic | merges `user_preferences` + `goal_facts` + `learned_facts` + `extracted_facts`. `subkind ∈ {preference, fact, claim, identity}`. |
| `episodic_memories` | episodic | merges `episodic_events`; events/scenes with participants and time. |
| `working_memories` | working | merges current `working_memory` + scratch tables. Goal-scoped; auto-evicts on `goal:completed`/`goal:abandoned`. |
| `procedural_memories` | procedural | skill recipes, success/failure patterns. Linked to skills registry via `skill_ref`. |
| `reflexive_memories` | reflexive | failure traces, BLOCKED-verdict outcomes, contradiction histories. **Not in any ContextPipeline layer.** Substrate for Writer's contradiction detection + v2 evolve. |

**Three substrate tables, kept as-is:**

| Table | Role |
|---|---|
| `pellets` | parliament outputs (markdown + embedding). Memory tables can FK to pellet IDs. |
| `trajectories` | turn-sequence container. |
| `trajectory_turns` | individual turns with verdicts. Substrate Writer reads from. |

**Four linkage/audit tables:**

| Table | Role |
|---|---|
| `memory_links` | A-MEM-style typed links between memories (`from_id`, `to_id`, `link_kind`, `created_at`). v1 allocates schema; v2 populates and queries. |
| `memory_access_log` | append-only `(memory_id, accessed_at, source_layer)`. Drives `last_accessed_at` for recency scoring + future Ebbinghaus. |
| `memory_invalidations` | append-only audit `(memory_id, invalidated_at, reason, source_event)`. Powers `/memory show <id>` history and `fact:retracted` event reasoning. |
| `memory_contradictions` | append-only `(new_id, existing_id, detected_at, resolution)` — Writer's contradiction trail. |

### 4.2 Common columns on every memory table

```sql
id                  TEXT PRIMARY KEY
subkind             TEXT NOT NULL
content             TEXT NOT NULL
embedding           BLOB                -- float32 vector
goal_id             TEXT                -- FK soft-link to active sub-goal
verifier_verdict    TEXT NOT NULL       -- ADVANCES|PARTIAL|BLOCKED|NEUTRAL|UNVERIFIED
importance          REAL NOT NULL       -- 0..1, set by IntelligenceRouter at write
created_at          TEXT NOT NULL
valid_at            TEXT NOT NULL       -- defaults to created_at
invalid_at          TEXT                -- NULL = currently valid
last_accessed_at    TEXT
access_count        INTEGER NOT NULL DEFAULT 0
source_turn_id      TEXT                -- FK to trajectory_turns
superseded_by_id    TEXT                -- FK to memory that replaced this one
```

### 4.3 Indexes (per memory table)

- `(goal_id, invalid_at)` — goal-scoped reads
- `(verifier_verdict, created_at DESC)` — verdict-filtered freshness
- `(invalid_at, importance DESC)` — currently-valid, ranked sweeps

`semantic_memories` additionally indexes `(subkind, invalid_at)` for the `searchSemanticByEntity` query path used by contradiction detection.

### 4.4 Migration v25

Single transaction. Reversible only via on-disk backup.

**Pre-flight:**
1. Snapshot `db.sqlite` → `db.sqlite.bak.v24` (VACUUM INTO if available, else file-copy under `BEGIN EXCLUSIVE`). **Refuse to start migration if backup fails.**
2. Capture row counts on every source table into temp `_migration_v25_preflight(table_name, row_count, captured_at)`.

**Body** (single `BEGIN IMMEDIATE TRANSACTION`):
1. `CREATE TABLE` for the 9 new tables (5 memory + 4 linkage).
2. `ALTER TABLE pellets ADD COLUMN verifier_verdict TEXT; ADD COLUMN valid_at TEXT; ADD COLUMN invalid_at TEXT;` (`trajectories`/`trajectory_turns` already aligned per Element 7's v17).
3. `INSERT INTO <kind_table> SELECT … FROM <source_table>` for each merge, with explicit `subkind` mapping per source.
4. **Post-insert verification (executed in JS):** compare `_migration_v25_preflight` rows to new-table COUNTs. If any mismatch, `ROLLBACK` and refuse to bump `schema_version`.
5. `DROP TABLE` each merged source.
6. `CREATE INDEX` on the new tables (after data load — faster).
7. `DROP TABLE _migration_v25_preflight`.
8. `COMMIT`.

**Failure modes:**

| Failure | Recovery |
|---|---|
| Backup fails pre-flight | Migration refuses to start. Engine boot continues on v24 with a warning. |
| Transaction body throws | SQLite auto-rollback. v24 schema intact. Writer/Layer/Repository code is dead-on-arrival until v25 succeeds; engine boot prints a hard error and refuses to start. |
| Post-insert count mismatch | JS calls `ROLLBACK`, prints which table mismatched, refuses to bump `schema_version`. |
| Reported data loss after success | `db.sqlite.bak.v24` on disk; operator restore (separate utility) replaces `db.sqlite` from backup. |

### 4.5 rawDb breach migration (same PR, separate commits)

For each of the 9 outside `rawDb` consumers:
1. Replace raw SQL with a typed `Repository` call.
2. Add a unit test on the consumer that injects a fake `Repository`.
3. Final commit removes the `get rawDb()` accessor on `MemoryDatabase`.

No commit ever leaves both raw access AND the new typed surface in place. After the final commit, anyone reintroducing a `rawDb` consumer fails to compile. **Structural enforcement, not convention.**

## 5. Writer pipeline (move #1 + move #4)

### 5.1 Single entry point

Writer subscribes to `turn:completed` on `GatewayEventBus`. No public `Writer.store()` method exposed to the LLM, tools, or other modules. Memory writes are event-driven only.

### 5.2 `Writer.ingest(turn)` ordering

```
1. SHORT-CIRCUIT: trivial-turn guard
   if turn has no assistant content && no tool calls && no user-supplied facts:
     return  // 0 LLM calls

2. CLASSIFY (one cheap-tier IntelligenceRouter call)
   Input:  turn.userMessage, turn.assistantMessage, turn.toolResults
   Output: {
     candidates: Array<{
       kind: "semantic" | "episodic" | "working" | "procedural" | "reflexive",
       subkind: string,
       content: string,
       importance: number 0..1,
       entities?: string[],         // collected for v2 entity index; unused in v1 retrieval
     }>
   }
   Prompt is fixed and short (~200 tokens). Returns within ~400ms p95.

3. SHORT-CIRCUIT: empty-extraction guard
   if candidates.length === 0: return

4. VERDICT TAG (no LLM call)
   For each candidate:
     verdict = engineContext.activeSubGoal?.verdict ?? "UNVERIFIED"

5. CONTRADICTION CHECK (cheap-tier, semantic candidates only)
   For each semantic candidate:
     existing = Repository.searchSemanticByEntity(candidate.entities, validOnly=true)
     if existing.length > 0:
       result = IntelligenceRouter.classify(
         "does CANDIDATE contradict any of EXISTING?"
       )
       if result.contradicts:
         emit memory:contradiction_detected({newId: <pending>, existingIds, score})
         apply auto-invalidate policy:
           - if candidate.source = user message              → invalidate older
           - if candidate.verdict = ADVANCES &&
             older.verdict ≠ ADVANCES                         → invalidate older
           - else                                              → leave both,
                                                                append memory_contradictions
                                                                resolution="deferred"

6. PERSIST
   Repository.insertBatch(candidates with verdicts + contradiction resolutions)
   Repository.appendInvalidations(any auto-invalidated rows)
   Repository.appendContradictions(detected pairs)

7. EMIT
   For each inserted row:    emit memory:written({id, kind, verdict, goal_id})
   For each invalidated row: emit memory:invalidated({id, reason})
```

### 5.3 Event subscriptions

```
eventBus.on("turn:completed",      e => writer.ingest(e.turn))
eventBus.on("goal:completed",      e => writer.expireWorkingMemories(e.goalId, "goal_completed"))
eventBus.on("goal:abandoned",      e => writer.expireWorkingMemories(e.goalId, "goal_abandoned"))
eventBus.on("tool:goal_blocked",   e => writer.recordReflexive(e))    // failure-trace write
// dna:mutated subscription deferred to v2 (move #3)
```

`expireWorkingMemories(goalId, reason)` sets `invalid_at = now()` on all `working_memories` rows with `goal_id = goalId AND invalid_at IS NULL`. Emits one `memory:invalidated` per row with `reason`.

### 5.4 Cost expectations (steady state)

- ~70% of turns: 0 LLM calls (short-circuit at step 1 or 3).
- ~25%: 1 cheap-tier call (classify).
- ~5%: 2 cheap-tier calls (classify + contradiction).
- Verifier reuse: 0 incremental verifier cost (read from ledger).

### 5.5 Error envelope

`Writer.ingest()` never throws to its caller. All failure paths emit events (§7) and return cleanly. Memory is best-effort and never blocks user-facing turns.

## 6. Layer rendering (move #5)

### 6.1 Four layers, channel-agnostic budgets

| Layer | Source table | Default budget | TTL semantics | Query shape |
|---|---|---|---|---|
| `SemanticMemoryLayer` | `semantic_memories` | 800 tok | long — facts persist across sessions; `invalid_at` ages them out | `verdict ∈ {ADVANCES, NEUTRAL, UNVERIFIED} AND invalid_at IS NULL`, ranked by score |
| `EpisodicMemoryLayer` | `episodic_memories` | 600 tok | medium — recent events relevant to current goal | `goal_id = activeGoalId OR created_at > now()-7d`, ranked by score |
| `WorkingMemoryLayer` | `working_memories` | 400 tok | short — auto-evicts on `goal:completed`/`goal:abandoned` via Writer | `goal_id = activeGoalId AND invalid_at IS NULL` |
| `ProceduralMemoryLayer` | `procedural_memories` | 200 tok | long — skill recipes/success patterns | `(skill_ref ∈ active_skills) OR (subkind = "success_pattern")` ranked by `success_count` |

**Total default budget: 2000 tokens across all four layers.** Tunable per-engine. **Not tunable per-channel** — channel-parity rule. If a channel can't render the same context as another, fix the gateway, not the memory budget.

### 6.2 Common protocol

```
class MemoryLayer implements ContextPipeline.Layer {
  constructor(private cfg: {
    name: string,
    table: MemoryTable,
    budgetTokens: number,
    queryBuilder: (ctx) => MemoryQuery,
  }) {}

  async render(ctx: PipelineContext): Promise<RenderedLayer> {
    const query = this.cfg.queryBuilder(ctx)
    const candidates = await this.repo.search(this.cfg.table, query, {
      limit: 50,                         // cosine top-50
      embedding: ctx.queryEmbedding,
    })
    const ranked = candidates
      .map(m => ({ m, score: this.score(m, ctx) }))
      .sort((a, b) => b.score - a.score)
    const packed = packToBudget(ranked, this.cfg.budgetTokens)
    this.eventBus.emit("memory:accessed", {
      ids: packed.map(p => p.m.id),
      sourceLayer: this.cfg.name,
    })
    return { name: this.cfg.name, content: format(packed) }
  }

  private score(m: Memory, ctx: PipelineContext): number {
    const recency    = Math.exp(-LAMBDA * hoursSince(m.last_accessed_at ?? m.created_at))
    const importance = m.importance
    const relevance  = cosine(ctx.queryEmbedding, m.embedding)
    return ALPHA * recency + BETA * importance + GAMMA * relevance
    // v2: ALPHA/BETA/GAMMA become DNA-coupled (move #3)
  }
}
```

### 6.3 `memory:accessed` semantics

Fired only when a memory is **included in a rendered prompt** (passed `packToBudget`'s budget cut), not when it merely appears in the cosine top-50. Repository increments `access_count` and updates `last_accessed_at` on receipt. This is the only feedback path from Layer back to Repository; layers remain read-only otherwise.

### 6.4 Reflexive exclusion

`reflexive_memories` is deliberately not rendered. Failure traces in the prompt is the documented Galileo/MindStudio failure mode and inverts the GoalVerifier's purpose. There is no `ReflexiveMemoryLayer` class to enable. Operator dump path (`/memory dump reflexive`) is the only read surface.

## 7. Repository surface

### 7.1 Internal typed surface (Writer + Layer)

```
class Repository {
  // READS
  search(table: MemoryTable, q: MemoryQuery, opts: { limit, embedding? }): Promise<Memory[]>
  searchSemanticByEntity(entities: string[], opts: { validOnly: boolean }): Promise<SemanticMemory[]>
  getById(id: string): Promise<Memory | null>
  history(id: string): Promise<Array<MemoryEvent>>

  // WRITES (Writer only)
  insertBatch(rows: NewMemory[]): Promise<{ ids: string[] }>
  invalidate(id: string, reason: string, sourceEvent?: string): Promise<InvalidateResult>
  appendInvalidations(rows: InvalidationRow[]): Promise<void>
  appendContradictions(rows: ContradictionRow[]): Promise<void>

  // FEEDBACK (Layer only)
  recordAccess(ids: string[], sourceLayer: string): Promise<void>

  // OPERATOR (gateway command + heartbeat)
  stats(): Promise<MemoryStats>
  export(opts: { kind?, since? }): AsyncIterable<Memory>
  evolveCandidates(): Promise<Memory[]>
}
```

All methods return typed `Memory*` types. No generic `Record<string, unknown>`. This is what replaces `rawDb`.

### 7.2 LLM tool — `memory`

```
Tool: memory
Description: Search the assistant's long-term memory or invalidate a known-stale fact.
Actions:
  - search:
      query: string
      kind?: "semantic"|"episodic"|"working"|"procedural"
      goalId?: string
      includeInvalidated?: boolean       # default false
      limit?: number                     # default 10
    Returns: { results: Array<{ id, kind, content, importance, validFrom, invalidatedAt? }> }

  - invalidate:
      id: string
      reason: string                     # required
    Returns: { success, invalidatedAt } | { success: false, pending: true, reason: "high_importance_review" }
```

**Approval gate:** if target memory has `importance ≥ 0.8`, Repository routes the invalidation through `HitlChannel` instead of executing immediately. The LLM gets `{ success: false, pending: true }`. The user approves/rejects via `/memory invalidate <id> --confirm` or the queued HITL prompt. Threshold tunable (`memory.invalidate.approval_threshold`, default `0.8`).

**Default search excludes** `BLOCKED`/`PARTIAL` verdicts and reflexive memories. Both opt-in via explicit flags only available to operator surface, not the LLM.

### 7.3 Operator surface — `/memory`

Gateway-uniform across CLI, Telegram, web. Routed through a shared `MemoryCommandRouter` (same pattern as Element 7d's `McpCommandRouter`).

| Verb | Behavior |
|---|---|
| `/memory` or `/memory list [--kind X]` | Recent memories grouped by kind, with verdict + validity |
| `/memory stats` | Counts by kind/verdict/validity; storage size; cache hit rate |
| `/memory search <query>` | Same as LLM tool, operator format |
| `/memory show <id>` | Full record + history (creations + invalidations + contradictions) |
| `/memory invalidate <id> [--reason "..."]` | Bypass-approval invalidate (operator authority) |
| `/memory restore <id>` | Clear `invalid_at` — recovery for wrongful invalidations |
| `/memory export [--kind X] [--since DATE]` | Stream as NDJSON |
| `/memory evolve` | Manually trigger consolidation (idle-window job) |
| `/memory contradictions` | List unresolved entries from `memory_contradictions` |
| `/memory dump reflexive` | Operator-only read of `reflexive_memories` |

Identical surface across CLI, Telegram, web. No verb is channel-specific.

## 8. Error handling & observability

### 8.1 Failure-domain rules

1. **Memory is best-effort. Never blocks a user-facing turn.** Writer ingestion runs after `turn:completed`; failures are logged + emitted, never thrown.
2. **Layer rendering degrades gracefully.** A failed `MemoryLayer.render()` returns empty content; other layers and the prompt are unaffected.
3. **Repository is the only path that throws.** Writer and Layer catch all Repository errors and convert to events. Repository throws on programmer errors only.

### 8.2 Error events

| Event | Emitter | Payload | Purpose |
|---|---|---|---|
| `memory:write_failed` | Writer | `{ source, candidates, error }` | Operator alert; usually disk/schema |
| `memory:classify_failed` | Writer | `{ turnId, error }` | Treated as zero-candidates; no DB write |
| `memory:contradict_failed` | Writer | `{ candidate, error }` | New row persists without invalidating older; row lands in `memory_contradictions` with `resolution="check_failed"` |
| `memory:render_failed` | MemoryLayer | `{ layerName, error }` | Operator signal; layer returns empty |
| `memory:invalidate_rejected` | Repository | `{ id, reason }` | Approval gate triggered |

### 8.3 Structured logs

Every Repository write/invalidate logs one structured line:
```
{ ts, op: "insert"|"invalidate"|"access", id, kind, verdict, goalId, sourceLayer? }
```
Append-only, JSON-per-line, rotated daily by the existing logger.

### 8.4 Metrics — backed by `memory_metrics(captured_at, key, value)`

Repository keeps in-memory counters, flushed every 60s:

```
writes.{kind}.{verdict}              counter
invalidations.{reason}               counter
contradictions.detected              counter
contradictions.auto_resolved         counter
contradictions.deferred              counter
layer.{name}.rendered_ids            histogram
layer.{name}.budget_used_pct         histogram
classify.latency_ms                  histogram
contradict.latency_ms                histogram
search.latency_ms                    histogram
search.cache_hit_rate                gauge
```

By going SQLite-backed from day one, none of these evaporate on restart (unlike the existing JSON-only ToolTracker).

### 8.5 Health check

Every 5 minutes (heartbeat-driven):
1. `SELECT COUNT(*) FROM semantic_memories` returns in <100ms.
2. Embedding sanity: random row's embedding decodes to expected dimension.
3. If `verdict_unknown_rate > 5%` for 1h → `memory:health_degraded`.

### 8.6 SLOs and graceful degradation

```
SLO: memory.llm_calls_per_turn p95 < 1.5
SLO: memory.classify.latency p95 < 400ms
SLO: memory.total_overhead_per_turn p95 < 600ms
```

Breach >5min raises `memory:slo_breach`. On sustained breach, `memory.contradiction_check.enabled` flips to `false`. Writer keeps writing; contradictions queue to `memory_contradictions` with `resolution="check_skipped"` for v2 batch resolution. **Move #4 degrades gracefully, never crashes the assistant.**

## 9. Testing

### 9.1 Test layers

| Layer | File | Approx. count |
|---|---|---|
| Unit — Repository | `__tests__/memory/repository.test.ts` | ~25 |
| Unit — Writer | `__tests__/memory/writer.test.ts` | ~30 |
| Unit — Layer | `__tests__/memory/layer.test.ts` | ~20 |
| Integration | `__tests__/memory/integration.test.ts` | ~15 |
| Migration | `__tests__/migrations/v25/*.test.ts` | ~10 |
| Performance | `__tests__/memory/perf.test.ts` | ~5 |

**Total: ~105 new tests.** Existing test count must not decrease (the `rawDb`-consumer migrations bring tests with them).

### 9.2 Coverage requirements

- Repository: every public method has a happy-path + at least one error-path test.
- Writer: each branch in §5.2's pipeline has a dedicated test. Each event subscription verified.
- Layer: each of the four layers verified independently. Reflexive exclusion verified (negative test on import).
- Integration: real SQLite, mocked router/embedder. 50-turn scenario covering all verdicts. Channel-parity check across CLI/Telegram-stub/web-stub adapters (identical token counts and rendered memory).
- Migration: three corpus snapshots (`empty`, `realistic`, `stress`). Pre/post row counts identical. Migration fits SLO (<2s realistic, <30s stress).
- Performance: `Repository.search` p95 <50ms; `Writer.ingest` (DB+events only) p95 <20ms; full pipeline render p95 <100ms.

### 9.3 What is not tested in v1

- v2 features (parliament retention, DNA-coupled retrieval, Zettelkasten link traversal, Ebbinghaus decay).
- Multi-process concurrency.
- Embedding drift between v24 and v25 embedders.

## 10. Open items deferred to v2

- **Move #2 — parliament-debated retention.** Trigger condition: `memory_contradictions.resolution = "deferred"` row count exceeds threshold. Parliament adjudicates. Output written as a synthesized `semantic_memories` row, both contradictory rows invalidated.
- **Move #3 — DNA-coupled retrieval.** `ALPHA/BETA/GAMMA` in §6.2 read from owl DNA per render. Memory in turn drives DNA mutation: success patterns reinforce traits, contradictions weaken trait stability.
- **A-MEM Zettelkasten links.** v1 schema (`memory_links`) is allocated. v2 populates on Writer.ingest and traverses on Layer.render.
- **Hybrid retrieval.** If cosine recall proves insufficient, add BM25 (FTS5) and entity-overlap as additional re-ranking terms in `score()`.
- **Ebbinghaus strength-based decay.** `recency = exp(-hours / strength)` where strength compounds on successful retrieval.

## 11. Risk register

| Risk | Mitigation |
|---|---|
| Migration data loss | Pre-flight backup; row-count verification; refuse to bump `schema_version` on mismatch. |
| `IntelligenceRouter` cost overrun | Two short-circuit guards (§5.2 steps 1+3). Steady-state ~70% turns = 0 calls. SLO breach flips contradiction check off. |
| LLM over-invalidates via tool | Approval gate at `importance ≥ 0.8`. Bitemporal model means invalidate is reversible (`/memory restore`). |
| Reflexive memories leak into prompt | No `ReflexiveMemoryLayer` class exists. Negative test in §9. |
| Channel context divergence | Channel-parity rule. No per-channel tuning in memory module. |
| `rawDb` breach reintroduced | Final migration commit removes the accessor entirely. Reintroducing fails to compile. |

## 12. Acceptance criteria

- Three new files under `src/memory/`: `repository.ts`, `writer.ts`, `layer.ts`. No more.
- v25 migration executes on `empty`, `realistic`, and `stress` corpora with zero row loss.
- All 9 outside `rawDb` consumers migrated. `MemoryDatabase.rawDb` accessor removed.
- ~12 memory tables remain (5 kinds + 3 substrate + 4 linkage). Down from 41.
- Four `MemoryLayer` instances registered in engine boot. `reflexive_memories` not rendered anywhere in prompt path.
- LLM `memory(action: "search" | "invalidate")` tool exposed; no other write surface.
- Operator `/memory` command works identically across CLI, Telegram, web.
- ~105 new tests pass. Existing test count not decreased.
- SLOs in §8.6 met on `realistic` corpus performance benchmarks.

---

**Spec inputs (re-listed for traceability):**
- Phase 1 audit: `_bmad-output/planning-artifacts/element15-memory-architecture-audit-2026-05-03.md`
- Phase 2 research: `_bmad-output/planning-artifacts/research/market-stackowl-element15-memory-db-research-2026-05-03.md`

**Brainstorm decisions (Q1–Q9 locked by Boss, 2026-05-03):**
- Q1: B (v1 ships moves 1+4+5; v2 ships 2+3)
- Q2: B (three new files: repository.ts, writer.ts, layer.ts)
- Q3: C (collapse semantic-ish + working tables; ~12 tables; bitemporal)
- Q4: B (`verifier_verdict` column; default filter excludes BLOCKED/PARTIAL; UNVERIFIED is fifth state)
- Q5: B (LLM tools = `memory(action: "search" | "invalidate")`; writes automatic)
- Q6: C (cosine top-50, re-rank by `α·recency + β·importance + γ·relevance`)
- Q7: C (full evented invalidation incl. contradiction-detection on write)
- Q8: B with channel-agnostic budgets (per Boss correction — channel parity is a gateway-layer rule)
- Q9: C (classify-first with two short-circuit guards)
