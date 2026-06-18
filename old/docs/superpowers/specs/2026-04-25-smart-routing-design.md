# Smart Routing Fix — Implementation Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make smart routing actually work end-to-end — fix the config schema, router, CLI onboarding, and Telegram menu so users can configure and use model-tier routing.

**Architecture:** Schema-first fix cascades through four layers: (1) config type updated, (2) router returns providerName on complexity path, (3) CLI onboarding gains a smart routing step, (4) Telegram menu replaces fallbackChain with a proper roster editor.

**Tech Stack:** TypeScript, grammY (Telegram), chalk + raw TTY (CLI onboarding), existing ModelLoader/ProviderRegistry

---

## Section 1 — Config Schema

`availableModels` type changes from `{ name: string; description: string }[]` to:

```typescript
availableModels: Array<{
  modelName: string;
  providerName: string;
  description?: string;
}>
```

`fallbackProvider` and `fallbackModel` remain as separate top-level fields on `smartRouting` — they represent the failure-escalation target, not a roster position.

Default config and validation logic updated to match. Validation rule unchanged: require 2+ entries when `enabled: true`.

**Files:** `src/config/loader.ts`

---

## Section 2 — Router Fix (R2 + R6)

Two surgical changes in `router.ts`:

**Complexity path** — currently returns only `modelName`. Fix to return both fields:
```typescript
return { modelName: selected.modelName, providerName: selected.providerName };
```

**Log line** — currently omits provider. Fix:
```typescript
log.engine.info(`[ModelRouter] Tier="${tier}" → ${selected.providerName} / ${selected.modelName}`);
```

**Files:** `src/engine/router.ts`

---

## Section 3 — CLI Onboarding (R1 + R4)

After `sectionProvider` completes, a new substep runs inside the same screen flow:

1. Ask **"Enable smart routing?"** (yes/no selector)
2. If yes — loop:
   - Pick a **provider** from all entries discovered in `src/models/` (same list the registry uses)
   - Pick a **model** from that provider's `availableModels`
   - Entry added to roster as `{ modelName, providerName }`
   - Show current roster, offer **"Add another"** or **"Done"**
   - **"Done"** only selectable when roster has 2+ entries
3. Writes populated `smartRouting.availableModels` to onboarding result

If user says no — `smartRouting.enabled` stays `false`, `availableModels` stays `[]`.

**Files:** `src/cli/onboarding.ts`, `src/cli/onboarding-flow.ts`

---

## Section 4 — Telegram Menu (R4)

Replaces the current `fallbackChain` section in `menu.ts` with a Smart Routing screen.

**Main view:**
- Toggle on/off (blocked if roster < 2 entries)
- Ordered roster list — each row: `[↑] [↓] [✕]  providerName · modelName`
- `[+ Add Model]` button at the bottom
- Separate `[Fallback Provider]` and `[Fallback Model]` fields for failure escalation

**Add flow:**
- Provider picker (from `src/models/` keys) → model picker (from that provider's `availableModels`) → appends `{ modelName, providerName }` to roster

**Reorder:**
- `[↑]` / `[↓]` shift the entry one position; disabled at list boundaries
- Order determines tier assignment: first = lightest, last = heaviest

**Data written to:** `config.smartRouting.availableModels` — `fallbackChain` key removed entirely.

**Files:** `src/gateway/adapters/telegram-config/menu.ts`

---

## Out of Scope

- LLM-based task classification
- Per-role routing (separate rosters for chat vs parliament)
- Cost accounting or budget-aware routing
- Skills command unification (R7) — separate plan
