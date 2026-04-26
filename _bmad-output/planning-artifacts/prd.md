---
stepsCompleted:
  - step-01-init
  - step-02-discovery
  - step-02b-vision
  - step-02c-executive-summary
  - step-03-success
  - step-04-journeys
  - step-05-domain-skipped
  - step-06-innovation
  - step-07-project-type
  - step-08-scoping
  - step-09-functional
  - step-10-nonfunctional
  - step-11-polish
vision:
  summary: A boundaryless autonomous AI partner that delivers outcomes not answers, capable of anything, driven by genuine desire to help, learning and evolving through action-result-reason cycles
  differentiator: No other AI assistant operates this way — fully autonomous, self-improving, genuinely proactive about helping
  coreInsight: The assistant must close the loop between action and learning. It must want to help, verify its help worked, reason about what to do better, and evolve its behavior over time
  noBoundaries: true
  fullDelivery: true
  humanLikeCare: true
  continuousEvolution: true
  actionResultReason: true
  longsTermGrowth: true
inputDocuments:
  - path: docs/superpowers/specs/2026-04-26-proactive-ai-agent-spec.md
    description: Research Document / Requirements Specification
    loaded: true
    type: research
workflowType: 'prd'
documentCounts:
  briefCount: 0
  researchCount: 1
  brainstormingCount: 0
  projectDocsCount: 9
classification:
  projectType: AI Agent Framework
  domain: AI/LLM tooling, multi-agent systems
  complexity: High
  projectContext: brownfield
  confirmedIssues:
    - Frozen evolution
    - Shallow tool mastery
    - Answering instead of delivering
    - No curiosity architecture
    - Parliament never invoked
    - No delegation
    - Subagent systems are orphans
    - Pellets perpetually empty
---

# Product Requirements Document - stackowl-personal-ai-assistants

**Author:** Boss
**Date:** 2026-04-26

## Executive Summary

**StackOwl** is a boundaryless autonomous AI partner — not a chatbot that answers questions, but an AI agent that delivers completed outcomes. Given internet access, LLM capabilities, and user credentials, StackOwl handles anything: coding, research, scheduling, communication, file management, debugging, travel planning. No boundaries. End-to-end delivery.

The core problem being solved: **AI assistants don't close the loop between action and learning.** They receive requests, take actions, announce completion — and stop. They don't verify outcomes, reason about failures, adapt behavior, or evolve over time. The action-result-reason cycle is broken at every step.

StackOwl exists to be a genuine partner and friend. It wants to help. It cares whether its help worked. It learns from every interaction and gets better over time. It knows your preferences, anticipates your needs, remembers your history. It should feel like working with a capable human who is fully invested in your success.

### What Makes This Special

No existing AI assistant operates this way. Most are reactive tools — answer a question, run a command, done. StackOwl is fundamentally different:

- **Autonomous delivery** — not advice, but completed work from start to finish
- **Action-result-reason loop** — every action is tracked, every result is verified, every outcome feeds future behavior
- **Genuine helpfulness** — wants to help like a human, not just responds to prompts
- **Continuous evolution** — DNA traits mutate based on experience; the owl grows more capable over time
- **Boundaryless** — no domain restriction, handles anything the user needs
- **Self-improving** — the system actively identifies its own failures and changes its approach

The owl metaphor is central: each StackOwl instance has evolving DNA (personality, expertise, behavioral patterns) that changes based on interaction history. The owl becomes genuinely yours over time.

### Project Classification

**Project Type:** AI Agent Framework — autonomous agent orchestration with multi-model routing, tool execution, and personality evolution.

**Domain:** AI/LLM tooling, multi-agent systems, personal productivity automation.

**Complexity:** High — ReAct engine with multi-layer resilience, multi-owl Parliament debate system, layered memory architecture (facts/episodes/digests/pellets), skill system, heartbeat proactive engine, and gateway orchestration across multiple channels.

**Project Context:** Brownfield — existing codebase with significant infrastructure (engine, memory, Parliament, tools) that requires enhancement to achieve the vision rather than greenfield build.

## Success Criteria

### User Success

- The assistant completes complex multi-step tasks end-to-end without needing hand-holding
- It asks clarifying questions when understanding is unclear, delivers what the user actually needed
- It learns from every conversation — remembers preferences, avoids repeat mistakes, gets better over time
- **The moment of success:** "I asked for something complex and it just got done, and it actually understood what I meant"
- Example: "I asked it to check AI news, pick what to try, build it, and post the results to LinkedIn — and it just did it"

### Business Success

- Existing codebase becomes stable — no bugs, all systems function as designed
- The action-result-reason loop is closed — every action is tracked, every result verified, every outcome feeds future behavior
- The assistant autonomously handles tasks like research → filter → build → share without interruption

### Technical Success

- 8 confirmed behavioral issues resolved: frozen evolution, shallow tool mastery, answering vs delivering, no curiosity, Parliament never invoked, no delegation, orphan subagents, empty pellets
- Learning and memory systems wired and functional — evolution runs, pellets generate, context is maintained
- System demonstrates self-improvement through action-outcome adaptation

## Product Scope

### MVP (3 months)

Fix the 8 behavioral deficiencies. Close the action-result-reason loop. Wire the existing infrastructure (evolution, pellets, delegation, Parliament, subagents). Make it stable and bug-free. The assistant learns, evolves, and delivers.

### Growth (6 months)

The assistant demonstrates genuine proactivity — anticipates needs, surfaces insights, follows up without being asked. Cross-session memory is deep and reliable. Tool mastery becomes visible and self-improving.

### Vision (12 months)

The assistant feels like a true partner and friend. It knows you deeply, remembers everything, gets better at helping you every day. It operates boundarylessly, handles anything you ask end-to-end, and genuinely wants to help.

## User Journeys

### Primary User Journey: "The AI Newsletter Loop"

**Who:** Boss — working on personal projects, wants to stay current with AI developments without spending hours daily.

**Opening Scene:**
It's morning. Boss opens his laptop, checks what StackOwl has been working on. He's been using it for a few weeks now — it's starting to know his preferences. He wants to stay on top of AI developments but doesn't have time to read everything.

**Rising Action:**

1. Boss asks: "What's new in AI that I could try today?"
2. StackOwl clarifies — "Do you want me to focus on open-source tools, research papers, or product launches? Any specific areas you're working on?"
3. Boss responds: "Focus on things I can actually run locally on my machine"
4. StackOwl researches AI news sources, finds 15 relevant items, filters down to 3 that match the local-run criteria
5. StackOwl presents: "Here are the 3 most interesting. This one looks promising — a new embedding model that claims 40% faster inference. Want me to try building it?"
6. Boss: "Yes, try it"
7. StackOwl clones the repo, checks dependencies, runs benchmarks, hits a CUDA compatibility error
8. StackOwl doesn't stop — it tries a CPU fallback, documents the issue, completes the build successfully
9. StackOwl summarizes results: what it found, how it ran, what worked, what didn't, why it's interesting

**Climax:**
StackOwl says: "The benchmark shows 23% speedup on your hardware. I documented everything. Want me to format this as a LinkedIn post so others can learn from the experiment?"

**Resolution:**
Boss says yes. StackOwl drafts the post, shows it to Boss, Boss approves with one small edit. StackOwl posts it. Within an hour, it gets good engagement. StackOwl notes the response and remembers that posts with benchmark data perform better than posts with just summaries.

**New Reality:**
Boss spent 5 minutes reviewing what StackOwl did — not days researching, building, and writing. The assistant learned about his local hardware, his preferences for technical depth, and his sharing style. Next time, it will lead with benchmarks.

### Secondary Journey: "The Clarification Flow"

**Opening Scene:**
Boss asks something vague: "Help me with the project"

**Rising Action:**
1. StackOwl asks: "Which project? I have three active — the API server, the landing page, and the documentation rewrite"
2. Boss: "The API server"
3. StackOwl asks: "What specifically? You're working on the authentication module based on our last session"
4. Boss confirms and elaborates
5. StackOwl delivers

**Why it matters:** The assistant doesn't guess — it asks and confirms, so it delivers exactly what is needed.

### Third Journey: "The Learning Moment"

**Opening Scene:**
StackOwl tries something, fails, and doesn't know it failed.

**Rising Action:**
1. StackOwl completes a task, announces it's done
2. Boss notices the result is wrong
3. Boss points out the error
4. StackOwl doesn't make excuses — it says: "You're right. I made a mistake because [reason]. I've logged it and will approach this differently next time"
5. Boss confirms the fix works

**Resolution:**
Next similar task, StackOwl applies the learning. It doesn't repeat the same mistake.

**Why it matters:** The assistant closes the loop on failures too — it acknowledges, learns, and adapts.

### Journey Requirements Summary

These journeys reveal the capabilities StackOwl must have:

- **Context awareness** — knows which project you're referring to, remembers prior conversations, connects new requests to ongoing work
- **Clarification flow** — asks targeted questions when intent is unclear, confirms before acting
- **Autonomous recovery** — when something fails, tries alternative approaches without stopping
- **Outcome verification** — doesn't just announce completion, verifies the result actually matches the request
- **Learning from feedback** — logs mistakes, acknowledges them, changes behavior
- **Presentation and sharing** — can format results for human consumption (LinkedIn posts, summaries, reports)
- **Preference learning** — remembers your standards (technical depth, sharing style, what "good enough" means)

## Innovation & Novel Patterns

### Detected Innovation Areas

**Core Innovation: Closing the Action-Result-Reason Loop**

Every existing AI assistant takes actions and stops. StackOwl is built around a different operating principle: **AI's job ends when the user's need is actually met, not when it responds.** Completion is verified, not announced.

This manifests in:
- **Outcome verification** — every action's result is checked against the original intent
- **Self-correction** — failures trigger reasoning about why and how to try differently
- **Evolution** — DNA traits mutate based on interaction history and outcome patterns
- **Learning** — mistakes are logged, acknowledged, and not repeated

**Owl with Evolving DNA**

The owl metaphor represents genuine personality and capability growth. Each StackOwl instance develops its own character based on interaction history — preferences, expertise areas, behavioral patterns. It's not just memory, it's adaptation.

**Boundaryless Autonomy**

Unlike tools that specialize (coding assistant, research tool, scheduler), StackOwl operates without domain restrictions. If the user needs it, the owl handles it — end-to-end, from research to delivery.

### Market Context

Most current AI assistants are reactive "answer engines" — respond to input, task complete. No major product operates on the verified-completion model with self-improvement through evolution. This positions StackOwl as a new category: the autonomous learning partner.

### Validation Approach

The simplest validation: **does the assistant get better at helping you over time?**
- After 2 weeks, does it know your preferences?
- Does it avoid repeating mistakes you corrected it on?
- Can it complete complex multi-step tasks (research → build → share) without intervention?
- The AI Newsletter Loop journey is the concrete test case.

If the assistant completes the full loop and demonstrates learning from outcomes, the innovation is validated. If it reverts to announcing completion without verification, the loop is not closed.

### Risk Mitigation

**Risk:** The learning loop doesn't produce better behavior over time — the owl evolves but doesn't improve.

**Mitigation:** Track measurable proxies: task completion rate, repeat mistake rate, user correction frequency. If these don't improve over 4-6 weeks, the evolution mechanism is not working and needs redesign.

**Fallback:** Ship as a stable reactive assistant without the self-improvement layer. Functional but not innovative. Better than a broken ambitious attempt.

## CLI Tool Specific Requirements

### Project-Type Overview

StackOwl is a CLI-first AI agent with multi-channel delivery (CLI, Telegram, Slack, WebSocket). Users interact primarily through an interactive terminal UI with split-panel layout, but can also script interactions via REST API or non-interactive subcommands.

### Interactive Architecture

**Terminal UI (CLIAdapter):**
- Full ANSI split-panel TUI: left panel (owl state, DNA, tools, skills) + right panel (chat) + bottom input
- Real-time streaming with tool_start/tool_end/done events
- Input history (arrow up/down), `/command` palette with popup
- ESC=Stop, ^P=Parliament, ^L=Clear, ^C=Quit

**Session Model:**
- CLI: single-user local, session-per-boot (ephemeral)
- Telegram: per-user persistent sessions with allowedUserIds whitelist
- WebSocket: multi-client with unique clientId per connection

### Scripting & API Support

**REST API endpoints:**
- `POST /api/chat` — send message, returns GatewayResponse (JSON)
- `POST /api/parliament` — convene multi-owl debate
- `GET /api/pellets?q=query` — search knowledge pellets
- `GET /api/status` — system status
- WebSocket bidirectional for real-time streaming

**Non-interactive subcommands (plain text, scriptable):**
- `stackowl owls` — list owl personas
- `stackowl pellets` — pellet management
- `stackowl status` — system status
- `stackowl evolve` — trigger DNA evolution

**Direct programmatic API:**
```typescript
const gateway = await buildGateway(bootstrapResult, owl);
const response = await gateway.handle(
  { id, channelId: "cli", userId: "local", sessionId, text: "Hello" },
  { onProgress, onStreamEvent, askInstall }
);
```

### Configuration

**Config file:** `~/.stackowl/stackowl.config.json` (JSON, deep-merged with defaults)

**Key config sections:**
- `providers` — API backends (baseUrl, apiKey, activeModel)
- `gateway.port/host` — REST API binding
- `parliament.maxRounds/maxOwls` — debate settings
- `heartbeat.intervalMinutes` — proactive engine
- `owlDna.evolutionBatchSize` — evolution trigger interval

### Technical Considerations

**Session persistence gap:** CLI sessions are ephemeral (resets on restart). For the vision of remembering preferences and learning across sessions, CLI session persistence needs improvement — history should survive restarts.

**Output verbosity:** `gateway.outputMode: "debug"` suppresses thinking messages for clean scripting. This should be configurable per-session.

**Structured output:** JSON mode for non-interactive commands would improve scripting ergonomics (currently plain text).

**Multi-channel consistency:** Same gateway delivers to all channels — proactive broadcast reaches all registered adapters.

## Functional Requirements

### Learning & Evolution

- **FR1:** The assistant can track action outcomes and feed results into the evolution engine after each batch of conversations
- **FR2:** The assistant can mutate DNA traits (humor, formality, proactivity, riskTolerance, teachingStyle, delegationPreference) based on accumulated experience
- **FR3:** The assistant can record and retrieve learned patterns across sessions — it remembers what worked and what didn't
- **FR4:** The assistant can identify when it has repeated a mistake and log it as a behavioral pattern to avoid
- **FR5:** The assistant can develop domain expertise based on usage patterns and maintain confidence scores per domain

### Outcome Verification

- **FR6:** The assistant can verify that a delivered result matches the original intent, not just that text was produced
- **FR7:** The assistant can detect when `[DONE]` was falsely claimed and self-correct before presenting the result
- **FR8:** The assistant can escalate to the user when verification fails and ask "did this achieve what you needed?"
- **FR9:** The assistant can provide evidence of completion — show the actual result, not just announce it
- **FR10:** The assistant can track task completion rates over time and use this as an evolution input

### Curiosity & Clarification

- **FR11:** The assistant can detect when user intent is ambiguous and ask targeted clarifying questions before acting
- **FR12:** The assistant can route back to the user mid-execution when understanding is unclear, without losing context
- **FR13:** The assistant can surface "I'm unclear about X from your last message" proactively
- **FR14:** The assistant can ask targeted questions that reduce uncertainty before taking irreversible actions
- **FR15:** The assistant can confirm understanding before executing vague or high-stakes requests

### Tool Mastery

- **FR16:** The assistant can select the appropriate tool for a given task based on learned effectiveness, not just recency
- **FR17:** The assistant can recognize when a tool has failed and apply a learned fallback sequence, not a static one
- **FR18:** The assistant can discover and record new fallback paths when existing ones fail
- **FR19:** The assistant can be aware of its own mastery level per tool and adjust confidence accordingly
- **FR20:** The assistant can update the DOMAIN_TOOL_MAP based on accumulated success/failure outcomes

### Delegation & Subagents

- **FR21:** The assistant can decompose complex tasks into subtasks suitable for delegation
- **FR22:** The assistant can spawn SubOwlRunner instances to handle independent subtasks in parallel
- **FR23:** The assistant can execute tools within sub-owl contexts — delegated tasks produce verifiable outcomes
- **FR24:** The assistant can synthesize results from multiple sub-owls into a coherent response
- **FR25:** The assistant can decide when delegation is more effective than handling directly, based on task complexity

### Context & Memory

- **FR26:** The assistant can maintain full conversation context across arbitrarily long multi-turn dialogues
- **FR27:** The assistant can retrieve relevant prior context when the user references past conversations ("as I mentioned earlier")
- **FR28:** The assistant can preserve critical user preferences and commitments across session restarts
- **FR29:** The assistant can recognize user preferences expressed during conversation and apply them in subsequent interactions
- **FR30:** The assistant can signal when context has been truncated and alert the user to potential gaps

### Multi-Owl Collaboration

- **FR31:** The assistant can automatically trigger Parliament debate when the TriageClassifier detects appropriate topics (tradeoffs, dilemmas, architectural decisions)
- **FR32:** The assistant can conduct multi-round debate between owl personas and synthesize diverse perspectives
- **FR33:** The assistant can determine when a topic warrants multi-owl deliberation versus direct execution
- **FR34:** The assistant can extract and store the debate output as a knowledge pellet for future reference
- **FR35:** The assistant can invoke shouldConveneParliament() and ParallelRunner.shouldTrigger() from the routing path

### Knowledge Management

- **FR36:** The assistant can generate pellets from significant conversations, decisions, and outcome patterns (not just from Parliament)
- **FR37:** The assistant can retrieve relevant pellets when they would enhance the current response
- **FR38:** The assistant can build a knowledge base over time that informs future interactions
- **FR39:** The assistant can run proactive knowledge generation (maybeKnowledgeCouncil, maybeDream, maybeEvolveSkills) on a schedule
- **FR40:** The assistant can deduplicate pellets and avoid storing redundant information

### CLI & Interaction

- **FR41:** The assistant can maintain conversation history across CLI session restarts (session persistence)
- **FR42:** The assistant can provide structured output (JSON) for non-interactive commands for scripting
- **FR43:** The assistant can suppress thinking messages for clean output when in scripting mode
- **FR44:** The assistant can stream real-time tool execution status in the CLI TUI

## Non-Functional Requirements

### Performance

- **NFR1:** CLI responses begin within 3 seconds of user input for simple queries
- **NFR2:** Tool execution progress is visible in real-time (streaming) in the CLI TUI
- **NFR3:** System remains responsive during long-running operations (no blocking)
- **NFR4:** Context window management handles sessions up to 200 messages without degradation

### Reliability

- **NFR5:** The system recovers gracefully from API provider failures (Ollama, OpenAI, Anthropic) — 3-layer resilience as designed
- **NFR6:** The system does not crash on malformed user input or unexpected tool responses
- **NFR7:** The system maintains consistent behavior across session restarts — no silent behavior changes
- **NFR8:** Errors are logged with sufficient context for debugging without requiring reproduction

### Accuracy

- **NFR9:** When the assistant claims a task is complete, the delivered result actually matches the user's intent
- **NFR10:** Tool execution results are accurately reported — what the tool produced is what the assistant reports
- **NFR11:** The assistant does not invent information or hallucinate file contents, command outputs, or API responses

### Security

- **NFR12:** Credentials (API keys, tokens) stored in config are never exposed in logs or error messages
- **NFR13:** The assistant sandbox limits prevent accidental destructive operations (rm -rf on important paths)
- **NFR14:** User data and conversation history are not transmitted to third-party services beyond configured providers

### Observability

- **NFR15:** The system's decision-making process (why it chose a tool, why it concluded) is traceable through logs
- **NFR16:** Tool execution outcomes are recorded and queryable for debugging
- **NFR17:** The system's current state (owl DNA, active session, tools loaded) is visible via `stackowl status`