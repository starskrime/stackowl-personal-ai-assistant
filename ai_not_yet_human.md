# AI Not Yet Human — Specification for Human-Like Cognition (v2)

> **Status:** Research specification — revised after PhD-level architectural review
> **Goal:** Make StackOwl think, remember, and reason like a person across conversations
> **Scope:** Conversational continuity, temporal awareness, context switching, cross-session coherence
> **Revision notes:** v2 incorporates codebase audit findings — activates dormant systems before building new ones, eliminates architectural duplication, adds degradation strategies and quantified success criteria

---

## 1. Problem Statement

The assistant currently operates as a **stateless request-response machine**. It answers each message in isolation, with no genuine understanding of:

- **When** things happened (no clock, no sense of time passing)
- **Whether** the current message continues a prior conversation or starts a new one
- **What** the user cared about yesterday, this morning, or 5 minutes ago
- **Why** the user is asking — what deeper goal connects their recent requests

A human colleague would say: *"Oh, you're still working on that email thing from this morning?"* or *"We talked about this last Tuesday — here's what changed."* StackOwl cannot do this.

### Concrete Failure Modes Observed

| Symptom | Root Cause | Current Codebase State |
|---------|-----------|----------------------|
| User says "continue" and owl doesn't know what to continue | Session has messages but no **semantic thread** linking them | `IntentStateMachine` exists with full lifecycle but `.create()` is **never called** — intents are never created from user messages |
| User returns after 24h, owl acts like they just met | No **temporal context** — system prompt lacks date/time, session has no gap detection | `buildSystemPrompt()` in `runtime.ts` injects persona, DNA, skills, memory — but **zero temporal information** |
| User says "like I said before" and owl hallucinates | **Episodic recall** partially wired but empty | `EpisodicMemory.search()` IS wired into `context-builder.ts` (lines 209-213) with 2s timeout — but `extractFromSession()` is never triggered, so there are no episodes to search |
| Owl doesn't notice topic shift mid-conversation | Topic switch detection is **implemented but never called** | `SessionManager.detectTopicSwitch()` has 10 keyword phrases — but no code path invokes it |
| Owl can't distinguish "quick follow-up" from "brand new request" | No **intent continuity model** — every message is treated as independent | `WorkingContext` class has `setCurrentTopic()`, `setActiveIntent()`, `isTopicStale()` — all **dead code**, never called |
| Long Telegram sessions become incoherent (28+ messages in one session) | Single session file grows unbounded; no **conversational segmentation** | Sessions capped at 50 messages but no logical segmentation within a session |

### Key Finding: Dormant Infrastructure

The codebase has **4 major subsystems** that are fully implemented but never activated:

| Component | File | What Exists | What's Missing |
|-----------|------|------------|----------------|
| `WorkingContext` | `src/memory/working-context.ts` | Per-session KV store with topic tracking, intent linking, staleness detection | Never injected into system prompt, no caller sets topic/intent |
| `IntentStateMachine` | `src/intent/state-machine.ts` | Full intent lifecycle (pending → completed/abandoned), checkpoints, commitments, stale detection (30min), `toContextString()` already injected | `.create()` never called — intents never created from user messages |
| `CommitmentTracker` | `src/intent/commitment-tracker.ts` | Tracked commitments with deadlines, follow-up messages, status lifecycle | `.track()` never called — owl promises never captured |
| `SessionManager.detectTopicSwitch()` | `src/gateway/handlers/session-manager.ts` | Keyword-based detection for resets, greetings, topic markers | Never invoked in any code path |

Additionally:
- `FactStore` (`src/memory/fact-store.ts`) — Mem0-style structured facts with categories, confidence, TTL, conflict resolution — **already injected into context-builder**
- `MemoryRetriever` (`src/memory/memory-retriever.ts`) — Unified query across 5 memory systems — **exists but not wired into gateway**

**Implication:** Phase 0 must activate these before building new systems.

---

## 2. Research Foundation

### 2.1 Cognitive Architecture (ACT-R / Soar / LIDA)

Human cognition operates on multiple memory systems simultaneously:

| Memory Type | Human Analogy | Current StackOwl | Gap |
|------------|---------------|------------------|-----|
| **Sensory buffer** | "What did they just say?" | `session.messages[-1]` | Exists but raw — no parsed intent |
| **Working memory** | "What are we doing right now?" | `WorkingContext` class | Exists but **never populated** — no caller sets topic/intent |
| **Episodic memory** | "What happened Tuesday?" | `EpisodicMemory` class | Wired for retrieval but **extraction never triggers** — no episodes exist |
| **Semantic memory** | "I know this user prefers short answers" | `FactStore` + `memory.md` + `user-profile.json` | Best-functioning layer — FactStore has structured facts with confidence |
| **Procedural memory** | "I know how to send an email" | Skills system | Working (after synthesis fixes) |
| **Prospective memory** | "I promised to remind them tomorrow" | `CommitmentTracker` | Exists but **`.track()` never called** — promises never captured |
| **Temporal awareness** | "It's 3pm on a Tuesday" | **Nothing** | **Completely missing from system prompt** |

Reference: Anderson, J.R. (2007). *How Can the Human Mind Occur in the Physical Universe?* — ACT-R's temporal module and goal buffer are the two components StackOwl lacks most critically.

### 2.2 Conversational Grounding Theory (Clark & Brennan, 1991)

Human conversation relies on **common ground** — the shared knowledge participants believe they have.

- **Presentation:** Speaker says something
- **Acceptance:** Listener signals understanding (or not)
- **Accumulation:** Shared ground grows over time

StackOwl's `FactStore` already captures structured facts with categories (`preference`, `project_detail`, `goal`, `context`), confidence scores, and conflict resolution. This IS a form of ground state — but it lacks:
- `decision` and `open_question` categories
- Session-scoped filtering (ground state for THIS conversation vs all-time)
- Active goal tracking (what we're working toward right now)

**Implication:** ConversationalGround should be a **view over FactStore**, not a parallel data structure.

### 2.3 Theory of Mind in Dialogue (Premack & Woodruff, 1978; Bara, 2010)

Humans model their conversational partner's mental state. StackOwl's `innerLife` models the **owl's** state but not the **user's**.

Key challenge: heuristic-based inference is unreliable without calibration. A user sending short messages might be frustrated, on mobile, naturally terse, or in a fast back-and-forth that's going well.

**Implication:** User mental model needs (a) a calibration period to establish baseline, (b) user-profile-aware thresholds, (c) confidence scores, (d) a principle: never surface low-confidence inferences.

### 2.4 Context-Dependent Memory (Tulving, 1972)

Humans recall memories better when retrieval context matches encoding context. Current episodic search uses keyword similarity only — no temporal, emotional, or contextual signal.

### 2.5 Conversation Topic Structure (Grosz & Sidner, 1986)

Three components of discourse structure:
1. **Linguistic structure** — sequence of utterances (StackOwl has this)
2. **Intentional structure** — hierarchy of purposes (IntentStateMachine exists but is never populated)
3. **Attentional structure** — what's in focus (WorkingContext exists but is never populated)

### 2.6 Generative Agents Architecture (Park et al., 2023)

The most directly applicable published work. Their memory architecture:
- **Memory stream** → StackOwl's session history + episodic memory
- **Retrieval scoring:** `score = recency_decay(timestamp) × importance × relevance`
- **Reflection** → StackOwl's `MemoryReflexionEngine`
- **Planning** → Missing from StackOwl

**Critical adoption:** StackOwl should use Park et al.'s **retrieval scoring function** for episode recall and thread matching. This replaces pure keyword/cosine similarity with a weighted formula that prioritizes recent, important, relevant memories.

```
retrieval_score = alpha * recency_decay(hours_since_event)
               + beta  * importance(0_to_1)
               + gamma * relevance(cosine_similarity_to_query)

where alpha=1.0, beta=1.0, gamma=1.0 (tunable)
and recency_decay = 0.99^hours_since_event
```

### 2.7 Temporal Reasoning in AI Agents (Google DeepMind, 2024)

Agents need: absolute time awareness, relative time reasoning ("yesterday" = specific date), duration estimation, recency weighting, temporal patterns.

### 2.8 Memory Decay (Ebbinghaus, via Zhong et al. 2024 — MemoryBank)

All persistent data structures need **decay functions**:
- NarrativeThreads: abandon after 14 days of inactivity
- Ground state facts: decay confidence by 10% per day of inactivity
- Episodes: compress/merge after 30 days
- Working context: clear after session timeout (2h)

This prevents unbounded growth and ensures the system "forgets" like a human.

---

## 3. Architectural Specification

### 3.0 Phase 0 — Activate Dormant Systems (prerequisite)

Before building anything new, wire up the 4 dormant subsystems. These are **free wins** — the code exists, it just needs activation triggers.

#### 3.0.1 Activate WorkingContext

**Current state:** Allocated in `src/index.ts:535`, passed to gateway, but never injected into system prompt and never populated.

**Activation:**
- In `context-builder.ts`: inject `workingContext.toContextString()` alongside intent context
- In `gateway/core.ts`: after continuity classification (Phase 2), call `workingContext.setCurrentTopic(detectedTopic)`
- On intent creation: call `workingContext.setActiveIntent(intentId)`

#### 3.0.2 Activate IntentStateMachine.create()

**Current state:** Initialized, `toContextString()` injected into system prompt, but no intents ever created.

**Activation:**
- After each user message, lightweight intent detection:
  - If no active intent for this session AND message is task-oriented → `create()`
  - If active intent exists AND message continues it → `touch(intentId)`
  - If active intent exists AND topic switch detected → `transition(intentId, "paused")` + `create()` new
- On tool completion → `completeCheckpoint()` on active intent
- After 30min stale → `transition(intentId, "abandoned")`

#### 3.0.3 Activate CommitmentTracker.track()

**Current state:** Initialized, used by ProactiveIntentionLoop for `getDue()`, but `.track()` never called.

**Activation:**
- Post-response analysis: scan owl response for commitment patterns:
  - "I'll remind you..." → track with deadline
  - "Let me check on that..." → track with context_change trigger
  - "Tomorrow I'll..." → track with time_delay trigger
- This can be a simple regex scan initially, upgraded to LLM extraction later.

#### 3.0.4 Activate SessionManager.detectTopicSwitch()

**Current state:** Implemented with keyword detection, never called.

**Activation:**
- Call in `gateway/core.ts` before context building
- On detection: inject the `[SYSTEM DIRECTIVE: Context has been flushed]` marker
- Clear WorkingContext topic
- Later (Phase 2): replace keyword detection with 3-layer continuity engine

### 3.1 Temporal Awareness Layer

**Problem:** The system prompt contains zero temporal information. The LLM doesn't know what day, time, or timezone it is.

**Design:**

```
TemporalContext {
  now: Date                      // Current timestamp
  timezone: string               // User's timezone (detected or configured)
  dayOfWeek: string              // "Tuesday"
  timeOfDay: string              // "morning" | "afternoon" | "evening" | "night"

  // Session temporality
  sessionAge: Duration           // How long since first message in this session
  lastMessageGap: Duration       // Time since last user message
  lastSessionGap: Duration       // Time since previous session ended

  // Temporal patterns (from user-profile.json interaction history)
  userActiveHours: Range[]       // When user typically engages
  isUnusualTime: boolean         // Outside normal patterns

  // Calendar awareness
  dayContext: string              // "It's a weekday morning" / "weekend evening"
}
```

**Bootstrapping rule:** `userActiveHours` and `isUnusualTime` require **minimum 7 sessions across 3+ days** before activation. Before threshold: assume all hours are normal. This prevents the "creepy omniscience" anti-pattern (Section 7.3).

Injected into system prompt as:

```
## Temporal Context
Current time: Tuesday, March 29, 2026 at 3:47 PM (Europe/Baku)
Session started: 12 minutes ago
Last user message: 45 seconds ago
Previous session: Yesterday at 9:15 PM (user discussed AI news)
```

**Architectural principle:** Computed at message time, injected into system prompt. No LLM call. Cheap and deterministic.

**Degradation:** If timezone is unknown, omit timezone-specific fields. If no prior session exists, omit "Previous session" line. Never inject partial/uncertain temporal data.

### 3.2 Conversation Continuity Engine

**Problem:** The system cannot distinguish continuation from new request. `SessionManager.detectTopicSwitch()` catches 10 keywords but is never called.

**Design: Three-Layer Continuity Detection**

**Layer 1 — Temporal Signal (no LLM, instant)**

```
TimeSinceLastMessage:
  < 5 min   -> LIKELY_CONTINUATION (same thought stream)
  5-30 min  -> POSSIBLE_CONTINUATION (check semantic)
  30min-4h  -> POSSIBLE_SWITCH (user went away, came back)
  4h-24h    -> LIKELY_NEW (but check for "as I was saying" patterns)
  > 24h     -> DEFINITELY_NEW (but inject prior session summary)
```

**Layer 2 — Linguistic Signal (no LLM, instant)**

Analyze the message for continuation markers:
- Anaphora: "it", "that", "this", "the thing", "what we discussed" -> CONTINUATION
- Sequence markers: "also", "and", "next", "another thing" -> CONTINUATION
- Topic markers: "about X", "regarding X" where X matches recent topic -> CONTINUATION
- Break markers: "hey", "hi", "new question", "different topic" -> NEW
- Void markers: (no markers, standalone question) -> AMBIGUOUS

**Layer 3 — Semantic Coherence (lightweight LLM call, only when ambiguous)**

Only invoked when Layers 1+2 disagree or return AMBIGUOUS. Single fast LLM call:

```
Given the last 3 messages and the new message, classify:
A) CONTINUATION — same topic/intent as recent messages
B) FOLLOW_UP — related but new angle on same topic
C) TOPIC_SWITCH — entirely new topic, keep history for reference
D) FRESH_START — new topic, history is irrelevant

Return one letter.
```

**Output determines context strategy:**

| Classification | Session Action | Context Injection | Intent Action |
|---------------|----------------|-------------------|---------------|
| CONTINUATION | Keep full history | None needed | `touch()` active intent |
| FOLLOW_UP | Keep full history | Add "The user is building on: {prior topic summary}" | `touch()` active intent |
| TOPIC_SWITCH | Keep history but add separator | Add "Previous topic was: {summary}. User has switched." | `transition(active, "paused")` + `create()` new |
| FRESH_START | Clear working context | Add "User is starting fresh." + prior session summary if recent | `transition(active, "paused")` + `create()` new |

**Degradation:** If Layer 3 LLM call times out (>2s), fall back to Layer 1+2 result. If Layer 1+2 disagree, default to FOLLOW_UP (safest — preserves context without forcing continuity).

### 3.3 Conversational Ground State (as FactStore view)

**Problem:** After 20+ messages, raw history is too large. The LLM loses track of what was agreed, what's pending, and the user's actual goal.

**Design: Ground State as a view over FactStore**

Instead of a parallel data structure, extend `FactStore` with new categories and add a session-scoped retrieval method:

**New FactStore categories:**
- `decision` — "We decided to use the Anthropic API" (confidence: 1.0, source: explicit)
- `open_question` — "Still need the API key" (TTL: session-scoped)
- `active_goal` — "Set up automated email sending" (linked to Intent)
- `sub_goal` — "Get API key" (parent: active_goal ID, status: pending/completed)

**GroundStateView:**

```
GroundStateView {
  // Computed from FactStore filtered by current session/thread
  getSharedFacts(sessionId): Fact[]       // category in [decision, project_detail, context]
  getOpenQuestions(sessionId): Fact[]      // category = open_question, not expired
  getActiveGoals(sessionId): Fact[]       // category = active_goal

  // Rolling summary
  lastTopicSummary: string                // 1-2 sentence, refreshed every 5 turns
  turnsSinceLastUpdate: number

  // Inject into system prompt
  toContextString(): string
}
```

**Update strategy:**
- Every 5 messages: Lightweight LLM call to refresh `lastTopicSummary` and extract new facts into FactStore
- On detected topic switch: Archive current ground state (set TTL on session-scoped facts), start fresh
- On session resume after gap: Load ground state, inject as "Where we left off: ..."

**Injection into system prompt:**
```
## Conversational Ground
You and the user are currently working on: {activeGoal}
Established facts: {sharedFacts as bullets}
Still open: {openQuestions as bullets}
Where we are: {lastTopicSummary}
```

**Degradation:** If LLM refresh fails, keep the stale summary (mark as stale in metadata). If FactStore has conflicting facts, use the one with highest confidence score (existing FactStore behavior). If no facts exist yet for this session, omit the section entirely.

### 3.4 Episodic Memory Activation

**Problem:** `EpisodicMemory` class exists, retrieval is wired into `context-builder.ts`, but `extractFromSession()` is never triggered. No episodes exist to retrieve.

**Design:**

**A. Session Segmentation**

Instead of one infinite session per user, segment based on temporal gaps:

```
When lastMessageGap > 30 minutes:
  1. Extract episode from messages[lastSegmentStart..current]
  2. Save episode to episodic memory with Park et al. importance score
  3. Start a new logical segment (but keep session file for backward compat)
  4. Inject previous segment summary into new context
```

**B. Episode Importance Scoring (Park et al.)**

At extraction time, assign an importance score (0-1):
- Contains a decision or commitment → 0.8+
- User expressed strong emotion → 0.7+
- Multi-turn deep discussion → 0.6+
- Simple Q&A → 0.3
- Greeting/small talk → 0.1

**C. Active Episodic Recall with Retrieval Scoring**

Replace pure keyword/cosine search with Park et al.'s weighted retrieval:

```
retrieval_score = alpha * recency_decay(hours_since_episode)
               + beta  * importance
               + gamma * relevance(cosine_similarity_to_query)

where recency_decay = 0.99^hours, alpha=1.0, beta=1.0, gamma=1.0
```

**D. Recall Triggers**

```
EpisodicRecall {
  // Triggered when user message contains temporal references
  temporalTriggers: ["yesterday", "last time", "before", "remember when",
                      "as I said", "we discussed", "you told me", "earlier"]

  // Triggered when user message matches a prior episode's topic
  topicTriggers: retrieval_score(userMessage, episode) > 0.7

  // Triggered on session resume after gap
  resumeTrigger: lastSessionGap > 4 hours
}
```

**E. Episode Compression (narrative, not factual)**

```
Bad:  "User asked about Bitcoin price. Assistant provided comparison."
Good: "User was researching crypto prices for an investment decision.
       They seemed focused on BTC vs ETH ratio. Importance: 0.5 (one-off query).
       No commitments made."
```

**F. Decay and Eviction**

- Episodes older than 30 days: compress (merge related episodes into summary episodes)
- Episodes older than 90 days with importance < 0.3: archive (remove from active search, keep on disk)
- Never delete — just move to cold storage

**Degradation:** If extraction LLM call fails, save a minimal episode with raw metadata only (topics from TF-IDF, no summary). Retry extraction on next gap. If retrieval returns no results, don't say "I don't remember" — just proceed without episodic context.

**Critical anti-pattern:** Never say "As you mentioned..." unless episodic recall actually returned a match with retrieval_score > 0.7. False memory is worse than no memory.

### 3.5 User Mental Model

**Problem:** The assistant doesn't model the user's cognitive state.

**Design:**

```
UserMentalModel {
  // Baseline (computed after calibration period)
  baselineMessageLength: number       // User's typical message length
  baselineResponseLatency: number     // User's typical reply speed
  baselineSessionCount: number        // Sessions used for calibration

  // Current signals (raw, per-message)
  responseLatency: number[]           // Moving avg (last 5) of reply time
  messageLengthTrend: "shorter" | "stable" | "longer"  // vs baseline
  shortMessageStreak: number          // Consecutive messages < baseline * 0.5
  clarificationRequests: number       // "What do you mean?" count in session
  questionRepetitionRate: number      // Re-asked same question

  // Inferred state (with confidence)
  likelyState: "focused" | "browsing" | "frustrated" | "in_a_hurry" | "exploring"
  confidence: number                  // 0.0-1.0
}
```

**Calibration protocol:**
- First 10 sessions: observation only, build baseline
- After 10 sessions: begin inference with minimum confidence threshold of 0.6
- Never surface inferred state to user when confidence < 0.8
- Use `user-profile.json` interaction history for initial baseline if available

**How it's used (only when confidence >= 0.6):**
- `frustrated` (conf >= 0.8) → Owl becomes more concise, acknowledges difficulty
- `in_a_hurry` (conf >= 0.7) → Skip explanations, direct answers, bullet points
- `exploring` (conf >= 0.6) → Offer related topics, be more expansive
- `focused` (conf >= 0.6) → Stay on topic, don't digress, track sub-goals

Updated **per message** with zero LLM cost (pure heuristics on message length, timing, punctuation, all relative to calibrated baseline).

**Degradation:** Before calibration threshold, default to `focused` with confidence 0.5 (neutral behavior). If signals conflict, default to `focused`. Never let UserMentalModel override explicit user instructions.

### 3.6 Cross-Session Narrative Threads (extends IntentStateMachine)

**Problem:** Each session is isolated. The assistant has no concept of "yesterday we were working on X."

**Design: NarrativeThread as an Intent extension**

Rather than a parallel tracking system, NarrativeThread extends `IntentStateMachine` for cross-session coherence. An Intent represents a single-session task; a NarrativeThread is a **multi-session Intent** that persists across sessions.

```
NarrativeThread extends Intent {
  // Additional fields beyond Intent
  sessions: SessionRef[]             // Which sessions contributed
  lastActivity: Date

  // Thread state (beyond Intent's checkpoints)
  summary: string                    // What's the thread about?
  progress: string                   // Where did we leave off?
  nextSteps: string[]                // What should happen next?
  blockers: string[]                 // What's preventing progress?

  // Temporal pattern
  resumeCount: number                // How many times has user come back to this?
  avgGapBetweenSessions: number      // How long between work sessions?
}
```

**Promotion rule:** An Intent becomes a NarrativeThread when:
- It spans 2+ session segments (user left and came back to same topic)
- OR it has 3+ checkpoints (complex enough to need cross-session tracking)
- OR it was explicitly linked to a Goal via `linkToGoal()`

**Thread lifecycle:**
1. **Created** — promoted from Intent when cross-session criteria met
2. **Updated** — after each relevant session segment (summary, progress, nextSteps refreshed)
3. **Paused** — when user switches to different thread (`transition("paused")`)
4. **Resumed** — when user returns (detected by semantic similarity or explicit reference)
5. **Completed** — when user signals completion or all checkpoints done
6. **Abandoned** — after 14 days of inactivity (decay function)

**On session start, inject:**
```
## Active Threads
You have 2 active threads with this user:

1. "Email automation setup" (last active: yesterday)
   Progress: API key obtained, template drafted
   Next: Test sending with real address

2. "AI news monitoring" (last active: 3 days ago)
   Progress: Set up news skill, user wants daily briefing
   Next: Configure schedule and sources
```

**Thread matching on message arrival:**
1. Compute `retrieval_score` for each active thread against user message
2. If top score > 0.7 → resume that thread
3. If no match → create new Intent (may promote to thread later)

**Decay:** Threads with no activity for 7 days: reduce retrieval weight by 50%. After 14 days: `transition("abandoned")`. Never delete — abandoned threads can be resurrected by explicit user reference.

---

## 4. Integration Architecture

### 4.1 Where Each Component Lives

```
Message arrives
  |
  +-- TemporalContext.compute()          [instant, no LLM]
  |   +-- Injects time, gaps, patterns into system prompt
  |
  +-- UserMentalModel.update()           [instant, no LLM]
  |   +-- Updates engagement/patience/attention from message heuristics
  |
  +-- ContinuityEngine.classify()        [instant or 1 fast LLM call]
  |   +-- Layer 1: Temporal gap analysis
  |   +-- Layer 2: Linguistic marker scan
  |   +-- Layer 3: Semantic coherence (only if ambiguous)
  |   +-- Updates WorkingContext.currentTopic
  |   +-- Touches or creates Intent via IntentStateMachine
  |
  +-- NarrativeThread.match()            [instant, retrieval scoring]
  |   +-- Find or resume thread for current interaction
  |
  +-- GroundStateView.inject()           [from FactStore cache, no LLM]
  |   +-- Add ground state to system prompt
  |
  +-- EpisodicMemory.recall()            [instant, retrieval scoring]
  |   +-- Retrieve relevant past episodes (Park et al. scoring)
  |
  +-- ContextBuilder.build()             [existing flow, enriched]
      +-- All signals merged into EngineContext

Response generated
  |
  +-- GroundStateView.update()           [every 5 turns, 1 LLM call]
  +-- CommitmentTracker.scan()           [instant, regex on response]
  +-- UserMentalModel.update()           [instant, from response timing]
  +-- IntentStateMachine.update()        [instant, checkpoint tracking]
  +-- NarrativeThread.update()           [instant, update progress]
  +-- EpisodicMemory.segment()           [on 30min+ gap, 1 LLM call]
```

### 4.2 Cost Budget

| Component | LLM Calls | When | Model | Fallback on Failure |
|-----------|-----------|------|-------|-------------------|
| Phase 0: Activate dormant systems | 0 | Always | N/A | N/A — pure wiring |
| Temporal Context | 0 | Always | N/A | Omit unknown fields |
| User Mental Model | 0 | Always | N/A | Default to "focused" |
| Continuity (Layer 1-2) | 0 | Always | N/A | N/A |
| Continuity (Layer 3) | 0-1 | When ambiguous (~20%) | Haiku/fast | Fall back to Layer 1-2 |
| Ground State refresh | 0-1 | Every 5 turns | Haiku/fast | Keep stale summary |
| Episode extraction | 0-1 | On 30min+ gap | Sonnet | Save minimal metadata |
| Commitment scan | 0 | Every response | N/A (regex) | Skip silently |
| Thread management | 0 | Always | N/A | N/A |

**Typical message:** 0 extra LLM calls (80% of messages)
**Ambiguous message:** 1 extra haiku call (~0.01 cents)
**Worst case (30min gap + ambiguous + ground state refresh):** 3 extra calls (~0.05 cents)
**Amortized average:** ~0.3 extra calls per message

### 4.3 Data Flow: Current vs Proposed

**Currently:**
```
message -> session.messages[] -> buildSystemPrompt() -> LLM -> response
             (raw transcript)    (persona + tools + memory.md)
```

**Proposed:**
```
message -> TemporalContext ----+
        -> ContinuityEngine ---+
        -> UserMentalModel ----+
        -> NarrativeThread ----+---> ContextBuilder.build() -> LLM -> response
        -> EpisodicRecall -----+        (enriched with human-like awareness)
        -> GroundStateView ----+
        -> WorkingContext -----+
                                          |
                              +-----------+
                              v
                    GroundStateView.update()
                    CommitmentTracker.scan()
                    IntentStateMachine.update()
                    NarrativeThread.update()
                    EpisodicMemory.segment()
```

---

## 5. Implementation Priority

### Phase 0: Activate Dormant Systems (prerequisite — zero new code needed)

**Why first:** Free wins. The code exists. Pure wiring changes.

1. Wire `WorkingContext.toContextString()` into context-builder
2. Add intent detection: call `IntentStateMachine.create()` from gateway
3. Add commitment scanning: regex-scan owl responses for promises
4. Call `SessionManager.detectTopicSwitch()` in message handling
5. Wire `MemoryRetriever` into gateway for unified memory queries

### Phase 1: Temporal Awareness (highest impact, lowest cost)

**Why second:** The single most impactful new feature. Zero LLM cost. Fixes "owl doesn't know what day it is."

1. Compute `TemporalContext` at message time
2. Inject current date/time + session gaps into system prompt
3. Detect returning users (gap > 4h) and inject "Welcome back" context
4. Add time-of-day awareness to response style

### Phase 2: Conversation Continuity Engine

**Why third:** Fixes "owl doesn't follow the conversation" — the user's primary complaint.

1. Implement 3-layer continuity detection
2. On TOPIC_SWITCH: summarize prior topic, inject separator, pause current intent
3. On FRESH_START: clear working context, inject prior session summary, create new intent
4. On CONTINUATION/FOLLOW_UP: enrich with "building on" context, touch active intent

### Phase 3: Session Segmentation + Episodic Memory

**Why fourth:** Fixes "owl forgets yesterday's conversation." Requires Phase 1 for gap detection.

1. Segment sessions on 30min+ gaps (extract episode, start new segment)
2. Enable episodic extraction for Telegram sessions (not just CLI /quit)
3. Implement Park et al. retrieval scoring (recency x importance x relevance)
4. Search episodes on session resume and temporal references
5. Add episode decay: compress after 30 days, archive after 90 days

### Phase 4: Conversational Ground State

**Why fifth:** Fixes "owl loses track during long conversations." Requires Phase 2 for topic tracking.

1. Add `decision`, `open_question`, `active_goal`, `sub_goal` categories to FactStore
2. Implement GroundStateView as session-scoped FactStore query
3. Refresh every 5 turns with lightweight LLM call
4. Inject into system prompt as structured context
5. Archive ground state on topic switch (set TTL on session-scoped facts)

### Phase 5: Narrative Threads + User Mental Model

**Why last:** Most complex, requires all prior phases. Enables true cross-session coherence.

1. Extend IntentStateMachine with NarrativeThread fields
2. Implement promotion rules (2+ sessions, 3+ checkpoints, or linked to goal)
3. Match returning users to active threads using retrieval scoring
4. Model user mental state from behavioral heuristics (after calibration period)
5. Adjust response style based on high-confidence inferred state

---

## 6. Success Criteria

### Qualitative: The "Colleague Test"

| Test | Expected Behavior |
|------|-------------------|
| User says "hi" at 9 AM after chatting until 11 PM yesterday | "Good morning! Yesterday we were looking at that news aggregation setup — want to continue, or something new?" |
| User says "what about the other thing?" | Owl correctly identifies "the other thing" from active intents/threads |
| User returns after 3 days of silence | Owl acknowledges the gap, summarizes active threads, asks what to focus on |
| User rapidly sends 5 short messages | Owl recognizes urgency/impatience (if calibrated), responds concisely |
| User says "like I told you before" | Owl searches episodic memory and finds the specific prior conversation |
| User works on email setup across 3 sessions over 2 days | Owl maintains thread state, knows what's done and what's pending |
| User switches topic mid-conversation | Owl cleanly transitions, pauses prior intent, preserves state for later |

### Quantitative: Measurable Criteria

| Metric | Target | How to Measure |
|--------|--------|---------------|
| **Temporal accuracy** | >95% | When user says "yesterday", system references correct date (sample 100 messages) |
| **Continuity precision** | >90% | Of messages classified CONTINUATION, human agrees (sample 100 messages) |
| **Episode recall** | >80% | When user says "like I told you before", correct episode in top-3 results |
| **Thread coherence** | >85% | After thread resume, system summary matches human judgment |
| **False memory rate** | <2% | System claims "you said X" when X was never said |
| **Latency overhead** | <500ms | P95 additional latency from all new components combined |
| **LLM cost overhead** | <$0.001/msg | Average extra LLM cost per message (amortized) |

---

## 7. Anti-Patterns to Avoid

1. **Over-prompting:** Don't inject 2000 tokens of context every message. Be surgical — only inject what's relevant to THIS message. Empty sections should be omitted entirely.

2. **Hallucinatory recall:** Never say "As you mentioned..." unless episodic memory returned a match with retrieval_score > 0.7. False memory is worse than no memory.

3. **Creepy omniscience:** Don't surface temporal patterns unprompted ("I notice you always message at 3 PM"). Let it inform behavior silently. Require minimum data threshold (7 sessions, 3+ days) before pattern activation.

4. **Excessive summarization:** Don't re-summarize every 5 messages. Only refresh ground state when there's meaningful new information (new decisions, new facts, goal progress).

5. **Breaking flow:** Continuity detection should be invisible. Don't say "I detected a topic switch." Just adapt naturally.

6. **Parallel systems:** Don't build ConversationalGround as a new data store when FactStore already handles structured facts. Don't build NarrativeThread as a new system when IntentStateMachine already has the lifecycle. Extend, don't duplicate.

7. **Overconfident inference:** Don't infer user frustration from 2 short messages without calibration data. Default to neutral/focused when uncertain. Confidence thresholds exist for a reason.

8. **Unbounded growth:** Every persistent data structure must have a decay/eviction rule. No exceptions. Threads abandon after 14 days. Facts decay confidence over time. Episodes compress after 30 days.

---

## 8. References

- Anderson, J.R. (2007). *How Can the Human Mind Occur in the Physical Universe?* Oxford University Press. (ACT-R cognitive architecture — temporal module, goal buffer)
- Clark, H.H. & Brennan, S.E. (1991). *Grounding in Communication.* In L.B. Resnick et al. (Eds.), Perspectives on Socially Shared Cognition. (Conversational grounding theory)
- Grosz, B.J. & Sidner, C.L. (1986). *Attention, Intentions, and the Structure of Discourse.* Computational Linguistics, 12(3). (Three-layer discourse structure)
- Tulving, E. (1972). *Episodic and Semantic Memory.* In Organization of Memory. (Episodic memory theory, encoding specificity)
- Premack, D. & Woodruff, G. (1978). *Does the Chimpanzee Have a Theory of Mind?* Behavioral and Brain Sciences, 1(4). (Theory of mind)
- Bara, B.G. (2010). *Cognitive Pragmatics: The Mental Processes of Communication.* MIT Press. (Mental models in dialogue)
- Park, J.S. et al. (2023). *Generative Agents: Interactive Simulacra of Human Behavior.* Stanford/Google. (Memory stream, retrieval scoring: recency x importance x relevance, reflection, planning)
- Zhong, W. et al. (2024). *MemoryBank: Enhancing Large Language Models with Long-Term Memory.* (Ebbinghaus forgetting curve applied to LLM memory — decay functions)
- Wang, L. et al. (2024). *A Survey on Large Language Model Based Autonomous Agents.* (Profile, memory, planning, action architecture taxonomy)

