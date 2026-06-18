# Skills Unification Design

**Date:** 2026-04-25  
**Status:** Approved  
**Scope:** Merge `src/instincts/` into `src/skills/` under a single unified SKILL.md format with community install support.

---

## Problem

StackOwl has two parallel skill systems that never talk to each other:

- **Task skills** (`src/skills/`) — OpenCLAW-compatible, ClawHub-connected, installable via CLI
- **Behavioral skills** (`src/instincts/`) — Reactive triggers, INSTINCT.md format, no install path, no community ecosystem

Users cannot download and install behavioral skills from ClawHub, GitHub, or any community registry. The two systems duplicate loading, parsing, and registry logic with no shared interface.

---

## Goals

1. Single `SKILL.md` format that covers both task and behavioral skills
2. Behavioral skills installable via the existing `stackowl skills install` command
3. Support ClawHub, GitHub URL, and local path as install sources
4. Auto-migrate existing `INSTINCT.md` files to `SKILL.md` on first startup
5. Full compatibility with OpenCLAW, superpowers, and nonclaw skill ecosystems

---

## Non-Goals

- Skill publishing to ClawHub (out of scope)
- Skill versioning beyond lock file SHA pinning
- Skill sandboxing or permissions model

---

## Architecture

### Before

```
src/instincts/
  registry.ts     — loads INSTINCT.md files
  engine.ts       — reactive LLM batch classifier
  defaults/
    cost-alarm/INSTINCT.md

src/skills/
  registry.ts     — loads SKILL.md files
  loader.ts       — file watching, hot reload
  clawhub.ts      — ClawHub install client
  types.ts        — Skill interface
  defaults/
    git_commit/SKILL.md
    ...90+ more
```

### After

```
src/skills/
  registry.ts     — loads ALL SKILL.md (task + behavioral)
  loader.ts       — unchanged
  engine.ts       — reactive classifier (moved from instincts/)
  installer.ts    — NEW: ClawHub + GitHub + local install
  clawhub.ts      — unchanged
  types.ts        — extended with optional behavioral fields
  migrator.ts     — NEW: one-time INSTINCT.md → SKILL.md rename
  defaults/
    cost_alarm/SKILL.md   — migrated from instincts/defaults/
    git_commit/SKILL.md
    ...

src/instincts/    — DELETED
```

The `InstinctRegistry` is deleted. `InstinctEngine` moves to `src/skills/engine.ts` and queries `SkillRegistry` for skills where `conditions.length > 0`. All runtime imports from `src/instincts/` are re-pointed to `src/skills/`.

---

## Unified SKILL.md Format

All fields are optional except `name` and `description`. The presence of `conditions` determines routing.

```yaml
---
# ── Shared (all skills) ─────────────────────────────
name: cost_alarm
description: Warn about cloud cost implications when detected in conversation
openclaw:
  emoji: "💸"
  os: [darwin, linux, win32]

# ── Behavioral (optional — presence = reactive) ──────
trigger: context                          # context | schedule | event
conditions:
  - "user mentions cloud costs or billing"
  - "user is comparing managed vs self-hosted"
relevant_owls: ["scrooge", "*"]
priority: high                            # low | medium | high | critical

# ── Task (optional — presence = executable) ──────────
parameters:
  budget:
    type: number
    description: Monthly budget threshold in USD
    default: 100
steps:
  - id: estimate
    type: llm
    prompt: "Estimate the monthly cost of the proposed plan..."
---

Skill instructions body here (injected into system prompt when triggered).
```

### Routing Rules

| Has `conditions` | Has `steps` | Behavior |
|---|---|---|
| No | No | Plain invocable — LLM uses based on relevance |
| Yes | No | Reactive — injected into system prompt when conditions match |
| No | Yes | Executable task — runs structured steps |
| Yes | Yes | Reactive + structured — triggers reactively, runs steps when fired |

### Ecosystem Compatibility

- A raw **superpowers skill** (no `conditions`, no `steps`) works unchanged as invocable instructions
- An **openclaw skill** with `conditions` becomes reactive with zero modification
- A **nonclaw skill** from GitHub installs identically to a ClawHub skill

---

## Data Flow

```
User message arrives
        │
        ▼
SkillRegistry.getBehavioral(owlName)
  → skills where conditions.length > 0
  → filtered by relevant_owls
        │
        ▼
InstinctEngine.evaluate(message, behavioralSkills)
  → single LLM batch call (unchanged)
  → returns first triggered Skill or null
        │
        ├── triggered → inject skill.instructions into system prompt
        │               if skill.steps present → also run SkillExecutor
        ▼
Runtime builds system prompt
  → owl persona + DNA
  → triggered behavioral skill (if any)
  → relevant task skills (existing SkillSelector, unchanged)
        │
        ▼
LLM generates response
```

**Hot path change:** `InstinctEngine.evaluate()` accepts `Skill[]` instead of `Instinct[]`. The internal LLM call is identical. Everything else in the runtime hot path is unchanged.

---

## Installer (`src/skills/installer.ts`)

Three install sources, one interface:

```bash
stackowl skills install clawhub:git_commit
stackowl skills install github:anthropics/superpowers/skills/tdd
stackowl skills install github:user/repo/path@branch
stackowl skills install ./my-skills/cost_alarm
```

### GitHub Install

Resolves to raw GitHub URL and downloads `SKILL.md`:

```
github:user/repo/path  →  https://raw.githubusercontent.com/user/repo/main/path/SKILL.md
```

- Supports branch pinning via `@branch`
- Writes to `workspace/skills/<skill-name>/SKILL.md`

### Local Install

Copies the skill directory into `workspace/skills/`. Useful for skill authors before publishing.

### Lock File

`.clawhub/lock.json` tracks all installed skills regardless of source:

```json
{
  "skills": {
    "cost_alarm": {
      "source": "github:user/repo/skills/cost_alarm",
      "sha": "abc123",
      "installedAt": "2026-04-25T10:00:00Z"
    }
  }
}
```

Enables `stackowl skills update` and `stackowl skills remove` in future iterations.

---

## Migrator (`src/skills/migrator.ts`)

Runs once on startup, before the registry loads. Handles two cases:

**Built-in defaults** (`src/instincts/defaults/`) — handled as a one-time code rename during the refactor (move `cost-alarm/INSTINCT.md` → `src/skills/defaults/cost_alarm/SKILL.md`). Not a runtime migration.

**User custom instincts** (`workspace/instincts/`) — runtime migration on first startup:

For each `INSTINCT.md` found:
1. Ensures `workspace/skills/<name>/` directory exists
2. Copies file content to `workspace/skills/<name>/SKILL.md`
3. Logs: `[Migrator] Migrated instincts/cost-alarm/INSTINCT.md → skills/cost_alarm/SKILL.md`

After migration, the loader **stops scanning** `workspace/instincts/` entirely — INSTINCT.md files are no longer loaded to prevent double-registration. Original files are left in place for manual cleanup.

---

## Type Changes (`src/skills/types.ts`)

Add optional behavioral fields to the existing `Skill` interface:

```typescript
export interface Skill {
  // ... existing fields unchanged ...

  // Behavioral fields (optional — populated when conditions present)
  trigger?: "context" | "schedule" | "event";
  conditions?: string[];
  relevantOwls?: string[];
  priority?: "low" | "medium" | "high" | "critical";
}
```

Add a helper to `SkillRegistry`:

```typescript
getBehavioral(owlName: string): Skill[]
// Returns skills where conditions.length > 0 and relevant_owls includes owlName or "*"
```

---

## Files Changed

| File | Change |
|---|---|
| `src/skills/types.ts` | Add optional behavioral fields to `Skill` |
| `src/skills/registry.ts` | Add `getBehavioral(owlName)` method |
| `src/skills/parser.ts` | Parse `trigger`, `conditions`, `relevant_owls`, `priority` from frontmatter |
| `src/skills/engine.ts` | New file — `InstinctEngine` moved here, accepts `Skill[]` |
| `src/skills/installer.ts` | New file — GitHub + local install sources |
| `src/skills/migrator.ts` | New file — one-time INSTINCT.md → SKILL.md rename |
| `src/skills/defaults/cost_alarm/SKILL.md` | Migrated from `src/instincts/defaults/cost-alarm/INSTINCT.md` |
| `src/engine/runtime.ts` | Re-point instinct imports to `src/skills/` |
| `src/gateway/core.ts` | Re-point instinct imports to `src/skills/` |
| `src/index.ts` | Re-point instinct imports, wire migrator on startup |
| `src/instincts/` | Deleted after migration |

---

## Error Handling

- **GitHub install fails** (404, network error): log error, do not write partial files, exit non-zero
- **SKILL.md missing `name` field**: skip with warning, do not crash loader
- **Conditions present but `trigger` absent**: default to `trigger: context`
- **Migration finds a name collision**: suffix with `_migrated`, log warning
- **Behavioral skill parse error**: skip that skill, log warning, continue loading others
