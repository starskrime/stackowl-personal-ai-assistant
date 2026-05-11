# StackOwl Rearchitecture Design
**Date:** 2026-05-10
**Author:** Winston (BMAD Architect) + Bakir
**Status:** Approved

---

## Vision

StackOwl becomes the deepest personal AI assistant in the world: not by copying OpenClaw's breadth, but by shipping OpenClaw's missing depth (real autonomy, always-loaded memory, skill growth) while protecting StackOwl's unique moats (Parliament, DNA, Pellets, Verification). The result: an assistant that is simultaneously more autonomous, more knowledgeable, more personalized, and available everywhere the user already is.

---

## Competitive Context

### Where StackOwl wins over OpenClaw

| Capability | StackOwl | OpenClaw |
|---|---|---|
| Multi-agent reasoning | Parliament — 3-round structured debates + synthesis | None |
| Personality evolution | Owl DNA traits that mutate over time | Static SOUL.md only |
| Inner life | Desires, mood, opinions | None |
| Knowledge graph | Pellets + LanceDB + Kuzu | Basic MEMORY.md |
| Completion verification | False-done detection, evidence-based | None |
| Screen automation | Oscar — macOS accessibility tree | Via external CLI only |
| Dynamic model routing | Haiku/Sonnet/Opus per task complexity | Static per-agent |
| Observability | Full W3C trace propagation, JSONL | Basic logging |

### Where StackOwl loses (critical gaps to close)

| Gap | StackOwl Problem | OpenClaw Solution to Adopt |
|---|---|---|
| Real autonomy | BackgroundOrchestrator stubs | Isolated agent cron with real tool execution |
| Memory recall speed | 7-layer async pipeline | Always-loaded MEMORY.md (instant, no query) |
| Skill growth | 149 TS modules only | SKILL.md markdown skills + skill creator |
| Channel breadth | 3 channels | 22+ channels (Discord, Slack, WhatsApp, etc.) |
| Scheduled execution | 5-min tick, no isolation | Full cron service + isolated agent context |
| Skill ecosystem | No marketplace | ClawHub equivalent (SkillHub) |
| MCP/ACP maturity | Partial implementations | Production-complete |

---

## Architecture Overview

Five epics, each independently deployable, in priority order:

```
Epic 1: Real Autonomy       — Fix stubs; background execution with real tools + memory
Epic 2: SKILL.md Skills     — Markdown-native skills + LLM skill creation
Epic 3: MEMORY.md Tier      — Always-loaded Tier 0 memory; instant recall
Epic 4: Channel Expansion   — Discord + Slack + WhatsApp adapters
Epic 5: Cron Isolated Agent — Full cron service + isolated OwlEngine runs
```

Each epic leaves the system in a shippable, better state than before.

---

## Epic 1: Real Autonomy

### Problem

StackOwl has the architecture for background autonomy but the implementations are scaffolding:
- `BackgroundOrchestrator.runMemoryConsolidation()` = one log line
- `CognitiveLoop.captureObservation()` = hardcoded zeros (`x:0, y:0, elements:[]`)
- `ProactiveAssistant.findHabitualSuggestions()` = hardcoded `if (app === "photoshop")` rules
- `BackgroundWorker.tick()` is never wired to execute real tool tasks

### Solution

#### 1a. BackgroundOrchestrator.runMemoryConsolidation()

**File:** `src/background/orchestrator.ts`

Real implementation:
1. Query episodic store: `episodicStore.getOlderThan(24 * 60 * 60 * 1000)`
2. Guard: skip if fewer than `config.memoryConsolidationBatchSize` (default 20) episodes
3. Batch compress via `MemoryCompressor.compress(episodes)` (already exists at `src/memory/compressor.ts`)
4. Write summary to SQLite digests table
5. Delete compressed raw episodes
6. Log: episode count compressed, estimated token savings

#### 1b. CognitiveLoop.captureObservation() — real data

**File:** `src/oscar/cognition/loop.ts`

Replace hardcoded zeros with actual observation:
1. Primary path (macOS): call `ScreenGraphObservatory.capture()` (already exists at `src/oscar/perception/observatory.ts`)
2. Fallback path (cross-platform): call `process.list()` to get focused process name
3. Return real `Observation` with actual `app`, `elements`, `cursorPosition`

This unblocks all proactive assistance that depends on real context.

#### 1c. ProactiveAssistant — LLM-driven suggestions

**File:** `src/oscar/cognition/proactive.ts`

Replace hardcoded rules with LLM call:
1. `suggest(context)` → format observation as natural language
2. Call provider with Haiku tier: "Given this context, what is one concise proactive suggestion?"
3. Cache result: TTL 5 minutes to avoid model hammering
4. Return LLM-generated suggestion as `Suggestion[]`

Cost guard: max 1 model call per 5 minutes per owl.

#### 1d. BackgroundWorker.tick() — real task execution

**File:** `src/agent/background-worker.ts`

Connect to OwlEngine for actual execution:
1. On `tick()`: fetch next pending task from `db.agentTasks.nextPending()`
2. Check task risk level: only auto-execute `risk: "low" | "medium"` tasks
3. Call `OwlEngine.run(task.prompt, engineContext)` with tools enabled
4. On success: save result as pellet via `PelletGenerator.generate()`
5. On completion: queue Telegram notification if `config.briefingTarget` set
6. Max task execution: `TASK_TIMEOUT_MS = 3 * 60 * 1000` (unchanged)

### Data Flow

```
CognitiveLoop.observe() [real screen/process data]
  → patterns detected
  → BackgroundOrchestrator.tick()
      → runMemoryConsolidation()   [real LLM compression]
      → runProactivePing()         [LLM-generated suggestion]
      → runDesireExecution()       [existing, already real]
  → BackgroundWorker.tick()
      → OwlEngine.run(task)        [full ReAct with tools]
      → PelletGenerator.generate() [save knowledge artifact]
      → Telegram notification
```

---

## Epic 2: SKILL.md Format

### Problem

149 TypeScript skill modules are unmaintainable and cannot be created by the LLM. OpenClaw skills are markdown files readable and writable by the agent itself, enabling exponential growth. StackOwl needs this growth mechanism without discarding the TypeScript tools.

### Solution

#### 2a. SKILL.md Schema

Skills directory structure (additive to existing TypeScript skills):

```
~/.stackowl/skills/
└── skill-name/
    ├── SKILL.md        ← required: YAML frontmatter (name, description) + instructions
    ├── scripts/        ← optional: scripts the LLM can execute
    └── references/     ← optional: domain docs loaded on demand
```

SKILL.md frontmatter (minimal):
```yaml
---
name: skill-name
description: >
  What this skill does and when to use it. Include triggers.
  Example: "Use when user wants to work with PDF files."
---
```

Body = markdown instructions, loaded only after skill triggers. Kept under 500 lines. Longer material goes in `references/`.

#### 2b. SkillsRegistry Enhancement

**File:** `src/skills/loader.ts`

Add SKILL.md directory scanning alongside existing TypeScript loading:
1. Scan `~/.stackowl/skills/` for directories containing `SKILL.md`
2. Parse frontmatter (using existing `gray-matter` dependency)
3. Register as `SkillEntry` with `source: "markdown"`
4. Format for system prompt injection via `formatSkillsForPrompt()`:

```xml
<available_skills>
  <skill>
    <name>skill-name</name>
    <description>What it does and when to use it.</description>
    <location>/path/to/SKILL.md</location>
  </skill>
</available_skills>
```

Progressive disclosure:
- Tier 1 (always in context): name + description (~50 words)
- Tier 2 (loaded on trigger): full SKILL.md body
- Tier 3 (loaded as needed): references/ files

#### 2c. CreateSkillTool

**New file:** `src/tools/create-skill.ts`

Tool that lets the LLM create new skills from conversation:
- Input: `name`, `description`, `instructions`, optional `scripts`
- Action: write SKILL.md to `~/.stackowl/skills/<name>/SKILL.md`
- Validation: name is hyphen-case, description is non-empty, under 64 chars
- Auto-register: `SkillsRegistry.refresh()` after write
- Used by `cognition/loop.ts` `autonomous_skill_synthesis` action (already defined)

#### 2d. SkillHub (Marketplace)

**New file:** `src/skills/hub.ts`

Lightweight skill marketplace:
- `SkillHub.search(query)` → fetches from JSON index (GitHub-hosted, no auth required)
- `SkillHub.install(name)` → downloads SKILL.md + resources to `~/.stackowl/skills/`
- Initial index: curated selection of the most useful community skills
- CLI: `stackowl skill add <name>`, `stackowl skill search <query>`

#### Migration Note

Existing 149 TypeScript skill modules remain unchanged and continue working. SKILL.md skills are purely additive. Over time, high-value TypeScript skills can be replaced with SKILL.md equivalents as they're validated.

---

## Epic 3: Always-Loaded MEMORY.md Tier

### Problem

Every conversation assembles context through 7 async layers (LanceDB → SQLite episodic → facts FTS → memory threads → preferences → skills → persona). This has measurable latency and cascading failure modes. For facts the user needs on every conversation turn, this is wasteful and unreliable.

### Solution

Adopt OpenClaw's proven 4-tier memory model as a complement to the existing pipeline:

#### 3a. MEMORY.md — Tier 0 (Always Loaded)

**File location:** `~/.stackowl/workspace/MEMORY.md`

Properties:
- Curated, ~100 lines, human-readable
- Loaded **unconditionally** on every conversation turn
- Injected at the top of system prompt, before everything else
- Never truncated (budget circuit breaker exempts it)
- Content: user's name, key preferences, ongoing projects, important relationships, facts that matter every day

Example initial content:
```markdown
# About me
- Name: Bakir
- Timezone: UTC+4
- Primary language: English

# Current projects
- StackOwl personal AI assistant (TypeScript, Node 22)

# Preferences
- Concise responses, no filler
- TypeScript strict mode always on

# Key relationships
(Add names and context here)
```

#### 3b. Dated Session Files — Tier 1 (Working Memory)

When the user starts a new session or sends `/new`:
1. Hook fires: `onSessionReset`
2. Save last session to `~/.stackowl/workspace/memory/YYYY-MM-DD-HHMM.md`
3. Content: last N messages (default 15) + LLM-generated descriptive filename slug
4. Auto-loaded: today's + yesterday's session files

**File:** `src/memory/session-saver.ts` (new)

#### 3c. UpdateMemoryTool

**New file:** `src/tools/update-memory.ts`

Tool that lets the LLM maintain MEMORY.md:
- Input: `operation: "add" | "update" | "remove"`, `section`, `content`
- Validates: no line over 200 chars, total under 150 lines
- System prompt instruction: "When you learn a durable fact (user preference, recurring goal, important relationship), add it to MEMORY.md with `update_memory`."

Wire to Owl DNA: when `learnedPreferences` updates, suggest a MEMORY.md addition.

#### 3d. Integration with Existing Pipeline

Loading order in `src/context/pipeline.ts`:
1. **MEMORY.md** (new Tier 0) — synchronous file read, ~1ms
2. **Dated session files** (new Tier 1) — synchronous reads
3. **Existing layers 1-6** — unchanged (facts, episodic, pellets, preferences, skills, persona)

MEMORY.md is never deduplicated against the LanceDB pipeline — they serve different purposes. MEMORY.md = instant curated recall. LanceDB = deep semantic search.

---

## Epic 4: Channel Expansion

### Problem

StackOwl reaches users on 3 channels (Telegram, CLI, Web). OpenClaw operates on 22+. Users who primarily use Discord, Slack, or WhatsApp cannot be served.

### Solution

Add 3 high-priority channels using adapters that map to the existing `GatewayMessage` / `GatewayResponse` shape. No engine changes required.

#### 4a. Discord Adapter

**New file:** `src/gateway/adapters/discord.ts`

Library: `discord.js` v14

Capabilities:
- DMs + server channels + threads
- Slash commands (register on startup)
- Reaction-based feedback
- File attachments (maps to `GatewayMessage.files`)
- Rich embeds for formatted responses

Config:
```json
"discord": {
  "botToken": "...",
  "guildIds": ["..."],
  "dmPolicy": "pairing"
}
```

#### 4b. Slack Adapter

**New file:** `src/gateway/adapters/slack.ts`

Library: `@slack/bolt` (already in `package.json`)

Capabilities:
- DMs + channel mentions + slash commands
- Thread replies
- Block Kit message formatting
- OAuth2 workspace installation flow

Config:
```json
"slack": {
  "botToken": "...",
  "appToken": "...",
  "signingSecret": "..."
}
```

#### 4c. WhatsApp Adapter

**New file:** `src/gateway/adapters/whatsapp.ts`

Library: `whatsapp-web.js` (local) or Twilio WhatsApp API (cloud)

Capabilities:
- DMs only (no group spam by default)
- Media message support (images, documents)
- DM pairing security model (see 4d)

Config:
```json
"whatsapp": {
  "mode": "web-local",
  "dmPolicy": "pairing"
}
```

#### 4d. DM Pairing Security Model

Adopt OpenClaw's security approach for all new channels:
1. Unknown sender message received → respond: "Send `PAIR <code>` to authorize."
2. User runs: `stackowl pairing approve <channel> <code>`
3. Sender added to allowlist for that channel
4. Implementation: `src/gateway/security/pairing.ts` (new)

This enables safe public exposure (Cloudflare Tunnel / Tailscale) without prompt injection risk.

#### 4e. Multi-Channel Routing

- Each channel registers via `ChannelRegistry.register(adapter)`
- `deliverResponse()` routes to originating channel (existing pattern, already works)
- Proactive pings delivered to `config.primaryChannel` (new config key)
- Heartbeat / cron results delivered to configured delivery target per job

---

## Epic 5: Cron Isolated-Agent Execution

### Problem

StackOwl's background worker runs tasks in-process with no isolation, no per-job auth, no delivery verification, no stagger, no completion guarantee. OpenClaw's cron service spawns fully isolated agent contexts per job with their own auth, delivery targeting, and completion notification.

### Solution

#### 5a. CronService

**New file:** `src/cron/service.ts`

Replaces BackgroundOrchestrator's `setInterval(5min)`:
- Full cron expression parsing via `croner` (add to deps)
- Per-job state: `lastRunAt`, `nextRunAt`, `status: "pending" | "running" | "completed" | "failed"`
- Max concurrent runs: `config.cron.maxConcurrentRuns` (default: 3)
- Stagger: jobs within 30s window get random 0–29s offset
- Persisted to `~/.stackowl/crons.json`

#### 5b. Isolated OwlEngine Context

Each cron job gets a fresh context:
- New `sessionHistory: []` (no contamination from main conversation)
- Configured tool subset per job `safetyProfile: "low" | "medium" | "full"`
- Own `traceId` for observability
- `deliveryTarget: { channel, userId }` for result routing

**New file:** `src/cron/isolated-runner.ts`

#### 5c. Default Scheduled Jobs

```json
[
  { "id": "memory-consolidation", "schedule": "0 * * * *",    "prompt": "Consolidate recent episodic memories", "safetyProfile": "low" },
  { "id": "desire-execution",     "schedule": "*/30 * * * *", "prompt": "Execute top owl desire",              "safetyProfile": "medium" },
  { "id": "dna-evolution",        "schedule": "0 2 * * *",    "prompt": "Evolve owl DNA based on recent interactions", "safetyProfile": "low" },
  { "id": "pellet-dedup",         "schedule": "0 3 * * *",    "prompt": "Deduplicate knowledge base",         "safetyProfile": "low" },
  { "id": "daily-briefing",       "schedule": "0 9 * * *",    "prompt": "Generate morning briefing for user", "safetyProfile": "low", "deliver": true }
]
```

#### 5d. User-Defined Cron Jobs

CLI: `stackowl cron add "every day at 9am: summarize my emails and send to Telegram"`

1. LLM parses natural language → cron expression + prompt
2. Confirmation shown: "I'll run this job at `0 9 * * *`. Confirm?"
3. Stored in `~/.stackowl/crons.json`
4. Listed: `stackowl cron list`
5. Removed: `stackowl cron remove <id>`

#### 5e. Delivery + Notification

On job completion:
- Result saved as pellet (if substantial knowledge)
- `deliver: true` jobs send result to `deliveryTarget` channel
- Failed jobs: alert sent to primary channel
- All runs logged to `~/.stackowl/logs/cron-YYYY-MM-DD.log`

---

## Non-Goals (explicitly out of scope)

- Replacing the core ReAct engine with Claude Code subprocess (Approach C) — preserve StackOwl's control
- Removing Parliament, DNA, Pellets, or Verification — these are competitive moats
- Full OpenClaw channel parity (Matrix, IRC, LINE, etc.) — Discord/Slack/WhatsApp cover 90% of demand
- Mobile companion apps (iOS/Android) — deferred

---

## Implementation Order

Each epic is independently shippable. Recommended order:

| Priority | Epic | Why first |
|---|---|---|
| P0 | Epic 1: Real Autonomy | Fixes broken promises; highest user-visible impact on "is this thing alive?" |
| P1 | Epic 3: MEMORY.md Tier | Every conversation improves immediately; zero risk, zero breakage |
| P2 | Epic 2: SKILL.md Format | Unlocks exponential capability growth via LLM-created skills |
| P3 | Epic 5: Cron Isolated Agent | Enables scheduled autonomous workflows |
| P4 | Epic 4: Channel Expansion | Broadest reach; depends on stability of above |

---

## Success Metrics

| Metric | Target |
|---|---|
| Background memory consolidation | Runs with real compression every 60 min when >20 episodes |
| Proactive suggestion quality | LLM-generated, context-aware, not hardcoded |
| MEMORY.md recall latency | <5ms (synchronous file read) |
| SKILL.md skills installable | `stackowl skill add <name>` works end-to-end |
| Discord/Slack/WhatsApp | User can chat with StackOwl on all three |
| Daily briefing cron | Runs at 9am, delivers via Telegram |
| New skills from conversation | LLM can create SKILL.md via CreateSkillTool |
