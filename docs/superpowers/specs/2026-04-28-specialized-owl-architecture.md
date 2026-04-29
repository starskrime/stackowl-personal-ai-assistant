# Specialized Owl Architecture — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify all owl types under a single `SpecializedOwlRegistry`, with Noctua as the default coordinator owl, session-pinned specialist routing, and persistent memory context — no database for owl definitions, no parallel registries.

**Architecture:** One file format (`specialized_owl.md`) per owl. One registry. Noctua's persona is the engine-level base prompt injected for all owls. Specialists add domain layers on top. Session pin persists to a JSON file per user. Long-term memory uses existing SQLite pellets/digests, wired into specialist activation.

**Tech Stack:** TypeScript, gray-matter (frontmatter parsing), better-sqlite3 (memory only), Node.js fs/promises

---

## Section 1 — File Format

Every owl lives in `workspace/owls/{name}/specialized_owl.md`.

New required frontmatter field: `type: coordinator | specialist`

**Coordinator (Noctua):**
```yaml
---
name: Noctua
type: coordinator
emoji: 🦉
role: "Chief of Staff"
keywords: []
domains: []
challengeLevel: medium
verbosity: balanced
tone: direct
---
```
No body needed. Noctua's persona lives in the engine as the base prompt constant.

**Specialist:**
```yaml
---
name: HistoryMan
type: specialist
emoji: 📚
role: "History & Culture Expert"
keywords: [history, ancient, medieval, roman, empire, culture]
domains: [history, culture, archaeology]
challengeLevel: high
verbosity: balanced
tone: academic
---

Optional authored domain-specific instructions go here.
These are appended on top of the base prompt when this owl is active.
```

**`SpecializedOwlSpec` gains two new fields:**
- `type: "coordinator" | "specialist"`
- `additionalPrompt?: string` — parsed from the markdown body (empty string if no body)

---

## Section 2 — Single Registry

`SpecializedOwlRegistry` is the one registry. `OwlRegistry` is retired.

**New/updated methods on `SpecializedOwlRegistry`:**

| Method | Description |
|---|---|
| `loadAll(workspacePath)` | Loads all owls from `workspace/owls/*/specialized_owl.md` |
| `getDefault()` | Returns the coordinator owl (type: coordinator) |
| `listSpecialists()` | Returns all owls where type === "specialist" |
| `get(name)` | Exact + prefix match (unchanged) |
| `listAll()` | All owls (unchanged) |

**DNA persistence moves into `SpecializedOwlRegistry`:**
- After loading each spec, registry reads `owl_dna.json` from the same folder if it exists
- `saveDNA(owlName)` writes `owl_dna.json` back to the owl folder
- `SpecializedOwlSpec` gains optional `dna?: OwlDNA` field

---

## Section 3 — Prompt Composition at Runtime

When any owl handles a message, the system prompt is assembled in layers:

```
Layer 1 (always):    Noctua base persona — engine constant, injected for every owl
Layer 2 (specialist): Synthesized context from metadata — "You are X, role. Expertise: Y. Style: Z."
                      + additionalPrompt body text (if present in specialized_owl.md)
Layer 3 (optional):  Instincts block — constraint lines from InstinctEngine (already implemented)
```

**Noctua** receives only Layer 1. She never adds a specialist layer to herself.

**Specialist owls** receive Layer 1 + Layer 2 + Layer 3 (if instincts match).

The `RoutingCoordinator` assembles this at activation time using the `SpecializedOwlSpec` fields. The `buildConstraintBlock` from `InstinctEngine` handles Layer 3 (unchanged).

---

## Section 4 — Session-Pinned Routing

`Session` gains one field: `activeOwlName?: string`

**`RoutingCoordinator.resolve()` updated logic (in order):**

1. Check `session.activeOwlName` — if set, load that owl from registry, return immediately (skip all routing)
2. Check for `@mention` — if found, activate that specialist, write pin to session
3. Run `SecretaryRouter` (LLM classify → keyword fallback) — if specialist found, activate + write pin
4. No match → use coordinator (Noctua), no pin written

**Unpin rules:**
- User sends `@noctua` explicitly → clear `session.activeOwlName`
- User sends `@otherSpecialist` → replace pin with new specialist name

Session `activeOwlName` is written to both:
- In-memory `Session` object (for current request)
- `workspace/sessions/{userId}.json` (for persistence across restarts)

---

## Section 5 — Memory & Session Persistence

### Pin State (file-based, routing concern)

`workspace/sessions/{userId}.json`:
```json
{
  "activeOwlName": "historyMan",
  "taskSummary": "Researching Byzantine trade routes",
  "pinnedAt": "2026-04-23T10:00:00Z"
}
```

- Written when owl is pinned or unpinned
- Read on first message from a user to restore active owl
- If file missing: no pin, coordinator handles

### Long-Term Memory (SQLite, existing machinery wired to specialists)

When a specialist is activated, `RoutingCoordinator` triggers two retrievals:

1. **PelletStore** — semantic search over pellets tagged with that owl's name → "what did we work on before?"
2. **ConversationDigest** — most recent digest for that user → "why were we doing this?"

Retrieved context injected as `## Past Context` block into `engineCtx.specialistPrompt` before the response is generated. Capped at a configurable `maxContextPellets` (default: 3) to control token budget.

This gives every specialist owl persistent memory: it knows the task, knows the history, and can answer questions from 5 days ago via semantic pellet retrieval.

---

## Section 6 — Type Cleanup

`RoutingDecision` in `secretary.ts` changes:

**Before:**
```typescript
| { type: "specialist"; owl: SpecializedOwl; reason: string }
// SpecializedOwl imported from memory/db.ts — wrong abstraction
```

**After:**
```typescript
| { type: "specialist"; owl: SpecializedOwlSpec; reason: string }
// SpecializedOwlSpec from owls/specialized-types.ts — correct
```

`SecretaryRouter.specToSyntheticOwl()` — deleted. It existed only to fake a db object from a spec.

`import type { SpecializedOwl } from "../memory/db.js"` — removed from `secretary.ts`.

`RoutingCoordinator` updated to use `SpecializedOwlSpec` fields directly (no unwrapping from fake db object).

---

## Section 7 — Deletions & Type Updates

| File / Symbol | Action |
|---|---|
| `src/owls/registry.ts` | Delete entire file |
| `src/owls/defaults/*/OWL.md` (7 files) | Delete — Noctua persona moves to engine constant |
| `SecretaryRouter.specToSyntheticOwl()` | Delete method |
| `OwlInstance` type in `persona.ts` | Delete — replaced by `ActiveOwl` (see below) |
| `OwlPersona` type in `persona.ts` | Delete — no longer loaded from OWL.md |
| `ctx.owlRegistry` in `GatewayContext` | Remove field |
| `SpecializedOwl` import in `secretary.ts` | Remove import |

`OwlDNA` type and DNA persistence logic — kept, moved into `SpecializedOwlRegistry`.

### ActiveOwl — replacing OwlInstance

`engineCtx.owl` is currently typed as `OwlInstance`. With `OwlInstance` retired, a new `ActiveOwl` type is introduced in `specialized-types.ts`:

```typescript
export interface ActiveOwl {
  spec: SpecializedOwlSpec;   // the loaded spec (name, role, type, keywords, etc.)
  dna: OwlDNA;                // loaded or default DNA
  systemPrompt: string;       // assembled prompt (Layer 1 + Layer 2 from Section 3)
}
```

`EngineContext.owl` type changes from `OwlInstance` to `ActiveOwl`.
`GatewayContext.owl` type changes from `OwlInstance` to `ActiveOwl`.

This is the only breaking change to the engine interface — everything that reads `ctx.owl.persona.name` switches to `ctx.owl.spec.name`.

---

## Implementation Order

1. Extend `SpecializedOwlSpec` + `parseSpecializedOwl` — add `type`, `additionalPrompt`, `dna`
2. Extend `SpecializedOwlRegistry` — add `getDefault()`, `listSpecialists()`, DNA persistence
3. Fix `RoutingDecision` type — replace `SpecializedOwl` with `SpecializedOwlSpec`
4. Remove `specToSyntheticOwl()` from `SecretaryRouter`
5. Update `RoutingCoordinator` — session pinning, prompt composition, memory injection
6. Add session state file persistence (`workspace/sessions/{userId}.json`)
7. Wire `PelletStore` + `ConversationDigest` into specialist activation
8. Delete `OwlRegistry` + `OWL.md` files
9. Update `GatewayContext` — remove `owlRegistry`, update `owl` field type
