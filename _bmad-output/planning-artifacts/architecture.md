---
stepsCompleted: [step-01-init, step-02-context, step-03-starter, step-04-decisions, step-05-patterns, step-06-structure, step-07-validation]
inputDocuments:
  - path: _bmad-output/planning-artifacts/prd.md
    loaded: true
    type: prd
  - path: docs/03-architecture-overview.md
    loaded: true
    type: architecture-reference
  - path: docs/02-memory-architecture.md
    loaded: true
    type: architecture-reference
workflowType: 'architecture'
project_name: 'stackowl-personal-ai-assistants'
user_name: 'Boss'
date: '2026-04-26'
---

# Architecture Decision Document

_stackowl-personal-ai-assistants_

*This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together.*

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**
44 FRs across 8 capability areas: Learning & Evolution, Outcome Verification, Curiosity & Clarification, Tool Mastery, Delegation & Subagents, Context & Memory, Multi-Owl Collaboration, Knowledge Management. Each area requires distinct architectural support — evolution needs DNA mutation logic, outcome verification needs result checking, curiosity needs gap detection routing, etc.

**Non-Functional Requirements:**
17 NFRs driving key decisions: 3-second CLI response, streaming TUI, 200-message context windows, 3-layer provider resilience (Ollama/OpenAI/Anthropic), credential protection, decision traceability.

**Scale & Complexity:**
- Primary domain: AI Agent Framework / CLI Tool
- Complexity level: High
- Estimated architectural components: 15-20 major components (Gateway, Engine, Memory, Tools, Parliament, Evolution, etc.)

### Technical Constraints & Dependencies

- Brownfield codebase — must integrate with existing infrastructure
- Single developer — architecture must be maintainable alone
- LLM provider dependency — must handle Ollama (local), OpenAI, Anthropic with fallback
- No proactive requirements — removed from scope

### Cross-Cutting Concerns Identified

- Tool execution safety (sandbox limits, destructive operation prevention)
- Session persistence across CLI restarts
- Multi-channel message routing (CLI/Telegram/Slack/WebSocket)
- Context window management for long conversations
- Evolution triggering and DNA mutation execution

## Starter Template Evaluation

### Technology Domain

**CLI Tool / AI Agent Framework** — based on project requirements.

### Existing Technology Stack (Established)

The existing codebase uses:
- **Language:** TypeScript (ES2023, NodeNext modules, strict)
- **Runtime:** Node.js ≥22
- **CLI Framework:** commander + chalk
- **Telegram:** grammY
- **Web:** Express.js
- **Tests:** Vitest
- **File Watching:** chokidar
- **Vector Search:** cosine-similarity (LanceDB, Kuzu for pellets)

### Brownfield Context

This is a **brownfield project** — existing codebase with established technology stack. No starter template needed. The architecture work is about enhancing and wiring existing systems:
- 8 behavioral deficiencies to fix (frozen evolution, shallow tool mastery, etc.)
- Existing infrastructure needs enhancement, not replacement
- Systems exist but are unwired or disabled (Parliament, delegation, pellets)

### Applicable Standards

- TypeScript strict mode with ES2023 NodeNext modules
- commander.js for CLI argument parsing
- chalk for terminal output styling
- Vitest for testing
- Express.js for REST API

## Core Architectural Decisions

### 1. Evolution Triggering Mechanism

**Decision:** Event-driven with scheduled backup

- **Primary:** After each `evolutionBatchSize` conversations, trigger evolution
- **Secondary:** If error rate exceeds threshold (>20% failure rate in last 10 interactions), run evolution immediately
- **Rationale:** Pure event-driven misses critical learning moments; pure scheduled may run unnecessarily

### 2. Tool Selection Strategy

**Decision:** Add learned effectiveness to existing BM25 + usage-weight

- Keep existing BM25 + usage-weight as base
- Add `toolEffectivenessScore` derived from `ApproachLibrary` outcomes
- Per-tool mastery levels affect model confidence but not selection probability directly
- Update `DOMAIN_TOOL_MAP` after each evolution run

### 3. Parliament Auto-Trigger Criteria

**Decision:** LLM-based detection as primary, confidence cascade as fallback

- **Primary:** `shouldConveneParliament()` LLM-based detection (already exists, needs wiring)
- **Fallback:** If strategy confidence < 0.65, check if topic matches `PARLIAMENT_PATTERNS` regex
- **Critical:** Never silently downgrade to STANDARD — if Parliament detected, Parliament fires

### 4. Delegation Decision Mechanism

**Decision:** LLM self-selection with complexity threshold

- Keep `orchestrate_tasks` tool for explicit delegation
- Add: complexity scoring based on subtask count and estimated tool calls
- If task estimated to need >5 tool calls AND has independent subtasks → model should consider delegation
- `delegationPreference` DNA trait influences threshold (autonomous owl delegates less readily)

### 5. Context Compaction Strategy

**Decision:** Tiered preservation with priority signals

- **Tier 1 (never compress):** User preferences, active commitments, recent decisions
- **Tier 2 (preserve longest):** Tool execution results for current task, conversation context
- **Tier 3 (compress first):** Casual conversation, filler, exploratory discussion
- Signal `[CONTEXT TRUNCATED]` to system prompt when compaction occurs

### 6. Pellet Generation Triggers

**Decision:** Event-based generation with deduplication

- **On task completion:** Generate pellet for significant multi-step tasks
- **On error pattern:** If same error occurs 3+ times, generate diagnostic pellet
- **On decision:** Major decisions (tool choice, approach selection) get captured
- **Deduplication:** Use semantic similarity > 0.85 threshold to avoid redundant pellets

## Implementation Patterns & Consistency Rules

### Naming Conventions

**Behavioral Event Naming:**
- Format: `behavioral.{system}.{action}` (e.g., `behavioral.evolution.triggered`, `behavioral.tool.fallback`)
- All lowercase, dot-separated

**Log Format:**
- `{timestamp} {level} [{component}] {event} {details}`
- Example: `2026-04-26T10:30:00Z INFO [EvolutionEngine] behavioral.evolution.batch_complete batchSize=5`

**Pellet Frontmatter:**
```yaml
type: behavioral-event | decision | error-pattern | knowledge
trigger: string
timestamp: ISO8601
importance: high | medium | low
tags: string[]
```

### Structure Patterns

**Evolution Tracking:**
- Evolution batch state stored in `owl.dna.evolutionBatchCounter`
- Batch completion triggers `behavioral.evolution.batch_complete` event
- DNA mutations logged with before/after values

**Tool Effectiveness:**
- `ApproachLibrary` keyed by `(owlId, toolName, taskType)`
- Update pattern: increment success/failure counters, recalculate effectiveness score
- Never overwrite — only increment and recalculate

**Context Compaction:**
- `[CONTEXT TRUNCATED]` signal inserted when messages are removed
- Tiered preservation enforced in `SessionManager.compact()`
- Compressed blocks tagged with `[MEMORY BLOCK]` marker

### Enforcement Guidelines

**All AI Agents Implementing Behavioral Systems MUST:**

1. Log all behavioral events in the established format
2. Update `ApproachLibrary` after every tool execution result
3. Check pellet semantic similarity before generating new pellet
4. Never silently downgrade PARLIAMENT — if detected, it fires
5. Always route ambiguity to clarification, never guess without flagging uncertainty

**Pattern Verification:**
- ESLint rules for log format (if feasible)
- Integration tests verify event format compliance
- Code review checklist for pattern adherence

## Project Structure & Boundaries

### Existing Project Structure

The codebase already has a well-organized structure:

```
src/
├── engine/           # ReAct runtime, router
├── gateway/          # Message routing, channel adapters
├── memory/           # Session, facts, episodes, digests
├── pellets/          # Knowledge generation and storage
├── owls/             # Persona, DNA, evolution
├── parliament/       # Multi-owl debate
├── tools/            # Tool registry and execution
├── channels/         # CLI, Telegram adapters
├── swarm/            # Multi-agent coordination
├── delegation/       # Sub-owl runner, task decomposer
├── triage/           # Message classification
├── heartbeat/       # Proactive engine
└── index.ts          # Entry point
```

### Enhancement Areas for 8 Behavioral Issues

| Enhancement | Location | Purpose |
|-------------|----------|---------|
| Evolution trigger wiring | `src/evolution/` (new) | Wire `EvolutionTrendAnalyzer.toGuardPrompt()` |
| Tool effectiveness tracker | `src/tools/` (enhance) | Track per-tool success/failure |
| Delegation handler | `src/delegation/` (enhance) | Consume `DELEGATE` triage decision |
| Pellet generator | `src/pellets/` (enhance) | Event-based generation |
| Gap detection router | `src/intent/` (new) | Route communicative gaps to questions |
| Session persistence | `src/memory/` (enhance) | Survive CLI restarts |
| Parliament auto-trigger | `src/parliament/` (enhance) | Wire `shouldConveneParliament()` |

### Integration Boundaries

- **Gateway → Engine:** Messages flow through `OwlGateway.handle()` → `OwlEngine.run()`
- **Engine → Tools:** Tool selection via `ToolIntentRouter`, execution via `ToolExecutor`
- **Engine → Memory:** Context built via `MemoryFirstContextBuilder`
- **Engine → Evolution:** Triggered after batch completion, feeds DNA mutations back
- **Parliament ↔ Pellets:** Debate output → pellet generation

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:** All 6 behavioral decisions align — no contradictions found
**Pattern Consistency:** Behavioral event naming supports all decisions
**Structure Alignment:** Enhancement areas map to existing `src/` directories

### Requirements Coverage Validation ✅

**All 44 FRs have architectural support** across 8 capability areas
**All 17 NFRs are addressed** across 5 quality categories

### Gap Analysis

**No Critical Gaps** — Architecture supports all requirements

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High

---

*Architecture document complete. Next: Create epics and stories.*