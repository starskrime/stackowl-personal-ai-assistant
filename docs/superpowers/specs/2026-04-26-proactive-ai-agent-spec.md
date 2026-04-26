# StackOwl: Proactive AI Agent Research — Requirements & Problem Specification

**Date:** 2026-04-26
**Type:** Research Document / Requirements Specification
**Status:** Draft — awaiting user review

---

## Executive Summary

StackOwl is a sophisticated multi-agent AI framework with extensive infrastructure for tool use, memory, knowledge management, and owl personality evolution. However, after deep code analysis, eight critical behavioral deficiencies emerge as systemic patterns — not incidental bugs but architectural choices that manifest as the symptoms the user experiences:

1. **Reactive-only operation** — The assistant never initiates contact, follows up, or acts without a user message first
2. **Frozen evolution** — Critical personality traits cannot mutate, trend analysis is not fed to the evolution LLM, and proactive learning is disabled
3. **Shallow tool mastery** — Tool selection is re-ranked by recency but not by learned effectiveness; fallback chains are hardcoded; no per-tool mastery levels
4. **Answering instead of delivering** — The system treats any non-empty text as task completion; outcome verification does not exist; the model self-reports completion via `[DONE]` with no independent check
5. **No curiosity architecture** — The system explicitly avoids asking clarifying questions; gap detection is for *technical* gaps only, not *communicative* gaps; ambiguous requests are guessed rather than clarified
6. **Parliament never invoked** — Multi-owl debate exists but all automatic triggering paths are dead code; confidence gates silently downgrade PARLIAMENT before execution
7. **No delegation** — The delegation infrastructure exists but is completely disconnected; two parallel routing systems exist but only the one that doesn't produce delegation decisions is used
8. **Subagent systems are orphans** — `SwarmCoordinator`, `TaskDecomposer`, `SubOwlRunner`, `TriageClassifier` are all fully implemented but never instantiated or wired; the `AgentRegistry` is created empty and never used
9. **Pellets perpetually empty** — Pellet retrieval infrastructure works perfectly but generation triggers are rare or disabled; the proactive knowledge pipeline is gutted

---

## Part I: Problem Statement — The "Whys"

### 1. Why Is the Assistant Not Proactive?

**Root cause: The Gateway is a pure request-response processor.**

The `OwlGateway.handle()` (`src/gateway/core.ts:431`) is the single entry point for all message processing. Every meaningful action in the system flows from: channel adapter receives message → `Gateway.handle()` → `OwlEngine.run()` → response. There is no `sendProactive()` method or autonomous outbound path in the Gateway.

**Proactive systems exist but are not wired to users:**

| System | Present | Active | Delivers to User |
|--------|---------|--------|-----------------|
| `ProactivePinger` (heartbeat) | Yes | Yes, timer-based | **No** — event bus fallback silently drops pings |
| `IdleActivityEngine` | Yes | Yes, after 5min idle | **No** — only internal artifact generation |
| `PerchManager` (file watchers) | Yes | Yes, file events | **No** — no broadcast callback wired in CLI |
| `ProactiveIntentionLoop` | Yes | Via heartbeat check-in | **No** — only via ProactivePinger which drops pings |
| `CognitiveLoop` | Yes | Runs continuously | **No** — internal self-improvement only |

- `ProactivePinger.generateAndSend()` (`src/heartbeat/proactive.ts:813-825`) emits to event bus; comment says "EventBus not available, dropping ping"
- `maybeKnowledgeCouncil()`, `maybeDream()`, `maybeEvolveSkills()` are all explicitly disabled with `return;` statements — comments say "DISABLED — burned tokens proactively. Learning now only happens reactively."
- `PerchManager` at `src/perch/manager.ts:101` only prints to console; the optional `broadcast` callback is not wired in CLI mode
- The cognitive loop updates the knowledge base internally but never produces user-facing output

**Architecture-level consequence:** The entire reactive pattern is self-reinforcing. The `IdleActivityEngine` cancels in-progress work the moment a user message arrives (`src/heartbeat/idle-engine.ts:133`). The system treats user input as an interruption of proactive work, not as a collaboration signal.

---

### 2. Why Is the Assistant Not Evolving?

**Root cause: Evolution is gated, incomplete, and disconnected from learning systems.**

**A. Six DNA traits are immutable by design.**

In `src/owls/persona.ts:35-48`, the DNA schema defines these as part of `evolvedTraits`:
```typescript
evolvedTraits: {
  humor: number;              // ❌ Never mutated
  formality: number;          // ❌ Never mutated
  proactivity: number;       // ❌ Never mutated — a proactive trait that cannot evolve!
  riskTolerance: string;      // ❌ Never mutated
  teachingStyle: string;     // ❌ Never mutated
  delegationPreference: string; // ❌ Never mutated
}
```

These traits are visible to the evolution LLM but cannot be changed. This means the system tells the LLM "here are personality traits" and then tells it "you cannot change 6 of them." The immutable traits may cause anchoring or misprioritization in the evolution LLM's mutation decisions.

**B. `domainConfidence` in DNA is dead code.**

`domainConfidence: Record<string, number>` in `persona.ts:51` is defined but **never written anywhere in the codebase**. An owl could develop expertise in "rust" but has no confidence score for how assertive to be when recommending it.

**C. `EvolutionTrendAnalyzer` gate output is never fed to the evolution LLM.**

`analyze()` produces `frozenTraits`, `avoidMutationTypes`, `preferMutationTypes` — but `toGuardPrompt()` is never called. The evolution LLM proceeds without knowing which traits are oscillating or which mutation types to avoid.

**D. Proactive learning is fully disabled.**

`LearningOrchestrator.runProactiveSession()` (`src/learning/orchestrator.ts:257-270`) is explicitly a no-op. The comment states: "DISABLED. Previously deep-researched random knowledge graph topics, burning tokens. Learning now only happens reactively (on failure via synthesis queue)."

**E. Evolution triggers on message count, not outcome quality.**

The evolution fires every `evolutionBatchSize` messages (default 5) regardless of whether those sessions succeeded or failed. The `MutationTracker` can freeze/rollback after the fact, but there's no pre-gate that considers session success rate.

**F. Tool failure diagnostics do not reach evolution.**

The evolution prompt receives aggregated reward by tool-pair pattern, but NOT:
- Specific error types (ENOENT vs. EACCES vs. timeout)
- Whether the same tool failed for the same reason previously
- Arguments that were used

**G. Skill synthesis failures don't adjust mutation strategy.**

Failed skill synthesis is tracked in `synthesisMemory` table but never feeds back into DNA evolution. If capability gaps of type X consistently fail, the evolution engine has no awareness.

---

### 3. Why Does the Assistant Have Limited Tool Mastery?

**Root cause: Tool learning is re-ranking, not modeling; fallback chains are static.**

**A. Tool selection is limited to 8 of 100+ tools per turn.**

`ToolIntentRouter` (`src/tools/registry.ts:146`) caps at `maxTools = 8`. With 100+ tools in the registry, most are hidden from the model each turn. The BM25 + usage-weighted selection may miss the right tool if it has low name/description similarity.

**B. `TOOL_FALLBACKS` is hard-coded, not learned.**

At `src/engine/runtime.ts:1100-1112`:
```typescript
const TOOL_FALLBACKS: Record<string, string[]> = {
  web_crawl:         ["scrapling_fetch", "web_search", "run_shell_command"],
  // ...
};
```
The system does not discover that "when `web_search` fails for technical docs, try `api_tester` instead." Every failure uses the same static fallback chain.

**C. DNA-based tool prioritization uses static `DOMAIN_TOOL_MAP`.**

`computeToolPriority()` in `src/owls/decision-layer.ts:53-77` uses a hard-coded map. Tool success/failure data does not update this map.

**D. No per-tool mastery levels.**

`ToolTracker` stores stats (selection count, success/failure, avg duration) but these are only used for re-ranking multipliers. The model never sees "you are expert at `run_shell_command`" or "this tool has low success rate for your environment."

**E. No tool-vs-task effectiveness matrix.**

`ApproachLibrary` records outcomes per (owl, tool, task) tuple but doesn't build a predictive model. The system cannot answer "should I use `web_crawl` or `scrapling_fetch` for technical documentation?"

**F. No cross-owl tool learning.**

Each owl's `ApproachLibrary` is per-owl. If owl A discovers a better approach for a task type, owl B never benefits.

---

### 4. Why Does the Assistant Give Answers Instead of Delivering Outcomes?

**Root cause: Task completion is self-reported and never verified.**

**A. `isTaskComplete()` treats any non-empty text as completion.**

At `src/orchestrator/orchestrator.ts:53-65`:
```typescript
function isTaskComplete(content: string): boolean {
  if (!content || content.trim().length === 0) return false;
  if (content.includes("__STACKOWL_EXHAUSTED__")) return false;
  return true; // Everything else is "complete"
}
```
A response that says "I've set up the basic structure" with one tool call is considered complete — regardless of whether the user's actual goal was achieved.

**B. The ReAct loop terminates on `[DONE]` signal, not outcome verification.**

At `src/engine/runtime.ts:175-189`, the engine checks for `hasDoneSignal()` and exits. At lines 1138-1154, the engine checks content for `[DONE]` before executing tools. The model **self-reports** completion — there is no independent verification that the underlying intent was fulfilled.

**C. "Sovereign Entity Constitution" is aspirational, not enforced.**

The system prompt instructs: "Fix it yourself. Do not hit `[DONE]` until the entire workflow is pristine." but this is a prompt instruction, not an architectural constraint. The engine has no mechanism to verify task outcome before accepting `[DONE]`.

**D. Sub-owl runners cannot execute tools.**

At `src/delegation/sub-owl-runner.ts:185-190`, the sub-owl ReAct loop runs chat-only — it can reason but not execute. Delegated subtasks return text, not verified outcomes.

**E. Ambiguous requests are guessed, not clarified.**

The `Assumption Over Interruption` principle (`src/engine/runtime.ts:2340`) instructs: "If a user gives a vague request, do not halt execution to ask 10 clarifying questions. Make an incredibly educated, opinionated guess based on ambient context, execute it, and hand them the result."

This means vague requests produce confident-but-wrong executions rather than clarifyingdialogs.

---

### 5. Why Does the Assistant Not Ask Questions When Stuck?

**Root cause: There is no curiosity architecture; gap detection is for technical gaps only.**

**A. The gap detector ignores question-asking behavior.**

At `src/evolution/detector.ts:118-129`, the classifier prompt explicitly says:
> Answer NO if the AI is: refusing for ethical/policy reasons, **asking for clarification**, saying it doesn't know a fact...

The gap detector was designed to avoid flagging question-asking behavior as a capability gap. This is a deliberate architectural choice.

**B. The engine never routes the model back to the user for clarification.**

At `src/engine/runtime.ts:1932-1947`, the exhaustion self-correction prompt says:
> "DO NOT give up. DO NOT ask the user for help unless you have exhausted radically different strategies."

The system explicitly avoids user-facing clarification mid-execution. The model is told to try 20+ approaches before considering asking the user.

**C. `TriageClassifier` never produces `NEED_CLARIFICATION`.**

At `src/triage/index.ts`, messages are routed into `DIRECT | AGENTIC | DELEGATE | PARLIAMENT` — even genuinely ambiguous messages are forced into one of the four buckets with a guessed path. The LLM fallback picks the "most likely" path, never asks the user.

**D. `IntentStateMachine` has `waiting_on_user` status but only for owl commitments.**

At `src/intent/state-machine.ts`, `waiting_on_user` is set when the *owl* is waiting, not when the *model* needs clarification. `buildStaleIntentMessage()` follows up on *owl commitments*, never on *what the owl is confused about*.

**E. "Curiosity" in `OwlInnerLife` is introspective, not dialogic.**

`OwlInnerLife` has `desires`, `mood: curious`, and `unspokenObservations` — but `monologueToDirective()` (`src/owls/inner-life.ts:482-497`) only generates style/approach guidance. The inner monologue never produces "ask the user about X."

**F. Risk Gate for destructive tools is the only "ask user" path.**

At `src/engine/runtime.ts:1420-1443`, the model is told to "Ask the user to confirm" only for destructive operations. This is a safety gate, not a curiosity mechanism.

---

## Part II: Additional Architectural Observations

### 6. Why Is There No Persistent Session Context Beyond Messages?

The `OwlGateway.handle()` processes each message as a fresh interaction (though with session history). There is no persistent "active task" state that the system proactively updates and reports on. The `GoalGraph` tracks goals but does not assert ownership or provide progress updates.

### 7. Why Does the System Discourage Clarification Even When Appropriate?

The `SubOwlRunner` explicitly says "Complete the subtask directly. Do not ask clarifying questions" (`src/delegation/sub-owl-runner.ts:155-157`). This principle propagates from the top-level instruction layer down to delegated subtasks, making question-asking structurally impossible even in contexts where it would be beneficial.

### 8. Why Is Self-Correction Focused on Tool Selection, Not Intent Verification?

The `DiagnosticEngine` generates multi-hypothesis fix directives — telling the model *what to try next* rather than *what information is missing*. Self-correction is tool-centric (try a different tool) rather than understanding-centric (clarify what the user actually wants).

### 9. Why Is Parliament Never Invoked?

**Root cause: Automatic triggering paths are all dead code, and confidence gates silently downgrade PARLIAMENT before execution.**

**A. `TriageClassifier` is never instantiated.**

`PARLIAMENT_PATTERNS` exist in `src/triage/index.ts:59-68` with regexes for "should", "pros and cons", "tradeoffs", "best way", etc. — but `new TriageClassifier` has zero matches in the codebase. These patterns are never evaluated against any message.

**B. `shouldConveneParliament()` in `parliament/detector.ts` is defined but never called.**

Only appears in a comment in `classifier.ts:4`. The LLM-based auto-detection path is dead code.

**C. `ParallelRunner.shouldTrigger()` has no callers.**

`src/parliament/parallel-runner.ts:182-202` defines an auto-trigger check but nothing invokes it.

**D. Confidence gates silently downgrade PARLIAMENT.**

At `src/gateway/core.ts:1193-1218`:
```typescript
if (strategy.confidence < 0.5) { strategy = "STANDARD" }
else if (strategy.confidence < 0.65) { strategy = "SPECIALIST" }
```
Since classifier confidence typically falls between 0.5–0.65 for non-dilemma queries, PARLIAMENT gets demoted before execution.

**E. The classifier prompt restricts PARLIAMENT to "dilemmas/tradeoffs."**

`src/orchestrator/classifier.ts:239` says: "PARLIAMENT only for genuine value, ethical, or architectural tradeoff dilemmas — NOT factual questions." Most user queries don't match this framing.

**F. Only manual invocation paths exist.**

Parliament can only be triggered via: CLI command (`stackowl parliament "topic"`), REST API (`POST /api/parliament`), or the `summon_parliament` tool — which the system prompt frames as "slow and expensive" and only hints for "high-stakes decisions requiring multiple perspectives."

---

### 10. Why Does the Assistant Do Everything Himself and Not Delegate?

**Root cause: The delegation infrastructure exists but is completely disconnected from the routing path. The system has two parallel routers and only one is used — the one that doesn't produce delegation decisions.**

**A. `TriageClassifier` produces `DELEGATE` but nothing consumes it.**

At `src/triage/index.ts:144`, the classifier can emit `DELEGATE` with `delegateTask` set. At `src/triage/index.ts:61-68`, `DELEGATE_PATTERNS` include multi-step phrases ("build/create/implement" + 20 chars, "step-by-step", etc.). But `handleCore()` in `gateway/core.ts` never calls `TriageClassifier` — it calls `classifyStrategy()` from `orchestrator/classifier.ts` which produces `PLANNED`/`SWARM`, not `DELEGATE`.

**B. `SubOwlRunner` and `TaskDecomposer` are never instantiated.**

Both are defined in `src/gateway/types.ts:308` as optional context fields but are **never passed** to `buildGateway()` in `index.ts`. Zero references to `new SubOwlRunner` or `new TaskDecomposer` exist in the codebase.

**C. The `DELEGATE` path from `depth-directive.ts:73` only sets "thorough" depth — no actual delegation handler exists.**

**D. `orchestrate_tasks` tool relies on LLM self-selection with no systematic trigger.**

The model must voluntarily choose to call `orchestrate_tasks` based purely on its own reasoning. There's no system prompt nudge toward delegation for complex tasks, and no threshold that proactively suggests it.

**E. `delegationPreference` DNA trait is vestigial.**

`src/owls/persona.ts:47,106` defines `delegationPreference: "autonomous" | "collaborative" | "confirmatory"` but the `DNADecisionLayer` never reads this field. The DNA trait has no effect on routing behavior.

**F. The goal loop escalation (STANDARD → PLANNED → SWARM) runs in the same engine, not true delegation.**

At `src/orchestrator/orchestrator.ts:326-330`, each escalation still runs through the **same** `OwlEngine.run()` — it never spawns a separate sub-owl. "SWARM" at attempt 3 is the same primary owl doing parallel thinking, not sub-agent spawning.

**G. Sub-owl runners cannot execute tools.**

At `src/delegation/sub-owl-runner.ts:185-190`: "Tool execution not available in sub-owl context." Even if delegation were wired, sub-owls can only reason, not act.

---

### 11. Why Do Subagent Systems Exist but Are Never Used?

**Root cause: Three subagent systems are fully implemented but completely orphaned — never instantiated, wired, or initialized at startup.**

| System | File | Instantiated? | Wired? | Used? |
|--------|------|--------------|--------|-------|
| `SwarmCoordinator` | `src/swarm/coordinator.ts` | **NO** | Optional/undefined | **NO** |
| `LocalSwarmNode` | `src/swarm/node.ts` | **NO** | Optional/undefined | **NO** |
| `TaskDecomposer` | `src/delegation/decomposer.ts` | **NO** | N/A | **NO** |
| `SubOwlRunner` | `src/delegation/sub-owl-runner.ts` | **NO** | Optional/undefined | **NO** |
| `EnvironmentScanner` | `src/delegation/env-scanner.ts` | **NO** | N/A | **NO** |
| `DefaultAgentRegistry` | `src/agents/registry.ts` | YES (empty) | YES | **NO** (empty) |
| `TriageClassifier` | `src/triage/index.ts` | **NO** | Optional/undefined | **NO** |

**`AgentRegistry` is instantiated but hollow:** `DefaultAgentRegistry` is created at `index.ts:818` and passed to `buildGateway()` at line 990 — but no agent ever calls `agentRegistry.register`. Zero matches for `agentRegistry.register` in the codebase.

**The SWARM strategy uses in-process parallelism only:** `TaskOrchestrator.executeSwarm()` (`orchestrator.ts:848-1031`) uses `SwarmBlackboard` for inter-agent communication and spawns parallel ReAct loops via `OwlEngine.run()` — but does **not** use `SwarmCoordinator` for distributed execution across nodes.

**Two parallel routing systems exist but only one runs:** `TriageClassifier` produces `DIRECT | AGENTIC | DELEGATE | PARLIAMENT`. `classifyStrategy` produces `DIRECT | STANDARD | SPECIALIST | PLANNED | PARLIAMENT | SWARM`. The gateway only calls `classifyStrategy`. The `DELEGATE` triage result is a zombie decision — produced but no handler exists anywhere.

---

### 12. Why Are Pellets Never Generated or Used?

**Root cause: Pellet retrieval infrastructure is fully functional, but pellet generation triggers are rare or disabled. The store is perpetually empty.**

**Generation triggers (all rare or disabled):**

| Trigger | Location | Frequency |
|---------|----------|-----------|
| `summon_parliament` tool | `parliament/orchestrator.ts:147` | LLM rarely calls (framed as "slow and expensive") |
| Desire executor research | `evolution/desire-executor.ts:63` | Only if background scheduler runs |
| Self-seed on empty store | `pellets/self-seed.ts:47` | Once on first startup only |
| 2 AM self-study | `heartbeat/proactive.ts:646` | **DISABLED** |

**Retrieval triggers (all active on every message):**

| Path | Location |
|------|----------|
| `buildSystemPrompt` | `runtime.ts:2481-2498` — every message |
| `MemoryFirstContextBuilder` | `memory/context-builder.ts:293` — non-conversational |
| Per-iteration injection | `runtime.ts:1787-1791` — after tools |
| `MemoryBus` | `memory/bus.ts:224` — cross-store search |
| `behavioralPatchContext` | `context-builder.ts:179-181` — every message |

**The disconnect:** Retrieval works perfectly — every message searches the pellet store. But the store is empty because generation triggers almost never fire during normal operation. The proactive knowledge pipeline (`maybeKnowledgeCouncil()`, `maybeDream()`, `maybeEvolveSkills()`) is fully disabled at `heartbeat/proactive.ts:696-726`.

---

## Part III: Requirements

### R1: Proactive Behavior

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R1.1 | The system MUST be able to initiate contact with the user without a prior user message | A heartbeat check-in or file change alert reaches the user via the active channel |
| R1.2 | The Gateway MUST expose a `sendProactive()` path distinct from `handle()` | Proactive items from `ProactiveIntentionLoop` are delivered to the user |
| R1.3 | Idle time (5+ minutes) MUST trigger background learning that can inform future interactions | `IdleActivityEngine` results are stored and retrievable in future sessions |
| R1.4 | Proactive pings MUST NOT silently drop when event bus is unavailable | A fallback path delivers pings via the active channel adapter |
| R1.5 | `ProactiveIntentionLoop` MUST support "information gap" and "needs clarification" item types | The owl can surface "I noticed you mentioned X but I'm unclear on Y" |
| R1.6 | User activity MUST NOT cancel proactive work — work should be queued, not aborted | `onUserActivity()` queues new work for after current task completion |

### R2: Evolution & Learning

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R2.1 | All 6 currently immutable DNA traits MUST become mutable | Evolution LLM can propose mutations for humor, formality, proactivity, riskTolerance, teachingStyle, delegationPreference |
| R2.2 | `domainConfidence` MUST be written and read by the evolution system | Evolution LLM receives confidence scores; growth influences assertiveness |
| R2.3 | `EvolutionTrendAnalyzer.toGuardPrompt()` MUST be called and injected into the evolution prompt | The LLM knows which traits are frozen and which mutation types to avoid |
| R2.4 | Proactive learning MUST be re-enabled with a token-efficient implementation | `runProactiveSession()` studies topics related to user's recent activity |
| R2.5 | Evolution MUST consider session success rate, not only message count | Evolution gates on satisfaction metrics when available |
| R2.6 | Evolution prompt MUST receive specific tool failure diagnostics | Error types (ENOENT, EACCES, timeout), not just binary success/failure |
| R2.7 | Skill synthesis failure patterns MUST feed back into DNA evolution strategy | If gap type X consistently fails, evolution LLM tries different approaches |
| R2.8 | MicroLearner patterns MUST influence DNA mutation direction | If user uses `run_shell_command` 50 times, owl develops shell expertise |
| R2.9 | Cross-session tool effectiveness MUST be tracked and modeled | `ApproachLibrary` informs tool selection beyond simple recency |

### R3: Tool Mastery

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R3.1 | `TOOL_FALLBACKS` MUST be learned from experience, not static | System discovers and records successful fallback sequences; fallbacks update over time |
| R3.2 | Per-tool mastery levels MUST be visible to the model | System prompt includes "you are expert/proficient/familiar with tool X" |
| R3.3 | DNA `DOMAIN_TOOL_MAP` MUST update based on accumulated tool outcomes | Tool prioritization in decision-layer reflects historical success/failure |
| R3.4 | Cross-session tool-vs-task effectiveness MUST be modeled | System can predict "tool X succeeds 90% for task type Y" |
| R3.5 | Cross-owl tool learning MUST be supported | Tool effectiveness data is shared across owl instances |
| R3.6 | Tool success rate threshold MUST trigger warning or retirement | If tool成功率 < 40% over 10 uses, alert or retire |
| R3.7 | `maxTools` routing MUST be configurable and tunable | 8-tool default is adjustable based on model context window |

### R4: Delivery & Ownership

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R4.1 | `isTaskComplete()` MUST verify outcome, not just content presence | Completion check validates that the user's stated goal was achieved |
| R4.2 | The ReAct loop MUST NOT terminate on `[DONE]` signal alone | `[DONE]` is a signal, not a guarantee — outcome verification required |
| R4.3 | Sub-owl runners MUST be able to execute tools, not just reason | Delegated subtasks can call actual tools; outcomes are verifiable |
| R4.4 | The system MUST track active tasks with ownership assertion | "I am working on X" state with progress updates, not just text responses |
| R4.5 | Ambiguous requests MUST trigger clarification before heavy execution | TriageClassifier can emit `NEED_CLARIFICATION`; system asks before guessing |
| R4.6 | Multi-step task completion MUST be verified per-step | Each wave's results are validated against the subtask's goal before synthesis |

### R5: Curiosity & Question-Asking

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R5.1 | The gap detector MUST flag communicative gaps, not just technical ones | Model "I don't understand what you mean" triggers gap detection |
| R5.2 | The system MUST support mid-execution clarification routing | When uncertain about intent, model asks instead of guessing |
| R5.3 | `TriageClassifier` MUST support `NEED_CLARIFICATION` decision | Ambiguous messages can return a clarification request, not a guessed path |
| R5.4 | `IntentStateMachine.waiting_on_user` MUST be settable for understanding gaps | Owl can surface "I'm unclear about X from your last message" |
| R5.5 | `OwlInnerLife` curiosity/desires MUST be able to produce dialogic output | Inner monologue can generate "ask the user about X" directives |
| R5.6 | The "do not ask clarifying questions" constraint MUST be removed from SubOwlRunner | Sub-owls can ask clarifying questions when needed |
| R5.7 | `Assumption Over Interruption` MUST be balanced with "verify understanding" | Vague requests trigger either confident execution OR clarification — contextual decision |
| R5.8 | The Risk Gate "ask user" pattern MUST be generalized to curiosity | Any high-uncertainty situation can route back to the user |

### R6: Parliament & Multi-Owl Debate

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R6.1 | `TriageClassifier` MUST be instantiated and called in `handleCore()` | PARLIAMENT_PATTERNS are evaluated against every message |
| R6.2 | `shouldConveneParliament()` from `parliament/detector.ts` MUST be called | LLM-based auto-detection is wired into the routing path |
| R6.3 | `ParallelRunner.shouldTrigger()` MUST be invoked | Static auto-trigger utility is connected |
| R6.4 | Confidence gates MUST NOT silently downgrade PARLIAMENT | PARLIAMENT selected → PARLIAMENT executed (no automatic downgrade) |
| R6.5 | The `summon_parliament` tool description MUST NOT frame it as "slow and expensive" | Tool is presented as a first-class option for appropriate topics |
| R6.6 | `parliament/detector.ts` MUST be imported and used in `classifier.ts` | Dead code function is wired into production path |

### R7: Delegation & Sub-Agent Systems

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R7.1 | `TriageClassifier` `DELEGATE` decision MUST be handled | `handleCore()` routes DELEGATE to `TaskDecomposer` + `SubOwlRunner` |
| R7.2 | `TaskDecomposer` and `SubOwlRunner` MUST be instantiated at startup | Both classes are constructed and passed to `buildGateway()` |
| R7.3 | `orchestrate_tasks` tool MUST have a proactive trigger, not just LLM self-selection | System prompts toward delegation for multi-step tasks; threshold-based suggestion |
| R7.4 | `delegationPreference` DNA trait MUST be read in routing logic | `DNADecisionLayer` reads trait; autonomous owls delegate more readily |
| R7.5 | Goal loop escalation (STANDARD→PLANNED→SWARM) MUST spawn actual sub-agents, not re-use primary engine | SWARM at attempt 3 spawns separate `SubOwlRunner` instances |
| R7.6 | `SubOwlRunner` MUST support real tool execution, not chat-only | Sub-owls can call actual tools; outcomes are verifiable |
| R7.7 | `AgentRegistry` MUST have registered agents or be removed as dead code | Either agents register, or the hollow registry is removed |

### R8: Knowledge Pellets

| ID | Requirement | Success Criterion |
|----|------------|-----------------|
| R8.1 | Pellet generation MUST NOT depend only on `summon_parliament` | Conversation summaries, significant tool sequences, and decisions generate pellets |
| R8.2 | `maybeKnowledgeCouncil()`, `maybeDream()`, `maybeEvolveSkills()` MUST be re-enabled | Proactive knowledge generation is restored with token-efficient implementation |
| R8.3 | Pellet store MUST NOT be empty after normal operation | Generation triggers fire sufficiently to populate the store |
| R8.4 | Pellets MUST be generated from desire executor research | `evolution/desire-executor.ts:63` pellet generation is active |
| R8.5 | Pellet retrieval results MUST be actively used, not just searched | Retrieved pellets inform responses; empty results don't mean "no pellets needed" |

---

## Part IV: Open Questions

These require user input before finalizing the spec:

1. **Token budget for proactive behavior**: Should proactive learning burn tokens during idle time? Should there be a daily/weekly token budget for proactive outreach?

2. **Channel priorities**: If multiple proactive items fire simultaneously, what is the priority order? (e.g., a file change alert + a scheduled check-in + a goal reminder)

3. **User control over proactivity**: Should users be able to configure "do not contact me proactively" or set quiet hours that suppress all proactive outreach?

4. **Evolution pace**: How many conversations should trigger evolution? Currently 5. Is this too aggressive (causes oscillation) or too passive (slow learning)?

5. **Tool mastery display**: Should the model self-report "I'm not very good at X yet" or is that undermines confidence? Should mastery be implicit (tool is used more) rather than explicit (tool has a label)?

6. **Clarification threshold**: How ambiguous must a request be before the system asks? Is there a confidence threshold below which clarification is triggered?

7. **Outcome verification scope**: For which task types is outcome verification feasible? (Code execution can verify; creative writing cannot.) Should verification be task-type-dependent?

---

## Appendix: Key File References

| Finding | File | Lines |
|---------|------|-------|
| Gateway is pure request-response | `src/gateway/core.ts` | 431-457 |
| Heartbeat drops pings silently | `src/heartbeat/proactive.ts` | 813-825 |
| Disabled proactive learning | `src/heartbeat/proactive.ts` | 696-726 |
| PerchManager no broadcast in CLI | `src/perch/manager.ts` | 101-106 |
| Immutable DNA traits | `src/owls/persona.ts` | 35-48 |
| domainConfidence dead code | `src/owls/persona.ts` | 51 |
| TrendAnalyzer guard not fed to LLM | `src/evolution/trend-analyzer.ts` | (toGuardPrompt never called) |
| TOOL_FALLBACKS hardcoded | `src/engine/runtime.ts` | 1100-1112 |
| isTaskComplete treats any text as complete | `src/orchestrator/orchestrator.ts` | 53-65 |
| [DONE] signal not verified | `src/engine/runtime.ts` | 175-189, 1138-1154 |
| Gap detector ignores question-asking | `src/evolution/detector.ts` | 118-129 |
| TriageClassifier never emits NEED_CLARIFICATION | `src/triage/index.ts` | 163-209 |
| SubOwlRunner no tool execution | `src/delegation/sub-owl-runner.ts` | 185-190 |
| Assumption Over Interruption | `src/engine/runtime.ts` | 2340 |
| Risk Gate only for destructive tools | `src/engine/runtime.ts` | 1420-1443 |
| IdleActivityEngine cancels on user activity | `src/heartbeat/idle-engine.ts` | 133 |
| LearningOrchestrator.runProactiveSession disabled | `src/learning/orchestrator.ts` | 257-270 |
| TriageClassifier never instantiated | `src/gateway/core.ts` | (zero matches for `new TriageClassifier`) |
| Parliament confidence downgrades | `src/gateway/core.ts` | 1193-1218 |
| Classifier restricts PARLIAMENT to dilemmas | `src/orchestrator/classifier.ts` | 239 |
| DELEGATE triage decision never consumed | `src/triage/index.ts` | 144, no downstream handler |
| SubOwlRunner/TaskDecomposer never instantiated | `src/index.ts` | (zero matches for `new SubOwlRunner`) |
| delegationPreference DNA trait unused | `src/owls/decision-layer.ts` | (trait never read) |
| Goal loop uses same engine, not true delegation | `src/orchestrator/orchestrator.ts` | 326-330 |
| AgentRegistry instantiated but empty | `src/agents/registry.ts` | (no `register` calls) |
| SwarmCoordinator never instantiated | `src/swarm/coordinator.ts` | (not in index.ts bootstrap) |
| Pellet retrieval works, generation disabled | `src/heartbeat/proactive.ts` | 696-726 |
| summon_parliament framed as expensive | `src/tools/parliament.ts` | 13 |
| shouldConveneParliament dead code | `src/parliament/detector.ts` | 22, never called |
| ParallelRunner.shouldTrigger has no callers | `src/parliament/parallel-runner.ts` | 182-202 |

---

*This specification is a living document. It captures the current state of the codebase and the behavioral problems identified. All "MUST" requirements are intended for future implementation — none are currently implemented unless otherwise noted in the findings.*