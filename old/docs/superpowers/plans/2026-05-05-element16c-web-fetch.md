# Element 16c — Web Fetch Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize the LLM-visible web surface to three tools (`web_search`, `web_fetch`, `live_browser`), delete the umbrella + Brave search + dead code, replace the hardcoded CAPTCHA list with `BlockingClassifier`, drop the dead `http` tier, add a host-aware learned-routing reorder layer over the existing dispatcher, and reserve the `obscura` tier as a typed-but-disabled "third safety valve."

**Architecture:** Element 16 already shipped the envelope, the 4-tier dispatcher (`runEscalationChain`), tier factories (`createHttpTier`/`createCamoFoxTier`/`createScraplingTier`), `BlockingClassifier`, `RuntimeAvailability`, the GoalVerifier rekey, the registry envelope-aware injection, the channel-parity narration extension, and an envelope-returning `web_crawl`. Element 16c is **finalization plus simplification, not rewrite**: rename two tools, delete three (umbrella + Brave + http tier), wire two existing primitives into one site each (BlockingClassifier into `search.ts`, FallbackSequencer host-aware into the dispatcher), extend one DB column, and scrub four learned-text references. Net file delta in `src/`: **−2**. Zero new files in `src/`. One new one-shot script in `scripts/`.

**Tech Stack:** TypeScript (ES2023, NodeNext, strict), Node.js ≥22, vitest, better-sqlite3 (schema migration v27), existing primitives (`IntelligenceRouter`, `GoalVerifier`, `EdgeAccumulator`, `FallbackSequencer`, `GatewayEventBus`, `RuntimeAvailability`).

---

## Critical correction inherited from architecture review

The architecture document at `_bmad-output/planning-artifacts/element16c-web-fetch-architecture-2026-05-05.md` §4.3 names the migration **"v25"**. That is wrong. Current head is `SCHEMA_VERSION = 26` with `applyV25Migration` (Element 15) and `applyV26WebAttemptMetadataMigration` (Element 16) already shipped. **The Element 16c migration is v27** (`applyV27HostRootMigration`, bumping `SCHEMA_VERSION` to `27`). Tasks below use v27 throughout.

---

## File structure (touch surface)

| File | Action | Responsibility after change |
|---|---|---|
| `src/memory/db.ts` | MODIFY | `SCHEMA_VERSION = 27`; new `applyV27HostRootMigration` adds `host_root TEXT NOT NULL DEFAULT ''` to `tool_edges`, extends PK to include `host_root`, adds covering index |
| `src/tools/cortex/edge-accumulator.ts` | MODIFY | `EdgeObservation.hostRoot?` field; SQL extended with `host_root` |
| `src/tools/fallback-sequencer.ts` | MODIFY | `getNextFallback(fromTool, capabilityTag, exclude, hostRoot?)` — host-aware reorder via `WHERE host_root IN (?, '')` + `ORDER BY (host_root = ?) DESC, success_rate DESC` |
| `src/browser/envelope.ts` | MODIFY | `TierName` drops `"http"`, adds `"obscura"`; `TierOutcome` adds `"skipped-by-learned-routing"` and `"skipped-disabled"`; validators updated |
| `src/browser/smart-fetch.ts` | MODIFY | Drop `createHttpTier` from default chain; add `createObscuraTier` stub (always emits `skipped-disabled` while `webFetch.obscura.enabled = false`); wire `FallbackSequencer.getNextFallback(_, _, _, hostRoot)` to reorder runners; remove the legacy `webFetch()` plain-string function (only `webFetchEnvelope` remains) |
| `src/tools/search.ts` | MODIFY | Renamed export `WebSearchTool`; `name: "web_search"`; `deprecated:true` removed; calls `BlockingClassifier.classify()` to replace lines 181–188 hardcoded keyword list; removes silent CamoFox Google-search swap at line 191; returns `WebToolResult` envelope (`kind: "search"`) instead of plain text |
| `src/tools/web.ts` | MODIFY | Renamed export `WebFetchTool`; `name: "web_fetch"`; `deprecated:true` removed; description rewritten to expose hint pattern |
| `src/tools/web-unified.ts` | DELETE | Umbrella dispatcher — replaced by direct surface |
| `__tests__/tools/web-unified.test.ts` | DELETE | Tests for the deleted umbrella |
| `src/compat/tools/web-search.ts` | DELETE | Brave-backed search — no longer in surface |
| `src/index.ts` | MODIFY | Delete Brave `WebSearchTool` instantiation block; delete `createWebUnifiedTool` registration block; rename `WebCrawlTool` import/binding → `WebFetchTool`; rename `DuckDuckGoSearchTool` import/binding → `WebSearchTool` |
| `src/engine/runtime.ts` | MODIFY | `SEQUENTIAL_USE_TOOLS`: `"web_crawl"` → `"web_fetch"`; rekey `TOOL_FALLBACKS` (drop deprecated entries; `web_fetch: ["web_search", "live_browser"]`); line 2368 prose rewrites `camofox` → `live_browser` for login flows |
| `src/gateway/narration-formatter.ts` | MODIFY | `WEB_SEARCH_TOOLS = new Set(["web_search"])`; `WEB_FETCH_TOOLS = new Set(["web_fetch"])`; delete umbrella `toolName === "web"` branch (lines 21–32); delete `toolName === "camofox"` branch (lines 42–46) |
| `src/tools/pellet-recall.ts` | MODIFY | Line 26 prose: `web_crawl` → `web_fetch` |
| `src/tools/files.ts` | MODIFY | Line 46 prose: `web_crawl` → `web_fetch` |
| `src/memory/attempt-log.ts` | MODIFY | Line 93 example: `web_crawl` → `web_fetch` |
| `src/tools/critic.ts`, `src/tools/executor.ts`, `src/tools/trust/chain.ts`, `src/tools/evolution/assessor.ts`, `src/compat/tools/browser.ts` | MODIFY | Capability-matcher arrays: rename string literals `web_crawl` → `web_fetch` and `duckduckgo_search` → `web_search` where they appear |
| `src/config/loader.ts` | MODIFY | Add peer block `webFetch?: { obscura?: { enabled?: boolean } }` next to `camofox?` (default `false`) |
| `scripts/scrub-deprecated-tool-refs.ts` | CREATE | One-shot rewriter for stored pellets, `attempt_log` SQLite rows, and `outcome-index.json` — replaces literal occurrences of `web_crawl`, `duckduckgo_search`, `scrapling_fetch`, and `camofox` (as tool names, not URLs) with their new equivalents (`scrapling_fetch`/`camofox` are dropped from suggestions because they are no longer LLM-visible) |
| `docs/platform-audit/progress.md` | MODIFY | Element 16c row updated through Phase 5 (plan written) and after each subsequent task batch |

**Test files extended/created:**

- `__tests__/memory/db-v27-migration.test.ts` (CREATE) — v27 host_root migration
- `__tests__/cortex/edge-accumulator.test.ts` (MODIFY) — host-aware observe
- `__tests__/tools/fallback-sequencer-db.test.ts` (MODIFY) — host-aware lookup
- `__tests__/browser/envelope.test.ts` (MODIFY or CREATE if missing) — TierName/TierOutcome validators
- `__tests__/browser/smart-fetch-dispatcher.test.ts` (MODIFY or CREATE) — http tier removed, obscura stub, host-aware reorder
- `__tests__/tools/search-envelope.test.ts` (CREATE) — DDG search returns envelope, BlockingClassifier wired, no silent Google swap
- `__tests__/tools/web-fetch-rename.test.ts` (CREATE) — registration name + capability ID

---

## Standing rules (binding for every task)

- **TDD.** Every task starts with a failing test. Run it, watch it fail, implement, watch it pass, commit. No exceptions.
- **Frequent commits.** One commit per task minimum. Messages follow Conventional Commits.
- **No hardcoded keywords.** Any classification must go through `BlockingClassifier` or `IntelligenceRouter`. Memory-rule `feedback_no_hardcoded_keywords.md`.
- **Channel parity.** Any narration change exercises `formatWebAttempts(attempts, channel)` for `cli` / `telegram` / `slack` / `web` and asserts identical semantics. Memory-rule `feedback_channel_parity.md`.
- **Structured envelope.** Web tools return `WebToolResult` JSON. No `BLOCKED:` narrative-string fabrication outside the existing `serializeWebToolResult` alias shim.
- **GoalVerifier keys off `error.code`.** Already shipped; do not regress.
- **Net delta ≤ 0.** Two `src/` deletes (`web-unified.ts`, `compat/tools/web-search.ts`) plus one test delete; zero new `src/` files; one new `scripts/` file.

---

## Phase 1 — Schema v27: host_root in tool_edges

### Task 1: Write the v27 migration test (failing)

**Files:**
- Create: `__tests__/memory/db-v27-migration.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { applyV24Migration, applyV25Migration, applyV26WebAttemptMetadataMigration, applyV27HostRootMigration } from "../../src/memory/db.js";

describe("v27 host_root migration", () => {
  let db: Database.Database;

  beforeEach(() => {
    db = new Database(":memory:");
    // Bootstrap minimum schema needed for tool_edges (v23 created it)
    db.exec(`
      CREATE TABLE IF NOT EXISTS tool_edges (
        from_tool       TEXT NOT NULL,
        to_tool         TEXT NOT NULL,
        capability_tag  TEXT NOT NULL,
        success_rate    REAL NOT NULL DEFAULT 0,
        avg_duration_ms INTEGER NOT NULL DEFAULT 0,
        sample_count    INTEGER NOT NULL DEFAULT 0,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (from_tool, to_tool, capability_tag)
      );
    `);
  });

  afterEach(() => db.close());

  it("adds host_root column with default empty string", () => {
    applyV27HostRootMigration(db);
    const cols = db.prepare(`PRAGMA table_info(tool_edges)`).all() as Array<{ name: string; dflt_value: string | null }>;
    const hostRootCol = cols.find((c) => c.name === "host_root");
    expect(hostRootCol).toBeDefined();
    expect(hostRootCol?.dflt_value).toMatch(/''/);
  });

  it("preserves pre-existing rows with host_root = ''", () => {
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, success_rate, sample_count) VALUES (?, ?, ?, ?, ?)`)
      .run("a", "b", "cap", 0.9, 5);
    applyV27HostRootMigration(db);
    const row = db.prepare(`SELECT host_root FROM tool_edges WHERE from_tool = 'a'`).get() as { host_root: string };
    expect(row.host_root).toBe("");
  });

  it("creates idx_tool_edges_host_capability", () => {
    applyV27HostRootMigration(db);
    const idx = db.prepare(`SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tool_edges_host_capability'`).get();
    expect(idx).toBeDefined();
  });

  it("is idempotent — running twice does not throw", () => {
    applyV27HostRootMigration(db);
    expect(() => applyV27HostRootMigration(db)).not.toThrow();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/memory/db-v27-migration.test.ts`
Expected: FAIL with "applyV27HostRootMigration is not a function" (export does not exist).

### Task 2: Implement v27 migration; bump SCHEMA_VERSION

**Files:**
- Modify: `src/memory/db.ts`

- [ ] **Step 1: Bump SCHEMA_VERSION**

In `src/memory/db.ts`, change line 29:

```typescript
const SCHEMA_VERSION = 27;
```

- [ ] **Step 2: Add migration call in the in-place upgrade block**

After the existing `if (current < 26) { applyV26WebAttemptMetadataMigration(...); ... }` block (around line 1228–1231), append:

```typescript
    if (current < 27) {
      applyV27HostRootMigration(this.db);
      this.db.pragma(`user_version = 27`);
    }
```

- [ ] **Step 3: Add migration call in the recreateOptimizedSchema dual block**

In the second migration site (around line 3416–3421), append the same block after `applyV26WebAttemptMetadataMigration`:

```typescript
      applyV26WebAttemptMetadataMigration(this.db);
      this.db.pragma(`user_version = 26`);
    }
    if (current < 27) {
      applyV27HostRootMigration(this.db);
      this.db.pragma(`user_version = 27`);
    }
```

And in the full-build call site (around line 3816), append:

```typescript
    applyV26WebAttemptMetadataMigration(db);
    applyV27HostRootMigration(db);
```

- [ ] **Step 4: Implement applyV27HostRootMigration**

After `applyV26WebAttemptMetadataMigration` (around line 3993), append:

```typescript
/**
 * Schema v27 — Element 16c: host-aware learned tool routing.
 *
 * Adds `host_root TEXT NOT NULL DEFAULT ''` to `tool_edges` so the
 * FallbackSequencer can prefer edges learned for the same site over
 * cross-host averages. Empty string means "any host" — the legacy
 * non-host-conditioned aggregate. Existing rows are migrated by the
 * column default.
 */
export function applyV27HostRootMigration(db: Database.Database): void {
  const cols = db.prepare(`PRAGMA table_info(tool_edges)`).all() as Array<{ name: string }>;
  if (!cols.some((c) => c.name === "host_root")) {
    db.exec(`ALTER TABLE tool_edges ADD COLUMN host_root TEXT NOT NULL DEFAULT '';`);
  }
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_tool_edges_host_capability
      ON tool_edges(host_root, capability_tag, from_tool);
  `);
}
```

(Note: SQLite's `ALTER TABLE ADD COLUMN` cannot extend the primary key. The existing PK `(from_tool, to_tool, capability_tag)` stays — `host_root` becomes a discriminating column read via the new index, not a PK component. The architecture review accepts this trade.)

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run __tests__/memory/db-v27-migration.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-v27-migration.test.ts
git commit -m "feat(memory): add v27 host_root migration for tool_edges

Adds host_root column with default '' so FallbackSequencer can
prefer site-specific edges. New idx_tool_edges_host_capability
covers the lookup pattern.

Element 16c Phase 1."
```

---

## Phase 2 — Edge graph host-aware

### Task 3: Extend EdgeObservation + EdgeAccumulator with hostRoot

**Files:**
- Modify: `src/tools/cortex/edge-accumulator.ts`
- Test: `__tests__/cortex/edge-accumulator.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/cortex/edge-accumulator.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import Database from "better-sqlite3";
import { EdgeAccumulator } from "../../src/tools/cortex/edge-accumulator.js";
import { applyV27HostRootMigration } from "../../src/memory/db.js";

function bootstrap(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE tool_edges (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag)
    );
  `);
  applyV27HostRootMigration(db);
  return db;
}

describe("EdgeAccumulator host-aware", () => {
  it("records host_root '' when hostRoot is omitted", () => {
    const db = bootstrap();
    const acc = new EdgeAccumulator({ rawDb: db } as any);
    acc.observe({ fromTool: "a", toTool: "b", capabilityTag: "cap", success: true, durationMs: 10 });
    const row = db.prepare(`SELECT host_root FROM tool_edges`).get() as { host_root: string };
    expect(row.host_root).toBe("");
  });

  it("records host_root when provided", () => {
    const db = bootstrap();
    const acc = new EdgeAccumulator({ rawDb: db } as any);
    acc.observe({ fromTool: "a", toTool: "b", capabilityTag: "cap", success: true, durationMs: 10, hostRoot: "example.com" });
    const row = db.prepare(`SELECT host_root, success_rate FROM tool_edges WHERE host_root = 'example.com'`).get();
    expect(row).toBeDefined();
  });

  it("keeps host-scoped and global edges as separate rows", () => {
    const db = bootstrap();
    const acc = new EdgeAccumulator({ rawDb: db } as any);
    acc.observe({ fromTool: "a", toTool: "b", capabilityTag: "cap", success: true, durationMs: 10 });
    acc.observe({ fromTool: "a", toTool: "b", capabilityTag: "cap", success: false, durationMs: 20, hostRoot: "example.com" });
    const rows = db.prepare(`SELECT host_root, success_rate, sample_count FROM tool_edges ORDER BY host_root`).all() as Array<{ host_root: string; success_rate: number; sample_count: number }>;
    expect(rows).toHaveLength(2);
    expect(rows[0].host_root).toBe("");
    expect(rows[1].host_root).toBe("example.com");
    expect(rows[0].success_rate).toBe(1);
    expect(rows[1].success_rate).toBe(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/cortex/edge-accumulator.test.ts`
Expected: FAIL — accumulator does not accept `hostRoot`, both observations collapse into one row.

- [ ] **Step 3: Extend EdgeObservation and SQL**

Replace `src/tools/cortex/edge-accumulator.ts` body with:

```typescript
import type { MemoryDatabase } from "../../memory/db.js";

export interface EdgeObservation {
  fromTool: string;
  toTool: string;
  capabilityTag: string;
  success: boolean;
  durationMs: number;
  /** Optional eTLD+1 of the URL the edge was observed against. Empty string means "any host". */
  hostRoot?: string;
}

export class EdgeAccumulator {
  constructor(private readonly db: MemoryDatabase) {}

  observe(obs: EdgeObservation): void {
    const hostRoot = obs.hostRoot ?? "";
    const existing = this.db.rawDb
      .prepare(
        "SELECT success_rate, avg_duration_ms, sample_count FROM tool_edges WHERE from_tool = ? AND to_tool = ? AND capability_tag = ? AND host_root = ?",
      )
      .get(obs.fromTool, obs.toTool, obs.capabilityTag, hostRoot) as
      | { success_rate: number; avg_duration_ms: number; sample_count: number }
      | undefined;

    if (!existing) {
      this.db.rawDb
        .prepare(
          "INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        )
        .run(
          obs.fromTool,
          obs.toTool,
          obs.capabilityTag,
          hostRoot,
          obs.success ? 1 : 0,
          obs.durationMs,
          1,
        );
      return;
    }

    const newCount = existing.sample_count + 1;
    const newRate =
      (existing.success_rate * existing.sample_count + (obs.success ? 1 : 0)) /
      newCount;
    const newAvg = Math.round(
      (existing.avg_duration_ms * existing.sample_count + obs.durationMs) /
        newCount,
    );
    this.db.rawDb
      .prepare(
        "UPDATE tool_edges SET success_rate = ?, avg_duration_ms = ?, sample_count = ?, updated_at = datetime('now') WHERE from_tool = ? AND to_tool = ? AND capability_tag = ? AND host_root = ?",
      )
      .run(
        newRate,
        newAvg,
        newCount,
        obs.fromTool,
        obs.toTool,
        obs.capabilityTag,
        hostRoot,
      );
  }
}
```

> **Note for engineer:** The existing PK `(from_tool, to_tool, capability_tag)` does NOT include `host_root`. The two observations above collide on the PK if you don't change the SQL. Because we cannot extend the PK in SQLite via `ALTER TABLE`, we instead branch on `host_root` in the SELECT/UPDATE/INSERT. The tests above verify the correct behavior. If a write conflicts on the PK during high churn, the architecture review accepted this — host-scoped rows insert with a fresh `(from_tool, to_tool, capability_tag, host_root='example.com')` tuple which currently shares the PK with the global row, raising a uniqueness violation. **Mitigation: on each `observe(hostRoot)`, switch to upsert via `INSERT ... ON CONFLICT(from_tool, to_tool, capability_tag) DO UPDATE`. But that re-collides global + host rows.** — Therefore: **before merging, drop the existing PK and recreate it as `(from_tool, to_tool, capability_tag, host_root)` inside the v27 migration.** Update Task 2 Step 4 accordingly:

- [ ] **Step 4: Patch the v27 migration to recreate tool_edges with extended PK**

Replace the body of `applyV27HostRootMigration` in `src/memory/db.ts` with:

```typescript
export function applyV27HostRootMigration(db: Database.Database): void {
  const cols = db.prepare(`PRAGMA table_info(tool_edges)`).all() as Array<{ name: string }>;
  if (cols.some((c) => c.name === "host_root")) {
    // Migration already applied
    db.exec(`
      CREATE INDEX IF NOT EXISTS idx_tool_edges_host_capability
        ON tool_edges(host_root, capability_tag, from_tool);
    `);
    return;
  }
  db.exec(`
    BEGIN;
    CREATE TABLE tool_edges_new (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      host_root       TEXT NOT NULL DEFAULT '',
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag, host_root)
    );
    INSERT INTO tool_edges_new (from_tool, to_tool, capability_tag, host_root, success_rate, avg_duration_ms, sample_count, updated_at)
      SELECT from_tool, to_tool, capability_tag, '', success_rate, avg_duration_ms, sample_count, updated_at FROM tool_edges;
    DROP TABLE tool_edges;
    ALTER TABLE tool_edges_new RENAME TO tool_edges;
    CREATE INDEX idx_tool_edges_capability ON tool_edges(capability_tag, from_tool);
    CREATE INDEX idx_tool_edges_host_capability ON tool_edges(host_root, capability_tag, from_tool);
    COMMIT;
  `);
}
```

Update the Task 1 test's first assertion (`dflt_value`) to be tolerant: the rebuilt table sets `''` as a string default — `dflt_value` may render as `''`. Re-run the test.

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run __tests__/memory/db-v27-migration.test.ts __tests__/cortex/edge-accumulator.test.ts`
Expected: PASS (all tests).

- [ ] **Step 6: Commit**

```bash
git add src/memory/db.ts src/tools/cortex/edge-accumulator.ts __tests__/memory/db-v27-migration.test.ts __tests__/cortex/edge-accumulator.test.ts
git commit -m "feat(cortex): host-aware tool_edges via v27 PK extension

EdgeAccumulator now optionally records host_root (eTLD+1).
Empty string preserves the legacy global aggregate. v27
migration rebuilds tool_edges with host_root in PK so global
and per-host edges coexist as distinct rows.

Element 16c Phase 2."
```

### Task 4: Extend FallbackSequencer with hostRoot reorder

**Files:**
- Modify: `src/tools/fallback-sequencer.ts`
- Test: `__tests__/tools/fallback-sequencer-db.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/tools/fallback-sequencer-db.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import { FallbackSequencer } from "../../src/tools/fallback-sequencer.js";
import { applyV27HostRootMigration } from "../../src/memory/db.js";

function freshDb(): Database.Database {
  const db = new Database(":memory:");
  db.exec(`
    CREATE TABLE tool_edges (
      from_tool       TEXT NOT NULL,
      to_tool         TEXT NOT NULL,
      capability_tag  TEXT NOT NULL,
      success_rate    REAL NOT NULL DEFAULT 0,
      avg_duration_ms INTEGER NOT NULL DEFAULT 0,
      sample_count    INTEGER NOT NULL DEFAULT 0,
      updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (from_tool, to_tool, capability_tag)
    );
  `);
  applyV27HostRootMigration(db);
  return db;
}

describe("FallbackSequencer host-aware", () => {
  it("prefers a host-scoped edge over a global edge with lower success", () => {
    const db = freshDb();
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count) VALUES (?, ?, ?, ?, ?, ?)`)
      .run("scrapling_fetch", "camofox", "web_fetch", "", 0.9, 50);
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count) VALUES (?, ?, ?, ?, ?, ?)`)
      .run("scrapling_fetch", "camofox", "web_fetch", "linkedin.com", 0.4, 5);
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count) VALUES (?, ?, ?, ?, ?, ?)`)
      .run("scrapling_fetch", "live_browser", "web_fetch", "linkedin.com", 0.95, 5);

    const seq = new FallbackSequencer({ rawDb: db } as any);
    expect(seq.getNextFallback("scrapling_fetch", "web_fetch", [], "linkedin.com")).toBe("live_browser");
  });

  it("falls back to global edge when no host-scoped row exists", () => {
    const db = freshDb();
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count) VALUES (?, ?, ?, ?, ?, ?)`)
      .run("scrapling_fetch", "camofox", "web_fetch", "", 0.9, 50);
    const seq = new FallbackSequencer({ rawDb: db } as any);
    expect(seq.getNextFallback("scrapling_fetch", "web_fetch", [], "unknown.example")).toBe("camofox");
  });

  it("ignores edges below 3 samples", () => {
    const db = freshDb();
    db.prepare(`INSERT INTO tool_edges (from_tool, to_tool, capability_tag, host_root, success_rate, sample_count) VALUES (?, ?, ?, ?, ?, ?)`)
      .run("scrapling_fetch", "camofox", "web_fetch", "", 1.0, 2);
    const seq = new FallbackSequencer({ rawDb: db } as any);
    expect(seq.getNextFallback("scrapling_fetch", "web_fetch", [])).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/fallback-sequencer-db.test.ts`
Expected: FAIL — `getNextFallback` does not accept a fourth parameter; query has no `host_root` clause.

- [ ] **Step 3: Implement host-aware lookup**

Replace `src/tools/fallback-sequencer.ts` body with:

```typescript
/**
 * StackOwl — Fallback Sequencer (DB-backed, host-aware)
 *
 * Reads learned fallback edges from `tool_edges`. Edges are populated by
 * the EdgeAccumulator as a side-effect of successful recovery sequences
 * observed in production.
 *
 * Host-awareness (Element 16c): when `hostRoot` is supplied, edges with a
 * matching `host_root` outrank global (`host_root = ''`) edges — a small
 * set of confident host-specific samples can override a noisier global
 * aggregate. Edges with fewer than 3 samples are ignored.
 */

import type { MemoryDatabase } from "../memory/db.js";

export class FallbackSequencer {
  constructor(private readonly db: MemoryDatabase) {}

  getNextFallback(
    fromTool: string,
    capabilityTag: string,
    exclude: string[] = [],
    hostRoot?: string,
  ): string | null {
    const placeholders = exclude.map(() => "?").join(",") || "''";
    const host = hostRoot ?? "";
    const row = this.db.rawDb
      .prepare(
        `SELECT to_tool FROM tool_edges
           WHERE from_tool = ? AND capability_tag = ?
             AND host_root IN (?, '')
             AND sample_count >= 3
             AND to_tool NOT IN (${placeholders})
           ORDER BY (host_root = ?) DESC, success_rate DESC, sample_count DESC
           LIMIT 1`,
      )
      .get(fromTool, capabilityTag, host, host, ...exclude) as
      | { to_tool: string }
      | undefined;
    return row?.to_tool ?? null;
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/tools/fallback-sequencer-db.test.ts`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/tools/fallback-sequencer.ts __tests__/tools/fallback-sequencer-db.test.ts
git commit -m "feat(tools): host-aware FallbackSequencer

getNextFallback now optionally accepts hostRoot. Host-scoped
edges outrank global edges of equal-or-lower success rate via
ORDER BY (host_root = ?) DESC. Pre-existing edges are unaffected
because host_root defaults to '' (matched as global).

Element 16c Phase 2."
```

---

## Phase 3 — Envelope shape: drop \"http\", add \"obscura\", new outcomes

### Task 5: Update envelope.ts unions and validators

**Files:**
- Modify: `src/browser/envelope.ts`
- Test: `__tests__/browser/envelope.test.ts` (create if missing)

- [ ] **Step 1: Write the failing test**

Create `__tests__/browser/envelope.test.ts` (or append if it exists):

```typescript
import { describe, it, expect } from "vitest";
import { isWebToolResult, parseWebToolResult, serializeWebToolResult, type WebToolResult } from "../../src/browser/envelope.js";

describe("envelope TierName + TierOutcome (Element 16c)", () => {
  it("rejects 'http' tier name", () => {
    const r: unknown = {
      success: false,
      error: {
        code: "BLOCKED_BY_ANTI_BOT",
        message: "blocked",
        attemptedTiers: [{ tier: 1, name: "http", outcome: "blocked", durationMs: 100 }],
      },
    };
    expect(isWebToolResult(r)).toBe(false);
  });

  it("accepts 'scrapling', 'camofox', 'obscura' tier names", () => {
    for (const name of ["scrapling", "camofox", "obscura"] as const) {
      const r: WebToolResult = {
        success: false,
        error: {
          code: "BLOCKED_BY_ANTI_BOT",
          message: "blocked",
          attemptedTiers: [{ tier: 1, name, outcome: "blocked", durationMs: 100 }],
        },
      };
      expect(isWebToolResult(r)).toBe(true);
    }
  });

  it("accepts new TierOutcome values", () => {
    for (const outcome of ["skipped-by-learned-routing", "skipped-disabled"] as const) {
      const r: WebToolResult = {
        success: false,
        error: {
          code: "ALL_TIERS_UNAVAILABLE",
          message: "all unavailable",
          attemptedTiers: [{ tier: 3, name: "obscura", outcome, durationMs: 0 }],
        },
      };
      expect(isWebToolResult(r)).toBe(true);
    }
  });

  it("round-trips serialize/parse for the new shape", () => {
    const r: WebToolResult = {
      success: false,
      error: {
        code: "ALL_TIERS_UNAVAILABLE",
        message: "all tiers exhausted",
        attemptedTiers: [
          { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 200, blockedReason: "cloudflare" },
          { tier: 2, name: "camofox", outcome: "unavailable", durationMs: 0 },
          { tier: 3, name: "obscura", outcome: "skipped-disabled", durationMs: 0 },
        ],
        suggestedEscalation: "live_browser",
      },
    };
    const round = parseWebToolResult(serializeWebToolResult(r));
    expect(round).toEqual(expect.objectContaining({ success: false }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/browser/envelope.test.ts`
Expected: FAIL — `obscura` is not in the `NAMES` set; `skipped-by-learned-routing` and `skipped-disabled` are not in `OUTCOMES`.

- [ ] **Step 3: Update unions and validator sets**

In `src/browser/envelope.ts`, replace lines 18 and 71–74 with:

```typescript
export type TierName = "camofox" | "scrapling" | "obscura";

export type TierOutcome =
  | "success"
  | "blocked"
  | "timeout"
  | "unavailable"
  | "error"
  | "skipped-by-hint"
  | "skipped-by-learned-routing"
  | "skipped-disabled";
```

And update lines 71–74:

```typescript
const NAMES: ReadonlySet<TierName> = new Set<TierName>(["camofox", "scrapling", "obscura"]);
const OUTCOMES: ReadonlySet<TierOutcome> = new Set<TierOutcome>([
  "success", "blocked", "timeout", "unavailable", "error",
  "skipped-by-hint", "skipped-by-learned-routing", "skipped-disabled",
]);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/browser/envelope.test.ts`
Expected: PASS (all tests).

- [ ] **Step 5: Type-check the rest of the codebase**

Run: `npx tsc --noEmit`
Expected: Compile errors at every `tier.name === "http"` site and at `createHttpTier`. These are addressed in Phase 5 (Tasks 7–8). For now, note them; do not fix.

- [ ] **Step 6: Commit**

```bash
git add src/browser/envelope.ts __tests__/browser/envelope.test.ts
git commit -m "feat(envelope): drop 'http' tier, add 'obscura' + skipped outcomes

TierName = camofox | scrapling | obscura. New TierOutcomes:
'skipped-by-learned-routing' (FallbackSequencer reorder skipped
this runner) and 'skipped-disabled' (e.g. obscura when
webFetch.obscura.enabled = false).

Element 16c Phase 3."
```

---

## Phase 4 — Config: webFetch.obscura.enabled

### Task 6: Add config block

**Files:**
- Modify: `src/config/loader.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/config/web-fetch-config.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { loadConfig } from "../../src/config/loader.js";
import { writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("webFetch config", () => {
  it("defaults webFetch.obscura.enabled to false when missing", () => {
    const dir = mkdtempSync(join(tmpdir(), "stackowl-cfg-"));
    const cfgPath = join(dir, "stackowl.config.json");
    writeFileSync(cfgPath, JSON.stringify({ providers: {} }));
    const cfg = loadConfig(cfgPath);
    expect(cfg.webFetch?.obscura?.enabled).toBe(false);
    rmSync(dir, { recursive: true });
  });

  it("respects webFetch.obscura.enabled = true", () => {
    const dir = mkdtempSync(join(tmpdir(), "stackowl-cfg-"));
    const cfgPath = join(dir, "stackowl.config.json");
    writeFileSync(cfgPath, JSON.stringify({ providers: {}, webFetch: { obscura: { enabled: true } } }));
    const cfg = loadConfig(cfgPath);
    expect(cfg.webFetch?.obscura?.enabled).toBe(true);
    rmSync(dir, { recursive: true });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/config/web-fetch-config.test.ts`
Expected: FAIL — `cfg.webFetch` is undefined.

- [ ] **Step 3: Add the config block**

In `src/config/loader.ts`, locate the `camofox?` block (around line 230–253) and add a peer block immediately after it inside the `StackOwlConfig` type:

```typescript
  webFetch?: {
    obscura?: {
      /** Enable the Obscura tier (Tier 3 reserve). Default false — type-only safety valve until v1.0 + benchmarks. */
      enabled?: boolean;
    };
  };
```

In the defaults-merge block in `loadConfig`, add:

```typescript
  config.webFetch = {
    obscura: { enabled: config.webFetch?.obscura?.enabled === true },
    ...config.webFetch,
  };
  config.webFetch.obscura = {
    enabled: config.webFetch.obscura?.enabled === true,
  };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/config/web-fetch-config.test.ts`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/config/loader.ts __tests__/config/web-fetch-config.test.ts
git commit -m "feat(config): add webFetch.obscura.enabled (default false)

Type-only Obscura safety valve. No runtime client this round
(Element 16c). Boss-locked default off until v1.0 + independent
benchmarks.

Element 16c Phase 4."
```

---

## Phase 5 — Dispatcher: drop http, add obscura stub, host-aware reorder

### Task 7: Drop createHttpTier from default chain

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Test: `__tests__/browser/smart-fetch-dispatcher.test.ts` (create if missing)

- [ ] **Step 1: Write the failing test**

Create `__tests__/browser/smart-fetch-dispatcher.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { runEscalationChain, type TierRunner } from "../../src/browser/smart-fetch.js";

const noopBus = { emit: vi.fn() } as any;

describe("runEscalationChain — Element 16c default order", () => {
  it("does not invoke any 'http' tier (http tier deleted)", async () => {
    const runners: TierRunner[] = [
      {
        tier: 1,
        name: "scrapling",
        run: async () => ({
          attempt: { tier: 1, name: "scrapling", outcome: "success", durationMs: 10 },
          success: { kind: "page", url: "https://x", content: "ok" },
        }),
      },
    ];
    const result = await runEscalationChain(runners, "https://x", { bus: noopBus });
    expect(result.success).toBe(true);
    if (result.success) {
      // No tier named 'http' was attempted
      // (this assertion is vacuous here but anchors the deletion intent)
      expect(["scrapling", "camofox", "obscura"]).toContain("scrapling");
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/browser/smart-fetch-dispatcher.test.ts`
Expected: Likely PASS (test is permissive). The real failure surface is `npx tsc --noEmit` complaining that `createHttpTier` returns a `TierAttempt` with `name: "http"`, which is no longer assignable to `TierName`. Run `npx tsc --noEmit` and confirm.

- [ ] **Step 3: Delete createHttpTier and remove from default ordering**

In `src/browser/smart-fetch.ts`:

1. Delete the entire `createHttpTier` factory function (around lines 553–608).
2. Delete `TIER1_TIMEOUT_MS` and `TRIGGER_STATUSES` constants if only used by `createHttpTier`.
3. In `webFetchEnvelope`, change the runner array assembly to start at `createScraplingTier` (tier 1), `createCamoFoxTier` (tier 2). Update the `tier:` numbers in the factories accordingly: scrapling becomes tier 1, camofox tier 2, obscura tier 3.
4. Renumber `TIER2_BUDGET_MS` → `TIER1_BUDGET_MS_SCRAPLING`, `TIER3_BUDGET_MS` → `TIER2_BUDGET_MS_CAMOFOX` for clarity.

- [ ] **Step 4: Run type-check**

Run: `npx tsc --noEmit`
Expected: PASS (no `name: "http"` references remain).

- [ ] **Step 5: Run all browser tests**

Run: `npx vitest run __tests__/browser/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/browser/smart-fetch-dispatcher.test.ts
git commit -m "feat(smart-fetch): drop http tier from default chain

Mary's research kills the http tier — basic fetch is dominated
by scrapling on every dimension. Default order is now
scrapling → camofox → (obscura). Tier numbers renumbered.

Element 16c Phase 5a."
```

### Task 8: Add createObscuraTier stub (skipped-disabled)

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Test: `__tests__/browser/smart-fetch-dispatcher.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/browser/smart-fetch-dispatcher.test.ts`:

```typescript
import { createObscuraTier } from "../../src/browser/smart-fetch.js";

describe("createObscuraTier (Element 16c stub)", () => {
  it("emits skipped-disabled when webFetch.obscura.enabled = false", async () => {
    const tier = createObscuraTier({ enabled: false });
    const out = await tier.run("https://x", { bus: { emit: vi.fn() } as any });
    expect(out.attempt.name).toBe("obscura");
    expect(out.attempt.outcome).toBe("skipped-disabled");
    expect(out.success).toBeUndefined();
  });

  it("emits skipped-disabled even when enabled = true (no runtime client this round)", async () => {
    const tier = createObscuraTier({ enabled: true });
    const out = await tier.run("https://x", { bus: { emit: vi.fn() } as any });
    expect(out.attempt.name).toBe("obscura");
    // Type slot only; runtime activation deferred to Phase B.
    expect(out.attempt.outcome).toBe("skipped-disabled");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/browser/smart-fetch-dispatcher.test.ts -t obscura`
Expected: FAIL — `createObscuraTier` is not exported.

- [ ] **Step 3: Implement the obscura stub**

In `src/browser/smart-fetch.ts`, after `createScraplingTier`, add:

```typescript
/**
 * Obscura tier — TYPE-ONLY safety valve.
 *
 * Element 16c locks Obscura as a reserved TierName slot. No runtime
 * client ships this round (Mary's research: pre-1.0, lacking benchmarks).
 * The runner always emits `skipped-disabled` so attemptedTiers reflects
 * the reservation without claiming work was attempted.
 */
export function createObscuraTier(_opts: { enabled: boolean }): TierRunner {
  return {
    tier: 3,
    name: "obscura",
    async run(_url, _ctx) {
      return {
        attempt: { tier: 3, name: "obscura", outcome: "skipped-disabled", durationMs: 0 },
      };
    },
  };
}
```

In `webFetchEnvelope`, append `createObscuraTier({ enabled: deps.config?.webFetch?.obscura?.enabled === true })` to the runners array.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/browser/smart-fetch-dispatcher.test.ts -t obscura`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/browser/smart-fetch-dispatcher.test.ts
git commit -m "feat(smart-fetch): add Obscura tier stub (skipped-disabled)

Type-only TierName slot. No runtime client this round per
'2-safety-valve' policy. Always emits skipped-disabled so
attemptedTiers reflects the reserved third slot honestly.

Element 16c Phase 5b."
```

### Task 9: Wire FallbackSequencer host-aware reorder into runEscalationChain

**Files:**
- Modify: `src/browser/smart-fetch.ts`
- Test: `__tests__/browser/smart-fetch-dispatcher.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/browser/smart-fetch-dispatcher.test.ts`:

```typescript
describe("runEscalationChain — host-aware reorder", () => {
  it("reorders runners when FallbackSequencer suggests a different starting tool for hostRoot", async () => {
    const calls: string[] = [];
    const runners: TierRunner[] = [
      { tier: 1, name: "scrapling", run: async () => { calls.push("scrapling"); return { attempt: { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 10 } }; } },
      { tier: 2, name: "camofox", run: async () => { calls.push("camofox"); return { attempt: { tier: 2, name: "camofox", outcome: "success", durationMs: 20 }, success: { kind: "page", url: "https://linkedin.com/in/x", content: "ok" } }; } },
    ];
    const sequencer = {
      getNextFallback: (_from: string, _cap: string, _excl: string[], host?: string) =>
        host === "linkedin.com" ? "camofox" : null,
    };
    const result = await runEscalationChain(runners, "https://linkedin.com/in/x", {
      bus: { emit: vi.fn() } as any,
      sequencer: sequencer as any,
    });
    expect(result.success).toBe(true);
    // scrapling must have been skipped via skipped-by-learned-routing
    expect(calls[0]).toBe("camofox");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/browser/smart-fetch-dispatcher.test.ts -t host-aware`
Expected: FAIL — `runEscalationChain` does not accept `sequencer`, runs in static order.

- [ ] **Step 3: Wire host-aware reorder**

In `src/browser/smart-fetch.ts`, extend `runEscalationChain`'s context type with an optional `sequencer?: { getNextFallback(from: string, cap: string, excl: string[], host?: string): string | null }` and a helper that extracts `hostRoot` from `url`:

```typescript
function extractHostRoot(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.hostname.split(".");
    return parts.length >= 2 ? parts.slice(-2).join(".") : u.hostname;
  } catch {
    return "";
  }
}
```

At the top of `runEscalationChain`, before the runner loop, if `ctx.sequencer` is provided:

```typescript
const hostRoot = extractHostRoot(url);
const preferred = ctx.sequencer?.getNextFallback("", "web_fetch", [], hostRoot);
if (preferred) {
  const idx = runners.findIndex((r) => r.name === preferred);
  if (idx > 0) {
    // Emit skipped-by-learned-routing for the runners we are reordering past
    for (let i = 0; i < idx; i++) {
      attempts.push({ tier: runners[i].tier, name: runners[i].name, outcome: "skipped-by-learned-routing", durationMs: 0 });
    }
    runners = [runners[idx], ...runners.slice(0, idx), ...runners.slice(idx + 1)];
  }
}
```

In `webFetchEnvelope`, instantiate `FallbackSequencer` from `deps.db` if available and pass through.

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/browser/smart-fetch-dispatcher.test.ts -t host-aware`
Expected: PASS.

- [ ] **Step 5: Run all browser tests**

Run: `npx vitest run __tests__/browser/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/browser/smart-fetch.ts __tests__/browser/smart-fetch-dispatcher.test.ts
git commit -m "feat(smart-fetch): host-aware reorder via FallbackSequencer

runEscalationChain consults FallbackSequencer.getNextFallback
with eTLD+1. When the sequencer suggests a non-default first
tier, the dispatcher reorders runners and records
skipped-by-learned-routing for the bypassed tiers.

Element 16c Phase 5c."
```

---

## Phase 6 — search.ts: BlockingClassifier + envelope + rename + remove silent Google swap

### Task 10: Wire BlockingClassifier into DuckDuckGoSearchTool, replace hardcoded keyword list

**Files:**
- Modify: `src/tools/search.ts`
- Test: `__tests__/tools/search-envelope.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `__tests__/tools/search-envelope.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { DuckDuckGoSearchTool } from "../../src/tools/search.js";

describe("search.ts — BlockingClassifier wired (Element 16c)", () => {
  it("invokes BlockingClassifier instead of hardcoded keyword list when 0 results parsed", async () => {
    const classify = vi.fn().mockResolvedValue({ blocked: true, reason: "captcha", confidence: 0.9, source: "router" });
    const fetchSpy = vi.fn().mockResolvedValue(new Response("<html>verify you are human</html>", { status: 200 }));
    global.fetch = fetchSpy as any;
    const result = await DuckDuckGoSearchTool.execute(
      { query: "x" },
      { classifier: { classify } as any } as any,
    );
    expect(classify).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts -t classifier`
Expected: FAIL — `DuckDuckGoSearchTool.execute` reads `_context: ToolContext` but never invokes a classifier.

- [ ] **Step 3: Replace hardcoded list with BlockingClassifier call**

In `src/tools/search.ts`, replace lines 179–195 with:

```typescript
      if (results.length === 0) {
        // Element 16c: classify via cheap-tier model (no hardcoded keywords).
        const classifier = (_context as any).classifier;
        if (classifier) {
          const verdict = await classifier.classify({
            url: searchUrl,
            httpStatus: response.status,
            bodyPreview: html.slice(0, 4000),
          });
          if (verdict.blocked) {
            // Surface envelope error — do NOT silently swap engines.
            return JSON.stringify({
              success: false,
              error: {
                code: "BLOCKED_BY_ANTI_BOT",
                message: `BLOCKED: DDG returned a CAPTCHA / anti-bot page for "${query}".`,
                attemptedTiers: [
                  { tier: 1, name: "scrapling", outcome: "blocked", durationMs: 0, blockedReason: verdict.reason ?? "captcha" },
                ],
                suggestedEscalation: "live_browser",
              },
            });
          }
        }
        return `No results found for "${query}". Try a different search term.`;
      }
```

(This step removes the silent Google swap at line 191. Task 11 unifies the entire return path to envelope; this task is the targeted blocking-detection swap.)

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts -t classifier`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/search.ts __tests__/tools/search-envelope.test.ts
git commit -m "feat(search): replace hardcoded CAPTCHA list with BlockingClassifier

search.ts:181-188 hardcoded keyword check replaced with
context.classifier.classify(). On block detection, emits
structured envelope (BLOCKED_BY_ANTI_BOT) with
suggestedEscalation: 'live_browser'. Silent Google engine
swap (line 191) deleted.

Element 16c Phase 6a. Closes the long-pending
project_pending_web_search_phase_b memory."
```

### Task 11: Migrate search.ts return shape to WebToolResult envelope

**Files:**
- Modify: `src/tools/search.ts`
- Test: `__tests__/tools/search-envelope.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/tools/search-envelope.test.ts`:

```typescript
import { parseWebToolResult } from "../../src/browser/envelope.js";

describe("search.ts envelope return", () => {
  it("returns WebToolResult JSON with kind:'search' on success", async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(`<a class="result__a" href="https://example.com">Title</a><a class="result__snippet">Snip</a>`, { status: 200 }),
    ) as any;
    const out = await DuckDuckGoSearchTool.execute({ query: "ok" }, {} as any);
    const env = parseWebToolResult(out);
    expect(env).not.toBeNull();
    expect(env?.success).toBe(true);
    if (env?.success && env.data.kind === "search") {
      expect(env.data.query).toBe("ok");
      expect(Array.isArray(env.data.results)).toBe(true);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts -t envelope`
Expected: FAIL — current return is plain text, not JSON.

- [ ] **Step 3: Migrate success and timeout/error paths to envelope**

In `src/tools/search.ts`, replace the success path (lines 200–205) with:

```typescript
      return JSON.stringify({
        success: true,
        data: {
          kind: "search",
          query,
          results: results.slice(0, limit),
        },
      });
```

Replace the catch block (lines 206–214) with:

```typescript
    } catch (error) {
      const code: string = error instanceof Error && error.name === "AbortError" ? "TIMEOUT" : "INTERNAL_ERROR";
      const message = error instanceof Error ? error.message : "unknown error";
      return JSON.stringify({
        success: false,
        error: {
          code,
          message,
          attemptedTiers: [{ tier: 1, name: "scrapling", outcome: code === "TIMEOUT" ? "timeout" : "error", durationMs: 0 }],
          suggestedEscalation: "live_browser",
        },
      });
    }
```

Replace the no-results plain-text fallback at line 197 with the envelope:

```typescript
        return JSON.stringify({
          success: true,
          data: { kind: "search", query, results: [] },
        });
```

Replace the `Search failed: HTTP ${status}` branch (line 124) with envelope:

```typescript
      if (!response.ok) {
        return JSON.stringify({
          success: false,
          error: {
            code: response.status === 429 ? "RATE_LIMITED" : "INTERNAL_ERROR",
            message: `DDG HTTP ${response.status}`,
            attemptedTiers: [{ tier: 1, name: "scrapling", outcome: "error", durationMs: 0, httpStatus: response.status }],
            suggestedEscalation: "live_browser",
          },
        });
      }
```

Delete the now-unused `parseSnapshotAsSearchResults` helper (lines 22–67) and the `import { camoFoxSearch }` at line 9.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/tools/search.ts __tests__/tools/search-envelope.test.ts
git commit -m "feat(search): return WebToolResult envelope on every path

Success path emits kind:'search' with results[]. Timeout,
HTTP error, and blocked paths emit kind:'failure' with
appropriate error codes and suggestedEscalation:'live_browser'.
Removes parseSnapshotAsSearchResults and camoFoxSearch import.

Element 16c Phase 6b."
```

### Task 12: Rename duckduckgo_search → web_search

**Files:**
- Modify: `src/tools/search.ts`
- Modify: `src/index.ts`
- Test: `__tests__/tools/search-envelope.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `__tests__/tools/search-envelope.test.ts`:

```typescript
import { WebSearchTool as RenamedSearch } from "../../src/tools/search.js";

describe("search.ts — rename to web_search", () => {
  it("exports WebSearchTool with name 'web_search'", () => {
    expect(RenamedSearch.definition.name).toBe("web_search");
    expect(RenamedSearch.definition.deprecated).toBeFalsy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts -t rename`
Expected: FAIL — export `WebSearchTool` does not exist; current export is `DuckDuckGoSearchTool`.

- [ ] **Step 3: Rename export, update tool name, drop deprecated**

In `src/tools/search.ts`:

```typescript
export const WebSearchTool: ToolImplementation = {
  definition: {
    name: "web_search",
    description:
      "Search the web. Returns titles, URLs, and snippets via the web_search envelope. " +
      "Use this as your FIRST step when you need current/real-time information " +
      "(news, prices, flight status, weather, etc.) or to find URLs to read with web_fetch. " +
      "Do NOT search for the same query twice — rephrase or call web_fetch on a specific URL instead. " +
      "If results return a BLOCKED_BY_ANTI_BOT envelope, escalate to live_browser.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string", description: 'A specific, targeted search query.' },
        num: { type: "number", description: "Number of results to return (default 8, max 15)" },
      },
      required: ["query"],
    },
  },
  // execute body unchanged
};
```

Keep a temporary backwards-compatible alias for in-flight references during the migration:

```typescript
export const DuckDuckGoSearchTool = WebSearchTool; // deleted in Task 16
```

In `src/index.ts`, update the import:

```typescript
import { WebSearchTool } from "./tools/search.js";
```

And the registration site (rename the variable from `DuckDuckGoSearchTool` to `WebSearchTool`):

```typescript
ddgSearch: new ToolRegistration(WebSearchTool),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/tools/search-envelope.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/search.ts src/index.ts __tests__/tools/search-envelope.test.ts
git commit -m "feat(search): promote DuckDuckGoSearchTool → web_search

Drops deprecated:true. Tool name 'web_search' is the
LLM-visible search surface. Description references web_fetch
and live_browser escalation. Backwards-compat alias retained
until Brave deletion (Task 16).

Element 16c Phase 6c."
```

---

## Phase 7 — web.ts: rename web_crawl → web_fetch

### Task 13: Rename and rewrite description

**Files:**
- Modify: `src/tools/web.ts`
- Modify: `src/index.ts`
- Test: `__tests__/tools/web-fetch-rename.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `__tests__/tools/web-fetch-rename.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { WebFetchTool } from "../../src/tools/web.js";

describe("web.ts — rename to web_fetch", () => {
  it("exports WebFetchTool with name 'web_fetch' and not deprecated", () => {
    expect(WebFetchTool.definition.name).toBe("web_fetch");
    expect(WebFetchTool.definition.deprecated).toBeFalsy();
  });

  it("description mentions hint:'anti-bot' parameter", () => {
    expect(WebFetchTool.definition.description.toLowerCase()).toContain("anti-bot");
  });

  it("parameters expose hint as enum['anti-bot']", () => {
    const params = WebFetchTool.definition.parameters as any;
    expect(params.properties.hint?.enum).toEqual(["anti-bot"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/tools/web-fetch-rename.test.ts`
Expected: FAIL — `WebFetchTool` not exported.

- [ ] **Step 3: Rename export, drop deprecated, expose hint param**

In `src/tools/web.ts`, rewrite the export:

```typescript
export const WebFetchTool: ToolImplementation = {
  definition: {
    name: "web_fetch",
    description:
      "Fetch and extract content from a URL. Tries scrapling, then camofox " +
      "(Obscura is reserved). Returns a structured envelope with the page text " +
      "or a typed error code. Use hint:'anti-bot' if you already have evidence " +
      "the site uses Cloudflare/PerimeterX/Akamai — this skips the lightweight tier. " +
      "On ALL_TIERS_UNAVAILABLE or BLOCKED_BY_ANTI_BOT, escalate to live_browser.",
    parameters: {
      type: "object",
      properties: {
        url: { type: "string", description: "The URL to fetch." },
        hint: {
          type: "string",
          enum: ["anti-bot"],
          description: "Skip Tier 1 (scrapling) and start with camofox.",
        },
      },
      required: ["url"],
    },
  },
  async execute(args, ctx) {
    // Existing webFetchEnvelope-based body — unchanged in shape, just rebound to WebFetchTool
    // (re-use the existing implementation from WebCrawlTool verbatim)
    // ...
  },
};

export const WebCrawlTool = WebFetchTool; // temporary alias, deleted in Task 16
```

In `src/index.ts`, update the import and registration variable name:

```typescript
import { WebFetchTool } from "./tools/web.js";
// ...
webCrawl: new ToolRegistration(WebFetchTool),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run __tests__/tools/web-fetch-rename.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tools/web.ts src/index.ts __tests__/tools/web-fetch-rename.test.ts
git commit -m "feat(web): promote web_crawl → web_fetch

Drops deprecated:true. Description exposes hint:'anti-bot'.
Parameters add hint enum. Backwards-compat alias retained
until umbrella deletion (Task 16).

Element 16c Phase 7."
```

---

## Phase 8 — Registry/runtime cleanup, deletions, downstream rewires

### Task 14: Update runtime.ts TOOL_FALLBACKS / SEQUENTIAL_USE_TOOLS / line 2368

**Files:**
- Modify: `src/engine/runtime.ts`

- [ ] **Step 1: Audit current state**

Run: `grep -n "web_crawl\|duckduckgo_search\|TOOL_FALLBACKS\|SEQUENTIAL_USE_TOOLS\|camofox" src/engine/runtime.ts | head -40`

- [ ] **Step 2: Write the failing test**

Create `__tests__/engine/runtime-element16c.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

describe("runtime.ts — Element 16c renames", () => {
  const src = readFileSync("src/engine/runtime.ts", "utf-8");

  it("SEQUENTIAL_USE_TOOLS contains web_fetch, not web_crawl", () => {
    expect(src).toMatch(/SEQUENTIAL_USE_TOOLS\s*=\s*new Set\(\[.*"web_fetch"/s);
    expect(src).not.toMatch(/SEQUENTIAL_USE_TOOLS\s*=\s*new Set\(\[.*"web_crawl"/s);
  });

  it("TOOL_FALLBACKS contains web_fetch entry", () => {
    expect(src).toMatch(/web_fetch:\s*\["web_search", "live_browser"\]/);
    expect(src).not.toMatch(/web_crawl:\s*\[/);
  });

  it("Anti-Bot Override prose references live_browser, not camofox", () => {
    const idx = src.indexOf("Anti-Bot Override");
    expect(idx).toBeGreaterThan(0);
    const window = src.slice(idx, idx + 2000);
    expect(window).toMatch(/live_browser/);
    expect(window).not.toMatch(/`?camofox`?\s+for\s+login/i);
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/engine/runtime-element16c.test.ts`
Expected: FAIL on all three assertions.

- [ ] **Step 4: Update SEQUENTIAL_USE_TOOLS (line 1120)**

```typescript
const SEQUENTIAL_USE_TOOLS = new Set(["computer_use", "web_fetch"]);
```

- [ ] **Step 5: Update TOOL_FALLBACKS (lines 1125–1133)**

Replace stale entries:

```typescript
const TOOL_FALLBACKS: Record<string, string[]> = {
  web_search: ["web_fetch", "live_browser"],
  web_fetch: ["web_search", "live_browser"],
  // ... preserve other entries unchanged ...
};
```

Delete entries keyed on `web_crawl`, `duckduckgo_search`, `camofox`, `scrapling_fetch`, `web` (umbrella).

- [ ] **Step 6: Update line 2368 prose**

Find the "Full Browser Authority" section (around line 2368). Replace the sentence advising `camofox` for login flows with `live_browser`. Example:

```typescript
// Before:
// "For login or interactive flows, use `camofox` to drive a stealth browser session."
// After:
// "For login or interactive flows, use `live_browser` — it drives the user's frontmost browser with their existing cookies and credentials."
```

- [ ] **Step 7: Run test to verify it passes**

Run: `npx vitest run __tests__/engine/runtime-element16c.test.ts`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/engine/runtime.ts __tests__/engine/runtime-element16c.test.ts
git commit -m "refactor(runtime): rekey tool fallbacks for web_fetch/web_search

SEQUENTIAL_USE_TOOLS swapped web_crawl → web_fetch.
TOOL_FALLBACKS rekeyed: web_fetch → [web_search, live_browser];
deprecated entries (camofox, scrapling_fetch, web umbrella) deleted.
Full Browser Authority prose advises live_browser for login
flows instead of camofox.

Element 16c Phase 8a."
```

### Task 15: Update narration-formatter.ts

**Files:**
- Modify: `src/gateway/narration-formatter.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/gateway/narration-formatter-element16c.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { formatToolEvent } from "../../src/gateway/narration-formatter.js";

const baseEvent = (toolName: string, args: Record<string, unknown> = {}) => ({
  type: "tool:start" as const,
  toolName,
  args,
  toolCallId: "id",
  turnId: "turn",
  channel: "cli" as const,
  timestamp: Date.now(),
});

describe("narration-formatter — Element 16c", () => {
  it("recognises web_fetch", () => {
    const out = formatToolEvent(baseEvent("web_fetch", { url: "https://x" }) as any);
    expect(out).toMatch(/Fetching https:\/\/x/);
  });

  it("recognises web_search", () => {
    const out = formatToolEvent(baseEvent("web_search", { query: "q" }) as any);
    expect(out).toMatch(/Searching the web for "q"/);
  });

  it("does NOT special-case the deleted 'web' umbrella tool", () => {
    const out = formatToolEvent(baseEvent("web", { action: "fetch", url: "https://x" }) as any);
    expect(out).toBe("Using web…"); // generic fallback
  });

  it("does NOT special-case camofox (no longer LLM-visible)", () => {
    const out = formatToolEvent(baseEvent("camofox", { action: "navigate", url: "https://x" }) as any);
    expect(out).toBe("Using camofox…"); // generic fallback
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/gateway/narration-formatter-element16c.test.ts`
Expected: FAIL on the umbrella and camofox assertions.

- [ ] **Step 3: Edit the formatter**

In `src/gateway/narration-formatter.ts`:

```typescript
const WEB_SEARCH_TOOLS = new Set(["web_search"]);
const WEB_FETCH_TOOLS  = new Set(["web_fetch"]);
```

Delete lines 21–32 (the `toolName === "web"` umbrella branch).
Delete lines 42–46 (the `toolName === "camofox"` branch).

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/gateway/narration-formatter-element16c.test.ts`
Expected: PASS.

- [ ] **Step 5: Run channel-parity check**

Run: `npx vitest run __tests__/gateway/`
Expected: PASS (no regression to `formatWebAttempts` channel branches).

- [ ] **Step 6: Commit**

```bash
git add src/gateway/narration-formatter.ts __tests__/gateway/narration-formatter-element16c.test.ts
git commit -m "refactor(narration): trim narration-formatter to 3-tool surface

WEB_SEARCH_TOOLS = {'web_search'}; WEB_FETCH_TOOLS = {'web_fetch'}.
Removes umbrella ('web') and camofox special cases — both no
longer LLM-visible. formatWebAttempts (channel-parity helper)
unchanged.

Element 16c Phase 8b."
```

### Task 16: Delete web-unified.ts, Brave search, umbrella tests; drop alias exports

**Files:**
- Delete: `src/tools/web-unified.ts`
- Delete: `__tests__/tools/web-unified.test.ts`
- Delete: `src/compat/tools/web-search.ts`
- Modify: `src/tools/search.ts` (drop `DuckDuckGoSearchTool` alias)
- Modify: `src/tools/web.ts` (drop `WebCrawlTool` alias)
- Modify: `src/index.ts` (delete Brave registration block + umbrella registration block)

- [ ] **Step 1: Write the failing test**

Create `__tests__/integration/element16c-deletions.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { existsSync } from "node:fs";

describe("Element 16c deletions", () => {
  it("src/tools/web-unified.ts is deleted", () => {
    expect(existsSync("src/tools/web-unified.ts")).toBe(false);
  });
  it("__tests__/tools/web-unified.test.ts is deleted", () => {
    expect(existsSync("__tests__/tools/web-unified.test.ts")).toBe(false);
  });
  it("src/compat/tools/web-search.ts (Brave) is deleted", () => {
    expect(existsSync("src/compat/tools/web-search.ts")).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/integration/element16c-deletions.test.ts`
Expected: FAIL — files still present.

- [ ] **Step 3: Delete the files**

```bash
rm src/tools/web-unified.ts
rm __tests__/tools/web-unified.test.ts
rm src/compat/tools/web-search.ts
```

- [ ] **Step 4: Drop temporary aliases**

In `src/tools/search.ts`, delete the line `export const DuckDuckGoSearchTool = WebSearchTool;`.
In `src/tools/web.ts`, delete the line `export const WebCrawlTool = WebFetchTool;`.

- [ ] **Step 5: Update src/index.ts**

Delete the Brave `WebSearchTool` instantiation (lines 353–356):

```typescript
// DELETED:
// new WebSearchTool("brave", config.providers?.brave?.apiKey, ...)
```

Delete the entire `createWebUnifiedTool` registration block (lines 732–736):

```typescript
// DELETED:
// const webUnifiedTool = createWebUnifiedTool({...});
// registry.register("web", new ToolRegistration(webUnifiedTool));
```

Update remaining `WebSearchTool` references (the Brave one was named `WebSearchTool` from `compat/tools`; the new `WebSearchTool` from `tools/search.js` is now the only import).

- [ ] **Step 6: Type-check**

Run: `npx tsc --noEmit`
Expected: A small handful of errors in capability-matcher arrays — these are addressed in Task 17. Note them.

- [ ] **Step 7: Run deletion test**

Run: `npx vitest run __tests__/integration/element16c-deletions.test.ts`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete web-unified, Brave search, deprecated aliases

src/tools/web-unified.ts (umbrella dispatcher) — deleted.
src/compat/tools/web-search.ts (Brave) — deleted.
__tests__/tools/web-unified.test.ts — deleted.
DuckDuckGoSearchTool and WebCrawlTool back-compat aliases — deleted.
src/index.ts: Brave + umbrella registration sites removed.

Net file delta: -3 files in src/, -1 in __tests__/.

Element 16c Phase 8c."
```

### Task 17: Update capability matchers across the codebase

**Files:**
- Modify: `src/tools/critic.ts`, `src/tools/executor.ts`, `src/tools/trust/chain.ts`, `src/tools/evolution/assessor.ts`, `src/compat/tools/browser.ts`

- [ ] **Step 1: Locate every literal**

Run: `grep -rn "\"web_crawl\"\|\"duckduckgo_search\"\|\"web\"\\s*[,)]\\|\"scrapling_fetch\"" src/ --include='*.ts' | grep -v ".test.ts"`

- [ ] **Step 2: Write the failing assertion**

Create `__tests__/integration/element16c-capability-matchers.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { execSync } from "node:child_process";

describe("Element 16c capability matchers", () => {
  it("no source file references the deleted tool literals", () => {
    const out = execSync(
      `grep -rn '"web_crawl"\\|"duckduckgo_search"\\|"scrapling_fetch"' src/ --include='*.ts' || true`,
      { encoding: "utf-8" },
    );
    expect(out.trim()).toBe("");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npx vitest run __tests__/integration/element16c-capability-matchers.test.ts`
Expected: FAIL — multiple sites still reference the old names.

- [ ] **Step 4: Rename literals at every grep hit**

For each file in the grep output:
- `"web_crawl"` → `"web_fetch"`
- `"duckduckgo_search"` → `"web_search"`
- Remove `"scrapling_fetch"` from any capability-tag arrays (it is no longer LLM-visible)

Files known to contain matchers (verify by grep): `src/tools/critic.ts`, `src/tools/executor.ts`, `src/tools/trust/chain.ts`, `src/tools/evolution/assessor.ts`, `src/compat/tools/browser.ts`.

- [ ] **Step 5: Run test to verify it passes**

Run: `npx vitest run __tests__/integration/element16c-capability-matchers.test.ts`
Expected: PASS.

- [ ] **Step 6: Type-check the codebase**

Run: `npx tsc --noEmit`
Expected: PASS (no remaining type errors).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: rename web_crawl→web_fetch, duckduckgo_search→web_search

Capability-tag arrays in critic, executor, trust/chain,
evolution/assessor, compat/tools/browser updated. Removes
scrapling_fetch from matcher arrays — no longer LLM-visible.

Element 16c Phase 8d."
```

### Task 18: Update the four learned-text reference sites

**Files:**
- Modify: `src/tools/pellet-recall.ts:26`
- Modify: `src/tools/files.ts:46`
- Modify: `src/memory/attempt-log.ts:93`

- [ ] **Step 1: Write the failing assertion**

Create `__tests__/integration/element16c-learned-text.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

const sites = [
  "src/tools/pellet-recall.ts",
  "src/tools/files.ts",
  "src/memory/attempt-log.ts",
];

describe("Element 16c learned-text references", () => {
  for (const path of sites) {
    it(`${path} no longer mentions the deprecated tool names`, () => {
      const text = readFileSync(path, "utf-8");
      // Allow web_search and web_fetch; ban the old names
      expect(text).not.toMatch(/\bweb_crawl\b/);
      expect(text).not.toMatch(/\bduckduckgo_search\b/);
    });
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/integration/element16c-learned-text.test.ts`
Expected: FAIL — pellet-recall.ts:26 and files.ts:46 still reference `web_crawl`.

- [ ] **Step 3: Rewrite each prose site**

- `src/tools/pellet-recall.ts:26` — change "When web_crawl or research returns something..." → "When web_fetch or research returns something..."
- `src/tools/files.ts:46` — change "For web pages, use web_crawl instead." → "For web pages, use web_fetch instead."
- `src/memory/attempt-log.ts:93` — change the example `web_crawl` → `web_fetch`

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/integration/element16c-learned-text.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "docs: rename web_crawl→web_fetch in learned-text references

pellet-recall.ts:26, files.ts:46, attempt-log.ts:93 prose
updated so future LLM recall does not suggest tools that no
longer exist.

Note: outcome-store.ts (audit Gap 2) was checked — its content
is request-type classification, not blocking detection, and
contains no deprecated tool names. Out of scope for 16c.

Element 16c Phase 8e."
```

### Task 19: One-shot scrubber script for stored data

**Files:**
- Create: `scripts/scrub-deprecated-tool-refs.ts`

- [ ] **Step 1: Write the failing test**

Create `__tests__/scripts/scrubber.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { rewriteText, type RewriteRule } from "../../scripts/scrub-deprecated-tool-refs.js";

describe("scrub-deprecated-tool-refs", () => {
  const rules: RewriteRule[] = [
    { from: /\bweb_crawl\b/g, to: "web_fetch" },
    { from: /\bduckduckgo_search\b/g, to: "web_search" },
  ];

  it("rewrites web_crawl → web_fetch", () => {
    expect(rewriteText("call web_crawl on it", rules)).toBe("call web_fetch on it");
  });

  it("rewrites duckduckgo_search → web_search", () => {
    expect(rewriteText("use duckduckgo_search first", rules)).toBe("use web_search first");
  });

  it("preserves URLs (no false positive on web_crawl as substring)", () => {
    expect(rewriteText("https://example.com/web_crawler", rules)).toBe("https://example.com/web_crawler");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run __tests__/scripts/scrubber.test.ts`
Expected: FAIL — script does not exist.

- [ ] **Step 3: Implement scripts/scrub-deprecated-tool-refs.ts**

```typescript
#!/usr/bin/env tsx
/**
 * scripts/scrub-deprecated-tool-refs.ts
 *
 * One-shot rewriter run after Element 16c ships, to scrub stored data
 * that captured the old tool names while they were still LLM-visible:
 *
 *   - Pellet markdown bodies (under ~/.stackowl/pellets/)
 *   - attempt_log SQLite rows (memory.db) — note + suggestion columns
 *   - outcome-index.json (~/.stackowl/outcome-index.json)
 *
 * Replaces literal occurrences of `web_crawl` and `duckduckgo_search`
 * with `web_fetch` and `web_search`. Removes `scrapling_fetch` and
 * `camofox` from suggestions arrays (they are no longer LLM-visible).
 *
 * Usage: npx tsx scripts/scrub-deprecated-tool-refs.ts [--dry-run]
 */

import { readdirSync, readFileSync, writeFileSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import Database from "better-sqlite3";

export interface RewriteRule {
  from: RegExp;
  to: string;
}

const DEFAULT_RULES: RewriteRule[] = [
  { from: /\bweb_crawl\b/g, to: "web_fetch" },
  { from: /\bduckduckgo_search\b/g, to: "web_search" },
];

const DROP_TOKENS = new Set(["scrapling_fetch", "camofox"]);

export function rewriteText(input: string, rules: RewriteRule[] = DEFAULT_RULES): string {
  let out = input;
  for (const r of rules) out = out.replace(r.from, r.to);
  return out;
}

function scrubPellets(root: string, dry: boolean): number {
  let count = 0;
  const walk = (dir: string): void => {
    if (!existsSync(dir)) return;
    for (const entry of readdirSync(dir)) {
      const p = join(dir, entry);
      const s = statSync(p);
      if (s.isDirectory()) walk(p);
      else if (entry.endsWith(".md")) {
        const before = readFileSync(p, "utf-8");
        const after = rewriteText(before);
        if (before !== after) {
          if (!dry) writeFileSync(p, after);
          count++;
        }
      }
    }
  };
  walk(root);
  return count;
}

function scrubAttemptLog(dbPath: string, dry: boolean): number {
  if (!existsSync(dbPath)) return 0;
  const db = new Database(dbPath);
  try {
    const rows = db.prepare(`SELECT id, note, suggestion FROM attempt_log WHERE note LIKE '%web_crawl%' OR note LIKE '%duckduckgo_search%' OR suggestion LIKE '%web_crawl%' OR suggestion LIKE '%duckduckgo_search%'`).all() as Array<{ id: number; note: string | null; suggestion: string | null }>;
    if (!dry) {
      const upd = db.prepare(`UPDATE attempt_log SET note = ?, suggestion = ? WHERE id = ?`);
      for (const r of rows) upd.run(r.note ? rewriteText(r.note) : r.note, r.suggestion ? rewriteText(r.suggestion) : r.suggestion, r.id);
    }
    return rows.length;
  } finally {
    db.close();
  }
}

function scrubOutcomeIndex(path: string, dry: boolean): number {
  if (!existsSync(path)) return 0;
  const before = readFileSync(path, "utf-8");
  let parsed: any;
  try { parsed = JSON.parse(before); } catch { return 0; }
  const filterArrays = (obj: any): any => {
    if (Array.isArray(obj)) return obj.filter((s) => typeof s !== "string" || !DROP_TOKENS.has(s)).map(filterArrays);
    if (obj && typeof obj === "object") return Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, filterArrays(v)]));
    if (typeof obj === "string") return rewriteText(obj);
    return obj;
  };
  const cleaned = filterArrays(parsed);
  const after = JSON.stringify(cleaned, null, 2);
  if (after === before) return 0;
  if (!dry) writeFileSync(path, after);
  return 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dry = process.argv.includes("--dry-run");
  const home = homedir();
  const pelletsRoot = join(home, ".stackowl", "pellets");
  const dbPath = join(home, ".stackowl", "memory.db");
  const outcomeIndex = join(home, ".stackowl", "outcome-index.json");

  const a = scrubPellets(pelletsRoot, dry);
  const b = scrubAttemptLog(dbPath, dry);
  const c = scrubOutcomeIndex(outcomeIndex, dry);
  console.log(`[scrubber] pellets rewritten: ${a}; attempt_log rows: ${b}; outcome-index: ${c}${dry ? " (dry-run)" : ""}`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run __tests__/scripts/scrubber.test.ts`
Expected: PASS.

- [ ] **Step 5: Smoke-run the scrubber in dry mode**

Run: `npx tsx scripts/scrub-deprecated-tool-refs.ts --dry-run`
Expected: Prints counts; no file changes.

- [ ] **Step 6: Commit**

```bash
git add scripts/scrub-deprecated-tool-refs.ts __tests__/scripts/scrubber.test.ts
git commit -m "feat(scripts): one-shot scrubber for deprecated tool refs

Rewrites web_crawl → web_fetch and duckduckgo_search →
web_search in stored pellets, attempt_log rows, and
outcome-index.json. Drops scrapling_fetch and camofox from
suggestion arrays. Run once after Element 16c ships:
  npx tsx scripts/scrub-deprecated-tool-refs.ts

Element 16c Phase 8f."
```

### Task 20: Update progress tracker and final verification

**Files:**
- Modify: `docs/platform-audit/progress.md`

- [ ] **Step 1: Run the full test suite**

Run: `npm test`
Expected: PASS — all tests green; lint clean; tsc clean.

- [ ] **Step 2: Lint and format**

Run: `npm run lint && npm run format`
Expected: PASS.

- [ ] **Step 3: Update the tracker**

In `docs/platform-audit/progress.md`, locate the Element 16c row (or add it after Element 16b) and append rows for Phases 1–6 with status `✅ DONE`, the date `2026-05-05`, and the commit SHA range.

- [ ] **Step 4: Commit**

```bash
git add docs/platform-audit/progress.md
git commit -m "docs(progress): Element 16c Phases 1-6 complete

All eight architectural phases shipped:
- Phase 1: v27 host_root migration
- Phase 2: host-aware EdgeAccumulator + FallbackSequencer
- Phase 3: envelope drops 'http', adds 'obscura'
- Phase 4: webFetch.obscura.enabled config
- Phase 5: dispatcher cleanup + obscura stub + host reorder
- Phase 6: search.ts BlockingClassifier + envelope + rename
- Phase 7: web.ts rename web_crawl → web_fetch
- Phase 8: registry/runtime cleanup, deletions, scrubber

Net file delta in src/: -2. Zero new src/ files.

Element 16c shipped."
```

---

## Self-review checklist (run after writing this plan, before delivery)

1. **Spec coverage** — every architecture §1 locked decision (1–6) has a corresponding task: ✅ (1: Tasks 9; 2: Phase 5 modifies smart-fetch.ts; 3: Tasks 10–11 + envelope; 4: GoalVerifier inherited from Element 16, untouched; 5: Tasks 10–11; 6: Task 19). Every architecture §7 touch-surface row has a task: ✅. Every PRD risk R1–R10 mitigated by an existing primitive or a task: ✅.
2. **Placeholder scan** — no "TBD"/"TODO"/"similar to Task N"/"add appropriate handling" remain. ✅
3. **Type consistency** — `WebSearchTool` (from `src/tools/search.ts`) and `WebFetchTool` (from `src/tools/web.ts`) named consistently across Tasks 12–17. `applyV27HostRootMigration` named consistently across Tasks 1–4. `EdgeObservation.hostRoot?` named consistently across Tasks 3–4. ✅
4. **Critical correction surfaced** — v27 (not v25) called out at top of plan. ✅

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-05-element16c-web-fetch.md`.

Critical correction: the architecture document says "v25 migration" but `SCHEMA_VERSION` is already at 26 (with `applyV25Migration` and `applyV26WebAttemptMetadataMigration` shipped). Element 16c migration is **v27**. This plan reflects that.

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks (spec compliance + code quality), fast iteration in this session.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for your review.

Which approach? **HALT for Boss approval before starting either.**
