# Element 19 — Skills Engine Design Spec

**Date:** 2026-05-09  
**Status:** Boss-approved, ready for implementation plan  
**Element:** 19 — Skills engine (match, inject, synthesize)  
**Inputs:** Phase 1 audit (15 gaps G1–G15), Phase 2 market research (11 sections), Phase 3 architecture review (9 decisions D1–D9)

---

## Goal

Repair the broken skill injection pipeline so skills actually reach the LLM, consolidate three competing matchers into one, wire the `invoke_skill` tool, enable auto-execution of structured skills, unify all skill management commands behind a single channel-agnostic router, and delete ~1000 LOC of dead code.

## Architecture

The core problem is a single underscore prefix. `_dynamicSkillsContext` at `context-builder.ts:34` is discarded by convention — the entire IntentRouter pipeline runs, builds XML, and silently disappears before the LLM sees anything. Every other fix in this element is additive on top of that one rename.

### Before (broken)

```
user message
  → SkillsEngine.evaluate()          ← LLM call #1 (duplicate, direct provider.chat)
  → IntentRouter.route()             ← LLM call #2 (5-tier, Tier 1 is a stub)
      → injector.injectIntoContext() ← builds <context_skills> XML
          → ContextBuilder._dynamicSkillsContext  ← DISCARDED (underscore)
              → EngineContext (skillsContext field never set)
                  → LLM sees nothing
```

### After (fixed)

```
user message
  → intelligenceRouter.classify("is_action_request")   ← cheap-tier pre-filter
  → IntentRouter.route({ mode: "behavioral|dynamic" }) ← single unified matcher
      → always-skills pre-pended (confidence: 1.0, max 3)
      → matched skills by score descending (max 5)
      → injector.injectIntoContext() ← builds <context_skills> XML
          → ContextBuilder.skillsContext  ← ONE RENAME, pipeline works
              → EngineContext.skillsContext
                  → runtime.ts:2563 "## Skills — AVAILABLE PLAYBOOKS"
                      → LLM sees skills ✓
```

---

## Components

### Deleted (−1181 LOC, −5 files)

| File | LOC | Reason |
|---|---|---|
| `src/skills/engine.ts` | 89 | D1: duplicate LLM matcher, G10 violation |
| `src/skills/evolver.ts` | 279 | D7: synthesis pipeline dead, SkillsBench −1.3pp |
| `src/skills/pattern-miner.ts` | 426 | D7: synthesis dead, bypasses SkillCritic (G8) |
| `src/skills/composer.ts` | 327 | Q3: multi-stage branch unreachable, parser never sets `composition` field |
| `src/skills/migrator.ts` | 60 | One-shot migrator, no version guard — delete after confirming ran |
| `SkillSelector` block in `clawhub.ts:188-272` | ~85 | D1: dead keyword scorer, never imported |

### Created (+2 files, ~270 LOC)

| File | Responsibility |
|---|---|
| `src/gateway/commands/skill-router.ts` | D8: `dispatchSkillCommand(verb, args, deps)` — channel-parity dispatcher; `verb ∈ {install, list, show, enable, disable, remove, run, metrics}` |
| `src/gateway/wizards/skill-creation.ts` | D9: `SkillCreationWizard` using `ChannelAdapterV2.ask()`, per-userId session Map |

### Modified (key changes)

| File | Changes |
|---|---|
| `src/gateway/handlers/context-builder.ts` | D2: rename `_dynamicSkillsContext` → `skillsContext`; set `skillsContext` on returned `EngineContext` |
| `src/gateway/core.ts` | D1 remove `SkillsEngine` wiring; D6 replace `if(false)`; D7 remove scheduler; D8 route slash commands to `dispatchSkillCommand`; Q1 replace `SKILL_ACTION_KEYWORDS`; Q7 rename memory accumulation variable |
| `src/skills/injector.ts` | D3 delete `formatForSystemPrompt`; D4 always-skills pre-pend; D5 add `executeByName()`; D9 thread `channelAdapter` into `MissingParamsError` handler |
| `src/skills/intent-router.ts` | D1: delete BM25 stub tier (Tier 1 → renamed tiers); add `mode` option for behavioral routing |
| `src/skills/executor.ts` | Q4: replace `withTimeout` `Promise.race` with `AbortController` pattern |
| `src/skills/tracker.ts` | Q2: write to SQLite `skill_usage` table; migrate from JSON on first boot |
| `src/skills/critic.ts` | Q1: replace 4 hardcoded regex/substring sites with `IntelligenceRouter` cheap-tier |
| `src/tools/invoke-skill.ts` | D5: no code change — executor passed from call site |
| `src/index.ts` | D5: pass `skillInjectorAdapter` to `createInvokeSkillTool()` |
| `src/cognition/loop.ts` | D7: remove `@ts-expect-error` vars, dead `executeSkillEvolution`/`executePatternMining` methods |
| `src/heartbeat/proactive.ts` | D7: remove `skill_evolution` job scheduling |
| `src/memory/db.ts` | Q2: add `skill_usage` table + index; schema v27 → v28 |

**Net file delta: +2 created, −5 deleted = net −3 files, −996 LOC.**

---

## Data Flow

### Turn-level skill injection (every user message)

```
1. PRE-FILTER (Q1)
   intelligenceRouter.classify(text, "is_action_request") → bool
   false → skip skill routing (conversational messages)
   true  → continue

2. BEHAVIORAL SKILLS (D1)
   skillInjector.getRelevantMatches(text, { mode: "behavioral" })
   IntentRouter filters to skills with conditions[]
   Tier-5 LLM confirms match → inject instructions into text prefix

3. ALWAYS SKILLS (D4)
   registry.getEligible() → filter always === true → top 3 by priority
   pre-pended with synthetic confidence: 1.0, exempt from score threshold

4. DYNAMIC SKILL MATCHING (D1 — repaired IntentRouter)
   Tier 1: usage-weighted scoring   (BM25 stub deleted)
   Tier 2: semantic re-rank via embeddings
   Tier 3: Jaccard dedup
   Tier 4: LLM disambiguation       (IntelligenceRouter cheap-tier, Q1)
            confidence = 1.0 if triggered, 0.0 if not

5. AUTO-EXECUTE GATE (D6)
   topMatch.confidence >= 0.85 && canExecuteStructured(topSkill)
   YES → SkillExecutor.execute() → return result, skip ReAct
   NO  → continue to context injection

6. CONTEXT INJECTION — THE CRITICAL FIX (D2)
   skillInjector.injectIntoContext(text) → skillsContext string
   ContextBuilder.build(..., skillsContext)          ← renamed param
   returns EngineContext { ...base, skillsContext }
   runtime.ts:2563 → "## Skills — AVAILABLE PLAYBOOKS\n{skillsContext}"
   LLM sees skills ✓
```

### invoke_skill tool (D5)

```
LLM emits: invoke_skill { name: "web-research", params: "{...}" }
ctx.skillCallDepth >= 3 → SKILL_DEPTH_LIMIT (configurable via config.skills.maxCallDepth)
ctx.skillCallDepth < 3  → skillInjector.executeByName(name, params)
                        → SkillExecutor.execute(skill, params)
                        → tool_result in ReAct observation ✓
```

### External skill install (D8 + D9 + CI-3)

```
/skill install <term>          ← slash command
"install me a skill for X"    ← NL (CI-3, IntelligenceRouter classifies)

→ dispatchSkillCommand("install", [term], deps)
→ SkillCreationWizard.start(userId, channelAdapter)
→ adapter.ask() — channel-agnostic sequential questions:
    "ClawHub search, GitHub URL, or local path?"
    [ClawHub] → search → show results → confirm
    [GitHub]  → paste URL → install
    [Local]   → paste path → install
→ SKILL.md written to disk → registry.reload() ✓
```

---

## Channel Parity Matrix

All skill surfaces route through `dispatchSkillCommand()`. No channel-specific code.

| Surface | CLI | Telegram | Slack | Voice | Web |
|---|---|---|---|---|---|
| `/skill list` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill show <name>` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill install <src>` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill enable/disable` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill remove <name> yes` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill run <name>` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `/skill metrics <name>` | ✓ | ✓ | ✓ | ✓ | ✓ |
| Install wizard (multi-step) | ✓ | ✓ | ✓ | ✓ | ✓ |
| NL install ("make me a skill for X") | ✓ | ✓ | ✓ | ✓ | ✓ |
| Auto-injection in system prompt | ✓ | ✓ | ✓ | ✓ | ✓ |
| `invoke_skill` tool | ✓ | ✓ | ✓ | ✓ | ✓ |
| Missing-param ask-back | ✓ | ✓ | ✓ | ✓ | ✓ |

**Parity mechanism:** `dispatchSkillCommand()` returns plain text strings. `ChannelAdapterV2.ask()` abstracts multi-step dialogue. `IntelligenceRouter` classifies intent before channel layer.

**Deleted channel-specific code:**
- `wizard.ts` Telegram `inlineKeyboard` state machine → replaced by D9
- `core.ts:1118-1212` slash regex block → replaced by D8
- `MissingParamsError` silent return → replaced by `channelAdapter.ask()`

---

## Error Handling

| Failure | Before | After |
|---|---|---|
| `invoke_skill` — no executor | Silent `NO_EXECUTOR` | D5: always wired; depth ≥ 3 → `SKILL_DEPTH_LIMIT` with LLM-actionable message |
| `invoke_skill` — skill not found | Throws | `toolError("SKILL_NOT_FOUND", ...)` |
| Auto-execution fails | `if(false)` — never ran | Falls back to context XML injection |
| Step timeout | Orphan promise leaks | `AbortController` cancels; `clearTimeout` in `finally` |
| Missing skill param | Silent string, LLM relays maybe | `channelAdapter.ask()` — user prompted directly |
| Always-skills > 3 | All injected, no guard | Top 3 by priority; excess logged as `WARN` |
| Skills XML over token ceiling | No guard | Truncate lowest-confidence matched skills first |
| ClawHub install fails | Throws | Caught in dispatcher, returns user-readable string |

---

## Token Budget

```
Skills section ceiling: ~3400 tokens

Slots:
  [0–2]  always-skills   max 3 × ~300 tokens = 900 tokens  (immune to truncation)
  [3–7]  matched skills  max 5 × ~500 tokens = 2500 tokens (truncated if over)
  ─────────────────────────────────────────────────────────
  total                                        3400 tokens

Truncation: sort matched skills by confidence DESC, pop lowest until within ceiling.
Log: WARN [Skills] truncated N skill(s) to fit token budget

Future config hook: config.context.skillsTokenCeiling (hardcoded constant this round)
```

---

## Test Plan

All new tests in `__tests__/skills-e19.test.ts`.

| # | Group | Proves |
|---|---|---|
| 1 | G2 fix — `ContextBuilder` | `EngineContext.skillsContext` populated when `injectIntoContext()` returns non-empty; `undefined` when empty |
| 2 | D4 — `always:true` pre-pend | Always-skill at index 0 regardless of score; capped at 3 |
| 3 | D1 — `IntentRouter` behavioral mode | `mode:"behavioral"` filters to `conditions[]` skills; score threshold disabled |
| 4 | D5 — `invoke_skill` tool | Executor wired → `tool_result` JSON; no executor → `NO_EXECUTOR`; depth ≥ 3 → `SKILL_DEPTH_LIMIT` |
| 5 | Q4 — `withTimeout` AbortController | Timed-out step: abort signal fired, `clearTimeout` called in `finally`; successful step: no abort |
| 6 | D8 — `SkillManagementRouter` | `list`, `show`, `run` (with/without `user-invocable`), `remove` requires "yes", unknown verb → help text |
| 7 | Q2 — `skill_usage` migration | JSON rows imported; JSON deleted; `recordSelection` increments `selected_count` |
| 8 | D2 end-to-end | Full chain: `getRelevantMatches` → `injectIntoContext` → `ContextBuilder.build` → `EngineContext.skillsContext` non-null |

Existing test files (`skills.test.ts`, `skills-engine.test.ts`, etc.) untouched.

---

## Migration Plan

### File deletion checklist

| File | Pre-delete grep check |
|---|---|
| `src/skills/engine.ts` | Zero imports outside `src/gateway/core.ts` |
| `src/skills/evolver.ts` | Zero imports outside `src/cognition/loop.ts` |
| `src/skills/pattern-miner.ts` | Zero imports outside `src/cognition/loop.ts` and `src/heartbeat/` |
| `src/skills/composer.ts` | Zero imports outside `src/skills/injector.ts` (remove injector wiring first) |
| `src/skills/migrator.ts` | Zero callers; if CLI entry exists, remove it first |

### Data migration (JSON → SQLite)

Schema adds `skill_usage` table at db.ts migration v28:
```sql
CREATE TABLE IF NOT EXISTS skill_usage (
  skill_name        TEXT PRIMARY KEY,
  selected_count    INTEGER NOT NULL DEFAULT 0,
  success_count     INTEGER NOT NULL DEFAULT 0,
  failure_count     INTEGER NOT NULL DEFAULT 0,
  last_used_at      TEXT,
  avg_latency_ms    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_skill_usage_last ON skill_usage(last_used_at DESC);
```

On `SkillTracker` construction: if `workspace/skills-stats.json` exists → import rows → delete JSON → log migration count.

### Variable rename (Q7 — no behavior change)

```
core.ts:1555  let dynamicSkillsContext = ""  →  let memoryContextPrefix = ""
core.ts:1611  dynamicSkillsContext = memoryContextParts.join(...)
              →  memoryContextPrefix = memoryContextParts.join(...)
core.ts:1677  dynamicSkillsContext = await skillInjector.injectIntoContext(text)
              (name stays — this IS the skills XML)
```

### Slash command rewire (D8)

`core.ts:1118-1212` `/^\/skill\s+(\S+)/i` block → deleted.  
Replaced by `dispatchSkillCommand(verb, args, deps)`. All existing slash syntaxes map 1:1 to new verb/args contract — no user-visible change.

---

## Naming Verdict

Keep **"skill"**. Matches the Agent Skills open standard (32 adopters, Dec 2025), Alexa 600M-device vocabulary, and StackOwl's own `SKILL.md` convention. "Playbook" is acceptable as a synonym in help text only.

---

## Creative Ideas

### In scope this element

**CI-1 — `invoke_skill` recursion guard** (wired in D5)  
Depth counter on `ToolContext`. At depth ≥ 3: `SKILL_DEPTH_LIMIT` with message `"Skill call depth limit reached (3). Return your current result instead of calling another skill."` Configurable via `config.skills.maxCallDepth` (default 3).

**CI-3 — NL skill install**  
`IntelligenceRouter.classify(text, "user_wants_to_install_skill") → bool` in pre-routing layer. When true, routes to `dispatchSkillCommand("install", [], deps)` → `SkillCreationWizard`. Triggers on: "make me a skill for X", "install a skill that does X", "can you save this as a skill", "I want a playbook for X". Works on all channels.

**CI-6 — `/skill metrics <name>`**  
Extra verb in `skill-router.ts`. Reads `skill_usage` table. Output:
```
📊 web-research
  Selected: 47×   Success: 43 (91%)   Failures: 4
  Avg latency: 2340ms   Last used: 2026-05-08
```

### Phase B (deferred)

| CI | Reason deferred |
|---|---|
| CI-2 — trigger explainability | Needs score trace store |
| CI-4 — ClawHub trust / signing | Needs publisher infrastructure |
| CI-5 — skill A/B testing | Needs Element 14 trajectory rewards |
| CI-7 — synthesis cost guard | D7 deletes synthesis; Phase B revives with benchmarks |
| CI-8 — `always:true` auto-demotion | Needs weeks of `skill_usage` data |

---

## Blockers / Out-of-Scope

**Requires production data not yet collected:**
- CI-2 explainability (score trace store)
- CI-8 always-skill auto-demotion (needs `skill_usage` history)

**Crosses into Element 18 (Providers):**
- Provider-aware skill cost guards
- Skill execution cost telemetry per provider

**Explicitly not in this round:**
- Multi-tenant skill sharing between users
- Skill versioning / rollback
- `always:true` skills scoped per-owl (global only)
- Voice TTS prosody for skill invocation confirmations
- ClawHub publisher signing / domain allowlist (CI-4)
- Synthesis pipeline revival (CI-7)

---

## Implementation Order

**Task 1 must be D2** — the G2 fix. One rename, one field assignment, unblocks everything. All other tasks build on a working pipeline.

Suggested order:
1. D2 (G2 fix — context-builder rename + field)
2. D1 (delete SkillsEngine, repair IntentRouter tiers, remove behavioral wiring from core)
3. Q1 (replace SKILL_ACTION_KEYWORDS + critic hardcoded regex with IntelligenceRouter)
4. D4 (always-skills pre-pend in getRelevantMatches)
5. D5 (wire invoke_skill tool — executeByName + depth guard)
6. D6 (replace if(false) with confidence gate)
7. D3 (delete formatForSystemPrompt)
8. Q4 (withTimeout AbortController fix)
9. Q2 (skill_usage table + SkillTracker migration — db v28)
10. D7 (delete evolver + pattern-miner + loop/proactive cleanup)
11. Q3 (delete composer + remove injector wiring)
12. D8 (SkillManagementRouter — skill-router.ts)
13. D9 (SkillCreationWizard — skill-creation.ts)
14. CI-3 (NL install intent classifier)
15. CI-6 (metrics verb in skill-router)
16. Q7 (variable rename — memoryContextPrefix)
17. Tests (skills-e19.test.ts — 8 groups)
18. Deletions (engine.ts, migrator.ts, SkillSelector block — after grep confirms zero callers)
19. Progress tracker update + commit

**Baseline:** 4981 tests passing (head `75c507b`). New baseline target: 4981 + new tests from skills-e19.test.ts.
