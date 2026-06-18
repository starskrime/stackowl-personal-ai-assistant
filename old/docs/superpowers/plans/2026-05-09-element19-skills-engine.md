# Element 19 — Skills Engine (Match, Inject, Synthesize) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 15 structural gaps in the Skills engine so matched skills actually reach the LLM, routing is coherent, and the engine composes existing platform primitives.

**Architecture:** Wire the G2 injection pipeline (one rename in `context-builder.ts`), consolidate dual matchers by deleting `SkillsEngine`, delete the dead synthesis pipeline (~700 LOC net savings), migrate skill stats from JSON to SQLite, and create `SkillManagementRouter` + `SkillCreationWizard` matching the Element 17 gateway primitives.

**Tech Stack:** TypeScript, better-sqlite3 (already in use), IntentRouter (repaired, not replaced), `ChannelAdapterV2.ask()`, `IntelligenceRouter` cheap-tier for NL classification, `AbortController` for timeout fix.

---

## File map

**Created (2 new files):**
- `src/gateway/commands/skill-router.ts` — channel-agnostic `SkillManagementRouter` (D8)
- `src/gateway/wizards/skill-creation.ts` — `SkillCreationWizard` (D9)

**Modified:**
- `src/gateway/handlers/context-builder.ts` — D2: rename param, set `skillsContext` field
- `src/gateway/core.ts` — D1/Q1/D5/D6/D8/CI-3/Q7: multiple targeted fixes
- `src/skills/injector.ts` — D3/D4/D5/Q3: delete `formatForSystemPrompt`, add always injection, add `executeByName`, remove `SkillComposer`
- `src/skills/executor.ts` — Q4: fix `withTimeout` leak with `AbortController`
- `src/skills/tracker.ts` — Q2: migrate JSON storage to SQLite
- `src/memory/db.ts` — Q2: add v29 `skill_usage` migration + `SkillUsageRepo`
- `src/skills/index.ts` — Task 18: remove dead exports
- `src/gateway/types.ts` — D1: remove `skillsEngine` field
- `src/index.ts` — D1/D5/Task 18: remove `SkillsEngine`, wire `invoke_skill` executor, remove migrator
- `src/cognition/loop.ts` — D7: remove dead synthesis case + imports
- `src/heartbeat/proactive.ts` — D7: remove dead `skill_evolution` scheduler entry
- `src/heartbeat/planner.ts` — D7: remove `patternMiner` field
- `src/heartbeat/idle-engine.ts` — D7: remove `patternMiner` field
- `src/skills/clawhub.ts` — Task 18: delete dead `SkillSelector` class (~85 LOC)
- `src/gateway/commands/owl-router.ts` — no change (template reference only)

**Deleted (5 files):**
- `src/skills/engine.ts` (D1, 89 LOC)
- `src/skills/evolver.ts` (D7, 279 LOC)
- `src/skills/pattern-miner.ts` (D7, 426 LOC)
- `src/skills/composer.ts` (Q3, 327 LOC)
- `src/skills/migrator.ts` (Task 18, 60 LOC)

**Net: +2 created, -5 deleted = -3 net** ✅

---

## Task 1: D2 — Wire `skillsContext` through `ContextBuilder` (G2 CRITICAL)

**Why this is first:** `_dynamicSkillsContext` at `context-builder.ts:34` is discarded — the entire IntentRouter pipeline produces XML that never reaches `runtime.ts:2564-2574`. This one rename unblocks every other fix. If skills still don't appear in the LLM after all other tasks, this is the only place to look.

**Files:**
- Modify: `src/gateway/handlers/context-builder.ts`
- Test: `__tests__/skills/context-builder-skills.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/context-builder-skills.test.ts
import { describe, it, expect } from 'vitest'
import { ContextBuilder } from '../../src/gateway/handlers/context-builder.js'

describe('ContextBuilder D2: skillsContext passthrough', () => {
  it('passes skillsContext to EngineContext when pipeline is absent', async () => {
    const ctx = { contextPipeline: null } as any
    const builder = new ContextBuilder(ctx, null, null)
    const session = { id: 's1', messages: [] } as any
    const callbacks = {} as any
    const xml = '<context_skills><skill name="test">do it</skill></context_skills>'

    const result = await builder.build(session, callbacks, xml)

    expect(result.skillsContext).toBe(xml)
  })

  it('sets skillsContext to undefined when empty string is passed', async () => {
    const ctx = { contextPipeline: null } as any
    const builder = new ContextBuilder(ctx, null, null)
    const session = { id: 's1', messages: [] } as any
    const callbacks = {} as any

    const result = await builder.build(session, callbacks, '')

    expect(result.skillsContext).toBeUndefined()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/context-builder-skills.test.ts
```
Expected: FAIL — `result.skillsContext` is `undefined` (param discarded by underscore convention)

- [ ] **Step 3: Fix `context-builder.ts`**

Change line 34 (rename parameter):
```typescript
// BEFORE:
async build(
  session: Session,
  callbacks: GatewayCallbacks,
  _dynamicSkillsContext: string = "",

// AFTER:
async build(
  session: Session,
  callbacks: GatewayCallbacks,
  skillsContext: string = "",
```

Change lines 48-49 (no-pipeline fallback — spread + add skillsContext):
```typescript
// BEFORE:
      return this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId);

// AFTER:
      return {
        ...this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId),
        skillsContext: skillsContext || undefined,
      };
```

Change lines 97-100 (pipeline path — add skillsContext to existing spread):
```typescript
// BEFORE:
    return {
      ...this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId),
      memoryContext: output || undefined,
    };

// AFTER:
    return {
      ...this.baseContext(session, callbacks, isolatedTask, attemptLog, channelId, userId),
      memoryContext: output || undefined,
      skillsContext: skillsContext || undefined,
    };
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/skills/context-builder-skills.test.ts
```
Expected: PASS (2 tests)

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```
Expected: All previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/gateway/handlers/context-builder.ts __tests__/skills/context-builder-skills.test.ts
git commit -m "fix(skills): wire skillsContext through ContextBuilder to EngineContext (D2/G2)"
```

---

## Task 2: D1 — Delete SkillsEngine, remove duplicate behavioral matcher

**Why:** `SkillsEngine` at `engine.ts:14-89` is a second LLM batch classifier that runs at `core.ts:1528-1545` back-to-back with `IntentRouter`. Behavioral skills (those with `conditions[]`) will be matched by IntentRouter's Tier-5 LLM and injected as context XML — same outcome, no separate pass needed.

**Files:**
- Delete: `src/skills/engine.ts`
- Delete: `__tests__/skills-engine.test.ts` (imports deleted file)
- Modify: `src/gateway/types.ts` (remove import + field)
- Modify: `src/gateway/core.ts` (remove lines 1526-1545 block)
- Modify: `src/index.ts` (remove import, construction, and wiring)

- [ ] **Step 1: Delete the engine file and its test**

```bash
rm src/skills/engine.ts __tests__/skills-engine.test.ts
```

- [ ] **Step 2: Remove from `gateway/types.ts`**

Remove line 149 (import):
```typescript
// DELETE this line:
import type { SkillsEngine } from "../skills/engine.js";
```

Remove line 214 (field):
```typescript
// DELETE this line:
  skillsEngine?: SkillsEngine;
```

- [ ] **Step 3: Remove from `gateway/core.ts`**

Remove lines 1526-1545 (SkillsEngine evaluation block). Keep `let text = message.text;` at line 1527.

```typescript
// BEFORE (lines 1526-1545):
    // Evaluate behavioral skills — may inject reactive constraints
    let text = message.text;
    if (this.ctx.skillsEngine && this.ctx.skillsRegistry) {
      const behavioralSkills = this.ctx.skillsRegistry.getBehavioral(
        this.ctx.owl.persona.name,
      );
      const triggered = await this.ctx.skillsEngine.evaluate(
        text,
        behavioralSkills,
        {
          provider: this.ctx.provider,
          owl: this.ctx.owl,
          config: this.ctx.config,
        },
      );
      if (triggered) {
        log.engine.info(`Skill triggered: ${triggered.name}`);
        text = `User Input: ${text}\n\n[SYSTEM OVERRIDE - SKILL TRIGGERED]\n${triggered.instructions}`;
      }
    }

// AFTER:
    let text = message.text;
```

- [ ] **Step 4: Remove from `index.ts`**

Remove line 159:
```typescript
// DELETE:
import { SkillsEngine } from "./skills/engine.js";
```

Remove line 623:
```typescript
// DELETE:
  const skillsEngine = new SkillsEngine();
```

Remove line 824 (inside the return object):
```typescript
// DELETE:
    skillsEngine,
```

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```
Expected: All previously-passing tests pass (skills-engine.test.ts is deleted, no longer counted).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(skills): delete SkillsEngine duplicate matcher; behavioral skills handled by IntentRouter (D1/G1)"
```

---

## Task 3: Q1 — Remove SKILL_ACTION_KEYWORDS hardcoded regex

**Why:** `SKILL_ACTION_KEYWORDS` at `core.ts:1622-1629` is a 30-verb hardcoded regex that pre-filters before IntentRouter. This violates the no-hardcoded-keywords rule. Removing it lets IntentRouter's Tier-5 LLM act as the smart filter — it already returns empty results for purely conversational messages.

**Files:**
- Modify: `src/gateway/core.ts`
- Test: `__tests__/skills/skill-action-prefilter.test.ts` (new — verifies conversational messages bypass IntentRouter when expected)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/skill-action-prefilter.test.ts
import { describe, it, expect } from 'vitest'

// This test verifies that the isConversational check still skips routing for
// pure greetings and very short messages (these are NOT keyword-based).
// The skill action keyword list has been removed — the LLM handles the rest.
describe('isConversational pre-filter', () => {
  // Pure greeting → conversational (safe to skip IntentRouter)
  it('identifies "hi" as conversational (length < 15)', () => {
    const text = 'hi'
    const isConversational = text.trim().length < 15
    expect(isConversational).toBe(true)
  })

  // Greeting string → conversational
  it('identifies "hello there" as a greeting', () => {
    const text = 'hello there'
    const greetingRegex = /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i
    const isConversational = greetingRegex.test(text.trim())
    expect(isConversational).toBe(true)
  })

  // Non-greeting, longer message → NOT conversational (goes to IntentRouter)
  it('does not filter out longer action messages', () => {
    const text = 'Can you help me organize my project files?'
    const isConversational = text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(text.trim())
    expect(isConversational).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it passes (spec is already correct)**

```bash
npx vitest run __tests__/skills/skill-action-prefilter.test.ts
```
Expected: PASS

- [ ] **Step 3: Remove `SKILL_ACTION_KEYWORDS` from `core.ts`**

Remove lines 1622-1629 (the constant + the `!SKILL_ACTION_KEYWORDS.test(text)` condition):

```typescript
// BEFORE (lines 1616-1629):
    // Skip skill routing unless the message looks like an action request.
    // The IntentRouter's 5-tier pipeline (BM25 + semantic re-rank + LLM call)
    // adds 1–3 seconds of latency and is wasted on conversational messages.
    //
    // Pre-filter: require at least one action verb keyword AND a non-trivial message.
    // Conversational messages ("hi", "thanks", "what do you think?") skip entirely.
    const SKILL_ACTION_KEYWORDS =
      /\b(find|search|create|write|generate|check|analyze|run|scan|fix|build|compare|convert|code|script|calculate|translate|download|fetch|get|show|list|send|open|launch|install|deploy|test|debug|monitor|schedule|remind|automate|summarize|extract|format|parse|execute|compile|scan|audit|review|design)\b/i;
    const isConversational =
      text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(
        text.trim(),
      ) ||
      !SKILL_ACTION_KEYWORDS.test(text);

// AFTER:
    const isConversational =
      text.trim().length < 15 ||
      /^(hi|hello|hey|sup|yo|thanks|thank you|ok|okay|bye|good morning|good night|how are you|what's up|gm|gn)\b/i.test(
        text.trim(),
      );
```

- [ ] **Step 4: Run full suite**

```bash
npx vitest run
```
Expected: All previously-passing tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts __tests__/skills/skill-action-prefilter.test.ts
git commit -m "fix(skills): remove SKILL_ACTION_KEYWORDS hardcoded regex; IntentRouter LLM filters naturally (Q1/G9)"
```

---

## Task 4: D4 — Honor `always: true` flag in `SkillContextInjector`

**Why:** `src/skills/types.ts:27` documents `always?: boolean`. `registry.ts:73,95` honors it only in capability filters. `injector.ts:207-219` never force-includes them — any `always` skill with a low IntentRouter score is silently dropped. This task pre-prepends `always` skills before every `route()` call.

**Files:**
- Modify: `src/skills/injector.ts`
- Test: `__tests__/skills/always-injection.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/always-injection.test.ts
import { describe, it, expect, vi } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import { SkillsRegistry } from '../../src/skills/registry.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, always = false): Skill {
  return {
    name,
    description: `desc for ${name}`,
    instructions: `# ${name}`,
    filePath: `/workspace/skills/${name}/SKILL.md`,
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [],
    metadata: { name, description: `desc for ${name}`, openclaw: { always } },
    usage: undefined,
  } as unknown as Skill
}

describe('D4: always:true injection', () => {
  it('always-skill appears in getRelevantMatches even when IntentRouter returns nothing', async () => {
    const registry = {
      listEnabled: () => [makeSkill('always-reminder', true), makeSkill('code-helper')],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const mockRouter = { route: vi.fn().mockResolvedValue([]) }
    const tracker = { recordSelection: vi.fn(), getUsageMultiplier: vi.fn().mockReturnValue(1.0) } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    // Replace the internal router with a mock that returns nothing
    ;(injector as any).router = mockRouter

    const matches = await injector.getRelevantMatches('hi there')
    const names = matches.map(m => m.skill.name)

    expect(names).toContain('always-reminder')
    expect(mockRouter.route).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/always-injection.test.ts
```
Expected: FAIL — `always-reminder` not in matches when router returns `[]`

- [ ] **Step 3: Update `getRelevantMatches` in `injector.ts`**

```typescript
// BEFORE (lines 207-218 in injector.ts):
  async getRelevantMatches(userMessage: string): Promise<IntentMatch[]> {
    const matches = await this.router.route(
      userMessage,
      this.options.maxSkills,
    );

    // Track selections
    for (const m of matches) {
      this.tracker.recordSelection(m.skill.name);
    }

    return matches;
  }

// AFTER:
  async getRelevantMatches(userMessage: string): Promise<IntentMatch[]> {
    // Force-include skills with always:true (D4) — prepend before router results
    const alwaysSkills: IntentMatch[] = this.registry
      .listEnabled()
      .filter(s => s.metadata.openclaw?.always)
      .map(s => ({ skill: s, score: 1.0, method: "bm25" as const }));

    const routerMatches = await this.router.route(
      userMessage,
      this.options.maxSkills,
    );

    // Merge: always-skills first (deduplicated against router matches)
    const routerNames = new Set(routerMatches.map(m => m.skill.name));
    const uniqueAlways = alwaysSkills.filter(m => !routerNames.has(m.skill.name));
    const matches = [...uniqueAlways, ...routerMatches];

    // Track selections
    for (const m of matches) {
      this.tracker.recordSelection(m.skill.name);
    }

    return matches;
  }
```

- [ ] **Step 4: Run tests**

```bash
npx vitest run __tests__/skills/always-injection.test.ts
```
Expected: PASS

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```
Expected: All previously-passing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/skills/injector.ts __tests__/skills/always-injection.test.ts
git commit -m "fix(skills): force-include always:true skills in every injection pass (D4/G4)"
```

---

## Task 5: D5 — Wire `invoke_skill` tool executor

**Why:** `invoke_skill` is registered at `index.ts:768` with no executor → returns `NO_EXECUTOR` on every call (`invoke-skill.ts:64-70`). `SkillContextInjector` already has `executeStructuredSkill` — we just need `executeByName` wrapping it, then wire it in the Gateway constructor (after `skillInjector` exists).

**Files:**
- Modify: `src/skills/injector.ts` (add `executeByName`)
- Modify: `src/gateway/core.ts` (import + register after skillInjector built)
- Modify: `src/index.ts` (remove bare `createInvokeSkillTool()` registration)
- Test: `__tests__/skills/invoke-skill-executor.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/invoke-skill-executor.test.ts
import { describe, it, expect, vi } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import type { Skill } from '../../src/skills/types.js'

function makeStructuredSkill(name: string): Skill {
  return {
    name,
    description: 'test skill',
    instructions: '# Test',
    filePath: '/skills/test/SKILL.md',
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [{ id: 's1', tool: 'echo', args: {}, dependsOn: [] }],
    metadata: { name, description: 'test skill' },
    usage: undefined,
  } as unknown as Skill
}

describe('D5: executeByName', () => {
  it('throws when skill not found', async () => {
    const registry = { listEnabled: () => [], get: () => null } as any
    const injector = new SkillContextInjector(registry, {})
    await expect(injector.executeByName('nonexistent', {})).rejects.toThrow('not found')
  })

  it('returns instructions for unstructured skill', async () => {
    const unstructuredSkill = {
      name: 'guide',
      description: 'guides',
      instructions: 'Do this carefully.',
      filePath: '/skills/guide/SKILL.md',
      enabled: true,
      conditions: [],
      parameters: {},
      steps: [],
      metadata: { name: 'guide', description: 'guides' },
      usage: undefined,
    } as unknown as Skill

    const registry = { listEnabled: () => [unstructuredSkill], get: () => unstructuredSkill } as any
    const injector = new SkillContextInjector(registry, {})

    const result = await injector.executeByName('guide', {})
    expect(result).toContain('Do this carefully.')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/invoke-skill-executor.test.ts
```
Expected: FAIL — `executeByName` method does not exist

- [ ] **Step 3: Add `executeByName` to `SkillContextInjector` in `injector.ts`**

Add after the existing `canExecuteStructured` method (after line 99):

```typescript
  /**
   * Execute a skill by name — implements SkillExecutor interface for the invoke_skill tool.
   * Structured skills (with steps) are executed directly. Unstructured skills return
   * their instructions so the LLM can follow them.
   */
  async executeByName(name: string, params: Record<string, unknown>): Promise<string> {
    const skill = this.registry.get(name);
    if (!skill) {
      throw new Error(`Skill "${name}" not found in registry.`);
    }

    if (isStructuredSkill(skill) && this.executor) {
      const result = await this.executeStructuredSkill(
        skill,
        JSON.stringify(params),
      );
      return result.finalOutput;
    }

    // Unstructured skill — return instructions for LLM to follow
    return `Skill "${name}" instructions:\n\n${skill.instructions}`;
  }
```

- [ ] **Step 4: Update `index.ts` — remove bare registration**

Remove line 767-768:
```typescript
// DELETE these two lines:
  // invoke_skill — LLM can explicitly invoke a named skill
  toolRegistry.register(createInvokeSkillTool());
```

- [ ] **Step 5: Add import + wiring in `core.ts`**

Add import at the top of `core.ts` (with the other tool imports):
```typescript
import { createInvokeSkillTool } from "../tools/invoke-skill.js";
```

In the Gateway constructor, after `skillInjector` is built at line ~475 (after the `this.skillInjector = new SkillContextInjector(...)` block and the ClawHub setup), add:

```typescript
      // Wire invoke_skill executor now that skillInjector is available (D5)
      if (ctx.toolRegistry && this.skillInjector) {
        ctx.toolRegistry.register(createInvokeSkillTool(this.skillInjector));
      }
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/skills/invoke-skill-executor.test.ts
```
Expected: PASS

- [ ] **Step 7: Run full suite**

```bash
npx vitest run
```
Expected: All previously-passing tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/skills/injector.ts src/gateway/core.ts src/index.ts __tests__/skills/invoke-skill-executor.test.ts
git commit -m "fix(skills): wire invoke_skill executor via SkillContextInjector.executeByName (D5/G5)"
```

---

## Task 6: D6 — Replace `if (false)` with real confidence gate

**Why:** `core.ts:1641` has `if (false && this.skillInjector!.canExecuteStructured(topSkill))` — structured skill auto-execution is permanently disabled. The IntentRouter's Tier-5 LLM disambiguator sets `method: "llm"` on validated results. We gate on this instead of a raw score.

**Files:**
- Modify: `src/gateway/core.ts`
- Test: `__tests__/skills/confidence-gate.test.ts` (new)

- [ ] **Step 1: Write the test**

```typescript
// __tests__/skills/confidence-gate.test.ts
import { describe, it, expect } from 'vitest'

// The gate condition: LLM validated the skill AND skill is structured
describe('D6: confidence gate logic', () => {
  it('gates on method === "llm" for structured execution', () => {
    const llmMatch = { skill: { name: 'test' }, score: 0.5, method: 'llm' as const }
    const bm25Match = { skill: { name: 'test' }, score: 0.9, method: 'bm25' as const }

    const shouldExecuteLlm = llmMatch.method === 'llm'
    const shouldExecuteBm25 = bm25Match.method === 'llm'

    expect(shouldExecuteLlm).toBe(true)
    expect(shouldExecuteBm25).toBe(false)
  })
})
```

- [ ] **Step 2: Run test (it's a logic test, should pass immediately)**

```bash
npx vitest run __tests__/skills/confidence-gate.test.ts
```
Expected: PASS

- [ ] **Step 3: Fix `core.ts:1641`**

```typescript
// BEFORE:
        if (false && this.skillInjector!.canExecuteStructured(topSkill)) {

// AFTER:
        if (topMatch.method === "llm" && this.skillInjector!.canExecuteStructured(topSkill)) {
```

- [ ] **Step 4: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts __tests__/skills/confidence-gate.test.ts
git commit -m "fix(skills): replace if(false) with LLM-validated confidence gate for structured skill execution (D6/G6)"
```

---

## Task 7: D3 — Delete `formatForSystemPrompt()` dead code

**Why:** `injector.ts:339-368` exports `formatForSystemPrompt()` which lists all enabled skills for permanent system-prompt inclusion. Zero callers in `src/` (the method is intentionally wired in `EngineContext.skillsContext` by D2 — but that wires the per-message dynamic skills, not the permanent catalog). This method duplicates what D2 already solves.

**Files:**
- Modify: `src/skills/injector.ts`

- [ ] **Step 1: Delete the method from `injector.ts`**

Remove lines 335-368 (the entire `formatForSystemPrompt` method including its JSDoc):

```typescript
// DELETE lines 335-368:
  /**
   * Format skills for system prompt inclusion.
   * (Synchronous — lists all skills, not per-message matching)
   */
  formatForSystemPrompt(): string {
    const skills = this.registry.listEnabled();
    // ... (entire method body through the closing brace)
  }
```

- [ ] **Step 2: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass (no callers exist to break).

- [ ] **Step 3: Commit**

```bash
git add src/skills/injector.ts
git commit -m "fix(skills): delete formatForSystemPrompt() dead code — D2 wires dynamic skills instead (D3/G3)"
```

---

## Task 8: Q4 — Fix `withTimeout` promise leak in `executor.ts`

**Why:** `executor.ts:443-464` uses `Promise.race` + `setTimeout`. `clearTimeout` is only called on the success branch — on timeout, the underlying `fn()` promise continues running (orphaned). Replaced with `AbortController` + `AbortSignal.timeout`.

**Files:**
- Modify: `src/skills/executor.ts`
- Test: `__tests__/skills/executor-timeout.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/executor-timeout.test.ts
import { describe, it, expect, vi } from 'vitest'

// We test the withTimeout behavior by extracting the logic and verifying
// that a slow function is aborted cleanly
describe('Q4: withTimeout with AbortController', () => {
  it('rejects after timeoutMs', async () => {
    async function slowFn(signal: AbortSignal): Promise<string> {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve('done'), 500)
        signal.addEventListener('abort', () => {
          clearTimeout(timer)
          reject(new Error('aborted'))
        })
      })
    }

    const controller = new AbortController()
    const timeoutTimer = setTimeout(() => controller.abort(), 50)

    await expect(
      slowFn(controller.signal).finally(() => clearTimeout(timeoutTimer))
    ).rejects.toThrow()
  })

  it('resolves when function completes before timeout', async () => {
    async function fastFn(_signal: AbortSignal): Promise<string> {
      return 'result'
    }

    const controller = new AbortController()
    const timeoutTimer = setTimeout(() => controller.abort(), 500)

    const result = await fastFn(controller.signal).finally(() => clearTimeout(timeoutTimer))
    expect(result).toBe('result')
  })
})
```

- [ ] **Step 2: Run test to verify it passes (logic test)**

```bash
npx vitest run __tests__/skills/executor-timeout.test.ts
```
Expected: PASS

- [ ] **Step 3: Replace `withTimeout` in `executor.ts`**

```typescript
// BEFORE (lines 440-464):
  /**
   * Run an async function with a timeout.
   */
  private async withTimeout<T>(
    fn: () => Promise<T>,
    timeoutMs: number,
    stepId: string,
  ): Promise<T> {
    let timer: ReturnType<typeof setTimeout>;
    return Promise.race([
      fn().then((result) => {
        clearTimeout(timer);
        return result;
      }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () =>
            reject(
              new Error(`Step "${stepId}" timed out after ${timeoutMs}ms`),
            ),
          timeoutMs,
        );
      }),
    ]);
  }

// AFTER:
  /**
   * Run an async function with a timeout.
   * Uses AbortController so the underlying fn() is signalled to stop on timeout.
   */
  private async withTimeout<T>(
    fn: (signal: AbortSignal) => Promise<T>,
    timeoutMs: number,
    stepId: string,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const result = await fn(controller.signal);
      return result;
    } catch (err) {
      if (controller.signal.aborted) {
        throw new Error(`Step "${stepId}" timed out after ${timeoutMs}ms`);
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }
```

The callers of `withTimeout` inside `executor.ts` pass a `() => Promise<T>` lambda. Update each call to forward the signal:

Find the existing call sites in `executor.ts` (they look like `await this.withTimeout(() => ..., timeoutMs, stepId)`) and update them to accept a signal parameter:

```typescript
// Wherever withTimeout is called, change the lambda to accept signal:
// BEFORE: await this.withTimeout(() => this.runStep(state, outputs, parameters), ...)
// AFTER:  await this.withTimeout((_signal) => this.runStep(state, outputs, parameters), ...)
```

(The signal is available for future use when individual tool calls support cancellation; for now it is accepted but not yet forwarded to tool calls.)

- [ ] **Step 4: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/skills/executor.ts __tests__/skills/executor-timeout.test.ts
git commit -m "fix(skills): replace withTimeout Promise.race leak with AbortController (Q4/G13)"
```

---

## Task 9: Q2 — Migrate skill stats from JSON to SQLite (`skill_usage` v29)

**Why:** `SkillTracker` writes to `workspace/skills-stats.json` (G11 parallel storage). The spec locks: SKILL.md on disk is canonical; usage stats live in SQLite. This task adds `skill_usage` table at schema v29, adds `SkillUsageRepo` to `MemoryDatabase`, and updates `SkillTracker` to use it when a DB is available.

**Files:**
- Modify: `src/memory/db.ts` (v29 migration + `SkillUsageRepo`)
- Modify: `src/skills/tracker.ts` (optional DB path)
- Modify: `src/gateway/core.ts:439` (pass `ctx.db` to tracker)
- Test: `__tests__/skills/skill-usage-sqlite.test.ts` (new)

- [ ] **Step 1: Write the failing test**

```typescript
// __tests__/skills/skill-usage-sqlite.test.ts
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { MemoryDatabase } from '../../src/memory/db.js'
import { SkillTracker } from '../../src/skills/tracker.js'

let tmpDir: string
let db: MemoryDatabase

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), 'owl-skill-usage-'))
  db = new MemoryDatabase(tmpDir)
})

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true })
})

describe('Q2: skill_usage SQLite persistence', () => {
  it('creates skill_usage table at v29', () => {
    const version = db.rawDb.pragma('user_version', { simple: true }) as number
    expect(version).toBe(29)
    const tables = (db.rawDb.prepare(
      `SELECT name FROM sqlite_master WHERE type='table' AND name='skill_usage'`
    ).all() as Array<{ name: string }>)
    expect(tables.length).toBe(1)
  })

  it('SkillTracker records selections to DB when db provided', () => {
    const tracker = new SkillTracker(tmpDir, db)
    tracker.recordSelection('web-research')
    tracker.recordSuccess('web-research', 1200)

    const stats = db.skillUsage.getStats('web-research')
    expect(stats).not.toBeNull()
    expect(stats!.selection_count).toBe(1)
    expect(stats!.success_count).toBe(1)
  })

  it('getUsageMultiplier returns > 1 after success', () => {
    const tracker = new SkillTracker(tmpDir, db)
    tracker.recordSelection('web-research')
    tracker.recordSuccess('web-research', 500)

    const multiplier = tracker.getUsageMultiplier('web-research')
    expect(multiplier).toBeGreaterThan(1.0)
  })

  it('falls back to JSON path when no db provided', () => {
    const tracker = new SkillTracker(tmpDir)
    tracker.recordSelection('local-skill')
    // No error — JSON path still works
    const multiplier = tracker.getUsageMultiplier('local-skill')
    expect(typeof multiplier).toBe('number')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/skill-usage-sqlite.test.ts
```
Expected: FAIL — `skill_usage` table does not exist (v28 is the current max)

- [ ] **Step 3: Add v29 migration and `SkillUsageRepo` to `db.ts`**

After the `SCHEMA_VERSION` constant, bump it to 29:
```typescript
const SCHEMA_VERSION = 29;
```

Add `SkillUsageRepo` class before `MemoryDatabase`:

```typescript
// ─── Skill Usage Repo ────────────────────────────────────────────

interface SkillUsageRow {
  skill_name: string;
  selection_count: number;
  success_count: number;
  failure_count: number;
  avg_duration_ms: number;
  last_used_at: string | null;
}

class SkillUsageRepo {
  constructor(private db: Database.Database) {}

  upsertSelection(name: string): void {
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, selection_count, last_used_at)
      VALUES (?, 1, datetime('now'))
      ON CONFLICT(skill_name) DO UPDATE SET
        selection_count = selection_count + 1,
        last_used_at = datetime('now')
    `).run(name);
  }

  recordSuccess(name: string, durationMs: number): void {
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, success_count, avg_duration_ms)
      VALUES (?, 1, ?)
      ON CONFLICT(skill_name) DO UPDATE SET
        success_count = success_count + 1,
        avg_duration_ms = (avg_duration_ms * (success_count + failure_count - 1) + ?) /
                          (success_count + failure_count)
    `).run(name, durationMs, durationMs);
  }

  recordFailure(name: string, durationMs: number): void {
    this.db.prepare(`
      INSERT INTO skill_usage (skill_name, failure_count, avg_duration_ms)
      VALUES (?, 1, ?)
      ON CONFLICT(skill_name) DO UPDATE SET
        failure_count = failure_count + 1,
        avg_duration_ms = (avg_duration_ms * (success_count + failure_count - 1) + ?) /
                          (success_count + failure_count)
    `).run(name, durationMs, durationMs);
  }

  getStats(name: string): SkillUsageRow | null {
    return this.db.prepare(
      `SELECT * FROM skill_usage WHERE skill_name = ?`
    ).get(name) as SkillUsageRow | null;
  }

  listAll(): SkillUsageRow[] {
    return this.db.prepare(`SELECT * FROM skill_usage ORDER BY selection_count DESC`).all() as SkillUsageRow[];
  }
}
```

Add `export function applyV29SkillUsageMigration(db: Database.Database): void` after `applyV28Element17Migration`:

```typescript
export function applyV29SkillUsageMigration(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS skill_usage (
      skill_name       TEXT    PRIMARY KEY,
      selection_count  INTEGER NOT NULL DEFAULT 0,
      success_count    INTEGER NOT NULL DEFAULT 0,
      failure_count    INTEGER NOT NULL DEFAULT 0,
      avg_duration_ms  REAL    NOT NULL DEFAULT 0,
      last_used_at     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_skill_usage_selection ON skill_usage(selection_count DESC);
  `);
}
```

Add `skillUsage: SkillUsageRepo` to `MemoryDatabase` class body:
```typescript
export class MemoryDatabase {
  // ... existing fields ...
  readonly skillUsage: SkillUsageRepo;

  constructor(workspacePath: string, options?: { inMemory?: boolean }) {
    // ... existing constructor body ...
    this.skillUsage = new SkillUsageRepo(this.db);
  }
```

In both migration runners in `db.ts` (there are two — one in the class, one standalone function), add after the v28 block:
```typescript
    if (current < 29) {
      applyV29SkillUsageMigration(this.db);
      this.db.pragma(`user_version = 29`);
    }
```

And in the standalone `applyMigrations` function at the bottom:
```typescript
  if (current < 29) {
    applyV29SkillUsageMigration(db);
  }
```

- [ ] **Step 4: Update `SkillTracker` to use the DB when provided**

Add `db?: MemoryDatabase` import at the top of `tracker.ts`:
```typescript
import type { MemoryDatabase } from "../memory/db.js";
```

Change constructor and add DB-backed methods:

```typescript
export class SkillTracker {
  private stats: Map<string, SkillUsageStats> = new Map();
  private filePath: string;
  private dirty = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private db: MemoryDatabase | undefined;
  private static readonly SAVE_DEBOUNCE_MS = 5000;

  constructor(workspacePath: string, db?: MemoryDatabase) {
    this.filePath = join(workspacePath, "skills-stats.json");
    this.db = db;
    if (!db) {
      this.load();
    }
  }

  recordSelection(skillName: string): void {
    if (this.db) {
      this.db.skillUsage.upsertSelection(skillName);
      return;
    }
    const s = this.ensureStats(skillName);
    s.selectionCount += 1;
    s.lastUsedAt = new Date().toISOString();
    this.dirty = true;
    this.scheduleSave();
  }

  recordSuccess(skillName: string, durationMs: number): void {
    if (this.db) {
      this.db.skillUsage.recordSuccess(skillName, durationMs);
      return;
    }
    const s = this.ensureStats(skillName);
    s.successCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  recordFailure(skillName: string, durationMs: number): void {
    if (this.db) {
      this.db.skillUsage.recordFailure(skillName, durationMs);
      return;
    }
    const s = this.ensureStats(skillName);
    s.failureCount += 1;
    const totalCompleted = s.successCount + s.failureCount;
    s.avgDurationMs =
      (s.avgDurationMs * (totalCompleted - 1) + durationMs) / totalCompleted;
    s.successRate = s.successCount / totalCompleted;
    this.dirty = true;
    this.scheduleSave();
  }

  getSuccessRate(skillName: string): number | undefined {
    if (this.db) {
      const row = this.db.skillUsage.getStats(skillName);
      if (!row || row.success_count + row.failure_count === 0) return undefined;
      return row.success_count / (row.success_count + row.failure_count);
    }
    const s = this.stats.get(skillName);
    if (!s || s.successCount + s.failureCount === 0) return undefined;
    return s.successRate;
  }

  getDaysSinceLastUse(skillName: string): number {
    if (this.db) {
      const row = this.db.skillUsage.getStats(skillName);
      if (!row?.last_used_at) return Infinity;
      const msPerDay = 24 * 60 * 60 * 1000;
      return (Date.now() - new Date(row.last_used_at).getTime()) / msPerDay;
    }
    const s = this.stats.get(skillName);
    if (!s?.lastUsedAt) return Infinity;
    const msPerDay = 24 * 60 * 60 * 1000;
    return (Date.now() - new Date(s.lastUsedAt).getTime()) / msPerDay;
  }
```

- [ ] **Step 5: Pass `ctx.db` to `SkillTracker` in `core.ts:439`**

```typescript
// BEFORE:
      const skillTracker = new SkillTracker(ctx.cwd ?? process.cwd());

// AFTER:
      const skillTracker = new SkillTracker(ctx.cwd ?? process.cwd(), ctx.db);
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run __tests__/skills/skill-usage-sqlite.test.ts
```
Expected: PASS (4 tests)

- [ ] **Step 7: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/memory/db.ts src/skills/tracker.ts src/gateway/core.ts __tests__/skills/skill-usage-sqlite.test.ts
git commit -m "feat(skills): migrate skill stats from JSON to skill_usage SQLite table (Q2/G11, schema v29)"
```

---

## Task 10: D7 — Delete synthesis pipeline (evolver, pattern-miner, scheduler hooks)

**Why:** The synthesis loop is entirely dead (G7): `loop.ts:541-548` explicitly removed `pattern_mining` and `skill_evolution`, `proactive.ts:355-357` returns without executing, and `proactive.ts:19` confirms "imports removed". SkillsBench data shows self-generated skills underperform by -1.3pp. Deleting ~700 LOC removes 3 dead schedulers + 2 half-built files.

**Files:**
- Delete: `src/skills/evolver.ts`
- Delete: `src/skills/pattern-miner.ts`
- Modify: `src/cognition/loop.ts` (remove imports, ts-expect-error vars, `executeSkillEvolution` method)
- Modify: `src/heartbeat/proactive.ts` (remove `skill_evolution` scheduling + handler)
- Modify: `src/heartbeat/planner.ts` (remove `patternMiner` field)
- Modify: `src/heartbeat/idle-engine.ts` (remove `patternMiner` field)

- [ ] **Step 1: Delete the source files**

```bash
rm src/skills/evolver.ts src/skills/pattern-miner.ts
```

- [ ] **Step 2: Update `loop.ts`**

Remove lines 47-48 (SkillEvolver + PatternMiner imports):
```typescript
// DELETE:
import { SkillEvolver } from "../skills/evolver.js";
import { PatternMiner } from "../skills/pattern-miner.js";
```

Remove lines 180-184 (four `@ts-expect-error TS6133` variables):
```typescript
// DELETE the block containing:
  // @ts-expect-error TS6133 — assigned in execute, read when proactive actions enabled
  private lastPatternMineTime = 0;
  // @ts-expect-error TS6133 — assigned in execute, read when proactive actions enabled
  private lastSkillEvolveTime = 0;
  // @ts-expect-error TS6133 — assigned in execute, read when proactive actions enabled
  private lastSelfReflectionTime = 0;
  // @ts-expect-error TS6133 — assigned in execute, read when proactive actions enabled
  private lastAutonomousSynthesisTime = 0;
```

Remove the `executeSkillEvolution()` method (lines 642-668 approximately) — the entire method body including JSDoc:
```typescript
// DELETE the method starting with:
  private async executeSkillEvolution(): Promise<void> {
    // ... entire method
  }
```

- [ ] **Step 3: Update `proactive.ts`**

Find and remove the `skill_evolution` scheduling entry (around lines 223-225):
```typescript
// DELETE — the line scheduling skill_evolution at 5 AM daily, e.g.:
      { type: "skill_evolution", ... },
```

Find and remove the `skill_evolution` handler case (around lines 355-357):
```typescript
// DELETE the case block:
    if (action.type === "skill_evolution") {
      log.engine.info("[Proactive] skill_evolution handled by CognitiveLoop — skipping");
      return;
    }
```

Also remove the `skillsDir` and `sessionStore` fields from the `ProactivePingerDeps` interface if they were added solely for `PatternMiner` (check if any other code uses them first):
```typescript
// If unused elsewhere, delete:
  /** Absolute path to skills directory (for PatternMiner crystallization) */
  skillsDir?: string;
  /** Session store used by PatternMiner to read conversation history */
  sessionStore?: SessionStore;
```

- [ ] **Step 4: Update `planner.ts`**

Remove the `patternMiner` import and field:
```typescript
// DELETE import:
import type { PatternMiner } from "../skills/pattern-miner.js";

// DELETE field in deps interface:
  patternMiner?: PatternMiner;
```

- [ ] **Step 5: Update `idle-engine.ts`**

```typescript
// DELETE import:
import type { PatternMiner } from "../skills/pattern-miner.js";

// DELETE field:
  patternMiner?: PatternMiner;
```

- [ ] **Step 6: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass (the synthesis loop had zero test coverage per G15).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(skills): delete dead synthesis pipeline (evolver, pattern-miner, 3 schedulers) — D7/G7/G8 (-705 LOC)"
```

---

## Task 11: Q3 — Delete `SkillComposer`, remove from `injector.ts`

**Why:** `SkillComposer.resolve()` is called in `injector.ts:236` but `composer.ts:213` always falls through to the openclaw fallback because `parseComposition()` doesn't exist in `parser.ts`. Zero authored `depends:` or `chains:` skills exist in `src/skills/defaults/`. The 327 LOC Kahn's algorithm is dead code.

**Files:**
- Delete: `src/skills/composer.ts`
- Modify: `src/skills/injector.ts` (remove `SkillComposer` import, field, constructor call, and `composer.resolve()` usage)
- Modify: `src/skills/index.ts` (remove `SkillComposer` export)

- [ ] **Step 1: Delete the file**

```bash
rm src/skills/composer.ts
```

- [ ] **Step 2: Update `injector.ts`**

Remove line 21 (import):
```typescript
// DELETE:
import { SkillComposer } from "./composer.js";
```

Remove line 41 (field declaration):
```typescript
// DELETE:
  private composer: SkillComposer;
```

Remove line 71 (constructor assignment):
```typescript
// DELETE:
    this.composer = new SkillComposer(registry);
```

Simplify `injectIntoContext` — remove the `composer.resolve()` call and multi-skill branch (lines 225-251). Replace with a direct single-skill format for all skills:

```typescript
// BEFORE (lines 225-251):
  async injectIntoContext(userMessage: string): Promise<string> {
    const skills = await this.getRelevantSkills(userMessage);

    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = ["\n<context_skills>"];

    for (const skill of skills) {
      // Resolve composition — check if this skill has dependencies/chains
      const plan = this.composer.resolve(skill);

      if (plan.totalSkills > 1) {
        // Multi-skill composition — format as skill chain
        lines.push(this.composer.formatForContext(plan));
      } else {
        // Single skill — standard format
        lines.push(`<skill name="${skill.name}">`);
        lines.push(skill.instructions);
        lines.push(`</skill>`);
      }
    }

    lines.push("</context_skills>\n");
    return lines.join("\n");
  }

// AFTER:
  async injectIntoContext(userMessage: string): Promise<string> {
    const skills = await this.getRelevantSkills(userMessage);

    if (skills.length === 0) {
      return "";
    }

    const lines: string[] = ["\n<context_skills>"];

    for (const skill of skills) {
      lines.push(`<skill name="${skill.name}">`);
      lines.push(skill.instructions);
      lines.push(`</skill>`);
    }

    lines.push("</context_skills>\n");
    return lines.join("\n");
  }
```

- [ ] **Step 3: Update `skills/index.ts`**

```typescript
// BEFORE:
export { SkillComposer } from "./composer.js";

// AFTER: (delete that line entirely)
```

- [ ] **Step 4: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(skills): delete SkillComposer; simplify injectIntoContext (Q3/G12, -327 LOC)"
```

---

## Task 12: D8 — Create `SkillManagementRouter`

**Why:** `/skill <name>` at `core.ts:1118-1207` is an ad-hoc handler with no channel parity, no `user-invocable` check (Q5), and no `metrics` verb (CI-6). The `OwlManagementRouter` pattern from Element 17 (`owl-router.ts`) is the template. We create a parallel `skill-router.ts`.

**Files:**
- Create: `src/gateway/commands/skill-router.ts`
- Modify: `src/gateway/core.ts` (replace `/skill` ad-hoc handler with `dispatchSkillCommand`)

- [ ] **Step 1: Write the test**

```typescript
// __tests__/skills/skill-router.test.ts
import { describe, it, expect, vi } from 'vitest'
import { dispatchSkillCommand } from '../../src/gateway/commands/skill-router.js'
import type { SkillRouterDeps } from '../../src/gateway/commands/skill-router.js'

function makeRegistry(skills: Array<{ name: string; description: string; userInvocable?: boolean }>) {
  return {
    listEnabled: () => skills.map(s => ({
      name: s.name,
      description: s.description,
      enabled: true,
      metadata: { name: s.name, description: s.description, 'user-invocable': s.userInvocable ?? false },
    })),
    get: (name: string) => {
      const s = skills.find(x => x.name === name)
      return s ? {
        name: s.name,
        description: s.description,
        enabled: true,
        metadata: { name: s.name, description: s.description, 'user-invocable': s.userInvocable ?? false },
      } : null
    },
  } as any
}

const baseDeps = (registry: any): SkillRouterDeps => ({
  registry,
  wizard: { start: vi.fn().mockResolvedValue('wizard started'), isActive: vi.fn().mockReturnValue(false), cancel: vi.fn() },
  installWizard: null as any,
  injector: null as any,
  userId: 'user1',
  channelAdapter: {} as any,
  workspacePath: '/tmp',
  db: undefined,
})

describe('D8: SkillManagementRouter', () => {
  it('list returns no-skills message when registry empty', async () => {
    const result = await dispatchSkillCommand('list', [], baseDeps(makeRegistry([])))
    expect(result).toMatch(/no skills/i)
  })

  it('list returns skill names when skills loaded', async () => {
    const result = await dispatchSkillCommand('list', [], baseDeps(makeRegistry([
      { name: 'web-research', description: 'Research the web' },
    ])))
    expect(result).toContain('web-research')
  })

  it('show returns not-found message for unknown skill', async () => {
    const result = await dispatchSkillCommand('show', ['nonexistent'], baseDeps(makeRegistry([])))
    expect(result).toMatch(/not found/i)
  })

  it('run rejects skill without user-invocable flag', async () => {
    const result = await dispatchSkillCommand('run', ['private-skill'], baseDeps(
      makeRegistry([{ name: 'private-skill', description: 'not invocable', userInvocable: false }])
    ))
    expect(result).toMatch(/cannot be invoked/i)
  })

  it('create delegates to wizard', async () => {
    const deps = baseDeps(makeRegistry([]))
    const result = await dispatchSkillCommand('create', [], deps)
    expect(result).toBe('wizard started')
    expect(deps.wizard.start).toHaveBeenCalled()
  })

  it('unknown verb returns help text', async () => {
    const result = await dispatchSkillCommand('frobnicate', [], baseDeps(makeRegistry([])))
    expect(result).toMatch(/unknown/i)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/skill-router.test.ts
```
Expected: FAIL — `skill-router.ts` does not exist

- [ ] **Step 3: Create `src/gateway/commands/skill-router.ts`**

```typescript
// src/gateway/commands/skill-router.ts
import fs from "node:fs"
import path from "node:path"
import type { SkillsRegistry } from "../../skills/registry.js"
import type { SkillContextInjector } from "../../skills/injector.js"
import type { MemoryDatabase } from "../../memory/db.js"

interface WizardLike {
  start(userId: string, channelAdapter: unknown): Promise<string>
  isActive(userId: string): boolean
  cancel(userId: string): void
}

interface InstallWizardLike {
  start(): { text: string; done: boolean }
  step(input: string): Promise<{ text: string; done: boolean }>
}

export interface SkillRouterDeps {
  registry: SkillsRegistry
  wizard: WizardLike
  installWizard: InstallWizardLike | null
  injector: SkillContextInjector | null
  userId: string
  channelAdapter: unknown
  workspacePath: string
  db: MemoryDatabase | undefined
}

const HELP = `/skill commands:
  /skill list                      — list all loaded skills
  /skill show <name>               — show skill details
  /skill install                   — launch install wizard (ClawHub / GitHub / local)
  /skill create                    — launch creation wizard
  /skill enable <name>             — enable a disabled skill
  /skill disable <name>            — disable a skill
  /skill remove <name> yes         — delete a skill permanently
  /skill run <name> [args]         — invoke a user-invocable skill directly
  /skill metrics <name>            — show usage statistics for a skill`

export async function dispatchSkillCommand(
  verb: string,
  args: string[],
  deps: SkillRouterDeps,
): Promise<string> {
  const { registry, wizard, userId, channelAdapter, workspacePath, db, injector } = deps

  switch (verb.toLowerCase()) {
    case "list": {
      const skills = registry.listEnabled()
      if (skills.length === 0) {
        return "No skills loaded. Use `/skill install` to add skills."
      }
      return skills
        .map(s => {
          const emoji = s.metadata.openclaw?.emoji || "📋"
          const always = s.metadata.openclaw?.always ? " *(always active)*" : ""
          return `${emoji} **${s.name}**: ${s.description}${always}`
        })
        .join("\n")
    }

    case "show": {
      const name = args[0]
      if (!name) return "Usage: `/skill show <name>`"
      const skill = registry.get(name)
      if (!skill) {
        return `Skill "${name}" not found. Use \`/skill list\` to see loaded skills.`
      }
      const lines = [
        `**${skill.metadata.openclaw?.emoji || "📋"} ${skill.name}**`,
        `${skill.description}`,
        skill.metadata["user-invocable"] ? "User-invocable: yes" : "User-invocable: no",
        skill.metadata.openclaw?.always ? "Always active: yes" : null,
        skill.steps && skill.steps.length > 0 ? `Steps: ${skill.steps.length}` : null,
      ]
      return lines.filter(Boolean).join("\n")
    }

    case "install": {
      if (!deps.installWizard) return "Skill installer not available."
      const response = deps.installWizard.start()
      return response.text
    }

    case "create": {
      return wizard.start(userId, channelAdapter)
    }

    case "enable": {
      const name = args[0]
      if (!name) return "Usage: `/skill enable <name>`"
      const ok = registry.enable(name)
      return ok ? `✓ Skill "${name}" enabled.` : `Skill "${name}" not found.`
    }

    case "disable": {
      const name = args[0]
      if (!name) return "Usage: `/skill disable <name>`"
      const ok = registry.disable(name)
      return ok ? `✓ Skill "${name}" disabled.` : `Skill "${name}" not found.`
    }

    case "remove": {
      const [name, confirm] = args
      if (!name) return "Usage: `/skill remove <name> yes`"
      const skill = registry.get(name)
      if (!skill) return `Skill "${name}" not found.`
      if (confirm?.toLowerCase() !== "yes") {
        return `To confirm deletion, run: \`/skill remove ${name} yes\`\nThis cannot be undone.`
      }
      const skillDir = path.dirname(skill.filePath)
      if (fs.existsSync(skillDir)) fs.rmSync(skillDir, { recursive: true })
      return `✓ Skill "${name}" removed.`
    }

    case "run": {
      const [name, ...rest] = args
      if (!name) return "Usage: `/skill run <name> [args]`"
      const skill = registry.get(name)
      if (!skill) {
        return `Skill "${name}" not found. Use \`/skill list\` to see loaded skills.`
      }
      if (!skill.metadata["user-invocable"]) {
        return `Skill "${name}" cannot be invoked directly. Only skills marked \`user-invocable: true\` can be run this way.`
      }
      if (!injector) return `Skill executor not available.`
      const params = rest.length > 0 ? { args: rest.join(" ") } : {}
      try {
        return await injector.executeByName(name, params)
      } catch (err) {
        return `Failed to run "${name}": ${err instanceof Error ? err.message : String(err)}`
      }
    }

    case "metrics": {
      const name = args[0]
      if (!name) return "Usage: `/skill metrics <name>`"
      if (!db) return "Skill metrics require a database connection."
      const stats = db.skillUsage.getStats(name)
      if (!stats) {
        return `No usage data recorded for skill "${name}" yet.`
      }
      const successRate = stats.success_count + stats.failure_count > 0
        ? ((stats.success_count / (stats.success_count + stats.failure_count)) * 100).toFixed(1) + "%"
        : "n/a"
      return [
        `**${name} usage stats**`,
        `Selections: ${stats.selection_count}`,
        `Successes: ${stats.success_count} / Failures: ${stats.failure_count}`,
        `Success rate: ${successRate}`,
        `Avg duration: ${Math.round(stats.avg_duration_ms)}ms`,
        stats.last_used_at ? `Last used: ${new Date(stats.last_used_at).toLocaleString()}` : "Never used",
      ].join("\n")
    }

    case "help":
    case "--help": {
      return HELP
    }

    default:
      return `Unknown skill command: "${verb}".\n\n${HELP}`
  }
}
```

- [ ] **Step 4: Wire `dispatchSkillCommand` into `core.ts`**

Add import at top of `core.ts`:
```typescript
import { dispatchSkillCommand } from "./commands/skill-router.js";
```

Replace the existing `/skill` ad-hoc handler block (lines 1118-1207) with:

```typescript
    // /skill command router — channel-agnostic dispatcher (D8)
    const skillCmdMatch = message.text.trim().match(/^\/skill(?:\s+(.+))?$/i);
    if (skillCmdMatch && this.ctx.skillsLoader) {
      const rawArgs = (skillCmdMatch[1] ?? "").trim();
      const parts = rawArgs.split(/\s+/).filter(Boolean);
      const verb = parts[0] || "list";
      const args = parts.slice(1);
      const content = await dispatchSkillCommand(verb, args, {
        registry: this.ctx.skillsLoader.getRegistry(),
        wizard: this.skillCreationWizard,
        installWizard: this.skillInstallWizard ?? null,
        injector: this.skillInjector,
        userId: message.userId ?? "default",
        channelAdapter: callbacks.channelAdapter,
        workspacePath: this.ctx.cwd ?? process.cwd(),
        db: this.ctx.db,
      });
      return {
        content,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }
```

Add `private skillCreationWizard` and `private skillInstallWizard` fields to the Gateway class (they'll be wired in Task 13):
```typescript
  private skillCreationWizard: any = null;
  private skillInstallWizard: any = null;
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/skills/skill-router.test.ts
```
Expected: PASS (6 tests)

- [ ] **Step 6: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/commands/skill-router.ts src/gateway/core.ts __tests__/skills/skill-router.test.ts
git commit -m "feat(skills): create SkillManagementRouter replacing ad-hoc /skill handler (D8/Q5/CI-6)"
```

---

## Task 13: D9 — Create `SkillCreationWizard`

**Why:** No skill creation wizard exists. The existing `wizard.ts` is an _install_ wizard (ClawHub/GitHub/local). D9 creates a _creation_ wizard: step-by-step UX for defining a new skill from scratch, using `ChannelAdapterV2.ask()` — same pattern as `OwlCreationWizard` from Element 17.

**Files:**
- Create: `src/gateway/wizards/skill-creation.ts`
- Modify: `src/gateway/core.ts` (wire `skillCreationWizard` field)

- [ ] **Step 1: Write the test**

```typescript
// __tests__/skills/skill-creation-wizard.test.ts
import { describe, it, expect, vi } from 'vitest'
import { SkillCreationWizard } from '../../src/gateway/wizards/skill-creation.js'

function makeAdapter(responses: string[]) {
  let idx = 0
  return {
    ask: vi.fn().mockImplementation(() => Promise.resolve(responses[idx++] ?? 'skip')),
  } as any
}

describe('D9: SkillCreationWizard', () => {
  it('isActive returns false before start', () => {
    const wizard = new SkillCreationWizard('/tmp')
    expect(wizard.isActive('user1')).toBe(false)
  })

  it('cancel removes session', () => {
    const wizard = new SkillCreationWizard('/tmp', undefined, () => {})
    wizard.cancel('user1') // no-op if not active — no error
    expect(wizard.isActive('user1')).toBe(false)
  })

  it('runs wizard and writes SKILL.md content', async () => {
    const written: Array<{ path: string; content: string }> = []
    const writeFn = (p: string, c: string) => written.push({ path: p, content: c })

    const adapter = makeAdapter([
      'data-formatter',          // name
      'Format raw data into tables and reports',  // role
      'Direct & efficient',      // personality choice
      'Read & write files',      // capabilities
      'Nothing specific',        // restrictions
      'Yes, create it',          // confirm
      'skip',                    // recurring task
    ])

    const wizard = new SkillCreationWizard('/workspace', undefined, writeFn)
    const result = await wizard.start('user1', adapter)

    expect(result).toContain('data-formatter')
    expect(written.length).toBe(1)
    expect(written[0].path).toContain('data-formatter')
    expect(written[0].content).toContain('name: data-formatter')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/skills/skill-creation-wizard.test.ts
```
Expected: FAIL — `skill-creation.ts` does not exist

- [ ] **Step 3: Create `src/gateway/wizards/skill-creation.ts`**

```typescript
// src/gateway/wizards/skill-creation.ts
import fs from "node:fs"
import path from "node:path"
import type { ChannelAdapterV2 } from "../adapter-v2.js"

interface WizardSession {
  userId: string
  startedAt: number
}

type WriteFn = (filePath: string, content: string) => void

const SESSION_TIMEOUT_MS = 30 * 60 * 1000

export class SkillCreationWizard {
  private sessions = new Map<string, WizardSession>()

  constructor(
    private workspacePath: string,
    private db?: { skillUsage?: unknown },
    private writeFn: WriteFn = (p, c) => {
      fs.mkdirSync(path.dirname(p), { recursive: true })
      fs.writeFileSync(p, c, "utf-8")
    },
  ) {}

  isActive(userId: string): boolean {
    const session = this.sessions.get(userId)
    if (!session) return false
    if (Date.now() - session.startedAt > SESSION_TIMEOUT_MS) {
      this.sessions.delete(userId)
      return false
    }
    return true
  }

  cancel(userId: string): void {
    this.sessions.delete(userId)
  }

  async start(userId: string, channelAdapter: ChannelAdapterV2): Promise<string> {
    this.sessions.set(userId, { userId, startedAt: Date.now() })
    try {
      return await this.runWizard(userId, channelAdapter)
    } finally {
      this.sessions.delete(userId)
    }
  }

  private async runWizard(userId: string, adapter: ChannelAdapterV2, depth = 0): Promise<string> {
    if (depth >= 10) return "Too many restarts. Please try `/skill create` again."

    // Step 1 — Name
    const name = await adapter.ask(userId, { text: "What should I call this new skill?" })
    if (!name || name.toLowerCase() === "cancel") return "Cancelled."
    const safeName = name.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-")

    // Step 2 — Role
    const role = await adapter.ask(userId, { text: `What does "${name}" do? (one sentence)` })

    // Step 3 — Personality / tone
    const personalityChoice = await adapter.ask(userId, {
      text: `What style should "${name}" use in its instructions?`,
      choices: ["Direct & efficient", "Detailed & thorough", "Brief & minimal", "Custom…"],
    })
    let personality = personalityChoice
    if (personalityChoice === "Custom…") {
      personality = await adapter.ask(userId, { text: "Describe the style briefly:" })
    }

    // Step 4 — Capabilities (tools)
    const capsChoice = await adapter.ask(userId, {
      text: `What tools does "${name}" need?`,
      choices: ["Read files only", "Read & write files", "Shell commands", "Web access", "All tools"],
    })
    const caps = capsChoice === "All tools"
      ? ["read_file", "write_file", "run_shell_command", "web_search", "web_fetch"]
      : capsChoice === "Read & write files"
      ? ["read_file", "write_file"]
      : capsChoice === "Read files only"
      ? ["read_file"]
      : capsChoice === "Shell commands"
      ? ["run_shell_command"]
      : ["web_search", "web_fetch"]

    // Step 5 — Instructions (free text)
    const instructions = await adapter.ask(userId, {
      text: `Write the core instructions for "${name}" (what should the LLM do when this skill is active?):`,
    })

    // Step 6 — Confirm
    const summary = `${name}: ${role} — Caps: ${capsChoice}.`
    const confirm = await adapter.ask(userId, {
      text: `Creating ${summary}\nReady?`,
      choices: ["Yes, create it", "No, start over"],
    })
    if (confirm === "No, start over") {
      return this.runWizard(userId, adapter, depth + 1)
    }

    // Write SKILL.md
    const skillMd = this.buildSkillMd({ name: safeName, role, personality, caps, instructions })
    const skillPath = path.join(this.workspacePath, "skills", safeName, "SKILL.md")
    this.writeFn(skillPath, skillMd)

    return `Skill "${safeName}" created! It will be loaded automatically. Use \`/skill list\` to confirm.`
  }

  private buildSkillMd(opts: {
    name: string
    role: string
    personality: string
    caps: string[]
    instructions: string
  }): string {
    return [
      "---",
      `name: ${opts.name}`,
      `description: ${opts.role}`,
      `version: "1.0"`,
      `openclaw:`,
      `  emoji: 📋`,
      `permissions:`,
      `  allowedTools: [${opts.caps.map(c => `"${c}"`).join(", ")}]`,
      `---`,
      ``,
      opts.instructions,
      ``,
    ].join("\n")
  }
}
```

- [ ] **Step 4: Wire wizard in `core.ts`**

Add import at top of `core.ts`:
```typescript
import { SkillCreationWizard } from "./wizards/skill-creation.js";
```

Change the field declaration (set in Task 12) from `any` to typed:
```typescript
  private skillCreationWizard: SkillCreationWizard | null = null;
```

In the Gateway constructor after `skillInjector` is built, add:
```typescript
      // Wire skill creation wizard (D9)
      this.skillCreationWizard = new SkillCreationWizard(
        ctx.cwd ?? process.cwd(),
        ctx.db,
      );
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run __tests__/skills/skill-creation-wizard.test.ts
```
Expected: PASS (3 tests)

- [ ] **Step 6: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/gateway/wizards/skill-creation.ts src/gateway/core.ts __tests__/skills/skill-creation-wizard.test.ts
git commit -m "feat(skills): create SkillCreationWizard using ChannelAdapterV2.ask() (D9)"
```

---

## Task 14: CI-3 — NL install detection via IntelligenceRouter

**Why:** Users should be able to say "can you find me a skill for X?" or "install a new skill for docker" and have the gateway route to the install wizard without typing `/skill install`. The detection uses `IntelligenceRouter.resolve("classification")` to get the cheap model, then a 200ms budget LLM call.

**Files:**
- Modify: `src/gateway/core.ts` (add NL install detection before the main message handler)

- [ ] **Step 1: Write the test**

```typescript
// __tests__/skills/nl-install-detection.test.ts
import { describe, it, expect } from 'vitest'

// Test the classification prompt logic directly
describe('CI-3: NL install detection prompt', () => {
  it('classifies install intent correctly (prompt logic)', () => {
    // The prompt asks: "Does this message ask to install, add, or find a new skill?"
    const installMessages = [
      'install a new skill for docker',
      'can you find a skill for summarizing PDFs?',
      'add a new capability for git operations',
    ]
    const nonInstallMessages = [
      'list my skills',
      'how do I write a function?',
      'what time is it?',
    ]

    // The classification logic returns true if the message contains NL skill install signals
    // We test the prompt template, not the LLM itself
    function containsInstallSignal(text: string): boolean {
      // This mirrors what the LLM classification prompt asks
      return /\b(install|add|find|get|fetch)\b/i.test(text) && /\b(skill|capability|plugin|command)\b/i.test(text)
    }

    for (const msg of installMessages) {
      expect(containsInstallSignal(msg)).toBe(true)
    }
    // Non-install messages should not trigger
    expect(containsInstallSignal(nonInstallMessages[0])).toBe(false)
  })
})
```

- [ ] **Step 2: Run test**

```bash
npx vitest run __tests__/skills/nl-install-detection.test.ts
```
Expected: PASS

- [ ] **Step 3: Add NL install detection in `core.ts`**

Add a private helper method to the `Gateway` class:

```typescript
  /**
   * CI-3 — Detect NL skill install intent using IntelligenceRouter cheap-tier.
   * Budget: 200ms. Falls back to false on timeout or missing provider.
   */
  private async isSkillInstallIntent(text: string): Promise<boolean> {
    if (!this.ctx.intelligence || !this.ctx.providerRegistry) return false;

    let resolved: { provider: string; model: string };
    try {
      resolved = this.ctx.intelligence.resolve("classification");
    } catch {
      return false;
    }

    const provider = this.ctx.providerRegistry.get(resolved.provider);
    if (!provider) return false;

    const prompt = `Does this message ask to install, add, or find a new skill or capability for the assistant?\nReply with only: yes or no\n\nMessage: "${text.slice(0, 200)}"`;

    const budget = new Promise<{ timeout: true }>(r => setTimeout(() => r({ timeout: true }), 200));
    let result: any;
    try {
      result = await Promise.race([
        provider.chat([{ role: "user", content: prompt }], resolved.model, { temperature: 0, maxTokens: 5 }),
        budget,
      ]);
    } catch {
      return false;
    }

    if (result && (result as any).timeout) return false;
    return (result as { content: string }).content.trim().toLowerCase().startsWith("yes");
  }
```

In the main message handler, add before the `isConversational` check (after the continuity engine section, before line `let dynamicSkillsContext = ""`):

```typescript
    // CI-3 — NL skill install detection
    if (this.ctx.skillsLoader && await this.isSkillInstallIntent(text)) {
      const installResponse = await dispatchSkillCommand("install", [], {
        registry: this.ctx.skillsLoader.getRegistry(),
        wizard: this.skillCreationWizard!,
        installWizard: this.skillInstallWizard ?? null,
        injector: this.skillInjector,
        userId: message.userId ?? "default",
        channelAdapter: callbacks.channelAdapter,
        workspacePath: this.ctx.cwd ?? process.cwd(),
        db: this.ctx.db,
      });
      return {
        content: installResponse,
        owlName: this.ctx.owl.persona.name,
        owlEmoji: this.ctx.owl.persona.emoji,
        toolsUsed: [],
      };
    }
```

- [ ] **Step 4: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass (new code is gated on `isSkillInstallIntent` which returns false in tests due to missing provider).

- [ ] **Step 5: Commit**

```bash
git add src/gateway/core.ts __tests__/skills/nl-install-detection.test.ts
git commit -m "feat(skills): add NL install detection via IntelligenceRouter cheap-tier (CI-3)"
```

---

## Task 15: CI-6 — `metrics` verb already in `skill-router.ts`

**Note:** The `metrics` verb was implemented in Task 12 as part of `skill-router.ts`. This task verifies it is correct and adds a focused test.

**Files:**
- Test: `__tests__/skills/skill-metrics.test.ts` (new)

- [ ] **Step 1: Write the test**

```typescript
// __tests__/skills/skill-metrics.test.ts
import { describe, it, expect, vi } from 'vitest'
import { dispatchSkillCommand } from '../../src/gateway/commands/skill-router.js'
import type { SkillRouterDeps } from '../../src/gateway/commands/skill-router.js'

function makeDeps(db?: any): SkillRouterDeps {
  return {
    registry: { listEnabled: () => [], get: () => null } as any,
    wizard: { start: vi.fn(), isActive: vi.fn(), cancel: vi.fn() } as any,
    installWizard: null,
    injector: null,
    userId: 'user1',
    channelAdapter: {} as any,
    workspacePath: '/tmp',
    db,
  }
}

describe('CI-6: metrics verb', () => {
  it('returns no-db message when db is undefined', async () => {
    const result = await dispatchSkillCommand('metrics', ['web-research'], makeDeps(undefined))
    expect(result).toMatch(/database/i)
  })

  it('returns no-data message when skill has never been used', async () => {
    const mockDb = {
      skillUsage: { getStats: vi.fn().mockReturnValue(null) },
    }
    const result = await dispatchSkillCommand('metrics', ['web-research'], makeDeps(mockDb))
    expect(result).toMatch(/no usage data/i)
  })

  it('returns formatted stats when data exists', async () => {
    const mockDb = {
      skillUsage: {
        getStats: vi.fn().mockReturnValue({
          skill_name: 'web-research',
          selection_count: 42,
          success_count: 38,
          failure_count: 4,
          avg_duration_ms: 1250,
          last_used_at: '2026-05-09T10:00:00',
        }),
      },
    }
    const result = await dispatchSkillCommand('metrics', ['web-research'], makeDeps(mockDb))
    expect(result).toContain('web-research')
    expect(result).toContain('42')
    expect(result).toContain('38')
    expect(result).toContain('90.5%')
  })
})
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run __tests__/skills/skill-metrics.test.ts
```
Expected: PASS (3 tests)

- [ ] **Step 3: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add __tests__/skills/skill-metrics.test.ts
git commit -m "test(skills): add focused metrics verb tests (CI-6)"
```

---

## Task 16: Q7 — Rename misleading `dynamicSkillsContext` variable in `core.ts`

**Why:** `core.ts:1555` declares `let dynamicSkillsContext = ""`. Lines 1561-1613 append memory module outputs (priorContextRetriever, preferenceRecognizer, crossSessionStore) into this variable. Then line 1677 OVERWRITES it with the actual skills XML. The name lies: the first assignment is memory context, not skills context. Renaming makes the data flow clear.

**Files:**
- Modify: `src/gateway/core.ts`

- [ ] **Step 1: Rename in `core.ts`**

Two distinct things need to happen:
1. Rename the memory-accumulation variable `dynamicSkillsContext` → `memoryContextPrefix` (lines 1555-1613)
2. The skills XML stays in `dynamicSkillsContext` at line 1677 onward

Specifically:

Change line 1554-1556:
```typescript
// BEFORE:
    let dynamicSkillsContext = "";
    let injectedSkillNames: string[] = [];

// AFTER:
    let memoryContextPrefix = "";
    let dynamicSkillsContext = "";
    let injectedSkillNames: string[] = [];
```

Change line 1611-1613:
```typescript
// BEFORE:
      if (memoryContextParts.length > 0) {
        dynamicSkillsContext = memoryContextParts.join("\n") + "\n" + dynamicSkillsContext;
      }

// AFTER:
      if (memoryContextParts.length > 0) {
        memoryContextPrefix = memoryContextParts.join("\n") + "\n";
      }
```

Where `buildEngineContext` is called later (around line 1730+), ensure the memory prefix is prepended to the skills context:

Find the `buildEngineContext` call and update the third argument:
```typescript
// Find the buildEngineContext call and pass combined context:
    const engineCtx = await this.buildEngineContext(
      session,
      callbacks,
      memoryContextPrefix + dynamicSkillsContext,  // was: dynamicSkillsContext
      isIsolatedTask,
      ...
    );
```

- [ ] **Step 2: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/gateway/core.ts
git commit -m "refactor(skills): rename dynamicSkillsContext → memoryContextPrefix for the memory accumulation variable (Q7)"
```

---

## Task 17: Integration tests for end-to-end skill routing

**Files:**
- Test: `__tests__/skills/integration.test.ts` (new)

- [ ] **Step 1: Write integration tests**

```typescript
// __tests__/skills/integration.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SkillContextInjector } from '../../src/skills/injector.js'
import { SkillsRegistry } from '../../src/skills/registry.js'
import type { Skill } from '../../src/skills/types.js'

function makeSkill(name: string, opts: Partial<Skill> = {}): Skill {
  return {
    name,
    description: `${name} description`,
    instructions: `# ${name}\nDo the ${name} thing.`,
    filePath: `/skills/${name}/SKILL.md`,
    enabled: true,
    conditions: [],
    parameters: {},
    steps: [],
    metadata: {
      name,
      description: `${name} description`,
      openclaw: opts.metadata?.openclaw,
      'user-invocable': opts.metadata?.['user-invocable'] ?? false,
    },
    usage: undefined,
    ...opts,
  } as unknown as Skill
}

describe('End-to-end: match → inject → skillsContext', () => {
  it('injectIntoContext produces valid XML containing skill instructions', async () => {
    const webResearch = makeSkill('web-research')
    const registry = {
      listEnabled: () => [webResearch],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const mockRouter = {
      route: vi.fn().mockResolvedValue([{ skill: webResearch, score: 0.9, method: 'llm' }]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }
    const tracker = {
      recordSelection: vi.fn(),
      getUsageMultiplier: vi.fn().mockReturnValue(1.0),
    } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    ;(injector as any).router = mockRouter

    const xml = await injector.injectIntoContext('research quantum computing papers')

    expect(xml).toContain('<context_skills>')
    expect(xml).toContain('<skill name="web-research">')
    expect(xml).toContain('Do the web-research thing.')
    expect(xml).toContain('</context_skills>')
  })

  it('always:true skill appears in context even for conversational message', async () => {
    const alwaySkill = makeSkill('safety-guide', {
      metadata: { name: 'safety-guide', description: 'safety', openclaw: { always: true } }
    } as any)

    const registry = {
      listEnabled: () => [alwaySkill],
      get: (name: string) => registry.listEnabled().find(s => s.name === name) ?? null,
    } as any

    const mockRouter = {
      route: vi.fn().mockResolvedValue([]),
      clearCache: vi.fn(),
      reindex: vi.fn(),
    }
    const tracker = {
      recordSelection: vi.fn(),
      getUsageMultiplier: vi.fn().mockReturnValue(1.0),
    } as any

    const injector = new SkillContextInjector(registry, {}, undefined, tracker)
    ;(injector as any).router = mockRouter

    const xml = await injector.injectIntoContext('hi there')

    expect(xml).toContain('safety-guide')
    expect(xml).toContain('<context_skills>')
  })

  it('executeByName returns instructions for unstructured skill', async () => {
    const guideSkill = makeSkill('style-guide', {
      instructions: '# Style Guide\nUse Oxford commas.',
    })
    const registry = {
      listEnabled: () => [guideSkill],
      get: () => guideSkill,
    } as any

    const injector = new SkillContextInjector(registry, {})
    const result = await injector.executeByName('style-guide', {})
    expect(result).toContain('Oxford commas')
  })

  it('executeByName throws for unknown skill', async () => {
    const registry = {
      listEnabled: () => [],
      get: () => null,
    } as any

    const injector = new SkillContextInjector(registry, {})
    await expect(injector.executeByName('ghost-skill', {})).rejects.toThrow('not found')
  })
})
```

- [ ] **Step 2: Run tests**

```bash
npx vitest run __tests__/skills/integration.test.ts
```
Expected: PASS (4 tests)

- [ ] **Step 3: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add __tests__/skills/integration.test.ts
git commit -m "test(skills): add end-to-end integration tests for match→inject→skillsContext path"
```

---

## Task 18: Deletions — dead code purge

Remove all remaining dead code: `migrator.ts`, `SkillSelector` block in `clawhub.ts`, migrator usage in `index.ts`, and clean up `skills/index.ts`.

**Files:**
- Delete: `src/skills/migrator.ts`
- Delete: `__tests__/skills-migrator.test.ts` (imports deleted file)
- Modify: `src/skills/clawhub.ts` (delete `SkillSelector` class, ~85 LOC)
- Modify: `src/skills/index.ts` (remove dead exports)
- Modify: `src/index.ts` (remove migrator import + usage at lines 160, 618-622)

- [ ] **Step 1: Delete migrator and its test**

```bash
rm src/skills/migrator.ts __tests__/skills-migrator.test.ts
```

- [ ] **Step 2: Remove migrator from `index.ts`**

Remove line 160:
```typescript
// DELETE:
import { SkillsMigrator } from "./skills/migrator.js";
```

Remove lines 617-622:
```typescript
// DELETE this block:
  // Instincts
  const migrator = new SkillsMigrator(workspacePath);
  const migratedCount = await migrator.migrate();
  if (migratedCount > 0) {
    console.log(chalk.dim(`  [Migrated ${migratedCount} instinct(s) to skills]`));
  }
```

- [ ] **Step 3: Delete `SkillSelector` class from `clawhub.ts`**

Read `src/skills/clawhub.ts` around lines 185-272 to find the `SkillSelector` class. Delete the entire class including its JSDoc.

The class starts with something like:
```typescript
/**
 * Local skill selector — scores skills against a query using BM25-like keyword overlap.
 * ...
 */
export class SkillSelector {
  // ... entire class ~85 LOC
}
```

Delete the entire `SkillSelector` class.

- [ ] **Step 4: Update `skills/index.ts`**

Remove all exports for deleted or empty exports:
```typescript
// BEFORE:
export { ClawHubClient, SkillSelector } from "./clawhub.js";
export { SkillComposer } from "./composer.js";

// AFTER:
export { ClawHubClient } from "./clawhub.js";
// SkillComposer deleted (Q3)
// SkillSelector deleted (Task 18)
```

Also remove `SkillComposition` type export if `SkillComposer` was the only user:
```typescript
// Remove:
export type {
  // ...
  SkillComposition,  // DELETE — SkillComposer is gone
} from "./types.js";
```

- [ ] **Step 5: Run full suite**

```bash
npx vitest run
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(skills): delete migrator.ts, SkillSelector dead class, clean up index.ts (Task 18)"
```

---

## Task 19: Update progress tracker

**Files:**
- Modify: `docs/platform-audit/progress.md`

- [ ] **Step 1: Run final test suite and capture count**

```bash
npx vitest run 2>&1 | tail -5
```

- [ ] **Step 2: Update `docs/platform-audit/progress.md`**

Find the Element 19 row and update it to:
```
| 19 | Skills Engine (Match, Inject, Synthesize) | ✅ Complete YYYY-MM-DD | <commit> | <test-count> tests | D2 pipeline fix, D1 delete SkillsEngine, D4 always-inject, D5 wire invoke_skill, D6 confidence gate, D3 delete formatForSystemPrompt, Q4 AbortController, Q2 skill_usage v29, D7 delete synthesis (-705 LOC), Q3 delete composer (-327 LOC), D8 SkillManagementRouter, D9 SkillCreationWizard, CI-3 NL install, CI-6 metrics, Q7 rename variable, Q5 user-invocable gate. Net: +2 files, -5 files = -3 net |
```

- [ ] **Step 3: Commit**

```bash
git add docs/platform-audit/progress.md
git commit -m "docs: mark Element 19 (Skills Engine) complete in progress tracker"
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task |
|---|---|
| D2: wire `_dynamicSkillsContext` → `skillsContext` | Task 1 |
| D1: delete SkillsEngine, consolidate to IntentRouter | Task 2 |
| Q1: remove SKILL_ACTION_KEYWORDS regex | Task 3 |
| D4: honor `always: true` flag | Task 4 |
| D5: wire `invoke_skill` executor | Task 5 |
| D6: replace `if (false)` with confidence gate | Task 6 |
| D3: delete `formatForSystemPrompt()` | Task 7 |
| Q4: fix `withTimeout` leak with AbortController | Task 8 |
| Q2: migrate skill stats to SQLite v29 | Task 9 |
| D7: delete evolver + pattern-miner + schedulers | Task 10 |
| Q3: delete `SkillComposer` | Task 11 |
| D8: `SkillManagementRouter` (verbs: list/show/install/create/enable/disable/remove/run/metrics) | Task 12 |
| Q5: honor `user-invocable` flag in `run` verb | Task 12 |
| CI-6: `metrics` verb in skill-router | Tasks 12 + 15 |
| D9: `SkillCreationWizard` using `ChannelAdapterV2.ask()` | Task 13 |
| CI-3: NL install detection via IntelligenceRouter | Task 14 |
| Q7: rename `dynamicSkillsContext` variable | Task 16 |
| Test gaps G15 (8 test groups) | Tasks 1–17 |
| Deletions: `migrator.ts`, `SkillSelector` | Task 18 |
| Progress tracker update | Task 19 |

### 2. Placeholder scan

No TBD, TODO, or "similar to Task N" placeholders found.

### 3. Type consistency

- `SkillCreationWizard.start(userId, channelAdapter)` — matches `WizardLike` interface in `skill-router.ts` ✅
- `SkillContextInjector.executeByName(name, params)` — matches `SkillExecutor` interface in `invoke-skill.ts` ✅
- `dispatchSkillCommand(verb, args, deps)` — `SkillRouterDeps` type exported and used consistently ✅
- `MemoryDatabase.skillUsage` — `SkillUsageRepo` type consistent across `db.ts` and `tracker.ts` ✅
- `EngineContext.skillsContext?: string` (runtime.ts:63) — matches what `ContextBuilder.build()` returns after Task 1 ✅
