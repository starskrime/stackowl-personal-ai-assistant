# DNA-Evolution Completion — Design (Phase-2 Story C)

> Complete the owl persona-evolution loop so a personality **grows but stays recognizably itself
> and is recoverable**: (1) re-center the evolution envelope on the owl's AUTHORED DNA instead of a
> fixed neutral 0.5; (2) a `DnaDefaults` holder killing the scattered `0.5`/trait-list duplication;
> (3) Schmitt-trigger hysteresis on the DNA→prompt directives so a trait hovering at a threshold
> stops flickering behavior; (4) `/owls reset-dna` to revert an owl's evolved DNA to its authored
> baseline. Pressure-tested by party-mode (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design approved (2026-06-06); pending spec re-review
**Builds on:** S4 persona-evolution ([[project_owl_builder_arc]] — `bound_dna` governor, `apply_dna_overlay`, `DnaHydrator`, `EvolutionCoordinator`); the in-memory-singleton + fail-safe patterns from Story B (`FOCUS_TRACKER`) and the boot-load pattern (`hydrate_dna`).
**Phase-2 arc:** A owl_build → B skill-tiering → **C (this)** → D delegation hardening → E memory-promotion governance.

---

## 1. Problem & approach

Owls have `OwlDNA` — 6 mutable traits in [0,1] (`challenge_level, verbosity, curiosity, formality, creativity, precision`, default 0.5). They evolve: `EvolutionCoordinator` proposes new DNA → `bound_dna(current, proposed)` rate-limits (±`MAX_DELTA` 0.05), clamps into a **fixed** envelope `[DNA_NEUTRAL−ENVELOPE, DNA_NEUTRAL+ENVELOPE]` = `[0.2, 0.8]`, applies safety floors (`TRAIT_FLOOR` 0.3 on challenge_level/precision); persists to `owl_dna`; overlays the registry at boot (`hydrate_dna`, which **overwrites** the authored manifest DNA). A stateless `DNAPromptInjector.inject(manifest, dna)` emits prompt directives at thresholds (HIGH >0.7, LOW <0.3).

Four gaps:
- **Envelope centered on 0.5, not the owl.** Evolution drags every owl toward the beige mean → identity collapse. An owl authored as a sharp challenger gets pulled to neutral. **Fix:** anchor the envelope on the owl's authored DNA → it explores *around its identity*.
- **Authored DNA isn't durably stored** (only in YAML, overwritten at boot). The keystone — both #1 and #4 need it.
- **The injector flickers.** A trait oscillating around 0.7 flips the directive on/off every batch. **Fix:** Schmitt hysteresis (enter 0.70 / exit 0.60).
- **No way back.** Once evolved, an owl can't be reverted to authored. **Fix:** `/owls reset-dna`.
- Plus a DRY debt: `0.5` duplicated in 8+ sites, the trait-name list in 6+.

**Decisions (user-approved):** authored = **YAML-tracking** (captured to a durable store at boot *before* hydrate; editing the YAML re-authors); **floor defers to author** (`min(TRAIT_FLOOR, anchor)`); **in-memory** Schmitt latch (no table); **DnaDefaults** centralizes neutral + canonical trait list; one migration `0051` (authored table only).

---

## 2. Architecture (5 units + one migration)

| Unit | File | Responsibility |
|---|---|---|
| `DnaDefaults` | `owls/dna_defaults.py` (new) | `NEUTRAL = 0.5`; `TRAIT_NAMES: tuple[str,...]` (canonical 6, same order as `_MUTABLE_TRAITS`). The single source for both. |
| Authored store | `owls/dna_authored.py` (new) + migration `0051` | `capture_one_authored(name, dna, db)` (idempotent, coerced, skip-don't-clobber); `capture_authored_dna(registry, db)` (boot loop); `read_authored_dna(name, db) -> OwlDNA | None`. |
| Governor anchor | `owls/dna_governor.py` (modify) | `bound_dna(current, proposed, anchor)` — envelope `[anchor±ENVELOPE]`, floor `min(TRAIT_FLOOR, anchor)`. |
| Schmitt latch | `owls/directive_latch.py` (new) + `dna_injector.py` (modify) | in-memory per-`(owl, trait)` latch, lazy-seeded, fail-open; injector emits directives from the latch. |
| reset-dna | `commands/owls_command.py` (modify) + shared upsert | `/owls reset-dna <name> YES` → authored, live-refresh, clear latch; `/owls dna` shows current-vs-authored. |
| Shared upsert | `owls/dna_storage.py` (modify) | `upsert_owl_dna(name, dna, db, *, table)` reused by EvolutionCoordinator persist + authored capture + reset (kills 3-way dup). |

Boot order (`startup/orchestrator.py`): `from_settings` → `register_builtin_personas` → **`capture_authored_dna`** (new, captures manifest.dna *before* it's overwritten) → `hydrate_dna` (overlays evolved) → `revalidate_agent_owls`.

---

## 3. DnaDefaults (DRY) — pure no-op refactor

`owls/dna_defaults.py`:
```python
NEUTRAL: float = 0.5
TRAIT_NAMES: tuple[str, ...] = (
    "challenge_level", "verbosity", "curiosity", "formality", "creativity", "precision",
)  # MUST equal dna.py:_MUTABLE_TRAITS exactly, in the SAME order
```
Repoint **Python** dup sites to it: `dna.py:_MUTABLE_TRAITS`, `evolution.py:DeltaValidator._TRAITS`, `dna_storage.py:_DNA_FIELDS`, and `evolution_limits.py:DNA_NEUTRAL = dna_defaults.NEUTRAL`. The neutral `0.5` literals in `owls_helpers.py`, `constellation_helpers.py`, `dna_attribution.py`, and the `dna.py` Field defaults reference `DnaDefaults.NEUTRAL`. **SQL DDL stays literal** (no codegen into SQL) — guarded by a test, not templating.

**No-behavior-change proof (Murat — lands BEFORE any old list is deleted):**
- A golden test captures each former list as a frozen literal and asserts `TRAIT_NAMES == <each old list>` **in order** (`assert list == list`, never `set`), and `len(TRAIT_NAMES) == 6`.
- Assert the SQL column order matches: parse the column list out of `_SELECT_ALL_DNA`/`_UPSERT_DNA_SQL` and assert it equals `TRAIT_NAMES` (the positional-unpacking transposition trap).
- A round-trip test with **distinct per-trait values** (`0.11, 0.22, 0.33, 0.44, 0.55, 0.66` — never all-0.5, which masks transposition): write → read → each trait keeps its own value.
- The refactor commits show **only delegation** to `DnaDefaults`, no value changes.

---

## 4. Authored store + capture (the keystone)

**Migration `0051_owl_dna_authored.sql`** — table mirroring `owl_dna` (same shape so one upsert helper serves both):
```
owl_dna_authored(id PK, owl_name TEXT, <6 trait REAL DEFAULT 0.5>, owner_id TEXT DEFAULT 'principal-default', updated_at, UNIQUE(owner_id, owl_name))
```
(Follow the migration-runner gotchas: no semicolons inside SQL comments; no non-constant DEFAULT; `'principal-default'` must equal `DEFAULT_PRINCIPAL_ID`.)

`owls/dna_authored.py`:
- `capture_one_authored(name, dna, db)` — idempotent upsert via the shared `upsert_owl_dna(..., table="owl_dna_authored")`. **Coerce** the DNA through the existing `_coerce_dna` (imported from `dna_hydrator` — a NaN authored value would poison the clamp math, since NaN comparisons are always false). **Skip-don't-clobber:** if the DNA is missing/empty/uncoercible, leave any existing row untouched and `log.engine.warning` (never overwrite a good authored row with neutral garbage — that silently re-widens the envelope to `[0.2,0.8]`). Fail-safe per call.
- `capture_authored_dna(registry, db)` — boot loop: for each manifest owl, `capture_one_authored(name, manifest.dna, db)`. Per-owl try/except (one bad owl never aborts boot). Idempotent (re-running yields identical rows).
- `read_authored_dna(name, db) -> OwlDNA | None` — coerced read; `None` if no row.

**Capture seams:** (A) boot, *before* hydrate (YAML reconciler — a YAML edit re-authors on next boot). (B) creation — `owl_build` create + `/owls add` call `capture_one_authored` for the new owl so a same-session owl evolves against its real anchor, not neutral. Both go through the one helper.

**Boot ordering invariant:** authored capture commits before the governor or hydrator can read it (tested). Orphan authored rows (owl removed) are inert; a re-created same-name owl's capture overwrites with the new manifest's DNA (tested) — no stale-anchor inheritance.

---

## 5. Authored-anchor envelope (governor)

`bound_dna(current, proposed, anchor)` — per trait `t` in `TRAIT_NAMES`:
```
moved = current.t + clamp(proposed.t − current.t, −MAX_DELTA, +MAX_DELTA)   # rate cap (unchanged)
lo, hi = clamp(anchor.t − ENVELOPE, 0, 1), clamp(anchor.t + ENVELOPE, 0, 1)  # envelope re-centered on anchor
moved = clamp(moved, lo, hi)
if t in FLOOR_TRAITS:                                                          # author-intent-wins floor
    moved = max(moved, min(TRAIT_FLOOR, anchor.t))
```
- The obsolete "envelope must straddle 0.5 / wider than the deadband" framing is **retired** (Winston): it was a property of the neutral default, not a global invariant. A high-authored owl (e.g. challenge_level 0.78 → band `[0.48, 1.0]`) legitimately expresses HIGH from boot — authored intent is the signal; you don't make a deliberately aggressive owl earn its aggression through drift. Asymmetry-after-clamp is intended and documented.
- **Floor defers to author** (user decision): `min(TRAIT_FLOOR, anchor)` means evolution can't drift a floored trait below where it was authored or below 0.3 (whichever is lower), but never overrides a deliberate sub-floor authoring. (Murat's dissent — "floor is a safety rail, YAML is untrusted" — recorded; the single-user-owns-the-YAML model + "never override the author" won.)
- Caller (`EvolutionCoordinator._evolve_one`) fetches `anchor = read_authored_dna(name, db)`; if `None`, falls back to a neutral `OwlDNA()` (logged) so evolution still functions for an owl with no authored row. `bound_dna` stays **pure** (anchor passed in, never reads the DB).

---

## 6. Schmitt hysteresis (in-memory latch)

`owls/directive_latch.py` — module singleton `DIRECTIVE_LATCH` (mirrors `FOCUS_TRACKER`: thread-safe `Lock`, fail-safe, bounded keys). Per `(owl_name, trait)`: `{high_on: bool, low_on: bool}`.

Thresholds (Dr. Quinn — the gap must exceed MAX_DELTA so one batch can't cross it; `enter − exit = 0.10 = 2×MAX_DELTA`):
- HIGH: enter when `value ≥ 0.70`, exit when `value < 0.60`, else **hold** (keep prior).
- LOW: enter when `value ≤ 0.30`, exit when `value > 0.40`, else hold.

`update(owl, trait, value) -> (high_on, low_on)`:
- **Lazy seed** (no prior entry, e.g. first call after a (re)boot): `high_on = value ≥ 0.70`, `low_on = value ≤ 0.30` — i.e. cold-start behaves like the old binary threshold, then hysteresis governs.
- Otherwise apply enter/exit; the hold zone keeps the prior latch (3-zone-aware latch, 2-zone binary output — graded directives deferred to Phase 2).
- **Audit** every flip: `log.engine.info("[owls] directive_latch.flip", {owl, trait, direction, old, new, value})` so "why did this owl get HIGH-challenge at dna=0.66?" is answerable from logs (Murat's auditability requirement).
- **Fail-open:** any latch error → the injector falls back to the plain stateless threshold (`value > 0.7` / `value < 0.3`) and logs once. The latch must never crash `assemble`.
- `reset_owl(owl_name)` — drop the owl's entries (used by reset-dna).

`dna_injector.py`: `DNAPromptInjector.inject(manifest, dna)` calls `DIRECTIVE_LATCH.update(manifest.name, trait, value)` per directive trait and emits the HIGH/LOW directive from the latch booleans (not raw thresholds). `formality` (in both HIGH and LOW tables) is keyed per-direction, so its two directives latch independently. assemble's call site (`persona = _injector.inject(manifest, manifest.dna)`) is unchanged in shape.

---

## 7. `/owls reset-dna` + legibility

**`_reset_dna(rest)`** (mirror `_remove`'s `YES` confirmation):
1. Parse `<name> YES`; without `YES` → return a confirmation prompt (no destructive action).
2. `authored = await read_authored_dna(name, db)`; `None` → `"no authored baseline recorded for '<name>'"`.
3. `manifest = registry.get(name)` (guard exists; owner-scoped — a user can only reset an owl they own; cross-owner → denied + audited).
4. `await upsert_owl_dna(name, authored, db, table="owl_dna")` — reset evolved → authored (written verbatim; under author-intent-wins, authored is the legitimate value, no extra flooring).
5. `apply_dna_overlay(registry, name, authored)` — **live registry refresh** (the running owl changes immediately, not after reboot).
6. `DIRECTIVE_LATCH.reset_owl(name)` — clear the latch so directives recompute fresh from the authored values (Dr. Quinn — a lingering latch = numbers say one persona, behavior expresses another).
7. **Keep** `dna_checkpoints` (audit trail of what was reset from — deleting history destroys investigability).
- Secretary/builtin: reset re-anchors to the builtin's authored DNA (safe — restoring authored, not a privilege change).

**Legibility (Dr. Quinn's remaining-risk mitigation, cheap win):** extend the existing `/owls dna <name>` readout to show **current vs authored** per trait (and flag traits currently expressing a latched directive). Reuses the authored store; makes the otherwise-invisible evolution legible so the user can trust it.

---

## 8. Testing (TDD; distinct boundary-adjacent values, never all-0.5)

- **DnaDefaults no-op proof** (§3) — equality-in-order vs every old list + SQL column order + len==6 + distinct-value round-trip. Lands first.
- **Authored store:** coerce (NaN/inf/out-of-range → clamp; non-finite → neutral); skip-don't-clobber a good row on broken input; idempotent (boot twice → identical); orphan/re-create-same-name → new manifest wins; boot-order (hydrate/governor sees the committed authored row).
- **Governor:** envelope re-centered on anchor (authored 0.75 → evolved clamped to `[0.45,1.0]`, NOT `[0.2,0.8]`); floor = `min(TRAIT_FLOOR, anchor)` (authored precision 0.1 → effective floor 0.1, runs at ~0.1; authored 0.5 → floor 0.3); rate cap unchanged; `bound_dna` pure (no DB).
- **Latch:** enter/exit/hold per direction; lazy-seed = old binary on cold start; `enter−exit > MAX_DELTA` (a single max-delta batch can't cross the band); fail-open → stateless threshold; flip audited; `reset_owl` clears; formality high/low independent.
- **reset-dna:** requires `YES`; no-authored → clean error; resets owl_dna→authored; live registry refreshed (assert the manifest object, not just the row); latch cleared; checkpoints retained; owner-scoping denial; secretary reset safe.
- **`/owls dna`** shows current vs authored.
- **Gateway journey (mock only the AI provider, distinct values):** author from manifest → boot upserts authored → evolve a trait and assert it's clamped inside the **anchored** envelope (not `[0.2,0.8]`) → drive the trait across 0.70 and assert the directive latches and **stays** latched while it sits in the 0.60–0.70 hold zone, then releases below 0.60 → `/owls reset-dna <name> YES` → assert DNA == authored, latch cleared, **live registry refreshed**, and a subsequent assemble emits fresh (un-latched) directives.

---

## 9. Cuts / deferred (tracked)
| Item | Why | Where |
|---|---|---|
| Persisted Schmitt latch | DNA only moves per-batch (not turn-to-turn); in-memory seeded-at-boot kills all within-session flicker; persisting only fixes a trait parked in the 5%-wide hold-gap across a restart (cosmetic) | cut (Winston, user-approved) |
| Graded directives (mild/strong) | the LLM collapses "push back somewhat" vs "strongly" into one behavior; the 3-zone latch already gives off/hold/on with binary output | Phase-2 |
| Centralizing MAX_DELTA/ENVELOPE/TRAIT_FLOOR into DnaDefaults | already single-homed in `evolution_limits.py`; only NEUTRAL + trait-list are duplicated | not needed |
| Decay (`decay_rate_per_week`) | no consumer exists today; out of scope | separate story |
| Rich evolution-history UI ("how your owl shifted and why") | the deeper legibility play; `/owls dna` current-vs-authored is the v1 slice | Phase-2 |
| Floor-as-absolute-safety-rail (Murat's dissent) | user chose author-intent-wins (`min`) | recorded; revisit if a bad-authoring incident occurs |
