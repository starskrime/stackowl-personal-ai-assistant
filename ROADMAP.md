# StackOwl — Product Transformation Roadmap

Four products extracted from the existing codebase. Each is a standalone module
under `products/` with its own entry point, web API, and README.
StackOwl (`src/`) continues to work and uses all four as libraries.

---

## Product 1 — Deliberation Engine
**Tag:** `deliberation`
**What:** Structured adversarial AI debate for high-stakes decisions.
**Core idea:** Input a decision (contract, product bet, policy) → 5 specialized
voices debate it for 3 rounds → synthesis with dissenting views documented.
**Uses existing:** `src/parliament/` (orchestrator, perspectives, protocol)
**New work:** REST API, voice presets (devil's advocate, legal risk, etc.),
web UI (single HTML file), streaming SSE output, session persistence.

### Phases
- [x] P0: Parliament engine exists (`src/parliament/orchestrator.ts`)
- [x] P1: Extract `DeliberationEngine` wrapper (clean API, no OwlEngine dependency)
- [x] P2: Voice preset library (8 built-in roles + custom)
- [x] P3: REST API server (`products/deliberation/server.ts`)
- [x] P4: Streaming SSE output
- [x] P5: Web UI (`products/deliberation/public/index.html`)
- [ ] P6: Session persistence (save/load debate history)
- [ ] P7: Export (PDF report, markdown, JSON)

---

## Product 2 — Memory SDK
**Tag:** `memory-sdk`
**What:** Drop-in memory middleware for any AI app.
**Core idea:** Three calls — `store()`, `recall()`, `context()` — give any LLM
app episodic memory, temporal awareness, narrative threads, and ground state.
**Uses existing:** `src/memory/` (episodic, fact-store, working-context, store),
`src/intent/state-machine.ts`, `src/cognition/` (temporal-context, user-mental-model)
**New work:** Clean unified API surface, npm package structure, provider-agnostic
adapter, hosted REST API for non-Node apps.

### Phases
- [x] P0: Core memory systems exist (episodic, fact-store, working-context)
- [x] P1: `MemorySDK` class — single entry point wrapping all memory systems
- [x] P2: `store(userId, message, response)` — extracts and persists facts/episodes
- [x] P3: `recall(userId, query)` — Park et al. retrieval scoring
- [x] P4: `context(userId)` — returns enriched context string for system prompt
- [x] P5: Provider-agnostic (works with any LLM, not just StackOwl providers)
- [x] P6: REST API server (`products/memory-sdk/server.ts`)
- [x] P7: TypeScript types + JSDoc for npm publish
- [x] P8: Example integrations (Express middleware, raw fetch)

---

## Product 3 — Longitudinal AI
**Tag:** `longitudinal`
**What:** The AI that tracks how you think and evolve over time.
**Core idea:** Not a task executor — a mirror. "Here's who you were 3 months ago
vs now." Decision archaeology, commitment tracking, priority drift detection.
**Uses existing:** `src/memory/episodic.ts`, `src/intent/commitment-tracker.ts`,
`src/owls/evolution.ts`, `src/cognition/user-mental-model.ts`,
`src/intent/state-machine.ts` (narrative threads)
**New work:** Timeline view, drift detector, decision archaeology query,
commitment follow-through rate, personality change report generator.
**Depends on:** Memory SDK (Product 2)

### Phases
- [x] P0: Building blocks exist (episodic, commitment-tracker, DNA evolution)
- [x] P1: `TimelineEngine` — ordered view of episodic memory with drift scoring
- [x] P2: `DriftDetector` — detects topic/priority/style shifts across sessions
- [ ] P3: `CommitmentArchive` — tracks promises made vs followed through
- [x] P4: `PersonalityReport` — generates "who you were then vs now" narrative
- [x] P5: REST API + web dashboard
- [ ] P6: Weekly digest generation (email/Telegram)

---

## Product 4 — Persona Engine
**Tag:** `persona`
**What:** DNA-based AI persona system for products and NPCs.
**Core idea:** Define a persona's starting traits. Engine evolves it per-user
based on interactions. Same brand voice, calibrated individually.
**Uses existing:** `src/owls/evolution.ts`, `src/owls/persona.ts`,
`src/owls/decision-layer.ts`, `src/owls/mutation-tracker.ts`
**New work:** Multi-tenant persona isolation (persona A evolves differently
for User 1 vs User 2), persona snapshot/rollback, trait bounds (prevent
drift outside acceptable range), SDK + REST API.

### Phases
- [x] P0: DNA evolution engine exists (`src/owls/evolution.ts`)
- [x] P1: `PersonaEngine` — multi-tenant wrapper (userId × personaId matrix)
- [x] P2: Trait bounds system (min/max per trait, prevent runaway drift)
- [x] P3: Persona snapshot + rollback
- [x] P4: REST API (`products/persona/server.ts`)
- [x] P5: Persona analytics (how persona has evolved per user over time)
- [x] P6: Export/import persona definitions (JSON schema)

---

## Implementation Order

| # | Product | Why first |
|---|---------|-----------|
| 1 | Deliberation Engine | Most self-contained. Parliament already works end-to-end. |
| 2 | Memory SDK | Foundation for #3. Memory systems exist, need clean API. |
| 3 | Longitudinal AI | Builds on #2. Highest novelty. |
| 4 | Persona Engine | Most B2B-focused, clearest monetization. |

---

## Shared Infrastructure

All products share:
- `src/providers/` — AI backends (Anthropic, OpenAI, Ollama)
- `src/logger.ts` — logging
- `products/shared/` — common types, config loader, server utilities

## Directory Layout (target)

```
stackowl-personal-ai-assistants/
  src/                          # Existing StackOwl (personal assistant)
  products/
    deliberation/
      engine.ts                 # DeliberationEngine class
      server.ts                 # Express REST API
      public/index.html         # Web UI
      voices.ts                 # Built-in voice presets
      README.md
    memory-sdk/
      index.ts                  # MemorySDK class (main export)
      server.ts                 # REST API
      adapters/                 # OpenAI / Anthropic / generic adapters
      README.md
    longitudinal/
      timeline.ts               # TimelineEngine
      drift.ts                  # DriftDetector
      report.ts                 # PersonalityReport
      server.ts
      README.md
    persona/
      engine.ts                 # PersonaEngine
      bounds.ts                 # Trait bounds
      server.ts
      README.md
    shared/
      types.ts
      server-utils.ts
```
