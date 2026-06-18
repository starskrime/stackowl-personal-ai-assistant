# Element 17 — Owl System Design Spec
**Date:** 2026-05-09  
**Status:** Boss-approved  
**Inputs:** Phase 1 audit (G1–G15), Phase 2 research (R1–R11), Phase 3 architecture review (D1–D9, Q1–Q7)  
**Deliverable:** `docs/superpowers/specs/2026-05-09-element17-owl-system-design.md`

---

## Goal

Ship the last major behavioral layer of StackOwl as a single bundle:

- **Helper rebrand** — replace "specialized owl/agent" with "Helper" across all user-visible surfaces
- **Gateway management** — channel-agnostic `dispatchOwlCommand()` + 6-step creation wizard from any channel
- **Persistent task assignment** — helpers can be given recurring autonomous tasks at creation time
- **Routing improvements** — per-channel pin isolation, soft-pin TTL, natural-language invocation
- **Inner life safety** — fix 100ms monologue race, delete jailbreak-risk leak surfaces
- **DNA completeness** — surface all 8 DNA traits as live system-prompt directives
- **Quality metrics** — EWMA reward signal feeds routing confidence
- **Sub-owl parallel execution** — complete the half-built delegation layer (real tool dispatch)
- **Parliament diversity** — shuffled participant selection

---

## Architecture

Two new dispatch primitives wrap the existing owl infrastructure. Nothing below `OwlBrain` changes structurally.

```
User (any channel)
    │
    ├─ management intent ──► OwlManagementRouter.dispatchOwlCommand()   [NEW]
    │                            │  list/show/create/rename/delete/design/capabilities
    │                            ▼
    │                        OwlCreationWizard                          [NEW]
    │                            │  6 steps via ChannelAdapterV2.ask()
    │                            ▼
    │                        helper.md on disk  (canonical store, unchanged)
    │
    └─ conversation ──────► OwlBrain.resolve()                          [EXTENDED]
                                │  1. owl_pins restore  (per channel_id)
                                │  2. @mention regex  (unchanged)
                                │  3. NL mention  (IntelligenceRouter inline)
                                │  4. Session soft-pin + 3-miss TTL
                                │  5. SecretaryRouter  (quality-weighted)
                                │  6. Default owl
                                ▼
                            OwlEngine / SubOwlRunner                    [COMPLETED]
                                │  Promise.all() parallel subtasks
                                │  real tool dispatch (not stubs)
                                ▼
                            Delivery → channel
```

**Unchanged:** file store (`owls/<Name>/helper.md`), OwlEvolutionEngine, Parliament debate mechanics, ContextPipeline layers, HeartbeatEngine scheduling.

---

## Section 1 — Helper System (Rebrand + Storage)

### 1.1 Rebrand

"Helper" (singular) / "Helpers" (plural) replaces all user-visible instances of "specialized owl", "specialized agent", and "specialization".

| Surface | Before | After |
|---------|--------|-------|
| Slash command | `/specialization` | `/helper` |
| User-facing output strings | "specialized owl", "specialized agent" | "helper", "Helper" |
| On-disk file | `owls/<Name>/specialized_owl.md` | `owls/<Name>/helper.md` |
| Type names (code-internal) | `SpecializedOwlSpec`, `SpecializedRegistry` | `HelperSpec`, `HelperRegistry` |
| Methods | `loadSpecialized()`, `parseSpecialized()` | `loadHelper()`, `parseHelper()` |

**Backward compatibility:** if `helper.md` is not found but `specialized_owl.md` exists in the same directory, load the old file silently. No user action required. This window covers 2 major versions.

### 1.2 Storage changes (v23 migration)

**Dropped:**
```sql
DROP TABLE IF EXISTS owls;
-- (was created in v10, never used by live routing code)
```

**Added:**
```sql
CREATE TABLE owl_quality_metrics (
  owl_name     TEXT NOT NULL,
  owner_id     TEXT NOT NULL,
  turn_count   INTEGER NOT NULL DEFAULT 0,
  ewma_reward  REAL    NOT NULL DEFAULT 0.7,
  last_updated TEXT,
  PRIMARY KEY (owl_name, owner_id)
);

CREATE TABLE owl_pins (
  user_id    TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  owl_name   TEXT NOT NULL,
  pinned_at  TEXT NOT NULL,
  PRIMARY KEY (user_id, channel_id)
);
```

`owl_quality_metrics` — written after every trajectory turn (EWMA α=0.15), read by `SecretaryRouter.calculateConfidence()`. New helpers start at `ewma_reward=0.7` (neutral).

`owl_pins` — replaces the single `active_pin` column on `user_profiles` for per-channel isolation. The existing `active_pin` column is kept as a `channel_id='global'` fallback and soft-deprecated.

### 1.3 OwlQualityRepo (replaces OwlsRepo in `db.ts`)

```typescript
interface OwlQualityRepo {
  get(owlName: string, ownerId: string): { ewmaReward: number; turnCount: number } | null
  update(owlName: string, ownerId: string, reward: number): void
  // update formula: new_ewma = 0.15 * reward + 0.85 * old_ewma
}
```

Replaces `OwlsRepo` class in-place in the same section of `db.ts`. No new file.

---

## Section 2 — Gateway Management Layer

### 2.1 OwlManagementRouter — `src/gateway/commands/owl-router.ts`

Mirrors `src/gateway/commands/memory-router.ts` exactly in shape.

```typescript
export interface OwlRouterDeps {
  registry: HelperRegistry
  wizard: OwlCreationWizard
  userId: string
  channelAdapter: ChannelAdapterV2
}

export async function dispatchOwlCommand(
  verb: string,
  args: string[],
  deps: OwlRouterDeps,
): Promise<string>
```

| Verb | Behavior |
|------|----------|
| `list` | Bulleted list of all helpers: `🦉 Nora — cooking helper` |
| `show <name>` | Full spec: role, personality, capabilities, restrictions, recurring task if set |
| `create` | Launches `OwlCreationWizard` for this userId |
| `design <name>` | Re-runs personality step for existing helper |
| `capabilities <name>` | Re-runs capabilities + restrictions steps |
| `rename <old> <new>` | Renames `owls/<old>/` directory to `owls/<new>/`, reloads registry |
| `delete <name> [yes]` | Requires `yes` confirm arg; removes directory + reloads |

Returns plain strings. Channels render the string. No owl logic lives in channel adapters.

**Channel wiring:**

- CLI: replace `cmdSpecialization` at `commands.ts:64–191` with thin wrapper calling `dispatchOwlCommand`
- Telegram: add `/helper` command dispatch
- Slack: add `/helper-list`, `/helper-show`, `/helper-create`, etc.
- Voice: cheap-tier `IntelligenceRouter` maps natural-language intent to verb, then calls `dispatchOwlCommand`

### 2.2 OwlCreationWizard — `src/gateway/wizards/owl-creation.ts`

```typescript
export class OwlCreationWizard {
  private sessions = new Map<string, WizardSession>()
  // per-userId — replaces module-scope singleton at commands.ts:60

  async start(userId: string, channelAdapter: ChannelAdapterV2): Promise<string>
  isActive(userId: string): boolean
  cancel(userId: string): void
}
```

**6-step sequence** (+ optional step 7):

```
Step 1 — Name
  ask: "What should I call your new helper?"
  input: free text

Step 2 — Role
  ask: "What will [Name] help with?"
  input: free text

Step 3 — Personality
  ask: "Pick a style for [Name]:"
  choices: Warm & patient | Direct & efficient |
           Curious & encouraging | Formal & precise | Custom…
  (Custom → follow-up: "Describe [Name]'s style in a few words")

Step 4 — Capabilities
  ask: "What can [Name] do?"
  choices: Search the web | Read & write files |
           Run code | Manage tasks | All of the above

Step 5 — Restrictions
  ask: "Anything [Name] should never do?"
  defaultChoice: "Nothing specific"

Step 6 — Confirm
  ask: "Creating [Name]: [role]. Style: [personality]. Can: [caps]. Ready?"
  choices: Yes, create it | No, start over

Step 7 — Recurring task (optional)
  ask: "Should [Name] work on anything automatically?
        For example: 'Check the news daily at 9am' (or skip)"
  defaultChoice: "Skip"
```

On confirm (Step 6 = Yes):
1. Write `owls/<Name>/helper.md` with all fields
2. If Step 7 was provided: write `recurring_task` field in `helper.md` AND insert row in `owl_jobs`
3. Reload `HelperRegistry`
4. Return "✓ [Name] is ready! Say '[Name], ...' anytime to reach her."

Session timeout: 30 minutes → silent discard.

### 2.3 Persistent task assignment

When a recurring task is specified in Step 7:

```typescript
// Written to helper.md:
recurring_task: "Check the news daily at 9am"

// Written to owl_jobs:
{
  helper_name: "Nora",
  owner_id: userId,
  schedule: "09:00 daily",       // parsed from natural-language description
  task_description: "Check the news daily at 9am",
  channel_id: channelId,         // deliver results back to originating channel
}
```

When the job fires, `SubOwlRunner` runs with Nora's personality + permissions (only her allowed tools). Output routes through the existing delivery system to `channel_id`. The `show` command for Nora includes the active recurring task.

Schedule parsing: cheap-tier `IntelligenceRouter` extracts cron-like schedule from the user's free-text description. Falls back to "daily at 09:00" if parsing is ambiguous.

---

## Section 3 — Routing & Invocation

### 3.1 Per-channel pin (Q1)

`OwlBrain` passes `message.channelId` on every pin get/set:

```typescript
// Get pin for this specific channel:
this.db.owlPins.get(userId, message.channelId)
// Falls back to channel_id='global' row if no channel-specific pin

// Set pin (explicit mention only):
this.db.owlPins.set(userId, message.channelId, owlName, new Date().toISOString())
```

Pinning Aria in Telegram no longer affects CLI. `message.channelId` is already present on `GatewayMessage` — no interface change needed.

### 3.2 Soft-pin TTL (Q2)

Signal-routing matches (SecretaryRouter implicit routing) write to **session only**, never to SQLite:

```typescript
// In OwlBrain after SecretaryRouter match:
if (routingDecision.type === "specialist") {
  session.metadata.activeOwlName = spec.name
  session.metadata.softPinMissCount = 0
  // DO NOT call db.owlPins.set() — soft pin only
}

// At top of resolve(), before signal routing:
if (session.metadata.activeOwlName) {
  const currentMatch = await router.route(text, userId)
  if (currentMatch.type === "specialist" &&
      currentMatch.owl.name === session.metadata.activeOwlName) {
    session.metadata.softPinMissCount = 0
  } else {
    session.metadata.softPinMissCount = (session.metadata.softPinMissCount ?? 0) + 1
    if (session.metadata.softPinMissCount >= 3) {
      session.metadata.activeOwlName = undefined
      session.metadata.softPinMissCount = 0
    }
  }
}
```

**Pin type summary:**
| Source | Pin type | Storage | Clears on |
|--------|----------|---------|-----------|
| Explicit @mention | Hard | SQLite `owl_pins` | `@coordinator` or `/helper unpin` |
| NL mention (conf ≥ 0.75) | Hard | SQLite `owl_pins` | `@coordinator` or `/helper unpin` |
| Signal routing match | Soft | Session only | 3 consecutive non-matching turns |

### 3.3 Natural-language mention parser (D9)

Added inline in `owl-brain.ts`, runs when message doesn't start with `@`:

```typescript
async function parseNaturalLanguageMention(
  text: string,
  activeRoster: string[],
  router: IntelligenceRouter,
): Promise<{ targeted: string | null; confidence: number }> {
  if (activeRoster.length === 0) return { targeted: null, confidence: 0 }

  const prompt =
    `Message: "${text}"\n` +
    `Active helpers: [${activeRoster.join(", ")}]\n` +
    `Is the user explicitly addressing one of these helpers by name?\n` +
    `Reply JSON: {"targeted": string|null, "confidence": 0-1}`

  const response = await router.classify("classification", prompt)
  return JSON.parse(response)
}

// Thresholds:
// confidence >= 0.75 → treat as explicit mention → hard pin
// confidence  < 0.75 → silent fallback (no interruption, use default owl)
// ambiguous/partial  → threshold raised to 0.85
```

Recognizes: `"Aria, can you..."`, `"Hey Aria,"`, `"ask Aria about X"`  
Falls through on: `"Aria said it was raining"` (talking *about* not *to*)  
Fallback on low confidence: **always silent** — never ask "did you mean Aria?"

### 3.4 Quality-weighted routing (D6)

`SecretaryRouter.calculateConfidence()` replaces the hardcoded `0.7` default:

```typescript
const qualityFactor =
  db.owlQualityMetrics.get(owlName, ownerId)?.ewmaReward ?? 0.7
// replaces: const qualityFactor = dna.routingQuality ?? 0.7
```

New helpers start neutral at 0.7 and earn their routing priority through outcomes.

**Write hook** in `post-processor.ts` — after each trajectory write:
```typescript
db.owlQualityMetrics.update(activeOwlName, ownerId, rewardValue)
// rewardValue comes from the existing reward field in trajectory_turns
```

---

## Section 4 — Inner Life & Safety

### 4.1 Monologue race fix (D3)

`thinkInBackground()` in `inner-life.ts` returns its Promise (currently `void`):

```typescript
// Before:
thinkInBackground(userMessage, sessionHistory): void

// After:
thinkInBackground(userMessage, sessionHistory): Promise<void>
```

In `core.ts`, replace `setImmediate + setTimeout(100ms)` with `.then()` chain:

```typescript
// Before (core.ts:3988-3989) — fires before LLM finishes ~95% of turns:
setImmediate(async () => {
  await new Promise(r => setTimeout(r, 100))
  const monologue = innerLife.getLastMonologue?.()   // null almost always
  if (monologue) digestManager.setLastMonologue(...)
})

// After — fires when actually ready:
innerLife.thinkInBackground(userMsg, messages)
  .then(() => {
    const monologue = innerLife.getLastMonologue()
    if (monologue && digestManager && sessionId) {
      digestManager.setLastMonologue(sessionId, {
        thoughts: monologue.thoughts,
        responseIntent: monologue.responseIntent,
        moodCurrent: monologue.moodShift?.current,
        storedAt: new Date().toISOString(),
      })
    }
  })
```

### 4.2 Jailbreak surface deletion (D3)

Delete from `src/owls/inner-life.ts`:
- `toContextString()` (L430–477) — injects raw mood, thoughts, desires, opinions, unspokenObservations
- `monologueToDirective()` (L482–497) — injects raw thoughts + unspokenObservation

Both methods expose private inner state via system-prompt injection. An adversarial user can extract these via prompt injection. The only safe output path is `InnerMonologueLayer` (`src/context/layers/inner-monologue.ts`) which emits only `responseIntent` and `moodCurrent` — redacted abstracts with no raw private content.

### 4.3 RelationshipContext wiring (D4)

In `runtime.ts` system prompt assembly, after the owl persona block:

```typescript
if (ctx.relationshipContext && ctx.userId) {
  const block = await ctx.relationshipContext.buildPromptBlock(ctx.userId)
  if (block) {
    systemPrompt += "\n\n" + trimToTokenBudget(block, 200)
  }
}
```

`buildPromptBlock()` returns a `<user_relationship>` XML block containing:
- `communicationStyle` (from UserMemoryStore)
- `expertiseLevel` (from UserMemoryStore)
- `recurringTopics` (from routing history)
- `openCommitments` (from owl_tasks)

200-token max — trimmed by existing `trimToTokenBudget()`.

### 4.4 OpinionInjector wiring (D4)

In `core.ts`, pre-LLM call:
```typescript
const opinionMatch = opinionInjector.findRelevant(
  userMessage,
  ctx.innerLife?.getState()?.opinions ?? [],
)
if (opinionMatch) {
  engineCtx.additionalSystemPrompt =
    (engineCtx.additionalSystemPrompt ?? "") +
    opinionInjector.formatForSystemPrompt(opinionMatch)
}
```

Post-response (fire-and-forget):
```typescript
opinionInjector.formOpinionAsync(userMessage, ctx.innerLife).catch(() => {})
```

### 4.5 Surface all 8 DNA traits (D5)

In `runtime.ts` after the existing `verbosityDirectives` block (`~L2459`):

```typescript
const humorDirectives: Record<string, string> = {
  low:    "Minimal humor — keep responses substantive.",
  medium: "Light wit when natural.",
  high:   "Lean into humor, wordplay, and levity.",
}
const formalityDirectives: Record<string, string> = {
  casual:   "Casual tone — talk like a friend.",
  balanced: "Professional yet warm.",
  formal:   "Formal, structured, precise.",
}
const proactivityDirectives: Record<string, string> = {
  low:    "Answer what's asked; don't over-volunteer.",
  medium: "Surface related ideas when relevant.",
  high:   "Proactively suggest follow-ups and next steps.",
}
const riskToleranceDirectives: Record<string, string> = {
  conservative: "Prefer proven, safe approaches.",
  moderate:     "Balance innovation with caution.",
  aggressive:   "Favor bold, fast solutions when stakes allow.",
}
const teachingStyleDirectives: Record<string, string> = {
  directive: "Give direct instructions.",
  adaptive:  "Match explanation depth to the user.",
  socratic:  "Guide with questions rather than answers.",
}
const delegationDirectives: Record<string, string> = {
  solo:          "Handle tasks yourself where possible.",
  collaborative: "Suggest other helpers when beneficial.",
  delegator:     "Proactively propose delegation to specialist helpers.",
}
```

All 8 DNA traits now mutate AND produce live system-prompt directives every turn.

---

## Section 5 — Sub-Owl Parallel Execution

### 5.1 Tool args fix (`subowl-executor.ts:41`)

```typescript
// Before:
const result = await tool.execute({}, context)

// After:
const result = await tool.execute(task.args ?? {}, context)
```

Add to `SubTask` interface in `decomposer.ts`:
```typescript
args?: Record<string, unknown>
```

### 5.2 Tool registry injection (`sub-owl-runner.ts`)

```typescript
// Constructor — add toolRegistry parameter:
constructor(
  private provider: ModelProvider,
  private toolRegistry: Map<string, ToolImplementation>,  // NEW
  private owlPersonality: string,
  private workspacePath: string,
  private maxIterations = 5,
) {}

// In reactLoop() — replace stub with real dispatch:
const { toolName, toolArgs } = parseToolCall(lastResponse)
const tool = this.toolRegistry.get(toolName)
const toolResult = tool
  ? await tool.execute(toolArgs ?? {}, context).catch(e => `[Tool error: ${e.message}]`)
  : `[Tool ${toolName} not found in registry]`
history.push({ role: "user", content: toolResult })
```

`parseToolCall()` — best-effort JSON extraction from `lastResponse`. Returns `{ toolName: string, toolArgs: Record<string, unknown> }`. If parsing fails, returns `{ toolName: lastResponse.trim(), toolArgs: {} }`.

### 5.3 Parallel execution

`SubOwlRunner.run()` already calls `Promise.all()` over subtasks. The fixes above make it actually execute tools instead of returning stubs. No structural changes to the parallel path.

Wire `SubOwlRunner` construction in `core.ts` to pass `ctx.toolRegistry` (already available on `GatewayContext`).

---

## Section 6 — Parliament Diversity

Two-line fix at `core.ts:1895` and `core.ts:2026`:

```typescript
// Before:
participants: this.ctx.owlRegistry.listOwls().slice(0, 3)

// After:
participants: shuffleArray([...this.ctx.owlRegistry.listOwls()]).slice(0, 3)
```

`shuffleArray` — Fisher-Yates implementation added as a local utility in `core.ts`:

```typescript
function shuffleArray<T>(arr: T[]): T[] {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]]
  }
  return a
}
```

Helpers excluded by default — only base owls participate. A future `parliamentParticipant: true` field in `helper.md` can opt helpers in (Phase B).

---

## Section 7 — Channel Parity Matrix

All channels call `dispatchOwlCommand()`. Only input parsing differs.

| Action | CLI | Telegram | Slack | Voice |
|--------|-----|----------|-------|-------|
| List helpers | `/helper list` | `/helper list` | `/helper-list` | "What helpers do I have?" |
| Show helper | `/helper show Aria` | `/helper show Aria` | `/helper-show Aria` | "Tell me about Aria" |
| Create helper | `/helper create` | `/helper create` | `/helper-create` | "Create a new helper" |
| Delete helper | `/helper delete Aria yes` | `/helper delete Aria yes` | `/helper-delete Aria yes` | "Delete Aria" |
| Invoke helper | `@aria` or "Aria, ..." | `@aria` or "Aria, ..." | `@aria` or "Aria, ..." | "Aria, ..." |
| Unpin | `@coordinator` | `@coordinator` | `@coordinator` | "Switch back" |

Voice: cheap-tier `IntelligenceRouter` maps natural-language intent to verb, then calls `dispatchOwlCommand`. Same dispatcher, same output string.

---

## Section 8 — Grandma Walkthrough (Telegram)

*Maria, 62, no technical background. Wants a cooking helper named Nora.*

**Creation flow:**
```
Maria:   I want a helper for cooking

Owl:     I can set that up! What should I call her?

Maria:   Nora

Owl:     What will Nora help with?

Maria:   recipes, grocery lists, what to cook when I have leftovers

Owl:     Pick a personality style for Nora:
         1. Warm & patient
         2. Direct & efficient
         3. Curious & encouraging
         4. Formal & precise

Maria:   1

Owl:     What can Nora do?
         1. Search the web
         2. Read & write files
         3. Run code
         4. Manage tasks
         5. All of the above

Maria:   1 and 4

Owl:     Anything Nora should never do?
         (or just say "skip")

Maria:   skip

Owl:     Creating Nora — cooking helper, warm & patient,
         can search the web and manage tasks. Ready?
         1. Yes, create her
         2. No, start over

Maria:   1

Owl:     ✓ Nora is ready! Say "Nora, ..." anytime to reach her.
         Should Nora work on anything automatically?
         For example: "remind me about dinner at 6pm daily"
         (or say "skip")

Maria:   skip

Owl:     Done! Next time you ask "Nora, what can I make with
         leftover chicken?" — she'll be right there.
```

**Three days later — natural invocation:**
```
Maria:   Nora, I have pasta, tomatoes and some cheese, what can I make?

Nora:    Oh, that's a lovely combination! You could make a quick
         pasta al pomodoro — sauté the tomatoes with garlic,
         toss with pasta, finish with the cheese on top.
         Want me to save a proper recipe for you?
```

No commands. No "agent". No "specialization". Maria never saw a technical term.

---

## Section 9 — Migration Plan

| What | How | Risk |
|------|-----|------|
| `owls` table dropped | v23 migration: `DROP TABLE IF EXISTS owls` | Low — never used by live routing |
| `owl_quality_metrics` created | v23 migration | None — new table |
| `owl_pins` created | v23 migration | None — new table |
| `active_pin` column | Kept as `channel_id='global'` fallback | None |
| `specialized_owl.md` → `helper.md` | Load-time compat in `loadHelper()` | None — silent |
| Type renames | In-place find+replace in existing files | Low — internal |
| `/specialization` → `/helper` | Old command removed, new registered | Low |
| `routing-coordinator.ts` deleted | `injectMemoryContext()` ported to `OwlBrain` first | Low |
| `specialization-wizard.ts` deleted | Replaced by `owl-creation.ts` first | Low |
| `dist/wizard/owl-creation.d.ts` | Delete orphan directly | None |
| `evolutionBatchSize ?? 10` | Change to `?? 5` in `post-processor.ts:219` | None |

---

## Section 10 — Test Plan

10 new test files (~70 tests total). Delete `__tests__/memory/owls-repo.test.ts` (tests dead code).

| File | Covers | ~Tests |
|------|--------|--------|
| `__tests__/routing/owl-brain-channel-pin.test.ts` | Telegram pin doesn't bleed to CLI | 6 |
| `__tests__/routing/owl-brain-soft-pin.test.ts` | 3-miss TTL clears session pin; @mention produces hard pin | 7 |
| `__tests__/routing/owl-mention-nl.test.ts` | Confidence threshold, ambiguous names, partial, silent fallback | 8 |
| `__tests__/gateway/owl-router.test.ts` | `dispatchOwlCommand()` all 7 verbs | 12 |
| `__tests__/gateway/owl-creation-wizard.test.ts` | 6-step sequence, per-userId isolation, timeout, recurring task | 9 |
| `__tests__/owls/inner-life-monologue-race.test.ts` | Monologue persisted after Promise resolves, not before | 5 |
| `__tests__/owls/dna-all-traits.test.ts` | All 8 DNA traits produce non-empty directives | 8 |
| `__tests__/delegation/subowl-args-passthrough.test.ts` | Tool args forwarded, not empty `{}` | 5 |
| `__tests__/delegation/subowl-tool-execution.test.ts` | Tool registry wired; actual tool call fires | 6 |
| `__tests__/parliament/shuffled-selection.test.ts` | Selection varies across 20 runs | 4 |

---

## Section 11 — File Delta

### New files in `src/` (2)
| File | Purpose |
|------|---------|
| `src/gateway/commands/owl-router.ts` | `dispatchOwlCommand()` dispatcher |
| `src/gateway/wizards/owl-creation.ts` | Channel-agnostic 6-step wizard |

### Deleted files from `src/` (2)
| File | Reason |
|------|--------|
| `src/gateway/handlers/routing-coordinator.ts` | Dead fallback; unique logic ported to OwlBrain |
| `src/cli/specialization-wizard.ts` | Superseded by owl-creation.ts |

**Net `src/` delta: 0** ✓

### Other deletions
- `dist/wizard/owl-creation.d.ts` — orphaned compiled artifact
- `__tests__/memory/owls-repo.test.ts` — tests deleted code

### Modified files (significant)
`src/memory/db.ts`, `src/routing/owl-brain.ts`, `src/owls/inner-life.ts`, `src/gateway/core.ts`, `src/engine/runtime.ts`, `src/gateway/handlers/post-processor.ts`, `src/routing/secretary.ts`, `src/parliament/orchestrator.ts`, `src/delegation/sub-owl-runner.ts`, `src/delegation/subowl-executor.ts`, `src/delegation/decomposer.ts`, `src/owls/specialized-types.ts`, `src/owls/specialized-registry.ts`, `src/owls/specialized-parser.ts`, `src/cli/commands.ts`, `src/gateway/adapters/telegram.ts`, `src/gateway/adapters/slack.ts`, `src/routing/user-profile-service.ts`

---

## Section 12 — Blockers / Out of Scope

**Deferred to Phase B (need production data first):**
- `/helper metrics` dashboard (CI-4) — needs quality data to accumulate
- Parliament opt-in for helpers (`parliamentParticipant: true` flag)
- Phase 7b/7c readiness gate (separate check: 2026-05-16)

**Deferred to Element 18/Providers:**
- Per-channel persona shaping (voice=concise, Telegram=rich text) — CI-2

**Out of scope entirely:**
- Multi-tenant helper sharing (one user's helper borrowed by another)
- Web channel helper management (no web adapter exists)
- `/inner_voice on|off` toggle (CI-5) — token cost analysis needed first
- Unified `OwlIdentity` view layer (CI-1) — needs production data to validate
- Voice TTS prosody per helper — requires platform-level audio work

---

## Boss Approval Gate

All 8 design sections approved by Boss on 2026-05-09.  
Phase 5 (implementation plan) ready to proceed.
