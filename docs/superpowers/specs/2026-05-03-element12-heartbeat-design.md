# Element 12 — Heartbeat: Goal-Anchored Proactive Delivery

**Status:** Design approved 2026-05-03  
**Schema version:** v22 (merges proactive-jobs.db + 3 new tables)  
**Replaces:** ad-hoc delivery in `proactive.ts`, dead `consolidation.ts`

---

## Problem

StackOwl's proactive delivery system has four compounding failures:

1. **Silent drop.** `proactive.ts:842` drops pings with `console.warn` when EventBus is unavailable. Jobs disappear with no retry, no record, no user awareness.
2. **No goal anchoring.** Proactive messages are generated from templates with no check that the message advances an active user goal. Noise accumulates; users tune out.
3. **Dead feedback loop.** Delivery outcomes are never recorded. `AutonomousPlanner` priorities are hardcoded constants (80, 70, 90…) that never update based on what the user actually responds to.
4. **Split DB and dead code.** `ProactiveJobQueue` lives in a separate `proactive-jobs.db` file, preventing joins with trajectory history. `MemoryConsolidator` reads `.owl_sessions/*.json` files that haven't been written since E3.

---

## Architecture: Two-Layer Contract

Element 12 draws one explicit boundary:

```
CognitiveLoop          →   ProactiveJobQueue   →   ProactivePinger
(decides what to do)       (durable handoff)       (verifies + assembles + delivers)
```

**CognitiveLoop** owns all background self-improvement decisions. When a completed action produces something worth telling the user, it enqueues a typed job into `ProactiveJobQueue`. That is the only coupling point between the two layers.

**ProactivePinger** owns delivery: consume jobs → `DeliveryVerifier` → assemble via `ContextPipeline` → deliver via EventBus → record outcome. It does not decide what background work to run.

**AutonomousPlanner** remains as the scheduling oracle for user-facing jobs (goal follow-ups, morning briefs, check-ins). It enqueues into the same `ProactiveJobQueue` and is subject to the same delivery verification.

This boundary makes each layer independently testable.

---

## Component 1 — DeliveryVerifier (`src/heartbeat/delivery-verifier.ts`)

A single cheap-tier LLM call (via `IntelligenceRouter.resolve("classification")`) that runs before every delivery. Returns:

```typescript
interface VerificationResult {
  verdict: "ADVANCES" | "NEUTRAL" | "NOISE";
  reason: string;
  suppressUntil?: Date;  // only on NEUTRAL
}
```

**Verdicts:**
- `ADVANCES` — deliver immediately.
- `NEUTRAL` — suppress and requeue at `priority - 15`, `scheduledFor = now + 2h`. If a job is suppressed 3× consecutively, escalate: deliver a single "I've been holding back on [topic] — want me to share?" message (max once per topic per 24h).
- `NOISE` — discard. Write `proactive_deliveries` row with `status = "discarded"`, log reason. No user output.

**Skip rules (to stay under 400ms p95):**
1. Job has a `goalId` already verified by the planner → skip (already classified).
2. Job type is `morning_brief` → always deliver during its window.
3. User idle > 4h and job priority ≥ 70 → always deliver.

**EventBus-absent path:** If EventBus is unavailable, `DeliveryVerifier` has not yet run — the job remains `pending` and is retried on the next 30s tick. This eliminates the silent drop at `proactive.ts:842`.

**Latency budget:** p95 < 400ms (same contract as E7's GoalAnchoredVerifier).

---

## Component 2 — Data Model (Schema v22)

### Migration: proactive-jobs.db → stackowl.db

On startup, if `proactive-jobs.db` exists:
1. Read all rows.
2. Insert into new `proactive_jobs` table in `stackowl.db`.
3. Rename `proactive-jobs.db` → `proactive-jobs.db.bak`.

Rollback: rename `.bak` back. `ProactiveJobQueue` is updated to use the main DB connection. No user-visible impact.

### New tables

```sql
-- Migrated from proactive-jobs.db
CREATE TABLE proactive_jobs (
  id           TEXT PRIMARY KEY,
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,          -- JSON
  priority     INTEGER NOT NULL DEFAULT 50,
  status       TEXT NOT NULL DEFAULT 'pending',
                                       -- pending|running|done|failed|discarded
  goal_id      TEXT,
  scheduled_for TEXT,                  -- ISO8601, null = immediate
  suppress_count INTEGER DEFAULT 0,
  retry_count    INTEGER DEFAULT 0,   -- EventBus delivery retry counter (max 3)
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  error        TEXT
);
CREATE INDEX idx_proactive_jobs_status
  ON proactive_jobs(status, priority DESC, scheduled_for);

-- Delivery outcomes (one row per delivery attempt)
CREATE TABLE proactive_deliveries (
  id             TEXT PRIMARY KEY,
  job_id         TEXT NOT NULL REFERENCES proactive_jobs(id),
  channel        TEXT NOT NULL,        -- telegram|cli|slack
  user_id        TEXT NOT NULL,
  message_preview TEXT,                -- first 100 chars for debugging
  verdict        TEXT NOT NULL,        -- ADVANCES|NEUTRAL|NOISE|skipped_check
  delivered_at   TEXT,                 -- null if not delivered
  status         TEXT NOT NULL,        -- delivered|discarded|suppressed|failed
  user_replied_at TEXT,                -- null until user responds
  created_at     TEXT NOT NULL
);

-- Engagement signal (written when user replies to a proactive message)
CREATE TABLE proactive_engagement (
  id                    TEXT PRIMARY KEY,
  delivery_id           TEXT NOT NULL REFERENCES proactive_deliveries(id),
  job_type              TEXT NOT NULL,
  goal_id               TEXT,
  replied               BOOLEAN NOT NULL,
  reply_latency_seconds INTEGER,       -- null if no reply
  created_at            TEXT NOT NULL
);
```

`proactive_engagement` feeds `AutonomousPlanner`: reply rate per job type over 30 days becomes the learned priority score. Cold-start floor values (30–90) remain until ≥ 20 delivery samples exist per type.

---

## Component 3 — ProactivePinger Rewrite (`src/heartbeat/proactive.ts`)

The 30s worker tick and job-queue consumer are kept. Four changes:

### 3a — Silent drop fix
Replace `proactive.ts:842` `console.warn` + drop with structured retry: if EventBus unavailable, set job `status = "pending"`, `scheduled_for = now + 60s`. Write a `proactive_deliveries` row with `status = "failed"` only after 3 consecutive retry failures on the same job.

### 3b — Message assembly via ContextPipeline
Replace `buildProactiveMessage()` string templates with a `ContextPipeline` pass:
- Layer 1 (priority 100): job payload (type + goal context)
- Layer 2 (priority 80): user's active goals from GoalGraph
- Layer 3 (priority 60): owl DNA (verbosity, tone from `learnedPreferences`)

Pipeline assembles and truncates to channel limits (4096 Telegram, 2000 Slack). Proactive messages become goal-aware by construction.

### 3c — Delivery outcome recording
After every delivery attempt, write a `proactive_deliveries` row. When the channel adapter receives a user reply to a proactive message (matched by message ID or session context), write a `proactive_engagement` row. Closes the feedback loop to `AutonomousPlanner`.

### 3d — Dead stub removal
Delete: `maybeDream()`, `maybeKnowledgeCouncil()`, `maybeEvolveSkills()` (all stubs returning immediately — CognitiveLoop owns these), `maybeConsolidateMemory()` (reads dead `.owl_sessions/*.json`).

**Result:** `proactive.ts` shrinks from ~850 lines to ~400 lines with one clear responsibility.

---

## Component 4 — AutonomousPlanner: Learned Priorities (`src/heartbeat/planner.ts`)

GoalGraph integration (`getStale`, `getBlocked`) is unchanged. Only priority scoring changes.

### Learned scoring function

```typescript
async function learnedPriority(type: ActionType, basePriority: number): Promise<number> {
  const stats = await db.getEngagementStats(type, { days: 30, minSamples: 20 });
  if (!stats) return basePriority;           // cold start: use constant
  const learned = Math.round(stats.replyRate * 100);
  return Math.max(basePriority - 20, Math.min(basePriority + 20, learned));
}
```

Hardcoded constants become `basePriority` anchors (floor/ceiling ±20), not final scores. After 20 delivery samples per job type, scoring is fully data-driven within that band.

### New action type: `goal_progress_update`
When CognitiveLoop completes a study session or reflexion cycle tied to a goal, it enqueues `goal_progress_update` with `goalId`. The planner scores it using engagement data for that specific goal (not aggregate `self_study` rate). This is the goal-anchored proactive scheduling identified in Phase 1 competitive research.

---

## Component 5 — CapabilityScanner fix (`src/heartbeat/capability-scanner.ts`)

Replace hardcoded `importantTools` array with a query against `tool_executions` (top 15 by `selectionCount` in last 30 days). One function change; no structural change.

---

## Dead Code Removal

| File | Action | Reason |
|------|--------|--------|
| `src/heartbeat/consolidation.ts` | **Delete** | Reads `.owl_sessions/*.json` which haven't been written since E3. CognitiveLoop's `memory_consolidation` action covers this correctly via IntelligenceRouter. |
| `proactive.ts` stubs | **Delete** | `maybeDream`, `maybeKnowledgeCouncil`, `maybeEvolveSkills`, `maybeConsolidateMemory` — all return immediately, CognitiveLoop owns these. |

---

## Files Modified / Added

| File | Change |
|------|--------|
| `src/heartbeat/delivery-verifier.ts` | **New** — DeliveryVerifier class |
| `src/heartbeat/proactive.ts` | **Rewrite** — silent drop fix, ContextPipeline assembly, delivery recording, dead stubs removed |
| `src/heartbeat/planner.ts` | **Extend** — learned priority scoring, new `goal_progress_update` action type |
| `src/heartbeat/job-queue.ts` | **Extend** — migrate to main DB connection, add delivery FK |
| `src/heartbeat/capability-scanner.ts` | **Extend** — replace hardcoded importantTools with DB query |
| `src/heartbeat/consolidation.ts` | **Delete** |
| `src/memory/db.ts` | **Extend** — schema v22, 3 new tables, migration script, `getEngagementStats()` query |
| `src/cognition/loop.ts` | **Extend** — enqueue `goal_progress_update` job when goal-tied action completes |

---

## Intelligence-First Compliance

| Violation | Fix |
|-----------|-----|
| `consolidation.ts:94` direct `engine.run()` call | File deleted — CognitiveLoop uses IntelligenceRouter |
| `planner.ts` hardcoded numeric priorities | Replaced with `learnedPriority()` data-driven scoring |
| `capability-scanner.ts` hardcoded `importantTools` | Replaced with `tool_executions` DB query |
| `proactive.ts:842` silent drop | Replaced with structured retry + `proactive_deliveries` record |

---

## Testing Strategy

All new components are test-driven. Estimated +40 tests on top of existing heartbeat coverage.

| Component | Required test cases |
|-----------|-------------------|
| `DeliveryVerifier` | `ADVANCES` / `NEUTRAL` / `NOISE` verdicts; 3 skip-rule cases; EventBus-absent retry path; 3× suppress → escalate |
| `ProactivePinger` | Delivery outcome recording; suppression requeue; ContextPipeline assembly; confirmed no `maybeDream` call paths |
| `AutonomousPlanner` | Learned priority with ≥20 samples; cold-start fallback constants; `goal_progress_update` per-goal scoring |
| DB migration (v22) | `proactive-jobs.db` present → migrated and renamed `.bak`; absent → no-op |
| `CapabilityScanner` | `importantTools` derived from `tool_executions` query result, not array literal |

---

## Phase 1 Competitive Research Summary

- **arXiv 2604.14178** (Heartbeat-Driven Cognitive Scheduling): learned heartbeat cadence from historical interaction logs outperforms fixed schedules — directly motivates `proactive_engagement` table + `learnedPriority()`.
- **arXiv 2410.12361** (Proactive Agent): F1 66.47% via human-labeled reward model. Goal-anchored delivery (DeliveryVerifier) is the practical equivalent without human labeling.
- **arXiv 2602.04482** (ProAgentBench): long-term memory + historical context are critical factors; burstiness B=0.787 in real user data shows synthetic cadence assumptions are wrong — motivates data-driven priority scoring over hardcoded constants.
- **Production failures** (AutoGPT loop bugs, LangGraph silent drops, OpenAI Tasks complaints): all share the same root cause — no delivery outcome recording, no goal-advance check before sending. DeliveryVerifier + `proactive_deliveries` table directly address this.

---

## Verification Checklist (before merge)

- [ ] `npm test` — all existing tests pass; +40 new tests pass
- [ ] `proactive-jobs.db` migration verified: in-flight jobs survive restart
- [ ] `DeliveryVerifier.verify()` p95 < 400ms under load
- [ ] Proactive message assembled via ContextPipeline includes active goal context
- [ ] `maybeDream`, `maybeKnowledgeCouncil`, `maybeEvolveSkills`, `maybeConsolidateMemory` absent from compiled output
- [ ] `consolidation.ts` absent from repo
- [ ] `CapabilityScanner.importantTools` absent from compiled output (no array literal)
- [ ] `proactive_deliveries` row written for every delivery attempt (success and failure)
- [ ] `proactive_engagement` row written when user replies to a proactive message
