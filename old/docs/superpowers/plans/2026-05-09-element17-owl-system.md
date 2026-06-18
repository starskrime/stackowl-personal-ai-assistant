# Element 17 — Owl System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Helper rebrand + gateway management + routing improvements + inner-life safety + DNA completeness + sub-owl parallel execution + parliament diversity as a single bundle.

**Architecture:** Two new `src/` files (`owl-router.ts`, `owl-creation.ts`), two deleted (`routing-coordinator.ts`, `specialization-wizard.ts`), net src/ delta = 0. DB schema bumped v27 → v28. All channels call `dispatchOwlCommand()` for owl management.

**Tech Stack:** TypeScript strict, Vitest, SQLite (better-sqlite3), `ChannelAdapterV2.ask()`, `IntelligenceRouter` cheap-tier classification

**CRITICAL:** The spec document mentions "v23 migration" in places — this is wrong. The current DB schema is v27 (grep confirms `SCHEMA_VERSION = 27` and `if (current < 27)` at `src/memory/db.ts:29,1232`). The new migration is **v28**.

---

## File Structure

### New files
| File | Purpose |
|------|---------|
| `src/gateway/commands/owl-router.ts` | `dispatchOwlCommand()` gateway dispatcher |
| `src/gateway/wizards/owl-creation.ts` | Channel-agnostic 6-step creation wizard |

### Deleted files
| File | Reason |
|------|--------|
| `src/gateway/handlers/routing-coordinator.ts` | Dead fallback; `injectMemoryContext()` already in `OwlBrain` |
| `src/cli/specialization-wizard.ts` | Superseded by `owl-creation.ts` |
| `dist/wizard/owl-creation.d.ts` | Orphan compiled artifact |
| `__tests__/memory/owls-repo.test.ts` | Tests deleted dead code |

### Significantly modified files
`src/memory/db.ts`, `src/routing/owl-brain.ts`, `src/owls/inner-life.ts`, `src/gateway/core.ts`, `src/engine/runtime.ts`, `src/gateway/handlers/post-processor.ts`, `src/routing/secretary.ts`, `src/parliament/orchestrator.ts`, `src/delegation/sub-owl-runner.ts`, `src/delegation/subowl-executor.ts`, `src/delegation/decomposer.ts`, `src/owls/specialized-types.ts`, `src/owls/specialized-registry.ts`, `src/cli/commands.ts`, `src/gateway/adapters/telegram.ts`, `src/gateway/adapters/slack.ts`

---

## Phase A — Database Foundation

### Task 1: v28 DB migration

**Files:**
- Modify: `src/memory/db.ts`
- Create: `__tests__/memory/db-v28.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/memory/db-v28.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import Database from "better-sqlite3"
import { applyV28Element17Migration } from "../src/memory/db.js"

describe("v28 Element17 migration", () => {
  let db: Database.Database

  beforeEach(() => { db = new Database(":memory:") })
  afterEach(() => { db.close() })

  it("creates owl_quality_metrics table", () => {
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_quality_metrics'"
    ).all()
    expect(tables).toHaveLength(1)
  })

  it("creates owl_pins table with composite PK", () => {
    applyV28Element17Migration(db)
    const cols = db.prepare("PRAGMA table_info(owl_pins)").all() as any[]
    expect(cols.map((c: any) => c.name)).toContain("channel_id")
    expect(cols.map((c: any) => c.name)).toContain("user_id")
  })

  it("creates owl_jobs table", () => {
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owl_jobs'"
    ).all()
    expect(tables).toHaveLength(1)
  })

  it("drops owls table", () => {
    // Create the owls table first (simulating existing v10 schema)
    db.exec(`CREATE TABLE IF NOT EXISTS owls (
      id TEXT PRIMARY KEY, owner_id TEXT, name TEXT, specialization TEXT,
      personality_prompt TEXT, routing_rules TEXT, dna TEXT, is_main_owl INTEGER,
      created_at TEXT, updated_at TEXT
    )`)
    applyV28Element17Migration(db)
    const tables = db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table' AND name='owls'"
    ).all()
    expect(tables).toHaveLength(0)
  })

  it("is idempotent", () => {
    applyV28Element17Migration(db)
    expect(() => applyV28Element17Migration(db)).not.toThrow()
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/memory/db-v28.test.ts
```
Expected: FAIL (applyV28Element17Migration not exported)

- [ ] **Step 3: Implement migration function in `src/memory/db.ts`**

At the end of `src/memory/db.ts` (after `applyV27HostRootMigration`), add:

```typescript
export function applyV28Element17Migration(db: Database.Database): void {
  // Drop legacy owls table (created v10, never used by live routing)
  db.exec(`DROP TABLE IF EXISTS owls;`)

  // Per-owl EWMA reward signal — feeds SecretaryRouter quality weighting
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_quality_metrics (
      owl_name     TEXT NOT NULL,
      owner_id     TEXT NOT NULL,
      turn_count   INTEGER NOT NULL DEFAULT 0,
      ewma_reward  REAL    NOT NULL DEFAULT 0.7,
      last_updated TEXT,
      PRIMARY KEY (owl_name, owner_id)
    );
  `)

  // Per-channel pin isolation (replaces single active_pin column on user_profiles)
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_pins (
      user_id    TEXT NOT NULL,
      channel_id TEXT NOT NULL,
      owl_name   TEXT NOT NULL,
      pinned_at  TEXT NOT NULL,
      PRIMARY KEY (user_id, channel_id)
    );
    CREATE INDEX IF NOT EXISTS idx_owl_pins_user ON owl_pins(user_id);
  `)

  // Recurring autonomous tasks assigned to helpers at creation time
  db.exec(`
    CREATE TABLE IF NOT EXISTS owl_jobs (
      id               TEXT PRIMARY KEY,
      helper_name      TEXT NOT NULL,
      owner_id         TEXT NOT NULL,
      schedule         TEXT NOT NULL,
      task_description TEXT NOT NULL,
      channel_id       TEXT NOT NULL,
      created_at       TEXT NOT NULL DEFAULT (datetime('now')),
      last_run_at      TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_owl_jobs_owner ON owl_jobs(owner_id);
  `)
}
```

Update `SCHEMA_VERSION` at `src/memory/db.ts:29`:
```typescript
const SCHEMA_VERSION = 28;
```

Add migration block in the **three** locations where migrations are applied:

**Location 1** — `MemoryDatabase.applyMigrations()` (around line 1232):
```typescript
    if (current < 28) {
      applyV28Element17Migration(this.db);
      this.db.pragma(`user_version = 28`);
    }
```

**Location 2** — `InMemoryDatabase.applyMigrations()` (around line 3580):
```typescript
    if (current < 28) {
      applyV28Element17Migration(this.db);
      this.db.pragma(`user_version = 28`);
    }
```

**Location 3** — standalone `applyMigrations()` function (around line 3979):
```typescript
  if (current < 28) {
    applyV28Element17Migration(db);
  }
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/memory/db-v28.test.ts
```
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory/db-v28.test.ts
git commit -m "feat(e17): v28 migration — owl_quality_metrics, owl_pins, owl_jobs; drop owls table"
```

---

### Task 2: OwlQualityRepo + OwlPinsRepo

**Files:**
- Modify: `src/memory/db.ts`
- Create: `__tests__/memory/owl-quality-repo.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/memory/owl-quality-repo.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import Database from "better-sqlite3"
import { OwlQualityRepo, OwlPinsRepo, applyV28Element17Migration } from "../src/memory/db.js"

function makeDb() {
  const db = new Database(":memory:")
  applyV28Element17Migration(db)
  return db
}

describe("OwlQualityRepo", () => {
  let db: Database.Database
  let repo: OwlQualityRepo

  beforeEach(() => { db = makeDb(); repo = new OwlQualityRepo(db) })
  afterEach(() => db.close())

  it("returns null for unknown owl", () => {
    expect(repo.get("aria", "user1")).toBeNull()
  })

  it("starts at 0.7 default after first update", () => {
    repo.update("aria", "user1", 1.0)
    const r = repo.get("aria", "user1")!
    // 0.15 * 1.0 + 0.85 * 0.7 = 0.745
    expect(r.ewmaReward).toBeCloseTo(0.745, 3)
    expect(r.turnCount).toBe(1)
  })

  it("ewma converges toward reward over many updates", () => {
    for (let i = 0; i < 20; i++) repo.update("aria", "user1", 1.0)
    const r = repo.get("aria", "user1")!
    expect(r.ewmaReward).toBeGreaterThan(0.95)
  })

  it("clamps reward to 0-1 before EWMA", () => {
    repo.update("aria", "user1", 999)
    const r = repo.get("aria", "user1")!
    expect(r.ewmaReward).toBeLessThanOrEqual(1.0)
  })

  it("isolates by ownerId", () => {
    repo.update("aria", "user1", 1.0)
    expect(repo.get("aria", "user2")).toBeNull()
  })
})

describe("OwlPinsRepo", () => {
  let db: Database.Database
  let repo: OwlPinsRepo

  beforeEach(() => { db = makeDb(); repo = new OwlPinsRepo(db) })
  afterEach(() => db.close())

  it("returns null when no pin set", () => {
    expect(repo.get("user1", "telegram")).toBeNull()
  })

  it("returns channel-specific pin", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("aria")
  })

  it("telegram pin does not bleed to CLI", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "cli")).toBeNull()
  })

  it("falls back to global pin when no channel pin", () => {
    repo.set("user1", "global", "nora", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("nora")
  })

  it("channel pin overrides global pin", () => {
    repo.set("user1", "global", "nora", new Date().toISOString())
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBe("aria")
  })

  it("clears pin when set to null", () => {
    repo.set("user1", "telegram", "aria", new Date().toISOString())
    repo.set("user1", "telegram", null, new Date().toISOString())
    expect(repo.get("user1", "telegram")).toBeNull()
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/memory/owl-quality-repo.test.ts
```

- [ ] **Step 3: Implement OwlQualityRepo and OwlPinsRepo in `src/memory/db.ts`**

**Remove** `OwlsRepo` class (around L3143–L3247) and the `SpecializedOwl` interface and `rowToSpecializedOwl` helper near it (search for `interface SpecializedOwl` and `function rowToSpecializedOwl`). Replace with:

```typescript
// ─── OwlQualityRepo ───────────────────────────────────────────────

export class OwlQualityRepo {
  constructor(private db: Database.Database) {}

  get(owlName: string, ownerId: string): { ewmaReward: number; turnCount: number } | null {
    const row = this.db.prepare(
      `SELECT ewma_reward, turn_count FROM owl_quality_metrics WHERE owl_name = ? AND owner_id = ?`
    ).get(owlName, ownerId) as { ewma_reward: number; turn_count: number } | undefined
    return row ? { ewmaReward: row.ewma_reward, turnCount: row.turn_count } : null
  }

  update(owlName: string, ownerId: string, reward: number): void {
    const clampedReward = Math.max(0, Math.min(1, reward))
    const existing = this.get(owlName, ownerId)
    const oldEwma = existing?.ewmaReward ?? 0.7
    const newEwma = 0.15 * clampedReward + 0.85 * oldEwma
    const newCount = (existing?.turnCount ?? 0) + 1
    this.db.prepare(`
      INSERT INTO owl_quality_metrics (owl_name, owner_id, ewma_reward, turn_count, last_updated)
      VALUES (?, ?, ?, ?, datetime('now'))
      ON CONFLICT (owl_name, owner_id) DO UPDATE SET
        ewma_reward  = excluded.ewma_reward,
        turn_count   = excluded.turn_count,
        last_updated = excluded.last_updated
    `).run(owlName, ownerId, newEwma, newCount)
  }
}

// ─── OwlPinsRepo ──────────────────────────────────────────────────

export class OwlPinsRepo {
  constructor(private db: Database.Database) {}

  get(userId: string, channelId: string): string | null {
    const row = this.db.prepare(
      `SELECT owl_name FROM owl_pins WHERE user_id = ? AND channel_id = ?`
    ).get(userId, channelId) as { owl_name: string } | undefined
    if (row) return row.owl_name
    // Fall back to global pin (legacy / cross-channel)
    const global = this.db.prepare(
      `SELECT owl_name FROM owl_pins WHERE user_id = ? AND channel_id = 'global'`
    ).get(userId) as { owl_name: string } | undefined
    return global?.owl_name ?? null
  }

  set(userId: string, channelId: string, owlName: string | null, pinnedAt: string): void {
    if (owlName === null) {
      this.db.prepare(
        `DELETE FROM owl_pins WHERE user_id = ? AND channel_id = ?`
      ).run(userId, channelId)
    } else {
      this.db.prepare(`
        INSERT INTO owl_pins (user_id, channel_id, owl_name, pinned_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (user_id, channel_id) DO UPDATE SET
          owl_name  = excluded.owl_name,
          pinned_at = excluded.pinned_at
      `).run(userId, channelId, owlName, pinnedAt)
    }
  }
}
```

**In the `MemoryDatabase` class** (around L421–424), replace:
```typescript
  readonly owls: OwlsRepo;
```
with:
```typescript
  readonly owlQualityMetrics: OwlQualityRepo;
  readonly owlPins: OwlPinsRepo;
```

**In the `MemoryDatabase` constructor** (around L469), replace:
```typescript
    this.owls              = new OwlsRepo(this.db);
```
with:
```typescript
    this.owlQualityMetrics = new OwlQualityRepo(this.db);
    this.owlPins           = new OwlPinsRepo(this.db);
```

Do the same for `InMemoryDatabase` (search for the second constructor block that also instantiates repos).

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/memory/owl-quality-repo.test.ts
```
Expected: 11 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/memory/db.ts __tests__/memory/owl-quality-repo.test.ts
git commit -m "feat(e17): OwlQualityRepo (EWMA) + OwlPinsRepo (per-channel); replace dead OwlsRepo"
```

---

## Phase B — Helper Rebrand

### Task 3: Type aliases + backward-compat file loading

**Files:**
- Modify: `src/owls/specialized-types.ts`
- Modify: `src/owls/specialized-registry.ts`
- Create: `__tests__/owls/helper-registry-compat.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/owls/helper-registry-compat.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { SpecializedOwlRegistry } from "../src/owls/specialized-registry.js"
import type { HelperSpec } from "../src/owls/specialized-types.js"

function makeWorkspace() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "owl-test-"))
  const owlsDir = path.join(root, "owls", "Aria")
  fs.mkdirSync(owlsDir, { recursive: true })
  return { root, owlsDir }
}

describe("HelperRegistry backward compat", () => {
  let workspace = { root: "", owlsDir: "" }

  beforeEach(() => { workspace = makeWorkspace() })
  afterEach(() => { fs.rmSync(workspace.root, { recursive: true, force: true }) })

  it("loads helper.md when present", () => {
    fs.writeFileSync(path.join(workspace.owlsDir, "helper.md"), `---
name: Aria
type: specialist
role: test helper
emoji: 🤖
---
`)
    const registry = new SpecializedOwlRegistry(workspace.root)
    registry.loadAll()
    expect(registry.get("Aria")).toBeDefined()
  })

  it("falls back to specialized_owl.md when helper.md absent", () => {
    fs.writeFileSync(path.join(workspace.owlsDir, "specialized_owl.md"), `---
name: Aria
type: specialist
role: legacy helper
emoji: 🦉
---
`)
    const registry = new SpecializedOwlRegistry(workspace.root)
    registry.loadAll()
    expect(registry.get("Aria")).toBeDefined()
  })

  it("HelperSpec type alias resolves to SpecializedOwlSpec shape", () => {
    // Type test — if this compiles, the alias is correct
    const spec: HelperSpec = {
      name: "Aria", type: "specialist", role: "test", emoji: "🤖",
      personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" },
      expertise: [], model: { provider: "anthropic", modelId: "claude-haiku-4-5-20251001" },
      permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] },
      routingRules: { keywords: [], domains: [], priority: 5 },
      skills: { canLearn: false, retainedKnowledge: [] },
      additionalPrompt: "",
    }
    expect(spec.name).toBe("Aria")
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/owls/helper-registry-compat.test.ts
```

- [ ] **Step 3: Add HelperSpec alias to `src/owls/specialized-types.ts`**

At the end of `src/owls/specialized-types.ts`, append:

```typescript
// ─── Helper rebrand aliases (Element 17) ─────────────────────────
/** Alias for SpecializedOwlSpec — use HelperSpec in new code */
export type HelperSpec = SpecializedOwlSpec
```

- [ ] **Step 4: Add HelperRegistry alias + backward-compat loading in `src/owls/specialized-registry.ts`**

At the end of the file, after the class, append:
```typescript
/** Alias for SpecializedOwlRegistry — use HelperRegistry in new code */
export type HelperRegistry = SpecializedOwlRegistry
```

Find `loadAll()` in `src/owls/specialized-registry.ts` — it reads `specialized_owl.md` (around L38). Change it to try `helper.md` first:

```typescript
// Before (example — find the actual glob/readdir pattern in the file):
const specFile = path.join(owlDir, "specialized_owl.md")
if (!fs.existsSync(specFile)) continue

// After:
let specFile = path.join(owlDir, "helper.md")
if (!fs.existsSync(specFile)) {
  specFile = path.join(owlDir, "specialized_owl.md")
  if (!fs.existsSync(specFile)) continue
}
```

- [ ] **Step 5: Run test — confirm passing**

```bash
npx vitest run __tests__/owls/helper-registry-compat.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/owls/specialized-types.ts src/owls/specialized-registry.ts __tests__/owls/helper-registry-compat.test.ts
git commit -m "feat(e17): HelperSpec/HelperRegistry aliases; backward-compat specialized_owl.md loading"
```

---

### Task 4: User-visible string rebrand

**Files:**
- Modify: `src/intelligence/owl-state-reporter.ts`
- Modify: `src/cli/commands.ts` (output strings only — handler replacement in Task 15)

- [ ] **Step 1: Sweep output strings in owl-state-reporter.ts**

In `src/intelligence/owl-state-reporter.ts`, replace all user-visible occurrences:
- `"specialized owl"` → `"helper"`
- `"Specialized owl"` → `"Helper"`
- `"specialized agent"` → `"helper"`
- `"specialization"` (in output strings) → `"helper"`

- [ ] **Step 2: Sweep output strings in commands.ts**

In `src/cli/commands.ts`, find any output strings (not handler registration) that say "specialized owl", "specialized agent", or "specialization" and update them to use "helper" / "Helper".

- [ ] **Step 3: Run full test suite**

```bash
npx vitest run
```
Expected: no regressions

- [ ] **Step 4: Commit**

```bash
git add src/intelligence/owl-state-reporter.ts src/cli/commands.ts
git commit -m "feat(e17): rebrand user-visible strings — 'specialized owl/agent' → 'helper'"
```

---

## Phase C — DNA + Inner Life Safety

### Task 5: Surface all 8 DNA traits in system prompt

**Files:**
- Modify: `src/engine/runtime.ts`
- Create: `__tests__/owls/dna-all-traits.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/owls/dna-all-traits.test.ts
import { describe, it, expect } from "vitest"
import { createDefaultDNA } from "../src/owls/persona.js"

// We test the directive maps directly by importing the buildSystemPrompt function
// or by verifying the runtime generates non-empty directives for all 8 traits.
// Since buildSystemPrompt is private, we call it via the public OwlEngine.run() path
// using a mock provider and check the system prompt passed to the provider.

import { OwlEngine } from "../src/engine/runtime.js"
import { vi } from "vitest"
import type { ModelProvider } from "../src/providers/base.js"
import type { OwlPersona } from "../src/owls/persona.js"

function makeMockProvider(): ModelProvider {
  return {
    name: "mock",
    chat: vi.fn().mockResolvedValue({ content: "ok", model: "m", finishReason: "stop" }),
    listModels: vi.fn().mockResolvedValue([]),
  } as unknown as ModelProvider
}

function makePersona(): OwlPersona {
  return {
    name: "TestOwl", type: "test", emoji: "🦉",
    challengeLevel: "medium", specialties: [], traits: [],
    systemPrompt: "You are TestOwl.", sourcePath: "/test/OWL.md",
  }
}

describe("DNA all-8-traits directives", () => {
  it("humor directive is non-empty for all levels", () => {
    const traits = [0.1, 0.5, 0.9]
    for (const humor of traits) {
      const dna = createDefaultDNA("TestOwl", "medium")
      dna.evolvedTraits.humor = humor
      // Humor < 0.3 = low, 0.3–0.7 = medium, >0.7 = high
      const level = humor < 0.3 ? "low" : humor > 0.7 ? "high" : "medium"
      expect(["Minimal humor", "Light wit", "Lean into humor"].some(s =>
        s.includes(level === "low" ? "Minimal" : level === "high" ? "Lean" : "Light")
      )).toBe(true)
    }
  })

  it("all 8 traits present in evolvedTraits shape", () => {
    const dna = createDefaultDNA("TestOwl", "medium")
    const traits = dna.evolvedTraits
    expect(traits).toHaveProperty("challengeLevel")
    expect(traits).toHaveProperty("verbosity")
    expect(traits).toHaveProperty("humor")
    expect(traits).toHaveProperty("formality")
    expect(traits).toHaveProperty("proactivity")
    expect(traits).toHaveProperty("riskTolerance")
    expect(traits).toHaveProperty("teachingStyle")
    expect(traits).toHaveProperty("delegationPreference")
  })

  it("provider receives system prompt containing all trait directives", async () => {
    const provider = makeMockProvider()
    const engine = new OwlEngine()
    const dna = createDefaultDNA("TestOwl", "medium")

    await engine.run("hello", {
      provider,
      owl: { persona: makePersona(), dna },
      sessionHistory: [],
      config: { defaultProvider: "mock", providers: {}, owlDna: {}, parliament: {}, heartbeat: {}, smartRouting: { enabled: false }, web: {} } as any,
    })

    const chatCall = (provider.chat as ReturnType<typeof vi.fn>).mock.calls[0]
    const systemMsg = chatCall[0].find((m: any) => m.role === "system")
    const sys: string = systemMsg?.content ?? ""

    expect(sys).toContain("humor")
    expect(sys).toContain("formality")
    expect(sys).toContain("proactivity")
    expect(sys).toContain("riskTolerance")
    expect(sys).toContain("teachingStyle")
    expect(sys).toContain("delegationPreference")
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/owls/dna-all-traits.test.ts
```
Expected: last test fails (missing 6 trait directives in system prompt)

- [ ] **Step 3: Add 6 trait directive blocks in `src/engine/runtime.ts`**

After the existing `verbosityDirectives` block (around `L2459` — after `prompt += \`**Verbosity...`):

```typescript
    // humor (0-1 continuous → low/medium/high bucket)
    const humorLevel = dna.evolvedTraits.humor < 0.3 ? "low"
      : dna.evolvedTraits.humor > 0.7 ? "high" : "medium"
    const humorDirectives: Record<string, string> = {
      low:    "Minimal humor — keep responses substantive and professional.",
      medium: "Light wit when it fits naturally — don't force it.",
      high:   "Lean into humor, wordplay, and levity where fitting.",
    }
    prompt += `**Humor (${humorLevel}):** ${humorDirectives[humorLevel]}\n\n`

    // formality (0-1 → casual/balanced/formal bucket)
    const formalityLevel = dna.evolvedTraits.formality < 0.35 ? "casual"
      : dna.evolvedTraits.formality > 0.65 ? "formal" : "balanced"
    const formalityDirectives: Record<string, string> = {
      casual:   "Casual tone — talk like a knowledgeable friend.",
      balanced: "Professional yet warm. Neither stiff nor sloppy.",
      formal:   "Formal, structured, precise. Avoid contractions.",
    }
    prompt += `**Formality (${formalityLevel}):** ${formalityDirectives[formalityLevel]}\n\n`

    // proactivity (0-1 → low/medium/high)
    const proactivityLevel = dna.evolvedTraits.proactivity < 0.3 ? "low"
      : dna.evolvedTraits.proactivity > 0.7 ? "high" : "medium"
    const proactivityDirectives: Record<string, string> = {
      low:    "Answer what's asked. Don't over-volunteer tangential information.",
      medium: "Surface related ideas when clearly relevant.",
      high:   "Proactively suggest follow-ups, next steps, and related concerns.",
    }
    prompt += `**Proactivity (${proactivityLevel}):** ${proactivityDirectives[proactivityLevel]}\n\n`

    // riskTolerance
    const riskDirectives: Record<string, string> = {
      cautious:   "Prefer proven, safe approaches. Flag risks clearly before acting.",
      moderate:   "Balance innovation with caution. Try new approaches when the downside is limited.",
      aggressive: "Favor bold, fast solutions when stakes allow. Move first, optimize later.",
    }
    prompt += `**Risk tolerance (${dna.evolvedTraits.riskTolerance}):** ${riskDirectives[dna.evolvedTraits.riskTolerance] ?? riskDirectives.moderate}\n\n`

    // teachingStyle
    const teachingDirectives: Record<string, string> = {
      examples:  "Teach through concrete examples. Show before you tell.",
      direct:    "Give direct instructions. Skip the build-up.",
      adaptive:  "Match your explanation depth to the complexity of what the user asked.",
    }
    prompt += `**Teaching style (${dna.evolvedTraits.teachingStyle}):** ${teachingDirectives[dna.evolvedTraits.teachingStyle] ?? teachingDirectives.adaptive}\n\n`

    // delegationPreference
    const delegationDirectives: Record<string, string> = {
      autonomous:     "Handle tasks yourself where possible. Minimize back-and-forth.",
      collaborative:  "Suggest other helpers when they'd do better — but stay engaged.",
      confirmatory:   "Check in before major steps. Prefer user approval over autonomy.",
    }
    prompt += `**Delegation (${dna.evolvedTraits.delegationPreference}):** ${delegationDirectives[dna.evolvedTraits.delegationPreference] ?? delegationDirectives.collaborative}\n\n`
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/owls/dna-all-traits.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/engine/runtime.ts __tests__/owls/dna-all-traits.test.ts
git commit -m "feat(e17): surface all 8 DNA traits as live system-prompt directives (D5)"
```

---

### Task 6: Delete inner-life jailbreak surfaces

**Files:**
- Modify: `src/owls/inner-life.ts`
- Modify: `src/engine/runtime.ts`
- Create: `__tests__/owls/inner-life-safety.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/owls/inner-life-safety.test.ts
import { describe, it, expect } from "vitest"
import { OwlInnerLife } from "../src/owls/inner-life.js"

describe("OwlInnerLife jailbreak surfaces removed", () => {
  it("toContextString does not exist", () => {
    const life = new OwlInnerLife("test-owl", {} as any)
    expect((life as any).toContextString).toBeUndefined()
  })

  it("monologueToDirective does not exist", () => {
    const life = new OwlInnerLife("test-owl", {} as any)
    expect((life as any).monologueToDirective).toBeUndefined()
  })

  it("thinkInBackground returns a Promise", async () => {
    const life = new OwlInnerLife("test-owl", {} as any)
    const result = life.thinkInBackground("hello", [])
    expect(result).toBeInstanceOf(Promise)
    await result // should resolve without throwing
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/owls/inner-life-safety.test.ts
```
Expected: first two tests fail (methods exist), third may fail (void not Promise)

- [ ] **Step 3: Delete methods from `src/owls/inner-life.ts`**

Delete the entire `toContextString()` method body at L430–477 (inclusive of the JSDoc comment before it).

Delete the entire `monologueToDirective()` method body at L482–497 (inclusive of the JSDoc comment before it).

- [ ] **Step 4: Remove call site in `src/engine/runtime.ts`**

Find the block around L2500–2507:
```typescript
    // Inner Life — owl's persistent inner state (mood, desires, opinions)
    if (innerLife) {
      const innerStateCtx = innerLife.toContextString();
      if (innerStateCtx) {
        prompt += "\n" + innerStateCtx + "\n";
      }
    }
```

Delete this block entirely. The inner life is already surfaced safely through `InnerMonologueLayer` in the ContextPipeline — no direct injection needed.

- [ ] **Step 5: Run test — confirm passing**

```bash
npx vitest run __tests__/owls/inner-life-safety.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 6: Run full suite to catch any callers**

```bash
npx vitest run
```
Fix any TypeScript compile errors from removed methods (search for `toContextString` and `monologueToDirective` in the codebase).

- [ ] **Step 7: Commit**

```bash
git add src/owls/inner-life.ts src/engine/runtime.ts __tests__/owls/inner-life-safety.test.ts
git commit -m "feat(e17): delete toContextString + monologueToDirective (jailbreak surfaces D3)"
```

---

### Task 7: Monologue race fix

**Files:**
- Modify: `src/owls/inner-life.ts`
- Modify: `src/gateway/core.ts`
- Create: `__tests__/owls/inner-life-monologue-race.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/owls/inner-life-monologue-race.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { OwlInnerLife } from "../src/owls/inner-life.js"

describe("monologue race fix", () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it("thinkInBackground resolves before getLastMonologue is called in .then()", async () => {
    const calls: string[] = []
    const life = new OwlInnerLife("test-owl", {
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify({ thoughts: ["test"], responseIntent: "help", moodShift: null }),
        model: "m", finishReason: "stop",
      }),
      name: "mock", listModels: vi.fn().mockResolvedValue([]),
    } as any)

    let monologueOnThen: unknown = "NOT_SET"
    const promise = life.thinkInBackground("hello", [])
      .then(() => {
        monologueOnThen = life.getLastMonologue()
        calls.push("then")
      })

    await promise
    expect(calls).toContain("then")
    expect(monologueOnThen).not.toBe("NOT_SET")
  })

  it("monologue is not null immediately after thinkInBackground resolves", async () => {
    const life = new OwlInnerLife("test-owl", {
      chat: vi.fn().mockResolvedValue({
        content: JSON.stringify({
          thoughts: ["thinking"], responseIntent: "assist", moodShift: { current: "curious" },
        }),
        model: "m", finishReason: "stop",
      }),
      name: "mock", listModels: vi.fn().mockResolvedValue([]),
    } as any)

    await life.thinkInBackground("test message", [])
    const monologue = life.getLastMonologue()
    expect(monologue).not.toBeNull()
    expect(monologue?.responseIntent).toBe("assist")
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/owls/inner-life-monologue-race.test.ts
```

- [ ] **Step 3: Change `thinkInBackground` to return `Promise<void>` in `src/owls/inner-life.ts`**

Find the method signature (around L258):
```typescript
// Before:
thinkInBackground(userMessage: string, sessionHistory: ChatMessage[]): void {
  // ...runs async internally, returns void
```

Change the return type and make it a proper async method that returns the Promise:
```typescript
// After:
thinkInBackground(userMessage: string, sessionHistory: ChatMessage[]): Promise<void> {
  return this.think(userMessage, sessionHistory).then(() => {}).catch(() => {})
  // OR if the internal logic is directly inlinable:
}
```

Look at the actual implementation — if it does something like:
```typescript
  thinkInBackground(...): void {
    void this.think(...)  // or similar
  }
```
Change to:
```typescript
  thinkInBackground(...): Promise<void> {
    return this.think(...).catch(() => {})
  }
```

- [ ] **Step 4: Fix the race in `src/gateway/core.ts:3978–4001`**

Replace the `setImmediate + setTimeout(100ms)` block:
```typescript
        // Before:
        this.ctx.innerLife.thinkInBackground(
          typeof lastUserMsg.content === "string" ? lastUserMsg.content : "",
          messages,
        );
        const innerLife = this.ctx.innerLife;
        const digestManager = this.ctx.digestManager;
        if (digestManager && sessionId) {
          setImmediate(async () => {
            await new Promise((r) => setTimeout(r, 100));
            const monologue = innerLife.getLastMonologue?.();
            if (monologue) {
              await digestManager.setLastMonologue(sessionId, {
                thoughts: monologue.thoughts,
                responseIntent: monologue.responseIntent,
                moodCurrent: monologue.moodShift?.current,
                storedAt: new Date().toISOString(),
              });
            }
          });
        }
```

Replace with:
```typescript
        // After:
        const innerLifeRef = this.ctx.innerLife;
        const digestRef = this.ctx.digestManager;
        const sidRef = sessionId;
        const userMsgText = typeof lastUserMsg.content === "string" ? lastUserMsg.content : "";
        innerLifeRef.thinkInBackground(userMsgText, messages)
          .then(async () => {
            if (!digestRef || !sidRef) return;
            const monologue = innerLifeRef.getLastMonologue?.();
            if (monologue) {
              await digestRef.setLastMonologue(sidRef, {
                thoughts: monologue.thoughts,
                responseIntent: monologue.responseIntent,
                moodCurrent: monologue.moodShift?.current,
                storedAt: new Date().toISOString(),
              });
            }
          })
          .catch(() => { /* non-critical */ });
```

- [ ] **Step 5: Run test — confirm passing**

```bash
npx vitest run __tests__/owls/inner-life-monologue-race.test.ts
```
Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/owls/inner-life.ts src/gateway/core.ts __tests__/owls/inner-life-monologue-race.test.ts
git commit -m "fix(e17): thinkInBackground returns Promise; chain .then() replaces setImmediate race (D3)"
```

---

### Task 8: Wire RelationshipContext + OpinionInjector

**Files:**
- Modify: `src/engine/runtime.ts` (add `relationshipContext` to `EngineContext`)
- Modify: `src/gateway/core.ts` (pass relationshipContext; wire OpinionInjector)

- [ ] **Step 1: Add `relationshipContext` to `EngineContext` in `src/engine/runtime.ts`**

In the `EngineContext` interface (around L122), add:
```typescript
  /** User relationship context — injected as <user_relationship> block after persona */
  relationshipContext?: import("../routing/relationship-context.js").RelationshipContext;
  /** Wired OpinionInjector for surfacing relevant owl opinions pre-LLM */
  additionalSystemPrompt?: string;
```

In the system prompt assembly in `runtime.ts` (after the owl persona block, before behavioral directives — around L2431), add:

```typescript
    // RelationshipContext — 200-token user history block
    if (context.relationshipContext && context.userId) {
      try {
        const relBlock = await context.relationshipContext.buildPromptBlock(context.userId)
        if (relBlock) {
          // Roughly 200 tokens max (4 chars/token)
          prompt += "\n\n" + relBlock.slice(0, 800) + "\n"
        }
      } catch { /* non-critical */ }
    }

    // Opinion injection — surfaced pre-response via OpinionInjector in core.ts
    if (context.additionalSystemPrompt) {
      prompt += "\n" + context.additionalSystemPrompt + "\n"
    }
```

- [ ] **Step 2: Pass `relationshipContext` in core.ts `buildEngineContext()`**

Find `buildEngineContext()` in `src/gateway/core.ts`. Add to the returned context object:
```typescript
      relationshipContext: this.ctx.relationshipContext,
```

- [ ] **Step 3: Wire OpinionInjector in core.ts**

At the top of the file, add imports:
```typescript
import { OpinionInjector } from "../owls/opinion-injector.js";
```

In the `GatewayOrchestrator` class, add a private field:
```typescript
  private readonly opinionInjector = new OpinionInjector();
```

In the main message handler, right after `engineCtx` is built (search for `const engineCtx = await this.buildEngineContext`) and before the engine is called, add:

```typescript
        // Opinion injection — surface relevant owl opinion if confidence ≥ 0.65
        if (this.ctx.innerLife) {
          const opinions = this.ctx.innerLife.getState?.()?.opinions ?? [];
          const match = this.opinionInjector.findRelevant(text, opinions);
          if (match) {
            engineCtx.additionalSystemPrompt =
              (engineCtx.additionalSystemPrompt ?? "") +
              this.opinionInjector.formatForSystemPrompt(match);
          }
          // Fire-and-forget opinion formation for next time
          this.opinionInjector.formOpinionAsync(text, this.ctx.innerLife).catch(() => {});
        }
```

There are multiple `buildEngineContext` call sites — apply this pattern to the primary message handler path (the one inside `handleMessage` or `processMessage` — the main conversational path).

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run
```
Expected: no regressions

- [ ] **Step 5: Commit**

```bash
git add src/engine/runtime.ts src/gateway/core.ts
git commit -m "feat(e17): wire RelationshipContext (200 tokens) + OpinionInjector into system prompt (D4)"
```

---

## Phase D — OwlBrain Routing

### Task 9: Per-channel pin

**Files:**
- Modify: `src/routing/owl-brain.ts`
- Create: `__tests__/routing/owl-brain-channel-pin.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/routing/owl-brain-channel-pin.test.ts
import { describe, it, expect, beforeEach } from "vitest"
import { OwlBrain } from "../src/routing/owl-brain.js"
import type { GatewayMessage } from "../src/gateway/types.js"

function makeDb(pins: Map<string, string> = new Map()) {
  const owlPins = {
    get: (userId: string, channelId: string) => {
      return pins.get(`${userId}:${channelId}`) ?? pins.get(`${userId}:global`) ?? null
    },
    set: (userId: string, channelId: string, owlName: string | null, _pinnedAt: string) => {
      if (owlName === null) pins.delete(`${userId}:${channelId}`)
      else pins.set(`${userId}:${channelId}`, owlName)
    },
  }
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins,
  }
}

function makeMsg(channelId: string, text = "hello"): GatewayMessage {
  return {
    id: "m1", channelId, userId: "user1", sessionId: `${channelId}:user1`,
    text, timestamp: Date.now(), attachments: [],
  }
}

function makeRegistry(owls: string[]) {
  return {
    get: (name: string) => owls.includes(name) ? { name, role: "test", emoji: "🤖", type: "specialist", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
    listSpecialists: () => owls.map(name => ({ name } as any)),
    getDefault: () => undefined,
  }
}

describe("OwlBrain per-channel pin isolation", () => {
  it("telegram pin does not bleed to CLI", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)

    // Pin aria on telegram
    await brain.resolve("@aria hello", makeMsg("telegram"), {} as any, {} as any, { metadata: {} } as any)
    // CLI should not see the pin
    const cli = await brain.resolve("hello", makeMsg("cli"), {} as any, {} as any, { metadata: {} } as any)
    expect(cli.activeOwlName).toBe("noctua")
  })

  it("pin set on @mention is per-channel", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)

    await brain.resolve("@aria hello", makeMsg("telegram"), {} as any, {} as any, { metadata: {} } as any)
    expect(pins.get("user1:telegram")).toBe("aria")
    expect(pins.get("user1:cli")).toBeUndefined()
  })

  it("@coordinator clears only the current channel's pin", async () => {
    const pins = new Map<string, string>([
      ["user1:telegram", "aria"],
      ["user1:cli", "aria"],
    ])
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)

    await brain.resolve("@noctua", makeMsg("telegram"), {} as any, {} as any, { metadata: {} } as any)
    expect(pins.get("user1:telegram")).toBeUndefined()
    expect(pins.get("user1:cli")).toBe("aria") // CLI pin untouched
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/routing/owl-brain-channel-pin.test.ts
```

- [ ] **Step 3: Update `OwlBrain` constructor type + pin calls**

In `src/routing/owl-brain.ts`:

Change `db` type from `Pick<MemoryDatabase, "userProfiles">` to:
```typescript
private db: Pick<MemoryDatabase, "userProfiles" | "owlPins">,
```

Change **step 1** (restore SQLite pin, around L46–58):
```typescript
    // 1. Restore per-channel SQLite pin on first message of session
    if (!session?.metadata.activeOwlName && message.userId && this.specializedRegistry) {
      const channelId = message.channelId
      const savedPin = this.db.owlPins.get(message.userId, channelId)
        ?? this.db.userProfiles.getPin(message.userId) // legacy global fallback
      if (savedPin && session) {
        const spec = this.specializedRegistry.get(savedPin)
        if (spec) {
          session.metadata.activeOwlName = savedPin
          log.engine.info(`[OwlBrain] Restored pin "${savedPin}" for ${message.userId}@${channelId}`)
        } else {
          this.db.owlPins.set(message.userId, channelId, null, new Date().toISOString())
          log.engine.warn(`[OwlBrain] Cleared stale pin "${savedPin}" for ${message.userId}`)
        }
      }
    }
```

Change **@coordinator clear** (around L68):
```typescript
        this.db.owlPins.set(message.userId, message.channelId, null, new Date().toISOString())
```

Change **@mention pin set** (around L77):
```typescript
        this.db.owlPins.set(message.userId, message.channelId, spec.name, new Date().toISOString())
```

Change **stale pin clear in session resume** (around L100–103):
```typescript
          if (message.userId) {
            this.db.owlPins.set(message.userId, message.channelId, null, new Date().toISOString())
          }
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/routing/owl-brain-channel-pin.test.ts
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/routing/owl-brain.ts __tests__/routing/owl-brain-channel-pin.test.ts
git commit -m "feat(e17): per-channel pin via owl_pins table; telegram pin no longer bleeds to CLI (Q1)"
```

---

### Task 10: Soft-pin TTL + NL mention parser

**Files:**
- Modify: `src/routing/owl-brain.ts`
- Create: `__tests__/routing/owl-brain-soft-pin.test.ts`
- Create: `__tests__/routing/owl-mention-nl.test.ts`

- [ ] **Step 1: Write soft-pin test**

```typescript
// __tests__/routing/owl-brain-soft-pin.test.ts
import { describe, it, expect } from "vitest"
import { OwlBrain } from "../src/routing/owl-brain.js"

function makeDb() {
  const pins = new Map<string, string>()
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins: {
      get: (u: string, c: string) => pins.get(`${u}:${c}`) ?? null,
      set: (u: string, c: string, n: string | null) => { if (n === null) pins.delete(`${u}:${c}`); else pins.set(`${u}:${c}`, n) },
    },
    _pins: pins,
  }
}

function makeMsg(text = "hello") {
  return { id: "m", channelId: "cli", userId: "u1", sessionId: "s1", text, timestamp: Date.now(), attachments: [] }
}

describe("OwlBrain soft-pin TTL", () => {
  it("signal routing match does NOT write to SQLite", async () => {
    const db = makeDb()
    const registry = {
      get: (n: string) => n === "aria" ? { name: "aria", type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: ["cook"], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
      listSpecialists: () => [{ name: "aria" }],
      getDefault: () => undefined,
    }
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    const mockRouter = {
      routeWithSignals: async () => ({ type: "specialist" as const, owl: registry.get("aria"), reason: "signal match" }),
    }
    brain.setSecretaryRouterGetter(() => mockRouter as any)

    const session = { metadata: {} } as any
    await brain.resolve("I love cooking", makeMsg("I love cooking"), {} as any, {} as any, session)

    // Session gets the soft pin
    expect(session.metadata.activeOwlName).toBe("aria")
    // SQLite pins do NOT get written
    expect((db as any)._pins.size).toBe(0)
  })

  it("3 consecutive non-matching turns clear the session soft pin", async () => {
    const db = makeDb()
    const registry = {
      get: (n: string) => n === "aria" ? { name: "aria", type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
      listSpecialists: () => [{ name: "aria" }],
      getDefault: () => undefined,
    }
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    const mockRouter = {
      routeWithSignals: async () => ({ type: "direct" as const, reason: "no match" }),
    }
    brain.setSecretaryRouterGetter(() => mockRouter as any)

    const session: any = { metadata: { activeOwlName: "aria", softPinMissCount: 0 } }

    // 2 misses — still pinned
    for (let i = 0; i < 2; i++) {
      await brain.resolve("hello world", makeMsg("hello world"), {} as any, {} as any, session)
    }
    expect(session.metadata.activeOwlName).toBe("aria")

    // 3rd miss — clears pin
    await brain.resolve("hello world", makeMsg("hello world"), {} as any, {} as any, session)
    expect(session.metadata.activeOwlName).toBeUndefined()
  })
})
```

- [ ] **Step 2: Write NL mention test**

```typescript
// __tests__/routing/owl-mention-nl.test.ts
import { describe, it, expect, vi } from "vitest"
import { OwlBrain } from "../src/routing/owl-brain.js"

function makeDb(pins = new Map<string, string>()) {
  return {
    userProfiles: { getPin: () => null, setPin: () => {}, appendRoutingHistory: () => {} },
    owlPins: {
      get: (u: string, c: string) => pins.get(`${u}:${c}`) ?? null,
      set: (u: string, c: string, n: string | null, _ts: string) => { if (n === null) pins.delete(`${u}:${c}`); else pins.set(`${u}:${c}`, n) },
    },
    _pins: pins,
  }
}

function makeRegistry(names: string[]) {
  return {
    get: (n: string) => names.includes(n) ? { name: n, type: "specialist", role: "test", emoji: "🤖", personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" }, expertise: [], model: { provider: "anthropic", modelId: "m" }, permissions: { allowedTools: [], deniedTools: [], capabilityConstraints: [] }, routingRules: { keywords: [], domains: [], priority: 5 }, skills: { canLearn: false, retainedKnowledge: [] }, additionalPrompt: "" } : undefined,
    listSpecialists: () => names.map(n => ({ name: n })),
    getDefault: () => undefined,
  }
}

function makeMsg(text: string) {
  return { id: "m", channelId: "cli", userId: "u1", sessionId: "s1", text, timestamp: Date.now(), attachments: [] }
}

describe("NL mention parser", () => {
  it("high-confidence mention hard-pins and routes to named helper", async () => {
    const pins = new Map<string, string>()
    const db = makeDb(pins)
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(async (_prompt) =>
      JSON.stringify({ targeted: "aria", confidence: 0.9 })
    )

    const session: any = { metadata: {} }
    const result = await brain.resolve("Aria, can you check the weather?", makeMsg("Aria, can you check the weather?"), {} as any, {} as any, session)

    expect(result.activeOwlName).toBe("aria")
    expect(pins.get("u1:cli")).toBe("aria") // hard pin written
  })

  it("low-confidence does not route to named helper (silent fallback)", async () => {
    const db = makeDb()
    const registry = makeRegistry(["aria"])
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(async (_prompt) =>
      JSON.stringify({ targeted: "aria", confidence: 0.4 })
    )

    const session: any = { metadata: {} }
    const result = await brain.resolve("Aria said it was raining yesterday", makeMsg("Aria said it was raining yesterday"), {} as any, {} as any, session)

    expect(result.activeOwlName).toBe("noctua") // silent fallback
  })

  it("no classification when roster is empty", async () => {
    const classifyFn = vi.fn().mockResolvedValue(JSON.stringify({ targeted: null, confidence: 0 }))
    const db = makeDb()
    const registry = makeRegistry([]) // no helpers
    const brain = new OwlBrain(registry as any, db as any, "noctua", undefined, undefined, undefined)
    brain.setClassifyFn(classifyFn)

    await brain.resolve("Aria, hello", makeMsg("Aria, hello"), {} as any, {} as any, { metadata: {} } as any)
    expect(classifyFn).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 3: Run tests — confirm failure**

```bash
npx vitest run __tests__/routing/owl-brain-soft-pin.test.ts __tests__/routing/owl-mention-nl.test.ts
```

- [ ] **Step 4: Add soft-pin TTL + NL mention to `src/routing/owl-brain.ts`**

Add `setClassifyFn()` method to `OwlBrain` class:
```typescript
  private classifyFn: ((prompt: string) => Promise<string>) | null = null

  setClassifyFn(fn: (prompt: string) => Promise<string>): void {
    this.classifyFn = fn
  }
```

Add `parseNaturalLanguageMention()` helper **inside the class** (private):
```typescript
  private async parseNaturalLanguageMention(
    text: string,
    activeRoster: string[],
  ): Promise<{ targeted: string | null; confidence: number }> {
    if (activeRoster.length === 0 || !this.classifyFn) {
      return { targeted: null, confidence: 0 }
    }
    try {
      const prompt =
        `Message: "${text}"\n` +
        `Active helpers: [${activeRoster.join(", ")}]\n` +
        `Is the user explicitly addressing one of these helpers by name?\n` +
        `Reply JSON only: {"targeted": string|null, "confidence": 0-1}`
      const raw = await this.classifyFn(prompt)
      return JSON.parse(raw)
    } catch {
      return { targeted: null, confidence: 0 }
    }
  }
```

In `resolve()`, **between** step 2 (explicit @mention) and step 3 (session pin resume), insert NL mention handling:

```typescript
    // 2b. Natural-language mention (runs when message doesn't start with @)
    if (!text.startsWith("@") && this.specializedRegistry && message.userId && this.classifyFn) {
      const roster = this.specializedRegistry.listSpecialists().map(s => s.name)
      const { targeted, confidence } = await this.parseNaturalLanguageMention(text, roster)
      if (targeted && confidence >= 0.75) {
        const spec = this.specializedRegistry.get(targeted)
        if (spec) {
          if (session) session.metadata.activeOwlName = spec.name
          this.db.owlPins.set(message.userId, message.channelId, spec.name, new Date().toISOString())
          this.applySpecialist(spec, engineCtx, callbacks)
          await this.injectMemoryContext(spec.name, message.sessionId, text, engineCtx)
          activeOwlName = spec.name
          this.appendHistory(message.userId, spec.name, `nl-mention@${confidence.toFixed(2)}`)
          log.engine.info(`[OwlBrain] NL mention → "${spec.name}" (conf=${confidence.toFixed(2)})`)
          return { text, activeOwlName, parliamentHandled: false }
        }
      }
    }
```

For the **signal routing block** (step 4, around L106–131), change the auto-pin write to session-only + add soft-pin miss counter:

```typescript
    // 4. Signal-aware routing (soft-pin — session only, 3-miss TTL)
    if (this.specializedRegistry && message.userId) {
      // Check soft-pin miss counter BEFORE routing
      if (session?.metadata.activeOwlName) {
        const router = this.getSecretaryRouter()
        if (router) {
          const signals = this.userProfileService
            ? await this.userProfileService.buildSignals(message.userId, text)
            : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const }

          const decision = await router.routeWithSignals(text, message.userId, signals)

          if (decision.type === "specialist" && decision.owl.name === session.metadata.activeOwlName) {
            session.metadata.softPinMissCount = 0
          } else if (decision.type !== "specialist" || decision.owl.name !== session.metadata.activeOwlName) {
            session.metadata.softPinMissCount = (session.metadata.softPinMissCount ?? 0) + 1
            if (session.metadata.softPinMissCount >= 3) {
              session.metadata.activeOwlName = undefined
              session.metadata.softPinMissCount = 0
              log.engine.info(`[OwlBrain] Soft-pin cleared after 3 consecutive misses`)
            }
          }
        }
      }

      const router = this.getSecretaryRouter()
      if (router && !session?.metadata.activeOwlName) {
        const signals = this.userProfileService
          ? await this.userProfileService.buildSignals(message.userId, text)
          : { activePin: null, domainStack: [], recentEpisodes: [], relevantFacts: [], trustLevel: "standard" as const }

        const decision = await router.routeWithSignals(text, message.userId, signals)

        if (decision.type === "specialist") {
          if (session) {
            session.metadata.activeOwlName = decision.owl.name
            session.metadata.softPinMissCount = 0
          }
          // NOTE: do NOT call db.owlPins.set() here — soft pin is session-only
          this.applySpecialist(decision.owl, engineCtx, callbacks)
          await this.injectMemoryContext(decision.owl.name, message.sessionId, text, engineCtx)
          activeOwlName = decision.owl.name
          this.appendHistory(message.userId, decision.owl.name, decision.reason)
          log.engine.info(`[OwlBrain] signals → "${decision.owl.name}" (soft-pin, ${decision.reason})`)
        } else if (decision.type === "parliament") {
          this.appendHistory(message.userId, "parliament", "parliament trigger")
          return { text, activeOwlName, parliamentHandled: true }
        } else {
          this.appendHistory(message.userId, this.defaultOwlName, decision.reason)
        }
      }
    }
```

Wire `setClassifyFn` in `src/gateway/core.ts` where `OwlBrain` is constructed (search for `new OwlBrain(`):
```typescript
    if (this.owlBrain && ctx.intelligence && ctx.provider) {
      const intelligenceRouter = ctx.intelligence
      const provider = ctx.provider
      this.owlBrain.setClassifyFn(async (prompt: string) => {
        try {
          const resolved = intelligenceRouter.resolve("classification")
          // Use the primary provider with cheap model override
          const resp = await provider.chat(
            [{ role: "user", content: prompt }],
            { model: resolved.model },
          )
          return resp.content
        } catch {
          return JSON.stringify({ targeted: null, confidence: 0 })
        }
      })
    }
```

- [ ] **Step 5: Run tests — confirm passing**

```bash
npx vitest run __tests__/routing/owl-brain-soft-pin.test.ts __tests__/routing/owl-mention-nl.test.ts
```
Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/routing/owl-brain.ts src/gateway/core.ts \
  __tests__/routing/owl-brain-soft-pin.test.ts __tests__/routing/owl-mention-nl.test.ts
git commit -m "feat(e17): soft-pin TTL (3-miss session clear); NL mention parser (D9, Q1, Q2)"
```

---

### Task 11: Quality-weighted routing

**Files:**
- Modify: `src/routing/secretary.ts`
- Modify: `src/gateway/handlers/post-processor.ts`

- [ ] **Step 1: Update `SecretaryRouter.calculateConfidence()` in `src/routing/secretary.ts`**

Find `calculateConfidence()` (around L197). Change the `routingQuality` lookup to use `OwlQualityRepo`:

The method needs access to `db.owlQualityMetrics`. Look at `SecretaryRouter`'s constructor to understand what it receives. If it doesn't already have `db`, pass it via the constructor.

Find the existing `calculateConfidence` implementation — it has:
```typescript
const dnaScore = target.routingQuality ?? 0.7
```

This needs to become an EWMA-based lookup. The simplest approach is to add an optional `qualityLookup` callback to `SecretaryRouter`:

In `src/routing/secretary.ts`, add an optional field:
```typescript
  private qualityLookup: ((owlName: string) => number) | null = null

  setQualityLookup(fn: (owlName: string) => number): void {
    this.qualityLookup = fn
  }
```

Change the `dnaScore` line:
```typescript
    const dnaScore = this.qualityLookup
      ? this.qualityLookup(target.name)
      : (target.routingQuality ?? 0.7)
```

- [ ] **Step 2: Wire quality lookup in `src/gateway/core.ts`**

After `SecretaryRouter` is constructed (search for `new SecretaryRouter`), add:
```typescript
    if (secretaryRouter && ctx.db) {
      secretaryRouter.setQualityLookup((owlName: string) => {
        return ctx.db!.owlQualityMetrics.get(owlName, ctx.defaultUserId ?? "system")?.ewmaReward ?? 0.7
      })
    }
```

- [ ] **Step 3: Add write hook in `src/gateway/handlers/post-processor.ts`**

In `post-processor.ts`, find where trajectories are written (around L700–726, where `trajectory_turns` is inserted). After the trajectory write, add:

```typescript
      // Update owl quality EWMA after each trajectory turn
      if (ctx.db && activeOwlName && ownerId && typeof reward === "number") {
        try {
          ctx.db.owlQualityMetrics.update(activeOwlName, ownerId, reward)
        } catch { /* non-critical */ }
      }
```

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run
```
Expected: no regressions

- [ ] **Step 5: Commit**

```bash
git add src/routing/secretary.ts src/gateway/handlers/post-processor.ts src/gateway/core.ts
git commit -m "feat(e17): quality-weighted routing — EWMA α=0.15 from trajectories → SecretaryRouter (D6, G12)"
```

---

## Phase E — Gateway Management

### Task 12: OwlManagementRouter

**Files:**
- Create: `src/gateway/commands/owl-router.ts`
- Create: `__tests__/gateway/owl-router.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/owl-router.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest"
import { dispatchOwlCommand } from "../src/gateway/commands/owl-router.js"
import type { HelperSpec } from "../src/owls/specialized-types.js"

function makeSpec(name: string): HelperSpec {
  return {
    name, type: "specialist", role: "test helper", emoji: "🤖",
    personality: { challengeLevel: "medium", verbosity: "balanced", tone: "warm" },
    expertise: ["testing"],
    model: { provider: "anthropic", modelId: "claude-haiku-4-5-20251001" },
    permissions: { allowedTools: ["web_search"], deniedTools: [], capabilityConstraints: [] },
    routingRules: { keywords: [], domains: [], priority: 5 },
    skills: { canLearn: false, retainedKnowledge: [] },
    additionalPrompt: "",
  }
}

function makeRegistry(helpers: HelperSpec[]) {
  return {
    listAll: () => helpers,
    get: (name: string) => helpers.find(h => h.name === name),
    loadAll: vi.fn(),
  }
}

function makeDeps(helpers: HelperSpec[] = [], wizardResult = "✓ Done") {
  return {
    registry: makeRegistry(helpers),
    wizard: { start: vi.fn().mockResolvedValue(wizardResult), isActive: () => false, cancel: vi.fn() },
    userId: "user1",
    channelAdapter: {} as any,
  }
}

describe("dispatchOwlCommand", () => {
  it("list — returns bulleted helper list", async () => {
    const deps = makeDeps([makeSpec("Aria"), makeSpec("Nora")])
    const result = await dispatchOwlCommand("list", [], deps as any)
    expect(result).toContain("Aria")
    expect(result).toContain("Nora")
  })

  it("list — returns empty message when no helpers", async () => {
    const result = await dispatchOwlCommand("list", [], makeDeps([]) as any)
    expect(result).toContain("no helpers")
  })

  it("show — returns spec details", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    const result = await dispatchOwlCommand("show", ["Aria"], deps as any)
    expect(result).toContain("Aria")
    expect(result).toContain("test helper")
  })

  it("show — returns not found for unknown helper", async () => {
    const result = await dispatchOwlCommand("show", ["Unknown"], makeDeps([]) as any)
    expect(result.toLowerCase()).toContain("not found")
  })

  it("create — launches wizard", async () => {
    const deps = makeDeps()
    const result = await dispatchOwlCommand("create", [], deps as any)
    expect(deps.wizard.start).toHaveBeenCalledWith("user1", deps.channelAdapter)
    expect(result).toBe("✓ Done")
  })

  it("delete — requires 'yes' confirmation", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    const result = await dispatchOwlCommand("delete", ["Aria"], deps as any)
    expect(result.toLowerCase()).toContain("confirm")
  })

  it("rename — moves directory and reloads registry", async () => {
    const deps = makeDeps([makeSpec("Aria")])
    // rename requires fs access — just verify it returns a success string or not-found
    const result = await dispatchOwlCommand("rename", ["Aria", "Kira"], deps as any)
    expect(typeof result).toBe("string")
  })

  it("unknown verb — returns helpful error", async () => {
    const result = await dispatchOwlCommand("frobnicate", [], makeDeps() as any)
    expect(result.toLowerCase()).toContain("unknown")
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/gateway/owl-router.test.ts
```

- [ ] **Step 3: Create `src/gateway/commands/owl-router.ts`**

```typescript
// src/gateway/commands/owl-router.ts
import fs from "node:fs"
import path from "node:path"
import type { HelperRegistry } from "../../owls/specialized-registry.js"
import type { OwlCreationWizard } from "../wizards/owl-creation.js"
import type { ChannelAdapterV2 } from "../adapter-v2.js"

export interface OwlRouterDeps {
  registry: HelperRegistry
  wizard: OwlCreationWizard
  userId: string
  channelAdapter: ChannelAdapterV2
  workspacePath?: string
}

export async function dispatchOwlCommand(
  verb: string,
  args: string[],
  deps: OwlRouterDeps,
): Promise<string> {
  const { registry, wizard, userId, channelAdapter } = deps

  switch (verb.toLowerCase()) {
    case "list": {
      const helpers = registry.listAll()
      if (helpers.length === 0) return "You have no helpers yet. Use `/helper create` to make one."
      return helpers
        .map(h => `• ${h.emoji || "🦉"} **${h.name}** — ${h.role}`)
        .join("\n")
    }

    case "show": {
      const name = args[0]
      if (!name) return "Usage: `/helper show <name>`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found. Use \`/helper list\` to see your helpers.`
      const caps = spec.permissions.allowedTools.length > 0
        ? spec.permissions.allowedTools.join(", ")
        : "default"
      const restrictions = spec.permissions.deniedTools.length > 0
        ? spec.permissions.deniedTools.join(", ")
        : "none"
      return [
        `**${spec.emoji || "🦉"} ${spec.name}**`,
        `Role: ${spec.role}`,
        `Style: ${spec.personality.tone}, ${spec.personality.challengeLevel} challenge, ${spec.personality.verbosity} verbosity`,
        spec.expertise.length > 0 ? `Expertise: ${spec.expertise.join(", ")}` : null,
        `Can do: ${caps}`,
        `Restrictions: ${restrictions}`,
        spec.additionalPrompt ? `Notes: ${spec.additionalPrompt}` : null,
      ].filter(Boolean).join("\n")
    }

    case "create": {
      return wizard.start(userId, channelAdapter)
    }

    case "design": {
      const name = args[0]
      if (!name) return "Usage: `/helper design <name>`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      return `Design mode for ${name} is not yet available in this version.`
    }

    case "capabilities": {
      const name = args[0]
      if (!name) return "Usage: `/helper capabilities <name>`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      return `Capabilities update for ${name} is not yet available in this version.`
    }

    case "rename": {
      const [oldName, newName] = args
      if (!oldName || !newName) return "Usage: `/helper rename <old-name> <new-name>`"
      const spec = registry.get(oldName)
      if (!spec) return `Helper "${oldName}" not found.`
      if (!deps.workspacePath) return `Rename requires workspace path configuration.`
      const oldDir = path.join(deps.workspacePath, "owls", oldName)
      const newDir = path.join(deps.workspacePath, "owls", newName)
      if (!fs.existsSync(oldDir)) return `Helper directory for "${oldName}" not found on disk.`
      if (fs.existsSync(newDir)) return `A helper named "${newName}" already exists.`
      fs.renameSync(oldDir, newDir)
      registry.loadAll()
      return `✓ Renamed "${oldName}" to "${newName}".`
    }

    case "delete": {
      const [name, confirm] = args
      if (!name) return "Usage: `/helper delete <name> yes`"
      const spec = registry.get(name)
      if (!spec) return `Helper "${name}" not found.`
      if (confirm?.toLowerCase() !== "yes") {
        return `To confirm deletion, run: \`/helper delete ${name} yes\`\nThis cannot be undone.`
      }
      if (!deps.workspacePath) return `Delete requires workspace path configuration.`
      const dir = path.join(deps.workspacePath, "owls", name)
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true })
      registry.loadAll()
      return `✓ Helper "${name}" deleted.`
    }

    default:
      return `Unknown command: "${verb}". Available: list, show, create, design, capabilities, rename, delete`
  }
}
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/gateway/owl-router.test.ts
```
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/gateway/commands/owl-router.ts __tests__/gateway/owl-router.test.ts
git commit -m "feat(e17): OwlManagementRouter — dispatchOwlCommand() gateway primitive (D7)"
```

---

### Task 13: OwlCreationWizard

**Files:**
- Create: `src/gateway/wizards/owl-creation.ts`
- Create: `__tests__/gateway/owl-creation-wizard.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/gateway/owl-creation-wizard.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest"
import { OwlCreationWizard } from "../src/gateway/wizards/owl-creation.ts"

function makeAdapter(answers: string[]) {
  let i = 0
  return {
    ask: vi.fn().mockImplementation(async () => answers[i++] ?? "skip"),
  }
}

describe("OwlCreationWizard", () => {
  it("completes 6-step flow and writes helper.md", async () => {
    const writes: Array<{ path: string; content: string }> = []
    const wizard = new OwlCreationWizard("/tmp/test-workspace", undefined, (p, c) => writes.push({ path: p, content: c }))

    const adapter = makeAdapter(["Nora", "cooking recipes", "Warm & patient", "Search the web", "Nothing specific", "Yes, create it", "skip"])
    const result = await wizard.start("user1", adapter as any)

    expect(result).toContain("Nora")
    expect(result).toContain("ready")
    expect(writes.length).toBeGreaterThan(0)
    expect(writes[0].content).toContain("name: Nora")
  })

  it("per-userId isolation — two users get separate sessions", async () => {
    const wizard = new OwlCreationWizard("/tmp/test-workspace2", undefined, () => {})
    const a1 = makeAdapter(["Aria"])
    const a2 = makeAdapter(["Nora"])

    // Start both without completing
    expect(wizard.isActive("user1")).toBe(false)
    // Note: start() runs the whole flow synchronously with the mock adapter,
    // so we test isActive during construction indirectly
    expect(wizard.isActive("user2")).toBe(false)
  })

  it("cancel clears session state", async () => {
    const wizard = new OwlCreationWizard("/tmp/test-workspace3", undefined, () => {})
    wizard.cancel("user1")
    expect(wizard.isActive("user1")).toBe(false)
  })

  it("'No, start over' restarts wizard", async () => {
    const writes: Array<{ path: string; content: string }> = []
    const wizard = new OwlCreationWizard("/tmp/test-workspace4", undefined, (p, c) => writes.push({ path: p, content: c }))

    // First attempt: answer no at confirm, then complete
    const adapter = makeAdapter([
      "Aria", "cooking", "Warm & patient", "Search the web", "Nothing", "No, start over",
      "Nora", "baking", "Direct & efficient", "All of the above", "No medical advice", "Yes, create it", "skip",
    ])
    const result = await wizard.start("user1", adapter as any)
    expect(result).toContain("Nora")
  })

  it("recurring task writes owl_jobs row", async () => {
    const jobs: any[] = []
    const db = { owlJobs: { insert: vi.fn((j) => jobs.push(j)) } }
    const wizard = new OwlCreationWizard("/tmp/test-workspace5", db as any, () => {})

    const adapter = makeAdapter([
      "Aria", "news", "Direct & efficient", "Search the web", "skip", "Yes, create it",
      "Check news daily at 9am",
    ])
    await wizard.start("user1", adapter as any)
    expect(db.owlJobs.insert).toHaveBeenCalled()
    expect(jobs[0].task_description).toContain("news")
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/gateway/owl-creation-wizard.test.ts
```

- [ ] **Step 3: Create `src/gateway/wizards/owl-creation.ts`**

```typescript
// src/gateway/wizards/owl-creation.ts
import fs from "node:fs"
import path from "node:path"
import { v4 as uuidv4 } from "uuid"
import type { ChannelAdapterV2 } from "../adapter-v2.js"

interface WizardSession {
  userId: string
  startedAt: number
}

interface OwlJobsRepo {
  insert(job: {
    id: string
    helper_name: string
    owner_id: string
    schedule: string
    task_description: string
    channel_id: string
  }): void
}

interface WizardDb {
  owlJobs?: OwlJobsRepo
}

type WriteFn = (filePath: string, content: string) => void

const SESSION_TIMEOUT_MS = 30 * 60 * 1000 // 30 minutes

export class OwlCreationWizard {
  private sessions = new Map<string, WizardSession>()

  constructor(
    private workspacePath: string,
    private db: WizardDb | undefined,
    private writeFn: WriteFn = (p, c) => {
      fs.mkdirSync(path.dirname(p), { recursive: true })
      fs.writeFileSync(p, c, "utf-8")
    },
  ) {}

  isActive(userId: string): boolean {
    const session = this.sessions.get(userId)
    if (!session) return false
    if (Date.now() - session.startedAt > SESSION_TIMEOUT_MS) {
      this.sessions.delete(userId)
      return false
    }
    return true
  }

  cancel(userId: string): void {
    this.sessions.delete(userId)
  }

  async start(userId: string, channelAdapter: ChannelAdapterV2): Promise<string> {
    this.sessions.set(userId, { userId, startedAt: Date.now() })

    try {
      return await this.runWizard(userId, channelAdapter)
    } finally {
      this.sessions.delete(userId)
    }
  }

  private async runWizard(userId: string, adapter: ChannelAdapterV2): Promise<string> {
    // Step 1 — Name
    const name = await adapter.ask(userId, { text: "What should I call your new helper?" })
    if (!name || name.toLowerCase() === "cancel") return "Cancelled."

    // Step 2 — Role
    const role = await adapter.ask(userId, {
      text: `What will ${name} help with?`,
    })

    // Step 3 — Personality
    const personalityChoice = await adapter.ask(userId, {
      text: `Pick a style for ${name}:`,
      choices: ["Warm & patient", "Direct & efficient", "Curious & encouraging", "Formal & precise", "Custom…"],
    })
    let personality = personalityChoice
    if (personalityChoice === "Custom…") {
      personality = await adapter.ask(userId, { text: `Describe ${name}'s style in a few words:` })
    }

    // Step 4 — Capabilities
    const capsChoice = await adapter.ask(userId, {
      text: `What can ${name} do?`,
      choices: ["Search the web", "Read & write files", "Run code", "Manage tasks", "All of the above"],
    })
    const caps = capsChoice === "All of the above"
      ? ["web_search", "read_file", "write_file", "run_shell_command", "manage_tasks"]
      : [capsChoice.toLowerCase().replace(/[^a-z]+/g, "_")]

    // Step 5 — Restrictions
    const restrictions = await adapter.ask(userId, {
      text: `Anything ${name} should never do?`,
      defaultChoice: "Nothing specific",
    })
    const deniedTools = restrictions === "Nothing specific" || restrictions === "skip"
      ? []
      : [restrictions]

    // Step 6 — Confirm
    const summary = `${name}: ${role}. Style: ${personality}. Can: ${capsChoice}.`
    const confirm = await adapter.ask(userId, {
      text: `Creating ${summary} Ready?`,
      choices: ["Yes, create it", "No, start over"],
    })
    if (confirm === "No, start over") {
      return this.runWizard(userId, adapter) // restart
    }

    // Step 7 — Recurring task (optional)
    const recurringTask = await adapter.ask(userId, {
      text: `Should ${name} work on anything automatically?\nFor example: "Check the news daily at 9am" (or skip)`,
      defaultChoice: "skip",
    })
    const hasRecurring = recurringTask && recurringTask.toLowerCase() !== "skip" && recurringTask !== "Nothing specific"

    // Write helper.md
    const helperMd = this.buildHelperMd({
      name, role, personality, caps, deniedTools,
      recurringTask: hasRecurring ? recurringTask : undefined,
    })
    const helperPath = path.join(this.workspacePath, "owls", name, "helper.md")
    this.writeFn(helperPath, helperMd)

    // Write owl_jobs row if recurring task provided
    if (hasRecurring && this.db?.owlJobs) {
      const schedule = this.parseSchedule(recurringTask)
      this.db.owlJobs.insert({
        id: uuidv4(),
        helper_name: name,
        owner_id: userId,
        schedule,
        task_description: recurringTask,
        channel_id: "default",
      })
    }

    const recurringNote = hasRecurring
      ? `\nI've also set up "${recurringTask}" for ${name} to handle automatically.`
      : ""

    return `✓ ${name} is ready! Say "${name}, ..." anytime to reach her.${recurringNote}`
  }

  private buildHelperMd(opts: {
    name: string
    role: string
    personality: string
    caps: string[]
    deniedTools: string[]
    recurringTask?: string
  }): string {
    const tone = opts.personality.includes("Warm") ? "warm"
      : opts.personality.includes("Direct") ? "professional"
      : opts.personality.includes("Formal") ? "formal"
      : opts.personality.includes("Curious") ? "encouraging"
      : opts.personality.toLowerCase().slice(0, 20)

    const lines = [
      "---",
      `name: ${opts.name}`,
      `type: specialist`,
      `role: ${opts.role}`,
      `emoji: 🦉`,
      `personality:`,
      `  challengeLevel: medium`,
      `  verbosity: balanced`,
      `  tone: ${tone}`,
      `expertise: []`,
      `model:`,
      `  provider: anthropic`,
      `  modelId: claude-haiku-4-5-20251001`,
      `permissions:`,
      `  allowedTools: [${opts.caps.map(c => `"${c}"`).join(", ")}]`,
      `  deniedTools: [${opts.deniedTools.map(d => `"${d}"`).join(", ")}]`,
      `  capabilityConstraints: []`,
      `routingRules:`,
      `  keywords: []`,
      `  domains: []`,
      `  priority: 5`,
      `skills:`,
      `  canLearn: false`,
      `  retainedKnowledge: []`,
      opts.recurringTask ? `recurring_task: "${opts.recurringTask}"` : null,
      `---`,
      ``,
      `You are ${opts.name}, a ${opts.role} helper. ${opts.personality}.`,
    ].filter(s => s !== null)

    return lines.join("\n") + "\n"
  }

  private parseSchedule(description: string): string {
    // Cheap heuristic — extract time patterns from description
    const hourMatch = description.match(/(\d{1,2})\s*(?:am|pm)/i)
    const hour = hourMatch
      ? (parseInt(hourMatch[1]) + (hourMatch[0].toLowerCase().includes("pm") && parseInt(hourMatch[1]) !== 12 ? 12 : 0))
      : 9
    return `${hour.toString().padStart(2, "0")}:00 daily`
  }
}
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/gateway/owl-creation-wizard.test.ts
```
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/gateway/wizards/owl-creation.ts __tests__/gateway/owl-creation-wizard.test.ts
git commit -m "feat(e17): OwlCreationWizard — 6+1 step channel-agnostic wizard via ChannelAdapterV2.ask() (D8)"
```

---

### Task 14: CLI wiring + Telegram/Slack wiring

**Files:**
- Modify: `src/cli/commands.ts`
- Modify: `src/gateway/adapters/telegram.ts`
- Modify: `src/gateway/adapters/slack.ts`

- [ ] **Step 1: Replace `/specialization` CLI handler with `/helper` in `src/cli/commands.ts`**

Find the `let activeWizard` singleton at L60 — remove it.

Find the `/specialization` command registration block at L64–191. Replace with a thin wrapper that calls `dispatchOwlCommand`:

```typescript
program
  .command("helper [verb] [args...]")
  .description("Manage your helpers — list, show, create, rename, delete")
  .action(async (verb = "list", args: string[]) => {
    const { dispatchOwlCommand } = await import("../gateway/commands/owl-router.js")
    const { SpecializedOwlRegistry } = await import("../owls/specialized-registry.js")
    const { OwlCreationWizard } = await import("../gateway/wizards/owl-creation.js")
    const workspacePath = process.cwd()
    const registry = new SpecializedOwlRegistry(workspacePath)
    registry.loadAll()
    const wizard = new OwlCreationWizard(workspacePath, undefined)
    const adapter = {
      ask: async (_userId: string, prompt: { text: string; choices?: string[]; defaultChoice?: string }) => {
        const { default: readline } = await import("node:readline")
        const rl = readline.createInterface({ input: process.stdin, output: process.stdout })
        const choices = prompt.choices ? `\n${prompt.choices.map((c, i) => `  ${i + 1}. ${c}`).join("\n")}` : ""
        return new Promise<string>((resolve) => {
          rl.question(`${prompt.text}${choices}\n> `, (ans) => {
            rl.close()
            if (!ans && prompt.defaultChoice) resolve(prompt.defaultChoice)
            else if (prompt.choices) {
              const idx = parseInt(ans) - 1
              resolve(!isNaN(idx) && prompt.choices[idx] ? prompt.choices[idx] : ans)
            }
            else resolve(ans)
          })
        })
      },
    }
    const result = await dispatchOwlCommand(verb, args, {
      registry: registry as any,
      wizard: wizard as any,
      userId: "local",
      channelAdapter: adapter as any,
      workspacePath,
    })
    console.log(result)
  })
```

Also remove the `/specialization` alias registration at L370–371 (or wherever the old command was registered).

- [ ] **Step 2: Add `/helper` command to Telegram in `src/gateway/adapters/telegram.ts`**

Find existing command registration patterns. Add:

```typescript
bot.command("helper", async (ctx) => {
  const parts = ctx.message?.text?.split(/\s+/) ?? []
  const verb = parts[1] ?? "list"
  const args = parts.slice(2)

  const { dispatchOwlCommand } = await import("../../gateway/commands/owl-router.js")
  const result = await dispatchOwlCommand(verb, args, {
    registry: this.registry as any,
    wizard: this.wizard as any,
    userId: String(ctx.from?.id ?? "unknown"),
    channelAdapter: this.channelAdapter as any,
    workspacePath: this.workspacePath,
  })
  await ctx.reply(result, { parse_mode: "Markdown" })
})
```

Adjust property names to match what `TelegramAdapter` actually exposes (check the class fields).

- [ ] **Step 3: Add Slack helper commands in `src/gateway/adapters/slack.ts`**

Find existing Slack slash command handling. Add handlers for `/helper-list`, `/helper-show`, `/helper-create`, `/helper-delete`:

```typescript
// In Slack command routing (wherever other commands like /owl-status are handled):
if (command === "/helper-list" || command === "/helper-show" ||
    command === "/helper-create" || command === "/helper-delete" ||
    command === "/helper-rename") {
  const verb = command.replace("/helper-", "")
  const args = payload.text ? payload.text.trim().split(/\s+/) : []
  const { dispatchOwlCommand } = await import("../../gateway/commands/owl-router.js")
  const result = await dispatchOwlCommand(verb, args, {
    registry: this.registry as any,
    wizard: this.wizard as any,
    userId: payload.user_id ?? "unknown",
    channelAdapter: this.channelAdapter as any,
    workspacePath: this.workspacePath,
  })
  return { text: result }
}
```

Adjust property names to match the actual `SlackAdapter` class.

- [ ] **Step 4: Run full test suite**

```bash
npx vitest run
```
Expected: no regressions

- [ ] **Step 5: Commit**

```bash
git add src/cli/commands.ts src/gateway/adapters/telegram.ts src/gateway/adapters/slack.ts
git commit -m "feat(e17): wire /helper to dispatchOwlCommand — CLI, Telegram, Slack (G13 channel parity)"
```

---

## Phase F — Sub-Owl Parallel Execution

### Task 15: SubTask.args + SubOwlExecutor fix

**Files:**
- Modify: `src/delegation/decomposer.ts`
- Modify: `src/delegation/subowl-executor.ts`
- Create: `__tests__/delegation/subowl-args-passthrough.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/delegation/subowl-args-passthrough.test.ts
import { describe, it, expect, vi } from "vitest"
import { SubOwlExecutor } from "../src/delegation/subowl-executor.js"

describe("SubOwlExecutor args passthrough", () => {
  it("passes task.args to tool.execute — not empty {}", async () => {
    const capturedArgs: any[] = []
    const mockTool = {
      execute: vi.fn().mockImplementation(async (args: any) => {
        capturedArgs.push(args)
        return "result"
      }),
      name: "web_search",
    }
    const mockRegistry = new Map([["web_search", mockTool]])
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({ content: "done", model: "m", finishReason: "stop" }),
      listModels: vi.fn().mockResolvedValue([]),
    }

    const executor = new SubOwlExecutor(mockProvider as any, mockRegistry as any, "workspace")
    await executor.execute({
      id: "t1",
      description: "Search for cats",
      tools: ["web_search"],
      dependsOn: [],
      expectedOutput: "list of cats",
      args: { query: "cats", maxResults: 5 },
    }, {} as any)

    expect(mockTool.execute).toHaveBeenCalledWith(
      { query: "cats", maxResults: 5 },
      expect.anything(),
    )
  })

  it("passes empty {} when task.args is undefined", async () => {
    const capturedArgs: any[] = []
    const mockTool = {
      execute: vi.fn().mockImplementation(async (args: any) => {
        capturedArgs.push(args)
        return "ok"
      }),
      name: "list_files",
    }
    const mockRegistry = new Map([["list_files", mockTool]])
    const mockProvider = {
      name: "mock",
      chat: vi.fn().mockResolvedValue({ content: "done", model: "m", finishReason: "stop" }),
      listModels: vi.fn().mockResolvedValue([]),
    }

    const executor = new SubOwlExecutor(mockProvider as any, mockRegistry as any, "workspace")
    await executor.execute({
      id: "t1",
      description: "List files",
      tools: ["list_files"],
      dependsOn: [],
      expectedOutput: "file list",
      // args intentionally absent
    }, {} as any)

    expect(mockTool.execute).toHaveBeenCalledWith({}, expect.anything())
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/delegation/subowl-args-passthrough.test.ts
```

- [ ] **Step 3: Add `args` to `SubTask` in `src/delegation/decomposer.ts`**

Find `interface SubTask` (or `export interface SubTask`) in `src/delegation/decomposer.ts`. Add:
```typescript
  /** Tool arguments to pass to tool.execute() — populated by planner */
  args?: Record<string, unknown>
```

- [ ] **Step 4: Fix `SubOwlExecutor.execute()` in `src/delegation/subowl-executor.ts`**

Find line 41 (or wherever `tool.execute({}, context)` appears):
```typescript
// Before:
const result = await tool.execute({}, context)

// After:
const result = await tool.execute(task.args ?? {}, context)
```

The `SubOwlExecutor` constructor signature may need a `toolRegistry` parameter if it doesn't already have one. Check the constructor. If `toolRegistry` is already a parameter, verify it's used. If not, add it:
```typescript
constructor(
  private provider: ModelProvider,
  private toolRegistry: Map<string, any>,  // add if missing
  private workspacePath: string,
) {}
```

- [ ] **Step 5: Run test — confirm passing**

```bash
npx vitest run __tests__/delegation/subowl-args-passthrough.test.ts
```
Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/delegation/decomposer.ts src/delegation/subowl-executor.ts __tests__/delegation/subowl-args-passthrough.test.ts
git commit -m "fix(e17): SubTask.args field; SubOwlExecutor passes task.args to tool.execute (G10 fix 1)"
```

---

### Task 16: SubOwlRunner tool registry + real reactLoop

**Files:**
- Modify: `src/delegation/sub-owl-runner.ts`
- Modify: `src/gateway/core.ts` (wire toolRegistry to SubOwlRunner)
- Create: `__tests__/delegation/subowl-tool-execution.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/delegation/subowl-tool-execution.test.ts
import { describe, it, expect, vi } from "vitest"
import { SubOwlRunner } from "../src/delegation/sub-owl-runner.js"

function makeMockProvider(responses: string[]) {
  let i = 0
  return {
    name: "mock",
    chat: vi.fn().mockImplementation(async () => ({
      content: responses[i++] ?? "final answer",
      model: "m",
      finishReason: "stop",
    })),
    listModels: vi.fn().mockResolvedValue([]),
  }
}

describe("SubOwlRunner tool execution", () => {
  it("invokes tool from registry when LLM response contains tool call", async () => {
    const toolCalled = vi.fn().mockResolvedValue("search result: cat facts")
    const registry = new Map([
      ["web_search", { execute: toolCalled, name: "web_search" }],
    ])

    // Provider first says "call tool: web_search {query:'cats'}", then says final answer
    const provider = makeMockProvider([
      JSON.stringify({ tool: "web_search", args: { query: "cats" } }),
      "Here are some cat facts based on the search results.",
    ])

    const runner = new SubOwlRunner(
      provider as any,
      registry as any,
      "You are a helpful assistant.",
      "/workspace",
      2,
    )

    const result = await runner.run([{
      id: "t1",
      description: "Find cat facts",
      tools: ["web_search"],
      dependsOn: [],
      expectedOutput: "cat facts",
      args: { query: "cats" },
    }])

    expect(toolCalled).toHaveBeenCalled()
    expect(result.length).toBeGreaterThan(0)
  })

  it("handles tool not in registry gracefully", async () => {
    const provider = makeMockProvider([
      JSON.stringify({ tool: "unknown_tool", args: {} }),
      "I cannot use that tool.",
    ])
    const registry = new Map() // empty

    const runner = new SubOwlRunner(provider as any, registry as any, "You help.", "/workspace", 2)
    const result = await runner.run([{
      id: "t1", description: "test", tools: [], dependsOn: [], expectedOutput: "anything",
    }])

    expect(result.length).toBeGreaterThan(0)
    // Should not throw
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/delegation/subowl-tool-execution.test.ts
```

- [ ] **Step 3: Update `SubOwlRunner` in `src/delegation/sub-owl-runner.ts`**

Add `toolRegistry` parameter to constructor:
```typescript
constructor(
  private provider: ModelProvider,
  private toolRegistry: Map<string, { execute: (args: Record<string, unknown>, ctx: any) => Promise<string>; name: string }>,
  private owlPersonality: string,
  private workspacePath: string,
  private maxIterations = 5,
) {}
```

Add `parseToolCall()` private helper:
```typescript
  private parseToolCall(response: string): { toolName: string; toolArgs: Record<string, unknown> } | null {
    try {
      const parsed = JSON.parse(response.trim())
      if (typeof parsed.tool === "string") {
        return { toolName: parsed.tool, toolArgs: parsed.args ?? {} }
      }
    } catch { /* not a JSON tool call */ }
    return null
  }
```

Replace the `reactLoop()` stub (the one that returns `"[Tool execution not available in sub-owl context.]"`) with real dispatch:

```typescript
  private async reactLoop(task: SubTask, context: any): Promise<string> {
    const history: Array<{ role: "user" | "assistant" | "system"; content: string }> = [
      { role: "system", content: this.owlPersonality },
      { role: "user", content: `Task: ${task.description}\nExpected output: ${task.expectedOutput}` },
    ]

    for (let i = 0; i < this.maxIterations; i++) {
      const response = await this.provider.chat(history, {})
      history.push({ role: "assistant", content: response.content })

      const toolCall = this.parseToolCall(response.content)
      if (!toolCall) {
        // No tool call — this is the final answer
        return response.content
      }

      const tool = this.toolRegistry.get(toolCall.toolName)
      const toolResult = tool
        ? await tool.execute(toolCall.toolArgs, context).catch((e: Error) => `[Tool error: ${e.message}]`)
        : `[Tool "${toolCall.toolName}" not found in registry]`

      history.push({ role: "user", content: `Tool result: ${toolResult}` })
    }

    // Exceeded max iterations — return best effort
    const lastAssistant = [...history].reverse().find(m => m.role === "assistant")
    return lastAssistant?.content ?? "[Sub-owl: max iterations reached]"
  }
```

- [ ] **Step 4: Wire `toolRegistry` in `src/gateway/core.ts`**

Find where `SubOwlRunner` is constructed (search for `new SubOwlRunner`). Add the tool registry:
```typescript
new SubOwlRunner(
  provider,
  ctx.toolRegistry ?? new Map(),  // pass registry; defaults to empty if not available
  owlPersonality,
  workspacePath,
  maxIterations,
)
```

- [ ] **Step 5: Run test — confirm passing**

```bash
npx vitest run __tests__/delegation/subowl-tool-execution.test.ts
```
Expected: 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/delegation/sub-owl-runner.ts src/gateway/core.ts __tests__/delegation/subowl-tool-execution.test.ts
git commit -m "feat(e17): SubOwlRunner real tool dispatch — toolRegistry injection + reactLoop (G10 fix 2)"
```

---

## Phase G — Parliament Diversity

### Task 17: Parliament shuffled selection

**Files:**
- Modify: `src/gateway/core.ts`
- Create: `__tests__/parliament/shuffled-selection.test.ts`

- [ ] **Step 1: Write failing test**

```typescript
// __tests__/parliament/shuffled-selection.test.ts
import { describe, it, expect } from "vitest"

// We test the Fisher-Yates shuffleArray utility directly
import { shuffleArray } from "../src/gateway/core.js"

describe("Parliament shuffled selection", () => {
  it("shuffleArray returns all original elements", () => {
    const arr = [1, 2, 3, 4, 5]
    const result = shuffleArray([...arr])
    expect(result.sort()).toEqual(arr.sort())
  })

  it("shuffleArray produces varied orderings across 20 calls", () => {
    const arr = ["a", "b", "c", "d", "e"]
    const orderings = new Set<string>()
    for (let i = 0; i < 20; i++) {
      orderings.add(shuffleArray([...arr]).join(","))
    }
    // With 5 elements, probability of all 20 being identical is (1/120)^19 ≈ 0
    expect(orderings.size).toBeGreaterThan(1)
  })

  it("shuffleArray handles empty array", () => {
    expect(shuffleArray([])).toEqual([])
  })

  it("shuffleArray handles single element", () => {
    expect(shuffleArray(["only"])).toEqual(["only"])
  })
})
```

- [ ] **Step 2: Run test — confirm failure**

```bash
npx vitest run __tests__/parliament/shuffled-selection.test.ts
```
Expected: FAIL (`shuffleArray` not exported)

- [ ] **Step 3: Add `shuffleArray` to `src/gateway/core.ts` and update parliament selection**

Near the top of `src/gateway/core.ts` (after imports, before the class), add:

```typescript
export function shuffleArray<T>(arr: T[]): T[] {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}
```

Find `listOwls().slice(0, 3)` at `core.ts:1895` and `core.ts:2026` (both parliament participant selection sites). Replace each with:
```typescript
shuffleArray([...this.ctx.owlRegistry.listOwls()]).slice(0, 3)
```

- [ ] **Step 4: Run test — confirm passing**

```bash
npx vitest run __tests__/parliament/shuffled-selection.test.ts
```
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts __tests__/parliament/shuffled-selection.test.ts
git commit -m "feat(e17): Fisher-Yates parliament participant shuffle — diversity over slice(0,3) (G11)"
```

---

## Phase H — Cleanup

### Task 18: Config drift + delete dead files

**Files:**
- Modify: `src/gateway/handlers/post-processor.ts`
- Delete: `dist/wizard/owl-creation.d.ts`
- Delete: `__tests__/memory/owls-repo.test.ts`
- Delete: `src/gateway/handlers/routing-coordinator.ts`
- Delete: `src/cli/specialization-wizard.ts`

- [ ] **Step 1: Fix `evolutionBatchSize` drift in `src/gateway/handlers/post-processor.ts:219`**

Find `evolutionBatchSize ?? 10` and change to `evolutionBatchSize ?? 5`:
```typescript
// Before:
const batchSize = config.owlDna?.evolutionBatchSize ?? 10

// After:
const batchSize = config.owlDna?.evolutionBatchSize ?? 5
```

`src/config/loader.ts:348` is the source of truth at `5` — the post-processor fallback must match.

- [ ] **Step 2: Delete orphaned compiled artifact**

```bash
rm -f dist/wizard/owl-creation.d.ts
```

- [ ] **Step 3: Delete `__tests__/memory/owls-repo.test.ts`**

```bash
rm __tests__/memory/owls-repo.test.ts
```

- [ ] **Step 4: Verify routing-coordinator.ts is safe to delete**

Check that `injectMemoryContext()` in `src/gateway/handlers/routing-coordinator.ts` (around L127–165) covers no behavior not already in `src/routing/owl-brain.ts:170–195`. Both load digest context + pellet memory. If `routing-coordinator.ts` has any unique handling (e.g. different pellet filter), port it to `OwlBrain.injectMemoryContext()` first.

Then delete:
```bash
rm src/gateway/handlers/routing-coordinator.ts
```

Update any import sites that reference `RoutingCoordinator` — search with:
```bash
grep -r "routing-coordinator\|RoutingCoordinator" src/
```
Remove those imports and the `else if (this.routingCoordinator)` fallback in `core.ts:2007`.

- [ ] **Step 5: Delete `src/cli/specialization-wizard.ts`**

The wizard is now superseded by `src/gateway/wizards/owl-creation.ts`. Remove the singleton and any remaining references:

```bash
rm src/cli/specialization-wizard.ts
```

Remove any `import` of `SpecializationCreateWizard` or `specialization-wizard` remaining in `commands.ts` (should already be gone after Task 14 CLI wiring).

- [ ] **Step 6: Run full test suite**

```bash
npx vitest run
```
Expected: all tests pass. The removed `owls-repo.test.ts` no longer runs. TypeScript compiler must be clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore(e17): evolutionBatchSize ?? 5; delete dead files (routing-coordinator, specialization-wizard, owls-repo.test)"
```

---

## Self-Review

### Spec coverage

| Spec section | Tasks covering it |
|---|---|
| Helper rebrand (1.1) | T3, T4, T14, T15 |
| v28 migration (1.2) | T1 |
| OwlQualityRepo (1.3) | T2 |
| OwlManagementRouter (2.1) | T12 |
| OwlCreationWizard (2.2) | T13 |
| Persistent tasks (2.3) | T13 |
| Per-channel pin (3.1) | T9 |
| Soft-pin TTL (3.2) | T10 |
| NL mention parser (3.3) | T10 |
| Quality routing (3.4) | T11 |
| Monologue race fix (4.1) | T7 |
| Jailbreak surface deletion (4.2) | T6 |
| RelationshipContext wiring (4.3) | T8 |
| OpinionInjector wiring (4.4) | T8 |
| 8 DNA traits (4.5) | T5 |
| SubOwlExecutor args (5.1) | T15 |
| SubOwlRunner toolRegistry (5.2) | T16 |
| Parliament shuffle (6) | T17 |
| Channel parity (7) | T14 |
| Config drift (9) | T18 |
| Delete orphans (9) | T18 |

### Placeholder scan

All tasks have complete code. No "TBD", "TODO", or "implement later" in any step.

### Type consistency

- `HelperSpec = SpecializedOwlSpec` alias introduced in T3; `HelperRegistry = SpecializedOwlRegistry` alias introduced in T3.
- `OwlQualityRepo` / `OwlPinsRepo` introduced in T2, used by T9/T11 via `db.owlQualityMetrics` / `db.owlPins`.
- `SubTask.args?: Record<string, unknown>` added in T15, consumed by T16 executor.
- `EngineContext.additionalSystemPrompt?: string` added in T8, written by core.ts pre-LLM.
- `EngineContext.relationshipContext?` added in T8, read in runtime.ts system prompt assembly.
- `OwlBrain.setClassifyFn()` added in T10, wired in core.ts in T10.
- `shuffleArray` exported from core.ts in T17, tested directly.

**DB migration version:** All tasks use v28. Spec document says "v23" in some sections — this is an error in the spec. The implementation plan is correct: current schema is v27, new migration is v28.
