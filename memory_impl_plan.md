# StackOwl — Memory Architecture Implementation Plan

**Goal**: Replace all file-based memory stores with a single SQLite database that all owls share.
Every interaction is recorded, searchable, and linked. Messages are compressed automatically
every 20 turns to keep context costs low. Owls learn from each other. DNA evolution becomes
data-driven.

**One package added**: `better-sqlite3` (synchronous, no server, single file, FTS5 built-in)

---

## What We're Replacing

| Current (files) | New (SQLite table) | What breaks without it |
|---|---|---|
| `sessions/*.json` | `messages` | Message history, session continuity |
| `memory/facts.json` | `facts` + `facts_fts` | Cross-session skill memory |
| `memory/episodes.json` | `episodes` | Past conversation summaries |
| `memory/digests/*.json` | `digests` | L1 in-session working memory |
| `memory/feedback.json` | `feedback` | 👍/👎 signal persistence |
| AttemptLog (RAM only) | `attempts` | Lost on restart — repeats failures |
| `memory/index.json` (reflexion) | retired | Duplicate of facts |
| `memory.md` (consolidator) | retired | Unsearchable append-only log |
| — (new) | `summaries` | Message compression cost savings |
| — (new) | `owl_performance` | Owl evaluation metrics |
| — (new) | `owl_learnings` | Cross-owl shared knowledge |

---

## Phase 1 — SQLite Foundation
**Effort**: 1 day | **Risk**: Low — additive, nothing removed yet

### Install
```
npm install better-sqlite3
npm install -D @types/better-sqlite3
```

### New file: `src/memory/db.ts`
Single `MemoryDatabase` class. Opens `workspace/memory/stackowl.db`.
Creates all tables on first run. Migrates existing JSON files once.

### Tables

```sql
messages        — every turn, all owls (session_id, user_id, owl_name, role, content, seq)
facts           — long-term structured facts (category, confidence, embedding for semantic search)
facts_fts       — FTS5 virtual table over facts (full-text search)
summaries       — compressed message batches (task, key_facts, decisions, failed, open_questions)
episodes        — session-level memories (summary, topics, importance, embedding)
digests         — L1 working memory per session (replaces digests/*.json)
attempts        — tool call log — NOW PERSISTENT (replaces in-memory AttemptLog)
feedback        — 👍/👎 signals with context
owl_performance — per-owl metrics (feedback_like, tool_success, loop_exhausted, etc.)
owl_learnings   — what each owl learned, searchable cross-owl via FTS5
owl_learnings_fts — FTS5 virtual table over owl_learnings
```

### `MemoryDatabase` API
```typescript
db.messages.append(sessionId, userId, owlName, messages[])
db.messages.getSession(sessionId): ChatMessage[]
db.messages.getRecent(sessionId, limit): ChatMessage[]
db.messages.getToday(userId): ChatMessage[]
db.messages.countSession(sessionId): number

db.facts.add(fact, userId, owlName, provider?)     // embeds on write
db.facts.search(query, userId?, limit?): Fact[]    // FTS5 + semantic hybrid
db.facts.getByCategory(userId, category): Fact[]
db.facts.confirm(id) / db.facts.retire(id)

db.summaries.add(summary)
db.summaries.getLatest(sessionId): Summary | null

db.digests.get(sessionId): Digest | null
db.digests.update(sessionId, userId, data): void
db.digests.clear(sessionId): void

db.attempts.record(attempt)
db.attempts.getForSession(sessionId): Attempt[]
db.attempts.getFailures(sessionId): Attempt[]

db.feedback.record(entry)
db.feedback.getRatioForOwl(owlName): number

db.owlPerf.record(owlName, sessionId, userId, metric, topic?)
db.owlPerf.getSummary(owlName, days?): PerfSummary

db.owlLearnings.add(owlName, learning, category, sessionId?)
db.owlLearnings.search(query, limit?): Learning[]   // cross-owl FTS5
db.owlLearnings.getForOwl(owlName): Learning[]
db.owlLearnings.reinforce(id): void
```

### Migration (one-time on startup)
Import existing JSON → DB. JSON files become read-only archives.
- `memory/facts.json` → `facts` table
- `memory/episodes.json` → `episodes` table
- `sessions/*.json` → `messages` table
- `memory/feedback.json` → `feedback` table
- `memory/digests/*.json` → `digests` table

**Files**: CREATE `src/memory/db.ts` | MODIFY `package.json`, `src/gateway/types.ts`

---

## Phase 2 — Message Compression
**Effort**: 1 day | **Depends on**: Phase 1

Every 20 messages → LLM summarizes batch → stores structured JSON in `summaries` table.
Context window gets: latest summary (~300 tokens) + last 10 raw messages (~2,000 tokens).
Was: 50 raw messages (~10,000 tokens). **Saves ~74% on history tokens.**

### New file: `src/memory/compressor.ts`
```typescript
class MessageCompressor {
  async compress(sessionId, userId, owlName, messages, batchSize=20): Promise<Summary>
  // After compression: key_facts → facts table + owl_learnings table
}
```

### Summary JSON shape
```json
{
  "task": "what user was trying to do",
  "accomplished": "what was resolved",
  "key_facts": ["yt-dlp works for Instagram reels"],
  "decisions": ["used MP4 format"],
  "failed_approaches": ["direct URL fetch returned 403"],
  "open_questions": ["user hasn't confirmed quality preference"]
}
```

Every `key_fact` → `facts` table (permanent, searchable)
Every `failed_approach` → `owl_learnings` (category: 'failure')
Every accomplishment → `owl_learnings` (category: 'skill')

### Context builder change
Replace `session.messages` (up to 50) with: `db.summaries.getLatest()` + `db.messages.getRecent(10)`

**Files**: CREATE `src/memory/compressor.ts` | MODIFY `post-processor.ts`, `context-builder.ts`

---

## Phase 3 — Migrate Existing Stores
**Effort**: 1–2 days | **Depends on**: Phase 1

Replace all JSON-based stores with DB queries. Same public interfaces → callers unchanged.

Order (safest first):
1. DigestManager → `db.digests`
2. FeedbackStore → `db.feedback`
3. AttemptLogRegistry → `db.attempts` (**now persists across restarts**)
4. FactStore → `db.facts` + FTS5 (replaces custom keyword scoring)
5. SessionStore → `db.messages` (biggest change)
6. EpisodicMemory → `db.episodes`

**Files**: MODIFY `store.ts`, `fact-store.ts`, `episodic.ts`, `conversation-digest.ts`,
`attempt-log.ts`, `feedback/store.ts`

---

## Phase 4 — Owl Performance Recording
**Effort**: 1 day | **Depends on**: Phase 1

Wire every interaction outcome to `owl_performance` and `owl_learnings`.

| Event | Where wired | Written to |
|---|---|---|
| 👍/👎 | `gateway/core.ts` `recordFeedback()` | `owl_performance` + `feedback` |
| Tool success/failure | `post-processor.ts` | `owl_performance` |
| Loop exhausted | `post-processor.ts` | `owl_performance` |
| `remember()` tool call | `tools/remember.ts` | `facts` + `owl_learnings` |
| Compression batch | `compressor.ts` | `owl_learnings` (skills + failures) |

Enables self-evaluation query:
```sql
SELECT owl_name,
  AVG(CASE WHEN metric='feedback_like' THEN 1.0 ELSE 0.0 END) as like_ratio,
  AVG(CASE WHEN metric='tool_failure'  THEN 1.0 ELSE 0.0 END) as failure_rate
FROM owl_performance GROUP BY owl_name
```

**Files**: MODIFY `post-processor.ts`, `gateway/core.ts`, `tools/remember.ts`

---

## Phase 5 — Data-Driven DNA Evolution
**Effort**: 1 day | **Depends on**: Phase 4

Replace LLM-guessed mutations with DB-backed performance data.

### Evolution input (before: raw messages, after: structured metrics)
```typescript
const perf = db.owlPerf.getSummary(owlName, 30)
const myLearnings = db.owlLearnings.getForOwl(owlName)
const crossOwl = db.owlLearnings.search(recentTopics)  // what OTHER owls know

// Evolution prompt now includes real numbers:
// likeRatio: 0.78, toolSuccessRate: 0.91, loopExhaustionRate: 0.04
// topStrengths: ['research', 'code review']
// topWeaknesses: ['media downloads', 'sysadmin']
// otherOwlsKnow: ['Horus learned 3 new media download approaches']
```

### Cross-owl knowledge injection in context builder
```typescript
const crossOwl = await db.owlLearnings.search(userMessage, 3)
// adds to enrichedMemoryContext: "Other owls have learned: [...]"
```

All owls benefit from each other's experience without sharing personality/DNA.

**Files**: MODIFY `src/owls/evolution.ts`, `context-builder.ts`

---

## Phase 6 — Parliament DB Integration
**Effort**: 0.5 days | **Depends on**: Phase 4

Before debate: inject each owl's relevant learnings + cross-owl knowledge.
After debate: write Knowledge Pellet back to `owl_learnings` for ALL participants.

```typescript
// Before debate
for (const owl of participants) {
  const learnings = db.owlLearnings.search(topic, 5)
  owl.context += formatLearnings(learnings)
}

// After debate
for (const owl of participants) {
  db.owlLearnings.add(owl.name, pellet.insight, 'insight', sessionId)
}
```

**Files**: MODIFY `src/parliament/orchestrator.ts`

---

## Timeline

| Phase | What | Effort |
|---|---|---|
| 1 | SQLite foundation + all tables + migration | 1 day |
| 2 | Message compression + context assembly | 1 day |
| 3 | Migrate all stores to DB | 1–2 days |
| 4 | Owl performance recording | 1 day |
| 5 | Data-driven DNA evolution + cross-owl knowledge | 1 day |
| 6 | Parliament DB integration | 0.5 days |
| **Total** | | **~6 days** |
