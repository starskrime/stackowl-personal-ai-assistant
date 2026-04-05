# StackOwl — Reality Check & Improvement Spec

**Date:** 2026-04-04  
**Based on:** Full codebase audit + user self-assessment report

---

## PART 1: WHAT THE SELF-ASSESSMENT GOT WRONG

Noctua's self-report diagnosed generic LLM limitations (knowledge cutoff, no files, no memory). These are **not the actual problems with this codebase**. StackOwl already has:

| Self-Report Says "Missing" | Reality |
|---|---|
| No persistent memory | ✓ 5 memory systems active: episodic, fact-store, working context, reflexion, knowledge graph |
| No internet/real-time info | ✓ GoogleSearchTool, WebCrawlTool, BrowserTool with anti-bot pool all registered |
| No file access | ✓ ReadFileTool, WriteFileTool, EditFileTool, ShellTool all working |
| No code execution | ✓ ShellTool runs arbitrary shell commands |
| No learning from mistakes | ✓ LearningOrchestrator, ReflexionEngine, behavioral patches all active |

**The real problems are architectural, not capability gaps.**

---

## PART 2: ACTUAL PROBLEMS FOUND

### Problem 1 — Context Overload (HIGH IMPACT)

The system prompt injects **20+ independent signals simultaneously**:

```
temporal context, episodic memory, fact store, ambient context mesh,
knowledge graph results, predictive queue, collaboration state,
user profile (micro-learner), inferred preferences (PreferenceModel),
user mental model, echo chamber analysis, behavioral patches,
socratic mode directive, active intents (IntentStateMachine),
working context, conversational ground state, mode directive,
pellets (past knowledge), skills context, preferences context,
attempt log block, DNA behavioral directives, inner life state,
inner monologue directive
```

**Impact:** The LLM receives 3000–6000 tokens of meta-context before seeing the user's message. It cannot prioritize — everything looks equally important. The result is responses that ignore most of the injected context or average across conflicting signals.

**Evidence:** The assistant asked random check-in questions despite instinct rules against it. Skills were used despite being irrelevant. Memory existed but responses didn't reflect it.

---

### Problem 2 — Too Many LLM Calls Per Message (HIGH IMPACT)

A single user message triggers **3 serial LLM calls before the response**:

1. `InnerLife.think()` — inner monologue (LLM call, temp=0.9)
2. `IntentRouter.disambiguate()` — skill validation (LLM call, temp=0)
3. `OwlEngine.run()` — main response + tool calls

Plus background work competing for the same API quota:
- `CognitiveLoop` fires every 15 min
- `LearningOrchestrator.processConversation()` runs after every response
- `EpisodicMemory` extraction after every response

For local Ollama: 3 sequential LLM calls = 10–30 second latency per message.  
For cloud APIs: 3 calls = 3× the cost.

---

### Problem 3 — Skill System Overhead vs. Value (MEDIUM IMPACT)

160+ skills loaded. Every message goes through:
1. BM25 retrieval (fast, in-memory)
2. Usage-weighted re-ranking
3. Semantic re-ranking (2 embedding calls)
4. Overlap deduplication
5. **LLM disambiguation (1 full LLM call)**

For most messages ("find me a cheap laptop", "what time is it") zero skills are relevant. The pipeline runs anyway — adding latency and cost — and may inject irrelevant skill context.

Structured skill auto-execution is permanently disabled (`if (false && ...)`). The skill system is **purely advisory** — the LLM can ignore it.

---

### Problem 4 — Memory Retrieved But Not Used (MEDIUM IMPACT)

Memory retrieval fires for every message but the retrieved content is diluted:
- Episodic memory: up to 5 episodes injected
- Fact store: up to 5 facts injected
- Pellets: up to 3 pellets injected
- Knowledge graph: up to 3 nodes injected
- Behavioral patches: up to 5 rules injected

All of this lands in `enrichedMemoryContext` which is **capped at 1500 chars** in the system prompt. With 20+ signals competing for 1500 chars, individual signals get truncated to ~75 chars each — meaningless noise.

---

### Problem 5 — No Response Quality Feedback Loop (HIGH IMPACT)

The system learns **what was said** but not **whether it was useful**:
- `LearningOrchestrator` extracts topics from every conversation regardless of quality
- `EpisodicMemory` stores summaries of every session regardless of outcome
- There is no signal for "this response was bad, learn from it"
- `ReflexionEngine` only triggers on tool failures, not on poor response quality
- DNA evolution uses LLM judgement with no user feedback signal

Result: The assistant accumulates knowledge volume but not response quality improvement.

---

### Problem 6 — OSCAR Complexity vs. Value (LOW IMPACT)

OSCAR (computer use) is ~100 files across 15 subdirectories — roughly 30% of the codebase's total complexity. It is registered as `ComputerUseTool` but:
- Desktop automation is fragile by nature (UI changes break it)
- No reliable test results in production
- The value-to-complexity ratio is very low for a personal assistant

---

### Problem 7 — Proactive Pings (FIXED in this session)

- Removed startup greeting
- Raised cooldown 5 min → 60 min
- `MAX_UNANSWERED_PINGS`: 2 → 1
- Removed generic "what's on your plate?" prompts
- Planner check-in now requires a specific goal idle > 4 hours

---

## PART 3: IMPROVEMENT SPEC

### S1 — Context Triage (Priority: HIGH)

**Problem:** 20+ signals injected into every system prompt.

**Fix:** Score each signal by relevance to the current message. Inject only top 4–5.

**Always inject:**
- Temporal context (cheap, always relevant)
- Mode directive (ASSISTANT vs REACTIVE)
- DNA behavioral directives
- Behavioral patches (top 3 — prevent repeated errors)

**Inject only when relevant:**

| Signal | Condition |
|---|---|
| Pellets | Score > 0.15 (current threshold 0.05 is too noisy) |
| Episodic memory | Temporal trigger word detected OR score > 0.35 |
| Facts | Keyword overlap with message > 50% |
| Skills | LLM validation passes (already gated) |
| Intent context | Only when there ARE active intents |
| Inner monologue | Only when inner life state has changed this session |

**Remove from always-included:**
- Echo chamber analysis (inject only on contentious opinion requests)
- User mental model (inject only on frustration signals)
- Predictive queue (inject only when confidence > 0.7)
- Ground state (inject only after 10+ exchanges)
- Working context (inject only within same task thread)

**Expected outcome:** System prompt shrinks from ~4000 to ~1200 tokens. LLM focuses better.

**File:** `src/gateway/handlers/context-builder.ts`

---

### S2 — Remove Inner Monologue from Hot Path (Priority: HIGH)

**Problem:** InnerLife.think() adds 2–5 seconds to every message.

**Fix:** Run async AFTER the main response. The previous turn's inner state is still available for injection.

```
User message arrives
  → Build system prompt using PREVIOUS inner state
  → Run ReAct loop → Send response  ← user gets response NOW
  → THEN async: run InnerLife.think() → update state for NEXT message
```

**Expected outcome:** 2–5 second latency reduction per message.

**File:** `src/engine/runtime.ts` (lines 386–402), `src/gateway/core.ts`

---

### S3 — Lazy Skill Routing (Priority: MEDIUM)

**Problem:** IntentRouter runs its 5-tier pipeline + LLM call on every message.

**Fix:** Skip IntentRouter entirely for conversational messages:

```typescript
// Fast pre-filter — only route if message looks like an action request
const SKILL_TRIGGER = /\b(find|search|create|write|generate|check|analyze|run|scan|fix|build|compare|convert|code|script|calculate|translate)\b/i;

if (!SKILL_TRIGGER.test(userMessage)) {
  return []; // Skip IntentRouter entirely
}
```

Also: **cache skill embeddings at startup** instead of recomputing on every semantic re-rank. Skill descriptions are static.

**Expected outcome:** Eliminates 1 LLM call for ~30–40% of messages (conversational, questions).

**Files:** `src/gateway/core.ts`, `src/skills/intent-router.ts`

---

### S4 — Response Quality Signal (Priority: HIGH)

**Problem:** No feedback on whether responses were actually useful.

**Fix:** After each response, record quality signals:

```typescript
// In PostProcessor:
const quality = {
  loopExhausted: response.loopExhausted,  // model got stuck
  toolFailures: response.toolsUsed.filter(t => t.failed).length,
  emptyContent: !response.content.trim(),
  // future: thumbs up/down from user
};

if (quality.loopExhausted || quality.toolFailures > 2) {
  // Record as failure in ReflexionEngine
  await reflexionEngine.recordFailure({
    message: userMessage,
    approach: response.toolsUsed,
    reason: "loop_exhausted_or_repeated_failures"
  });
}
```

**Expected outcome:** ReflexionEngine gets quality signal, not just "what was discussed" signal. Behavioral patches improve response quality over time.

**File:** `src/gateway/handlers/post-processor.ts`, `src/engine/runtime.ts`

---

### S5 — Memory Context Budget (Priority: MEDIUM)

**Problem:** 1500-char cap on 20+ signals = ~75 chars each = useless.

**Fix:**
- Raise budget to **3000 chars**
- Reduce to 3 signals: episodic (top 2 at 400 chars each), facts (top 3 at 200 chars each), behavioral patches (top 3 at 150 chars each)
- Each signal gets enough space to actually mean something

**File:** `src/engine/runtime.ts` (line 1517), `src/gateway/handlers/context-builder.ts`

---

### S6 — Tool List Pruning Per Request (Priority: MEDIUM)

**Problem:** All 70+ tools sent to LLM on every request — cognitive overload.

**Fix:** Preselect tools based on message content:

```typescript
function selectRelevantTools(message: string, allTools: ToolDef[]): ToolDef[] {
  const hasURL = /https?:\/\//.test(message);
  const hasPath = /\/[a-z]|\.[a-z]{2,4}$/.test(message);
  const hasMemory = /\b(remember|recall|what did|last time|before)\b/i.test(message);

  if (hasURL) return filter(allTools, ["web_crawl", "google_search", "browser"]);
  if (hasPath) return filter(allTools, ["read", "write", "edit", "shell"]);
  if (hasMemory) return filter(allTools, ["memory_search", ...top10ByUsage]);
  return top15ByRecentUsage(allTools);  // Default: most-used tools
}
```

**Expected outcome:** System prompt shrinks further. Tool selection accuracy improves.

**Files:** New `src/tools/selector.ts`, `src/engine/runtime.ts`

---

### S7 — Proactive Message Quality (Priority: MEDIUM)

**Problem:** Even when proactive messages DO fire (morning brief, goal follow-up), they're generic.

**Fix:**
- **Morning brief:** Pull stale goals + last 3 topics + today's date → generate specific agenda with action items, not "Good morning!"
- **Goal check-in:** Show current progress %, last action taken, AND offer one specific concrete next step
- **Commitment follow-up:** Reference the specific commitment, remind what was agreed, offer to execute

**File:** `src/heartbeat/proactive.ts`, `src/heartbeat/planner.ts`

---

## PART 4: WHAT TO DEPRIORITIZE / CONSIDER REMOVING

| Feature | Why |
|---|---|
| OSCAR computer use | 30% of codebase complexity, fragile, no proven production value |
| Echo chamber detector in every prompt | Rarely relevant; adds noise; inject only on demand |
| Constellation miner | Redundant with CognitiveLoop pattern mining |
| Predictive queue | Low-confidence items add prompt noise, rarely useful |
| Knowledge Council | Already disabled; keep disabled |
| Growth Journal generator | Not core to usefulness |
| Quests/Time Capsules | Gamification overhead, low practical value |
| ACP (Agent-to-Agent protocol) | No concrete use case yet |
| Inner monologue in hot path | See S2 |

---

## PART 5: QUICK WINS (1–2 hours each)

1. **Raise pellet retrieval threshold** `0.05` → `0.15` in `buildSystemPrompt` — cuts noisy pellets immediately
2. **Raise memory context budget** `1500` → `3000` in `buildSystemPrompt`
3. **Add SKILL_TRIGGER_KEYWORDS pre-filter** in `core.ts` before calling IntentRouter
4. **Move inner monologue to post-response** in `runtime.ts` — immediate latency win
5. **Cap tool list at 20** by relevance score in `buildSystemPrompt` tool section
6. **Record loopExhausted in ReflexionEngine** as a quality failure signal

---

## PART 6: ROOT CAUSE SUMMARY

The assistant's **intelligence ceiling** is set by the base model.  
The **effective intelligence** — what the user actually experiences — is determined by:

| What user feels | Root cause | Fix |
|---|---|---|
| Slow responses | 3 serial LLM calls before reply | S2: async inner monologue |
| Ignores past context | 20 signals compete for 1500 chars | S1 + S5: triage + budget |
| Uses wrong tools | 70+ tools in prompt = noise | S6: preselect tools |
| Doesn't improve over time | No quality feedback signal | S4: record failures |
| Annoying pings | Fixed | S7: meaningful content only |
| Wrong skill activated | Fixed (IntentRouter LLM gate) | S3: lazy routing |

None of these require a better model. They require better signal selection.
