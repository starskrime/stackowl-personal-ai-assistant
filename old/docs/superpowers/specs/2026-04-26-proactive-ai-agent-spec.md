# StackOwl: AI Agent Research — Requirements & Problem Specification

**Date:** 2026-04-26
**Type:** Research Document / Requirements Specification
**Status:** Draft — awaiting user review

---

## Executive Summary

StackOwl is a sophisticated multi-agent AI framework with extensive infrastructure for tool use, memory, knowledge management, and owl personality evolution. However, after deep code analysis, critical behavioral deficiencies emerge as systemic patterns — not incidental bugs but architectural choices:

1. **Reactive-only operation** — The assistant never initiates contact, follows up, or acts without a user message first
2. **Frozen evolution** — Critical personality traits cannot mutate, trend analysis is not fed to the evolution LLM, and proactive learning is disabled
3. **Shallow tool mastery** — Tool selection is re-ranked by recency but not by learned effectiveness; fallback chains are hardcoded; no per-tool mastery levels
4. **Answering instead of delivering** — The system treats any non-empty text as task completion; outcome verification does not exist; the model self-reports completion via `[DONE]` with no independent check
5. **No curiosity architecture** — The system explicitly avoids asking clarifying questions; gap detection is for *technical* gaps only, not *communicative* gaps; ambiguous requests are guessed rather than clarified
6. **Parliament never invoked** — Multi-owl debate exists but all automatic triggering paths are dead code; confidence gates silently downgrade PARLIAMENT before execution
7. **No delegation** — The delegation infrastructure exists but is completely disconnected; two parallel routing systems exist but only the one that doesn't produce delegation decisions is used
8. **Subagent systems are orphans** — `SwarmCoordinator`, `TaskDecomposer`, `SubOwlRunner`, `TriageClassifier` are all fully implemented but never instantiated or wired
9. **Pellets perpetually empty** — Pellet retrieval infrastructure works perfectly but generation triggers are rare or disabled
10. **Context history failures** — The assistant loses track of old conversations; follow-up questions referencing earlier context fail silently; context compaction discards important information without warning

---

## Part I: Problem Statement — The "Whys"

### 1. Why Is the Assistant Not Proactive?

**Root cause: The Gateway is a pure request-response processor.**

The `OwlGateway.handle()` is the single entry point for all message processing. Every meaningful action flows from: channel adapter receives message → `Gateway.handle()` → `OwlEngine.run()` → response. There is no `sendProactive()` method or autonomous outbound path.

**Proactive systems exist but are not wired to users:**

| System | Present | Active | Delivers to User |
|--------|---------|--------|-----------------|
| `ProactivePinger` (heartbeat) | Yes | Yes, timer-based | **No** — event bus fallback silently drops pings |
| `IdleActivityEngine` | Yes | Yes, after 5min idle | **No** — only internal artifact generation |
| `PerchManager` (file watchers) | Yes | Yes, file events | **No** — no broadcast callback wired in CLI |
| `ProactiveIntentionLoop` | Yes | Via heartbeat check-in | **No** — only via ProactivePinger which drops pings |
| `CognitiveLoop` | Yes | Runs continuously | **No** — internal self-improvement only |

---

### 2. Why Is the Assistant Not Evolving?

**Root cause: Evolution is gated, incomplete, and disconnected from learning systems.**

**A. Six DNA traits are immutable by design.**

`evolvedTraits` includes: `humor`, `formality`, `proactivity`, `riskTolerance`, `teachingStyle`, `delegationPreference` — all marked as never mutated. The evolution LLM is told about these traits but instructed not to change them.

**B. `domainConfidence` in DNA is dead code.**

`domainConfidence: Record<string, number>` is defined but **never written anywhere**. An owl could develop expertise in "rust" but has no confidence score for how assertive to be when recommending it.

**C. `EvolutionTrendAnalyzer` gate output is never fed to the evolution LLM.**

`analyze()` produces `frozenTraits`, `avoidMutationTypes`, `preferMutationTypes` — but `toGuardPrompt()` is never called. The evolution LLM proceeds without knowing which traits are oscillating or which mutation types to avoid.

**D. Proactive learning is fully disabled.**

`LearningOrchestrator.runProactiveSession()` is explicitly a no-op. Comment: "DISABLED. Previously deep-researched random knowledge graph topics, burning tokens. Learning now only happens reactively."

**E. Evolution triggers on message count, not outcome quality.**

Evolution fires every `evolutionBatchSize` messages (default 5) regardless of whether those sessions succeeded or failed.

**F. Tool failure diagnostics do not reach evolution.**

The evolution prompt receives aggregated reward by tool-pair pattern, but NOT: specific error types (ENOENT vs. EACCES vs. timeout), whether the same tool failed for the same reason previously, or arguments that were used.

**G. Skill synthesis failures don't adjust mutation strategy.**

Failed skill synthesis is tracked in `synthesisMemory` table but never feeds back into DNA evolution.

---

### 3. Why Does the Assistant Have Limited Tool Mastery?

**Root cause: Tool learning is re-ranking, not modeling; fallback chains are static.**

**A. Tool selection is limited to 8 of 100+ tools per turn.**

`ToolIntentRouter` caps at `maxTools = 8`. With 100+ tools, most are hidden. BM25 + usage-weighted selection may miss the right tool.

**B. `TOOL_FALLBACKS` is hard-coded, not learned.**

The system does not discover that "when `web_search` fails for technical docs, try `api_tester` instead." Every failure uses the same static fallback chain.

**C. DNA-based tool prioritization uses static `DOMAIN_TOOL_MAP`.**

`computeToolPriority()` uses a hard-coded map. Tool success/failure data does not update this map.

**D. No per-tool mastery levels.**

`ToolTracker` stores stats but these are only used for re-ranking multipliers. The model never sees "you are expert at `run_shell_command`".

**E. No tool-vs-task effectiveness matrix.**

`ApproachLibrary` records outcomes per (owl, tool, task) tuple but doesn't build a predictive model.

**F. No cross-owl tool learning.**

Each owl's `ApproachLibrary` is per-owl. If owl A discovers a better approach, owl B never benefits.

---

### 4. Why Does the Assistant Give Answers Instead of Delivering Outcomes?

**Root cause: Task completion is self-reported and never verified.**

**A. `isTaskComplete()` treats any non-empty text as completion.**

A response that says "I've set up the basic structure" with one tool call is considered complete — regardless of whether the user's actual goal was achieved.

**B. The ReAct loop terminates on `[DONE]` signal, not outcome verification.**

The model **self-reports** completion — there is no independent verification that the underlying intent was fulfilled.

**C. "Sovereign Entity Constitution" is aspirational, not enforced.**

The system prompt instructs "Do not hit `[DONE]` until the entire workflow is pristine" but this is a prompt instruction, not an architectural constraint.

**D. Sub-owl runners cannot execute tools.**

The sub-owl ReAct loop runs chat-only — it can reason but not execute. Delegated subtasks return text, not verified outcomes.

**E. Ambiguous requests are guessed, not clarified.**

"Assumption Over Interruption" principle says: "If a user gives a vague request, make an educated guess, execute it, and hand them the result." This means vague requests produce confident-but-wrong executions.

---

### 5. Why Does the Assistant Not Ask Questions When Stuck?

**Root cause: There is no curiosity architecture; gap detection is for technical gaps only.**

**A. The gap detector ignores question-asking behavior.**

The classifier prompt explicitly says to answer NO if the AI is "asking for clarification". The gap detector was designed to avoid flagging question-asking behavior as a capability gap.

**B. The engine never routes the model back to the user for clarification.**

The exhaustion self-correction prompt says: "DO NOT give up. DO NOT ask the user for help unless you have exhausted radically different strategies." The system explicitly avoids user-facing clarification mid-execution.

**C. `TriageClassifier` never produces `NEED_CLARIFICATION`.**

Messages are routed into `DIRECT | AGENTIC | DELEGATE | PARLIAMENT` — even genuinely ambiguous messages are forced into one of the four buckets with a guessed path.

**D. `IntentStateMachine` has `waiting_on_user` status but only for owl commitments.**

`waiting_on_user` is set when the *owl* is waiting, not when the *model* needs clarification.

**E. "Curiosity" in `OwlInnerLife` is introspective, not dialogic.**

`monologueToDirective()` only generates style/approach guidance. The inner monologue never produces "ask the user about X".

---

### 6. Why Is Parliament Never Invoked?

**Root cause: Automatic triggering paths are all dead code, and confidence gates silently downgrade PARLIAMENT before execution.**

**A. `TriageClassifier` is never instantiated.**

`PARLIAMENT_PATTERNS` exist but `new TriageClassifier` has zero matches in the codebase.

**B. `shouldConveneParliament()` in `parliament/detector.ts` is defined but never called.**

Only appears in a comment. The LLM-based auto-detection path is dead code.

**C. `ParallelRunner.shouldTrigger()` has no callers.**

Defined but nothing invokes it.

**D. Confidence gates silently downgrade PARLIAMENT.**

If confidence < 0.5 → STANDARD. If confidence < 0.65 → SPECIALIST. Since classifier confidence typically falls between 0.5–0.65, PARLIAMENT gets demoted before execution.

**E. The classifier prompt restricts PARLIAMENT to "dilemmas/tradeoffs."**

Most user queries don't match this framing.

**F. Only manual invocation paths exist.**

Parliament can only be triggered via CLI command, REST API, or the `summon_parliament` tool — which the system prompt frames as "slow and expensive."

---

### 7. Why Does the Assistant Do Everything Himself and Not Delegate?

**Root cause: The delegation infrastructure exists but is completely disconnected from the routing path.**

**A. `TriageClassifier` produces `DELEGATE` but nothing consumes it.**

`handleCore()` never calls `TriageClassifier` — it calls `classifyStrategy()` which produces `PLANNED`/`SWARM`, not `DELEGATE`.

**B. `SubOwlRunner` and `TaskDecomposer` are never instantiated.**

Both are defined in `GatewayContext` as optional fields but never passed to `buildGateway()`.

**C. `orchestrate_tasks` tool relies on LLM self-selection with no systematic trigger.**

The model must voluntarily choose to call it based purely on its own reasoning.

**D. `delegationPreference` DNA trait is vestigial.**

Defined but never read in routing/orchestration code.

**E. Goal loop escalation (STANDARD → PLANNED → SWARM) runs in the same engine, not true delegation.**

Each escalation still runs through the **same** `OwlEngine.run()` — it never spawns a separate sub-owl.

---

### 8. Why Do Subagent Systems Exist but Are Never Used?

**Root cause: Three subagent systems are fully implemented but never instantiated, wired, or initialized at startup.**

| System | Instantiated? | Wired? | Used? |
|--------|--------------|--------|-------|
| `SwarmCoordinator` | **NO** | Optional/undefined | **NO** |
| `LocalSwarmNode` | **NO** | Optional/undefined | **NO** |
| `TaskDecomposer` | **NO** | N/A | **NO** |
| `SubOwlRunner` | **NO** | Optional/undefined | **NO** |
| `EnvironmentScanner` | **NO** | N/A | **NO** |
| `DefaultAgentRegistry` | YES (empty) | YES | **NO** (empty) |
| `TriageClassifier` | **NO** | Optional/undefined | **NO** |

**`AgentRegistry` is instantiated but hollow:** No agent ever calls `agentRegistry.register`.

**Two parallel routing systems exist but only one runs:** `TriageClassifier` produces `DIRECT | AGENTIC | DELEGATE | PARLIAMENT`. `classifyStrategy` produces `DIRECT | STANDARD | SPECIALIST | PLANNED | PARLIAMENT | SWARM`. The gateway only calls `classifyStrategy`. The `DELEGATE` triage result is a zombie decision.

---

### 9. Why Are Pellets Never Generated or Used?

**Root cause: Pellet retrieval infrastructure is fully functional, but pellet generation triggers are rare or disabled. The store is perpetually empty.**

**Generation triggers (all rare or disabled):**

| Trigger | Frequency |
|---------|-----------|
| `summon_parliament` tool | LLM rarely calls (framed as "slow and expensive") |
| Desire executor research | Only if background scheduler runs |
| Self-seed on empty store | Once on first startup only |
| 2 AM self-study | **DISABLED** |

**Retrieval triggers (all active on every message):**
`buildSystemPrompt`, `MemoryFirstContextBuilder`, per-iteration injection, `MemoryBus`, `behavioralPatchContext`.

**The disconnect:** Retrieval works perfectly — every message searches the pellet store. But the store is empty because generation triggers almost never fire. The proactive knowledge pipeline (`maybeKnowledgeCouncil()`, `maybeDream()`, `maybeEvolveSkills()`) is fully disabled.

---

### 10. Why Does the Assistant Lose Track of Old Conversations?

**Root cause: Multiple context truncation and isolation mechanisms silently discard history, and the system has no awareness of what was lost.**

**A. 50-message hard cap discards older messages without warning.**

When a session exceeds 50 messages, only the **last 50** are retained. Older messages are compressed into `summaries` table if 20+ have accumulated — otherwise simply discarded. The assistant has no signal that context was truncated.

**B. Isolation pattern incorrectly triggers on follow-up references.**

At `runtime.ts`, `isNewTask` regex matches phrases like "actually, let me clarify something from earlier". When triggered, the system explicitly tells the LLM: "The previous conversation history below is for REFERENCE ONLY. Do NOT continue from where the previous conversation left off." This means legitimate follow-up questions referencing old context get context isolation applied.

**C. `isolatedTask` flag flushes session history from external callers.**

Any channel/tool that sets `isolatedTask = true` causes the ReAct loop to use only the last 2 messages, ignoring the full session history.

**D. Continuity misclassification sends wrong prior context.**

The `ContinuityEngine` classifies messages as `CONTINUATION | FOLLOW_UP | TOPIC_SWITCH | FRESH_START`. If a user says "as I mentioned earlier" but the temporal gap is small and no linguistic markers match, the system may classify as `CONTINUATION` rather than `FRESH_START`. The wrong prior response context is injected based on misclassification.

**E. Semantic search thresholds filter out relevant episodic memory.**

Episodic search uses relevance thresholds (0.2 for temporal triggers, 0.35 for normal messages). If the old conversation's embedding similarity falls below threshold, it won't be retrieved — even if the user explicitly references it with "remember when X" or "you told me Y".

**F. Temporal trigger detection misses implicit references.**

`TEMPORAL_TRIGGERS` regex only matches explicit phrases like "yesterday", "last time", "remember when". If the user says "following up on the API issue" without those keywords, the system does not query episodic memory for the relevant prior session.

**G. `MemoryBus` recall is capped at 2500 characters.**

`memoryBus.recall(userMessage, 10, 2500)` — only 2500 characters of recalled content go into the system prompt. Relevant older context may be truncated.

**H. Compression summary may lose critical decision context.**

When history exceeds 20 messages, the oldest batch is compressed into a `[MEMORY BLOCK]` via LLM summarization. The summary captures "task", "accomplished", "keyFacts", "decisions", "failedApproaches", "openQuestions" — but nuance, qualifications, and context around decisions may be lost. If the user's follow-up depends on understanding the reasoning behind a past decision, the compressed summary may not suffice.

---

### 11. Why Does the Assistant Not Understand What User Is Referring To in Follow-Up Questions?

**Root cause: The system cannot connect follow-up questions to the specific prior context they reference.**

**A. No explicit message-to-prior-message linking.**

When the user says "like I said earlier" or "following up on that", the system has no mechanism to identify *which* prior message or *which* specific point the user is referencing. The `continuityContext` block provides the last assistant response verbatim, but it does not provide the broader conversation structure needed to understand what "that" refers to.

**B. `continuityContext` only provides the last response, not the thread.**

For `FOLLOW_UP`/`CONTINUATION`, the system injects up to 2000 characters of the last assistant response. But if the user's question references something from 5 messages ago, not the last message, the assistant receives no direct signal about it. The user says "as for the authentication approach we discussed" — the system doesn't know which message contained that discussion.

**C. No conversation thread summary for multi-message references.**

When the user references something from the middle of a conversation thread (not the most recent message), the system provides either: the full verbatim last response (too recent), or the compression summary (too compressed), or nothing (if classified as `FRESH_START`). There is no targeted retrieval of the specific region of conversation being referenced.

**D. Episodic memory search may fail on implicit topic references.**

`episodicMemory.searchWithScoring()` uses semantic embeddings and recency decay. If the user references "the plan we settled on for the deployment", the search may not match if the episode summary uses different phrasing (e.g., "decided on blue-green deployment strategy"). FTS5 fallback requires literal substring matching which also fails on paraphrasing.

**E. No "you mentioned X" confirmation mechanism.**

When the system fails to retrieve relevant context for a follow-up, it does not ask "did you mean X from our conversation on [date]?" It either proceeds with insufficient context (wrong answer) or declares inability to help.

---

### 12. Why Does Context Compaction Lose Information Without User Awareness?

**Root cause: Compaction is silent, invisible to the user, and happens without any signal that important information was discarded.**

**A. Hard cap at 50 messages discards without notification.**

`SessionManager` silently slices to the last 50 messages. No `[CONTEXT TRUNCATED]` signal is sent to the user. The assistant doesn't know what was lost.

**B. Token-based compression operates invisibly.**

When `estimatedTokens > maxContextTokens`, older messages are compressed into a `[MEMORY BLOCK]` — a single LLM-generated summary. The user receives no indication that the original messages were replaced with a summary. If a tool result 30 messages ago contained specific data the user is now asking about, the assistant working from the summary may have lost access to that specific data point.

**C. Batch summarization extracts structured facts but loses conversational nuance.**

`MessageCompressor` extracts: `task`, `accomplished`, `keyFacts`, `decisions`, `failedApproaches`, `openQuestions`. This is useful for factual recall but loses: tone of discussion, user preferences expressed in passing, qualifications and caveats, relationship dynamics, and context around decisions.

**D. LLM summarization may introduce inaccuracies.**

The compression summary is generated by an LLM. It may omit details the user considers important, summarize details inaccurately, or conflate separate topics. If the original messages contained a nuanced discussion about why a particular approach was chosen, the summary may only record "chose approach X" without the reasoning.

**E. No tiered preservation for important context.**

The system treats all messages equally for compression purposes. A message containing a critical user preference ("I always want you to use TypeScript"), a sensitive commitment, or a specific constraint is compressed the same way as a casual aside. There's no mechanism to mark certain content as "never compress" or "preserve verbatim longer."

**F. Tool results are second-class citizens in compression.**

Tool call results (file contents, command outputs, API responses) are part of the message history but may receive less weight in LLM summarization than conversational text. A 50-message session that included 30 tool calls may have its compression summary weighted heavily toward the conversational framing rather than the actual outputs.

---

## Part II: Requirements

### R1: Learning & Evolution

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R1.1 | All 6 currently immutable DNA traits MUST become mutable | Evolution LLM can propose mutations for humor, formality, proactivity, riskTolerance, teachingStyle, delegationPreference |
| R1.2 | `domainConfidence` MUST be written and read by the evolution system | Evolution LLM receives confidence scores; growth influences assertiveness |
| R1.3 | `EvolutionTrendAnalyzer.toGuardPrompt()` MUST be called and injected into the evolution prompt | The LLM knows which traits are frozen and which mutation types to avoid |
| R1.4 | Proactive learning MUST be re-enabled with a token-efficient implementation | `runProactiveSession()` studies topics related to user's recent activity |
| R1.5 | Evolution MUST consider session success rate, not only message count | Evolution gates on satisfaction metrics when available |
| R1.6 | Evolution prompt MUST receive specific tool failure diagnostics | Error types (ENOENT, EACCES, timeout), not just binary success/failure |
| R1.7 | Skill synthesis failure patterns MUST feed back into DNA evolution strategy | If gap type X consistently fails, evolution LLM tries different approaches |
| R1.8 | MicroLearner patterns MUST influence DNA mutation direction | If user uses `run_shell_command` 50 times, owl develops shell expertise |
| R1.9 | Cross-session tool effectiveness MUST be tracked and modeled | `ApproachLibrary` informs tool selection beyond simple recency |

### R2: Tool Mastery

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R2.1 | `TOOL_FALLBACKS` MUST be learned from experience, not static | System discovers and records successful fallback sequences; fallbacks update over time |
| R2.2 | Per-tool mastery levels MUST be visible to the model | System prompt includes "you are expert/proficient/familiar with tool X" |
| R2.3 | DNA `DOMAIN_TOOL_MAP` MUST update based on accumulated tool outcomes | Tool prioritization in decision-layer reflects historical success/failure |
| R2.4 | Cross-session tool-vs-task effectiveness MUST be modeled | System can predict "tool X succeeds 90% for task type Y" |
| R2.5 | Cross-owl tool learning MUST be supported | Tool effectiveness data is shared across owl instances |
| R2.6 | Tool success rate threshold MUST trigger warning or retirement | If tool成功率 < 40% over 10 uses, alert or retire |
| R2.7 | `maxTools` routing MUST be configurable and tunable | 8-tool default is adjustable based on model context window |

### R3: Delivery & Ownership

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R3.1 | `isTaskComplete()` MUST verify outcome, not just content presence | Completion check validates that the user's stated goal was achieved |
| R3.2 | The ReAct loop MUST NOT terminate on `[DONE]` signal alone | `[DONE]` is a signal, not a guarantee — outcome verification required |
| R3.3 | Sub-owl runners MUST be able to execute tools, not just reason | Delegated subtasks can call actual tools; outcomes are verifiable |
| R3.4 | The system MUST track active tasks with ownership assertion | "I am working on X" state with progress updates, not just text responses |
| R3.5 | Ambiguous requests MUST trigger clarification before heavy execution | System asks before guessing |
| R3.6 | Multi-step task completion MUST be verified per-step | Each wave's results are validated against the subtask's goal before synthesis |

### R4: Curiosity & Question-Asking

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R4.1 | The gap detector MUST flag communicative gaps, not just technical ones | Model "I don't understand what you mean" triggers gap detection |
| R4.2 | The system MUST support mid-execution clarification routing | When uncertain about intent, model asks instead of guessing |
| R4.3 | `TriageClassifier` MUST support `NEED_CLARIFICATION` decision | Ambiguous messages can return a clarification request, not a guessed path |
| R4.4 | `IntentStateMachine.waiting_on_user` MUST be settable for understanding gaps | Owl can surface "I'm unclear about X from your last message" |
| R4.5 | `OwlInnerLife` curiosity/desires MUST be able to produce dialogic output | Inner monologue can generate "ask the user about X" directives |
| R4.6 | The "do not ask clarifying questions" constraint MUST be removed from SubOwlRunner | Sub-owls can ask clarifying questions when needed |
| R4.7 | `Assumption Over Interruption` MUST be balanced with "verify understanding" | Vague requests trigger either confident execution OR clarification — contextual decision |
| R4.8 | The Risk Gate "ask user" pattern MUST be generalized to curiosity | Any high-uncertainty situation can route back to the user |

### R5: Parliament & Multi-Owl Debate

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R5.1 | `TriageClassifier` MUST be instantiated and called in `handleCore()` | PARLIAMENT_PATTERNS are evaluated against every message |
| R5.2 | `shouldConveneParliament()` from `parliament/detector.ts` MUST be called | LLM-based auto-detection is wired into the routing path |
| R5.3 | `ParallelRunner.shouldTrigger()` MUST be invoked | Static auto-trigger utility is connected |
| R5.4 | Confidence gates MUST NOT silently downgrade PARLIAMENT | PARLIAMENT selected → PARLIAMENT executed (no automatic downgrade) |
| R5.5 | The `summon_parliament` tool description MUST NOT frame it as "slow and expensive" | Tool is presented as a first-class option for appropriate topics |
| R5.6 | `parliament/detector.ts` MUST be imported and used in `classifier.ts` | Dead code function is wired into production path |

### R6: Delegation & Sub-Agent Systems

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R6.1 | `TriageClassifier` `DELEGATE` decision MUST be handled | `handleCore()` routes DELEGATE to `TaskDecomposer` + `SubOwlRunner` |
| R6.2 | `TaskDecomposer` and `SubOwlRunner` MUST be instantiated at startup | Both classes are constructed and passed to `buildGateway()` |
| R6.3 | `orchestrate_tasks` tool MUST have a proactive trigger, not just LLM self-selection | System prompts toward delegation for multi-step tasks; threshold-based suggestion |
| R6.4 | `delegationPreference` DNA trait MUST be read in routing logic | `DNADecisionLayer` reads trait; autonomous owls delegate more readily |
| R6.5 | Goal loop escalation (STANDARD→PLANNED→SWARM) MUST spawn actual sub-agents, not re-use primary engine | SWARM at attempt 3 spawns separate `SubOwlRunner` instances |
| R6.6 | `SubOwlRunner` MUST support real tool execution, not chat-only | Sub-owls can call actual tools; outcomes are verifiable |
| R6.7 | `AgentRegistry` MUST have registered agents or be removed as dead code | Either agents register, or the hollow registry is removed |

### R7: Knowledge Pellets

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R7.1 | Pellet generation MUST NOT depend only on `summon_parliament` | Conversation summaries, significant tool sequences, and decisions generate pellets |
| R7.2 | `maybeKnowledgeCouncil()`, `maybeDream()`, `maybeEvolveSkills()` MUST be re-enabled | Proactive knowledge generation is restored with token-efficient implementation |
| R7.3 | Pellet store MUST NOT be empty after normal operation | Generation triggers fire sufficiently to populate the store |
| R7.4 | Pellets MUST be generated from desire executor research | `evolution/desire-executor.ts:63` pellet generation is active |
| R7.5 | Pellet retrieval results MUST be actively used, not just searched | Retrieved pellets inform responses; empty results don't mean "no pellets needed" |

### R8: Context History & Conversation Continuity

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R8.1 | Follow-up questions referencing old context MUST retrieve the correct prior messages | When user says "as I mentioned earlier", system identifies and retrieves the specific referenced content — not just the last message |
| R8.2 | Context compaction MUST signal what was lost | When messages are compressed or truncated, the system prompt includes `[CONTEXT TRUNCATED: N messages from earlier session]` so the model knows context may be incomplete |
| R8.3 | `continuityContext` MUST support multi-message references, not just last response | When user references something from 5 messages ago, the system retrieves that specific region, not just the last assistant response |
| R8.4 | Temporal trigger detection MUST handle implicit references | Phrases like "following up on the API issue" without explicit "remember when" trigger episodic memory search |
| R8.5 | Episodic memory search MUST use lower thresholds for explicit user references | When user says "you told me X" or "as we discussed about Y", the retrieval threshold is lower than for normal messages |
| R8.6 | `isNewTask` isolation MUST NOT apply to legitimate follow-up questions | Phrases containing "earlier", "mentioned", "discussed", "as we agreed" do not trigger context isolation — they trigger context retrieval instead |
| R8.7 | The system MUST confirm understanding when context retrieval fails | When system cannot find the referenced content, it asks "did you mean X from our conversation on [date]?" rather than proceeding with no context |
| R8.8 | Hard cap (50 messages) MUST preserve critical user preferences and commitments verbatim | User statements about preferences, constraints, and commitments are excluded from the 50-message cap or preserved in a dedicated layer |
| R8.9 | Compression summary MUST preserve reasoning behind decisions, not just outcomes | "Chose approach X because Y" not just "chose approach X" |
| R8.10 | Tiered compression MUST be implemented | Critical context (preferences, commitments, constraints) is preserved verbatim or at higher fidelity than casual conversation |
| R8.11 | Tool results MUST be first-class citizens in compression decisions | Tool outputs are weighted equally to conversational text; specific data points from tool results are preserved in compression summaries |

### R9: Proactive Behavior

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R9.1 | The Gateway MUST expose a `sendProactive()` path distinct from `handle()` | Proactive items from `ProactiveIntentionLoop` are delivered to the user |
| R9.2 | Proactive pings MUST NOT silently drop when event bus is unavailable | A fallback path delivers pings via the active channel adapter |
| R9.3 | `ProactiveIntentionLoop` MUST support "information gap" and "needs clarification" item types | The owl can surface "I noticed you mentioned X but I'm unclear on Y" |

---

## Part III: Open Questions

These require user input before finalizing:

1. **Evolution pace**: How many conversations should trigger evolution? Currently 5. Too aggressive (oscillation) or too passive (slow learning)?

2. **Tool mastery display**: Should the model explicitly say "I'm not very good at X yet" — or is mastery implicit (just uses tool more)?

3. **Clarification threshold**: How ambiguous must a request be before the system asks instead of guesses?

4. **Outcome verification scope**: For which task types is outcome verification feasible? Should verification be task-type-dependent?

5. **Context truncation tolerance**: How should the system handle a user asking about something 60+ messages ago — by retrieving from episodic/summary stores, by warning of incomplete context, or by asking for clarification?

---

## Appendix: Key File References

| Finding | File | Lines |
|---------|------|-------|
| Gateway is pure request-response | `src/gateway/core.ts` | 431-457 |
| Heartbeat drops pings silently | `src/heartbeat/proactive.ts` | 813-825 |
| Disabled proactive learning | `src/heartbeat/proactive.ts` | 696-726 |
| Immutable DNA traits | `src/owls/persona.ts` | 35-48 |
| domainConfidence dead code | `src/owls/persona.ts` | 51 |
| TrendAnalyzer guard not fed to LLM | `src/evolution/trend-analyzer.ts` | (toGuardPrompt never called) |
| TOOL_FALLBACKS hardcoded | `src/engine/runtime.ts` | 1100-1112 |
| isTaskComplete treats any text as complete | `src/orchestrator/orchestrator.ts` | 53-65 |
| [DONE] signal not verified | `src/engine/runtime.ts` | 175-189, 1138-1154 |
| Gap detector ignores question-asking | `src/evolution/detector.ts` | 118-129 |
| SubOwlRunner no tool execution | `src/delegation/sub-owl-runner.ts` | 185-190 |
| Assumption Over Interruption | `src/engine/runtime.ts` | 2340 |
| TriageClassifier never instantiated | `src/gateway/core.ts` | (zero matches for `new TriageClassifier`) |
| Parliament confidence downgrades | `src/gateway/core.ts` | 1193-1218 |
| DELEGATE triage decision never consumed | `src/triage/index.ts` | 144, no downstream handler |
| SubOwlRunner/TaskDecomposer never instantiated | `src/index.ts` | (zero matches for `new SubOwlRunner`) |
| Pellet retrieval works, generation disabled | `src/heartbeat/proactive.ts` | 696-726 |
| 50-message hard cap | `src/memory/store.ts` | 110 |
| isNewTask isolation pattern | `src/engine/runtime.ts` | 948-982 |
| Temporal trigger detection | `src/gateway/handlers/context-builder.ts` | 126-128 |
| Continuity engine classifications | `src/cognition/continuity-engine.ts` | (3-layer classification) |
| MemoryBus recall cap | `src/memory/bus.ts` | 224, 2500 chars |
| SessionManager MAX_SESSION_HISTORY | `src/memory/session-manager.ts` | 110 |
| Context compression triggers | `src/engine/runtime.ts` | 903-945 |
| MessageCompressor batch summarization | `src/memory/compressor.ts` | 58-153 |

---

*This specification captures the current state of the codebase and the behavioral problems identified. All "MUST" requirements are intended for future implementation.*