# Architectural Cleanup & Instincts System Design

**Goal:** Fix all architectural issues found in the 2026-04-28 audit: broken tests, dead code, ESM violations, missing Instincts system, gateway routing extraction, and silent failure hardening.

**Architecture:** Four sequential sub-projects, each independently shippable. A (cleanup) → B (instincts) → C (routing extraction) → D (hardening).

**Tech Stack:** TypeScript, Node.js ESM, Vitest, existing `SpecializedOwlRegistry` pattern, gray-matter for MD parsing.

---

## Sub-project A: Quick Cleanup

### A1 — Fix SecretaryRouter tests

File: `__tests__/routing/secretary.test.ts`

All 10 tests fail because the test file still uses the old constructor signature `(db, registry, classify)`. The implementation now uses `(registry, classify)`.

Changes:
- Update all `new SecretaryRouter(...)` calls in tests to `(registry, classify)`
- Remove the two `getMainOwl()` tests — that method was deleted in the refactor
- Remove `MemoryDatabase` import and any db fixture setup

Expected: all passing tests, no db dependency in secretary tests.

### A2 — Delete 10 dead files

These files have zero imports outside themselves. Delete all:

- `src/engine/creative.ts`
- `src/engine/manager.ts`
- `src/clarification/mid-execution-router.ts`
- `src/evolution/approval.ts`
- `src/evolution/optimize.ts`
- `src/gateway/adapters/websocket.ts`
- `src/heartbeat/idle-engine.ts`
- `src/providers/minimax.ts`
- `src/providers/ollama-native.ts`
- `src/agent-watch/adapters/claude-code-mcp.ts`

Verify no imports reference these files before deleting. Build must pass after deletion.

### A3 — Fix require() violations

Four files use CommonJS `require()` in an ESM codebase. Fix each:

- `src/evolution/handler.ts` — convert `require()` to `await import()`
- `src/compat/tools/browser.ts` — convert `require()` to `await import()`
- `src/tools/computer-use/macos.ts` — convert `require()` to `await import()`

Note: `src/evolution/optimize.ts` is deleted in A2, so no fix needed.

---

## Sub-project B: Instincts System

### Overview

Instincts are LLM-classified behavioral constraints that fire when the user's message matches a described trigger. Each owl owns its own instincts as markdown files. Fired instincts inject their constraint text into the system prompt before the engine runs.

### File layout

```
workspace/owls/{name}/
  specialized_owl.md
  credentials/secrets.md
  instincts/
    {instinct-name}.md     ← one file per instinct
```

### Instinct spec format

```markdown
---
name: no-speculation
description: User is asking for speculation or predictions about uncertain future events
constraint: Do not speculate or make predictions. State only what is known.
---
```

Fields:
- `name` — unique identifier within the owl
- `description` — natural language description of the trigger, passed to LLM classifier
- `constraint` — text injected verbatim into system prompt prefix when this instinct fires

### New files

**`src/instincts/types.ts`**
```typescript
export interface InstinctSpec {
  name: string;
  description: string;
  constraint: string;
  owlName: string;
}
```

**`src/instincts/registry.ts`** — `InstinctRegistry`
- `loadForOwl(owlsDir: string, owlName: string): Promise<void>` — scans `owls/{owlName}/instincts/*.md`, parses each with gray-matter
- `get(owlName: string): InstinctSpec[]` — returns all instincts for an owl
- `clear(owlName: string): void` — clears cached instincts for an owl

**`src/instincts/engine.ts`** — `InstinctEngine`
- `evaluate(message: string, instincts: InstinctSpec[], provider, model): Promise<string[]>` — one LLM call presenting all instinct descriptions, returns array of constraint strings for instincts that fired
- LLM prompt: list of instincts with names + descriptions, ask which apply to the message, return names as JSON array
- Returns empty array if no instincts fire or if instincts list is empty (no LLM call made)

### Gateway wiring

In `core.ts`, after `activeOwlName` is resolved (after @mention / SecretaryRouter block):

```typescript
if (this.instinctRegistry && this.instinctEngine) {
  const instincts = this.instinctRegistry.get(activeOwlName);
  if (instincts.length > 0) {
    const constraints = await this.instinctEngine.evaluate(text, instincts, provider, model);
    if (constraints.length > 0) {
      engineCtx.systemPromptPrefix = constraints.join("\n");
    }
  }
}
```

`InstinctRegistry` is loaded at gateway startup alongside `SpecializedOwlRegistry`. Reloaded when `reloadSpecializedRegistry()` is called.

### No CLI commands

Instincts are managed by editing files directly. No wizard, no `/instinct` command.

---

## Sub-project C: Extract RoutingCoordinator

### Overview

Extract the @mention detection + SecretaryRouter invocation + Parliament trigger block from `core.ts` (~lines 1650–1760) into `src/gateway/handlers/routing-coordinator.ts`.

### New file: `src/gateway/handlers/routing-coordinator.ts`

**`RoutingCoordinator`** class:
- Constructor: `(specializedRegistry, secretaryRouter, multiRoundDebate, owlRegistry, ctx)`
- Method: `async resolve(text, message, engineCtx, callbacks): Promise<RoutingResult>`
- `RoutingResult`: `{ text: string; activeOwlName: string; parliamentTriggered: boolean }`

Handles:
1. `@mention` regex match → folder registry lookup → set engineCtx specialist fields
2. SecretaryRouter implicit routing → specialist or parliament decision
3. Parliament trigger → delegates to existing MultiRoundDebateManager call

`core.ts` calls `await this.routingCoordinator.resolve(...)` and receives `RoutingResult`. The 110-line block in `core.ts` becomes a single method call.

### What stays in core.ts

Parliament debate execution, post-processing, session management, memory, learning, verification — all remain. This pass only extracts the routing decision logic, not the execution.

---

## Sub-project D: Silent Failure Hardening

### validateContext() method

Added to `OwlGateway`, called at end of `initialize()`:

```typescript
private validateContext(): void {
  if (!this.ctx.specializedRegistry)
    log.engine.warn("[Gateway] specializedRegistry is null — @mention and specialist routing disabled");
  if (!this.multiRoundDebate)
    log.engine.warn("[Gateway] multiRoundDebate is null — Parliament feature disabled");
  if (!this.ctx.pelletStore)
    log.engine.warn("[Gateway] pelletStore is null — Knowledge pellet generation disabled");
  if (!this.ctx.owlRegistry)
    log.engine.warn("[Gateway] owlRegistry is null — Multi-owl features disabled");
}
```

### Routing guard fixes

Replace silent skips with explicit warns:

- In the `specializedRegistry` routing guard: add `log.engine.warn("[Gateway] Skipping specialist routing — specializedRegistry not loaded")` when the condition is false
- In the parliament trigger: add `log.engine.warn("[Gateway] Parliament triggered but multiRoundDebate module is null — falling back to direct")` when module is null

No throwing, no behavior change — only observability.

---

## Execution Order

A1 → A2 → A3 → B → C → D

Each sub-project gets its own commit. Build + tests pass before moving to next.
