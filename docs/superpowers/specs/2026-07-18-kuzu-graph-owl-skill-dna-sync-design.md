# Graph-Backed Owl/Skill/DNA Ownership — Design (Dynamic-Injection Arc, Sub-project 1 of 4)

> Give the Kuzu graph (`memory/kuzu_adapter.py`, today Fact/Entity-only) two new node
> types — `Owl` and `Skill`/`Trait` — connected by `OWNS` and `HAS_TRAIT` edges, kept in
> sync with SQLite (the authoritative store, unchanged) via best-effort inline writes at
> the two existing mutation sites, backstopped by a weekly reconciliation sweep. This is
> deliberately narrow: schema + sync only. No relevance/similarity edges yet — those are
> sub-project 2's concern, once we've decided what actually powers push/pull delivery.

**Status:** Design approved (2026-07-18); pending spec re-review
**Builds on:** [[project_dynamic_context_window_probe_fixed]] and the same-session audit of
`pipeline/steps/assemble.py`'s flat system-prompt concatenation that motivated this arc —
see the artifact "Context Window Flow — StackOwl" published this session. Reuses the
existing `KuzuSyncJobHandler` (`memory/kuzu_sync_handler.py`) as the pattern reference for
idempotent, self-healing sync, without touching it.
**Dynamic-injection arc:** **1 (this)** graph schema + sync → 2 push/pull hybrid delivery
mechanism (agent-pull tool + framework-push for high-confidence matches) → 3 DNA directive
text generated from learned traits instead of picked from ~9 fixed sentences → 4 one
combined token budget across all dynamic content instead of per-block caps.

---

## 1. Problem & approach

`assemble.py` builds `system_prompt` as a flat, undifferentiated join of base charter,
capability notes, persona/DNA text, owl list, skills, and memory recall — no structural
boundary between "always-present minimal core" and "dynamically relevant content" (traced
this session, `assemble.py:305-308`). The user's direction: system prompt should carry
*direction* and an *index* of what's available (skills, DNA, owls), not everything's full
content up front — modeled on how Hermes Agent / DeerFlow separate durable operating rules
from on-demand-loaded skills, and DeerFlow specifically: "a skill shows up when the current
task actually needs it," not dumped all at once.

Before that delivery mechanism can exist (sub-project 2), the two things it needs to reason
about — skill ownership and DNA trait state — must be queryable as a *graph*, since the
whole point is to let relationships (which skill mattered when which fact/entity came up,
which trait gates which directive) drive relevance, something plain embedding-cosine can't
express. Today neither `owls/*.py` nor `skills/*.py` reference the graph at all (confirmed
by trace — zero existing connection).

**Decisions (user-approved this session):**
1. Kuzu becomes a **derived index** — SQLite (`owl_dna`, `skills`, `skill_ownership`
   tables) stays authoritative. No migration of existing reads/writes.
2. Sync is **event-driven inline**, not a new nightly batch phase — skill/DNA mutations
   are far less frequent than the fact-consolidation traffic the existing
   `KuzuSyncJobHandler` handles, so a dedicated batch phase would be over-engineering here.
3. Best-effort only, no synchronous retry — a failed inline sync logs a warning and never
   blocks or fails the real (SQLite) mutation.
4. A weekly reconciliation sweep backstops the above, so an extended Kuzu outage (or a bug)
   can't let the derived index drift forever.
5. Scope stops at ownership/state mirroring. Relevance-traversal edges (skill similarity,
   skill-to-entity co-occurrence) are explicitly deferred to sub-project 2.

---

## 2. Architecture

Two new node types alongside the existing `Fact`/`Entity`:

- **`Owl`** — one node per owl (`name` key). Anchors ownership; lets a future query
  traverse "from this owl, what do they own/have."
- **`Skill`** — one node per skill, keyed by `(owner_id, name)` — matching
  `skill_ownership`'s own columns exactly (its PK is `(owner_id, owl_name, skill_name)`),
  so syncing an ownership row never needs a join through `skills.skill_id` (the table's
  own surrogate PK, used elsewhere for `success_rate`/embeddings but not needed as the
  graph key). `skill_id` is stored as a plain property for cross-reference, not the key.
- **`Trait`** — one node per `(owl_name, trait_name)` pair. `owl_dna` is ONE ROW PER OWL
  with each trait as its own column (`challenge_level`, `verbosity`, `curiosity`,
  `formality`, `creativity`, `precision`, `completion_drive`) — the sync code iterates
  those 7 columns per row, it does not read from a per-trait table. Each `Trait` node
  carries the current `value` as a property (overwritten on each sync, not
  versioned/history — SQLite's `dna_checkpoints` table already owns mutation history if
  that's ever needed).

Edges:
- **`OWNS`** (`Owl` → `Skill`) — mirrors a `skill_ownership` row.
- **`HAS_TRAIT`** (`Owl` → `Trait`) — mirrors one `owl_dna` column value.

No `Skill`↔`Skill`, `Skill`↔`Entity`, or `Trait`↔`Skill` edges yet — deliberately deferred
(see §7).

---

## 3. Sync mechanism

**Inline, at the two existing mutation sites — best-effort, never blocking:**

- `owls/skill_ownership.py:attach_skill_to_owl` — after the SQLite append succeeds, call
  `KuzuAdapter.upsert_skill_node(...)` + `link_owl_owns_skill(...)` inside a
  try/except that logs (`log.memory.warning`) and swallows any exception. A skill
  *removal* path (if/when one exists) gets the symmetric best-effort delete.
- `owls/evolution.py` — wherever `EvolutionCoordinator` commits a new trait value to
  `owl_dna` (post-attribution, post-clamp, post-shadow-validation), the same
  best-effort call syncs that one `Trait` node.

**Weekly reconciliation — a new lightweight `JobHandler`, not a dream_worker phase:**

`GraphReconciliationHandler`, registered and seeded exactly like the existing maintenance
sweeps (`browser_recycle`, `profile_backup`, `knowledge_prune` in `scheduler/assembly.py`) —
own cadence (`daily@HH:MM`-shaped or a weekly cron), own registration, no coupling to
`dream_worker`'s fact-consolidation concern. Each run:
1. Reads the full current `(owl, skill)` ownership set and `(owl, trait, value)` set from
   SQLite.
2. Reads the graph's current `Owl`/`Skill`/`Trait` nodes+edges.
3. Diffs and backfills anything missing (same upsert/link calls the inline hooks use) —
   and drops graph nodes/edges for owls/skills that no longer exist in SQLite, so the
   derived index doesn't accumulate stale entries either.
4. Per-item try/except (one bad row doesn't stop the sweep), mirroring
   `retry_sweep`/`objective_driver`'s existing per-item isolation pattern.

---

## 4. Error handling

Every inline sync call and every reconciliation-sweep item follows the same rule already
established elsewhere in this codebase for non-critical side effects (e.g. scheduler's
`_notify_failure`): try/except, log a warning with context, never raise, never block the
real operation. `KuzuAdapter`'s own upserts are already documented as non-atomic across a
crash (`kuzu_adapter.py`, "F067-followup") — that's an accepted, pre-existing property this
design doesn't change; the reconciliation sweep is the mitigation for it, not a new risk.

---

## 5. Implementation surface

| File | Change |
|---|---|
| `memory/kuzu_adapter.py` | + `upsert_owl_node`, `upsert_skill_node`, `upsert_trait_node`, `link_owl_owns_skill`, `link_owl_has_trait` (+ symmetric delete methods) |
| `memory/kuzu_helpers.py` | + DDL for `Owl`/`Skill`/`Trait` node tables and `OWNS`/`HAS_TRAIT` edge tables in `sync_create_schema` |
| `owls/skill_ownership.py` | `attach_skill_to_owl` (and the removal path, if one exists) gets a best-effort post-write sync call |
| `owls/evolution.py` | `EvolutionCoordinator`'s trait-commit path gets a best-effort post-write sync call |
| `scheduler/handlers/graph_reconciliation.py` (new) | `GraphReconciliationHandler` — diff + backfill + prune, per-item isolated |
| `scheduler/assembly.py` | register + seed the new handler (weekly), same pattern as `browser_recycle`/`profile_backup` |

---

## 6. Testing

- Unit tests for the 5 new `KuzuAdapter` methods, mirroring the existing Fact/Entity
  adapter test structure.
- Unit tests at both inline-hook sites: mock `KuzuAdapter` to raise, assert the SQLite
  write still succeeds and a warning was logged (never an exception propagating).
- `GraphReconciliationHandler` unit test: seed SQLite with a full owl/skill/trait set,
  seed Kuzu with a deliberately incomplete/stale subset, run the handler, assert the graph
  matches SQLite afterward (including that stale entries were pruned).

---

## 7. Cuts / deferred (tracked)

| Item | Why | Where |
|---|---|---|
| Skill↔Skill / Skill↔Entity relevance edges | This is schema+sync only; relevance-traversal logic belongs to the delivery mechanism, not this project | sub-project 2 |
| Generalized multi-source sync driver (one shared loop instead of near-duplicate handlers) | Considered (approach B) against mirroring the existing pattern exactly (approach A) and event-driven inline (approach C, chosen) — inline sync for these two low-frequency sources needs no batch-loop abstraction at all | rejected this round, in favor of C |
| Kuzu as authoritative store (replacing SQLite) | Higher risk, touches every owl/skill read/write path for no benefit this arc needs | rejected — derived index only |
| Versioned/historical trait values in the graph | SQLite's `dna_checkpoints` already owns mutation history | not duplicated here |
