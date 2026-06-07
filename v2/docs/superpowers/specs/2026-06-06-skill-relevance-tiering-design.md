# Skill-Injection Relevance-Tiering — Design (Phase-2 Story B)

> Refine the shipped skill instruction-injector (S2): instead of packing **all** owned skills in
> **manifest order** until a 4000-char cap, **relevance-rank** the owl's owned skills against the
> current user message and tier them — most-relevant get FULL instructions, next get a one-line
> summary, the rest a name-only line. Nothing an owl owns disappears; relevance picks the *tier*.
> Adds a relevance floor (budget is a ceiling, not a target), cross-turn hysteresis (stickiness),
> and owl-pinned always-FULL skills. Pressure-tested by party-mode (Winston/Murat/Dr. Quinn/Amelia).

**Status:** Design approved (2026-06-06); pending spec re-review
**Builds on:** S2 skill instruction-injection ([[project_owl_builder_arc]] — `SkillInstructionInjector`, the `<skill_reference trust="untrusted">` fence + `_neutralize`, the cached `summary` field, skill-tool coupling); the classify-step semantic recall (`skill_store.semantic_recall`, embedding registry in pipeline services).
**Phase-2 arc:** A owl_build (shipped) → **B (this)** → C DNA-evolution completion → D delegation hardening → E memory-promotion governance.

---

## 1. Problem & approach

Today (`SkillInstructionInjector.render`, `instruction_injector.py`): owned skills are injected **full-text in manifest order** until a 4000-char budget, then overflow → a name-only line. There is **no relevance signal** — skill #1 in the manifest eats the budget whether or not it's relevant to what the user just asked, and a highly-relevant skill late in the manifest can be demoted to a bare name.

The exact primitive to fix this already exists but is used elsewhere: the **classify** step embeds `state.input_text` and runs `skill_store.semantic_recall(query_embedding) -> [(Skill, sim)]` (owner-scoped cosine) to rank **non-owned** skills for a "## Relevant Skills" block, deliberately filtering owned skills *out*. Story B reuses that embedding + recall to rank the **owned** set and tier the injection.

**Decisions (user-approved):** (1) **3-tier relevance** — FULL → SUMMARY → NAME-ONLY; (2) **relevance floor** — budget is a ceiling, not a target (leave ACTIVE sparse rather than pad with marginal skills); (3) **hysteresis** — a skill ACTIVE last turn or recently `skill_view`'d is stickier; (4) **pinned skills** — owl-pinned skills always FULL; (5) **fallback** — no query/embeddings → today's manifest-order behavior, fail-open.

---

## 2. Architecture (rank in classify, render in assemble)

One relevance pass, computed where the embedding already lives. **`render` stays dumb.**

```
classify step (already has the query embedding + the owner-scoped store):
  - embeds state.input_text ONCE (existing) → query_embedding
  - existing: semantic_recall over NON-owned → "## Relevant Skills" block (unchanged)
  - NEW: score the OWNED set against query_embedding (a separate owner-scoped cosine pass —
    classify's existing recall filters owned out, so owned must be scored separately)
  - NEW: apply hysteresis bonus (SkillFocusTracker) → final owned scores
  - stash on PipelineState (immutable .evolve): owned_skill_scores: tuple[(name, score), ...] | None

assemble step (the prompt builder; NO embedder import):
  - reads manifest.skills (owned) + manifest.pinned_skills + state.owned_skill_scores
  - assign_tiers(owned, scores, pinned, *, budget, summary_floor) -> [(skill, Tier)]  (PURE helper)
  - SkillInstructionInjector.render(owl_name, tiered) -> str  (dumb: formats + security chokepoint)
  - records the resulting FULL-tier set back into SkillFocusTracker (for next turn's hysteresis)
  - fail-open: any exception → skills_block = "" (unchanged)
```

**Why classify, not assemble (Winston/Amelia):** re-embedding the same text in assemble is wasteful on the hot path; classify already holds the embedding and the store. Assemble becomes a pure consumer of a forwarded ranking. If `owned_skill_scores is None` (proactive/heartbeat with no message, or embeddings unavailable), assemble takes the **manifest-order fallback** — that absence *is* the fallback signal.

**Units (one responsibility each):**
| Unit | File | Responsibility |
|---|---|---|
| `SkillRelevanceScorer` | `skills/skill_relevance.py` (new) | score owned skills vs query embedding (owner-scoped cosine over `Skill.embedding`) + apply hysteresis bonus → `[(name, score)]` |
| `SkillFocusTracker` | `skills/skill_focus.py` (new) | session+owl-scoped in-memory recency: last-ACTIVE-turn + last-`skill_view`-turn per skill; bonus + decay |
| `assign_tiers` | pure fn in `skills/instruction_injector.py` | scores+pins+floors+budget → tier-tagged ordered list. No I/O. |
| `SkillInstructionInjector.render` | `skills/instruction_injector.py` (extend) | format a tier-tagged list; **security chokepoint** (neutralize+fence per untrusted string, every tier); hard-cap safety demote |

---

## 3. Tiering & budget (the `assign_tiers` contract)

**Tiers (Dr. Quinn's imperative framing — the labels carry the cognition):**
- **ACTIVE — apply now:** FULL instructions. Pinned skills + relevance-FULL.
- **AVAILABLE — call `skill_view <name>` before using:** one-line `summary`.
- **CATALOG — exists; `skill_view` if a task needs it:** name-only.

**Relevance floors (gate the tier; budget is the ceiling):**
- `FULL_FLOOR` (default cosine **0.40**, a module constant, tunable): score ≥ floor → eligible for ACTIVE.
- `SUMMARY_FLOOR` (default **0.20**): floor ≤ score < FULL_FLOOR → AVAILABLE.
- score < SUMMARY_FLOOR → CATALOG.
- A skill eligible for ACTIVE that doesn't fit the budget is **demoted to AVAILABLE** (not CATALOG). Budget never *promotes* below-floor skills to fill space (no padding — leave ACTIVE sparse).

**Budget — one running budget, priority order, with a reserved summary floor (Winston):**
- One budget `_DEFAULT_CAP = 4000` chars (unchanged). Consumed in strict priority order:
  `pinned-FULL → relevance-FULL (score desc) → AVAILABLE summaries → CATALOG (free, unbudgeted)`.
- **Reserve `_SUMMARY_BUDGET_FLOOR` (default 800 chars)** that the FULL tiers cannot consume, so the summary tier is never starved by a greedy ACTIVE block. FULL tiers spend against `budget − floor`; summaries spend the floor plus whatever FULL left unused. CATALOG (a name list) is unbudgeted.
- Per-skill untrusted cap stays at `_PER_SKILL_NEUTRALIZE_CAP = 600` (unchanged), applied in every tier.

**Pins (always-FULL, but subordinate to budget & trust):**
- `OwlAgentManifest.pinned_skills: tuple[str, ...] = ()` (new field). A pin is honored **only if the name is in `manifest.skills`** (owned); a non-owned/unknown pin is ignored + logged (never injects an unowned skill — Murat). Pins bypass the relevance floor + hysteresis (always ACTIVE), positioned at the **top** of ACTIVE under a "core standing skills (always apply)" sub-line, budgeted **first**.
- **Pin overflow rule:** if pinned-FULL alone exceeds budget → inject pins in manifest order until budget, then **demote remaining pins to AVAILABLE summary (never CATALOG)** and `log.engine.warn("assemble: pinned skills exceed budget", {...})` (over-pinning is a config smell the user should see, not a silent truncation). Never raise the budget dynamically.

---

## 4. Hysteresis (cross-turn stickiness — `SkillFocusTracker`)

Pure per-message ranking can drop a skill ACTIVE→CATALOG mid-task when turn-3 phrasing differs from turn-1, losing a playbook the model was mid-use on. Hysteresis makes it **easier to STAY active than to ENTER**.

- **State:** session+owl-scoped, **in-memory** (a focus heuristic, not durable data — cold-start on restart costs at most one manifest-ish turn, acceptable). Keyed by `(owl_name, session_id)`; a bounded LRU over sessions. Per skill: `last_active_turn`, `last_viewed_turn`. A monotonic `turn` counter per `(owl, session)` increments each pipeline run.
- **Bonus (applied in `SkillRelevanceScorer` before tier assignment):**
  - was-ACTIVE-recently: `+ACTIVE_BONUS (0.15) * decay^(turn − last_active_turn)`.
  - recently-`skill_view`'d (stronger, the model demonstrated active use): `+VIEW_BONUS (0.25) * decay^(turn − last_viewed_turn)`.
  - `decay = 0.5`, bonus → 0 after `FOCUS_DECAY_TURNS = 3` turns. Take the max of the two (not sum) to bound the bonus.
- **Asymmetry** is emergent: the bonus lifts a borderline skill back over `FULL_FLOOR` to *stay* ACTIVE, but a brand-new skill must clear the floor on raw relevance to *enter*.
- **Writes:** after `assign_tiers`, assemble records the FULL-tier skill names → `tracker.mark_active(owl, session, names, turn)`. The **`skill_view` tool** records `tracker.mark_viewed(owl, session, name, turn)` when the model pulls a full playbook.
- **Fail-safe:** the tracker is best-effort; any tracker error is logged and ignored (ranking proceeds without the bonus). No-session-id (proactive) → no hysteresis, pure relevance (or manifest fallback if also no query).

---

## 5. Security — the one invariant (Murat, merge-blocking)

**Trust handling is a property of the text's source, not the tier.** Every untrusted-sourced string rendered into the prompt — FULL body, AVAILABLE **summary**, *and* CATALOG **name** — passes through the **same single chokepoint** (`_neutralize` + `<skill_reference trust="untrusted">` fence + trust-mark), gated on `skill.source != "builtin"`, **never on tier or relevance**.

- **The summary is a laundering channel:** `summary` is LLM-generated from the untrusted body. The new AVAILABLE tier MUST neutralize+fence it exactly like a full body. The trust tag is **re-applied at render from the skill's current `source`** — never cached/baked into the summary.
- **Names are attacker-controlled** (an installed skill name could be `</skill_reference>…`): neutralize the name in the CATALOG tier too.
- **One render chokepoint:** a single `_render_untrusted(text) -> fenced+neutralized` function that ALL three tiers and the fallback path call. No tier hand-rolls its own untrusted formatting. (This is the regression the spec exists to prevent — a copy-pasted summary-tier formatter that skips neutralize ships a prompt-injection hole.)
- **Pins elevate relevance, never trust:** a pinned untrusted skill is still neutralized+fenced; a pin does not promote `source` to builtin, and cannot pin an unowned skill.
- **Fail-open ≠ fail-trust-open:** the no-embedder fallback disables *ranking only*; it must produce a byte-identical fence/neutralize to today's manifest-order injection (only tier assignment differs: all → FULL-until-budget). A broken embedder is a *likely* production state on this box, so the fallback is a primary path, tested as such.
- **Relevance-gaming** (an untrusted owned skill stuffing its embedding-source to always rank #1 and claim the FULL tier every turn): LOW-severity, single-user/single-trust-domain, accepted — *because the fence holds in every tier.* The whole threat reduction rests on invariant #1 being airtight.

---

## 6. Double-altitude coherence

classify already filters owned skills OUT of its "## Relevant Skills" (non-owned) block. Story B adds owned-skill ranking in the same step. The two are **one partition of one query**: classify scores the owl's skill universe against one embedding → `ranked_unowned` (stays in the classify block, unchanged) and `owned_skill_scores` (forwarded for the 3-tier injection). The disjointness invariant (`owned ∩ relevant-block = ∅`) is preserved and now provably so (one partition, not two filtering passes). No second embedding, no second drift source.

---

## 7. Fallback (no relevance signal)

`owned_skill_scores is None` on PipelineState (no user message — proactive/heartbeat; or `embedding_registry is None`; or embed raised in classify, caught+logged) → assemble calls `render` with **manifest-order, all-FULL-until-budget** tiering = today's exact behavior, still fully fenced/neutralized. Zero regression on the no-query path.

---

## 8. Behavioral framing (Dr. Quinn — spec requirements, not nice-to-haves)

- Per-tier **imperative headers**: "## ACTIVE SKILLS — apply these now", "## AVAILABLE — call `skill_view <name>` to load before using", "## CATALOG — exists; `skill_view` if needed".
- Pins under an ACTIVE sub-line "core standing skills (always apply)" at the top.
- The AVAILABLE header is an **instruction** ("call skill_view before using"), co-locating the fetch affordance with the temptation to act prematurely from a summary.
- **Deferred to Phase-2 (not this story):** rewriting the `summary` *generation* prompt to be recognizer/trigger-style ("Use when task involves X; full procedure via skill_view") rather than capability-style. v1 reuses existing cached summaries; the imperative AVAILABLE header mitigates premature action. Tracked in the backlog.

---

## 9. Implementation surface (smallest-correct, Amelia)

| File | Change |
|---|---|
| `owls/manifest.py` | + `pinned_skills: tuple[str, ...] = ()` (additive, default empty; yaml round-trips as list; backward-compatible — old manifests load with `()`; no DB migration — manifests are yaml). |
| `pipeline/state.py` | + `owned_skill_scores: tuple[tuple[str, float], ...] | None = None` (immutable `.evolve`). |
| `skills/skill_focus.py` (new) | `SkillFocusTracker` (in-memory, session+owl-scoped, bounded; `mark_active`/`mark_viewed`/`bonus_for`; decay). |
| `skills/skill_relevance.py` (new) | `SkillRelevanceScorer.score_owned(owned_skills, query_embedding, tracker, owl, session, turn) -> [(name, score)]` (owner-scoped cosine over `Skill.embedding` + hysteresis bonus). |
| `pipeline/steps/classify.py` | after the existing recall, score owned skills + stash `owned_skill_scores` on state. Existing non-owned block untouched. |
| `skills/instruction_injector.py` | + pure `assign_tiers(...)`; extend `render` to a tier-tagged input + the shared `_render_untrusted` chokepoint used by all 3 tiers; hard-cap safety demote. |
| `pipeline/steps/assemble.py` | call `assign_tiers` + `render` with the forwarded scores; record FULL set into the tracker; fail-open unchanged. |
| `tools/.../skill_view` | record `tracker.mark_viewed(...)` when a full playbook is pulled. |

The injector must NOT import the embedder/store — ranking lives upstream. `assign_tiers` is pure (testable with fixed scores, no live embedder).

---

## 10. Testing (TDD; mock the AI provider; deterministic fake scores)

The block is now message-dependent — **test invariants, not bytes.**
- **`assign_tiers` unit:** fixed scores → correct FULL/SUMMARY/CATALOG buckets; relevance floors honored (below-floor never promoted to FULL even with free budget); pins always FULL; pin-overflow → manifest-order pins then demote-to-summary + warn; reserved summary floor not starved; fallback (scores=None) → manifest-order all-FULL-until-budget.
- **`render` unit:** FULL emits full text; SUMMARY emits summary **still fenced+neutralized** for untrusted; CATALOG emits name **neutralized** for untrusted; builtin stays plain in every tier; oversized FULL → hard-cap safety demote.
- **`SkillFocusTracker` unit:** active/view bonus applied, decays to 0 after 3 turns, max-not-sum, session+owl-scoped isolation, bounded eviction, fail-safe.
- **`SkillRelevanceScorer` unit:** cosine ranking (fake embeddings) + bonus integration; no-embedding skills sink to CATALOG.
- **Gateway journeys (`tests/journeys/`, mock only the AI provider, deterministic injected scores):**
  - **J-sec (merge-blocking):** one malicious untrusted owned skill whose body, summary, AND name each carry a breakout payload (`</skill_reference>`, markdown header, "ignore previous instructions"). Force it into FULL (high score), then SUMMARY (mid score), then CATALOG (low score). Assert in all three: no unescaped `<`/`>`, `trust="untrusted"` present, payload neutralized.
  - **J-pin:** a pinned skill stays FULL even when a higher-relevance non-pinned skill exists; a pinned *unowned* name never injects.
  - **J-fallback:** `embedding_registry=None` and embed-raises both yield a safe fenced manifest-order block (fence present, not empty).
  - **J-hysteresis:** skill ranks FULL on turn 1; on turn 2 with an off-topic message it stays ACTIVE via the bonus; after `FOCUS_DECAY_TURNS` off-topic turns it falls to CATALOG.

---

## 11. Cuts / deferred (tracked)
| Item | Why | Where |
|---|---|---|
| Per-tier sub-budgets | one running budget + one summary floor is simpler and wastes no space | cut (by design) |
| Per-skill relevance-threshold *config* | the tier floors ARE the threshold; a config knob re-introduces "disappearance" | cut |
| Recognizer-style `summary` regeneration | improves the AVAILABLE tier but touches summary generation (assembly.py); v1 reuses existing summaries + imperative header | Phase-2 backlog |
| Durable (persisted) hysteresis state | focus is a heuristic; in-memory cold-start costs ≤1 turn | revisit only if cold-start proves costly |
| Hysteresis for proactive/no-session turns | no session id → no stickiness; pure relevance/fallback | by design |
