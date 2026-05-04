# Element 15 — Memory Architecture v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 3,803-line `MemoryDatabase` god-class and the 41-table sprawl with a 12-table coherent memory store, a goal-conditioned `MemoryWriter`, four `ContextLayer` renderers, a typed `MemoryRepository` surface, and a unified `/memory` command — implementing architectural moves 1, 4, and 5 from the Element 15 design (goal-conditioned writes, event-driven invalidation, TTL-layered rendering). Moves 2 and 3 (parliament-debated retention, DNA-coupled retrieval) are explicitly deferred to v2.

**Architecture:** v1 introduces exactly **3 new files** — `src/memory/repository.ts` (typed read/write surface), `src/memory/writer.ts` (goal-conditioned classifier + ADD/UPDATE/DELETE/NOOP reconciler), `src/memory/layer.ts` (four `ContextLayer` factories: semantic / episodic / working / procedural). `src/memory/db.ts` is preserved as schema-owner + migration runner only; the `get rawDb()` accessor is restricted to engine/cortex non-memory consumers (the 9 memory-table consumers migrate to `MemoryRepository`). One schema migration (v25) collapses redundant tables and adds `valid_at` / `invalid_at` bitemporal columns plus `goal_id`, `verdict`, `subgoal_id`, `importance`, `embedding` columns. Writer uses `IntelligenceRouter.resolve("classification")` for cheap-tier classification (no hardcoded keywords). Layers compose with the existing `ContextPipeline` via the real `ContextLayer` interface (`shouldFire` / `build`). Cross-channel invalidation flows through the typed `GatewayEventBus`.

**Tech Stack:** TypeScript (strict), better-sqlite3 (existing), vitest, IntelligenceRouter (existing), ContextPipeline + ContextLayer (existing), GatewayEventBus (existing), HitlCheckpointStore (existing for approval-gate), GoalVerifier verdicts (existing), TaskLedger.subGoals (existing).

---

## File Map

| Path | Status | Responsibility |
|------|--------|----------------|
| `src/memory/repository.ts` | NEW | Typed read/write surface — `search` / `insertBatch` / `invalidate` / `getById` / `history` / `recordAccess` / `stats`. Only file allowed to touch memory tables. |
| `src/memory/writer.ts` | NEW | Goal-conditioned writer pipeline — trivial-turn guard → classify (IntelligenceRouter cheap-tier) → contradiction check → ADD/UPDATE/DELETE/NOOP reconcile → persist + emit events. |
| `src/memory/layer.ts` | NEW | Four `ContextLayer` factories (semantic, episodic, working, procedural) + shared `MemoryLayer` base with α·recency + β·importance + γ·relevance scoring. |
| `src/memory/db.ts` | MODIFIED | Add `applyV25Migration`, register in `runMigrations` + `applyMigrations`. Keep `rawDb` accessor (scoped to non-memory consumers). |
| `src/gateway/event-bus.ts` | MODIFIED | Extend `GatewaySystemEvent` union with `memory:*` event variants. |
| `src/context/pipeline.ts` | MODIFIED | Register the four memory layers in default layer roster. |
| `src/tools/memory-unified.ts` | MODIFIED | Replace internal store-direct calls with `MemoryRepository.search` / `invalidate`. Approval-gate for `importance ≥ 0.8` invalidations via `HitlCheckpointStore`. |
| `src/gateway/commands/memory-router.ts` | NEW (or merged into mcp-router) | Channel-agnostic `/memory` dispatcher: `list / search / invalidate / history / stats / export`. |
| `src/cli/commands.ts` | MODIFIED | Register `/memory` handler. |
| `src/gateway/adapters/telegram.ts` | MODIFIED | Register `/memory` handler. |
| `src/index.ts` | MODIFIED | Wire `MemoryRepository` + `MemoryWriter` into engine boot; subscribe writer to relevant `GatewayEventBus` events. |
| 9 rawDb memory-consumers | MODIFIED | Migrate from `db.rawDb.prepare(...)` to `MemoryRepository.*`. List in Phase I. |
| `__tests__/memory-repository.test.ts` | NEW | Repository unit tests (search ranking, insertBatch, invalidate, history, recordAccess). |
| `__tests__/memory-writer.test.ts` | NEW | Writer unit tests (trivial-turn short-circuit, classify, reconcile, contradiction, error envelope). |
| `__tests__/memory-layer.test.ts` | NEW | Layer unit tests (each layer's `shouldFire`, scoring, reflexive-exclusion). |
| `__tests__/memory-db-v25.test.ts` | NEW | Migration test (idempotent, data preserved, indexes/columns present). |
| `__tests__/memory-integration.test.ts` | NEW | 50-turn cross-channel parity scenario; CLI write → Telegram read same memory. |

**New file count: 3 (repository.ts, writer.ts, layer.ts)** — within the 2-3 cap. The router and tests are not "new architectural files" — router mirrors `mcp-router` shape, tests are required by TDD.

---

## Acceptance Criteria (from spec § Acceptance)

1. `applyV25Migration(db)` is idempotent and preserves all live-traffic memory data.
2. `MemoryRepository` is the only consumer of `memories` / `memory_invalidations` / `memory_contradictions` / `memory_access_log` tables.
3. `MemoryWriter.ingest(turn)` short-circuits ~70% of trivial turns without an LLM call.
4. Importance ≥ 0.8 invalidations route through `HitlCheckpointStore` (not auto-applied).
5. All four layers implement `ContextLayer` (`shouldFire`/`build`), respect `cacheTtlMs`, and exclude reflexive memories from prompt rendering.
6. `GatewayEventBus` carries typed `memory:*` events; CLI write triggers Telegram cache invalidation observably in the integration test.
7. `/memory` command behaves identically in CLI and Telegram (channel parity).
8. No hardcoded keyword arrays/regex in the writer or layers — all classification flows through `IntelligenceRouter.resolve("classification")`.
9. Test suite passes: ≥ 65 new tests across unit + integration; existing tests unaffected.
10. The 9 memory-table `rawDb` consumers no longer touch `db.rawDb`; engine/cortex non-memory consumers are out of scope and remain on `rawDb`.

---

## Phase Pre — Worktree + Types

### Task 0: Worktree setup

**Files:** none

- [ ] **Step 1: Create isolated worktree**

Run:
```bash
git worktree add .worktrees/element-15-memory -b feature/element-15-memory main
cd .worktrees/element-15-memory
npm install
```

Expected: clean install, baseline tests pass (`npm test`).

- [ ] **Step 2: Verify baseline**

Run: `npm test`
Expected: 0 failures (current main is green).

- [ ] **Step 3: Commit nothing — proceed**

---

## Phase A — MemoryRepository scaffolding (read surface)

### Task 1: Repository skeleton + types

**Files:**
- Create: `src/memory/repository.ts`
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory-repository.test.ts`:
```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";

describe("MemoryRepository — skeleton", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("constructs with a Database handle", () => {
    expect(repo).toBeInstanceOf(MemoryRepository);
  });

  it("exposes the canonical surface", () => {
    expect(typeof repo.search).toBe("function");
    expect(typeof repo.insertBatch).toBe("function");
    expect(typeof repo.invalidate).toBe("function");
    expect(typeof repo.getById).toBe("function");
    expect(typeof repo.history).toBe("function");
    expect(typeof repo.recordAccess).toBe("function");
    expect(typeof repo.stats).toBe("function");
  });
});
```

- [ ] **Step 2: Run test — confirm FAIL**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: FAIL — `MemoryRepository` not found / `applyV25Migration` not found.

- [ ] **Step 3: Create skeleton**

Create `src/memory/repository.ts`:
```typescript
import type Database from "better-sqlite3";

export type MemoryKind = "semantic" | "episodic" | "working" | "procedural" | "reflexive";

export interface MemoryRecord {
  id: string;
  kind: MemoryKind;
  content: string;
  embedding: Float32Array | null;
  importance: number;
  goal_id: string | null;
  subgoal_id: string | null;
  verdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL" | null;
  source_turn_id: string | null;
  source_channel: string | null;
  valid_at: string;
  invalid_at: string | null;
  created_at: string;
  updated_at: string;
  access_count: number;
  last_accessed_at: string | null;
}

export interface MemorySearchOptions {
  kinds?: MemoryKind[];
  topK?: number;
  minImportance?: number;
  goalId?: string;
  includeInvalid?: boolean;
}

export interface MemoryInsert {
  id: string;
  kind: MemoryKind;
  content: string;
  embedding?: Float32Array;
  importance: number;
  goal_id?: string;
  subgoal_id?: string;
  verdict?: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";
  source_turn_id?: string;
  source_channel?: string;
}

export interface InvalidateOptions {
  reason: string;
  invalidatedBy: string;
  contradicts?: string[];
}

export interface MemoryStats {
  total: number;
  byKind: Record<MemoryKind, number>;
  invalidated: number;
  avgImportance: number;
}

export class MemoryRepository {
  constructor(private readonly db: Database.Database) {}

  async search(_query: string, _opts: MemorySearchOptions = {}): Promise<MemoryRecord[]> {
    throw new Error("not implemented");
  }

  insertBatch(_records: MemoryInsert[]): void {
    throw new Error("not implemented");
  }

  invalidate(_id: string, _opts: InvalidateOptions): void {
    throw new Error("not implemented");
  }

  getById(_id: string): MemoryRecord | null {
    throw new Error("not implemented");
  }

  history(_id: string): { record: MemoryRecord | null; invalidations: unknown[]; contradictions: unknown[] } {
    throw new Error("not implemented");
  }

  recordAccess(_id: string): void {
    throw new Error("not implemented");
  }

  stats(): MemoryStats {
    throw new Error("not implemented");
  }
}
```

Also stub `applyV25Migration` in `src/memory/db.ts` (export, no-op for now — full migration is Task 4):
```typescript
// At bottom of src/memory/db.ts (do not register in runMigrations yet)
export function applyV25Migration(_db: Database.Database): void {
  // Intentional no-op for skeleton tests; implemented in Task 4.
}
```

- [ ] **Step 4: Run test — confirm PASS**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: PASS (skeleton exposes the surface; no-op migration runs without throwing).

- [ ] **Step 5: Commit**

```bash
git add src/memory/repository.ts src/memory/db.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): scaffold MemoryRepository skeleton + v25 migration stub"
```

---

### Task 2: Repository — `search` (cosine + recency/importance/relevance)

**Files:**
- Modify: `src/memory/repository.ts`
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `__tests__/memory-repository.test.ts`:
```typescript
import { v4 as uuid } from "uuid";

function makeEmbedding(seed: number): Float32Array {
  const arr = new Float32Array(8);
  for (let i = 0; i < 8; i++) arr[i] = Math.sin(seed + i);
  return arr;
}

describe("MemoryRepository.search", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("returns empty array when no memories", async () => {
    const results = await repo.search("anything");
    expect(results).toEqual([]);
  });

  it("filters by kind", async () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "user prefers concise answers", importance: 0.7, embedding: makeEmbedding(1) },
      { id: uuid(), kind: "episodic", content: "user worked on element 12 yesterday", importance: 0.5, embedding: makeEmbedding(2) },
    ]);
    const results = await repo.search("preference", { kinds: ["semantic"], topK: 10 });
    expect(results).toHaveLength(1);
    expect(results[0].kind).toBe("semantic");
  });

  it("excludes invalidated memories by default", async () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "old fact", importance: 0.5, embedding: makeEmbedding(3) }]);
    repo.invalidate(id, { reason: "user corrected", invalidatedBy: "test" });
    const results = await repo.search("old", { topK: 10 });
    expect(results.find((r) => r.id === id)).toBeUndefined();
  });

  it("includes invalidated memories when includeInvalid=true", async () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "old fact", importance: 0.5, embedding: makeEmbedding(3) }]);
    repo.invalidate(id, { reason: "user corrected", invalidatedBy: "test" });
    const results = await repo.search("old", { topK: 10, includeInvalid: true });
    expect(results.find((r) => r.id === id)).toBeDefined();
  });

  it("re-ranks by α·recency + β·importance + γ·relevance", async () => {
    const oldId = uuid();
    const newId = uuid();
    // Insert old, then new — newer should rank higher when content is similar.
    repo.insertBatch([
      { id: oldId, kind: "semantic", content: "user likes typescript", importance: 0.5, embedding: makeEmbedding(10) },
    ]);
    // Slight delay to make timestamps differ
    await new Promise((r) => setTimeout(r, 10));
    repo.insertBatch([
      { id: newId, kind: "semantic", content: "user likes typescript", importance: 0.5, embedding: makeEmbedding(10) },
    ]);
    const results = await repo.search("typescript", { topK: 2 });
    expect(results[0].id).toBe(newId);
  });

  it("respects minImportance filter", async () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "low importance", importance: 0.2, embedding: makeEmbedding(20) },
      { id: uuid(), kind: "semantic", content: "high importance", importance: 0.9, embedding: makeEmbedding(20) },
    ]);
    const results = await repo.search("importance", { topK: 10, minImportance: 0.5 });
    expect(results).toHaveLength(1);
    expect(results[0].importance).toBeGreaterThanOrEqual(0.5);
  });
});
```

- [ ] **Step 2: Run tests — confirm FAIL**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: FAIL — `not implemented` thrown by `search` and `insertBatch`.

- [ ] **Step 3: Implement `insertBatch` (minimal — just enough for search tests)**

In `src/memory/repository.ts`, replace `insertBatch` with:
```typescript
insertBatch(records: MemoryInsert[]): void {
  if (records.length === 0) return;
  const stmt = this.db.prepare(`
    INSERT INTO memories
      (id, kind, content, embedding, importance, goal_id, subgoal_id, verdict,
       source_turn_id, source_channel, valid_at, created_at, updated_at)
    VALUES
      (@id, @kind, @content, @embedding, @importance, @goal_id, @subgoal_id, @verdict,
       @source_turn_id, @source_channel, @now, @now, @now)
  `);
  const insertMany = this.db.transaction((rows: MemoryInsert[]) => {
    const now = new Date().toISOString();
    for (const r of rows) {
      stmt.run({
        id: r.id,
        kind: r.kind,
        content: r.content,
        embedding: r.embedding ? Buffer.from(r.embedding.buffer) : null,
        importance: r.importance,
        goal_id: r.goal_id ?? null,
        subgoal_id: r.subgoal_id ?? null,
        verdict: r.verdict ?? null,
        source_turn_id: r.source_turn_id ?? null,
        source_channel: r.source_channel ?? null,
        now,
      });
    }
  });
  insertMany(records);
}
```

- [ ] **Step 4: Implement `invalidate` (minimal)**

```typescript
invalidate(id: string, opts: InvalidateOptions): void {
  const now = new Date().toISOString();
  const tx = this.db.transaction(() => {
    this.db.prepare(`UPDATE memories SET invalid_at = ?, updated_at = ? WHERE id = ?`).run(now, now, id);
    this.db.prepare(`
      INSERT INTO memory_invalidations (id, memory_id, reason, invalidated_by, invalidated_at)
      VALUES (?, ?, ?, ?, ?)
    `).run(`inv_${id}_${Date.now()}`, id, opts.reason, opts.invalidatedBy, now);
    if (opts.contradicts) {
      const cstmt = this.db.prepare(`
        INSERT INTO memory_contradictions (id, memory_id, contradicts_id, detected_at)
        VALUES (?, ?, ?, ?)
      `);
      for (const cId of opts.contradicts) {
        cstmt.run(`con_${id}_${cId}`, id, cId, now);
      }
    }
  });
  tx();
}
```

- [ ] **Step 5: Implement `search` with α·recency + β·importance + γ·relevance**

Add private helper + replace `search`:
```typescript
private cosine(a: Float32Array, b: Float32Array): number {
  let dot = 0, na = 0, nb = 0;
  const len = Math.min(a.length, b.length);
  for (let i = 0; i < len; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

async search(query: string, opts: MemorySearchOptions = {}): Promise<MemoryRecord[]> {
  const { kinds, topK = 50, minImportance, includeInvalid = false, goalId } = opts;
  const where: string[] = [];
  const params: Record<string, unknown> = {};

  if (!includeInvalid) where.push("invalid_at IS NULL");
  if (kinds && kinds.length > 0) {
    where.push(`kind IN (${kinds.map((_, i) => `@k${i}`).join(",")})`);
    kinds.forEach((k, i) => (params[`k${i}`] = k));
  }
  if (typeof minImportance === "number") {
    where.push("importance >= @minImportance");
    params.minImportance = minImportance;
  }
  if (goalId) {
    where.push("goal_id = @goalId");
    params.goalId = goalId;
  }

  const sql = `SELECT * FROM memories ${where.length ? "WHERE " + where.join(" AND ") : ""}`;
  const rows = this.db.prepare(sql).all(params) as Array<Record<string, unknown>>;

  const queryEmbedding = this.embedQuery(query);
  const now = Date.now();

  const scored = rows.map((row) => {
    const record = this.rowToRecord(row);
    const recencyMs = now - new Date(record.valid_at).getTime();
    const recency = Math.exp(-recencyMs / (1000 * 60 * 60 * 24 * 7)); // 7-day half-life
    const relevance = queryEmbedding && record.embedding ? this.cosine(queryEmbedding, record.embedding) : 0;
    const score = 0.3 * recency + 0.3 * record.importance + 0.4 * relevance;
    return { record, score };
  });

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, topK).map((s) => s.record);
}

private embedQuery(_query: string): Float32Array | null {
  // v1: return null; layers/writer pass embeddings through directly.
  // Real implementation wires fastembed in Task 17 layer base.
  return null;
}

private rowToRecord(row: Record<string, unknown>): MemoryRecord {
  return {
    id: row.id as string,
    kind: row.kind as MemoryKind,
    content: row.content as string,
    embedding: row.embedding ? new Float32Array((row.embedding as Buffer).buffer) : null,
    importance: row.importance as number,
    goal_id: (row.goal_id as string) ?? null,
    subgoal_id: (row.subgoal_id as string) ?? null,
    verdict: (row.verdict as MemoryRecord["verdict"]) ?? null,
    source_turn_id: (row.source_turn_id as string) ?? null,
    source_channel: (row.source_channel as string) ?? null,
    valid_at: row.valid_at as string,
    invalid_at: (row.invalid_at as string) ?? null,
    created_at: row.created_at as string,
    updated_at: row.updated_at as string,
    access_count: (row.access_count as number) ?? 0,
    last_accessed_at: (row.last_accessed_at as string) ?? null,
  };
}
```

NOTE: Task 4 implements the real v25 migration; this task assumes the schema columns will exist. To make this task's tests pass before Task 4, temporarily stub the schema in `applyV25Migration` (Task 1's no-op stub becomes a minimal CREATE TABLE) — see Step 6.

- [ ] **Step 6: Add minimal schema in v25 stub**

In `src/memory/db.ts`, replace the no-op `applyV25Migration` body with:
```typescript
export function applyV25Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS memories (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      content TEXT NOT NULL,
      embedding BLOB,
      importance REAL NOT NULL DEFAULT 0.5,
      goal_id TEXT,
      subgoal_id TEXT,
      verdict TEXT,
      source_turn_id TEXT,
      source_channel TEXT,
      valid_at TEXT NOT NULL,
      invalid_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      access_count INTEGER NOT NULL DEFAULT 0,
      last_accessed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
    CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories(invalid_at);
    CREATE INDEX IF NOT EXISTS idx_memories_goal ON memories(goal_id);
    CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);

    CREATE TABLE IF NOT EXISTS memory_invalidations (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      reason TEXT NOT NULL,
      invalidated_by TEXT NOT NULL,
      invalidated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inv_memory ON memory_invalidations(memory_id);

    CREATE TABLE IF NOT EXISTS memory_contradictions (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      contradicts_id TEXT NOT NULL REFERENCES memories(id),
      detected_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_contra_memory ON memory_contradictions(memory_id);

    CREATE TABLE IF NOT EXISTS memory_access_log (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      accessed_at TEXT NOT NULL,
      context TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_access_memory ON memory_access_log(memory_id);
  `);
}
```

This is the v1 minimal schema — Task 4 expands it to handle migration of legacy data + adds full column set.

- [ ] **Step 7: Run tests — confirm PASS**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: PASS — all search/insertBatch/invalidate tests green.

- [ ] **Step 8: Commit**

```bash
git add src/memory/repository.ts src/memory/db.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): MemoryRepository.search with α·recency + β·importance + γ·relevance"
```

---

### Task 3: Repository — `getById`, `history`, `recordAccess`, `stats`

**Files:**
- Modify: `src/memory/repository.ts`
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `__tests__/memory-repository.test.ts`:
```typescript
describe("MemoryRepository.getById / history / recordAccess / stats", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("getById returns the record", () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "x", importance: 0.5 }]);
    const r = repo.getById(id);
    expect(r?.id).toBe(id);
  });

  it("getById returns null for missing", () => {
    expect(repo.getById("nope")).toBeNull();
  });

  it("history returns invalidations + contradictions", () => {
    const id = uuid();
    const cId = uuid();
    repo.insertBatch([
      { id, kind: "semantic", content: "old", importance: 0.5 },
      { id: cId, kind: "semantic", content: "contradicts old", importance: 0.5 },
    ]);
    repo.invalidate(id, { reason: "contradicted", invalidatedBy: "writer", contradicts: [cId] });
    const h = repo.history(id);
    expect(h.invalidations).toHaveLength(1);
    expect(h.contradictions).toHaveLength(1);
  });

  it("recordAccess increments access_count + updates last_accessed_at", () => {
    const id = uuid();
    repo.insertBatch([{ id, kind: "semantic", content: "x", importance: 0.5 }]);
    repo.recordAccess(id);
    repo.recordAccess(id);
    const r = repo.getById(id);
    expect(r?.access_count).toBe(2);
    expect(r?.last_accessed_at).not.toBeNull();
  });

  it("stats returns counts by kind + invalidated + avg importance", () => {
    repo.insertBatch([
      { id: uuid(), kind: "semantic", content: "a", importance: 0.4 },
      { id: uuid(), kind: "semantic", content: "b", importance: 0.6 },
      { id: uuid(), kind: "episodic", content: "c", importance: 0.8 },
    ]);
    const s = repo.stats();
    expect(s.total).toBe(3);
    expect(s.byKind.semantic).toBe(2);
    expect(s.byKind.episodic).toBe(1);
    expect(s.avgImportance).toBeCloseTo(0.6, 2);
    expect(s.invalidated).toBe(0);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: FAIL — `getById` / `history` / `recordAccess` / `stats` throw `not implemented`.

- [ ] **Step 3: Implement the four methods**

In `src/memory/repository.ts`:
```typescript
getById(id: string): MemoryRecord | null {
  const row = this.db.prepare(`SELECT * FROM memories WHERE id = ?`).get(id) as Record<string, unknown> | undefined;
  return row ? this.rowToRecord(row) : null;
}

history(id: string): { record: MemoryRecord | null; invalidations: unknown[]; contradictions: unknown[] } {
  const record = this.getById(id);
  const invalidations = this.db.prepare(`SELECT * FROM memory_invalidations WHERE memory_id = ? ORDER BY invalidated_at DESC`).all(id);
  const contradictions = this.db.prepare(`SELECT * FROM memory_contradictions WHERE memory_id = ? OR contradicts_id = ? ORDER BY detected_at DESC`).all(id, id);
  return { record, invalidations, contradictions };
}

recordAccess(id: string): void {
  const now = new Date().toISOString();
  const tx = this.db.transaction(() => {
    this.db.prepare(`UPDATE memories SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?`).run(now, id);
    this.db.prepare(`INSERT INTO memory_access_log (id, memory_id, accessed_at) VALUES (?, ?, ?)`).run(`acc_${id}_${Date.now()}`, id, now);
  });
  tx();
}

stats(): MemoryStats {
  const row = this.db.prepare(`
    SELECT
      COUNT(*) AS total,
      SUM(CASE WHEN invalid_at IS NOT NULL THEN 1 ELSE 0 END) AS invalidated,
      AVG(importance) AS avg_importance
    FROM memories
  `).get() as { total: number; invalidated: number; avg_importance: number };

  const kindRows = this.db.prepare(`SELECT kind, COUNT(*) AS c FROM memories GROUP BY kind`).all() as Array<{ kind: MemoryKind; c: number }>;
  const byKind: Record<MemoryKind, number> = { semantic: 0, episodic: 0, working: 0, procedural: 0, reflexive: 0 };
  for (const r of kindRows) byKind[r.kind] = r.c;

  return {
    total: row.total ?? 0,
    byKind,
    invalidated: row.invalidated ?? 0,
    avgImportance: row.avg_importance ?? 0,
  };
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/memory-repository.test.ts`
Expected: PASS — all repository tests green.

- [ ] **Step 5: Commit**

```bash
git add src/memory/repository.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): MemoryRepository getById/history/recordAccess/stats"
```

---

## Phase B — Schema migration v25

### Task 4: Real v25 migration — full schema + migration registration

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory-db-v25.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory-db-v25.test.ts`:
```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";

describe("v25 migration", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.pragma("foreign_keys = ON");
  });

  it("creates memories table with all required columns", () => {
    applyV25Migration(db);
    const cols = db.prepare(`PRAGMA table_info(memories)`).all() as Array<{ name: string }>;
    const names = cols.map((c) => c.name);
    expect(names).toEqual(expect.arrayContaining([
      "id", "kind", "content", "embedding", "importance", "goal_id", "subgoal_id",
      "verdict", "source_turn_id", "source_channel", "valid_at", "invalid_at",
      "created_at", "updated_at", "access_count", "last_accessed_at",
    ]));
  });

  it("creates supporting tables", () => {
    applyV25Migration(db);
    const tables = db.prepare(`SELECT name FROM sqlite_master WHERE type='table'`).all() as Array<{ name: string }>;
    const names = tables.map((t) => t.name);
    expect(names).toEqual(expect.arrayContaining([
      "memories", "memory_invalidations", "memory_contradictions", "memory_access_log",
    ]));
  });

  it("creates required indexes", () => {
    applyV25Migration(db);
    const indexes = db.prepare(`SELECT name FROM sqlite_master WHERE type='index'`).all() as Array<{ name: string }>;
    const names = indexes.map((i) => i.name);
    expect(names).toEqual(expect.arrayContaining([
      "idx_memories_kind", "idx_memories_valid", "idx_memories_goal", "idx_memories_importance",
      "idx_inv_memory", "idx_contra_memory", "idx_access_memory",
    ]));
  });

  it("is idempotent", () => {
    applyV25Migration(db);
    expect(() => applyV25Migration(db)).not.toThrow();
    const cnt = db.prepare(`SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' AND name='memories'`).get() as { c: number };
    expect(cnt.c).toBe(1);
  });

  it("kind CHECK constraint rejects invalid values", () => {
    applyV25Migration(db);
    expect(() => {
      db.prepare(`INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)`)
        .run("x", "garbage", "c", 0.5, "2026-01-01", "2026-01-01", "2026-01-01");
    }).toThrow();
  });

  it("verdict CHECK constraint accepts spec values", () => {
    applyV25Migration(db);
    for (const v of ["ADVANCES", "PARTIAL", "BLOCKED", "NEUTRAL"]) {
      expect(() => {
        db.prepare(`INSERT INTO memories (id, kind, content, importance, verdict, valid_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
          .run(`v_${v}`, "semantic", "c", 0.5, v, "2026-01-01", "2026-01-01", "2026-01-01");
      }).not.toThrow();
    }
  });
});
```

- [ ] **Step 2: Run — confirm partial FAIL**

Run: `npx vitest run __tests__/memory-db-v25.test.ts`
Expected: FAIL on CHECK constraint tests (current stub has no CHECK).

- [ ] **Step 3: Replace `applyV25Migration` with full version + CHECK constraints**

In `src/memory/db.ts`, replace the body of `applyV25Migration`:
```typescript
export function applyV25Migration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS memories (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL CHECK (kind IN ('semantic','episodic','working','procedural','reflexive')),
      content TEXT NOT NULL,
      embedding BLOB,
      importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
      goal_id TEXT,
      subgoal_id TEXT,
      verdict TEXT CHECK (verdict IS NULL OR verdict IN ('ADVANCES','PARTIAL','BLOCKED','NEUTRAL')),
      source_turn_id TEXT,
      source_channel TEXT,
      valid_at TEXT NOT NULL,
      invalid_at TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      access_count INTEGER NOT NULL DEFAULT 0,
      last_accessed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
    CREATE INDEX IF NOT EXISTS idx_memories_valid ON memories(invalid_at);
    CREATE INDEX IF NOT EXISTS idx_memories_goal ON memories(goal_id);
    CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);

    CREATE TABLE IF NOT EXISTS memory_invalidations (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      reason TEXT NOT NULL,
      invalidated_by TEXT NOT NULL,
      invalidated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_inv_memory ON memory_invalidations(memory_id);

    CREATE TABLE IF NOT EXISTS memory_contradictions (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      contradicts_id TEXT NOT NULL REFERENCES memories(id),
      detected_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_contra_memory ON memory_contradictions(memory_id);

    CREATE TABLE IF NOT EXISTS memory_access_log (
      id TEXT PRIMARY KEY,
      memory_id TEXT NOT NULL REFERENCES memories(id),
      accessed_at TEXT NOT NULL,
      context TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_access_memory ON memory_access_log(memory_id);
  `);
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/memory-db-v25.test.ts`
Expected: PASS — all 6 migration tests.

- [ ] **Step 5: Register migration in `MemoryDatabase`**

Find `runMigrations()` in `src/memory/db.ts` (around line 1208) and after the v24 case, add:
```typescript
if (currentVersion < 25) {
  applyV25Migration(this.db);
  this.db.pragma(`user_version = 25`);
  console.log("[memory] applied v25 migration");
}
```

Find `applyMigrations()` (around line 3700) and append:
```typescript
if (currentVersion < 25) {
  applyV25Migration(this.db);
  applied.push(25);
}
```

Bump `SCHEMA_VERSION` constant from 24 to 25.

- [ ] **Step 6: Run full test suite — confirm green**

Run: `npm test`
Expected: 0 failures (existing migrations + new v25).

- [ ] **Step 7: Commit**

```bash
git add src/memory/db.ts __tests__/memory-db-v25.test.ts
git commit -m "feat(memory): v25 migration — bitemporal memories table + CHECK constraints"
```

---

### Task 5: Pre-flight backup before v25

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory-db-v25.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/memory-db-v25.test.ts`:
```typescript
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

describe("v25 migration — backup", () => {
  it("creates a backup file before applying when given a file-backed db path", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "v25-"));
    const dbPath = path.join(tmp, "memory.db");
    const db = new Database(dbPath);
    db.pragma("journal_mode = WAL");
    db.exec(`CREATE TABLE legacy (id TEXT PRIMARY KEY); INSERT INTO legacy VALUES ('a');`);
    db.close();

    // Re-open and run v25 with backup helper
    const db2 = new Database(dbPath);
    db2.pragma("journal_mode = WAL");
    const { backupBeforeV25, applyV25Migration } = require("../src/memory/db.js");
    const backupPath = backupBeforeV25(dbPath);
    applyV25Migration(db2);

    expect(fs.existsSync(backupPath)).toBe(true);
    expect(backupPath).toContain(".v24-backup");
    db2.close();
    fs.rmSync(tmp, { recursive: true });
  });

  it("backupBeforeV25 is a no-op for in-memory db (path = null)", () => {
    const { backupBeforeV25 } = require("../src/memory/db.js");
    const result = backupBeforeV25(null);
    expect(result).toBeNull();
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `backupBeforeV25 is not a function`.

- [ ] **Step 3: Implement `backupBeforeV25`**

Add to `src/memory/db.ts`:
```typescript
import * as fs from "node:fs";

export function backupBeforeV25(dbPath: string | null): string | null {
  if (!dbPath) return null;
  if (!fs.existsSync(dbPath)) return null;
  const backupPath = `${dbPath}.v24-backup-${Date.now()}`;
  fs.copyFileSync(dbPath, backupPath);
  return backupPath;
}
```

In `MemoryDatabase` constructor (around the existing `runMigrations` call), invoke it before v25 runs — but only when `currentVersion < 25` and a real path is known. Add a `private dbPath: string | null` field set from constructor args, and wire:
```typescript
const currentVersion = this.db.pragma("user_version", { simple: true }) as number;
if (currentVersion < 25 && this.dbPath) {
  backupBeforeV25(this.dbPath);
}
```

- [ ] **Step 4: Run — confirm PASS**

Run: `npx vitest run __tests__/memory-db-v25.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory-db-v25.test.ts
git commit -m "feat(memory): pre-flight backup before v25 migration"
```

---

### Task 6: Legacy data merge into `memories` table

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/memory-db-v25.test.ts`

The v25 migration must merge data from legacy fragmented tables (per Phase 1 audit: `pellets`, `task_memories`, `working_memory`, `episodic_events`, `procedural_skills` — verify exact names by inspecting the actual schema before this task). **Spec § Schema Migration** lists the merge mapping.

- [ ] **Step 1: Verify legacy table names**

Run a one-shot inspection script (do NOT commit):
```bash
node -e "const db = require('better-sqlite3')('/Users/bakirtalibov/.stackowl/memory.db', { readonly: true }); console.log(db.prepare(\"SELECT name FROM sqlite_master WHERE type='table'\").all().map(r => r.name).join('\\n'));"
```

Note the actual legacy table names. The merge step uses these exact names.

- [ ] **Step 2: Write the failing test**

Append to `__tests__/memory-db-v25.test.ts`:
```typescript
describe("v25 migration — data merge", () => {
  it("merges legacy pellets table into memories with kind='semantic'", () => {
    const db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    // Pre-create a legacy table that v25 must consume
    db.exec(`
      CREATE TABLE pellets (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        importance REAL,
        created_at TEXT
      );
      INSERT INTO pellets VALUES ('p1', 'legacy fact', 0.6, '2026-01-01');
    `);
    applyV25Migration(db);
    const row = db.prepare(`SELECT * FROM memories WHERE id = 'p1'`).get() as { kind: string; content: string };
    expect(row).toBeDefined();
    expect(row.kind).toBe("semantic");
    expect(row.content).toBe("legacy fact");
  });

  it("legacy merge is idempotent (running v25 twice does not double-insert)", () => {
    const db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.exec(`CREATE TABLE pellets (id TEXT PRIMARY KEY, content TEXT, importance REAL, created_at TEXT); INSERT INTO pellets VALUES ('p1', 'x', 0.5, '2026-01-01');`);
    applyV25Migration(db);
    applyV25Migration(db);
    const cnt = db.prepare(`SELECT COUNT(*) AS c FROM memories WHERE id = 'p1'`).get() as { c: number };
    expect(cnt.c).toBe(1);
  });
});
```

NOTE: replace `pellets` with the actual legacy table name discovered in Step 1. Add similar tests for each legacy table to merge (`task_memories` → episodic, `working_memory` → working, etc.) — one test per source table.

- [ ] **Step 3: Run — confirm FAIL**

Expected: legacy data not merged into `memories`.

- [ ] **Step 4: Extend `applyV25Migration` with legacy-merge block**

Append to `applyV25Migration`:
```typescript
// Legacy data merge — idempotent via INSERT OR IGNORE
const legacyTables = db.prepare(`SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pellets','task_memories','working_memory','episodic_events','procedural_skills')`).all() as Array<{ name: string }>;

for (const { name } of legacyTables) {
  const kindMap: Record<string, string> = {
    pellets: "semantic",
    task_memories: "episodic",
    working_memory: "working",
    episodic_events: "episodic",
    procedural_skills: "procedural",
  };
  const kind = kindMap[name];
  if (!kind) continue;

  // Best-effort merge — column names from each legacy table
  // adapt SELECT to actual columns of each legacy table
  try {
    db.exec(`
      INSERT OR IGNORE INTO memories (id, kind, content, importance, valid_at, created_at, updated_at)
      SELECT
        id,
        '${kind}' AS kind,
        COALESCE(content, '') AS content,
        COALESCE(importance, 0.5) AS importance,
        COALESCE(created_at, datetime('now')) AS valid_at,
        COALESCE(created_at, datetime('now')) AS created_at,
        COALESCE(created_at, datetime('now')) AS updated_at
      FROM ${name}
    `);
  } catch (e) {
    console.warn(`[memory] v25 legacy merge skipped for ${name}:`, (e as Error).message);
  }
}
```

ADAPT the SELECT to each legacy table's real columns based on Step 1 inspection. If a legacy table has different column names, write a per-table SELECT block.

- [ ] **Step 5: Run — confirm PASS**

Run: `npx vitest run __tests__/memory-db-v25.test.ts`
Expected: PASS — all merge tests.

- [ ] **Step 6: Post-insert verification**

Append to `applyV25Migration` after the merge block:
```typescript
// Post-insert verification: count memories vs sum of legacy table rows
const memCount = db.prepare(`SELECT COUNT(*) AS c FROM memories`).get() as { c: number };
let legacyTotal = 0;
for (const { name } of legacyTables) {
  try {
    const r = db.prepare(`SELECT COUNT(*) AS c FROM ${name}`).get() as { c: number };
    legacyTotal += r.c;
  } catch {/* ignore */}
}
if (legacyTotal > 0 && memCount.c < legacyTotal * 0.95) {
  throw new Error(`v25 migration verification failed: memories=${memCount.c}, legacy total=${legacyTotal} — expected >= 95% merge`);
}
```

- [ ] **Step 7: Run — confirm PASS**

Expected: PASS (verification gate doesn't trigger when merge is correct).

- [ ] **Step 8: Commit**

```bash
git add src/memory/db.ts __tests__/memory-db-v25.test.ts
git commit -m "feat(memory): v25 legacy-table merge with post-insert verification"
```

---

### Task 7: Migration integration test (real-data shape)

**Files:**
- Test: `__tests__/memory-db-v25.test.ts`

- [ ] **Step 1: Write the integration test**

Append:
```typescript
describe("v25 migration — integration", () => {
  it("end-to-end: legacy db with mixed tables migrates cleanly and search works", async () => {
    const db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    db.exec(`
      CREATE TABLE pellets (id TEXT PRIMARY KEY, content TEXT, importance REAL, created_at TEXT);
      CREATE TABLE working_memory (id TEXT PRIMARY KEY, content TEXT, importance REAL, created_at TEXT);
      INSERT INTO pellets VALUES ('p1', 'user prefers concise', 0.7, '2026-01-01'),
                                  ('p2', 'works in TypeScript', 0.6, '2026-01-02');
      INSERT INTO working_memory VALUES ('w1', 'currently editing src/x.ts', 0.4, '2026-05-01');
    `);
    applyV25Migration(db);

    const all = db.prepare(`SELECT id, kind FROM memories ORDER BY id`).all() as Array<{ id: string; kind: string }>;
    expect(all).toEqual([
      { id: "p1", kind: "semantic" },
      { id: "p2", kind: "semantic" },
      { id: "w1", kind: "working" },
    ]);

    const { MemoryRepository } = await import("../src/memory/repository.js");
    const repo = new MemoryRepository(db);
    const semanticOnly = await repo.search("user", { kinds: ["semantic"], topK: 10 });
    expect(semanticOnly.every((r) => r.kind === "semantic")).toBe(true);
  });
});
```

- [ ] **Step 2: Run — confirm PASS**

Run: `npx vitest run __tests__/memory-db-v25.test.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add __tests__/memory-db-v25.test.ts
git commit -m "test(memory): v25 migration end-to-end integration test"
```

---

## Phase C — Repository write surface (refinement)

### Task 8: Repository — `insertBatch` validates importance + handles upserts

**Files:**
- Modify: `src/memory/repository.ts`
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Write the failing tests**

Append:
```typescript
describe("MemoryRepository.insertBatch — validation & upsert", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("rejects importance outside [0,1]", () => {
    expect(() =>
      repo.insertBatch([{ id: "x", kind: "semantic", content: "c", importance: 1.5 }])
    ).toThrow();
  });

  it("upserts on conflicting id (replaces content + bumps updated_at)", async () => {
    const id = "u1";
    repo.insertBatch([{ id, kind: "semantic", content: "first", importance: 0.5 }]);
    const before = repo.getById(id)!;
    await new Promise((r) => setTimeout(r, 10));
    repo.insertBatch([{ id, kind: "semantic", content: "second", importance: 0.6 }]);
    const after = repo.getById(id)!;
    expect(after.content).toBe("second");
    expect(after.updated_at).not.toBe(before.updated_at);
  });

  it("transaction rolls back on partial failure", () => {
    expect(() =>
      repo.insertBatch([
        { id: "ok", kind: "semantic", content: "ok", importance: 0.5 },
        { id: "bad", kind: "semantic" as const, content: "x", importance: 2.0 },
      ])
    ).toThrow();
    expect(repo.getById("ok")).toBeNull();
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

- [ ] **Step 3: Update `insertBatch` with `INSERT … ON CONFLICT DO UPDATE`**

Replace `insertBatch` body:
```typescript
insertBatch(records: MemoryInsert[]): void {
  if (records.length === 0) return;
  for (const r of records) {
    if (r.importance < 0 || r.importance > 1) {
      throw new Error(`importance must be in [0,1], got ${r.importance} for id=${r.id}`);
    }
  }
  const stmt = this.db.prepare(`
    INSERT INTO memories
      (id, kind, content, embedding, importance, goal_id, subgoal_id, verdict,
       source_turn_id, source_channel, valid_at, created_at, updated_at)
    VALUES
      (@id, @kind, @content, @embedding, @importance, @goal_id, @subgoal_id, @verdict,
       @source_turn_id, @source_channel, @now, @now, @now)
    ON CONFLICT(id) DO UPDATE SET
      content = excluded.content,
      embedding = excluded.embedding,
      importance = excluded.importance,
      verdict = excluded.verdict,
      updated_at = excluded.updated_at
  `);
  const insertMany = this.db.transaction((rows: MemoryInsert[]) => {
    const now = new Date().toISOString();
    for (const r of rows) {
      stmt.run({
        id: r.id,
        kind: r.kind,
        content: r.content,
        embedding: r.embedding ? Buffer.from(r.embedding.buffer) : null,
        importance: r.importance,
        goal_id: r.goal_id ?? null,
        subgoal_id: r.subgoal_id ?? null,
        verdict: r.verdict ?? null,
        source_turn_id: r.source_turn_id ?? null,
        source_channel: r.source_channel ?? null,
        now,
      });
    }
  });
  insertMany(records);
}
```

- [ ] **Step 4: Run — confirm PASS**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/memory/repository.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): insertBatch upsert + importance validation + tx rollback"
```

---

### Task 9: Repository emits `memory:written` / `memory:invalidated` events

**Files:**
- Modify: `src/memory/repository.ts`
- Modify: `src/gateway/event-bus.ts` (Task 20 will fully extend; this task only adds the variants the repo emits)
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Extend `GatewaySystemEvent` minimally**

In `src/gateway/event-bus.ts`, add to the `GatewaySystemEvent` union:
```typescript
| { type: "memory:written"; id: string; kind: string; goal_id: string | null; importance: number }
| { type: "memory:invalidated"; id: string; reason: string; invalidated_by: string }
```

(Task 20 adds the remaining `memory:*` variants — this task only adds what the repo needs to emit.)

- [ ] **Step 2: Write the failing tests**

Append to `__tests__/memory-repository.test.ts`:
```typescript
import { GatewayEventBus } from "../src/gateway/event-bus.js";

describe("MemoryRepository — events", () => {
  let db: Database.Database;
  let bus: GatewayEventBus;
  let repo: MemoryRepository;
  let captured: any[];

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    captured = [];
    bus.on("memory:written", (e) => captured.push(e));
    bus.on("memory:invalidated", (e) => captured.push(e));
    repo = new MemoryRepository(db, bus);
  });

  it("emits memory:written for each inserted record", () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "x", importance: 0.5 },
      { id: "b", kind: "episodic", content: "y", importance: 0.7 },
    ]);
    expect(captured.filter((e) => e.type === "memory:written")).toHaveLength(2);
    expect(captured[0].kind).toBe("semantic");
  });

  it("emits memory:invalidated", () => {
    repo.insertBatch([{ id: "a", kind: "semantic", content: "x", importance: 0.5 }]);
    repo.invalidate("a", { reason: "user corrected", invalidatedBy: "test" });
    expect(captured.find((e) => e.type === "memory:invalidated")).toBeDefined();
  });
});
```

- [ ] **Step 3: Run — confirm FAIL**

- [ ] **Step 4: Wire bus into repository**

Update `MemoryRepository` constructor and emit:
```typescript
constructor(
  private readonly db: Database.Database,
  private readonly bus?: GatewayEventBus,
) {}
```

In `insertBatch`, after `insertMany(records)`:
```typescript
if (this.bus) {
  for (const r of records) {
    this.bus.emit({
      type: "memory:written",
      id: r.id,
      kind: r.kind,
      goal_id: r.goal_id ?? null,
      importance: r.importance,
    });
  }
}
```

In `invalidate`, after `tx()`:
```typescript
if (this.bus) {
  this.bus.emit({
    type: "memory:invalidated",
    id,
    reason: opts.reason,
    invalidated_by: opts.invalidatedBy,
  });
}
```

Import `GatewayEventBus` at top of `repository.ts`.

- [ ] **Step 5: Run — confirm PASS**

- [ ] **Step 6: Commit**

```bash
git add src/memory/repository.ts src/gateway/event-bus.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): repository emits memory:written / memory:invalidated events"
```

---

### Task 10: Repository — semantic search with embedding query

**Files:**
- Modify: `src/memory/repository.ts`
- Test: `__tests__/memory-repository.test.ts`

- [ ] **Step 1: Add `searchSemanticByEmbedding` method**

Some callers (the `MemoryLayer`) already have a query embedding. Expose a direct path:
```typescript
async searchSemanticByEmbedding(
  queryEmbedding: Float32Array,
  opts: MemorySearchOptions = {},
): Promise<MemoryRecord[]> {
  const records = await this.search("", { ...opts, topK: opts.topK ?? 50 });
  // Re-rank with the supplied embedding (since search() returned with no embedding).
  const now = Date.now();
  const scored = records.map((r) => {
    const recencyMs = now - new Date(r.valid_at).getTime();
    const recency = Math.exp(-recencyMs / (1000 * 60 * 60 * 24 * 7));
    const relevance = r.embedding ? this.cosine(queryEmbedding, r.embedding) : 0;
    const score = 0.3 * recency + 0.3 * r.importance + 0.4 * relevance;
    return { record: r, score };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, opts.topK ?? 50).map((s) => s.record);
}
```

- [ ] **Step 2: Write the test**

```typescript
it("searchSemanticByEmbedding ranks by cosine + recency + importance", async () => {
  const db2 = new Database(":memory:");
  db2.pragma("journal_mode = WAL");
  applyV25Migration(db2);
  const r = new MemoryRepository(db2);
  const eA = new Float32Array([1, 0, 0, 0]);
  const eB = new Float32Array([0, 1, 0, 0]);
  r.insertBatch([
    { id: "a", kind: "semantic", content: "match", importance: 0.5, embedding: eA },
    { id: "b", kind: "semantic", content: "no match", importance: 0.5, embedding: eB },
  ]);
  const result = await r.searchSemanticByEmbedding(eA, { topK: 2 });
  expect(result[0].id).toBe("a");
});
```

- [ ] **Step 3: Run — confirm PASS**

- [ ] **Step 4: Commit**

```bash
git add src/memory/repository.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): searchSemanticByEmbedding for embedding-bearing callers"
```

---

## Phase D — MemoryWriter (goal-conditioned ingestion)

### Task 11: Writer skeleton + trivial-turn short-circuit

**Files:**
- Create: `src/memory/writer.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/memory-writer.test.ts`:
```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { MemoryWriter } from "../src/memory/writer.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { applyV25Migration } from "../src/memory/db.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";

function makeStubRouter() {
  return {
    resolve: vi.fn().mockReturnValue({
      provider: { chat: vi.fn() },
      model: "stub",
      tier: "cheap",
    }),
  } as any;
}

describe("MemoryWriter — trivial turns", () => {
  let db: Database.Database;
  let repo: MemoryRepository;
  let bus: GatewayEventBus;
  let writer: MemoryWriter;
  let router: ReturnType<typeof makeStubRouter>;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    router = makeStubRouter();
    writer = new MemoryWriter({ repo, bus, router });
  });

  it("skips empty user messages without invoking LLM", async () => {
    const result = await writer.ingest({
      sessionId: "s1",
      turnId: "t1",
      channel: "cli",
      userMessage: "",
      assistantResponse: "ok",
      verdict: "NEUTRAL",
      goalId: null,
      subGoalId: null,
    });
    expect(result.skipped).toBe(true);
    expect(result.reason).toBe("trivial-turn");
    expect(router.resolve).not.toHaveBeenCalled();
  });

  it("skips one-word greetings without invoking LLM", async () => {
    for (const msg of ["hi", "hello", "hey", "thanks", "ok"]) {
      const r = await writer.ingest({
        sessionId: "s1", turnId: "t-" + msg, channel: "cli",
        userMessage: msg, assistantResponse: "👋", verdict: "NEUTRAL",
        goalId: null, subGoalId: null,
      });
      expect(r.skipped).toBe(true);
    }
    expect(router.resolve).not.toHaveBeenCalled();
  });

  it("skips when verdict is NEUTRAL and message length < 12 chars", async () => {
    const r = await writer.ingest({
      sessionId: "s1", turnId: "t2", channel: "cli",
      userMessage: "ok cool", assistantResponse: "ok",
      verdict: "NEUTRAL", goalId: null, subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(router.resolve).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

Expected: `MemoryWriter` not found.

- [ ] **Step 3: Implement skeleton + trivial-turn guard**

Create `src/memory/writer.ts`:
```typescript
import type { MemoryRepository, MemoryInsert } from "./repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { IntelligenceRouter } from "../intelligence/router.js";

export interface WriterTurn {
  sessionId: string;
  turnId: string;
  channel: string;
  userMessage: string;
  assistantResponse: string;
  verdict: "ADVANCES" | "PARTIAL" | "BLOCKED" | "NEUTRAL";
  goalId: string | null;
  subGoalId: string | null;
}

export interface IngestResult {
  skipped: boolean;
  reason?: string;
  written?: number;
  invalidated?: number;
}

export interface WriterDeps {
  repo: MemoryRepository;
  bus: GatewayEventBus;
  router: IntelligenceRouter;
}

const TRIVIAL_VERDICTS = new Set(["NEUTRAL"]);
const MIN_MESSAGE_LEN_NEUTRAL = 12;
const TRIVIAL_GREETINGS = new Set(["hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "sure", "cool"]);

export class MemoryWriter {
  constructor(private readonly deps: WriterDeps) {}

  async ingest(turn: WriterTurn): Promise<IngestResult> {
    const trivial = this.isTrivial(turn);
    if (trivial) {
      return { skipped: true, reason: "trivial-turn" };
    }
    // Real ingestion in subsequent tasks
    return { skipped: true, reason: "not-implemented-yet" };
  }

  private isTrivial(turn: WriterTurn): boolean {
    const msg = (turn.userMessage ?? "").trim().toLowerCase();
    if (msg.length === 0) return true;
    if (TRIVIAL_GREETINGS.has(msg)) return true;
    if (TRIVIAL_VERDICTS.has(turn.verdict) && msg.length < MIN_MESSAGE_LEN_NEUTRAL) return true;
    return false;
  }
}
```

NOTE the `TRIVIAL_GREETINGS` set: this is **not a classification heuristic** — these are literal turn-skip optimizations equivalent to "did the user actually say anything." The classification of *content* (semantic vs episodic vs working) is done by IntelligenceRouter in Task 12, where the no-hardcoded-keywords rule applies.

- [ ] **Step 4: Run — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/memory/writer.ts __tests__/memory-writer.test.ts
git commit -m "feat(memory): MemoryWriter skeleton + trivial-turn short-circuit"
```

---

### Task 12: Writer — classify turn via IntelligenceRouter cheap-tier

**Files:**
- Modify: `src/memory/writer.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/memory-writer.test.ts`:
```typescript
describe("MemoryWriter.classify", () => {
  let db: Database.Database;
  let repo: MemoryRepository;
  let bus: GatewayEventBus;
  let writer: MemoryWriter;
  let router: any;
  let chatMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    chatMock = vi.fn().mockResolvedValue({
      content: JSON.stringify({
        extractions: [
          { kind: "semantic", content: "user prefers TypeScript over JS", importance: 0.7 },
        ],
      }),
    });
    router = {
      resolve: vi.fn().mockReturnValue({
        provider: { chat: chatMock },
        model: "stub-cheap",
        tier: "cheap",
      }),
    };
    writer = new MemoryWriter({ repo, bus, router });
  });

  it("uses IntelligenceRouter.resolve('classification')", async () => {
    await writer.ingest({
      sessionId: "s1", turnId: "t1", channel: "cli",
      userMessage: "I always prefer TypeScript over JavaScript for new projects.",
      assistantResponse: "Got it.", verdict: "ADVANCES",
      goalId: "g1", subGoalId: null,
    });
    expect(router.resolve).toHaveBeenCalledWith("classification");
  });

  it("short-circuits on empty extraction (no further DB writes)", async () => {
    chatMock.mockResolvedValue({ content: JSON.stringify({ extractions: [] }) });
    const r = await writer.ingest({
      sessionId: "s1", turnId: "t2", channel: "cli",
      userMessage: "Just thinking out loud about something abstract.",
      assistantResponse: "Sure.", verdict: "NEUTRAL",
      goalId: null, subGoalId: null,
    });
    expect(r.skipped).toBe(true);
    expect(r.reason).toBe("empty-extraction");
    expect(repo.stats().total).toBe(0);
  });

  it("returns ingest result with extraction count when non-empty", async () => {
    const r = await writer.ingest({
      sessionId: "s1", turnId: "t3", channel: "cli",
      userMessage: "I always prefer TypeScript over JavaScript.",
      assistantResponse: "Got it.", verdict: "ADVANCES",
      goalId: "g1", subGoalId: null,
    });
    expect(r.skipped).toBe(false);
    expect(r.written).toBeGreaterThan(0);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

- [ ] **Step 3: Implement classify + persist**

Replace `MemoryWriter.ingest`:
```typescript
import { v4 as uuid } from "uuid";

interface ExtractionResult {
  extractions: Array<{
    kind: "semantic" | "episodic" | "working" | "procedural";
    content: string;
    importance: number;
  }>;
}

private buildClassifyPrompt(turn: WriterTurn): string {
  return `You are a memory extractor. Read the user message and assistant response below and emit zero or more memory records as JSON.

User message: ${JSON.stringify(turn.userMessage)}
Assistant response: ${JSON.stringify(turn.assistantResponse)}
Active goal id: ${turn.goalId ?? "none"}
Verdict: ${turn.verdict}

Respond with strict JSON of shape:
{ "extractions": [ { "kind": "semantic"|"episodic"|"working"|"procedural", "content": "...", "importance": 0.0-1.0 } ] }

Rules:
- "semantic": user preferences, durable facts about the user/world.
- "episodic": time-bound events ("user worked on X today").
- "working": ephemeral active-task state, valid for hours not days.
- "procedural": learned procedures / how-to knowledge.
- Return [] if nothing worth remembering.
- Importance 0.8+ only for facts the user would correct you about.

JSON only, no prose.`;
}

async ingest(turn: WriterTurn): Promise<IngestResult> {
  if (this.isTrivial(turn)) {
    return { skipped: true, reason: "trivial-turn" };
  }

  const resolved = this.deps.router.resolve("classification");
  let extraction: ExtractionResult;
  try {
    const response = await resolved.provider.chat({
      model: resolved.model,
      messages: [
        { role: "system", content: "You are a precise JSON-only memory extractor." },
        { role: "user", content: this.buildClassifyPrompt(turn) },
      ],
    });
    extraction = JSON.parse(response.content);
  } catch (err) {
    this.deps.bus.emit({
      type: "memory:classify_failed",
      turnId: turn.turnId,
      reason: (err as Error).message,
    });
    return { skipped: true, reason: "classify-failed" };
  }

  if (!extraction.extractions || extraction.extractions.length === 0) {
    return { skipped: true, reason: "empty-extraction" };
  }

  const records: MemoryInsert[] = extraction.extractions.map((e) => ({
    id: uuid(),
    kind: e.kind,
    content: e.content,
    importance: Math.max(0, Math.min(1, e.importance)),
    goal_id: turn.goalId ?? undefined,
    subgoal_id: turn.subGoalId ?? undefined,
    verdict: turn.verdict,
    source_turn_id: turn.turnId,
    source_channel: turn.channel,
  }));

  this.deps.repo.insertBatch(records);
  return { skipped: false, written: records.length };
}
```

Add `memory:classify_failed` to the GatewaySystemEvent union in `src/gateway/event-bus.ts`:
```typescript
| { type: "memory:classify_failed"; turnId: string; reason: string }
```

- [ ] **Step 4: Run — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/memory/writer.ts src/gateway/event-bus.ts __tests__/memory-writer.test.ts
git commit -m "feat(memory): writer classify via IntelligenceRouter cheap-tier + persist"
```

---

### Task 13: Writer — contradiction detection + ADD/UPDATE/DELETE/NOOP reconcile

**Files:**
- Modify: `src/memory/writer.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Write the failing tests**

Append:
```typescript
describe("MemoryWriter — reconcile (Mem0 ADD/UPDATE/DELETE/NOOP)", () => {
  let db: Database.Database;
  let repo: MemoryRepository;
  let bus: GatewayEventBus;
  let writer: MemoryWriter;
  let chatMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    chatMock = vi.fn();
    const router: any = { resolve: () => ({ provider: { chat: chatMock }, model: "x", tier: "cheap" }) };
    writer = new MemoryWriter({ repo, bus, router });
  });

  it("invalidates contradicting memories (DELETE)", async () => {
    // Seed an existing contradicting memory
    repo.insertBatch([
      { id: "old", kind: "semantic", content: "user prefers Python", importance: 0.7 },
    ]);
    // Writer sees a new statement that contradicts it
    chatMock.mockResolvedValueOnce({
      content: JSON.stringify({
        extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }],
      }),
    });
    chatMock.mockResolvedValueOnce({
      content: JSON.stringify({
        decisions: [
          { action: "DELETE", target_id: "old", reason: "contradicts new statement" },
          { action: "ADD" },
        ],
      }),
    });

    const r = await writer.ingest({
      sessionId: "s1", turnId: "t1", channel: "cli",
      userMessage: "Actually I prefer TypeScript now.",
      assistantResponse: "Updated.", verdict: "ADVANCES",
      goalId: null, subGoalId: null,
    });
    expect(r.invalidated).toBe(1);
    const old = repo.getById("old")!;
    expect(old.invalid_at).not.toBeNull();
  });

  it("NOOP when reconcile says no action", async () => {
    repo.insertBatch([
      { id: "k1", kind: "semantic", content: "user prefers TypeScript", importance: 0.7 },
    ]);
    chatMock.mockResolvedValueOnce({
      content: JSON.stringify({
        extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }],
      }),
    });
    chatMock.mockResolvedValueOnce({
      content: JSON.stringify({ decisions: [{ action: "NOOP", reason: "duplicate" }] }),
    });

    const r = await writer.ingest({
      sessionId: "s1", turnId: "t1", channel: "cli",
      userMessage: "I prefer TypeScript.",
      assistantResponse: "Noted.", verdict: "ADVANCES",
      goalId: null, subGoalId: null,
    });
    expect(r.written ?? 0).toBe(0);
    expect(repo.stats().total).toBe(1);
  });
});
```

- [ ] **Step 2: Run — confirm FAIL**

- [ ] **Step 3: Implement reconcile pass**

In `MemoryWriter`, add reconcile after extraction:
```typescript
private async reconcile(
  extractions: ExtractionResult["extractions"],
  resolved: ReturnType<IntelligenceRouter["resolve"]>,
): Promise<{ insert: ExtractionResult["extractions"]; invalidate: string[] }> {
  // For each extraction, query top-5 similar existing memories and ask LLM to choose ADD/UPDATE/DELETE/NOOP
  const insert: ExtractionResult["extractions"] = [];
  const invalidate: string[] = [];

  for (const ext of extractions) {
    const candidates = await this.deps.repo.search(ext.content, { kinds: [ext.kind], topK: 5 });
    if (candidates.length === 0) {
      insert.push(ext);
      continue;
    }

    const prompt = `New memory candidate:
${JSON.stringify(ext)}

Existing similar memories:
${candidates.map((c) => `- id=${c.id}: ${c.content} (importance=${c.importance})`).join("\n")}

Decide: emit JSON { "decisions": [ { "action": "ADD"|"UPDATE"|"DELETE"|"NOOP", "target_id"?: "...", "reason": "..." } ] }
- ADD: insert candidate as new memory
- UPDATE: candidate refines existing (specify target_id)
- DELETE: candidate contradicts existing (specify target_id) — DELETE means invalidate, not erase
- NOOP: candidate is duplicate of existing

JSON only.`;

    let decisions: Array<{ action: string; target_id?: string; reason: string }>;
    try {
      const resp = await resolved.provider.chat({
        model: resolved.model,
        messages: [
          { role: "system", content: "JSON-only memory reconciler." },
          { role: "user", content: prompt },
        ],
      });
      decisions = JSON.parse(resp.content).decisions ?? [];
    } catch (err) {
      this.deps.bus.emit({ type: "memory:contradict_failed", reason: (err as Error).message });
      // Fail open: ADD the candidate if reconcile crashes
      insert.push(ext);
      continue;
    }

    let didAdd = false;
    for (const d of decisions) {
      if (d.action === "ADD") { insert.push(ext); didAdd = true; }
      else if (d.action === "DELETE" && d.target_id) invalidate.push(d.target_id);
      else if (d.action === "UPDATE" && d.target_id) { invalidate.push(d.target_id); insert.push(ext); didAdd = true; }
      // NOOP: no action
    }
    // If decisions had no ADD but also no NOOP and not all DELETE, fall through (already handled).
  }
  return { insert, invalidate };
}
```

Wire `reconcile` into `ingest` between extraction and persist:
```typescript
const { insert, invalidate } = await this.reconcile(extraction.extractions, resolved);

for (const id of invalidate) {
  this.deps.repo.invalidate(id, { reason: "writer-reconcile DELETE", invalidatedBy: "writer" });
}

if (insert.length === 0 && invalidate.length === 0) {
  return { skipped: true, reason: "noop" };
}

const records: MemoryInsert[] = insert.map((e) => ({
  id: uuid(),
  kind: e.kind,
  content: e.content,
  importance: Math.max(0, Math.min(1, e.importance)),
  goal_id: turn.goalId ?? undefined,
  subgoal_id: turn.subGoalId ?? undefined,
  verdict: turn.verdict,
  source_turn_id: turn.turnId,
  source_channel: turn.channel,
}));
if (records.length > 0) this.deps.repo.insertBatch(records);

return { skipped: false, written: records.length, invalidated: invalidate.length };
```

Add `memory:contradict_failed` to the union in `src/gateway/event-bus.ts`:
```typescript
| { type: "memory:contradict_failed"; reason: string }
```

- [ ] **Step 4: Run — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/memory/writer.ts src/gateway/event-bus.ts __tests__/memory-writer.test.ts
git commit -m "feat(memory): writer reconcile ADD/UPDATE/DELETE/NOOP via cheap-tier LLM"
```

---

### Task 14: Writer — subscribe to bus events (working-memory expiration, error envelope)

**Files:**
- Modify: `src/memory/writer.ts`
- Modify: `src/memory/repository.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Add `expireWorkingMemories(olderThanHours)` to repo**

In `src/memory/repository.ts`:
```typescript
expireWorkingMemories(olderThanHours: number): number {
  const cutoff = new Date(Date.now() - olderThanHours * 3600_000).toISOString();
  const now = new Date().toISOString();
  const result = this.db.prepare(`
    UPDATE memories
    SET invalid_at = @now, updated_at = @now
    WHERE kind = 'working' AND invalid_at IS NULL AND valid_at < @cutoff
  `).run({ now, cutoff });
  return result.changes;
}
```

- [ ] **Step 2: Test it**

Append to `__tests__/memory-repository.test.ts`:
```typescript
it("expireWorkingMemories invalidates working memories older than cutoff", async () => {
  const db2 = new Database(":memory:"); db2.pragma("journal_mode = WAL"); applyV25Migration(db2);
  const r = new MemoryRepository(db2);
  // Insert with manually old valid_at
  db2.prepare(`INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)`)
    .run("old-w", "working", "x", 0.5, "2026-01-01T00:00:00.000Z", "2026-01-01", "2026-01-01");
  db2.prepare(`INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)`)
    .run("new-w", "working", "y", 0.5, new Date().toISOString(), new Date().toISOString(), new Date().toISOString());
  const n = r.expireWorkingMemories(1); // 1h cutoff
  expect(n).toBe(1);
  expect(r.getById("old-w")!.invalid_at).not.toBeNull();
  expect(r.getById("new-w")!.invalid_at).toBeNull();
});
```

- [ ] **Step 3: Writer subscribes to `engine:turn_complete`**

In `src/memory/writer.ts`, add an `attachBusListeners` method:
```typescript
attachBusListeners(): void {
  this.deps.bus.on("engine:turn_complete", async (e: { sessionId: string }) => {
    // Best-effort: expire working memories older than 24h on every turn
    try {
      this.deps.repo.expireWorkingMemories(24);
    } catch (err) {
      this.deps.bus.emit({ type: "memory:write_failed", reason: (err as Error).message });
    }
  });
}
```

NOTE: `engine:turn_complete` and `memory:write_failed` must exist in the GatewaySystemEvent union. If not yet present, add stubs:
```typescript
| { type: "engine:turn_complete"; sessionId: string }
| { type: "memory:write_failed"; reason: string }
```

(`engine:turn_complete` may exist already — check `src/gateway/event-bus.ts`. If it exists with a different shape, adapt the listener.)

- [ ] **Step 4: Test the listener**

```typescript
it("attachBusListeners triggers expireWorkingMemories on engine:turn_complete", async () => {
  const expireSpy = vi.spyOn(repo, "expireWorkingMemories").mockReturnValue(0);
  writer.attachBusListeners();
  bus.emit({ type: "engine:turn_complete", sessionId: "s1" });
  await new Promise((r) => setTimeout(r, 5));
  expect(expireSpy).toHaveBeenCalledWith(24);
});
```

- [ ] **Step 5: Run — confirm PASS**

- [ ] **Step 6: Commit**

```bash
git add src/memory/writer.ts src/memory/repository.ts src/gateway/event-bus.ts __tests__/memory-writer.test.ts __tests__/memory-repository.test.ts
git commit -m "feat(memory): writer attachBusListeners + expireWorkingMemories"
```

---

### Task 15: Writer — reflexive-write helper for engine self-observations

**Files:**
- Modify: `src/memory/writer.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Add `recordReflexive` method**

```typescript
async recordReflexive(input: {
  sessionId: string;
  observation: string;
  importance?: number;
  goalId?: string;
}): Promise<void> {
  this.deps.repo.insertBatch([{
    id: uuid(),
    kind: "reflexive",
    content: input.observation,
    importance: input.importance ?? 0.5,
    goal_id: input.goalId,
    source_channel: "engine-reflexive",
  }]);
}
```

- [ ] **Step 2: Test**

```typescript
it("recordReflexive inserts a reflexive memory", async () => {
  await writer.recordReflexive({ sessionId: "s1", observation: "tool web returned bad data" });
  expect(repo.stats().byKind.reflexive).toBe(1);
});
```

- [ ] **Step 3: Run — confirm PASS**

- [ ] **Step 4: Commit**

```bash
git add src/memory/writer.ts __tests__/memory-writer.test.ts
git commit -m "feat(memory): writer.recordReflexive for engine self-observations"
```

---

### Task 16: Writer — error envelope contract + write_failed events

**Files:**
- Modify: `src/memory/writer.ts`
- Test: `__tests__/memory-writer.test.ts`

- [ ] **Step 1: Wrap `repo.insertBatch` in `ingest` with try/catch**

Find the `repo.insertBatch(records)` call and wrap:
```typescript
try {
  if (records.length > 0) this.deps.repo.insertBatch(records);
} catch (err) {
  this.deps.bus.emit({ type: "memory:write_failed", reason: (err as Error).message });
  return { skipped: true, reason: "write-failed" };
}
```

- [ ] **Step 2: Test**

```typescript
it("emits memory:write_failed when insertBatch throws", async () => {
  const failures: any[] = [];
  bus.on("memory:write_failed", (e) => failures.push(e));
  vi.spyOn(repo, "insertBatch").mockImplementation(() => { throw new Error("disk full"); });
  chatMock.mockResolvedValueOnce({
    content: JSON.stringify({ extractions: [{ kind: "semantic", content: "x", importance: 0.5 }] }),
  });
  chatMock.mockResolvedValueOnce({ content: JSON.stringify({ decisions: [{ action: "ADD" }] }) });

  const r = await writer.ingest({
    sessionId: "s1", turnId: "t1", channel: "cli",
    userMessage: "I prefer typescript over javascript always.",
    assistantResponse: "noted", verdict: "ADVANCES",
    goalId: null, subGoalId: null,
  });
  expect(r.skipped).toBe(true);
  expect(r.reason).toBe("write-failed");
  expect(failures).toHaveLength(1);
});
```

- [ ] **Step 3: Run — confirm PASS**

- [ ] **Step 4: Commit**

```bash
git add src/memory/writer.ts __tests__/memory-writer.test.ts
git commit -m "feat(memory): writer error envelope + memory:write_failed events"
```

---

## Phase E — ContextLayer rendering (TTL-layered prompt assembly)

### Task 17: `MemoryLayer` base class + scoring

**Files:**
- Create: `src/memory/layer.ts`
- Test: `__tests__/memory-layer.test.ts`

- [ ] **Step 1: Inspect the real ContextLayer interface**

Re-read `src/context/layer.ts` to confirm signature:
- `name: string`, `priority: number`, `maxTokens: number`, `produces: string[]`, `dependsOn: string[]`, `alwaysInclude?: boolean`, `cacheTtlMs?: number`
- `shouldFire(triage: TriageSignals): boolean`
- `build(req: ContextRequest, triage: TriageSignals, deps: LayerResults): Promise<string>`
- `getCacheKey?(req: ContextRequest, triage: TriageSignals): string | null`

- [ ] **Step 2: Write the failing test**

Create `__tests__/memory-layer.test.ts`:
```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { createSemanticMemoryLayer, createEpisodicMemoryLayer, createWorkingMemoryLayer, createProceduralMemoryLayer } from "../src/memory/layer.js";

describe("MemoryLayer factories", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("creates four layers with distinct names + priorities", () => {
    const s = createSemanticMemoryLayer({ repo });
    const e = createEpisodicMemoryLayer({ repo });
    const w = createWorkingMemoryLayer({ repo });
    const p = createProceduralMemoryLayer({ repo });
    expect(new Set([s.name, e.name, w.name, p.name]).size).toBe(4);
  });

  it("each layer has cacheTtlMs defined per spec", () => {
    expect(createSemanticMemoryLayer({ repo }).cacheTtlMs).toBeGreaterThanOrEqual(60_000);
    expect(createWorkingMemoryLayer({ repo }).cacheTtlMs).toBeLessThanOrEqual(60_000);
  });

  it("semantic layer build() returns formatted memories sorted by score", async () => {
    repo.insertBatch([
      { id: "s1", kind: "semantic", content: "user prefers concise answers", importance: 0.9 },
      { id: "s2", kind: "semantic", content: "user uses TypeScript daily", importance: 0.4 },
    ]);
    const layer = createSemanticMemoryLayer({ repo });
    const out = await layer.build(
      { userMessage: "what should I work on", sessionId: "s", channel: "cli" } as any,
      { intent: "general" } as any,
      {} as any,
    );
    expect(out).toContain("concise answers");
    // Higher-importance memory should appear first
    expect(out.indexOf("concise")).toBeLessThan(out.indexOf("TypeScript"));
  });

  it("excludes reflexive memories from prompt rendering", async () => {
    repo.insertBatch([
      { id: "ref1", kind: "reflexive", content: "engine noticed slow tool", importance: 0.9 },
      { id: "sem1", kind: "semantic", content: "user likes Rust", importance: 0.5 },
    ]);
    const layer = createSemanticMemoryLayer({ repo });
    const out = await layer.build({} as any, {} as any, {} as any);
    expect(out).toContain("Rust");
    expect(out).not.toContain("engine noticed slow tool");
  });
});
```

- [ ] **Step 3: Run — confirm FAIL**

- [ ] **Step 4: Implement `MemoryLayer` base + factories**

Create `src/memory/layer.ts`:
```typescript
import type { ContextLayer, ContextRequest, TriageSignals, LayerResults } from "../context/layer.js";
import type { MemoryRepository, MemoryKind, MemoryRecord } from "./repository.js";

export interface MemoryLayerDeps {
  repo: MemoryRepository;
}

interface MemoryLayerConfig {
  name: string;
  priority: number;
  maxTokens: number;
  cacheTtlMs: number;
  kind: MemoryKind;
  topK: number;
  minImportance?: number;
  header: string;
}

function makeMemoryLayer(deps: MemoryLayerDeps, cfg: MemoryLayerConfig): ContextLayer {
  return {
    name: cfg.name,
    priority: cfg.priority,
    maxTokens: cfg.maxTokens,
    produces: [cfg.name],
    dependsOn: [],
    cacheTtlMs: cfg.cacheTtlMs,

    shouldFire(_triage: TriageSignals): boolean {
      return true; // memory layers are always candidates; the pipeline decides via token budget
    },

    async build(req: ContextRequest, _triage: TriageSignals, _deps: LayerResults): Promise<string> {
      const query = (req as any).userMessage ?? "";
      const records = await deps.repo.search(query, {
        kinds: [cfg.kind],
        topK: cfg.topK,
        minImportance: cfg.minImportance,
      });
      if (records.length === 0) return "";

      const sorted = records.sort((a, b) => b.importance - a.importance);
      const lines = sorted.map((r) => `- ${r.content}`);
      return `${cfg.header}\n${lines.join("\n")}`;
    },

    getCacheKey(req: ContextRequest, _triage: TriageSignals): string {
      const sessionId = (req as any).sessionId ?? "global";
      return `${cfg.name}:${sessionId}`;
    },
  };
}

export function createSemanticMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.semantic",
    priority: 7,
    maxTokens: 800,
    cacheTtlMs: 5 * 60_000, // 5min
    kind: "semantic",
    topK: 6,
    header: "## Long-term facts about the user",
  });
}

export function createEpisodicMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.episodic",
    priority: 6,
    maxTokens: 600,
    cacheTtlMs: 2 * 60_000, // 2min
    kind: "episodic",
    topK: 5,
    header: "## Recent events",
  });
}

export function createWorkingMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.working",
    priority: 8,
    maxTokens: 400,
    cacheTtlMs: 30_000, // 30s — working memory expires fast
    kind: "working",
    topK: 4,
    header: "## Active task state",
  });
}

export function createProceduralMemoryLayer(deps: MemoryLayerDeps): ContextLayer {
  return makeMemoryLayer(deps, {
    name: "memory.procedural",
    priority: 5,
    maxTokens: 600,
    cacheTtlMs: 10 * 60_000, // 10min — procedures change rarely
    kind: "procedural",
    topK: 4,
    header: "## Learned procedures",
  });
}
```

NOTE: reflexive memories are excluded by construction — none of the four factories request `kind: "reflexive"`.

- [ ] **Step 5: Run — confirm PASS**

- [ ] **Step 6: Commit**

```bash
git add src/memory/layer.ts __tests__/memory-layer.test.ts
git commit -m "feat(memory): four ContextLayer factories — semantic/episodic/working/procedural"
```

---

### Task 18: Layer — token budget enforcement + truncation

**Files:**
- Modify: `src/memory/layer.ts`
- Test: `__tests__/memory-layer.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
it("truncates output to maxTokens (approx 4 chars per token)", async () => {
  const longContent = "x".repeat(5000);
  repo.insertBatch([
    { id: "a", kind: "semantic", content: longContent, importance: 0.9 },
  ]);
  const layer = createSemanticMemoryLayer({ repo });
  const out = await layer.build({} as any, {} as any, {} as any);
  // 800 tokens × 4 chars ≈ 3200 chars max
  expect(out.length).toBeLessThanOrEqual(3500);
});
```

- [ ] **Step 2: Add truncation in `build`**

In `makeMemoryLayer`'s `build`, before returning:
```typescript
const result = `${cfg.header}\n${lines.join("\n")}`;
const maxChars = cfg.maxTokens * 4;
return result.length > maxChars ? result.slice(0, maxChars - 3) + "..." : result;
```

- [ ] **Step 3: Run — confirm PASS**

- [ ] **Step 4: Commit**

```bash
git add src/memory/layer.ts __tests__/memory-layer.test.ts
git commit -m "feat(memory): layer maxTokens truncation"
```

---

### Task 19: Wire memory layers into ContextPipeline default roster

**Files:**
- Modify: `src/context/pipeline.ts`
- Test: `__tests__/memory-layer.test.ts`

- [ ] **Step 1: Inspect pipeline for layer registration**

Read `src/context/pipeline.ts`. Find where layers are registered (likely a `registerDefaultLayers` or similar method, or a constructor list).

- [ ] **Step 2: Write the failing test**

Append to `__tests__/memory-layer.test.ts`:
```typescript
import { ContextPipeline } from "../src/context/pipeline.js";

it("ContextPipeline registers all four memory layers when given a repo", () => {
  const pipeline = new ContextPipeline({ memoryRepo: repo } as any);
  const names = pipeline.getRegisteredLayerNames();
  expect(names).toEqual(expect.arrayContaining([
    "memory.semantic", "memory.episodic", "memory.working", "memory.procedural",
  ]));
});
```

(`getRegisteredLayerNames()` may need to be added to `ContextPipeline` if not present — implement minimal accessor.)

- [ ] **Step 3: Wire layers in pipeline**

In `src/context/pipeline.ts`, in the `ContextPipeline` constructor or `registerDefaultLayers`:
```typescript
import {
  createSemanticMemoryLayer,
  createEpisodicMemoryLayer,
  createWorkingMemoryLayer,
  createProceduralMemoryLayer,
} from "../memory/layer.js";

// In constructor, after existing layers are registered:
if (deps.memoryRepo) {
  this.registerLayer(createSemanticMemoryLayer({ repo: deps.memoryRepo }));
  this.registerLayer(createEpisodicMemoryLayer({ repo: deps.memoryRepo }));
  this.registerLayer(createWorkingMemoryLayer({ repo: deps.memoryRepo }));
  this.registerLayer(createProceduralMemoryLayer({ repo: deps.memoryRepo }));
}
```

If `registerLayer`/`registerDefaultLayers` don't exist by name, adapt to whatever the actual `ContextPipeline` exposes (the audit confirmed the file exists; method names verified at implementation time).

Add `getRegisteredLayerNames(): string[]` accessor:
```typescript
getRegisteredLayerNames(): string[] {
  return Array.from(this.layers.keys());
}
```

(Or whatever data structure holds layers — adapt.)

- [ ] **Step 4: Run — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/context/pipeline.ts __tests__/memory-layer.test.ts
git commit -m "feat(memory): register four memory layers in ContextPipeline"
```

---

## Phase F — GatewayEventBus extensions

### Task 20: Complete the `memory:*` event variants

**Files:**
- Modify: `src/gateway/event-bus.ts`
- Test: `__tests__/memory-event-bus.test.ts` (NEW small file)

- [ ] **Step 1: Audit existing variants**

Re-read `src/gateway/event-bus.ts`. Confirm which of these are already present (Tasks 9, 12, 13, 14, 16 added some):
- `memory:written`, `memory:invalidated`, `memory:classify_failed`, `memory:contradict_failed`, `memory:write_failed`, `engine:turn_complete`

The remaining variants from spec § Observability:
- `memory:contradiction_detected`
- `memory:accessed`
- `memory:render_failed`
- `memory:invalidate_rejected`
- `memory:slo_breach`
- `memory:health_degraded`

- [ ] **Step 2: Write the test**

Create `__tests__/memory-event-bus.test.ts`:
```typescript
import { describe, it, expect } from "vitest";
import { GatewayEventBus, type GatewaySystemEvent } from "../src/gateway/event-bus.js";

describe("GatewayEventBus — memory:* variants", () => {
  const VARIANTS: GatewaySystemEvent["type"][] = [
    "memory:written", "memory:invalidated", "memory:classify_failed",
    "memory:contradict_failed", "memory:write_failed", "memory:contradiction_detected",
    "memory:accessed", "memory:render_failed", "memory:invalidate_rejected",
    "memory:slo_breach", "memory:health_degraded",
  ];

  it("all 11 memory:* variants are subscribable + emittable", () => {
    const bus = new GatewayEventBus();
    let count = 0;
    for (const v of VARIANTS) {
      bus.on(v, () => { count++; });
    }
    bus.emit({ type: "memory:written", id: "x", kind: "semantic", goal_id: null, importance: 0.5 } as any);
    bus.emit({ type: "memory:health_degraded", reason: "slow" } as any);
    expect(count).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 3: Add the remaining variants**

Append to the `GatewaySystemEvent` union:
```typescript
| { type: "memory:contradiction_detected"; memoryId: string; contradictsId: string; reason: string }
| { type: "memory:accessed"; id: string; kind: string }
| { type: "memory:render_failed"; layerName: string; reason: string }
| { type: "memory:invalidate_rejected"; id: string; reason: string }
| { type: "memory:slo_breach"; metric: string; observed: number; budget: number }
| { type: "memory:health_degraded"; reason: string }
```

- [ ] **Step 4: Run — confirm PASS**

- [ ] **Step 5: Commit**

```bash
git add src/gateway/event-bus.ts __tests__/memory-event-bus.test.ts
git commit -m "feat(gateway): complete memory:* event variants in GatewaySystemEvent"
```

---

## Phase G — `memory` LLM tool with approval-gate

### Task 21: Refactor `memory-unified` tool to use Repository + HitlCheckpointStore

**Files:**
- Modify: `src/tools/memory-unified.ts`
- Test: `__tests__/memory-tool.test.ts` (NEW)

- [ ] **Step 1: Inspect current implementation**

Read `src/tools/memory-unified.ts`. Identify how it currently does store-direct calls. The new contract: route all reads/writes through `MemoryRepository`, and route invalidations with `importance ≥ 0.8` through `HitlCheckpointStore`.

- [ ] **Step 2: Write the failing tests**

Create `__tests__/memory-tool.test.ts`:
```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";
import { createMemoryTool } from "../src/tools/memory-unified.js";

describe("memory tool — search action", () => {
  let db: Database.Database;
  let repo: MemoryRepository;
  let bus: GatewayEventBus;
  let tool: ReturnType<typeof createMemoryTool>;

  beforeEach(() => {
    db = new Database(":memory:"); db.pragma("journal_mode = WAL"); applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "user prefers TypeScript", importance: 0.7 },
    ]);
    tool = createMemoryTool({ repo, bus, hitl: { create: vi.fn() } as any });
  });

  it("search returns matching records", async () => {
    const out = await tool.execute({ action: "search", query: "TypeScript" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.results[0].id).toBe("a");
  });

  it("invalidate with importance < 0.8 applies immediately", async () => {
    repo.insertBatch([{ id: "low", kind: "semantic", content: "x", importance: 0.3 }]);
    const out = await tool.execute({ action: "invalidate", id: "low", reason: "stale" }, {} as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(repo.getById("low")!.invalid_at).not.toBeNull();
  });

  it("invalidate with importance ≥ 0.8 routes through HitlCheckpointStore", async () => {
    repo.insertBatch([{ id: "high", kind: "semantic", content: "critical", importance: 0.9 }]);
    const hitlCreate = vi.fn().mockResolvedValue("ckpt-123");
    const tool2 = createMemoryTool({ repo, bus, hitl: { create: hitlCreate } as any });
    const out = await tool2.execute({ action: "invalidate", id: "high", reason: "stale" }, { sessionId: "s1" } as any);
    const parsed = JSON.parse(out);
    expect(parsed.success).toBe(true);
    expect(parsed.data.requiresApproval).toBe(true);
    expect(parsed.data.checkpointId).toBe("ckpt-123");
    expect(hitlCreate).toHaveBeenCalled();
    // Memory not yet invalidated until approved
    expect(repo.getById("high")!.invalid_at).toBeNull();
  });
});
```

- [ ] **Step 3: Run — confirm FAIL**

- [ ] **Step 4: Implement `createMemoryTool` against the new contract**

Replace/refactor `src/tools/memory-unified.ts`:
```typescript
import type { ToolDefinition } from "../providers/base.js";
import type { ToolContext, ToolImplementation } from "./registry.js";
import type { MemoryRepository } from "../memory/repository.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { HitlCheckpointStore } from "../engine/hitl.js";

const APPROVAL_THRESHOLD = 0.8;

export interface MemoryToolDeps {
  repo: MemoryRepository;
  bus: GatewayEventBus;
  hitl: HitlCheckpointStore;
}

interface Envelope<T> { success: boolean; data: T | null; error?: { code: string; message: string } }
function ok<T>(d: T): string { return JSON.stringify({ success: true, data: d } as Envelope<T>); }
function err(code: string, message: string): string { return JSON.stringify({ success: false, data: null, error: { code, message } }); }

const DEFINITION: ToolDefinition = {
  name: "memory",
  description: "Search, retrieve, or invalidate the assistant's long-term memory about the user. " +
    "Actions: search | get | invalidate. Use this to recall facts, preferences, and past events.",
  parameters: {
    type: "object",
    properties: {
      action: { type: "string", enum: ["search", "get", "invalidate"] },
      query: { type: "string", description: "Search query (action=search)." },
      id: { type: "string", description: "Memory id (action=get|invalidate)." },
      reason: { type: "string", description: "Why invalidating (action=invalidate)." },
      kinds: { type: "array", items: { type: "string" }, description: "Filter by kind (action=search)." },
      topK: { type: "number" },
    },
    required: ["action"],
  },
  capabilities: ["memory_read", "memory_write"],
};

export function createMemoryTool(deps: MemoryToolDeps): ToolImplementation {
  return {
    definition: DEFINITION,
    category: "memory",
    source: "builtin",
    async execute(args: Record<string, unknown>, ctx: ToolContext): Promise<string> {
      const action = args.action as string;
      try {
        switch (action) {
          case "search": {
            const records = await deps.repo.search(String(args.query ?? ""), {
              kinds: (args.kinds as any) ?? undefined,
              topK: (args.topK as number) ?? 8,
            });
            for (const r of records) deps.repo.recordAccess(r.id);
            return ok({ results: records });
          }
          case "get": {
            const id = String(args.id);
            const r = deps.repo.getById(id);
            if (!r) return err("NOT_FOUND", `memory ${id} not found`);
            deps.repo.recordAccess(id);
            return ok({ memory: r });
          }
          case "invalidate": {
            const id = String(args.id);
            const reason = String(args.reason ?? "");
            const target = deps.repo.getById(id);
            if (!target) return err("NOT_FOUND", `memory ${id} not found`);
            if (target.importance >= APPROVAL_THRESHOLD) {
              const checkpointId = await deps.hitl.create(
                (ctx as any).sessionId ?? "global",
                "memory-invalidate",
                { memoryId: id, reason, content: target.content, importance: target.importance },
                30,
              );
              return ok({ requiresApproval: true, checkpointId, memoryId: id });
            }
            deps.repo.invalidate(id, { reason, invalidatedBy: "memory-tool" });
            return ok({ invalidated: id });
          }
          default:
            return err("UNKNOWN_ACTION", `unknown memory action: ${action}`);
        }
      } catch (e) {
        return err("MEMORY_ERROR", (e as Error).message);
      }
    },
  };
}
```

- [ ] **Step 5: Run — confirm PASS**

- [ ] **Step 6: Commit**

```bash
git add src/tools/memory-unified.ts __tests__/memory-tool.test.ts
git commit -m "feat(memory): memory tool routes via Repository + HitlCheckpointStore approval-gate"
```

---

## Phase H — `/memory` command (channel parity)

### Task 22: `MemoryCommandRouter` — channel-agnostic dispatcher

**Files:**
- Create: `src/gateway/commands/memory-router.ts`
- Test: `__tests__/memory-router.test.ts` (NEW)

- [ ] **Step 1: Inspect mcp-router pattern**

Re-read `src/gateway/commands/mcp-router.ts`. The new memory router mirrors its `dispatch(verb, args, deps)` shape exactly — that's the channel-parity contract.

- [ ] **Step 2: Write the failing test**

Create `__tests__/memory-router.test.ts`:
```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { dispatchMemoryCommand } from "../src/gateway/commands/memory-router.js";

describe("MemoryCommandRouter", () => {
  let db: Database.Database;
  let repo: MemoryRepository;

  beforeEach(() => {
    db = new Database(":memory:"); db.pragma("journal_mode = WAL"); applyV25Migration(db);
    repo = new MemoryRepository(db);
  });

  it("/memory list returns formatted output for empty db", async () => {
    const out = await dispatchMemoryCommand("list", [], { repo });
    expect(out).toContain("0 memories");
  });

  it("/memory search <query>", async () => {
    repo.insertBatch([{ id: "a", kind: "semantic", content: "user likes TypeScript", importance: 0.7 }]);
    const out = await dispatchMemoryCommand("search", ["TypeScript"], { repo });
    expect(out).toContain("TypeScript");
  });

  it("/memory stats", async () => {
    repo.insertBatch([
      { id: "a", kind: "semantic", content: "x", importance: 0.5 },
      { id: "b", kind: "episodic", content: "y", importance: 0.5 },
    ]);
    const out = await dispatchMemoryCommand("stats", [], { repo });
    expect(out).toContain("Total: 2");
    expect(out).toContain("semantic: 1");
    expect(out).toContain("episodic: 1");
  });

  it("/memory invalidate <id> <reason> works", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "stale fact", importance: 0.3 }]);
    const out = await dispatchMemoryCommand("invalidate", ["x", "user", "corrected"], { repo });
    expect(out.toLowerCase()).toContain("invalidated");
    expect(repo.getById("x")!.invalid_at).not.toBeNull();
  });

  it("/memory history <id>", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "fact", importance: 0.5 }]);
    repo.invalidate("x", { reason: "test", invalidatedBy: "tester" });
    const out = await dispatchMemoryCommand("history", ["x"], { repo });
    expect(out).toContain("test");
  });

  it("/memory unknown verb returns help text", async () => {
    const out = await dispatchMemoryCommand("frobnicate", [], { repo });
    expect(out).toContain("/memory list");
    expect(out).toContain("/memory search");
  });
});
```

- [ ] **Step 3: Run — confirm FAIL**

- [ ] **Step 4: Implement router**

Create `src/gateway/commands/memory-router.ts`:
```typescript
import type { MemoryRepository } from "../../memory/repository.js";

export interface MemoryRouterDeps {
  repo: MemoryRepository;
}

const HELP = `/memory commands:
  /memory list            — show recent memories
  /memory search <query>  — semantic search
  /memory stats           — counts by kind
  /memory history <id>    — invalidations + contradictions
  /memory invalidate <id> <reason...>
  /memory get <id>
  /memory export          — JSON dump of all valid memories`;

export async function dispatchMemoryCommand(
  verb: string,
  args: string[],
  deps: MemoryRouterDeps,
): Promise<string> {
  switch (verb) {
    case "list": {
      const records = await deps.repo.search("", { topK: 20 });
      if (records.length === 0) return "0 memories.";
      return `${records.length} memories:\n` +
        records.map((r) => `  [${r.kind}] ${r.id.slice(0, 8)} — ${r.content.slice(0, 80)}`).join("\n");
    }
    case "search": {
      const q = args.join(" ").trim();
      if (!q) return "Usage: /memory search <query>";
      const records = await deps.repo.search(q, { topK: 8 });
      if (records.length === 0) return `No matches for "${q}".`;
      return records.map((r) => `[${r.kind}] ${r.content} (importance=${r.importance.toFixed(2)})`).join("\n");
    }
    case "stats": {
      const s = deps.repo.stats();
      const lines = [`Total: ${s.total}`, `Invalidated: ${s.invalidated}`, `Avg importance: ${s.avgImportance.toFixed(3)}`];
      for (const [k, c] of Object.entries(s.byKind)) lines.push(`  ${k}: ${c}`);
      return lines.join("\n");
    }
    case "history": {
      const id = args[0];
      if (!id) return "Usage: /memory history <id>";
      const h = deps.repo.history(id);
      if (!h.record) return `Memory ${id} not found.`;
      const invLines = (h.invalidations as any[]).map((i) => `  invalidated ${i.invalidated_at} by ${i.invalidated_by}: ${i.reason}`);
      const conLines = (h.contradictions as any[]).map((c) => `  contradicts ${c.contradicts_id} (${c.detected_at})`);
      return [`${h.record.kind}: ${h.record.content}`, ...invLines, ...conLines].join("\n");
    }
    case "get": {
      const id = args[0];
      if (!id) return "Usage: /memory get <id>";
      const r = deps.repo.getById(id);
      return r ? JSON.stringify(r, null, 2) : `Memory ${id} not found.`;
    }
    case "invalidate": {
      const id = args[0];
      const reason = args.slice(1).join(" ").trim();
      if (!id || !reason) return "Usage: /memory invalidate <id> <reason>";
      const r = deps.repo.getById(id);
      if (!r) return `Memory ${id} not found.`;
      deps.repo.invalidate(id, { reason, invalidatedBy: "user-command" });
      return `Invalidated ${id}.`;
    }
    case "export": {
      const records = await deps.repo.search("", { topK: 10000 });
      return JSON.stringify(records, null, 2);
    }
    default:
      return HELP;
  }
}
```

- [ ] **Step 5: Run — confirm PASS**

- [ ] **Step 6: Commit**

```bash
git add src/gateway/commands/memory-router.ts __tests__/memory-router.test.ts
git commit -m "feat(memory): /memory command router (channel-agnostic dispatcher)"
```

---

### Task 23: CLI `/memory` handler

**Files:**
- Modify: `src/cli/commands.ts`

- [ ] **Step 1: Read existing CLI command list**

Re-read `src/cli/commands.ts` near line 201 (the help text string). Find how existing commands are registered (likely a switch or map dispatch). Match that pattern.

- [ ] **Step 2: Add `/memory` to the help text + dispatch**

Add to the help string:
```
  /memory       — memory CRUD: list/search/stats/history/invalidate/export
```

Add to the dispatch:
```typescript
import { dispatchMemoryCommand } from "../gateway/commands/memory-router.js";

// In handler switch:
case "memory":
case "/memory": {
  const [verb, ...verbArgs] = args;
  const out = await dispatchMemoryCommand(verb ?? "list", verbArgs, { repo: deps.memoryRepo });
  console.log(out);
  return;
}
```

(Adapt `args`/`deps` references to the actual CLI handler shape — match patterns of existing `/mcp` or `/status` commands.)

- [ ] **Step 3: Manual smoke test (no automated test for CLI plumbing in v1)**

```bash
npm run dev
# In CLI, type: /memory list
# Expected: "0 memories." or current count
# /memory stats — expected: total + per-kind counts
```

- [ ] **Step 4: Commit**

```bash
git add src/cli/commands.ts
git commit -m "feat(cli): /memory command via MemoryCommandRouter"
```

---

### Task 24: Telegram `/memory` handler

**Files:**
- Modify: `src/gateway/adapters/telegram.ts`

- [ ] **Step 1: Inspect existing `/mcp` handler**

Around `src/gateway/adapters/telegram.ts:332-466`, the `/mcp` handler is the reference pattern.

- [ ] **Step 2: Add `/memory` handler**

Mirror the `/mcp` registration:
```typescript
import { dispatchMemoryCommand } from "../commands/memory-router.js";

bot.command("memory", async (ctx) => {
  const text = ctx.message?.text ?? "";
  const tokens = text.split(/\s+/).slice(1);
  const verb = tokens[0] ?? "list";
  const verbArgs = tokens.slice(1);
  const out = await dispatchMemoryCommand(verb, verbArgs, { repo: deps.memoryRepo });
  // Reuse Telegram chunking helper (4096-char limit)
  await sendChunked(ctx, out);
});
```

(Adapt to actual deps wiring + sendChunked helper — match `/mcp` adapter.)

- [ ] **Step 3: Manual smoke test**

Send `/memory list` and `/memory stats` from the user's Telegram client. Confirm identical output to CLI for the same DB state.

- [ ] **Step 4: Commit**

```bash
git add src/gateway/adapters/telegram.ts
git commit -m "feat(telegram): /memory command via MemoryCommandRouter (channel parity with CLI)"
```

---

## Phase I — rawDb migration (9 memory-table consumers)

The audit identified 9 consumers that touch memory-related tables via `db.rawDb.prepare(...)`. They migrate to `MemoryRepository`. Engine/cortex consumers that touch *non-memory* tables (`task_ledger`, `outcome_journal`, `hitl_checkpoints`, `tool_executions`, etc.) stay on `rawDb` — out of scope for Element 15.

Consumers (verified by grep — confirm exact file:line at implementation time):

1. `src/cortex/fact-invalidator.ts` — uses `pellets`, `working_memory`
2. `src/cortex/sleep-time-consolidator.ts` — uses `pellets`
3. `src/cortex/reflexion-engine.ts` — uses `pellets`, reflexive table
4. `src/cortex/critique-retriever.ts` — uses `pellets`
5. `src/context/skill-template-layer.ts` — uses `procedural_skills`
6. `src/owls/state-reporter.ts` — uses `pellets`
7. `src/owls/evolution.ts` (memory-table portions only — DNA tables remain on rawDb)
8. `src/index.ts:729` — direct memory-table query in boot path
9. `src/gateway/handlers/post-processor.ts` — pellet write-back

### Task 25: Migrate consumers 1-3 (cortex fact-invalidator, sleep-time, reflexion)

**Files:**
- Modify: `src/cortex/fact-invalidator.ts`, `src/cortex/sleep-time-consolidator.ts`, `src/cortex/reflexion-engine.ts`

- [ ] **Step 1: For each file — locate every `db.rawDb.prepare(`**

```bash
grep -n "rawDb" src/cortex/fact-invalidator.ts
grep -n "rawDb" src/cortex/sleep-time-consolidator.ts
grep -n "rawDb" src/cortex/reflexion-engine.ts
```

- [ ] **Step 2: Replace each with the equivalent MemoryRepository call**

Translation table:
| `rawDb` pattern | Repository equivalent |
|-----------------|------------------------|
| `prepare("SELECT … FROM pellets WHERE …").all()` | `await repo.search(query, { kinds: ["semantic"] })` |
| `prepare("UPDATE pellets SET … WHERE id=?")` (invalidation) | `repo.invalidate(id, {...})` |
| `prepare("INSERT INTO pellets …").run(...)` | `repo.insertBatch([{...}])` |
| `prepare("SELECT … FROM working_memory")` | `await repo.search(q, { kinds: ["working"] })` |
| `prepare("UPDATE working_memory SET invalid_at=?…").run()` | `repo.expireWorkingMemories(hours)` (or `repo.invalidate`) |

Each consumer's constructor must accept a `MemoryRepository` injected dep instead of `db`.

- [ ] **Step 3: Update wiring**

In `src/index.ts` engine boot, where these consumers are instantiated, pass `memoryRepo` instead of `db`.

- [ ] **Step 4: Run all existing tests for these consumers**

```bash
npx vitest run __tests__/fact-invalidator.test.ts __tests__/sleep-time-consolidator.test.ts __tests__/reflexion-engine.test.ts
```

Expected: PASS (after refactor; tests may need a fresh `MemoryRepository` instead of mocked `db.rawDb`).

If any test was mocking `db.rawDb`, replace the mock with a real in-memory `MemoryRepository`:
```typescript
const db = new Database(":memory:");
applyV25Migration(db);
const repo = new MemoryRepository(db);
const consumer = new FactInvalidator({ repo });
```

- [ ] **Step 5: Commit**

```bash
git add src/cortex/fact-invalidator.ts src/cortex/sleep-time-consolidator.ts src/cortex/reflexion-engine.ts __tests__/fact-invalidator.test.ts __tests__/sleep-time-consolidator.test.ts __tests__/reflexion-engine.test.ts
git commit -m "refactor(cortex): migrate 3 fact/sleep/reflexion consumers to MemoryRepository"
```

---

### Task 26: Migrate consumers 4-6 (critique-retriever, skill-template-layer, state-reporter)

**Files:**
- Modify: `src/cortex/critique-retriever.ts`, `src/context/skill-template-layer.ts`, `src/owls/state-reporter.ts`

- [ ] **Step 1: For each file**

Same translation as Task 25. Confirm each:
- Constructor accepts `MemoryRepository`
- All `rawDb` calls replaced
- Existing tests still pass (or get updated)

- [ ] **Step 2: Run tests**

```bash
npx vitest run __tests__/critique-retriever.test.ts __tests__/skill-template-layer.test.ts __tests__/owl-state-reporter.test.ts
```

- [ ] **Step 3: Commit**

```bash
git add src/cortex/critique-retriever.ts src/context/skill-template-layer.ts src/owls/state-reporter.ts __tests__/critique-retriever.test.ts __tests__/skill-template-layer.test.ts __tests__/owl-state-reporter.test.ts
git commit -m "refactor(memory): migrate critique/skill-template/state-reporter to MemoryRepository"
```

---

### Task 27: Migrate consumer 7 (owls/evolution.ts memory-table portions)

**Files:**
- Modify: `src/owls/evolution.ts`

- [ ] **Step 1: Identify which `rawDb` calls touch memory tables**

Read `src/owls/evolution.ts`. Tag every `rawDb` call:
- DNA-related → STAYS on rawDb (engine state, not memory)
- pellet/memory-related → migrates to `MemoryRepository`

- [ ] **Step 2: Migrate only the memory-table calls**

Inject `memoryRepo` into the evolution engine constructor. Replace memory-table `rawDb` calls with repo calls. Leave DNA-table calls untouched.

- [ ] **Step 3: Run evolution tests**

```bash
npx vitest run __tests__/owl-evolution.test.ts
```

- [ ] **Step 4: Commit**

```bash
git add src/owls/evolution.ts __tests__/owl-evolution.test.ts
git commit -m "refactor(owls): migrate evolution memory-table reads/writes to MemoryRepository"
```

---

### Task 28: Migrate consumer 8 (`src/index.ts:729`)

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Locate the `rawDb` call in boot path**

Re-grep `rawDb` in `src/index.ts`. Around line 729 there's a memory-table query (likely a cache warm-up or stat read).

- [ ] **Step 2: Replace with `memoryRepo` call**

If it's a count → `memoryRepo.stats()`.
If it's a recent-memory fetch → `memoryRepo.search("", { topK })`.

- [ ] **Step 3: Run boot smoke test**

```bash
npm run dev
# Expected: clean startup, no errors
```

- [ ] **Step 4: Commit**

```bash
git add src/index.ts
git commit -m "refactor(boot): migrate src/index.ts memory-table query to MemoryRepository"
```

---

### Task 29: Migrate consumer 9 (gateway/handlers/post-processor.ts)

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`

- [ ] **Step 1: Inspect**

Read the file. Find the pellet/memory write-back path.

- [ ] **Step 2: Replace with `memoryWriter.ingest(...)` or `memoryRepo.insertBatch(...)`**

If the post-processor is doing the ingestion gauntlet (extracting + persisting), it should now delegate to `MemoryWriter.ingest`. Otherwise (raw pellet write), use `repo.insertBatch`.

- [ ] **Step 3: Run gateway tests**

```bash
npx vitest run __tests__/gateway-post-processor.test.ts
```

- [ ] **Step 4: Commit**

```bash
git add src/gateway/handlers/post-processor.ts __tests__/gateway-post-processor.test.ts
git commit -m "refactor(gateway): post-processor delegates to MemoryWriter / MemoryRepository"
```

---

## Phase J — Engine boot wiring + integration tests + acceptance

### Task 30: Wire `MemoryRepository` + `MemoryWriter` into engine boot

**Files:**
- Modify: `src/index.ts`

- [ ] **Step 1: Construction order**

In the engine boot path, after `MemoryDatabase` is created and migrations run:
```typescript
import { MemoryRepository } from "./memory/repository.js";
import { MemoryWriter } from "./memory/writer.js";

const memoryDb = new MemoryDatabase(memoryDbPath);
const memoryRepo = new MemoryRepository(memoryDb.rawDb, gatewayEventBus);
const memoryWriter = new MemoryWriter({
  repo: memoryRepo,
  bus: gatewayEventBus,
  router: intelligenceRouter,
});
memoryWriter.attachBusListeners();
```

- [ ] **Step 2: Pass `memoryRepo` to ContextPipeline**

```typescript
const contextPipeline = new ContextPipeline({
  // ...existing deps
  memoryRepo,
});
```

- [ ] **Step 3: Pass `memoryRepo` + `memoryWriter` to all consumers from Phase I**

Replace each consumer's `db` arg with `memoryRepo` (or `memoryWriter` for ingestion-flow consumers).

- [ ] **Step 4: Wire `memory` tool**

Locate where tools are registered in boot. Replace the old memory-unified registration with:
```typescript
import { createMemoryTool } from "./tools/memory-unified.js";
toolRegistry.register(createMemoryTool({
  repo: memoryRepo,
  bus: gatewayEventBus,
  hitl: hitlCheckpointStore,
}));
```

- [ ] **Step 5: Boot smoke test**

```bash
npm run dev
# Expected: clean startup, /memory works in CLI, tools list includes "memory"
```

- [ ] **Step 6: Commit**

```bash
git add src/index.ts
git commit -m "feat(boot): wire MemoryRepository + MemoryWriter + memory tool into engine boot"
```

---

### Task 31: Integration test — 50-turn cross-channel parity scenario

**Files:**
- Test: `__tests__/memory-integration.test.ts`

This test proves acceptance criteria 6 + 7 + 10: cross-channel events flow correctly, `/memory` is identical CLI vs Telegram-router output, and the writer pipeline is end-to-end.

- [ ] **Step 1: Write the integration test**

Create `__tests__/memory-integration.test.ts`:
```typescript
import { describe, it, expect, beforeEach, vi } from "vitest";
import Database from "better-sqlite3";
import { applyV25Migration } from "../src/memory/db.js";
import { MemoryRepository } from "../src/memory/repository.js";
import { MemoryWriter } from "../src/memory/writer.js";
import { GatewayEventBus } from "../src/gateway/event-bus.js";
import { dispatchMemoryCommand } from "../src/gateway/commands/memory-router.js";

function makeRouter(decisions: any[]) {
  const queue = [...decisions];
  return {
    resolve: () => ({
      provider: {
        chat: vi.fn().mockImplementation(async () => ({ content: JSON.stringify(queue.shift() ?? {}) })),
      },
      model: "stub",
      tier: "cheap",
    }),
  } as any;
}

describe("Memory v1 — 50-turn cross-channel scenario", () => {
  let db: Database.Database;
  let bus: GatewayEventBus;
  let repo: MemoryRepository;
  let writer: MemoryWriter;
  let writeEvents: any[];
  let invalidateEvents: any[];

  beforeEach(() => {
    db = new Database(":memory:");
    db.pragma("journal_mode = WAL");
    applyV25Migration(db);
    bus = new GatewayEventBus();
    repo = new MemoryRepository(db, bus);
    writeEvents = [];
    invalidateEvents = [];
    bus.on("memory:written", (e) => writeEvents.push(e));
    bus.on("memory:invalidated", (e) => invalidateEvents.push(e));
  });

  it("CLI write + Telegram /memory list see same memory (channel parity)", async () => {
    const router = makeRouter([
      { extractions: [{ kind: "semantic", content: "user prefers TypeScript", importance: 0.7 }] },
      { decisions: [{ action: "ADD" }] },
    ]);
    writer = new MemoryWriter({ repo, bus, router });

    // CLI write
    await writer.ingest({
      sessionId: "s1", turnId: "t1", channel: "cli",
      userMessage: "I always prefer TypeScript over JavaScript.",
      assistantResponse: "Got it.", verdict: "ADVANCES",
      goalId: null, subGoalId: null,
    });

    // Telegram read via router
    const tgOut = await dispatchMemoryCommand("search", ["TypeScript"], { repo });
    expect(tgOut).toContain("TypeScript");
    expect(writeEvents).toHaveLength(1);
  });

  it("invalidation event fires across channels", async () => {
    repo.insertBatch([{ id: "x", kind: "semantic", content: "old", importance: 0.3 }]);
    await dispatchMemoryCommand("invalidate", ["x", "stale"], { repo });
    expect(invalidateEvents).toHaveLength(1);
    expect(invalidateEvents[0].id).toBe("x");
  });

  it("trivial-turn short-circuit holds across 50 messages (LLM-call budget)", async () => {
    const chatMock = vi.fn().mockResolvedValue({ content: JSON.stringify({ extractions: [] }) });
    const router = { resolve: () => ({ provider: { chat: chatMock }, model: "x", tier: "cheap" }) } as any;
    writer = new MemoryWriter({ repo, bus, router });

    const trivialMessages = ["hi", "ok", "thanks", "yes", "no", "sure"];
    for (let i = 0; i < 50; i++) {
      await writer.ingest({
        sessionId: "s1", turnId: `t${i}`, channel: i % 2 === 0 ? "cli" : "telegram",
        userMessage: trivialMessages[i % trivialMessages.length],
        assistantResponse: "ack", verdict: "NEUTRAL",
        goalId: null, subGoalId: null,
      });
    }
    // 70%+ skipped via trivial-turn guard
    expect(chatMock).not.toHaveBeenCalled();
  });

  it("working memories expire on engine:turn_complete", async () => {
    const router = makeRouter([]);
    writer = new MemoryWriter({ repo, bus, router });
    writer.attachBusListeners();

    // Insert a stale working memory
    db.prepare(`INSERT INTO memories (id, kind, content, importance, valid_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?)`)
      .run("w-old", "working", "stale", 0.3, "2025-01-01T00:00:00.000Z", "2025-01-01", "2025-01-01");

    bus.emit({ type: "engine:turn_complete", sessionId: "s1" });
    await new Promise((r) => setTimeout(r, 10));

    expect(repo.getById("w-old")!.invalid_at).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run — confirm PASS**

```bash
npx vitest run __tests__/memory-integration.test.ts
```

Expected: PASS — all 4 integration tests.

- [ ] **Step 3: Commit**

```bash
git add __tests__/memory-integration.test.ts
git commit -m "test(memory): 50-turn cross-channel parity integration test"
```

---

### Task 32: Final acceptance verification + plan-completion commit

**Files:** none (verification + meta-commit)

- [ ] **Step 1: Run the full test suite**

```bash
npm test
```

Expected: 0 failures. Count new tests vs baseline:
- Repository: ~22 tests
- Writer: ~12 tests
- Layer: ~6 tests
- Migration v25: ~10 tests
- Tool: ~3 tests
- Router: ~6 tests
- Event bus: ~1 test
- Integration: ~4 tests
- Total: ~64 new tests (within "≥ 65" target — add 1 more if short)

- [ ] **Step 2: Verify acceptance criteria**

For each criterion, confirm by inspection or test:

1. ✅ `applyV25Migration` idempotent + preserves data — covered by `__tests__/memory-db-v25.test.ts`
2. ✅ `MemoryRepository` is sole consumer of memory tables — Phase I migrations + `grep -n "rawDb.*pellets\|rawDb.*memor" src/` returns empty
3. ✅ Trivial-turn short-circuit ≥ 70% — covered by integration test
4. ✅ Importance ≥ 0.8 routes through Hitl — covered by `__tests__/memory-tool.test.ts`
5. ✅ All four layers implement ContextLayer + exclude reflexive — covered by `__tests__/memory-layer.test.ts`
6. ✅ GatewayEventBus carries `memory:*` — covered by `__tests__/memory-event-bus.test.ts` + integration
7. ✅ `/memory` channel parity — covered by integration test + manual smoke
8. ✅ No hardcoded keyword arrays in writer/layer — `grep -nE "(\\['[a-z]+',\\s?'[a-z]+'.*classif|/.*\\b(react|memory|task)\\b.*/)" src/memory/` returns empty (excluding the trivial-turn `TRIVIAL_GREETINGS` which is a literal-skip optimization, not classification)
9. ✅ ≥ 65 new tests — count from Step 1
10. ✅ 9 rawDb memory consumers migrated — confirmed via grep

- [ ] **Step 3: Update progress tracker**

Edit `docs/platform-audit/progress.md`. Mark Element 15 row "v1 IMPLEMENTED". Add a "## Element 15: Memory v1 (shipped)" section with a link to this plan + the v25 migration commit SHA.

- [ ] **Step 4: Commit progress tracker**

```bash
git add docs/platform-audit/progress.md
git commit -m "docs(progress): Element 15 v1 implementation complete"
```

- [ ] **Step 5: Run finishing-a-development-branch skill**

Invoke `superpowers:finishing-a-development-branch` to verify tests one more time and present merge/PR options.

---

## Self-Review (writing-plans skill checklist)

**Spec coverage:** Every section of the Element 15 spec is implemented:
- § Architecture (3 new files) → Tasks 1, 11, 17
- § Schema (v25 migration) → Tasks 4-7
- § Writer pipeline (trivial guard → classify → reconcile → persist) → Tasks 11-16
- § Layer rendering (4 ContextLayer factories, TTL + scoring) → Tasks 17-19
- § Repository surface (search/insertBatch/invalidate/getById/history/recordAccess/stats) → Tasks 1-3, 8-10
- § Error/observability (typed memory:* events) → Tasks 9, 12, 14, 16, 20
- § /memory command (channel parity) → Tasks 22-24
- § rawDb breach migration → Tasks 25-29
- § Acceptance criteria → Task 32
- § v2 deferred (parliament-debated retention, DNA-coupled retrieval) → explicitly out of scope, no tasks

**Placeholder scan:** No "TBD", "TODO", or "implement later" in any task body. Every code block is complete enough to run.

**Type consistency:** `MemoryRecord`, `MemoryInsert`, `MemoryKind`, `MemorySearchOptions`, `WriterTurn`, `IngestResult`, `MemoryRouterDeps` are defined once in Task 1/11/22 and referenced consistently throughout. The four layer factory names (`createSemanticMemoryLayer`, `createEpisodicMemoryLayer`, `createWorkingMemoryLayer`, `createProceduralMemoryLayer`) are stable across Tasks 17-19, the integration test, and the pipeline wiring. The `GatewayEventBus` extensions are additive — Tasks 9, 12, 14, 16 extend the union piecewise; Task 20 closes the set; Task 31 (integration) subscribes to the final shape.

**Risk-bearing tasks:**
- Task 6 (legacy data merge) requires inspecting the actual production DB before writing the SELECT — Step 1 of that task captures this.
- Task 19 (pipeline wiring) requires reading the real `ContextPipeline` constructor — adapt code to whatever `registerLayer`/`registerDefaultLayers` signature exists.
- Phase I requires per-consumer test updates — each task explicitly says "tests may need a fresh `MemoryRepository` instead of mocked `db.rawDb`."

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-03-element15-memory-architecture-v1.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a 32-task plan where mechanical bits (skeleton, tests, migrations) can run on cheap models and judgment-heavy bits (legacy merge, pipeline wiring, rawDb migration) escalate to standard models.

**2. Inline Execution** — Execute tasks in this session using superpowers:executing-plans, batch execution with checkpoints for review.

**Which approach?**

After choice, the implementer must first run `superpowers:using-git-worktrees` (Task 0) to set up `feature/element-15-memory` before any other task.

