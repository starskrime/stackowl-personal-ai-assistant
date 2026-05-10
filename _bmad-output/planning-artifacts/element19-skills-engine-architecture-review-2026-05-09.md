---
element: 19
title: "Skills Engine (Match, Inject, Synthesize) — Architecture Review"
author: Winston (System Architect)
date: 2026-05-09
status: complete — awaiting Boss approval before Phase 4
inputs:
  - _bmad-output/planning-artifacts/element19-skills-engine-audit-2026-05-09.md
  - _bmad-output/planning-artifacts/research/market-element19-skills-engine-research-2026-05-09.md
---

# Element 19 — Skills Engine Architecture Review

> **9 locked decisions at top, then 7 resolved questions, file matrix, risk tieback, deferred ideas.**  
> All code claims verified firsthand at file:line. HALT after delivery — wait for Boss approval before Phase 4.

---

## 9 Locked Decisions

### D1 — Matcher Consolidation (G1): Delete `SkillsEngine`, repair `IntentRouter` Tier 1

**LOCKED:** Delete `src/skills/engine.ts` (89 LOC). The `SkillsEngine.evaluate()` path at `engine.ts:14-89` is a duplicate LLM batch classifier that calls `provider.chat` directly (G10 violation) with no IntelligenceRouter routing. It runs back-to-back with `IntentRouter` on every user turn via `core.ts:1528-1545` and `core.ts:1631`, creating two separate LLM calls for the same job. Behavioral skills (those with `conditions[]`) are not a special case requiring a separate class — `IntentRouter` already has a Tier-5 LLM disambiguation step (`intent-router.ts:189-191`) that can handle them. Extend `IntentRouter.route()` to accept a `mode` option: when `mode: "behavioral"`, filter to skills with `conditions[]`, disable score-threshold filtering (behavioral skills that fire should fire unconditionally), and inject their `instructions` as a system-prompt constraint (same effect as `core.ts:1543`). Tier 1 BM25 stub (`intent-router.ts:132-138` returns flat `score: 0.5` for all skills) is deleted — rename the tier numbering to start at Tier 1 = usage-weighted (previously Tier 2). The gateway rewire at `core.ts:1528-1545` is removed entirely; behavioral skill evaluation flows through the single `skillInjector.getRelevantMatches(text, { mode: "behavioral" })` call at `core.ts:1631`. Dead `SkillSelector` (~85 LOC at `clawhub.ts:188-272`) is deleted from the file (not a separate file deletion).

### D2 — Repair the Injection Pipeline (G2 — CRITICAL): Wire `skillsContext` through `ContextBuilder`

**LOCKED:** The G2 break is a single underscore prefix at `context-builder.ts:34` (`_dynamicSkillsContext: string = ""` → `skillsContext: string = ""`). The returned `EngineContext` at `context-builder.ts:97-100` must add `skillsContext: skillsContext || undefined`. The runtime at `runtime.ts:2563-2574` already consumes this field and emits `## Skills — AVAILABLE PLAYBOOKS` — no runtime changes needed. The caller at `core.ts:1677` builds the XML via `skillInjector.injectIntoContext(text)` and assigns it to `dynamicSkillsContext`; that value is passed to `buildEngineContext()` at `core.ts:4089, 4101` and reaches `ContextBuilder.build()`. Token budget: max 5 matched skills × ~500 tokens each = 2500 tokens ceiling; truncate lowest-scoring skills first when over budget; `always: true` skills (D4) are pre-pended and protected from truncation. Priority ordering: always-skills first (exempt from score filter), then matched skills by descending confidence score. This is the highest-priority fix in the entire element — without it every other improvement in this element is invisible to the LLM.

### D3 — `formatForSystemPrompt()`: Delete (G3)

**LOCKED:** Delete `injector.ts:339-368`. The D2 fix already routes per-turn matched skills into the system prompt via `skillsContext`. A permanent "always-on" full catalog (`formatForSystemPrompt()` lists every enabled skill at every turn) would cost ~1000–3000 tokens per turn regardless of relevance — a fixed overhead the platform cannot justify for a feature with zero callers since it was written. The `always: true` path (D4) is the correct mechanism for skills that must always be visible. Grep confirms zero callers in `src/`. Verdict: dead code, delete.

### D4 — Honor `always: true` Flag (G4): Pre-pend before score filter

**LOCKED:** In `injector.ts:198-219` `getRelevantMatches()`, before calling `router.route()`, collect all always-skills via `registry.getEligible()` filtered by `skill.metadata.openclaw?.always === true` (both `registry.ts:73` and `registry.ts:95` already have this logic — reuse it). Pre-pend these skills to the result with a synthetic `confidence: 1.0` score so they survive any downstream score threshold. Token cost cap: maximum 3 always-skills injected per turn; if more are marked `always`, sort by `priority` (falling back to alphabetical) and take top 3; emit a warning log for the excess. Override hierarchy: always-skills occupy slots 0–2 of the injection window; matched skills from `router.route()` fill the remaining budget (up to 5 total per D2's budget rule).

### D5 — Wire `invoke_skill` Tool (G5): Add `executeByName` to injector, pass adapter to tool factory

**LOCKED:** Wire the executor. Add `executeByName(name: string, params: Record<string, unknown>): Promise<string>` to `SkillContextInjector` — it looks up the skill by name in the registry and dispatches to `executeStructuredSkill()` (already implemented at `injector.ts`). Pass a thin adapter object (implementing the `SkillExecutor` interface at `invoke-skill.ts:8-10`) to `createInvokeSkillTool()` at `index.ts:768`. Result-synthesis contract: `invoke_skill` returns a `tool_result` JSON string (already handled by `toolSuccess()` at `invoke-skill.ts:74`) which lands in the ReAct loop's observation step — the LLM sees the skill output as a regular tool result and continues reasoning from it. Recursion guard: add a `skillCallDepth` counter to `ToolContext`; throw `SKILL_DEPTH_LIMIT` when depth ≥ 3 (configurable). The LLM has been silently lied to about this tool since it was registered — fixing it is non-negotiable.

### D6 — Auto-Execute Structured Skills (G6): Replace `if (false)` with real confidence gate

**LOCKED:** Replace `if (false && this.skillInjector!.canExecuteStructured(topSkill))` at `core.ts:1641` with a real gate: `if (topMatch.confidence >= 0.85 && this.skillInjector!.canExecuteStructured(topSkill))`. The `topMatch` object returned from `getRelevantMatches()` already carries a `score` field from `IntentRouter.route()` (see `intent-router.ts:140-150`); rename to `confidence` and normalize to [0, 1] during the Tier-5 LLM disambiguation step which already returns a boolean `triggered` verdict (`intent-router.ts:189-191`). When Tier-5 LLM runs and returns `triggered: true`, set `confidence = 1.0`; when it returns `triggered: false`, set `confidence = 0.0`; when Tier-5 is skipped (no provider or single match), use normalized BM25 score capped at 0.8. Threshold: 0.85 (requires Tier-5 confirmation in practice). Fallback when below threshold: continue to context injection (existing XML path at `core.ts:1676-1690`). No replan support in this round — if `SkillExecutor` fails, fall back to injecting the skill as XML context and letting the LLM proceed normally.

### D7 — Synthesis Loop: DELETE (G7, G8)

**LOCKED:** Delete the synthesis pipeline entirely. Evidence: (1) SkillsBench 2026 shows self-generated skills perform −1.3pp vs. baseline — synthesis actively degrades quality; (2) the pipeline is already dead — `loop.ts:541-548` explicitly removes pattern_mining and skill_evolution from candidates, `proactive.ts:355-357` logs "handled by CognitiveLoop — skipping" and returns, `proactive.ts:19` confirms imports were removed; (3) three competing schedulers (CognitiveLoop, ProactivePinger, HeartbeatPlanner) with no coordination; (4) G8 confirms mined skills bypass SkillCritic entirely. Delete: `src/skills/evolver.ts` (~279 LOC), `src/skills/pattern-miner.ts` (~426 LOC). Remove: `loop.ts:180-184` `@ts-expect-error` vars (`lastPatternMineTime`, `lastSkillEvolveTime`), `loop.ts:660-668` `executeSkillEvolution()` method (plus any unreachable `executePatternMining()` method), `proactive.ts:223-225` `skill_evolution` job scheduling. The `crystallize()` path that bypassed `SkillCritic` at `pattern-miner.ts:346-425` is deleted with the file. Synthesis can be reconsidered in Phase B only after a controlled benchmark on this dataset with critic-gated quality. Total deletion: ~705 LOC.

### D8 — `SkillManagementRouter` Gateway Primitive: New file mirroring `OwlManagementRouter`

**LOCKED:** Create `src/gateway/commands/skill-router.ts` mirroring `src/gateway/commands/owl-router.ts` (Element 17 D7 pattern). Single export: `dispatchSkillCommand(verb: string, args: string[], deps: SkillRouterDeps): Promise<string>` where `verb ∈ {install, list, show, enable, disable, remove, run}`. `SkillRouterDeps` carries `registry: SkillsRegistry`, `installer: SkillInstaller`, `wizard: SkillCreationWizard` (D9), `userId: string`, `channelAdapter: unknown`. CLI, Telegram, Slack, Voice, Web all call the same dispatcher — channel parity is structural, not convention. Delete the scattered slash-command handling at `core.ts:1118-1212` (regex `/^\/skill\s+(\S+)/i` block) and route through `dispatchSkillCommand`. Args contract per verb: `install {url|search-term}` (launches D9 wizard), `list [--all]`, `show {name}` (name/description/status/stats), `enable {name}`, `disable {name}`, `remove {name} yes` (requires "yes" confirmation), `run {name} [json-params]` (honors `user-invocable` flag, Q5). Help text from a `HELP` constant at top of file (same pattern as `owl-router.ts:27-34`).

### D9 — Channel-Agnostic Skill-Creation Wizard: New file using `ChannelAdapterV2.ask()`

**LOCKED:** Create `src/gateway/wizards/skill-creation.ts` mirroring `src/gateway/wizards/owl-creation.ts` (Element 17 D8 pattern). `SkillCreationWizard` class with `sessions = new Map<string, WizardSession>()` (per-userId, same as `owl-creation.ts:31`), `start(userId, channelAdapter)`, `isActive(userId)`, `cancel(userId)`. Internal `runWizard(userId, adapter)` uses sequential `adapter.ask()` calls instead of inline keyboard state machine — works identically across CLI, Telegram, Slack, Voice. Replace `wizard.ts`'s Telegram-flavored `inlineKeyboard` WizardResponse (not a separate file yet — it's a state machine inside `wizard.ts` that uses `inlineKeyboard` field and text choices specific to Telegram). The existing `SkillInstallWizard` in `wizard.ts` handles the source-selection + install flow; `SkillCreationWizard` (D9) is for NL-guided SKILL.md authoring. Also fix the `MissingParamsError` handler at `injector.ts:131-144`: instead of returning a status string and hoping the LLM relays the question, call `channelAdapter.ask(userId, { text: \`Missing parameter: \${missingParam}\` })` directly. This requires threading `channelAdapter` and `userId` into `injectIntoContext()` — add them as optional parameters.

---

## 7 Resolved Open Questions

### Q1 — Hardcoded Keyword Purge (G9): Strategy

Six violation sites, two categories:

**Category A — Intent classification (replace with IntelligenceRouter):**
- `core.ts:1622-1629` `SKILL_ACTION_KEYWORDS` regex: Replace with `intelligenceRouter.classify(text, "is_action_request") → boolean` (cheap-tier, single-sentence prompt). Falls back to `text.trim().length >= 15` if router unavailable.
- `pattern-miner.ts:235-238` failure detection substrings (`"i couldn't"`, etc.): Replace with `intelligenceRouter.classify(response, "response_indicates_failure") → boolean` (cheap-tier).
- `critic.ts:215-216` `"anything"` / `"all tasks"` substring match: Replace with `intelligenceRouter.classify(description, "skill_description_is_too_broad") → boolean` (cheap-tier).

**Category B — Structural heuristics (TOOL_ALIASES exempted):**
- `executor.ts:53-64` `TOOL_ALIASES` map: This is a **translation table** (class-name → registry-name), not intent classification. The no-hardcoded rule targets classification arrays. `TOOL_ALIASES` is exempt — it's deterministic schema mapping for SKILL.md authoring convenience, analogous to a constant lookup table. Keep.
- `critic.ts:53-60` `GENERIC_NAME_PATTERNS` regex array: The primary critique path is `critiqueWithLLM()` at `critic.ts:88-103`; this regex array only fires in the heuristic fallback (LLM failed twice). For a last-resort fallback, the regex is pragmatic — but to comply strictly, replace with `intelligenceRouter.classify(name, "is_generic_skill_name") → boolean` (cheap-tier, 0 tokens from main model).
- `critic.ts:66-76` `concreteMarkers` regex: Same — last-resort heuristic. Replace with `intelligenceRouter.classify(instructions, "has_concrete_instructions") → boolean`.

**Lock:** Replace 5 of 6 sites with IntelligenceRouter cheap-tier. TOOL_ALIASES exempted as a translation constant.

### Q2 — Storage Unification (G11): Migrate stats from JSON to SQLite

`tracker.ts:27` stores stats at `workspace/skills-stats.json` (flat file, no transactional safety, fails silently on concurrent writes). `db.ts:3616` has `skill_templates` for NL templates — separate concern, untouched.

**Lock:** Add new `skill_usage` table to the schema migration in `db.ts`:
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

Migration: at `SkillTracker` construction, if `skills-stats.json` exists, import rows into `skill_usage` via INSERT OR REPLACE, then delete the JSON file. `SkillTracker` methods (`recordSelection`, `recordSuccess`, `recordFailure`) become synchronous SQLite writes (no debounce needed — SQLite WAL handles concurrent access). `skill_templates` table remains as-is — it's owned by `skill-template-layer.ts` and has no coupling to `src/skills/`.

### Q3 — Composer (G12): Delete

**LOCKED: DELETE `src/skills/composer.ts` (327 LOC).** The multi-stage plan builder at `composer.ts:88-157` is unreachable because `parser.ts` never parses a `composition:` field into the `Skill` object — the `extended.composition` cast at `composer.ts:213` always falls through to the `openclaw.depends` metadata path. The `openclaw.depends` / `openclaw.chains` metadata path itself is unreachable because grep of `src/skills/defaults/` returns zero SKILL.md files with `depends:` or `chains:` fields. `SkillExecutor.buildWaves()` already handles step-level DAG for structured skills (topological sort + cycle detection at `executor.ts:361-408`). Remove `injector.ts:71` (`composer = new SkillComposer(registry)`) and all `composer.resolve(skill)` calls in the injection path. If skill chaining becomes a requirement in Phase B, it can be added as a trivial `openclaw.chains: [name, ...]` pre-pend in the injection XML — no 327-LOC Kahn's algorithm needed.

### Q4 — `withTimeout` Leak (G13): Replace with AbortController

`executor.ts:443-464` current: `Promise.race([fn().then(r => { clearTimeout(timer); return r; }), timeout promise])`. `clearTimeout` is only called in the `.then()` success branch — on timeout rejection, `fn()` continues running unsupervised.

**LOCKED — fix shape:**
```typescript
private async withTimeout<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  timeoutMs: number,
  stepId: string,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new Error(`Step "${stepId}" timed out after ${timeoutMs}ms`)), timeoutMs);
  try {
    return await fn(controller.signal);
  } catch (err) {
    if (controller.signal.aborted) throw controller.signal.reason;
    throw err;
  } finally {
    clearTimeout(timer);
  }
}
```

Pass `signal` down to `executeToolStep` → `toolRegistry.execute()` as an optional arg on `ToolContext`. Tool implementations that support cancellation check `ctx.signal?.aborted`. Tools that ignore `signal` still benefit from the `clearTimeout` in `finally` — no more orphan timeout handles.

### Q5 — `user-invocable` Flag (G14): Honor it

`types.ts:38` declares the flag; `parser.ts:99-100` parses it. Zero readers confirmed.

**LOCKED:** The new `dispatchSkillCommand("run", ...)` in `SkillManagementRouter` (D8) checks `skill.metadata.openclaw?.userInvocable !== false` before allowing user-initiated slash invocations. Skills without `user-invocable: true` can only be triggered by: (a) auto-execution via D6 confidence gate, or (b) the `invoke_skill` tool (LLM-driven, not user-driven). The existing `/skill <name>` slash handling at `core.ts:1118-1212` (which will be deleted and replaced by D8) also checks the flag. Default when flag is absent: `userInvocable: true` (backwards compatible — all existing skills without the field remain user-invocable).

### Q6 — Test Gaps (G15): Minimum tests required to merge

Minimum new test coverage required:
1. `context-builder.ts` G2 fix: `EngineContext.skillsContext` is set when `injectIntoContext()` returns non-empty string.
2. `injector.ts` `getRelevantMatches()` with `always: true` skill: always-skill appears at index 0 regardless of score.
3. `IntentRouter.route()`: behavioral mode filter, Tier-1-removed (usage-weighted is now Tier 1), Tier-3 semantic re-rank, Tier-5 LLM disambiguation toggleable by mock provider.
4. `invoke_skill` tool: executor wired → returns `tool_result` JSON; executor absent → returns `NO_EXECUTOR`.
5. `withTimeout` fix: timed-out step propagates abort signal; `clearTimeout` is called in finally (verify via fake timers).
6. `SkillManagementRouter.dispatchSkillCommand()`: `list`, `show`, `run` (with and without `user-invocable` flag), `remove` (requires "yes" confirmation).
7. `skill_usage` table migration: rows from JSON file imported, JSON file deleted, stats incremented via `recordSelection`.
8. `SkillContextInjector.injectIntoContext()` end-to-end: XML is built from IntentRouter matches AND returned (not discarded).

These 8 test groups are the merge gate. Existing 6 test files (`skills.test.ts`, `skills-engine.test.ts`, etc.) cover install/critic paths only — they are untouched.

### Q7 — `dynamicSkillsContext` Variable Name (core.ts:1611-1612): Split and rename

At `core.ts:1555-1612`, the variable `dynamicSkillsContext` is first initialized to `""` then filled with memory module fragments (PriorContextRetriever, PreferenceRecognizer, CrossSessionStore outputs). At `core.ts:1677`, `skillInjector.injectIntoContext(text)` assigns the actual skill XML to the same variable — overwriting memory content only if skills match, otherwise leaving the memory prefix. The variable conflates two concerns.

**LOCKED:** Rename the memory accumulation variable to `memoryContextPrefix`. Keep `dynamicSkillsContext` for the skill XML only. At `core.ts:1677`, if skills matched: `dynamicSkillsContext = await this.skillInjector.injectIntoContext(text)`. At `buildEngineContext()` call site: pass `memoryContextPrefix` and `dynamicSkillsContext` as separate arguments. `ContextBuilder.build()` receives `skillsContext: string` (D2 fix) and the memory prefix is already handled by `ContextPipeline` (the pipeline's `memoryContext` output). This removes the confusing conflation and makes the variable name truthful.

---

## File-by-File Change Matrix

| File | Action | Size Impact | Rationale |
|---|---|---|---|
| `src/skills/engine.ts` | **DELETE** | −89 LOC | D1: duplicate matcher |
| `src/skills/evolver.ts` | **DELETE** | −279 LOC | D7: synthesis deleted |
| `src/skills/pattern-miner.ts` | **DELETE** | −426 LOC | D7: synthesis deleted |
| `src/skills/composer.ts` | **DELETE** | −327 LOC | Q3: unreachable multi-stage branch |
| `src/skills/migrator.ts` | **DELETE** (post-verify) | −60 LOC | One-shot migrator with no version guard; delete after confirming migration ran |
| `src/gateway/commands/skill-router.ts` | **CREATE** | +~120 LOC | D8: SkillManagementRouter |
| `src/gateway/wizards/skill-creation.ts` | **CREATE** | +~150 LOC | D9: channel-agnostic creation wizard |
| `src/gateway/handlers/context-builder.ts` | **MODIFY** | ~+3 LOC | D2: rename `_dynamicSkillsContext`, set `skillsContext` field |
| `src/gateway/core.ts` | **MODIFY** | −80 LOC net | D1 (remove engine wiring), D6 (replace `if(false)`), D7 (remove scheduler), D8 (route to SkillManagementRouter), Q1 (SKILL_ACTION_KEYWORDS → classifier), Q7 (rename variable) |
| `src/skills/injector.ts` | **MODIFY** | −40 LOC net | D3 (delete formatForSystemPrompt), D4 (always pre-pend), D5 (add executeByName), D9 (MissingParamsError → ask), Q3 (remove composer wiring) |
| `src/skills/intent-router.ts` | **MODIFY** | −20 LOC net | D1 (remove BM25 stub tier, add behavioral mode option) |
| `src/skills/executor.ts` | **MODIFY** | ~0 LOC | Q4 (withTimeout AbortController fix) |
| `src/skills/tracker.ts` | **MODIFY** | −50 LOC net | Q2 (SQLite adapter, remove JSON write path) |
| `src/skills/critic.ts` | **MODIFY** | −30 LOC net | Q1 (replace 4 regex/substring sites with IntelligenceRouter cheap-tier) |
| `src/skills/clawhub.ts` | **MODIFY** | −85 LOC | D1: delete dead SkillSelector block (lines 185-272) |
| `src/tools/invoke-skill.ts` | **MODIFY** | ~0 LOC | D5: no change needed (executor passed from call site) |
| `src/index.ts` | **MODIFY** | ~+3 LOC | D5: pass skillInjectorAdapter to createInvokeSkillTool |
| `src/cognition/loop.ts` | **MODIFY** | −20 LOC | D7: remove @ts-expect-error vars, executeSkillEvolution/executePatternMining methods |
| `src/heartbeat/proactive.ts` | **MODIFY** | −5 LOC | D7: remove skill_evolution scheduling at lines 223-225 |
| `src/memory/db.ts` | **MODIFY** | +15 LOC | Q2: add skill_usage table + index to schema migration |

**File budget check:** 2 new files in `src/`. Net delta: +270 LOC created − (89+279+426+327+60+85) = +270 − 1266 = **−996 LOC net**. Comfortably within max 4–6 new files and net delta ≤ 0.

---

## Risk Tieback Table

| Risk | Phase 2 Description | Architecture Mitigation | Status |
|---|---|---|---|
| R1 | Single-tier routing accuracy — BM25 stub returns flat 0.5, false positive rate unknown | D1: Delete BM25 stub tier; Tier-5 LLM disambiguation required for auto-execution (D6 confidence gate = 0.85, equivalent to requiring Tier-5 confirmation) | **MITIGATED** |
| R2 | DAG runner edge cases — timeout leak, orphan promises, partial failure | Q4: AbortController fix; `clearTimeout` in `finally`; `signal` passed to tool execution | **MITIGATED** |
| R3 | Self-generated skill quality — SkillsBench −1.3pp | D7: Delete synthesis pipeline entirely. No self-generated skills until Phase B benchmarks. | **MITIGATED** |
| R4 | Skill critique accuracy — hardcoded regex heuristics miss edge cases | Q1: Replace 5 of 6 heuristic sites with IntelligenceRouter cheap-tier classification; `critiqueWithLLM()` remains the primary path | **MITIGATED** |
| R5 | Channel fragmentation — wizard is Telegram-flavored, other channels degraded | D8: SkillManagementRouter dispatcher (identical output on all channels); D9: ChannelAdapterV2.ask() wizard | **MITIGATED** |
| R6 | `invoke_skill` recursion — LLM calls a skill that calls another skill ad infinitum | D5: Depth counter ≥ 3 throws SKILL_DEPTH_LIMIT; full CI-1 explainability deferred to Phase 4 brainstorming | **PARTIALLY MITIGATED** — depth guard wired; full recursion tracing deferred to Phase B |
| R7 | `always: true` skills token bloat — many always-skills saturate system prompt | D4: Max 3 always-skills per turn; sorted by priority; excess logged as warning | **MITIGATED** |
| R8 | SkillJect attack — crafted SKILL.md file as jailbreak vector; ClawHub 13.4% critical severity | Q5: `user-invocable` flag honored (reduces attack surface for automated injection); full publisher signing / allowlist is Phase B (CI-4) | **PARTIALLY MITIGATED** — flag gating wired; remote registry trust is Phase B |
| R9 | DDG/search SERP shape changes (web tools) | Out of scope — Element 16, not Element 19 | **DEFERRED (out of scope)** |
| R10 | Stats persistence — `skills-stats.json` non-transactional, no telemetry for routing | Q2: SQLite WAL `skill_usage` table; feeds back into Tier-2 usage-weighted routing in `IntentRouter` | **MITIGATED** |
| R11 | Naming confusion — "skill" vs "playbook" vs "action" | Keep "skill" — matches Agent Skills open standard (32 adopters, Dec 2025), Alexa 600M device recognition, StackOwl's own SKILL.md convention. No rename needed. | **NON-ISSUE** |

---

## Creative Ideas — Deferred to Phase 4 Brainstorming

The following ideas emerged from the Phase 2 research and code review. They are sound but scope-exceeding for this element. Phase 4 brainstorming (CI-1 through CI-8) should prioritize them.

**CI-1 — `invoke_skill` recursion explainability.** D5 wires a depth limit (≥ 3 throws). Phase 4 should design the `/why` explainability surface (what triggered the skill, what depth it ran at, what the output was).

**CI-2 — Skill trigger explainability.** When a skill fires, expose the IntentRouter score breakdown to the user. `/why` command shows tier scores for the last turn. Token cost: ~50 tokens (just the score object, already computed).

**CI-3 — NL skill creation ("make me a skill for X").** Phase 4 should design the gateway intent routing for NL skill creation requests, tying into Element 17 D9 NL-invocation pattern. The `SkillCreationWizard` (D9) is the execution target; Phase 4 designs the intent classifier that routes to it.

**CI-4 — ClawHub remote registry trust.** Signed SKILL.md + domain allowlist. Deferred to Phase B — requires publisher infrastructure beyond this round.

**CI-5 — Skill A/B testing.** Two skills match → run both via Parliament-lite, pick winner by reward signal. Deferred to Phase B — requires trajectory reward infrastructure (Element 14).

**CI-6 — Skill quality dashboard.** `/skill metrics <name>` shows EWMA success rate, last 10 invocations, cost-per-invocation. `skill_usage` table (Q2) provides the data. Phase 4 designs the display.

**CI-7 — Synthesis cost guard.** If Phase B revives synthesis: hard token budget per evolution pass (≤ 5K tokens), daily kill-switch, `skill_evolution_log` table. D7 deletes synthesis now; Phase B resets with benchmarks.

**CI-8 — `always: true` token-budget rebalance.** Auto-demote `always` skills with success rate < 0.4 (from `skill_usage` table) to context-injected only. Phase 4 designs the threshold tuner.

---

## Summary

**Critical path:** D2 (G2 fix — rename `_dynamicSkillsContext`, set `skillsContext` field at `context-builder.ts:34, 98`) is the single highest-value change. One parameter rename unblocks the entire skill injection pipeline. Everything else is additive.

**Net effect of this architecture:**
- Dual matchers → single `IntentRouter` (D1)
- Skills pipeline output → actually reaches the LLM (D2)
- `always: true` skills → honored (D4)
- `invoke_skill` tool → actually works (D5)
- Structured auto-execution → real gate, not `if (false)` (D6)
- Synthesis loop → deleted, freeing ~700 LOC of dead code (D7)
- Slash commands → unified channel-parity router (D8)
- Skill creation wizard → works on all channels (D9)
- Storage → single SQLite table (Q2)
- Hardcoded keywords → IntelligenceRouter (Q1)
- Orphan timeouts → AbortController (Q4)
- 5 files deleted, 2 files created, net ~−1000 LOC

**HALT — awaiting Boss approval before Phase 4 (brainstorming) launches.**
