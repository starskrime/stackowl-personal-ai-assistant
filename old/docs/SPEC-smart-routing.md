# Smart Routing — Specification

## Implementation Status

| Requirement | Status | Notes |
|---|---|---|
| **R1** Roster-based selection | ❌ BROKEN | `availableModels` never populated — CLI stops at single model, Telegram writes `fallbackChain[]` (wrong schema) |
| **R2** Cross-provider (complexity) | ❌ BROKEN | `router.ts:112` only returns `modelName`, no `providerName` — runtime can't switch providers by complexity |
| **R2** Cross-provider (failure) | ✅ WORKING | `router.ts:71-79` returns both `modelName + providerName` on 2+ failures; `runtime.ts:750-763` swaps provider correctly |
| **R3** Failure fallback | ✅ WORKING | 2+ tool failures → fallback provider/model escalation via `router.ts:71-79` |
| **R4** Configurable roster | ❌ BROKEN | CLI writes nothing to smart routing; Telegram writes `fallbackChain[]` (provider keys), router reads `availableModels[]` (objects) — completely mismatched |
| **R5** Validation enforcement | ✅ WORKING | `loader.ts:572-577` throws error, refuses to save invalid config |
| **R6** Transparent operation | ⚠️ PARTIAL | Logs show tier + model at `router.ts:111`, but provider is missing from the log line |
| **R7** Unified `/skills` command | 🟡 IN PROGRESS | Two commands exist (`/skills`, `/skill`); consolidate to `/skills` only. CLI skill install also needed. |

## Problem Statement

Smart routing is a config feature that should dynamically select the right model for each request based on task complexity. It currently **exists in the code but does not work**. The router is called but always falls back to a single default model, giving users no benefit while appearing enabled.

### Root Causes

**1. Roster is always empty** — ✅ Still accurate
The onboarding wizard asks for one provider and one model, then stops. The router checks `availableModels` — if it's empty, it returns the default model. Result: smart routing is always bypassed.

**2. Telegram UI stores data the router doesn't read** — ✅ Still accurate
The Telegram `/config` menu stores provider keys in `fallbackChain[]` (a non-standard key written via `as any` at `menu.ts:871-874`). The router reads `availableModels[]` which stays empty. Different data structures.

**3. Provider switching never happens during normal routing** — ✅ Still accurate, but partial fix exists for failure path
The router's complexity-based selection only returns `modelName` (`router.ts:112`). Cross-provider switching only triggers on failure (`router.ts:71-79`) — NOT on complexity analysis. The failure-escalation path is now correctly wired (`runtime.ts:750-763`).

**4. Validation is insufficient** — 🟡 Partially fixed
`loader.ts:572-577` now throws an error and refuses to save when `enabled=true` with empty roster. Previously it only warned and continued. This is now working correctly.

---

## Requirements

### R1 — Roster-Based Model Selection ⚠️ BROKEN
The router must maintain a **roster** of available models ordered by capability (light → strong). For each incoming request, the router must analyze the prompt and select the appropriate model from the roster without an LLM call.

> **Status:** `availableModels` is never populated. `router.ts:84-87` bypasses routing entirely when roster is empty.

### R2 — Cross-Provider Routing ⚠️ BROKEN (complexity) / ✅ WORKING (failure)
The router must be able to route to different providers. When the selected model belongs to a different provider than the current one, the runtime must be able to obtain and use that provider instance.

> **Status (complexity path):** `router.ts:112` only returns `{ modelName }` — no `providerName` field. Runtime cannot determine which provider to use.
> **Status (failure path):** ✅ Working — `router.ts:71-79` correctly returns both fields; `runtime.ts:750-763` swaps provider.

### R3 — Failure Fallback ✅ WORKING
On repeated tool failures (2+ consecutive), the system must automatically escalate to a designated fallback provider/model, independent of the complexity-based selection.

> **Status:** Fully implemented and wired. Escalation fires correctly.

### R4 — Configurable Roster ⚠️ BROKEN
Users must be able to build and maintain the model roster through both:
- CLI onboarding (first-run experience)
- Telegram `/config` menu (runtime reconfiguration)

> **Status:** CLI onboarding writes nothing to smart routing. Telegram `/config` writes to `fallbackChain[]` (provider-key array), which is a different data structure than `availableModels[]` (array of `{name, description}` objects). The two config paths are completely mismatched.

### R5 — Validation Enforcement ✅ WORKING
Smart routing must refuse to enable if the roster has fewer than 2 models. No silent fallthrough.

> **Status:** `loader.ts:572-577` throws on invalid config. `saveConfig` at line 609+ refuses to persist.

### R6 — Transparent Operation ⚠️ PARTIAL
The selected model and provider must be visible in the engine logs so users can verify the routing decisions are working.

> **Status:** `router.ts:111` logs `Tier="${tier}" → ${modelName}`. Provider name is not included in the log output.

### R7 — Unified `/skills` Command + Install Support 🟡 IN PROGRESS
Two commands currently exist: `/skills` (list skills) and `/skill` (detail/action). These must be consolidated to a single `/skills` command. Skills must be installable via both Telegram and CLI.

**Telegram command structure:**
- `/skills` — list all installed skills
- `/skills install <source>` — install from GitHub URL, local path, or ClawHub
- `/skills uninstall <name>` — remove a skill

**CLI command structure:**
- `stackowl skills install <source>` — same install sources as Telegram
- `stackowl skills list` — show installed skills
- `stackowl skills uninstall <name>` — remove a skill

**Install sources** (both Telegram and CLI):
- GitHub: `github:owner/repo` or full GitHub URL
- Local path: `./my-skill` or `/absolute/path/to/skill`
- ClawHub: `clawhub:skill-name`

> **Status:** Two commands exist in Telegram (`/skills`, `/skill`); consolidation to `/skills` with subcommands needed. CLI `stackowl skills` subcommand not yet implemented.

---

## How It Should Work

### User-Facing Behavior

**When smart routing is OFF (default):**
Every message goes to the single configured default provider and model. No change from today.

**When smart routing is ON:**
The system maintains a ranked list of 2+ models spanning capability levels. When a message arrives:

1. The system analyzes the message content — not the sender, not the conversation history
2. Simple messages (greetings, one-liners, casual chat) → lightest/cheapest model
3. Standard messages (questions, requests, explanations) → mid-tier model
4. Complex messages (code writing, multi-step analysis, research, detailed explanations) → strongest model

**Failure escalation:**
If a tool call fails 2+ times in a row, the system silently switches to the fallback provider for the remainder of that task — the user does not need to intervene or notice.

### Configuration UX

**Onboarding:**
During first-run setup, after selecting the primary provider/model, the user is asked:
- "Do you want smart routing?" (yes/no)
- If yes: select 2+ additional models from available providers to form a roster
- The first selected model = lightest, last = strongest, middle = standard tier

**Telegram `/config` menu:**
Inside the config menu, a "Smart Routing" section replaces the current "Fallback Chain":
- Toggle on/off (blocked if fewer than 2 models in roster)
- Roster editor: view ordered list, add models, remove models, reorder
- Each entry shows: `provider · model name`
- Fallback provider/model fields for failure escalation

### Routing Decision Visibility

In debug output mode, each request prints the routing decision:
```
[ModelRouter] Tier="heavy" → anthropic / claude-sonnet-4-6
[ModelRouter] Tier="simple" → ollama / llama3.2
```

---

## Out of Scope

- LLM-based task classification (must stay zero-latency, heuristic only)
- Per-role routing (chat vs synthesis vs parliament having separate rosters)
- Cost accounting or budget-aware routing
- Automatically pulling models from Ollama (done via existing detection)
