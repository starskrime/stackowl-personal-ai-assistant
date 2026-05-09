# Element 19 — Skills Engine Audit
**Date:** 2026-05-09  
**Auditors:** 3-agent parallel Explore squad + spot-verification pass  
**Baseline:** Element 17 shipped at `75c507b`, 4981 tests passing  
**Scope:** `src/skills/` (20 files), wiring in `src/gateway/core.ts`, `src/gateway/handlers/context-builder.ts`, `src/engine/runtime.ts`, `src/cognition/loop.ts`, `src/heartbeat/proactive.ts`, `src/tools/invoke-skill.ts`, `src/index.ts`

---

## Verification Status

All 15 gaps were spot-checked against the live tree on 2026-05-09.  
**No divergence detected** — all claims confirmed at the cited file:line locations.

---

## G1 — Three competing skill matchers, only two wired ✅ CONFIRMED

**Three distinct skill-matching paths exist:**

1. `SkillsEngine.evaluate()` at `src/skills/engine.ts:14-89` — batch LLM classifier over **behavioral** skills only (those with `conditions[]`). Wired at `src/gateway/core.ts:1528-1545`. Injects matched skill instructions directly into the user message text.

2. `IntentRouter.route()` via `SkillContextInjector` — 5-tier (BM25 stub + usage + semantic + dedup + LLM). Wired at `src/gateway/core.ts:1630-1695`. Tier 1 BM25 is a no-op stub: returns `score: 0.5` flat for all skills (`src/skills/intent-router.ts:132-138`).

3. `SkillSelector.findRelevant()` at `src/skills/clawhub.ts:188-272` — keyword-scoring, exported but **never imported** outside `src/skills/`. Dead code (~85 LOC). Only reference is the barrel export at `src/skills/index.ts`.

Both wired matchers run **back-to-back on every turn** with no coordination. Same dual-router anti-pattern as Element 17 G2.

---

## G2 — Dynamic skill XML never reaches the system prompt ✅ CONFIRMED (CRITICAL)

**The injection pipeline runs but its output is silently discarded.**

Execution path:
1. `src/gateway/core.ts:1677` — `dynamicSkillsContext = await this.skillInjector.injectIntoContext(text)` — XML built
2. `core.ts:4089,4101` — passed to `buildEngineContext` as the `dynamicSkillsContext` argument
3. `src/gateway/handlers/context-builder.ts:34` — parameter is named `_dynamicSkillsContext` (underscore = ignored by convention)
4. `context-builder.ts:97-100` — returned `EngineContext` never sets `skillsContext` field
5. `src/engine/runtime.ts:62-63` — `skillsContext?: string` field is defined on `EngineContext`
6. `runtime.ts:2564-2574` — `skillsContext` drives the `## Skills — AVAILABLE PLAYBOOKS` section in the system prompt

**Net effect:** IntentRouter pipeline executes (logs fire, tracker increments at `injector.ts:215`, progress notifications fire at `core.ts:1683-1690`), but the resulting XML never enters the LLM context. The LLM is told skills are being used — but never told what they say.

**Note:** `core.ts:1611-1612` also prepends memory blobs into `dynamicSkillsContext` before the skills context is assigned — this variable name is misleading (it contains memory blobs, not just skills). The G7 rename is needed here too.

---

## G3 — `formatForSystemPrompt()` exported, zero callers ✅ CONFIRMED

`SkillContextInjector.formatForSystemPrompt()` at `src/skills/injector.ts:339-368` formats every enabled skill with usage stats for permanent system-prompt inclusion. Grep confirms **zero callers** in `src/` that call it on the *skill* injector.

Note: `opinionInjector.formatForSystemPrompt()` IS called at `core.ts:2026` — but that's the opinion injector, not the skill injector. The skill method is a distinct dead export.

---

## G4 — `always: true` skills are not always-injected ✅ CONFIRMED

`src/skills/types.ts:27` documents `openclaw.always` flag.

`src/skills/registry.ts:73,95` references the flag only inside capability filter methods (`getEligible()` / `getIneligible()`). These methods are used for install-time filtering, not runtime injection.

The runtime injection path (`injector.ts:198-219`) runs `IntentRouter.route()`. IntentRouter returns `[]` when no skills match above threshold (`intent-router.ts:245-250`). **No code path force-includes `always: true` skills** regardless of match score. Type contract unmet.

---

## G5 — `invoke_skill` tool registered with no executor ✅ CONFIRMED

`src/index.ts:768`:
```ts
toolRegistry.register(createInvokeSkillTool());
```
No `skillExecutor` argument passed.

`src/tools/invoke-skill.ts:17-18`: `skillExecutor` parameter defaults to `undefined`.

`invoke-skill.ts:64-70`: Every LLM call to `invoke_skill` short-circuits and returns `toolError("NO_EXECUTOR", ...)`.

The tool appears in the catalog. The LLM believes it can call it. Every invocation silently fails.

---

## G6 — Auto-execution of structured skills disabled by `if (false)` ✅ CONFIRMED

`src/gateway/core.ts:1641`:
```ts
if (false && this.skillInjector!.canExecuteStructured(topSkill)) {
```

Comment at `core.ts:1634-1638` explains: "BM25 scores are unnormalized (can be 5-15+), so keyword-based confidence thresholds cannot reliably distinguish...". However, IntentRouter Tier-5 LLM validator (`intent-router.ts:189-191`) was designed specifically to gate this — its verdict is unused for execution decisions.

`SkillExecutor` at `src/skills/executor.ts:66-465` is fully implemented (topo-sort + retries + `on_failure`). Reachable only via explicit `/skill <name>` slash path (`core.ts:1131-1160`).

---

## G7 — Auto-skill-synthesis pipeline is a dead end ✅ CONFIRMED

**Three competing schedulers, all dead:**

1. **CognitiveLoop** — `src/cognition/loop.ts:578-582` has `case "pattern_mining"` and `case "skill_evolution"` branches. BUT `selectNextAction()` at `loop.ts:541-548` documents these as `// REMOVED (proactive token burners)` — they are excluded from the candidates array, so the switch cases are unreachable.

2. **ProactivePinger** — `src/heartbeat/proactive.ts:223-225` schedules `skill_evolution` daily at 5 AM. Handler at `proactive.ts:355-357` logs `"skill_evolution handled by CognitiveLoop — skipping"` and returns. Job is scheduled, dispatched, dropped.

3. **HeartbeatPlanner / IdleEngine** — `src/heartbeat/planner.ts:265-269` enqueues candidates; `idle-engine.ts:122,135-159` wires `pattern_mining`. But `proactive.ts:19` confirms: `// SkillEvolver and PatternMiner imports removed — proactive learning disabled`.

**Net: the entire synthesis loop is unreachable from the running system.**

---

## G8 — Mined skills bypass the critic ✅ CONFIRMED

`src/skills/pattern-miner.ts:346-425` (`crystallize()`) writes a freshly-generated `SKILL.md` to disk **without ever calling `SkillCritic`**. No quality gate before persistence.

`SkillEvolver.evolveSkill()` at `evolver.ts:142-147` does gate on `needsRewrite` — but that path is dead (G7). `evolution/synthesizer.ts:186-198` gates via critic, but that's the separate Element-14 path.

---

## G9 — Hardcoded keyword/regex arrays (Element-17 ban violations) ✅ CONFIRMED

Six violation sites:

| Site | File:Line | Pattern |
|------|-----------|---------|
| `GENERIC_NAME_PATTERNS` | `src/skills/critic.ts:53-60` | Regex array matching `skill_N`, `synthesized_skill`, etc. |
| `concreteMarkers` | `src/skills/critic.ts:66-76` | Regex array matching numbered lists, tool names, etc. |
| Broad-description substrings | `src/skills/critic.ts:215-216` | `"anything"` / `"all tasks"` substring checks |
| Failure-detection substrings | `src/skills/pattern-miner.ts:235-238` | `"i couldn't"`, `"i was unable"`, `"failed to"`, `"EXHAUSTED"` |
| `TOOL_ALIASES` | `src/skills/executor.ts:53-64` | Hardcoded class-name → registry-name map |
| `SKILL_ACTION_KEYWORDS` | `src/gateway/core.ts:1622-1629` | 30+ verb regex pre-filter for conversational message detection |

Element 17 banned this pattern. All six sites must be replaced with `IntelligenceRouter` cheap-tier or LLM classification.

---

## G10 — Skills bypass platform primitives ✅ CONFIRMED

- **`IntelligenceRouter`** — zero imports in any `src/skills/*.ts` file. `intent-router.ts:236` and `executor.ts:342` call `provider.chat` directly.
- **`GatewayEventBus`** — zero imports in `src/skills/`. `executor.ts` uses per-call `ProgressCallback` closure (`executor.ts:38-42`) instead of bus events.
- **`ContextPipeline`** — zero imports in `src/skills/`. `injector.ts:225-251` builds its own `<context_skills>` XML outside the pipeline.
- **`OwlBrain`** — zero imports in `src/skills/`. `engine.ts:19` takes raw `OwlInstance` but never calls the brain for routing decisions.
- **`ChannelAdapterV2.ask()`** — zero imports in `src/skills/`. `wizard.ts:53-67` implements its own multi-step state machine with Telegram-specific inline keyboards. `injector.ts:131-144` (`MissingParamsError` handler) emits a status string and does not ask the user.

---

## G11 — Parallel storage (Element-17 G1 redux) ✅ CONFIRMED

Three storage layers:

1. **Markdown files** — `SkillsLoader.load()` at `src/skills/loader.ts:36-82` — canonical source of truth
2. **JSON stats file** — `src/skills/tracker.ts:27` writes to `workspace/skills-stats.json`
3. **SQLite `skill_templates` table** — `src/memory/db.ts:3616` — **orthogonal**, used only by `src/intelligence/skill-template-layer.ts:59,87` for NL-template storage (not `src/skills/`)

Evolution also writes back to markdown files (`evolver.ts:271-277`). Pattern miner writes new markdown files (`pattern-miner.ts:412-422`). No transactional coherence between any layers.

---

## G12 — Composer wired but no authored composite skills ✅ CONFIRMED

`SkillComposer` is constructed in `SkillContextInjector` (`injector.ts:71`) and `composer.resolve(skill)` is called per injection (`injector.ts:236`). Algorithm is solid (Kahn's topo-sort + cycle detection at `composer.ts:289-326`).

Verification: `grep -r "depends:\|chains:\|isComposite" src/skills/defaults/` → **0 matches**. The multi-stage composition branch at `composer.ts:88-157` is unreachable in the shipped skill catalog. `SkillComposition` parsing is absent from `parser.ts` entirely — the cast at `composer.ts:213` always falls through to the openclaw fallback.

---

## G13 — `withTimeout` leaks orphan promises ✅ CONFIRMED

`src/skills/executor.ts:443-464`:
```ts
return Promise.race([
  fn().then((result) => {
    clearTimeout(timer);   // ← only cleared on SUCCESS path
    return result;
  }),
  new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(...), timeoutMs);
  }),
]);
```

On timeout reject, the underlying `fn()` promise continues running unsupervised (no AbortController). Tool calls execute after timeout with no way to cancel them. Slow resource leak under concurrent load.

---

## G14 — Slash command `/skill` ignores `user-invocable` ✅ CONFIRMED

`src/skills/types.ts:38` documents `user-invocable: true`. `src/skills/parser.ts:99-100` parses it into metadata. Grep for `user-invocable` in `src/gateway/` and `src/skills/` → **found only in `parser.ts` and `types.ts`**. The slash dispatcher regex at `core.ts:1118-1212` accepts any registered skill name. The gating bit is decorative.

---

## G15 — Test gaps ✅ CONFIRMED

**Zero unit tests** for the following production paths:
- `IntentRouter` 5-tier flow (`intent-router.ts`)
- `SkillContextInjector.injectIntoContext()` — the broken G2 pipeline
- `SkillExecutor.execute()` — DAG runner
- `SkillComposer.resolve()` — Kahn's algorithm
- `SkillEvolver.evolveAll()` — Self-Refine loop
- `PatternMiner.minePatterns()` — sequence detection

**Existing test files (coverage skewed to install + critic heuristic):**
- `__tests__/skills.test.ts`
- `__tests__/skills-engine.test.ts`
- `__tests__/skills-migrator.test.ts`
- `__tests__/skills-installer.test.ts`
- `__tests__/skill-install.test.ts`
- `__tests__/skill-critic.test.ts`

**Zero end-to-end tests** for the lifecycle: user message → match → inject → ReAct sees skill → success.

---

## File Inventory

| File | LOC | Role | Gap |
|------|-----|------|-----|
| `src/skills/engine.ts` | 89 | Behavioral-skill batch classifier | G1 duplicate |
| `src/skills/loader.ts` | 188 | SKILL.md loader + chokidar watcher | — |
| `src/skills/registry.ts` | 191 | In-memory skill map, filter/lookup | G4 |
| `src/skills/parser.ts` | 326 | YAML-frontmatter → Skill object | G12 |
| `src/skills/types.ts` | 216 | Type definitions | G4, G14 |
| `src/skills/injector.ts` | 369 | Composes router + extractor + executor | G2, G3, G10 |
| `src/skills/executor.ts` | 465 | DAG executor for structured skills | G9, G13 |
| `src/skills/evolver.ts` | 279 | Self-Refine evolution loop | G7 |
| `src/skills/pattern-miner.ts` | 426 | Mines repeat tool sequences | G7, G8, G9 |
| `src/skills/critic.ts` | 243 | 3-axis quality scorer | G9 |
| `src/skills/composer.ts` | 327 | Skill dependency DAG (Kahn's) | G12 |
| `src/skills/intent-router.ts` | 471 | 5-tier matcher | G1, G10 |
| `src/skills/param-extractor.ts` | 172 | LLM-based param extraction | G10 |
| `src/skills/tracker.ts` | 203 | Usage stats → JSON file | G11 |
| `src/skills/wizard.ts` | 330 | Install wizard | G10 |
| `src/skills/migrator.ts` | 60 | One-shot INSTINCT.md → SKILL.md migrator | — |
| `src/skills/installer.ts` | 104 | GitHub raw / local-fs installer | — |
| `src/skills/clawhub.ts` | 272 | ClawHub HTTP client + dead SkillSelector | G1 |
| `src/skills/config-context.ts` | 209 | Platform snapshot for skill-gen prompts | — |
| `src/skills/index.ts` | 25 | Barrel export (re-exports dead SkillSelector) | G1 |

**Cross-cutting wiring sites:**

| Location | Gap |
|----------|-----|
| `src/gateway/core.ts:1528-1545` | G1 (behavioral matcher) |
| `src/gateway/core.ts:1622-1629` | G9 (SKILL_ACTION_KEYWORDS) |
| `src/gateway/core.ts:1630-1695` | G1, G2 (IntentRouter wired back-to-back) |
| `src/gateway/core.ts:1641` | G6 (`if (false &&...)`) |
| `src/gateway/handlers/context-builder.ts:34, 97-100` | **G2 CRITICAL** |
| `src/engine/runtime.ts:62-63, 2564-2574` | G2 (skillsContext expected but never set) |
| `src/cognition/loop.ts:541-548, 578-582` | G7 (cases exist but unreachable) |
| `src/heartbeat/proactive.ts:19, 223-225, 355-357` | G7 (punts to CognitiveLoop) |
| `src/heartbeat/planner.ts:265-269` | G7 |
| `src/heartbeat/idle-engine.ts:122, 135-159` | G7 |
| `src/tools/invoke-skill.ts:17, 64-70` | G5 (NO_EXECUTOR) |
| `src/index.ts:768` | G5 (no executor arg) |
| `src/intelligence/skill-template-layer.ts:59, 87` | G11 (orthogonal DB table) |
| `src/memory/db.ts:3616-3626` | G11 |

---

## Deletion Candidates (~560 LOC — enables net-negative file delta)

| File / Block | LOC | Reason |
|---|---|---|
| `src/skills/engine.ts` | 89 | G1: duplicate of IntentRouter behavioral path |
| `SkillSelector` block in `src/skills/clawhub.ts:188-272` | ~85 | G1: dead keyword-scoring code |
| `src/skills/migrator.ts` | 60 | One-shot migration, no version guard, long shipped |
| `src/skills/composer.ts` (if Q3 = delete) | 327 | G12: no authored composite skills; delete and inline chaining |
| `src/skills/evolver.ts` (if D7 = delete) | 279 | G7: dead synthesis loop |
| `src/skills/pattern-miner.ts` (if D7 = delete) | 426 | G7: dead synthesis loop |

**If D7 = delete and Q3 = delete:** ~1,266 LOC deleted. New files budget: 4–6 `src/` files, net delta comfortably negative.

---

## Priority Order for Fixes

1. **G2 (CRITICAL)** — Wire `_dynamicSkillsContext` → `skillsContext` in `context-builder.ts`. One surgical fix that makes the entire IntentRouter pipeline visible to the LLM. Every other fix is invisible until G2 is patched.
2. **G1** — Delete `SkillsEngine` (89 LOC), wire behavioral skills through `IntentRouter` with `trigger:"context"` filter.
3. **G9** — Replace all hardcoded regex/keyword arrays with `IntelligenceRouter` cheap-tier.
4. **G5** — Wire `SkillExecutor` into `invoke_skill` tool at `index.ts:768` OR delete the tool.
5. **G4** — Force-include `always: true` skills in injection regardless of IntentRouter score.
6. **G3** — Wire `formatForSystemPrompt()` into startup OR delete it.
7. **D8** — `SkillManagementRouter` (mirrors Element 17 D7 `OwlManagementRouter`).
8. **D9** — Channel-agnostic wizard using `ChannelAdapterV2.ask()`.
9. **G7/G8/D7** — Synthesis verdict: complete with critic gate OR delete evolver + pattern-miner.
10. **G13** — AbortController for `withTimeout`.
11. **G6** — Replace `if (false &&...)` with real confidence gate.
12. **G15** — New tests for injection pipeline, DAG executor, IntentRouter tiers.
