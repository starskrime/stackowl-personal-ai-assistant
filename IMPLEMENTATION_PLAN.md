# Implementation Plan: Human-Like Cognition

> Based on `ai_not_yet_human.md` v2 specification
> Each phase is self-contained — delivers user-visible improvement before the next phase starts

---

## Phase 0: Activate Dormant Systems

**Goal:** Wire up 4 fully-implemented but never-activated subsystems. Zero new classes. Pure integration.

**Estimated changes:** ~120 lines across 3 files

### Task 0.1 — Activate `detectTopicSwitch()` in message flow

**File:** `src/gateway/core.ts`
**Where:** Inside `handleCore()`, after session is loaded (~line 1923 area where topic switch logic already exists)
**What:**
- The gateway already has inline topic-switch detection with the same keywords as `SessionManager.detectTopicSwitch()`
- Verify it's actually being used (check if the result influences context or session)
- If the detection result is computed but discarded, wire it into WorkingContext and session metadata
- On detection: set a flag in session metadata so context-builder can react

**Acceptance:** When user says "new topic" or "fresh start", the system clears working context and adds a separator directive.

### Task 0.2 — Activate `WorkingContext` injection into system prompt

**File:** `src/gateway/handlers/context-builder.ts`
**Where:** After intent context injection (~line 161), before the final merge (~line 241)
**What:**
- Get `WorkingContextManager` from gateway context
- Call `workingContextManager.get(sessionId).toContextString()`
- Append to `enrichedMemoryContext` alongside intent/fact/episodic context
- Guard: skip if `toContextString()` returns empty

**File:** `src/gateway/core.ts`
**Where:** After continuity/topic detection, before context building
**What:**
- On each message: `workingContext.setLastUserMessage(message.content)`
- On each response: `workingContext.setLastOwlResponse(response.content)`
- On topic switch detection: `workingContext.setCurrentTopic(detectedTopic)` or clear it

**Acceptance:** `WorkingContext.toContextString()` appears in LLM system prompt when topic/intent are set.

### Task 0.3 — Activate `IntentStateMachine.create()` from user messages

**File:** `src/gateway/core.ts`
**Where:** Post-response processing area (~line 1911), after the response is generated
**What:**
- After each user message + response cycle:
  - If no active intent for this session (`intentStateMachine.getActiveForSession(sessionId).length === 0`):
    - AND message looks task-oriented (contains verbs like "set up", "create", "fix", "help me", "I want to", "can you"):
    - Call `intentStateMachine.create({ description: <extracted from message>, rawQuery: message.content, type: <inferred>, sessionId })`
  - If active intent exists:
    - Call `intentStateMachine.touch(activeIntent.id)` to update `lastActiveAt`
  - If topic switch was detected:
    - Call `intentStateMachine.transition(activeIntent.id, "paused", "Topic switch detected")`

**Intent type detection (simple heuristic, no LLM):**
```
"task"        — contains action verbs (set up, create, fix, build, configure, install)
"question"    — ends with "?" or starts with "what/how/why/when/where/who"
"information" — contains "tell me", "explain", "what is"
"exploration" — contains "let's think", "brainstorm", "ideas", "what if"
```

**Acceptance:** After 3+ messages on one topic, `IntentStateMachine.toContextString()` shows an active intent with description in the system prompt.

### Task 0.4 — Activate `CommitmentTracker.track()` from owl responses

**File:** `src/gateway/core.ts`
**Where:** Post-response processing, after response is generated
**What:**
- Scan owl response text for commitment patterns (regex, no LLM):
  ```
  /\b(?:i(?:'ll| will)\s+(?:remind|check|look into|get back|follow up|send|prepare|update))\b/i
  /\b(?:tomorrow|later today|next time|in \d+ (?:minutes?|hours?|days?))\b/i  — for deadline extraction
  /\b(?:let me (?:know|check|find|look))\b/i
  ```
- On match: extract the commitment statement and optional deadline
- Call `commitmentTracker.track({ intentId: activeIntent?.id, sessionId, statement: matchedText, deadline: extractedDeadline })`
- Cap: max 3 commitments per session to avoid noise

**Acceptance:** When owl says "I'll check on that tomorrow", a commitment appears in tracker. ProactiveIntentionLoop can act on it.

### Task 0.5 — Activate stale intent detection

**File:** `src/gateway/core.ts`
**Where:** In `handleCore()`, early in the flow (before context building)
**What:**
- On each message: check `intentStateMachine.getStale()` (30min threshold)
- For each stale intent: `intentStateMachine.transition(intent.id, "abandoned", "Stale — no activity for 30+ minutes")`
- This prevents zombie intents from accumulating

**Acceptance:** Intents that go untouched for 30min auto-transition to "abandoned" and stop appearing in context.

---

## Phase 1: Temporal Awareness Layer

**Goal:** Inject current date/time/timezone and session gap information into every system prompt. Zero LLM cost.

**Estimated changes:** ~150 lines — 1 new file, 2 modified files

### Task 1.1 — Create `TemporalContext` module

**New file:** `src/cognition/temporal-context.ts`
**What:**
```typescript
interface TemporalSnapshot {
  now: Date;
  timezone: string;
  dayOfWeek: string;           // "Monday"
  timeOfDay: string;           // "morning" | "afternoon" | "evening" | "night"
  sessionAge: string;          // "12 minutes" | "2 hours"
  lastMessageGap: string;      // "45 seconds" | "3 hours"
  lastSessionGap: string;      // "Yesterday at 9:15 PM" | null
  lastSessionTopic: string;    // Brief topic from prior session | null
  dayContext: string;           // "weekday morning" | "weekend evening"
  isReturningUser: boolean;    // gap > 4 hours
}

function computeTemporalContext(
  session: Session,
  previousSession: Session | null,
  timezone: string
): TemporalSnapshot

function formatForSystemPrompt(snapshot: TemporalSnapshot): string
```

**Key logic:**
- `timeOfDay`: 5-11 = morning, 11-17 = afternoon, 17-21 = evening, 21-5 = night
- `sessionAge`: diff between `session.metadata.startedAt` and now
- `lastMessageGap`: diff between last message timestamp and now
- `lastSessionGap`: requires loading previous session from `SessionStore.list()` — find most recent session that isn't the current one
- `isReturningUser`: `lastMessageGap > 4 hours` OR `lastSessionGap > 4 hours`
- `lastSessionTopic`: take last 3 messages from previous session, extract topic (keyword extraction, no LLM — use most frequent nouns/verbs)

**Output format:**
```
## Temporal Context
Current time: Tuesday, March 29, 2026 at 3:47 PM (Europe/Baku)
Session started: 12 minutes ago
Last user message: 45 seconds ago
Previous session: Yesterday at 9:15 PM (topic: AI news monitoring)
Note: User is returning after 16 hours — acknowledge the gap naturally.
```

### Task 1.2 — Inject temporal context into system prompt

**File:** `src/engine/runtime.ts`
**Where:** In `buildSystemPrompt()`, as the FIRST injected section (before persona/DNA, ~line 1359)
**What:**
- Import `computeTemporalContext` and `formatForSystemPrompt`
- Compute snapshot from current session + previous session
- Prepend to system prompt

**Why first:** Temporal awareness should frame everything else. The LLM should know "it's Tuesday afternoon and the user was here yesterday" before reading persona, skills, or memory.

### Task 1.3 — Detect returning users and inject welcome-back context

**File:** `src/gateway/core.ts`
**Where:** In `handleCore()`, after session load, before context building
**What:**
- If `isReturningUser` (gap > 4h):
  - Load previous session's last 5 messages
  - Extract brief topic summary (TF-IDF keyword extraction, no LLM)
  - Set `workingContext.setCurrentTopic("Returning after gap — previous: {topic}")` (requires Phase 0.2)
  - This naturally flows into the system prompt via WorkingContext injection

### Task 1.4 — Configure timezone

**File:** `src/config/loader.ts`
**What:**
- Add `timezone` field to config schema (default: `"UTC"`)
- `start.sh` can prompt for timezone during setup
- Fallback: detect from system via `Intl.DateTimeFormat().resolvedOptions().timeZone`

**Acceptance criteria:**
- System prompt contains current date, time, timezone on every message
- When user returns after 8+ hours, system prompt includes previous session topic
- Zero additional LLM calls
- Latency overhead < 5ms

---

## Phase 2: Conversation Continuity Engine

**Goal:** 3-layer detection that classifies each message as CONTINUATION, FOLLOW_UP, TOPIC_SWITCH, or FRESH_START. Drives intent lifecycle and context strategy.

**Estimated changes:** ~300 lines — 1 new file, 2 modified files

### Task 2.1 — Create `ContinuityEngine` module

**New file:** `src/cognition/continuity-engine.ts`
**What:**
```typescript
type ContinuityClass = "CONTINUATION" | "FOLLOW_UP" | "TOPIC_SWITCH" | "FRESH_START";

interface ContinuityResult {
  classification: ContinuityClass;
  confidence: number;            // 0-1
  reason: string;                // Human-readable explanation for debugging
  layerUsed: 1 | 2 | 3;         // Which layer determined the result
  priorTopicSummary?: string;    // Set on TOPIC_SWITCH / FRESH_START
}

class ContinuityEngine {
  classify(
    message: string,
    session: Session,
    temporalSnapshot: TemporalSnapshot,
    provider?: AIProvider  // Only needed for Layer 3
  ): Promise<ContinuityResult>
}
```

**Layer 1 — Temporal Signal (instant):**
```
gap < 5min    → LIKELY_CONTINUATION (confidence 0.7)
5-30min       → POSSIBLE_CONTINUATION (confidence 0.4)  → needs Layer 2
30min-4h      → POSSIBLE_SWITCH (confidence 0.5)        → needs Layer 2
4h-24h        → LIKELY_NEW (confidence 0.6)              → needs Layer 2
> 24h         → DEFINITELY_NEW (confidence 0.9)
```

**Layer 2 — Linguistic Signal (instant):**
```
Continuation markers (boost confidence toward CONTINUATION):
  - Anaphora: /\b(it|that|this|these|those|the thing)\b/ in first 20 chars
  - Sequence: /\b(also|and|next|another|plus|additionally)\b/ at start
  - Reference: /\b(about that|regarding|as for|back to)\b/
  - Explicit: /\b(continue|continuing|where were we|as I was saying)\b/

Break markers (boost confidence toward TOPIC_SWITCH/FRESH_START):
  - Greeting: /^(hi|hello|hey|good morning|good evening)\b/i (standalone, not mid-sentence)
  - Reset: /\b(new topic|different question|unrelated|by the way|btw)\b/
  - Explicit: /\b(new question|something else|forget that|start over)\b/
```

Combine Layer 1 + Layer 2 scores. If the combined result has confidence >= 0.7, return without Layer 3.

**Layer 3 — Semantic Coherence (1 fast LLM call, only when ambiguous):**
- Invoked when confidence < 0.7 after Layers 1+2
- Estimated ~20% of messages need this
- Use fastest available model (Haiku-class)
- Prompt:
  ```
  Last 3 messages: {last3}
  New message: {newMessage}
  Classify: A) CONTINUATION B) FOLLOW_UP C) TOPIC_SWITCH D) FRESH_START
  Return one letter.
  ```
- Timeout: 2 seconds. On timeout: fall back to Layer 1+2 result.

### Task 2.2 — Integrate continuity into message flow

**File:** `src/gateway/core.ts`
**Where:** In `handleCore()`, after temporal context computation, before context building
**What:**
- Call `continuityEngine.classify(message, session, temporalSnapshot, fastProvider)`
- Based on result:

| Classification | Action |
|---|---|
| CONTINUATION | `intentStateMachine.touch(activeIntent)` |
| FOLLOW_UP | `intentStateMachine.touch(activeIntent)`, inject "Building on: {topic}" |
| TOPIC_SWITCH | `intentStateMachine.transition(active, "paused")`, `create()` new, inject "Previous topic: {summary}" |
| FRESH_START | Clear WorkingContext, `intentStateMachine.transition(active, "paused")`, inject prior session summary |

### Task 2.3 — Replace hardcoded topic switch detection

**File:** `src/gateway/core.ts`
**Where:** Remove/replace the inline keyword detection (~line 1923) and `SessionManager.detectTopicSwitch()`
**What:**
- Remove the 10-keyword detection
- Route through `ContinuityEngine.classify()` instead
- The linguistic markers in Layer 2 subsume all existing keywords plus many more

**Acceptance criteria:**
- Messages after <5min gap with anaphora ("it", "that") correctly classified as CONTINUATION
- Messages after 8h gap with greeting correctly classified as FRESH_START
- Messages with "btw" or "by the way" correctly classified as TOPIC_SWITCH
- Ambiguous messages trigger Layer 3 (fast LLM call) <20% of the time
- Layer 3 timeout doesn't block response (falls back to Layer 1+2)

---

## Phase 3: Session Segmentation + Episodic Memory

**Goal:** Break infinite Telegram sessions into logical segments, trigger episode extraction, enable retrieval with Park et al. scoring.

**Estimated changes:** ~250 lines — 1 new file, 3 modified files

### Task 3.1 — Implement session segmentation

**New file:** `src/memory/session-segmenter.ts`
**What:**
```typescript
interface SessionSegment {
  startIndex: number;          // Index into session.messages[]
  endIndex: number;
  startedAt: number;           // Timestamp
  endedAt: number;
  topic: string;               // Extracted from messages (TF-IDF, no LLM)
  messageCount: number;
}

class SessionSegmenter {
  segment(session: Session): SessionSegment[]
  getCurrentSegmentStart(session: Session): number
}
```

**Segmentation rule:** A new segment starts when:
- Gap between consecutive messages > 30 minutes
- OR a TOPIC_SWITCH/FRESH_START was classified by ContinuityEngine (requires Phase 2)

**On segment boundary:**
1. Mark the boundary in session metadata (add `segmentBoundaries: number[]` to Session.metadata)
2. Trigger episodic extraction for the completed segment
3. New messages continue in the same session file (backward compat)

### Task 3.2 — Trigger episodic extraction on segment close

**File:** `src/gateway/core.ts`
**Where:** After continuity classification, when a gap > 30min is detected
**What:**
- If `lastMessageGap > 30min` AND current session has messages since last segment boundary:
  - Extract the completed segment's messages
  - Call `episodicMemory.extractFromSession(segmentMessages, owlName)` (fire-and-forget, don't block response)
  - Update session metadata with new segment boundary
- Also trigger on TOPIC_SWITCH classification from ContinuityEngine

**File:** `src/memory/episodic.ts`
**Where:** In `extractFromSession()` method
**What:**
- Add importance scoring to extracted episodes:
  ```
  importance = 0.3 (baseline)
  + 0.2 if contains decisions/commitments
  + 0.2 if multi-turn (5+ exchanges)
  + 0.1 if contains strong sentiment
  + 0.1 if user asked follow-up questions (engagement signal)
  ```
- Store importance score in Episode interface

### Task 3.3 — Implement Park et al. retrieval scoring

**File:** `src/memory/episodic.ts`
**Where:** In `search()` method — augment existing search
**What:**
- Current search: keyword + cosine similarity
- Add retrieval scoring:
  ```typescript
  function retrievalScore(episode: Episode, query: string, now: Date): number {
    const hoursSince = (now.getTime() - new Date(episode.date).getTime()) / 3600000;
    const recency = Math.pow(0.99, hoursSince);
    const importance = episode.importance ?? 0.5;
    const relevance = cosineSimilarity(queryEmbedding, episode.embedding);
    return recency + importance + relevance;
  }
  ```
- Sort results by `retrievalScore` descending instead of pure relevance
- Threshold: only return episodes with score > 0.7

### Task 3.4 — Add episodic recall triggers

**File:** `src/gateway/handlers/context-builder.ts`
**Where:** In the episodic memory search section (~line 209)
**What:**
- Current: searches episodic memory with message text
- Add: boost search on temporal triggers
  ```
  temporalTriggers = ["yesterday", "last time", "before", "remember when",
                       "as I said", "we discussed", "you told me", "earlier"]
  ```
- If message contains temporal triggers: increase result limit from default to 5, lower threshold to 0.5
- If `isReturningUser` (from TemporalContext): auto-search for recent episodes even without trigger

### Task 3.5 — Episode decay and compression

**File:** `src/memory/episodic.ts`
**What:**
- On `load()`: check episode ages
- Episodes > 30 days old with importance < 0.5: mark as `compressed` (keep summary, drop keyFacts and embedding)
- Episodes > 90 days old with importance < 0.3: mark as `archived` (excluded from search, kept on disk)
- This prevents unbounded growth of the episode store

**Acceptance criteria:**
- Telegram sessions with 30min+ gaps get segmented automatically
- Episodes extracted from completed segments appear in `workspace/memory/` storage
- "Like I told you before" triggers episodic recall and finds the correct prior conversation
- Returning user (>4h gap) sees relevant recent episodes in system prompt
- Episode store doesn't grow unbounded (30-day compression, 90-day archival)

---

## Phase 4: Conversational Ground State

**Goal:** Maintain a rolling summary of shared facts, decisions, and open questions. Refresh every 5 turns with a lightweight LLM call.

**Estimated changes:** ~200 lines — 1 new file, 2 modified files

### Task 4.1 — Extend FactStore with ground state categories

**File:** `src/memory/fact-store.ts`
**What:**
- Add new categories to the category type: `"decision"`, `"open_question"`, `"active_goal"`, `"sub_goal"`
- These use existing FactStore infrastructure (confidence, TTL, conflict resolution)
- `open_question` facts get a default TTL of 24 hours (auto-expire)
- `active_goal` facts linked to Intent ID via entity field

### Task 4.2 — Create `GroundStateView`

**New file:** `src/cognition/ground-state.ts`
**What:**
```typescript
class GroundStateView {
  constructor(private factStore: FactStore, private provider: AIProvider) {}

  // Query FactStore for session-scoped ground state
  getState(sessionId: string): {
    sharedFacts: Fact[];
    decisions: Fact[];
    openQuestions: Fact[];
    activeGoals: Fact[];
  }

  // Lightweight LLM call to refresh ground state
  async refresh(session: Session, segmentStart: number): Promise<void>

  // Format for system prompt injection
  toContextString(sessionId: string): string

  // Rolling summary
  private lastSummary: string;
  private turnsSinceRefresh: number;
}
```

**Refresh prompt (Haiku-class, ~100 input tokens):**
```
Given these recent messages, extract:
1. FACTS: What has been established/agreed?
2. DECISIONS: What was decided?
3. OPEN: What questions remain?
4. GOAL: What is the user trying to accomplish?

Messages: {last 5 messages since last refresh}

Return JSON: { facts: string[], decisions: string[], open: string[], goal: string }
```

### Task 4.3 — Integrate ground state into context flow

**File:** `src/gateway/core.ts`
**Where:** Post-response, alongside other post-processing
**What:**
- Track `turnsSinceRefresh` counter
- Every 5 turns: call `groundState.refresh(session, segmentStart)`
- On TOPIC_SWITCH: set TTL on current ground state facts, reset counter
- On FRESH_START: clear session-scoped ground state

**File:** `src/gateway/handlers/context-builder.ts`
**Where:** Alongside fact/episodic memory injection
**What:**
- Call `groundState.toContextString(sessionId)`
- Inject into system prompt if non-empty

**Output format:**
```
## Conversational Ground
Working on: Set up automated email sending
Established: API key obtained, AgentMail installed, template drafted
Open questions: Which email address to test with?
Progress: 3/5 steps complete
```

**Acceptance criteria:**
- After 5+ messages on one topic, ground state appears in system prompt
- Ground state correctly identifies active goal and open questions
- On topic switch, previous ground state is archived (not lost)
- Refresh adds ~100ms latency every 5th message (Haiku-class call)
- Empty ground state (new session, no facts) produces no injection (omitted entirely)

---

## Phase 5: Narrative Threads + User Mental Model

**Goal:** Cross-session coherence via thread tracking. Behavioral adaptation via user state inference.

**Estimated changes:** ~350 lines — 2 new files, 3 modified files

### Task 5.1 — Extend IntentStateMachine with NarrativeThread fields

**File:** `src/intent/state-machine.ts`
**What:**
- Add optional fields to Intent interface:
  ```typescript
  // NarrativeThread extension (set when promoted)
  isThread?: boolean;
  sessions?: string[];          // Session IDs that contributed
  summary?: string;             // Cross-session summary
  progress?: string;            // Where we left off
  nextSteps?: string[];         // What should happen next
  blockers?: string[];          // What's preventing progress
  resumeCount?: number;         // Times user returned to this
  lastActivity?: number;        // Timestamp of last activity
  ```
- Add methods:
  ```typescript
  promoteToThread(intentId: string, summary: string): void
  getActiveThreads(): Intent[]     // isThread === true && status === "active" | "paused"
  getThreadForTopic(query: string, threshold: number): Intent | null  // semantic match
  ```
- Add decay: in `load()`, check threads with `lastActivity` > 14 days → transition to "abandoned"

### Task 5.2 — Implement thread promotion and matching

**File:** `src/gateway/core.ts`
**Where:** Post-response processing
**What:**
- **Promotion rules** — check after each message:
  - Intent has been active across 2+ session segments → promote
  - Intent has 3+ completed checkpoints → promote
  - Intent was linked to a Goal → promote
- **Thread matching on message arrival:**
  - Before creating a new Intent, check `getActiveThreads()`
  - Compare message to each thread's `summary` (keyword overlap or cosine similarity if embeddings available)
  - If match score > 0.7: resume that thread instead of creating new
  - Increment `resumeCount`, update `lastActivity`, add session ID to `sessions[]`
- **Thread injection:**
  - On session start (first message after gap): inject active threads list into system prompt
  - Format matches spec Section 3.6

### Task 5.3 — Create `UserMentalModel` module

**New file:** `src/cognition/user-mental-model.ts`
**What:**
```typescript
interface UserState {
  likelyState: "focused" | "browsing" | "frustrated" | "in_a_hurry" | "exploring";
  confidence: number;
}

class UserMentalModel {
  private baseline: {
    avgMessageLength: number;
    avgResponseLatency: number;
    sessionCount: number;
  };

  // Called on every message (instant, no LLM)
  update(message: ChatMessage, session: Session): void

  // Returns inferred state (only if calibrated and confident)
  getState(): UserState | null   // null if not calibrated

  // Format for system prompt (only high-confidence)
  toContextString(): string      // empty if confidence < 0.6
}
```

**Calibration:** Observe for 10 sessions, compute baseline averages. Before calibration: return null (no inference).

**Heuristics (all relative to baseline):**
- `frustrated`: message length < baseline * 0.3 for 3+ consecutive messages, OR clarification requests > 2 in session, OR question repetition detected
- `in_a_hurry`: response latency < baseline * 0.5 AND message length < baseline * 0.5
- `exploring`: message contains "what if", "brainstorm", "ideas", topic dwell time > 10 messages
- `browsing`: rapid topic switches (3+ TOPIC_SWITCH in 10 messages)
- `focused`: default when no other signal is strong enough

### Task 5.4 — Integrate user mental model into response style

**File:** `src/engine/runtime.ts`
**Where:** In `buildSystemPrompt()`, after temporal context
**What:**
- If `userMentalModel.toContextString()` is non-empty, inject:
  ```
  ## User State
  The user appears to be in a hurry — keep responses concise and direct.
  ```
- Only inject when confidence >= 0.6
- Never mention the inference to the user ("I notice you seem frustrated" — anti-pattern)

### Task 5.5 — Thread summary refresh

**File:** `src/gateway/core.ts`
**Where:** On thread resume (when user returns to a paused thread)
**What:**
- On resume: refresh thread `summary` and `progress` from recent session messages
- Use lightweight LLM call (same model as ground state refresh):
  ```
  Given thread "{title}" and recent messages, update:
  1. Summary: What is this thread about? (1 sentence)
  2. Progress: What's been done? (1 sentence)
  3. Next steps: What should happen next? (bullet list)
  Return JSON.
  ```
- Fire-and-forget (don't block the response)

**Acceptance criteria:**
- Multi-session work (e.g., email setup across 3 sessions) tracked as a single thread
- On return after gap, active threads listed in system prompt with progress
- User sending rapid short messages correctly classified as "in_a_hurry" (after calibration)
- Thread abandoned after 14 days of inactivity
- User mental model never surfaces uncertain inferences

---

## Phase Dependencies

```
Phase 0 ──> Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4 ──> Phase 5
  |            |            |            |            |            |
  |            |            |            |            |            +-- Threads, UserMentalModel
  |            |            |            |            +-- GroundStateView over FactStore
  |            |            |            +-- Segmentation, episodic extraction, Park et al. scoring
  |            |            +-- 3-layer continuity, replaces keyword detection
  |            +-- TemporalContext, timezone, session gaps
  +-- Wire dormant WorkingContext, IntentStateMachine, CommitmentTracker, TopicSwitch
```

Each phase delivers independently:
- **Phase 0** → Intents appear in prompt, commitments tracked, topic resets work
- **Phase 1** → Owl knows date/time, acknowledges returning users
- **Phase 2** → Owl follows conversation flow, handles topic switches gracefully
- **Phase 3** → Owl remembers yesterday's conversation, retrieves relevant episodes
- **Phase 4** → Owl tracks what's decided, what's open, what's the goal during long chats
- **Phase 5** → Owl maintains cross-session threads, adapts to user's pace

---

## New Files Summary

| File | Phase | Purpose |
|------|-------|---------|
| `src/cognition/temporal-context.ts` | 1 | Compute and format temporal awareness |
| `src/cognition/continuity-engine.ts` | 2 | 3-layer continuity classification |
| `src/memory/session-segmenter.ts` | 3 | Segment sessions on temporal gaps |
| `src/cognition/ground-state.ts` | 4 | GroundStateView over FactStore |
| `src/cognition/user-mental-model.ts` | 5 | Heuristic user state inference |

## Modified Files Summary

| File | Phases | Changes |
|------|--------|---------|
| `src/gateway/core.ts` | 0,1,2,3,4,5 | Main integration point — continuity, intent lifecycle, commitment scan, segmentation triggers |
| `src/gateway/handlers/context-builder.ts` | 0,2,3,4 | Inject WorkingContext, episodic recall triggers, ground state |
| `src/engine/runtime.ts` | 1,5 | Temporal context + user mental model in system prompt |
| `src/memory/episodic.ts` | 3 | Importance scoring, Park et al. retrieval, decay |
| `src/memory/fact-store.ts` | 4 | New categories for ground state |
| `src/intent/state-machine.ts` | 5 | NarrativeThread extension fields |
| `src/config/loader.ts` | 1 | Timezone config |

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Layer 3 LLM call adds latency | 2s timeout, fall back to Layer 1+2. Only ~20% of messages need it. |
| Ground state refresh hallucination | LLM extracts from raw messages only (no prior context to hallucinate from). Facts have confidence scores. |
| Episodic extraction blocks response | Fire-and-forget pattern — extraction runs after response is sent. |
| Intent detection false positives | Conservative: only create intents for clearly task-oriented messages. Touch (not create) on ambiguous. |
| UserMentalModel wrong inference | Calibration period (10 sessions). Confidence threshold (0.6). Never surface to user. |
| Thread count grows unbounded | 14-day auto-abandon. Max 10 active threads per user. |
| System prompt token bloat | Each component has empty-guard: omit section if nothing to inject. Cap total injection at 500 tokens. |
