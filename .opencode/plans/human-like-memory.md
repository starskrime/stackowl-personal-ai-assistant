# SPEC: Human-Like Memory — "What I Just Said" for Follow-Up Continuity

## Problem Statement

When a user asks a follow-up question like "tell me more about Letta" after the assistant just delivered a research report about Letta, the assistant cannot connect the reference. It should work like a human conversation:

> **Human**: Tell me about AI agents  
> **Assistant**: [delivers research report on Letta, Mem0, etc.]  
> **Human**: Tell me more about Letta  
> **Assistant**: "Letta (formerly MemGPT) is..." ← **understands the reference immediately**

Today: The assistant forgets it just said anything about Letta and restarts research as if from scratch.

---

## Root Cause Analysis

StackOwl has 7 parallel memory systems but **none capture the raw assistant response text** for injection into the next turn.

| System               | What it captures                                 | Gap                                                             |
| -------------------- | ------------------------------------------------ | --------------------------------------------------------------- |
| `ConversationDigest` | URLs, files, decisions, failures, open questions | ❌ No narrative content                                         |
| `FactStore`          | Structured facts with embeddings                 | ❌ Only extracted every 10 messages                             |
| `EpisodicMemory`     | Session summaries                                | ❌ Only on TOPIC_SWITCH or 30-min gaps                          |
| `GroundStateView`    | Session facts/decisions                          | ❌ Every 5 turns, not immediate                                 |
| `MemoryBus`          | Unified retrieval                                | ❌ Never called in main context path                            |
| `WorkingContext`     | `lastOwlResponse` stored                         | ❌ Never output in `toContextString()`                          |
| `ContinuityEngine`   | Classification labels                            | ❌ `priorTopicSummary` field is defined but **never populated** |

The research report's content exists only in `session.messages` (raw history). The model sees it buried in history without an explicit pointer.

---

## Letta Reference Architecture

Letta solves this with **named memory blocks** that the agent reads and writes autonomously:

- **Core Memory** — labeled blocks (`human`, `persona`) that persist across all messages
- **Recall Memory** — compressed conversation history
- **Archival Memory** — older data stored externally

Key insight: Memory management is an **agent action**, not post-processing. The agent writes memory via tool calls AFTER seeing context, not before the next turn.

Borrowed principle for StackOwl: **"For CONTINUATION/FOLLOW_UP, inject the verbatim last response as a named memory block."**

---

## Design Decisions

1. **Don't replace existing architecture** — fix the gap, don't rebuild
2. **Lowest effort, highest impact first** — store last assistant response in digest → inject for FOLLOW_UP/CONTINUATION
3. **Keep continuity classification** — it drives intent/thread management correctly; just wire its output to actual content
4. **No new infrastructure** — reuse existing `ConversationDigest` (already per-session, already injected)
5. **Verbatim over summarized** — the user selected "last response verbatim" over "summary block" because summaries lose detail

---

## Implementation Plan

### Step 1: Store `lastAssistantResponse` in ConversationDigest

- **File**: `src/memory/conversation-digest.ts`
- **Interface**: Add `lastAssistantResponse?: string` to `ConversationDigest`
- **Update logic**: In `update()`, capture last `assistant` role message content (first 2000 chars)
- **Output**: In `toContextString()`, emit as `<my_last_response>` XML block

### Step 2: Inject verbatim response for CONTINUATION/FOLLOW_UP

- **File**: `src/gateway/handlers/context-builder.ts`
- **Context builder**: For `CONTINUATION`/`FOLLOW_UP`, inject `digest.lastAssistantResponse` as "## What I Just Told You (read-only)" block
- **Context**: Prepend to `continuityContext` before existing label-only block

### Step 3: Populate `priorTopicSummary` in ContinuityEngine

- **File**: `src/cognition/continuity-engine.ts`
- **Logic**: When Layer 3 semantic classification fires, extract last assistant content and set as `priorTopicSummary`
- **Fallback**: Also set for Layers 1+2 when classification is CONTINUATION/FOLLOW_UP

### Step 4: Ensure episodic extraction triggers for short follow-up sessions

- **File**: `src/gateway/core.ts`, `src/memory/episodic.ts`
- **Logic**: Also trigger episode extraction when `sessionDepth` increases by 3+ without extraction, even if not TOPIC_SWITCH
- **Purpose**: Research report gets saved as an episode before user asks follow-up

### Step 5: Wire MemoryBus into context path (optional/best-effort)

- **File**: `src/gateway/handlers/context-builder.ts`
- **Logic**: Add `MemoryBus.toSystemPrompt()` output to the context assembly
- **Status**: Lower priority — MemoryBus exists but is never invoked

---

## Acceptance Criteria

1. User asks "tell me about Letta" → assistant gives research report
2. User asks "tell me more about Letta" → **assistant immediately expands on the report** — no new web search needed, references by name
3. User asks "what about the memory architecture part?" → **assistant knows this was in the report** and answers specifically
4. The verbatim response is available to the model without requiring it to parse raw session history

---

## Files to Modify

| Step | File                                                   | Change                                                     |
| ---- | ------------------------------------------------------ | ---------------------------------------------------------- |
| 1    | `src/memory/conversation-digest.ts`                    | Add `lastAssistantResponse` field + capture logic + output |
| 2    | `src/gateway/handlers/context-builder.ts`              | Inject verbatim response for CONTINUATION/FOLLOW_UP        |
| 3    | `src/cognition/continuity-engine.ts`                   | Populate `priorTopicSummary`                               |
| 4    | `src/gateway/core.ts`                                  | Trigger episodic extraction on session depth increase      |
| 5    | `src/gateway/handlers/context-builder.ts` (or core.ts) | Wire MemoryBus if feasible                                 |

---

## Rollback Plan

Each step is independently reversible:

- Step 1: Remove `lastAssistantResponse` field and the 2 usages
- Step 2: Revert continuity block to label-only version
- Step 3: Remove the `priorTopicSummary` assignment (it's already optional)
- Step 4: Revert episodic trigger condition
- Step 5: Remove MemoryBus call
