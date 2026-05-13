# Parliament Validation Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `parliament_verdicts.validated` actually mean something by adding an adversarial validator LLM call after synthesis, replacing the boolean flag with a confidence score, requiring the synthesizer to cite agent positions, and fixing the recall system to inject only top-2 high-confidence past verdicts.

**Architecture:** Four changes in sequence: (1) DB schema gains `confidence_score`, `topic_class`, `expires_at`, `validator_reasoning`, `agent_citations`; (2) Round 3 synthesis prompt requires the synthesizer to cite which agent's position drove the verdict; (3) orchestrator runs one adversarial LLM call after synthesis, updates confidence, re-convenes once on high-stakes INVALID verdicts; (4) recall query filters expired records and injects top-2 by confidence instead of all validated. A 3-sentence audit summary is emitted to the user after every session.

**Tech Stack:** TypeScript, better-sqlite3 (synchronous), Vitest, existing OwlEngine for validator LLM call

---

## File Map

| Action | Path |
|--------|------|
| MODIFY | `src/memory/db.ts` — schema migration, `ParliamentVerdictRecord` type, `record()`, `validate()`, `findRelated()`, new `updateConfidence()` method |
| MODIFY | `src/parliament/protocol.ts` — add `agentCitations?: string` to `ParliamentSession` |
| MODIFY | `src/parliament/multi-round-debate.ts` — Round 3 prompt adds citation requirement, parse and store `session.agentCitations` |
| MODIFY | `src/parliament/orchestrator.ts` — adversarial validator call after `runDebate()`, top-2 recall query, pass confidence to `record()` |
| MODIFY | `src/tools/parliament.ts` — 3-sentence audit summary emitted via `onProgress` |
| CREATE | `__tests__/parliament/validation.test.ts` — tests for citation parsing, confidence update, recall top-2 |

---

### Task 1: DB schema — add confidence_score and related columns

**Files:**
- Modify: `src/memory/db.ts`
- Test: `__tests__/parliament/validation.test.ts`

Context: `parliament_verdicts` table is defined at line ~917. `ParliamentVerdictRecord` interface is at line ~334. `ParliamentVerdictsRepo` class is at line ~2800. The existing `validated INTEGER NOT NULL DEFAULT 0` column stays — we add confidence_score alongside it. The `record()` method at line ~2804 inserts a new verdict. The `validate()` method at line ~2826 sets `validated = 1`.

- [ ] **Step 1: Write the failing test**

Create `__tests__/parliament/validation.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import Database from "better-sqlite3";
import { MemoryDatabase } from "../../src/memory/db.js";
import * as os from "os";
import * as path from "path";
import * as fs from "fs";

function makeTempDb(): { db: MemoryDatabase; cleanup: () => void } {
  const tmpPath = path.join(os.tmpdir(), `test-parliament-${Date.now()}.db`);
  const db = new MemoryDatabase(tmpPath);
  return { db, cleanup: () => { try { fs.unlinkSync(tmpPath); } catch {} } };
}

describe("parliament_verdicts confidence_score", () => {
  it("record() stores confidence_score and topic_class", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const id = db.parliamentVerdicts.record(
        "session-1", "Should we use GraphQL?", "PROCEED",
        ["Mary", "Winston"], "synthesis text",
        { confidenceScore: 0.8, topicClass: "architectural" },
      );
      const rows = (db as any).db.prepare(
        "SELECT confidence_score, topic_class FROM parliament_verdicts WHERE id = ?"
      ).all(id);
      expect(rows[0].confidence_score).toBeCloseTo(0.8);
      expect(rows[0].topic_class).toBe("architectural");
    } finally { cleanup(); }
  });

  it("updateConfidence() sets confidence_score and validator_reasoning", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const id = db.parliamentVerdicts.record(
        "session-2", "Test topic", "HOLD",
        ["Mary"], "synthesis",
      );
      db.parliamentVerdicts.updateConfidence(id, 0.95, "Logic is sound");
      const rows = (db as any).db.prepare(
        "SELECT confidence_score, validator_reasoning FROM parliament_verdicts WHERE id = ?"
      ).all(id);
      expect(rows[0].confidence_score).toBeCloseTo(0.95);
      expect(rows[0].validator_reasoning).toBe("Logic is sound");
    } finally { cleanup(); }
  });

  it("findRelated() returns top-2 by confidence_score and filters expired", () => {
    const { db, cleanup } = makeTempDb();
    try {
      const now = Math.floor(Date.now() / 1000);
      const id1 = db.parliamentVerdicts.record(
        "s1", "GraphQL architecture decision", "PROCEED",
        ["Mary"], "high confidence",
        { confidenceScore: 0.9, topicClass: "architectural" },
      );
      db.parliamentVerdicts.updateConfidence(id1, 0.9, "valid");

      const id2 = db.parliamentVerdicts.record(
        "s2", "GraphQL vs REST API design", "HOLD",
        ["Winston"], "medium confidence",
        { confidenceScore: 0.6, topicClass: "architectural" },
      );
      db.parliamentVerdicts.updateConfidence(id2, 0.6, "uncertain");

      // Expired verdict — should be excluded
      const id3 = db.parliamentVerdicts.record(
        "s3", "GraphQL query optimization", "PROCEED",
        ["John"], "expired",
        { confidenceScore: 0.85, topicClass: "tactical", expiresAt: now - 1 },
      );
      db.parliamentVerdicts.updateConfidence(id3, 0.85, "expired");

      const results = db.parliamentVerdicts.findRelated("GraphQL API design", 2);
      expect(results.length).toBeLessThanOrEqual(2);
      expect(results.every(r => r.confidenceScore >= 0.5)).toBe(true);
      // Expired verdict must not appear
      expect(results.find(r => r.id === id3)).toBeUndefined();
    } finally { cleanup(); }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: FAIL — `record()` doesn't accept options object, `updateConfidence` is not a function, `findRelated` doesn't filter expired.

- [ ] **Step 3: Update `ParliamentVerdictRecord` interface in `src/memory/db.ts`**

Find the interface at line ~334. Replace it with:

```typescript
export interface ParliamentVerdictRecord {
  id: string;
  sessionId: string;
  topic: string;
  verdict: ParliamentVerdictSignal;
  synthesis?: string;
  participants: string[];
  /** 1 when the outcome has been observed via trajectory reward */
  validated: number;
  validationSignal?: ParliamentValidationSignal;
  /** Reward from the trajectory that followed this verdict */
  validationReward?: number;
  /** Confidence score 0.0–1.0. Warm start 0.6, updated by validator and user signal. */
  confidenceScore: number;
  /** "tactical" | "architectural" — controls decay rate */
  topicClass: string;
  /** Unix timestamp after which this verdict is excluded from recall. null = never. */
  expiresAt?: number;
  /** One-sentence reason from the adversarial validator. */
  validatorReasoning?: string;
  /** JSON: array of {agentName, claim} cited by the synthesizer. */
  agentCitations?: string;
  createdAt: string;
}
```

- [ ] **Step 4: Add columns to the CREATE TABLE statement in `src/memory/db.ts`**

Find the `parliament_verdicts` CREATE TABLE at line ~917. Replace it:

```sql
CREATE TABLE IF NOT EXISTS parliament_verdicts (
  id                  TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL,
  topic               TEXT NOT NULL,
  verdict             TEXT NOT NULL,
  synthesis           TEXT,
  participants        TEXT NOT NULL DEFAULT '[]',
  validated           INTEGER NOT NULL DEFAULT 0,
  validation_signal   TEXT,
  validation_reward   REAL,
  confidence_score    REAL NOT NULL DEFAULT 0.6,
  topic_class         TEXT NOT NULL DEFAULT 'tactical',
  expires_at          INTEGER,
  validator_reasoning TEXT,
  agent_citations     TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pv_topic       ON parliament_verdicts(topic);
CREATE INDEX IF NOT EXISTS idx_pv_validated   ON parliament_verdicts(validated);
CREATE INDEX IF NOT EXISTS idx_pv_confidence  ON parliament_verdicts(confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_pv_expires     ON parliament_verdicts(expires_at);
```

- [ ] **Step 5: Add migration block for existing databases**

Find the migration section (around line ~1145 where the duplicate `CREATE TABLE IF NOT EXISTS parliament_verdicts` appears). After the existing migration block, add:

```typescript
// Migration: add confidence_score, topic_class, expires_at, validator_reasoning, agent_citations
const pvCols = this.db.prepare("PRAGMA table_info(parliament_verdicts)").all() as { name: string }[];
const pvColNames = pvCols.map(c => c.name);
if (!pvColNames.includes("confidence_score")) {
  this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN confidence_score REAL NOT NULL DEFAULT 0.6");
}
if (!pvColNames.includes("topic_class")) {
  this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN topic_class TEXT NOT NULL DEFAULT 'tactical'");
}
if (!pvColNames.includes("expires_at")) {
  this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN expires_at INTEGER");
}
if (!pvColNames.includes("validator_reasoning")) {
  this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN validator_reasoning TEXT");
}
if (!pvColNames.includes("agent_citations")) {
  this.db.exec("ALTER TABLE parliament_verdicts ADD COLUMN agent_citations TEXT");
}
```

- [ ] **Step 6: Update `record()` to accept options and `rowToParliamentVerdict` to map new fields**

Find `record()` at line ~2804. Replace the entire method:

```typescript
record(
  sessionId: string,
  topic: string,
  verdict: ParliamentVerdictSignal,
  participants: string[],
  synthesis?: string,
  options?: {
    confidenceScore?: number;
    topicClass?: string;
    expiresAt?: number;
    agentCitations?: string;
  },
): string {
  const id = uuidv4();
  const confidenceScore = options?.confidenceScore ?? 0.6;
  const topicClass = options?.topicClass ?? "tactical";
  const expiresAt = options?.expiresAt ?? null;
  const agentCitations = options?.agentCitations ?? null;
  this.db.prepare(`
    INSERT INTO parliament_verdicts
      (id, session_id, topic, verdict, participants, synthesis,
       confidence_score, topic_class, expires_at, agent_citations)
    VALUES (?,?,?,?,?,?,?,?,?,?)
  `).run(
    id, sessionId,
    topic.slice(0, 400),
    verdict,
    JSON.stringify(participants),
    synthesis ? synthesis.slice(0, 1000) : null,
    confidenceScore,
    topicClass,
    expiresAt,
    agentCitations,
  );
  return id;
}
```

- [ ] **Step 7: Add `updateConfidence()` method to `ParliamentVerdictsRepo`**

Add after the `validate()` method:

```typescript
/** Update confidence score and validator reasoning after adversarial validation. */
updateConfidence(id: string, confidenceScore: number, validatorReasoning?: string): void {
  this.db.prepare(`
    UPDATE parliament_verdicts
    SET confidence_score = ?, validator_reasoning = ?
    WHERE id = ?
  `).run(Math.min(0.95, Math.max(0.0, confidenceScore)), validatorReasoning ?? null, id);
}
```

- [ ] **Step 8: Update `findRelated()` to filter expired, order by confidence, limit 2**

Replace the `findRelated()` method:

```typescript
findRelated(topic: string, limit = 2): ParliamentVerdictRecord[] {
  const nowSec = Math.floor(Date.now() / 1000);
  const words = topic
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 4)
    .slice(0, 10);

  const expireFilter = `(expires_at IS NULL OR expires_at > ${nowSec})`;

  if (words.length === 0) {
    const rows = this.db.prepare(
      `SELECT * FROM parliament_verdicts WHERE ${expireFilter}
       ORDER BY confidence_score DESC, created_at DESC LIMIT ?`
    ).all(limit) as any[];
    return rows.map(rowToParliamentVerdict);
  }

  const conditions = words.map(() => `topic LIKE ?`).join(" OR ");
  const params = words.map((w) => `%${w}%`);
  const rows = this.db.prepare(
    `SELECT * FROM parliament_verdicts
     WHERE (${conditions}) AND ${expireFilter}
     ORDER BY confidence_score DESC, created_at DESC LIMIT ?`
  ).all(...params, limit) as any[];
  return rows.map(rowToParliamentVerdict);
}
```

- [ ] **Step 9: Update `rowToParliamentVerdict` helper to map new fields**

Find the `rowToParliamentVerdict` function (search for `rowToParliamentVerdict`). Update it to include:

```typescript
confidenceScore: row.confidence_score ?? 0.6,
topicClass: row.topic_class ?? "tactical",
expiresAt: row.expires_at ?? undefined,
validatorReasoning: row.validator_reasoning ?? undefined,
agentCitations: row.agent_citations ?? undefined,
```

- [ ] **Step 10: Run the test**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: all 3 tests PASS.

- [ ] **Step 11: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "db\.ts|validation"
```

Expected: no errors.

- [ ] **Step 12: Commit**

```bash
git add src/memory/db.ts __tests__/parliament/validation.test.ts
git commit -m "feat(db): parliament_verdicts gains confidence_score, topic_class, expires_at, validator_reasoning, agent_citations"
```

---

### Task 2: Citation requirement in Round 3 synthesis prompt

**Files:**
- Modify: `src/parliament/protocol.ts`
- Modify: `src/parliament/multi-round-debate.ts`
- Test: `__tests__/parliament/validation.test.ts`

Context: `ParliamentSession` in `protocol.ts` needs a new optional `agentCitations` field. `runRound3()` in `multi-round-debate.ts` (line ~308) sends the synthesis prompt and parses the verdict keyword from the response — we extend the prompt to require a CITED line and parse it.

- [ ] **Step 1: Add `agentCitations` to `ParliamentSession` in `src/parliament/protocol.ts`**

Find the `ParliamentSession` interface (line ~52). Add two fields before the closing `}`:

```typescript
  /** Citations from the synthesizer: "AgentName — because one-sentence-reason" */
  agentCitations?: string;
  /** One-sentence reason from the adversarial validator (set by orchestrator). */
  validatorReasoning?: string;
```

- [ ] **Step 2: Write the failing test for citation parsing**

Add to `__tests__/parliament/validation.test.ts`:

```typescript
import { parseCitationFromSynthesis } from "../../src/parliament/multi-round-debate.js";

describe("parseCitationFromSynthesis", () => {
  it("extracts CITED line from synthesis response", () => {
    const response = "PROCEED. The group agrees on the direction.\n\nCITED: Winston — because his risk assessment was most thorough.";
    const result = parseCitationFromSynthesis(response);
    expect(result).toBe("Winston — because his risk assessment was most thorough.");
  });

  it("returns undefined when no CITED line present", () => {
    const response = "PROCEED. The group agrees.";
    const result = parseCitationFromSynthesis(response);
    expect(result).toBeUndefined();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | grep -E "FAIL|parseCitation"
```

Expected: FAIL — `parseCitationFromSynthesis` is not exported.

- [ ] **Step 4: Extend Round 3 prompt and export `parseCitationFromSynthesis` in `src/parliament/multi-round-debate.ts`**

Find the `prompt` constant in `runRound3()` at line ~358. Replace it:

```typescript
const prompt =
  `Here is the transcript of a Parliament session:\n\n${history}\n\n` +
  `Task: Synthesize this debate into a final verdict. ` +
  `1. Provide a clear recommendation (e.g., PROCEED, HOLD, ABORT, REVISE). ` +
  `2. Summarize the critical tradeoffs identified by the group. ` +
  `3. Suggest the concrete next step. ` +
  `Do NOT give a non-answer. Make a call even if the group is divided.\n\n` +
  `REQUIRED: End your response with exactly this format on its own line:\n` +
  `CITED: [AgentName] — because [one sentence explaining which position most influenced your verdict]`;
```

Then after `session.verdict = match ? match[1].toUpperCase() : "CONSENSUS_REACHED";`, add:

```typescript
session.agentCitations = parseCitationFromSynthesis(response.content);
```

Export the helper function (add before the class or at the bottom of the file):

```typescript
export function parseCitationFromSynthesis(content: string): string | undefined {
  const match = content.match(/^CITED:\s*(.+)$/m);
  return match ? match[1].trim() : undefined;
}
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: all citation tests PASS.

- [ ] **Step 6: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "protocol|multi-round"
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/parliament/protocol.ts src/parliament/multi-round-debate.ts __tests__/parliament/validation.test.ts
git commit -m "feat(parliament): Round 3 synthesis requires CITED attribution; parseCitationFromSynthesis exported"
```

---

### Task 3: Adversarial validator in orchestrator

**Files:**
- Modify: `src/parliament/orchestrator.ts`
- Test: `__tests__/parliament/validation.test.ts`

Context: After `this.multiRoundDebate.runDebate(session)` in `convene()` (line ~129), we add a validator LLM call. The validator receives the synthesis + citations and outputs `VALID | INVALID | UNCERTAIN + 1 sentence`. For ABORT/REJECT + INVALID: re-convene once. For PROCEED/HOLD + INVALID: continue with lowered confidence (0.3). The confidence is passed to `this.db.parliamentVerdicts.record()` when recording.

The validator uses the existing `OwlEngine` — import it and create a throwaway "validator" owl identity for the call.

- [ ] **Step 1: Write the failing test for validator integration**

Add to `__tests__/parliament/validation.test.ts`:

```typescript
import { parseValidatorResponse } from "../../src/parliament/orchestrator.js";

describe("parseValidatorResponse", () => {
  it("extracts VALID from validator output", () => {
    const r = parseValidatorResponse("VALID — the verdict follows logically from Winston's cited position.");
    expect(r.signal).toBe("VALID");
    expect(r.reason).toContain("verdict follows");
  });

  it("extracts INVALID from validator output", () => {
    const r = parseValidatorResponse("INVALID — the synthesizer ignored Mary's AGAINST position entirely.");
    expect(r.signal).toBe("INVALID");
  });

  it("extracts UNCERTAIN for ambiguous output", () => {
    const r = parseValidatorResponse("The synthesis is somewhat reasonable.");
    expect(r.signal).toBe("UNCERTAIN");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | grep -E "FAIL|parseValidator"
```

Expected: FAIL — `parseValidatorResponse` is not exported.

- [ ] **Step 3: Add imports to `src/parliament/orchestrator.ts`**

At the top of `orchestrator.ts`, add:

```typescript
import { OwlEngine } from "../engine/runtime.js";
import { createDefaultDNA } from "../owls/persona.js";
import type { OwlInstance } from "../owls/persona.js";
```

- [ ] **Step 4: Export `parseValidatorResponse` from `src/parliament/orchestrator.ts`**

Add this exported function before the `ParliamentOrchestrator` class:

```typescript
export interface ValidatorResult {
  signal: "VALID" | "INVALID" | "UNCERTAIN";
  reason: string;
}

export function parseValidatorResponse(content: string): ValidatorResult {
  const upper = content.toUpperCase();
  let signal: ValidatorResult["signal"] = "UNCERTAIN";
  if (upper.includes("INVALID")) signal = "INVALID";
  else if (upper.includes("VALID")) signal = "VALID";

  // Extract the reason (everything after the first em-dash or colon)
  const reasonMatch = content.match(/(?:VALID|INVALID|UNCERTAIN)[^\n]*?[—–-]\s*(.+)/i);
  const reason = reasonMatch ? reasonMatch[1].trim() : content.slice(0, 200).trim();
  return { signal, reason };
}
```

- [ ] **Step 5: Add `runAdversarialValidator()` private method to `ParliamentOrchestrator`**

Add this method inside the `ParliamentOrchestrator` class (after the constructor):

```typescript
private async runAdversarialValidator(
  session: ParliamentSession,
): Promise<ValidatorResult> {
  const engine = new OwlEngine();
  const validatorOwl: OwlInstance = {
    persona: {
      name: "Validator",
      type: "specialist",
      emoji: "🔍",
      challengeLevel: "relentless",
      specialties: ["logic", "critical thinking"],
      traits: ["skeptical"],
      systemPrompt:
        "You are an adversarial logic validator. Your job is to find flaws in reasoning, not to agree. Be brief and ruthless.",
      sourcePath: "",
    },
    dna: createDefaultDNA("Validator", "relentless"),
  } as OwlInstance;

  const positionsText = session.positions
    .map((p) => `- ${p.owlName} [${p.position}]: ${p.argument}`)
    .join("\n");

  const prompt =
    `You are validating a Parliament verdict.\n\n` +
    `TOPIC: ${session.config.topic}\n\n` +
    `POSITIONS:\n${positionsText}\n\n` +
    `VERDICT: ${session.verdict}\n\n` +
    `SYNTHESIS: ${session.synthesis?.slice(0, 600) ?? "(none)"}\n\n` +
    (session.agentCitations ? `CITED BY SYNTHESIZER: ${session.agentCitations}\n\n` : "") +
    `Task: Does the verdict logically follow from the positions and cited reasoning? ` +
    `Output EXACTLY one of: VALID, INVALID, or UNCERTAIN. ` +
    `Follow it with an em-dash and ONE sentence explaining why. ` +
    `Example: "VALID — the cited position directly supports the PROCEED recommendation."`;

  try {
    const response = await engine.run(prompt, {
      provider: this.provider,
      owl: validatorOwl,
      sessionHistory: [],
      config: this.config,
    });
    return parseValidatorResponse(response.content);
  } catch (err) {
    log.parliament.warn("[Parliament] Adversarial validator failed — defaulting UNCERTAIN", err);
    return { signal: "UNCERTAIN", reason: "Validator call failed." };
  }
}
```

- [ ] **Step 6: Store `provider` and `config` on `ParliamentOrchestrator`**

Currently `provider` and `config` are only passed to `MultiRoundDebateManager`. We need them for the validator. Update the constructor:

```typescript
export class ParliamentOrchestrator {
  private pelletGenerator: PelletGenerator;
  private pelletStore: PelletStore;
  private db?: MemoryDatabase;
  private readonly multiRoundDebate: MultiRoundDebateManager;
  private readonly provider: ModelProvider;
  private readonly config: StackOwlConfig;

  constructor(
    provider: ModelProvider,
    config: StackOwlConfig,
    pelletStore: PelletStore,
    _toolRegistry?: ToolRegistry,
    db?: MemoryDatabase,
  ) {
    this.provider = provider;
    this.config = config;
    this.pelletStore = pelletStore;
    this.db = db;
    this.pelletGenerator = new PelletGenerator(makeProviderRouter(provider));
    this.multiRoundDebate = new MultiRoundDebateManager(provider, config);
  }
```

- [ ] **Step 7: Wire validator into `convene()` after `runDebate()`**

In `convene()`, find the line `session.completedAt = Date.now();` (around line ~131). Replace the block from `session.completedAt` through the pellet generation with:

```typescript
session.completedAt = Date.now();
session.phase = "complete";

// ── Adversarial validator: check if synthesis follows from citations ──────
const HIGH_STAKES_VERDICTS = new Set(["ABORT", "REJECT"]);
let confidenceScore = 0.6; // warm start
let validatorResult: ValidatorResult = { signal: "UNCERTAIN", reason: "" };

try {
  validatorResult = await this.runAdversarialValidator(session);
  session.validatorReasoning = validatorResult.reason;

  if (validatorResult.signal === "VALID") {
    confidenceScore = Math.min(0.95, 0.6 + 0.2); // 0.8
    log.engine.info(`[Parliament] Validator: VALID — ${validatorResult.reason.slice(0, 80)}`);
  } else if (validatorResult.signal === "INVALID") {
    if (HIGH_STAKES_VERDICTS.has(session.verdict ?? "")) {
      // High-stakes: re-convene once with rotated participants
      log.engine.warn(`[Parliament] Validator INVALID on high-stakes verdict "${session.verdict}" — re-convening`);
      const rotated = [...session.config.participants].reverse();
      const retrySession: ParliamentSession = {
        id: session.id + "-retry",
        config: { ...session.config, participants: rotated },
        phase: "setup",
        positions: [],
        challenges: [],
        startedAt: Date.now(),
      };
      try {
        await this.multiRoundDebate.runDebate(retrySession);
        const retryValidator = await this.runAdversarialValidator(retrySession);
        if (retryValidator.signal === "VALID") {
          // Adopt retry session's verdict
          session.synthesis = retrySession.synthesis;
          session.verdict = retrySession.verdict;
          session.agentCitations = retrySession.agentCitations;
          session.validatorReasoning = retryValidator.reason;
          confidenceScore = 0.75;
          log.engine.info(`[Parliament] Retry VALID — adopted retry verdict "${session.verdict}"`);
        } else {
          session.verdict = "PARLIAMENT_INCONCLUSIVE";
          session.synthesis = `Original verdict ${session.verdict} was rejected by the validator. Retry also inconclusive. Reason: ${retryValidator.reason}`;
          confidenceScore = 0.1;
          log.engine.warn("[Parliament] Retry also invalid — verdict set to PARLIAMENT_INCONCLUSIVE");
        }
      } catch (retryErr) {
        log.parliament.warn("[Parliament] Re-convene failed", retryErr);
        confidenceScore = 0.2;
      }
    } else {
      // Low-stakes: continue but lower confidence
      confidenceScore = 0.3;
      log.engine.warn(`[Parliament] Validator INVALID on "${session.verdict}" — confidence lowered to 0.3`);
    }
  }
  // UNCERTAIN: keep warm start 0.6
} catch (err) {
  log.parliament.warn("[Parliament] Validator pipeline error", err);
}

// Automatically generate a Pellet from this session
```

- [ ] **Step 8: Pass confidence to `record()` call**

Find `this.db.parliamentVerdicts.record(` in `convene()`. Replace it with:

```typescript
this.db.parliamentVerdicts.record(
  session.id,
  config.topic,
  session.verdict as import("../memory/db.js").ParliamentVerdictSignal,
  config.participants.map((p) => p.persona.name),
  session.synthesis,
  {
    confidenceScore,
    topicClass: "tactical",
    agentCitations: session.agentCitations,
  },
);
```

- [ ] **Step 9: Run the validator response parsing tests**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 10: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep orchestrator
```

Expected: no errors.

- [ ] **Step 11: Commit**

```bash
git add src/parliament/orchestrator.ts __tests__/parliament/validation.test.ts
git commit -m "feat(parliament): adversarial validator post-synthesis — confidence score, re-convene on high-stakes INVALID"
```

---

### Task 4: Fix recall query — top-2, confidence-ordered, TTL-filtered

**Files:**
- Modify: `src/parliament/orchestrator.ts`
- Test: `__tests__/parliament/validation.test.ts`

Context: The recall block in `convene()` (line ~74–102) currently calls `this.db.parliamentVerdicts.findRelated(config.topic, 5)` and filters for `v.validated`. Since `findRelated()` now returns top-2 by confidence and filters expired records (Task 1), we only need to update the orchestrator to use the results directly — no need to filter for `v.validated` anymore. All records returned by `findRelated()` are usable because they have a confidence score.

- [ ] **Step 1: Write test for recall injection**

Add to `__tests__/parliament/validation.test.ts`:

```typescript
describe("recall context injection", () => {
  it("findRelated returns at most 2 results ordered by confidence", () => {
    const { db, cleanup } = makeTempDb();
    try {
      for (let i = 0; i < 5; i++) {
        const id = db.parliamentVerdicts.record(
          `s${i}`, `Should we use GraphQL for our API?`, "PROCEED",
          ["Mary"], `synthesis ${i}`,
          { confidenceScore: i * 0.1 + 0.4 },
        );
        db.parliamentVerdicts.updateConfidence(id, i * 0.1 + 0.4, "reason");
      }
      const results = db.parliamentVerdicts.findRelated("GraphQL API", 2);
      expect(results.length).toBeLessThanOrEqual(2);
      if (results.length === 2) {
        expect(results[0].confidenceScore).toBeGreaterThanOrEqual(results[1].confidenceScore);
      }
    } finally { cleanup(); }
  });
});
```

- [ ] **Step 2: Run test**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -10
```

Expected: PASS (findRelated already fixed in Task 1).

- [ ] **Step 3: Update the recall block in `src/parliament/orchestrator.ts`**

Find the recall block starting at `// ── E3: Parliament recall` (line ~72). Replace the `validatedVerdicts` filter and injection:

```typescript
if (this.db) {
  try {
    const pastVerdicts = this.db.parliamentVerdicts.findRelated(config.topic, 2);
    if (pastVerdicts.length > 0) {
      const verdictBlock =
        "\n[Past Parliament decisions on similar topics (highest confidence first)]:\n" +
        pastVerdicts
          .map(
            (v) =>
              `  • "${v.topic.slice(0, 80)}" → ${v.verdict}` +
              ` (confidence: ${v.confidenceScore.toFixed(2)})` +
              (v.agentCitations ? ` | Cited: ${v.agentCitations.slice(0, 80)}` : "") +
              (v.synthesis ? `: ${v.synthesis.slice(0, 100)}` : ""),
          )
          .join("\n") + "\n";
      session.config.contextMessages = [
        ...session.config.contextMessages,
        { role: "system" as const, content: verdictBlock },
      ];
      log.engine.info(
        `[Parliament] Injected ${pastVerdicts.length} past verdict(s) for recall (top-2 by confidence)`,
      );
    }
  } catch (err) {
    log.parliament.warn("parliament verdict recall failed", err);
  }
}
```

- [ ] **Step 4: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep orchestrator
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/parliament/orchestrator.ts __tests__/parliament/validation.test.ts
git commit -m "feat(parliament): recall injects top-2 verdicts by confidence_score with TTL filter"
```

---

### Task 5: 3-sentence audit summary emitted to user

**Files:**
- Modify: `src/tools/parliament.ts`
- Test: `__tests__/parliament/validation.test.ts`

Context: After `orchestrator.formatSessionMarkdown(session)` in `parliament.ts` (line ~130), we construct a 3-sentence summary and emit it via `onProgress` if available. The summary format: "Parliament debated [topic]. [AgentName] argued [FOR/AGAINST], [AgentName] argued [opposing]. Verdict: [VERDICT] — [citation or synthesizer's summary sentence]."

- [ ] **Step 1: Write test for audit summary generation**

Add to `__tests__/parliament/validation.test.ts`:

```typescript
import { buildAuditSummary } from "../../src/tools/parliament.js";
import type { ParliamentSession } from "../../src/parliament/protocol.js";

describe("buildAuditSummary", () => {
  it("produces a 3-sentence summary from session data", () => {
    const session: Partial<ParliamentSession> = {
      config: { topic: "Should we use GraphQL?", participants: [], contextMessages: [] },
      positions: [
        { owlName: "Winston", owlEmoji: "🏗️", position: "FOR", argument: "Better developer experience." },
        { owlName: "Mary", owlEmoji: "📊", position: "AGAINST", argument: "Adds complexity for simple APIs." },
      ],
      verdict: "HOLD",
      agentCitations: "Winston — because his DX argument was most compelling.",
    };
    const summary = buildAuditSummary(session as ParliamentSession);
    expect(summary).toContain("GraphQL");
    expect(summary).toContain("Winston");
    expect(summary).toContain("HOLD");
    expect(summary.split(".").filter(s => s.trim().length > 0).length).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | grep -E "FAIL|buildAudit"
```

Expected: FAIL — `buildAuditSummary` is not exported.

- [ ] **Step 3: Export `buildAuditSummary` from `src/tools/parliament.ts`**

Add this exported function before the `SummonParliamentTool` class:

```typescript
export function buildAuditSummary(session: ParliamentSession): string {
  const topic = session.config.topic.slice(0, 100);

  // Sentence 1: what was debated
  const s1 = `Parliament debated: "${topic}."`;

  // Sentence 2: two most opposing positions
  const forPos = session.positions.find((p) => p.position === "FOR" || p.position === "CONDITIONAL");
  const againstPos = session.positions.find((p) => p.position === "AGAINST");
  let s2: string;
  if (forPos && againstPos) {
    s2 = `${forPos.owlEmoji} ${forPos.owlName} argued FOR; ${againstPos.owlEmoji} ${againstPos.owlName} argued AGAINST.`;
  } else if (session.positions.length >= 2) {
    const [a, b] = session.positions;
    s2 = `${a.owlEmoji} ${a.owlName} said [${a.position}]; ${b.owlEmoji} ${b.owlName} said [${b.position}].`;
  } else {
    s2 = `${session.positions.length} position(s) presented.`;
  }

  // Sentence 3: verdict + citation
  const verdict = session.verdict ?? "PENDING";
  const citationNote = session.agentCitations
    ? ` (Cited: ${session.agentCitations.slice(0, 120)})`
    : "";
  const s3 = `Verdict: **${verdict}**${citationNote}.`;

  return [s1, s2, s3].join(" ");
}
```

Also import `ParliamentSession` at the top of `parliament.ts` if not already:

```typescript
import type { ParliamentSession } from "../parliament/protocol.js";
```

- [ ] **Step 4: Emit audit summary in `execute()` after session completes**

In the `execute()` method of `SummonParliamentTool`, find the line `return orchestrator.formatSessionMarkdown(session);` (around line ~130). Replace it:

```typescript
const formatted = orchestrator.formatSessionMarkdown(session);

// Emit 3-sentence audit summary for user awareness
if (onProgress) {
  const summary = buildAuditSummary(session);
  await onProgress(`\n📋 **Parliament Audit Summary**\n${summary}`);
}

return formatted;
```

- [ ] **Step 5: Run all validation tests**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 6: TypeScript check**

```bash
npx tsc --noEmit 2>&1 | grep -E "tools/parliament|validation"
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/tools/parliament.ts __tests__/parliament/validation.test.ts
git commit -m "feat(parliament): buildAuditSummary — 3-sentence audit summary emitted to user after every session"
```

---

### Task 6: Full verification

**Files:** None (verification only)

- [ ] **Step 1: Full TypeScript check**

```bash
npx tsc --noEmit 2>&1
```

Expected: 0 errors.

- [ ] **Step 2: Run the full validation test suite**

```bash
npx vitest run __tests__/parliament/validation.test.ts 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 3: Run all parliament tests**

```bash
npx vitest run __tests__/parliament/ 2>&1 | tail -20
```

Expected: all tests PASS.

- [ ] **Step 4: Run full test suite**

```bash
npm test 2>&1 | tail -20
```

Expected: same pass/fail as before (3349+ tests).

- [ ] **Step 5: Grep to verify no stale `validated` boolean filter remains in orchestrator**

```bash
grep -n "filter.*validated\|\.validated\b" src/parliament/orchestrator.ts
```

Expected: no matches (the old `pastVerdicts.filter((v) => v.validated)` line is gone).

- [ ] **Step 6: Smoke-check the new confidence flow**

```bash
grep -n "confidenceScore\|updateConfidence\|confidence_score\|findRelated" src/memory/db.ts src/parliament/orchestrator.ts | head -30
```

Expected: `confidence_score` in schema, `updateConfidence` method, `findRelated` ordering by confidence, orchestrator passing `confidenceScore` to `record()`.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat(parliament): validation layer complete — confidence score, adversarial validator, audit summary, top-2 recall"
```
